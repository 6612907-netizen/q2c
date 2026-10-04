# Q2C PRODUCT SPEC v0.1

| 字段 | 值 |
|---|---|
| 文档 | `Q2C-PRODUCT-SPEC-v0.1.md` |
| 状态 | Phase 0 定稿草案（供 Owner 复核；不替代 `PROTOCOL.md` 与 `Q2C-BOUNDARY-AUDIT.md`） |
| 冻结定位 | **q2c = Reliable handoff infrastructure for AI coding agents.** |
| 核心原则 | **q2c guarantees the handoff, not the outcome.** |
| 上游依据 | 任务书 `TASKBOOK-Q2C-PRODUCT-01.md` ＋ 主理人 2026-10-03 产品定义（桥不是编排器）＋ 已验证实现在 `q2c-wakeup-fix02` |
| 现读证据 | `python3 -m unittest discover -s tests -t .` ⇒ 46 格 OK；`python3 tools/smoke-cli.py` ⇒ rc=0、`SMOKE_RESULT=ACKED` |
| 本文不做什么 | 不重定义定位、不新增产品能力、不把已剥出的工作流职责放回核心 |

---

## 1. Product Vision（产品愿景）

让两个互不信任、运行在不同机器与不同进程里的 AI 编码 Agent，能够把一件事**交出去并且知道它交到了**。

今天的现实是：Agent A 让 Agent B 去看一份提交，靠的是"发一条消息然后猜"。
消息可能没送到、可能送给了别人的会话、可能是上一轮那句看起来很像的话、
可能 A 已经重启而 B 的答复落在没人读的角落。q2c 要做的就是把这段"猜"变成**可查的事实**：
谁发的、谁收的、什么时候送到、这一轮跑没跑、回了什么、什么时候确认。

愿景的边界同样重要：q2c **不**承诺这件事办得好、办得对、办得完。
它承诺的是——如果我说交出去了，那么我拿得出交出去的证据；如果没交到，我不会假装交到了。

## 2. Problem Statement（问题陈述）

具体的、被实测过的四类失败：

1. **送达冒充**：执行器退出码 0 就被当成"消息送到了"。实测反例：`is_error=true`、
   退出码 7、正文里同样带着确认词的回执，被旧写法记成送达。
2. **归因冒充**：拿上一轮那段"看起来核读过"的答复复述一遍，形状一样能全过。
   实测反例：回执字段全对，但它回出的是别轮的绑定串。
3. **重启后重复消费**：中转进程被杀，恢复时把"读不出来"当成"确认没跑过"，
   于是同一请求出现两份对象、两枚绑定串、两条互相打脸的答复，并多烧一次真实模型调用。
4. **静默丢失**：链路断了（凭据不可用、对侧起不来、投递不上）但没有任何人被告知，
   台账上只留一行事件；事后读起来像"没有欠账"。

这四类都不是"功能不够"，而是**通信事实与自我叙述之间的缝隙**。q2c 填的就是这条缝。

## 3. Target Users（目标用户）

| 用户 | 他要什么 | q2c 给他什么 |
|---|---|---|
| 同时驱动多个 Agent 的开发者／技术负责人 | 不用一直盯着两个窗口来回贴消息 | 一条 `q2c send` 把活交出去，稍后 `q2c trace` 看落到哪一步 |
| 被指派干活的 Agent（Codex 侧／Qoder 侧） | 收到的东西可归因、不被重复叫醒、答复有地方放 | 带绑定串的派发文 + 幂等键 + 结果原件落盘 |
| 需要为交付留证据的人（评审、交接、复盘） | 一份能对着命令复跑、能对号取回的通信记录 | 只追加的事件流 + 引用＋摘要的产物清单 |
| 自建流水线的工程师 | 稳定的进程／重启／失败语义，不要一个会自己长大的框架 | 无常驻、无守护、退出码明确、失败可核 |

不服务的人：想要"帮我决定这活能不能上线"的人。那不是 q2c 的答案，也不该是。

## 4. Primary Use Cases（主要用例）

1. **交付后请对侧核读**：Qoder 写完一笔提交，`q2c send` 给 Codex 一条核读请求，
   Codex 那一轮跑完，回复送回 Qoder 会话并确认——q2c 到"确认送达"为止，回复内容是好是坏它不管。
