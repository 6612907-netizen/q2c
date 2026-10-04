#!/usr/bin/env python3
"""重启恢复判据（任务书 §14 restart recovery ＋ §15 "重启不重复消费"）。

迁移自实验根的 C3／C4／C5／G4／G6／K50／K62／K51 一族
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 B 组）。

这一组测的都是同一句话：**桥死了不要紧，别把已经做过的事再做一遍**。
所以每一格都用"两个 Transport 实例"来扮演重启（真进程被杀后重新拉起，
家目录里只剩落盘件——这与实验根里用 `RELAY_STOP_*` 接缝把进程杀在半路是同一形态）。
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import ledger, protocol, security, transport  # noqa: E402
from tests import helpers  # noqa: E402


class TestRestartRecovery(unittest.TestCase):
    def _two_transport(self, **cfg):
        h = helpers.TempHome(**cfg)
        t, sender, receiver = h.sessions()
        return h, t, sender, receiver

    def test_01_result_on_disk_restarts_deliver_only(self):
        """结果已在案 ⇒ 重启后只补送达，对侧一次都不许再被起（C5 本体）。"""
        h, t, sender, receiver = self._two_transport(mode="ack")
        try:
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                      # DELIVERED
            t.pump(rid)                      # 收答复 ⇒ RESPONDED
            recv = t.sessions.get(receiver).current()
            before = h.responder_calls(session=recv)
            self.assertEqual(before, 1)
            t2 = transport.Transport(h.home)  # ← 扮演重启后的新进程
            acted = t2.recover()["recovered"]
            self.assertIn(rid, acted)
            self.assertEqual(t2.read(rid)["record"]["state"], protocol.ST_RESPONDED)
            self.assertEqual(t2.read(rid)["record"]["recovered"], "result_on_disk")
            self.assertEqual(h.responder_calls(session=recv), before, "重启把对侧又跑了一遍")
            out = t2.pump(rid)
            self.assertEqual(out["state"], protocol.ST_ACKED)
        finally:
            h.close()

    def test_02_inflight_survives_restart_and_is_collected(self):
        """到点不杀 ⇒ 新进程等它自己跑完，用同一枚绑定串收回，然后静默推进。

        这一格对应在册 K52/K54：等待窗口不是把红等成绿的手段，
        也不是"到点杀掉记一次失败再重派"的理由。
        """
        h = helpers.TempHome(mode="ack", receiver_mode="sleep", sleep_s=2.0, receive_timeout_s=0.3)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                                   # DELIVERED
            out = t.pump(rid)                             # 起应答器后到点：IN_FLIGHT
            self.assertEqual(out["status"], "in_flight")
            recv = t.sessions.get(receiver).current()
            self.assertEqual(h.responder_calls(session=recv), 1)
            pid = t.read(rid)["record"]["handles"]["receiver"]["pid"]

            t2 = transport.Transport(h.home)               # ← 重启
            acted = t2.recover()["recovered"]
            self.assertIn(rid, acted)
            self.assertEqual(t2.read(rid)["record"]["recovered"], "await_inflight")
            self.assertEqual(h.responder_calls(session=recv), 1, "重启后又起了一遍")
            self.assertEqual(security.pid_state(pid, t2.read(rid)["record"]["handles"]["receiver"]["pid_start"]),
                             "alive")
            self.assertTrue(helpers.wait_for(
                lambda: os.path.isfile(_rc_of(h, recv, _mid(t2, rid))), timeout_s=12))
            state = None
            for _ in range(4):
                state = t2.pump(rid).get("state")           # 收回迟到那一拍，再送结果腿
                if state == protocol.ST_ACKED:
                    break
            self.assertEqual(state, protocol.ST_ACKED)
            self.assertEqual(h.responder_calls(session=recv), 1)
        finally:
            h.close()

    def test_03_unknown_is_not_absent(self):
        """探针读不出来 ⇒ 停手，不重投。这一格是 K62 那一族的核心红线。

        现场用"应答器还在睡、父进程已收工"造：这时结果**不在案**、进程读不出来，
        恢复阶梯必须落到 unknown_hold。旧写法在这里踩过一次：结果已在案的场景
        永远走不到未知那一档，那一格看着在测、其实测的是上一档（假绿）。
        """
        h = helpers.TempHome(mode="ack", receiver_mode="sleep", sleep_s=3.0,
                             receive_timeout_s=0.3)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                                    # DELIVERED
            out = t.pump(rid)                              # 起应答器后到点：IN_FLIGHT
            self.assertEqual(out["status"], "in_flight")
            rec = t.read(rid)["record"]
            self.assertFalse(rec.get("result", {}).get("sha256"),
                             "结果已在案就走不到未知这一档，这一格会测成别的分支")
            db = ledger_load(t)
            rec["handles"]["receiver"]["pid"] = 999999      # 登记过 pid
            rec["handles"]["receiver"]["pid_start"] = "某读不出来的时刻"
            db[rid] = rec
            ledger_save(t, db)
            orig = security.pid_state
            security.pid_state = lambda pid, start: "unknown"     # 打桩：探针坏了
            try:
                t2 = transport.Transport(h.home)
                acted = t2.recover()["recovered"]
                self.assertIn(rid, acted)
                rec2 = t2.read(rid)["record"]
                self.assertEqual(rec2["recovered"], "unknown_hold")
                self.assertTrue(rec2.get("uncertain"))
                self.assertNotEqual(rec2["state"], protocol.ST_DELIVERY_RETRY,
                                    "把「读不出来」当「确认没跑」⇒ 会自动重投")
                recv = t.sessions.get(receiver).current()
                self.assertEqual(h.responder_calls(session=recv), 1)
            finally:
                security.pid_state = orig
        finally:
            h.close()

    def test_03b_inflight_child_not_counted_as_failure(self):
        """落盘件在、退出码侧件还没有、孩子还活着 ⇒ 只能等，不能记失败重派。

        这一格是真腿第一次跑出来的缺陷（不是假想）：网络重连把一轮拖过等待窗口，
        旧代码把"还没退"读成"退了且失败"⇒ 下一拍重新入队＋再 exec resume，
        同一个请求被叫醒两次（台账里请求腿 attempts 从 1 涨到 2 就是它的指纹）。
        """
        h = helpers.TempHome(mode="ack", receiver_mode="sleep", sleep_s=4.0,
                             receive_timeout_s=0.3, delivery_attempts_max=2)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                                       # DELIVERED
            recv = t.sessions.get(receiver).current()
            for i in range(4):                               # 孩子在飞的这段时间里反复推
                out = t.pump(rid)
                if out.get("status") != "in_flight":
                    break
            rec = t.read(rid)["record"]
            self.assertEqual(rec["state"], protocol.ST_DELIVERED,
                             "在飞那一腿被判成了 %s（应当原地等）" % rec["state"])
            self.assertEqual(rec["delivery"]["request"]["attempts"], 1,
                             "请求腿被重投＝对侧被叫醒两次")
            self.assertEqual(h.responder_calls(session=recv), 1)
            self.assertTrue(helpers.wait_for(
                lambda: os.path.isfile(_rc_path(h, recv, env.message_id)), timeout_s=15))
            state = None
            for _ in range(6):
                state = t.pump(rid).get("state")
                if state == protocol.ST_ACKED:
                    break
            self.assertEqual(state, protocol.ST_ACKED)
            self.assertEqual(h.responder_calls(session=recv), 1,
                             "补送达之后又把对侧跑了一遍")
        finally:
            h.close()

    def test_04_confirmed_absent_redelivers_within_budget(self):
        """确认没跑过（无落盘件、无退出码侧件、进程确认不在）⇒ 允许重投**消息**。"""
        h, t, sender, receiver = self._two_transport(mode="ack", delivery_attempts_max=2)
        try:
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                          # DELIVERED（请求已入信箱）
            rec = t.read(rid)["record"]
            db = ledger_load(t)
            rec["state"] = protocol.ST_DELIVERING     # 假装崩在"起了但没落盘"之前
            rec["delivery"]["request"]["attempts"] = 1
            rec["handles"]["receiver"]["pid"] = None
            rec["handles"]["receiver"]["pid_start"] = ""
            db[rid] = rec
            ledger_save(t, db)
            t2 = transport.Transport(h.home)
            acted = t2.recover()["recovered"]
            self.assertIn(rid, acted)
            self.assertEqual(t2.read(rid)["record"]["recovered"], "confirmed_absent_redeliver")
            self.assertEqual(t2.read(rid)["record"]["state"], protocol.ST_DELIVERY_RETRY)
        finally:
            h.close()

    def test_05_restart_does_not_double_consume_acked(self):
        """已经 ACKED 的一笔，重启后任何动作都不许把它再推一遍（终态不可翻）。"""
        h, t, sender, receiver = self._two_transport(mode="ack")
        try:
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            self.assertEqual(t.send(env, wait=True)["pump"]["state"], protocol.ST_ACKED)
            rid = env.request_id
            recv = t.sessions.get(receiver).current()
            before = h.responder_calls(session=recv)
            t2 = transport.Transport(h.home)
            self.assertEqual(t2.recover()["recovered"], [], "已确认的笔被恢复流程捡起来了")
            out = t2.pump(rid)
            self.assertEqual(out["status"], "terminal")
            with self.assertRaises(transport.TransportError):
                t2.retry_delivery(rid)
            self.assertEqual(h.responder_calls(session=recv), before)
        finally:
            h.close()

    def test_06_nonce_never_changes_across_restart(self):
        h, t, sender, receiver = self._two_transport(mode="ack")
        try:
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)
            nonce = t.read(rid)["record"]["nonce"]
            for _ in range(3):
                transport.Transport(h.home).recover()
            self.assertEqual(transport.Transport(h.home).read(rid)["record"]["nonce"], nonce,
                             "绑定串换了 ⇒ 上一腿迟到的答复永远认领不了（在册 R10 那一格）")
        finally:
            h.close()


def _rc_path(h, provider, message_id):
    import glob
    hits = glob.glob(os.path.join(h.home, "state", "inflight", provider, "%s.a*.out.rc" % message_id))
    return hits[0] if hits else os.path.join(h.home, "state", "inflight", provider,
                                             "%s.a1.out.rc" % message_id)


def ledger_load(t):
    """测试专用的直接写台账：用来构造"崩在半路"的现场。

    注意这里绕的是 `Transport` 这一层，不是绕开一致性——仍然走 `ledger.save`，
    所以三方合并与陈旧检测照常生效；伪造的是"上一拍的状态"，不是"绕过落盘"。
    """
    return ledger.load_for_write(t.paths)


def ledger_save(t, db):
    ledger.save(t.paths, db)


def _rc_of(h, provider, message_id):
    return os.path.join(h.home, "state", "inflight", provider, "%s.a1.out.rc" % message_id)


def _mid(t, rid):
    return t.read(rid)["record"]["envelope"]["message_id"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
