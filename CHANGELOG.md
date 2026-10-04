# 更新记录

格式遵循 Keep a Changelog；版本号遵循 SemVer。
**注意**：`protocol_version`（现在是 `q2c/1`）与包版本是两个独立编号——
加状态或改字段语义必须升协议主版本，见 `PROTOCOL.md` §9。

## Unreleased

（下一个 PR 在这里起一段，写行为变化而不是文件清单。）

## 0.1.0 — 2026-10-04（首次准备公开发布，尚未发布）

### 新增

- 协议 `q2c/1`：17 项信封字段、6 类消息、11 枚传输态、8 枚失败归类、9 类产物引用；
  显式排除 `TASK_COMPLETED`／`READY_TO_RELEASE` 等项目真相取值。
- 传输层：有界重投（只重投消息）、幂等抑制、到期、取消、重启恢复阶梯（未知≠没跑）。
- 送达确认六闸＋终态闸，对所有适配器同一套尺；`ACKED` 定义为"回了话且可归因"。
- 逻辑会话与 provider 绑定表（换绑留档、要理由、按时间轴取当时那一枚）。
- 产物引用：引用＋摘要、快照清单指纹、四态核验（`OK/MISSING/DIVERGED/UNVERIFIABLE`）。
- 只追加跟踪，九问读数；写前脱敏；事件流拒绝项目真相字段名。
- 安全层：凭据六条、路径与工作区白名单、进程组清理、退出码侧件、状态件 0600、fail-closed 总则。
- 适配器：`codex`／`qoder`（公开 CLI 档）＋ `loopback`（真子进程判据与 Quick Start）
  ＋ `inproc`（负载判据专用，能力位标 LOAD-ONLY）。
- CLI 11 子命令，退出码 `0/1/2/3` 为契约；只读子命令零写入。
- 判据全部走 unittest（无第三方依赖）；**格数不在这里抄**——由 `tools/report-readings.py`
  现读进发布报告（在册教训：手打的计数一定会漂成假话）。含 1000 枚合成交接：
  0 静默丢失、0 意外重复效果。
- 文档：`README`／`SECURITY`／`PROTOCOL`／`ADAPTERS`／`ARCHITECTURE`／`CONTRIBUTING`
  ／`Q2C-PRODUCT-SPEC-v0.1`／`Q2C-BOUNDARY-AUDIT`（232 行逐点分类）。

### 变更（相对旧实现 `q2c-wakeup-fix02`）

- 状态词表：`COMPLETED` ⇒ `RESPONDED`/`ACKED`；`BLOCKED` ⇒ `DELIVERY_FAILED`（停放态）。
- `MIN_BODY=60` 的字数下限删除，改为"非空正文"形状判据（长度是质量打分，非空是防空口令）。
- 尾巴预算（驱动等收尾的墙钟）并入协议 `expires_at`／`EXPIRED`，不再作为独立预算存在。
- 叫人链保留机制（逐笔送达回执），剥离"待办／签字／审批"词汇。
- 不再依赖 Codex 私有 SQLite 队列库与 `~/.codex/sessions` 内部 JSONL 形状；
  对应恢复证据位降级为 `UNVERIFIABLE ⇒ 停手等人`（代价写进 KNOWN_LIMITATIONS）。

### 移出产品路径（原件与证据一律未动）

验收定级、评审批准权、放行闸门（含豁免通道）、缺口判词、自动串单、常驻轮询循环、
一键清台账。逐条坐标与"移除会丢哪条保护"见 `Q2C-BOUNDARY-AUDIT.md` §C-2。

### 修复（冻结前四跑真腿＋两枚独立 CI 复验抓出来的，逐条都有反例格）

- `ack.receipt_from_cli_json()` 的 `stream_terminal` 参数被收下却**没写进回执**：
  凡声明 `terminal_evidence=True` 又走这条参考实现的适配器，终态恒读成"未提供"，
  真投递永远被终态闸拒。现在参数真的接进回执，`None`（不提供）与 `absent`（提供了但没终态）
  保持两档不并。
- Qoder 腿用 **Codex 的事件词表**读自己的单帧收口 ⇒ 合格答复被拒。新增
  `ack.scan_result_frame_terminal()`（成功／失败／absent／unknown 四档），
  两套词表各自独立且互不采信（`ADAPTERS.md` §4.1）。
- 在飞子进程被判失败 ⇒ 下一拍重新入队并再次 `exec resume`，**同一请求被叫醒两次**
  （真腿实测）。现在先看进程存活，再决定是失败还是在飞。
- 结果正文"除协议两行之外还有没有内容"从闸门降回**观测**（`result_body_only_protocol`），
  桥不判答复质量；两条读数仍在。
- 发布门脚本的 shell 方言两处：`&&` 起行（macOS 与 ubuntu 的 `sh` 都在解析期拒）、
  `$VAR` 紧跟全角标点被并进变量名（macOS 托管 runner 报未绑变量，脚本在末行状态前就死）。
  本机 shell 看不见第二处 ⇒ 新增静态判据 `tests/test_shell_portability.py`（含 `sh -n`／`dash -n`
  ＋变量紧邻非 ASCII＋行首逻辑符＋把门真跑一遍）。
- `install.pip` 那一档以前要求机器预装 setuptools 且 PATH 上有 `pip`，
  把"能不能试"当成了前提，结果 macOS 托管 runner 明明装得上却被报 `UNVERIFIED`。
  现在走 venv 自带的 pip；装不上才如实报，且 `FAIL`（装上了跑不起来）判红不豁免。
