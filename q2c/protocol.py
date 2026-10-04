#!/usr/bin/env python3
"""q2c 协议层：信封、枚举、状态机、校验。

本模块是 PROTOCOL.md 的机器可读形态。**两面的不一致由测试盯着**
（tests/test_protocol_doc_sync.py 逐字比对枚举名单），所以文档不会被改旧而代码还绿。

三条贯穿全模块的纪律（都是从已验证实现里搬来的，不是新造的）：

1. **fail-closed**：未知的 protocol_version／消息类型／状态／取值 ⇒ 抛 ProtocolError，
   调用方一律"零副作用 + 退出码 2"。控制入口不许在读不懂时选择"做最多的那件事"。
2. **未知 ≠ 没发生**：读不动的东西记成 UNKNOWN，绝不折成 absent、绝不因为读不动就放行重投。
3. **终态不可翻**：ACKED／DELIVERY_FAILED／EXPIRED／CANCELLED 之后到达的事件只进 trace
   （stale_event），不改状态。旧台账里"重启后重复消费 ACCEPTED"那一类事故就是这么挡的。
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# §1 版本
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "q2c/1"
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)

# ---------------------------------------------------------------------------
# §2 信封字段（顺序即 PROTOCOL.md 表格顺序；改动要同步文档，测试会红）
# ---------------------------------------------------------------------------

ENVELOPE_FIELDS = (
    "protocol_version",
    "message_id",
    "request_id",
    "correlation_id",
    "sender",
    "receiver",
    "handoff_type",
    "session_id",
    "workspace_ref",
    "repo_ref",
    "commit_ref",
    "payload",
    "artifact_refs",
    "idempotency_key",
    "created_at",
    "expires_at",
    "metadata",
)

REQUIRED_ENVELOPE_FIELDS = (
    "protocol_version",
    "message_id",
    "request_id",
    "correlation_id",
    "sender",
    "receiver",
    "handoff_type",
    "session_id",
    "payload",
    "idempotency_key",
    "created_at",
)

# ---------------------------------------------------------------------------
# §3 消息类型
# ---------------------------------------------------------------------------

MSG_HANDOFF_REQUEST = "HANDOFF_REQUEST"
MSG_HANDOFF_STARTED = "HANDOFF_STARTED"
MSG_HANDOFF_PROGRESS = "HANDOFF_PROGRESS"
MSG_HANDOFF_RESULT = "HANDOFF_RESULT"
MSG_HANDOFF_FAILED = "HANDOFF_FAILED"
MSG_HANDOFF_ACK = "HANDOFF_ACK"

MESSAGE_TYPES = (
    MSG_HANDOFF_REQUEST,
    MSG_HANDOFF_STARTED,
    MSG_HANDOFF_PROGRESS,
    MSG_HANDOFF_RESULT,
    MSG_HANDOFF_FAILED,
    MSG_HANDOFF_ACK,
)

# ---------------------------------------------------------------------------
# §4 传输状态
# ---------------------------------------------------------------------------

ST_CREATED = "CREATED"
ST_QUEUED = "QUEUED"
ST_DELIVERING = "DELIVERING"
ST_DELIVERED = "DELIVERED"
ST_STARTED = "STARTED"
ST_RESPONDED = "RESPONDED"
ST_ACKED = "ACKED"
ST_DELIVERY_RETRY = "DELIVERY_RETRY"
ST_DELIVERY_FAILED = "DELIVERY_FAILED"
ST_EXPIRED = "EXPIRED"
ST_CANCELLED = "CANCELLED"

TRANSPORT_STATES = (
    ST_CREATED,
    ST_QUEUED,
    ST_DELIVERING,
    ST_DELIVERED,
    ST_STARTED,
    ST_RESPONDED,
    ST_ACKED,
    ST_DELIVERY_RETRY,
    ST_DELIVERY_FAILED,
    ST_EXPIRED,
    ST_CANCELLED,
)

# 终态只有这三枚。**DELIVERY_FAILED 故意不算终态**，它是"停放态"：
# 预算用尽 ⇒ 桥自己不再试，但两条合法的路还能把它推走——
#   ① 人显式 `q2c retry-delivery`（这是"人的处置"，不是自动重试）；
#   ② 上一腿迟到的答复终于回来了且合格（在册那一格：迟到答复被收回 ⇒ ACKED 且静默）。
# 把它写成终态，就等于规定"人重投之后这条记录永远停在失败"，那既不对，也会逼人伪造新请求号。
TERMINAL_STATES = (ST_ACKED, ST_EXPIRED, ST_CANCELLED)
PARKED_STATES = (ST_DELIVERY_FAILED,)

# §4.1 失败归类（开放名单；新增值属同一主版本内的兼容扩展）
FAILURE_CLASSES = (
    "ADAPTER_UNAVAILABLE",
    "CREDENTIAL_UNAVAILABLE",
    "UPSTREAM_UNAVAILABLE",
    "PROCESS_TIMEOUT",
    "NO_TERMINAL_EVENT",
    "ATTRIBUTION_FAILED",
    "SESSION_LOST",
    "UNKNOWN",
)

# 这些取值属项目/发布真相，**不许**出现在本协议的任何枚举里（PROTOCOL.md §0）。
# 名字与历史实现对照过：COMPLETED／ACCEPTED／REJECTED／APPROVED／CHANGES_REQUESTED
# 都曾在旧内核里当状态或结论词用过，降级路径记在 Q2C-BOUNDARY-AUDIT.md。
FORBIDDEN_VALUES = (
    "TASK_COMPLETED",
    "READY_TO_RELEASE",
    "COMPLETED",
    "ACCEPTED",
    "REJECTED",
    "APPROVED",
    "CHANGES_REQUESTED",
    "RELEASE_READY",
    "BLOCKED_ON_QUALITY",
    "PROVEN",
    "V1_CLOSED",
)

# ---------------------------------------------------------------------------
# §7 产物引用类型
# ---------------------------------------------------------------------------

ARTIFACT_TYPES = (
    "git_commit",
    "file",
    "diff",
    "patch",
    "log",
    "test_report",
    "screenshot",
    "evidence_package",
    "generic_uri",
)

ARTIFACT_REF_FIELDS = ("type", "ref", "digest", "media_type", "size_bytes")

# ---------------------------------------------------------------------------
# 状态迁移表
# ---------------------------------------------------------------------------

_TRANSITIONS = {
    ST_CREATED: (ST_QUEUED, ST_DELIVERING, ST_DELIVERY_FAILED, ST_EXPIRED, ST_CANCELLED),
    ST_QUEUED: (ST_DELIVERING, ST_DELIVERY_RETRY, ST_DELIVERY_FAILED, ST_EXPIRED, ST_CANCELLED),
    # DELIVERING 之后可直接 ACKED：适配器在同一次调用里就给出了合格回执（通知腿就是这样）
    ST_DELIVERING: (
        ST_DELIVERED,
        ST_STARTED,
        ST_ACKED,
        ST_DELIVERY_RETRY,
        ST_DELIVERY_FAILED,
        ST_EXPIRED,
        ST_CANCELLED,
    ),
    ST_DELIVERED: (ST_STARTED, ST_ACKED, ST_DELIVERY_RETRY, ST_DELIVERY_FAILED, ST_EXPIRED, ST_CANCELLED),
    ST_STARTED: (ST_RESPONDED, ST_ACKED, ST_DELIVERY_RETRY, ST_DELIVERY_FAILED, ST_EXPIRED, ST_CANCELLED),
    # 结果腿投递不上时回到重试态：接收方已经跑完，**只补送达，绝不重跑**（PROTOCOL.md §5）
    ST_RESPONDED: (ST_ACKED, ST_DELIVERY_RETRY, ST_DELIVERY_FAILED, ST_CANCELLED),
    ST_DELIVERY_RETRY: (ST_DELIVERING, ST_DELIVERY_FAILED, ST_EXPIRED, ST_CANCELLED),
    # 停放态：只接受"人显式重投"或"迟到答复被收回"，其余一律不动（pump 不会自动从这里起来）
    ST_DELIVERY_FAILED: (ST_DELIVERY_RETRY, ST_ACKED, ST_EXPIRED, ST_CANCELLED),
    ST_ACKED: (),
    ST_EXPIRED: (),
    ST_CANCELLED: (),
}

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-\u4e00-\u9fff]{0,127}$")


class ProtocolError(Exception):
    """协议层唯一的拒绝方式。

    `side_effects = 0` 是契约的一部分：抛出本异常＝这一轮**一个字节都不许写、
    一次子进程都不许起**。调用方（CLI／适配器）负责把它换成退出码 2 加一行 trace。
    """

    side_effects = 0

    def __init__(self, code: str, detail: str = ""):
        super().__init__("%s%s" % (code, (": " + detail) if detail else ""))
        self.code = code
        self.detail = detail


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def is_parked(state: str) -> bool:
    """停放态：桥自己不催，等人处置。与终态分开，是因为它**可以**合法地再往前走。"""
    return state in PARKED_STATES


def allowed_transitions(state: str) -> tuple:
    if state not in TRANSPORT_STATES:
        raise ProtocolError("UNKNOWN_STATE", repr(state))
    return _TRANSITIONS[state]


def next_state(current: str, target: str) -> str:
    """唯一合法的状态推进入口。非法迁移＝IllegalTransition（零副作用）。"""
    if current not in TRANSPORT_STATES:
        raise ProtocolError("UNKNOWN_STATE", repr(current))
    if target not in TRANSPORT_STATES:
        raise ProtocolError("UNKNOWN_STATE", repr(target))
    if is_terminal(current):
        raise ProtocolError("STALE_EVENT", "终态 %s 不可再迁移（迟到事件只进 trace）" % current)
    if target not in _TRANSITIONS[current]:
        raise ProtocolError("ILLEGAL_TRANSITION", "%s → %s" % (current, target))
    return target


# ---------------------------------------------------------------------------
# id 与时间
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """UTC、带偏移、**到微秒**。

    秒级不够用：`provider_at()` 要在绑定时间轴上取"当时那一枚"，
    同一秒内发生的换绑会被读成"换绑之前"，历史绑定就取错了对象。
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_id(prefix: str) -> str:
    return "%s-%s" % (prefix, uuid.uuid4().hex[:16])


