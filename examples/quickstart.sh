#!/bin/sh
# Quick Start：60 秒跑通一次完整交接（零模型调用、零外部依赖、不需要 pip）。
#
#   sh examples/quickstart.sh
#
# 它做的事与 README 里写的一字不差：init → doctor → 登记两侧逻辑会话 → send →
# inspect → trace → list。跑的是随包的 `inproc` 适配器（同进程应答），
# 所以这台机器上可以既没有 Codex 也没有 Qoder。
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
Q2C="$here/bin/q2c"
HOME_DIR=$(mktemp -d)
export Q2C_HOME="$HOME_DIR"
export PYTHONPATH="$here"

echo "== 1) q2c init（建状态根与默认配置）"
python3 -m q2c init

echo "== 2) q2c doctor（只读体检：环境／适配器／凭据可用性／台账可读性）"
python3 -m q2c doctor | python3 -c 'import json,sys; d=json.load(sys.stdin); print({"home_writable": d["home_writable"], "ledger": d["ledger"].get("state", "ok"), "adapters": sorted(d["adapters"]), "secret_scan": d["secret_scan"]})'

echo "== 3) 登记两侧逻辑会话（发起方／接收方）"
SENDER=$(python3 -m q2c sessions create --role sender --adapter inproc --label quickstart-s | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
RECEIVER=$(python3 -m q2c sessions create --role receiver --adapter inproc --label quickstart-r | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
echo "  sender   = $SENDER"
echo "  receiver = $RECEIVER"

echo "== 4) 交出一件事（send 会推这一腿到闭环）"
python3 -m q2c send --from "$SENDER" --to "$RECEIVER" \
  --type review-request \
  --payload "请把这件事接手过去：回一句话说明你收到了这一笔。"

echo "== 5) 查这一笔落到哪一步（inspect＝读数，不判定）"
RID=$(python3 -m q2c list | python3 -c 'import json,sys;print(list(json.load(sys.stdin)["records"].keys())[0])')
python3 -m q2c inspect "$RID" | python3 -c 'import json,sys;d=json.load(sys.stdin);print({"state": d["record"]["state"], "result_sha256": d["record"]["result"].get("sha256","")[:12]})'

echo "== 6) 跟踪九问（通信事实，不是项目完成事实）"
python3 -m q2c trace "$RID" | python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:d[k] for k in ("who_sent","who_received","when_delivered","when_started","when_acknowledged")})'

echo
echo "QUICKSTART_STATE=$(python3 -m q2c inspect "$RID" | python3 -c 'import json,sys;print(json.load(sys.stdin)["record"]["state"])')"
echo "QUICKSTART_HOME=$Q2C_HOME（临时根，用完可删；q2c 不常驻、不留后台进程）"
