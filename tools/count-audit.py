#!/usr/bin/env python3
"""数 Q2C-BOUNDARY-AUDIT.md 的分类行数（现读，不抄文档里的自报数）。

行数 ≠ 站点数：一行可以只写一个站点，也可以写一组同族站点；
复核方自报的"184 站"是它自己按语义数的。本脚本只回答一件事——
**这份审计里有没有哪一档一条都没分**（那是审计没做完的形状）。
"""
import os
import re
import sys
from collections import Counter

CLASSES = ("KEEP_AS_TRANSPORT", "RENAME_AS_TRANSPORT", "REMOVE_FROM_PRODUCT_PATH", "DEPRECATE")


def count(path: str):
    rows = [l for l in open(path, encoding="utf-8") if l.startswith("|") and l.count("|") >= 5]
    cnt, classified = Counter(), 0
    for r in rows:
        if r.startswith("|---") or "分类" in r or "含义" in r:
            continue
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        for c in cells:
            hit = [k for k in CLASSES if c.startswith(k) or ("`%s`" % k) in c]
            if hit:
                cnt[hit[0]] += 1
                classified += 1
                break
    return classified, cnt


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Q2C-BOUNDARY-AUDIT.md")
    if not os.path.isfile(path):
        print("AUDIT_COUNT: 文件不在 %s" % path)
        return 2
    classified, cnt = count(path)
    print("AUDIT_COUNT: 已分类行数=%d（现读，非站点数）" % classified)
    for k in CLASSES:
        print("  %-26s %d" % (k, cnt.get(k, 0)))
    missing = [k for k in CLASSES if cnt.get(k, 0) == 0]
    if missing:
        print("AUDIT_INCOMPLETE: 这些一档一条没有 %s" % ",".join(missing))
        return 1
    if classified < 150:
        print("AUDIT_THIN: 分类行数少于 150，内核＋工装两层不可能这么少")
        return 1
    print("AUDIT_COUNT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
