#!/usr/bin/env python3
"""读数工装自己的判据：`tools/ci-readings.py` 是一台"把原始日志折成结论"的机器，
它错了没人会看见——因为它的输出**长得和正确读数一模一样**（同一套键名、同一个 `CI_VERDICT=PASS`）。

2026-10-05 08:5x 写这一组时，我刚被它骗了一次：我把**整 run 的合流日志**
喂进去，`--os macos` 与 `--os ubuntu` 两次调用吐出**逐字相同**的一页读数（同一个 hostname、
同一个 request_id）。原因很朴素：`--os` 只被写进抬头当标签，取数时把整篇扫一遍取**最后一条**命中，
而合流日志里两枚 job 的行都在一起。这份读数一旦被抄进发布报告，"双 OS 各自复验通过"
其实是"同一枚 OS 被数了两遍"——正是主理人裁定第 3 条要防的那种假证据。

四条规矩（每条一格，先红后绿）：
  1. 指定 `--os` 就必须只从**那一枚 job 的行**里取数；两枚 job 的读数不许撞成同一页；
  2. 日志里没有那一档 runner ⇒ 拒（`OS_NOT_IN_LOG`）＋退出码 2，不许"那就取最后一条"；
  3. 合流日志没给 `--os` ⇒ 同样拒（`AMBIGUOUS_LOG`）：多来源取一条当整体＝假读数；
  4. 取数键不许钉死在某一枚版本号上（`q2c-v0.1.0-source.tar.gz` 那类字面在 0.1.1 上直接 MISSING，
     而 MISSING 会被读成"那一格没做"，实际上做了）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(ROOT, "tools", "ci-readings.py")


def _load():
    spec = importlib.util.spec_from_file_location("q2c_ci_readings", SPEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()

#: 两枚 job 混在同一份日志里（`gh run view --log` 的真实形状：每行以 job 名打头，制表符分段）。
#: 每一档各写各的身份，好让"撞成同一页"这件事测得出来。
MAC = "clean-machine (macos-latest)"
UBU = "clean-machine (ubuntu-latest)"
TWO_JOB_LOG = "\n".join([
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z sha=aaaa1111 ref_source=env.GIT_SHA" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z hostname=mac-host-1" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z uname=Darwin arm64" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z ENVIRONMENT=INDEPENDENT_GITHUB_HOSTED" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z package_files=83" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1  q2c-v0.1.1-source.tar.gz" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z Ran 314 tests in 40.0s" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z OK (skipped=62)" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z tests_rc=0" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z clean_machine_rc=0" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z PIP_INSTALL=OK" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z send→receive request_id=req-MAC state=ACKED" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z CLEAN_MACHINE_STATE=ACKED" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:36:57.9Z CI_CLEAN_MACHINE=PASS" % MAC,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z sha=bbbb2222 ref_source=env.GIT_SHA" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z hostname=ubu-host-2" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z uname=Linux x86_64" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z ENVIRONMENT=INDEPENDENT_GITHUB_HOSTED" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z package_files=83" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2  q2c-v0.1.1-source.tar.gz" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z Ran 314 tests in 44.0s" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z OK (skipped=62)" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z tests_rc=0" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z clean_machine_rc=0" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z PIP_INSTALL=OK" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z send→receive request_id=req-UBU state=ACKED" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z CLEAN_MACHINE_STATE=ACKED" % UBU,
    "%s\t独立干净环境复验\t2026-10-05T00:40:11.1Z CI_CLEAN_MACHINE=PASS" % UBU,
]) + "\n"


class _LogFixture(unittest.TestCase):
    def write_log(self, text):
        fd, path = tempfile.mkstemp(prefix="q2c-ci-readings-", suffix=".log", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path


class Test01_按runner取数(_LogFixture):
    def test_two_jobs_in_one_log_do_not_collapse_into_one_page(self):
        """指定 macos 与指定 ubuntu 必须取出**不同**的一页；相同＝`--os` 是装饰。"""
        path = self.write_log(TWO_JOB_LOG)
        mac = MOD.extract(path, "macos")
        ubu = MOD.extract(path, "ubuntu")
        self.assertIn("mac-host-1", mac.get("hostname", ""),
                      "取 ubuntu/macos 之外那枚 job 的行了（读数和标签对不上＝假双 OS 复验）")
        self.assertIn("ubu-host-2", ubu.get("hostname", ""),
                      "两枚 job 的读数撞成同一页：--os 只被写进抬头，取数仍在扫整篇")
        self.assertNotEqual(mac.get("identity"), ubu.get("identity"))
        self.assertIn("req-MAC", mac.get("send_receive", ""))
        self.assertIn("req-UBU", ubu.get("send_receive", ""))
        self.assertTrue(mac.get("pass"), "形状正确的日志本该判 PASS：%s" % (mac,))
        self.assertTrue(ubu.get("pass"))

    def test_unknown_runner_os_is_refused_with_exit_2(self):
        """`--os windows`（日志里没这档 runner）⇒ 退 2 ＋ 说清是 OS 的事，不许"取最后一条"顶替。"""
        path = self.write_log(TWO_JOB_LOG)
        p = subprocess.run([sys.executable, SPEC_PATH, "--log", path, "--os", "windows"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 2, "非零但没分档（1＝跑过了没通过，2＝测不了）：%s" % p.stdout[-200:])
        self.assertIn("OS_NOT_IN_LOG", p.stdout, p.stdout[-300:])

    def test_multi_job_log_without_os_is_refused(self):
        """不给 `--os` 而日志里有多枚 job ⇒ 拒（AMBIGUOUS_LOG）：多来源里挑一条当整体＝假读数。"""
        path = self.write_log(TWO_JOB_LOG)
        p = subprocess.run([sys.executable, SPEC_PATH, "--log", path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 2, p.stdout[-200:])
        self.assertIn("AMBIGUOUS_LOG", p.stdout, p.stdout[-300:])

    def test_single_job_log_needs_no_os_label(self):
        """只有一枚 job 的日志（按作业下载的形状）不指 `--os` 也该能读数——别把路堵死。"""
        one = "".join(ln + "\n" for ln in TWO_JOB_LOG.splitlines() if ln.startswith(MAC))
        path = self.write_log(one)
        p = subprocess.run([sys.executable, SPEC_PATH, "--log", path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout[-300:])
        self.assertIn("CI_VERDICT=PASS", p.stdout)
        self.assertIn("mac-host-1", p.stdout)

    def test_per_job_api_log_has_no_job_prefix_and_still_reads(self):
        """`gh api repos/…/actions/jobs/<id>/logs` 那种日志**没有** `job名\t步骤\t` 前缀。

        这是 2026-10-05 09:0x 真实取数撞出来的：加了 os 过滤之后我把"整篇没有 job 前缀"
        当成了"没有那一档 runner"，于是最该用的一条路（按作业下载）反倒退 2。
        规矩：没有前缀＝单作业日志，`--os` 只当标签； refusal 只在**日志里确实有带前缀的行、
        而没有匹配的那一枚**时才发生。
        """
        plain = "\n".join(ln.split("\t")[-1] for ln in TWO_JOB_LOG.splitlines() if ln.startswith(MAC)) + "\n"
        path = self.write_log(plain)
        p = subprocess.run([sys.executable, SPEC_PATH, "--log", path, "--os", "macos"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, "按作业下载的日志被拒了（那条路是最常用的）：%s" % p.stdout[-250:])
        self.assertIn("CI_VERDICT=PASS", p.stdout)
        self.assertIn("mac-host-1", p.stdout)


class Test02_取数键不许钉版本号(_LogFixture):
    def test_package_sha_line_follows_the_version_being_released(self):
        """0.1.1 的日志里那行是 `q2c-v0.1.1-source.tar.gz`；旧写法把它读成 MISSING。

        MISSING 的害处不是"少一格"，是**会被抄进报告当成"那一格没做"**——
        而这件包里明明有 SHA256 行、CI 也真算过。同一族缺陷这轮我在两处修过
        （`tools/make-manifest.py` 与 `tools/ci-clean-machine.sh` 的文件名写死），这是第三处。
        """
        path = self.write_log(TWO_JOB_LOG)
        mac = MOD.extract(path, "macos")
        self.assertNotEqual(mac.get("package_sha_line"), "MISSING",
                            "包 SHA256 那一行被版本号钉死了（只认 v0.1.0）：%s" % (mac.get("package_sha_line"),))
        self.assertIn("d1d1d1d1", mac.get("package_sha_line", ""))
        self.assertIn("q2c-v0.1.1-source.tar.gz", mac.get("package_sha_line", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
