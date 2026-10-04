# Q2C-BOUNDARY-AUDIT —— 产品边界审计（任务书 Q2C-PRODUCT-01 §3）

审计对象：**当前已验证的实现**（fix02 交付根 `<本地已验证根>/q2c-wakeup-fix02`，
内核 `relay.py` 84 个顶层函数／2900 行；工装与插件层见 §B）。
审计口径：**只做分类与坐标登记，不删任何证据与历史**。分档含义：

| 档 | 含义 |
|---|---|
| `KEEP_AS_TRANSPORT` | 它做的本来就是"投递／关联／幂等／送达确认／重投／会话映射／进程与凭据安全"，随传输层进产品 |
| `RENAME_AS_TRANSPORT` | 行为是传输层的，但**名字或词表**在说项目真相（COMPLETED／review／release）；保留行为，换名换边界 |
| `DEPRECATE` | 作为取证/验证件仍然有用，但不进产品交付路径（留在实验根） |
| `REMOVE_FROM_PRODUCT_PATH` | 它在断言项目/发布真相（定级、批准、放行闸、重试预算、调度）；产品包里不许存在。原件与证据一律不动 |

七类禁责的缩写：**AG** 验收定级｜**PC** 项目完成｜**RA** 评审批准权｜**WR** 工作流恢复｜
**RB** worker 重试预算｜**PS** 项目调度｜**RG** 放行闸门。

---

## §A 内核 `relay.py` 逐点清单

### A-1 词表与信封（协议层的地基）

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:67` `STATES=["RECEIVED","QUEUED","SENT","RUNNING","COMPLETED","BLOCKED"]` | 记录状态机的全部取值 | PC | `RENAME_AS_TRANSPORT` | `COMPLETED` 说的是"审核这件事办完了"，是项目完成词。行为（谁在途、谁排队）是传输事实，换成任务书 §4 的 11 枚传输态即可。**丢掉它的代价**：`is_already_reviewed`、`running_count`、`recover`、`advance` 四处都按这个名字判定，换名必须同批改，否则去重与单飞一起失效 |
| `relay.py:68` `VERDICT_RE = APPROVED\|CHANGES_REQUESTED\|HUMAN_REQUIRED\|UNKNOWN` | 认审核结论的四个词 | RA | `REMOVE_FROM_PRODUCT_PATH` | 这是"这份码行不行"的批准权词表，产品不许持有。产品里载荷是**不透明文本**；"回复合格不合格"改由发送方给的结果谓词决定。**保护措施不能丢**：结论必须是**解析出来的字段**且整行逐字——这条纪律以另一种形式保留在 ACK 判定里（见 A-3） |
| `relay.py:70-72` `ACCEPT_RE = ACCEPT: ACCEPTED\|REJECTED` | 认验收结论的词表 | AG | `REMOVE_FROM_PRODUCT_PATH` | 同上，且注释里已写明"验收与审核两套词表故意不同"——混用曾真造成过 REJECTED 被当通过。产品不引入第二套结论词表 |
| `relay.py:73-77` `ACK_LINE`（独立整行 `ACKED`，正文含词不算） | 送达确认的结构化判据 | — | `KEEP_AS_TRANSPORT` | 这是 §2 明列的 transport ACK。它的出身是一条真事故：`is_error=true`、退出码 7、正文里同样带 `ACKED` 的回执被旧版当成交达 |
| `relay.py:77` `MIN_BODY=60` | 回复必须带依据正文才采信 | RA 边缘 | `RENAME_AS_TRANSPORT` | 换个中立名字（`MIN_RESULT_BODY`）：它证明的是"这条回复不是空口令复述"，不是"内容质量合格"。质量判断归发送方 |
| `relay.py:64-66` `REQUIRED=[task_id, request_id, commit_sha, repo_path, qoder_session_id, test_exit_code, evidence_paths, review_request]` | 交接信封的必填字段 | PC/RA 边缘 | `RENAME_AS_TRANSPORT` | `test_exit_code`／`review_request` 是 CI 与评审事实，属发送方 `payload`/`metadata`。换成 §4 的 16 字段信封；**"缺字段即拒收"这条硬行为保留** |

### A-2 收单、去重、单飞

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:498-516` `validate()` | 缺字段／SHA 形状／证据路径存在／repo 是 git 仓 ⇒ 拒收 | — | `KEEP_AS_TRANSPORT` | 拆开看两半都留：形状校验＝协议层；路径与仓库可达性＝§8 产物引用完整性＋§11 路径校验。**不加回** `review_request` 的语义判断 |
| `relay.py:516-555` `ingest()` | 扫 inbox 收单、拒件改名归档、重复信封只留副本 | PS 边缘 | `RENAME_AS_TRANSPORT` | 收单＝receive，留。但产品按"Push First"，**扫描只用于漏单恢复**，不是常态泵；入口改名 `ingest_pending()` 并在文档写明这一条 |
| `relay.py:555-563` `is_already_reviewed()` + `555 dedupe_key=task_id+commit_sha[:12]` | 同一提交已出过结论就不再送审 | PC | `RENAME_AS_TRANSPORT` | 本质是 §2 的 **idempotency**，机制必须留；语义要收窄成"同一幂等键的**消息**不重复投递"，不再写 `state=COMPLETED`（那是在替项目宣布完成） |
| `relay.py:563-567` `running_count()`；`2071-2076` 单飞早退 | 同一时刻最多一个模型轮在跑 | PS | `RENAME_AS_TRANSPORT` | 它看着像调度，实际守的是"同一条会话上两次唤醒会互相把答复认领掉"——这是传输事实。改名 `inflight_on(session)`，判据从"全局一个"收成"每条会话一个" |
| `relay.py:2018-2062` `lock_acquire()/lock_release()` | 跨进程 fcntl 锁，第三者持锁时本轮不派发 | — | `KEEP_AS_TRANSPORT` | 防并发双投递的那道闸，随传输层走 |

### A-3 投递 · 唤醒 · 送达确认（产品的核心，全部留）

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:587-591` `review_bind_nonce()`；`2423-2432` `bind_ok()`；`1080-1098` `review_bind_ok()` | 每轮派发一枚一次性串，回复必须逐字整行回出 | — | `KEEP_AS_TRANSPORT` | 反 replay／反"拿上一轮看起来核读过的话复述一遍"，是 §2 的 correlation＋ACK。名字里的 review 去掉，改叫 `handoff_nonce` |
| `relay.py:591-596` `review_send()`（直接 `raise AssertionError`） | 把"发送+唤醒"拆成两步的防退化闸 | — | `DEPRECATE` | 函数本身废弃，但**这道"不许合并成一步"的守卫要留在测试里**：拆开才有 `SENT`／`RUNNING` 之间被杀那一格可测 |
| `relay.py:596-640` `enqueue()` | 派发文落盘＋入队到 provider 线程＋登记 attempts | — | `KEEP_AS_TRANSPORT` | 里面的 `codex queue --thread …` 与 `codex exec resume` 属 Codex 适配器（见 A-6），核心不许直写 CLI。`RELAY_STOP_BEFORE_SEND`／`RELAY_STOP_AFTER_QUEUE`／`RELAY_STOP_AFTER_RESULT` 三枚故障注入接缝**必须一起迁**——§14 的 restart recovery 全靠它们 |
| `relay.py:753-859` `wake_and_collect()` | 唤醒同一会话跑完这轮、起进程**立刻**登记 pid＋lstart、等、取回文本 | RA 边缘 | `KEEP_AS_TRANSPORT` | 主体＝§6 的 start/resume＋§9 的 started 时刻。要拆掉的只有 `VERDICT_RE.findall(out)` 那一格采信闸（换成结果谓词），其余四道（rc、流终态、正文非空、BIND）留 |
| `relay.py:859-910` `parse_ack()` | 五道送达判定：rc=0／可解析 `type=result`／`is_error=false`＋`subtype=success`／会话号相等／整行 ACKED／回出本轮 BIND | — | `KEEP_AS_TRANSPORT` | 全套留。注释里的三次真复现（子串冒充、别人的会话号、JSON 外面补一行）就是它的存在理由 |
| `relay.py:910-972` `deliver_back()` | 把结果送回原会话，失败回执改名留存再写新件 | PC/RA 边缘 | `RENAME_AS_TRANSPORT` | 投递与"失败证据不许被覆盖"两条留；派发文正文里"若为 CHANGES_REQUESTED 请整改、不要回退工作树"是评审指令，改由发送方 payload 提供 |
| `relay.py:1048-1080` `notify_delivered()` | 通知腿的结构化送达判定（退 0 ≠ 送达） | — | `KEEP_AS_TRANSPORT` | §2 的 transport ACK 在"通知"这一路的同款判据 |
| `relay.py:2199-2213` `abs_cmd()` | 相对可执行名按根解析 | — | `KEEP_AS_TRANSPORT` | §11 的安全进程启动＋工作区限制的一环 |

### A-4 恢复（重启不重复消费）

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:2605-2747` `recover()` | 七路证据依次查：结果在案／捕获件／会话落盘件／活进程／队列未消费／确认没执行／其余为**未知** | WR 边缘 | `KEEP_AS_TRANSPORT` | 这是 §2 明列的 transport recovery，也是整包最硬的一条安全性质：**未知 ≠ 确认没有执行**，读不动就停手，绝不自动重发。里面 `kind == "accept"` 那一支（1638 起的验收腿分流）随验收一起出产品路径 |
| `relay.py:2213-2243` `result_file()/usable_result()/register_result()` | 结果原件＋SHA，重启后按摘要验货再复用 | — | `KEEP_AS_TRANSPORT` | "只补送达、绝不再花一次模型"的落点；§8 的 reference+digest 在这里已经有原型 |
| `relay.py:2561-2605` `capture_verdict()`；`2463-2525` `transcript_verdict()`；`2441-2463` `call_terminal_failed()` | 从捕获件／会话落盘件反证这一轮跑完没有 | RA 边缘 | `RENAME_AS_TRANSPORT` | 判的是"调用是否收口"，属传输事实（`turn_terminal`、`stream_gate` 同一族）；把返回的"结论词"改成"原始回复文本＋是否合格" |
| `relay.py:1488-1509` `stream_gate()`；`2525-2561` `_stream_terminal()` | 事件流必须以合格终态收尾，`turn.failed`／无终态一律不采信 | — | `KEEP_AS_TRANSPORT` | R19 独立复验抓到的正是"退 0＋正文够长＋BIND 对得上"但事件流 `turn.failed` 的半截件。这条闸是送达判据的一部分 |
| `relay.py:2285-2313` `pid_state()` | pid＋lstart 双对，三态 alive／gone／unknown | — | `KEEP_AS_TRANSPORT` | §11 进程清理＋fail-closed 的"未知不折成没有"；`unknown` 与 `gone` 混过一次，成本是一整条重复审核 |

