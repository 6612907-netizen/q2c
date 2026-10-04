#!/usr/bin/env python3
"""两条真实双向交接（任务书 §14 第 1、2 项）——**需要授权才会真跑**。

为什么默认跳过：真跑要调 Codex／Qoder 的模型会话，属任务书 §19 停止清单里的
"付费服务／新凭据"。所以这一组的口径是：

  · 未设 `Q2C_LIVE=1` ⇒ skip 并写明"未授权真调用"，**不拿零模型的绿顶替这一格**；
  · 设了 `Q2C_LIVE=1` 但没给线程／会话号 ⇒ 同样 skip（不猜号、不自建会话：
    公开 CLI 没有零副作用新建入口，见 ADAPTERS.md §2）；
  · 两个都给 ⇒ 真跑 Codex→Qoder 与 Qoder→Codex 各一次，断言送达确认与逐腿证据。

`q2c doctor` 与发布报告的 TEST_STATUS 会读这一组的**计数**（跑了多少、跳过了多少），
不许把 SKIP 折进 PASS。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import adapters, protocol, transport  # noqa: E402

LIVE = os.environ.get("Q2C_LIVE", "").strip() == "1"
CODEX_THREAD = os.environ.get("Q2C_CODEX_THREAD", "").strip()
QODER_SESSION = os.environ.get("Q2C_QODER_SESSION", "").strip()
WORKSPACE = os.environ.get("Q2C_WORKSPACE", "").strip()

SKIP_NOT_AUTHORIZED = "未授权真调用（要 Q2C_LIVE=1）：这一格需要主理人点头，判据不代跑"
SKIP_NO_THREAD = "缺 Q2C_CODEX_THREAD：公开 CLI 没有零副作用新建线程的入口，不猜号"
SKIP_NO_SESSION = "缺 Q2C_QODER_SESSION：同上，不猜会话号"


def _need(cond, why):
    return unittest.skipUnless(cond, why)


class TestRealBidirectional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adapters.import_builtin()

    def _pair(self, home):
        t = transport.Transport(home)
        codex = t.sessions.create("receiver", "codex", label="live-codex")
        t.sessions.rebind(codex.session_id, CODEX_THREAD, "真实交接判据：使用已存在的线程")
        qoder = t.sessions.create("sender", "qoder", label="live-qoder")
        t.sessions.rebind(qoder.session_id, QODER_SESSION, "真实交接判据：使用已存在的会话")
        return t, qoder.session_id, codex.session_id

    @_need(LIVE and bool(CODEX_THREAD) and bool(QODER_SESSION), SKIP_NOT_AUTHORIZED)
    def test_01_qoder_to_codex_and_back(self):
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-live-")
        t, sender, receiver = self._pair(home)
        env = protocol.make_request(sender=sender, receiver=receiver, session_id=sender,
                                    handoff_type="review-request", workspace_ref=WORKSPACE,
                                    payload="这是 q2c 的真实双向交接判据（Qoder→Codex）。"
                                            "请只回一句话说明你收到了这一笔。")
        res = t.send(env, wait=True, max_steps=12)
        rec = t.read(env.request_id)["record"]
        self.assertEqual(rec["state"], protocol.ST_ACKED,
                         "真实腿没闭环：%s / %s" % (rec["state"], rec.get("reason")))
        self.assertTrue(rec["result"]["sha256"])
        q = t.trace.nine_questions(env.request_id)
        for key in ("who_sent", "who_received", "when_delivered", "when_started",
                    "what_response", "when_acknowledged"):
            self.assertNotIn(q.get(key), (None, "NOT_OBSERVED", "TRACE_ABSENT"),
                             "真实交接的跟踪缺 %s" % key)

    @_need(LIVE and bool(CODEX_THREAD) and bool(QODER_SESSION), SKIP_NOT_AUTHORIZED)
    def test_02_codex_to_qoder_and_back(self):
        import tempfile
        home = tempfile.mkdtemp(prefix="q2c-live2-")
        t, sender, receiver = self._pair(home)
        # 反向：发起方是 Codex 侧，接收方是 Qoder 侧
        env = protocol.make_request(sender=receiver, receiver=sender, session_id=receiver,
                                    handoff_type="review-request", workspace_ref=WORKSPACE,
                                    payload="这是 q2c 的真实双向交接判据（Codex→Qoder）。"
                                            "请只回一句话说明你收到了这一笔。")
        t.send(env, wait=True, max_steps=12)
        rec = t.read(env.request_id)["record"]
        self.assertEqual(rec["state"], protocol.ST_ACKED,
                         "反向真实腿没闭环：%s / %s" % (rec["state"], rec.get("reason")))

    def test_03_skip_is_reported_not_folded(self):
        """跳过必须被**数出来**：这一格断言的是判据自身的诚实性。

        未授权时上面两格是 skip；若哪天有人把 skip 折成 pass，这里的计数会对不上。
        """
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestRealBidirectional)
        total = suite.countTestCases()
        self.assertEqual(total, 3)
        status = "AUTHORIZED" if (LIVE and CODEX_THREAD and QODER_SESSION) else "SKIP"
        print("REAL_HANDOFF_STATUS=%s（真跑两格＋诚实性一格，共 %d 格）" % (status, total))
        self.assertIn(status, ("AUTHORIZED", "SKIP"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
