#!/bin/sh
# 提交前的在册门：一条命令，退出码说真话。
#
# 为什么要有它（2026-10-04 20:3x 的真实自伤）：我把一串动作写成
# `python3 -m unittest discover … | grep -E "^Ran |^OK|^FAILED" && git add … && git commit …`
# ——`grep` 在**匹配到 FAILED** 时照样退 0，于是这条链在判据红着的情况下继续走下去并提交了
# （那一笔 a14fd5c 后来把 staging 的全矩阵 CI 判红）。工具没错，错在我拿"看见了红"当"门拦住了"。
# 门要拦，就得是**退出码**，不是一段能被人眼漏掉的输出。
#
# 用法：
#   sh tools/gate-commit.sh          # 全量判据 ＋ smoke
#   SHALLOW=1 sh tools/gate-commit.sh  # 只跑判据（改文档时想快一档；仍不降标准）
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$here"
say() { printf '%s\n' "$*"; }
fail() { say "GATE_COMMIT=FAIL $*"; exit 1; }

RC_TESTS=0
python3 -W error::ResourceWarning -m unittest discover -s tests -t . > /tmp/q2c-gate-tests.$$.log 2>&1 || RC_TESTS=$?
tail -3 /tmp/q2c-gate-tests.$$.log | sed 's/^/  判据: /'
[ "$RC_TESTS" = 0 ] || fail "判据退出码=${RC_TESTS}（不许带着红提交）"
grep -qE '^OK' /tmp/q2c-gate-tests.$$.log || fail "判据输出里没有 OK 行"

if [ "${SHALLOW:-0}" != "1" ]; then
  RC_SMOKE=0
  python3 tools/smoke-cli.py > /tmp/q2c-gate-smoke.$$.log 2>&1 || RC_SMOKE=$?
  tail -1 /tmp/q2c-gate-smoke.$$.log | sed 's/^/  smoke: /'
  [ "$RC_SMOKE" = 0 ] || fail "smoke 退出码=${RC_SMOKE}"
  grep -q 'SMOKE_RESULT=ACKED' /tmp/q2c-gate-smoke.$$.log || fail "smoke 没到 ACKED"
  rm -f /tmp/q2c-gate-smoke.$$.log
fi
rm -f /tmp/q2c-gate-tests.$$.log
say "GATE_COMMIT=PASS"