### A-5 验收／定级／放行（产品路径之外，逐条列出丢了什么）

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:1010` `ACCEPTANCE_GRADES=("A","B","C")`；`1013` `DEFECT_CLOSED_STATUSES` | 交付定级档位／缺陷"算已闭"的状态白名单 | AG/RG | `REMOVE_FROM_PRODUCT_PATH` | 定级权在 Owner。丢的保护：状态词表由代码单点定义，改状态名开不了闸——这个**方向**要在产品里以另一种形式保留（未知取值一律 fail-closed 拒绝，见 A-7） |
| `relay.py:1136-1202` `acceptance_gap_summary()` | 把台账分四档未闭、给出唯一判词（`PENDING_ACCEPTANCE_GAP`／`NO_PENDING_ACCEPTANCE_GAP`） | AG+PC+RG | `REMOVE_FROM_PRODUCT_PATH` | 它在替项目宣布"有没有欠账"。丢的保护三条要单独迁走：①台账读不到 ⇒ `LEDGER_ABSENT_NOT_ACCEPTABLE`，绝不折成"零缺口"；②逐笔送达证据不许被全局配置顶替（R4-A 第 4 条）；③分类互斥、未闭数取并集去重 |
| `relay.py:1016-1048` `unreleasable_defects()` | 数登记册里还不许放行的缺陷；册子读不动返回 `None` 而非空表 | RG | `REMOVE_FROM_PRODUCT_PATH` | 放行权在 Owner。`None`（读不动）与 `[]`（读到、零项）是两回事——这条要作为**产品的通用 fail-closed 约定**留下 |
| `relay.py:972-985` `machine_acceptance_configured()`；`985-1016` `human_escalation_configured()` | "有没有执行器能跑验收"／"结论到不到得了人的眼睛（＋送达回执文件）" | RG | `REMOVE_FROM_PRODUCT_PATH`（人侧那半 `RENAME_AS_TRANSPORT`） | "配了线程号≠有人收到"这条被 F2-3/4/5 三条 REJECTED 换来的**两命题不许并一格**的纪律，保留在适配器 `capabilities()`＋逐笔回执里；"验收执行器"这一格随验收出产品 |
| `relay.py:1202-1232` `create_acceptance()` | 交付 acked 后**自动再生成一张验收单**并入队 | AG+PS+WR | `REMOVE_FROM_PRODUCT_PATH` | 这是编排器行为（产品在替工作流新建任务）。丢的保护只有"未 acked 不建单＝没送达的东西没有验收对象"——这条本身是幂等纪律，留在产品：自动串单改由发送方带 `correlation_id` 再 `q2c send` 一次 |
| `relay.py:1232-1387` `build_accept_prompt()` | 验收派发文（候选坐标、复跑指令、nonce、门禁清单） | AG+RA | `REMOVE_FROM_PRODUCT_PATH` | 内容整体降级为 `examples/` 里的一份示例派发文，不进核心。它带的两条工程事实（"先定这腿走哪条路再决定给不给候选"、"给正在变的活根当验收对象＝判决没有对象"）写进 ADAPTERS.md 的坑位说明 |
| `relay.py:1387-1466` `_snap_ignore()/_candidate_rows()/_candidate_manifest()/make_candidate()` | 从活根导一份**可复跑快照**＋清单指纹，重派复用同一枚指纹 | — | `RENAME_AS_TRANSPORT` | 剥掉"验收"外壳，它就是 §8 的 `evidence_package` 产物引用：reference＋digest、排除运行态、重试时**对象不变**。必须随产品走 |
| `relay.py:1466-1484` `preflight_manifest()` | 给预跑日志算指纹、与派发文里的号对得上才算同一枚 | — | `RENAME_AS_TRANSPORT` | 同上：产物引用的完整性核验 |
| `relay.py:1484-1559` `accept_file/accept_stream_gate/accept_rc_file/_accept_rc/qualify_accept()` | 验收结论四道采信闸 | AG | `RENAME_AS_TRANSPORT` | 结论词表（`ACCEPT: ACCEPTED/REJECTED`）删；**四道闸的形状留**：rc、结论必须是**逐字最后一行**、正文非空、BIND 逐字整行——这形状是"回复可归因于本轮"的通用 ACK 判据，`qualify_accept` 的 `INVALID` vs `UPSTREAM` 区分也留（读得动但不合格／调用不可用，两档不能并） |
| `relay.py:1559-1638` `collect_accepts()` | 上一拍没等完的腿：还在跑就等（不改状态、不加 attempts、不重派），跑完按同一枚 nonce 收 | WR+AG | `RENAME_AS_TRANSPORT` | 剥掉验收语义，它是通用的"迟到答复认领"机制，防的是 R9/R10 实证的**同一请求两份候选、两枚 nonce、两条互相打脸的答复**。留机制，收进 `pump()` 的 in-flight 分支 |
| `relay.py:1638-1867` `run_accept()` | 派发验收一轮（凭据闸、候选、真腿/桩腿分流、超时） | AG+PS | `REMOVE_FROM_PRODUCT_PATH` | 其中三条通用保护迁进适配器与 `pump()`：①先定这腿走哪条路再出 prompt；②候选导出失败 ⇒ **本轮不叫人**（拿残缺对象去要判决没有对象）；③未显式授权真调用 ⇒ 一律 UNAVAILABLE 不派发 |
| `relay.py:1867-1885` `finish_accept()` | 写验收终态、回写父单、"只有不合格才叫人" | AG+RA+RG | `REMOVE_FROM_PRODUCT_PATH` | 通知的**触发策略**属评审权；通知的**执行与回执**留（A-6） |
| `relay.py:2062-2199` `advance()` | 一轮推进：收迟到件→单飞早退→取 QUEUED 首→恢复优先→派发或补唤醒→送达→重试/上限→串验收单 | PS+RB+AG 混合 | `RENAME_AS_TRANSPORT` | 拆成三件：`pump_once(session)`（队列泵＋单飞，留）、`deliver+ack`（留）、`attempts/deliv_attempts >= RETRY_MAX ⇒ BLOCKED` 改写成 **DELIVERY_FAILED（有界重投、绝不重跑任务）**，即 §5 的实现点。串验收单那一格（`if machine_acceptance_configured(): create_acceptance(...)`）删 |
| `relay.py:2764-2806` `_main_body()` 的 `backfill` | 把"该叫到人却没叫到"的历史笔补叫一次 | RG | `REMOVE_FROM_PRODUCT_PATH` | 这是对旧台账的**补救动作**，属项目历史；产品不背着补发通知的义务。它的纪律值得抄：必须显式指定根、原件改名不覆盖、每一笔留一条事件写明"为什么现在才叫" |
| `relay.py:2807-2818` `gap` 子命令 | 未闭台账的唯一出口 | AG+RG | `REMOVE_FROM_PRODUCT_PATH` | 产品给 `q2c list`／`q2c trace`（通信事实），不给缺口判词 |
| `relay.py:2846-2860` `invalidate` 子命令 | 作废一条完成记录、摘去重键允许重审 | RA+PC | `REMOVE_FROM_PRODUCT_PATH` | "作废"在产品里对应 `cancel`（取消**投递**），不覆盖"结论不合格"的裁定权。它的"不删记录、只标 invalid＋留原因"处理习惯要沿用 |

### A-6 会话与凭据

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:61-63` `STATE/review_session_id`；`1638-1650` 线程号三级回退（env→文件→上一轮线程） | 决定"用哪条线程去发" | — | `RENAME_AS_TRANSPORT` | 这就是 §7 的 provider session 绑定，但要升一层：产品要有**稳定的 `q2c_session_id`**，provider 线程号只是它的一个可换绑定。注意在册教训：拿线程号顶"有没有人收到"是把两条命题并成一条（`1002-1010` 注释） |
| `relay.py:464-497` `rollout_of()`／`walk_sessions()`；`2326-2423` `rollout_turns/turn_terminal/_rollout_texts/_content_text`；`640-661` `last_turn_text()` | 读 `~/.codex/sessions/**/*.jsonl` 内部格式找这一轮的答复 | — | `RENAME_AS_TRANSPORT`（**移入 Codex 适配器**） | 任务书 §9/10 硬令：核心不得依赖 Codex 私有库/内部 JSONL 形状/不稳定 `~/.codex` 结构。这套读法整体搬进适配器 `receive()`，并在文档写明"内部格式非稳定依赖"，SDK＞公开 CLI＞公开兼容协议逐档降级 |
| `relay.py:2243-2285` `queue_store_pending()` | 开 Codex 私有队列库数还有几条未消费 | — | `RENAME_AS_TRANSPORT`（移入适配器，标最高风险） | 它是 §4 恢复阶梯里"只补唤醒不重复入队"的唯一证据源，不能凭空删；但它现在**直读私有 SQLite**，属核心越界。搬进适配器并写清：读不到 ⇒ `UNKNOWN`，恢复阶梯落到"未知⇒停手"，绝不因读不到而放行重发 |
| `relay.py:682-691` `_cred_probe_applies()`；`691-728` `credential_probe()`；`728-753` `credential_gate()` | 派发前问"这一侧 CLI 能不能无人值守完成一次最小认证"，stdout 直接丢弃 | — | `KEEP_AS_TRANSPORT` | §11 的凭据边界与 §14 的 credential-unavailable 用例。六条硬规矩原样随产品：不读钥匙串、不调会弹界面的取密钥命令、不改钥匙串、只答可否无人值守、**判不出来按不可用**、回包只有 rc／耗时／分类 |
| `relay.py:1885-1921` `human_ack_path()/parse_human_ack()`；`1098-1136` `human_delivered()` | 逐笔"送到人没有"：只认指名到这笔的结构化回执（整词匹配，不子串） | — | `KEEP_AS_TRANSPORT` | 人也是接收方，这条就是人类通道的 ACK。R9 复现过的冒充（另一行 `R9-A-OTHER rc=0`）与 `rc=0` 命中 `rc=01` 两个坑，靠整词＋rid 相等挡住 |
| `relay.py:1921-2018` `escalate()` | 叫人：待办件必落盘，配了会话号才发消息，同一笔只发一次 | RA/RG 触发语 | `RENAME_AS_TRANSPORT` | 机制＝向 human 接收方投一条消息并留回执（§2 的 delivery＋ACK）。触发标签里 `REJECTED`／`UNGRADED` 那类评审语删；`INTERACTIVE_AUTH_BLOCKER`／`DELIVERY_UNACKED`／`EXEC_RECORD_UNKNOWN` 是传输故障，保留为 `HANDOFF_FAILED` 的原因码 |

### A-7 落盘、并发与日志

| 坐标 | 它实际在做什么 | 命中禁责 | 分类 | 理由／若移除会丢什么保护 |
|---|---|---|---|---|
| `relay.py:86-119` `log()` | 事件流 JSONL 追加 | — | `KEEP_AS_TRANSPORT` | §9 trace 的底座。**必须补的两件事**：现在多处把 `p.stdout[:160..200]` 原样写进事件（`enqueue/wake/deliver`），§11 要求先过脱敏；trace 要带 message_id／correlation_id 才查得出"谁发谁收" |
| `relay.py:123-175` `ledger_lock()/ledger_txn()/_read_rev()/_ledger_hash()`；`119-123` `StaleLedgerError` | 整轮临界区＋写版本号＋哈希，第三者绕锁直写就**响**不静默 | — | `KEEP_AS_TRANSPORT` | QA-DEBT-1（P1）的落点：两个进程并发时后写不能整块盖掉前一笔状态转移 |
| `relay.py:182-296` `Ledger/LedgerUnreadable/_bare/_adopt/load()/save()` | 读不到就抛、非法记录摘出、原子落盘 | — | `KEEP_AS_TRANSPORT` | "读不到 ≠ 空"这条产品级约定在这里（与 A-5 的 `unreleasable_defects` 同一族，那处删、这里的**方向**留） |
| `relay.py:342-464` `_disk_ledger()/merge_ledger()/_merge_list()`＋`200-239` `_base_remember/_prov_remember` | 写前重读磁盘、三方合并、逐字段保留来源 | — | `KEEP_AS_TRANSPORT` | §2 幂等与恢复的物理前提；合并方向（磁盘优先还是本笔优先）在册断言盯着 |
| `relay.py:2747-2764` `main()`；`2761` `READ_ONLY_ACTIONS` | 只有会写的动作进临界区；只读动作一个字节不许写 | — | `KEEP_AS_TRANSPORT` | R9 第 3 条抓到 `gap` 在建目录＋取写锁，"只读核查"改写了被核查的根。这条要成为产品 CLI 的硬约束（`inspect/list/trace` 必须证明不写） |
| `relay.py:2822-2845` `scan`；`2861-2866` `run-one` | 收单→恢复→推进一轮／把某张单跑到出结果 | PS 边缘 | `RENAME_AS_TRANSPORT` | 对应 `q2c send`＋`q2c pump`（内部）；"跑到出结果"里的"结果"只到 ACK 为止，不替任务宣布完成 |
| `relay.py:2867-2875` `status` | 打台账（state/verdict/attempts/acked） | PC 边缘 | `RENAME_AS_TRANSPORT` | 变 `q2c list`／`q2c inspect`，字段换成传输态；`verdict` 字段在产品里是 `result_ref`（指向原件），不是结论词 |
| `relay.py:2807-2818` `loop`（120 轮 × POLL_S，全 COMPLETED/BLOCKED 才停） | 常驻轮询把整条链跑到终态 | PS+PC | `REMOVE_FROM_PRODUCT_PATH` | 它是"跑到项目终态"的自轮询循环，且与"无常驻"的产品前提冲突。产品只有：`send` 同步一腿＋`retry-delivery` 手动／外部调度触发。**丢的保护**：无。它提供的确定性等待在测试里用显式两拍（enqueue 拍／collect 拍）覆盖，不需循环 |
| `relay.py:2876-2900` `reset` | 清台账与 inbox（唯一会删东西的子命令，必须显式设根） | PC | `REMOVE_FROM_PRODUCT_PATH` | 产品不许有"一键清历史"；测试夹具改用临时根。它那条"删除动作必须显式指定目标根"的闸保留为通用纪律 |
| `relay.py:2764-2772` `_main_body` 开头四个 `makedirs` | 非只读动作前建目录 | — | `KEEP_AS_TRANSPORT` | 配 §11 的路径校验一起：根必须由配置给出、不许落在只读候选里、写前核可达 |

---

## §A 小结：内核里真正"必须随产品走"的十二条保护

按任务书 §15 与本次审计，产品路径里一条都不能少的机制（其余全部是"名字换掉、语义收窄"）：

1. 送达判定五道闸（rc／结构化记录／自报成功／会话身份／整行 ACKED＋本轮绑定串逐字回出）— `relay.py:859-910`
2. 通知腿"退 0 ≠ 送达"＋逐笔回执整词匹配 — `relay.py:1048-1080`、`1885-1136`
3. 迟到答复用**同一枚** nonce 认领，绝不重派（重派＝两份对象两枚串两条打脸的答复）— `relay.py:1559-1638`、`relay.py:2672-2685`
4. 恢复阶梯七路证据，**未知 ≠ 确认没有执行**，读不动一律停手 — `relay.py:2605-2747`
5. 结果原件＋SHA：在案就只补送达，绝不再花一次模型 — `relay.py:2213-2243`
6. 进程归属：起子进程立刻登记 pid＋lstart，探针三态 alive/gone/unknown — `relay.py:753-859`、`2285-2313`
7. 事件流终态闸：`turn.failed`／无终态一律不采信（哪怕 rc=0 且正文够长）— `relay.py:1488-1509`、`2525-2561`
8. 台账整轮 flock＋写前重读三方合并＋陈旧检测会响 — `relay.py:123-464`
9. 只读动作一个字节都不写（不建目录、不取写锁）— `relay.py:2747-2764`
10. 凭据前置检查六条（不读密钥、判不出来按不可用、stdout 丢弃）— `relay.py:682-753`
11. 有界重投＋到上限停手等人（自动重试自愈不了认证与断链）— `relay.py:2134-2146`、`2187-2193`
12. 失败证据不许被覆盖（改名 `.failed-N` 再写当前件）— `relay.py:960-970`

§B（工装／插件层）由同一次审计的另一半补齐，见下节。

---

## §B 工装与插件层（待并入）

## §B 工装与插件层（独立只读复核产出，184 个站点）

这一节由一次**独立的只读复核**产出（另一个会话，未参与实现），坐标全部来自实读行号。
被审目录零改动。覆盖：`verify-fix02.sh`、`checks-fix02.sh`（2773 行全量）、`mutations-fg.sh`、
`mutate.py`（888 行，约 80 枚变异逐一归类）、`checks.sh`／`checks-fix01.sh`（抽样归类职责）、
`preflight-*.sh`、`delivery-note.sh`、`manifest-check.sh`、`notify_human.sh`、`proc_probe.sh`、
各桩件、`round4-real.sh`、`accept-real.sh`、`tools/denybin`、`state/`（仅列名），
以及交付的 MCP 插件 `<插件交付目录>/q2c-plugin` 的五枚暴露工具与 selftest 九格。
未覆盖部分在末尾自报。

### 逐点清单（第 1 批：verify-fix02.sh ＋ checks-fix02.sh 前半 K1–K48）

### verify-fix02.sh

