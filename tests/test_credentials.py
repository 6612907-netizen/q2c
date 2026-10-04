#!/usr/bin/env python3
"""凭据边界与路径校验判据（任务书 §11 ＋ §14 credential unavailable ＋ §15 同名回归）。

迁移自实验根的 K76 五档与 E-cred-gate 两枚反证
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 F 组）。

核心口径三句：
  · **q2c 不持有凭据**——不读钥匙串、不复制 token、不落盘密钥；
  · 前置检查只回答"这一侧能不能无人值守完成一次最小认证调用"，
    **判不出来（含超时）一律按不可用**；
  · 不可用 ⇒ 一次 CLI 都不叫、状态写明原因、并让人可见。绝不"照发，等超时才发现是认证问题"。
"""

import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import config as config_mod, ledger, protocol, security, transport  # noqa: E402
from tests import helpers  # noqa: E402

SENTINEL = "sk-ABCDEFGHIJ0123456789-SENTINEL-NOT-A-REAL-KEY"


class TestCredentialProbe(unittest.TestCase):
    def test_01_nonzero_probe_is_unavailable(self):
        v, note = security.probe_credential("codex", env={"Q2C_CRED_PROBE": "exit 3"})
        self.assertEqual(v, security.CREDENTIAL_UNAVAILABLE)
        self.assertIn("rc=3", note)

    def test_02_zero_probe_is_ready(self):
        v, _ = security.probe_credential("codex", env={"Q2C_CRED_PROBE": "true"})
        self.assertEqual(v, security.READY)

    def test_03_timeout_counts_as_unavailable(self):
        """判不出来 ≠ 可用。旧写法在这儿折成"通过"，就是"照发、等超时"那一格。"""
        v, note = security.probe_credential("codex",
                                           env={"Q2C_CRED_PROBE": "sleep 5",
                                                "Q2C_CRED_TIMEOUT": "0.3"})
        self.assertEqual(v, security.CREDENTIAL_UNAVAILABLE)
        self.assertIn("无法证明", note)

    def test_04_unknown_kind_is_not_defaulted(self):
        v, note = security.probe_credential("claude", env={})
        self.assertEqual(v, security.UNKNOWN_KIND)
        self.assertIn("不在册", note)

    def test_05_key_reading_commands_are_refused(self):
        """探针里含"取密钥"形状 ⇒ 拒绝执行，而不是改写成另一条近似命令。"""
        marker = os.path.join(os.getcwd(), "_q2c_should_not_run.marker")
        probe = "touch %s ; security find-generic-password -w" % marker
        if os.path.exists(marker):
            os.remove(marker)
        v, note = security.probe_credential("codex", env={"Q2C_CRED_PROBE": probe})
        self.assertEqual(v, security.CREDENTIAL_UNAVAILABLE)
        self.assertIn("拒绝执行", note)
        self.assertFalse(os.path.exists(marker), "被拒的探针却还是跑了")

    def test_06_probe_stdout_is_discarded(self):
        """探针 stdout 直接丢弃 ⇒ 凭据内容连"进内存再落原件"的机会都没有。"""
        v, note = security.probe_credential("codex",
                                           env={"Q2C_CRED_PROBE": "echo %s" % SENTINEL})
        self.assertEqual(v, security.READY)
        self.assertNotIn(SENTINEL, note)


