# Q2C v0.1.1 RELEASE REPORT

读数一律现跑（`python3 tools/report-readings.py --json`；每个状态值后面都跟着能复跑的命令）。
本报告不由缓存或上一轮结论填格子。v0.1.0 那一份仍在仓里（`Q2C-v0.1.0-RELEASE-REPORT.md`），
它是**上一版的发布记录**，本轮不改写它。

## 0. 版本身份（主理人 2026-10-04 裁定的那三条）

```
VERSION=0.1.1
SEMVER_REASON=补丁号——只改打包／分发／文档边界与工装口径，产品逻辑与协议一字未动
PYPI_VERSION_CHOICE=(a)  # PyPI 上的 0.1.0 不指向本轮那套字节
v0.1.0 冻结件=永久不动（tag 与 GitHub Release 附件都保持原字节）
GitHub v0.1.0 → 提交 2213ba68561d8562d666f51b41aa9876f8b2776a（历史冻结）
GitHub v0.1.1 → packaging／distribution 完善版
PyPI q2c 0.1.1 → Q2C 第一个 PyPI 正式版本（本轮待身份授权）
```

任何人可复跑这两条来自证「产品逻辑没动」：

```
git diff --name-only v0.1.0..<本报告头部记的那枚 v0.1.1 提交> -- q2c/
  # 只应出现 q2c/_version.py（版本号那一行）与 q2c/__init__.py（改成从 _version 取）
git show <那枚提交>:q2c/protocol.py | shasum -a 256
  # 应与 v0.1.0 那枚的同名文件同哈希 ⇒ 协议与状态机一字未动
```

## 1. 协议与产品边界（未变）

```
PROTOCOL_STATUS=DOCUMENTED+FROZEN(q2c/1) — 17 项信封字段、6 类消息、11 枚传输态、
  8 枚失败归类、9 类产物引用；显式排除 TASK_COMPLETED／READY_TO_RELEASE／COMPLETED 等取值。
  钉法：tests/test_protocol_doc_sync.py（代码与文档逐字一致）。复跑：python3 -m unittest tests.test_protocol_doc_sync
PRODUCT_SCOPE=消息投递／关联／幂等／传输 ACK／传输重投（只重投消息）／逻辑会话映射／
  Agent 适配器／产物引用／传输恢复／通信跟踪／传输安全。
  不含：工作流任务状态／项目调度／CI 真相／代码质量判断／评审批准／放行决定／
  项目重试预算／工作流恢复／人工批准／任务完成。逐点依据见 Q2C-BOUNDARY-AUDIT.md
```

## 2. 厂商边界（本轮新写死的一屏）

**验过的目标是 Qoder CN，不是 Qoder International。** 这一条以前只在代码里（`BIN = "qoderclicn"`），
文档泛称"Qoder"——读者会以为整条产品线都验过，然后拿另一支照抄命令、第一条就起不来，
回头怀疑桥不可靠。现在四行机器可读口径进 README（＝PyPI 包页面正文），并由判据钉住：

```
QODER_CN_VERIFIED=YES                     # 真跑过：与 Codex 双向各一次，两腿都 ACKED
QODER_INTERNATIONAL_VERIFIED=NOT_VERIFIED # 没跑过，不在支持声明里
CODEX_VERIFIED=YES                        # 公开 CLI 档：queue 投递＋exec resume --json 取答复
CORE_PROTOCOL_VENDOR_NEUTRAL=YES          # 核心（协议／台账／跟踪／ACK）不含厂商字段
CN_SPECIFIC_DEPENDENCIES=CLI 名 qoderclicn; 命令形状 -p -r <会话号> -w <工作区> --permission-mode auto --output-format json; 起子进程前剥掉的 QODER_AGENT_SDK_* 一族变量; 收口帧 {"type":"result", is_error:false, subtype:"success"} 的单帧形状
```