| 坐标 | 它实际断言了什么(大白话) | 命中禁责 | 分类 | 理由/若移除会丢什么保护 |
|---|---|---|---|---|
| verify-fix02.sh:25-32 `Q2C_DRIVER_DEPTH` 递归保险丝 | 统一入口被子套件再叫一次就直接拒（防 150 枚进程自我繁殖） | 无 | KEEP_AS_TRANSPORT | 纯进程繁殖/再入安全，任务书 §15 点名要保留的"recursive runner/reentry"回归 |
| verify-fix02.sh:39-43 模式白名单 fail-closed | 认不出的模式立刻退 2、一个子进程都不生 | 无 | KEEP_AS_TRANSPORT | unknown-mode fail-closed，属传输安全 |
| verify-fix02.sh:88-93 被复验对象缺失即 ABORT | 交付根/清单读不到⇒退 2 不计数，防止"没有对象也能判绿" | 无 | KEEP_AS_TRANSPORT | fail-closed 读数纪律，可移植进产品测试 |
| verify-fix02.sh:55-78 `run_suite` 剥 RELAY_*＋BASELINE 根剥离 | 调子套件前剥净中转开关，防夹具被环境喂成另一种形状、防"身份错的绿" | 无 | KEEP_AS_TRANSPORT | 测试环境隔离纪律；产品套件沿用 |
| verify-fix02.sh:104-113 A段 两份交包清单全量复算 | 已交付对象 SHA 复算 0 失败＝对象没被改过 | 7(弱) | RENAME_AS_TRANSPORT | 机制=artifact 引用+digest 完整性（q2c 拥有），但"交包不许动"话术是发布纪律；改名 artifact-integrity check 即可 |
| verify-fix02.sh:116-125 relay.py 与基线必须不同 | 本轮确实动过实现（哈希不等） | 1 | REMOVE_FROM_PRODUCT_PATH | 这是"本轮改动有效性"的验收性断言，产品发布不该钉"实现必须变"；证据留 history |
| verify-fix02.sh:129-142 checks.sh 哈希登记册核对 | 工装改动必须留登记+理由，否则红 | 3,7 | DEPRECATE | 评审签字文化的工装自审（Gate 3 词汇）；对 v0.1 产品路径无必要，留在 legacy 实验根 |
| verify-fix02.sh:147-164 B/C/D 段跑四套判据＋断言数≥34 | 统一入口把全部套件跑齐、覆盖数不许缩水 | 1 | DEPRECATE | 套件计数门是"本轮验收"的口径；产品 CI 应改为按用例本身跑，不用"计数不缩"当门 |
| verify-fix02.sh:167-178 E1 复跑惰性（快照前后一致） | 任何判据跑完不许改写被复验对象 | 无 | KEEP_AS_TRANSPORT | 幂等/只读性=传输层拥有；这是最值钱的机器性质之一 |
| verify-fix02.sh:185-206 E1c/E1b 未结缺陷册闸（Q2C_WAIVE_DEBT 豁免） | OPEN-DEFECTS 里还有没签字的缺陷⇒整个复验入口判红；主理人一句话可豁免 | 1,3,7 | REMOVE_FROM_PRODUCT_PATH | 缺陷=项目真相；"待签字"=评审批准权；`Q2C_WAIVE_DEBT=1`=人批通道。产品不得有。证据/history 保留，仅从产品路径剔除 |
| verify-fix02.sh:216-223 F1 三态进程探针 | 残留 relay 进程：NONE/N=k/UNMEASURED，测不到不折成干净 | 无 | KEEP_AS_TRANSPORT | 进程清理安全（§11 safe process spawning / §14 process cleanup） |
| verify-fix02.sh:224 F2 wake.lock 残留 | 状态根不留悬挂锁 | 无 | KEEP_AS_TRANSPORT | 传输层锁卫生 |
| verify-fix02.sh:225-235 F3 台账无 SENT/RUNNING 悬挂单 | 台账里停在 SENT/RUNNING 的=红；读不到台账=UNREADABLE 也红 | 无(边界) | RENAME_AS_TRANSPORT | 断言的是"在途消息未闭环"，是通信真相；但 verify 用它当放行门。语义保留、命名从"闸门"改"trace 在途读数" |
| verify-fix02.sh:237-243 G 变异反证入口 | 每枚变异必须打红对应反例 | 1 | DEPRECATE | 变异工装是验证件质量自检，属实验根，不进产品包 |
| verify-fix02.sh:246-252 H 真腿入口(accept-real) | 真实模型调用腿必须绿、验收线程与审核线程不同 | 1,3 | DEPRECATE | 真腿依赖上游付费会话；作为回归证据保留，不作产品发布门 |

### checks-fix02.sh（K 组前半）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| checks-fix02.sh:1-22 头注释契约＋:23-33 RELAY_* 前缀拒绝闸(rc=3) | 契约写明"验收单/ACCEPTED 判据"；环境带任何 RELAY_* 拒跑 | 1,2(契约词)/无 | RENAME_AS_TRANSPORT | 拒绝污染环境的机制是纯传输测试卫生(KEEP 性质)；但契约 1)-5) 通篇是"验收"词汇——机制留、话术改 HANDOFF_RESULT/ACK |
| :198-207 K1 acked 后自动建"验收单"并回填 acceptance=ACCEPTED | 送达闭环后触发一次独立复验并登记结论 | 1,2,3 | REMOVE_FROM_PRODUCT_PATH | 整条"验收"支线（ACCEPTED 回填交付记录）就是评审批准权建模；transport 只该到 HANDOFF_ACKED/HANDOFF_RESULT。桩腿机制（新建会话、回填 parent 字段）如保留须改名 handoff-result |
| :209-214 K2 REJECTED⇒升级到人、不许写成通过 | 复验拒收落 NEEDS-HUMAN 待办 | 1,3 | REMOVE_FROM_PRODUCT_PATH | 拒收语义=人裁；"不许写成通过"这条反向保护可移植为"未 ACK 不许登 DELIVERED" |
| :216-220 K3 无依据正文⇒INVALID 不算通过 | 采信需要足够依据 | 1,3 | REMOVE_FROM_PRODUCT_PATH | 质量判据（依据长度）属 code-quality/acceptance 判断 |
| :222-226 K4 回不出本次 BIND nonce⇒不采信复述 | 答复必须逐字回出本次派发内的随机绑定串 | 无 | KEEP_AS_TRANSPORT | 这就是 correlation：nonce/BIND 是 correlation_id 回出校验，防旧文本冒充本次回执。产品必须保留并改名 correlation_check |
| :228-234 K5 调用不可用⇒重试到上限(2)后 BLOCKED+UNAVAILABLE+叫人 | 叫醒次数有硬上限，绝不无限重试 | 5 | RENAME_AS_TRANSPORT | 机制=transport retry 投递次数上限（q2c 拥有 §5"retry 只重投不重跑"）；但上限叫 RELAY_RETRY_MAX、落"验收预算"话术。改名 delivery_retry_max，断言本身是传输安全 |
| :236-245 K6 重复 scan 不重建验收单、不再叫醒 | 幂等：同一交付只触发一轮 | 无 | KEEP_AS_TRANSPORT | duplicate delivery/idempotency 本体（按 dedupe_key 断言"仍只一张"） |
| :247-258 K6b run-one 再驱动不重建、不再叫醒 | 显式重放请求号也不重复副作用 | 无 | KEEP_AS_TRANSPORT | 0 unexpected duplicate handoff effects 的直接机器测试 |
| :260-274 K7b 审核有结论但送达被拒⇒不建下一环 | acked=false 时不得推进 | 无 | KEEP_AS_TRANSPORT | 送达 ACK 判据（is_error/退出码结构判）；"COMPLETED"字样若入产品须换 DELIVERED/ACKED |
| :276-280 K7 未 acked⇒不生成验收单 | 同上反向 | 无(1 词面) | KEEP_AS_TRANSPORT | ACK 门控推进=传输；用例名里的"验收"要改 |
| :282-291 K8 打扰人只认显式 RELAY_HUMAN_SESSION_ID；同笔只通知一次 | 不猜会话号；重复 scan 不重复通知 | 无 | KEEP_AS_TRANSPORT | 逻辑会话映射显式化＋通知幂等，纯传输 |
| :294-313 K9 审核方回旧 nonce⇒VERDICT 不采信、不推进 | 答非所问的答复不能闭环本请求 | 无 | KEEP_AS_TRANSPORT | correlation 拒串账；钉 relay.py capture/BIND 校验行为 |
| :315-319 K10 审核路失败到上限⇒BLOCKED+落到人 | 失败升级不挑路径 | 5 | RENAME_AS_TRANSPORT | 投递失败到上限后"落到人"=DELIVERY_FAILED+trace；"叫人"是人机通知通道(传输拥有)，预算措辞需去项目化 |
| :321-329 K11 验收/审核用两条不同线程 | 独立 logical session 不复用上下文 | 无 | KEEP_AS_TRANSPORT | 逻辑会话映射：不同用途不同 q2c_session；防"上一轮上下文当本轮事实"其实是防串话=correlation 安全 |
| :331-342 K12 没配执行器⇒不建单、写 NOT_CONFIGURED、零模型调用 | 未配置绝不偷偷调真实付费执行器 | 无 | KEEP_AS_TRANSPORT | fail-closed adapter capability：能力缺失显式记账。产品要（adapter failure 测试同类） |

### checks-fix02.sh（K 组后半 ＋ U 组，:1407-2773）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| :1407-1501 K48 迟到答复收回/进程在跑不收不重派 attempts 不涨/turn.failed 不采信 | 在途答复的收回纪律：同一绑定串收回、在途不重复投递 | 无 | KEEP_AS_TRANSPORT | correlation＋duplicate-delivery 防护本体；钉 relay.collect_accepts。期望值里 COMPLETED/ACCEPTED 字样→改名 |
| :1503-1511 K49 三套件带 RELAY_* 拒跑 | 环境隔离闸在全部套件生效 | 无 | KEEP_AS_TRANSPORT | 同上卫生闸 |
| :1513-1590 K50 恢复器不把验收单当审核单（RUNNING 原地/SENT 有捕获件转 RUNNING 不换 nonce/纯 SENT 可重派同 nonce/不落审核三档出口） | 重启后对"在途不同种类消息"分别正确恢复，绝不误重发 | 4(词)/无 | KEEP_AS_TRANSPORT | 这就是 transport recovery（非 workflow recovery）：状态在途恢复、防孤儿答复。钉 relay.recover 行为 |
| :1592-1621 K51 重派沿用登记 nonce、派发件不重写、留痕 accept_prompt_reused | 重试不换关联凭据 | 无 | KEEP_AS_TRANSPORT | delivery-retry 保持 correlation_id 稳定——协议级性质 |
| :1623-1676 K52 真进程迟到：到点不杀不重派、答复不猜、收回后静默 | 等待窗口≠失败；收回≠重派 | 无(2 词面) | KEEP_AS_TRANSPORT | 进程/在途安全；"ACCEPTED 后无待办"里"待办"是人通知路由，可移植为"DELIVERED 后不再 escalate" |
| :1678-1693 K53 驱动尾巴预算 R4_TAIL_S 落在可执行行＋到点如实登记"仍在飞" | 驱动不许先收工造成答复孤儿；预算到点不折成结论 | 5(词)/无 | RENAME_AS_TRANSPORT | "预算"此处=等待子进程收尾的时间窗（进程清理），非 worker 重试预算；机制 KEEP，命名去"预算/round4"化。注意：判据是 grep 源码文本，弱测试形状 |
| :1695-1738 K54 真进程退 7 的迟到答复⇒BLOCKED、无 verdict、落人 | 形状合格≠调用成功，退出码说话 | 无 | KEEP_AS_TRANSPORT | adapter 失败诚实登记（§14 adapter failure） |
| :1740-1799 K55 探针 unknown≠跑完；rc 件 0/7/非整 三档 | 进程读数三态，读不出绝不折成终态 | 无 | KEEP_AS_TRANSPORT | 传输安全核心：fail-closed 读数 |
| :1801-1807 K56 verify 剥 RELAY_*（结构 grep） | 入口调子套件的方式被钉死 | 无 | KEEP_AS_TRANSPORT | 工装卫生（同样 grep 源码形状，弱测试形状） |
| :1809-1813 K58 真腿包装用 /bin/zsh 绝对路径＋rc 件同族 | 防"裸 zsh 起不来=无声失败" | 无 | KEEP_AS_TRANSPORT | 安全进程启动（§11） |
| :1815-1843 K57 候选导出排除 .zion-mcp 运行态日志、同树同指纹 | artifact 引用稳定可digest | 无 | KEEP_AS_TRANSPORT | reference+digest 产品拥有；钉 relay.make_candidate/_candidate_rows |
| :1845-1899 K59 即时派发拍也过终态闸 accept_stream_gate（failed 关/completed 开/无流退到 rc 件/error 关/理由可读/接线真把关） | 事件流终态=采信前提，两条腿共用一道闸 | 无 | KEEP_AS_TRANSPORT | 钉 relay.accept_stream_gate＋接线；"ACCEPTED"字样改名 RESULT_TRUSTED。接线格用"下一行必须 if not _ok"防无牙变异，好性质 |
| :1901-1941 K60 pid_state gone/never_started/unknown/他人进程≠gone | ps 报错≠进程没了 | 无 | KEEP_AS_TRANSPORT | 三态进程读数，移植必备 |
| :1943-1980 K61 锁主不可读或属他人⇒不拆锁；确认死⇒回收并持 | 残锁回收不伤活人 | 无 | KEEP_AS_TRANSPORT | 锁卫生/进程组安全；钉 relay.lock_acquire |
| :1982-2025 K62 有 pid 且探针 unknown⇒恢复不重派（等收回路）；无 pid⇒可重派同 nonce | 重启后再消费保护 | 无 | KEEP_AS_TRANSPORT | restart re-consumption 的直接机器测试 |
| :2027-2070 K65 送达判据=结构化回执(is_error/subtype/会话号/rc)五档＋真过程 delivered=false 仍算未闭 | "脚本跑完"≠"消息送到" | 无 | KEEP_AS_TRANSPORT | transport ACK 本体（§14 ACK/duplicate）。缺口判词 PENDING_ACCEPTANCE_GAP 字样属验收词，改名 |
| :2072-2152 K66 真件截断复放：本轮无终态不许借上一轮 task_complete；不借后轮；默认退回最后一轮 | 终态必须绑到答复所在那一轮 | 无 | KEEP_AS_TRANSPORT | 钉 relay.turn_terminal vs _stream_terminal 的区别；per-turn correlation，通信真相 |
| :2154-2230 K67 重派前清旧 rc 件；到点不杀；本轮真退 7⇒BLOCKED/父单 UNAVAILABLE/落人 | 上一轮凭据不许给本轮背书（防凭据串用） | 无 | KEEP_AS_TRANSPORT | credential/receipt freshness＋真实进程；同 K54 家族 |
| :2232-2260 K68 回执必须回出本轮审核原件 BIND 整行；带尾巴/没有/别轮/免检默认 都拒 | 执行方回执的 correlation 校验六档 | 无 | KEEP_AS_TRANSPORT | correlation 拒串账；钉 relay.review_bind_ok |
| :2262-2292 K69 验收派发文自带兄弟根坐标＋剥 RELAY_*（五枚齐全） | 给外部验收 Agent 的指令文案完备性 | 1,3 | DEPRECATE | 这是"叫谁来验收、怎么验收"的工作流文案工程；产品协议里对应 HANDOFF payload 的 workspace_ref/坐标完备性可借鉴，但"验收"编排不进产品 |
| :2294-2319 K71 无本轮 BIND 的回执⇒链不推进、不建下一环单；合规回执⇒照常推进 | ACK 判据含 correlation 项 | 无(2 词面) | KEEP_AS_TRANSPORT | 机制=DELIVERED/ACKED 推进门；"COMPLETED/生成验收单"→改名 |
| :2321-2350 K72 预跑件指纹键 写入方＝读取方（旧键读空、不留两把尺） | 防"恒读空的死保护" | 7 | DEPRECATE | 键名同一性机制可借鉴；preflight 件本身属发布预跑实验根 |
| :2352-2383 K73 verify 不许按调用方目录取件；哈希长度三处验；缺根 ABORT rc=2（行为档） | 入口取件锚定自身＋fail-closed | 无 | KEEP_AS_TRANSPORT | 读数可归因性纪律，移植 |
| :2385-2457 K74 假 codex 退 0 但流 failed/无终态⇒REF+拒登且拒因点名"终态闸"；接线审核/验收同闸 | 上游调用失败绝不登记成完成 | 无 | KEEP_AS_TRANSPORT | adapter failure＋stream gate 接线；钉 wake_and_collect |
| :2459-2476 K75 预跑汇总必须含仓库 unittest；全 0 才算过；空清单=用法错退 2 | 发布预跑门不许漏 CI 项 | 1,7 | DEPRECATE | preflight-verdict 聚合退出码机制诚实，但它门的是"能不能拿去叫人验收"=发布闸门职责；产品 CI 自己做 |
| :2478-2511 U1 旧快照 save 不覆盖新状态＋ledger_merge/ledger_stale_write_merged 留痕 | 台账并发写不丢更新 | 无 | KEEP_AS_TRANSPORT | atomic ledger write 主案（QA-DEBT-1）；必迁产品测试 |
| :2513-2543 U3 同字段冲突=我这笔落地但响；history 并集后者不胜 | 台账合并语义确定、不静默 | 无 | KEEP_AS_TRANSPORT | 同上（merge_ledger 语义） |
| :2545-2586 U4 台账写锁真互斥（持锁 4s，第二把等待≥1.5s） | 临界区不是摆设 | 无 | KEEP_AS_TRANSPORT | 真并发计时测量，移植；注意其死锁教训（先放锁再 wait） |
| :2588-2608 U2 双 relay 真并发各推一笔⇒都 COMPLETED、验收单不丢 | 并发投递不丢状态 | 无(2 词面) | KEEP_AS_TRANSPORT | 端到端并发传输；改名状态 |
| :2610-2614 夹具自检：$AKD 桩件全部可执行 | 防"拒因是 126 而不是判据"的假反例 | 无 | KEEP_AS_TRANSPORT | 工装诚实性检查，思路移植 |
| :2616-2687 K76 凭据探针四档＋哨兵串零泄漏＋argv 不碰钥匙串＋白名单逐条等两命令＋派发点≥4 前置闸 | credential unavailable fail-closed + secret 不外泄不落盘 | 无 | KEEP_AS_TRANSPORT | §11/§14 credential unavailable 全套；必迁产品测试。钉 relay.credential_gate/CRED_PROBE_CMD |
| :2689-2732 K77 清单按冻结提交校验：ghost/哈希不符/畸形行都点名非零；工作树篡改不改判（读提交不读树）；叶子不生套件 | artifact 清单 digest vs commit_ref | 无 | KEEP_AS_TRANSPORT | artifact reference integrity＋"生成不许读工作树"的 reproducibility；产品 q2c inspect 可用 |
| :2734-2755 K80 gitlink 嵌套夹具仓库按父提交钉号；子仓前进⇒非零点名 | 嵌套引用完整性 | 无 | KEEP_AS_TRANSPORT | refs 语义（commit id 而非文件级） |
| :2757-2767 K78 调度器两道环：未知模式退 2 且零启动；子层再入退 2 DRIVER_REENTRANCY_BLOCKED；depth 非法退 2 | runner fail-closed | 无 | KEEP_AS_TRANSPORT | recursive runner/reentry 回归（§15 点名）；"调度器"指测试 runner 非项目调度 |

