#!/usr/bin/env python3
"""送达确认（ACK）判据：六闸逐条正反例 + 真子进程回执。

对应任务书 §14 的 "ACK" 与 §2 的 transport ACK，迁移自实验根的 F1–F5／K65／K37／K13 一族
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 C 组）。

这一组里最值钱的不是"合规回执被接受"，而是**四个不合格的都不被接受**：
每一格都对应一次真出过的冒充。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import ack, protocol, transport  # noqa: E402
from tests import helpers  # noqa: E402


def rec(**over):
    kw = dict(returncode=0, has_structured_record=True, self_reported_success=True,
              reported_session_id="lb-target", ack_line_present=True,
              bind_echo="nonce-1", result_body_present=True)
    kw.update(over)
    return ack.Receipt(**kw)


class TestSixGates(unittest.TestCase):
    """六闸：每一闸单独把它掰断，都必须拒；全对才认。"""

    SID = "lb-target"
    NONCE = "nonce-1"

    def _judge(self, r, require_line=True):
        return ack.judge_ack(r, self.SID, self.NONCE, require_ack_line=require_line)

    def test_01_all_six_hold(self):
        ok, code, why = self._judge(rec())
        self.assertTrue(ok, why)
        self.assertEqual(code, ack.REASON_OK)

    def test_02_nonzero_exit_rejected(self):
        ok, code, _ = self._judge(rec(returncode=7))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_RC)

    def test_03_no_structured_record_rejected(self):
        ok, code, _ = self._judge(rec(has_structured_record=False))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_NO_RECORD)

    def test_04_self_reported_failure_rejected(self):
        """真反例：is_error=true、退 7、正文里同样带着 ACKED 字样的回执。"""
        ok, code, _ = self._judge(rec(self_reported_success=False))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_SELF_ERROR)

    def test_05_session_identity_must_match(self):
        ok, code, _ = self._judge(rec(reported_session_id="somebody-elses-session"))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_SESSION)

    def test_06_missing_ack_line_rejected(self):
        ok, code, _ = self._judge(rec(ack_line_present=False))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_NO_ACK_LINE)

    def test_07_bind_must_be_echoed(self):
        ok, code, _ = self._judge(rec(bind_echo=""))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_BIND)

    def test_08_other_round_bind_rejected(self):
        ok, code, _ = self._judge(rec(bind_echo="last-rounds-nonce"))
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_BIND)

    def test_09_no_bind_required_when_never_asked(self):
        """本轮没要求回绑定串（旧单／崩在派发之前）⇒ 无从要求，不能判成"没回出"。
        这一格是修判据咬错对象时留下的钉：当时四格一起红，看着像实现推进。"""
        ok, code, _ = ack.judge_ack(rec(bind_echo=""), self.SID, "")
        self.assertTrue(ok)
        self.assertEqual(code, ack.REASON_OK)

    def test_10_terminal_gate_only_for_adapters_with_evidence(self):
        bad = rec(terminal=ack.TERMINAL_FAILED)
        ok, code, _ = ack.judge_ack(bad, self.SID, self.NONCE, require_terminal=True)
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_TERMINAL)
        ok2, _c, _w = ack.judge_ack(bad, self.SID, self.NONCE, require_terminal=False)
        self.assertTrue(ok2, "没有终态证据的桩对侧不该被这一刀砍")

    def test_11_result_leg_does_not_require_ack_line(self):
        """收**答复**那一腿要归因与可信收口，不要求 ACKED（ACKED 属于结果腿）。"""
        r = rec(ack_line_present=False)
        ok, code, _ = ack.judge_ack(r, self.SID, self.NONCE, require_ack_line=False)
        self.assertTrue(ok)
        self.assertEqual(code, ack.REASON_OK)


class TestParsingIsNotSubstring(unittest.TestCase):
    """判据落在**解析出来的字段**上，不落在整段 stdout 的子串上。"""

    def _stdout(self, inner="正文。\nQ2C-BIND: abc\nACKED\n", **over):
        doc = {"type": "result", "is_error": over.get("is_error", False),
               "subtype": over.get("subtype", "success"),
               "session_id": over.get("session_id", "lb-1"), "result": inner}
        return json.dumps(doc, ensure_ascii=False)

    def test_12_word_in_prose_is_not_an_ack(self):
        raw = "这条回执里没有确认行，只是正文里出现过 ACKED 这个词而已。"
        r = ack.receipt_from_cli_json(raw, 0)
        self.assertFalse(r.has_structured_record)
        ok, code, _ = ack.judge_ack(r, "lb-1", "abc")
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_NO_RECORD)

    def test_13_bind_outside_json_does_not_count(self):
        """R17 第 4 条实测：旧写法把 stdout 与 result 拼起来找，等于"外面补一行"就能过关。"""
        raw = "Q2C-BIND: abc\n" + self._stdout(inner="回执正文，里面**没有**绑定行。\nACKED\n")
        r = ack.receipt_from_cli_json(raw, 0)
        self.assertEqual(r.bind_echo, "", "绑定串写在 JSON 之外不该被认出来")
        ok, code, _ = ack.judge_ack(r, "lb-1", "abc")
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_BIND)

    def test_14_last_record_wins(self):
        raw = self._stdout(inner="开场白\n") + "\n" + self._stdout(inner="正文。\nQ2C-BIND: abc\nACKED\n")
        r = ack.receipt_from_cli_json(raw, 0)
        self.assertTrue(r.ack_line_present)
        self.assertEqual(r.bind_echo, "abc")

    def test_15_is_error_string_false_is_not_success(self):
        r = ack.receipt_from_cli_json(self._stdout(is_error="false"), 0)
        self.assertFalse(r.self_reported_success)

    def test_16_is_error_missing_is_not_success(self):
        doc = {"type": "result", "subtype": "success", "session_id": "lb-1", "result": "ACKED"}
        self.assertFalse(ack.record_self_reports_success(doc))

    def test_17_ack_line_must_be_whole_line(self):
        self.assertFalse(ack.has_ack_line("请查看 ACKED 字样"))
        self.assertTrue(ack.has_ack_line("话话说完\n  ACKED  \n"))

    def test_18_bind_with_tail_is_not_a_match(self):
        """带尾巴的绑定行不等于绑定串（K68 六档之一）。

        这里断言的是**判决性质**而不是抽取形状：`extract_bind_echo` 取整行剩下的内容，
        带尾巴时它一定不等于本轮那枚串，于是 judge_ack 必须拒。
        把"抽取就返回空"当判据会让"只回了一部分"这种形状在证据里消失。
        """
        tail = ack.extract_bind_echo("Q2C-BIND: abc 还有一些话")
        self.assertNotEqual(tail, "abc")
        ok, code, _ = ack.judge_ack(rec(bind_echo=tail), "lb-target", "abc")
        self.assertFalse(ok)
        self.assertEqual(code, ack.REASON_BIND)
        self.assertEqual(ack.extract_bind_echo("Q2C-BIND: abc"), "abc")

    def test_19_malformed_lines_are_skipped_not_guessed(self):
        raw = "{坏了的 JSON\n" + self._stdout()
        r = ack.receipt_from_cli_json(raw, 0)
        self.assertTrue(r.has_structured_record)
        self.assertEqual(r.reported_session_id, "lb-1")


class TestBodySubstanceIsObservation(unittest.TestCase):
    """正文除协议两行之外还有没有内容＝**观测**，不是送达闸。

    钉的是今天定下来的一条边界：对侧按规定就该回那两行；再要求"更多字"
    等于桥在判答复质量（§5 禁止，旧内核 MIN_BODY=60 同一族已被判移出产品路径）。
    想要更严的答复形状，是发送方在自己的话术或结果谓词里提，不由桥代做。
    """

    def test_34_shape_rule_reports_but_does_not_reject(self):
        ok, why = ack.judge_result_shape("Q2C-BIND: abc\nACKED\n")
        self.assertFalse(ok)
        self.assertIn("没有正文", why)
        # 同一份内容走六道闸：只要 rc／结构／自报成功／会话号／确认行／绑定串都对，就该认
        r = ack.Receipt(returncode=0, has_structured_record=True, self_reported_success=True,
                        reported_session_id="lb-t", ack_line_present=True, bind_echo="abc",
                        result_body_present=False, body="Q2C-BIND: abc\nACKED\n")
        acked, code, _ = ack.judge_ack(r, "lb-t", "abc", require_ack_line=True)
        self.assertTrue(acked, code)

    def test_35_observation_reaches_the_readings(self):
        """观测要能在读数里看到，否则"降级为观测"=悄悄丢掉。"""
        h = helpers.TempHome(mode="protocol-lines-only")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            for _ in range(6):
                if t.pump(rid).get("state") == protocol.ST_ACKED:
                    break
            rec = t.read(rid)["record"]
            self.assertEqual(rec["state"], protocol.ST_ACKED, "只回协议两行也应算送达")
            self.assertTrue(rec.get("result_body_only_protocol"),
                            "这一格降级成观测后，读数里必须还看得见")
            evs = [e for e in t.trace.events(rid) if e.get("ev") == "responded"]
            self.assertTrue(evs and evs[0].get("result_body_only_protocol") is True,
                            "跟踪里丢了这一项")
        finally:
            h.close()


class TestTerminalScan(unittest.TestCase):
    def test_20_completed(self):
        raw = '{"type":"turn.started"}\n{"type":"item.completed"}\n{"type":"turn.completed"}\n'
        self.assertEqual(ack.scan_terminal(raw), ack.TERMINAL_COMPLETED)

    def test_21_failed_last_wins(self):
        raw = '{"type":"turn.completed"}\n{"type":"turn.failed"}\n'
        self.assertEqual(ack.scan_terminal(raw), ack.TERMINAL_FAILED)

    def test_22_absent_when_only_non_terminal(self):
        raw = '{"type":"turn.started"}\n{"type":"item.started"}\n'
        self.assertEqual(ack.scan_terminal(raw), ack.TERMINAL_ABSENT)

    def test_23_r19_shape_zero_exit_but_no_terminal(self):
        """R19 独立复验那一条：退 0、正文够长、BIND 对得上，可这一轮根本没跑完。"""
        body = "ARGUMENTS blah\n"
        r = ack.receipt_from_cli_json(body, 0, stream_terminal=ack.scan_terminal(body))
        ok, code, _ = ack.judge_ack(r, "lb-1", "", require_terminal=True)
        self.assertFalse(ok)


class TestNotificationReceipt(unittest.TestCase):
    """失败通知那一路：只认**指名到这一笔**的结构化回执。"""

    def test_24_exit_zero_alone_is_not_delivery(self):
        r = ack.receipt_from_cli_json("", 0)
        ok, why = ack.judge_notification_receipt(r, 0, "req-1")
        self.assertFalse(ok)
        self.assertIn("结构化回执", why)

    def test_25_other_rid_not_counted(self):
        raw = json.dumps({"type": "result", "is_error": False, "subtype": "success",
                          "rid": "req-OTHER", "result": "delivered"})
        r = ack.receipt_from_cli_json(raw, 0, want_type=None)
        ok, why = ack.judge_notification_receipt(r, 0, "req-1")
        self.assertFalse(ok)
        self.assertIn("不匹配", why)

    def test_26_matching_rid_ok(self):
        raw = json.dumps({"type": "result", "is_error": False, "subtype": "success",
                          "rid": "req-1", "result": "delivered"})
        r = ack.receipt_from_cli_json(raw, 0, want_type=None)
        self.assertTrue(ack.judge_notification_receipt(r, 0, "req-1")[0])

    def test_27_whole_word_rid_match_not_substring(self):
        """K37 那族：`req-1-OTHER` 不能命中 `req-1`。"""
        raw = json.dumps({"type": "result", "is_error": False, "subtype": "success",
                          "rid": "req-1-OTHER", "result": "x"})
        r = ack.receipt_from_cli_json(raw, 0, want_type=None)
        self.assertFalse(ack.judge_notification_receipt(r, 0, "req-1")[0])


class TestAckThroughRealSubprocess(unittest.TestCase):
    """同样六闸，跑在真子进程上（`Q2C_LB_MODE` 决定回执形状）。"""

    def _run(self, mode):
        h = helpers.TempHome(mode=mode)
        try:
            t, sid, rid = h.sessions()
            env = h.request(sender=sid, receiver=rid, session_id=sid)
            res = t.send(env, wait=True)
            return h, res
        except Exception:
            h.close()
            raise

    def test_28_good_receipt_reaches_ack(self):
        h, res = self._run("ack")
        try:
            self.assertEqual(res["pump"]["state"], protocol.ST_ACKED)
            # 这一格不许是"没起进程也能过"：两腿各起一次应答器，计数必须看得见
            self.assertGreaterEqual(h.responder_calls(), 2,
                                    "应答器一次都没起 ⇒ 这格在测空，不叫端到端")
        finally:
            h.close()

    def test_29_no_ack_line_never_reaches_ack(self):
        """no-ack 那形状在**收答复**那一腿是合法的（有归因、有正文），
        但作为结果腿的确认不合格 ⇒ 链路到不了 ACKED。"""
        h, res = self._run("no-ack")
        try:
            self.assertNotEqual((res["pump"] or {}).get("state"), protocol.ST_ACKED)
            self.assertEqual((res["pump"] or {}).get("failure_class"), "BIND_NOT_ECHOED")
        finally:
            h.close()

    def test_30_wrong_session_never_reaches_ack(self):
        h, res = self._run("wrong-session")
        try:
            self.assertNotEqual((res["pump"] or {}).get("state"), protocol.ST_ACKED)
            self.assertIn("身份", (res["pump"] or {}).get("why", ""))
        finally:
            h.close()

    def test_31_self_error_receipt_rejected(self):
        h, res = self._run("self-error")
        try:
            self.assertNotEqual((res["pump"] or {}).get("state"), protocol.ST_ACKED)
            self.assertEqual((res["pump"] or {}).get("failure_class"),
                             "RECEIVER_REPORTS_FAILURE")
            self.assertGreaterEqual(h.responder_calls(), 1)
        finally:
            h.close()

    def test_32_no_json_receipt_rejected(self):
        h, res = self._run("no-json")
        try:
            self.assertNotEqual((res["pump"] or {}).get("state"), protocol.ST_ACKED)
            self.assertIn((res["pump"] or {}).get("failure_class"),
                          ("RESULT_BODY_EMPTY", "NO_STRUCTURED_RECORD"))
        finally:
            h.close()

    def test_33_bind_outside_json_rejected_end_to_end(self):
        h, res = self._run("tail-bind")
        try:
            self.assertNotEqual((res["pump"] or {}).get("state"), protocol.ST_ACKED)
            self.assertEqual((res["pump"] or {}).get("failure_class"), "BIND_NOT_ECHOED")
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