class TestDispatchGate(unittest.TestCase):
    def test_07_credential_unavailable_blocks_without_a_single_call(self):
        h = helpers.TempHome(mode="ack")
        try:
            cfgp = os.path.join(h.home, "config.json")
            _patch_config(cfgp, {"adapters": {"loopback": {"mode": "ack", "calls_log": h.calls,
                                                          "cred_probe": "exit 4"}}})
            t = transport.Transport(h.home)
            s, r = t.sessions.create("sender", "loopback", label="g-s"), \
                t.sessions.create("receiver", "loopback", label="g-r")
            env = h.request(sender=s.session_id, receiver=r.session_id, session_id=s.session_id)
            rid = t.create(env)["request_id"]
            out = t.pump(rid)
            self.assertEqual(out["failure_class"], "CREDENTIAL_UNAVAILABLE")
            self.assertEqual(out["state"], protocol.ST_DELIVERY_FAILED)
            self.assertEqual(h.responder_calls(), 0, "认证没过却把对侧叫起来了")
            rec = t.read(rid)["record"]
            self.assertEqual(rec["failure_class"], "CREDENTIAL_UNAVAILABLE")
            self.assertIn("凭据不可用", rec["reason"])
            evs = [e for e in t.trace.events(rid) if e.get("ev") == "auto_retry_withheld"]
            self.assertTrue(evs, "认证类失败还打算自动重投")
        finally:
            h.close()

    def test_08_secrets_never_reach_the_trace_or_receipts(self):
        """哨兵串不许出现在**桥自己的**事件流／回执／通知件里。

        这条判据起初写成了"全树 grep＝0 命中"，那是错的口径：
        载荷必须逐字投递，派发文与台账必然带着它——脱了敏就不是同一条消息。
        所以桥的义务拆成三件，本格与 test_08b 分别钉：
          ① 事件流／回执／通知件里不许出现正文（q2c 自己的记录）；
          ② 用户数据那几件（台账、派发文）必须 0600；
          ③ 发出去时给一个可见标记，别让用户以为凭据被"处理过"了。
        """
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender,
                            payload="请核读。额外带一句：Authorization: Bearer %s" % SENTINEL)
            t.send(env, wait=True)
            own = []
            for rel in ("trace/events.jsonl", os.path.join("state", "notify-ack.jsonl")):
                p = os.path.join(h.home, rel)
                if not os.path.isfile(p):
                    continue
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    if SENTINEL in fh.read():
                        own.append(rel)
            for dirpath, _dirs, files in os.walk(os.path.join(h.home, "state", "inflight")):
                for fn in files:
                    fp = os.path.join(dirpath, fn)
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        if SENTINEL in fh.read():
                            own.append(os.path.relpath(fp, h.home))
            self.assertEqual(own, [], "哨兵出现在桥自己的记录里：%s" % own)
            res = t.send(h.request(sender=sender, receiver=receiver, session_id=sender,
                                   payload="再来一句 Authorization: Bearer %s" % SENTINEL,
                                   idempotency_key="idem-secret-2"), wait=False)
            self.assertTrue(res.get("warning"), "没给用户任何标记＝让人以为桥处理过凭据")
        finally:
            h.close()

    def test_09_config_refuses_to_hold_secrets(self):
        h = helpers.TempHome(mode="ack")
        try:
            cfgp = os.path.join(h.home, "config.json")
            _patch_config(cfgp, {"codex_api_token": SENTINEL})
            with self.assertRaises(config_mod.ConfigError) as cm:
                config_mod.load(h.home)
            self.assertEqual(cm.exception.code, "CONFIG_WOULD_HOLD_SECRETS")
        finally:
            h.close()


class TestRedaction(unittest.TestCase):
    def test_10_known_shapes_are_redacted(self):
        for raw in ("token=abcdef1234567890", "sk-ABCDEFGHIJ0123456789",
                    "Bearer abcdef123456789", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ12",
                    "password: hunter2secret"):
            out = security.redact(raw)
            self.assertNotIn(raw.split("=")[-1].split(":")[-1].strip(), out,
                             "%r 没被脱敏：%r" % (raw, out))

    def test_11_jwt_shape_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghIJKLMNOPQRSTUVWXYZ1234"
        self.assertIn("<jwt-redacted>", security.redact("回执里带 %s 就结束了" % jwt))

    def test_12_long_output_is_bounded(self):
        out = security.redact("x" * 100000)
        self.assertLess(len(out), 4000)
        self.assertIn("truncated", out)

    def test_13_contains_secret_shape_does_not_return_content(self):
        self.assertTrue(security.contains_secret_shape("api_key=zzzz111122223333"))
        self.assertFalse(security.contains_secret_shape("正常一句回执"))


    def test_08b_state_files_are_private(self):
        """载荷必然逐字落盘（那是投递的本分），所以权限必须是 0600。"""
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            t.send(h.request(sender=sender, receiver=receiver, session_id=sender,
                             payload="带一句 api_key=zzzz111122223333 的正文"), wait=True)
            bad = []
            for rel in ("state/ledger.json", "trace/events.jsonl",
                        os.path.join("state", "notify-ack.jsonl")):
                p = os.path.join(h.home, rel)
                if os.path.isfile(p) and (os.stat(p).st_mode & 0o077):
                    bad.append((rel, oct(os.stat(p).st_mode)))
            for dirpath, _d, files in os.walk(os.path.join(h.home, "mailbox")):
                for fn in files:
                    p = os.path.join(dirpath, fn)
                    if os.stat(p).st_mode & 0o077:
                        bad.append((os.path.relpath(p, h.home), oct(os.stat(p).st_mode)))
            self.assertEqual(bad, [], "用户数据件对同组／其他人可读：%s" % bad)
        finally:
            h.close()