### checks-fix02.sh 纯验收/发布类（前半已列 K1-K3/K23-K27/K31 的补充坐标）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| :574-613 K23 acceptance_gap_summary 全局通道≠本笔送达 | 缺口名单按"每笔送达证据"算 | 1,3 | REMOVE_FROM_PRODUCT_PATH | gap/ACCEPTANCE_REJECTED_NOT_ESCALATED 类=验收台账真相；但"每笔送达要有本笔凭据"可移植为 per-delivery ACK |
| :615-638 K24 验收 prompt 四条同校（可写工作区/原件只读/禁写清单/结论词） | 派发给验收 Agent 的消息内容约束 | 1,3 | DEPRECATE | prompt 模板属工作流；其中"原件只读/工作区限制"两条是 §11 workspace restrictions，可移植进产品 adapter 契约文案 |
| :640-655 K25 裸 RELAY_ROOT 读数须来自脚本自身根且明印 root；不许指冻结交包 | 读数归因＋不许读别人树 | 无 | KEEP_AS_TRANSPORT | 钉 relay 默认根行为；归因/防假绿性质 |
| :657-683 K26 UNAVAILABLE/INVALID/REJECTED 都不许从缺口名单消失 | 未知比未过更接近危险，绝不折算闭合 | 1 | REMOVE_FROM_PRODUCT_PATH | 名单本身=验收真相；但"未知态不许静默闭合"移植为 trace 规则（EXPIRED/DELIVERY_FAILED 保留在册） |
| :685-704 K27 缺陷册改名 fixed-pending-acceptance 仍挡 | 防"改名即放行"的门 | 1,3,7 | REMOVE_FROM_PRODUCT_PATH | unreleasable_defects=发布闸数据源；产品不得内置。方向性保护（状态改名不绕闸）可移植为白名单枚举 |
| :706-736 K28 基线复放套件缺夹具 ABORT、不许静默 40 绿；无硬编码绝对根 | 旧回归可跑性 fail-closed | 无 | DEPRECATE | 机制诚实；对象是"上一轮冻结实验复放"，属实验根不进产品包 |
| :780-797 K30 五处进程判定全走探针＋旧"空输出=没残留"归零＋三驱动留 exit 7 停手档 | 探针接线完整性 | 无 | KEEP_AS_TRANSPORT | process cleanup 接线钉；grep 形状弱但性质真 |
| :799-837 K31 delivery-note 三格读数生成，"无已知未闭项"手打话清零 | 交付文案不许人嘴说天话 | 1,3,7 | REMOVE_FROM_PRODUCT_PATH | 交付说明/未闭项口径=项目真相；证据保留。"文案只能由读数生成"思路可在 RELEASE-REPORT 工具里复用，但不进 q2c 产品代码 |
| :839-943 K32 候选导出（排除运行态）＋派发文点名 CANDIDATE/state root/放行闸/UNMEASURED＋先候选后文案 | 一次派发一枚不可变候选，验收方拿得到复跑坐标 | 1,3(文案) | RENAME_AS_TRANSPORT | make_candidate/顺序/排除运行态=KEEP；文案格中"放行闸、QA-DEBT 点名"REMOVE（见 K34 同族） |
| :945-986 K34 派发文坐标全＋三套断言不 cp 被验收根会话号 | 会话号本地生成，不偷运行态 | 无 | KEEP_AS_TRANSPORT | 逻辑会话映射卫生：产品不许把 provider session 当可 cp 的运行态 |
| :988-1003 K35 预检清单=真正所需；夹具缺⇒ABORT | 预检不多要不该在的东西、少了必红 | 无 | KEEP_AS_TRANSPORT | fail-closed 预检 |
| :1005-1027 K33 verify 缺对象 ABORT/NOLEDGER 红/VROOT 可显式 | 入口归因三件 | 无 | KEEP_AS_TRANSPORT | 同 verify-fix02 A/F 段 |
| :1029-1054 K36 拒收计数改数真发得出的事件名；死判据清零；NOFILE 非零 | 断言不许数空气 | 无 | KEEP_AS_TRANSPORT | 测试诚实性元规则，移植进产品测试评审 |
| :1056-1088 K37 human_delivered 整词匹配/rc=0 精确/delivered=false 不算 | 送达日志防串账五档 | 无 | KEEP_AS_TRANSPORT | 送达判定=ACK；钉 relay.human_delivered |
| :1090-1118 K38 缺陷册读不动⇒None⇒各方按未结挡（UNREADABLE/读不动文案） | 读不到=挡，不=空 | 7 | RENAME_AS_TRANSPORT | fail-closed 模式 KEEP；对象（OPEN-DEFECTS 发布册）出产品。移植时把"册"换成"delivery ledger/trace store 读不动⇒按未完成挡" |
| :1120-1146 K39 采信只认结论独占末行＋BIND 整行逐字；无 nonce⇒INVALID；审核侧同尺 | 采信文本协议 | 无 | KEEP_AS_TRANSPORT | correlation/anti-replay 文本判据；钉 relay.qualify_accept。结论词 ACCEPTED 改名 |
| :1148-1167 K40 只读动作零写入；台账损坏⇒LEDGER_UNREADABLE 不搬走不修复 | 观测不改被观测物 | 无 | KEEP_AS_TRANSPORT | 幂等/只读性＋损坏件 fail-closed；钉 gap/status 行为 |
| :1169-1211 K41-K43 一次派发一枚不可变候选；重试复用不换对象；指纹不符拒绝派发 | 候选=artifact 引用，重试不得换对象 | 无 | KEEP_AS_TRANSPORT | 与 K6b/K51 同族：delivery-retry 不重跑/不换对象；钉 make_candidate(expect_manifest) |

### mutations-fg.sh ＋ mutate.py（变异反证工装）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| mutations-fg.sh:26-31 变异前预检（动过文件全部已提交＋锚点全量先过） | 没有回退点不许动手；死锚点先列全 | 无 | KEEP_AS_TRANSPORT(方法) | 工装安全纪律；"先有检查点再动手"值得移植进产品 repo 的贡献规程，但不进产品代码 |
| mutations-fg.sh:54-104 进程组隔离＋墙钟＋LOOP-DETECTED 记账 | 反证套件放进自己进程组，到点整组 KILL；摘保险丝那两枚成环＝反证成立 | 无 | KEEP_AS_TRANSPORT(方法) | process-group cleanup 的活教材；§11 直接对应 |
| mutations-fg.sh:87-92 标签脱钩自检（expect 串必须能在判据件里找到） | 防"登记的期望名与断言标签各写各的"死反证 | 无 | KEEP_AS_TRANSPORT(方法) | 测试诚实性元规则 |
| mutations-fg.sh:105-115 每枚跑完必还原＋标记 0 命中＋git 状态核对 | 变异不许留在现场 | 无 | KEEP_AS_TRANSPORT(方法) | 同上 |
| mutations-fg.sh:117-127 还原后回归复跑两套 | 不是留变异交差 | 无 | DEPRECATE | 机制好；对象（fix01/fix02 套件）属实验根。产品 CI 里"摘保护→红"思路可做成少量核心 mutation 测试 |
| mutate.py:30-54 E-cred-gate/E-verify-mode/E-verify-depth/E-manifest-* 六枚 | 摘凭据闸/模式白名单/再入保险丝/清单读提交 四道保护的反证 | 无 | KEEP_AS_TRANSPORT(方法) | 对应 K76/K77/K78 的牙；移植时保护本体进产品 |
| mutate.py:57-108 M-A…M-I（子串 ACK、身份匹配、确认行、未确认登记完成、未知折零、在途等待、rollout 相对路径、读不动折没有、捕获路摘除） | 钉死 relay.py 的 ACK/correlation/fail-closed 九道闸各有牙 | 无 | KEEP_AS_TRANSPORT(方法) | 全部是传输判据的反证——最有移植价值的一批 |
| mutate.py:110-137 M-J…M-P（未 acked 建验收单/无 nonce 采信/无依据采信/UPSTREAM 折 INVALID/验收不幂等/成功也扰人/猜人会话号） | 验收支线七保护的反证 | 1,3(支线本身) | DEPRECATE | 机制里 M-K/M-N/M-P 是纯传输性质（correlation、幂等、不猜会话），其余随"验收"支线出产品；摘出来单测移植 |
| mutate.py:139-170 Q-/S-/T- 六枚（审核 BIND 检查、失败不升级、线程复用、BLOCKED 不叫人、未知不叫人、恢复不查绑定、耗尽不升级） | 升级到人＋恢复绑定各处有牙 | 无(3 词面) | KEEP_AS_TRANSPORT(方法) | "落到人"=通知路由（传输拥有：交付失败要有人可见）；BIND/线程/耗尽各处均为传输性质 |
| mutate.py:174-212 U-merge/U-base/U-conflict/U-history/U-stale/V-adopt/U-noflock 七枚 | 台账并发七种坏法的反证 | 无 | KEEP_AS_TRANSPORT(方法) | atomic ledger write 全套牙检；随 U1-U4 一起移植 |
| mutate.py:216-235 W-ack-from-exit-code/W-append/W-ignore-rid | 送达回执三保护反证 | 无 | KEEP_AS_TRANSPORT(方法) | ACK 本体 |
| mutate.py:240-252 X-capture-ignores-terminal/X-gap-uses-global-ok | 恢复终态检查＋缺口逐笔送达 | 1(gap) | RENAME_AS_TRANSPORT | 前者 KEEP；X-gap 钉的 acceptance_gap_summary 本体属验收真相→随 gap 出产品，但"全局顶替逐笔"这个反证形状要移植到 trace/ACK 汇总 |
| mutate.py:256-259 Y-root-default-frozen | 隐式根不许硬编码到别人树 | 无 | KEEP_AS_TRANSPORT(方法) | 归因 |
| mutate.py:263-270 Z-gap-only-rejected/Z-defect-gate-open-only | 缺口分类收窄＋放行数回 open | 1,3,7 | REMOVE_FROM_PRODUCT_PATH | 两枚钉的都是发布闸数据逻辑（unreleasable_defects/gap 分类）；证据保留，产品路径不得存在这两个对象。"改名即开门"这一坏法要保留为 trace 状态白名单的反证 |
| mutate.py:274-313 P-/N-/H-/C- 六枚 | 探针两档、交付说明缺件读零、手打话回归、候选含运行态/无预检 | 1,7(N/H) | RENAME_AS_TRANSPORT | P-/C- 全 KEEP；N-/H- 钉的 delivery-note/round4 文案属发布叙事→对象出产品，"读数不许折成零/手打话"判法移植 |
| mutate.py:317-368 D-/E- 组（送达整词/登记册 None/nonce 宽松/gap 写盘/候选换对象/终态/删除传播/空收回/子串回执） | 十枚全钉 fail-closed 读数与幂等 | 无(1 词面) | KEEP_AS_TRANSPORT(方法) | 传输本体 |
| mutate.py:372-417 E5-E12＋H2/H3（分流/换 nonce/收回/静默/等待/终态/尾巴预算） | 迟到答复家族反证 | 2(ACCEPTED 词面) | KEEP_AS_TRANSPORT(方法) | E-silent-wakes-on-accepted 的"ACCEPTED 后静默"若入产品改名为"ACKED 后停止升级通知" |
| mutate.py:421-510 E11/E13/E14/E15-E23（rc 门/双层复合变异/包装路径/即时终态/ps 错读 gone/拆锁/unknown 重派/无终态当答复/rc-only/清旧凭据/按轮匹配/REVIEW-BIND） | R13-R16 缺陷修法的逐层牙检 | 无 | KEEP_AS_TRANSPORT(方法) | 含"复合变异"重要方法论：保护在哪一层失效就摘哪一层（E13 双摘） |
| mutate.py:514-560 E24-E32（预跑键漂移/尾读/cd/基线透传/剥离/终态闸恒开/汇总漏 unittest/审核腿终态/派发文点名） | 接线级反证批 | 7(E32) | RENAME_AS_TRANSPORT | E32 钉 preflight 汇总（发布预跑）→DEPRECATE；其余为传输/工装接线 |
| mutate.py:13-17,783-809 checkpoint_status 双条件（git 干净或快照 sha256 相同，否则退 4 NO_CHECKPOINT） | 无检查点拒绝变异 | 无 | KEEP_AS_TRANSPORT(方法) | 破坏性动作前置检查，产品脚本通用 |

