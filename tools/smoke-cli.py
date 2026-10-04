#!/usr/bin/env python3
"""把 `q2c` 的 CLI 走一遍真路径（零模型调用，loopback 对侧）。

跑法：`python3 tools/smoke-cli.py`。它只做一件事——
在临时根里 init → 建两条逻辑会话 → send → inspect → trace → list → doctor → cancel，
然后把每一步的退出码与关键字段打出来。任何一步不如预期就 **退非 0**，不印"看起来没问题"。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(home, *args, expect_rc=0, label=""):
    env = dict(os.environ, Q2C_HOME=home, PYTHONPATH=ROOT)
    p = subprocess.run([sys.executable, "-m", "q2c"] + list(args), cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    tag = label or args[0]
    print("---- %s ⇒ rc=%s" % (tag, p.returncode))
    out = p.stdout.strip()
    doc = None
    if out:
        for candidate in (out, out.splitlines()[-1]):
            try:
                doc = json.loads(candidate)
                break
            except ValueError:
                continue
    print((json.dumps(doc, ensure_ascii=False, indent=1) if doc else out)[:1800])
    if p.returncode != expect_rc:
        print("FAIL: %s 期望 rc=%s 实得 %s" % (tag, expect_rc, p.returncode))
        sys.exit(1)
    return doc


def main() -> int:
    home = tempfile.mkdtemp(prefix="q2c-smoke-")
    run(home, "version")
    run(home, "init")
    s = run(home, "sessions", "create", "--role", "sender", "--adapter", "loopback",
             "--label", "smoke-sender")
    r = run(home, "sessions", "create", "--role", "receiver", "--adapter", "loopback",
            "--label", "smoke-receiver")
    sent = run(home, "send", "--from", s["session_id"], "--to", r["session_id"],
               "--type", "handoff", "--payload", "请把这件事接手过去：回一句话就行。")
    rid = sent["request_id"]
    state = (sent.get("pump") or {}).get("state")
    print("pump 后状态：%s" % state)
    if state != "ACKED":
        # 推台阶：in-flight 那一腿到下一拍才收回
        for i in range(3):
            ins = run(home, "inspect", rid, label="inspect-%d" % i)
            state = (ins.get("record") or {}).get("state")
            print("第 %d 拍状态：%s" % (i, state))
            if state == "ACKED":
                break
    run(home, "trace", rid)
    run(home, "list")
    run(home, "doctor", expect_rc=0)
    run(home, "adapters")
    print("SMOKE_RESULT=%s" % ("ACKED" if state == "ACKED" else state))
    return 0 if state == "ACKED" else 1


if __name__ == "__main__":
    sys.exit(main())
