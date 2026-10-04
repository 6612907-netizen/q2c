#!/usr/bin/env python3
"""传输层：交接记录的创建、投递、送达确认、有界重投、到期、取消、重启恢复。

这一段是产品的本体，也是任务书 §1「不许重写已验证传输」的对象。
它做的事只有一件：**把一条消息送到，并证明它送到了**；
它不做的事（定级／批准／放行／调度／重跑任务）在 `Q2C-BOUNDARY-AUDIT.md` 里逐条点名了。

五条硬规矩（每条都有对应的判据文件）：

1. **一整轮＝一个临界区**（`ledger.ledger_lock`）。只读动作不进这个区，也不写一个字节。
2. **幂等**：同一 `idempotency_key` 只投一次；重复投来的同键消息记 `duplicate_suppressed`，
   不改状态、不新增副作用。
3. **重投＝重投消息，绝不重跑任务**（协议 §5）。结果已在案时，任何路径只允许补送达；
   绑定串与派发文一字节都不许变（变了，上一腿迟到的答复就永远认领不了）。
4. **未知时停手**：读不动／进程读不出／退出码侧件缺失 ⇒ 一律 `UNKNOWN`，
   保持原状态并记账，**不自动重投**。旧版把"读不出来"当成"确认没跑过"，
   于是在对方还在跑或已跑完时又送了一遍——那是被独立复现过的阻断级缺陷。
5. **到上限就停手等人**：投递预算用尽 ⇒ `DELIVERY_FAILED` ＋ 一条失败通知（逐笔留回执）。
   自动重试自愈不了认证问题与断链，只会把同一笔反复推给队列。
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager

from . import ack, adapters, artifacts, config as config_mod, ledger, protocol, security
from .sessions import SessionStore
from .trace import Trace

LEGS = ("request", "result")
RESULT_SUFFIX = ".result.txt"
NOTIFY_DIR = "notify"
NOTIFY_ACK_FILE = os.path.join("state", "notify-ack.jsonl")


class TransportError(Exception):
    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


def new_nonce() -> str:
    return os.urandom(8).hex()


class Transport:
    def __init__(self, home: str | None = None, cfg: config_mod.Config | None = None):
        self._defer_saves = False
        self.cfg = cfg or config_mod.load(home)
        self.home = self.cfg.home
        self.paths = ledger.LedgerPaths(self.home)
        self.trace = Trace(self.home)
        self.sessions = SessionStore(self.home)
        adapters.import_builtin()

    def _save(self, db) -> dict:
        """transport 唯一的落盘出口。

        批量推进（`pump_ready`）期间只登记不写，收尾一次写完：
        一笔一次 fsync 会把负载判据变成"测磁盘"而不是"测桥"。
        但延迟**不等于丢掉**——被推迟的是"合并重读"，不是"这轮的现场"：
        每一笔推进仍然要求可见可恢复，所以：
          · 延迟期内的中间写只刷新内存快照（`self._snapshot`），不碰磁盘；
          · 收尾用那份快照当"读入基线"落一次盘，合并语义与单笔路径完全一致。
        """
        if self._defer_saves:
            return {"deferred": True}
        return ledger.save(self.paths, db)

    # ---- 锁与读 ------------------------------------------------------------

    @contextmanager
    def critical(self):
        """会写的动作统一走这里；拿不到锁 ⇒ 本轮不派发（不是"照做"）。"""
        events = []
        with ledger.ledger_lock(self.paths, events=events) as got:
            if not got:
                for e in events:
                    self.trace.emit(dict(e, ev="ledger_lock_timeout"))
            yield got

    def read(self, request_id: str = "") -> dict:
        """只读：不建目录、不取锁、零写入。

        读数分三档，不许并格：**ABSENT**（这本册子不在，多半是 Q2C_HOME 指错了根）／
        **UNREADABLE**（在但读不动）／**ok**（读到了，可以是零条）。
        把前两档折成"零条"，下游就会把"没读到"报成"没有欠账"。
        """
        if not os.path.isfile(self.paths.ledger):
            return {"ledger": "ABSENT", "why": self.paths.ledger, "home": self.home}
        try:
            db = ledger.load(self.paths, read_only=True)
        except ledger.LedgerUnreadable as exc:
            return {"ledger": "UNREADABLE", "why": str(exc), "home": self.home}
        if request_id:
            rec = db.get(request_id)
            return {"ledger": "ok", "record": rec} if rec else {"ledger": "ok", "record": None}
        return {"ledger": "ok", "records": {k: v.get("state") for k, v in db.items()}}

    # ---- 创建 --------------------------------------------------------------

    def create(self, envelope: protocol.Envelope) -> dict:
        """收下一条 HANDOFF_REQUEST：校验 → 幂等判定 → 登记 → 入队。

        重复投来的同键消息**不改状态、不再投递、不新增副作用**，
        但原件要留（`duplicate_of` 指着被它撞上那一笔），因为"有人重复投了"
        本身就是通信事实，不许悄悄丢。
        """
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不收单")
            db = ledger.load_for_write(self.paths)
            out = self._create_one(db, envelope)
            self._save(db)
            return out

    def create_many(self, envelopes) -> dict:
        """批量收单：一个临界区、一次落盘。

        存在的唯一理由是判据与漏单重放（1000 枚那一格）：
        一笔一次 fsync 会把这一格变成"测磁盘"，而不是"测状态机与账"。
        单笔逻辑仍然走 `_create_one`——**两条入口共用一份实现**，不许各写一套判据。
        """
        envelopes = list(envelopes)
        if not envelopes:
            return {"status": "noop", "created": 0, "duplicates": 0}
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不收单")
            db = ledger.load_for_write(self.paths)
            created, dups, ids = 0, 0, []
            for env in envelopes:
                out = self._create_one(db, env)
                if out["status"] == "created":
                    created += 1
                    ids.append(out["request_id"])
                else:
                    dups += 1
            self._save(db)
            return {"status": "ok", "created": created, "duplicates": dups, "request_ids": ids}

    def _create_one(self, db, envelope: protocol.Envelope) -> dict:
        if envelope.message_type != protocol.MSG_HANDOFF_REQUEST:
            raise TransportError("NOT_A_REQUEST", envelope.message_type)
        self._check_workspace(envelope)
        for rid, rec in db.items():
            if rec.get("idempotency_key") == envelope.idempotency_key:
                self.trace.emit(_ids(envelope) | {"ev": "duplicate_suppressed",
                                                  "idem": envelope.idempotency_key,
                                                  "duplicate_of": rid})
                return {"status": "duplicate_suppressed", "request_id": rid,
                        "state": rec.get("state")}
        suspected = security.contains_secret_shape(envelope.payload)
        if suspected:
            # 载荷必须逐字投递，所以这里**不**改正文、也**不**把正文抄进事件流；
            # 只记布尔标记＋一条可见提示（SECURITY.md §6）。
            self.trace.emit(_ids(envelope) | {"ev": "payload_secret_suspected",
                                              "note": "载荷里有凭据形状的内容；"
                                                      "正文不抄进跟踪，状态件按 0600 落盘"})
        rid = envelope.request_id
        db[rid] = {
            "request_id": rid,
            "protocol_version": envelope.protocol_version,
            "created_at": envelope.created_at,
            "idempotency_key": envelope.idempotency_key,
            "correlation_id": envelope.correlation_id,
            "envelope": envelope.to_dict(),
            "nonce": new_nonce(),
            "state": protocol.ST_QUEUED,
            "delivery": {"request": {"attempts": 0, "max": self.cfg["delivery_attempts_max"]},
                         "result": {"attempts": 0, "max": self.cfg["delivery_attempts_max"]}},
            "handles": {},
            "result": {},
            "payload_secret_suspected": bool(suspected),
            "history": [],
        }
        self.trace.emit(_ids(envelope) | {"ev": "created", "from_state": protocol.ST_CREATED,
                                         "to_state": protocol.ST_CREATED,
                                         "sender": envelope.sender, "receiver": envelope.receiver,
                                         "session_id": envelope.session_id,
                                         "handoff_type": envelope.handoff_type,
                                         "artifact_refs": [a.to_dict() for a in envelope.artifact_refs]})
        self.trace.emit(_ids(envelope) | {"ev": "queued", "from_state": protocol.ST_CREATED,
                                         "to_state": protocol.ST_QUEUED,
                                         "note": "已收单入队，等这一腿被投出去"})
        out = {"status": "created", "request_id": rid, "state": protocol.ST_QUEUED}
        if suspected:
            out["warning"] = ("payload_secret_suspected：载荷里有凭据形状的内容。"
                              "q2c 会逐字投递、不会改写，但状态件按 0600 落盘；"
                              "别把凭据当载荷发。")
        return out

    # ---- 投递（Push First） --------------------------------------------------

    def send(self, envelope: protocol.Envelope, wait: bool = True, max_steps: int = 8) -> dict:
        """`q2c send`：收单＋**有界地**把这一腿推到底（协议 §Push First）。

        `max_steps` 是给"一条命令走完一次交接"用的硬上界，不是轮询循环：
        到界就停手返回当前读数，绝不自轮询、绝不常驻（产品定义：桥不是编排器、无常驻进程）。
        """
        res = self.create(envelope)
        if res["status"] == "duplicate_suppressed":
            return res
        if wait:
            steps, last = 0, None
            while steps < max_steps:
                last = self.pump(envelope.request_id)
                steps += 1
                state = last.get("state") or last.get("status")
                if state in protocol.TERMINAL_STATES or state in protocol.PARKED_STATES \
                        or last.get("status") in ("terminal", "no_progress", "in_flight", "expired"):
                    # 停放态也在这里停：预算用尽后**不许**在同一句命令里继续催（协议 §5）
                    break
            res["pump"] = last
            res["steps"] = steps
        return res

    def pump(self, request_id: str) -> dict:
        """把这一笔往前推一个**可观测**台阶。推不动就如实返回当前状态。"""
        with self.critical() as got:
            if not got:
                return {"request_id": request_id, "status": "held", "why": "拿不到台账锁"}
            db = ledger.load_for_write(self.paths)
            rec = db.get(request_id)
            if not rec:
                raise TransportError("NO_SUCH_REQUEST", request_id)
            if self._expire_if_due(db, rec):
                out = {"request_id": request_id, "status": "expired", "state": rec["state"]}
                self._save(db)
                return out
            if rec["state"] in protocol.TERMINAL_STATES:
                out = {"request_id": request_id, "status": "terminal", "state": rec["state"]}
                self._save(db)
                return out
            if rec["state"] in (protocol.ST_CREATED, protocol.ST_QUEUED, protocol.ST_DELIVERY_RETRY):
                out = self._deliver_request(db, rec)
            elif rec["state"] in (protocol.ST_DELIVERING, protocol.ST_DELIVERED, protocol.ST_STARTED):
                out = self._await_or_respond(db, rec)
            elif rec["state"] == protocol.ST_RESPONDED and self._result_pending(rec):
                out = self._deliver_result(db, rec)
            else:
                out = {"status": "no_progress", "state": rec["state"]}
            self._save(db)
            return out

    # ---- 重投：只重投消息 ----------------------------------------------------

    def pump_ready(self, max_rounds: int = 8, limit: int | None = None) -> dict:
        """漏单恢复入口：**一个临界区里**把在途记录各推一拍，推完一次落盘。

        为什么要有它（而不是让调用方循环 `pump()`）：一笔一次锁＋一次整写文件，
        1000 枚就是几千次 fsync；判据跑到十分钟后测的是磁盘不是桥。
        它与 `pump()` 走**同一个** `_advance`，只是把"锁＋读＋多次推进＋写"合并一轮，
        所以并发语义、合并语义、跟踪事件都一致——两条路径不许各立一套判据（双真源）。

        `max_rounds` 是硬上界（防成环），不是常驻轮询；到界就返回当前读数。
        """
        self._defer_saves = True
        try:
            return self._pump_ready_inner([], 0, limit, max_rounds)
        finally:
            self._defer_saves = False

    def _pump_ready_inner(self, touched, rounds, limit, max_rounds) -> dict:
        with self.critical() as got:
            if not got:
                return {"status": "held", "why": "拿不到台账锁，本轮不推进", "advanced": 0}
            db = ledger.load_for_write(self.paths)
            for _ in range(max_rounds):
                todo = [rid for rid, rec in db.items()
                        if rec.get("state") not in protocol.TERMINAL_STATES
                        and rec.get("state") not in protocol.PARKED_STATES]
                if limit is not None:
                    todo = todo[:limit]
                if not todo:
                    break
                rounds += 1
                for rid in sorted(todo):
                    rec = db.get(rid)
                    if not rec:
                        continue
                    self._advance(db, rec)
                    touched.append(rid)
            # 收尾这一笔必须真落盘：绕过延迟标记，把整轮的现场一次写完。
            # 关键：**不动 db.base**。读入基线必须是"这轮开始时盘上那份"；
            # 把基线换成自己的中间态，上一段里 disk 的旧值会被读成"对方改了"，
            # 于是三方合并把我这一轮整轮判输——实测就是这样把 1000 枚 ACKED 抹回 QUEUED。
            self._defer_saves = False
            ledger.save(self.paths, db)
            states = {rid: (db.get(rid) or {}).get("state") for rid in set(touched)}
        return {"status": "ok", "rounds": rounds, "advanced": len(touched),
                "terminal": sum(1 for s in states.values() if s in protocol.TERMINAL_STATES),
                "states_sample": dict(sorted(states.items())[:3])}

    def _advance(self, db, rec) -> dict:
        """`pump()` 的单笔推进部分（两条路径共用，不许写两份）。"""
        rid = rec["request_id"]
        if self._expire_if_due(db, rec):
            return {"request_id": rid, "status": "expired", "state": rec["state"]}
        if rec["state"] in protocol.TERMINAL_STATES:
            return {"request_id": rid, "status": "terminal", "state": rec["state"]}
        if self._result_pending(rec):
            return self._deliver_result(db, rec)
        if rec["state"] in (protocol.ST_CREATED, protocol.ST_QUEUED, protocol.ST_DELIVERY_RETRY):
            return self._deliver_request(db, rec)
        if rec["state"] in (protocol.ST_DELIVERING, protocol.ST_DELIVERED, protocol.ST_STARTED):
            return self._await_or_respond(db, rec)
        return {"status": "no_progress", "state": rec["state"]}

    def retry_delivery(self, request_id: str, leg: str = "") -> dict:
        """协议 §5 的落点。

        允许的前提（三条同时成立才动手，否则零副作用拒绝）：
          1. 这一笔不在终态；
          2. 指定的腿（或自动判出的腿）**确实**处于可重投状态（QUEUED/DELIVERING/DELIVERY_RETRY
             投请求腿；RESPONDED 投结果腿）；
          3. 结果已在案时**只许**投结果腿——再叫一次对侧模型就是缺陷。
        """
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不重投")
            db = ledger.load_for_write(self.paths)
            rec = db.get(request_id)
            if not rec:
                raise TransportError("NO_SUCH_REQUEST", request_id)
            state = rec["state"]
            if state in protocol.TERMINAL_STATES:
                raise TransportError("RETRY_ON_TERMINAL", "%s 已是终态，重投没有对象" % state)
            if leg not in ("",) + LEGS:
                raise TransportError("UNKNOWN_LEG", repr(leg))
            pending = self._result_pending(rec)
            chosen = leg or ("result" if pending else "request")
            if chosen == "request" and pending:
                # 结果已在案 ⇒ 绝不再投请求腿（那等于重跑对侧任务）。改投结果腿并把这一刀留痕。
                self.trace.emit({"ev": "retry_refused_result_on_disk", "request_id": request_id,
                                 "asked": "request", "did": "result",
                                 "note": "协议 §5 第 3 条：结果已在案只许补送达"})
                chosen = "result"
            if chosen == "result" and not pending:
                raise TransportError("RETRY_LEG_MISMATCH",
                                     "%s 没有待投的回复（要么没结果，要么已确认）" % state)
            if chosen == "request" and state not in (protocol.ST_CREATED, protocol.ST_QUEUED,
                                                     protocol.ST_DELIVERING, protocol.ST_DELIVERY_RETRY,
                                                     protocol.ST_DELIVERED, protocol.ST_DELIVERY_FAILED):
                raise TransportError("RETRY_LEG_MISMATCH", "state=%s 不能投请求腿" % state)
            rec["retry_of"] = rec.get("retry_of", 0) + 1
            self.trace.emit({"ev": "retry_delivery", "request_id": request_id, "leg": chosen,
                             "retry_of": rec["retry_of"],
                             "nonce_kept": rec["nonce"],
                             "note": "同一 message_id、同一绑定串、同一份派发文；不重跑对侧任务"})
            if chosen == "result":
                out = self._deliver_result(db, rec)
            else:
                if rec["state"] == protocol.ST_DELIVERY_FAILED:
                    rec["state"] = protocol.next_state(rec["state"], protocol.ST_DELIVERY_RETRY)
                out = self._deliver_request(db, rec)
            self._save(db)
            return out

    def cancel(self, request_id: str, reason: str = "") -> dict:
        """取消**投递**。不撤销对侧已经开始的工作（那不归 q2c 管）。"""
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不取消")
            db = ledger.load_for_write(self.paths)
            rec = db.get(request_id)
            if not rec:
                raise TransportError("NO_SUCH_REQUEST", request_id)
            if rec["state"] in protocol.TERMINAL_STATES:
                return {"request_id": request_id, "status": "already-terminal", "state": rec["state"]}
            handled = self._cancel_inflight(rec)
            before = rec["state"]
            rec["state"] = protocol.next_state(before, protocol.ST_CANCELLED)
            rec["history"].append({"state": protocol.ST_CANCELLED, "at": protocol.utc_now(),
                                   "note": security.redact(reason or "发送方取消投递")})
            self._save(db)
            self.trace.emit({"ev": "cancelled", "request_id": request_id,
                             "from_state": before, "to_state": protocol.ST_CANCELLED,
                             "inflight_cancelled": handled,
                             "note": "只断投递；对侧已开始的工作不由 q2c 撤销"})
            return {"request_id": request_id, "status": "cancelled", "state": protocol.ST_CANCELLED,
                    "inflight_cancelled": handled}

    def expire_due(self) -> dict:
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不判到期")
            db = ledger.load_for_write(self.paths)
            out = []
            for rid, rec in db.items():
                if self._expire_if_due(db, rec):
                    out.append(rid)
            self._save(db)
            return {"expired": out, "checked": len(db)}

    # ---- 重启恢复 ------------------------------------------------------------

    def recover(self) -> dict:
        """重启后的处置：证据依次查，**未知**与**确认没有跑**分开。

        阶梯（产品里可用的证据比实验根少一档，那档的缺失在 ADAPTERS.md 里写明）：
          1. 结果原件在案且摘要对得上 ⇒ 只补送达；
          2. 落盘件里有合格答复 ⇒ 登记结果，只补送达；
          3. 在飞进程还活着（pid＋启动时刻双对）⇒ 等，不重复起；
          4. 探针读不出来（`unknown`）⇒ **未知**：保持原状态、记一笔、不重投；
          5. 确认没跑（无落盘件、无退出码侧件、进程确认不在）⇒ 在预算内重投请求腿；
          6. 其余一律未知处理。
        """
        with self.critical() as got:
            if not got:
                raise TransportError("LEDGER_BUSY", "拿不到台账锁，本轮不恢复")
            db = ledger.load_for_write(self.paths)
            acted = []
            for rid, rec in db.items():
                st = rec.get("state")
                if st in protocol.TERMINAL_STATES:
                    continue
                if st in protocol.PARKED_STATES:
                    # 停放态是"已经判过、正等人处置"。恢复阶梯再走一遍会把
                    # "确认没跑"重新判一次，等于替人催。这里必须不动。
                    rec.setdefault("recover_skipped", "parked")
                    continue
                env = protocol.Envelope.from_dict(rec["envelope"])
                why = ""
                if rec.get("result", {}).get("sha256") and self._result_usable(rec):
                    self._set_state(db, rec, protocol.ST_RESPONDED, "结果已在案 ⇒ 只补送达，不重跑对侧")
                    rec["recovered"] = "result_on_disk"
                    acted.append(rid)
                    continue
                cap_state = self._capture_state(rec, env)
                if cap_state == "usable":
                    self._register_result(db, rec, env)
                    rec["recovered"] = "capture_hit"
                    acted.append(rid)
                    continue
                ps = self._proc_state(rec)
                if ps == "alive":
                    rec["recovered"] = "await_inflight"
                    self.trace.emit({"ev": "recover_await", "request_id": rid, "proc": ps,
                                     "note": "原进程仍在跑 ⇒ 等待，不重复启动（重复＝重复花钱＋两条打脸的答复）"})
                    acted.append(rid)
                    continue
                if ps == "unknown":
                    rec["recovered"] = "unknown_hold"
                    rec["uncertain"] = True
                    self.trace.emit({"ev": "exec_record_unknown", "request_id": rid, "proc": ps,
                                     "note": "未知≠确认没有执行；拒绝自动重投"})
                    self.notify(rid, protocol.FAILURE_CLASSES and "UNKNOWN",
                                "执行记录未知（进程探针读不出来）⇒ 需人看")
                    acted.append(rid)
                    continue
                if cap_state == "unreadable":
                    rec["recovered"] = "unknown_hold"
                    rec["uncertain"] = True
                    self.trace.emit({"ev": "exec_record_unknown", "request_id": rid,
                                     "capture": "unreadable", "note": "落盘件读不动＝未知，不按缺席处理"})
                    acted.append(rid)
                    continue
                if st in (protocol.ST_CREATED, protocol.ST_QUEUED):
                    acted.append(rid)
                    continue
                dmax = rec["delivery"]["request"]["max"]
                if rec["delivery"]["request"]["attempts"] >= dmax:
                    rec["recovered"] = "confirmed_absent_exhausted"
                    self._fail(db, rec, "ADAPTER_UNAVAILABLE",
                               "确认没有跑过且投递预算已用尽（attempts=%s）"
                               % rec["delivery"]["request"]["attempts"])
                else:
                    rec["recovered"] = "confirmed_absent_redeliver"
                    self._set_state(db, rec, protocol.ST_DELIVERY_RETRY,
                                    "确认没有跑过 ⇒ 在预算内重投**消息**（同一绑定串，不重跑任务）")
                acted.append(rid)
            self._save(db)
            return {"recovered": acted, "home": self.home}

    # ---- 读数 --------------------------------------------------------------

    def inspect(self, request_id: str) -> dict:
        rec = self.read(request_id).get("record")
        env = protocol.Envelope.from_dict(rec["envelope"]) if rec and rec.get("envelope") else None
        out = {"home": self.home, "request_id": request_id, "record": rec,
               "trace": self.trace.nine_questions(request_id) if rec else None}
        if env and env.artifact_refs:
            out["artifact_verification"] = [
                dict(ref=ar.to_dict(), state=artifacts.verify(ar, self.home)[0],
                     note=artifacts.verify(ar, self.home)[1]) for ar in env.artifact_refs]
        return out

    def list_records(self) -> dict:
        got = self.read()
        if got.get("ledger") != "ok":
            # ABSENT／UNREADABLE 一律如实带出去，且**不**给出"零笔在途"这种读数
            return {"home": self.home, "ledger": got.get("ledger"), "why": got.get("why"),
                    "records": [], "counts_by_state": {},
                    "note": "这不是「没有交接」，是「没读到台账」——先核 Q2C_HOME 指到哪棵树"}
        counts = {}
        for rec in got["records"].values():
            counts[rec] = counts.get(rec, 0) + 1
        return {"home": self.home, "ledger": "ok",
                "counts_by_state": {k: counts[k] for k in sorted(counts)},
                "in_flight": sorted([k for k, v in got["records"].items()
                                     if v not in protocol.TERMINAL_STATES]),
                "records": got["records"]}

    # ---- 失败通知（逐笔留回执，不拿"通道开着"顶替"这一笔送到了"） -------------

    def notify(self, request_id: str, failure_class: str, why: str) -> dict:
        """投递失败要**让某个接收方可见**，否则谈不上"非静默丢失"。

        通道没配 ⇒ 只落文件，并把 `notified=file-only` 如实写进台账：
        这一格在 `q2c list`／`inspect` 里读得到，不算闭环（在册教训：
        三条 REJECTED 就是死在"叫过了但没人看见"）。
        """
        os.makedirs(os.path.join(self.home, NOTIFY_DIR), exist_ok=True)
        path = os.path.join(self.home, NOTIFY_DIR, "%s.txt" % request_id)
        body = json.dumps({"request_id": request_id, "failure_class": failure_class,
                           "why": security.redact(why), "at": protocol.utc_now()},
                          ensure_ascii=False, indent=1)
        security.refuse_overwrite(path)
        security.atomic_write(path, body + "\n")
        cmd = (self.cfg.get("notification_cmd") or "").strip()
        delivered, detail = False, "file-only（未配置 notification_cmd）"
        if cmd:
            argv = security.safe_argv(cmd, self.home, request_id=request_id,
                                      note_file=path, failure_class=failure_class)
            env = security.strip_runtime_env(dict(os.environ))
            env.update({"Q2C_NOTIFY_RID": request_id, "Q2C_NOTIFY_FILE": path,
                        "Q2C_NOTIFY_CLASS": failure_class})
            cap = os.path.join(self.home, "state", "notify-out", "%s.out" % request_id)
            phase, rc, pid, _ = _spawn_cap(argv, env, cap, float(self.cfg.get("notify_timeout_s", 30)))
            receipt = ack.receipt_from_cli_json(_read(cap), rc, want_type=None,
                                                stream_terminal=ack.TERMINAL_NOT_PROVIDED)
            delivered, detail = ack.judge_notification_receipt(receipt, rc, request_id)
            detail = "%s %s" % (detail, security.redact(receipt.detail or ""))
        self._record_notify_ack(request_id, delivered, detail)
        self.trace.emit({"ev": "failure_notified", "request_id": request_id,
                         "failure_class": failure_class, "delivered": delivered, "detail": detail})
        return {"request_id": request_id, "notified": delivered, "detail": detail, "note_file": path}

    def notify_delivered(self, request_id: str) -> bool:
        """**这一笔**有没有送到人：只认指名到这笔的送达回执，不认"全局通道开着"。"""
        p = os.path.join(self.home, NOTIFY_ACK_FILE)
        if not os.path.isfile(p):
            return False
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln.startswith("{"):
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(d, dict) and str(d.get("rid")) == str(request_id) \
                        and d.get("delivered") is True:
                    return True
        return False

    # ---- 内部：三腿 ----------------------------------------------------------

    def _deliver_request(self, db, rec) -> dict:
        env = protocol.Envelope.from_dict(rec["envelope"])
        env, adapter, handle = self._bind_receiver(rec, env)
        before = rec["state"]
        if before != protocol.ST_DELIVERING:
            # 先落成 DELIVERING 再过凭证闸：闸门是"这一腿投不出去"的原因，
            # 而 CREATED→DELIVERY_RETRY 是跳格（协议里 CREATED 只能进队或开投）。
            rec["state"] = protocol.next_state(before, protocol.ST_DELIVERING)
            self._save(db)
            self.trace.emit(_ids(env) | {"ev": "delivering", "from_state": before,
                                         "to_state": protocol.ST_DELIVERING,
                                         "adapter": adapter.name})
        ok, why = adapter.check_credential()
        if not ok:
            self._fail(db, rec, "CREDENTIAL_UNAVAILABLE", why)
            return {"status": "blocked", "state": rec["state"], "failure_class": "CREDENTIAL_UNAVAILABLE",
                    "why": why, "adapter": adapter.name}
        rec["delivery"]["request"]["attempts"] += 1
        handle.attempt = rec["delivery"]["request"]["attempts"]
        rec["handles"]["receiver"] = handle.to_dict()
        rec["nonce"] = rec["nonce"] or new_nonce()
        rec["envelope"]["metadata"]["bind_nonce"] = rec["nonce"]
        env = protocol.Envelope.from_dict(rec["envelope"])
        sent_text = adapter.render(env)
        rec["request_sha256"] = hashlib.sha256(sent_text.encode("utf-8")).hexdigest()
        self._save(db)
        self.trace.emit(_ids(env) | {"ev": "delivering", "from_state": before,
                                     "to_state": protocol.ST_DELIVERING,
                                     "attempt": rec["delivery"]["request"]["attempts"],
                                     "adapter": adapter.name, "request_sha256": rec["request_sha256"]})
        try:
            handle = adapter.send(handle, env, sent_text)
        except Exception as exc:
            return self._on_failure(db, rec, "request", exc)
        rec["handles"]["receiver"] = handle.to_dict()
        caps = adapter.capabilities()
        if caps.get("split_send_receive", True):
            rec["state"] = protocol.next_state(rec["state"], protocol.ST_DELIVERED)
            self.trace.emit(_ids(env) | {"ev": "delivered", "to_state": protocol.ST_DELIVERED,
                                         "adapter": adapter.name})
        else:
            rec["state"] = protocol.next_state(rec["state"], protocol.ST_STARTED)
            self.trace.emit(_ids(env) | {"ev": "started", "to_state": protocol.ST_STARTED,
                                         "adapter": adapter.name, "collapsed_delivery_start": True})
        self._save(db)
        return {"status": "delivered", "state": rec["state"], "adapter": adapter.name}

    def _await_or_respond(self, db, rec) -> dict:
        env = protocol.Envelope.from_dict(rec["envelope"])
        env, adapter, handle = self._bind_receiver(rec, env)
        try:
            r = adapter.receive(handle, env, float(self.cfg.get("receive_timeout_s", 600)))
        except Exception as exc:
            return self._on_failure(db, rec, "request", exc)
        if r.detail == "IN_FLIGHT" or r.returncode is None:
            # 落盘件在、退出码侧件还没有 ⇒ **先问孩子是活着还是读不出死活**，只有确认不在了才谈失败。
            # 真腿第一跑实测：网络重连把一轮拖过等待窗口，旧代码把"还没退"读成"退了且失败"
            # ⇒ 下一拍重新入队＋再 exec resume，同一请求被叫醒两次（请求腿 attempts 1→2 是它的指纹）。
            ps = adapter.status(handle)
            if r.detail == "IN_FLIGHT" or ps in ("alive", "unknown"):
                rec["handles"]["receiver"] = handle.to_dict()
                self.trace.emit(_ids(env) | {"ev": "in_flight", "proc": ps,
                                             "note": "到点不杀：这一腿仍在飞（或读不出死活），"
                                                     "下一拍按同一枚绑定串收回，不重派"})
                self._save(db)
                return {"status": "in_flight", "state": rec["state"], "proc": ps}
        handle.attempt = rec["delivery"]["request"]["attempts"] or 1
        require_terminal = bool(adapter.capabilities().get("terminal_evidence"))
        # 这一腿收的是 **答复（HANDOFF_RESULT）**，不是确认行：要归因（绑定串）＋可信收口，
        # 不要求 ACKED——ACKED 那一行属于结果腿（发起方确认自己收到了回复）。
        acked, code, why = ack.judge_ack(r, handle.provider_session_id, rec["nonce"],
                                         require_terminal=require_terminal,
                                         require_ack_line=False)
        # "除协议两行之外还有没有正文"记成**观测**，不当送达闸：
        # 规定的答复就是那两行，再要求"更多字"就是桥在判内容质量——那是 §5 明令不做的判据
        # （旧内核 MIN_BODY=60 同一族，审计里已判 REMOVE）。想要更严的答复形状，
        # 由发送方在自己的话术／结果谓词里要求，不由桥代做。
        ok_shape, shape_why = ack.judge_result_shape(r.body or "")
        rec["result_body_only_protocol"] = (not ok_shape)
        if not acked:
            return self._on_failure(db, rec, "request", code, why, receipt=r)
        rec["handles"]["receiver"] = handle.to_dict()
        self._register_result(db, rec, env, receipt=r)
        self._save(db)
        return {"status": "responded", "state": rec["state"], "result_sha256": rec["result"]["sha256"]}

    def _deliver_result(self, db, rec) -> dict:
        env = protocol.Envelope.from_dict(rec["envelope"])
        if not rec.get("result", {}).get("sha256"):
            raise TransportError("NO_RESULT_TO_DELIVER", rec["request_id"])
        sender_env, adapter, handle = self._bind_sender(rec, env)
        ok, why = adapter.check_credential()
        if not ok:
            self._fail(db, rec, "CREDENTIAL_UNAVAILABLE", why)
            return {"status": "blocked", "state": rec["state"], "failure_class": "CREDENTIAL_UNAVAILABLE",
                    "why": why}
        rec["delivery"]["result"]["attempts"] += 1
        handle.attempt = rec["delivery"]["result"]["attempts"]
        rec["handles"]["sender"] = handle.to_dict()
        text = self._result_text(rec)
        reply = self._render_result(rec, env, text)
        before = rec["state"]
        if before in (protocol.ST_RESPONDED, protocol.ST_DELIVERY_FAILED):
            rec["state"] = protocol.next_state(before, protocol.ST_DELIVERY_RETRY)
            self.trace.emit(_ids(env) | {"ev": "result_retry_of_delivery",
                                         "from_state": before, "to_state": protocol.ST_DELIVERY_RETRY,
                                         "note": "投这条**已生成的回复**，不重跑对侧任务"})
        rec["state"] = protocol.next_state(rec["state"], protocol.ST_DELIVERING)
        self._save(db)
        self.trace.emit(_ids(env) | {"ev": "result_delivering", "from_state": before,
                                     "to_state": protocol.ST_DELIVERING,
                                     "attempt": rec["delivery"]["result"]["attempts"],
                                     "result_sha256": rec["result"]["sha256"],
                                     "adapter": adapter.name})
        try:
            handle = adapter.send(handle, sender_env, reply)
            r = adapter.receive(handle, sender_env, float(self.cfg.get("receive_timeout_s", 600)))
        except Exception as exc:
            return self._on_failure(db, rec, "result", exc)
        rec["handles"]["sender"] = handle.to_dict()
        if r.detail == "IN_FLIGHT" or (r.returncode is None
                                       and adapter.status(handle) in ("alive", "unknown")):
            # 确认那一腿同理：对方还在想，不判失败、不重投（否则一次确认变两次打扰）
            self.trace.emit(_ids(env) | {"ev": "result_in_flight",
                                         "attempt": rec["delivery"]["result"]["attempts"],
                                         "note": "确认那一腿仍在飞 ⇒ 原地等，不重投"})
            self._save(db)
            return {"status": "in_flight", "state": rec["state"], "leg": "result"}
        require_terminal = bool(adapter.capabilities().get("terminal_evidence"))
        acked, code, why = ack.judge_ack(r, handle.provider_session_id, rec["nonce"],
                                         require_terminal=require_terminal)
        if not acked:
            return self._on_failure(db, rec, "result", code, why, receipt=r)
        rec["state"] = protocol.next_state(rec["state"], protocol.ST_ACKED)
        rec["result"]["acked_at"] = protocol.utc_now()
        rec["result"]["ack_reason"] = code
        rec["history"].append({"state": protocol.ST_ACKED, "at": protocol.utc_now(), "note": "ok"})
        self._save(db)
        self.trace.emit(_ids(env) | {"ev": "acked", "to_state": protocol.ST_ACKED,
                                     "result_sha256": rec["result"]["sha256"],
                                     "ack_evidence": r.to_dict()})
        return {"status": "acked", "state": protocol.ST_ACKED}

    # ---- 内部：失败与预算 ----------------------------------------------------

    def _on_failure(self, db, rec, leg, exc_or_code, why="", receipt=None) -> dict:
        code, note = self._classify(exc_or_code, why)
        if receipt is not None:
            rec["last_receipt"] = receipt.to_dict()
        return self._fail(db, rec, code, note, leg=leg)

    #: 这几类失败**不自动重投**：自愈不了的毛病，重投只会把同一笔反复推给队列
    NO_AUTO_RETRY = ("CREDENTIAL_UNAVAILABLE", "SESSION_LOST", "UNKNOWN")

    def _fail(self, db, rec, failure_class, why, leg="request") -> dict:
        d = rec["delivery"][leg]
        rec["failure_class"] = failure_class
        rec["reason"] = security.redact(why)
        before = rec["state"]
        park_now = failure_class in self.NO_AUTO_RETRY
        if park_now:
            self.trace.emit({"ev": "auto_retry_withheld", "request_id": rec["request_id"],
                             "failure_class": failure_class,
                             "note": "这类失败自愈不了（认证／会话／读数未知）⇒ 停手等人，不自动重投"})
        if park_now or d["attempts"] >= d["max"]:
            rec["state"] = (before if before == protocol.ST_DELIVERY_FAILED
                            else protocol.next_state(before, protocol.ST_DELIVERY_FAILED))
            self.trace.emit({"ev": "delivery_failed", "request_id": rec["request_id"],
                             "from_state": before, "to_state": protocol.ST_DELIVERY_FAILED,
                             "failure_class": failure_class, "attempts": d["attempts"],
                             "note": "投递预算用尽 ⇒ 停手等人；对侧结果若已在案，绝不重跑"})
            self.notify(rec["request_id"], failure_class, why)
        else:
            rec["state"] = protocol.next_state(before, protocol.ST_DELIVERY_RETRY)
            self.trace.emit({"ev": "delivery_retry", "request_id": rec["request_id"],
                             "from_state": before, "to_state": protocol.ST_DELIVERY_RETRY,
                             "failure_class": failure_class, "attempts": d["attempts"],
                             "note": "只重投消息（同一绑定串），不重跑对侧任务"})
        rec["history"].append({"state": rec["state"], "at": protocol.utc_now(), "note": rec["reason"]})
        self._save(db)
        # status 与 state 同值一起给：调用方拿哪个都不该拿到不同的话
        return {"status": rec["state"], "state": rec["state"],
                "failure_class": failure_class, "why": rec["reason"],
                "attempts": d["attempts"], "max": d["max"]}

    # ---- 内部：结果与工件 ----------------------------------------------------

    def _register_result(self, db, rec, env, receipt=None) -> str:
        text = (receipt.body if receipt is not None else self._capture_text(rec, env)) or ""
        path = self._result_path(rec["request_id"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        security.atomic_write(path, text)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec["result"] = {"sha256": sha, "file": path, "bytes": len(text.encode("utf-8")),
                         "responded_at": protocol.utc_now()}
        if rec["state"] == protocol.ST_DELIVERED:
            # 拿到答复这件事本身证明"对侧这一轮跑过"：先补 STARTED 再进 RESPONDED，
            # 不许跳格（跳格＝状态机不再可审计，DELIVERED 与 STARTED 的区别会被抹平）
            rec["state"] = protocol.next_state(rec["state"], protocol.ST_STARTED)
            self.trace.emit(_ids(env) | {"ev": "started_by_reply", "to_state": protocol.ST_STARTED,
                                         "note": "由收回的答复反证这一轮跑过"})
        rec["state"] = protocol.next_state(rec["state"], protocol.ST_RESPONDED)
        self.trace.emit(_ids(env) | {"ev": "responded", "to_state": protocol.ST_RESPONDED,
                                     "result_sha256": sha,
                                     "result_body_only_protocol": bool(
                                         rec.get("result_body_only_protocol")),
                                     "artifact_refs": [protocol.ArtifactRef(
                                         type="file", ref=path, digest=sha).to_dict()]})
        self._save(db)
        return sha

    def _result_usable(self, rec) -> bool:
        r = rec.get("result") or {}
        p, sha = r.get("file"), r.get("sha256")
        if not p or not sha or not os.path.isfile(p):
            return False
        try:
            with open(p, encoding="utf-8") as fh:
                return hashlib.sha256(fh.read().encode("utf-8")).hexdigest() == sha
        except OSError:
            return False

    def _result_text(self, rec) -> str:
        with open(rec["result"]["file"], encoding="utf-8") as fh:
            return fh.read()

    def _render_result(self, rec, env, text) -> str:
        """把结果送回发起方，并要求逐字回出绑定行＋确认行（协议 §4.2 的两行约定）。"""
        return ("这是请求号 %s 的回复（消息 %s）。\n"
                "回复原文：\n\n%s\n\n"
                "请**单独占一行、逐字**回出下面两行，缺任一行都不算收到：\n%s: %s\n%s\n" % (
                    env.request_id, env.message_id, text,
                    ack.BIND_PREFIX, rec["nonce"], ack.ACK_TOKEN))

    def _capture_text(self, rec, env) -> str:
        adapter, handle = self._receiver_parts(rec)
        cap = getattr(adapter, "_capture_path", None)
        if not callable(cap):
            return ""
        try:
            with open(cap(handle, env.message_id), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    # ---- 内部：会话与适配器绑定 ----------------------------------------------

    def _bind_receiver(self, rec, env):
        return self._bind(rec, env, side="receiver")

    def _bind_sender(self, rec, env):
        return self._bind(rec, env, side="sender")

    def _bind(self, rec, env, side: str):
        logical = env.receiver if side == "receiver" else env.sender
        if side == "sender":
            logical = env.sender
        existing = rec.get("handles", {}).get(side if side == "receiver" else "sender")
        sess = None
        try:
            sess = self.sessions.get(logical)
        except Exception:
            sess = None
        if sess is None:
            raise TransportError("NO_SUCH_SESSION",
                                 "%s 侧逻辑会话 %r 没登记；先跑 q2c sessions bind" % (side, logical))
        name = (existing or {}).get("adapter") or sess.adapter
        adapter = adapters.build(name, self.cfg.adapter_config(name))
        handle = _handle_from(sess, existing)
        handle.role = sess.role            # 以会话册为准，不被旧台账里的空值带偏
        handle.workspace_ref = (handle.workspace_ref or envelope_field(env, "workspace_ref"))
        handle = adapter.resume(handle) if handle.provider_session_id else adapter.start(handle)
        if sess.current() != handle.provider_session_id:
            self.sessions.rebind(sess.session_id, handle.provider_session_id,
                                 "transport 绑定刷新（provider 号变了，逻辑号不变）")
        env = _with_ids(env, sess, side, handle)
        rec["handles"][side if side == "receiver" else "sender"] = handle.to_dict()
        return env, adapter, handle

    def _proc_state(self, rec) -> str:
        h = (rec.get("handles") or {}).get("receiver") or {}
        if not h.get("pid"):
            return "never_started"
        return security.pid_state(h.get("pid"), h.get("pid_start"))

    def _capture_state(self, rec, env) -> str:
        try:
            adapter, handle = self._receiver_parts(rec)
        except Exception:
            return "absent"
        cap = getattr(adapter, "_capture_path", None)
        if not callable(cap):
            return "absent"
        p = cap(handle, env.message_id)
        if not os.path.exists(p):
            return "absent"
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            return "unreadable"
        r = ack.receipt_from_cli_json(raw, None,
                                      stream_terminal=ack.scan_terminal(raw)
                                      if adapter.capabilities().get("terminal_evidence") else "")
        require_terminal = bool(adapter.capabilities().get("terminal_evidence"))
        ok, _code, _why = ack.judge_ack(r, handle.provider_session_id, rec["nonce"],
                                        require_terminal=require_terminal)
        return "usable" if ok else "present_untrusted"

    def _receiver_parts(self, rec):
        env = protocol.Envelope.from_dict(rec["envelope"])
        h = (rec.get("handles") or {}).get("receiver") or {}
        name = h.get("adapter") or self.sessions.get(env.receiver).adapter
        adapter = adapters.build(name, self.cfg.adapter_config(name))
        from .adapters.base import Handle
        handle = Handle(adapter=name, provider_session_id=h.get("provider_session_id", ""),
                        workspace_ref=h.get("workspace_ref", ""), nonce=rec.get("nonce", ""),
                        pid=h.get("pid"), pid_start=h.get("pid_start", ""))
        return adapter, handle

    def _cancel_inflight(self, rec) -> bool:
        try:
            adapter, handle = self._receiver_parts(rec)
            return bool(adapter.cancel(handle))
        except Exception:
            return False

    def _result_pending(self, rec) -> bool:
        """有结果、且这条结果还没被对方确认 ⇒ 结果腿待投。"""
        r = rec.get("result") or {}
        return bool(r.get("sha256")) and not r.get("acked_at")

    def _expire_if_due(self, db, rec) -> bool:
        if rec["state"] in protocol.TERMINAL_STATES:
            return False
        env = protocol.Envelope.from_dict(rec["envelope"])
        if not env.is_expired():
            return False
        before = rec["state"]
        rec["state"] = protocol.next_state(before, protocol.ST_EXPIRED)
        rec["history"].append({"state": protocol.ST_EXPIRED, "at": protocol.utc_now(),
                               "note": "到 expires_at 仍未 ACKED"})
        self.trace.emit({"ev": "expired", "request_id": rec["request_id"], "from_state": before,
                        "to_state": protocol.ST_EXPIRED, "expires_at": env.expires_at})
        return True

    def _set_state(self, db, rec, target, note) -> None:
        before = rec["state"]
        if before == target:
            # 同态不算迁移，也不算翻案：恢复阶梯被反复跑时（人或工具又调了一次 recover）
            # 必须幂等，否则"已经处在目标态"会被判成非法迁移而把整个恢复打断。
            rec["history"].append({"state": target, "at": protocol.utc_now(), "note": note})
            return
        rec["state"] = protocol.next_state(before, target)
        rec["history"].append({"state": target, "at": protocol.utc_now(), "note": note})
        self.trace.emit({"ev": "state", "request_id": rec["request_id"], "from_state": before,
                         "to_state": target, "note": note})

    def _check_workspace(self, envelope: protocol.Envelope) -> None:
        """先查白名单，再查形状。

        两条规则的先后是实打实的：`allowed_workspaces` 就是"允许根外工作区"的**唯一**正当入口，
        若先按"必须落在 q2c 根内"拒掉，配置里明明写了的工作区永远进不来，
        那条白名单就成了摆设（摆设判据比没有判据更坏）。
        """
        allowed = self.cfg.get("allowed_workspaces") or []
        ws = envelope.workspace_ref
        flag_outside = bool(self.cfg.get("allow_outside_refs")
                            or self.cfg.get("allow_refs_outside_home"))
        if ws and allowed and not security.workspace_allowed(ws, allowed):
            # 白名单非空时，它就是工作区的边界：不在册 ⇒ WORKSPACE_NOT_ALLOWED。
            # 这条要**先**判，否则用户收到的是"不在 q2c 根内"，
            # 而正确答复是"把这条路加进 allowed_workspaces"——两者修法完全不同。
            raise TransportError("WORKSPACE_NOT_ALLOWED",
                                 "%s 不在册（在册：%s）" % (ws, ", ".join(allowed)))
        for field_name, value in envelope.path_fields():
            if not value:
                continue
            # 白名单在册的工作区允许在根外（那正是白名单的用途）；其余情况仍守根内
            allow_outside = (flag_outside
                             or (field_name == "workspace_ref" and bool(allowed)))
            security.check_ref_path(field_name, value, self.home, allow_outside=allow_outside)

    def _classify(self, exc_or_code, why) -> tuple:
        if isinstance(exc_or_code, str):
            return exc_or_code, why or exc_or_code
        from .adapters.base import AdapterError
        if isinstance(exc_or_code, AdapterError):
            mapping = {"ENQUEUE_FAILED": "ADAPTER_UNAVAILABLE", "MAILBOX_ABSENT": "SESSION_LOST",
                       "CAPTURE_ABSENT": "NO_TERMINAL_EVENT", "LIVE_NOT_AUTHORIZED": "ADAPTER_UNAVAILABLE"}
            return mapping.get(exc_or_code.code, "ADAPTER_UNAVAILABLE"), exc_or_code.detail or why
        if isinstance(exc_or_code, protocol.ProtocolError):
            return "ATTRIBUTION_FAILED", exc_or_code.detail or why
        return "UNKNOWN", security.redact(str(exc_or_code) or why)

    def _record_notify_ack(self, rid, delivered, detail) -> None:
        p = os.path.join(self.home, NOTIFY_ACK_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        row = {"rid": rid, "delivered": bool(delivered), "detail": security.redact(detail),
               "at": protocol.utc_now(), "pid": os.getpid()}
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _result_path(self, rid: str) -> str:
        return os.path.join(self.home, "results", "%s%s" % (rid, RESULT_SUFFIX))


def envelope_field(env, name):
    return getattr(env, name, "") if env is not None else ""


def _ids(env: protocol.Envelope) -> dict:
    return {"message_id": env.message_id, "request_id": env.request_id,
            "correlation_id": env.correlation_id, "session_id": env.session_id}


def _handle_from(sess, existing) -> "adapters.base.Handle":
    from .adapters.base import Handle
    if existing:
        return Handle(adapter=existing.get("adapter", sess.adapter),
                      role=existing.get("role") or sess.role,
                      attempt=int(existing.get("attempt") or 1),
                      provider_session_id=existing.get("provider_session_id", ""),
                      workspace_ref=existing.get("workspace_ref", ""),
                      nonce=existing.get("nonce", ""),
                      pid=existing.get("pid"), pid_start=existing.get("pid_start", ""))
    return Handle(adapter=sess.adapter, role=sess.role,
                  provider_session_id=sess.current(), nonce="")


def _with_ids(env, sess, side, handle) -> protocol.Envelope:
    """把 provider 号写进 metadata（**信封本身不变**：provider 号不是协议字段）。"""
    doc = env.to_dict()
    doc.setdefault("metadata", {})["%s_provider_session_id" % side] = handle.provider_session_id
    return protocol.Envelope.from_dict(doc)


def _inner_text(receipt) -> str:
    extra = getattr(receipt, "extra", {}) or {}
    return str(extra.get("result_text") or extra.get("inner") or receipt.detail or "")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _spawn_cap(argv, env, capture, timeout):
    from .adapters import _spawn
    return _spawn.spawn(argv, env, capture, timeout)
