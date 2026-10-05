# q2c

**Reliable handoff infrastructure for AI coding agents.**

q2c guarantees the handoff, **not** the outcome.

q2c 是一座桥，不是编排器：它把一条消息从一个 AI 编码 Agent 交到另一个，
并留下**可查、可复跑、可对号**的通信证据——谁发的、谁收的、什么时候送到、
这一轮跑没跑、回了什么、什么时候确认。它不判断这份代码行不行、这活干完没干完、
能不能上线：那些是 CI、评审与你的判断，不是桥的职责。

```
Agent A ── HANDOFF_REQUEST ──► q2c ──► Agent B
Agent A ◄── HANDOFF_RESULT + ACK ── q2c ◄── Agent B
                 ↑
        全程只追加的跟踪：q2c trace <request_id>
```

## 装上就跑（pipx／pip，三条命令）

```sh
pipx install q2c                        # 或：python3 -m pip install q2c
q2c --help                              # 命令在 PATH 上了
```

跑通第一次交接（零模型调用，用内置的 loopback 应答器；不需要 Codex 也不需要 Qoder）：

```sh
export Q2C_HOME=$(mktemp -d)            # 状态根；q2c 不常驻、不留后台进程
q2c init
S=$(q2c sessions create --role sender   --adapter loopback --label demo-s |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
R=$(q2c sessions create --role receiver --adapter loopback --label demo-r |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
q2c send --from "$S" --to "$R" --payload "请把这件事接手过去：回一句话说明你收到了这一笔。"
q2c list                                # 状态分布：这应该只有 1 笔，且落在 ACKED
```

`send` 的输出里 `"pump": {… "state": "ACKED"}` 就是成功了。`ACKED` 的意思是
**对方回了话、且能证明它回答的是这一笔**，不是"这活干完了"（见 [PROTOCOL.md](PROTOCOL.md) §4）。
`--adapter loopback` 不能省：适配器名空缺一律按 `UNKNOWN_ADAPTER` 拒（零副作用、退 2），
这是设计，不是缺件。

> `pipx install q2c` 要等 PyPI 上真有 `q2c` 这个名字才能跑（现在还没有，见发布报告
> `PYPI_NAME` 那一格）。今天就能照抄的等价命令是从公开仓装：
> `pipx install git+https://github.com/6612907-netizen/q2c.git@v0.1.0`。
> 这两条路都被 `tools/pkg-install-test.sh` 在干净环境里真跑过，不是写下就算。

## 验到哪一枚 CLI（兼容性边界，读代码前先看这一屏）

先把边界说清：桥的核心（协议／台账／跟踪／送达确认）**不含厂商假设**，
但两个真适配器**认具体 CLI**——所以"验过"这件事只能一枚一枚说。

```
QODER_CN_VERIFIED=YES （Qoder CN 的公开 CLI `qoderclicn`：与 Codex 双向各一次真调用，两腿都 ACKED）
QODER_INTERNATIONAL_VERIFIED=NOT_VERIFIED （没跑过，不在支持声明里；照抄本节命令不算已证）
CODEX_VERIFIED=YES （公开 CLI 档：队列投递＋`exec resume --json` 取答复）
CORE_PROTOCOL_VENDOR_NEUTRAL=YES （信封字段／状态词表／ACK 六闸里没有厂商字段，也不认厂商取值）
CN_SPECIFIC_DEPENDENCIES=CLI 名 `qoderclicn`;命令形状 `-p -r <会话号> -w <工作区> --permission-mode auto --output-format json`;起子进程前必须剥掉的 `QODER_AGENT_SDK_*` 那一族变量;收口帧 `{"type":"result", is_error:false, subtype:"success"}` 的单帧形状
```

`NOT_VERIFIED` 这一档我是怎么定的：**没跑过就写没跑过**。
换成 International 那一支之前，得把上面那四条 CN 专属依赖逐条核一遍，
再在真环境里把双向交接跑一次才算验过——我没跑那一支，所以不能替它签字。
这条纪律和"不拿 Agent 的自述当执行事实"是同一条。

## Quick Start（60 秒，零模型调用，不需要 Codex 也不需要 Qoder）

```sh
git clone <本仓库> && cd q2c
sh examples/quickstart.sh
```

手动走一遍（`bin/q2c` 免安装，`python3 -m q2c` 等价）：

