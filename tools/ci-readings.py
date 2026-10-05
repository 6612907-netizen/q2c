#!/usr/bin/env python3
"""把独立复验的**原始日志**折成一页可引用的读数（主理人 2026-10-04 裁定第 3 条的四件东西）。

要求是：环境身份、源码包 SHA256、完整日志、末行 `CLEAN_MACHINE_STATE`。
前两件在 artifact 里，第三件在 run log 里，这一页只做一件事：**从原件里 grep 出读数**，
让发布报告不出现任何手打数字（在册纪律：手打的计数一定会漂成假话）。

用法：
    python3 tools/ci-readings.py --run <run_id> --os macos --log <保存下来的 log 文件> [--json]

`--log` 给**一枚 job 的日志**最省事（`gh api repos/<repo>/actions/jobs/<job_id>/logs`）。
如果给的是整 run 的合流日志（`gh run view --log`），**必须带 `--os`**：
没有它就无法判断这一页读数属于哪一枚 runner，工具会报 `AMBIGUOUS_LOG` 并退 2；
`--os` 写了但日志里没那一档 ⇒ `OS_NOT_IN_LOG` 退 2。这两种都不是"没通过"，是"测不了"。

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
    ("package_sha_line", r"^([0-9a-f]{64}\s+q2c-v\d+\.\d+\.\d+-source\.tar\.gz)"),
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


def _job_of(line):
    """托管 runner 合流日志每行是 `job名\t步骤\t时间戳 正文`；没有这个形状就返回空串。"""
    parts = line.split("\t")
    return parts[0].strip() if len(parts) >= 3 else ""


def _jobs(lines):
    seen = []
    for ln in lines:
        j = _job_of(ln)
        if j and j not in seen:
            seen.append(j)
    return seen


def extract(path, os_name=""):
    """从一份日志里取**一枚 job** 的读数。取不到那一枚就拒，不许"那就扫整篇取最后一条"。

    这里原来是假读数发生器（2026-10-05 08:5x 实测）：`--os` 只被写进抬头当标签，
    正文扫整篇 ⇒ 把 macos＋ubuntu 混在一份的 run 日志喂两次，两页读数逐字相同，
    而且那一页属于哪一枚 runner 全看谁排在后面。抄进报告就是"双 OS 各自复验通过"。
    """
    out = {}
    if not os.path.isfile(path):
        return {k: "MISSING（日志文件不在：%s）" % path for k, _ in KEYS}
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    jobs = _jobs(lines)
    if jobs and os_name:
        keep = [ln for ln in lines if os_name.strip().lower() in _job_of(ln).lower()]
        if not keep:
            return {"_refusal": "OS_NOT_IN_LOG（--os=%s 在这份日志里没有对应的那枚 job；日志里的 job=%s）"
                    % (os_name, ",".join(jobs))}
    elif len(jobs) > 1:
        return {"_refusal": "AMBIGUOUS_LOG（这份日志混了 %d 枚 job：%s；要么按作业下载单份，"
                            "要么用 --os 指明朝哪一枚取数——多来源挑一条当整体＝假读数）"
                % (len(jobs), ",".join(jobs))}
    else:
        # jobs 为空＝按作业下载的日志（每行只有时间戳，没有 `job名\t步骤\t` 前缀）：
        # 那本来就是一枚 job，`--os` 在这里只是标签，不许当成"日志里找不到这一档"而拒。
        keep = lines
    for key, pat in KEYS:
        hit = "MISSING"
        for ln in keep:
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
    d = extract(a.log, a.os)
    if d.get("_refusal"):
        # 三档退出码（在册口径）：0 合格／1 跑过了不合格／2 测不了或取数来源不明。
        # 拒的时候一个字节都不写，也别让"MISSING 一堆"被下游当成"那一格没做"。
        print("CI_READINGS=%s" % d["_refusal"])
        print("log=%s os=%s" % (a.log, a.os or "-"))
        return 2
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
