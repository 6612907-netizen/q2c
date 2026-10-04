#!/usr/bin/env python3
"""1000 枚合成交接（任务书 §14）：0 静默丢失、0 意外重复效果。

两条车都要跑，因为它们是两件事：
  · `inproc`（同进程应答，无子进程）跑满 1000 枚——这一格测的是**状态机与账**：
    收单、幂等、推进、跟踪、台账对得上，不多不少。
  · `loopback`（真子进程）抽样跑 40 枚——这一格测的是**投递本身**：
    进程起停、退出码侧件、回执解析、送达确认。1000 枚全用真进程跑，
    测到的是磁盘与调度耐心，不是桥。

"静默丢失"在本判据里有唯一定义：**台账里没有、跟踪里也没有**。
只数台账不数跟踪＝工装自己造出来的丢法（在册老坑：断言不许数空气）。
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import ledger, protocol, trace, transport  # noqa: E402
from tests import helpers  # noqa: E402

N_BULK = 1000
N_SUBPROCESS_SAMPLE = 40


class TestSyntheticFanout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = helpers.TempHome(mode="ack")

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def _bulk(self):
        """1000 枚合成交接：idempotency_key 唯一，外加 200 枚**故意重复投**的。"""
        t = transport.Transport(self.h.home)
        sender = t.sessions.create("sender", "inproc", label="bulk-sender").session_id
        receiver = t.sessions.create("receiver", "inproc", label="bulk-receiver").session_id
        envs = [protocol.make_request(
            sender=sender, receiver=receiver, session_id=sender,
            handoff_type="load-handoff", payload="负载判据第 %d 枚：请回一句话就行。" % i,
            idempotency_key="bulk-idem-%06d" % i) for i in range(N_BULK)]
        res = t.create_many(envs)
        self.assertEqual(res["created"], N_BULK, res)
        ids = res["request_ids"]
        # 200 枚**重复投**同键（换 message_id／request_id，只留 idempotency_key 相同）
        dupes = 0
        dups_envs = []
        for i in range(0, N_BULK, 5):
            dups_envs.append(protocol.Envelope.from_dict(dict(
                envs[i].to_dict(), message_id=protocol.new_message_id(),
                request_id=protocol.new_request_id())))
        res2 = t.create_many(dups_envs)
        self.assertEqual(res2["created"], 0, "重复投居然建成了 %d 笔" % res2["created"])
        dupes = res2["duplicates"]
        self.assertEqual(dupes, len(dups_envs))
        return t, sender, receiver, ids, dupes

    def test_01_all_records_land(self):
        t, sender, receiver, ids, dupes = self._bulk()
        got = t.read()
        self.assertEqual(got["ledger"], "ok")
        self.assertEqual(len(got["records"]), N_BULK,
                         "台账里不是 1000 笔（重复投还多造了笔？）")
        self.assertEqual(dupes, 200)

    def test_02_zero_silent_loss(self):
        t = transport.Transport(self.h.home)
        db = ledger.load_for_write(t.paths)
        missing_ledger = [rid for rid in db if not t.trace.events(rid)]
        self.assertEqual(missing_ledger, [], "有记录一条跟踪都没留")
        t.pump_ready(max_rounds=8)
        db2 = ledger.load_for_write(t.paths)
        states = {rid: rec.get("state") for rid, rec in db2.items()}
        lost = [rid for rid, st in states.items()
                if st not in protocol.TRANSPORT_STATES]
        self.assertEqual(lost, [], "出现了协议外的状态")
        self.assertEqual(len(states), N_BULK)

    def test_03_everything_reaches_a_terminal_state(self):
        t = transport.Transport(self.h.home)
        t0 = time.time()
        res = t.pump_ready(max_rounds=8)
        db = ledger.load_for_write(t.paths)
        states = [rec.get("state") for rec in db.values()]
        non_terminal = [s for s in states if s not in protocol.TERMINAL_STATES
                        and s not in protocol.PARKED_STATES]
        self.assertEqual(non_terminal, [],
                         "推到上界还有 %d 笔没到终态" % len(non_terminal))
        acked = sum(1 for s in states if s == protocol.ST_ACKED)
        self.assertEqual(acked, N_BULK, "ACKED 数不等于投递数：%s" % res)
        self.printContext = {"secs": round(time.time() - t0, 2)}

    def test_04_no_unexpected_duplicate_effects(self):
        """每一笔只该被应答一次：结果腿补送达不得把对侧再跑一遍。"""
        t = transport.Transport(self.h.home)
        db = ledger.load_for_write(t.paths)
        bad = []
        for rid, rec in db.items():
            if rec["delivery"]["request"]["attempts"] != 1:
                bad.append((rid, rec["delivery"]["request"]["attempts"]))
        self.assertEqual(bad, [], "有记录被投了不止一次：%s" % bad[:5])
        dup_events = [e for e in t.trace.events() if e.get("ev") == "duplicate_suppressed"]
        self.assertEqual(len(dup_events), 200, "抑制事件数应与重复投数一致")

    def test_05_subprocess_sample_actually_spawns(self):
        """抽样 40 枚走**真子进程**：证明上面那 1000 枚不是靠"没起进程"蒙过去的。"""
        t, sender, receiver = self.h.sessions()      # loopback 两条会话
        rids = []
        for i in range(N_SUBPROCESS_SAMPLE):
            env = h_request(self.h, sender, receiver, "sub-idem-%03d" % i)
            rids.append(t.create(env)["request_id"])
        t.pump_ready(max_rounds=8)
        db = ledger.load_for_write(t.paths)
        acked = [rid for rid in rids if (db.get(rid) or {}).get("state") == protocol.ST_ACKED]
        self.assertEqual(len(acked), N_SUBPROCESS_SAMPLE,
                         "真子进程那一路只成了 %d/%d" % (len(acked), N_SUBPROCESS_SAMPLE))
        self.assertGreaterEqual(self.h.responder_calls(), N_SUBPROCESS_SAMPLE * 2,
                                "应答器起得比腿数还少＝抽样这一路没真跑")


def h_request(h, sender, receiver, idem):
    return h.request(sender=sender, receiver=receiver, session_id=sender,
                     idempotency_key=idem, handoff_type="handoff")


if __name__ == "__main__":
    unittest.main(verbosity=2)
