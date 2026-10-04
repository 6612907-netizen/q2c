# 适配器（Adapter）

契约七枚：`capabilities / start / resume / send / receive / status / cancel`
（`q2c/adapters/base.py`）。分工写死：

| 位置 | 职责 |
|---|---|
| 适配器 | 对侧字段叫什么、命令怎么起、回执／事件流什么形状；把事实折成 `Receipt` |
| `q2c/ack.py` | 这些事实够不够叫**送达**（六闸＋终态闸），对所有适配器同一套尺 |
| `q2c/transport.py` | 要不要投、投到哪一步、预算、恢复；不知道任何 provider 字段名 |

依赖顺序（产品定义）：**官方 SDK ＞ 公开 CLI ＞ 公开兼容协议**。
v0.1 的两个正式适配器走**公开 CLI**，并显式列出不碰的东西。

## 在册四个适配器

| 名字 | live | terminal_evidence | split_send_receive | 用途 |
|---|---|---|---|---|
| `codex` | 是（需 `Q2C_LIVE=1`） | 是 | 是 | OpenAI Codex CLI 会话 |
| `qoder` | 是（需 `Q2C_LIVE=1`） | 是 | **否**（同步调用） | Qoder CLI 会话 |
| `loopback` | 否 | 否 | 是 | Quick Start 与真子进程判据 |
| `inproc` | 否 | 否 | 是 | 负载判据专用（LOAD-ONLY） |
| `notify`（配置项，不是适配器） | — | — | — | 失败通知通道 `notification_cmd` |

## 1. Codex

公开 CLI 形态：

```
投递   codex queue --thread <线程号> --message <派发文>
取答复 codex exec resume --json -c sandbox_mode="read-only" --skip-git-repo-check \
            -o <末条消息文件> <线程号> <提示>
凭据   codex login status          （只问能不能无人值守，stdout 丢弃）
```

**不碰**：Codex 私有 SQLite 队列库、`~/.codex/sessions/**/*.jsonl` 的内部形状、
内部 Rust 类型、隐藏参数。旧内核拿这些当"是否还有未消费项／这一轮是否已答复"的证据；
产品不用，因为那是内部结构，随时会变。

**代价（如实记账，不当能力报）**：恢复阶梯里"从会话落盘件反证这一轮已答复"两档在产品中不存在，
对应情形读成 `UNKNOWN ⇒ 停手等人`，不自动重发。宁可停手，不可重复烧一次真调用。

**限制**：公开 CLI 没有"零副作用新建空线程"的入口 ⇒ `start()` 直接拒绝，
要求 `q2c sessions bind` 登记已有线程号（不猜号、不另起一条把上下文混进来）。

## 2. Qoder

```
qoderclicn -p -r <会话号> [-w <工作区>] --permission-mode auto --output-format json [--model …] <正文>
```

两点形状差必须知道：

- **同步**：一次调用既投递又答复 ⇒ `DELIVERED` 与 `STARTED` 之间没有可观测边界。
  桥把这一腿记成一次迁移并在跟踪里写 `collapsed_delivery_start=true`——**不假装看见了起点**。
- **SDK 入口变量必须剥掉**（`QODER_AGENT_SDK_ENTRYPOINT` 那一族）：被无头拉起的子 CLI
  一旦以为自己还在 SDK 里，会要求 stream-json 并直接拒启，表现是"叫起来就失败"，
  很容易被误读成对侧不肯回话。

**验到哪一枚**：代码里 `q2c/adapters/qoder.py` 写的是 `BIN = "qoderclicn"`——真跑过的那条腿
是 **Qoder CN** 的公开 CLI。Qoder **International 没跑过 ⇒ 记 `NOT_VERIFIED`，不在支持声明里**；
上面那条命令对它不构成任何保证。要把这条腿换到另一支，得先逐条核这四件：CLI 名、
参数形状（`-p -r -w --permission-mode --output-format`）、`QODER_AGENT_SDK_*` 剥离、
`{"type":"result", …}` 单帧收口；核完还得在真环境里跑一次双向交接拿到 ACKED 才算验过。
这一档与 README「验到哪一枚 CLI」那屏同源，判据在 `tests/test_vendor_claims.py`。

## 3. 逻辑会话与 provider 号（§7）

```
q2c_session_id（稳定） ──当前绑定──► provider_session_id（会变）
                    └── bindings[]: {provider, since, until, reason}
```

