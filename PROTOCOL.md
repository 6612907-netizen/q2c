# q2c Protocol v1（`q2c/1`）

> 冻结对象：q2c 作为**通信桥**的线格式与状态机。
> 本文件与 `q2c/protocol.py` 是同一件事的两面：代码是机器可读真源，本文件是人的读法。
> 二者不一致＝测试红（`tests/test_protocol_doc_sync.py` 逐字比对枚举名单与字段名单），
> 所以文档不会被悄悄改旧，代码也不会漂出文档。

## 0. 这份协议管什么、不管什么

q2c 保证的是**交接**（handoff），不是**结果**（outcome）。

协议里存在的每一个状态都只回答一个问题：**这条消息走到哪一步了**。
它不回答、也不许回答："这份活干完了吗""这次交付合格吗""可以发布了吗"。

因此本协议**明确排除**下列取值，出现即属非法（`PROTOCOL_ERROR`，零副作用）：

```
TASK_COMPLETED            READY_TO_RELEASE          RELEASE_READY
COMPLETED                 ACCEPTED                  REJECTED
APPROVED                  CHANGES_REQUESTED         BLOCKED_ON_QUALITY
PROVEN                    V1_CLOSED
```

（历史实现里有过 `COMPLETED`、`VERDICT:`、`ACCEPT:` 三套词。它们的**归属**与**降级路径**
记在 `Q2C-BOUNDARY-AUDIT.md`；本协议只保留其中属于传输事实的判据形状。）

## 1. `protocol_version`

```
q2c/1
```

写在每一条消息的信封里。读到不认识的版本 ⇒ **拒收**（不猜测、不向下兼容地硬吃）。
同一主版本内只允许**加**可选字段；改语义或删字段 ⇒ 升主版本。

## 2. 信封字段（冻结 17 项）

| 字段 | 必填 | 含义 | 谁写 | 谁不许碰 |
|---|---|---|---|---|
| `protocol_version` | ✅ | 见 §1 | 发送方 | 任何人 |
| `message_id` | ✅ | 这一**条消息**的唯一号（同一次交接里每类消息各有各的号） | 发送方/桥 | 接收方 |
| `request_id` | ✅ | 这一次交接的主号，人读得到、日志里可 grep | 发送方 | 桥与接收方 |
| `correlation_id` | ✅ | 把后续消息挂回最初那次请求（回复、重投、追发都带它） | 桥 | 任何人 |
| `sender` | ✅ | 发起一方的**逻辑**身份（`q2c_session_id`，见 §6） | 发送方 | 接收方 |
| `receiver` | ✅ | 接收一方的逻辑身份＋适配器名 | 发送方 | 接收方 |
| `handoff_type` | ✅ | 交接种类，开放命名空间（见 §3 的 `handoff_type` 规则） | 发送方 | 桥 |
| `session_id` | ✅ | 承载这次交接的**逻辑会话**号 | 桥 | 提供方（它只有 provider 侧的号） |
| `workspace_ref` | ⬜ | 工作区坐标（路径或 ref id），受限见 SECURITY.md §4 | 发送方 | 桥 |
| `repo_ref` | ⬜ | 仓库坐标（不含凭据的 URL／本地 ref id） | 发送方 | 桥 |
| `commit_ref` | ⬜ | 提交号（7–40 位十六进制；短号合法但记原样） | 发送方 | 桥 |
| `payload` | ✅ | **不透明**正文。桥按原样投递，不解析语义、不改写、不判好坏 | 发送方 | 桥 |
| `artifact_refs` | ⬜ | 类型化产物引用数组（见 §7），优先"引用＋摘要" | 发送方 | 桥 |
| `idempotency_key` | ✅ | 幂等键：同键的**投递**只发生一次（见 §5） | 发送方 | 桥 |
| `created_at` | ✅ | 创建时刻（UTC，ISO-8601 带时区偏移） | 桥 | 任何人 |
| `expires_at` | ⬜ | 过期时刻；到点 ⇒ `EXPIRED`，不再投 | 发送方 | 桥 |
| `metadata` | ⬜ | 自由字典。桥只透传，永不据它做流程判断 | 发送方 | 桥 |

