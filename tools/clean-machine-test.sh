#!/bin/sh
# 清洁机复验（任务书 §16）：install → doctor → 配置适配器 → send → receive → trace。
#
# 口径：每一步都现读、都印 rc；跑不了的步骤**如实写 UNVERIFIED**，不折成通过。
# 这条脚本也是 CI 的门，所以它必须在一台"没配过任何东西"的机器上能自证。
set -u
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOME_DIR=$(mktemp -d)
LOG=$(mktemp)
say() { printf '%s\n' "$*"; }
mark() { printf '%-22s %s\n' "$1" "$2" | tee -a "$LOG"; }

export Q2C_HOME="$HOME_DIR"
cd "$here"

mark "root" "$HOME_DIR"

# 1) 安装路径两档：免安装（主路）与 pip（需 setuptools，缺则如实报）
if PYTHONPATH="$here" python3 -m q2c version >/dev/null 2>&1; then
  mark "install.no-pip" "OK（python3 -m q2c 可用）"
else
  mark "install.no-pip" "FAIL"
fi
if command -v ./bin/q2c >/dev/null 2>&1 && ./bin/q2c version >/dev/null 2>&1; then
  mark "install.bin/q2c" "OK"
else
  mark "install.bin/q2c" "FAIL"
fi
# 1c) pip 档：用 venv 自带的 pip（不要求机器预装 setuptools，也不要求 PATH 上有 pip）。
#     旧写法把这两条当"要不要试"的前提，结果 2026-10-04 macOS 托管 runner 明明装得上，
#     却被我自己的门报成 UNVERIFIED —— 那是门的错，不是环境的错。
#     装的时候先在临时目录里复制一份再 pip install：**不许在源根里构建**，
#     否则 setuptools 会往仓库落 build/ 与 *.egg-info/（本机复演一次就脏了两枚目录，
#     差点跟着冻结件走；下面第 6 段的"仓库干净"那格正是为了当场抓住它）。
V=$(mktemp -d)/venv
SRC=$(mktemp -d)/src
PIP_STATE=UNVERIFIED
if cp -R "$here" "$SRC" 2>/dev/null && python3 -m venv "$V" >>"$LOG" 2>&1; then
  if "$V/bin/pip" install -q "$SRC" >>"$LOG" 2>&1; then
    if "$V/bin/q2c" version >>"$LOG" 2>&1; then
      PIP_STATE=OK
    else
      PIP_STATE=FAIL
    fi
  fi
fi
if [ "$PIP_STATE" = "OK" ]; then
  mark "install.pip" "OK（venv 里 pip 装得上，入口能跑；构建在临时副本里，源根不脏）"
elif [ "$PIP_STATE" = "FAIL" ]; then
  mark "install.pip" "FAIL（装上了但入口跑不起来，见日志）"
else
  mark "install.pip" "UNVERIFIED（这台机建不了 venv 或 pip 装不上；不折成通过）"
fi

# 2) init + doctor
PYTHONPATH="$here" python3 -m q2c init >/dev/null 2>&1 && mark "q2c init" "rc=0" || mark "q2c init" "FAIL"
DOCTOR=$(PYTHONPATH="$here" python3 -m q2c doctor 2>&1); DRC=$?
mark "q2c doctor" "rc=${DRC}；ledger=$(printf '%s' "$DOCTOR" | python3 -c 'import json,sys;print(json.load(sys.stdin)["ledger"].get("state","ok"))' 2>/dev/null || echo UNREADABLE)"

# 3) 配置两侧适配器（默认在册适配器，不指定任何真 CLI）
S=$(PYTHONPATH="$here" python3 -m q2c sessions create --role sender --adapter loopback --label cm-s 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
R=$(PYTHONPATH="$here" python3 -m q2c sessions create --role receiver --adapter loopback --label cm-r 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
mark "adapters configured" "sender=$S receiver=$R"

# 4) send → receive → ACK
SEND=$(PYTHONPATH="$here" python3 -m q2c send --from "$S" --to "$R" --payload "清洁机复验：请回一句话。" 2>&1)
RID=$(printf '%s' "$SEND" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["request_id"])' 2>/dev/null)
ST=$(PYTHONPATH="$here" python3 -m q2c inspect "$RID" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["record"]["state"])' 2>/dev/null)
mark "send→receive" "request_id=$RID state=${ST:-UNREADABLE}"

# 5) trace 九问
Q=$(PYTHONPATH="$here" python3 -m q2c trace "$RID" 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)
need=("who_sent","who_received","when_delivered","when_started","which_session","what_response","when_acknowledged")
bad=[k for k in need if d.get(k) in (None,"NOT_OBSERVED","TRACE_ABSENT","UNVERIFIABLE")]
print("OK" if not bad else "MISSING:"+",".join(bad))')
mark "trace nine" "${Q:-UNREADABLE}"

# 6) 不留后台进程／不留临时状态在仓库里
#    清单里带上 build/ 与 *.egg-info/：`pip install .` 会在源根落下这两类东西，
#    上一版只数 state/mailbox/trace，于是本机复演一遍就把一堆可再生垃圾留在树里（差点进冻结件）。
LEFT=$(ls -d "$here"/state "$here"/mailbox "$here"/trace "$here"/build "$here"/*.egg-info 2>/dev/null | wc -l | tr -d ' ')
mark "repo stays clean" "仓库内运行态与构建产物目录数=${LEFT}（应为 0）"
# 解包件里没有 .git：这一格在那种环境下"测不到"，不许折成"改动数=0"这种像结论的数。
if git -C "$here" rev-parse --git-dir >/dev/null 2>&1; then
  CLEAN=$(git -C "$here" status --porcelain 2>/dev/null | grep -v '^??' | wc -l | tr -d ' ')
  mark "working tree" "已跟踪文件改动数=${CLEAN}"
else
  mark "working tree" "NO_GIT（这里不是 git 仓，测不到就不折成 0）"
fi

say ""
say "CLEAN_MACHINE_STATE=${ST:-UNREADABLE}"
say "日志：$LOG"
if [ "${ST:-}" != "ACKED" ]; then
  exit 1
fi
if [ "$PIP_STATE" = "FAIL" ]; then
  exit 1
fi
