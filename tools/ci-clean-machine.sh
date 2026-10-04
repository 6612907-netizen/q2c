#!/bin/sh
# 独立干净环境复验（主理人 2026-10-04 裁定第 3 条）：
#   对**冻结提交打出的源码包**做清洁机复验，并留下环境身份、包的 SHA256、完整日志、末行状态。
#
# 三条硬要求写进代码，不靠自觉：
#   1. 被测物是 `git archive <ref>` 生成的 tar.gz（不是工作树、不是这台机器的目录）；
#   2. 环境身份必须在跑之前落文件（hostname／镜像／python／是否 hosted runner）；
#      当前开发机上另一个目录**不算**异机证据 —— 所以这里显式检查"是不是 CI 托管环境"，
#      不是就报 `ENVIRONMENT=NOT_INDEPENDENT` 并非零退出，不给自己留蒙混的口子；
#   3. 末行 `CLEAN_MACHINE_STATE` 必须为 ACKED，否则整个作业红。
set -eu
REF=${1:-${GIT_SHA:-HEAD}}
REF_SRC=arg
if [ -n "${GIT_SHA:-}" ]; then
  REF_SRC=env.GIT_SHA
fi
if [ "$REF" = "HEAD" ]; then
  REF_SRC=worktree.HEAD
fi
OUT=${OUT_DIR:-$(pwd)/clean-machine-out}
mkdir -p "$OUT"
S=$OUT/environment-identity.txt
{
  echo "as_of=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "hostname=$(hostname)"
  echo "uname=$(uname -a)"
  echo "os_release=$(cat /etc/os-release 2>/dev/null | head -2 | tr '\n' ' ' || sw_vers 2>/dev/null | tr '\n' ' ')"
  echo "python=$(python3 -V 2>&1)"
  echo "whoami=$(id -un)"
  echo "runner=${RUNNER_ENVIRONMENT:-unset}  os=${RUNNER_OS:-unset}  arch=${RUNNER_ARCH:-unset}"
  echo "run_id=${GITHUB_RUN_ID:-unset}  rehearsal=${REHEARSAL:-0}"
  echo "sha=${REF}  ref_source=${REF_SRC}"
  echo "cwd=$(pwd)"
  echo "cpu=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2-)"
} > "$S" 2>&1 || true
cat "$S"

if [ "${REHEARSAL:-0}" = "1" ]; then
  echo "ENVIRONMENT=REHEARSAL_ON_DEV_MACHINE（本机复演，明确不算异机证据）"
