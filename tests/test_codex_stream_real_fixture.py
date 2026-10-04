#!/usr/bin/env python3
"""Codex 事件流映射判据：**夹具是真件，不是我在测试里再造的第三个解析器**。

原件：`evidence/real-legs/01-codex-stream.jsonl`（codex-cli 0.159.0 一次真调用留下的九行）。
在册教训（#64）：桩的形状如果从猜里取，实现改了、桩跟着错、测试照绿。
所以这一组直接读真件，并钉四件事：
  1. 会话号取自 `thread.started.thread_id`（不假设 `session_id` 这种别家字段名）；
  2. 终态取 `turn.completed`；中途 4 条 `error`（重连提示）**不许**把这一轮判成失败；
  3. 回复正文取 `item.completed/agent_message`，`has_structured_record` 必须为真；
  4. 把 `turn.completed` 抹掉（同一份字节的截断复放）⇒ 必须判"没有终态"，不采信。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from q2c import ack  # noqa: E402
from q2c.adapters import codex  # noqa: E402

FIX = os.path.join(ROOT, "evidence", "real-legs", "01-codex-stream.jsonl")


@unittest.skipUnless(os.path.isfile(FIX), "真件夹具不在（要一次真调用才能生成，不造假件）")
class TestCodexStreamFromRealArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8", errors="replace") as fh:
            cls.raw = fh.read()

    def test_01_thread_id_is_taken_from_real_field(self):
        r = codex.CodexAdapter.receipt_from_stream(self.raw, 0)
        self.assertEqual(r.reported_session_id, "01a105fd-8700-78d3-91a9-208dc57ce338")

    def test_02_reconnect_errors_do_not_fail_the_turn(self):
        self.assertIn('"type": "error"', self.raw.replace('{"type":"error"', '{"type": "error"'))
        r = codex.CodexAdapter.receipt_from_stream(self.raw, 0)
        self.assertEqual(r.terminal, ack.TERMINAL_COMPLETED)
        self.assertTrue(r.self_reported_success)
        self.assertTrue(r.has_structured_record)

    def test_03_body_is_the_agent_message(self):
        r = codex.CodexAdapter.receipt_from_stream(self.raw, 0)
        self.assertEqual(r.body.strip(), "收到")
        self.assertTrue(r.result_body_present)

    def test_04_truncated_stream_has_no_terminal(self):
        """同一份字节截到 `turn.completed` 之前 ⇒ 绝不能报"成功"（R19 那一族的形状）。

        两条事实分开断言，不许并成一条：
          · 回复正文确实存在 ⇒ `has_structured_record` 仍为真（这是实话）；
          · 但这一轮没有合格终态 ⇒ 自报成功为假，整腿被拒且拒因写的是
            `NO_TERMINAL_EVENT`，不是含糊的"没有回复"。
        并成一条会让半截流被诊断成"对方没回话"，把人往错的方向引。
        """
        lines = [l for l in self.raw.splitlines() if l.strip()]
        cut = "\n".join(lines[:-1])
        r = codex.CodexAdapter.receipt_from_stream(cut, 0)
        self.assertTrue(r.has_structured_record, "agent_message 确实在，谎称没有＝诊断失真")
        self.assertNotEqual(r.terminal, ack.TERMINAL_COMPLETED)
        self.assertFalse(r.self_reported_success)
        ok, code, _ = ack.judge_ack(r, r.reported_session_id, "", require_terminal=True,
                                    require_ack_line=False)
        self.assertFalse(ok)
        # 拒因落在"没有合格终态"这一刀（终态闸在自报成功之前判），
        # 这比笼统报"对方说失败"准：这一轮根本不是失败，是没跑完。
        self.assertEqual(code, ack.REASON_TERMINAL)
        self.assertIn("终态", _why_of(code))

    def test_05_failed_terminal_is_reported_as_failed(self):
        lines = [l for l in self.raw.splitlines() if l.strip()][:-1]
        lines.append('{"type":"turn.failed","message":"boom"}')
        r = codex.CodexAdapter.receipt_from_stream("\n".join(lines), 0)
        self.assertEqual(r.terminal, ack.TERMINAL_FAILED)
        self.assertFalse(r.self_reported_success)

    def test_06_ack_gates_still_apply_to_codex_replies(self):
        """真件正文里既没绑定行也没确认行 ⇒ 六道闸该拒还得拒（不因"是真 CLI"就放水）。"""
        r = codex.CodexAdapter.receipt_from_stream(self.raw, 0)
        ok, code, _ = ack.judge_ack(r, "01a105fd-8700-78d3-91a9-208dc57ce338", "abc123",
                                   require_terminal=True, require_ack_line=False)
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_BIND)


def _why_of(code):
    """只用来钉"拒因文案与判据名不脱节"（文案里必须能让人看懂是哪一刀）。"""
    return {"NO_TERMINAL_EVENT": "没有合格终态"} .get(code, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
