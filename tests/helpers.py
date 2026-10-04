#!/usr/bin/env python3
"""判据工装：临时根、应答器调用计数、把一腿推到某个状态。

这里只放**测量工具**，不放判据。判据一律在各 `test_*.py` 里。
一条纪律（在册老坑）：工装不许自己造出它要测的输入——
所有交接单都由被测模块（`protocol.make_request`／`Transport.create`）真造，
工装只提供家目录、时钟读数与计数器。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from q2c import protocol, transport, adapters  # noqa: E402


class TempHome:
    """一个干净的 q2c 根。用完即删（只删自己创建的那枚临时目录）。"""

    def __init__(self, mode="ack", **cfg_over):
        self.home = tempfile.mkdtemp(prefix="q2c-t-")
        self.calls = os.path.join(self.home, "state", "responder-calls.log")
        cfg = {"home": self.home, "mode": mode, "calls_log": self.calls}
        cfg.update(cfg_over)
        cfg_over = dict(cfg_over or {})
        cfg_over.setdefault("calls_log", self.calls)   # 计数件必须真的进 adapters.loopback
        self.cfg = cfg
        os.makedirs(os.path.join(self.home, "state"), exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as fh:
            fh.write(_config_json(mode, cfg_over))
        adapters.import_builtin()

    def close(self):
        # 收一次在飞的孩子（非阻塞）：判据进程不许把子进程漏给下一格，
        # 也不许在解释器退出时对着"到点不杀"的孩子报警——那是规定，不是错误
        try:
            from q2c.adapters import _spawn
            _spawn.reap_all()
        except Exception:
            pass
        shutil.rmtree(self.home, ignore_errors=True)

    # ---- 计数与读数 --------------------------------------------------------

    def responder_calls(self, rid: str = "", session: str = "") -> int:
        """应答器真起过几次进程。

        可按请求号与**会话号**过滤：同一笔交接的两条腿共用一个 rid，
        但 provider 号不同——不分会话就没法证明"重投没重跑对侧"。
        """
        if not os.path.isfile(self.calls):
            return 0
        n = 0
        with open(self.calls, encoding="utf-8") as fh:
            for ln in fh:
                parts = ln.split()
                if len(parts) < 4:
                    continue
                if rid and parts[2] != rid:
                    continue
                if session and parts[3] != session:
                    continue
                n += 1
        return n

    def provider_of(self, t, logical: str) -> str:
        return t.sessions.get(logical).current()

    def transport(self) -> transport.Transport:
        from q2c import config as config_mod
        return transport.Transport(self.home, config_mod.load(self.home))

    def sessions(self, sender_adapter="loopback", receiver_adapter="loopback"):
        t = self.transport()
        s = t.sessions.create("sender", sender_adapter, label="t-sender")
        r = t.sessions.create("receiver", receiver_adapter, label="t-receiver")
        return t, s.session_id, r.session_id

    def request(self, **over):
        kw = dict(sender="qs-00000000000000000001", receiver="qs-00000000000000000002",
                  session_id="qs-00000000000000000001", handoff_type="handoff",
                  payload="请把这件事接手过去，回一句话就行（判据用的固定正文）。")
        kw.update(over)
        return protocol.make_request(**kw)

    def read(self, rid):
        return self.transport().read(rid).get("record")

    def state(self, rid):
        rec = self.read(rid)
        return (rec or {}).get("state")

    def tree_sha(self):
        """整棵树的哈希集合（测"只读动作零写入"用）。"""
        import hashlib
        rows = []
        for dirpath, dirnames, filenames in os.walk(self.home):
            dirnames.sort()
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, self.home)
                if rel.startswith("state" + os.sep + "ledger.lock"):
                    continue       # 锁文件允许被 flock 碰（它是锁的本体，不是被观测数据）
                try:
                    with open(p, "rb") as fh:
                        rows.append("%s\t%s" % (rel, hashlib.sha256(fh.read()).hexdigest()))
                except OSError:
                    rows.append("%s\t<unreadable>" % rel)
        return "\n".join(rows)


LB_KEYS = ("mode", "sleep_s", "rc", "calls_log", "timeout_s")


def _config_json(mode, over) -> str:
    """写临时根的配置。

    模式与计数文件走 `adapters.loopback`（**不是环境变量**）：
    用 env 会被继承进子测试进程，一格配错整批跟着假绿／假红。
    """
    import json
    from q2c import config as config_mod
    doc = dict(config_mod.DEFAULTS)
    over = dict(over or {})
    doc.update({k: v for k, v in over.items()
                if k in ("delivery_attempts_max", "receive_timeout_s", "allowed_workspaces",
                         "notification_cmd", "notify_timeout_s")})
    doc.setdefault("receive_timeout_s", 20)
    lb = {"mode": mode}
    roles = {}
    if over.get("receiver_mode"):
        roles["receiver"] = over.pop("receiver_mode")
    if over.get("sender_mode"):
        roles["sender"] = over.pop("sender_mode")
    if roles:
        lb["modes_by_role"] = roles
    for k in LB_KEYS[1:]:
        if k in over:
            lb[k] = over[k]
    doc["adapters"] = dict(over.get("adapters", {}) or {})
    doc["adapters"]["loopback"] = lb
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def wait_for(predicate, timeout_s=8.0, every=0.05):
    """等到条件成立。到点不满足就返回 False——**不把红等成绿**（在册判据口径）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(every)
    return bool(predicate())