elif [ "${RUNNER_ENVIRONMENT:-}" = "github-hosted" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
  echo "ENVIRONMENT=INDEPENDENT_GITHUB_HOSTED run_id=${GITHUB_RUN_ID}"
elif [ "${RUNNER_ENVIRONMENT:-}" = "github-hosted" ]; then
  # 光有 RUNNER_ENVIRONMENT 不够：那枚变量在开发机上也能被随手设出来（我自己就这么复演过）。
  # 真托管作业必然带 GITHUB_RUN_ID，所以缺它就按"不是独立环境"处理，不给自己留蒙混的口子。
  echo "ENVIRONMENT=NOT_INDEPENDENT（RUNNER_ENVIRONMENT 有但缺 GITHUB_RUN_ID ⇒ 不可信）"
  exit 1
else
  if [ "$(cat /sys/class/dmi/product_name 2>/dev/null || true)" = "Virtual Machine" ] || \
       grep -qa docker /proc/1/cgroup 2>/dev/null; then
       echo "ENVIRONMENT=INDEPENDENT_OTHER_VM"
     else
       echo "ENVIRONMENT=NOT_INDEPENDENT（这台开发机不算异机证据）"
       exit 1
     fi
fi

PKG=q2c-v0.1.0-source.tar.gz
echo "== 1) 从冻结提交构建发布包（git archive，不读工作树）"
# 打包范围＝**发布域**：`evidence/` 不随源码包走。两条理由：
#   · 发布物清单（evidence/SHA256SUMS-v0.1.0.txt）的收录规则就是"除 evidence/"，
#     包里却装着它，等于清单描述的东西比包少；
#   · 那些件是我这台开发机的现场（含 <外部卷>/… 绝对路径、会话号、额度读数），
#     不是别人装 q2c 需要的东西。判据在没证据件的包照跑，只把证据相关那几格如实 skip。
git archive --format=tar.gz --prefix=q2c/ -o "$OUT/$PKG" "$REF" -- . ':(exclude)evidence' ':(exclude)Q2C-v0.1.0-RELEASE-REPORT.md'
FILES=$(tar -tzf "$OUT/$PKG" | sed -n '/\/$/!p' | wc -l | tr -d ' ')
echo "package_files=${FILES}（发布域＝包里的件，应与发布物清单条目数一致）"
SHA=$(shasum -a 256 "$OUT/$PKG" | awk '{print $1}')
echo "$SHA  $PKG" > "$OUT/$PKG.sha256"
cat "$OUT/$PKG.sha256"

echo "== 2) 解到全新目录（与仓库工作树无关联）"
WORK=$(mktemp -d)
tar -xzf "$OUT/$PKG" -C "$WORK"
ls -la "$WORK/q2c" | head -5

echo "== 3) 在解包件里跑判据（独立环境的第二重证明：不依赖开发机任何残留）"
# 退出码必须**单独接**：`... | tee` 会把 rc 换成 tee 的 0，判据红了作业照样绿
# （在册同一族：验牙工装用 `| tail -6` 时，15 枚变异全被读成"没变红"）。
TESTS_RC=0
( cd "$WORK/q2c" && python3 -m unittest discover -s tests -t . ) >"$OUT/tests.raw" 2>&1 || TESTS_RC=$?
tail -4 "$OUT/tests.raw" | tee "$OUT/tests.log"
echo "tests_rc=$TESTS_RC"
if [ "$TESTS_RC" != 0 ]; then
  echo "独立环境里判据没全绿 ⇒ 不复验了"
  exit 1
fi
grep -qE '^OK' "$OUT/tests.raw" || { echo "判据输出里没有 OK 行 ⇒ 这格不算成立"; exit 1; }

echo "== 4) 跑清洁机复验（install→doctor→配置适配器→send→receive→trace）"
CM_RC=0
( cd "$WORK/q2c" && sh tools/clean-machine-test.sh ) >"$OUT/clean-machine.log" 2>&1 || CM_RC=$?
cat "$OUT/clean-machine.log"
echo "clean_machine_rc=$CM_RC"

echo "== 5) pip 安装档（独立环境里能不能真装上；装不上就明写 UNVERIFIED）"
# 这一段以前有两个毛病：① 把 `&&` 写在行首 —— macOS 与 ubuntu 的 sh 都在**解析期**拒
# （`syntax error near unexpected token '&&'`，2026-10-04 两枚 job 各红在这里）；
#  ② 要求机器预装 setuptools 且 PATH 上有 pip —— 托管 runner 明明能用 venv 自带的 pip 装上，
#     却被我自己判成 UNVERIFIED。现在直接用 venv 自带的 pip，装不上才如实报。
PIP_STATE=UNVERIFIED
V="$WORK/venv"
if python3 -m venv "$V" >"$OUT/pip-install.log" 2>&1; then
  if "$V/bin/pip" install -q "$WORK/q2c" >>"$OUT/pip-install.log" 2>&1; then
    if "$V/bin/q2c" version >>"$OUT/pip-install.log" 2>&1; then
      PIP_STATE=OK
    else
      PIP_STATE=FAIL
    fi
  fi
fi
echo "PIP_INSTALL=$PIP_STATE" | tee -a "$OUT/pip-install.log"
if [ "$PIP_STATE" = "FAIL" ]; then
  echo "pip 装上了但入口跑不起来 ⇒ 这格算失败"
  exit 1
fi

echo "ARTIFACT_SHA256=$SHA"
echo "ARTIFACT=$PKG"
echo "REF=$REF  ref_source=$REF_SRC"
if [ "$CM_RC" != 0 ]; then
  echo "清洁机复验退出码=$CM_RC ⇒ 这格不判通过（不靠末行文本自救）"
  exit 1
fi
grep -E "^CLEAN_MACHINE_STATE=" "$OUT/clean-machine.log" || { echo "末行状态缺失"; exit 1; }
grep -q "^CLEAN_MACHINE_STATE=ACKED$" "$OUT/clean-machine.log" || { echo "复验未过"; exit 1; }
echo "CI_CLEAN_MACHINE=PASS"
