# q2c v0.1.0 交付与发布说明

更新：2026-10-04 21:0x（钟点取 `date` 现读）。这份文件放在仓里（不放交付目录独有），
因为交付副本是 `rsync --delete` 出来的——只存在于副本里的文件会被同步带走，这个坑我踩过一次。

## 发布状态

| 项 | 值 |
|---|---|
| 公开仓 | https://github.com/6612907-netizen/q2c （PUBLIC，匿名可读已核） |
| release | https://github.com/6612907-netizen/q2c/releases/tag/v0.1.0 |
| tag | `v0.1.0` → tag 对象 `a13f86ae59ad27e3840ea7fcb1f8df58dbd41d61` → 提交 `2213ba68561d8562d666f51b41aa9876f8b2776a` |
| 发布物 | `q2c-v0.1.0-source.tar.gz`，SHA256 `c37374b5868db984604391f4a165edf852cae93ea7fec81f2440b1f5b63afd4c`（71 件） |
| 三处同字节已核 | tag 归档＝release 附件的 API digest＝CI 归档验过的那一枚 |
| 独立复验 | 公开仓 run 37202188662（托管 macOS＋Ubuntu 双 job success）＋ run 37202515727（6 组合 ci 全绿） |
| 内容身份 | 发布物清单头记 `464e518548017a92f8b8975aa7694212b2919e4b`（两张清单见 `evidence/`） |
| 私有侧 | `q2c-staging`（私有，完整历史＋全部证据，未删）；其 Actions 已关，权威 CI 在公开仓 |
| 发布后一轮 | main 上另有"包分发"那几笔（wheel／sdist、pip／pipx、CI 打包门）：**tag 与 Release 附件未动**，也**未上传 PyPI**；读数与身份见 `Q2C-v0.1.0-RELEASE-REPORT.md` 末节「发布后一轮」 |

## 三行自检（装了 Python 3.9+ 的任何一台机器）

```
shasum -a 256 q2c-v0.1.0-source.tar.gz    # 期望 c37374b5868db984…
tar -xzf q2c-v0.1.0-source.tar.gz && cd q2c && python3 -m unittest discover -s tests -t .
sh tools/clean-machine-test.sh            # 期望末行 CLEAN_MACHINE_STATE=ACKED
```

不依赖我方原件的发布物验法（在公开仓跑，tag 只在那里）：

```
git clone https://github.com/6612907-netizen/q2c v && cd v
git archive --format=tar.gz --prefix=q2c/ v0.1.0 -- . ':(exclude)evidence' \
  ':(exclude)Q2C-v0.1.0-RELEASE-REPORT.md' | shasum -a 256
```

## 现在被验到什么程度

- 真实双向交接两个方向各一次，均以 `ACKED` 收口；原件在 `evidence/real-legs/run-20261004-171323/`，
  哪一枚报告算数由 `evidence/real-legs/LATEST.json` 声明，判据每次从盘上重算那 33 枚 SHA256。
- 判据 269 格（unittest，零第三方依赖），独立两台主机各自 `Ran 269 tests` 全绿。
- 27 处"把保护摘掉"的变异全部把对应格打红（`tools/teeth.py`）。
- 提交前的门 `tools/gate-commit.sh`：判据非零或没打出 `OK` ⇒ 退 1（反照做过：塞一格红 ⇒ rc=1）。

## 公开仓与源码包里为什么不放证据

`evidence/` 与发布报告不进包也不进公开仓：那些原件带本机绝对路径、外置卷标、会话号与额度读数，
公开出去泄露的是构建用的那台机器，不是产品。这条边界由
`tests/test_release_domain_local_paths.py`（发布域扫本机坐标）与
`tests/test_release_manifest.py`（包件集＝清单项、报告不在包里）钉住；
README 的"验证证据在哪"一节向读者解释去哪复核。

## 发布之后 main 上还会走，怎么读 CI 颜色

- `python3 tools/make-manifest.py` 今后会正常报 `DRIFTED`：它说的是"清单不再描述分支尖"，
  不是"发布物被改动"。发布物用上面 `git archive v0.1.0` 那条命令验，字节不随 main 变。
- 已发生的一次：私有 staging 在提交 `a14fd5c` 上全矩阵判红——我把"扫描规则要抓的字面量"
  原样写进了报告的脱敏说明里，于是发布域扫描格抓到报告本身。修在 `7efa3e7`，两作业回到 success；
  账与三条"不推翻发布结论"的现读证据在 `evidence/ci-staging-a14fd5c-note.md`。
  同批把 CI 噪声源头关掉（staging Actions `enabled=false`），并把"grep 退 0 不算门"这次自伤
  变成一条退出码门。

## 已知边界（不粉饰）

1. 真实双向交接只跑过这一批（一台机器、一对账号、一种网络状况）；跨机器／跨账号／断网中途重启
   那些形状，桩级判据覆盖的是协议行为不是环境。
2. Codex 腿重启后的证据比早期实现少两档（不读私有队列库与内部 JSONL）：读成 `UNKNOWN` ⇒ 停手等人。
3. Qoder 公开 CLI 是同步的：投递与开始之间没有可观测边界，九问里那一问在该腿上永远 `NOT_OBSERVED`。
4. 无 Windows 支持路径；桥不拥有、不读取、不写入任何凭据（见 `SECURITY.md`）。
