#!/usr/bin/env python3
"""进程安全判据：进程组清理、无孤儿、无自繁殖、探针三态。

迁移自实验根的 K29／K60／K61／K78／C7／K58／mutations-fg 进程组那一族
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 E 组）。

这一组里最贵的一格是"嵌套子进程必须一起清掉"：
`shell=True` 那条链上只杀 shell，孙子进程会继续写盘——实验根里为 this 清了三遍才干净，
而且"杀叶子会越杀越多"（父还在生）。所以这里的断言是**扫进程表**，不是"我们调了 kill"。
"""

import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import protocol, security, transport  # noqa: E402
from tests import helpers  # noqa: E402

PS_AVAILABLE = subprocess.run(["/bin/ps", "-eo", "pid,pgid,command"],
                              stdout=subprocess.PIPE, text=True).returncode == 0


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def responder_procs(needle):
    """命令行里带着这枚 `needle`（请求号）的应答器进程。

    按请求号认而不是按路径认：这台机器上同时跑着别人的 q2c 进程，
    按"像不像 q2c"筛会把别人算成我的孤儿，也会把我的漏网进程放过去。
    """
    if not PS_AVAILABLE:
        return None                              # 读不到＝UNMEASURED，不折成 0
    p = subprocess.run(["/bin/ps", "-eo", "pid,pgid,command"],
                       stdout=subprocess.PIPE, text=True)
    hits = []
    for ln in p.stdout.splitlines():
        if "q2c.stub_responder" in ln and needle in ln:
            parts = ln.split(None, 2)
            hits.append((int(parts[0]), int(parts[1])))
    return hits


