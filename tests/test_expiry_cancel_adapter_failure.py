#!/usr/bin/env python3
"""到期、取消、以及适配器失败判据（任务书 §14 expired／cancelled／adapter failure）。

迁移自实验根的 K12／K78／M-* 一族（未配置执行器绝不偷偷调真实付费执行器，
未知 kind 一律拒，坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 E／C 组）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import adapters, protocol, transport  # noqa: E402
from q2c.adapters import base as adapter_base  # noqa: E402
from tests import helpers  # noqa: E402


class TestExpiry(unittest.TestCase):
    def _soon(self, secs=0.4):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat(
            timespec="microseconds")

    def test_01_expired_before_delivery(self):
        """还没投就到期 ⇒ EXPIRED，一次进程都不起。

        造法用"即将到期＋等一会儿"，不是"创建即过期"：后者在信封校验里就该被拒
        （`EXPIRES_BEFORE_CREATED`），拿它当到期场景等于测了另一回事。
        """
        import time
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender,
                            expires_at=self._soon())
            rid = t.create(env)["request_id"]
            time.sleep(0.6)
            out = t.pump(rid)
            self.assertEqual(out["state"], protocol.ST_EXPIRED)
            self.assertEqual(h.responder_calls(), 0, "过期的东西还投＝投递对象已经不存在了")
        finally:
            h.close()

    def test_02_expire_due_moves_only_non_terminal(self):
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            import time
            rid_old = t.create(h.request(sender=sender, receiver=receiver, session_id=sender,
                                        expires_at=self._soon(0.2)))["request_id"]
            rid_live = t.create(h.request(sender=sender, receiver=receiver, session_id=sender,
                                          expires_at=self._soon(120)))["request_id"]
            time.sleep(0.35)
            res = t.expire_due()
            self.assertIn(rid_old, res["expired"])
            self.assertNotIn(rid_live, res["expired"])
            self.assertEqual(t.read(rid_live)["record"]["state"], protocol.ST_QUEUED)
        finally:
            h.close()

    def test_03_expired_is_terminal(self):
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            import time
            rid = t.create(h.request(sender=sender, receiver=receiver, session_id=sender,
                                     expires_at=self._soon(0.2)))["request_id"]
            time.sleep(0.35)
            t.pump(rid)
            with self.assertRaises(transport.TransportError) as cm:
                t.retry_delivery(rid)
            self.assertEqual(cm.exception.code, "RETRY_ON_TERMINAL")
            self.assertEqual(t.pump(rid)["status"], "terminal")
        finally:
            h.close()


class TestCancel(unittest.TestCase):
    def test_04_cancel_moves_to_cancelled_and_traces(self):
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            rid = t.create(h.request(sender=sender, receiver=receiver, session_id=sender))["request_id"]
            out = t.cancel(rid, reason="发起方不干了")
            self.assertEqual(out["state"], protocol.ST_CANCELLED)
            evs = [e for e in t.trace.events(rid) if e.get("ev") == "cancelled"]
            self.assertTrue(evs)
            self.assertIn("只断投递", evs[0]["note"],
                          "跟踪里必须写清：取消的是投递，不是对侧已经开始的工作")
        finally:
            h.close()

    def test_05_cancel_after_ack_is_noop(self):
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            self.assertEqual(t.send(env, wait=True)["pump"]["state"], protocol.ST_ACKED)
            out = t.cancel(env.request_id)
            self.assertEqual(out["status"], "already-terminal")
            self.assertEqual(t.read(env.request_id)["record"]["state"], protocol.ST_ACKED)
        finally:
            h.close()


class TestAdapterFailure(unittest.TestCase):
    def test_06_unknown_adapter_is_rejected_not_defaulted(self):
        adapters.import_builtin()
        with self.assertRaises(adapter_base.AdapterError) as cm:
            adapters.build("claude", {"home": "/tmp"})
        self.assertEqual(cm.exception.code, "UNKNOWN_ADAPTER")
        self.assertEqual(cm.exception.side_effects, 0)

    def test_07_unknown_mode_on_stub_is_rejected(self):
        h = helpers.TempHome(mode="teleport")
        try:
            t, sender, receiver = h.sessions()
            rid = t.create(h.request(sender=sender, receiver=receiver, session_id=sender))["request_id"]
            state = None
            for _ in range(4):
                state = t.pump(rid).get("state")
                if state in protocol.TERMINAL_STATES:
                    break
            self.assertEqual(state, protocol.ST_DELIVERY_FAILED)
            self.assertEqual(h.responder_calls(), 0, "认不出的模式还是把孩子起起来了")
        finally:
            h.close()

    def test_08_missing_session_is_rejected_before_any_dispatch(self):
        h = helpers.TempHome(mode="ack")
        try:
            t = transport.Transport(h.home)
            rid = t.create(h.request(sender="qs-nobody-000000000000", receiver="qs-ghost-0000000000000",
                                     session_id="qs-nobody-000000000000"))["request_id"]
            with self.assertRaises(transport.TransportError) as cm:
                t.pump(rid)
            self.assertEqual(cm.exception.code, "NO_SUCH_SESSION")
            self.assertEqual(h.responder_calls(), 0)
        finally:
            h.close()

    def test_09_live_adapters_need_explicit_authorization(self):
        """没设 Q2C_LIVE=1 ⇒ 真适配器一次外部调用都不起（这条保护花钱也买不回来）。"""
        adapters.import_builtin()
        for name in ("codex", "qoder"):
            a = adapters.build(name, {"home": "/tmp", "cmd": ""})
            env = dict(os.environ)
            env.pop("Q2C_LIVE", None)
            for k in ("Q2C_CODEX_CMD", "Q2C_QODER_CMD"):
                env.pop(k, None)
            a.config["env"] = env
            self.assertTrue(a.is_live(), "%s 的 live 位漂了" % name)
            from q2c.adapters.base import Handle
            with self.assertRaises(adapter_base.AdapterError) as cm:
                a.receive(Handle(adapter=name, provider_session_id="t-1"),
                          helpers.TempHome.__init__ and _env_for(name), timeout_s=1)
            self.assertEqual(cm.exception.code, "LIVE_NOT_AUTHORIZED")

    def test_10_codex_and_qoder_refuse_implicit_session_creation(self):
        """公开 CLI 没有"零副作用新建会话"的入口 ⇒ 明写拒绝，不假装会新建。"""
        adapters.import_builtin()
        for name in ("codex", "qoder"):
            a = adapters.build(name, {"home": "/tmp"})
            caps = a.capabilities()
            self.assertIn("limits", caps, "%s 的能力位没写限制" % name)
            from q2c.adapters.base import Handle
            with self.assertRaises(adapter_base.AdapterError) as cm:
                a.start(Handle(adapter=name))
            self.assertTrue(cm.exception.code.endswith("START_UNSUPPORTED"), cm.exception.code)

    def test_11_capability_flags_are_honest(self):
        adapters.import_builtin()
        for name in adapters.known():
            caps = adapters.build(name, {"home": "/tmp"}).capabilities()
            for key in ("name", "live", "terminal_evidence", "split_send_receive", "evidence"):
                self.assertIn(key, caps, "%s 的能力位缺 %s" % (name, key))

    def test_12_contract_methods_all_present(self):
        adapters.import_builtin()
        for name in adapters.known():
            a = adapters.build(name, {"home": "/tmp"})
            for m in adapter_base.CONTRACT_METHODS:
                self.assertTrue(callable(getattr(a, m, None)), "%s 缺契约方法 %s" % (name, m))


def _env_for(name):
    from q2c import protocol as P
    return P.Envelope(protocol_version=P.PROTOCOL_VERSION, message_id="msg-x", request_id="req-x",
                      correlation_id="cor-x", sender="qs-s", receiver="qs-r", handoff_type="handoff",
                      session_id="qs-s", payload="x", idempotency_key="idem-x",
                      created_at=P.utc_now())


if __name__ == "__main__":
    unittest.main(verbosity=2)
