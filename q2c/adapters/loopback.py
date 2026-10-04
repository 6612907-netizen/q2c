#!/usr/bin/env python3
"""`loopback` 适配器：本机文件信箱 ＋ **真子进程**应答器（零模型调用、零外部依赖）。

它在产品里的位置有两个，都不是"临时凑数"：

1. **Quick Start 的车**：新用户装完 q2c，在既没有 Codex 也没有 Qoder 的机器上，
   要能真跑一遍 send → receive → ACK → trace。做不到这一点的 README 是废纸。
2. **判据的车**：到点不杀、迟到答复、失败回执不被覆盖、进程组清理、退出码说话——
   这些性质只有用真进程才测得出来（`q2c/stub_responder.py` 提供八种回执形状）。

它**不是**真对侧：`capabilities()["live"] is False`、`terminal_evidence is False`，
永不叫外部 CLI，也不许被生产配置当默认适配器（`q2c doctor` 把它标成 DEMO）。

`receive()` 的两拍语义（照搬已验证内核，别改成"一步到位"）：
  · 第一次调用 ⇒ 起应答器进程（自成进程组）、登记 pid＋启动时刻、等到超时；
  · 到点**不杀**，返回"仍在飞"的 Receipt（`detail=IN_FLIGHT`）；
  · 之后对同一 handle 再调 ⇒ 从落盘件收这一腿，用**同一枚**绑定串归因。
超时即杀并记失败，实测代价是同一请求两份对象、两枚绑定串、两条互相打脸的答复
（在册 R9 第 7 条、R10 时间线）。

起进程与收落盘这两段**一律走 `adapters/_spawn.py`**，不在本文件里再写一套包装：
两适配器各写一份"退出码怎么取"＝双真源，漂了不会自己红。
"""

from __future__ import annotations

import os
import sys
import uuid

from . import _spawn
from .base import STATUS_ALIVE, STATUS_NEVER, STATUS_UNKNOWN, Adapter, AdapterError, Handle, register
from .. import ack, protocol, security

NAME = "loopback"
from ..stub_responder import MODES   # 单一真源：名单只在桩件里有一份