### preflight-candidate.sh / preflight-verdict.sh / delivery-note.sh / manifest-check.sh

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| preflight-candidate.sh:34-61 候选导出＋指纹登记进日志＋候选内不许有运行态 | 派发方先在同一枚对象上自跑一遍 | 无 | KEEP_AS_TRANSPORT | make_candidate 同路径＋指纹留痕=artifact 引用；"预跑"用途本身属验收编排→见下行 |
| preflight-candidate.sh:74-97 三套在候选里复跑（checks.sh 用基线根、fix01/02 用候选根） | 分身份复放，防"上一轮行为被套成本轮代码" | 1 | DEPRECATE | 复放路由纪律好；整套"验收对象预检"属实验根工装，不进产品包 |
| preflight-candidate.sh:22-31 Q2C_DENY_PROC 用 denybin 造"无进程权限"形态预跑件 | 环境受限形态由我先把会红的格子点名 | 无 | DEPRECATE | 思路（给受限沙箱预生成红格清单）可移植；实现件留实验根 |
| preflight-candidate.sh:103-113 仓库 unittest 计入同一份汇总，结论交唯一汇总件 | 汇总不许漏项 | 1,7 | DEPRECATE | 见 K75；"漏项即谎报"教训写进产品 CI 设计 |
| preflight-verdict.sh:10-33 名称=退出码 汇总；空清单/畸形/非整数⇒退 2 用法错；有点名非零 | 结论只由读数折出，读不到≠全绿 | 7 | RENAME_AS_TRANSPORT | 通用诚实聚合器（无项目语义），产品脚本可直接采用；只需去掉"叫独立验收"文案 |
| delivery-note.sh:25-53 未结缺陷/缺口读数，读不到⇒写"不许声明无未闭项"绝不折零 | 对外交付说明=读数函数 | 1,2,7 | REMOVE_FROM_PRODUCT_PATH | unreleasable_defects＋gap verdict 是发布叙事闸；产品不得生成"未闭项/放行"声明。"文案只能由读数生成＋读不到不许折零"两条纪律移植到 q2c trace 报表 |
| delivery-note.sh:55-73 "与上一轮一致"必须由两个 SHA 现比，取不到就明说比不了 | 引用一致性现算不嘴说 | 无 | KEEP_AS_TRANSPORT | artifact_refs 的 digest 比对语义，可移植进 q2c inspect |
| manifest-check.sh:14-17 清单/提交取不到⇒退 2 fail-closed | 读不到≠没问题 | 无 | KEEP_AS_TRANSPORT | |
| manifest-check.sh:24-39 逐条回提交取 blob 现算哈希；畸形行单列计数 | 清单↔commit 可复现性 | 无 | KEEP_AS_TRANSPORT | reference+digest 校验器本体；产品打包复现直接用 |
| manifest-check.sh:40-66 gitlink 按父提交钉号；钉不住=发布物不可复现判不过 | 嵌套仓库引用完整性 | 7(弱) | RENAME_AS_TRANSPORT | 机制=refs 完整性（拥有）；"发布物不能复现⇒不通过"话术是发布门，改名 check 即可 |
| manifest-check.sh:4-7 头注释"叶子永不生套件/调度器不当自己的叶子命令" | 断环：master/叶子分工 | 无 | KEEP_AS_TRANSPORT | 150 枚进程事故本体（§15 recursive runner） |

### notify_human.sh / proc_probe.sh / tools/denybin / 桩件

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| notify_human.sh:10-15 通道分级：待办文件＋osascript 通知都成才算 delivered；③会话消息必须结构化成功回执 | 不许拿"脚本退 0"冒充"人收到了" | 无 | KEEP_AS_TRANSPORT | 送达判据诚实（transport ACK 到人的最后一公里）；产品可保留为 notification adapter 参考实现 |
| notify_human.sh:31-39 osascript 走 argv 传参防引号劈坏命令 | 安全拼命令 | 无 | KEEP_AS_TRANSPORT | 安全进程/脚本调用 |
| notify_human.sh:45-48 剥 QODER_AGENT_SDK_* 环境变量再调子 CLI | 防子进程误认在 SDK 里拒启 | 无 | KEEP_AS_TRANSPORT | adapter 启动卫生（真实坑） |
| notify_human.sh:58-91 回执按结构判：末条 type=result、顶层 is_error=false＋subtype=success＋session_id 等于目标；嵌套层冒充不算 | 送达=回证据 | 无 | KEEP_AS_TRANSPORT | ACK 结构化判据；钉 relay parse_human_ack 的同类逻辑，产品测试必迁 |
| proc_probe.sh:42-62 三态读数 NONE/N=k/UNMEASURED，只认退出码；rc>=2 绝不折成 NONE | 探针坏了不许读成"干净" | 无 | KEEP_AS_TRANSPORT | 进程清理核心件，整体搬进产品 |
| proc_probe.sh:36-53 剔自身＋并发兄弟探针（按脚本名不按 $$） | 自数/互数残留的两种坑 | 无 | KEEP_AS_TRANSPORT | |
| proc_probe.sh:69-80 --wait 到点仍残留按 N=k 报，"等待窗口不是把红等成绿的手段" | 超时不折中 | 无 | KEEP_AS_TRANSPORT | |
| tools/denybin/pgrep、tools/denybin/ps | 模拟"读不到进程表"的沙箱替身（退 3／退 1） | 无 | DEPRECATE | 测试夹具，注释自己写明"不许被产品代码调用"；留实验根 |
| stub_qoder.sh:11-19 | 合规送达回执桩：结构化 result＋回显本轮 RELAY_REVIEW_BIND | 无 | KEEP_AS_TRANSPORT | ACK 正例夹具思路随测试移植；文件本身属实验根夹具 |
| stub_review.sh（全 21 行）／stub_bare.sh／stub_accept_reject.sh | 零模型桩：可控失败/慢/结论；accept_reject 明写"这份读数不许写成独立验收通过" | 无(桩)/1(验收语义) | DEPRECATE | 移植为产品 handoff-adapter 假执行器；stub_accept_reject 的"不许冒充验收通过"纪律KEEP |