- CI 复验作业里 `python3 -m unittest … | tee` 会把退出码换成 `tee` 的 0 ⇒ 判据红了作业照绿。
  现在退出码单独接、单独判，并加 `OK` 行核；`git status` 那格在非 git 环境报 `NO_GIT`，
  不折成"改动数=0"。
- 真跑跑批件 `tools/real-handoff.py`：派发文里删掉手抄的第二份绑定行模板（占位符没填就发出去，
  等于同一封信给对侧两条互相矛盾的指令）；默认**每次新建会话**，避免对侧回上一轮那枚串
  （`ADAPTERS.md` §4.2）；每次跑完把原件逐枚登记 SHA256，判据从盘上重算比对。
- 跟踪九问两处**读数撒谎**（真腿第四跑抓出）：`state_now` 只认 `ev=="state"` 而产品从不这么发事件名，
  于是每条真记录都被报成 `UNVERIFIABLE`（时间线上明明看得见收口）；`which_retries` 只认人手
  `retry-delivery` 写的 `retry_of`，结果腿的补送达被漏读。现按 `to_state` 取末态、两条重投来路都可见，
  并钉住反向（没有的证据不许读出来；同步形态的 `when_delivered` 仍须 `NOT_OBSERVED`）。
- 文档与注释里五处指向**从没落地过**的判据件名（`test_correlation.py` 那一族）与一枚不存在的
  模块名 `notify.py` ⇒ 新增 `tests/test_doc_references.py` 逐个查存在性；审计表补
  "计划名 → 实际在册件"的落点对照（`Q2C-BOUNDARY-AUDIT.md`）。
- 真跑报告新增 `--from-run` 复分模式：跑批件自己改字段时从已在盘的原件重建报告，
  **零新调用、不改任何原件**（前后逐枚哈希同校，已复算通过）。
- 真跑证据的身份改为**声明**而不是猜：新增 `evidence/real-legs/LATEST.json` 指针件
  （记 run_id／报告路径／报告自身的 SHA256／判决），判据只读指针指的那一枚。
  旧规则"按 mtime 挑最新"在 `git archive` 解包件里所有 mtime 相同 ⇒ 两枚 runner 各挑到
  不同的旧失败报告，同一份代码报出两种 `REAL_HANDOFF_EVIDENCE`。
  同时钉死一条：有报告却没指针 ⇒ **报红**，不许静默 skip（摘掉写指针那一步不该让整组证据判据消失）。
- 清单工具那一处同族缺陷（`exclude_prefix` 参数写下没用）另见上；新增两页清单与
  `tests/test_release_manifest.py`（解包件里没有 `.git` ⇒ 如实 skip 并写清测不到）。
- 发布包与清单的分工定死（并由判据钉住）：包里只装代码／文档／工装／判据；
  `evidence/` 与本报告自身**不随包走**。理由不是省事——报告里最后一行是发布结论，
  若报告算发布物，则"写下结论"这一步会改动发布物字节，结论要么先写（预言）要么后写（包不符）。
  代价写明白：包里读不到本次结论，读者去仓里或交付副本读。
- 清单校验的对象改成**清单头记的那枚提交**（不再是分支尖）：冻结后往 `evidence/` 存新原件
  不会让清单变红；动了发布域文件则报 `DRIFTED` 并非零退出。旧规则下每存一份证据就得重打一张
  清单，而重打的那张又让下次校验变 STALE——那是个死循环。
- 新增判据 `test_06`（包的件集＝发布域清单项，逐一对齐）／`test_07`（临时小仓里验"加证据不变红、
  改发布物必变红"）／`test_08`（报告不在包里、但在证据域清单里）；`tools/ci-clean-machine.sh`
  的打包 pathspec 与之同源。
- 补一枚**本机复演**件 `tools/pkg-rehearsal.sh`：从提交打包 → 解到全新目录 → 跑整包判据 →
  跑清洁机门。独立 CI 连红三轮，后两轮的拒因（按 mtime 挑报告、包里没证据件时某格没自己的门）
  只在那种"没有 `.git`、mtime 齐平、没有 `evidence/`"的树上现形，开发机跑整包看不见。
  这格现在先在本机烧，不再拿托管 runner 当本地测不出来的补锅。
  独立性判定同时收严：要 `RUNNER_ENVIRONMENT=github-hosted` **且** `GITHUB_RUN_ID` 才算托管，
  复演走显式 `REHEARSAL=1` 并明写 `ENVIRONMENT=REHEARSAL_ON_DEV_MACHINE`，不伪造变量。
- 复演第一跑就抓到一处：`tests/test_real_handoff_evidence.py` 的 test_14 属于没带门的那个类，
  包里没有报告时 `open("")` 抛 FileNotFoundError ⇒ 独立环境整包红。该格现在有自己的门。
- 新增 `tools/ci-readings.py`：从 CI 原始日志 grep 出那四件要求（环境身份／包 SHA256／
  完整日志／末行状态）折成一页读数，取不到写 `MISSING`；发布报告里的独立复验数字全部出自它，
  不手打。
- 公开前脱敏：发布域 71 个文件里扫出本机绝对路径、外置卷标与另一个私人项目的目录名
  （`/Users/<用户名>/…`、`<外部卷>/…`）——这些对读者零价值，只是把这台机器的坐标公开。
  已全部换成 `<本地构建根>`／`<外部卷>` 形态占位，证据域不动（那里本机路径是事实的一部分，
  且它不进包、不进公开仓）。并补一格静态闸 `tests/test_release_domain_local_paths.py`：
  发布域再出现这类坐标＝判据红（"路径外泄没有测试会红"这条老坑，这次提前堵）。