@register
class LoopbackAdapter(Adapter):
    name = NAME

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.mailbox = os.path.join(self.home, "mailbox")

    # ---- 契约七枚 ----------------------------------------------------------

    def capabilities(self) -> dict:
        return {
            "name": NAME,
            "live": False,
            "terminal_evidence": False,
            "split_send_receive": True,
            "credential_kind": "",
            "supports_cancel": True,
            "evidence": "本机信箱文件＋子进程退出码侧件（无外部 CLI 证据）",
            "modes_by_role": dict(self.config.get("modes_by_role") or {}),
            "note": "演示与判据用车；不是真对侧，q2c doctor 标 DEMO",
        }

    def start(self, handle: Handle) -> Handle:
        pid = "lb-" + uuid.uuid4().hex[:12]
        os.makedirs(os.path.join(self.mailbox, pid, "inbox"), exist_ok=True)
        handle.provider_session_id = pid
        return handle

    def resume(self, handle: Handle) -> Handle:
        box = os.path.join(self.mailbox, handle.provider_session_id or "", "inbox")
        if not os.path.isdir(box):
            raise AdapterError("MAILBOX_ABSENT", box)
        return handle

    def send(self, handle: Handle, envelope: protocol.Envelope, text=None) -> Handle:
        if not handle.provider_session_id:
            handle = self.start(handle)
        inbox = os.path.join(self.mailbox, handle.provider_session_id, "inbox")
        os.makedirs(inbox, exist_ok=True)
        body = text if text is not None else self.render(envelope)
        security.atomic_write(os.path.join(inbox, "%s.txt" % envelope.message_id), body)
        handle.extra["last_message_id"] = envelope.message_id
        return handle

    def receive(self, handle: Handle, envelope: protocol.Envelope,
                timeout_s: float = 20.0) -> ack.Receipt:
        cap = self._capture_path(handle, envelope.message_id)
        if os.path.isfile(cap) or os.path.isfile(_spawn.rc_path_of(cap)):
            # 已有落盘 ⇒ 这一腿早就结束了（多半是迟到那一拍）。只读不改，绝不重跑对侧。
            return self._collect(handle, cap)
        by_role = self.config.get("modes_by_role") or {}
        mode = ((by_role.get(handle.role) if isinstance(by_role, dict) else "")
                or self.config.get("mode") or os.environ.get("Q2C_LB_MODE") or "ack").strip()
        if mode not in MODES:
            raise AdapterError("UNKNOWN_LB_MODE", "=%r（在册：%s）" % (mode, ",".join(MODES)))
        self.resume(handle)
        env = security.strip_runtime_env(dict(os.environ))
        env.update({
            "Q2C_LB_MODE": mode,
            "Q2C_LB_SESSION": handle.provider_session_id,
            "Q2C_LB_BIND": envelope.metadata.get("bind_nonce", ""),
            "Q2C_LB_RID": envelope.request_id,
            "Q2C_LB_SLEEP": str(self.config.get("sleep_s", "") or ""),
            "Q2C_LB_RC": str(self.config.get("rc", "") or ""),
            "Q2C_LB_CALLS": (self.config.get("calls_log") or os.environ.get("Q2C_LB_CALLS", "") or ""),
            "PYTHONPATH": os.pathsep.join(
                [os.path.dirname(os.path.dirname(security.__file__)),
                 env.get("PYTHONPATH", "")]).rstrip(os.pathsep),
        })
        # 请求号同时作为**命令行参数**带上：`ps` 里能看见它。
        # 只靠环境变量认不出自己的子进程（命令行里没有根路径），
        # 那样写出来的进程扫描格会永远返回空表——判据咬空，比没有判据更坏。
        argv = [sys.executable, "-m", "q2c.stub_responder", "--rid", envelope.request_id]
        phase, rc, pid, pid_start = _spawn.spawn(argv, env, cap, timeout_s)
        handle.pid, handle.pid_start = pid, pid_start
        handle.extra["capture"] = cap
        if phase == _spawn.IN_FLIGHT:
            return ack.Receipt(returncode=None, detail=_spawn.IN_FLIGHT,
                               terminal=ack.TERMINAL_NOT_PROVIDED,
                               extra={"pid": pid, "capture": cap})
        return self._receipt(handle, cap, rc)

    def status(self, handle: Handle) -> str:
        if not handle.pid:
            return STATUS_NEVER
        if not handle.pid_start:
            return STATUS_UNKNOWN
        return security.pid_state(handle.pid, handle.pid_start)

    def cancel(self, handle: Handle) -> bool:
        if not handle.pid:
            return False
        outcome = security.kill_group(handle.pid)
        return outcome in ("gone-after-term", "gone-after-kill", "killed-group", "already-gone",
                           "reaped-after-term", "reaped-after-kill")

    # ---- 恢复用：不重投的前提下把在飞那一腿收回来 ----------------------------

    def inflight_messages(self, handle: Handle) -> list:
        d = os.path.dirname(self._capture_path(handle, "x"))
        if not os.path.isdir(d):
            return []
        return sorted(fn[:-len(".out")] for fn in os.listdir(d) if fn.endswith(".out"))

    def has_capture(self, handle: Handle, message_id: str) -> bool:
        return os.path.isfile(self._capture_path(handle, message_id))

    def collect_late(self, handle: Handle, envelope: protocol.Envelope) -> ack.Receipt:
        """第二拍：进程可能已经自己跑完了，从落盘件＋退出码侧件收回。"""
        cap = self._capture_path(handle, envelope.message_id)
        rc = _spawn.read_rc(cap)
        if rc is None and self.status(handle) == STATUS_ALIVE:
            return ack.Receipt(returncode=None, detail=_spawn.IN_FLIGHT,
                               terminal=ack.TERMINAL_NOT_PROVIDED, extra={"pid": handle.pid})
        return self._receipt(handle, cap, rc)

    # ---- 内部 --------------------------------------------------------------

    def _capture_path(self, handle: Handle, message_id: str) -> str:
        # 文件名里有 attempt：第 2 次投递必须起新进程、写新件，
        # 不能因为"这个 message_id 已经有落盘件"就把上一拍的失败回执当本轮答复读
        return os.path.join(self.home, "state", "inflight",
                            handle.provider_session_id or "anon",
                            "%s.a%s.out" % (message_id, getattr(handle, "attempt", 1) or 1))

    def _collect(self, handle, cap) -> ack.Receipt:
        try:
            rc, _raw = _spawn.collect(cap)
        except FileNotFoundError:
            raise AdapterError("CAPTURE_ABSENT", cap)
        return self._receipt(handle, cap, rc)

    def _receipt(self, handle, cap, rc) -> ack.Receipt:
        """落盘件 → Receipt。三条诚实性规矩（都是真出过的事）：

        · 退出码侧件没写出来 ⇒ rc 记 `None`，**不是 0**；读不到退出码就不是"跑成功了"；
        · 侧件内容不是整数 ⇒ 同样 `None`，detail 里写明按未知处理；
        · 落盘件不在 ⇒ 抛 `CAPTURE_ABSENT`，不返回空回执（空回执会被读成"对方回了个空"）。
        """
        try:
            side_rc, raw = _spawn.collect(cap)
        except FileNotFoundError:
            raise AdapterError("CAPTURE_ABSENT", cap)
        use_rc = rc if rc is not None else side_rc
        r = ack.receipt_from_cli_json(raw, use_rc, stream_terminal=ack.TERMINAL_NOT_PROVIDED)
        if use_rc is None:
            r.detail = (r.detail + "；" if r.detail else "") + _spawn.NO_RC
        r.extra = {"capture": cap, "pid": handle.pid}
        return r
