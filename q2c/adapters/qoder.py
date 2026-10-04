#!/usr/bin/env python3
"""Qoder 适配器（公开 CLI 档，`qoderclicn`）。

与 Codex 那一条最大的形状差：这一侧的公开 CLI 是**同步**的——一次调用既投递又答复。
所以：

  · `send()` 只把派发文落盘、不调用外部进程（能力位 `split_send_receive=False`）；
  · `receive()` 才是真正那一跳，`DELIVERED` 与 `STARTED` 之间在这条腿上**没有可观测边界**；
  · 传输层据此把两枚状态合并登记为一次迁移，并在 trace 里写明 `collapsed_delivery_start=true`
    ——**不假装**看见了起点。这一点是产品定义里"不把自述当执行事实"的同一条纪律。

SDK 入口变量必须在起子进程前剥掉（`QODER_AGENT_SDK_ENTRYPOINT` 那一族）：
被无头拉起的子 CLI 一旦以为自己还在 SDK 里，会要求 stream-json 并直接拒启，
表现是"叫起来就失败"，很容易被误读成对侧不肯回话。
"""

from __future__ import annotations

import os

from . import _spawn
from .base import STATUS_NEVER, STATUS_UNKNOWN, Adapter, AdapterError, Handle, register
from .. import ack, protocol, security

NAME = "qoder"
BIN = "qoderclicn"