class TestProcessSafety(unittest.TestCase):
    def test_01_completed_run_leaves_no_child(self):
        if not PS_AVAILABLE:
            self.skipTest("UNMEASURED：本机读不到进程表（这一格没测，不算过）")
        h = helpers.TempHome(mode="ack")
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            self.assertEqual(t.send(env, wait=True)["pump"]["state"], protocol.ST_ACKED)
            time.sleep(0.3)
            left = responder_procs(env.request_id)
            self.assertEqual(left, [], "跑完还有应答器活着＝孤儿／僵尸：%s" % left)
        finally:
            h.close()

    def test_02_cancel_kills_the_whole_group(self):
        """取消一腿 ⇒ 整组走人，包括 zsh 包装下面的那个 python。"""
        if not PS_AVAILABLE:
            self.skipTest("UNMEASURED：本机读不到进程表")
        h = helpers.TempHome(mode="ack", receiver_mode="sleep", sleep_s=6.0,
                             receive_timeout_s=0.3)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)
            self.assertEqual(t.pump(rid)["status"], "in_flight")
            pid = t.read(rid)["record"]["handles"]["receiver"]["pid"]
            self.assertTrue(security.group_alive(pid))
            adapter = transport.adapters.build("loopback", t.cfg.adapter_config("loopback"))
            from q2c.adapters.base import Handle
            rec = t.read(rid)["record"]
            handle = Handle(adapter="loopback", role="receiver",
                            provider_session_id=rec["handles"]["receiver"]["provider_session_id"],
                            pid=pid, pid_start=rec["handles"]["receiver"]["pid_start"])
            self.assertTrue(adapter.cancel(handle))
            deadline = time.time() + 5
            while time.time() < deadline and security.group_alive(pid):
                time.sleep(0.1)
            self.assertFalse(security.group_alive(pid), "整组没清掉")
            left = responder_procs(env.request_id) or []
            self.assertEqual(left, [], "孙子进程还在（只杀了包装＝孤儿继续写盘）：%s" % left)
        finally:
            h.close()

    def test_03_pid_state_three_states(self):
        """alive／gone／never_started／reused／unknown——**unknown 永不折成 gone**。"""
        self.assertEqual(security.pid_state(None, ""), "never_started")
        self.assertEqual(security.pid_state(0, "x"), "never_started")
        self.assertEqual(security.pid_state(os.getpid(), "显然不是它的启动时刻"), "reused")
        self.assertEqual(security.pid_state("不是数字", "x"), "unknown")
        self.assertEqual(security.pid_state(os.getpid(), ""), "unknown",
                         "登记过 pid 却没启动时刻 ⇒ 必须 unknown，不能当 alive 也不能当 gone")
        self.assertEqual(security.pid_state(os.getpid(), security._proc_start_marker(os.getpid())),
                         "alive")
        self.assertEqual(security.pid_state(4194305, "Wed Jan  1 00:00:00 2025"), "gone")

    def test_04_kill_group_is_honest_about_failures(self):
        self.assertEqual(security.kill_group(4194305), "no-such-process")
        self.assertIn(security.kill_group(None), ("no-such-process",))

    def test_05_pump_on_parked_record_spawns_nothing(self):
        """§15 "递归／自繁殖"的产品版：停手的记录被反复 pump，一次进程都不许多起。

        旧内核那 150 枚进程的教训换算到这里就一句话：
        **控制入口不许在"读不懂／已停手"时选择做最多的那件事。**
        """
        h = helpers.TempHome(mode="no-ack", delivery_attempts_max=1)
        try:
            t, sender, receiver = h.sessions()
            env = h.request(sender=sender, receiver=receiver, session_id=sender)
            rid = t.create(env)["request_id"]
            t.pump(rid)
            t.pump(rid)                              # 到上限 ⇒ 停放
            self.assertEqual(t.read(rid)["record"]["state"], protocol.ST_DELIVERY_FAILED)
            recv = t.sessions.get(receiver).current()
            before = h.responder_calls(session=recv)
            for _ in range(8):
                t.pump(rid)
                t.recover()
            self.assertEqual(h.responder_calls(session=recv), before,
                             "停手的记录被 pump／recover 催活了：自动重试越过了预算")
        finally:
            h.close()

    def test_06_no_self_reexec(self):
        """`q2c` 的入口不许把自己再叫起来（调度器不得当自己的叶子命令）。

        做法：只读命令跑在子进程里，扫进程表看有没有第二枚 `q2c` 被它带起来。
        """
        if not PS_AVAILABLE:
            self.skipTest("UNMEASURED：本机读不到进程表")
        h = helpers.TempHome(mode="ack")
        try:
            p = subprocess.Popen([sys.executable, "-m", "q2c", "--home", h.home, "list"],
                                 cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 env=dict(os.environ, PYTHONPATH=os.path.dirname(
                                     os.path.dirname(os.path.abspath(__file__)))))
            out, _ = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, out)
            mine = subprocess.run(["/bin/ps", "-eo", "command"], stdout=subprocess.PIPE,
                                  text=True).stdout
            # 只数"又起了一枚 -m q2c 且带着同一个 home"的：入口自繁殖的唯一可见形状。
            # 别的路径里带 q2c 字样的进程（插件、本测试自身）不算，也不算过。
            procs = [ln for ln in mine.splitlines()
                     if "-m q2c " in ln and h.home in ln]
            self.assertEqual(procs, [], "只读命令带起了新的 q2c 进程＝再入")
        finally:
            h.close()

    def test_07_lock_is_a_real_mutex(self):
        """写锁不是摆设（U4 那一族）：第二把必须等待，且超时后如实报"没拿到"。"""
        from q2c import ledger
        h = helpers.TempHome(mode="ack")
        try:
            paths = ledger.LedgerPaths(h.home)
            p = subprocess.Popen([sys.executable, "-c",
                                  "import sys,time;sys.path.insert(0,%r);"
                                  "from q2c import ledger;"
                                  "p=ledger.LedgerPaths(%r);"
                                  "import contextlib;"
                                  "ctx=ledger.ledger_lock(p,timeout_s=8.0,events=[]);"
                                  "got=ctx.__enter__();print('HELD',got);time.sleep(3);"
                                  "ctx.__exit__(None,None,None)" % (
                                      os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      h.home)],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(0.7)
            t0 = time.time()
            with ledger.ledger_lock(paths, timeout_s=1.0, events=[]) as got:
                waited = time.time() - t0
            self.assertFalse(got, "锁被别人持有却拿到了＝临界区是空的")
            self.assertGreaterEqual(waited, 0.9, "没等就返回＝锁形同虚设")
            out, _ = p.communicate(timeout=20)
            self.assertIn("HELD True", out)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
