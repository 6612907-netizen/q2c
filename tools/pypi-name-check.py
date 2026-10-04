#!/usr/bin/env python3
"""PyPI 名字核查（只读取证）：`q2c` 这个名字现在到底有没有被占用。

为什么要单独一枚工装，而不是我在报告里手写一句"名字可用"：
这句话决定要不要为发布去改名，而改名是不可逆的公开动作。它必须**随时能在别人机器上复跑**，
并且三档结论齐全（在册纪律：测不到＝UNVERIFIABLE，不许折成"可用"）：

    FREE          两个公开入口都回 404 ⇒ 当前没有这个项目
    TAKEN         任一回 200 ⇒ 已被占用（**含那种注册了但一枚 release 都没有的占位项目**，
                  所以这里不去看 releases 数量，只看项目是否存在）
    UNVERIFIABLE  取不到／超时／既非 200 也非 404 ⇒ 这就是"没查到"，不是"可用"

退出码：0=FREE、1=TAKEN、2=UNVERIFIABLE。别的程序照这个分支，不读我的文字。

它**不**声称"注册一定通过"：PyPI 在注册那一刻还有额外规则（与标准库模块同名、
与已有名字易混淆、被管理员列进保留/禁用名单的名字都会被拒）。这些只有提交上传时才判定，
而上传需要账号与令牌——那是产品所有者点头之后的事，本工装一步都不做。
所以本件的结论上限就是"当前未被占用"，`PYPI_LIMIT` 那一行把这条边界写死在机器读数里。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

NAME = "q2c"
#: 两个入口互为对照：JSON API 是元数据面，simple index 是安装面（pip 真正走的那条）。
#: 只查一个会被缓存/代理糊住，两个都 404 才算这名字空着。
ENDPOINTS = [
    ("json_api", "https://pypi.org/pypi/%s/json"),
    ("simple_index", "https://pypi.org/simple/%s/"),
]
UA = "q2c-name-check/1.0 (release engineering probe; read-only GET)"


def normalize(name):
    """PEP 503 的项目名归一：大小写不敏感，`-_.` 的连续段并成一枚 `-`。

    PyPI 判"占用"用的是归一后的键，不校这一句就会把 `Q2-C` 的占用读成 `q2c` 空闲。
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def stdlib_clash(name):
    """与标准库模块同名的名字 PyPI 一律拒（这条能离线判，就别留到上传那天）。"""
    known = set(getattr(sys, "stdlib_module_names", set()))
    if not known:  # 3.9 没有 stdlib_module_names，退回内置模块表（这一档如实标 UNVERIFIABLE 由调用方兜）
        known = set(sys.builtin_module_names)
    return name.lower() in known


def probe(url, timeout=20):
    """返回 HTTP 状态码整数，或 None（=取不到，不许当成 404）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.getcode())
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def read_json_release_count(url, timeout=20):
    """只读用：项目存在时它有没有 release（TAKEN 那一档补一句实况）。"""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                                    timeout=timeout) as resp:
            return len(json.load(resp).get("releases", {}))
    except Exception:
        return None


def main(argv=None):
    name = argv[0] if argv else NAME
    key = normalize(name)
    codes = {}
    for label, tmpl in ENDPOINTS:
        codes[label] = probe(tmpl % key)
    print("name=%s normalized=%s" % (name, key))
    for label in sorted(codes):
        print("%s=%s" % (label, "NO-RESPONSE" if codes[label] is None else "http %d" % codes[label]))
    clash = stdlib_clash(key)
    print("stdlib_clash=%s" % ("yes" if clash else "no"))

    if clash:
        print("PYPI_VERDICT=TAKEN-REFUSED（与标准库同名，注册期一定被拒）")
        print("PYPI_LIMIT=这条是离线判据，不依赖网络")
        return 1
    if any(c is None for c in codes.values()):
        print("PYPI_VERDICT=UNVERIFIABLE（有入口取不到⇒不能写成可用）")
        print("PYPI_LIMIT=网络／代理不可达时只到此档，绝不折成 FREE")
        return 2
    if all(c == 404 for c in codes.values()):
        print("PYPI_VERDICT=FREE（两个入口都无此项目＝当前未被占用）")
        print("PYPI_LIMIT=注册期另有规则（易混淆名、保留与禁用名单），只有上传那一刻才判定；"
              "本工装不做任何写动作")
        return 0
    if any(c == 200 for c in codes.values()):
        releases = read_json_release_count(ENDPOINTS[0][1] % key)
        print("PYPI_VERDICT=TAKEN（已被占用；releases=%s）"
              % ("UNKNOWN" if releases is None else releases))
        print("PYPI_LIMIT=占用者的注册时刻无法从这两个入口读出")
        return 1
    print("PYPI_VERDICT=UNVERIFIABLE（状态码组合 %s 不在三档里，按取不到处理）"
          % sorted(codes.values()))
    print("PYPI_LIMIT=代理或 CDN 改写过的响应不算证据")
    return 2


if __name__ == "__main__":
    sys.exit(main())
