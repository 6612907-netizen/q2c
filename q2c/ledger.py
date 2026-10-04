#!/usr/bin/env python3
"""传输台账：整轮临界区、原子落盘、写前重读三方合并、陈旧检测会响。

这一段是从已验证内核（fix02 `relay.py:123-464`）整块搬来的，行为不变、只换名字。
它守的四条性质，每一条背后都有一次真事故（判据落在 `tests/test_stores_and_honesty.py`
与 `tests/test_recovery.py`，两处都在跑）：

1. **一整轮＝一个临界区**。旧实现是 `load()` → 一堆推进 → `save()`，中间不互斥；
   两个进程并发时后写的把先写那笔状态转移整块盖掉（台账是整写文件，不是逐条追加）。
2. **只读动作一个字节都不写**。R9 第 3 条抓到"只读核查"在被核查的根里建了 `state/`，
   于是"复跑判定有没有改写对象"这一问的答案变成"是"。
3. **读不到 ≠ 空**。`LedgerAbsent`（文件不在）与"读到但零条记录"是两回事；
   把前者当后者＝闸门自己开了。
4. **第三者绕锁直写盘 ⇒ 响，不静默**。版本号＋整本哈希对不上就记 `stale_write`，
   合并按字段走，不整块覆盖。
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import time

from . import security

BARE_KEYS = ("request_id", "protocol_version", "created_at")


class LedgerAbsent(Exception):
    """台账文件不在。与"读到、零条"严格分开（协议 §9 fail-closed 总则）。"""

    side_effects = 0


class LedgerUnreadable(Exception):
    """台账在但读不动／形状坏。绝不"抢救"成空表，也不就地修复被观测物。"""

    side_effects = 0


class StaleLedgerError(RuntimeError):
    """检测到陈旧写：本进程读到的版本已被别人改过。"""


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger(dict):
    """`request_id -> record` 的映射，附三方合并需要的"读入时快照"。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.base = {}
        self.rev = 0
        self.digest = ""
        self.notes = []


def _bare(rec: dict) -> dict:
    """取记录里"不许被合并改写"的那几枚身份字段。"""
    return {k: rec.get(k) for k in BARE_KEYS if k in rec}