def new_message_id() -> str:
    return _new_id("msg")


def new_request_id() -> str:
    return _new_id("req")


def new_correlation_id() -> str:
    return _new_id("cor")


def new_session_id() -> str:
    return _new_id("qs")


def new_idempotency_key(seed: str = "") -> str:
    if seed:
        return "idem-" + uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:16]
    return _new_id("idem")


def parse_ts(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("BAD_TIMESTAMP", "%s 为空" % field_name)
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ProtocolError("BAD_TIMESTAMP", "%s=%r 解析不了（要求 ISO-8601）" % (field_name, value))
    if dt.tzinfo is None:
        raise ProtocolError("BAD_TIMESTAMP", "%s 缺时区偏移：%r" % (field_name, value))
    return dt


# ---------------------------------------------------------------------------
# 信封
# ---------------------------------------------------------------------------


@dataclass
class ArtifactRef:
    type: str
    ref: str
    digest: str = ""
    media_type: str = ""
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        out = {"type": self.type, "ref": self.ref}
        if self.digest:
            out["digest"] = self.digest
        if self.media_type:
            out["media_type"] = self.media_type
        if self.size_bytes is not None:
            out["size_bytes"] = self.size_bytes
        return out

    @classmethod
    def from_dict(cls, doc: dict) -> "ArtifactRef":
        if not isinstance(doc, dict):
            raise ProtocolError("BAD_ARTIFACT_REF", "不是对象：%r" % (doc,))
        kind = doc.get("type")
        if kind not in ARTIFACT_TYPES:
            # 未知的产物类型＝拒收（不猜它是什么、更不按 file 处理）
            raise ProtocolError("UNKNOWN_ARTIFACT_TYPE", repr(kind))
        ref = doc.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ProtocolError("BAD_ARTIFACT_REF", "缺 ref")
        digest = (doc.get("digest") or "").strip()
        if digest and not _SHA256_RE.fullmatch(digest):
            raise ProtocolError("BAD_ARTIFACT_DIGEST", "digest 不是 64 位十六进制：%r" % (digest,))
        size = doc.get("size_bytes")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise ProtocolError("BAD_ARTIFACT_REF", "size_bytes 形状不对：%r" % (size,))
        return cls(
            type=kind,
            ref=ref.strip(),
            digest=digest.lower(),
            media_type=(doc.get("media_type") or "").strip(),
            size_bytes=size,
        )


@dataclass
class Envelope:
    """一条消息的不可变信封。

    未认识的**字段**原样留在 `extra`（前向兼容：不许丢别人加的可选字段）；
    未认识的**取值**在 `from_dict` 里就已经抛了。
    """

    protocol_version: str
    message_id: str
    request_id: str
    correlation_id: str
    sender: str
    receiver: str
    handoff_type: str
    session_id: str
    payload: str
    idempotency_key: str
    created_at: str
    workspace_ref: str = ""
    repo_ref: str = ""
    commit_ref: str = ""
    artifact_refs: tuple = ()
    expires_at: str = ""
    metadata: dict = field(default_factory=dict)
    message_type: str = MSG_HANDOFF_REQUEST
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "handoff_type": self.handoff_type,
            "session_id": self.session_id,
            "workspace_ref": self.workspace_ref,
            "repo_ref": self.repo_ref,
            "commit_ref": self.commit_ref,
            "payload": self.payload,
            "artifact_refs": [a.to_dict() for a in self.artifact_refs],
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, doc: dict) -> "Envelope":
        """把收到的 JSON 变成信封：**只**做类型层面的检查，形状与取值一律交给 validate()。

        这一层与 validate() 之间不许再写第二套判据（双真源）。
        """
        if not isinstance(doc, dict):
            raise ProtocolError("BAD_ENVELOPE", "信封不是 JSON 对象")
        absent = [k for k in REQUIRED_ENVELOPE_FIELDS if k not in doc]
        if absent:
            raise ProtocolError("MISSING_FIELDS", ",".join(sorted(absent)))
        if not isinstance(doc.get("payload"), str):
            raise ProtocolError("BAD_ENVELOPE", "payload 必须是字符串")
        refs = doc.get("artifact_refs") or []
        if not isinstance(refs, list):
            raise ProtocolError("BAD_ARTIFACT_REFS", "artifact_refs 必须是数组")
        artifacts = tuple(ArtifactRef.from_dict(a) for a in refs)

        def s(name: str) -> str:
            v = doc.get(name)
            return v.strip() if isinstance(v, str) else ""

        known = set(ENVELOPE_FIELDS) | {"message_type"}
        extra = {k: v for k, v in doc.items() if k not in known}

        env = cls(
            protocol_version=s("protocol_version"),
            message_id=s("message_id"),
            request_id=s("request_id"),
            correlation_id=s("correlation_id"),
            sender=s("sender"),
            receiver=s("receiver"),
            handoff_type=s("handoff_type"),
            session_id=s("session_id"),
            payload=doc["payload"],
            idempotency_key=s("idempotency_key"),
            created_at=s("created_at"),
            workspace_ref=s("workspace_ref"),
            repo_ref=s("repo_ref"),
            commit_ref=s("commit_ref"),
            artifact_refs=artifacts,
            expires_at=s("expires_at"),
            metadata=dict(doc.get("metadata") or {}) if isinstance(doc.get("metadata") or {}, dict) else {},
            message_type=doc.get("message_type", MSG_HANDOFF_REQUEST),
            extra=extra,
        )
        if not isinstance(doc.get("metadata") or {}, dict):
            raise ProtocolError("BAD_METADATA", "metadata 必须是对象")
        env.validate()
        return env

    def validate(self) -> None:
        """信封形状的唯一一道闸。

        构造路径（`make_request`）与解析路径（`from_dict`）**共用这一处**，不许各写一套：
        两处判据不一致时，发出去的信封和收进来的信封就不是同一种东西。
        """
        if self.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ProtocolError("UNSUPPORTED_PROTOCOL_VERSION", repr(self.protocol_version))
        if self.message_type not in MESSAGE_TYPES:
            raise ProtocolError("UNKNOWN_MESSAGE_TYPE", repr(self.message_type))
        if not isinstance(self.payload, str):
            raise ProtocolError("BAD_ENVELOPE", "payload 必须是字符串")

        blank = [k for k in REQUIRED_ENVELOPE_FIELDS
                 if k != "payload" and not str(getattr(self, k) or "").strip()]
        if blank:
            # 空串／全空格＝没填。报 MISSING_FIELDS 而不是 BAD_ID：
            # "你没写" 与 "你写的形状不对" 是两句话，给错了会让人去改格式而不是去补字段。
            raise ProtocolError("MISSING_FIELDS", ",".join(sorted(blank)))

        for name in ("message_id", "request_id", "correlation_id", "sender", "receiver",
                     "session_id", "idempotency_key"):
            val = getattr(self, name)
            if not isinstance(val, str) or not _ID_RE.fullmatch(val.strip()):
                raise ProtocolError("BAD_ID", "%s=%r 形状不合法" % (name, val))

        # handoff_type 是发送方命名空间，但**位置**在信封上：项目真相词不许从这儿溜进来
        if self.handoff_type.strip() in FORBIDDEN_VALUES:
            raise ProtocolError("FORBIDDEN_VALUE", "handoff_type=%s 属项目/发布真相" % self.handoff_type)
        if not _ID_RE.fullmatch(self.handoff_type.strip()):
            raise ProtocolError("BAD_ID", "handoff_type=%r" % (self.handoff_type,))

        if self.commit_ref and not _SHA_RE.fullmatch(self.commit_ref):
            raise ProtocolError("BAD_COMMIT_REF", repr(self.commit_ref))

        created = parse_ts(self.created_at, "created_at")
        if self.expires_at:
            exp = parse_ts(self.expires_at, "expires_at")
            if exp <= created:
                raise ProtocolError("EXPIRES_BEFORE_CREATED", self.expires_at)

        for a in self.artifact_refs:
            ArtifactRef.from_dict(a.to_dict())      # 类型／摘要／size 形状都在这一个地方校

        if not isinstance(self.metadata, dict):
            raise ProtocolError("BAD_METADATA", "metadata 必须是对象")

    def is_expired(self, at: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        now_dt = at or datetime.now(timezone.utc)
        return parse_ts(self.expires_at, "expires_at") <= now_dt

    def path_fields(self) -> tuple:
        """需要过 SECURITY.md 路径校验的那几枚字段。"""
        return (("workspace_ref", self.workspace_ref), ("repo_ref", self.repo_ref))


def make_request(
    *,
    sender: str,
    receiver: str,
    session_id: str,
    handoff_type: str,
    payload: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    workspace_ref: str = "",
    repo_ref: str = "",
    commit_ref: str = "",
    artifact_refs=(),
    expires_at: str = "",
    metadata: dict | None = None,
    message_id: str | None = None,
) -> Envelope:
    """发送方造一条 HANDOFF_REQUEST。

    桥在这里只做三件事：发号、盖时间戳、按 §2 校验。**不碰 payload 的语义**。
    """
    refs = tuple(a if isinstance(a, ArtifactRef) else ArtifactRef.from_dict(a) for a in artifact_refs)
    env = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=message_id or new_message_id(),
        request_id=request_id or new_request_id(),
        correlation_id=correlation_id or new_correlation_id(),
        sender=sender,
        receiver=receiver,
        handoff_type=handoff_type,
        session_id=session_id,
        payload=payload,
        idempotency_key=idempotency_key or new_idempotency_key(),
        created_at=utc_now(),
        workspace_ref=workspace_ref,
        repo_ref=repo_ref,
        commit_ref=commit_ref,
        artifact_refs=refs,
        expires_at=expires_at,
        metadata=dict(metadata or {}),
        message_type=MSG_HANDOFF_REQUEST,
    )
    env.validate()
    return env
