#!/usr/bin/env python3
"""桩应答器：一个**真子进程**，按环境变量决定这份回执长什么样。

为什么要有它（而不是在测试里直接构造 `Receipt` 对象）：
产品里最贵的几条性质——到点整组清理、迟到答复、失败回执不被覆盖、退出码说话——
都只有用**真进程**才测得到。`tests` 里那些"摘掉某道闸必须变红"的反证，
一半跑在这个应答器上（另一半跑在 Codex/Qoder 的真件夹具上）。

它**不是**产品的一部分，也**不许**被生产路径调用：
`capabilities()["live"] is False`，且 README 里明写它是 Quick Start 与判据用的假对侧。

模式（`Q2C_LB_MODE`）：

    ack            合规回执：结构化、自报成功、会话号相等、整行 ACKED、逐字回出绑定串
    no-ack         缺确认行（正文里出现 ACKED 字样也不算）
    wrong-session  会话号是别人的
    self-error     is_error=true／subtype=error（退出码照旧 0）
    fail-exit      非零退出（默认 7），stdout 同样带着 ACKED 字样
    sleep          睡过等待窗口（测"到点不杀 ⇒ 下拍收回"与进程组清理）
    no-json        退 0 但只打散文（测"退 0 ≠ 送达"）
    tail-bind      绑定串写在 JSON 之外（测"外面补一行不算"，R17 第 4 条）
"""

import json
import os
import sys
import time  # noqa: 需要现成时钟给调用计数打时刻

from . import ack as A

#: 假对侧能回出的回执形状，**唯一名单**：`loopback` 适配器 import 它。
#: 这条名单先前在两个文件里各有一份，加一档忘同步就会漂（今天漂过两次）。
MODES = ("ack", "result", "protocol-lines-only", "no-ack", "wrong-session", "self-error",
         "fail-exit", "sleep", "no-json", "tail-bind")


def main(argv=None) -> int:
    # 命令行只取一个用途：让 `ps` 能认出这是哪一笔的哪一腿（--rid <请求号>）。
    # 值本身仍以环境变量为准，桩件不许有第二套输入通道。
    mode = (os.environ.get("Q2C_LB_MODE", "ack") or "ack").strip()
    sid = os.environ.get("Q2C_LB_SESSION", "")
    bind = os.environ.get("Q2C_LB_BIND", "")
    secs = float(os.environ.get("Q2C_LB_SLEEP", "0") or 0)
    rid = os.environ.get("Q2C_LB_RID", "")
    out = sys.stdout
    # 调用计数：这一腿到底被跑了几次，是"重投＝重投消息、不重跑任务"唯一的直接量具。
    # 数信箱文件不行——投两次只写一份派发文；必须数**应答器真起过几次**。
    calls = (os.environ.get("Q2C_LB_CALLS", "") or "").strip()
    if calls:
        try:
            os.makedirs(os.path.dirname(calls), exist_ok=True)
            with open(calls, "a", encoding="utf-8") as cf:
                # 字段：pid mode rid session ts。分腿计数要按 session 分，
                # 否则"结果腿又投了一次"会被读成"对侧又被跑了一次"——那是两回事。
                cf.write("%s %s %s %s %s\n" % (os.getpid(), mode, rid, sid,
                                                int(time.time() * 1000)))
        except OSError:
            pass

    def emit(body: str, is_error=False, subtype="success", session_id=None):
        rec = {
            "type": "result",
            "is_error": is_error,
            "subtype": subtype,
            "session_id": sid if session_id is None else session_id,
            "result": body,
        }
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()

    if mode == "sleep":
        # 睡过窗口再回：父进程"到点不杀"才谈得上收回这一格。
        # 只有 sleep 模式才睡——过去把 sleep_s 做成全局，另一条本该秒回的腿
        # 也被拖慢，于是一个场景测出了它根本没打算测的东西（结果腿变 IN_FLIGHT）。
        time.sleep(secs or 30)
        emit("答复来晚了\n%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN))
        return 0

    body = "已读这一笔（请求号 %s）。理由：桩应答器按模式 %s 产出回执。\n" % (rid or "?", mode)
    if mode == "ack":
        emit(body + "%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN))
        return 0
    if mode == "result":
        # 答复形状：有正文、回出绑定行，但**不**回确认行（结果腿才要确认行）
        emit(body + "这条答复的内容在这里，长度足够成一句人话，不是空口令复述。\n%s %s\n"
             % (A.BIND_PREFIX + ":", bind))
        return 0
    if mode == "protocol-lines-only":
        # 对侧**只**回规定的两行（真腿上就出现过这种最简合规答复；见 PROTOCOL §4.2 末段）
        emit("%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN))
        return 0
    if mode == "no-ack":
        emit(body + "（正文里出现过 %s 这个词，但没有独立整行）\n" % A.ACK_TOKEN)
        return 0
    if mode == "wrong-session":
        emit(body + "%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN),
             session_id="other-session-0000")
        return 0
    if mode == "self-error":
        emit(body + "%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN),
             is_error=True, subtype="error_during_execution")
        return 0
    if mode == "fail-exit":
        emit(body + "%s: %s\n%s\n" % (A.BIND_PREFIX, bind, A.ACK_TOKEN))
        return int(os.environ.get("Q2C_LB_RC", "7") or 7)
    if mode == "no-json":
        out.write("我看过这一笔了，觉得没问题。\n")
        return 0
    if mode == "tail-bind":
        # 绑定串写在 JSON 之外的物理行上，JSON 内部没有 ⇒ 应当不被采信
        out.write("%s %s\n" % (A.BIND_PREFIX + ":", bind))
        emit(body + A.ACK_TOKEN + "\n")
        return 0
    emit(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
