#!/usr/bin/env python3
"""跟踪九问的**读数形状**：有证据必须报出来，没证据不许编。

为什么单独有一组（2026-10-04 真腿第四跑抓出来的两枚）：

  · `state_now` 只认 `ev=="state"` 的事件，而产品从来不这么发事件名——它发的是
    `created`／`queued`／`delivering`／`delivered`／`started`／`responded`／`acked`。
    结果一条 ACKED 的真记录在九问里报 `UNVERIFIABLE`：**时间线上明明看得见收口，
    读数却说证明不了**。`q2c/trace.py` 自己在十几行上面就写着这个坑（"只认 ev=='state'
    会让三问集体变 NOT_OBSERVED"），但 `_state_now` 漏在同一处。
  · `which_retries` 只认事件字段 `retry_of`，那只有人手敲 `retry-delivery` 才写；
    结果腿自己的补送达发的是 `ev="result_retry_of_delivery"`＋`to_state=DELIVERY_RETRY`。
    原件里两笔各有 1 次补送达（`evidence/real-legs/run-20261004-171323/trace/events.jsonl`），
    九问却报 `NOT_OBSERVED`。

两条都是"读数撒谎"，不是"缺字段"：前者把人引向"跟踪不可信"，后者把已经发生的重投藏起来。
修法只动读法，不动事件；同时钉住反向：没有的证据**不许**被读出来。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from q2c import protocol, trace as trace_mod  # noqa: E402
from q2c.trace import NOT_OBSERVED, UNVERIFIABLE  # noqa: E402

RUN_TRACE = os.path.join(ROOT, "evidence", "real-legs", "run-20261004-171323",
                         "trace", "events.jsonl")


class TestStateNowReadsObservedMigrations(unittest.TestCase):
    def _emit(self, evs, home):
        tr = trace_mod.Trace(home)
        for e in evs:
            tr.emit(e)
        return tr

    def test_01_named_migration_events_answer_state_now(self):
        """产品实际发的事件名（acked／responded／…）必须能回答"现在到哪一步"。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-tr-")
        try:
            tr = self._emit([
                {"request_id": "req-x", "ev": "created", "to_state": "CREATED"},
                {"request_id": "req-x", "ev": "queued", "to_state": "QUEUED"},
                {"request_id": "req-x", "ev": "acked", "to_state": "ACKED"},
            ], home)
            q = tr.nine_questions("req-x")
            self.assertEqual(q.get("state_now"), "ACKED",
                             "时间线末格就是 ACKED，读数不许报 %r" % q.get("state_now"))
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_02_state_event_name_still_works(self):
        """旧形状（真有一条 `ev=="state"`）不许被我改坏——两条路都得给出答案。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-tr-")
        try:
            tr = self._emit([{"request_id": "req-y", "ev": "state", "to_state": "RESPONDED"}], home)
            self.assertEqual(tr.nine_questions("req-y").get("state_now"), "RESPONDED")
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_03_no_state_evidence_stays_unverifiable(self):
        """反向那半边：真的没有状态证据时，不许顺手编一枚出来。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-tr-")
        try:
            tr = self._emit([{"request_id": "req-z", "ev": "note", "note": "只是话"}], home)
            self.assertEqual(tr.nine_questions("req-z").get("state_now"), UNVERIFIABLE)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_04_retries_seen_in_the_real_legs_are_reported(self):
        """`which_retries` 要认结果腿的补送达事件，不是只认人手敲的那一路。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-tr-")
        try:
            tr = self._emit([
                {"request_id": "req-r", "ev": "responded", "to_state": "RESPONDED"},
                {"request_id": "req-r", "ev": "result_retry_of_delivery",
                 "to_state": "DELIVERY_RETRY", "at": "2026-10-04T09:20:17.813132+00:00"},
            ], home)
            got = tr.nine_questions("req-r").get("which_retries")
            self.assertNotEqual(got, NOT_OBSERVED, "有补送达事件却报没发生＝把已发生的重投藏了")
            txt = json.dumps(got, ensure_ascii=False, sort_keys=True)
            self.assertIn("result_retry_of_delivery", txt)
            self.assertIn("DELIVERY_RETRY", txt)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)

    def test_05_no_retry_events_stays_not_observed(self):
        """没发生过的事不许被读出来：这一格防的是"把 delivered 当 retry"这种凑数。"""
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-tr-")
        try:
            tr = self._emit([{"request_id": "req-n", "ev": "delivered", "to_state": "DELIVERED"}],
                            home)
            self.assertEqual(tr.nine_questions("req-n").get("which_retries"), NOT_OBSERVED)
        finally:
            import shutil
            shutil.rmtree(home, ignore_errors=True)


@unittest.skipUnless(os.path.isfile(RUN_TRACE), "真腿第四跑的跟踪原件不在（不造假件顶替）")
class TestNineQuestionsOnRealRun(unittest.TestCase):
    """同一套读法打在**真跑原件**上：四条腿式读数各归各位。"""

    @classmethod
    def setUpClass(cls):
        home = os.path.dirname(os.path.dirname(RUN_TRACE))
        cls.tr = trace_mod.Trace(home)
        with open(RUN_TRACE, encoding="utf-8") as fh:
            cls.rids = sorted({json.loads(l).get("request_id") for l in fh
                               if l.strip() and json.loads(l).get("request_id")})

    def test_06_both_real_legs_answer_state_now(self):
        self.assertEqual(len(self.rids), 2, "真跑原件里应当正好两笔")
        for rid in self.rids:
            q = self.tr.nine_questions(rid)
            self.assertEqual(q.get("state_now"), protocol.ST_ACKED,
                             "%s 的时间线末端是 ACKED，读数却报 %r" % (rid, q.get("state_now")))

    def test_07_real_retries_are_not_hidden(self):
        for rid in self.rids:
            got = self.tr.nine_questions(rid).get("which_retries")
            self.assertNotEqual(got, NOT_OBSERVED, "%s：原件里明明有 result_retry_of_delivery" % rid)

    def test_08_collapsed_delivery_boundary_stays_honest(self):
        """Qoder 那一腿"投递与开始之间无可观测边界"，`when_delivered` 就该是 NOT_OBSERVED。

        这一格钉的是另一侧：修 `state_now` 时不许把 collapsed 的边界**伪造成观测到了**。
        两枚方向里必须恰好一枚有 `when_delivered`（Codex 腿两拍分开），另一枚没有（Qoder 腿合一拍）。
        """
        vals = {rid: self.tr.nine_questions(rid).get("when_delivered") for rid in self.rids}
        observed = [rid for rid, v in vals.items() if v not in (None, NOT_OBSERVED, UNVERIFIABLE)]
        self.assertEqual(len(observed), 1, "两枚都报观测到＝其中一枚在编；两枚都没＝读法失效：%s" % (vals,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
