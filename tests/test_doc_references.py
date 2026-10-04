#!/usr/bin/env python3
"""文档与注释里点到的判据件／模块名，必须真的存在。

为什么单独有一格（今天撞上的）：写审计表时我按计划里的名字引用了
`test_correlation.py`、`test_ledger.py` 等五枚**从没落地过**的文件（这里按名引用，
不带 `tests/` 前缀——带前缀就会被本格的扫描器抓到，那是它该有的反应），
`q2c/ack.py` 的注释也跟着指向其中一枚。后果不是"文档不好看"，是**下一位读者按引用去找保护，
找不到，就认为那条保护不存在**——2026-10-04 那位独立评审正是这么读到旧包的。
所以引用关系本身要有牙：悬空引用＝红。

口径两条，都是静态核，不跑被测件：
  · 凡文中出现 `tests/test_*.py`，该文件必须在 `tests/` 里；
  · 凡文中出现 `q2c/….py`（顶层模块或 `q2c/adapters/…`），该文件必须在仓里。
历史文档（审计表左列那种"计划名"）允许存在，但必须同页给出"实际在册件"的对照——
本仓的对照写在 `Q2C-BOUNDARY-AUDIT.md` 的落点对照一节，这里核的是**右列全部为真**。
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEST_REF = re.compile(r"tests/(test_[a-z0-9_]+\.py)")
MODULE_REF = re.compile(r"(q2c/(?:adapters/)?[a-z_]+\.py)")

#: 仓根每一枚 md 都在射程内（**按目录现读，不写名单**）：写名单＝新增一份就少扫一份，
#: 而新写的那一份恰恰最爱抄错引用。
DOC_GLOBS = sorted(f for f in os.listdir(ROOT) if f.endswith(".md"))


def _sources():
    out = []
    for rel in DOC_GLOBS:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            out.append((rel, p))
    for d in ("q2c", "tools", "tests"):
        dd = os.path.join(ROOT, d)
        if not os.path.isdir(dd):
            continue
        for fn in sorted(os.listdir(dd)):
            if fn.endswith(".py"):
                out.append(("%s/%s" % (d, fn), os.path.join(dd, fn)))
        sub = os.path.join(dd, "adapters")
        if os.path.isdir(sub):
            for fn in sorted(os.listdir(sub)):
                if fn.endswith(".py"):
                    out.append(("%s/adapters/%s" % (d, fn), os.path.join(sub, fn)))
    return out


class TestDocumentedReferencesResolve(unittest.TestCase):
    def setUp(self):
        self.srcs = _sources()
        self.assertTrue(self.srcs, "一枚源文件都没读到＝扫描器接错了地方，不算通过")

    def test_01_every_referenced_test_file_exists(self):
        have = set(os.listdir(os.path.join(ROOT, "tests")))
        dangling = {}
        for rel, p in self.srcs:
            with open(p, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            for m in TEST_REF.findall(txt):
                if m not in have:
                    dangling.setdefault(m, []).append(rel)
        self.assertEqual(dangling, {},
                         "这些引用指向不存在的判据件（读者会以为那条保护没落地）：%s" % (dangling,))

    def test_02_every_referenced_module_file_exists(self):
        missing = {}
        for rel, p in self.srcs:
            with open(p, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            for m in set(MODULE_REF.findall(txt)):
                if not os.path.isfile(os.path.join(ROOT, m)):
                    missing.setdefault(m, []).append(rel)
        self.assertEqual(missing, {}, "这些引用的模块文件不在仓里：%s" % (missing,))

    def test_03_reference_scanner_is_not_toothless(self):
        """这一格校扫描器本身：给它一段悬空引用，它必须报出来。

        为什么要有：静态扫描器最容易变成"永远返回空"的死判据（在册同类事故三次）。
        这里在临时目录里造一份最小源件＋不存在的被引用件，复算同一套规则，要求命中。
        """
        import tempfile
        # 件名按段拼出来：本文件的注释里若写下"目录名/文件名"的完整形态，
        # 本格自己的扫描就会把它当悬空引用（自己把自己判红，那不算判据有牙）。
        ghost = "test_" + "ghost.py"
        real = "test_" + "real.py"
        with tempfile.TemporaryDirectory() as td:
            tdir = os.path.join(td, "tests")
            os.makedirs(tdir)
            open(os.path.join(tdir, real), "w").close()
            doc = os.path.join(td, "NOTE.md")
            with open(doc, "w", encoding="utf-8") as fh:
                fh.write("判据见 %s/%s 与 %s/%s。\n" % ("tests", real, "tests", ghost))
            with open(doc, encoding="utf-8") as fh:
                refs = TEST_REF.findall(fh.read())
            self.assertIn(ghost, refs, "扫描器没抓到悬空引用＝它是死的")
            have = set(os.listdir(tdir))
            self.assertEqual([r for r in refs if r not in have], [ghost])


if __name__ == "__main__":
    unittest.main(verbosity=2)
