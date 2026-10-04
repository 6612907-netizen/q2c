#!/usr/bin/env python3
"""发布物清单：从**冻结提交**生成，逐条可取回校验；发布域与证据域**各一张**。

三条口径（都是旧内核用事故换来的）：
  1. 清单只能从 commit 生成，不许读工作树——读工作树会把"没提交的改动"混进发布物；
  2. 校验时逐条 `git cat-file` 回 blob 现算哈希，与清单对；对不上就点名，不退 0；
  3. 中文路径一律 `-z`（NUL 分隔），不解析 git 的人类格式（quotePath 曾把 16 条读成 246 条）。

再加两条（2026-10-04 被自己的校验抓到之后补的）：
  4. **两张分工**：发布域那张不含 `evidence/`（证据里的读数文件会随复算再写一次，
     混进发布物清单就永远对不上），证据域那张只含 `evidence/`；两页清单互不收录；
  5. 收录规则**写进文件头**，并由 tests/test_release_manifest.py 校"说的=做的"
     （旧代码那个 `exclude_prefix` 参数从写下那天起没被用过，等于门是虚的）。

用法：
    python3 tools/make-manifest.py --both          # 两张一起生成（冻结时用的就是这条）
    python3 tools/make-manifest.py                 # 校验发布域那张（默认）
    python3 tools/make-manifest.py --scope evidence
"""
import argparse
import ast
import hashlib
import re
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_PREFIX = "evidence/"
#: 发布域的**排除**名单：证据件与发布报告本身。
#: 报告是"关于这次发布的记录"（含事后才有的 CI 引用与结论行），不是别人 `pip install` 得到的东西；
#: 把它算进发布物，就意味着"写下结论"这一步本身会改变发布物字节 ⇒ 结论永远无法自证。
#: 所以：包里装的是代码／文档／工装／判据；报告与证据随仓与交付副本发布，不随包走。
RELEASE_EXCLUDE_PREFIXES = (EVIDENCE_PREFIX,)
#: 发布报告的**形状**（不是某一枚文件名）：版本一升，旧写法会把新报告当成发布物收进包里，
#: 上面那条老毛病当场复发。判据 `TestVersionAwareNames` 钉的就是这个形状。
REPORT_RE = re.compile(r"^Q2C-v\d+\.\d+\.\d+-RELEASE-REPORT\.md$")


def is_release_report(path):
    """这一枚是不是发布报告（任何版本的那一份都算）。"""
    return bool(REPORT_RE.match(path))


def package_version():
    """版本号从**唯一真源** `q2c/_version.py` 现读（AST 取字面量，不 import）。

    写死在这里就等于第二次真源：升版本时清单文件名会悄悄不跟着走，
    于是新一轮的清单续写进旧那一页，两份身份混在同一张纸上。
    """
    path = os.path.join(ROOT, "q2c", "_version.py")
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError) as exc:
        raise SystemExit("VERSION_SOURCE_UNREADABLE（%s：%s）" % (path, exc))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__version__" \
                        and isinstance(node.value, ast.Constant):
                    # 取的是**常量值**，不是那个 AST 节点（写错时文件名会长成
                    # `SHA256SUMS-vConstant(value='0.1.0', kind=None).txt`——本轮真踩过一次）
                    return str(node.value.value)
    raise SystemExit("VERSION_LITERAL_MISSING（q2c/_version.py 里没读到 __version__ 字面量）")


VERSION = package_version()
RELEASE_OUT = os.path.join(ROOT, "evidence", "SHA256SUMS-v%s.txt" % VERSION)
EVIDENCE_OUT = os.path.join(ROOT, "evidence", "SHA256SUMS-evidence-v%s.txt" % VERSION)
OUT = RELEASE_OUT      # 兼容旧引用；两张清单各走 out_for(scope)


