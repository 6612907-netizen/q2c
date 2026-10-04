#!/usr/bin/env python3
"""把独立复验的**原始日志**折成一页可引用的读数（主理人 2026-10-04 裁定第 3 条的四件东西）。

要求是：环境身份、源码包 SHA256、完整日志、末行 `CLEAN_MACHINE_STATE`。
前两件在 artifact 里，第三件在 run log 里，这一页只做一件事：**从原件里 grep 出读数**，
让发布报告不出现任何手打数字（在册纪律：手打的计数一定会漂成假话）。

用法：
    python3 tools/ci-readings.py --run <run_id> --os macos --log <保存下来的 log 文件> [--json]

取不到的一律写成 `MISSING`，不许折成通过；末行结论由 `CI_CLEAN_MACHINE=PASS` 与
`CLEAN_MACHINE_STATE=ACKED` 两行**同时**存在才算 PASS。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

KEYS = (
    ("as_of", r"^as_of=(.*)"),
    ("hostname", r"^hostname=(.*)"),
    ("uname", r"^uname=(.*)"),
    ("python", r"^python=(.*)"),
    ("runner_env", r"^runner=(.*)"),
    ("identity", r"^(sha=\S+.*ref_source=\S+.*)"),
    ("environment", r"^(ENVIRONMENT=\S+)"),
    ("package_sha_line", r"^([0-9a-f]{64})\s+q2c-v0\.1\.0-source\.tar\.gz"),
    ("package_files", r"^package_files=(\d+)"),
    ("tests_line", r"^(Ran \d+ tests in [\d.]+s)"),
    ("tests_verdict", r"^(OK.*)"),
    ("tests_rc", r"^tests_rc=(\d+)"),
    ("clean_machine_rc", r"^clean_machine_rc=(\d+)"),
    ("install_pip", r"^install\.pip\s+(.*)"),
    ("pip_install", r"^PIP_INSTALL=(\w+)"),
    ("send_receive", r"^send→receive\s+(.*)"),
    ("trace_nine", r"^trace nine\s+(.*)"),
    ("repo_clean", r"^repo stays clean\s+(.*)"),
    ("working_tree", r"^working tree\s+(.*)"),
    ("state_line", r"^(CLEAN_MACHINE_STATE=\S+)"),
    ("job_verdict", r"^CI_CLEAN_MACHINE=(\w+)"),
)


def extract(path):
    out = {}
    if not os.path.isfile(path):
        return {k: "MISSING（日志文件不在：%s）" % path for k, _ in KEYS}
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for key, pat in KEYS:
        hit = "MISSING"
        for ln in text.splitlines():
            # 托管 runner 的日志每行前面带 job 名与时间戳，取最后一段再比
            body = ln.split("\t")[-1]
            body = re.sub(r"^\S+Z\s*", "", body.strip())
            m = re.match(pat, body)
            if m:
                hit = " ".join(g for g in m.groups() if g)
        out[key] = hit
    out["pass"] = (out.get("job_verdict") == "PASS"
                   and out.get("state_line") == "CLEAN_MACHINE_STATE=ACKED"
                   and out.get("tests_rc") == "0"
                   and out.get("clean_machine_rc") == "0"
                   and out.get("environment", "").startswith("ENVIRONMENT=INDEPENDENT"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="GitHub Actions run id（只写进抬头，供人回查）")
    ap.add_argument("--os", default="", help="这枚日志属于哪个 runner（macos／ubuntu）")
    ap.add_argument("--log", required=True, help="保存下来的完整日志文件")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = extract(a.log)
    d["run"] = a.run or "MISSING"
    d["runner_os"] = a.os or "MISSING"
    d["log_file"] = a.log
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0 if d["pass"] else 1
    print("# 独立复验读数（生成自原件 grep；原件见 log_file）")
    print("run=%s runner_os=%s log_file=%s" % (d["run"], d["runner_os"], d["log_file"]))
    for k, _ in KEYS:
        print("%-17s %s" % (k, d.get(k, "MISSING")))
    print("CI_VERDICT=%s" % ("PASS" if d["pass"] else "NOT_PASS"))
    return 0 if d["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
