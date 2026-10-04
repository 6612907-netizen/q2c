#!/usr/bin/env python3
"""Qoder 腿的终态读法：单帧收口形态（真件夹具，不是我在测试里再造的形状）。

原件：`evidence/real-legs/04-qoder-result-frame.txt`
     ＝ 2026-10-04 真腿第三跑里 Qoder CLI 对 `req-d782de7d548d4075` 的真实 stdout
       （逐字节复分自 `run-20261004-164619/state/inflight/…／msg-d504….a1.out`，`cmp` 已证一致）。

那一跑里这一腿被判 `NO_TERMINAL_EVENT terminal='未提供'`，而**回声明明是合格的**：
`{"type":"result","subtype":"success","is_error":false,…}`，正文里逐字回了
`Q2C-BIND: 03923b5c6721f5d8` 与 `ACKED`。根因在产品侧：Qoder 适配器声明了
`terminal_evidence=True`，却用 **Codex 的事件词表**（`turn.completed`）去读 Qoder 的
单帧收口 ⇒ 真投递**永远**被这一刀拒掉。

修法不许变成放水，所以这一组同时钉住两个方向：
  · 正向：Qoder 词表读出来的合格终态必须被采信（今天红，修完绿）；
  · 反向：合格帧缺失／自报失败／形状读不出 ⇒ 仍须拒，且拒因分别可指；
  · 反串词：同一份 Qoder 字节喂给 Codex 词表**不许**变 completed，
    同一份 Codex 事件流喂给单帧读法**必须**是 absent
    —— 钉"我没有把两套词表并成一套什么都算"。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from q2c import ack, protocol  # noqa: E402
from q2c.adapters import base as adapters_base, qoder  # noqa: E402
from q2c.adapters.codex import CodexAdapter  # noqa: E402

FIX = os.path.join(ROOT, "evidence", "real-legs", "04-qoder-result-frame.txt")
FIX_RC = FIX + ".rc"
CODEX_FIX = os.path.join(ROOT, "evidence", "real-legs", "01-codex-stream.jsonl")

REAL_SESSION = "c5d43f9b-d793-4119-8111-ed268be78c15"
REAL_BIND = "03923b5c6721f5d8"


def _frame_of(raw: str) -> dict:
    for ln in raw.splitlines():
        ln = ln.strip()
        if ln.startswith("{"):
            return json.loads(ln)
    raise AssertionError("夹具里没有结构化帧")


@unittest.skipUnless(os.path.isfile(FIX), "真件夹具不在（不造假件顶替）")
class TestQoderTerminalFromRealArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8", errors="replace") as fh:
            cls.raw = fh.read()
        cls.frame = _frame_of(cls.raw)

    # ---- 正向：这一腿本来就该被采信 --------------------------------------

    def test_01_result_frame_is_the_terminal_for_this_cli(self):
        self.assertEqual(ack.scan_result_frame_terminal(self.raw), ack.TERMINAL_COMPLETED)

    def test_02_adapter_collects_real_capture_as_completed(self):
        """走适配器那一路（`collect_late`），不是只测工具函数。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-qterm-")
        try:
            ad = qoder.QoderAdapter({"home": home})
            mid = "msg-fixture01"
            d = os.path.join(home, "state", "inflight", REAL_SESSION)
            os.makedirs(d, exist_ok=True)
            cap = os.path.join(d, "%s.a1.out" % mid)
            with open(cap, "w", encoding="utf-8") as fh:
                fh.write(self.raw)
            with open(cap + ".rc", "w", encoding="utf-8") as fh:
                fh.write("0\n")
            h = adapters_base.Handle(adapter=qoder.NAME, role="receiver",
                                     provider_session_id=REAL_SESSION)
            h.attempt = 1
            env = protocol.make_request(sender="qs-fake-sender", receiver="qs-fake-receiver",
                                        session_id="qs-logic-1", handoff_type="selfcheck",
                                        payload="真件复放", request_id="req-fixture01",
                                        message_id=mid)
            r = ad.collect_late(h, env)
            self.assertEqual(r.returncode, 0, "退出码侧件要真读进来")
            self.assertEqual(r.terminal, ack.TERMINAL_COMPLETED)
            self.assertEqual(r.reported_session_id, REAL_SESSION)
            self.assertEqual(r.bind_echo, REAL_BIND)
            self.assertTrue(r.ack_line_present)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_03_six_gates_plus_terminal_accept_this_real_reply(self):
        """整条判据链：这一腿今天红在哪里，就在这一格看见。"""
        r = ack.receipt_from_cli_json(self.raw, 0,
                                      stream_terminal=ack.scan_result_frame_terminal(self.raw))
        ok, code, why = ack.judge_ack(r, REAL_SESSION, required_bind=REAL_BIND,
                                      require_terminal=True, require_ack_line=False)
        self.assertTrue(ok, "合格答复被判拒：code=%s why=%s" % (code, why))
        self.assertEqual(code, ack.REASON_OK)

    def test_04_ack_line_gate_still_applies_when_the_leg_demands_it(self):
        """结果腿不要求 ACKED 行（那是送达腿的约定），送达腿要求 —— 两档各测各的。

        这一格用真件把"送达档"也跑一遍：正文里 `ACKED` 确实独立成行，所以两档都该过；
        它钉的是"我为了修终态而顺手把确认行那道闸也放掉了"这种改法。
        """
        r = ack.receipt_from_cli_json(self.raw, 0, stream_terminal=ack.scan_result_frame_terminal(self.raw))
        ok, code, _ = ack.judge_ack(r, REAL_SESSION, required_bind=REAL_BIND,
                                    require_terminal=True, require_ack_line=True)
        self.assertTrue(ok, "正文里 `ACKED` 独立整行确实在（真件），送达档也该过")

    # ---- 反向：三种坏形状仍须拒 ------------------------------------------

    def test_05_error_frame_reads_as_failed_not_absent(self):
        bad = copy.deepcopy(self.frame)
        bad["is_error"] = True
        bad["subtype"] = "error_during_execution"
        raw = "MCP issues detected.\n" + json.dumps(bad, ensure_ascii=False)
        self.assertEqual(ack.scan_result_frame_terminal(raw), ack.TERMINAL_FAILED)
        r = ack.receipt_from_cli_json(raw, 0, stream_terminal=ack.TERMINAL_FAILED)
        ok, code, _ = ack.judge_ack(r, REAL_SESSION, required_bind=REAL_BIND, require_terminal=True,
                                    require_ack_line=False)
        self.assertFalse(ok)

    def test_06_missing_result_frame_is_absent(self):
        raw = "MCP issues detected.\n" + json.dumps({"type": "assistant", "text": "在想了"},
                                                    ensure_ascii=False)
        self.assertEqual(ack.scan_result_frame_terminal(raw), ack.TERMINAL_ABSENT)
        r = ack.receipt_from_cli_json(raw, 0, stream_terminal=ack.TERMINAL_ABSENT)
        ok, code, why = ack.judge_ack(r, REAL_SESSION, required_bind=REAL_BIND,
                                      require_terminal=True, require_ack_line=False)
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_TERMINAL, "缺终态要落在终态那一刀，不是含糊的\"没回复\"：%s" % why)

    def test_07_unreadable_shape_is_unknown_not_folded_to_either_side(self):
        """有 result 帧但成功／失败读不出来 ⇒ unknown（既不采信也不谎报失败）。"""
        rec = {"type": "result", "session_id": REAL_SESSION,
               "result": "Q2C-BIND: %s\nACKED" % REAL_BIND}
        raw = json.dumps(rec, ensure_ascii=False)
        self.assertEqual(ack.scan_result_frame_terminal(raw), ack.TERMINAL_UNKNOWN)

    # ---- 反串词：两套词表不许并成一套 ------------------------------------

    def test_08_codex_vocabulary_does_not_claim_a_qoder_frame(self):
        """Codex 事件词表读 Qoder 的字节必须**不是** completed。

        这一格是"修法不是放水"的反证：如果有人图省事把两套名单并到一起
        （`completed=("turn.completed","result")`），这一腿会照样过，
        但下一轮任何带 `{"type":"result"}` 字样的开场白都会被当成本轮跑完。
        """
        self.assertNotEqual(ack.scan_terminal(self.raw), ack.TERMINAL_COMPLETED)

    @unittest.skipUnless(os.path.isfile(CODEX_FIX), "Codex 真件夹具不在")
    def test_09_single_frame_reader_does_not_claim_a_codex_stream(self):
        with open(CODEX_FIX, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        self.assertEqual(ack.scan_result_frame_terminal(raw), ack.TERMINAL_ABSENT,
                         "Codex 的收口是 `turn.completed` 事件，不是 result 帧；"
                         "单帧读法在这里必须报 absent（不许顺手在事件流里找个 type=result 就算数）")

    def test_10_stream_terminal_is_not_silently_dropped(self):
        """`stream_terminal` 这个参数必须真的进回执。

        这是今天那一刀背后更深的一层：参数从写下那天起没被用过，
        于是凡是走这条参考实现又声明 `terminal_evidence=True` 的适配器，
        终态恒为 ''（渲染成"未提供"）⇒ 真投递**永远**被终态闸拒。
        `None` 才表示"这一腿不提供终态"（桩对侧就是那种），两者不许混。
        """
        r_given = ack.receipt_from_cli_json(self.raw, 0, stream_terminal=ack.TERMINAL_COMPLETED)
        self.assertEqual(r_given.terminal, ack.TERMINAL_COMPLETED)
        r_none = ack.receipt_from_cli_json(self.raw, 0)
        self.assertEqual(r_none.terminal, ack.TERMINAL_NOT_PROVIDED)
        self.assertNotEqual(r_none.terminal, ack.TERMINAL_ABSENT,
                            "『不提供终态』与『提供了但没有终态』是两件事，不许并成一枚读数")


@unittest.skipUnless(os.path.isfile(os.path.join(
        ROOT, "evidence", "real-legs", "05-codex-stale-bind-stream.jsonl")), "真件夹具不在")
class TestStaleAttributionFromRealLegs(unittest.TestCase):
    """真腿第三跑里 Codex 那一腿的原件：它回的是**上一轮**的请求号与绑定串。

    复用同一条 thread 时，上一跑留在队列里那条消息先被 answered：
    流里 `turn.completed` 确实在（本轮真跑了），正文里的会话号也对得上，
    但 `req-48de02e598f94510` / `Q2C-BIND: 0e84a8a0f5fba931` 都不是本轮的。
    这一格钉的就是"归属那一刀不许因为看起来跑完了就放水"——
    修法是每次真跑用一条**新** thread／新会话（`tools/real-handoff.py` 的 `--fresh`），
    而不是把绑定串这道闸松掉。
    """

    THIS_RID = "req-8de81c391e794b25"
    THIS_BIND = "19c8af2b39acc04c"
    THREAD = "01a10609-f0c7-7a62-a789-c4a07c37de62"

    @classmethod
    def setUpClass(cls):
        p = os.path.join(ROOT, "evidence", "real-legs", "05-codex-stale-bind-stream.jsonl")
        with open(p, encoding="utf-8", errors="replace") as fh:
            cls.raw = fh.read()
        cls.r = CodexAdapter.receipt_from_stream(cls.raw, 0)

    def test_11_the_turn_did_complete_and_identity_matches(self):
        """两件事先钉住：本轮确实跑完、会话号确实对得上（拒因不在这两处）。"""
        self.assertEqual(self.r.terminal, ack.TERMINAL_COMPLETED)
        self.assertEqual(self.r.reported_session_id, self.THREAD)

    def test_12_stale_bind_is_refused_even_though_the_turn_completed(self):
        ok, code, why = ack.judge_ack(self.r, self.THREAD, required_bind=self.THIS_BIND,
                                      require_terminal=True, require_ack_line=False)
        self.assertFalse(ok, "上一轮的绑定串被当成本轮送达 ⇒ 归属闸失效")
        self.assertEqual(code, ack.REASON_BIND)
        self.assertIn(self.THIS_BIND, why)
        self.assertEqual(self.r.bind_echo, "0e84a8a0f5fba931", "回的是**别的一轮**那枚串")

    def test_13_the_stale_reply_names_a_different_request(self):
        """正文自报的请求号与本轮不同 ⇒ 这是"另一笔的答复"，不是"这一笔的迟到答复"。"""
        self.assertIn("req-48de02e598f94510", self.r.body)
        self.assertNotIn(self.THIS_RID, self.r.body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
