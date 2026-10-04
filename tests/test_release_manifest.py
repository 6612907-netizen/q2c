#!/usr/bin/env python3
"""发布物清单的收录范围：发布域与证据域必须是两张，且规则真被执行。

为什么有这一组（2026-10-04 自己撞上的）：`tools/make-manifest.py` 写着
`build(exclude_prefix=("evidence/",))`，循环里**从没读过这个参数**。
于是那次生成的"发布物清单"196 条里有 128 条是证据件，真正的发布物只有 68 条；
更糟的是证据里的读数文件在清单生成**之后**又被重生成一次，校验当场报 `diverged=1`——
这张清单既不是发布物清单，也不是当时那枚提交。

四条规矩钉住：
  1. `release` 那张**不含**任何 `evidence/` 路径；
  2. `evidence` 那张**只含** `evidence/` 路径；两张互不重叠、各自不含自己；
  3. 两张相加＝该提交跟踪的全部常规文件（不许"半张清单"冒充完整）；
  4. 文件头里的"收录规则"一行必须与代码实际行为一致（文档说排除，代码就得排除）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "tools", "make-manifest.py")


def _tool():
    spec = importlib.util.spec_from_file_location("q2c_make_manifest", SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tracked():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], stdout=subprocess.PIPE,
                         check=True).stdout
    return sorted(f.decode("utf-8") for f in out.split(b"\0") if f)


def _is_git_repo():
    """解包件里没有 `.git` ⇒ 这一组**如实 skip**，不拿"没测到"当通过。

    2026-10-04 独立 CI 上这一组在 `git archive` 解出来的目录里跑，`git ls-files` 直接失败，
    stderr 里冒出一句 "fatal: not a git repository"。清单工具本来就需要一枚提交才能干活，
    所以在没有提交的地方它测不了——说测不了，别折成红也别折成绿。
    """
    p = subprocess.run(["git", "-C", ROOT, "rev-parse", "--git-dir"], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL)
    return p.returncode == 0


@unittest.skipUnless(_is_git_repo(), "这里不是 git 仓（解包件）⇒ 清单工具无从取提交，测不到")
class TestManifestScopeRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _tool()
        cls.rel_head, cls.rel_rows = cls.m.build("release")
        cls.ev_head, cls.ev_rows = cls.m.build("evidence")

    @staticmethod
    def _paths(rows):
        return [r.split("  ", 1)[1] for r in rows]

    def test_01_release_scope_excludes_evidence(self):
        leaked = [p for p in self._paths(self.rel_rows) if p.startswith("evidence/")]
        self.assertEqual(leaked, [],
                         "发布域清单里混进 %d 条证据件（第一条例外就是读数文件自己会变）" % len(leaked))

    def test_02_evidence_scope_holds_only_non_release_items(self):
        wrong = [p for p in self._paths(self.ev_rows)
                 if not (p.startswith("evidence/") or self.m.is_release_report(p))]
        self.assertEqual(wrong, [], "证据域清单混进发布物：%s" % (wrong[:5],))

    def test_03_two_scopes_partition_the_tree(self):
        rel, ev = set(self._paths(self.rel_rows)), set(self._paths(self.ev_rows))
        self.assertEqual(rel & ev, set(), "两张清单不许有交集（有交集＝同一件被两处登记，改一处另一处不会红）")
        tracked = set(_tracked())
        # 每张都不含自己那一页（自引用会让清单永远无法描述含自己的提交）
        for own in (os.path.relpath(self.m.RELEASE_OUT, ROOT), os.path.relpath(self.m.EVIDENCE_OUT, ROOT)):
            tracked.discard(own)
        self.assertEqual(rel | ev, tracked,
                         "两张相加不等于跟踪面 ⇒ 有文件谁都不收录（半张清单冒充完整）")

    def test_04_unknown_scope_is_refused_not_defaulted(self):
        """fail-closed：认不出的 scope 必须拒＋零副作用，不许"就当 release 跑"。

        拒在哪一层不算（argparse 的 choices 或 `out_for` 的 SCOPE_UNKNOWN 都算拒），
        算数的是：非零退出 ＋ 已存在的清单文件一个字节没被改写。
        """
        path = self.m.RELEASE_OUT
        before = (os.path.getmtime(path), os.path.getsize(path)) if os.path.isfile(path) else None
        p = subprocess.run(["python3", SPEC, "--write", "--scope", "nonsense"],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=120)
        self.assertNotEqual(p.returncode, 0, "未知 scope 被吃掉了：%s" % p.stdout[-200:])
        out = (p.stdout or "").lower()
        self.assertTrue("scope" in out or "scope_unknown" in out,
                        "退出码非零但没说明是 scope 的问题：%s" % p.stdout[-200:])
        if before:
            after = (os.path.getmtime(path), os.path.getsize(path))
            self.assertEqual(after, before, "被拒的那一次仍然动了清单文件＝有副作用")

    def test_05_header_rule_line_matches_behaviour(self):
        """文件头写着"除 evidence/"，代码就必须真排除——防"文档一套代码一套"。"""
        path = self.m.RELEASE_OUT
        if not os.path.isfile(path):
            self.skipTest("发布域清单还没生成（这一格等生成后再核）")
        with open(path, encoding="utf-8") as fh:
            head = [next(fh) for _ in range(3)]
        rule = head[2]
        self.assertIn("收录规则", rule, "清单头部缺收录规则那一行：%s" % rule.strip()[:80])
        claims_excludes_evidence = ("evidence/" in rule and "除" in rule)
        leaked = [p for p in self._paths(self.rel_rows) if p.startswith("evidence/")]
        self.assertEqual(claims_excludes_evidence, not leaked,
                         "头部规则与实测行为不一致：规则=%s 实漏=%d 条" % (rule.strip()[:60], len(leaked)))

    def test_06_shipped_package_equals_release_manifest(self):
        """发布包的件集＝发布域清单的条目，一件不多一件不少。

        以前 `git archive` 把 `evidence/` 一起打包，于是"包里的东西"比清单描述的还多，
        而清单又说自己不描述那些件——两处口径打架。现在打包范围与清单收录规则同源：
        都排除 `evidence/`。这一格按真 `git archive` 现算比对，不靠谁去读注释。
        """
        p = subprocess.run(["git", "-C", ROOT, "archive", "--format=tar", "--prefix=q2c/",
                            "HEAD", "--", ".", ":(exclude)evidence",
                            ":(exclude,glob)Q2C-v*-RELEASE-REPORT.md"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        self.assertEqual(p.returncode, 0, "git archive 失败：%s" % p.stderr.decode()[:200])
        listing = subprocess.run(["tar", "-t"], input=p.stdout, stdout=subprocess.PIPE, check=True)
        names = [l for l in listing.stdout.decode().splitlines() if l and not l.endswith("/")]
        names = sorted(n.split("/", 1)[1] for n in names)      # 去掉 --prefix=q2c/
        listed = sorted(self._paths(self.rel_rows))
        self.assertEqual(names, listed,
                         "包与清单不一致：包多=%s 清单多=%s" % (
                             sorted(set(names) - set(listed))[:5],
                             sorted(set(listed) - set(names))[:5]))

    def test_packaging_exclusions_are_not_pinned_to_one_report_name(self):
        """三处"排除发布报告"的写法都得按形状，不许钉死某一枚文件名。

        2026-10-05 现读抓到的形状：判据 test_06 比对"包件集＝清单条目"时，归档命令里排除的还是
        上一版那一枚报告的文件名。升到 v0.1.1 后新报告被清单的排除规则认出（所以不在清单里），
        却没被归档命令排除 ⇒ 包比清单多一件，test_06 当场红。
        这一格把"排除＝形状"钉成静态判据，免得下一次又在提交之后才发现。

        写法规避一格老坑：**断言不许扫到自己那一行**——要搜的字面量在这里是拼出来的，
        不然这一格会因为自己的源码里含那个串而永远红（09-28 同族）。
        """
        pinned = ":(exclude)" + "Q2C-v0.1.0-RELEASE-REPORT.md"
        glob_form = ":(exclude,glob)" + "Q2C-v*-RELEASE-REPORT.md"
        for rel in ("tools/ci-clean-machine.sh", "tools/make-manifest.py",
                    "tools/pkg-install-test.sh", os.path.abspath(__file__)):
            body = open(rel, encoding="utf-8").read()
            self.assertNotIn(pinned, body,
                             "%s 里还把发布报告的排除写死成一枚文件名" % os.path.basename(rel))
        ci = open(os.path.join(ROOT, "tools", "ci-clean-machine.sh"), encoding="utf-8").read()
        self.assertIn(glob_form, ci,
                      "归档命令的排除没写成形状⇒下一版报告会跟着包一起发出去")
        # 清单工具那一侧不在这里读源码文本：它的形状识别由 TestVersionAwareNames 直接调
        # is_release_report() 现证（读源码正则＝两处口径，容易各自漂）
        pk = open(os.path.join(ROOT, "tools", "pkg-install-test.sh"), encoding="utf-8").read()
        self.assertIn('"-RELEASE-REPORT.md"', pk,
                      "打包门按整枚文件名排除报告，而不是按后缀")

    def test_07_verify_targets_the_recorded_commit_not_the_tip(self):
        """校验对象＝清单头记的那一枚；之后往 `evidence/` 加东西**不该**让清单变红。

        这是把"发布物身份"与"分支尖"分开的机器版：
          · 加一枚**发布域**文件 ⇒ 必须报 DRIFTED 并非零（清单不再描述当前提交）；
          · 只加 `evidence/` 下的东西（CI 回来的原件、最后那段读数）⇒ 仍 MANIFEST_OK。
        否则每存一份新证据都得重打一张清单，而重打的那张又让下次校验变 STALE——死循环。
        """
        import shutil
        import tempfile
        td = tempfile.mkdtemp(prefix="q2c-mm-")
        try:
            def git(*args):
                r = subprocess.run(["git"] + list(args), cwd=td, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, " ".join(args) + " → " + r.stdout[-300:])
                return r.stdout
            os.makedirs(os.path.join(td, "tools"))
            os.makedirs(os.path.join(td, "evidence"))
            os.makedirs(os.path.join(td, "q2c"))
            shutil.copy2(SPEC, os.path.join(td, "tools", "make-manifest.py"))
            with open(os.path.join(td, "hello.md"), "w", encoding="utf-8") as fh:
                fh.write("# hi\n")
            # 版本唯一真源：清单文件名必须跟着它（写死 v0.1.0 的旧形状在这里会直接暴露）
            with open(os.path.join(td, "q2c", "_version.py"), "w", encoding="utf-8") as fh:
                fh.write('__version__ = "9.9.9"\n')
            git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
            git("add", "."); git("commit", "-qm", "A")
            head_a = git("rev-parse", "HEAD").strip()      # 清单就以这一枚为身份
            out = subprocess.run(["python3", "tools/make-manifest.py", "--write"], cwd=td,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 timeout=120)
            self.assertEqual(out.returncode, 0, out.stdout[-300:])
            self.assertTrue(os.path.isfile(os.path.join(td, "evidence", "SHA256SUMS-v9.9.9.txt")),
                            "临时仓里版本是 9.9.9，清单却没跟着起名⇒清单文件名仍由人写死："
                            "%s" % sorted(os.listdir(os.path.join(td, "evidence"))))
            # 情况一：只加证据件 ⇒ 清单照旧成立
            with open(os.path.join(td, "evidence", "note.txt"), "w", encoding="utf-8") as fh:
                fh.write("新证据\n")
            git("add", "."); git("commit", "-qm", "B-evidence-only")
            v = subprocess.run(["python3", "tools/make-manifest.py"], cwd=td,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               timeout=120)
            self.assertIn("MANIFEST_OK", v.stdout, "加证据件不该让清单变红：%s" % v.stdout[-400:])
            self.assertIn("drift=0", v.stdout)
            self.assertNotEqual(head_a, git("rev-parse", "HEAD").strip(),
                                "测试自己得真的移动了提交，否则这格是空的")
            # 情况二：动了发布域文件 ⇒ 必须报漂移，不许蒙过去
            with open(os.path.join(td, "hello.md"), "w", encoding="utf-8") as fh:
                fh.write("# hi 改了\n")
            git("add", "."); git("commit", "-qm", "C-release-changed")
            v2 = subprocess.run(["python3", "tools/make-manifest.py"], cwd=td,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                timeout=120)
            self.assertNotEqual(v2.returncode, 0, "发布域漂移被读成通过：%s" % v2.stdout[-400:])
            self.assertIn("DRIFTED", v2.stdout)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_08_report_is_excluded_from_package_by_rule(self):
        """发布报告**不在包里**，这条得写成一格而不是靠默契。

        为什么：报告的最后一行是发布结论，而结论要引用"独立复验通过"这件事。
        若报告算发布物，那"写下结论"这一步就会改变发布物字节 ⇒ 结论永远无法自证，
        要么先写（预言）要么后写（包与身份不符）。
        分工就这样定：包里是代码／文档／工装／判据；报告与证据随仓与交付副本发布。
        这条规则的代价也写明白：包里看不到本次结论，读者得去仓里或交付副本读。
        """
        rel = set(self._paths(self.rel_rows))
        ev = set(self._paths(self.ev_rows))
        reports = [n for n in rel | ev if self.m.is_release_report(n)]
        self.assertTrue(reports, "两张清单里一份报告都没认出⇒识别规则失效，"
                                 "这一格会永远绿")
        leaked = [n for n in reports if n in rel]
        self.assertEqual(leaked, [], "报告混进发布域⇒结论段会自己改动发布物：%s" % leaked)
        missing = [n for n in reports if n not in ev]
        self.assertEqual(missing, [], "报告不在任何一张清单里⇒它改了什么没人钉得住：%s" % missing)


@unittest.skipUnless(_is_git_repo(), "这里不是 git 仓（解包件）⇒ 清单工具无从取提交，测不到")
class TestVersionAwareNames(unittest.TestCase):
    """版本号一升，工装的口径得跟着升，而且**不许靠人记**（主理人 2026-10-04 选 (a) 那条的后果）。

    现在的形状（2026-10-04 现读）：`make-manifest.py` 里 `RELEASE_OUT`／`EVIDENCE_OUT`／
    `REPORT_FILE` 三处把 `v0.1.0` 写死。升到 v0.1.1 时如果没人想起来改，会发生两件具体的事：
      1. 新一轮的清单**续写进上一版那张文件**里 ⇒ 两份发布物身份混在同一页，
         `SHA256SUMS-v0.1.0.txt` 的头部却记着 v0.1.1 的提交——历史被就地改写；
      2. 新的发布报告 `Q2C-v0.1.1-RELEASE-REPORT.md` 不被排除规则认出 ⇒ 它**进了发布域**，
         于是"结论那一行会改动发布物字节"这条老毛病原样复发（报告不进包是定过的规矩）。
    所以：文件名跟着 `q2c.__version__` 走，报告识别改成形状匹配。这两格先红后绿。
    """

    @classmethod
    def setUpClass(cls):
        cls.m = _tool()

    def test_manifest_paths_follow_the_package_version(self):
        from q2c import __version__ as ver
        rel = self.m.out_for("release")
        ev = self.m.out_for("evidence")
        self.assertTrue(rel.endswith("SHA256SUMS-v%s.txt" % ver),
                        "发布域清单文件名没跟着版本走（现在=%s）。升一次版就会把新一轮的清单"
                        "续写进旧那一页⇒两份身份混在一页，且旧那页的头部被改写" % os.path.basename(rel))
        self.assertTrue(ev.endswith("SHA256SUMS-evidence-v%s.txt" % ver),
                        "证据域清单同上（现在=%s）" % os.path.basename(ev))

    def test_report_rule_is_a_shape_not_one_filename(self):
        self.assertTrue(hasattr(self.m, "is_release_report"),
                        "清单工具没有 is_release_report()——报告排除还写死成一枚文件名，"
                        "下一版报告会直接进发布域")
        for name in ("Q2C-v0.1.0-RELEASE-REPORT.md", "Q2C-v0.1.1-RELEASE-REPORT.md",
                     "Q2C-v0.2.0-RELEASE-REPORT.md"):
            self.assertTrue(self.m.is_release_report(name), "%s 该被认出是发布报告" % name)
        for name in ("README.md", "DELIVERY-v0.1.1.md", "Q2C-PRODUCT-SPEC-v0.1.md",
                     "evidence/notes.md", "REPORT.md"):
            self.assertFalse(self.m.is_release_report(name), "%s 不是发布报告" % name)

    def test_release_scope_holds_no_report_of_any_version(self):
        head, rows = self.m.build("release")
        names = [r.split("  ", 1)[1] for r in rows]
        leaked = [n for n in names if self.m.is_release_report(n)]
        self.assertEqual(leaked, [],
                         "发布域混进发布报告：%s ⇒ 写结论那一步会改动发布物字节" % leaked)

    def test_evidence_scope_holds_every_report(self):
        head, rows = self.m.build("evidence")
        names = [r.split("  ", 1)[1] for r in rows]
        reports = [n for n in names if self.m.is_release_report(n)]
        self.assertGreaterEqual(len(reports), 1,
                               "证据域里一份报告都没有⇒报告到底归谁管没人说")
        for name in reports:
            self.assertTrue(self.m.in_evidence_scope(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
