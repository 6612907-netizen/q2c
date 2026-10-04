#!/usr/bin/env python3
"""送达确认（transport ACK）的判据。

分工写死在这里，因为混过一次：
  · **适配器**负责"把对侧的回执解析成事实"（provider 的字段名、JSONL 形状都在适配器里）；
  · **本模块**负责"这些事实够不够格叫送达"（六条判据，PROTOCOL.md §4.2）。
判据放核心、解析放适配器，是为了让六条规则对 Codex 与 Qoder 是**同一套尺**，
也让"摘掉某一条闸"这种反证能落在一个确定的位置。

四条来自真事故的形状判据（不是质量判断）：

- **退 0 ≠ 送达**：`/bin/true` 也退 0，它什么人都没叫到。
- **结构化字段，不是子串**：正文里出现过 `ACKED`、或出现字面量 `"type":"result"`，
  都不构成成功证据（真反例：`is_error=true`、退出码 7、正文同样带着 ACKED 的回执）。
- **身份要相等**：回执自报的会话号必须等于我要送达的那一个，拿别人的"成功"顶替不算。
- **绑定串要逐字整行回出**：不回＝证明不了它核读的是本轮那一份（复述上一轮形状一样能全对）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

ACK_TOKEN = "ACKED"
BIND_PREFIX = "Q2C-BIND"
BIND_LABEL = BIND_PREFIX + ":"
# 绑定行的**唯一**写法：`Q2C-BIND: <nonce>` 独立整行。
# 只留一种形状是有意的：上一版同时认 "BIND: " 与 "REVIEW-BIND: "，
# 于是"少写一个前缀"这种漂移不会被发现。
BIND_PREFIXES = (BIND_LABEL + " ",)

# 六条判据的失败原因码（写进 trace 的 failure_class 之外的细项，便于人查）
REASON_OK = "ok"
REASON_RC = "NONZERO_EXIT"
REASON_NO_RECORD = "NO_STRUCTURED_RECORD"
REASON_SELF_ERROR = "RECEIVER_REPORTS_FAILURE"
REASON_SESSION = "SESSION_IDENTITY_MISMATCH"
REASON_NO_ACK_LINE = "ACK_LINE_MISSING"
REASON_BIND = "BIND_NOT_ECHOED"
REASON_TERMINAL = "NO_TERMINAL_EVENT"

TERMINAL_COMPLETED = "completed"
TERMINAL_FAILED = "failed"
TERMINAL_ABSENT = "absent"
TERMINAL_UNKNOWN = "unknown"
TERMINAL_NOT_PROVIDED = ""      # 这一腿的对侧不提供终态证据（桩对侧就是这种）


@dataclass
class Receipt:
    """适配器交回的一份"对侧回执事实"。

    注意每个字段都是**事实**，不是结论：`self_reported_success` 是"对侧自己说它成功了"，
    不是"这件事成功了"。是否采信由 `judge_ack` 判。
    """

    returncode: int | None = None
    has_structured_record: bool = False
    self_reported_success: bool = False
    reported_session_id: str = ""
    ack_line_present: bool = False
    bind_echo: str = ""
    result_body_present: bool = False
    detail: str = ""
    terminal: str = ""            # 对侧这一轮的收口读数：completed／failed／absent／unknown
    body: str = ""                # 对侧答复的**原文**（从结构化记录里解出来的那一段）
    rid: str = ""                 # 回执自报的请求号（通知通道用它证明"指名到这一笔"）
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "returncode": self.returncode,
            "has_structured_record": self.has_structured_record,
            "self_reported_success": self.self_reported_success,
            "reported_session_id": self.reported_session_id,
            "ack_line_present": self.ack_line_present,
            "bind_echo": self.bind_echo,
            "result_body_present": self.result_body_present,
            "terminal": self.terminal,
            "body_bytes": len(self.body.encode("utf-8")),
            "rid": self.rid,
            "detail": self.detail,
        }


def full_lines(text: str) -> list:
    return [ln.strip() for ln in (text or "").splitlines()]


def has_ack_line(text: str) -> bool:
    """要求**独立整行**等于 `ACKED`（去掉首尾空白后逐字相等）。"""
    return ACK_TOKEN in full_lines(text)


def extract_bind_echo(text: str) -> str:
    """取逐字整行回出的那枚绑定串；带尾巴的、写在句子中间的都不算。"""
    for ln in full_lines(text):
        for pref in BIND_PREFIXES:
            if ln.startswith(pref):
                return ln[len(pref):].strip()
    return ""


def last_structured_record(stdout: str, want_type: str = "result") -> dict | None:
    """从输出里取**最后一条可解析的** JSON 对象（其 `type` 等于 `want_type`）。

    取"最后一条"是有理由的：CLI 在结果之后还会打话，取第一条会读到开场白。
    解析失败的行直接跳过——**不返回半成品**，宁可为 `None`（`None` 在判据里是
    "没有结构化回执"，与"有一个说失败的回执"是两条不同的路）。
    """
    rec = None
    for ln in (stdout or "").splitlines():
        t = ln.strip()
        if not t.startswith("{"):
            continue
        try:
            d = json.loads(t)
        except ValueError:
            continue
        if isinstance(d, dict) and (want_type is None or d.get("type") == want_type):
            rec = d
    return rec


def record_self_reports_success(rec: dict | None) -> bool:
    """对侧自报成功＝`is_error` 恰为 False **且** `subtype` 恰为 "success"。

    写 `is_error is not False` 而不是 `if rec.get("is_error")`：缺键、`null`、
    字符串 `"false"` 都不是"没失败"。（真反例里正是缺键与字符串两种形状被旧写法放过。）
    """
    if not isinstance(rec, dict):
        return False
    return rec.get("is_error") is False and rec.get("subtype") == "success"


def judge_ack(Receipt_, target_session_id: str, required_bind: str = "",
              require_terminal: bool = False, require_ack_line: bool = True) -> tuple:
    """PROTOCOL.md §4.2 的六道闸 ＋ 终态那一刀。返回 (acked, reason_code, why)。

    顺序有讲究：先便宜的后花钱的；`REASON_RC` 在前是因为非零退出时
    后面的字段全是不可信半成品。

    `require_terminal` 由适配器的 `capabilities()["terminal_evidence"]` 决定：
    真 CLI（Codex／Qoder）必须查"这一轮调用收口没有"——R19 独立复验用的就是普通流程复现：
    事件流以 `turn.failed` 收尾、或根本没有终态，只要进程退 0、正文够长、绑定串对得上，
    旧版照样登记完成。桩对侧没有事件流可查，不硬要求（否则测一条永远走不到的分支）。
    """
    r = Receipt_
    if r.returncode != 0:
        return False, REASON_RC, "回执进程退出码 %s" % (r.returncode,)
    if require_terminal and r.terminal != TERMINAL_COMPLETED:
        return False, REASON_TERMINAL, "这一轮调用没有合格终态（terminal=%r）⇒ 不采信为送达" % (
            r.terminal or "未提供",)
    if not r.has_structured_record:
        return False, REASON_NO_RECORD, "无可解析的结构化回执（只有散文或被截断的输出）"
    if not r.self_reported_success:
        return False, REASON_SELF_ERROR, "对侧自报失败（is_error 非 False 或 subtype 非 success）"
    if str(r.reported_session_id or "") != str(target_session_id or ""):
        return False, REASON_SESSION, "会话身份不匹配：回执 %r ≠ 目标 %r" % (
            r.reported_session_id, target_session_id)
    if require_ack_line and not r.ack_line_present:
        return False, REASON_NO_ACK_LINE, "缺约定确认行（要求独立整行 %s，正文含词不算）" % ACK_TOKEN
    # 本轮派发文里要求过（required_bind 非空）才要回它；要求本身是硬的：
    # 没有串可回的一律不算（不许把"那就免检"当默认）。
    if required_bind and r.bind_echo != required_bind:
        return False, REASON_BIND, "回执没有逐字整行回出本轮绑定串（got=%r want=%r）" % (
            r.bind_echo, required_bind)
    return True, REASON_OK, "ok"


def scan_terminal(raw: str, completed=("turn.completed",), failed=("turn.failed", "error",
                                                                   "cancelled")) -> str:
    """从事件流里取**最后一个**终态读数：completed／failed／absent／unknown。

    只看 `type` 字段落在名单里的那些行；取"最后"而不是"第一个"，因为一轮调用后面
    可能还有收尾事件。已知局限（写进 ADAPTERS.md，不当能力报）：多轮共用一条流时，
    这里归给"最后一次终态"，**上一轮的 `turn.completed` 会被这一轮读到**。
    把这种借证挡在门外的是归属那一刀（本轮绑定串必须逐字回出）：
    真腿第一跑里 Codex 回的就是**上一轮**那枚串，于是 `BIND_NOT_ECHOED` 拒了整腿
    （原件 `evidence/real-legs/05-codex-stale-bind-*.txt`，反例格见
    `tests/test_qoder_terminal_real_fixture.py::TestStaleAttributionFromRealLegs`）。
    """
    last = TERMINAL_ABSENT
    for ln in (raw or "").splitlines():
        t = ln.strip()
        if not t.startswith("{"):
            continue
        try:
            d = json.loads(t)
        except ValueError:
            last = TERMINAL_UNKNOWN
            continue
        if not isinstance(d, dict):
            continue
        ty = str(d.get("type") or "")
        if ty in completed:
            last = TERMINAL_COMPLETED
        elif ty in failed:
            last = TERMINAL_FAILED
    return last


def scan_result_frame_terminal(raw: str, want_type: str = "result") -> str:
    """**单帧收口**形态的终态读法（与 `scan_terminal` 的事件流词表互不替换）。

    有些对侧 CLI 不是一条事件流，而是最后打一条收口记录：
    `{"type":"result","subtype":"success","is_error":false,…}` —— Qoder 公开 CLI 就是这种。
    这里按"最后一条可解析的 `want_type` 记录"判：

      · 自报成功（`record_self_reports_success`）⇒ `completed`；
      · `is_error` 恰为 True，或 `subtype` 以 `error` 起头 ⇒ `failed`；
      · 有这条记录但成功／失败读不出来 ⇒ `unknown`（两边都不折）；
      · 根本没有这条记录 ⇒ `absent`。

    为什么不让它去认 `turn.completed`：那是**另一家的词表**。把两套名单并成一套
    （`completed=("turn.completed","result")`）看着省一行，实际后果是任何开场白里
    出现 `{"type":"result"` 字样都会被读成"本轮跑完了"。两枚反例分别由
    `tests/test_qoder_terminal_real_fixture.py` 的 test_08／test_09 钉住。
    """
    rec = last_structured_record(raw, want_type)
    if rec is None:
        return TERMINAL_ABSENT
    if not isinstance(rec, dict):
        return TERMINAL_UNKNOWN
    if record_self_reports_success(rec):
        return TERMINAL_COMPLETED
    if rec.get("is_error") is True or str(rec.get("subtype") or "").startswith("error"):
        return TERMINAL_FAILED
    return TERMINAL_UNKNOWN


def judge_result_shape(text: str) -> tuple:
    """回复本体必须是"结论行＋绑定行之外还有非空正文"。

    这里**没有字数下限**：长度是质量打分，非空是防空口令冒充回执。
    审计 §C-3 第 5 条记着这个取舍（旧内核写的是 60 字）。
    """
    lines = [ln for ln in full_lines(text) if ln]
    substance = [ln for ln in lines if ln != ACK_TOKEN and not any(ln.startswith(p) for p in BIND_PREFIXES)]
    if not substance:
        return False, "只有确认行与绑定行，没有正文"
    return True, "ok"


def receipt_from_cli_json(stdout: str, rc: int | None, want_type: str = "result",
                          stream_terminal: str | None = None) -> Receipt:
    """把一次 CLI 调用的 stdout 折成 `Receipt`（适配器与测试共用的参考实现）。

    三个细节都是踩过坑的：
      1. `ack_line_present`／`bind_echo` 只看**解析出来的那条记录内部**（`result` 字段），
         不看整段 stdout——"在外面补一行"不能算过关；
      2. JSON 里的 `result` 要先解码，`REVIEW-BIND`/`Q2C-BIND` 那行躺在字符串内部，
         按 stdout 的物理行去找永远找不到；
      3. `reported_session_id` 取记录里的字段，缺字段就当空（身份不相等 ⇒ 拒）。
    """
    rec = last_structured_record(stdout, want_type)
    inner = ""
    if isinstance(rec, dict):
        val = rec.get("result")
        inner = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    return Receipt(
        returncode=rc,
        has_structured_record=rec is not None,
        self_reported_success=record_self_reports_success(rec),
        reported_session_id=str((rec or {}).get("session_id") or "") if isinstance(rec, dict) else "",
        # 只认**解析出来的那条记录内部**。把正确的绑定串写在 JSON 之外的正文里不算过关
        # （R17 第 4 条实测：旧写法把 stdout 与 result 拼起来找，等于"外面补一行"就能过）。
        ack_line_present=has_ack_line(inner),
        bind_echo=extract_bind_echo(inner),
        result_body_present=bool(inner.strip()),
        body=inner,
        # 终态读数必须真的接进回执。2026-10-04 真腿第三跑就是死在这一格缺席：
        # 这个参数从写下来那天起**没被用过**，于是凡走这条参考实现的腿 `terminal` 恒为 ''，
        # 声明了 `terminal_evidence=True` 的适配器（Qoder）每次真投递都被终态那一刀判
        # `NO_TERMINAL_EVENT terminal='未提供'`。传 None 才表示"这一腿不提供终态"。
        terminal=(TERMINAL_NOT_PROVIDED if stream_terminal is None else stream_terminal),
        rid=str((rec or {}).get("rid") or "") if isinstance(rec, dict) else "",
        detail=str((rec or {}).get("subtype") or "") if isinstance(rec, dict) else "",
    )


def judge_notification_receipt(receipt: "Receipt", rc: int | None, request_id: str) -> tuple:
    """失败通知那一路的送达判定：只认**指名到这一笔**的结构化回执。

    三件同时成立才算送到：退出码 0、末行是可解析 JSON 且自报成功、`rid` 就是这一笔。
    为什么不让"脚本跑完了"顶替回执：`/bin/true` 也退 0，但它什么人都没叫到——
    在册那三条 REJECTED 就是死在这种"我叫过了"的自述上。
    """
    if rc != 0:
        return False, "执行器退出码 %s" % (rc,)
    if not receipt.has_structured_record:
        return False, "执行器没有给出结构化回执（末行不是 JSON）"
    if not receipt.self_reported_success:
        return False, "回执自报失败（is_error 非 False 或 subtype 非 success）"
    if str(receipt.rid or "") != str(request_id):
        return False, "回执请求号不匹配：%r ≠ %r" % (receipt.rid, request_id)
    return True, "ok"
