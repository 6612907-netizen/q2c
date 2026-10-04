# 怎么改 q2c

## 先读三份文件

1. `Q2C-PRODUCT-SPEC-v0.1.md` —— 定位与十条"不许放回核心"的能力；
2. `PROTOCOL.md` —— 字段／枚举／状态机是**行为契约**，不是装饰；
3. `Q2C-BOUNDARY-AUDIT.md` —— 你要加的东西是不是已经被判定为项目真相。

## 本地跑什么

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -t .   # 全部判据
sh tools/clean-machine-test.sh                                          # 清洁机复验
python3 tools/report-readings.py --json                                 # 发布读数（不许手打数字）
```

真实双向交接那一组默认 `SKIP`（需要 `Q2C_LIVE=1` ＋已存在的线程／会话号）。
**别为了变绿把 SKIP 改成 pass**，也别拿零模型的绿顶替那一格。

## 六条规矩（违反者按不合格处理）

1. **不许改判据来变绿。** 测不到就写测不到（`UNVERIFIABLE`／`NOT_OBSERVED`／
   `SKIP` 三档都在读数里）。旧内核里"改个状态名就等于自己开了闸门"这类事被抓住过不止一次。
2. **不许把项目真相放回核心。** 想加 `approve`／`grade`／`release` 类动词或字段，
   先拿产品授权，再谈实现；`test_10_no_project_management_verbs_exist` 与
   `trace.FORBIDDEN_EVENT_KEYS` 会直接红。
3. **重投 = 重投消息。** 任何"再叫一次对侧模型"的写法都是缺陷（协议 §5）。
   PR 里请附上 `responder_calls()` 的前后对比，或等价证据。
4. **未知输入不许"挑最多的那条路"。** 新枚举值必须与 fail-closed 分支同批出现，
   且有一格反例判据（`UNKNOWN_*` ⇒ 拒绝＋零副作用＋退出码 2）。
5. **文档与代码不许两份真源。** 改了 `protocol.py` 的名单，`PROTOCOL.md` 必须同步——
   `tests/test_protocol_doc_sync.py` 逐字比，包括 §2 字段顺序与"有行读不出取值即红"。
6. **只读命令不许写盘。** `inspect/list/trace/doctor/adapters/version` 跑完，
   被观测对象的哈希集合必须一字不变（`test_28_read_only_commands_write_nothing`）。

## 加一个适配器

照 `ADAPTERS.md` §5。要点：`capabilities()` 必须如实报限制；解析放适配器、判定放核心；
能力位 honesty 与契约完整性两格判据要跟着加。

## 提交前的门（退出码说话，不靠人盯输出）

```
sh tools/gate-commit.sh        # 全量判据 ＋ smoke；任何一步非零或没打出 OK／ACKED ⇒ 退 1
```

为什么单独有这条（2026-10-04 的自伤）：我把一串动作写成
`unittest discover … | grep -E "^Ran |^OK|^FAILED" && git commit …`——`grep` 匹配到 `FAILED`
仍退 0，于是判据红着也照样提了，那一笔把 CI 全矩阵判红。
**"我看见红了"不等于"门拦住了"**：门必须是退出码。所以提交前跑这一条，别拿 grep 的返回值当门。

## 提交与 CHANGELOG

- 每个 PR 更新 `CHANGELOG.md`（`Unreleased` 段），写清**行为**变化而不是文件清单；
- `protocol_version` 的兼容规则见 `PROTOCOL.md` §9：加可选字段可以同版本，
  **加状态或改语义必须升主版本**；
- 破坏性动作（清理类）必须显式指定目标根才允许执行——这条继承自实验根的一次真事故。

## 代码风格

纯标准库、Python ≥3.9、不引入常驻服务／网络出站。
含中文的字符串一律用全角引号或三引号（ASCII 双引号嵌进中文串会在解析期炸，踩过）。
git 路径一律 `-z`／NUL 分隔，不解析人类格式输出（`quotePath` 坑过中文路径）。