```sh
export Q2C_HOME=$(mktemp -d)          # 状态根；q2c 不常驻、不留后台进程
export PYTHONPATH=$PWD

./bin/q2c init                        # 建状态根与默认配置
./bin/q2c doctor                      # 只读体检：环境／适配器／凭据可用性／台账可读性

S=$(./bin/q2c sessions create --role sender   --adapter loopback --label demo-s |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
R=$(./bin/q2c sessions create --role receiver --adapter loopback --label demo-r |
   python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')

./bin/q2c send --from "$S" --to "$R" --type review-request \
  --payload "请把这件事接手过去：回一句话说明你收到了这一笔。"

./bin/q2c list                        # 状态分布与在途笔数
./bin/q2c inspect <request_id>        # 这一笔的读数（不判定）
./bin/q2c trace  <request_id>         # 九问：谁发／谁收／何时送达／何时开始／哪条会话／
                                      #       哪些产物／哪些重投／回了什么／何时确认
```

看到 `"state": "ACKED"` 就跑通了。`ACKED` 的意思是**对方回了话且能证明它回答的是这一笔**，
不是"这活干完了"（见 [PROTOCOL.md](PROTOCOL.md) §4）。

想接真 CLI（Codex／Qoder），照 [ADAPTERS.md](ADAPTERS.md) 登记已有的线程号／会话号即可：

```sh
./bin/q2c sessions create --role receiver --adapter codex --label review-leg
./bin/q2c sessions bind --session <qs-…> --provider <codex 线程号> --reason "使用已存在的线程"
Q2C_LIVE=1 ./bin/q2c send --from <qs-…> --to <qs-…> --payload "…"   # 真调用需显式授权
```

## 为什么是桥，不是编排器

因为交付里真正坏掉的是**通信**，不是判断。四类失败都被实测过（细节与反例见
[Q2C-BOUNDARY-AUDIT.md](Q2C-BOUNDARY-AUDIT.md)）：

1. 执行器退出码 0 被当成"消息送到了"——真反例是 `is_error=true`、退 7、正文同样带确认词的回执；
2. 拿上一轮那段"看起来核读过"的答复复述一遍，形状一样全过；
3. 桥被杀后重启，把"读不出来"当成"确认没跑过"，于是同一请求两份对象、两枚绑定串、
   两条互相打脸的答复，还多烧一次真实调用；
4. 链路断了却没人被告知，事后读起来像"没有欠账"。

q2c 的答复是：把送达判定写成六条同时成立的硬闸、把每一轮派发绑一枚一次性串、
把重启后的处置写成"未知就停手"、把失败写成逐笔可见的通知并留下送达回执。

## 产品边界（读代码前先读这一节）

**q2c 拥有**：消息投递、关联、幂等、传输 ACK、传输重投、逻辑会话映射、Agent 适配器、
产物引用、传输恢复、通信跟踪、传输安全。

**q2c 不拥有，也不接受"顺手加一个"**：工作流任务状态、项目调度、CI 真相、代码质量判断、
评审批准、放行决定、项目级重试预算、工作流恢复、人工批准、任务完成。

这条边界不是口头承诺，是会让程序失败的形状：

- 协议枚举里没有 `TASK_COMPLETED`／`READY_TO_RELEASE`／`COMPLETED` 这类取值（§0）；
- 事件流里出现 `acceptance`／`grade`／`release_ready` 字段名 ⇒ 直接拒写（`FORBIDDEN_EVENT_KEY`）；
- CLI 里没有 `approve`／`gate`／`release`／`waive` 这类动词，
  判据 `test_10_no_project_management_verbs_exist` 盯着它别长回来；
- 未知的适配器名／消息类型／状态／协议版本 ⇒ 拒绝、零副作用、退出码 2。

## 核心不变量（改代码前必读）

| # | 不变量 | 落在哪 | 判据 |
|---|---|---|---|
| 1 | 退 0 ≠ 送达；六闸全查才算 `ACKED` | `q2c/ack.py` | `tests/test_ack.py` |
| 2 | 重投 = 重投**消息**，绝不重跑对侧任务 | `transport.retry_delivery` | `tests/test_retry_delivery.py` |
| 3 | 未知 ≠ 确认没跑；未知一律停手 | `transport.recover` | `tests/test_recovery.py` |
| 4 | 同一 `idempotency_key` 只投一次，抑制要留痕 | `transport._create_one` | `tests/test_idempotency.py` |
| 5 | 终态不可翻；迟到事件只进跟踪 | `protocol.next_state` | `tests/test_protocol.py` |
| 6 | 只读命令零写入（不建目录、不取写锁） | `cli.READ_ONLY` | `tests/test_stores_and_honesty.py` |
| 7 | 跟踪是通信真相，不是项目完成真相 | `q2c/trace.py` | `test_24_forbidden_project_fields_are_reused` |
| 8 | q2c 不持有凭据 | `q2c/security.py` | `tests/test_credentials.py` |
| 9 | 文档说的枚举 = 代码里的枚举 | `PROTOCOL.md` ↔ `protocol.py` | `tests/test_protocol_doc_sync.py` |