- provider 重启、换号 ⇒ 只追加绑定，**逻辑号不变**，历史交接身份不变；
- 重放旧消息按时间轴取当时那一枚（`provider_at()`），不永远用最新值；
  因此时刻必须到微秒——秒级会在同一秒内的换绑上取错对象（有判据）；
- 换绑必须写 `reason`；不同用途不共用一条会话（真机上共用线程那次，
  答的是上一笔的 SHA：字段全过、证据自相矛盾）。

## 4. 送达判定（`q2c/ack.py`，PROTOCOL.md §4.2）

六闸：`rc=0` → 有可解析结构化记录 → 对侧自报成功（`is_error is False` 且 `subtype=="success"`）
→ 会话身份相等 → 独立整行 `ACKED` → 逐字整行回出 `Q2C-BIND: <本轮串>`。
真 CLI 还要过**终态闸**（事件流须以 `turn.completed` 之类合格终态收尾）。

**收答复那一腿只要求归因与可信收口，不要求 `ACKED`**：`ACKED` 属于结果腿
（发起方确认自己收到了回复）。这条口径是收敛出来的，不是宽松化——
旧内核把两腿混在一套词表里，正是"审核说 CHANGES_REQUESTED、验收复用同一句被判成通过"的来源。

### 4.1 终态证据有两套词表（2026-10-04 真腿第三跑定下的）

`terminal_evidence=True` 的对侧必须给出本轮终态，但"终态长什么样"两家长得不一样：

| 对侧 | 收口形态 | 读法 | 认什么 |
|---|---|---|---|
| Codex | 一条事件流 | `ack.scan_terminal(raw)` | `turn.completed` 为成功；`turn.failed`／`error` 为失败 |
| Qoder | 最后一条记录 | `ack.scan_result_frame_terminal(raw)` | `{"type":"result", is_error:false, subtype:"success"}` 为成功；`is_error:true` 或 `subtype` 以 `error` 起头为失败；缺该帧为 absent；有帧但读不出为 unknown |

四档读数互不折衷，`absent`／`unknown`／`failed` 都**不采信**为送达。两套词表不许并成一套
（并起来后任何开场白里出现 `result` 字样都会被当成本轮跑完）。三格反例都在
`tests/test_qoder_terminal_real_fixture.py`：正向采信（真件 stdout 复放）、
缺帧为 absent、串词表不许假绿（test_08／test_09）。

两处已修的真缺陷记在这里，防"顺手改回去"：

1. `ack.receipt_from_cli_json(..., stream_terminal=…)` 这个参数曾被收下却**没写进回执**，
   于是走这条参考实现又声明终态证据的腿永远读成"未提供"；摘掉这一行的变异
   （`ack-terminal-arg-ignored`）必须让判据变红。
2. Qoder 腿曾经直接用 Codex 的事件词表读自己的收口帧 ⇒ 每次真投递都被终态闸拒；
   对应的摘词表变异（`qoder-read-with-codex-vocabulary`）同族。

### 4.2 真跑必须各用一条新会话（同一条流会"回上一轮"）

复用同一条 Codex 线程时，上一跑留在队列里的那条消息会先被回答：本轮 `turn.completed`
确实在、会话号也对，但正文回的是**上一轮**的 `req-…` 与 `Q2C-BIND: …`。
归属那一刀拒得对（原件 `evidence/real-legs/05-codex-stale-bind-stream.jsonl`），
但真跑不该常态性撞它。所以 `tools/real-handoff.py` **默认每次新建会话**
（四个角色各一条：两枚线程、两枚会话），建会话那一次调用在报告里明确标
`Setup 调用：只为新建会话，不构成交接（无信封、无绑定串）`；
只有显式 `--reuse` 才允许拿旧号跑，且报告里会写 `reused=true`。

## 5. 写一个新适配器

1. 继承 `Adapter`，实现七枚方法；
2. `capabilities()` 如实报：`live`／`terminal_evidence`／`split_send_receive`／
   `supports_cancel`／`evidence`／`limits`——取不到的证据位**不许报"支持"**；
3. 把对侧回执折成 `Receipt`（用 `ack.receipt_from_cli_json` 或等价解析），
   **不要在适配器里判送达**；
4. 落盘件命名走 `adapters/_spawn.py`（含 `attempt`，重投不复用旧回执）；
5. 加一格判据：能力位 honesty（参照 `test_11_capability_flags_are_honest`）
   与 contract 完整性（`test_12_contract_methods_all_present`）。