2. **跨机／跨进程叫醒**：A 侧的 Agent 会话已经结束，B 侧要让它回头看一件事；
   叫醒是一次投递，投递失败要有界重投、重投只重投消息。
3. **失败要有人看见**：凭据不可用、对侧 CLI 起不来、投递预算用尽 ⇒
   逐笔留下指名到这一笔的送达证据；送不到就如实写"file-only"，不算闭环。
4. **事后复盘**：三个月后问"这笔到底送没送到、送的时候带了哪些产物、中间重投过几次"，
   `q2c trace` 用现读的事件流回答九问。
5. **漏单恢复**：机器重启、进程被杀之后，`q2c recover` 按证据阶梯处置，
   未知与确认没跑严格分开，宁可停手也不重复叫醒。

## 5. Non-Goals（非目标，冻结清单）

v0.1 **不做**，且不因"顺手能加"而加：

```
workflow engine            工作流引擎
project scheduler          项目调度
quality judgment           代码质量判断
CI truth                   CI 真相
review approval            评审批准
release decision           放行决定
project retry budget       项目级重试预算
workflow recovery          工作流恢复
human approval             人工批准
task completion            任务完成
```

同批不做的配套（任务书 §13）：DAG、quality engine、release manager、AI reviewer、
云服务、计费、Web dashboard。要加其中任何一项，需要一版新的产品授权，不是这一个版本里的"优化"。

这条清单在产品里的**物理体现**：协议枚举里没有 `TASK_COMPLETED`／`READY_TO_RELEASE`
等取值（`PROTOCOL.md` §0），事件流里不许出现 `acceptance`／`grade`／`release_ready`
这些字段名（`q2c/trace.py` 会直接拒绝并抛 `FORBIDDEN_EVENT_KEY`），
CLI 里没有对应子命令。不是口头承诺，是会让程序失败的形状。

## 6. Product Positioning（产品定位）

**q2c = Reliable handoff infrastructure for AI coding agents.**（冻结，不改）

一句话摆位：q2c 是**桥**，不是**编排器**。

| 相邻的东西 | 它答的问题 | q2c 答不答 |
|---|---|---|
| CI／质量门 | 这份码行不行 | 不答 |
| 任务系统／看板 | 这活归谁、排第几 | 不答 |
| 编排器／Agent 框架 | 接下来该做哪一步 | 不答 |
| 消息队列／总线 | 这条字节送到哪了 | **答，且只答这个** |

q2c 与前三类可以搭在一起用，但那些是**用户的选择**，不是 q2c 的功能。
它输出的是"通信事实"，让别的东西去据此做决定。

## 7. Core Product Principle（核心产品原则）

**q2c guarantees the handoff, not the outcome.**

拆成四条可执行的规矩（每条都有对应判据，不是标语）：

1. **`ACKED` 只证明回了话，不证明唤醒成功，更不证明活干完了。**
   它是链路终点，不是任务终点（`tests/test_ack.py`）。
2. **queue 成功 ≠ 唤醒。** `QUEUED`／`DELIVERED`／`STARTED` 是三件不同的事，
   各自要有自己的证据；适配器给不出起点证据时不许替它编（`ADAPTERS.md`）。
3. **不把 Agent 的自述当执行事实。** 退出码 0、正文里出现关键词、别人的会话号，
   任何单一信号都不构成送达（六闸全查）。
4. **测不到就说测不到。** 读数是三态（发生过／没发生／证明不了），
   第三态**永不**折进前两种（`PROTOCOL.md` §9、`tests/test_stores_and_honesty.py`；
   §B 计划里给这组起的名字是 `test_read_honesty.py`，落地时并入前者，见审计的落点对照）。

## 8. Product Boundary（产品边界）

**q2c 拥有**（任务书 §2，逐条落到模块）：

