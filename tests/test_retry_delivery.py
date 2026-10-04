#!/usr/bin/env python3
"""重投＝只重投消息，绝不重跑对侧任务（任务书 §5 的机器判据）。

这条边界是产品定义里最容易在"顺手优化"中被越过去的一条：
一旦"投递没回音"被实现成"再叫模型跑一轮"，就会出现——
同一个请求两份对象、两枚绑定串、两条互相打脸的答复，外加一次白烧的真实调用。
（实验根 R9 第 7 条、R10 时间线就是这么记的。）

五枚硬断言（协议 §5 的三条规定在这里逐条对上）：
    1. 重投不换 `message_id`、不改 `payload`、复用同一 `correlation_id`；
    2. 重投**沿用同一枚绑定串**（换了串，上一腿迟到的答复永远认领不了）；
    3. 结果已在案 ⇒ 只许投结果腿，对侧应答器进程数**一个都不许多**；
    4. 投到终态 ⇒ 拒（重投没有对象）；
    5. 预算用尽 ⇒ 停手等人，不自轮询、不把这一笔重新排回队列。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import ledger, protocol, transport  # noqa: E402
from tests import helpers  # noqa: E402


def _fix_sender_leg(h):
    """把送不上的那条通道修好（**只动适配器配置，不动交接单**）。"""
    import json as _json
    cfg = os.path.join(h.home, "config.json")
    with open(cfg, encoding="utf-8") as fh:
        doc = _json.loads(fh.read())
    doc["adapters"]["loopback"]["modes_by_role"] = {"sender": "ack"}
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(_json.dumps(doc, ensure_ascii=False))


class TestRetryIsDeliveryOnly(unittest.TestCase):
    def _broken_result_leg(self, receiver_mode="ack", sender_mode="no-ack", attempts=1):
        """把一腿跑到"对侧答了、回复送不上"的状态。"""
        h = helpers.TempHome(mode=receiver_mode, sender_mode=sender_mode,
                             delivery_attempts_max=attempts)
        t, sender, receiver = h.sessions()
        env = h.request(sender=sender, receiver=receiver, session_id=sender)
        res = t.send(env, wait=True)
        return h, t, sender, receiver, env, res

    def test_01_result_leg_fails_without_touching_receiver_twice(self):
        h, t, sender, receiver, env, res = self._broken_result_leg()
        try:
            self.assertEqual(res["pump"]["state"], protocol.ST_DELIVERY_FAILED)
            rec = t.read(env.request_id)["record"]
            self.assertTrue(rec["result"]["sha256"], "结果没在案，这格就没测到东西")
            recv = h.responder_calls(session=t.sessions.get(receiver).current())
            sendd = h.responder_calls(session=t.sessions.get(sender).current())
            self.assertEqual(recv, 1, "对侧被起了 %d 次：投不上就该重投消息，不该重跑" % recv)
            self.assertGreaterEqual(sendd, 1)
        finally:
            h.close()

    def test_02_retry_completes_without_new_receiver_run(self):
        h, t, sender, receiver, env, res = self._broken_result_leg()
        try:
            recv_before = h.responder_calls(session=t.sessions.get(receiver).current())
            msg_before = t.read(env.request_id)["record"]["envelope"]["message_id"]
            nonce_before = t.read(env.request_id)["record"]["nonce"]
            req_sha_before = t.read(env.request_id)["record"]["request_sha256"]
            # 修好那条送不上的通道（换了配置，交接单本身一个字没动）
            cfg = os.path.join(h.home, "config.json")
            with open(cfg, encoding="utf-8") as fh:
                doc = json.loads(fh.read())
            doc["adapters"]["loopback"]["modes_by_role"] = {"sender": "ack"}
            with open(cfg, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, ensure_ascii=False))
            t2 = transport.Transport(h.home)
            out = t2.retry_delivery(env.request_id)
            self.assertEqual(out["state"], protocol.ST_ACKED, out)
            recv_after = h.responder_calls(session=t2.sessions.get(receiver).current())
            self.assertEqual(recv_after, recv_before,
                             "重投之后对侧又被跑了一次：%d→%d" % (recv_before, recv_after))
            rec = t2.read(env.request_id)["record"]
            self.assertEqual(rec["envelope"]["message_id"], msg_before, "重投换了消息号")
            self.assertEqual(rec["nonce"], nonce_before, "重投换了绑定串（迟到答复就认领不了）")
            self.assertEqual(rec["request_sha256"], req_sha_before, "重投改了派发文")
            evs = [e for e in t2.trace.events(env.request_id) if e.get("ev") == "retry_delivery"]
            self.assertTrue(evs and evs[0]["nonce_kept"] == nonce_before)
        finally:
            h.close()

    def test_03_explicit_request_leg_is_redirected_when_result_on_disk(self):
        """明明指着"投请求腿"也不许投——协议 §5 第 3 条唯一的硬拦法。

        合法处置是：**改投结果腿并留一行 `retry_refused_result_on_disk`**，
        对侧应答器一次都不许多起。直接拒也合法，但那样人得自己判断该投哪条腿，
        容易照着"重投一次"的心智模型去伪造新请求号——所以这里选择改投＋留痕。
        """
        h, t, sender, receiver, env, res = self._broken_result_leg()
        try:
            recv = t.sessions.get(receiver).current()
            before = h.responder_calls(session=recv)
            _fix_sender_leg(h)
            t2 = transport.Transport(h.home)
            out = t2.retry_delivery(env.request_id, leg="request")
            self.assertEqual(out["state"], protocol.ST_ACKED, out)
            self.assertEqual(h.responder_calls(session=t2.sessions.get(receiver).current()), before,
                             "对着请求腿重投，把已经跑完的对侧又跑了一遍")
            evs = [e for e in t2.trace.events(env.request_id)
                   if e.get("ev") == "retry_refused_result_on_disk"]
            self.assertTrue(evs, "改了腿却没留痕，事后读不出这是被拦下来的")
        finally:
            h.close()

    def test_04_retry_on_acked_is_rejected(self):
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            res = t.send(env, wait=True)
            self.assertEqual(res["pump"]["state"], protocol.ST_ACKED)
            with self.assertRaises(transport.TransportError) as cm:
                t.retry_delivery(env.request_id)
            self.assertEqual(cm.exception.code, "RETRY_ON_TERMINAL")
        finally:
            h.close()

    def test_05_budget_exhaustion_stops_and_notifies(self):
        h = helpers.TempHome(mode="no-ack", delivery_attempts_max=2)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            states = []
            for _ in range(6):
                states.append(t.pump(rid).get("state") or t.pump(rid).get("status"))
            rec = t.read(rid)["record"]
            self.assertEqual(rec["state"], protocol.ST_DELIVERY_FAILED)
            self.assertLessEqual(rec["delivery"]["request"]["attempts"], 3,
                                 "预算之外还在自己试＝把停止条件设成了配额")
            # 失败必须让某个接收方可见：逐笔回执记着 delivered=false（没配通道时如实报）
            self.assertFalse(t.notify_delivered(rid))
            self.assertTrue(os.path.isfile(os.path.join(h.home, "notify", "%s.txt" % rid)))
        finally:
            h.close()

    def test_06_redelivery_of_request_leg_reuses_same_bytes(self):
        """请求腿重投：同一枚绑定串、同一份派发文，只多一次"投递"。"""
        h = helpers.TempHome(mode="no-json", delivery_attempts_max=3)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)                    # DELIVERING→DELIVERED
            t.pump(rid)                    # 收答复：无结构化回执 ⇒ 失败
            rec = t.read(rid)["record"]
            nonce = rec["nonce"]
            sha = rec.get("request_sha256")
            out = t.retry_delivery(rid, leg="request")
            rec2 = t.read(rid)["record"]
            self.assertEqual(rec2["nonce"], nonce, "换了绑定串")
            self.assertEqual(rec2.get("request_sha256"), sha, "派发文变了")
        finally:
            h.close()


    def test_07_human_retry_is_not_gated_by_the_auto_budget(self):
        """预算管的是**桥自己催不催**，不管人下不下这道令。

        这条不是放宽：自动路径一次都不许多投（test_05 钉着），
        而人显式 `retry-delivery` 必须能投得出去——否则记录永远停在失败，
        人只会去做更坏的事：伪造一个新 request_id 绕开规则。
        """
        h, t, sender, receiver, env, res = self._broken_result_leg(attempts=1)
        try:
            self.assertEqual(res["pump"]["state"], protocol.ST_DELIVERY_FAILED)
            rec0 = t.read(env.request_id)["record"]
            self.assertGreaterEqual(rec0["delivery"]["result"]["attempts"], 1)
            _fix_sender_leg(h)
            t2 = transport.Transport(h.home)
            out = t2.retry_delivery(env.request_id)
            self.assertEqual(out["state"], protocol.ST_ACKED, out)
            rec1 = t2.read(env.request_id)["record"]
            self.assertEqual(rec1["delivery"]["result"]["max"], 1,
                             "预算被重投偷偷抬高了：这一格就测不到「人显式重投」这件事")
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
