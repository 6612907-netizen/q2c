#!/usr/bin/env python3
"""Codex 适配器（公开 CLI 档）。

依赖顺序（任务书 §9/§10）：**官方 SDK ＞ 公开 CLI ＞ 公开兼容协议**。
这一版走的是**公开 CLI**，理由与降级都写在 ADAPTERS.md §2，摘要：

  · 不用 Codex 私有 SQLite（`state_5.sqlite` 的队列库）——旧内核用它当"还有几条没消费"的证据，
    那是内部结构，随时会变；产品里这一档降级为 `UNVERIFIABLE`，恢复阶梯据此停手，不重投；
  · 不解析 `~/.codex/sessions/**/*.jsonl` 的内部形状——旧内核的三路反证里那两路依赖它；
    产品只用 `codex exec resume --json` 自己打出来的事件流 ＋ `-o` 末条消息；
  · 不碰任何内部 Rust 类型、不猜隐藏参数。

代价（诚实记账，写进 RELEASE-REPORT 的 KNOWN_LIMITATIONS）：
恢复阶梯里"会话落盘件里已有本条请求的合格答复"这一档在产品中**不存在**，
所以重启后如果落盘件也不在，那一腿一律读成 `UNKNOWN ⇒ 停手等人`，
不会自动重发。这是**故意收窄**，不是漏实现：宁可停手，不可重复烧一次真调用。

真调用需要显式授权：`Q2C_LIVE=1`。没授权 ⇒ `LIVE_NOT_AUTHORIZED`（零副作用）。
"""

from __future__ import annotations

import os
import sys

from . import _spawn
from .base import STATUS_NEVER, STATUS_UNKNOWN, Adapter, AdapterError, Handle, register
from .. import ack, protocol, security

NAME = "codex"