| 职责 | 落在哪 |
|---|---|
| 消息投递 | `q2c/transport.py` ＋ `q2c/adapters/*` |
| 关联 | `correlation_id` ＋ 一次性绑定串（`q2c/protocol.py`、`q2c/ack.py`） |
| 幂等 | `idempotency_key` 抑制重复投递（`transport.create`） |
| 传输 ACK | 六闸（`q2c/ack.py`） |
| 传输重投 | 有界重投消息，绝不重跑任务（`transport.retry_delivery`，协议 §5） |
| 逻辑会话映射 | `q2c/sessions.py` |
| Agent 适配器 | `q2c/adapters/base.py` 契约 + Codex/Qoder/loopback |
| 产物引用 | `q2c/artifacts.py`（引用＋摘要） |
| 传输恢复 | `transport.recover`（证据阶梯，未知≠没跑） |
| 通信跟踪 | `q2c/trace.py`（九问） |
| 传输安全 | `q2c/security.py`（凭据边界、进程组、路径、脱敏、原子落盘） |

**q2c 不拥有**（§5 的清单）——边界上还有一条容易漏的**反向禁令**：
q2c 也不许在 `payload` 里"顺手"解释这些概念。载荷是不透明的：
里面写着 `APPROVED` 或 `READY_TO_RELEASE`，桥照投不误，但**绝不据此改状态**。
（这条有判据：`tests/test_protocol.py::test_05_payload_is_opaque`。）

**剥出去的代价与保留的保护**：见 `Q2C-BOUNDARY-AUDIT.md` §C-2 与 §C-3——
每一条被移出产品路径的站点都写清了"移除会丢哪条保护、那条保护现在落在哪"。

## 9. Core Concepts（核心概念）

| 概念 | 一句话 | 标识 |
|---|---|---|
| Handoff 交接 | 一次"把这件事交给对方"的完整通信事实 | `request_id` |
| Message 消息 | 交接里的某一条可寻址消息（请求腿／结果腿各有各的号） | `message_id` |
| Leg 腿 | 请求腿（发给接收方）与结果腿（回复发回发起方） | `legs.request` / `legs.result` |
| Correlation 关联 | 后续消息挂回最初那次请求 | `correlation_id` |
| Binding 绑定串 | 一次性随机串，答复必须逐字整行回出，用来证明"答的是这一笔" | `Q2C-BIND: <nonce>` |
| Idempotency 幂等 | 同键只投一次；重复投来不新增副作用 | `idempotency_key` |
| Logical Session 逻辑会话 | 跨 provider 重启的稳定身份 | `q2c_session_id` |
| Provider binding 对侧绑定 | 逻辑会话当前挂在哪个真实线程／会话号上 | `provider_session_id` |
| Receipt 回执 | 适配器交回的"对侧自报事实"，是否采信由核心判 | `q2c.ack.Receipt` |
| Artifact 引用 | 指向产物的类型化引用（优先摘要，不复制正文） | `artifact_refs` |
| Trace 跟踪 | 只追加的事件流＝通信事实 | `trace/events.jsonl` |
| Terminal 终态 | `ACKED`／`DELIVERY_FAILED`／`EXPIRED`／`CANCELLED`，之后不许翻案 | `protocol.TERMINAL_STATES` |

## 10. Protocol Model（协议模型）

规范正文在 `PROTOCOL.md`（协议版本 `q2c/1`），代码真源在 `q2c/protocol.py`，
两者不一致由 `tests/test_protocol_doc_sync.py` 打红。这里只给模型骨架：

- **信封 17 字段**（顺序即冻结顺序）：
  `protocol_version, message_id, request_id, correlation_id, sender, receiver, handoff_type,
  session_id, workspace_ref, repo_ref, commit_ref, payload, artifact_refs, idempotency_key,
  created_at, expires_at, metadata`
- **6 类消息**：`HANDOFF_REQUEST / STARTED / PROGRESS / RESULT / FAILED / ACK`
- **11 枚传输态**：`CREATED, QUEUED, DELIVERING, DELIVERED, STARTED, RESPONDED, ACKED,
  DELIVERY_RETRY, DELIVERY_FAILED, EXPIRED, CANCELLED`
- **8 枚失败归类**：`ADAPTER_UNAVAILABLE, CREDENTIAL_UNAVAILABLE, UPSTREAM_UNAVAILABLE,
  PROCESS_TIMEOUT, NO_TERMINAL_EVENT, ATTRIBUTION_FAILED, SESSION_LOST, UNKNOWN`
