#!/usr/bin/env python3
"""发布报告读数的唯一生成器。

写这份报告的人（包括我自己）不许手打数字：所有数字都从现跑的命令里取。
覆盖范围：判据总数、跳过数、审计分档计数、CLI 契约、真实交接那一组的授权态，
以及真跑原件的 SHA256 复算读数（REAL_HANDOFF_EVIDENCE，两件事分开报，不互相顶替）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def sh(cmd, cwd=ROOT):
    p = subprocess.run(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=1800)
    return p.returncode, p.stdout


def readings():
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "root": ROOT}
    rc, txt = sh("python3 -W error::ResourceWarning -m unittest discover -s tests -t . 2>&1 | tail -25")
    m = re.search(r"Ran (\d+) tests in ([\d.]+)s", txt)
    out["tests"] = {"ran": int(m.group(1)) if m else None,
                    "seconds": float(m.group(2)) if m else None,
                    "rc": rc,
                    "skipped": len(re.findall(r"skipped=(\d+)", txt)) and
                               int(re.search(r"skipped=(\d+)", txt).group(1)),
                    "verdict": "OK" if "OK" in txt else ("FAILED" if "FAILED" in txt else "UNREADABLE")}
    rc, txt = sh("python3 tools/count-audit.py")
    out["audit"] = {"rc": rc, "line": txt.strip().splitlines()[0] if txt.strip() else "",
                    "detail": [l.strip() for l in txt.splitlines()[1:] if l.strip()]}
    rc, txt = sh("python3 tools/smoke-cli.py 2>&1 | tail -3")
    m = re.search(r"SMOKE_RESULT=(\S+)", txt)
    out["smoke"] = {"rc": rc, "result": m.group(1) if m else "UNREADABLE"}
    rc, txt = sh("python3 -m unittest tests.test_real_bidirectional 2>&1 | tail -6")
    m = re.search(r"REAL_HANDOFF_STATUS=(.+)", txt)
    out["real_handoff"] = {"rc": rc, "status": (m.group(1).strip() if m else "UNREADABLE"),
                           "note": "这一组是「另开现场再跑一次」的可选复跑：未授权即 SKIP，不折成 PASS。"
                                   "真实交接是否成立看 real_handoff_evidence（原件复算）。"}
    rc, txt = sh("python3 -m unittest tests.test_real_handoff_evidence 2>&1 | tail -8")
    m = re.search(r"REAL_HANDOFF_EVIDENCE=(.+)", txt)
    out["real_handoff_evidence"] = {
        "rc": rc, "status": (m.group(1).strip() if m else "UNREADABLE"),
        "line": (txt.strip().splitlines() or [""])[-1],
        "note": "真跑原件逐枚 SHA256 复算（每次跑判据都重算，防事后改证据）"}
    rc, txt = sh('git status --porcelain')
    out["working_tree"] = {"dirty_tracked": [l for l in txt.splitlines() if l and not l.startswith("??")],
                           "untracked": [l for l in txt.splitlines() if l.startswith("??")]}
    rc, txt = sh("git rev-parse HEAD")
    out["head"] = txt.strip()
    rc, txt = sh("python3 -c \"import q2c,sys;sys.path.insert(0,'.');print(q2c.__version__)\"")
    out["version"] = txt.strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = readings()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    for k, v in r.items():
        print("%-14s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
