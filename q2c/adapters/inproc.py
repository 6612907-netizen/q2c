#!/usr/bin/env python3
"""`inproc` 适配器：同进程内应答，**只用于负载判据**（1000 枚合成交接那一格）。

为什么要它（以及为什么不能用 loopback 代替）：
`loopback` 每一腿都要起一个真子进程。1000 枚交接 ⇒ 两千次进程起停，
判据跑到十分钟后就开始测机器的耐心而不是测桥的吞吐。

它省掉的正是这一格里**不该被测**的东西（起进程／超时／进程组），
它保留的是这一格**真正要测**的东西：
  · 同一套 `ack.receipt_from_cli_json` 解析路径（回执形状与桩件逐字同形）；
  · 同一套绑定串与幂等判定；
  · 同一套台账写入与跟踪事件。

所以它的能力位如实写着 `subprocess_evidence=False`，并且
`q2c doctor` 里被标成 LOAD-ONLY：进程／超时／清理类判据**不许**用它。
"""

from __future__ import annotations

import json
import os
import uuid

from .base import STATUS_NEVER, Adapter, AdapterError, Handle, register
from .. import ack, protocol, security

NAME = "inproc"


@register
class InprocAdapter(Adapter):
    name = NAME

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.box = os.path.join(self.home, "inproc-box")
        self.mode = (self.config.get("mode") or "ack").strip()

    def capabilities(self) -> dict:
        return {
            "name": NAME,
            "live": False,
            "terminal_evidence": False,
            "subprocess_evidence": False,
            "split_send_receive": True,
            "credential_kind": "",
            "supports_cancel": False,
            "evidence": "同进程构造的回执（与桩件同形，走同一套解析与判定）",
            "note": "LOAD-ONLY：只用于负载判据；进程／超时／清理类判据不许用它",
        }

    def start(self, handle: Handle) -> Handle:
        handle.provider_session_id = "ip-" + uuid.uuid4().hex[:12]
        os.makedirs(os.path.join(self.box, handle.provider_session_id), exist_ok=True)
        return handle

    def resume(self, handle: Handle) -> Handle:
        if not os.path.isdir(os.path.join(self.box, handle.provider_session_id or "")):
            raise AdapterError("BOX_ABSENT", handle.provider_session_id)
        return handle

    def send(self, handle: Handle, envelope: protocol.Envelope, text=None) -> Handle:
        if not handle.provider_session_id:
            handle = self.start(handle)
        body = text if text is not None else self.render(envelope)
        d = os.path.join(self.box, handle.provider_session_id)
        os.makedirs(d, exist_ok=True)
        security.atomic_write(os.path.join(d, "%s.txt" % envelope.message_id), body)
        return handle

    def receive(self, handle: Handle, envelope: protocol.Envelope,
                timeout_s: float = 0.0) -> ack.Receipt:
        """在同进程里产出**与桩件逐字同形**的回执，再走同一套解析。

        不许在这里直接手工拼 `Receipt` 字段：那样六闸判的是我自己造的对象，
        解析层（`receipt_from_cli_json`）就没被测到。
        """
        d = os.path.join(self.box, handle.provider_session_id, "%s.txt" % envelope.message_id)
        if not os.path.isfile(d):
            raise AdapterError("MSG_NOT_DELIVERED", d)
        nonce = envelope.metadata.get("bind_nonce", "")
        reply = "已读这一笔（请求号 %s）。理由：负载判据用的同进程应答器。" % envelope.request_id
        lines = [reply]
        if nonce:
            lines.append("%s: %s" % (ack.BIND_PREFIX, nonce))
        if self.mode != "no-ack":
            lines.append(ack.ACK_TOKEN)
        inner = "\n".join(lines) + "\n"
        doc = {"type": "result", "is_error": False, "subtype": "success",
               "session_id": ("other-ip-session" if self.mode == "wrong-session"
                              else handle.provider_session_id),
               "result": inner}
        line = json.dumps(doc, ensure_ascii=False) + "\n"
        security.atomic_write(d + ".receipt", line)
        r = ack.receipt_from_cli_json(line, 0, stream_terminal=ack.TERMINAL_NOT_PROVIDED)
        r.extra = {"inproc": True}
        return r

    def status(self, handle: Handle) -> str:
        return STATUS_NEVER          # 同进程没有子进程可报，不许假报 alive

    def cancel(self, handle: Handle) -> bool:
        return False                 # 没有进程可取消；能力位 supports_cancel=False