- **状态机纪律**：跳格被拒（`ILLEGAL_TRANSITION`）、终态不可翻（`STALE_EVENT`）、
  未知取值一律拒收（`UNKNOWN_STATE`／`UNKNOWN_MESSAGE_TYPE`／`UNSUPPORTED_PROTOCOL_VERSION`）。
- **兼容**：未认识的**字段**原样保留继续透传；未认识的**取值**拒收；
  加状态＝升主版本（因为状态是行为契约）。

## 11. User Journey（用户旅程）

最小一条路，也是 README Quick Start 的那条（零模型调用、零外部依赖）：

```
1) 安装            python3 -m pip install .        （或 clone 后直接 python3 -m q2c）
2) 体检            q2c init && q2c doctor          → 看 home／适配器／凭据可用性／台账可读性
3) 登记两侧身份    q2c sessions create --role sender   --adapter loopback
                   q2c sessions create --role receiver --adapter loopback
4) 交出一件事      q2c send --from <qs-sender> --to <qs-receiver> \
                     --type review-request --payload "请核读这笔提交" \
                     --commit 1875b95e --artifact git_commit=1875b95e
5) 看落到哪一步    q2c inspect <req-…>             → 记录 + 九问 + 产物核验三态
6) 事后复盘        q2c trace  <req-…>              → 谁发/谁收/何时送达/…/何时确认
7) 送不上时        q2c retry-delivery <req-…>      → 只重投消息；或 q2c cancel <req-…>
```

换成真实两端：把第 3 步的 `--adapter` 换成 `codex` / `qoder`，
用 `q2c sessions bind` 登记已有线程／会话号（**不新建**，因为公开 CLI 没有零副作用新建入口）。

旅程里用户**得不到**的东西：任何"这活完成了／合格了／可以发了"的回答。
这不是缺功能，是这一版的产品决定（§5、§7）。

## 12. CLI Experience（命令行体验）

11 个子命令（任务书 §10），一个都不多：

```
q2c init | doctor | adapters | send | inspect | list | trace |
retry-delivery | cancel | sessions | version
```

外加两个恢复动作 `expire`／`recover`（它们不是新功能，是协议 §4 的 `EXPIRED`
与"传输恢复"职责的必要入口；写进文档以免被当成漂移）。

体验约定：

- **输出是 JSON**，字段是事实字段（状态、时刻、摘要、读数），不是判词；
  人读的话术留给上游工具。
- **退出码是契约**：`0` 成功｜`1` 运维性失败｜`2` 拒绝（协议／路径／凭据／配置越界，**零副作用**）｜
  `3` 读不动（存在但解析不了）。脚本可以照着分支，这是唯一允许被依赖的"判断"。
- **只读子命令零写入**：`inspect/list/trace/doctor/adapters/version` 不建目录、不取写锁。
  （判据：跑完后整棵树哈希集合相等。）
- **没有** `q2c approve`／`gate`／`close`／`waive` 这类动词，也不许将来加。
- 破坏性动作（未来的清理类）必须显式指定目标根才允许——这条纪律继承自实验根的 `reset` 教训。

## 13. Adapter Strategy（适配器策略）

**契约七枚**：`capabilities / start / resume / send / receive / status / cancel`（任务书 §6）。

分工写死：

| 位置 | 职责 |
|---|---|
| 适配器 | 对侧的字段叫什么、命令怎么起、回执/事件流什么形状；把事实折成 `Receipt` |
| 核心 `q2c/ack.py` | 这些事实够不够叫送达（六闸 + 终态闸），对 Codex 与 Qoder 是**同一套尺** |
| 核心 `q2c/transport.py` | 要不要投、投到哪一步、预算、恢复；不知道任何 provider 字段名 |

依赖顺序（任务书 §9/§10）：**官方 SDK ＞ 公开 CLI ＞ 公开兼容协议**。
v0.1 两个正式适配器都走**公开 CLI**，并显式列出**不碰**的东西：
Codex 私有 SQLite 队列库、`~/.codex/sessions` 内部 JSONL 形状、内部 Rust 类型、隐藏参数。

代价如实记账（不是"待修"，是这一版的取舍）：
旧内核恢复阶梯里"从会话落盘件反证这一轮已答复"那两档，在产品中不存在，
对应情形读成 `UNKNOWN ⇒ 停手等人`。**宁可停手，不可重复烧一次真调用。**