### round4-real.sh / accept-real.sh（真腿驱动）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| round4-real.sh:37-45 出口探针不通⇒一分不花，退 5 | 花钱动作前置检查 fail-closed | 无 | DEPRECATE | 习惯好（凭证/费用前置探）；实现绑本机代理 7892，属实验根驱动 |
| round4-real.sh:46-48 每轮换 TAG：旧 rid 第二跑会吃"结果已在案⇒只补送达"，把旧判决读成新读数 | 真腿重放鉴别 | 无 | KEEP_AS_TRANSPORT | 这是 idempotency 的另一面：命中幂等≠重新判定。产品测试该有这条（新消息用新 request_id） |
| round4-real.sh:49-58 PRECHECK 档：变量/路径/出口自检零调用 | 昂贵路径先自检 | 无 | DEPRECATE | |
| round4-real.sh:60-69 起跑闸＝三态探针；探针不可读⇒不开腿（无法证明单写者） | 并发写者保护 fail-closed | 无 | KEEP_AS_TRANSPORT | 移植：单写者证明不了就不动 |
| round4-real.sh:82-100 新建 review/accept 专用只读线程并落 state/*_session_id | 逻辑会话=显式新建，不复用他人上下文 | 无 | KEEP_AS_TRANSPORT | 逻辑会话映射；产品 adapters start 的原型 |
| round4-real.sh:122-138 handoff 证据件由 delivery-note 现读生成 | 见上 REMOVE 族 | 1,7 | REMOVE_FROM_PRODUCT_PATH | 证据句内容（门禁退出码、未闭项）=项目真相叙事 |
| round4-real.sh:139-166 drive()：8 轮快扫＋R4_TAIL_S 尾巴预算；到点不杀、"仍在飞"如实登记 | 驱动收工不许造成孤儿答复 | 无 | KEEP_AS_TRANSPORT | 等待/孤儿进程语义；预算词改名 |
| round4-real.sh:168-188 A 档：真验收 ACCEPTED⇒无待办无通知无回执 | 成功路径静默 | 1,2,3 | REMOVE_FROM_PRODUCT_PATH | 整档是"验收结论的权力效应"；产品只测 ACKED 后不重复通知 |
| round4-real.sh:191-238 C 档：拿旧派发原文叫醒真线程⇒旧 BIND 回执必须被拒＋ref_count 只认真事件名 | 真实模型上的 anti-replay | 无(3 词面) | DEPRECATE | 性质(correlation 拒旧绑定)=KEEP 已由 K4/K39 零模型覆盖；真腿执行留实验根。:227-231 死判据教训（数发不出的事件名）值得抄进产品测试评审 |
| round4-real.sh:241-262 B 档：真 REJECTED⇒真通道送达；模型没给 REJECTED 就记"未成立无可观察" | 不许把没观察到的写成通过 | 1,3 | REMOVE_FROM_PRODUCT_PATH | 验收权力链；"没跑出读数≠成立"的记账方向 KEEP 为方法论 |
| round4-real.sh:265-286 D 档：剥 RELAY_* 后跑并发套件＋h1 五遍查"一笔被审两次" | 重复处理检测 | 无 | KEEP_AS_TRANSPORT | duplicate handoff effects 复测器（h1-per-rid 输出'重复审核=[]'）；产品 1000-handoff 测试可复用形状 |
| round4-real.sh:290-291 费用口径行：真实调用次数清单＋UNKNOWN | 花钱可审计 | 无 | KEEP_AS_TRANSPORT(文档) | |
| accept-real.sh:23-31 同一起跑三态探针闸 | 同上 | 无 | KEEP_AS_TRANSPORT | |
| accept-real.sh:36-39 RELAY_ACCEPT_REAL=1 只在本脚本设置＝真调用显式授权开关 | 测试永远不许触发真付费调用 | 无 | KEEP_AS_TRANSPORT | 显式授权开关——产品里"live adapter 真跑"应有同类 opt-in |
| accept-real.sh:43-51 每轮换新建线程：复用旧线程时真机答了上一笔的 SHA，字段闸门过了但证据自相矛盾 | provider 上下文污染=correlation 事故 | 无 | KEEP_AS_TRANSPORT | 逻辑会话纪律的实证（§7 provider 上下文不复用） |
| accept-real.sh:82-87 空线程号继续跑⇒rc 还是 0 的假跑；现在当场停 | 关键输入为空不许"跑完" | 无 | KEEP_AS_TRANSPORT | |
| accept-real.sh:109-119 证据件现读＋inbox 单据形状（task_id/request_id/commit_sha/repo_path/evidence_paths/review_request） | 交接消息 payload 原型 | 无 | KEEP_AS_TRANSPORT | 即 PROTOCOL §4 字段集雏形；"门禁退出码 0"半句是项目真相，产品 payload 里该挪进 metadata |
| accept-real.sh:121-138 三轮快扫＋同一 R4_TAIL_S 尾巴预算＋"仍在飞" | 同 drive() | 无 | KEEP_AS_TRANSPORT | |

### checks.sh（基线复放，C0–C9）——按任务要求只分类其断言的职责任

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| checks.sh:121-128 C0 缺字段交接单被拒并归档、一次审核都不发 | 入站消息 schema 校验 fail-closed | 无 | KEEP_AS_TRANSPORT | 钉 relay.ingest 校验；产品 HANDOFF_REQUEST 校验同款 |
| :134-145 C1 重复交付不再走审核、第二张判去重完成 | 幂等（dedupe） | 无 | KEEP_AS_TRANSPORT | "去重完成"的 COMPLETED 字样→ACKED/duplicate-delivered |
| :147-165 C2 单飞排队：第二张留 QUEUED，锁释放后各审一次 | 并发投递排队不重复执行 | 无 | KEEP_AS_TRANSPORT | |
| :167-175 C3 收单后重启：停在 QUEUED 一次没发→重启后跑完 | 重启不丢队列 | 无 | KEEP_AS_TRANSPORT | restart recovery |
| :178-189 C4 SENT 后重启：如实重发、attempts=2 不静默 | 重启再消费可计数 | 无 | KEEP_AS_TRANSPORT | restart re-consumption |
| :191-203 C5 结果在案时重启不重复审 | 重启重复处理保护 | 无 | KEEP_AS_TRANSPORT | §15 "restart duplicate review risk" 本体 |
| :205-214 C6 失败重试上限 2 次⇒BLOCKED | 投递重试预算 | 5 | RENAME_AS_TRANSPORT | RELAY_RETRY_MAX=投递次数上限=transport retry；改名 delivery_retry_max 后随产品走 |
| :216-225 C7 残锁回收后仍跑完 | 崩溃锁卫生 | 无 | KEEP_AS_TRANSPORT | |
| :227-283 C8/C8b 同请求号换提交/结果件被改⇒重新审，不拿旧结论交差；夹具缺⇒ABORT | 幂等键必须绑内容 digest | 无 | KEEP_AS_TRANSPORT | dedupe_key+raw_sha 反陈旧（钉 relay 行为）；"摘要不匹配"是 digest 校验非质量判断 |
| :284-311 C9 无依据结论拒收＋原件改名保留＋不登记可复用摘要＋失败证据不销毁＋次数停上限 | 混合：采信判据(质量) 与 证据保留(传输) | 1,5(词) | RENAME_AS_TRANSPORT | 证据保留/不复用/上限=KEEP；"正文零依据不采信"的长度判据=质量判断→产品里只留"结构+BIND 判据"（K39 同形），长度门出产品 |

### checks-fix01.sh（F/G/H 组）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| checks-fix01.sh:137-155 F1/F2 成功回执 vs is_error=true＋rc7：不得登记 COMPLETED、running_count=0、acked False、拒因留痕、结果 raw_sha 在案不重调模型 | ACK 结构化判定＋失败不冒充在飞＋结果复用 | 无 | KEEP_AS_TRANSPORT | transport ACK 主案；钉 relay.parse_ack |
| :157-172 F3/F5 正文含 ACKED≠确认行；退 0 无 JSON≠送达 | 确认要结构不要散文 | 无 | KEEP_AS_TRANSPORT | |
| :163-167 F4 回执 session_id 不匹配⇒没送到 | 送达对象身份校验 | 无 | KEEP_AS_TRANSPORT | logical session 匹配 |
| :174-184 F6 失败回执不得进"已完成"去重集；同键第二张不被顶掉 | 失败不污染幂等集 | 无 | KEEP_AS_TRANSPORT | 极重要：duplicate-delivery 判定的负路径 |
| :186-195 F7 送达失败后只补送达、审核调用/入队次数不变 | **retry=重投不重跑** | 无 | KEEP_AS_TRANSPORT | 任务书 §5 的机器测试原型（wakes/enqs 计数不变＋reuse_existing_result 留痕）——直接移植 |
| :197-203 F8 连三送不上⇒BLOCKED 且只跑一次审核、失败证据不销毁 | 投递预算＋不重跑 | 5(词) | RENAME_AS_TRANSPORT | 同上改名 |
| :205-239 F9 真回执原件正/负对照（改 session_id、翻 is_error 都不认）＋冻结原件不可达⇒FROZEN_UNREACHABLE 不许算比过 | 判据对真数据有效＋比对缺席如实报 | 无 | KEEP_AS_TRANSPORT | 真件夹具法；"没比过≠通过"要移植 |
| :297-445 G1-G7 队列读数 None=未知不盲发；探针 0 条才准有限重发；原进程在跑⇒等待不重起；已停且结果有效⇒只补送达；未知与确认缺席两个落笔态；中转被杀从捕获件登记；会话件读不动=未知 | 执行记录未知的全套 fail-closed | 5(词) | KEEP_AS_TRANSPORT | transport recovery 全家桶：unknown≠absent、alive≠done。逐条移植 |
| :447-546 G8 真 rollout 结构正对照（现场重切 vs 随包节选逐字同；无原件⇒UNSliced 明写没比过；且读出绝对路径防相对路径死代码） | 会话落盘件判据活在真结构上 | 无 | KEEP_AS_TRANSPORT | 与 make-rollout-fixture.py 配套；"没比过不许当绿"移植 |
| :548-591 H1 双写者并发：同时 RUNNING≤1（单飞）、同一 request_id 不被审两次（空集判红）、台账仍可解析、两笔都在 | 并发写者不互相挤掉审核 | 无 | KEEP_AS_TRANSPORT | 判据形状修正注释（前缀计数把缺陷当通过条件）值得抄进产品测试评审 |
| :592-630 H1 已知缺陷 QA-DEBT-1 登记复现"不计红"＋登记写临时根不写被验根 | 缺陷登记册联动 | 1,3,7 | DEPRECATE | "复现不自动改状态、要人签"=评审签字；登记册本体出产品；"登记动作不许写进被复验对象"这条纪律 KEEP |
| :602-617 GATE 放行闸（unreleasable_defects＋Q2C_WAIVE_DEBT）印在读数里但不计 FAIL | 套件尾报"能不能放行" | 1,3,7 | REMOVE_FROM_PRODUCT_PATH | 发布闸＋人豁免通道显式内嵌断言套件；证据保留。其自我克制（不混进代码断言）也救不了职责越界 |
| :635-640 DEBT>0⇒整套退出码 1（"不是豁免，等主理人裁"） | 登记缺陷门改变套件红绿 | 1,7 | REMOVE_FROM_PRODUCT_PATH | 让"代码回归信号"被发布状态染色，正是任务书要剥离的耦合；产品 CI 绿=测试绿，与放行无关 |

### test_relay_fix03.py（verify B 段第四套：并发四格＋分级白名单＋变异检查点）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| test_relay_fix03.py:66-96 test_acceptance_split：机器验收配置 vs 人升级配置两半 | "有没有执行器/有没有人通道"分开判 | 1,3 | REMOVE_FROM_PRODUCT_PATH | machine/human acceptance 属验收编排；"配置缺失要分开如实报"的思路可移植到 adapter capabilities |
| :98-116 test_human_escalation_needs_evidence | 全局通道开着≠这一笔送到 | 无 | KEEP_AS_TRANSPORT | per-delivery receipt 原则（同 K37） |
| :118-165 test_concurrent_save_does_not_lose_transitions ＋:164 F2-INTRUDER | 并发 save 不丢转移；外来记录不许被整写带进带出 | 无 | KEEP_AS_TRANSPORT | atomic ledger 单元形 |
| :208-237 test_classification_five_items：PRE_ACCEPTANCE_BLOCKED/ACCEPTANCE_REJECTED_NOT_ESCALATED 分类＋判词 PENDING_ACCEPTANCE_GAP | 缺口按验收状态分类 | 1,2,3 | REMOVE_FROM_PRODUCT_PATH | 整个 classes 词表是验收真相；证据保留 |
| :240-257 test_grade_outside_abc_is_blocked：grade=D 越级⇒不许报无缺口 | 分级白名单（A/B/C 之外即缺口） | 1 | REMOVE_FROM_PRODUCT_PATH | 教科书式"acceptance grading"——q2c 未来不得有 grade 概念 |
| :260-277 台账不存在⇒LEDGER_ABSENT_NOT_ACCEPTABLE | 没读到≠没缺口 | 无 | KEEP_AS_TRANSPORT | fail-closed 判词形状（改词后移植） |
| :285-306 无检查点拒绝 mutate.apply；有匹配快照放行 | 破坏性动作前置检查单测 | 无 | KEEP_AS_TRANSPORT | |

### MCP 插件交付面 <插件交付目录>/q2c-plugin（每枚暴露工具"拥有什么"）

| 坐标 | 大白话 | 禁责 | 分类 | 理由 |
|---|---|---|---|---|
| q2c_mcp.py:33-49 q2c_submit（投单：request_id/task_id/commit_sha/repo_path/qoder_session_id＋可选 evidence_paths/review_request/test_exit_code） | 把一笔交接放进收件口并推一拍 | 无(1 词面) | KEEP_AS_TRANSPORT | 消息投递入口=产品核心。注意两个字段带项目真相：review_request（评审请求语）与 test_exit_code（CI 真值入必填邻域）——降为 metadata/artifact_refs 即可；:96-120 缺字段点名不补默认、同名不覆盖（去重依据不抹）=KEEP |
| q2c_mcp.py:51-55 q2c_scan（推进一拍：送达/唤醒/收回执与"验收结论"，幂等） | 传输状态机的心跳 | 无(1 词面) | KEEP_AS_TRANSPORT | 描述里的"验收结论"改 HANDOFF_RESULT；幂等声明本身是产品承诺 |
| q2c_mcp.py:56-64 q2c_status | 单笔台账读数原文不加工 | 无 | KEEP_AS_TRANSPORT | = q2c inspect |
| q2c_mcp.py:65-69 q2c_gap（未闭缺口汇总） | 对外暴露"验收缺口"汇总 | 1,2,3,7 | REMOVE_FROM_PRODUCT_PATH | 该工具拥有的是项目验收真相分类；产品对应用 q2c list/trace 报传输态（EXPIRED/DELIVERY_FAILED），不得有 gap 判词。:84-89 run_relay 的"只读动作"约束思路可留 |
| q2c_mcp.py:70-76＋:141-201 q2c_whoami | 自报状态根/HEAD/代码面脏否/两枚预跑件候选指纹是否同枚；测不到写 UNAVAILABLE 不编号；:189-193 形状闸防"两个 UNKNOWN 字符串相等⇒报同枚" | 7(弱,报告不裁决) | RENAME_AS_TRANSPORT | 全是事实读数不是闸——身份/引用完整性属通信真相；但 code_dirty 与"放行"话术若被下游当闸用即越界。改名 q2c identity/inspect 并在文档钉死"它不判放行"。指纹长度正则 [0-9a-f]{6,} 不写死 32 的教训 KEEP |
| q2c_mcp.py:84-93 run_relay（Q2C_ROOT 未设⇒拒绝写或叫人；RELAY 指不到⇒硬错） | 不指状态根就不许动别人家 | 无 | KEEP_AS_TRANSPORT | 工作区限制/防猜路径（§11） |
| selftest.sh:1-15 九格（协议应答/无根拒绝/relay 缺失 isError 非静默/只读不建运行态/缺字段点名/重复投不覆盖＋台账不造第二笔/入口转调动作名与 relay 分派漂移自检/whoami 测不到如实报/入口自解析读数=relay 真源读数反双真源) | 插件入口的诚实性套件 | 无 | KEEP_AS_TRANSPORT | 逐格都是产品 MCP/adapter 入口测试形状，直接随插件迁移；"漂移自检＋反双真源"两格尤其值钱 |
| README-评审.md（:18-22 工具表；:109 引用"真验收 ACCEPTED 后无打扰"读数） | 评审包叙事文档 | 1,3 | DEPRECATE | 文档身份是"评审包"；工具表可回收进 ADAPTERS.md，验收叙事留证据 |
| 门牌-Q2C.md:33-37（"E1c 是放行闸……不许设 Q2C_WAIVE_DEBT=1"）/:43-59（复跑姿势）/:71-74（改判据变绿=不合格、没比过说没比过） | 给接手 Agent 的目录身份与纪律 | 3,7(前段) | RENAME_AS_TRANSPORT | 前半是发布/签字指令→REMOVE 部分；"三口目录哪口活的/从提交态导副本/先剥 RELAY_*"是 session-artifact 映射纪律→KEEP 思路；末条读数诚实规矩是通信真相纪律→KEEP 改写 |
| V1.1-方向-事件与唤醒分离.md:5-27（queued≠started；ACKED 只证明回话不证明唤醒；on_agent_started 必须来自可核运行态观察） | 传输状态机的语义分层 | 无 | KEEP_AS_TRANSPORT | 这份文档正好是 PROTOCOL §4 状态（QUEUED/DELIVERING/DELIVERED/STARTED…）的输入；不是项目真相，是通信真相 |

### state/（只列名，未读内容）

| 文件 | 拥有物 | 分类 |
|---|---|---|
| state/ledger.json ＋ .lock ＋ .rev | 传输台账（含 rev 号供三方合并） | KEEP_AS_TRANSPORT |
| state/review_session_id / accept_session_id | provider 会话号映射（评审/验收命名） | RENAME_AS_TRANSPORT（改为 role→session 映射表） |
| state/human_escalation_ack.jsonl | 逐笔"送到人"的回执账 | KEEP_AS_TRANSPORT（通知通道的送达账，非审批账） |

#### 逐点清单 结束（共 184 个站点分类完毕；另有 64 条迁移保护行、11 条产品外站点行）

### 需要随传输层一起迁移的保护（最值钱的一节）

以下每一条都是"消息送达/关联/幂等/ACK/重试投递/进程安全/台账原子/凭据 fail-closed"的机器测试，不是项目真相。给出用例 ID＋坐标＋一句话测法，可直接照搬进产品测试套件（改状态名即可）。

### A. 重复投递 / 幂等（duplicate delivery）
| 用例 | 坐标 | 机器测法（一句话） |
|---|---|---|
| C1 重复交付不再走审核 | checks.sh:134-145 | 同 dedupe_key 投两张：wake.log 行数不变、第二张直接判去重（状态记 ACKED/duplicate） |
| F6 失败回执不得进"已完成"去重集 | checks-fix01.sh:174-184 | 第一笔 err7 回执后投同 task＋同 SHA 第二笔：review_from≠dedupe，第二笔真走完成——失败永不顶掉后来者 |
| K6 重复 scan 不重建不二次叫醒 | checks-fix02.sh:236-245 | settled 后记 wakes 基线，再 scan×2：wake 计数与 `-ACC` 条数逐字不变 |
| K6b run-one 再驱动不重建 | checks-fix02.sh:247-258 | 显式 `relay.py run-one` 重放同一 rid：accwakes 与 -ACC 计数不变 |
| F7 retry＝只补送达不重跑（§5 直接对应） | checks-fix01.sh:186-195 | 送达桩先 err7 后 ok：wakes/enqs 两计数不变、最终 ACKED、留痕 reuse_existing_result |
| H1 同一 request_id 不被审两次 | checks-fix01.sh:575-583 | 双进程并发后按 rid 聚合 wake_start 计数：max≤1 且空集判红 |
| plugin selftest#6 重复投单不覆盖 | <插件交付目录>/q2c-plugin/selftest.sh:12-15(6) | 同名 inbox 文件二次 submit：返回 submitted=false、原件字节不变、台账不造第二笔 |
| K52 收回≠重派 | checks-fix02.sh:1623-1676 | 迟到答复收回后 accwakes 仍=1 |

### B. 重启后再消费 / 恢复（restart re-consumption, transport recovery）
| 用例 | 坐标 | 测法 |
|---|---|---|
| C3 收单后重启 | checks.sh:167-175 | kill 于 QUEUED：wake 次数=0；重启 scan 到终态 |
| C4 SENT 后重启如实重发 | checks.sh:178-189 | kill 于 SENT：重启重发且 attempts=2 留痕不静默 |
| C5 结果在案时重启不重复审 | checks.sh:191-203 | 结果落盘后 kill：重启 wakes 不涨——§15 "restart duplicate review risk" 本体 |
| G4 已停且结果有效⇒只补送达 | checks-fix01.sh:366 | 进程 dead＋raw 在案：不新起调用 |
| G6 中转中途被杀⇒从捕获件登记只补送达 | checks-fix01.sh:399 | 半路 kill：capture route 生效 |
| K50 恢复器三档分流（RUNNING 原地/SENT 有捕获转 RUNNING/纯 SENT 重派同 nonce） | checks-fix02.sh:1513-1590 | 直调 relay.recover，断言 state/attempts/bind_nonce 三元组 |
| K62 探针 unknown⇒不转 QUEUED 不重派 | checks-fix02.sh:1982-2025 | 打桩 pid_state⇒unknown：recover 后仍 RUNNING、nonce 不变；对照 never_started⇒QUEUED 同 nonce |
| K51 重派不换绑定串 | checks-fix02.sh:1592-1621 | run_accept 重放：nonce 与派发件 sha 不变，留痕 accept_prompt_reused |
| 对侧身份复用禁令 | round4-real.sh:46-48 | 同 rid 二跑会命中"结果已在案"——真腿每轮换 TAG；产品测"新尝试必须新 request_id" |

### C. 送达 ACK 诚实性（transport ACK）
| 用例 | 坐标 | 测法 |
|---|---|---|
| F1-F5 ACK 五连（结构化/身份/确认行/退0无JSON/is_error+rc7） | checks-fix01.sh:137-172 | 五枚 qoder 回执桩逐字段翻转：只有全合规才 ACKED；失败笔 running_count=0 |
| K65 notify_delivered 五档＋真过程 delivered=false | checks-fix02.sh:2027-2070 | 单元五档(结构/会话号/rc/无回执/delivered=false)＋process 级落笔 delivered=false |
| K37 送达日志整词匹配 | checks-fix02.sh:1056-1088 | `R9-A-OTHER…rc=0` 行、rc=01、行尾整词、delivered=false 五读法逐位断言 |
| K71 回执必须回出本轮 REVIEW-BIND | checks-fix02.sh:2294-2319 | 合规回执(退0)但不回 BIND⇒链不推进；桩件 chmod 自检防"126 假反例" |
| K13 失败回执原件不被成功回执覆盖 | checks-fix02.sh:344-358 | 前后两次 scan 间比对失败原件 sha：改名保留 prior_ack_preserved |
| K21 回执 rid 不匹配不入本笔账 | checks-fix02.sh:512-535 | esc 桩回别人的 rid：ack 文件必须仍空 |
| K20 退 0 无结构化回执⇒无送达账 | checks-fix02.sh:478-510 | esc_noreceipt 桩＋"escalation rc=0"前置自证 |
| notify_human.sh:58-91 嵌套 is_error=false 不冒充 | 同文件 | 失败回执内嵌 success 形状；结构判只认顶层 |
| F9 真回执原件正/负对照 | checks-fix01.sh:205-239 | 三份真实 qoder-ack：改 session_id／翻 is_error 都拒；冻结原件不可达⇒FROZEN_UNREACHABLE 不算比过 |

### D. 关联 / 防串账 / 防复述（correlation）
| 用例 | 坐标 | 测法 |
|---|---|---|
| K4 回不出本次 nonce⇒拒 | checks-fix02.sh:222-226 | replay.zsh 写死旧 BIND：INVALID 不采信 |
| K9 答非所问(旧 nonce)的 VERDICT 不登记完成 | checks-fix02.sh:294-313 | stale_review.zsh 真件形状：状态不推进、不建下一环 |
| K17 恢复路径也查 BIND | checks-fix02.sh:382-419 | 杀在途＋换错绑定捕获件：recovered_but_unbound＋重审(wake≥2) |
| K22/K45 流以 turn.failed 收尾⇒恢复不采信 | checks-fix02.sh:537-571,1263-1340 | 一正两负同批；transcript 路与 capture 路都挡（transcript_refused_call_failed） |
| K39 采信文本协议（结论独占末行＋BIND 整行逐字） | checks-fix02.sh:1120-1146 | qualify_accept 四档直测＋审核侧同尺 grep 接线 |
| K66 终态按轮绑定不借上一轮 task_complete | checks-fix02.sh:2072-2152 | 用真 rollout 件逐行截断复放：turn_terminal(pre)=none 而 _stream_terminal=completed（危害现形） |
| K68 回执 BIND 六档 | checks-fix02.sh:2232-2260 | 逐字/别轮/缺失/带尾巴/双枚并存/无 nonce⇒False 矩阵 |
| K11 验收/审核分线程 | checks-fix02.sh:321-329 | 台账 thread 字段两值不等——逻辑会话不混用 |
| K8 叫人只认显式会话号＋同笔一次 | checks-fix02.sh:282-291 | 未配置⇒不发消息；配置⇒计数=1 且重复 scan 不涨 |

### E. 进程 / 孤儿 / 递归 / 锁（safe spawning & cleanup）
| 用例 | 坐标 | 测法 |
|---|---|---|
| K29 探针三态四档＋rc=3 反例 | checks-fix02.sh:738-778 | 无此进程/N=k/并发兄弟探针不自数/denybin pgrep⇒UNMEASURED rc=2 |
| K60 pid_state 四档 | checks-fix02.sh:1901-1941 | 死pid=gone/无登记=never_started/ps 报错注入=unknown/pid1=not-gone |
| K61 锁主不可读不拆锁 | checks-fix02.sh:1943-1980 | 锁内 pid=1：lock_acquire False 且锁目录与 pid 件原样；死 pid⇒回收 |
| K52/K54/K67 到点不杀＋迟到收回＋旧 rc 件先清 | checks-fix02.sh:1623,1695,2154 | 真子进程睡过窗口：RUNNING 保留→下拍按真退出码收回；派发瞬间旧 rc 件必须消失 |
| K78 再入/未知模式/畸形 depth 三连退 2 | checks-fix02.sh:2757-2767 | 直接以子环境调 verify：rc=2、零套件启动、读数点名 DRIVER_REENTRANCY_BLOCKED |
| verify-fix02.sh:216-235 F 段 | 同文件 | 探针期望写死 NONE；wake.lock 不存在；台账 SENT/RUNNING=0 且读不到=红 |
| mutations-fg.sh:64-80 进程组墙钟 | 同文件 | perl setpgrp＋整组 KILL；rc=137/143 归一并注明"摘保护会自繁殖"＝反证成立 |
| C7 残锁回收 | checks.sh:216-225 | 留一把死锁→仍能跑完 |
| accept/round4 起跑三态探针闸 | accept-real.sh:23-31; round4-real.sh:60-69 | 探针不可读⇒退 7 不开腿（无法证明单写者就不动） |
| K58 绝对解释器包装 | checks-fix02.sh:1809-1813 | `/bin/zsh -c wrap` 硬编码检查——静默起不来＝无声失败 |

### F. 凭据不可用 fail-closed ＋ 秘密不落地（§11/§14）
| 用例 | 坐标 | 测法 |
|---|---|---|
| K76 五档全组 | checks-fix02.sh:2616-2687 | ①READY 照发＋事件与台账留痕 ②不可用⇒不派发 BLOCKED CREDENTIAL_UNAVAILABLE 且桩执行器零唤醒 ③超时同 BLOCKED ④哨兵串在 evidence/state 全树 grep=0 ⑤argv 不出现 security/dump-keychain/find-generic-password＋CRED_PROBE_CMD 白名单逐字=两条官方子命令＋派发点≥4 处有闸 |
| E-cred-gate-always-pass / E-cred-blocked-overwritten | mutate.py:30-37 | 两枚反证：闸恒真、BLOCKED 被覆盖——都必须打红 K76 二档 |

### G. 台账原子写 / 并发合并（atomic ledger）
| 用例 | 坐标 | 测法 |
|---|---|---|
| U1 旧快照不盖新状态 | checks-fix02.sh:2478-2511 | A/B 交叉 save：A1 仍 BLOCKED、B1 仍 COMPLETED、A2 存在；留痕 ledger_merge＋ledger_stale_write_merged |
| U3 同字段冲突保我这笔＋响＋history 并集 | checks-fix02.sh:2513-2543 | 断言 state/len(history)/含对方轨迹 三元组＋field_conflict_keep_mine 事件 |
| U4 写锁真互斥计时 | checks-fix02.sh:2545-2586 | 父持锁 4s，子 ledger_lock() 等待≥1.5s；顺序教训（先放锁再 communicate 防自死锁） |
| U2 双进程各推一笔都完成 | checks-fix02.sh:2588-2608 | 真并发 scan：两笔终态都在，验收记录不丢 |
| test_lost_update_is_loud / concurrent_save | test_relay_fix03.py:118-202 | 第三者绕锁直写：双方都不丢＋不抛＋留痕；正常读改写不被闸锁死（正对照） |
| K46 删除传播 | checks-fix02.sh:1342-1361 | merge_ledger：对方没动⇒删生效；对方改过同字段⇒保盘上值并响 |

### H. 读数诚实性 / fail-closed 通用形状（移植为产品 trace 规则）
| 用例 | 坐标 | 测法 |
|---|---|---|
| K40 只读动作零写入 | checks-fix02.sh:1148-1167 | gap 前后全树 find|shasum 集合相等；损坏台账⇒LEDGER_UNREADABLE 且不搬走不修复 |
| K36 死判据检测（数发得出的事件名） | checks-fix02.sh:1029-1054 | ref_count.sh 对不存在 rid=0、写入即=1、流缺失⇒NOFILE 非零 |
| K33/K73/K35/K28 缺对象/缺夹具/换目录⇒ABORT | checks-fix02.sh:1005,2352,988,706 | 各以"删一行/指错根/从别处调"构造，要求非零＋点名 ABORT 文案，不许静默绿 |
| K25 隐式根=脚本自身且读数印根 | checks-fix02.sh:640-655 | 裸调用 gap：root 字段=本目录，且≠冻结交包 |
| K75/preflight-verdict 空清单=用法错 | checks-fix02.sh:2459-2476; preflight-verdict.sh:12-14 | 无输入退 2；非"名称=整数"形状退 2 |
| K55 探针 unknown≠跑完；rc 件非整数⇒不采信 | checks-fix02.sh:1740-1799 | 四档矩阵（含 monkeypatch pid_state） |
| K38 册读不动⇒None⇒各方按未结挡 | checks-fix02.sh:1090-1118 | 不在/无表头/坏行 三档都 None；verify/delivery-note 调用方各钉一格 |

### I. artifact 引用完整性（reference + digest）
| 用例 | 坐标 | 测法 |
|---|---|---|
| K77 清单↔提交五档 | checks-fix02.sh:2689-2732 | git archive 生成清单：ghost⇒点名非零；哈希不符⇒点名；畸形行 MALFORM 非零；工作树篡改（提交未动）⇒仍 0（证明读 commit 不读树）；条目数现印 |
| K80 gitlink 两档 | checks-fix02.sh:2734-2755 | 子仓钉号一致⇒GITLINK OK；子仓前进⇒非零＋"钉在哪/现在哪" |
| K32/K41/K42/K43 候选不可变 | checks-fix02.sh:839-943,1169-1211 | 排除 state/inbox/handoffs/.git；排除运行态日志(K57)；expect_manifest 复用同枚；指纹不符拒派并提示换新请求号 |
| make-rollout-fixture.py 全文 | 同文件 | 只从真件切片（含本轮终态），sha256＋PROVENANCE 自证；旧切片天然成反例——产品夹具法照搬 |

### 属项目真相、必须留在产品路径之外（不删证据，仅出产品包）

| 站 | 它闸什么／断言什么 | 坐标 |
|---|---|---|
| 未结缺陷放行闸（含人豁免通道） | verify 整套判红/放行；`Q2C_WAIVE_DEBT=1`＝主理人签字通道 | verify-fix02.sh:185-206；checks-fix01.sh:602-617；checks-fix02.sh:685-704(K27),1090-1118(K38 对象部分)；relay.unreleasable_defects 全体调用方 |
| 套件退出码被缺陷染色 | DEBT>0 ⇒ checks-fix01 整体 exit 1（代码回归信号与发布状态耦合） | checks-fix01.sh:635-640 |
| 验收单/ACCEPTED/REJECTED/INVALID 支线 | "交付完成后自动叫醒第三方验收并回填结论"整条链：K1/K2/K3/K5(结论侧)/K7b 生成门/verify H 段/round4 A·B 档 | checks-fix02.sh:198-343,246-252(verify)；round4-real.sh:173-262；test_relay_fix03.py:208-257 |
| 分级白名单（grade A/B/C，越级即缺口） | acceptance grading 的字面实现 | test_relay_fix03.py:240-257；checks-fix02.sh:593-595(grade 字段) |
| gap 缺口判词（PENDING_ACCEPTANCE_GAP 等）作为对外工具 | q2c_gap 把"验收未闭"当产品 API 暴露 | q2c_mcp.py:65-69；K23/K26；delivery-note.sh:44-53 |
| 交付说明=对外唯一口径的"未闭项/同一枚候选"声明 | 发布叙事闸 | delivery-note.sh 全文（其中"SHA 现比"一格可摘出移植）；mutations N-1/H-1 反证随之 |
| 预跑→叫独立验收的门 | preflight-verdict 结论文案"可以拿它去叫独立验收"；preflight-candidate 的 0/1/6 退出码语义 | preflight-verdict.sh:29-33；preflight-candidate.sh:13-14；K75 |
| 验收派发文工程（gate_ids 点名待签字缺陷、放行闸写进 prompt） | 把发布审批状态注入给对侧 Agent 的消息 | checks-fix02.sh:919-933(K32 文案格),945-969(K34),2262-2292(K69),1213-1260(K44 第六格) |
| 工装哈希登记制 | "改工装必须留理由否则红"＝对验收过程本身的审批 | verify-fix02.sh:126-142 |
| COMPLETED/ACK_LINE/VERDICT: APPROVED 词汇本体 | 状态机终态名与采信行冒充项目结论 | 全部套件（改名映射：COMPLETED→DELIVERED/ACKED/RESPONDED 之一；APPROVED 不再是 q2c 登记的合法终态词，只作为 payload 内容透传） |
| 真腿 H 档"验收线程≠审核线程"当放行条件 | verify H2 | verify-fix02.sh:249-252 |

### 我不确定的（两种读法＋判定证据）

1. **"叫人"升级链（escalate→notify_human→NEEDS-HUMAN 待办）整体算不算传输。**
   读法 A：它是"对侧不可用时通知人类操作者"，属于 communication trace 的出口，q2c 拥有（K8/K10/K15/K18/K54/K67 全是它的牙）。
   读法 B：NEEDS-HUMAN＋"需人裁/签字"话术把人变成了工作流审批节点，human approval 被禁。
   定夺证据：任务书 §14 要"credential unavailable/adapter failure"测试且 §18 有 0 silent loss——失败必须让**某个接收方**可见才谈得上"silent"。我倾向 A 为机制保留、B 为词汇剥离（escalation→failure-notification），但需要主理人对"通知之外是否允许'待办/裁'语义"落笔。
2. **verify-fix02.sh:116-125 "relay.py 必须与基线不同哈希"。**
   读法 A：反"套件空转自我复证"的工装归因检查（本轮没动代码却叫"本轮判定"＝假绿）→RENAME 可留。
   读法 B："代码有改动"本身是项目状态断言→REMOVE。
   定夺证据：看它能否改成"被复验对象与所声称的提交一致"（whoami 形状）——能则转 A；若坚持"必须变过"则是 B。
3. **K59/K66/K74 的"终态闸"边界。**
   读法 A：判定"这轮 provider 调用是否真的跑完"=adapter 运行态观察，通信真相（V1.1 文档也把它归为 on_agent_completed 的证据）→KEEP。
   读法 B：turn.completed 之后再登 ACCEPTED/verdict，已跨入"任务完成真相"（任务书 §4 明令不许 TASK_COMPLETED）→KEEP 闸、砍登记。
   定夺证据：PROTOCOL §4 的 HANDOFF_PROGRESS/HANDOFF_FAILED 若覆盖"跑了但失败"，则闸留、其下游登记（verdict/acceptance 字段）出产品。这条边界建议由协议冻结时统一裁。
4. **q2c_whoami 的 code_dirty/kernel_head 报告。**
   读法 A：只报事实、不裁决——通信/引用真相，KEEP。
   读法 B：门牌文档把它与"放行闸"并读，实际充当发布门→REMOVE。
   定夺证据：产品 README 是否允许出现"working tree clean"字样（§17 发布条件里有！）——若 §17 由人执行，工具只报数就是 A。
5. **C9/K3/M-L 的"依据正文长度"判据。**
   读法 A：防空载荷冒充回执（协议层 minimum-payload 校验），RENAME 保留为"非空结构化正文"。
   读法 B：MIN_BODY 实质是给答复质量打分→REMOVE。
   定夺证据：把 40 字下限改为"结论行＋BIND 之外必须存在非空正文"这类形状判据即可归 A；保留具体长度即 B。
6. **K53/round4 的"尾巴预算 R4_TAIL_S"。**
   读法 A：父等子的墙钟（进程组清理），KEEP。
   读法 B：驱动等终态=替工作流盯进度，属 workflow state。
   我按 A 处理（它只等"答复落盘"这一物理事件，不判其内容），但若产品 trace 有 expires_at，则应并入 EXPIRED 机制而非独立预算。

### 覆盖度自报

- 逐行读完：verify-fix02.sh、checks-fix02.sh(2773 行全量)、mutations-fg.sh、mutate.py(全量)、preflight-candidate.sh、preflight-verdict.sh、delivery-note.sh、manifest-check.sh、notify_human.sh、proc_probe.sh、stub_qoder.sh、make-rollout-fixture.py、round4-real.sh、accept-real.sh、tools/denybin/{ps,pgrep}、stub_review.sh、stub_accept_reject.sh、stub_bare.sh、q2c_mcp.py(28-208 全文＋其余 grep)、门牌-Q2C.md(全文)。
- 按任务允许"抽样并只分类其职责"：checks.sh(标签＋判据行全 grep，正文关键段抽读)、checks-fix01.sh(120-239,548-643 全读，240-548 以标签＋grep 覆盖)、test_relay_fix03.py(96-206 全读，其余以 grep 行覆盖)、selftest.sh(1-60 全读＋头部九格清单)、README-评审.md/V1.1-方向(结构化 grep＋首段)。
- **未覆盖/未审**（不在给定清单，或被引用但本体未读）：h1-per-rid.sh（round4 D 档调用它，其"重复审核=[]"输出被引用）、ref_count.sh（仅经 K36/C 档读数间接）、verify-fix01.sh、real-legs.sh/real-legs-bc.sh/real-leg-c-mixed.sh/escalate-real-channel.sh（早期真腿驱动）、h1-rate.sh、BASELINE-*.txt/FIX-REPORT-*.md/SHA256-MANIFEST-*（纯文档/账）、evidence/**（按要求不读内容，仅记录写入方）、handoffs/、inbox/、fixrepo/、sample-repo/。
- evidence/ 写入方记录：mutations-fg.sh→evidence/mutations-fg/；round4-real.sh/accept-real.sh→evidence/legs/；preflight-candidate.sh→evidence/preflight-candidate*.log；checks-fix01/02→临时桩根（刻意不写被验根，见 K31/H1 教训行）；verify-fix02 只读 evidence/HARNESS-HASH-CHANGE.tsv 与 OPEN-DEFECTS.tsv。
### §B 结论后的六条待裁项与我的处置（记录在案，不因此停手）

审计里剩下六处"两种读法都能站住"的站点。任务书 §19 把"文件命名／模块归属／小幅重构"
划成不必请示的范围，同时产品定义里有一条更高的规矩：**与已验证的可靠性机制冲突时，
先保留机制、记录冲突、不自行删除保护**。我按这条逐条处置：

| # | 争的是什么 | 我的读法与处置 | 这条读法在产品里落到哪 |
|---|---|---|---|
| 1 | `escalate → notify_human → NEEDS-HUMAN` 整条"叫人"链是传输还是 human approval | **机制留、词汇剥**。q2c 只做到"失败必须让某个接收方可见，并留下指名到这一笔的送达回执"；不写"待裁/签字/审批"。任务书 §14 要 credential-unavailable／adapter-failure 测试、§18 要 0 silent loss——失败不可见就谈不上"silent"，所以这一格属传输 | `q2c/transport.py` 的 `notify()`／`notify_delivered()`／`_record_notify_ack()` （审计当时计划的
计划的模块名 `notify.py` 没有单列成文件，机制并进传输那层）＋协议里的 `HANDOFF_FAILED` 原因码；`NEEDS-HUMAN-*.txt` 那种"待办"物件改名成 failure 通知原件 |
| 2 | `verify-fix02.sh:116-125` "relay.py 必须与基线不同哈希" | **出产品路径**。它断言的是"本轮代码必须变过"＝项目改动有效性，不是通信事实。它要防的"套件空转自我复证"由另一格替代：被复验对象必须与所声称的提交一致（`commit_ref` ↔ 提交 blob 现算） | `q2c inspect` 的身份自证 + `tests` 里的"读数必须来自被点名的那枚提交" |
| 3 | 终态闸（`turn.failed`／无终态不采信）算不算 TASK_COMPLETED | **闸留、登记砍**。它判的是"这一轮 provider 调用跑完没有"，属运行态观察＝通信事实。跨界的是其后登记 `verdict`／`acceptance` 那一步 | 协议 §4 的 `STARTED`/`RESPONDED`/`HANDOFF_FAILED`；判据在 `q2c/ack.py`，结论词表不在产品里 |
| 4 | `q2c_whoami` 报 `code_dirty`／`kernel_head` | **留，改名 `doctor`/`inspect` 的读数**，且文档钉死"它不判放行"。任务书 §17 的 `working tree clean` 是**人执行**的发布条件，工具只报数 | `q2c doctor` 输出事实字段；SECURITY/README 里明写"读数≠批准" |
| 5 | 依据正文长度下限（`MIN_BODY=60`） | **改成形状判据，删掉数字**。产品要求"结论行与绑定行之外必须存在非空正文"，不再规定多少字——长度是质量打分，非空是防空口令冒充回执 | `q2c/ack.py` 的 `MIN_RESULT_BODY`（只判非空，不判长度） |
| 6 | "尾巴预算"（驱动等子进程收尾的墙钟 `R4_TAIL_S`） | **并入协议里的 `expires_at`/`EXPIRED`**，不再作为独立预算存在。它等的只是"答复落盘"这一物理事件，与 `expires_at` 同义 | 协议 §4 `EXPIRED`；`q2c/transport.py` 的到期判定 |

这六条里，只有第 1 条与第 4 条我认为是**主理人可能想改我读法**的（牵动约 20 行最终归类
和 README 的措辞），已在 `Q2C-v0.1.0-RELEASE-REPORT.md` 的 KNOWN_LIMITATIONS 里点名，
不等答复、按上面的处置继续做。


---

## §C 汇总：分档计数、迁移映射、与产品测试的对应

### C-1 现读计数（别抄上面任何自报数，跑这条）

```
python3 tools/count-audit.py            # 本文件随包带着这个计数器
```

本轮读数（2026-10-04 14:3x，产品根 `q2c-product-v01`）：**已分类 232 行**
— `KEEP_AS_TRANSPORT` 142／`RENAME_AS_TRANSPORT` 38／`REMOVE_FROM_PRODUCT_PATH` 30／`DEPRECATE` 22。
四档都有条目，审计不是"只挑好的分"。行数≠站点数：一行可写一族站点，
§B 的复核方按语义自报 184 站；两个数各回答各的问题，不许互相顶替。

### C-2 七类禁责的落点（产品里去哪儿了）

| 禁责 | 现在在哪 | 产品里的对应物 |
|---|---|---|
| 验收定级 AG | `ACCEPTANCE_GRADES`／`acceptance_gap_summary`／K1–K3／round4 A·B 档 | **无**。q2c 不产生也不保存定级；`payload`／`metadata` 里出现什么词由发送方负责，桥不解析 |
| 项目完成 PC | `COMPLETED` 状态／`is_already_reviewed` 写完成／delivery-note 的"闭环"叙事 | 拆成两件事：幂等抑制重复投递（`idempotency_key`）＋传输终态 `ACKED`。`ACKED` 只说"回了话且可归因" |
| 评审批准权 RA | `VERDICT_RE`／`ACCEPT_RE`／`invalidate`／gate_ids 注入派发文 | **无**。结果谓词由发送方给（`Adapter.deliver()` 返回 `Receipt`，判据在 `ack.py` 只查结构与归因） |
| 工作流恢复 WR | `collect_accepts` 的验收腿分流／`backfill` | 保留成**传输恢复**：迟到答复同绑定串认领、重启后不重复消费、未知⇒停手（`transport.recover`） |
| worker 重试预算 RB | `RELAY_RETRY_MAX`＋`attempts`＋"到上限转 BLOCKED 等人裁" | 改名 `delivery_attempts_max`，语义收窄为**只重投消息**（协议 §5），到上限 ⇒ `DELIVERY_FAILED`＋失败通知，不重跑任务 |
| 项目调度 PS | `advance` 取 QUEUED 首／`loop` 120 轮常驻／`create_acceptance` 自动串单 | 只留"每条会话同一时刻至多一腿在飞"的串行化（`inflight_on(session)`）。无常驻循环、不自动新建后续任务 |
| 放行闸门 RG | `unreleasable_defects`／`Q2C_WAIVE_DEBT`／verify E1c／套件退出码被缺陷染色 | **无**。产品 CI 绿＝测试绿；放行由 Owner 判。§B 里那条"代码回归信号不许被发布状态染色"作为产品 CI 的设计约束写进 CONTRIBUTING.md |

### C-3 §B 的 64 条迁移保护 → 产品测试文件

§B 第 2 节按 A–I 九组列了要随传输层一起走的机器测试。这九组在产品的落点（任务书 §14/§15 的
每一项都至少有一组对着，没有"文档里承诺了但没文件"的格子——文件不存在就不许写进这张表）：

| §B 组 | 保护本体 | 产品测试文件 | 任务书 §14 对应项 |
|---|---|---|---|
| A 幂等／重复投递 | C1／F6／K6／K6b／F7／H1／selftest#6／K52 | `tests/test_idempotency.py` | idempotency、duplicate delivery、**0 unexpected duplicate effects** |
| B 重启再消费／恢复 | C3／C4／C5／G4／G6／K50／K62／K51／换 TAG 教训 | `tests/test_recovery.py` | restart recovery、§15 restart duplicate review |
| C 送达 ACK | F1–F5／K65／K37／K71／K13／K21／K20／notify_human 结构判／F9 | `tests/test_ack.py` | ACK、adapter failure |
| D 关联／防串账／防复述 | K4／K9／K17／K22／K45／K39／K66／K68／K11／K8 | `test_correlation.py`（计划名） | correlation、§15 无（属新增边界） |
| E 进程／孤儿／递归／锁 | K29／K60／K61／K52／K54／K67／K78／U4 锁／C7／K58 | `tests/test_process_safety.py` | process cleanup、§15 recursive runner／orphan child |
| F 凭据 fail-closed＋不泄密 | K76 五档／E-cred-gate／E-cred-blocked-overwritten | `tests/test_credentials.py` | credential unavailable、§15 credential unavailable |
| G 台账原子写／并发合并 | U1／U2／U3／U4／test_lost_update_is_loud／K46 | `test_ledger.py`（计划名） | 传输恢复的物理前提 |
| H 读数诚实性 | K40／K36／K33／K73／K35／K28／K25／K55／K38 | `test_read_honesty.py`（计划名） | "0 silent loss" 的可信度前提（测不到就说测不到） |
| I 产物引用完整性 | K77／K80／K32／K41／K42／K43／K57／make-rollout-fixture 法 | `test_artifacts.py`（计划名） | artifact reference integrity |
| （新增，无旧件） | 1000 枚合成交接的吞吐与非静默 | `tests/test_synthetic_fanout.py` | 1000 synthetic handoffs |
| （新增，需真机） | Codex↔Qoder 双向真实交接各一次 | `tests/test_real_bidirectional.py`（默认跳过并如实报 SKIP，须显式 `Q2C_LIVE=1`） | 两条 real handoff |

**落点对照（2026-10-04 现读，不改上面那张计划表本身）**：上表"产品测试文件"那列写的是
§B 计划里的名字，落地时按产品模块边界合并过。勘误口径只有一条：那五枚计划名在这里
**按名引用**（去掉了 `tests/` 前缀），因为带前缀就成了"这个文件存在"的断言，而它不存在——
名字本身与当时的计划逐字保留，右列才是今天的真落点。逐字对照如下，引用请按右列找：

| 计划名（表中按名引用，不带 `tests/` 前缀＝不代表已落地） | 实际在册件 |
|---|---|
| `test_correlation.py`（计划名） | `tests/test_idempotency.py`、`tests/test_retry_delivery.py`、`tests/test_qoder_terminal_real_fixture.py`（`TestStaleAttributionFromRealLegs`＝上一轮绑定串被复述那一族） |
| `test_ledger.py`（计划名） | `tests/test_stores_and_honesty.py`（原子写／三方合并／陈旧检测） |
| `test_read_honesty.py`（计划名） | `tests/test_stores_and_honesty.py`（读不到就说读不到那几格） |
| `test_artifacts.py`（计划名） | `tests/test_stores_and_honesty.py`（产物引用四态＋清单指纹） |
| `test_retry_is_delivery_only.py`（计划名） | `tests/test_retry_delivery.py` |

另加两件不在计划表里的：`tests/test_shell_portability.py`（发布门脚本在异机 `/bin/sh`
上的可移植性；2026-10-04 独立 CI 两枚 job 红在这里）、
`test_real_handoff_evidence.py`（真跑原件的哈希复算，件名不带前缀＝本格的扫描器不许把它当已落地引用，防"证据躺在目录里没人对"）。
`tests/test_doc_references.py` 会扫全部文档与代码注释里出现的 `tests/test_*.py` 件名并逐个查
存在性——文档指向不存在的判据件＝判据直接红，不用等人发现（这一格今天就抓到了上面五行悬空引用）。

### C-4 一句话结论

已验证的传输机制**一条都没打算重写**：产品的 `ledger／ack／transport／recovery` 就是
`relay.py` 里那几段被逐条点过名的代码搬出来换了名字；
被剥掉的只有**定级、批准、放行、串单、常驻循环**五件，
它们的原件与证据留在 fix02 交付根，一个字节没动。

<!-- 公开口径脱敏（2026-10-04 发布前）：上面出现的 `<本地已验证根>`／`<本地构建根>`／`<插件交付目录>`
     是我这台机器上的绝对路径，公开文本里不留本机坐标与用户名；原件位置未变，仍在私有一侧。
     这一条由 tests/test_release_domain_local_paths.py 盯着：发布域里出现本机路径模式＝判据红。 -->
