#!/usr/bin/env python3
"""仓里那几枚 `sh` 脚本必须在**别的机器的 /bin/sh** 上跑得起来。

为什么要这一组判据（不是我想加仪表，是 CI 抓出来的）：
2026-10-04 独立托管环境复验两枚 job 全红，拒因都不在产品逻辑里，而在 shell 方言里 ——

  1. `tools/ci-clean-machine.sh:61` 把 `&&` 写在行首。本机从来没跑到这一行，
     而 macOS 与 ubuntu 的 `sh`（bash 5.x／dash）都在**解析期**就拒：
     `syntax error near unexpected token '&&'` ⇒ 整个作业退 2。
  2. `tools/clean-machine-test.sh:44` 写的是 `rc=$DRC；ledger=…`，`$DRC` 紧跟一枚全角分号。
     那三个字节被跑在托管 runner 上的 shell 并进变量名 ⇒ `set -u` 报
     `line 44: DRC\ufffd: unbound variable`，脚本在末行 `CLEAN_MACHINE_STATE` **之前**就死了。
     这一条本机（bash 3.2／dash）不复现 —— 所以"我这边跑绿"根本不构成异机证据。

三条纪律由此钉成机器判据：POSIX 解析干净、变量紧邻非 ASCII 必须带花括号、
`&&`／`||` 不许起行。末格是把清洁机门真跑一遍到 `ACKED`（这条本机一直是绿的，
它钉的是"别把门自己改坏"，不声称能复现第 2 条——第 2 条由上面那格静态判据兜住）。

在册同族教训：变量引用一律 `${VAR}`（后跟全角字符会被吞进变量名）——这次是 shell 版。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 待检的 shell 件：全部 `#!/bin/sh` 入口（工具与免安装入口）。
TARGETS = ["bin/q2c", "tools/clean-machine-test.sh", "tools/ci-clean-machine.sh",
           "tools/pkg-rehearsal.sh", "tools/verify.sh"]


def _shell_scripts():
    out = []
    for rel in TARGETS:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            out.append(rel)
    # 再加一遍全量扫（防有人新写一枚 .sh 却没登记进上面名单）
    for d in ("tools", "bin"):
        dd = os.path.join(ROOT, d)
        if os.path.isdir(dd):
            for fn in sorted(os.listdir(dd)):
                rel = "%s/%s" % (d, fn)
                if rel in out or not os.path.isfile(os.path.join(ROOT, rel)):
                    continue
                with open(os.path.join(ROOT, rel), "rb") as fh:
                    head = fh.readline()
                if head.startswith(b"#!/bin/sh") or head.startswith(b"#!/usr/bin/env sh"):
                    out.append(rel)
    return sorted(out)


# `$NAME` 后面紧跟一个非 ASCII 字节 ⇒ 某些 shell 会把它并进变量名（本机不复现，异机炸）。
VAR_ADJ_NON_ASCII = re.compile(rb"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]")
# `&&`／`||` 起行（不在续行反斜杠之后）＝ POSIX 语法错。
LEADING_LOGIC = re.compile(r"^\s*(&&|\|\|)")


class TestShellPortability(unittest.TestCase):
    def scripts(self):
        got = _shell_scripts()
        self.assertTrue(got, "一枚 shell 件都没找到＝扫描器接错了地方，不算通过")
        return got

    # ---- 1) 解析期：POSIX sh 能解析 ---------------------------------------

    def test_01_posix_sh_can_parse_every_script(self):
        bad = []
        for rel in self.scripts():
            p = subprocess.run(["/bin/sh", "-n", rel], cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=60)
            if p.returncode != 0:
                bad.append("%s: %s" % (rel, (p.stdout or "").strip().splitlines()[:2]))
        self.assertEqual(bad, [], "这些 shell 件在 POSIX sh 的**解析期**就过不去：%s" % (bad,))

    def test_02_dash_can_parse_every_script(self):
        """ubuntu 托管 runner 的 /bin/sh 是 dash；本机也有 /bin/dash，多一道对照。"""
        if not os.path.exists("/bin/dash"):
            self.skipTest("本机没有 /bin/dash（对照档跑不了，不折成通过）")
        bad = []
        for rel in self.scripts():
            p = subprocess.run(["/bin/dash", "-n", rel], cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, timeout=60)
            if p.returncode != 0:
                bad.append("%s: %s" % (rel, (p.stdout or "").strip().splitlines()[:2]))
        self.assertEqual(bad, [], "dash 解析不过：%s" % (bad,))

    # ---- 2) 已知会炸的两种写法：静态钉 ------------------------------------

    def test_03_no_logic_operator_at_line_start(self):
        bad = []
        for rel in self.scripts():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                for i, ln in enumerate(fh, 1):
                    if ln.lstrip().startswith("#"):
                        continue
                    if LEADING_LOGIC.match(ln):
                        bad.append("%s:%d" % (rel, i))
        self.assertEqual(bad, [], "`&&`/`||` 起行在 POSIX sh 是语法错（CI 里两枚 OS 都拒）：%s" % (bad,))

    def test_04_variable_adjacent_to_non_ascii_is_braced(self):
        bad = []
        for rel in self.scripts():
            with open(os.path.join(ROOT, rel), "rb") as fh:
                for i, raw in enumerate(fh, 1):
                    if raw.lstrip().startswith(b"#"):
                        continue
                    if VAR_ADJ_NON_ASCII.search(raw):
                        bad.append("%s:%d" % (rel, i))
        self.assertEqual(bad, [], "这些行 `$VAR` 紧跟非 ASCII 字符，必须写成 ${VAR}：%s" % (bad,))

    def test_05_scripts_are_utf8_and_newline_terminated(self):
        """清单与脚本按逐字节哈希钉身份，非 UTF-8／末行无换行的件会让复算歧义。"""
        for rel in self.scripts():
            with open(os.path.join(ROOT, rel), "rb") as fh:
                b = fh.read()
            try:
                b.decode("utf-8")
            except UnicodeDecodeError as exc:
                self.fail("%s 不是合法 UTF-8：%s" % (rel, exc))
            self.assertTrue(b.endswith(b"\n"), "%s 末行没有换行符" % rel)
            self.assertNotIn(b"\r\n", b, "%s 混进 CRLF（POSIX shell 会把 \\r 当命令一部分）" % rel)

    # ---- 3) 门自己得真跑得完 ----------------------------------------------

    def test_06_clean_machine_gate_runs_to_state_line_under_posix_sh(self):
        """`/bin/sh tools/clean-machine-test.sh` 必须跑到末行状态，中间不许有 shell 报错。

        这条本机常绿（本机 shell 看不见第 2 格那类 bug），它钉的是另一件事：
        门脚本不许被改坏到跑不完、不许末行状态缺失。
        """
        script = os.path.join(ROOT, "tools", "clean-machine-test.sh")
        if not os.path.isfile(script):
            self.skipTest("门脚本不在")
        env = dict(os.environ)
        env.pop("Q2C_HOME", None)
        p = subprocess.run(["/bin/sh", "tools/clean-machine-test.sh"], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        out = p.stdout or ""
        self.assertNotIn("unbound variable", out, "门脚本里有变量在 set -u 下没定义：\n%s" % out[-800:])
        self.assertNotIn("syntax error", out, "门脚本里有 POSIX 语法错：\n%s" % out[-800:])
        self.assertIn("CLEAN_MACHINE_STATE=ACKED", out, "末行状态没到 ACKED：\n%s" % out[-1200:])
        self.assertEqual(p.returncode, 0, "门脚本退出码非 0：\n%s" % out[-1200:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