`capabilities()` 必须自报限制与能力位（`live`／`terminal_evidence`／`split_send_receive`／`limits`），
`q2c doctor` 与 `q2c adapters` 把它们原样印出来。取不到的证据位不许报"支持"。

未知适配器名 ⇒ `UNKNOWN_ADAPTER`，拒绝、零副作用、退出码 2
（同一形状也挡住未知 mode／event／state／version）。

## 14. Session Model（会话模型）

```
q2c_session_id（稳定，桥发的） ──1:1 当前绑定──► provider_session_id（会变）
                              └── 历史绑定全部留档（since/until/reason）
```

- provider 重启、线程号换了、老线程被回收 ⇒ **不改** `sender`/`receiver`/`session_id`，
  只改绑定表。重放旧消息时按时间轴取当时那一枚，不永远用最新值。
- **不同用途不共用一条逻辑会话**（实测：共用线程时真机答了上一笔的 SHA，
  字段全过、证据自相矛盾）。
- 换绑必须写 `reason`，空理由拒绝：不许静默搬家。
- 逻辑会话号由 `role|adapter|label` 派生（可复现），不做"每次调用随机生成一个新身份"。
- 会话册读不动 ⇒ 抛错，**不返回空表**（"没登记"与"册坏了"必须是两句话）。

## 15. Artifact Model（产物模型）

9 类 typed ref（`git_commit / file / diff / patch / log / test_report / screenshot /
evidence_package / generic_uri`），优先 **引用＋摘要**，不复制正文。

- 摘要＝所指字节的 SHA-256（能算就必须算）；`git_commit` 的摘要位**故意留空**：
  把 40 位提交号再哈希成 64 位是假装有摘要，完整性靠"在该仓库里能否取回这枚提交"验。
- 核验是**四态**，不折 0：`OK` / `MISSING`（判过，确实不在）/ `DIVERGED`（字节变了）/
  `UNVERIFIABLE`（我没能力判）。第三种与第一种之间的差别，就是"能不能信这份读数"。
- `evidence_package`＝目录快照的引用＋**清单指纹**（`相对路径\tsha256` 排序后再哈希），
  排除运行态（`state/`、`inbox/`、`trace/`、锁、日志、缓存）与凭据类文件；
  重投时复用同一枚指纹 ⇒ 对象不变。
- 桥**不打开产物内容做判断**。要看内容，是人或上游工具的权力，不是桥的。

## 16. Trace / Observability（跟踪与可观测）

`q2c trace <request_id>` 回答九问（协议 §8）：

```
谁发的 · 谁收的 · 何时送达 · 何时开始 · 哪条会话 · 哪些产物 · 哪些重投 · 回了什么 · 何时确认
```

- 载体是**只追加**的 JSONL 事件流；每一次状态迁移一行，
  带 `protocol_version / message_id / request_id / correlation_id / from_state / to_state /
  failure_class / adapter / created_at`。
- 每一问都有第三态：`NOT_OBSERVED`（没发生过）与 `UNVERIFIABLE`（发生了但证明不了）分开写；
  跟踪件不在 ⇒ `TRACE_ABSENT`，明确说"这是没读到，不是没有事件"。
- 写盘前统一脱敏（`security.redact`），事件里不许出现凭据形状的内容。
- trace 是**通信真相**：里面没有、也不许有 `acceptance`／`grade`／`release_ready`
  这类字段名——程序会拒写，不是靠自觉。
- 观测不改被观测物：只读命令零写入，有判据（跑完前后全树哈希集合相等）。

## 17. Security Model（安全模型）

规范正文在 `SECURITY.md`（Phase 0 之后随实现出）。模型六条：

1. **q2c 不持有凭据**：不读钥匙串、不复制 token、不落盘密钥；
   配置文件里出现凭据形状 ⇒ 整份拒绝加载（`CONFIG_WOULD_HOLD_SECRETS`）。
   只允许一件事：问"这一侧能不能无人值守完成一次最小认证调用"，六条硬规矩
   （不读密钥／不调会弹界面的取密钥命令／不改钥匙串／只回 rc·耗时·分类／
   **判不出来按不可用**／stdout 直接丢弃）。
