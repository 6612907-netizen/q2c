#!/bin/sh
# 取证工装：从**公开 PyPI** 全新安装 q2c，并把"装完到 ACKED"整条路径连输出一起留档。
# 只在临时目录里动（PIPX_HOME／PIPX_BIN_DIR／Q2C_HOME 都是新造的），不碰任何仓。
set -eu
OUT=${1:?用法：sh tools/pypi-install-evidence.sh <输出文件>}
PY3=${Q2C_PYTHON:-$(command -v python3)}
W=$(mktemp -d)
export PIPX_HOME=$W/pipx-home
export PIPX_BIN_DIR=$W/pipx-bin
{
  echo "# 从公开 PyPI 全新环境安装并跑到 ACKED（发布验收路径）"
  echo "python=$("$PY3" -V 2>&1)  pipx=$(pipx --version 2>/dev/null || echo MISSING)"
  date "+as_of=%Y-%m-%d %H:%M:%S %z"
  echo
  echo "\$ pipx install q2c          # PIPX_HOME／PIPX_BIN_DIR 都指向全新临时目录"
  # 不写 `pipx install … | tail`：管道会把退出码换成 tail 的 0（在册老坑），
  # 那样"没装上"也能被这份取证读成装上了。先落文件，成败看真退出码。
  if ! pipx install q2c > "$W/pipx.log" 2>&1; then
    echo "PIPX_INSTALL=FAIL"
    tail -8 "$W/pipx.log"
    exit 1
  fi
  echo "PIPX_INSTALL=OK"
  tail -6 "$W/pipx.log"
  echo
  echo "\$ q2c --help | head -3"
  "$PIPX_BIN_DIR/q2c" --help 2>&1 | head -3
  echo "help_rc=$?"
  echo
  echo "\$ q2c version"
  "$PIPX_BIN_DIR/q2c" version
} > "$OUT" 2>&1

# Quick Start 段单独跑（PATH 里只有刚装上的那枚 q2c，cwd 不在任何仓里）
{
  echo
  echo "\$ export Q2C_HOME=\$(mktemp -d); q2c init; q2c sessions create ×2 --adapter loopback; q2c send"
  cd "$W"
  export PATH="$PIPX_BIN_DIR:$PATH"
  export Q2C_HOME=$W/home
  q2c init > /dev/null
  S=$(q2c sessions create --role sender --adapter loopback --label demo-s |
      "$PY3" -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
  R=$(q2c sessions create --role receiver --adapter loopback --label demo-r |
      "$PY3" -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
  echo "sender=$S receiver=$R"
  OUT_JSON=$(q2c send --from "$S" --to "$R" \
      --payload "请把这件事接手过去：回一句话说明你收到了这一笔。")
  echo "$OUT_JSON"
  RID=$(printf '%s' "$OUT_JSON" | "$PY3" -c 'import json,sys;print(json.load(sys.stdin)["request_id"])')
  echo
  echo "\$ q2c trace ${RID}（九问读数，节选）"
  q2c trace "$RID" | "$PY3" -c '
import json, sys
d = json.load(sys.stdin)
for k in ("state_now", "who_sent", "who_received", "when_acknowledged", "what_response"):
    v = d.get(k)
    print("%s = %s" % (k, json.dumps(v, ensure_ascii=False)[:120]))
'
} >> "$OUT" 2>&1
echo "EVIDENCE_WRITTEN=$OUT"