**未认识的字段**：读到时**原样保留**、继续透传，不许丢弃、不许因它拒收整条消息
（前向兼容的唯一办法）。**未认识的必填字段取值**（枚举外）：拒收。

## 3. 消息类型（6 种，冻结）

| 类型 | 谁发 | 含义 | 允许的即时状态效果 |
|---|---|---|---|
| `HANDOFF_REQUEST` | 发送方 → 桥 → 接收方 | "请把这件事接手过去" | `CREATED`→`QUEUED`→`DELIVERING`→`DELIVERED` |
| `HANDOFF_STARTED` | 接收方 → 桥 | "我这轮的调用真的开始了"（适配器可证明的起点） | `DELIVERED`→`STARTED` |
| `HANDOFF_PROGRESS` | 接收方 → 桥 | 中途的可观测事实（可选、可丢，丢了不影响投递） | 无状态迁移（只进 trace） |
| `HANDOFF_RESULT` | 接收方 → 桥 → 发送方 | 接收方**这一轮产出的回复**（内容不透明） | `STARTED`→`RESPONDED` |
| `HANDOFF_FAILED` | 任一腿 → 桥 | 这一腿失败，带 `failure_class`（见 §4.1） | →`DELIVERY_RETRY`／`DELIVERY_FAILED`／`EXPIRED` |
| `HANDOFF_ACK` | 收到消息的一方 → 桥 | "这条我收到了，且能证明它归因于本轮" | →`ACKED` |

`handoff_type` 是**发送方命名空间**（例：`review-request`、`notify-human`、`ping`）。
桥不许按 `handoff_type` 改变投递行为——否则它就开始在替工作流分类了。

## 4. 传输状态（11 枚，冻结）

```
CREATED        信封已成立，尚未入队
QUEUED         已入队，等这一腿被投出去
DELIVERING     正在投（适配器调用在飞）
DELIVERED      适配器给出合格回执：这条消息送到了那个接收方
STARTED        接收方这一轮确实开始跑（有可核证据，不是猜）
RESPONDED      接收方产出了回复并已被桥收下（内容不透明）
ACKED          对方按 §4.2 确认收到——这是链路终点，不是任务终点
DELIVERY_RETRY 这一腿投递失败但还在重投预算内（只重投消息，见 §5）
DELIVERY_FAILED 重投预算用尽 ⇒ 停手，等人处置（停放态，见下）；不许静默丢弃
EXPIRED        到 `expires_at` 仍未 `ACKED`
CANCELLED      发送方主动取消投递（不撤销已发生的接收方工作）
```

终态只有三枚：`ACKED`／`EXPIRED`／`CANCELLED`。
**没有 `COMPLETED`**。`ACKED` 只证明"回了话且能归因于本轮"，不证明唤醒之后活干成了。

`DELIVERY_FAILED` 不是终态，是**停放态**：桥自己绝不从这里自动再试一轮，
但两条合法的路可以把它推走——① 人显式 `q2c retry-delivery`（那是人的处置，不是自动重试）；
② 上一腿迟到的答复终于回来且合格（迟到答复被收回 ⇒ `ACKED`，且不再重复打扰）。
把停放态写成终态，实际后果是逼人伪造一个新 `request_id` 去绕开自己定的规则。

### 4.1 `failure_class`（`HANDOFF_FAILED` 的原因码，开放名单，取值必落 trace）

```
ADAPTER_UNAVAILABLE        适配器起不来／CLI 不在
CREDENTIAL_UNAVAILABLE     无人值守资格不成立（SECURITY.md §5）
UPSTREAM_UNAVAILABLE       提供方调用不可用（额度／网络／非零退出）
PROCESS_TIMEOUT            子进程到点未收口，已被进程组清理
NO_TERMINAL_EVENT          事件流没有合格终态（含 turn.failed 收尾）
ATTRIBUTION_FAILED         回复读得动但证明不了它属于本轮（缺绑定串／号不相等）
SESSION_LOST               逻辑会话找不到可用的 provider 绑定
UNKNOWN                    读不动 ⇒ 当未知处理，绝不折成"没发生"
```

`UNKNOWN` 与 `confirmed absent` 是两回事：桥在未知时**停手**，不重投、不重跑（§5）。

### 4.2 ACK 的形状（判据继承自已验证实现，`relay.py:859-910`）

