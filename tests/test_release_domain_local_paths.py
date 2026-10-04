#!/usr/bin/env python3
"""发布域里不许留本机绝对路径与用户名（公开前一晚自己加的闸）。

起因是我自己在册的一条老教训换了个方向复发：**私人项目名、家目录路径这类东西外泄，
没有任何测试会红**。这一轮要把仓推去公开，我把发布域 71 个文件扫了一遍，
撞见 `<外部卷名>/…`、`/Users/<用户名>/…` 这些本机坐标——它们对读者毫无价值，
只会暴露这台机器的卷标、用户名和另一个私人项目的目录名。
所以：脱敏做完，还得有一格判据盯着，否则下一次"顺手写个本机路径"照样没人拦。

三条规矩：
  1. 发布域任何文件里不许出现**这台机器的家目录**（含 `/Users/<user>`、`/home/<user>` 形态）；
  2. 不许出现 macOS 外置卷挂载点前缀（那串里带卷标＝私人设备名）；
  3. 扫描器自己要有牙：造一条命中必须报出来，不许写成永远返回空的死判据。

证据域（`evidence/`）不在这一条范围内：那是原件，本机路径是事实的一部分，
不删不改（在册纪律），而它**不进发布包、不进公开仓**。
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
USER = getpass.getuser()

#: 会被判成"本机坐标"的模式。全部由这台机器现算，测试源码里不留字面量
#: （留了就把要抓的东西又写回发布域里了）。
PATTERNS = [
    ("home-dir", re.escape(HOME)),
    ("users-dir", "/Users/" + re.escape(USER) + r"\b"),
    ("home-user", "/home/" + re.escape(USER) + r"\b"),
    ("external-volume", r"/Volu" + r"mes/"),
]


def _release_files():
    raw = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], stdout=subprocess.PIPE,
                         check=True).stdout
    out = []
    for f in raw.split(b"\0"):
        if not f:
            continue
        rel = f.decode("utf-8")
        if rel.startswith("evidence/"):
            continue                      # 证据域允许留本机坐标（它不进包、不进公开仓）
        if os.path.isfile(os.path.join(ROOT, rel)):
            out.append(rel)
    return out


@unittest.skipUnless(subprocess.run(["git", "-C", ROOT, "rev-parse", "--git-dir"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL).returncode == 0,
                    "不是 git 仓（解包件）⇒ 没有跟踪面可扫")
class TestReleaseDomainHasNoLocalPaths(unittest.TestCase):
    def _scan(self, texts):
        """texts: [(名字, 内容)] ⇒ 命中列表 [(名字, 模式名, 片段)]。片段本身已截断，不带凭据。"""
        hits = []
        for name, body in texts:
            for label, pat in PATTERNS:
                m = re.search(pat, body)
                if m:
                    start = max(0, m.start() - 20)
                    hits.append((name, label, body[start:m.end() + 20].replace("\n", " ")))
        return hits

    def test_01_release_files_have_no_local_coordinates(self):
        files = _release_files()
        self.assertTrue(files, "一枚发布域文件都没读到＝扫描器接错了地方，不算通过")
        texts = []
        for rel in files:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
                texts.append((rel, fh.read()))
        hits = self._scan(texts)
        self.assertEqual(hits, [],
                         "发布域里出现本机坐标（公开后就是别人的信息）：\n%s"
                         % "\n".join("  %s：%s ← …%s…" % h for h in hits[:8]))

    def test_02_scanner_is_not_toothless(self):
        """反照：同一套规则喂一条本机路径，必须报出来。"""
        probe = [("probe.md", "原件在 " + HOME + "/secret-project 下")]
        hits = self._scan(probe)
        self.assertTrue(hits, "扫描器抓不到自家绝对路径＝它是死的")
        self.assertEqual(hits[0][1], "home-dir")

    def test_03_evidence_domain_is_deliberately_out_of_scope(self):
        """证据域**故意**不在范围内：那里本机路径是事实的一部分，不许改写。

        这一格钉的是"范围收窄"这件事本身：`evidence/` 不进发布包也不进公开仓，
        所以它可以留坐标；哪天有人把 evidence 也打进包里，这条边界就必须在打包那侧拦，
        而不是跑到这里来给证据加脱敏（那等于改史）。
        """
        p = subprocess.run(["git", "-C", ROOT, "ls-files", "evidence/"],
                           stdout=subprocess.PIPE, text=True)
        tracked_evidence = [l for l in p.stdout.splitlines() if l.strip()]
        self.assertTrue(tracked_evidence, "仓里没有证据件？那这条边界说明就该整段重写")
        self.assertTrue(all(not f.startswith("evidence/") for f in _release_files()),
                        "evidence/ 混进发布域扫描范围了（要么改代码要么改这一格，别两边都改）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
