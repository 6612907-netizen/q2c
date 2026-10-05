#!/usr/bin/env python3
"""判据的牙：把产品代码改坏，对应的判据格必须变红。

为什么要有这个工装（在册教训：**格数不等于牙数**）：
一格断言如果摘掉保护后照样全绿，它等于没测。旧内核里出过三次——
判据写在造反例之前、期望名与标签各写各的、变异打的对象不对。

用法：
    python3 tools/teeth.py            # 跑全部（每枚单独还原）
    python3 tools/teeth.py --only ack-no-session

纪律（写死在代码里，不是注释）：
  · 开工前工作树必须干净（已跟踪文件零改动），否则 ABORT——变异不许污染未提交实现；
  · 还原只认备份件，不用 `git checkout --`；
  · 还原后同校三条：变异串 0 命中、文件与原件逐字相同、基线判据复跑仍绿；
  · 打不红的枚子记 NO-TEETH 并非零退出，不许悄悄跳过去。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")

#: (id, 文件, 原文, 改成, 期望变红的那一格, 这一枚在测什么)
MUTATIONS = [
    ("ack-no-session-gate", "q2c/ack.py",
     '''    if str(r.reported_session_id or "") != str(target_session_id or ""):''',
     '''    if False:''',
     "tests.test_ack.TestSixGates.test_05_session_identity_must_match",
     "拿别人的会话号顶替『成功』必须被拒"),
    ("ack-ackline-from-stdout", "q2c/ack.py",
     '''        ack_line_present=has_ack_line(inner),
        bind_echo=extract_bind_echo(inner),''',
     '''        ack_line_present=has_ack_line(stdout) or has_ack_line(inner),
        bind_echo=extract_bind_echo(stdout) or extract_bind_echo(inner),''',
     "tests.test_ack.TestParsingIsNotSubstring.test_13_bind_outside_json_does_not_count",
     "把绑定行写在 JSON 之外（外面补一行）不许过关（R17 第 4 条）"),
    ("retry-may-rerun-receiver", "q2c/transport.py",
     '''            if chosen == "request" and pending:''',
     '''            if False and chosen == "request" and pending:''',
     "tests.test_retry_delivery.TestRetryIsDeliveryOnly.test_03_explicit_request_leg_is_redirected_when_result_on_disk",
     "结果已在案时指着请求腿重投＝重跑对侧任务（协议 §5 第 3 条）"),
    ("fail-ignores-budget", "q2c/transport.py",
     '''        if park_now or d["attempts"] >= d["max"]:''',
     '''        if park_now and False:''',
     "tests.test_retry_delivery.TestRetryIsDeliveryOnly.test_05_budget_exhaustion_stops_and_notifies",
     "投递预算用尽必须停手等人，不许无限自投"),
    ("recover-unknown-as-absent", "q2c/transport.py",
     '''                if ps == "unknown":''',
     '''                if False:''',
     "tests.test_recovery.TestRestartRecovery.test_03_unknown_is_not_absent",
     "未知 ≠ 确认没跑：读不出来时不许自动重投（在册阻断级缺陷）"),
    ("ledger-blind-overwrite", "q2c/ledger.py",
     '''    else:
        merged = merge_ledger(db.base, disk, mine, notes)''',
     '''    else:
        merged = mine''',
     "tests.test_stores_and_honesty.TestLedger.test_02_stale_write_is_loud_not_silent",
     "第三者绕锁直写必须被合并保住并响，不许整块盖掉"),
    ("trace-allows-project-fields", "q2c/trace.py",
     '''        bad = [k for k in event if str(k).lower() in FORBIDDEN_EVENT_KEYS]''',
     '''        bad = []''',
     "tests.test_stores_and_honesty.TestTrace.test_24_forbidden_project_fields_are_refused",
     "跟踪是通信真相：项目真相字段名必须拒写"),
    ("no-redaction", "q2c/security.py",
     '''    for pat, rep in SECRET_PATTERNS:
        s = pat.sub(rep, s)''',
     '''    pass''',
     "tests.test_stores_and_honesty.TestTrace.test_25_redaction_before_write",
     "写盘前必须脱敏（事件流是最容易沾凭据的位置）"),
    ("dotdot-checked-after-resolve", "q2c/security.py",
     '''    raw_parts = os.path.expanduser(value).split(os.path.sep)
    if ".." in raw_parts:''',
     '''    raw_parts = []
    if False:''',
     "tests.test_credentials.TestPathAndWorkspace.test_15_dotdot_rejected",
     "`..` 必须在 realpath 之前查，否则永远查不到"),
    ("protocol-terminal-mutable", "q2c/protocol.py",
     '''    if is_terminal(current):
        raise ProtocolError("STALE_EVENT", "终态 %s 不可再迁移（迟到事件只进 trace）" % current)''',
     '''    if False:''',
     "tests.test_protocol.TestStateMachine.test_46_terminal_states_immutable",
     "终态不可翻：迟到事件只进跟踪，不改状态"),
    ("pump-ignores-pending-result", "q2c/transport.py",
     '''        if self._result_pending(rec):
            return self._deliver_result(db, rec)''',
     '''        if False:
            return self._deliver_result(db, rec)''',
     "tests.test_synthetic_fanout.TestSyntheticFanout.test_03_everything_reaches_a_terminal_state",
     "结果腿失败落到 DELIVERY_RETRY 后按状态会被误投请求腿＝重跑对侧"),
    ("config-tolerates-secrets", "q2c/config.py",
     '''    hits = []
    for k in doc:''',
     '''    hits = []
    for k in []:''',
     "tests.test_credentials.TestDispatchGate.test_09_config_refuses_to_hold_secrets",
     "配置里放凭据 ⇒ 整份不认（不是忽略那一项）"),
    ("credential-gate-always-open", "q2c/adapters/base.py",
     '''        kind = caps.get("credential_kind") or self.name''',
     '''        return True, "muted"
        kind = caps.get("credential_kind") or self.name''',
     "tests.test_credentials.TestDispatchGate.test_07_credential_unavailable_blocks_without_a_single_call",
     "认证不过 ⇒ 一次 CLI 都不叫（在册：不许照发、等超时才发现是认证问题）"),
    ("state-files-not-private", "q2c/security.py",
     '''    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)''',
     '''    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)''',
     "tests.test_credentials.TestDispatchGate.test_08b_state_files_are_private",
     "用户数据件必须 0600（载荷逐字落盘是本分，权限是唯一能做的物理决定）"),
    ("inflight-read-as-failure", "q2c/transport.py",
     '''            if r.detail == "IN_FLIGHT" or ps in ("alive", "unknown"):''',
     '''            if False:''',
     "tests.test_recovery.TestRestartRecovery.test_03b_inflight_child_not_counted_as_failure",
     "孩子还在飞时被判失败⇒下一拍重新入队＋再 exec resume，同一请求被叫醒两次（真腿实测缺陷）"),
    ("body-shape-turned-back-into-gate", "q2c/transport.py",
     '''        ok_shape, shape_why = ack.judge_result_shape(r.body or "")
        rec["result_body_only_protocol"] = (not ok_shape)''',
     '''        ok_shape, shape_why = ack.judge_result_shape(r.body or "")
        rec["result_body_only_protocol"] = (not ok_shape)
        if not ok_shape:
            return self._on_failure(db, rec, "request", "RESULT_BODY_EMPTY", shape_why)''',
     "tests.test_ack.TestBodySubstanceIsObservation.test_35_observation_reaches_the_readings",
     "把正文形状变回闸门＝桥又在判答复质量（§5 禁止的那条边界）"),
    ("idempotency-not-suppressed", "q2c/transport.py",
     '''            if rec.get("idempotency_key") == envelope.idempotency_key:''',
     '''            if False:''',
     "tests.test_idempotency.TestIdempotency.test_02_same_key_second_time_suppressed",
     "同一幂等键只投一次（重复投不许多造一笔）"),
    # ---- 2026-10-04 那两处（真腿＋独立 CI 各抓一枚）对应的牙 ----
    ("ack-terminal-arg-ignored", "q2c/ack.py",
     '''        terminal=(TERMINAL_NOT_PROVIDED if stream_terminal is None else stream_terminal),''',
     '''''',
     "tests.test_qoder_terminal_real_fixture.TestQoderTerminalFromRealArtifact."
     "test_10_stream_terminal_is_not_silently_dropped",
     "参数收下却不接进回执＝声明了终态证据的适配器永远读成『未提供』（真腿第三跑实测）"),
    ("qoder-read-with-codex-vocabulary", "q2c/adapters/qoder.py",
     '''        r = ack.receipt_from_cli_json(raw, use_rc,
                                      stream_terminal=ack.scan_result_frame_terminal(raw))''',
     '''        r = ack.receipt_from_cli_json(raw, use_rc,
                                      stream_terminal=ack.scan_terminal(raw))''',
     "tests.test_qoder_terminal_real_fixture.TestQoderTerminalFromRealArtifact."
     "test_02_adapter_collects_real_capture_as_completed",
     "拿别家的终态词表读这一侧的收口帧 ⇒ 合格答复被拒（今天那一刀的本体）"),
    ("result-frame-absent-reads-completed", "q2c/ack.py",
     '''    rec = last_structured_record(raw, want_type)
    if rec is None:
        return TERMINAL_ABSENT''',
     '''    rec = last_structured_record(raw, want_type)
    if rec is None:
        return TERMINAL_COMPLETED''',
     "tests.test_qoder_terminal_real_fixture.TestQoderTerminalFromRealArtifact."
     "test_06_missing_result_frame_is_absent",
     "没有收口帧也说跑完了＝把『没证据』折成『有证据』（禁的那条口径）"),
    ("result-frame-error-reads-completed", "q2c/ack.py",
     '''    if record_self_reports_success(rec):
        return TERMINAL_COMPLETED
    if rec.get("is_error") is True or str(rec.get("subtype") or "").startswith("error"):
        return TERMINAL_FAILED''',
     '''    return TERMINAL_COMPLETED
    if rec.get("is_error") is True or str(rec.get("subtype") or "").startswith("error"):
        return TERMINAL_FAILED''',
     "tests.test_qoder_terminal_real_fixture.TestQoderTerminalFromRealArtifact."
     "test_05_error_frame_reads_as_failed_not_absent",
     "对方自报失败也被读成跑完 ⇒ 失败轮被采信为送达"),
    ("shell-var-next-to-non-ascii", "tools/clean-machine-test.sh",
     '''mark "q2c doctor" "rc=${DRC}；''',
     '''mark "q2c doctor" "rc=$DRC；''',
     "tests.test_shell_portability.TestShellPortability."
     "test_04_variable_adjacent_to_non_ascii_is_braced",
     "2026-10-04 macOS 托管 runner 红在这里（变量名吞掉全角分号 ⇒ set -u 未绑变量）"),
    ("shell-logic-operator-at-line-start", "tools/ci-clean-machine.sh",
     '''echo "PIP_INSTALL=$PIP_STATE" | tee -a "$OUT/pip-install.log"''',
     '''echo "PIP_INSTALL=$PIP_STATE" | tee -a "$OUT/pip-install.log"
  && echo 复现那枚解析期错''',
     "tests.test_shell_portability.TestShellPortability."
     "test_01_posix_sh_can_parse_every_script",
     "行首 `&&` 在 POSIX sh 是语法错；两枚 OS 都拒（本机能跑绿不算异机证据）"),
    ("trace-state-now-by-event-name", "q2c/trace.py",
     '''    for e in reversed(evs):
        if e.get("to_state"):
            return str(e["to_state"])''',
     '''    for e in reversed(evs):
        if e.get("ev") == "state" and e.get("to_state"):
            return str(e["to_state"])''',
     "tests.test_trace_readings.TestStateNowReadsObservedMigrations."
     "test_01_named_migration_events_answer_state_now",
     "只认 `ev==\"state\"` 会让每条真记录都报 UNVERIFIABLE＝时间线看得见却声称证明不了"),
    ("trace-retries-manual-only", "q2c/trace.py",
     '''        hit = (e.get("retry_of") is not None
               or str(e.get("to_state") or "") == protocol.ST_DELIVERY_RETRY
               or "retry" in str(e.get("ev") or ""))''',
     '''        hit = (e.get("retry_of") is not None)''',
     "tests.test_trace_readings.TestStateNowReadsObservedMigrations."
     "test_04_retries_seen_in_the_real_legs_are_reported",
     "结果腿的补送达被漏读＝把已发生的重投藏起来（第 7 问存在的意义就没了）"),
    ("manifest-release-scope-not-excluded", "tools/make-manifest.py",
     '''        if scope == "release" and is_evidence:
            continue''',
     '''        if False and scope == "release" and is_evidence:
            continue''',
     "tests.test_release_manifest.TestManifestScopeRules.test_01_release_scope_excludes_evidence",
     "发布域清单混进证据件＝读数文件一变整张清单就自判红（今天真撞过一次 diverged=1）"),
    ("manifest-lists-itself", "tools/make-manifest.py",
     '''        if f in skip:
            continue''',
     '''        if False and f in skip:
            continue''',
     "tests.test_release_manifest.TestManifestScopeRules.test_03_two_scopes_partition_the_tree",
     "清单把自身当发布物⇒哈希永远对不上；两张互不划清就会有一张是半张"),
    # ---- 包分发那一组（2026-10-04 加，任务书第 2/4/6/7 条）-----------------
    ("pkg-version-written-twice", "pyproject.toml",
     '''dynamic = ["version"]''',
     '''version = "0.1.0"
dynamic = ["version"]''',
     "tests.test_packaging.Test01_版本一个真源.test_pyproject_does_not_write_its_own_version",
     "pyproject 自己再写一遍版本⇒与 q2c/_version.py 成双真源，包上标的和代码里跑的会分家"),
    ("pkg-url-relative-path", "pyproject.toml",
     '''Protocol = "https://github.com/6612907-netizen/q2c/blob/main/PROTOCOL.md"''',
     '''Protocol = "PROTOCOL.md"''',
     "tests.test_packaging.Test02_发货形状.test_every_project_url_is_a_real_url",
     "包页面那行「链接」写成仓内相对路径⇒用户点不开（这就是发布当天被发现的原状）"),
    ("pkg-license-deprecated-table", "pyproject.toml",
     '''license = "MIT"''',
     '''license = {text = "MIT"}''',
     "tests.test_packaging.Test02_发货形状.test_spdx_license_declared_and_floor_backs_it",
     "旧表写法：构建期告警点名 2027-02-18 起不再支持，那一天 pip install 会直接失败"),
    ("pkg-packages-list-stale", "pyproject.toml",
     '''packages = ["q2c", "q2c.adapters"]''',
     '''packages = ["q2c"]''',
     "tests.test_packaging.Test02_发货形状.test_declared_packages_equal_packages_on_disk",
     "声明的包少了 adapters⇒wheel 里没适配器，装上的人一跑就 ImportError"),
    ("pkg-sdist-drops-test-package-init", "MANIFEST.in",
     '''recursive-include tests *.py''',
     '''recursive-include tests test_*.py''',
     "tests.test_packaging.Test03_sdist_收录规则.test_suite_support_files_are_shipped",
     "源码包漏掉 tests/__init__.py 与 helpers.py⇒别人解包后跑不了我们让他跑的判据"),
    ("pkg-quickstart-ghost-verb", "README.md",
     '''q2c list                                # 状态分布：这应该只有 1 笔，且落在 ACKED''',
     '''q2c status                                # 状态分布：这应该只有 1 笔，且落在 ACKED''',
     "tests.test_packaging.Test05_装完就能抄的_quick_start.test_verbs_used_in_quickstart_exist_in_cli",
     "Quick Start 写了 CLI 上不存在的动词⇒新用户照抄第一条就失败（文档与代码分家）"),
    ("pkg-ci-job-not-calling-the-gate", ".github/workflows/package.yml",
     '''          sh tools/pkg-install-test.sh''',
     '''          echo "打包门本轮没跑"''',
     "tests.test_packaging.Test06_打包门.test_ci_has_a_packaging_job_calling_the_script",
     "CI 那道 job 不再叫本体的脚本⇒它变成一枚绿灯装饰，装不上也照样绿"),
    ("pkg-gate-folds-unverified-into-pass", "tools/pkg-install-test.sh",
     '''    say "PKG_INSTALL_TEST=UNVERIFIED"
    exit 2''',
     '''    say "PKG_INSTALL_TEST=UNVERIFIED"
    exit 0''',
     "tests.test_packaging.Test06_打包门.test_script_never_folds_a_missing_tool_into_pass",
     "把「测不了」退 0⇒缺 pipx／缺构建件的机器上报成通过，这道门自己就成了假绿源"),
    ("pkg-name-tool-reads-nothing-as-free", "tools/pypi-name-check.py",
     '''        print("PYPI_LIMIT=网络／代理不可达时只到此档，绝不折成 FREE")
        return 2''',
     '''        print("PYPI_LIMIT=网络／代理不可达时只到此档，绝不折成 FREE")
        return 0''',
     "tests.test_packaging.Test06_打包门.test_name_tool_verdicts_are_three_way",
     "取不到 PyPI 时退 0＝把「没查到」写成「这名字可用」，改名与发布决策会据此做错"),
    ("pkg-build-timestamp-from-now", "tools/pkg-install-test.sh",
     '''EPOCH=$(git -C "$here" log -1 --format=%ct 2>/dev/null || true)''',
     '''EPOCH=$(date +%s)''',
     "tests.test_packaging.Test06_打包门.test_build_is_pinned_to_the_commit_clock",
     "造件时间戳取现在⇒同一枚提交两次造出来的件哈希不一样，那枚哈希不能当发布物身份"),
    ("pkg-name-tool-no-normalize", "tools/pypi-name-check.py",
     '''    return re.sub(r"[-_.]+", "-", name).lower()''',
     '''    return name.lower()''',
     "tests.test_packaging.Test06_打包门.test_pypi_name_tool_is_fail_closed",
     "不做 PEP 503 归一⇒Q_2.C 与 q2c 会被读成两个名字，占用判定会漏"),
    # ---- 版本口径与厂商边界（2026-10-05 那半轮）--------------------------
    ("manifest-report-rule-pinned-to-one-version", "tools/make-manifest.py",
     '''REPORT_RE = re.compile(r"^Q2C-v\\d+\\.\\d+\\.\\d+-RELEASE-REPORT\\.md$")''',
     '''REPORT_RE = re.compile(r"^Q2C-v0\\.1\\.0-RELEASE-REPORT\\.md$")''',
     "tests.test_release_manifest.TestVersionAwareNames.test_report_rule_is_a_shape_not_one_filename",
     "报告排除规则只认 0.1.0 那一枚文件名⇒下一版报告被当发布物收进包里，结论一行改动发布物字节"),
    ("manifest-path-hardcoded-version", "tools/make-manifest.py",
     '''RELEASE_OUT = os.path.join(ROOT, "evidence", "SHA256SUMS-v%s.txt" % VERSION)''',
     '''RELEASE_OUT = os.path.join(ROOT, "evidence", "SHA256SUMS-v0.1.0.txt")''',
     "tests.test_release_manifest.TestVersionAwareNames.test_manifest_paths_follow_the_package_version",
     "清单文件名写死版本⇒升一次版就把新一轮清单续写进旧那一页，两份身份混在同一张纸上"),
    ("pkg-forgets-new-root-doc", "MANIFEST.in",
     '''include DELIVERY-v0.1.0.md DELIVERY-v0.1.1.md''',
     '''include DELIVERY-v0.1.0.md''',
     "tests.test_packaging.Test03_sdist_收录规则.test_every_root_doc_is_declared_for_the_sdist",
     "新增的根级交付页没进收录名单⇒装包的人少一页说明书，而没有任何东西会红"),
    ("vendor-international-written-as-verified", "README.md",
     '''QODER_INTERNATIONAL_VERIFIED=NOT_VERIFIED （没跑过，不在支持声明里；照抄本节命令不算已证）''',
     '''QODER_INTERNATIONAL_VERIFIED=YES （另一支也已验证，命令可直接照抄）''',
     "tests.test_vendor_claims.Test02_PyPI页面上看得见边界.test_pypi_surface_never_claims_international_verified",
     "把没跑过的那一支写成已验证＝用户在另一支上照抄命令然后失败（主理人明令禁的一条）"),
    ("vendor-cn-verdict-downgraded", "README.md",
     '''QODER_CN_VERIFIED=YES （Qoder CN 的公开 CLI `qoderclicn`''',
     '''QODER_CN_VERIFIED=NO （Qoder CN 的公开 CLI `qoderclicn`''',
     "tests.test_vendor_claims.Test02_PyPI页面上看得见边界.test_readme_carries_the_four_machine_readable_lines",
     "已验证那一档的取值被改＝边界话术漂了（四行既要存在也得是真话）"),
    ("vendor-adapter-bin-drift", "q2c/adapters/qoder.py",
     '''BIN = "qoderclicn"''',
     '''BIN = "qoder"''',
     "tests.test_vendor_claims.Test01_代码与文档说的同一枚CLI.test_adapter_bin_is_the_cn_cli",
     "适配器换了目标 CLI 而文档还在说另一枚⇒读者按文档的命令抄，第一条就起不来"),
    ("ci-archive-exclusion-pinned-to-one-report", "tools/ci-clean-machine.sh",
     """:(exclude,glob)Q2C-v*-RELEASE-REPORT.md'""",
     """:(exclude)Q2C-v0.1.0-RELEASE-REPORT.md'""",
     "tests.test_release_manifest.TestManifestScopeRules.test_packaging_exclusions_are_not_pinned_to_one_report_name",
     "归档命令里把报告排除写死成一枚文件名⇒包比清单多一件（0.1.1 这次就是这么红的）"),
    ("vendor-block-missing-from-release-report", "Q2C-v0.1.1-RELEASE-REPORT.md",
     '''QODER_CN_VERIFIED=YES                     # 真跑过：与 Codex 双向各一次，两腿都 ACKED''',
     '''厂商边界见 README（这里不写）              # 故意不留那一档''',
     "tests.test_vendor_claims.Test04_发布记录里那一档也在.test_release_report_has_the_vendor_verdict_block",
     "发布记录里没写验到哪一枚 CLI⇒读发布记录的人以为整条产品线都验过了"),
    # ---- PyPI 发布作业的前置闸（2026-10-05 那两次拒发教我的两件事）------
    ("publish-gate-no-actions-read", ".github/workflows/publish.yml",
     """  actions: read          # 前置闸要读别的作业的结论；没这一格就是 403（2026-10-05 现撞到）\n""",
     """"""
     ,
     "tests.test_packaging.Test07_PyPI发布作业.test_precheck_has_read_permission_for_the_query",
     "前置闸没权限读结论⇒它只能报\"取不到\"却让人以为 CI 不绿，白查一轮"),
    ("publish-gate-wrong-endpoint", ".github/workflows/publish.yml",
     """actions/runs?head_sha=""",
     """commits/…/check-suites?head_sha=""",
     "tests.test_packaging.Test07_PyPI发布作业.test_precheck_reads_a_source_that_actually_carries_names",
     "换成不带工作流名的那个接口⇒三道绿作业被读成没有结论，发布被无故拒掉"),
    ("publish-gate-swallows-api-error", ".github/workflows/publish.yml",
     '''>"$RUNS_FILE" 2>"$ERR"''',
     '''>"$RUNS_FILE" 2>/dev/null''',
     "tests.test_packaging.Test07_PyPI发布作业.test_failure_reason_is_not_swallowed",
     "把 API 错误咽掉＝拒发时没人知道为什么，下一轮还得从头查（在册老坑：静默的失败最难查）"),
    ("publish-readback-uses-dead-top-level-version", ".github/workflows/publish.yml",
     '''print("pypi_version=%s" % (info.get("version") or ""))''',
     '''print("pypi_version=%s" % d.get("version", ""))''',
     "tests.test_packaging.Test07_PyPI发布作业.test_readback_uses_the_field_pypi_actually_fills",
     "回读取一个 PyPI 早就不填的顶层键⇒上传成功也被报成失败（0.1.1 那次就这么红的）"),
    # ---- CI 读数工装本身（2026-10-05 09:0x：它曾把两枚 job 折成同一页假读数）----
    ("ci-readings-os-filter-decorative", "tools/ci-readings.py",
     '''        keep = [ln for ln in lines if os_name.strip().lower() in _job_of(ln).lower()]''',
     '''        keep = lines''',
     "tests.test_ci_readings.Test01_按runner取数.test_two_jobs_in_one_log_do_not_collapse_into_one_page",
     "把 --os 的过滤摘掉＝两枚 job 的读数撞成同一页，报告里那句\"双 OS 各自通过\"当场变假话"),
    ("ci-readings-ambiguous-accepted", "tools/ci-readings.py",
     '''    elif len(jobs) > 1:''',
     '''    elif False:''',
     "tests.test_ci_readings.Test01_按runner取数.test_multi_job_log_without_os_is_refused",
     "合流日志不指明朝哪枚 job 取数时，工具必须拒（退 2）而不是随手挑一条当整体"),
    ("ci-readings-package-name-pinned-to-010", "tools/ci-readings.py",
     '''    ("package_sha_line", r"^([0-9a-f]{64}\\s+q2c-v\\d+\\.\\d+\\.\\d+-source\\.tar\\.gz)"),''',
     '''    ("package_sha_line", r"^([0-9a-f]{64})\\s+q2c-v0\\.1\\.0-source\\.tar\\.gz"),''',
     "tests.test_ci_readings.Test02_取数键不许钉版本号.test_package_sha_line_follows_the_version_being_released",
     "取数键写死上一版文件名⇒0.1.1 的包 SHA 读成 MISSING，而 MISSING 会被抄成\"那一格没做\""),
]


