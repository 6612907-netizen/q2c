#!/bin/sh
# 打包门：把"一个没参与过 Q2C 的人装 q2c"这条路**整条真跑一遍**（造件→装件→跑 CLI→
# 用装上的包跑判据），并留下一份可对号的日志。
#
# 为什么是脚本而不是 workflow 里手写几步（任务书第 6 条的落地方式）：
#   2026-10-04 那次最先红的从来不是产品逻辑，是安装路径 —— shell 方言、缺 setuptools、
#   按 mtime 挑件。那些事写在 YAML 里，只有 CI 第一次跑的时候才暴露；
#   收成"一条命令＋一个退出码"之后，本机就能在烧 CI 之前先跑同一份门。
#   CI 那道 job 叫的就是本脚本，两边不存在两套口径。
#
# 五条口径写死在代码里：
#   1. 全程只在临时目录造／装／跑。仓里不落 dist/、build/、*.egg-info/（末档 REPO_CLEAN 校这一条）；
#   2. 退出码三档：0＝每一档都真绿；1＝有档不合格；**2＝测不了**（环境缺件，绝不折成通过）；
#   3. 装完之后跑的那套判据，读的是**装进 site-packages 的那份 q2c**，不是源码树 ——
#      所以"包里少一个模块／CLI 入口是坏的"这类事一定红，蒙不过去；
#   4. 任何一档要判绿，看的是**退出码＋输出里的读数行**，不看"跑完了没报错"；
#   5. 造件时间戳取自**提交时刻**（SOURCE_DATE_EPOCH）——发布物的身份是按字节对的，
#      取现在的时间＝每次造出来哈希都不一样，那枚哈希当不了身份。
#
# 用法：sh tools/pkg-install-test.sh                     日志与造的件落在 ./pkg-out/
#       OUT_DIR=/tmp/x Q2C_PYTHON=/path/python3 sh tools/pkg-install-test.sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${OUT_DIR:-$here/pkg-out}
PY=${Q2C_PYTHON:-}
if [ -z "$PY" ]; then
  PY=$(command -v python3 || true)
fi

say() { printf '%s\n' "$*"; }
step() { say ""; say "== $*"; }

FAILS=""
UNS=""
note_fail() { FAILS="$FAILS $1"; say "$1=FAIL（${2}）"; }
note_unver() { UNS="$UNS $1"; say "$1=UNVERIFIED（${2}）"; }

digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

verdict() {
  say ""
  if [ -n "$UNS" ]; then
    say "取不到的档：$UNS"
  fi
  if [ -n "$FAILS" ]; then
    say "不合格的档：$FAILS"
  fi
  if [ -n "$FAILS" ]; then
    say "PKG_INSTALL_TEST=FAIL"
    exit 1
  fi
  if [ -n "$UNS" ]; then
    say "PKG_INSTALL_TEST=UNVERIFIED"
    exit 2
  fi
  say "PKG_INSTALL_TEST=PASS"
  exit 0
}

if [ -z "$PY" ]; then
  say "PY=MISSING（连 python3 都取不到，这条门整条测不了）"
  say "PKG_INSTALL_TEST=UNVERIFIED"
  exit 2
fi

WORK=$(mktemp -d)
SRC=$WORK/src
DIST=$WORK/dist
LOG=$WORK/logs
SUIT=$WORK/suite
WV=$WORK/wheel-venv
SV=$WORK/sdist-venv
BV=$WORK/build-venv
mkdir -p "$SRC" "$DIST" "$LOG" "$SUIT"
say "python=$("$PY" -V 2>&1)"
say "workdir=$WORK"

# --------------------------------------------------------------------------
step "1) 把工作树的发布域复制到临时目录（evidence/ 与发布报告不复制＝不进包）"
if ! "$PY" - "$here" "$SRC" <<'PY' >"$LOG/copy.log" 2>&1
import os, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
SKIP_DIRS = {".git", "__pycache__", "dist", "build", ".q2c", "evidence", "pkg-out",
             "clean-machine-out", "node_modules"}
SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = ("-RELEASE-REPORT.md",)
n = 0
for base, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")
               and not (d.startswith(".") and d != ".github")]
    for name in files:
        if name in SKIP_FILES or name.endswith((".pyc", ".pyo")) or \
                name.endswith(SKIP_SUFFIXES):
            continue
        rel = os.path.relpath(os.path.join(base, name), src)
        target = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(os.path.join(base, name), target)
        n += 1
print("copied=%d" % n)
PY
then
  note_fail SRC_COPY "$(tail -1 "$LOG/copy.log" | tr '\n' ' ')"
  verdict
fi
say "SRC_COPY=OK $(cat "$LOG/copy.log")"

