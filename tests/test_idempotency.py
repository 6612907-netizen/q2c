#!/usr/bin/env python3
"""幂等与重复投递判据（任务书 §14 的 idempotency ＋ duplicate delivery）。

迁移自实验根的 C1／F6／K6／K6b／H1／selftest#6 一族
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 A 组）。

产品语义收窄写死在这里（这是新边界与旧实现的关键差）：
**幂等抑制的是"重复投递"，不是"重复完成"**。同一 `idempotency_key` 的第二条消息
只会留下 `duplicate_suppressed` 证据，不会替任何人宣布这件事办完了。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import ledger, protocol, transport  # noqa: E402
from tests import helpers  # noqa: E402


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")
        self.t, self.sender, self.receiver = self.h.sessions()

    def tearDown(self):
        self.h.close()

    def _send(self, idem="idem-fixed-1", **over):
        env = self.h.request(sender=self.sender, receiver=self.receiver,
                             session_id=self.sender, idempotency_key=idem, **over)
        return self.t.create(env), env

    def test_01_first_create_registers(self):
        """收单即入队：`CREATED` 与 `QUEUED` 各留一条事件，落账状态是 `QUEUED`。

        过去这一格断言"状态是 CREATED"，那是把"收单"当成一个可停留的态——
        实际它只存在两行事件之间；判据跟着口径改，不许为了旧期望把状态机拖回去。
        """
        res, env = self._send()
        self.assertEqual(res["status"], "created")
        self.assertEqual(res["state"], protocol.ST_QUEUED)
        evs = [(e.get("ev"), e.get("to_state")) for e in self.t.trace.events(env.request_id)]
        self.assertIn(("created", protocol.ST_CREATED), evs)
        self.assertIn(("queued", protocol.ST_QUEUED), evs)
        rec = self.t.read(env.request_id)["record"]
        self.assertEqual(rec["idempotency_key"], "idem-fixed-1")

    def test_02_same_key_second_time_suppressed(self):
        res1, env1 = self._send()
        res2, env2 = self._send()          # 同幂等键、不同 request_id
        self.assertEqual(res2["status"], "duplicate_suppressed")
        self.assertEqual(res2["request_id"], env1.request_id)
        got = self.t.read()
        self.assertEqual(len(got["records"]), 1, "台账里出现了第二笔＝幂等没牙")

    def test_03_suppression_leaves_evidence(self):
        """被抑制这件事本身是通信事实，必须留痕、不许静默丢。"""
        self._send()
        _res2, env2 = self._send()
        evs = self.t.trace.events(env2.request_id)
        dups = [e for e in self.t.trace.events() if e.get("ev") == "duplicate_suppressed"]
        self.assertTrue(dups, "抑制了却没留一行")
        self.assertEqual(dups[0]["idem"], "idem-fixed-1")

    def test_04_suppression_does_not_move_state(self):
        res1, env = self._send()
        before = self.t.read(env.request_id)["record"]["state"]
        for _ in range(3):
            self._send()
        after = self.t.read(env.request_id)["record"]["state"]
        self.assertEqual(before, after, "重复投来把状态推进了＝幂等变成了一次副作用")

    def test_05_different_keys_are_separate(self):
        r1, _ = self._send(idem="idem-a")
        r2, _ = self._send(idem="idem-b")
        self.assertEqual(r1["status"], "created")
        self.assertEqual(r2["status"], "created")
        self.assertEqual(len(self.t.read()["records"]), 2)

    def test_06_pump_twice_does_not_rerun_receiver(self):
        """重复 scan／重复推进不重复叫醒（K6 那一族的直接机器测）。"""
        res, env = self._send()
        rid = res["request_id"]
        for _ in range(3):
            self.t.pump(rid)
        recv_calls = self.h.responder_calls(session=self.h.provider_of(self.t, self.receiver))
        self.assertLessEqual(recv_calls, 1,
                             "对侧被起了 %d 次：结果已在案时只许补送达" % recv_calls)

    def test_07_failure_does_not_enter_the_dedupe_set_as_done(self):
        """F6 那一族：投递失败过的一笔，不能被"同键第二张"顶掉，也不能反过来污染判定。

        这里断言的是**方向**：失败 ⇒ 记录还在、状态不是 ACKED、幂等仍在抑制重复投递。
        旧内核的坏形状是"失败回执进了已完成去重集"，于是同提交第二张真单被顶掉。
        """
        self.h.close()
        self.h = helpers.TempHome(mode="no-ack")
        self.t, self.sender, self.receiver = self.h.sessions()
        res, env = self._send(idem="idem-fail-1")
        rid = res["request_id"]
        out = self.t.pump(rid)
        self.assertNotEqual(out.get("state"), protocol.ST_ACKED)
        rec = self.t.read(rid)["record"]
        self.assertNotEqual(rec["state"], protocol.ST_ACKED)
        again = self._send(idem="idem-fail-1")
        self.assertEqual(again[0]["status"], "duplicate_suppressed")

    def test_08_read_only_never_creates_dirs(self):
        """只读动作零写入（R9 第 3 条：核查在被核查的根里留下了 state/）。"""
        h = helpers.TempHome(mode="ack")
        try:
            fresh = os.path.join(h.home, "fresh-root")
            t = transport.Transport(fresh)
            before = sorted(os.listdir(h.home))
            got = t.read()                       # 只读
            self.assertEqual(got.get("ledger"), "ABSENT",
                             "新根上读到 ABSENT 才对，折成空表就是假绿")
            self.assertFalse(os.path.exists(os.path.join(fresh, "state")),
                             "只读命令建了目录")
            self.assertEqual(sorted(os.listdir(h.home)), before)
        finally:
            h.close()

    def test_09_absent_versus_empty_ledger_are_two_sentences(self):
        h = helpers.TempHome(mode="ack")
        try:
            t = transport.Transport(os.path.join(h.home, "no-such-ledger-yet"))
            self.assertEqual(t.read().get("ledger"), "ABSENT")
            t.create(h.request(sender=self.sender, receiver=self.receiver, session_id=self.sender))
            self.assertEqual(t.read().get("ledger"), "ok")
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
