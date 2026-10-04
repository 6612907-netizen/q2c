# q2c v0.1.1 交付与发布说明

这一版改的是**装得上、跑得起来、边界说清**：产品逻辑与协议 `q2c/1` 一字未动
（自证命令在本文件最后一屏）。v0.1.0 那份交付说明仍在仓里（`DELIVERY-v0.1.0.md`），
它记的是上一版发布，本轮不改写。

## 怎么装

```sh
pipx install q2c            # PyPI 正式版本（0.1.1）——需主理人完成身份授权后才有
python3 -m pip install q2c  # 等价
```

还没等到 PyPI 的时候，今天就能跑的等价命令（真跑过）：

```sh
pipx install git+https://github.com/6612907-netizen/q2c.git@v0.1.1
```

## 装完第一条命令

```sh
q2c --help
```

## 第一次跑通（零模型调用，不需要 Codex 也不需要 Qoder）

```sh
export Q2C_HOME=$(mktemp -d)
q2c init
S=$(q2c sessions create --role sender   --adapter loopback --label demo-s |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
R=$(q2c sessions create --role receiver --adapter loopback --label demo-r |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
q2c send --from "$S" --to "$R" --payload "请把这件事接手过去：回一句话说明你收到了这一笔。"
q2c list
```

看到 `"state": "ACKED"` 就成功了。`--adapter loopback` 不能省（适配器名空缺一律拒，零副作用、退 2）。

## 三行自检（别人在自己机器上复跑，不依赖我方原件）

```sh
sh tools/pkg-install-test.sh                       # 造件→装件→CLI→Quick Start→用发出去的那份代码跑整批判据
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
sh tools/clean-machine-test.sh
```

## 版本身份（这一版为什么是 0.1.1）

- `v0.1.0` 的 tag 与 GitHub Release 附件**永久冻结**；PyPI 上的 `0.1.0` 不指向本轮那套字节
  （主理人 2026-10-04 裁定选 (a)）。
- 协议仍是 `q2c/1`；`protocol_version` 与包版本是两个独立编号（见 `PROTOCOL.md` §9）。
- 自证「产品逻辑没动」：

```sh
git diff --name-only v0.1.0 v0.1.1 -- q2c/    # 只应出现 _version.py 与 __init__.py
git show v0.1.0:q2c/protocol.py | shasum -a 256
git show v0.1.1:q2c/protocol.py | shasum -a 256    # 两行应相同
```

## 验到哪一枚 CLI（别把没跑过的写成支持）

```
QODER_CN_VERIFIED=YES
QODER_INTERNATIONAL_VERIFIED=NOT_VERIFIED
CODEX_VERIFIED=YES
CORE_PROTOCOL_VENDOR_NEUTRAL=YES
```

真跑过的那条 Qoder 腿用的是 CN 版 CLI（`qoderclicn`）；International 那一支**没跑过**，
本文档对它不作任何保证。逐条依赖与原因见 `README.md`「验到哪一枚 CLI」与 `ADAPTERS.md` §2。
