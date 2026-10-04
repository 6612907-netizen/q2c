#!/usr/bin/env python3
"""通信跟踪（transport trace）：只可追加的事件流，回答九问。

它记录的是**通信事实**：谁发、谁收、何时送达、何时开始、哪条会话、哪些产物、
哪些重投、回了什么、何时确认（PROTOCOL.md §8）。

它**不是**项目完成真相。这里没有"任务完成""可以发布""验收通过"三类字段，
出现即属非法（`FORBIDDEN_VALUE`）。想宣布项目状态，请把结论写进 `payload` 或
你自己的系统里，q2c 只负责把它送到。

三条实现纪律：

1. **写前脱敏**（`security.redact`）：事件里会带对侧输出的片段，那是最容易沾凭据的位置。
2. **只读查询零写入**：`inspect`／`list`／`trace` 在被复验的根里留下一个字节，
   这一轮的读数就全部作废（这条在实验根里被抓过一次）。
3. **取不到就说取不到**：九问的每一项都可能是 `NOT_OBSERVED`（没发生过）或
   `UNVERIFIABLE`（发生了但我这套读数证明不了）。两者不许互相顶替，也不许折成"没有欠账"。
"""

from __future__ import annotations

import json
import os

from . import protocol, security

TRACE_FILE = "events.jsonl"

NOT_OBSERVED = "NOT_OBSERVED"
UNVERIFIABLE = "UNVERIFIABLE"
ABSENT = "TRACE_ABSENT"

# 九问 ↔ 事件字段。加一问就得在这里加一行，并同步 PROTOCOL.md §8（doc-sync 测试盯着条数）。
NINE_QUESTIONS = (
    ("who_sent", ("sender",), "谁发的"),
    ("who_received", ("receiver",), "谁收的"),
    ("when_delivered", ("state", "DELIVERED", "at"), "何时送达"),
    ("when_started", ("state", "STARTED", "at"), "何时开始"),
    ("which_session", ("session_id",), "哪条会话"),
    ("which_artifacts", ("artifact_refs",), "哪些产物"),
    ("which_retries", ("retry_of",), "哪些重投"),
    ("what_response", ("result_sha256",), "回了什么（引用，不是正文）"),
    ("when_acknowledged", ("state", "ACKED", "at"), "何时确认"),
)

# 事件里不许出现的字段名（项目真相三类）
FORBIDDEN_EVENT_KEYS = ("task_completed", "ready_to_release", "acceptance", "grade", "release_ready")


class TraceError(Exception):
    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


