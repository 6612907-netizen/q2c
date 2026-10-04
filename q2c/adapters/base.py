#!/usr/bin/env python3
"""适配器契约（任务书 §6）：start／resume／send／receive／status／cancel／capabilities。

分工边界写死在这里：

  · **核心（`q2c/transport.py`）只决定"要不要投、投没投上、能不能算送达"**；
  · **适配器只负责"对侧的字段叫什么、命令怎么起、回执长什么样"**；
  · 送达判据（六条）在 `q2c/ack.py`，对 Codex 与 Qoder 是同一套尺。适配器**不许**自己宣布送达。

依赖规则（任务书 §9/§10）：核心与适配器都不许依赖 Codex 私有数据库、内部 JSONL 形状、
不稳定的 `~/.codex` 文件结构或内部 Rust 类型。取证据的顺序：
**官方 SDK ＞ 公开 CLI ＞ 公开兼容协议**；每一档降级都必须在 `ADAPTERS.md` 写明，
并在 `capabilities()` 里如实报出来（不许报"支持"）。

未知的适配器名 ⇒ `UNKNOWN_ADAPTER`（拒绝＋零副作用）。这条不是洁癖：
旧内核的调度入口曾因"未知 mode 落到默认分支"繁殖出 150 枚进程。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from .. import ack, protocol, security

CONTRACT_METHODS = ("capabilities", "start", "resume", "send", "receive", "status", "cancel")

# status 的三态（**永不**把 unknown 折成 gone：读不出来时重投＝可能在对方还在跑时再叫一次）
STATUS_ALIVE = "alive"
STATUS_GONE = "gone"
STATUS_UNKNOWN = "unknown"
STATUS_NEVER = "never_started"
STATUSES = (STATUS_ALIVE, STATUS_GONE, STATUS_UNKNOWN, STATUS_NEVER)


class AdapterError(Exception):
    side_effects = 0

    def __init__(self, code, detail=""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


@dataclass
class Handle:
    """一次投递所指向的那个"对侧口子"。

    `provider_session_id` 是当前绑定的对侧号（逻辑号在 `sessions.py` 那边，别混）。
    `nonce` 是本轮的绑定串：重投时**必须**沿用，换新串会让上一腿迟到的答复永远认领不了。
    """

    adapter: str
    role: str = ""                # sender／receiver／observer／notify（来自逻辑会话，不是猜的）
    provider_session_id: str = ""
    workspace_ref: str = ""
    nonce: str = ""
    attempt: int = 1              # 第几次投递。落盘件按它分件：重投绝不能读上一拍的旧回执
    pid: int | None = None
    pid_start: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"adapter": self.adapter, "role": self.role,
                "provider_session_id": self.provider_session_id,
                "workspace_ref": self.workspace_ref, "nonce": self.nonce,
                "pid": self.pid, "pid_start": self.pid_start}


class Adapter(abc.ABC):
    name = ""

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.home = self.config.get("home") or security.real_home()

    # ---- 必须实现的方法（七枚） -------------------------------------------

    @abc.abstractmethod
    def capabilities(self) -> dict:
        """如实报告这一腿能做什么。返回里必须有 `live`（会不会真叫外部进程）。"""

    @abc.abstractmethod
    def start(self, handle: Handle) -> Handle:
        """在对侧开一个新口子（新线程／新会话），把 provider 号写回 handle。"""

    @abc.abstractmethod
    def resume(self, handle: Handle) -> Handle:
        """续上已有对侧口子。拿不到旧号 ⇒ 抛 `AdapterError`，不许悄悄另起一个。"""

    @abc.abstractmethod
    def send(self, handle: Handle, envelope: protocol.Envelope, text: str) -> Handle:
        """把这条消息投出去（不等待答复）。"""

    @abc.abstractmethod
    def receive(self, handle: Handle, envelope: protocol.Envelope, timeout_s: float) -> ack.Receipt:
        """取回这一轮的答复，折成 `Receipt`。**不判送达**（判据在 `ack.judge_ack`）。"""

    @abc.abstractmethod
    def status(self, handle: Handle) -> str:
        """三态之一（`unknown` 是合法答案，且必须存在）。"""

    @abc.abstractmethod
    def cancel(self, handle: Handle) -> bool:
        """取消**投递**（把在飞的那一腿收掉）。不许撤销对侧已经开始的工作。"""

    # ---- 公共部分（不许各适配器写一套） -----------------------------------

    def render(self, envelope: protocol.Envelope) -> str:
        """把信封渲染成发给对侧的文本。

        绑定串在这一步进正文（逐字整行，要求对方原样回出）；
        这是**唯一**一处把 nonce 写进消息的地方——重投时复用同一份渲染结果，
        才能证明"投出去的东西没变"（协议 §5 第 1 条）。
        """
        body = envelope.payload
        if envelope.artifact_refs:
            lines = ["\n产物引用："]
            for a in envelope.artifact_refs:
                d = (" 摘要 %s" % a.digest[:12]) if a.digest else ""
                lines.append("  - %s：%s%s" % (a.type, a.ref, d))
            body = body + "\n".join(lines) + "\n"
        tail = ""
        if envelope.expires_at:
            tail += "\n这一笔在 %s 之后作废（过期即停投）。\n" % envelope.expires_at
        bind = "\n%s: %s\n" % (ack.BIND_PREFIX, envelope.metadata.get("bind_nonce", ""))
        return "%s%s\n请求号 %s\n收到后请**单独占一行、逐字**回出下面两行：\n%s%s" % (
            body, tail, envelope.request_id, bind, ack.ACK_TOKEN)

    def is_live(self) -> bool:
        """默认看 `capabilities()["live"]`；有接缝概念的适配器自己覆写。"""
        return bool(self.capabilities().get("live"))

    def check_credential(self) -> tuple:
        """派发前的凭据闸：只问"能不能无人值守完成一次最小认证调用"。

        判不出来按不可用（`security.probe_credential` 的六条硬规矩）。
        返回 (放行?, 原因)。桩适配器（`live=False`）默认放行——它不叫外部 CLI，
        但只要显式配了 `Q2C_CRED_PROBE` 就照判，免得测一条永远跑不到的分支。
        """
        caps = self.capabilities()
        probe = (self.config.get("cred_probe") or "").strip()
        if not caps.get("live") and not probe:
            return True, ""
        kind = caps.get("credential_kind") or self.name
        verdict, note = security.probe_credential(kind, env=self.config.get("env") or None)
        if verdict == security.READY:
            return True, "ok"
        return False, "%s 侧凭据不可用：%s" % (kind, note)


_REGISTRY: dict = {}


def register(cls: type) -> type:
    if not cls.name:
        raise AdapterError("ADAPTER_NAME_MISSING", cls.__name__)
    _REGISTRY[cls.name] = cls
    return cls


def known() -> tuple:
    return tuple(sorted(_REGISTRY))


def build(name: str, config: dict | None = None) -> Adapter:
    """按名字取适配器。**未知名字 ⇒ 拒绝，不回落到任何默认适配器。**"""
    cls = _REGISTRY.get((name or "").strip())
    if cls is None:
        raise AdapterError("UNKNOWN_ADAPTER",
                           "=%r（在册：%s）" % (name, ", ".join(known()) or "无"))
    return cls(config)


def import_builtin():
    """把随包适配器登记进表。CLI 与测试都从这里进，避免"import 顺序决定有没有这个适配器"。"""
    from . import inproc, codex, loopback, qoder  # noqa: F401
    return known()