@register
class CodexAdapter(Adapter):
    name = NAME

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.cmd = (self.config.get("cmd") or os.environ.get("Q2C_CODEX_CMD") or "codex").strip()
        self.sandbox = (self.config.get("sandbox_mode") or "read-only").strip()
        self.timeout_s = float(self.config.get("timeout_s") or 600)

    # ---- 契约七枚 ----------------------------------------------------------

    def capabilities(self) -> dict:
        return {
            "name": NAME,
            "live": self.is_live(),
            "terminal_evidence": True,
            "split_send_receive": True,
            "credential_kind": "codex",
            "supports_cancel": True,
            "transport": "公开 CLI（codex queue / codex exec resume --json）",
            "evidence": "exec --json 事件流的终态 ＋ 子进程退出码侧件 ＋ -o 末条消息",
            "not_used": ["Codex 私有 SQLite 队列库", "~/.codex/sessions 内部 JSONL 形状",
                         "内部 Rust 类型／隐藏参数"],
            "limits": ["没有『不产生模型调用就能新建空线程』的公开入口：start() 拒做，改走 sessions bind",
                       "重启后若落盘件也不在 ⇒ 只能报 UNKNOWN，不自动重发"],
        }

    def is_live(self) -> bool:
        """配了 `cmd`／`Q2C_CODEX_CMD` ⇒ 这是测试接缝（不起真 CLI）。"""
        override = (self.config.get("cmd") or os.environ.get("Q2C_CODEX_CMD") or "").strip()
        return not bool(override)

    def _require_live(self, what: str) -> None:
        if self.is_live() and os.environ.get("Q2C_LIVE", "").strip() != "1":
            raise AdapterError("LIVE_NOT_AUTHORIZED",
                               "%s 要真调 Codex，必须显式设 Q2C_LIVE=1（当前：零调用）" % what)

    def start(self, handle: Handle) -> Handle:
        raise AdapterError(
            "CODEX_START_UNSUPPORTED",
            "公开 CLI 没有『零模型调用新建空线程』的入口。请把已有线程号交给 "
            "`q2c sessions bind --adapter codex --session <q2c号> --provider <线程号>`")

    def resume(self, handle: Handle) -> Handle:
        if not (handle.provider_session_id or "").strip():
            raise AdapterError("EMPTY_PROVIDER_ID", "Codex 腿必须有线程号（不猜、不另起一条）")
        return handle

    def send(self, handle: Handle, envelope: protocol.Envelope, text=None) -> Handle:
        self._require_live("send")
        handle = self.resume(handle)
        body = text if text is not None else self.render(envelope)
        # 派发文先落盘：投出去的是什么，事后必须能逐字对（协议 §5 第 1 条的凭据）
        sent = self._sent_path(handle, envelope.message_id)
        security.atomic_write(sent, body)
        argv = self._argv(["queue", "--thread", handle.provider_session_id, "--message", body])
        env = security.strip_runtime_env(dict(os.environ))
        cap = self._capture_path(handle, envelope.message_id, suffix=".queue.out")
        phase, rc, pid, pid_start = _spawn.spawn(argv, env, cap, min(self.timeout_s, 120),
                                                cwd=handle.workspace_ref)
        handle.pid, handle.pid_start = pid, pid_start
        handle.extra["queue_rc"] = rc
        handle.extra["sent_file"] = sent
        if phase == _spawn.IN_FLIGHT:
            raise AdapterError("ENQUEUE_IN_FLIGHT", "入队调用仍未收口（不重复入队，交恢复阶梯）")
        if rc != 0:
            raise AdapterError("ENQUEUE_FAILED", "codex queue 退 %s（详情见 %s）" % (rc, cap))
        return handle

    def receive(self, handle: Handle, envelope: protocol.Envelope,
                timeout_s: float = None) -> ack.Receipt:
        self._require_live("receive")
        cap = self._capture_path(handle, envelope.message_id)
        if os.path.isfile(cap) or os.path.isfile(_spawn.rc_path_of(cap)):
            return self._collect(handle, cap)
        env = security.strip_runtime_env(dict(os.environ))
        last = cap + ".last.txt"   # 与 _collect 里同一个名字
        # 工作根用**子进程 cwd**表达，不用 `-C`：`codex exec resume` 不认这个旗标
        # （真腿实测报 "unexpected argument '-C' found"，整腿退 2）。
        # 指工作根这件事本身是必须的：不指就继承跑批进程目录，同一命令两次跑出不同现场。
        argv = self._argv(["exec", "resume", "--json",
                           "-c", 'sandbox_mode="%s"' % self.sandbox,
                           "--skip-git-repo-check", "-o", last,
                           handle.provider_session_id,
                           "请处理刚入队的那条请求（同一会话内），并按要求回出绑定行与确认行。"])
        phase, rc, pid, pid_start = _spawn.spawn(argv, env, cap,
                                                 float(timeout_s or self.timeout_s),
                                                 cwd=handle.workspace_ref)
        handle.pid, handle.pid_start = pid, pid_start
        handle.extra["capture"] = cap
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
        if os.path.isabs(head) or head == "codex":
            return [head] + sub
        found = security.resolve_executable(head, self.home)
        return [found] + sub

    def _dir(self, handle: Handle) -> str:
        return os.path.join(self.home, "state", "inflight", handle.provider_session_id or "anon")

    def _capture_path(self, handle: Handle, message_id: str, suffix: str = ".out") -> str:
        attempt = getattr(handle, "attempt", 1) or 1
        return os.path.join(self._dir(handle), "%s.a%s%s" % (message_id, attempt, suffix))

    def _sent_path(self, handle: Handle, message_id: str) -> str:
        return os.path.join(self._dir(handle), "%s.sent.txt" % message_id)

    def _collect(self, handle, cap, rc=None) -> ack.Receipt:
        """把 `codex exec --json` 的事件流折成 `Receipt`。

        这份映射是**照真件写的**，不是照着猜测写的：夹具就是
        `evidence/real-legs/01-codex-stream.jsonl`（一次真调用留下的九行事件流），
        判据 `tests/test_codex_stream_real_fixture.py` 直接读它。
        真件形状（codex-cli 0.159.0）：

            {"type":"thread.started","thread_id":"01a105fd-…"}
            {"type":"turn.started"}
            {"type":"error","message":"Reconnecting…"}     ← 网络重试，不是本轮失败
            {"type":"item.completed","item":{"type":"agent_message","text":"收到"}}
            {"type":"turn.completed","usage":{…}}

        两处刻意不宽容：

        * **没有 `{"type":"result"}` 这一行**（那是别的 CLI 的形状）。所以"有结构化记录"
          判的是"有一个 `item.completed/agent_message` 或 `-o` 末条消息文件"，
          两者都取不到 ⇒ `has_structured_record=False`，绝不退回去读散文。
        * 自报成功只认 `turn.completed`；中途的 `error` 事件是重连提示，
          终态读数由 `ack.scan_terminal` 判"最后一个终态"，重连后跑完 ⇒ completed。
        """
        try:
            side_rc, raw = _spawn.collect(cap)
        except FileNotFoundError:
            raise AdapterError("CAPTURE_ABSENT", cap)
        use_rc = rc if rc is not None else side_rc
        r = self.receipt_from_stream(raw, use_rc, last_message_file=cap + ".last.txt")
        if use_rc is None:
            r.detail = (r.detail + "；" if r.detail else "") + _spawn.NO_RC
        r.extra = {"capture": cap, "pid": handle.pid}
        return r

    @staticmethod
    def receipt_from_stream(raw: str, rc: int | None,
                            last_message_file: str = "") -> ack.Receipt:
        """事件流 → Receipt（公开出来给判据直接用真夹具打）。"""
        import json as _json
        thread_id = ""
        agent_texts = []
        saw_turn = False
        for ln in (raw or "").splitlines():
            t = ln.strip()
            if not t.startswith("{"):
                continue
            try:
                d = _json.loads(t)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            ty = d.get("type")
            if ty in ("thread.started", "session.created", "session.started"):
                thread_id = str(d.get("thread_id") or d.get("session_id") or "")
            elif ty == "item.completed":
                item = d.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    agent_texts.append(str(item.get("text") or ""))
            elif ty in ("turn.completed", "turn.failed", "error", "cancelled"):
                saw_turn = True
        tail = ""
        if last_message_file and os.path.isfile(last_message_file):
            try:
                with open(last_message_file, encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()
            except OSError:
                tail = ""
        body = max(agent_texts, key=len) if agent_texts else ""
        # `-o` 那份经常只剩最后一行结论，事件流里那条更完整：取长的，不拼来拼去
        inner = body if len(body) >= len(tail) else tail
        return ack.Receipt(
            returncode=rc,
            # "有没有拿到回复正文"与"这一轮跑完没有"是两条事实，分开报：
            # 混成一条会让半截流被诊断成"没回复"，而真实原因是"没有终态"（R19 那一族）。
            has_structured_record=bool(agent_texts) or bool(tail.strip()),
            self_reported_success=(ack.scan_terminal(raw) == ack.TERMINAL_COMPLETED),
            reported_session_id=thread_id,
            ack_line_present=ack.has_ack_line(inner),
            bind_echo=ack.extract_bind_echo(inner),
            result_body_present=bool(inner.strip()),
            body=inner,
            terminal=ack.scan_terminal(raw),
            detail="events" if saw_turn else "no-terminal-event",
        )