一条 `HANDOFF_ACK` 成立，要求**同时**满足：

1. 执行器退出码为 0；
2. 输出里有**可解析的**结构化结果记录（取最后一条，不看散文）；
3. 该记录自报成功（例如 `is_error is false` 且 `subtype == "success"`——具体字段名属适配器，见 ADAPTERS.md）；
4. 该记录里的会话身份**等于**本次要送达的那个会话（拿别人的"成功"顶替＝不算）；
5. 约定的确认行**独立整行**存在（正文里出现过这个词 ⇒ 不算）；
6. 本轮派发时要求回出的**绑定串**被逐字整行回出（没要求 ⇒ 不要求；要求了却没回 ⇒ 不算）。

第 5、6 条的存在理由（真事故：失败回执正文里同样带着确认词；上一轮的答复复述一遍就能过关）
记在 `Q2C-BOUNDARY-AUDIT.md` A-3。

**正文除协议两行之外还有没有内容，是观测不是闸门。**
桥要求对侧回的就是那两行；再要求"更多字"就是桥在判答复质量（§5 禁止，旧内核 `MIN_BODY=60`
同一族已判移出产品路径）。读数里保留 `result_body_present` 与
`result_body_only_protocol` 两项，谁想要更严的答复形状，请在自己的话术或结果谓词里提。

**终态证据的"读法"属适配器，"要不要"属协议。** 协议只规定：声明了
`capabilities()["terminal_evidence"]` 的对侧，必须给出本轮的合格终态；
至于终态长什么样，两家不同——Codex 是一条事件流、以 `turn.completed` 收口，
Qoder 是最后一条 `{"type":"result","subtype":"success","is_error":false}` 记录。
核心不许猜，也不许把两套名单并成一套（并起来后任何开场白里出现 `result` 字样
都会被读成"本轮跑完"）。四条读数互不折衷：`completed`／`failed`／`absent`／`unknown`，
后三档一律不采信为送达。反面实例（2026-10-04 真腿第三跑）与反例格见 ADAPTERS.md §4。

## 5. 幂等与重投（本协议最容易误解的一节）

**幂等**：同一个 `idempotency_key` 的 `HANDOFF_REQUEST` 只被投递一次。重复投来的同键消息
进 trace 记 `duplicate_suppressed=true`，**不改状态、不新增副作用、不重跑任何东西**。

**重投（retry-delivery）**：只对**投递**这一动作重试，预算 `delivery_attempts_max`（默认 2）。

```
允许：把同一条消息、同一个绑定串，再投一次给同一个接收方。
禁止：因为"上一轮没出结果"而再叫一次接收方的模型去重跑任务。
```

三条硬规定，全部有机器判据（`tests/test_retry_delivery.py`）：

1. `retry-delivery` 不产生新的 `message_id`，不改 `payload`，复用原 `correlation_id` 与绑定串；
2. 重投时**必须**沿用第一次派发用的那枚绑定串。换新串 ⇒ 上一腿迟到的答复永远认领不了
   （实测代价：白烧一次真调用，还丢掉一格证据，`relay.py:2663-2685` 注释）；
3. 一旦结果原件已在案（`result_sha256` 对得上），任何路径都只允许**补送达**，
   适配器再被叫一次就是缺陷。

到预算上限 ⇒ `DELIVERY_FAILED` ＋ 一条 `HANDOFF_FAILED`（`failure_class`），**停手等人**。
桥不许自动把这一笔重新排进队列"再试一轮"。

## 6. 逻辑会话（`session_id` 与 provider 号的关系）

- `q2c_session_id`：桥自己发的、**稳定**的逻辑会话号。
- provider 会话号（Codex thread id、Qoder session id）：可变，是逻辑会话的一个**绑定**。

```
q2c_session_id  ──1:1(当前绑定)──►  provider_session_id
                └─历史绑定全部留档（重放时按时间轴取当时那一枚）
```

provider 重启、线程号换了、老线程被回收 ⇒ **不改** `sender`／`receiver`／`session_id`，
只改绑定表。桥不许因为"拿不到旧线程"就把这次交接认成新的一笔。
详见 `q2c/sessions.py` 与 ADAPTERS.md §3。

## 7. 产物引用（`artifact_refs`）

