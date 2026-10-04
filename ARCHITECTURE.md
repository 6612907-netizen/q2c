# 架构

## 1. 分层与依赖方向（单向，箭头指向"被依赖"）

```
                 ┌──────────────────────────────┐
  用户 / CI ────►│  cli.py  （11 个子命令）       │
                 └───────────────┬──────────────┘
                                 │
        ┌────────────────────────┼─────────────────────────┐
        ▼                        ▼                         ▼
  transport.py            sessions.py                trace.py
  （收单/投递/重投/         （逻辑会话↔               （只追加事件流
   到期/取消/恢复）          provider 绑定）            ＋九问读数）
        │                        │                         ▲
        ├──────────► ack.py ◄────┤                         │
        │            （六闸＋终态闸：判据在核心）              │
        ├──────────► ledger.py ◄─┘（台账：flock＋原子写＋三方合并）
        │
        ├──────────► artifacts.py（typed ref＋摘要＋快照清单）
        │
        └──────────► adapters/base.py（契约七枚＋注册表，未知名字硬拒）
                          │
              ┌───────────┼───────────┬─────────────┐
              ▼           ▼           ▼             ▼
          codex.py     qoder.py    loopback.py     inproc.py
              │           │           │             │
              └───────────┴─────┬─────┴─────────────┘
                                ▼
                          security.py ＋ _spawn.py
                  （凭据六条／路径／进程组／脱敏／原子落盘／退出码侧件）
                                ▲
                          protocol.py（信封／枚举／状态机）
                                ▲
                          config.py（默认＋env 覆盖；拒绝持有凭据）
```

**不许反向依赖**：`security`／`protocol` 不认识 transport；`adapters/base` 不认识 transport；
`trace` 不认识适配器。核心永远不知道 provider 的字段名——那是适配器的本分。

## 2. 一条消息的调用图（`q2c send`）

```
cli.main
 └ transport.send
    ├ transport.create
    │  └ _create_one
    │     ├ _check_workspace            → security.check_ref_path / workspace_allowed
    │     ├ 幂等扫描（同 idempotency_key ⇒ duplicate_suppressed＋留痕）
    │     ├ security.contains_secret_shape（只出标记，不改正文）
    │     ├ ledger.load_for_write / ledger.save（flock＋合并＋原子写）
    │     └ trace.emit ×2（created / queued）
    └ 有界推进（max_steps=8，到界就返回，不自轮询）
       └ transport.pump → _advance
          ├ _deliver_request
          │  ├ _bind → sessions.get / adapters.build / adapter.resume|start
          │  ├ state → DELIVERING
          │  ├ adapter.check_credential（六条；不过 ⇒ 一次 CLI 都不叫）
          │  ├ adapter.send（派发文逐字落盘＋投递）
          │  └ state → DELIVERED（可分腿）| STARTED（同步腿，记 collapsed_delivery_start）
          ├ _await_or_respond
          │  ├ adapter.receive → _spawn.spawn（进程组／退出码侧件／到点不杀）
          │  ├ ack.judge_ack(require_terminal=能力位, require_ack_line=False)
          │  ├ ack.judge_result_shape（非空正文，不判长度）
          │  └ _register_result（结果原件＋SHA256）→ RESPONDED
          └ _deliver_result
             ├ _render_result（要求逐字回出 Q2C-BIND 与 ACKED）
             ├ adapter.send / adapter.receive（结果腿）
             └ ack.judge_ack(require_ack_line=True) → ACKED
                └ 失败：_fail → DELIVERY_RETRY（预算内）| DELIVERY_FAILED（停放＋失败通知）
```

## 3. 状态机（`q2c/protocol.py::_TRANSITIONS`）

```
CREATED ─► QUEUED ─► DELIVERING ─► DELIVERED ─► STARTED ─► RESPONDED ─► ACKED ✚
                       │              │            │           │
                       └──────────────┴────────────┴───────────┴─► DELIVERY_RETRY ─► DELIVERING
                       │                        （同一 message_id／绑定串／派发文）
                       └──► DELIVERY_FAILED（停放态）──► DELIVERY_RETRY | ACKED | EXPIRED | CANCELLED
任意非终态 ─► EXPIRED（到 expires_at） | CANCELLED（发送方取消投递）
终态：ACKED | EXPIRED | CANCELLED（之后任何事件只进跟踪，记 STALE_EVENT）
```

**`DELIVERY_FAILED` 是停放态而不是终态**：桥自己绝不从这里自动再试，
但"人显式 `retry-delivery`"与"迟到答复被收回"两条合法路可以推走它。
写成终态的实际后果是逼人伪造新 `request_id` 去绕开规则。

## 4. 落盘布局（`Q2C_HOME`，一个目录装全部，删掉即清除）

```
config.json                 0600  配置（拒绝持有凭据）
state/ledger.json           0600  台账 {rev, records{}, written_at, writer_pid}
state/ledger.lock                   flock 的把手（整轮临界区）
state/sessions.json         0600  逻辑会话与绑定历史
state/inflight/<provider>/  0600  每腿落盘件：<message_id>.a<attempt>.out(+.rc)、*.sent.txt
state/notify-ack.jsonl      0600  逐笔"送到人没有"的回执账
trace/events.jsonl          0600  只追加事件流（写前脱敏）
results/<request_id>.result.txt  0600  回复原文（引用＋SHA）
mailbox/ inproc-box/               假对侧信箱（loopback／inproc）
packages/                          证据快照（artifacts.export_package）
notify/<request_id>.txt     0600  失败通知原件
```

## 5. 为什么这些件在核心、那些在适配器

判据只有一条：**换一家 provider 时会不会变。**
"退 0 不算送达""绑定串要逐字整行""重投不换消息号""未知就停手"——换谁都不变 ⇒ 核心。
"`subtype=="success"`""`turn.completed`""`-r <会话号>`""要不要剥 SDK 变量"——换一家就变 ⇒ 适配器。

旧实现（fix02 交付根）里这两层是搅在一起的：同一段代码既读 provider 字段又判送达，
于是加一个对侧就得改核心，改核心就得重跑全部回归。
收敛结果与逐点分类见 `Q2C-BOUNDARY-AUDIT.md`；剥出去的五件事
（定级／批准／放行／串单／常驻循环）的原件与证据一个字节没动。

## 6. 规模与形状（现读，别抄这里的数字）

```
python3 tools/report-readings.py --json   # 判据数、耗时、审计分档、HEAD
grep -c "^def \|^class " q2c/*.py q2c/adapters/*.py
```