class TestPathAndWorkspace(unittest.TestCase):
    def test_14_relative_ref_rejected(self):
        with self.assertRaises(security.PathError) as cm:
            security.check_ref_path("workspace_ref", "some/relative/path", "/tmp/q2c-home")
        self.assertEqual(cm.exception.code, "REF_NOT_ABSOLUTE")

    def test_15_dotdot_rejected(self):
        with self.assertRaises(security.PathError) as cm:
            security.check_ref_path("repo_ref", "/tmp/q2c-home/../etc", "/tmp/q2c-home",
                                    allow_outside=True)
        self.assertEqual(cm.exception.code, "REF_HAS_DOTDOT")

    def test_16_outside_home_rejected_unless_explicitly_allowed(self):
        with self.assertRaises(security.PathError) as cm:
            security.check_ref_path("workspace_ref", "/Users/other/project", "/tmp/q2c-home")
        self.assertEqual(cm.exception.code, "REF_OUTSIDE_HOME")
        self.assertTrue(security.check_ref_path("workspace_ref", "/Users/other/project",
                                                "/tmp/q2c-home", allow_outside=True))

    def test_17_credential_paths_refused(self):
        with self.assertRaises(security.PathError) as cm:
            security.check_ref_path("workspace_ref", "/Users/someone/Library/Keychains/login.keychain-db",
                                    "/tmp", allow_outside=True)
        self.assertEqual(cm.exception.code, "REF_IS_CREDENTIAL_PATH")

    def test_18_workspace_allowlist_is_enforced(self):
        h = helpers.TempHome(mode="ack", allowed_workspaces=["/tmp/allowed-only"])
        try:
            t = transport.Transport(h.home)
            s = t.sessions.create("sender", "loopback", label="w-s")
            r = t.sessions.create("receiver", "loopback", label="w-r")
            env = h.request(sender=s.session_id, receiver=r.session_id, session_id=s.session_id,
                            workspace_ref="/tmp/somewhere-else")
            with self.assertRaises(transport.TransportError) as cm:
                t.create(env)
            self.assertEqual(cm.exception.code, "WORKSPACE_NOT_ALLOWED")
        finally:
            h.close()

    def test_19_within_is_not_string_prefix(self):
        self.assertFalse(security.within("/tmp/a", "/tmp/ab/x"))
        self.assertTrue(security.within("/tmp/a", "/tmp/a/x"))
        self.assertTrue(security.within("/tmp/a", "/tmp/a"))


