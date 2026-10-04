#!/bin/sh
# 本机**复演**独立复验（不是异机证据，别拿它当那一格）。
#
# 为什么要有这么一枚叶子件：独立 CI 连着三轮红，前两轮的拒因（shell 方言、按 mtime 挑报告）
# 都只在 `git archive` 解出来的那种树上才现形——那棵树没有 `.git`、所有 mtime 都一样、
# 也没有 `evidence/`。在开发机上跑整包判据是看不见的。每次烧一轮 CI 才学一课，太贵。
# 这条脚本把同样的形状在本地先跑一遍：从提交打包 → 解到全新目录 → 跑判据 → 跑清洁机门。
#
# 边界（写死）：
#   · 只往临时目录写，不碰仓，不碰 evidence/；
#   · 独立性的判定改由 `REHEARSAL=1` 明说：脚本会写 `ENVIRONMENT=REHEARSAL_ON_DEV_MACHINE`，
#     而**不是**去伪造 RUNNER_ENVIRONMENT（真托管作业还要 `GITHUB_RUN_ID` 才算数，
#     单靠那枚变量在开发机上也能设出来——这条门就是这么被自己绕开过一次才补严的）；
#   · 退出码＝被复演的作业退出码，红就是红。
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REF=${1:-$(git -C "$here" rev-parse HEAD)}
OUT=$(mktemp -d)/pkg-rehearsal
mkdir -p "$OUT"
say() { printf '%s\n' "$*"; }
say "== 本机复演（REHEARSAL，不是异机证据）ref=${REF}"
say "   输出目录：$OUT"
cd "$here"
REHEARSAL=1 OUT_DIR="$OUT" /bin/sh tools/ci-clean-machine.sh "$REF"
rc=$?
say ""
say "REHEARSAL_DONE rc=${rc} out=${OUT}"
say "REHEARSAL_NOT_INDEPENDENT_EVIDENCE=yes（hostname 看 $(cat "$OUT/environment-identity.txt" 2>/dev/null | sed -n 's/^hostname=//p')）"
exit "$rc"