class Trace:
    """事件流的唯一写者。

    `Trace(home)` 只算路径，不建目录；目录在**第一次写**时才建——
    这样"跑一遍只读命令"能在一个从未写过的根上证明它一个字节都没动。
    """

    def __init__(self, home: str):
        self.home = home
        self.path = os.path.join(home, "trace", TRACE_FILE)

    # ---- 写 ---------------------------------------------------------------

    def emit(self, event: dict) -> dict:
        bad = [k for k in event if str(k).lower() in FORBIDDEN_EVENT_KEYS]
        if bad:
            raise TraceError("FORBIDDEN_EVENT_KEY", ",".join(sorted(bad)))
        pv = event.get("protocol_version")
        if pv and pv not in protocol.SUPPORTED_PROTOCOL_VERSIONS:
            raise TraceError("UNSUPPORTED_PROTOCOL_VERSION", repr(pv))
        row = dict(event)
        row.setdefault("at", protocol.utc_now())
        row.setdefault("writer_pid", os.getpid())
        redacted = _redact_row(row)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        new = not os.path.exists(self.path)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(redacted, ensure_ascii=False, sort_keys=True) + "\n")
        if new:
            try:
                os.chmod(self.path, 0o600)     # 事件流会带正文摘要＝用户数据
            except OSError:
                pass
        return redacted

    def state_change(self, envelope_or_ids, from_state: str, to_state: str, **extra) -> dict:
        """一次状态迁移。非法迁移在这里就被 `protocol.next_state` 拦住（零写入）。"""
        ids = _ids(envelope_or_ids)
        if from_state and to_state:
            protocol.next_state(from_state, to_state)      # 先判，判得过才落盘
        ev = {"ev": "state", "from_state": from_state, "to_state": to_state,
              "protocol_version": protocol.PROTOCOL_VERSION}
        ev.update(ids)
        ev.update(extra)
        return self.emit(ev)

    # ---- 读（零写入） ------------------------------------------------------

    def events(self, request_id: str = "") -> list:
        if not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    out.append({"ev": "malformed_line", "raw_sha256": security.redact(ln)[:64]})
                    continue
                if isinstance(d, dict) and (not request_id or str(d.get("request_id")) == request_id):
                    out.append(d)
        return out

    def present(self) -> bool:
        return os.path.isfile(self.path)

    def nine_questions(self, request_id: str) -> dict:
        """九问逐条回答。这一格没发生过 ⇒ `NOT_OBSERVED`；发生了但证明不了 ⇒ `UNVERIFIABLE`。"""
        if not self.present():
            return {"request_id": request_id, "trace": ABSENT,
                    "why": "跟踪件不在（%s）⇒ 这不是「没有事件」，是「没读到」" % self.path}
        evs = self.events(request_id)
        if not evs:
            return {"request_id": request_id, "trace": NOT_OBSERVED,
                    "why": "跟踪件在，但这一笔一条事件都没有"}
        # 任何带 to_state 的事件都算状态证据（事件名不要求叫 "state"：
        # 只认 ev=="state" 会让 delivered/started/acked 三问在读数里集体变 NOT_OBSERVED，
        # 而时间线上明明发生过——那是"有证据却报没发生"，比缺字段更坏）
        states = {}
        for e in evs:
            if e.get("to_state"):
                states[str(e["to_state"])] = e.get("at")
        answers = {}
        for key, spec, label in NINE_QUESTIONS:
            if key == "which_retries":
                # 重投有两条来路：人手敲 `retry-delivery`（写 `retry_of` 字段），
                # 以及结果腿自己的补送达（`ev=result_retry_of_delivery`＋`to_state=DELIVERY_RETRY`）。
                # 早先只认前者，于是真腿里明明发生过两笔补送达，九问报 NOT_OBSERVED——
                # 那是"把已发生的重投藏起来"，比缺字段坏（§8 第 7 问存在的意义就是让它可见）。
                answers[key] = _retries(evs)
                continue
            if len(spec) == 1 and spec[0] in ("sender", "receiver", "session_id",
                                              "artifact_refs", "retry_of", "result_sha256"):
                vals = [e.get(spec[0]) for e in evs if e.get(spec[0]) not in (None, "", [])]
                answers[key] = _collapse(vals)
            else:
                want_state = spec[1]
                answers[key] = states.get(want_state, NOT_OBSERVED)
        answers["_timeline"] = [{"at": e.get("at"), "ev": e.get("ev"),
                                 "to_state": e.get("to_state")} for e in evs]
        answers["state_now"] = _state_now(evs)
        return answers


def _retries(evs) -> list:
    """从事件里**举出**每一次重投的形状；一条都没有 ⇒ NOT_OBSERVED（不折成"零欠账"）。"""
    out = []
    for e in evs:
        hit = (e.get("retry_of") is not None
               or str(e.get("to_state") or "") == protocol.ST_DELIVERY_RETRY
               or "retry" in str(e.get("ev") or ""))
        if not hit:
            continue
        out.append({"at": e.get("at"), "ev": e.get("ev"), "from_state": e.get("from_state"),
                    "to_state": e.get("to_state"), "retry_of": e.get("retry_of")})
    return out or NOT_OBSERVED


def _state_now(evs) -> str:
    """现在停在哪个状态＝时间线上**最后一条带 to_state 的事件**。

    曾经这里只认 `ev=="state"`，而产品的事件名是 `created`／`queued`／`delivering`／
    `delivered`／`started`／`responded`／`acked`——一条 ACKED 的真记录因此被报成
    `UNVERIFIABLE`。同一段代码上面十几行就写着这个坑（九问的其余项已按 to_state 取），
    这里漏改属于"读数撒谎"那一族，不是"字段缺失"。
    反向同样钉住：真的没有任何状态证据时保持 `UNVERIFIABLE`，不许编一枚末态出来。
    """
    for e in reversed(evs):
        if e.get("to_state"):
            return str(e["to_state"])
    return UNVERIFIABLE


def _collapse(vals):
    vals = [v for v in vals if v not in (None, "")]
    if not vals:
        return NOT_OBSERVED
    uniq, seen = [], set()
    for v in vals:
        k = json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(v)
    return uniq[0] if len(uniq) == 1 else {"multiple": uniq}


def _ids(obj) -> dict:
    if isinstance(obj, protocol.Envelope):
        return {"message_id": obj.message_id, "request_id": obj.request_id,
                "correlation_id": obj.correlation_id, "session_id": obj.session_id}
    if isinstance(obj, dict):
        return {k: obj.get(k) for k in ("message_id", "request_id", "correlation_id", "session_id")
                if obj.get(k)}
    return {"request_id": str(obj)}


def _redact_row(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            out[k] = security.redact(v)
        elif isinstance(v, dict):
            out[k] = _redact_row(v)
        elif isinstance(v, list):
            out[k] = [security.redact(x) if isinstance(x, str) else
                      (_redact_row(x) if isinstance(x, dict) else x) for x in v]
        else:
            out[k] = v
    return out