def _digest_of(records: dict) -> str:
    blob = json.dumps(records, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LedgerPaths:
    def __init__(self, home: str):
        self.home = home

    @property
    def state_dir(self):
        return os.path.join(self.home, "state")

    @property
    def ledger(self):
        return os.path.join(self.state_dir, "ledger.json")

    @property
    def lock_file(self):
        return os.path.join(self.state_dir, "ledger.lock")

    @property
    def trace_dir(self):
        return os.path.join(self.home, "trace")


@contextlib.contextmanager
def ledger_lock(paths: LedgerPaths, timeout_s: float = 8.0, events=None):
    """整轮临界区。等不到锁 ⇒ 记一笔并返回 False 语义（调用方本轮不派发）。

    只读动作**不许**走到这里来（见 `open_read_only`）。
    """
    os.makedirs(paths.state_dir, exist_ok=True)
    fh = open(paths.lock_file, "a+", encoding="utf-8")
    deadline = time.time() + timeout_s
    got = False
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            break
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            if time.time() >= deadline:
                break
            time.sleep(0.05)
    if not got:
        if events is not None:
            events.append({"ev": "lock_timeout", "waited_s": round(timeout_s, 2)})
        try:
            fh.close()
        except OSError:
            pass
        yield False
        return
    try:
        fh.seek(0)
        fh.truncate(0)
        fh.write(json.dumps({"pid": os.getpid(), "at": _now()}) + "\n")
        fh.flush()
        yield True
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def load_for_write(paths: LedgerPaths) -> Ledger:
    """写路径专用的读法：**没有台账**＝第一笔要写下，空表起步；
    但"读不动"仍然抛——把坏了的台账当空表再整写覆盖回去，等于销毁别人的账。

    与 `load()` 的分工就这一条（其余判据共用）。`inspect`／`list`／`gap` 那类
    读数一律走 `load()`，那里"不在"必须与"零条"分开说。
    """
    try:
        return load(paths)
    except LedgerAbsent:
        db = Ledger()
        db.notes.append({"ev": "ledger_created_on_first_write", "path": paths.ledger})
        return db


def _disk_records(paths: LedgerPaths):
    """返回 (records|None, absent, unreadable_reason)。"""
    if not os.path.isfile(paths.ledger):
        return None, True, ""
    try:
        with open(paths.ledger, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, False, "读不了：%s" % type(exc).__name__
    if not raw.strip():
        return {}, False, ""
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, False, "JSON 解析失败：%s" % exc
    if not isinstance(doc, dict) or "records" not in doc or not isinstance(doc["records"], dict):
        return None, False, "顶层形状不对（要 {records:{…}, rev:n}）"
    return doc["records"], False, ""


def load(paths: LedgerPaths, read_only: bool = False) -> Ledger:
    """读台账。文件不在 ⇒ `LedgerAbsent`；形状坏 ⇒ `LedgerUnreadable`。

    旧版这两档都折成"空表"，于是 `gap` 在被验收的根里读不到台账时能报"零条未闭"。
    """
    if not read_only:
        os.makedirs(paths.state_dir, exist_ok=True)
    records, absent, why = _disk_records(paths)
    if absent:
        if read_only:
            # 只读动作可以面对"没有台账"这个事实；写路径由调用方决定怎么处理
            return Ledger()
        raise LedgerAbsent(paths.ledger)
    if why:
        raise LedgerUnreadable("%s（%s）" % (why, paths.ledger))
    db = Ledger()
    for rid, rec in records.items():
        if isinstance(rec, dict) and rec.get("request_id"):
            db[rid] = rec
        else:
            db.notes.append({"ev": "dropped_malformed_record", "key": rid})
    db.base = json.loads(json.dumps({k: v for k, v in db.items()}, ensure_ascii=False))
    try:
        with open(paths.ledger, encoding="utf-8") as fh:
            db.rev = int(json.loads(fh.read()).get("rev", 0))
            db.digest = _digest_of(records)
    except (OSError, ValueError):
        db.rev, db.digest = 0, ""
    return db


def merge_ledger(base: dict, disk: dict, mine: dict, notes: list) -> dict:
    """三方合并：base＝本进程读入时那一份，disk＝盘上现值，mine＝本进程改完的值。

    规则（逐字段，不整块覆盖）：
      · 只有我改了 ⇒ 用我的；
      · 只有盘上改了 ⇒ 用盘上的（别人那一笔状态转移不许被我盖掉）；
      · 两边都改了同一个字段 ⇒ 保我这笔，**并记一条 `field_conflict_keep_mine`**（响，不静默）；
      · 我删了、盘上那条没人动过 ⇒ 删生效；
      · 我删了、盘上那条被别人改过 ⇒ 保盘上值并响（K46：删除不许吞掉别人的更新）。
    """
    out = {}
    all_keys = set(base) | set(disk) | set(mine)
    for rid in all_keys:
        b, d, m = base.get(rid), disk.get(rid), mine.get(rid)
        if m is None and d is not None and b is not None:
            # 我这轮删了它
            if json.dumps(d, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False):
                notes.append({"ev": "merge_delete_applied", "request_id": rid})
                continue
            notes.append({"ev": "merge_delete_conflict", "request_id": rid,
                          "note": "对方在删之前改过这条 ⇒ 保盘上值并响"})
            out[rid] = d
            continue
        if m is None:
            if d is not None:
                out[rid] = d          # 我没这条（或本来就没有），保盘上
            continue
        if d is None or b is None:
            out[rid] = m
            continue
        merged = dict(d)
        for field in set(list(b.keys()) + list(d.keys()) + list(m.keys())):
            if field in ("history",):
                continue
            bv, dv, mv = b.get(field), d.get(field), m.get(field)
            if mv == bv:
                merged[field] = dv if dv is not None or field not in m else mv
            elif dv == bv:
                merged[field] = mv
            elif json.dumps(mv, sort_keys=True, ensure_ascii=False) != json.dumps(dv, sort_keys=True, ensure_ascii=False):
                notes.append({"ev": "field_conflict_keep_mine", "request_id": rid, "field": field})
                merged[field] = mv
            else:
                merged[field] = mv
        merged["history"] = _merge_history(b.get("history", []), d.get("history", []), m.get("history", []))
        out[rid] = merged
    return out


def _merge_history(base, disk, mine) -> list:
    """history 取**并集**（顺序：盘上在前、我这轮追加在后），不去掉对方的轨迹。

    后写不胜：把 history 当普通字段"谁的赢"会直接丢掉并发那一笔的证据。
    """
    seen, out = set(), []
    for row in list(disk or []) + list(mine or []):
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def save(paths: LedgerPaths, db: Ledger, force: bool = False) -> dict:
    """写前重读盘上现值 → 三方合并 → 原子落盘。返回本次产生的合并事件。

    `force` 只给测试夹具用；产品路径的写者都该走默认分支。
    """
    notes = []
    disk, absent, why = _disk_records(paths)
    if disk is None and not absent:
        # 盘上现值读不动 ⇒ 不许整块覆盖它（那会把读不懂的东西当不存在）
        raise LedgerUnreadable("save 前重读失败：%s（拒绝覆盖不可读台账）" % why)
    mine = {k: v for k, v in db.items()}
    if disk is None:
        merged = mine
    elif force or not db.base:
        merged = mine
    else:
        merged = merge_ledger(db.base, disk, mine, notes)
    rev = 0
    if os.path.isfile(paths.ledger):
        try:
            with open(paths.ledger, encoding="utf-8") as fh:
                rev = int(json.loads(fh.read()).get("rev", 0))
        except (OSError, ValueError, TypeError):
            rev = int(db.rev or 0)
    if db.digest and disk is not None and _digest_of(disk) != db.digest:
        notes.append({"ev": "stale_write_detected", "loaded_digest": db.digest[:16],
                      "disk_digest": _digest_of(disk)[:16],
                      "note": "第三者绕锁直写盘：已按字段合并，未整块覆盖"})
    doc = {"rev": int(rev) + 1, "records": merged, "written_at": _now(), "writer_pid": os.getpid()}
    security.atomic_write(paths.ledger, json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    db.base = json.loads(json.dumps(merged, ensure_ascii=False))
    db.rev = doc["rev"]
    db.digest = _digest_of(merged)
    for n in notes:
        db.notes.append(dict(n, at=_now()))
    return {"merged_records": len(merged), "events": notes, "rev": doc["rev"]}


@contextlib.contextmanager
def open_read_only(paths: LedgerPaths):
    """只读动作的入口：不建目录、不取锁、不写一个字节。

    这条不是风格问题：`inspect`／`list`／`trace` 一旦在被复验的对象里留下 `state/`，
    "复跑判定有没有改写对象"就答成"是"，那一轮的读数全部作废。
    """
    yield