class TestSafeSpawning(unittest.TestCase):
    def test_24_wrapper_actually_runs_and_records_rc(self):
        """行为面对照：真起一次，落盘件与退出码侧件都必须出现。"""
        from q2c.adapters import _spawn
        import tempfile
        d = tempfile.mkdtemp(prefix="q2c-spawn-")
        cap = os.path.join(d, "x.out")
        phase, rc, pid, pid_start = _spawn.spawn([sys.executable, "-c", "print('hi')"],
                                                 dict(os.environ), cap, 10.0)
        self.assertEqual(phase, _spawn.DONE)
        self.assertEqual(rc, 0)
        self.assertEqual(_spawn.collect(cap)[1].strip(), "hi")
        self.assertTrue(os.path.isfile(_spawn.rc_path_of(cap)))
        self.assertEqual(pid_start != "", True, "启动时刻必须登记（pid 会被复用）")
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        _spawn.reap_all()

    def test_20_absolute_interpreter_used(self):
        """起进程必须走**绝对路径**的 shell：裸名字起不来＝无声失败，会被读成「对方没回话」。

        形状钉（读源码）＋行为钉（test_24 真起一次并核对落盘件）两条都要在：
        只留形状那条会漂，只留行为那条查不出"硬写某一家 shell"。
        第二档是本轮新增的：硬写 `/bin/zsh` 在没有 zsh 的干净 Linux 环境里直接跑不动
        （GitHub hosted runner 上撞过），所以还要求候选表存在且不裸用名字。
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "q2c", "adapters", "_spawn.py"), encoding="utf-8") as fh:
            src = fh.read()
        with open(os.path.join(root, "q2c", "security.py"), encoding="utf-8") as fh:
            sec = fh.read()
        self.assertIn("security.posix_shell()", src, "包装 shell 必须走候选表，不许硬写一家")
        self.assertNotIn('["zsh"', src, "裸名字 zsh 不许出现在起进程的地方")
        self.assertIn('"/bin/zsh"', sec)
        self.assertIn('"/bin/sh"', sec, "没有 zsh/bash 的最小环境要有兜底候选")
        self.assertIn("NO_POSIX_SHELL", sec, "一个 shell 都没有时必须显式失败，不退回裸名字")
        # zsh 专有的 `print -r -- $rc` 不许出现在包装脚本里（bash/sh 上语义不同）；
        # 比的是**拼出来的命令串**，不是提到它的注释
        self.assertIn("printf", src)
        self.assertNotIn("print -r -- $rc", src)

    def test_20b_wrapper_runs_under_plain_sh(self):
        """行为钉：把候选 shell 逼到 `/bin/sh`，起一次真调用，退出码与落盘件都得对。

        只有形状钉会漂：注释里写着"用绝对路径"不代表跑得住。这一格证明
        "没有 zsh／bash 的最小环境"里侧件机制仍然成立。
        """
        import tempfile
        from q2c.adapters import _spawn
        from q2c import security as SEC
        if not os.path.exists("/bin/sh"):
            self.skipTest("UNMEASURED：这台机器没有 /bin/sh")
        orig = SEC.posix_shell
        SEC.posix_shell = lambda: "/bin/sh"
        try:
            d = tempfile.mkdtemp(prefix="q2c-sh-")
            cap = os.path.join(d, "x.out")
            phase, rc, pid, pid_start = _spawn.spawn(
                [sys.executable, "-c", "print('via-sh')"], dict(os.environ), cap, 15.0)
            self.assertEqual(phase, _spawn.DONE)
            self.assertEqual(rc, 0, "在 /bin/sh 下退出码侧件没写对：%r" % rc)
            self.assertEqual(_spawn.collect(cap)[1].strip(), "via-sh")
            self.assertEqual(_spawn.read_rc(cap), 0)
        finally:
            SEC.posix_shell = orig

    def test_21_bad_command_template_rejected(self):
        with self.assertRaises(security.PathError) as cm:
            security.safe_argv("echo {not_a_field}", "/tmp")
        self.assertEqual(cm.exception.code, "BAD_TEMPLATE")

    def test_22_missing_executable_rejected(self):
        with self.assertRaises(security.PathError) as cm:
            security.safe_argv("/definitely/not/here arg", "/tmp")
        self.assertEqual(cm.exception.code, "NOT_EXECUTABLE")

    def test_23_empty_command_rejected(self):
        with self.assertRaises(security.PathError) as cm:
            security.safe_argv("   ", "/tmp")
        self.assertEqual(cm.exception.code, "EMPTY_COMMAND")


def _patch_config(path, over):
    import json
    with open(path, encoding="utf-8") as fh:
        doc = json.loads(fh.read())
    doc.update(over)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