## 退出码（脚本可以照着分支）

```
0 成功
1 运维性失败（投递没送上、取消没成、记录不在）
2 拒绝：协议／适配器／路径／凭据／配置越界 —— 零副作用
3 读不动：台账／跟踪／配置存在但解析不了（≠"没有"，也≠"没问题"）
```

## 仓库地图

```
q2c/                     包（纯标准库，无第三方依赖）
  protocol.py            信封／枚举／状态机（PROTOCOL.md 的机器形态）
  ack.py                 送达确认六闸 ＋ 终态闸（判据在核心，解析在适配器）
  transport.py           收单、投递、重投、到期、取消、重启恢复
  ledger.py              台账：flock ＋ 原子落盘 ＋ 三方合并 ＋ 陈旧检测
  sessions.py            逻辑会话 ↔ provider 会话号（换绑留档、要理由）
  artifacts.py           9 类 typed ref ＋ 摘要 ＋ 快照清单指纹
  trace.py               只追加事件流 ＋ 九问 ＋ 写前脱敏
  security.py            凭据边界、路径与工作区、进程组、脱敏、原子落盘
  config.py              <home>/config.json（拒绝持有凭据）
  cli.py / __main__.py   11 个子命令
  adapters/              base（契约七枚）＋ codex ＋ qoder ＋ loopback ＋ inproc
  stub_responder.py      判据用的假对侧（真子进程，八种回执形状）
bin/q2c                  免安装入口
examples/                quickstart.sh ＋ review-handoff.sh
tests/                   判据（unittest，无需装任何东西）
tools/                   clean-machine-test.sh ＋ smoke-cli.py ＋ count-audit.py ＋ report-readings.py
PROTOCOL.md / SECURITY.md / ADAPTERS.md / ARCHITECTURE.md / CONTRIBUTING.md / CHANGELOG.md
Q2C-BOUNDARY-AUDIT.md    产品边界审计（232 行逐点分类）
Q2C-PRODUCT-SPEC-v0.1.md 产品方案（Phase 0）
```

## 跑判据

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
sh tools/clean-machine-test.sh
python3 tools/report-readings.py --json      # 发布报告的读数唯一来源（不许手打数字）
```

## 验证证据在哪（公开仓与源码包里为什么没有）

源码包与这个仓只装**代码／文档／工装／判据**。发布过程产生的原件——真实双向交接的现场
（派发文、对侧原始 stdout、退出码侧件、台账、跟踪事件流）、独立干净环境复验的完整日志与
artifact、每一次验牙的 JSON、发布报告与两张清单——既不在包里，也不在这里：

- **不进包**：打包范围＝发布域清单所列，规则写在 `tools/make-manifest.py`，
  并由 `tests/test_release_manifest.py` 校"包件集＝清单项"；
- **不进公开仓**：那些原件里带本机绝对路径、外置卷标、会话号与额度读数——公开出去泄露的是
  这台机器，不是产品。发布域里出现同类坐标会被
  `tests/test_release_domain_local_paths.py` 直接判红。

所以复核不必依赖我的原件：照上面两条命令自己跑一遍（判据＋`tools/clean-machine-test.sh`），
再对 `Q2C-v0.1.0-RELEASE-REPORT.md` 里那三个数——包 SHA256、独立复验作业号、tag 指向的 Commit。
报告里每一格都写明它是谁验的、在哪台机器上验的、复跑命令是什么。

## 已知局限（不粉饰，详见 Q2C-v0.1.0-RELEASE-REPORT.md）

- **两条真实双向交接需要真实模型调用**（付费），因此默认 `SKIP` 并如实报，
  不拿零模型的绿顶替那一格。要闭合需要产品所有者点头。
- Codex 腿的重启证据比旧实现少两档：不读 Codex 私有 SQLite 队列库、
  不解析 `~/.codex/sessions` 内部 JSONL 形状。对应情形读成 `UNKNOWN ⇒ 停手等人`。
- Qoder 公开 CLI 是同步的，`DELIVERED` 与 `STARTED` 之间无可观测边界，
  跟踪里如实记 `collapsed_delivery_start=true`。
- 两个真适配器都不提供"零副作用新建会话"，需要 `sessions bind` 显式登记已有号。
- 无 Windows 支持路径（`fcntl` 与进程组语义按 POSIX 实现）。
- 已发到 PyPI：`pipx install q2c` 拿到的就是 0.1.1 这一枚（发布记录见 `Q2C-v0.1.1-RELEASE-REPORT.md`）。
  三条路都真跑过：公开 PyPI 全新环境装、`pipx install git+…@v0.1.1`、免安装（`bin/q2c`／`python3 -m q2c`）。

## 许可

MIT（见 [LICENSE](LICENSE)）。
