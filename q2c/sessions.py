#!/usr/bin/env python3
"""逻辑会话：`q2c_session_id` ↔ provider 会话号的映射。

要解决的问题（任务书 §7）：provider 重启、线程被回收、换了号 ⇒
**这次交接的身份不能变**。旧内核里那一枚 `state/review_session_id` 文件只有一个作用：
"这次用哪条线程去发"。它一换，历史账就查不回去了，而且曾因此把上一轮的上下文当本轮事实。

三条规矩：

1. **逻辑号只增不换**。provider 号换了 ⇒ 往 `bindings` 里追加一条，旧绑定留档
   （重放旧消息时要能取到"当时那一枚"，不是永远用最新的）。
2. **不同用途不许共用一条逻辑会话**。审核腿与验收腿共用线程那次，
   真机上答的是上一笔的 SHA——字段闸门全过、证据自相矛盾。`role` 不同 ⇒ 不同会话。
3. **换绑必须有理由**。`rebind()` 要写 `reason`；空理由拒绝（不静默搬家）。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from . import protocol, security

ROLES = ("sender", "receiver", "observer", "notify")


class SessionError(Exception):
    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


@dataclass
class Session:
    session_id: str
    role: str
    adapter: str
    bindings: list        # [{provider_session_id, since, until, reason}]
    label: str = ""

    def current(self) -> str:
        for b in reversed(self.bindings):
            if not b.get("until"):
                return b.get("provider_session_id", "")
        return ""

    def provider_at(self, when_iso: str) -> str:
        """取 `when_iso` 那一刻生效的那枚 provider 号（重放按时间轴，不用最新值）。"""
        winner = ""
        for b in self.bindings:
            if str(b.get("since") or "") <= when_iso:
                winner = b.get("provider_session_id", "")
        return winner


class SessionStore:
    def __init__(self, home: str):
        self.path = os.path.join(home, "state", "sessions.json")

    def _read(self) -> dict:
        if not os.path.isfile(self.path):
            return {"sessions": {}}
        try:
            with open(self.path, encoding="utf-8") as fh:
                doc = json.loads(fh.read())
        except (OSError, ValueError):
            # 读不动 ⇒ 抛，不返回空表。空表会让"会话不存在"与"会话册坏了"读成同一句话。
            raise SessionError("SESSIONS_UNREADABLE", self.path)
        if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), dict):
            raise SessionError("SESSIONS_UNREADABLE", "顶层形状不对：%s" % self.path)
        return doc

    def _write(self, doc: dict) -> None:
        security.atomic_write(self.path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")

    def create(self, role: str, adapter: str, provider_session_id: str = "",
               label: str = "") -> Session:
        if role not in ROLES:
            raise SessionError("UNKNOWN_ROLE", repr(role))
        if not (adapter or "").strip():
            raise SessionError("ADAPTER_REQUIRED", "建逻辑会话必须指明适配器")
        doc = self._read()
        seed = "%s|%s|%s" % (role, adapter, label or protocol.new_session_id())
        sid = "qs-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:18]
        if sid in doc["sessions"]:
            raise SessionError("SESSION_EXISTS", sid)
        rec = {"session_id": sid, "role": role, "adapter": adapter, "label": label, "bindings": []}
        if provider_session_id:
            rec["bindings"].append({"provider_session_id": provider_session_id,
                                    "since": protocol.utc_now(), "until": "", "reason": "initial"})
        doc["sessions"][sid] = rec
        self._write(doc)
        return Session(**rec)

    def get(self, session_id: str) -> Session:
        doc = self._read()
        rec = doc["sessions"].get(session_id)
        if not rec:
            raise SessionError("NO_SUCH_SESSION", session_id)
        return Session(session_id=rec["session_id"], role=rec["role"], adapter=rec["adapter"],
                       bindings=list(rec.get("bindings") or []), label=rec.get("label", ""))

    def find(self, adapter: str, role: str, label: str = "") -> Session | None:
        doc = self._read()
        for rec in doc["sessions"].values():
            if rec.get("adapter") == adapter and rec.get("role") == role and (
                    not label or rec.get("label") == label):
                return Session(session_id=rec["session_id"], role=rec["role"], adapter=rec["adapter"],
                               bindings=list(rec.get("bindings") or []), label=rec.get("label", ""))
        return None

    def rebind(self, session_id: str, provider_session_id: str, reason: str) -> Session:
        """换绑：旧绑定关闭、新绑定追加。逻辑号不变 ⇒ 历史交接的身份不变。"""
        if not (reason or "").strip():
            raise SessionError("REBIND_REASON_REQUIRED",
                               "换 provider 会话号必须写理由（不静默搬家）")
        if not (provider_session_id or "").strip():
            raise SessionError("EMPTY_PROVIDER_ID", "空线程号不许当绑定值（曾造成「看着跑完了」的假跑）")
        doc = self._read()
        rec = doc["sessions"].get(session_id)
        if not rec:
            raise SessionError("NO_SUCH_SESSION", session_id)
        now = protocol.utc_now()
        for b in rec["bindings"]:
            if not b.get("until"):
                b["until"] = now
        rec["bindings"].append({"provider_session_id": provider_session_id.strip(),
                                "since": now, "until": "", "reason": reason.strip()})
        self._write(doc)
        return Session(session_id=rec["session_id"], role=rec["role"], adapter=rec["adapter"],
                       bindings=list(rec["bindings"]), label=rec.get("label", ""))

    def bind_if_absent(self, session_id: str, provider_session_id: str, reason: str = "first-bind") -> Session:
        rec = self._read()["sessions"].get(session_id)
        if not rec:
            raise SessionError("NO_SUCH_SESSION", session_id)
        if rec["bindings"]:
            return self.get(session_id)
        return self.rebind(session_id, provider_session_id, reason)

    def which(self, adapter: str, provider_session_id: str) -> Session | None:
        """按 provider 号反查逻辑会话（包含历史绑定）。"""
        doc = self._read()
        for rec in doc["sessions"].values():
            if rec.get("adapter") != adapter:
                continue
            for b in rec.get("bindings") or []:
                if b.get("provider_session_id") == provider_session_id:
                    return Session(session_id=rec["session_id"], role=rec["role"],
                                   adapter=rec["adapter"], bindings=list(rec["bindings"]),
                                   label=rec.get("label", ""))
        return None

    def list_all(self) -> list:
        doc = self._read()
        return [Session(session_id=r["session_id"], role=r["role"], adapter=r["adapter"],
                        bindings=list(r.get("bindings") or []), label=r.get("label", ""))
                for r in doc["sessions"].values()]
