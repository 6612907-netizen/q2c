#!/usr/bin/env python3
"""厂商口径判据：**验过的是 Qoder CN，不是 Qoder International**，文档不许把两件事混着说。

为什么单独一组（主理人 2026-10-04 明令）：
`q2c/adapters/qoder.py:26` 里 `BIN = "qoderclicn"`——真跑过的那条腿用的是 **CN 版 CLI**，
它连的后端、SDK 入口变量那一族、`--permission-mode auto` 的取值都是 CN 这一支的形状。
代码里写死了目标，文档却只说"Qoder"——读者会以为整条产品线都验过。
后果不是难看，是**误用**：拿 International 那头照抄我们的命令，第一条就起不来，
然后回头怀疑桥不可靠。

四条规矩：
  1. 代码里那个 BIN 是什么，文档就得写什么（不许泛称）；
  2. README（＝PyPI 页面正文）里必须有四行机器可读口径：
     `QODER_CN_VERIFIED`／`QODER_INTERNATIONAL_VERIFIED`／`CN_SPECIFIC_DEPENDENCIES`／
     `CORE_PROTOCOL_VENDOR_NEUTRAL`——装包的人在包页面上就能看见边界；
  3. 任何文档里"International"与"已验证／VERIFIED"同句出现 ⇒ 红（这一条防的是我自己在
     写发布记录时顺手把话说过头）；
  4. 不许为了让这一组绿就把措辞改成含糊话：四行的**取值**也被钉住，
     International 那一行必须是未验证档，改成 VERIFIED 就红。
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 已验证的那一侧（真跑过＝CN 版 CLI；见 evidence/real-legs/）
CN_BIN = "qoderclicn"
DOC_FILES = ["README.md", "ADAPTERS.md", "SECURITY.md", "ARCHITECTURE.md", "PROTOCOL.md",
             "CHANGELOG.md", "CONTRIBUTING.md"]
#: 会被搬上 PyPI 包页面的两份（正文＝README；描述字段＝pyproject）
PYPI_SURFACE = ["README.md", "pyproject.toml"]

REQUIRED_KEYS = {
    "QODER_CN_VERIFIED": "YES",
    "QODER_INTERNATIONAL_VERIFIED": "NOT_VERIFIED",
    "CORE_PROTOCOL_VENDOR_NEUTRAL": "YES",
}
FORBIDDEN_INTERNATIONAL_VERDICTS = ("YES", "已验证", "成立", re.compile(r"\bVERIFIED\b"))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class Test01_代码与文档说的同一枚CLI(unittest.TestCase):
    def test_adapter_bin_is_the_cn_cli(self):
        src = read(os.path.join("q2c", "adapters", "qoder.py"))
        m = re.search(r'^BIN\s*=\s*"([^"]+)"', src, re.M)
        self.assertIsNotNone(m, "qoder 适配器里没有 BIN 那一行⇒目标是谁读不出来")
        self.assertEqual(m.group(1), CN_BIN,
                          "适配器指向的 CLI 变了（现在=%s）。改了代码必须同时改文档与本组口径，"
                          "不许让文档继续说旧的那一枚" % m.group(1))

    def test_adapters_doc_names_the_cn_cli(self):
        text = read("ADAPTERS.md")
        self.assertIn(CN_BIN, text,
                      "ADAPTERS.md 只说“Qoder”，不写实际那枚 CLI ⇒ 用户拿另一支照抄，"
                      "第一条命令就起不来")
        sec = re.search(r"^##\s*2\.\s*Qoder(.*?)(?=^## |\Z)", text, re.M | re.S)
        self.assertIsNotNone(sec, "ADAPTERS.md 没有 Qoder 那一节")
        self.assertRegex(sec.group(1), r"(未验证|NOT_VERIFIED|没有验证|没验)",
                          "Qoder 那一节必须写清另一支（International）**没验过**，"
                          "光写命令形状不算交代边界")


class Test02_PyPI页面上看得见边界(unittest.TestCase):
    def test_readme_carries_the_four_machine_readable_lines(self):
        text = read("README.md")
        for key, want in REQUIRED_KEYS.items():
            m = re.search(r"^%s=(\S+)" % key, text, re.M)
            self.assertIsNotNone(m, "README 缺 %s 这一行（PyPI 页面正文就是 README，"
                                    "边界不在那儿写就等于没说）" % key)
            self.assertEqual(m.group(1), want,
                             "%s 这一档的取值不许漂：现在写的是 %s，应是 %s"
                             % (key, m.group(1), want))

    def test_cn_specific_dependencies_are_enumerated_not_described(self):
        """依赖清单要**逐条列出来**，不是一句“可能依赖 CN 特性”。

        一句含糊话挡不住任何人：真要换另一支，得能对着这张表逐条核。
        """
        text = read("README.md")
        m = re.search(r"^CN_SPECIFIC_DEPENDENCIES=(.+)$", text, re.M)
        self.assertIsNotNone(m, "缺 CN 专属依赖那一行")
        items = [s.strip() for s in re.split(r"[;；]", m.group(1)) if s.strip()]
        self.assertGreaterEqual(len(items), 3,
                                "依赖只列了 %d 条：至少要有 CLI 名、命令形状里的参数、"
                                "SDK 入口变量那一族三件才算成表" % len(items))

    def test_pypi_surface_never_claims_international_verified(self):
        for rel in PYPI_SURFACE + ["ADAPTERS.md"]:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                continue
            for line in read(rel).splitlines():
                # 大小写都算：`QODER_INTERNATIONAL_VERIFIED=YES` 那一行必须被抓到
                if "international" not in line.lower():
                    continue
                hit = []
                for word in FORBIDDEN_INTERNATIONAL_VERDICTS:
                    if hasattr(word, "search"):
                        if word.search(line):
                            hit.append(word.pattern)
                    elif word in line:
                        hit.append(word)
                self.assertEqual(hit, [],
                                  "%s 里这行把 International 说成已验证（命中 %s）：%s"
                                  % (rel, ",".join(hit), line.strip()[:120]))


class Test03_中性那句不许说过头(unittest.TestCase):
    def test_vendor_neutral_claim_is_scoped_to_the_core(self):
        """`CORE_PROTOCOL_VENDOR_NEUTRAL=YES` 只说**协议与传输核心**不含厂商假设。

        不许被读成“任何 Qoder 都能用”——真适配器是有具体目标的（见 CODEX/QODER 两档）。
        """
        text = read("README.md")
        m = re.search(r"^CORE_PROTOCOL_VENDOR_NEUTRAL=(\S+)", text, re.M)
        self.assertIsNotNone(m)
        nearby = text[max(0, m.start() - 1200):m.start()]
        self.assertRegex(nearby, r"(不含厂商|不认厂商|厂商中性|没有厂商)",
                          "这一行旁边必须写清“中性”指的是核心，不是整条产品都厂商无关")
        self.assertRegex(text, r"(真适配器|适配器).{0,80}(有具体目标|具体 CLI|认具体 CLI)",
                          "缺一句限制：真适配器仍然指向具体那一枚 CLI")


class Test04_发布记录里那一档也在(unittest.TestCase):
    def test_release_report_has_the_vendor_verdict_block(self):
        """v0.1.1 那份发布记录里必须有一节写厂商边界（报告不进包，但它是对外口径的原件）。"""
        reports = sorted(f for f in os.listdir(ROOT)
                         if re.match(r"^Q2C-v\d+\.\d+\.\d+-RELEASE-REPORT\.md$", f))
        self.assertTrue(reports, "仓根一份发布报告都没有")
        latest = reports[-1]
        text = read(latest)
        for key in ("QODER_CN_VERIFIED", "QODER_INTERNATIONAL_VERIFIED"):
            self.assertIn(key, text,
                          "%s 里没有 %s 这一档⇒读发布记录的人不知道该腿验到哪一层"
                          % (latest, key))


if __name__ == "__main__":
    unittest.main(verbosity=2)
