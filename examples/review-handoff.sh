#!/bin/sh
# 一份可抄的派发文：请对侧独立核读某一笔提交。
#
# 这一条是**例子**，不是产品能力：q2c 只保证这条消息送到并留下跟踪，
# 不解释正文、不判定核读结果、不代替任何人批准或放行（PROTOCOL.md §0）。
# 把 payload 换成你自己的话术即可；要求对侧逐字回出的那两行由桥自己加。
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$here"
export PYTHONPATH="$here"
: "${Q2C_SENDER:?先 export Q2C_SENDER=<发起方 q2c 逻辑会话号>}"
: "${Q2C_RECEIVER:?先 export Q2C_RECEIVER=<接收方 q2c 逻辑会话号>}"
: "${Q2C_REPO:?仓库绝对路径}"
: "${Q2C_COMMIT:?提交号}"

python3 -m q2c send \
  --from "$Q2C_SENDER" --to "$Q2C_RECEIVER" \
  --type review-request \
  --workspace "$Q2C_REPO" --repo "$Q2C_REPO" --commit "$Q2C_COMMIT" \
  --artifact "git_commit=$Q2C_COMMIT" \
  --payload "请独立核读提交 $Q2C_COMMIT（不要相信转述，自己跑只读命令）：
1) 这笔提交改了哪些文件；
2) 声称的门禁退出码与实跑是否一致；
3) 有没有放宽或删除测试来凑绿；
4) 是否残留未提交改动。
请用一段话给出你实际跑过的命令与读数。"

echo "跟踪查询：python3 -m q2c trace <上面回显的 request_id>"