def git(*args):
    p = subprocess.run(["git", "-C", ROOT] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise SystemExit("git %s 失败：%s" % (" ".join(args), p.stderr.decode("utf-8", "replace")[:200]))
    return p.stdout


def tracked_files():
    raw = git("ls-files", "-z")
    return sorted(f.decode("utf-8") for f in raw.split(b"\0") if f)


def blob_of(path):
    """读的是 **HEAD 那枚提交里的 blob**，不是工作树。"""
    return git("show", "HEAD:%s" % path)


# REPORT_FILE 这枚写死的名字已经删掉：报告改由 `is_release_report()` 认形状。
# 留着它＝升一次版本就多一处"没人想起来改"的地方（判据 TestVersionAwareNames）。


def in_evidence_scope(path):
    """证据域＝`evidence/` 下的一切 ＋ 发布报告本身（它属于"关于发布的记录"）。"""
    return path.startswith(EVIDENCE_PREFIX) or is_release_report(path)


def out_for(scope):
    if scope == "evidence":
        return EVIDENCE_OUT
    if scope == "release":
        return RELEASE_OUT
    raise SystemExit("SCOPE_UNKNOWN=%r（只认 release／evidence）" % scope)


def build(scope="release"):
    """按域收录：**发布域**不含证据件，**证据域**只含证据件。

    这里原来写着一个 `exclude_prefix=("evidence/",)` 参数，但循环里**从没用过它**——
    于是 2026-10-04 那次生成的"发布物清单"把 128 枚证据件一起收了进去（196 条里只有 68 条
    是发布物）。后果不是"清单长了一点"：证据里的读数文件在清单生成**之后**又重生成了一次，
    校验当场报 `diverged=1`。也就是说这张清单描述的既不是发布物、也不是当时那枚提交。
    规则改由代码执行，并且有判据盯着（tests/test_release_manifest.py）。
    """
    rows = []
    head = git("rev-parse", "HEAD").decode().strip()
    skip = {os.path.relpath(RELEASE_OUT, ROOT), os.path.relpath(EVIDENCE_OUT, ROOT)}
    for f in tracked_files():
        # 两张清单**互不收录**：A 描述 B、B 描述 A 的话，任何一张落笔都要重生成另一张，
        # 这是个死循环；清单也不该把自身当发布物（哈希永远对不上）。
        if f in skip:
            continue
        is_evidence = in_evidence_scope(f)
        if scope == "release" and is_evidence:
            continue
        if scope == "evidence" and not is_evidence:
            continue
        rows.append("%s  %s" % (hashlib.sha256(blob_of(f)).hexdigest(), f))
    return head, rows


def blob_at(rev, path):
    """读的是**指定那一枚提交里的 blob**，不是工作树。"""
    return git("show", "%s:%s" % (rev, path))


def verify(scope="release"):
    """校验＝逐条从**清单记的那枚提交**里取 blob 现算哈希。

    为什么不再拿 HEAD 比：发布物的身份是"清单头记的那一枚"，而不是分支尖。
    冻结之后还会往仓里加东西（CI 回来的原件、报告的最后一段），那些都在 `evidence/` 里，
    不属于发布域；如果校验只认 HEAD，每加一笔证据就得重打一张清单，
    而重打的那张又让下一次校验变 STALE —— 死循环。现在：
      · 逐条哈希打在 recorded 那一枚上（这才是"发布物可复现"的真正含义）；
      · 另附一行漂移读数：从 recorded 到 HEAD 之间**发布域**有没有文件变过。
        变了就是真漂移（发布物与清单不再同物），得重新冻结；没变就写"零漂移"。
    """
    OUT = out_for(scope)
    if not os.path.isfile(OUT):
        print("MANIFEST=ABSENT（先 --write 生成）")
        return 2
    head = git("rev-parse", "HEAD").decode().strip()
    with open(OUT, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    recorded = ""
    rows = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("#"):
            if "清单：" in line and "收录规则" not in line:
                recorded = line.split("：", 1)[1].strip()
            continue
        rows.append(line)
    rev = recorded or "HEAD"
    probe = subprocess.run(["git", "-C", ROOT, "cat-file", "-e", rev],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if probe.returncode != 0:
        print("MANIFEST_HEAD_UNRESOLVED（清单记的 %s 在本仓取不到 ⇒ 无法校验）" % rev[:12])
        return 2
    ghosts, diverged, checked = [], [], 0
    for line in rows:
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            print("MALFORMED 行（不计通过）：%s" % line[:80])
            return 2
        want, path = parts
        try:
            got = hashlib.sha256(blob_at(rev, path)).hexdigest()
        except SystemExit:
            ghosts.append(path)
            continue
        checked += 1
        if got != want:
            diverged.append(path)
    drift = ""
    if scope == "release":
        d = git("diff", "--name-only", rev, head, "--", ".",
                ":(exclude)evidence", ":(exclude,glob)Q2C-v*-RELEASE-REPORT.md")
        drift = d.decode("utf-8").strip().splitlines()
    print("manifest_head=%s current_head=%s %s" % (
        rev[:12], head[:12], "SAME" if rev == head else "清单做于 %s（发布物身份就是这一枚）" % rev[:12]))
    print("checked=%d ghosts=%d diverged=%d" % (checked, len(ghosts), len(diverged)))
    if ghosts:
        print("GHOST（清单里有、提交里取不到）：%s" % ",".join(ghosts[:5]))
    if diverged:
        print("DIVERGED（提交里的 blob 与清单不符）：%s" % ",".join(diverged[:5]))
    if ghosts or diverged:
        return 1
    if scope == "release":
        if drift:
            print("DRIFTED（%s→HEAD 发布域有文件变过 ⇒ 清单不再描述当前提交，需重冻结）：%s"
                  % (rev[:12], ",".join(drift[:5])))
            return 1
        print("drift=0（%s→HEAD 发布域零变化；evidence/ 里加的东西不算发布物漂移）" % rev[:12])
    print("MANIFEST_OK（清单逐条可从 %s 取回且哈希相符）" % rev[:12])
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--scope", default="release", choices=["release", "evidence"],
                    help="release＝发布域（代码／文档／工装／判据）；evidence＝证据域")
    ap.add_argument("--both", action="store_true", help="两张一起生成")
    a = ap.parse_args()
    if a.write or a.both:
        scopes = ["release", "evidence"] if a.both else [a.scope]
        for scope in scopes:
            head, rows = build(scope)
            OUT = out_for(scope)
            os.makedirs(os.path.dirname(OUT), exist_ok=True)
            rule = ("收录＝本提交跟踪的全部常规文件，除 evidence/、发布报告本身与两页清单自身"
                    if scope == "release" else
                    "收录＝evidence/ 下的跟踪件 ＋ 发布报告本身，除两页清单自身")
            title = ("发布物清单" if scope == "release" else "证据与发布记录清单")
            with open(OUT, "w", encoding="utf-8") as fh:
                fh.write("# %s：%s\n" % (title, head))
                fh.write("# 生成方式：git show HEAD:<path> 现算，不读工作树\n")
                fh.write("# 收录规则：%s\n" % rule)
                fh.write("\n".join(rows) + "\n")
            print("WROTE %s（%d 条，HEAD=%s）" % (OUT, len(rows), head[:12]))
        return 0
    rc = verify(a.scope)
    if a.scope == "release" and not os.path.isfile(EVIDENCE_OUT):
        print("EVIDENCE_MANIFEST=ABSENT（证据域那张还没生成）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
