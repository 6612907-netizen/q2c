#!/usr/bin/env python3
"""产物引用：类型化 ref ＋ 摘要，优先"引用"而不是复制正文（任务书 §8）。

这一模块的原型是已验证内核里的候选导出那一段（`relay.py:1387-1466`）。
它当时解决的问题很具体：把**正在被自己写的活根**交给对方复跑，
对方复制到的是刚生成的锁与正在飞的记录，判决没有对象。所以导出规则整块保留：

  · 排除运行态（`state/`、`inbox/`、`trace/`、`.git`、各类日志与缓存），
  · 排除凭据类文件（见 `security.looks_like_credential_path`），
  · 导出之后立刻算**清单指纹**，重投时复用同一枚指纹 ⇒ 对象不变，
  · 指纹对不上 ⇒ 拒绝派发（拿残缺对象去叫人，得到的判决没有对象可言）。

诚实性三态：`OK`／`MISSING`／`DIVERGED`，读不动就是 `UNVERIFIABLE`——**一律不折成 OK**。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from . import protocol, security

# 快照时永不带走的运行态目录名（来自候选导出的在册名单，含真事故里被复制到的那几个）
SNAPSHOT_SKIP_DIRS = (
    ".git", "state", "inbox", "handoffs", "trace", "evidence", "__pycache__",
    ".zion-mcp", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache",
)
SNAPSHOT_SKIP_SUFFIX = (".log", ".jsonl", ".lock", ".tmp", ".pyc")
SNAPSHOT_SKIP_NAMES = (".DS_Store", "wake.lock", "ledger.lock")

VERIFY_OK = "OK"
VERIFY_MISSING = "MISSING"
VERIFY_DIVERGED = "DIVERGED"
VERIFY_UNVERIFIABLE = "UNVERIFIABLE"

FILE_TYPES = ("file", "diff", "patch", "log", "test_report", "screenshot")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def describe_file(kind: str, path: str) -> protocol.ArtifactRef:
    """把本地文件折成一条 typed ref（digest＝字节摘要，不是路径摘要）。"""
    if kind not in FILE_TYPES + ("evidence_package",):
        raise protocol.ProtocolError("BAD_ARTIFACT_TYPE_FOR_FILE", kind)
    p = os.path.realpath(os.path.expanduser(path))
    if not os.path.exists(p):
        raise protocol.ProtocolError("ARTIFACT_MISSING", p)
    if security.looks_like_credential_path(p):
        raise protocol.ProtocolError("ARTIFACT_IS_CREDENTIAL_PATH", p)
    if os.path.isdir(p):
        if kind != "evidence_package":
            raise protocol.ProtocolError("ARTIFACT_IS_DIR", "%s 要标 evidence_package" % p)
        digest = manifest_fingerprint(p)
        size = None
    else:
        digest = sha256_file(p)
        size = os.path.getsize(p)
    return protocol.ArtifactRef(type=kind, ref=p, digest=digest, size_bytes=size)


def commit_ref(commit: str, repo_path: str) -> protocol.ArtifactRef:
    """git 提交的引用：ref＝提交号，完整性靠"在该仓库里能不能取回这枚提交"验。

    摘要位留空是有意的：把 40 位提交号哈希成 64 位再存一遍，是**假装有摘要**。
    """
    return protocol.ArtifactRef(type="git_commit", ref=(commit or "").strip(), digest="")


def manifest_rows(root: str) -> list:
    """目录快照的清单行：`相对路径\\tsha256`，按路径排序（同输入同输出，可复现）。"""
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        for fn in sorted(filenames):
            if _skip_file(fn):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                rows.append("%s\t%s" % (rel, sha256_file(full)))
            except OSError:
                rows.append("%s\t<unreadable>" % rel)
    return rows


def _skip_dir(name: str) -> bool:
    # 只按在册名单剪运行态；不做"凡点开头的目录都剪"——`.github/` 是要随包走的东西，
    # 宽规则会让导出的快照悄悄缺件，而缺件的指纹仍然自洽（这就是"可复现的错误对象"）。
    return name in SNAPSHOT_SKIP_DIRS


def _skip_file(name: str) -> bool:
    if name in SNAPSHOT_SKIP_NAMES:
        return True
    if any(name.endswith(s) for s in SNAPSHOT_SKIP_SUFFIX):
        return True
    return security.looks_like_credential_path(name)


def manifest_fingerprint(root: str) -> str:
    return sha256_text("\n".join(manifest_rows(root)))


def export_package(src: str, dest_parent: str | None = None, extra_skip_dirs=()) -> tuple:
    """导一份排除运行态的快照，返回 (路径, 清单指纹)。

    `extra_skip_dirs` 给调用方追加本项目的运行态目录（产品的配置目录名不许写死在这里）。
    """
    src = os.path.realpath(os.path.expanduser(src))
    if not os.path.isdir(src):
        raise protocol.ProtocolError("PACKAGE_SRC_NOT_DIR", src)
    parent = dest_parent or os.path.join(os.path.expanduser("~"), ".q2c", "packages")
    os.makedirs(parent, exist_ok=True)
    name = "%s-%s" % (os.path.basename(src.rstrip(os.path.sep)), manifest_fingerprint(src)[:12])
    dest = os.path.join(parent, name)
    if os.path.isdir(dest):
        shutil.rmtree(dest)

    skip = set(SNAPSHOT_SKIP_DIRS) | set(extra_skip_dirs or ())

    def _ignore(dirpath, names):
        skip_now = set()
        for n in names:
            full = os.path.join(dirpath, n)
            if n in skip or n in SNAPSHOT_SKIP_NAMES or n in SNAPSHOT_SKIP_DIRS:
                skip_now.add(n)
            elif os.path.isdir(full) and _skip_dir(n):
                skip_now.add(n)
            elif os.path.isfile(full) and _skip_file(n):
                skip_now.add(n)
        return skip_now

    shutil.copytree(src, dest, ignore=_ignore, symlinks=True)
    return dest, manifest_fingerprint(dest)


def verify(ref: protocol.ArtifactRef, home: str = "") -> tuple:
    """核一条引用还在不在、还是不是那些字节。返回 (state, note)。

    四态里 `UNVERIFIABLE` 与 `MISSING` 严格分开：前者是"我没能力判"，后者是"判过，确实不在"。
    把前者写成后者＝把工装坏了报成对象丢了。
    """
    try:
        if ref.type == "git_commit":
            return _verify_commit(ref)
        if ref.type == "generic_uri":
            return VERIFY_UNVERIFIABLE, "generic_uri 不做可达性核验（q2c 不打开产物内容）"
        p = os.path.realpath(os.path.expanduser(ref.ref))
        if ref.type == "evidence_package":
            if not os.path.isdir(p):
                return VERIFY_MISSING, "快照目录不在：%s" % p
            got = manifest_fingerprint(p)
        else:
            if not os.path.isfile(p):
                return VERIFY_MISSING, "文件不在：%s" % p
            got = sha256_file(p)
        if not ref.digest:
            return VERIFY_UNVERIFIABLE, "引用没带摘要，只核到了可达：%s" % p
        if got == ref.digest:
            return VERIFY_OK, got
        return VERIFY_DIVERGED, "摘要不符：引用记 %s…，现算 %s…" % (ref.digest[:12], got[:12])
    except OSError as exc:
        return VERIFY_UNVERIFIABLE, "读不动：%s" % type(exc).__name__


def _verify_commit(ref: protocol.ArtifactRef) -> tuple:
    if not os.environ.get("Q2C_GIT_BIN", "git"):
        return VERIFY_UNVERIFIABLE, "git 被显式禁用"
    sha = ref.ref
    # 引用里只存提交号，不存仓库路径：仓库坐标在信封的 repo_ref/workspace_ref 上
    return VERIFY_UNVERIFIABLE, "git_commit 需在具体仓库内核验，请用 verify_in_repo()"


def verify_in_repo(ref: protocol.ArtifactRef, repo_path: str) -> tuple:
    if ref.type != "git_commit":
        return VERIFY_UNVERIFIABLE, "类型不对（%s）" % ref.type
    repo = os.path.realpath(os.path.expanduser(repo_path or ""))
    if not (os.path.isdir(os.path.join(repo, ".git")) or os.path.isfile(os.path.join(repo, ".git"))):
        return VERIFY_UNVERIFIABLE, "不是 git 仓库：%s" % repo
    try:
        p = subprocess.run(["git", "-C", repo, "cat-file", "-e", "%s^{commit}" % ref.ref],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except Exception as exc:
        return VERIFY_UNVERIFIABLE, "探不了：%s" % type(exc).__name__
    if p.returncode == 0:
        return VERIFY_OK, ref.ref
    return VERIFY_MISSING, "该仓库里取不到这枚提交：%s" % ref.ref
