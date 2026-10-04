#!/usr/bin/env python3
"""适配器共用的"起一次外部调用并把落盘件与退出码留住"的两拍工装。

三个设计点都是踩出来的，别在单个适配器里各写一套：

1. **退出码走侧件**（`<capture>.out.rc`）。父进程被杀之后，子进程可能还在跑、
   最后跑完了也没人 `communicate()` 它；这时唯一能证明"它退了几"的就是它自己写完的侧件。
2. **绝对解释器包装**（`/bin/zsh -c`）。裸 `zsh` 在受限 PATH 下起不来 ⇒ 捕获件是空的 ⇒
   很容易被读成"对方没回话"（在册收尾项，产品里就地做掉）。
3. **到点不杀**。返回 `IN_FLIGHT`，让上层决定等还是取消；超时即杀并记失败，
   实测会把同一个请求推成两份对象、两枚绑定串、两条互相打脸的答复。
"""

from __future__ import annotations

import atexit
import os
import shlex
import subprocess
import time

from .. import security

IN_FLIGHT = "IN_FLIGHT"
DONE = "DONE"
NO_RC = "NO_RC_RECORD"

# 本进程起过、但还没回收的孩子。到点不杀 ⇒ 我们**故意**不等它，
# 那它退出后就会挂在进程表里当僵尸；长活的调用者会越攒越多。
# 所以下面每一条出口都先试着收一次（非阻塞）。
_LIVE = {}          # pid → Popen：本进程起过、还没收到退出码的孩子


def detach(p):
    """我们**故意**不等的孩子：把 Popen 对象与这个责任解绑。

    `Popen.__del__` 见到"还活着却没有 returncode"就报警。这不是我们的错——
    到点不杀是协议规定（杀了会把同一请求推成两份对象两枚绑定串）。
    解绑对象不等于放弃回收：pid 还留在 `_UNREAPED` 里，后续 `reap()` 用裸
    `waitpid(WNOHANG)` 照样能收，进程表不会攒僵尸。
    """
    if p is None:
        return
    # `Popen.__del__` 的报警条件是"returncode 还是 None"。我们已经把管道关掉、
    # 也确定不再对这个孩子负责（回收走 _UNREAPED＋裸 waitpid），所以这里声明
    # "这个对象不再拥有孩子"——比伪造一个 returncode 干净，也比留着报警正确。
    try:
        p._child_created = False
    except Exception:
        pass


def reap(pid):
    """尽力回收一枚本进程起过的孩子（非阻塞）。收不到不算错——它可能还在跑。

    走 `Popen.poll()` 而不是裸 `waitpid`：poll 会把退出码落回对象本身，
    于是解释器回收时不会对着"我们故意没等"的孩子报 ResourceWarning，
    也不会出现"我们收过、对象却以为孩子是活的"这种两处认知不一致。
    """
    if pid in (None, ""):
        return False
    p = _LIVE.get(int(pid)) if str(pid).lstrip("-").isdigit() else None
    if p is None:
        return False
    try:
        rc = p.poll()
    except OSError:
        rc = 0
    if rc is None:
        return False
    _LIVE.pop(int(pid), None)
    return True


def reap_all():
    """把本进程起过、还没收的孩子各试一次。长活的调用方靠它不攒僵尸。"""
    for pid in list(_LIVE.keys()):
        reap(pid)


def _release_on_exit():
    """退出兜底：收得掉的收掉；收不掉的声明"这个对象不再拥有孩子"。

    到点不杀是协议规定，所以"还活着的孩子"是正常状态，
    不该在解释器拆解阶段变成一屏 ResourceWarning。
    """
    for pid, p in list(_LIVE.items()):
        if not reap(pid):
            detach(p)
    _LIVE.clear()


atexit.register(_release_on_exit)


def spawn(argv: list, env: dict, capture: str, timeout_s: float,
          cwd: str = "") -> tuple:
    """起一次调用并等它收口。返回 (phase, rc, pid, pid_start)。

    phase ∈ {DONE, IN_FLIGHT}；DONE 时 rc 从侧件读（读不到 ⇒ None，**不折成 0**）。

    这里刻意不用 `Popen.communicate(timeout=...)`：到点不杀是产品规定，
    而留下一个 `returncode is None` 的 Popen 对象，解释器回收时会在 stderr 上
    报 "subprocess is still running"（ResourceWarning），更糟的是它暗示"我们
    以为自己还拥有这个孩子"。改成自己轮询＋`waitpid(WNOHANG)`：
      · 父进程不欠任何人一个 reap（长活的桥不会攒僵尸进程）；
      · 到点就返回 IN_FLIGHT，对象与子进程各走各的，靠退出码侧件会合。
    """
    os.makedirs(os.path.dirname(capture), exist_ok=True)
    rc_path = rc_path_of(capture)
    # 上一拍的原件**改名留住**再写新的：失败证据不许被下一次成功覆盖
    security.refuse_overwrite(capture)
    security.refuse_overwrite(rc_path)
    inner = " ".join(shlex.quote(str(a)) for a in argv)
    # POSIX 语法写侧件（`print -r --` 是 zsh 专有，bash/sh 上是另一种脾气）：
    # 先写临时侧件再 mv，保证读到的 rc 件永远是完整一行。
    wrapper = ("%s\nrc=$?\ntmp=%s.tmp\nprintf '%%s\\n' \"$rc\" > \"$tmp\"\n"
               "mv -f \"$tmp\" %s\nexit $rc" % (inner, shlex.quote(rc_path), shlex.quote(rc_path)))
    shell = security.posix_shell()
    fh = open(capture, "w", encoding="utf-8")
    kw = {"cwd": cwd} if cwd else {}
    p = security.popen_group([shell, "-c", wrapper], stdout=fh, stderr=subprocess.STDOUT,
                             text=True, env=env, **kw)
    fh.close()
    pid, pid_start = p.pid, security._proc_start_marker(p.pid) or ""
    _LIVE[pid] = p
    deadline = time.time() + max(float(timeout_s), 0.05)
    while True:
        reaped = reap(pid)
        if reaped and os.path.isfile(rc_path):
            return DONE, read_rc(capture), pid, pid_start
        if reaped and not os.path.isfile(rc_path):
            # 孩子没了却写不出退出码侧件：宁可报「没有 rc」，也不补一个 0 上去
            return DONE, None, pid, pid_start
        if time.time() >= deadline:
            # 故意不等它：对象留在 _LIVE 里（有人引用就不会被回收），
            # 下一次 reap()／退出兜底再收。
            return IN_FLIGHT, None, pid, pid_start
        time.sleep(0.02)


def read_rc(capture: str):
    """从侧件取退出码。缺件／非整数 ⇒ None（未知），绝不返回 0。"""
    rc_path = rc_path_of(capture)
    if not os.path.isfile(rc_path):
        return None
    try:
        with open(rc_path, encoding="utf-8") as fh:
            raw = (fh.read() or "").strip()
    except OSError:
        return None
    if not raw or not raw.lstrip("-").isdigit():
        return None
    return int(raw)


def collect(capture: str) -> tuple:
    """不重投的前提下把已落盘的那一腿收回来。返回 (rc, raw_text)。

    捕获件不在 ⇒ 抛，不返回空串（空串会被下游读成"对方回了个空"）。
    """
    if not os.path.isfile(capture):
        raise FileNotFoundError(capture)
    with open(capture, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    return read_rc(capture), raw


def rc_path_of(capture: str) -> str:
    return capture + ".rc"