每项是一个带类型的对象。允许的 `type`（9 枚，冻结）：

```
git_commit  file  diff  patch  log  test_report  screenshot  evidence_package  generic_uri
```

引用形状：

```json
{"type": "git_commit", "ref": "1875b95e", "digest": "<64 位十六进制>", "media_type": "text/x-diff", "size_bytes": 123}
```

规则：**优先"引用＋摘要"，不复制正文**。`digest` 是引用所指字节的 SHA-256（能算就必须算）；
桥不许打开产物内容做判断。`evidence_package` 是一个目录快照的引用，配套清单指纹
（生成规则见 `q2c/artifacts.py`，来自已验证的候选导出机制 `relay.py:1387-1466`）。

## 8. 跟踪（trace）是通信事实，不是项目事实

`q2c trace <request_id>` 必须能回答这九问：

```
谁发的 · 谁收的 · 何时送达 · 何时开始 · 哪条会话 · 哪些产物 · 哪些重投 · 回了什么 · 何时确认
```

trace 是**只可追加**的事件流（JSONL）。任何一条状态迁移都留一行，含
`protocol_version／message_id／request_id／correlation_id／from_state／to_state／
failure_class（若有）／adapter／created_at`。

trace 里**没有**"任务完成""可以发布""验收通过"这三类事实——谁想写，属主不在 q2c。

### 8.1 九问的取数规矩（读数撒谎比缺字段坏）

每一问只可能是三种读数：发生过（给出证据里的值）／`NOT_OBSERVED`（时间线上确实没有）／
`UNVERIFIABLE`（有事件但这套读法证明不了）。三态不许互相顶替，也不许折成"没有欠账"。

四条取数规矩（2026-10-04 真腿第四跑把前两处改坏了的形状修回来，反例格在
`tests/test_trace_readings.py`）：

1. **状态证据认 `to_state`，不认事件名。** 产品发的事件名是 `created`／`queued`／
   `delivering`／`delivered`／`started`／`responded`／`acked`，从来没有一条叫 `state`。
   按事件名取会让每条真记录的"现在到哪一步"都报 `UNVERIFIABLE`——时间线上明明看得见收口。
   同时反向钉住：真的没有状态证据时保持 `UNVERIFIABLE`，不许编一枚末态。
2. **重投两条来路都要可见。** 人手 `retry-delivery`（写 `retry_of`）与结果腿的补送达
   （`ev=result_retry_of_delivery`＋`to_state=DELIVERY_RETRY`）都算；只认前者会把已经
   发生过的重投藏起来。没有重投事件时才是 `NOT_OBSERVED`。
3. **投递与开始之间没有可观测边界 ⇒ 就报没有。** 同步形态的对侧（`capabilities()` 里
   `split_send_receive=false`，Qoder 公开 CLI）在 `when_delivered` 那问上必须是
   `NOT_OBSERVED`，`when_started` 那问给出那一拍的时间；这一格有判据钉着"不许凑成观测到了"
   （`test_08_collapsed_boundary_is_not_fabricated`，真件基准）。
4. **原件复算。** 报告里登记的每一枚原件（派发文／捕获件／退出码侧件／结果件／事件流／台账）
   都带 SHA256，判据每次从盘上重算比对；结果件还要与记录里的 `nonce` 逐字对上——
   摘要对但归因错＝拿别人那一轮的答复顶账。

## 9. 版本与兼容

| 变更 | 允许 | 要求 |
|---|---|---|
| 加可选字段 | 同一主版本内 | 旧读者必须能忽略；`metadata` 不许当新语义的藏身处 |
| 加枚举取值（含 `failure_class`） | 同一主版本内 | 旧读者读到未知取值 ⇒ 原样记 `unknown_enum_value`，不许当默认值吃 |
| 改字段语义／删字段 | ❌ | 升主版本 |
| 加状态 | ❌ | §4 的 11 枚冻结；加状态＝升主版本，因为它是行为契约不是装饰 |

**fail-closed 总则**（本协议所有实现共享的一条）：未知的
`protocol_version`／消息类型／状态／适配器名／`failure_class` ⇒
拒绝 + 零副作用 + 记一行 trace + 退出码 2。
控制入口**不许**在"读不懂"时选择"做最多的那件事"。