判据：`tests/test_vendor_claims.py`（7 格）。其中三格是**反向钉**：
① 文档里的 CLI 名必须与 `q2c/adapters/qoder.py` 的 `BIN` 一致（改代码不改文档就红）；
② 任何进入 PyPI 页面的文本把 International 与"已验证／VERIFIED"写在一起 ⇒ 红；
③ `NOT_VERIFIED` 那一档被改成 `YES` ⇒ 红。
"中性"那句的限制也钉住了：它只说核心，真适配器仍然认具体 CLI——不许被读成"任何 Qoder 都能用"。

## 3. 包分发（本轮主体）

四处缺陷，全是现读抓到的：

1. **版本双真源**：`pyproject.toml` 与 `q2c/__init__.py` 各写一遍 `0.1.0`。
   现在唯一真源＝`q2c/_version.py`，pyproject 走 `[tool.setuptools.dynamic] attr` 取它（AST 字面量，不 import）。
2. **坏链**：`[project.urls] Protocol = "PROTOCOL.md"` 不是 URL；补齐 Homepage／Repository／Issues／
   Changelog／Protocol／Security。
3. **发出去的 sdist 跑不了它自带的判据**：带着判据却没有 `tests/__init__.py`，也没带
   `bin/`／`tools/`／`examples/`／文档 ⇒ 别人解包后 `unittest discover` 当场 ImportError，
   而交付页一直在教他这么做。现在由 `MANIFEST.in` 声明收录；`evidence/` 与发布报告仍**不进包**。
4. **装完没有可抄的命令**：README 新增「装上就跑」，并由门的第 6 档**逐字抽出那一节真跑**。
   另修：`license = {text = "MIT"}` 表写法构建期告警点名 **2027-02-18 起拒建** ⇒ 换 SPDX 串，
   构建下限提到 `setuptools>=77`。

门＝`tools/pkg-install-test.sh`（CI 的 `package` job 只叫这一条命令；退出码 0 合格／1 不合格／2 测不了）：

```
命令：sh tools/pkg-install-test.sh
读数：<见下面 §4 托管首跑那一屏，跑完填>
```

## 4. 托管环境与独立复验（本轮的关口在这里）

```
CI_MAIN_PUSH=<待推 main 后填 run 号>
CI_PACKAGE_JOB=<待填：ubuntu-latest py3.13／macos-latest py3.13／ubuntu-latest py3.9>
CI_TESTS_JOB=<现成那道 ci.yml：3 个 Python × 双 OS>
CI_CLEAN_MACHINE=<现成那道：对冻结提交做独立干净机复验，双 OS>
PACKAGE_SHA_WHEEL=<现算>
PACKAGE_SHA_SDIST=<现算>
```

**这一屏没填齐之前，本轮结论只能是 BLOCKED。** 规则照旧：托管环境那一格不许拿本机绿顶替。

## 5. 判据与验牙（现读）

```
TEST_STATUS=306 格 OK／0 失败
  命令：python3 -W error::ResourceWarning -m unittest discover -s tests -t .
  SKIP 分两档（测不到不等于通过，也不等于失败）：
    · 直跑那条命令 ⇒ 7 格 SKIP ＝ 2 格真调用未授权 ＋ 5 格「现造 wheel/sdist」组
      （本解释器取不到 `python -m build`，那一组如实写明缺什么）；
    · 带构建件跑 ⇒ 3 格 SKIP（2 真调用 ＋ 1 格等清单生成，本轮清单已生成故转实跑）：
      命令 Q2C_BUILD_PYTHON=<装了 build 的解释器> python3 -W error::ResourceWarning -m unittest discover -s tests -t .
TEETH=45 枚变异全部把对应格打红 ⇒ verdict=TEETH-OK、no_teeth=[]、还原后整包复跑 rc=0
  命令：python3 tools/teeth.py
  原件：evidence/teeth-v011-20261005-001925/teeth-v011b.json
  同批留档：那一次 **TEETH-PROBLEM 之前**的中止跑（baseline 不绿 ⇒ rc=3，没往下打变异）——
    它抓出的正是"两处 git archive 的报告排除还钉着上一版文件名"，
    原件 evidence/teeth-v011-20261005-001925/teeth-44-aborted-baseline-20261005-001925.log
  本轮新增 12 枚：包分发 11 枚 ＋ 归档排除形状 1 枚
分档现读（23 枚判据件；这一列由收集脚本现算并入库，不是手抄：
  `evidence/readings-20261005-002013/per-cell.txt`，脚本只 discover 不执行）：
  protocol 39／ack 35／stores_and_honesty 32／credentials 26／packaging 25／
  real_handoff_evidence 17／qoder_terminal_real_fixture 13／release_manifest 13／
  expiry_cancel_adapter_failure 12／protocol_doc_sync 12／cli 11／idempotency 9／
  trace_readings 8／process_safety 7／recovery 7／retry_delivery 7／vendor_claims 7／
  codex_stream_real_fixture 6／shell_portability 6／synthetic_fanout 5／
  doc_references 3／real_bidirectional 3／release_domain_local_paths 3 ⇒ TOTAL 306
```