2. **载荷秘密防护**：写盘前统一脱敏；回执原件保留但**失败件改名不覆盖**
   （失败证据不许被下一次成功抹掉）。
3. **安全进程启动**：命令串经模板校验与可执行文件解析；子进程**自成进程组**；
   到点清理走 `TERM → grace → KILL`，处置结果如实返回（`gone-after-term`／`permission-denied`／…）。
4. **工作区与路径限制**：ref 必须绝对路径、不许含 `..`、默认必须落在 q2c 根内
   （要放开得显式配置）；`allowed_workspaces` 白名单；凭据类路径直接拒。
5. **进程读数三态**：`pid + 启动时刻` 双判，`unknown` 永不折成 `gone`；
   探针读不出来 ⇒ 恢复阶梯停手，不重投。
6. **fail-closed 总则**：未知的 mode／event／kind／state／version／command
   ⇒ 拒绝 + 零副作用 + 留痕 + 退出码 2。控制入口**不许**在"读不懂"时选择"做最多的那件事"。

已知风险与边界（不粉饰）：`payload` 是不透明文本，若用户自己把密钥写进正文，
q2c 会原样投递——桥能做的是写盘脱敏与不落盘凭据，不能替用户管他发出去的内容。
这条写进 SECURITY.md 的报告流程，不当缺陷修。

---

## 18. v0.1 Scope（本版本范围）

**做**：协议 `q2c/1`（17 字段／6 类消息／11 状态／8 失败归类）；
两腿传输（投递、有界重投、到期、取消）；幂等；ACK 六闸＋终态闸；
逻辑会话与 provider 绑定表；9 类产物引用＋摘要＋快照清单；只追加跟踪；
安全层（凭据边界、进程组、路径、脱敏、原子落盘）；
适配器契约 + Codex / Qoder / loopback 三个实现；11 个 CLI 子命令；
任务书 §14 的判据套件（含 1000 枚合成交接）＋§15 的四组旧缺陷回归；
公开仓文件与可跑通的 Quick Start。

**不做**（§5 清单 ＋ 以下明确排除）：不发布到公网仓库（需 Owner 明示）；
不做 pip/brew/npm 包发布；不装常驻服务／LaunchAgent／cron；
不改 `~/bin` 那套 legacy 四件套；不删实验根任何原件与证据。

## 19. Explicitly Deferred（明确推迟，附"什么时候才谈"）

| 推迟项 | 谈它的前提 |
|---|---|
| 工作流／编排能力 | 新的产品授权，且先解决"它为什么必须是 q2c 而不是上游工具" |
| Hook 平台／事件总线／多 Agent 框架 | 同上（主理人 10-03 已明列不做） |
| Codex 私有存储证据（队列库、sessions JSONL） | 出现**公开且稳定**的等价接口；否则永久排除 |
| 逻辑会话自动新建（provider 侧） | 对侧公开 CLI 提供零副作用新建入口 |
| 通知到人以外的"人工介入"（待办／批准） | 不属于本产品；若要，独立工具 |
| 多接收方广播、优先级队列、持久服务 | 需要证明"投递语义因此更可靠"，而不是"更省事" |

## 20. Release Criteria（v0.1.0 放行条件，任务书 §17）

每条都必须**现跑**给读数，绿不绿由 Owner 判，不由本文判：

```
1 协议成文且与代码逐字一致          tests/test_protocol_doc_sync.py（12 格）
2 判据全绿                          python3 -m unittest discover -s tests -t .
3 双向真实交接各一次成立            tests/test_real_bidirectional.py（Q2C_LIVE=1；需授权，见 §21）
4 重启／幂等成立                    tests/test_recovery.py + test_idempotency.py
5 安全文档存在                      SECURITY.md
6 Quick Start 被真人跑通            tools/smoke-cli.py（本机已过）＋清洁机复跑
7 工作树干净                        git status --porcelain 为 0
8 发布物可复现                      清单按冻结提交生成、逐条可取回
```

## 21. Known Limitations（已知局限，不粉饰）

1. **两条真实双向交接（任务书 §14 第 1、2 项）需要真实模型调用**，
   属于停止清单里的"付费服务／新凭据"类。默认判据以 `Q2C_LIVE` 未设跳过并如实报
   `SKIP（未授权真调用）`，**不拿零模型绿顶替它**。要闭合这一格需要 Owner 一句话授权。