# --------------------------------------------------------------------------
step "2) 造件：PEP 517 构建 wheel＋sdist（构建隔离，不碰仓）"
PKG_BUILD=UNVERIFIED
WHL=""
SD=""
# 造件的时间戳**取自提交时刻**，不用现在的时间：wheel/sdist 里每条成员都带 mtime，
# 而 `*.dist-info/METADATA`、`RECORD`、`WHEEL` 这几条是**构建期现生成**的文件——
# 不钉这一枚，它们每次都是"现在"那一刻（2026-10-04 现跑证实：两次解出来的内容逐字节相同，
# zip 目录项的时间戳却不同）。不钉这一枚的话同一份代码两次造出来的件哈希不一样，
# 而发布物的身份是靠哈希对的
# （v0.1.0 那枚 tar.gz 就是这么对上的）。取不到提交时刻就如实说不可复现，不静默用现在时间。
EPOCH=$(git -C "$here" log -1 --format=%ct 2>/dev/null || true)
if [ -n "$EPOCH" ]; then
  export SOURCE_DATE_EPOCH="$EPOCH"
  say "SOURCE_DATE_EPOCH=${EPOCH}（提交 $(git -C "$here" rev-parse --short HEAD 2>/dev/null)；两次造件应当逐字节相同）"
else
  say "SOURCE_DATE_EPOCH=NO_GIT（取不到提交时刻⇒本轮件的字节不可复现，如实记这一条）"
fi
if "$PY" -m venv "$BV" >"$LOG/venv1.log" 2>&1 && \
   "$BV/bin/python" -m pip install -q --disable-pip-version-check build twine \
        >"$LOG/pipbuild.log" 2>&1; then
  if "$BV/bin/python" -m build --outdir "$DIST" "$SRC" >"$LOG/build.log" 2>&1; then
    if grep -qE "consider removing the following classifiers|as a TOML table is deprecated" \
         "$LOG/build.log"; then
      PKG_BUILD=FAIL
      note_fail PKG_BUILD "构建期有弃用告警（今天只印一行，过了 2027-02-18 就是拒建）"
    else
      PKG_BUILD=OK
    fi
  else
    PKG_BUILD=FAIL
    note_fail PKG_BUILD "构建退非 0，见 $LOG/build.log"
    tail -20 "$LOG/build.log"
  fi
  WHL=$(find "$DIST" -maxdepth 1 -name 'q2c-*.whl' -print | head -1)
  SD=$(find "$DIST" -maxdepth 1 -name 'q2c-*.tar.gz' -print | head -1)
  if [ "$PKG_BUILD" = OK ] && { [ -z "$WHL" ] || [ -z "$SD" ]; }; then
    PKG_BUILD=FAIL
    note_fail PKG_BUILD "造出来的件不齐：wheel=[${WHL}] sdist=[${SD}]"
  fi
else
  note_unver PKG_BUILD "构建工装装不上（venv 或 pip 取不到 build/twine，多半是网络）：$(tail -1 "$LOG/pipbuild.log" 2>/dev/null | tr '\n' ' ')"
fi
if [ "$PKG_BUILD" = OK ]; then
  say "${PKG_BUILD}（wheel=$(basename "$WHL") sdist=$(basename "$SD")）"
  say "wheel_sha256=$(digest "$WHL")"
  say "sdist_sha256=$(digest "$SD")"
else
  verdict
fi