## 6. 真实双向交接（继承 v0.1.0 那批原件，本轮未重烧）

```
REAL_HANDOFF_STATUS=BOTH_DIRECTIONS_ACKED（原件来自 run 20261004-171323，见 v0.1.0 报告 §REAL_HANDOFF_STATUS）
本轮未重跑的理由：产品代码路径未变（§0 那两条命令自证），重跑一次要多烧一轮真调用；
代价如实记：这条结论的"新鲜度"停留在 v0.1.0 那一跑，0.1.1 的字节没被真模型调用验过一次。
要闭合：Q2C_LIVE=1 python3 tools/real-handoff.py --workspace /tmp（需主理人点头）
```

## 7. PyPI

```
PYPI_NAME=q2c UNVERIFIABLE-AS-REGISTRATION／FREE-AS-NOT-OCCUPIED
  现跑：python3 tools/pypi-name-check.py ⇒ json_api 与 simple_index 双 404、非标准库同名、退 0
PYPI_PUBLISH=NOT-DONE（不建账号、不要令牌、不碰公开不可逆动作）
PYPI_CREDENTIAL_POLICY=不索取、不记录、不提交任何明文凭证；
  发布通道按 GitHub Trusted Publisher（OIDC）设计 ⇒ 不需要长期 API token 落在这台机器上，
  但需要主理人本人在 PyPI 上建项目并把该 GitHub 仓配成 Trusted Publisher
IF_NAME_REFUSED=STOP，不自改包名，回报主理人裁决
```

## 8. 已知局限（不粉饰）

1. PyPI 未上传 ⇒ `pipx install q2c` 目前是将来式；今天能照抄的是 git 那条。
2. CI 的 `package` job 在托管环境的**首跑**结果决定 §4 那一格；本机 PASS 不顶替它。
3. sdist 的 `.tar.gz` 字节不可复现（三处随造件时刻变）；wheel 钉提交时刻后可复现。身份口径分两条。
4. Qoder International 没跑过；CN 那支的兼容性也只覆盖公开 CLI 档，不碰私有队列库与 sessions JSONL。
5. 无 Windows 支持路径（`fcntl`／进程组语义按 POSIX）；`ps` 读数在非 macOS/Linux 上 UNVERIFIABLE。
6. 真交接样本仍只有一批（一台机、一对账号、一种网络状况）。

## 9. 距离发布还剩硬关口

```
1) 推 main 后托管 CI（ci／clean-machine／package）全绿——红就按普通工程问题定位修，不降 Gate、不盲目 rerun；
2) 建 v0.1.1 tag 与 GitHub Release（附件＝被 CI 验过的那枚字节）；
3) PyPI 身份授权（主理人本人）→ 上传 q2c 0.1.1 → 从公开 PyPI 在全新环境装并跑到 ACKED → 回读 metadata。
```

距离 v0.1.1 发布还剩硬关口：3 个。

Q2C_V0_1_1_BLOCKED