2. Codex 腿的重启证据比实验根少两档（不读私有队列库与 sessions JSONL），
   对应情形读成 `UNKNOWN ⇒ 停手等人`。
3. Qoder 公开 CLI 是同步的，`DELIVERED → STARTED` 之间无可观测边界，
   trace 记 `collapsed_delivery_start=true`；不假装看见了起点。
4. `start()` 在两个真适配器上都不提供"零副作用新建会话"，需要 `sessions bind` 显式登记。
5. 载荷里的用户自写秘密不在桥的保护范围内（§17 第 7 条末段）。
6. v0.1 无 Windows 支持路径（`fcntl`／进程组语义按 POSIX 实现）；`ps` 读数在非 macOS／Linux 上为
   `UNVERIFIABLE`。
7. 本文 §20 第 3、6、8 条**尚未闭合**，因此 `Q2C-v0.1.0-RELEASE-REPORT.md` 当前只能是
   `Q2C_V0_1_RELEASE_BLOCKED`，不是 READY。

## 22. Future Roadmap（后续路线，不含承诺日期）

```
v0.1.x  把 §20 剩下三格闭合（需 Owner 授权真调用与发布动作）
        清洁机安装复验固化成脚本；产物清单↔提交可复现门
─────────────────────────────────────────────
之后每一步都要单独授权，不在 v0.1 范围内：

A. 适配器面拓宽：官方 SDK 档（若公开）＞现有公开 CLI 档；新增第三侧 Agent 只做契约实现
B. 证据面：把"引用＋摘要"接到外部对象存储的 only-reference 模式（不落正文）
C. 可观测出口：trace 的只读导出格式（NDJSON／OTel 事件映射），不改语义、不加判词
D. 兼容性：`q2c/2` 只有在"加状态或改字段语义"时才谈，且必须先给旧台账的读取路径
E. 上游集成示范：一个"q2c 之外"的最小放行工具，作为**用户侧例子**存在，不进核心
```

路线图里的任何一项都**不**意味着把 §5 那十条放回核心。放回那十条的动作，
在这份定位下不叫"迭代"，叫改产品——那需要新的产品授权。

---

## 附：PRODUCT_CONFLICT 检查结论

按任务书要求逐条比对"现有任务书"与"冻结定位／核心原则／十条禁放清单"，结论：

- **未发现需要停工的定义级冲突。** §5 十条在任务书 §2/§13 与冻结定位之间完全一致；
  任务书没有一处要求 q2c 拥有其中任何一条。
- 有 **三处需要澄清但不冲突**，按"先保留可靠性机制、记录冲突、不自行删除保护"处置并记录在案：

| # | 事项 | 冲突吗 | 现在的处置 |
|---|---|---|---|
| C-1 | 任务书 §18 要求报告里有 `RELEASE_READY=` 与最终判词 `Q2C_V0_1_RELEASE_READY/BLOCKED`；§5 禁止 q2c 拥有 release decision | 不冲突 | 判词是**关于这份代码库在某枚 SHA 上的人读报告**，由 Qoder 出、Owner 签；`q2c` 任何命令都不输出 READY 类取值（协议 §0 把 `RELEASE_READY` 列成非法取值，事件流也拒写该字段名） |
| C-2 | 失败要"通知到人"是否等于 q2c 拥有 human approval（审计 §B 待裁项 1） | 不冲突 | 只保留"投递失败必须让某个接收方可见＋逐笔送达回执"；不引入待办／批准／签字语义（§C 处置表第 1 行） |
| C-3 | 任务书 §14 要求两条真实双向交接；停止清单含"付费服务" | 不冲突，但**越权** | 判据写好并默认 SKIP＋如实报；要闭合需 Owner 明示授权（§21 第 1 条） |

若 Owner 认为 C-2 的读法不对（即"通知人也算越界"），改动范围是
`q2c/transport.notify` 与相关 3–4 格判据，不影响协议与传输主干——这条我按现有读法继续做，
不等答复，并在 `Q2C-v0.1.0-RELEASE-REPORT.md` 的 KNOWN_LIMITATIONS 里点名。