# --------------------------------------------------------------------------
step "3) 元数据校验（twine check：包页面的字段与描述 PyPI 收不收）"
if "$BV/bin/python" -m twine check "$DIST"/* >"$LOG/twine.log" 2>&1; then
  TWINE_CHECK=OK
  say "${TWINE_CHECK}"
  sed -n '1,4p' "$LOG/twine.log"
else
  TWINE_CHECK=FAIL
  note_fail TWINE_CHECK "$(tail -3 "$LOG/twine.log" | tr '\n' ' ')"
fi

# --------------------------------------------------------------------------
step "4) 全新环境装 wheel，再跑装完后的第一条命令"
CLI_HELP=SKIP
CLI_VERSION=SKIP
WHEEL_INSTALL=UNVERIFIED
if "$PY" -m venv "$WV" >"$LOG/venv2.log" 2>&1; then
  if "$WV/bin/python" -m pip install -q --disable-pip-version-check --no-deps "$WHL" \
       >"$LOG/inst-whl.log" 2>&1; then
    WHEEL_INSTALL=OK
    say "${WHEEL_INSTALL}（venv 里 pip 装本地 wheel；本包零第三方依赖）"
    if "$WV/bin/q2c" --help >"$LOG/help.log" 2>&1 && \
       grep -q "^usage: q2c" "$LOG/help.log"; then
      CLI_HELP=OK
      say "${CLI_HELP}（控制台脚本在 PATH 上、用法能打出来）"
    else
      CLI_HELP=FAIL
      note_fail CLI_HELP "装完的 q2c --help 不成立（退非 0 或没打出 usage ⇒ 入口是空的）"
    fi
    if "$WV/bin/q2c" version >"$LOG/ver.log" 2>&1 && \
       grep -q '"protocol_version": "q2c/1"' "$LOG/ver.log"; then
      CLI_VERSION=OK
      say "${CLI_VERSION} $(tr -d ' \n' < "$LOG/ver.log")"
    else
      CLI_VERSION=FAIL
      note_fail CLI_VERSION "装上的包报不出 q2c/1"
    fi
  else
    WHEEL_INSTALL=FAIL
    note_fail WHEEL_INSTALL "$(tail -2 "$LOG/inst-whl.log" | tr '\n' ' ')"
  fi
else
  note_unver WHEEL_INSTALL "venv 造不出来"
fi

# --------------------------------------------------------------------------
step "5) 全新环境装 sdist（源码包要能自己构建：判据树里那些件就靠这条路）"
SDIST_INSTALL=UNVERIFIED
CLI_HELP_FROM_SDIST=SKIP
if "$PY" -m venv "$SV" >"$LOG/venv3.log" 2>&1; then
  if "$SV/bin/python" -m pip install -q --disable-pip-version-check --no-deps "$SD" \
       >"$LOG/inst-sd.log" 2>&1; then
    SDIST_INSTALL=OK
    say "${SDIST_INSTALL}"
    if "$SV/bin/q2c" --help >"$LOG/help-sd.log" 2>&1 && \
       grep -q "^usage: q2c" "$LOG/help-sd.log"; then
      CLI_HELP_FROM_SDIST=OK
      say "${CLI_HELP_FROM_SDIST}"
    else
      CLI_HELP_FROM_SDIST=FAIL
      note_fail CLI_HELP_FROM_SDIST "从源码包装完，q2c --help 不成立"
    fi
  else
    SDIST_INSTALL=FAIL
    note_fail SDIST_INSTALL "$(tail -2 "$LOG/inst-sd.log" | tr '\n' ' ')"
  fi
else
  note_unver SDIST_INSTALL "venv 造不出来"
fi

# --------------------------------------------------------------------------
step "6) 把 README「装上就跑」那一节**逐字**跑一遍（用装上的 q2c，不读仓里任何脚本）"
QUICKSTART=FAIL
"$PY" - "$here" "$WORK/quickstart.sh" <<'PY' >"$LOG/qs-extract.log" 2>&1 || true
import re, sys
root, out = sys.argv[1], sys.argv[2]
text = open(root + "/README.md", encoding="utf-8").read()
sec = re.search(r"^##\s*装上就跑[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
if not sec:
    sys.exit("README 里没有「装上就跑」这一节")
blocks = re.findall(r"```(?:sh|bash)\n(.*?)```", sec.group(1), re.S)
run = [b for b in blocks if "export Q2C_HOME" in b]
if not run:
    sys.exit("「装上就跑」里没有那条以 export Q2C_HOME 开头的运行块")
open(out, "w", encoding="utf-8").write("\n".join(run))
print("extracted_blocks=%d" % len(run))
PY
if [ -f "$WORK/quickstart.sh" ]; then
  if PATH="$WV/bin:$PATH" sh "$WORK/quickstart.sh" >"$LOG/qs.log" 2>&1; then
    if grep -q '"state": "ACKED"' "$LOG/qs.log"; then
      QUICKSTART=ACKED
      say "${QUICKSTART}（README 那几条命令在干净 venv 里真跑通，末态读到了 ACKED）"
    else
      note_fail QUICKSTART "跑完了但输出里没有 ACKED，见 $LOG/qs.log"
    fi
  else
    note_fail QUICKSTART "$(tail -2 "$LOG/qs.log" | tr '\n' ' ')"
  fi
else
  note_fail QUICKSTART "$(tail -1 "$LOG/qs-extract.log" | tr '\n' ' ')"
fi

# --------------------------------------------------------------------------
step "7) 跑**要发出去的那份代码**：判据树里的 q2c/ 是从 wheel 里解出来的，不是工作树"
# 这一档第一版搭错过（2026-10-04 现跑抓到）：原本是"删掉源码 q2c/、让 import 落到
# site-packages"，结果 7 枚**读源码文本**的格（协议转移表 AST 扫描、文档引用、
# 动态版本号那一行、绝对解释器那条）全成 FileNotFoundError ——那是判据树搭错，
# 不是包有问题。现在改成用 `python -m zipfile` 把 wheel 里的 q2c/ 解进判据树：
# 这批判据读的就是要发出去的那些字节；"装完之后命令能不能跑"由第 4～6 档单独证。
SUITE_ON_INSTALLED=FAIL
if [ "$WHEEL_INSTALL" = OK ] && [ -d "$SRC/tests" ]; then
  cp -R "$SRC"/. "$SUIT"/
  rm -rf "$SUIT/q2c"
  if ! ( cd "$SUIT" && "$BV/bin/python" -m zipfile -e "$WHL" . ) >"$LOG/unzip.log" 2>&1; then
    note_fail SUITE_ON_INSTALLED "wheel 解不开：$(tail -1 "$LOG/unzip.log" | tr '\n' ' ')"
    verdict
  fi
  if [ ! -f "$SUIT/q2c/__init__.py" ]; then
    note_fail SUITE_ON_INSTALLED "解出来的 wheel 里没有 q2c/__init__.py⇒包是空的"
    verdict
  fi
  SUITE_RC=0
  if ! ( cd "$SUIT" && "$WV/bin/python" -W error::ResourceWarning \
            -m unittest discover -s tests -t . ) >"$LOG/suite.log" 2>&1; then
    SUITE_RC=1
  fi
  RAN=$(grep -E '^Ran [0-9]+ test' "$LOG/suite.log" | tail -1)
  if [ "$SUITE_RC" -eq 0 ] && grep -qE '^OK' "$LOG/suite.log"; then
    SUITE_ON_INSTALLED=OK
    say "${SUITE_ON_INSTALLED}（${RAN}；退 0 **且**有 OK 行才算，只看退出码会被 grep 糊住）"
    grep -E '^OK' "$LOG/suite.log" | tail -1 | sed 's/^/verdict_line=/'
  else
    note_fail SUITE_ON_INSTALLED "rc=$SUITE_RC [${RAN}]，见 $LOG/suite.log"
    grep -E '^(FAIL|ERROR): ' "$LOG/suite.log" | head -10
    tail -25 "$LOG/suite.log"
  fi
else
  note_unver SUITE_ON_INSTALLED "wheel 没装上或判据树不在，这一档没跑到"
fi

# --------------------------------------------------------------------------
step "8) pipx 安装（装成独立命令，不进任何已有环境）"
if command -v pipx >/dev/null 2>&1; then
  PIPX_RC=0
  if ! PIPX_HOME=$WORK/pipx-home PIPX_BIN_DIR=$WORK/pipx-bin \
       pipx install --quiet --force "$WHL" >"$LOG/pipx.log" 2>&1; then
    PIPX_RC=1
  fi
  if [ "$PIPX_RC" -eq 0 ] && [ -x "$WORK/pipx-bin/q2c" ] && \
     "$WORK/pipx-bin/q2c" --help >"$LOG/pipx-help.log" 2>&1; then
    PIPX=OK
    say "${PIPX}（pipx $(pipx --version 2>/dev/null) 独立环境，q2c 可直接调用）"
  else
    PIPX=FAIL
    note_fail PIPX "rc=$PIPX_RC $(tail -2 "$LOG/pipx.log" | tr '\n' ' ')"
  fi
else
  PIPX=UNVERIFIED
  note_unver PIPX "这台机器没有 pipx ⇒ 这一档不能折成通过"
fi

# --------------------------------------------------------------------------
step "9) 仓里没落下构建产物"
STRAY=$(find "$here" -maxdepth 2 \( -name dist -o -name build -o -name '*egg-info*' \) \
         -not -path "$here/pkg-out*" 2>/dev/null | head -3 || true)
if [ -z "$STRAY" ]; then
  REPO_CLEAN=OK
  say "${REPO_CLEAN}（构建全在临时目录里）"
else
  REPO_CLEAN=FAIL
  note_fail REPO_CLEAN "仓里出现构建产物：$STRAY"
fi

# --------------------------------------------------------------------------
step "10) 归档到 ${OUT}（本轮造的件＋日志，供发布记录对号；不进仓）"
mkdir -p "$OUT/dist"
cp "$WHL" "$SD" "$OUT/dist/" 2>/dev/null || true
for f in copy.log build.log twine.log help.log ver.log qs.log suite.log pipx.log; do
  cp "$LOG/$f" "$OUT/$f" 2>/dev/null || true
done
{
  printf 'as_of=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'hostname=%s\n' "$(hostname)"
  printf 'python=%s\n' "$("$PY" -V 2>&1)"
  printf 'wheel=%s sha256=%s\n' "$(basename "$WHL")" "$(digest "$WHL")"
  printf 'sdist=%s sha256=%s\n' "$(basename "$SD")" "$(digest "$SD")"
  printf 'head=%s\n' "$(git -C "$here" rev-parse HEAD 2>/dev/null || echo NO_GIT)"
} > "$OUT/identity.txt"

verdict
