#!/usr/bin/env python3
"""包分发判据：让一个没参与过 Q2C 的人能 `pip install q2c` 然后跑起来。

为什么要这一组（全是 2026-10-04 现读到的事实，不是我推测的）：
`pyproject.toml` 早就在仓里，本机现跑 `pip wheel`／`pip install` 也确实能过 ——
所以缺的不是"能不能构建"，而是**四件会伤到陌生人的事没人钉**：

  1. 版本号在 `pyproject.toml:7` 与 `q2c/__init__.py:25` 各写一遍。
     一改一忘 ⇒ PyPI 上写的版本和代码里跑的版本不是同一个（在册老坑：双真源）。
  2. `[project.urls]` 里写着 `Protocol = "PROTOCOL.md"` —— 那不是 URL，
     包页面上那行是坏链，读者按它找不到协议。
  3. 现造的 sdist 里带着 `tests/` 的 22 枚判据件，却**没带 `tests/__init__.py`**，
     也没带 `bin/`／`tools/`／`examples/`／文档。结果：别人解开源码包后
     `python3 -m unittest discover -s tests -t .` 直接 ImportError ——
     我们等于发了一套"声称可复跑其实跑不了"的判据。
  4. README 的 Quick Start 只有 `git clone` 那条免安装路；装完包的人没有可抄的命令。
     而且 README 里还留着"`pip install .` 这一路在本机未验证"这句**已经作废**的话。

外加一条门的门（任务书第 6 条）：CI 里必须有一道**打包门**，
让"源码判据全绿但发布物装不上／CLI 入口是坏的"这类事不能再只在发布那天才发现。

判据怎么落地（口径写死，别读成"我测了但其实没测"）：
  · 静态那几格读的是**声明**（pyproject／MANIFEST.in／README／workflow／脚本），
    任何解释器都能跑，3.9 上也不 skip；
  · 现造 wheel／sdist 那两格读的是**真件**：造件工具取不到时如实 skip 并写明缺什么，
    绝不折成绿；真正的"装进干净环境再跑"由 `tools/pkg-install-test.sh` 实跑（CI 那道门叫的就是它）。
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PYPROJECT = os.path.join(ROOT, "pyproject.toml")
MANIFEST_IN = os.path.join(ROOT, "MANIFEST.in")
README = os.path.join(ROOT, "README.md")
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")
PKG_SCRIPT = os.path.join(ROOT, "tools", "pkg-install-test.sh")
NAME_TOOL = os.path.join(ROOT, "tools", "pypi-name-check.py")

#: 装完包后必须能跑的运行时模块（wheel 里一枚不许少）。
RUNTIME_PKG = "q2c"
#: 判据件在解包树上跑起来要读的非代码件（缺一件就复跑不了，第 3 条病灶）。
SUITE_SUPPORT_FILES = ["tests/__init__.py", "tests/helpers.py", "bin/q2c",
                       "examples/quickstart.sh", "PROTOCOL.md", "README.md",
                       "pyproject.toml", "LICENSE", "tools/report-readings.py",
                       "tools/count-audit.py"]
#: 包里的证据与发布报告一律不许出现（发布域规则：报告与证据随仓走，不随包走）。
PACKAGE_FORBIDDEN_PREFIXES = ["evidence/"]
REPORT_NAME_RE = re.compile(r"^Q2C-v\d+\.\d+\.\d+-RELEASE-REPORT\.md$")


def is_report_name(path):
    """发布报告按**形状**认，不按某一枚文件名认。

    写死一枚＝下一版的报告会被当发布物收进包里，「写下结论就改动发布物字节」那条老毛病复发。
    """
    return bool(REPORT_NAME_RE.match(path))


# --------------------------------------------------------------------------
# 自有 pyproject 的读数器（不引第三方 TOML 件：判据在 3.9 上也要跑）
# --------------------------------------------------------------------------

def read_pyproject():
    """把仓里这一份 pyproject 读成 {段: {键: 原样值串}}。

    只支持本仓这一种写法（一行一键）。读不到就返回空字典 —— 所以有 `test_00`
    专门校"读数器自己读得到已知键"，否则下面几格会因为空字典而假绿。
    """
    if not os.path.isfile(PYPROJECT):
        return {}
    sections, current = {}, None
    with open(PYPROJECT, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            head = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
            if head:
                current = head.group(1).strip()
                sections.setdefault(current, {})
                continue
            kv = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*(.*?)\s*$", line)
            if kv and current is not None:
                sections[current][kv.group(1)] = kv.group(2)
    return sections


def string_list(value):
    """`["a", "b"]` → ['a','b']；单串 `"x"` → ['x']。读不出就返回 None（宁可判红）。"""
    if value is None:
        return None
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        found = re.findall(r'"([^"]*)"|\'([^\']*)\'', inner)
        return [a or b for a, b in found]
    one = re.match(r'^"([^"]*)"$', inner) or re.match(r"^'([^']*)'$", inner)
    return [one.group(1)] if one else None


def inline_attr(value, key):
    """从 `{attr = "q2c._version.__version__"}` 这类内联表里取一枚键。"""
    if value is None:
        return None
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(key), value)
    return m.group(1) if m else None


def literal_in_module(dotted):
    """AST 读 `包.模块.名字` 里那行**字面量**（不 import，避免解释器差异）。"""
    module, _, name = dotted.rpartition(".")
    if not module or not name:
        return None
    base = os.path.join(ROOT, *module.split("."))
    path = base + ".py"
    if not os.path.isfile(path):
        path = os.path.join(base, "__init__.py")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node.value
    return None


# --------------------------------------------------------------------------
# MANIFEST.in 声明的收录集（静态算，不依赖能否真的构建）
# --------------------------------------------------------------------------

def release_files():
    """发布域该收的文件＝**跟踪的 ∪ 盘上的**，除 `evidence/` 与发布报告。

    为什么取并集（2026-10-04 自己撞的）：只按 `git ls-files` 算，那么"刚写好还没提交"的
    `MANIFEST.in`、`q2c/_version.py` 就不在构建目录里，构建当场炸（动态版本号读不到那一行）。
    本模块校的是**工作树**，冻结那一步另有 `tools/make-manifest.py` 与 `git status` 门
    （`tools/pkg-install-test.sh` 里的 `REPO_CLEAN` 也钉这件事）。
    """
    out = set()
    try:
        p = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        p = None
    if p is not None and p.returncode == 0:
        out |= {f.decode("utf-8") for f in p.stdout.split(b"\0") if f}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "dist", "build", ".q2c")
                   and not d.endswith(".egg-info")
                   and (not d.startswith(".") or d == ".github")]
        for name in files:
            if name.endswith(".egg-info"):
                continue
            rel = os.path.relpath(os.path.join(base, name), ROOT).replace(os.sep, "/")
            out.add(rel)
    return sorted(f for f in out
                  if not any(f.startswith(p_) for p_ in PACKAGE_FORBIDDEN_PREFIXES)
                  and not is_report_name(f))


def tracked_release_files():
    """兼容旧名：判据里把它当"发布域该有哪些件"读，取的是 `release_files()`。"""
    return release_files()


def sdist_declared_includes(files):
    """按 MANIFEST.in 的声明算出"sdist 会收到哪些仓内文件"。

    只实现本仓用到的指令：`include`／`exclude`／`recursive-include`／`global-exclude`。
    MANIFEST.in 不存在 ⇒ 返回 set()（于是第 06 格红，正好是我们要的症状）。
    """
    if not os.path.isfile(MANIFEST_IN):
        return set()
    picked, banned = set(), set()
    with open(MANIFEST_IN, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    for ln in lines:
        parts = ln.split()
        directive, args = parts[0].lower(), parts[1:]
        if not args:
            continue
        if directive == "include":
            picked |= {f for f in files if any(fnmatch.fnmatch(f, g) for g in args)}
        elif directive == "exclude":
            banned |= {f for f in files if any(fnmatch.fnmatch(f, g) for g in args)}
        elif directive == "recursive-include":
            base, globs = args[0], args[1:] or ["*"]
            picked |= {f for f in files
                       if (f == base or f.startswith(base.rstrip("/") + "/"))
                       and any(fnmatch.fnmatch(os.path.basename(f), g) or
                               fnmatch.fnmatch(f, g) for g in globs)}
        elif directive == "graft":
            picked |= {f for f in files if f.startswith(args[0].rstrip("/") + "/")}
        elif directive == "global-exclude":
            banned |= {f for f in files if any(fnmatch.fnmatch(os.path.basename(f), g)
                                               for g in args)}
    return picked - banned


# --------------------------------------------------------------------------
# 真件：现造一次 wheel＋sdist（造不出如实 skip；造出来但不合格＝红）
# --------------------------------------------------------------------------

_BUILT = {}


def _has_build(python):
    try:
        p = subprocess.run([python, "-c", "import build"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return False
    return p.returncode == 0


def build_python():
    for cand in (os.environ.get("Q2C_BUILD_PYTHON", ""), sys.executable,
                 shutil.which("python3") or ""):
        if cand and os.path.exists(cand) and _has_build(cand):
            return cand
    return None


def _do_build():
    """在临时副本上现造一次 wheel＋sdist（不在仓里落 dist/、build/、*.egg-info/）。"""
    python = build_python()
    if python is None:
        raise unittest.SkipTest(
            "本解释器取不到构建件（没有 `python -m build`，也没设 Q2C_BUILD_PYTHON）⇒ "
            "这两格测不了，如实 skip；真正装进干净环境由 tools/pkg-install-test.sh 实跑")
    work = tempfile.mkdtemp(prefix="q2c-pkgbuild-")
    src = os.path.join(work, "src")
    os.makedirs(src)
    for rel in release_files():
        dst = os.path.join(src, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, rel), dst)
    dist = os.path.join(work, "dist")
    # 与 tools/pkg-install-test.sh 同一口径：造件时间戳取自提交时刻。
    # 不钉这一枚时，wheel 里 `*.dist-info/*` 那几条是**构建期现生成**的文件，
    # 它们的 mtime 进 zip 目录项 ⇒ 内容一模一样、字节却两次不同（本格就是这么发现的）。
    env = dict(os.environ)
    try:
        head = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%ct"],
                              stdout=subprocess.PIPE, timeout=60)
        if head.returncode == 0 and head.stdout.strip().isdigit():
            env["SOURCE_DATE_EPOCH"] = head.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        pass
    p = subprocess.run([python, "-m", "build", "--outdir", dist, src],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900, env=env)
    if p.returncode != 0:
        raise AssertionError("构建失败（不是 skip，是真红）：%s"
                             % p.stdout.decode("utf-8", "replace")[-1200:])
    wheel = sdist = None
    for name in sorted(os.listdir(dist)):
        if name.endswith(".whl"):
            wheel = os.path.join(dist, name)
        elif name.endswith(".tar.gz"):
            sdist = os.path.join(dist, name)
    return {"wheel": wheel, "sdist": sdist, "dist": dist, "work": work,
            "log": p.stdout.decode("utf-8", "replace")}


def build_once():
    """整组只造一次（真造，慢），结果给同组几格共用。"""
    if "res" not in _BUILT:
        _BUILT["res"] = _do_build()
    return _BUILT["res"]


def build_again():
    """再造一次（复现性那一格专用：同一份源码两次造，身份要能对上）。"""
    return _do_build()


def _sdist_file_hashes(path):
    """sdist 里每个文件的 sha256（按解包后的相对路径归键，不带 tar 的 mtime）。"""
    import hashlib
    out = {}
    with tarfile.open(path) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            handle = tf.extractfile(member)
            out[rel] = hashlib.sha256(handle.read()).hexdigest()
    return out


def wheel_names(wheel):
    with zipfile.ZipFile(wheel) as zf:
        return sorted(i.filename for i in zf.infolist())


def sdist_names(sdist):
    with tarfile.open(sdist) as tf:
        return sorted(m.name.split("/", 1)[1] if "/" in m.name else m.name
                      for m in tf.getmembers() if m.isfile())


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load_name_tool():
    """把核查件当模块载进来（它的联网那步在 `main` 里，测试一律把 probe 换掉）。"""
    import importlib.util
    if not os.path.isfile(NAME_TOOL):
        raise AssertionError("tools/pypi-name-check.py 不存在⇒名字这一格没人核查")
    spec = importlib.util.spec_from_file_location("q2c_pypi_name", NAME_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ==========================================================================


class Test00_读数器自证(unittest.TestCase):
    """读数器如果读成空字典，下面那一串"没有版本号字面量"就全成假绿——先自证。"""

    def test_parser_sees_known_keys(self):
        sec = read_pyproject()
        self.assertEqual(sec.get("project", {}).get("name"), '"q2c"',
                         "pyproject 读数器连 name 都读不到⇒后面的格子全是假绿")
        self.assertIn("build-backend", sec.get("build-system", {}))
        self.assertEqual(sec.get("project.scripts", {}).get("q2c"), '"q2c.cli:main"')
        self.assertTrue(sec.get("project.urls"), "读不到 [project.urls] 段")


class Test01_版本一个真源(unittest.TestCase):
    def test_pyproject_does_not_write_its_own_version(self):
        sec = read_pyproject()
        self.assertNotIn("version", sec.get("project", {}),
                         "pyproject 里自己写了一行 version ⇒ 与 q2c.__version__ 成双真源，"
                         "改一处忘一处，包上标的和代码里跑的不是同一个版本")
        dyn = string_list(sec.get("project", {}).get("dynamic"))
        self.assertIsNotNone(dyn, "没声明 dynamic ⇒ 版本号无处可取")
        self.assertIn("version", dyn, "dynamic 里没有 version")

    def test_dynamic_version_resolves_to_the_one_literal(self):
        from q2c import __version__ as code_version
        sec = read_pyproject()
        raw = sec.get("tool.setuptools.dynamic", {}).get("version")
        dotted = inline_attr(raw, "attr")
        self.assertIsNotNone(dotted, "没写 [tool.setuptools.dynamic] version={attr=…}")
        node = literal_in_module(dotted)
        self.assertIsNotNone(node, "%s 读不出一行字面量（构建期会退化成 import，"
                                   "解释器差异会当场炸构建）" % dotted)
        self.assertIsInstance(node, ast.Constant, "%s 那行不是字面量" % dotted)
        self.assertEqual(node.value, code_version,
                          "包版本与代码里的 __version__ 不是同一个值")


class Test02_发货形状(unittest.TestCase):
    def test_declared_packages_equal_packages_on_disk(self):
        sec = read_pyproject()
        declared = string_list(sec.get("tool.setuptools", {}).get("packages"))
        self.assertIsNotNone(declared, "没声明 packages")
        on_disk = set()
        for base, dirs, files in os.walk(os.path.join(ROOT, RUNTIME_PKG)):
            if "__pycache__" in base:
                continue
            if "__init__.py" in files:
                on_disk.add(os.path.relpath(base, ROOT).replace(os.sep, "."))
        self.assertEqual(sorted(on_disk), sorted(declared),
                         "盘上的包与声明的包不一致：多出来的那些**不会进 wheel**，"
                         "装上包的人 import 就会缺件")

    def test_console_script_target_is_callable_and_returns_int(self):
        from q2c import cli
        self.assertTrue(callable(cli.main))
        self.assertIsInstance(cli.main(["version"]), int,
                              "控制台脚本入口不返回整数⇒setuptools 生成的包装脚本行为不定")

    def test_every_project_url_is_a_real_url(self):
        sec = read_pyproject()
        bad = {k: v for k, v in sec.get("project.urls", {}).items()
               if not re.match(r'^"https?://', str(v))}
        self.assertEqual(bad, {},
                          "包页面上这些「链接」是坏链（读者按它找不到任何东西）：%s"
                          % ", ".join(sorted(bad)))

    def test_spdx_license_declared_and_floor_backs_it(self):
        """许可声明要**有人背书**：SPDX 串要求 setuptools>=77，下限写低了构建就炸。

        现读到的告警里写着 2027-02-18 之后旧表写法不再支持 ——
        那一天不是遥远的事，是"某个人的 `pip install q2c` 突然装不上"的那一天。
        """
        sec = read_pyproject()
        lic = sec.get("project", {}).get("license")
        self.assertIsNotNone(lic, "没声明 license⇒包页面许可那一栏是空的")
        self.assertRegex(lic.strip(), r'^"MIT"$',
                         "还是旧表写法（%s）：构建期告警已点名 2027-02-18 起不再支持" % lic)
        reqs = string_list(sec.get("build-system", {}).get("requires")) or []
        floor = [r for r in reqs if r.startswith("setuptools>=")]
        self.assertTrue(floor, "没声明 setuptools 下限，光换写法等于赌构建机版本够新")
        self.assertGreaterEqual(int(floor[0].split(">=")[1]), 77,
                                "SPDX 串要 setuptools>=77，声明的下限比它低⇒按下限装的人会构建失败")


class Test03_sdist_收录规则(unittest.TestCase):
    def test_suite_support_files_are_shipped(self):
        files = tracked_release_files()
        picked = sdist_declared_includes(files)
        missing = [f for f in SUITE_SUPPORT_FILES if f not in picked]
        self.assertEqual(missing, [],
                         "源码包里少了这些件⇒别人解包后跑不了我们让他跑的判据：%s"
                         % ", ".join(missing))

    def test_evidence_and_release_report_stay_out(self):
        picked = sdist_declared_includes(tracked_release_files())
        leaked = [f for f in picked
                  if any(f.startswith(p) for p in PACKAGE_FORBIDDEN_PREFIXES)
                  or is_report_name(f)]
        self.assertEqual(leaked, [], "证据／发布报告混进包里：%s" % ", ".join(leaked))

    def test_no_caches_in_the_package(self):
        picked = sdist_declared_includes(tracked_release_files())
        junk = [f for f in picked if "__pycache__" in f or f.endswith((".pyc", ".egg-info"))]
        self.assertEqual(junk, [], "包里带了运行期垃圾：%s" % ", ".join(junk[:5]))

    def test_every_root_doc_is_declared_for_the_sdist(self):
        """仓根每一枚**非报告**的 md 都得在收录名单里——新增文档不许默默不进包。

        为什么按目录现读而不是再写一份名单：写名单这件事本身就是漏件的源头
        （`MANIFEST.in` 里那行 `DELIVERY-v0.1.0.md` 就是这么只声明了上一版那一枚，
        v0.1.1 的交付页一旦忘了加，别人解包后就少一页说明书，而且没有任何东西会红）。
        报告仍然故意**不声明**：它不进包（见 test_evidence_and_release_report_stay_out）。
        """
        files = release_files()
        picked = sdist_declared_includes(files)
        root_docs = [f for f in files
                     if "/" not in f and f.endswith(".md") and not is_report_name(f)]
        self.assertTrue(root_docs, "仓根一枚文档都没读到⇒扫描器接错了地方")
        missing = [f for f in root_docs if f not in picked]
        self.assertEqual(missing, [],
                         "这些根级文档没被 MANIFEST.in 声明，装包的人拿不到：%s"
                         % ", ".join(missing))
        reports = [f for f in picked if is_report_name(f)]
        self.assertEqual(reports, [], "发布报告被声明进包里了：%s" % reports)


@unittest.skipUnless(build_python(), "取不到构建件（见模块说明）")
class Test04_现造出来的件(unittest.TestCase):
    def test_wheel_carries_every_runtime_module(self):
        built = build_once()
        names = wheel_names(built["wheel"])
        on_disk = []
        for base, _dirs, files in os.walk(os.path.join(ROOT, RUNTIME_PKG)):
            if "__pycache__" in base:
                continue
            for name in files:
                if name.endswith(".py"):
                    on_disk.append(os.path.relpath(os.path.join(base, name), ROOT)
                                   .replace(os.sep, "/"))
        absent = sorted(set(on_disk) - set(names))
        self.assertEqual(absent, [], "wheel 里缺这些运行时模块：%s" % ", ".join(absent))
        self.assertTrue(any(n.endswith("entry_points.txt") for n in names),
                        "wheel 没有 entry_points.txt⇒`q2c` 命令装不出来")

    def test_sdist_can_run_the_suite_static_shape(self):
        built = build_once()
        names = sdist_names(built["sdist"])
        missing = [f for f in SUITE_SUPPORT_FILES if f not in names]
        self.assertEqual(missing, [], "解出来的 sdist 跑不了判据，缺：%s" % ", ".join(missing))

    def test_sdist_keeps_evidence_and_report_out(self):
        """现造出来的 sdist 里不许有证据域与发布报告（发布域规则在包上也要成立）。"""
        names = sdist_names(build_once()["sdist"])
        leaked = [n for n in names
                  if any(n.startswith(p) for p in PACKAGE_FORBIDDEN_PREFIXES)
                  or is_report_name(n)]
        self.assertEqual(leaked, [], "包里混进证据／报告：%s" % ", ".join(leaked[:5]))

    def test_second_build_of_same_sources_matches(self):
        """同一份源码连造两次：wheel 逐字节相同；sdist 解包后逐文件相同。

        现读到的 sdist 差异只有三处来源：tar 里顶层目录条目与 `q2c.egg-info/` 各条目的
        mtime（＝造件那一刻），以及 gzip 头里那 4 字节时间戳。
        这一格校的是**内容的身份**，不假装 `.tar.gz` 的字节能对上——
        要把那三处也钉平，就得自己重排 tar 与 gzip 头，那发出去的就不是
        `python -m build` 的原样产物了；这条取舍写在这里，不靠默契。
        """
        import hashlib
        first = build_once()
        second = build_again()
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
        self.assertEqual(h(first["wheel"]), h(second["wheel"]),
                         "wheel 字节不可复现⇒发布物身份不能按哈希对（这条必须真相同）")
        a = _sdist_file_hashes(first["sdist"])
        b = _sdist_file_hashes(second["sdist"])
        self.assertEqual(sorted(a), sorted(b), "两次 sdist 的**文件清单**不一样")
        same = [n for n in a if a[n] == b[n]]
        self.assertEqual(len(same), len(a),
                         "sdist 解包后有文件内容变了：%s"
                         % ", ".join(sorted(n for n in a if a[n] != b[n])[:5]))

    def test_build_output_carries_no_deprecation_block(self):
        """构建期告警现在只印一行 `!!`，下个 setuptools 就是拒绝。

        抓到过的源头：`license = {text = "MIT"}` 配 `License ::` classifier，
        setuptools 77 起判 PEP 639 迁移告警。宁可现在红，也别在别人装不上的那天红。
        """
        log = build_once()["log"]
        hits = [ln.strip() for ln in log.splitlines()
                if "consider removing the following classifiers" in ln
                or "!!" == ln.strip()]
        self.assertEqual(hits, [], "构建输出里有弃用告警块：%s" % ", ".join(hits[:3]))


class Test05_装完就能抄的_quick_start(unittest.TestCase):
    """任务书第 4 条：陌生人不看内部文档也能跑通第一次。"""

    def _install_block(self):
        text = read(README)
        m = re.search(r"^##\s*装上就跑[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        self.assertIsNotNone(m, "README 没有「装上就跑」一节⇒装完包的人不知道下一条命令敲什么")
        block = m.group(0)
        fences = re.findall(r"```(?:sh|bash)\n(.*?)```", block, re.S)
        self.assertTrue(fences, "「装上就跑」一节里没有命令块")
        return block, "\n".join(fences)

    def test_install_commands_present(self):
        _block, cmds = self._install_block()
        self.assertRegex(cmds, r"pipx install q2c", "没有 pipx 那条")
        self.assertRegex(cmds, r"pip install .*q2c|python3? -m pip install", "没有 pip 那条")
        self.assertRegex(cmds, r"(?m)^\s*q2c --help", "没有装完后的第一条自检命令")

    def test_verbs_used_in_quickstart_exist_in_cli(self):
        import argparse
        from q2c import cli
        _block, cmds = self._install_block()
        subs = [a for a in cli.build_parser()._actions
                if isinstance(a, argparse._SubParsersAction)][0]
        known = set(subs.choices)
        used = set(re.findall(r"(?:^|[;&|(]\s*)q2c\s+([a-z][a-z-]+)", cmds, re.M))
        unknown = sorted(used - known)
        self.assertEqual(unknown, [],
                         "Quick Start 写了 CLI 上不存在的动词（新用户照抄就失败）：%s"
                         % ", ".join(unknown))

    def test_no_stale_unverified_claim_left_in_readme(self):
        text = read(README)
        stale = "这一路在本机未验证"
        hits = [ln.strip() for ln in text.splitlines() if stale in ln]
        self.assertEqual(hits, [],
                          "已知局限里还留着过期的未验证声明（现在这条路已经真跑过了）：%s"
                          % " / ".join(hits)[:200])


class Test06_打包门(unittest.TestCase):
    """任务书第 6 条：源码绿但包装不上／入口坏，不能再等发布那天才发现。"""

    def test_ci_has_a_packaging_job_calling_the_script(self):
        """CI 里必须有一道 job **真的执行**那条命令，而不是在注释里提到那个文件名。

        这格第一版写的是"workflow 文本里出现 pkg-install-test.sh 就算有门"，
        验牙当场把它打成 NO-TEETH：包体注释里也写着那个名字，把 job 里的执行行删掉
        照样绿。判据要钉的是**执行行**，不是文件名。
        """
        self.assertTrue(os.path.isdir(WORKFLOW_DIR), "没有 .github/workflows")
        call = re.compile(r"(?m)^\s*sh tools/pkg-install-test\.sh\s*$")
        found = []
        for name in sorted(os.listdir(WORKFLOW_DIR)):
            if name.endswith((".yml", ".yaml")) and call.search(read(os.path.join(WORKFLOW_DIR, name))):
                found.append(name)
        self.assertTrue(found, "CI 里没有一行真的执行 `sh tools/pkg-install-test.sh`："
                                "这道门只在发布那天由人肉跑，就会漏")

    def test_script_declares_every_state_key(self):
        self.assertTrue(os.path.isfile(PKG_SCRIPT), "tools/pkg-install-test.sh 不存在")
        body = read(PKG_SCRIPT)
        self.assertTrue(body.startswith("#!/bin/sh"), "打包门必须是 /bin/sh（异机方言坑在册）")
        for key in ("PKG_BUILD", "TWINE_CHECK", "WHEEL_INSTALL", "SDIST_INSTALL",
                    "CLI_HELP", "QUICKSTART", "SUITE_ON_INSTALLED", "PIPX",
                    "REPO_CLEAN", "PKG_INSTALL_TEST"):
            self.assertIn(key + "=", body, "脚本不输出 %s 这一档⇒那一件事没被判定" % key)
        self.assertRegex(body, r"PKG_INSTALL_TEST=(PASS|FAIL|UNVERIFIED)",
                         "末行判决必须只有三档，不许折绿")

    def test_script_never_folds_a_missing_tool_into_pass(self):
        body = read(PKG_SCRIPT)
        # 取不到 pipx／构建件 ⇒ 必须写 UNVERIFIED **并当场退 2**，不许"没测到"= 通过。
        self.assertIn('say "PKG_INSTALL_TEST=UNVERIFIED"\n    exit 2', body,
                      "UNVERIFIED 那一档没有配套的退出码⇒判决行还在，门却是虚的")
        self.assertRegex(body, r"PKG_INSTALL_TEST=FAIL", "判决行得能出现")
        self.assertIn('say "PKG_INSTALL_TEST=FAIL"\n    exit 1', body,
                      "不合格必须退 1（与测不了那档分开），CI 才能把两种红分开读")

    def test_build_is_pinned_to_the_commit_clock(self):
        """造件要按**提交时刻**打时间戳，不然同一枚提交两次造出来的字节不一样。

        这条不是洁癖：发布物的身份是按字节对的（v0.1.0 那枚 tar.gz 就是靠 SHA256 对上的）。
        wheel/sdist 里每条成员都带 mtime，不写 SOURCE_DATE_EPOCH 时它取的是**文件被复制的那一刻**，
        于是"重跑一遍看看哈希"必然对不上——那种哈希没法当身份用。
        实测边界（写在这里免得被读成"两件都逐字节可复现"）：钉了之后 **wheel 两次造出来逐字节相同**，
        sdist 仍有三处随造件时刻变（tar 顶层目录条目、egg-info 各条目 mtime、gzip 头时间戳），
        那条取舍见 `test_second_build_of_same_sources_matches`。
        """
        body = read(PKG_SCRIPT)
        self.assertIn("SOURCE_DATE_EPOCH", body,
                      "造件没钉时间戳⇒同一枚提交两次造出来的件字节不同，哈希不能当身份")
        self.assertRegex(body, r"log -1 --format=%ct",
                          "时间戳要取自提交时刻（取 `date +%s` 等于每次现造现变，哈希当不了身份）")
        self.assertIn('say "SOURCE_DATE_EPOCH=NO_GIT', body,
                      "取不到提交时刻时要如实说一句不可复现，不许静默用现在的时间")

    def test_name_tool_verdicts_are_three_way(self):
        """核查件的三档要**真跑出来**：把 probe 换成三种读数，退出码必须各不相同。

        这一格不联网（联网那一步由现跑取证）。它钉的是"取不到时不许退 0"这条行为，
        而不是源代码里有没有那个词——把 `exit 2` 改成 `exit 0` 必须让这一格红。
        """
        import contextlib
        import io
        mod = _load_name_tool()
        original = mod.probe
        original_count = mod.read_json_release_count
        mod.read_json_release_count = lambda url, timeout=20: 0  # TAKEN 那档也别真联网
        cases = {"两个入口都 404": (lambda u, timeout=20: 404, 0, "FREE"),
                 "有一个入口回 200": (lambda u, timeout=20: 200, 1, "TAKEN"),
                 "有一个入口取不到": (lambda u, timeout=20: None, 2, "UNVERIFIABLE")}
        try:
            for label, (stub, want_rc, want_word) in cases.items():
                mod.probe = stub
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = mod.main(["q2c"])
                out = buf.getvalue()
                self.assertEqual(rc, want_rc, "%s 这一档退出码不对（输出：%s）"
                                 % (label, out.strip()[:160]))
                self.assertIn(want_word, out, "%s 这一档没写出 %s 这个结论" % (label, want_word))
        finally:
            mod.probe = original
            mod.read_json_release_count = original_count

    def test_pypi_name_tool_is_fail_closed(self):
        """核查件本身必须三档（空闲／被占／测不了），且**测不了不许退 0**。

        这格不联网：联网那一步由现跑取证（`evidence/pypi-name-*.txt`）。
        这里钉的是形状——万一哪天有人把 UNVERIFIABLE 折成 FREE，判据得拦。
        """
        mod = _load_name_tool()
        self.assertEqual(mod.normalize("Q2-C"), "q2-c",
                         "名字归一化不对⇒会把已被占用的名字读成空闲")
        self.assertEqual(mod.normalize("Q_2.C"), "q-2-c",
                         "下划线与点号也要并（PyPI 用同一个键判占用）")
        self.assertEqual(mod.normalize("Q2C"), "q2c", "大小写要归一（PyPI 不区分）")
        src = read(NAME_TOOL)
        for word in ("FREE", "TAKEN", "UNVERIFIABLE"):
            self.assertIn(word, src, "核查件少了 %s 这一档" % word)


class Test07_PyPI发布作业(unittest.TestCase):
    """发布作业的前置闸本身也得有牙：它读不到证据时必须**说清为什么读不到**。

    2026-10-05 00:55 现跑抓到的两处（都是我这边的 bug，不是 CI 不绿）：
      1. `gh api repos/…/commits/<sha>/check-suites` 的返回里**没有 `workflow_name` 这个键**
         （实测键名列表里就没有它），我却拿 `.workflow_name == "ci"` 做匹配 ⇒ 三道作业永远"无结论"；
      2. workflow 顶层 `permissions` 只给了 `contents: read` ＋ `id-token: write`，
         没给 `actions: read` ⇒ 那次 API 调用其实是被 403 拒了，而我在命令上挂了 `2>/dev/null`
         把拒因咽掉，只留下一句"没有可判结论"。
    闸的行为是对的（读不到就拒发、退 2、不发布），错的是它没说"为什么读不到"。
    """

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(ROOT, ".github", "workflows", "publish.yml")
        cls.body = read(cls.path) if os.path.isfile(cls.path) else ""

    def test_job_exists_and_is_manual_only(self):
        self.assertTrue(os.path.isfile(self.path), "publish.yml 不存在⇒PyPI 这条路没人写")
        self.assertRegex(self.body, r"(?m)^\s*workflow_dispatch:",
                         "发布必须手动触发：自动发布＝把\"要不要发\"交给了 CI，那决定权在主理人")

    def test_precheck_reads_a_source_that_actually_carries_names(self):
        """前置闸要问**真带名字与结论的那个接口**，不是键都不存在的那个。"""
        self.assertIn("actions/runs", self.body,
                      "前置闸没查 actions/runs（这个接口才有 .name 与 .conclusion）")
        self.assertNotIn("workflow_name", self.body,
                         "又在用 check-suites 的 workflow_name 做匹配——那个键在这个响应里不存在，"
                         "匹配永远为空，于是三道绿作业被读成\"没有结论\"")

    def test_precheck_has_read_permission_for_the_query(self):
        self.assertRegex(self.body, r"(?m)^\s*actions:\s*read\b",
                         "没给 actions: read ⇒ 那次 API 调用是 403，闸只能报\"取不到\"而不是\"没绿\"")

    def test_failure_reason_is_not_swallowed(self):
        """拒发的理由必须落在日志里：`2>/dev/null` 把 403 咽掉＝下一轮还得从头查一遍。"""
        block = self.body[self.body.find("前置闸"):] if "前置闸" in self.body else self.body
        self.assertNotIn("2>/dev/null", block.split("run: |")[1] if "run: |" in block else block,
                         "前置闸里有用 2>/dev/null 吞错误的写法⇒失败无诊断")

    def test_no_long_lived_credential_is_referenced(self):
        """全 OIDC：仓里不许出现任何 secret 引用（这是主理人\"不索取明文凭证\"那条的执行面）。"""
        self.assertNotIn("secrets.", self.body,
                         "发布作业引用了 secret⇒就有了长期凭证这条路，口径要改回\"不索取凭证\"")
        self.assertRegex(self.body, r"(?m)^\s*id-token:\s*write\b",
                         "没给 id-token: write ⇒ 换不到 OIDC 短期凭证，上传一定失败")

    def test_verdict_branches_are_distinct(self):
        for pat, why in ((r"PRECHECK=REFUSED", "有作业不绿的档"),
                         (r"PRECHECK=UNKNOWN", "读不到结论的档（≠没绿）"),
                         (r"PRECHECK=PASS", "放行那一档")):
            self.assertRegex(self.body, pat, "前置闸缺 %s" % why)
        self.assertRegex(self.body, r"exit 1", "不合格要退 1")
        self.assertRegex(self.body, r"exit 2", "取不到要退 2，与 1 分开")


if __name__ == "__main__":
    unittest.main(verbosity=2)