@register
class QoderAdapter(Adapter):
    name = NAME

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.cmd = (self.config.get("cmd") or os.environ.get("Q2C_QODER_CMD") or BIN).strip()
        self.model = (self.config.get("model") or os.environ.get("Q2C_QODER_MODEL") or "").strip()
        self.timeout_s = float(self.config.get("timeout_s") or 600)

    def capabilities(self) -> dict:
        return {
            "name": NAME,
            "live": self.is_live(),
            "terminal_evidence": True,
            "split_send_receive": False,
            "credential_kind": "qoder",
            "supports_cancel": True,
            "transport": "公开 CLI（%s -p -r <会话号> -w <工作区> --output-format json）" % BIN,
            "evidence": "结构化 result 记录（is_error／subtype／session_id）＋退出码侧件",
            "not_used": ["Qoder 内部 SDK 入口（子进程里必须剥掉）", "任何凭据文件"],
            "limits": ["投递与开始之间无可观测边界，trace 记 collapsed_delivery_start=true"],
        }

    def is_live(self) -> bool:
        override = (self.config.get("cmd") or os.environ.get("Q2C_QODER_CMD") or "").strip()
        return not bool(override)

    def _require_live(self, what: str) -> None:
        if self.is_live() and os.environ.get("Q2C_LIVE", "").strip() != "1":
            raise AdapterError("LIVE_NOT_AUTHORIZED",
                               "%s 要真调 Qoder，必须显式设 Q2C_LIVE=1（当前：零调用）" % what)

    def start(self, handle: Handle) -> Handle:
        raise AdapterError(
            "QODER_START_UNSUPPORTED",
            "公开 CLI 没有零副作用新建会话的入口；请用 q2c sessions bind 登记已有会话号")

    def resume(self, handle: Handle) -> Handle:
        if not (handle.provider_session_id or "").strip():
            raise AdapterError("EMPTY_PROVIDER_ID", "Qoder 腿必须有会话号（不猜、不串到别人的会话）")
        return handle

    def send(self, handle: Handle, envelope: protocol.Envelope, text=None) -> Handle:
        body = text if text is not None else self.render(envelope)
        os.makedirs(self._dir(handle), exist_ok=True)
        security.atomic_write(self._sent_path(handle, envelope.message_id), body)
        handle.extra["sent_file"] = self._sent_path(handle, envelope.message_id)
        return handle

    def receive(self, handle: Handle, envelope: protocol.Envelope,
                timeout_s: float = None) -> ack.Receipt:
        self._require_live("receive")
        handle = self.resume(handle)
        cap = self._capture_path(handle, envelope.message_id)
        if os.path.isfile(cap) or os.path.isfile(_spawn.rc_path_of(cap)):
            return self._collect(handle, cap)
        sent = self._sent_path(handle, envelope.message_id)
        if not os.path.isfile(sent):
            raise AdapterError("SENT_FILE_ABSENT", "先 send 再 receive（派发文不在：%s）" % sent)
        with open(sent, encoding="utf-8") as fh:
            body = fh.read()
        argv = self._argv(["-p", "-r", handle.provider_session_id,
                           "--permission-mode", self.config.get("permission_mode", "auto"),
                           "--output-format", "json"])
        # 会话号是**按项目目录**解析的：不带 -w（或带错目录）就会报
        # "Invalid session identifier"——真腿第一跑正是这么失败的（不是认证问题，是找错目录）。
        ws = envelope.workspace_ref or handle.workspace_ref
        if ws:
            argv += ["-w", ws]
        if self.model:
            argv += ["--model", self.model]
        argv += [body]
        env = security.strip_runtime_env(dict(os.environ))
        phase, rc, pid, pid_start = _spawn.spawn(argv, env, cap,
                                                 float(timeout_s or self.timeout_s),
                                                 cwd=ws or None)
        handle.pid, handle.pid_start = pid, pid_start
        handle.extra["capture"] = cap
        handle.extra["collapsed_delivery_start"] = True
        if phase == _spawn.IN_FLIGHT:
            return ack.Receipt(returncode=None, detail=_spawn.IN_FLIGHT,
                               extra={"pid": pid, "capture": cap})
        return self._collect(handle, cap, rc=rc)

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

    def collect_late(self, handle: Handle, envelope: protocol.Envelope) -> ack.Receipt:
        cap = self._capture_path(handle, envelope.message_id)
        rc = _spawn.read_rc(cap)
        if rc is None and self.status(handle) == "alive":
            return ack.Receipt(returncode=None, detail=_spawn.IN_FLIGHT, extra={"pid": handle.pid})
        return self._collect(handle, cap)

    # ---- 内部 --------------------------------------------------------------

    def _argv(self, sub: list) -> list:
        head = self.cmd
        if os.path.isabs(head) or head == BIN:
            return [head] + sub
        return [security.resolve_executable(head, self.home)] + sub

    def _dir(self, handle: Handle) -> str:
        return os.path.join(self.home, "state", "inflight", handle.provider_session_id or "anon")

    def _capture_path(self, handle, message_id) -> str:
        attempt = getattr(handle, "attempt", 1) or 1
        return os.path.join(self._dir(handle), "%s.a%s.out" % (message_id, attempt))

    def _sent_path(self, handle, message_id) -> str:
        return os.path.join(self._dir(handle), "%s.sent.txt" % message_id)

    def _collect(self, handle, cap, rc=None) -> ack.Receipt:
        try:
            side_rc, raw = _spawn.collect(cap)
        except FileNotFoundError:
            raise AdapterError("CAPTURE_ABSENT", cap)
        use_rc = rc if rc is not None else side_rc
        # 这一侧的收口是**一条 result 记录**，不是 Codex 那种事件流。
        # 早先这里直接套 `ack.scan_terminal`（事件词表 `turn.completed`），后果是真腿
        # 每一拍都被判 `NO_TERMINAL_EVENT`：合格答复在案，桥自己读不懂（2026-10-04 第三跑，
        # 原件 `evidence/real-legs/04-qoder-result-frame.txt`）。词表按对侧分，闸门不放水。
        r = ack.receipt_from_cli_json(raw, use_rc,
                                      stream_terminal=ack.scan_result_frame_terminal(raw))
        if use_rc is None:
            r.detail = (r.detail + "；" if r.detail else "") + _spawn.NO_RC
        r.extra = {"capture": cap, "pid": handle.pid}
        return r
