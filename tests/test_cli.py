#!/usr/bin/env python3
"""CLI 判据：命令面、退出码契约、只读零写入。

退出码是**契约**（README 与 SECURITY.md 都照着它写），所以这一组的断言全是 rc，
不是"输出里有没有某个词"。脚本要能照着分支，人才敢把它接进别的流程。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import helpers  # noqa: E402


def run(home, *args, expect=None):
    env = dict(os.environ, Q2C_HOME=home, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "q2c"] + list(args), cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    if expect is not None and p.returncode != expect:
        raise AssertionError("rc=%s 期望 %s：%s\n%s" % (p.returncode, expect, list(args),
                                                        p.stdout[:900]))
    return p


def jout(p):
    txt = p.stdout.strip()
    for candidate in (txt, txt.splitlines()[-1] if txt else ""):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


class TestCli(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="q2c-cli-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def test_01_version_and_init(self):
        d = jout(run(self.home, "version", expect=0))
        self.assertEqual(d["protocol_version"], "q2c/1")
        run(self.home, "init", expect=0)
        self.assertTrue(os.path.isfile(os.path.join(self.home, "config.json")))

    def test_02_doctor_and_adapters_are_read_only(self):
        run(self.home, "init", expect=0)
        before = helpers.TempHome.__module__ and _tree(self.home)
        run(self.home, "doctor", expect=0)
        run(self.home, "adapters", expect=0)
        self.assertEqual(_tree(self.home), before, "只读命令改写了根目录")

    def test_03_full_handoff_via_cli(self):
        run(self.home, "init", expect=0)
        s = jout(run(self.home, "sessions", "create", "--role", "sender",
                     "--adapter", "inproc", "--label", "cli-s", expect=0))
        r = jout(run(self.home, "sessions", "create", "--role", "receiver",
                     "--adapter", "inproc", "--label", "cli-r", expect=0))
        d = jout(run(self.home, "send", "--from", s["session_id"], "--to", r["session_id"],
                     "--type", "handoff", "--payload", "请把这件事接手过去。", expect=0))
        self.assertEqual(d["pump"]["state"], "ACKED")
        run(self.home, "inspect", d["request_id"], expect=0)
        run(self.home, "list", expect=0)
        q = jout(run(self.home, "trace", d["request_id"], expect=0))
        self.assertEqual(q["when_acknowledged"] not in (None, "NOT_OBSERVED"), True)

    def test_04_unknown_artifact_type_is_rejected_with_rc2(self):
        run(self.home, "init", expect=0)
        s = jout(run(self.home, "sessions", "create", "--role", "sender",
                     "--adapter", "inproc", "--label", "c-s2", expect=0))
        r = jout(run(self.home, "sessions", "create", "--role", "receiver",
                     "--adapter", "inproc", "--label", "c-r2", expect=0))
        p = run(self.home, "send", "--from", s["session_id"], "--to", r["session_id"],
                "--payload", "x", "--artifact", "signoff=whatever", expect=2)
        self.assertIn("UNKNOWN_ARTIFACT_TYPE", p.stdout)

    def test_05_inspect_unknown_is_rc1(self):
        run(self.home, "init", expect=0)
        run(self.home, "inspect", "req-not-there", expect=1)

    def test_06_trace_without_events_is_rc3(self):
        run(self.home, "init", expect=0)
        run(self.home, "trace", "req-not-there", expect=3)

    def test_07_unknown_adapter_is_rc2(self):
        run(self.home, "init", expect=0)
        p = run(self.home, "sessions", "create", "--role", "receiver",
                "--adapter", "nope", "--label", "x", expect=2)
        self.assertIn("UNKNOWN_ADAPTER", p.stdout)

    def test_08_retry_missing_record_is_rc2(self):
        run(self.home, "init", expect=0)
        run(self.home, "retry-delivery", "req-ghost", expect=2)

    def test_09_bogus_subcommand_is_rc2(self):
        run(self.home, "approve-release", expect=2)

    def test_10_no_project_management_verbs_exist(self):
        """产品边界在 CLI 上的物理体现：这些动词**不存在**。

        判据用 `--help` 的文本，不用"我猜它没有"：将来有人加了 `approve`，这一格会红。
        """
        p = run(self.home, "--help", expect=0)
        for verb in ("approve", "gate", "release", "close", "waive", "schedule",
                     "complete-task", "grade"):
            self.assertNotIn(verb, p.stdout, "CLI 里冒出了项目管理的动词：%s" % verb)

    def test_11_cancel_and_expire_and_recover_are_reachable(self):
        run(self.home, "init", expect=0)
        s = jout(run(self.home, "sessions", "create", "--role", "sender",
                     "--adapter", "inproc", "--label", "c-s3", expect=0))
        r = jout(run(self.home, "sessions", "create", "--role", "receiver",
                     "--adapter", "inproc", "--label", "c-r3", expect=0))
        d = jout(run(self.home, "send", "--from", s["session_id"], "--to", r["session_id"],
                     "--payload", "这一笔我要取消。", "--no-wait", expect=0))
        run(self.home, "cancel", d["request_id"], "--reason", "不投了", expect=0)
        run(self.home, "expire", expect=0)
        run(self.home, "recover", expect=0)
        rec = jout(run(self.home, "inspect", d["request_id"], expect=0))
        self.assertEqual(rec["record"]["state"], "CANCELLED")


def _tree(home):
    return sorted(os.path.relpath(os.path.join(d, f), home)
                  for d, _dirs, fs in os.walk(home) for f in fs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