def sh(cmd, timeout=900):
    # 注意：不许在 shell 里接 `| tail`——那样退出码是 tail 的 0，
    # 十二枚变异会全部读成"判据没红"（= 工装把自己变成假绿的来源）。
    p = subprocess.run(cmd, shell=True, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return p.returncode, p.stdout


def dirty_tracked():
    rc, txt = sh("git status --porcelain")
    return [l for l in txt.splitlines() if l and not l.startswith("??")]


def run_cell(cell):
    p = subprocess.run([sys.executable, "-m", "unittest", cell], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
    return p.returncode, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=os.path.join(EVID, "teeth-%s.json" % time.strftime("%Y%m%d-%H%M%S")))
    a = ap.parse_args()
    os.makedirs(EVID, exist_ok=True)

    dirty = dirty_tracked()
    if dirty:
        print("ABORT：工作树不干净，不许在改动之上做变异：%s" % dirty)
        return 3
    rc, base = run_cell("tests.test_protocol")
    p_all = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=1800)
    rc_all, out_all = p_all.returncode, p_all.stdout
    if rc_all != 0:
        print("ABORT：基线判据不绿，先修基线再验牙\n%s" % out_all)
        return 3

    results, no_teeth = [], []
    for mid, rel, old, new, cell, why in MUTATIONS:
        if a.only and a.only != mid:
            continue
        path = os.path.join(ROOT, rel)
        original = open(path, encoding="utf-8").read()
        if old not in original:
            results.append({"id": mid, "status": "ANCHOR-MISS", "detail": "锚点串不在文件里"})
            no_teeth.append(mid)
            continue
        backup = path + ".teeth-backup"
        shutil.copy2(path, backup)
        abort = None
        try:
            open(path, "w", encoding="utf-8").write(original.replace(old, new, 1))
            rc, txt = run_cell(cell)
            red = rc != 0
            results.append({"id": mid, "measures": why, "cell": cell, "cell_rc": rc,
                            "went_red": red, "tail": txt.strip().splitlines()[-1] if txt.strip() else ""})
            if not red:
                no_teeth.append(mid)
        finally:
            shutil.copy2(backup, path)
            os.remove(backup)
            after = open(path, encoding="utf-8").read()
            if after != original:
                abort = "还原后与原件逐字不一致（%s）" % mid
        if abort:
            # 还原校验只看一件事：**逐字回到原件**。
            # 早期版本还额外 grep 一次"变异串还在不在"，那是多余的弱判据：
            # 被改坏的那一行原文往往在文件里别处也存在，命中≠残留。
            print("ABORT：%s" % abort)
            return 4

    p_back = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=1800)
    rc_back, out_back = p_back.returncode, p_back.stdout
    dirty_after = dirty_tracked()
    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "baseline": {"rc": rc_all, "line": out_all.strip().splitlines()[-1] if out_all.strip() else "",
                     "note": "基线是整包判据，不是单格"},
        "mutations": results,
        "after_restore": {"rc": rc_back, "line": out_back.strip().splitlines()[-1:],
                          "tracked_dirty": dirty_after},
        "no_teeth": no_teeth,
        "verdict": "TEETH-OK" if (rc_back == 0 and not no_teeth and not dirty_after) else "TEETH-PROBLEM",
        "run_cell_note": "每枚只跑它自己那一格（快）；整包基线在开头与结尾各跑一次（真）",
        "base_cell_ignored": base.strip().splitlines()[-1],
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"counted": len(results), "no_teeth": no_teeth,
                      "verdict": report["verdict"], "baseline": report["baseline"],
                      "after": report["after_restore"]["line"]}, ensure_ascii=False, indent=1))
    print("原件：%s" % a.out)
    return 0 if report["verdict"] == "TEETH-OK" else 5


if __name__ == "__main__":
    sys.exit(main())
