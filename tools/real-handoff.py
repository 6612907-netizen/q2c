#!/usr/bin/env python3
"""两条真实双向交接的跑批件（任务书 §14 第 1、2 项；主理人 2026-10-04 16:0x 授权真调用）。

它做四件事，且**不替代**任何一步：
  1. 造一个专用状态根（在 `evidence/real-legs/<run_id>/` 下，全程留档）；
  2. 登记两条**已存在**的 provider 会话（不猜号、不新建——公开 CLI 无零副作用新建入口，
     线程号与会话号由 `--codex-thread`／`--qoder-session` 显式给出）；
  3. 跑 Qoder→Codex 与 Codex→Qoder 各一次完整交接（投递→唤醒→收答复→结果送回→确认）；
  4. 把台账、跟踪九问、每一腿的捕获件与退出码侧件、原始 CLI 事件流全部留在 run 目录里，
     并印一段机器可读的摘要（`REAL_HANDOFF_*`）。

口径：失败就报失败并保留现场。这条脚本**不许**为了变绿去放宽六道闸或伪造回执——
真要修的是适配器或文案，改完重跑，读数重生成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from q2c import ack, adapters, config as config_mod, ledger, protocol, security, transport  # noqa: E402
from q2c.adapters import _spawn  # noqa: E402

#: Setup 调用要说什么。刻意**不含**任何绑定行或确认行——它不是交接，
#: 唯一目的是让公开 CLI 新建一条空会话并把号吐出来（两家都没有零副作用的建会话入口）。
SETUP_PROMPT = "初始化检查：只回一个字「好」，不要执行任何命令、不要读写任何文件。"


def _env(envelope):
    return os.environ.get("Q2C_LIVE", "").strip()


def _setup_conversation(kind, workspace, capdir, label):
    """起一次 Setup 调用，拿一条**全新**的对侧会话号。返回 (会话号, 原始件路径, rc)。

    为什么跑批件要做这件事（真腿第三跑现场）：复用同一条 Codex 线程时，上一跑留在
    队列里的那条消息先被回答 ⇒ 回的是**上一轮**的 `req-…` 与 `Q2C-BIND: …`。
    归属闸拒得对（原件 `evidence/real-legs/05-codex-stale-bind-stream.jsonl`，
    反例格 `tests/test_qoder_terminal_real_fixture.py::TestStaleAttributionFromRealLegs`），
    但真跑不该把"被自己的闸拒掉"当常态 —— 所以每次真跑各用一条新会话。

    这里**不走产品适配器**（`start()` 两家都明写不支持：没有零模型调用的建会话入口），
    走的是同一条安全起子进程的路（`_spawn.spawn` ＋剥环境变量），原始 stdout 全留档。
    """
    os.makedirs(capdir, exist_ok=True)
    cap = os.path.join(capdir, "%s.out" % label)
    env = security.strip_runtime_env(dict(os.environ))
    if kind == "codex":
        argv = ["codex", "exec", "--json", "-c", 'sandbox_mode="read-only"',
                "--skip-git-repo-check", SETUP_PROMPT]
    elif kind == "qoder":
        argv = [os.environ.get("Q2C_QODER_CMD", "qoderclicn"), "-p",
                "--permission-mode", "auto", "--output-format", "json", SETUP_PROMPT]
        if workspace:
            argv += ["-w", workspace]
    else:
        raise ValueError("SETUP_KIND_UNKNOWN=%r（只认 codex／qoder）" % kind)
    found = argv[0] if os.path.isabs(argv[0]) else security.resolve_executable(argv[0], workspace)
    argv[0] = found
    phase, rc, _pid, _ps = _spawn.spawn(argv, env, cap, 240.0, cwd=workspace or None)
    if phase == _spawn.IN_FLIGHT:
        raise RuntimeError("SETUP_IN_FLIGHT=%s（这一条不重发，人工看现场：%s）" % (label, cap))
    raw = ""
    with open(cap, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    if rc != 0:
        raise RuntimeError("SETUP_FAILED=%s rc=%s 见 %s" % (label, rc, cap))
    if kind == "codex":
        sid = ""
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln.startswith("{"):
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("type") == "thread.started" and d.get("thread_id"):
                sid = str(d["thread_id"])
        if not sid:
            raise RuntimeError("SETUP_NO_THREAD_ID=%s（原始件 %s）" % (label, cap))
    else:
        rec = ack.last_structured_record(raw, "result")
        sid = str((rec or {}).get("session_id") or "")
        if not sid:
            raise RuntimeError("SETUP_NO_SESSION_ID=%s（原始件 %s）" % (label, cap))
        if not ack.record_self_reports_success(rec):
            raise RuntimeError("SETUP_SELF_REPORTED_FAILURE=%s（原始件 %s）" % (label, cap))
    return sid, cap, rc


def _artifact_manifest(home):
    """把这一跑的**原件**逐枚登记 SHA256（锁件除外）。

    为什么要它：主理人要的是"原始证据留着"，不是"我转述过"。登记之后，
    判据件 `test_real_handoff_evidence.py` 每次跑判据都从盘上重算一遍比对 ——
    事后改一个字节就会被自己的判据抓住，比"放在 evidence/ 里没人看"强。
    """
    rows = []
    for d, _dirs, files in os.walk(home):
        for fn in sorted(files):
            if fn.endswith(".lock") or fn.endswith(".rev"):
                continue
            p = os.path.join(d, fn)
            try:
                with open(p, "rb") as fh:
                    b = fh.read()
            except OSError:
                continue
            rows.append({"path": os.path.relpath(p, HERE).replace(os.sep, "/"),
                         "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()})
    rows.sort(key=lambda r: r["path"])
    return rows


def _leg_summary(t, rid, direction=""):
    """从台账＋跟踪里现读一枚方向的摘要（真跑与事后重建共用这一份，不另立口径）。"""
    rec = t.read(rid)["record"]
    q = t.trace.nine_questions(rid)
    res = dict(rec.get("result") or {})
    if res.get("file"):
        # 产品写的是绝对路径；报告里必须另存**仓内相对路径**，否则异机解包后
        # 判据对着一个不存在的 `<外部卷>/…` 说"证据丢了"。
        res["file_rel"] = os.path.relpath(res["file"], HERE).replace(os.sep, "/")
    summary = {
        "direction": direction,
        "request_id": rid,
        "state": rec["state"],
        "idempotency_key": rec["idempotency_key"],
        "correlation_id": rec["correlation_id"],
        "message_id": rec["envelope"]["message_id"],
        "nonce": rec["nonce"],
        "request_sha256": rec.get("request_sha256", ""),
        "delivery": rec["delivery"],
        "result": res,
        "last_receipt": rec.get("last_receipt", {}),
        "handles": rec.get("handles", {}),
        "result_body_only_protocol": rec.get("result_body_only_protocol"),
        "trace_nine": q,
    }
    return summary


def _direction_from(t, rid, rec):
    """按**逻辑会话绑的适配器**定方向，不靠文案里那句"方向：…"。"""
    roles = {}
    for item in t.sessions.list_all():
        roles[item.session_id] = item.adapter or "?"
    snd = roles.get(rec["envelope"]["sender"], "?")
    rcv = roles.get(rec["envelope"]["receiver"], "?")
    return "%s-to-%s" % (snd, rcv)


def rebuild_from_run(run_dir):
    """事后从**已在盘的原件**重建报告（零新调用、不新花一分钱、不改任何原件）。

    为什么要有这条路：报告是派生件，跑批件改字段时不该逼人再烧一次真调用。
    口径写死在这里——不许造任何读数，只从 `state/ledger.json`、`trace/events.jsonl`、
    `results/`、`state/inflight/` 现读；输出的 JSON 里带 `rebuilt` 与源报告指纹，
    谁看都知道这枚是哪一枚的复分。
    """
    run_id = os.path.basename(run_dir).replace("run-", "")
    cfgp = os.path.join(run_dir, "config.json")
    if not os.path.isfile(cfgp):
        print("拒绝重建：%s 里没有 config.json（这不是 q2c 的跑批根）" % run_dir)
        return 2
    adapters.import_builtin()
    t = transport.Transport(run_dir)
    recs = ledger.load(ledger.LedgerPaths(run_dir), read_only=True)
    legs = []
    for rid in sorted(recs, key=lambda k: str(recs[k].get("created_at") or k)):
        rec = recs[rid]
        if not isinstance(rec, dict):
            continue
        legs.append(_leg_summary(t, rid, _direction_from(t, rid, rec)))
    report = {"run_id": run_id, "home": run_dir, "rebuilt": {
                  "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "no_new_model_calls": True,
                  "from": "state/ledger.json + trace/events.jsonl + results/ + state/inflight/"},
              "setup": _read_setup(run_dir),
              "ledger_states": {rid: recs[rid].get("state") for rid in recs
                                if isinstance(recs[rid], dict)},
              "legs": legs,
              "artifacts": _artifact_manifest(run_dir)}
    ok = [l for l in legs if l["state"] == protocol.ST_ACKED]
    report["verdict"] = ("BOTH_DIRECTIONS_ACKED" if legs and len(ok) == len(legs) else "NOT_ACKED")
    out = os.path.join(HERE, "evidence", "real-legs",
                       "real-handoff-%s-rebuilt-%s.json" % (run_id, time.strftime("%H%M%S")))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    ptr = _write_pointer(out, run_id, report["verdict"])
    print("REAL_HANDOFF_VERDICT=%s" % report["verdict"])
    print("REAL_HANDOFF_ARTIFACTS=%d" % len(report["artifacts"]))
    print("REAL_HANDOFF_REPORT=%s" % out)
    print("REAL_HANDOFF_POINTER=%s report_sha256=%s" % (LATEST, ptr["report_sha256"][:16]))
    return 0 if report["verdict"] == "BOTH_DIRECTIONS_ACKED" else 1


def _read_setup(run_dir):
    p = os.path.join(run_dir, "setup.json")
    if not os.path.isfile(p):
        return {"note": "这一跑没有 setup.json（早于新建会话那版跑批件）"}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


#: 指向"哪一枚报告是本次发布的证据"的那张指针件。
LATEST = os.path.join(HERE, "evidence", "real-legs", "LATEST.json")


def _write_pointer(report_path, run_id, verdict):
    """把最新一枚报告的路径与**它自己的 SHA256** 记进指针件。

    为什么要有指针（2026-10-04 独立 CI 自己撞出来的）：判据原先按 **mtime** 挑"最新报告"，
    而 `git archive` 解出来的每一件 mtime 都是解包那一刻——挑到哪一枚全看解包顺序。
    两枚 runner 上各挑到不同的旧失败报告（一枚挑到 162502 的 NOT_ACKED，一枚挑到复分件），
    于是同一份代码在两枚 OS 上报出不同的 REAL_HANDOFF_EVIDENCE，这等于没有读数。
    指针件让选择变成**声明**：写的人（跑批件）当场写下是哪一枚，判据只认这一枚，
    并且复算指针里记的 SHA256；对不上就红，不猜。
    """
    with open(report_path, "rb") as fh:
        data = fh.read()
    doc = {"run_id": run_id,
           "report": os.path.relpath(report_path, HERE).replace(os.sep, "/"),
           "report_sha256": hashlib.sha256(data).hexdigest(),
           "verdict": verdict,
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "rule": "判据只读这一枚；按 mtime 猜在 git archive 解包件里不可复现"}
    security.atomic_write(LATEST, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def run_leg(t, home, sender_logical, receiver_logical, direction, workspace=""):
    """跑一条方向的交接，返回 (request_id, 摘要 dict)。

    派发文里**只写要对方做什么**，绑定行与确认行由适配器渲染那一份给出（唯一真源）。
    早先这里还手抄了一遍"请回 `Q2C-BIND: <绑定串>`"，占位符没填就发出去了，
    等于同一封信里给对侧两条互相矛盾的指令（真件 `evidence/real-legs/04-qoder-sent-*`）——
    对侧照哪一条都是运气。现在这类"我自己造第二个版本"的文案一律删掉。
    """
    payload = (
        "这是一次 q2c 真实交接自检（方向：%s）。请不要执行任何命令、不要读写任何文件。\n"
        "只做一件事：回一段不超过两句话的答复，说明你已经收到这一笔并知道它属于哪个请求号。\n"
        "下面那段是协议要求的收口格式（逐字整行，各占一行，不加粗、不加标点、不写进句子），"
        "请按它回出。\n"
        % direction
    )
    env = protocol.make_request(sender=sender_logical, receiver=receiver_logical,
                                session_id=sender_logical, handoff_type="selfcheck",
                                payload=payload,
                                workspace_ref=workspace,
                                idempotency_key="real-%s-%s" % (direction, time.strftime("%H%M%S")))
    rid = t.create(env)["request_id"]
    steps = []
    state = None
    for i in range(14):
        out = t.pump(rid)
        rec = t.read(rid)["record"]
        state = rec["state"]
        steps.append({"step": i, "status": out.get("status"), "state": state,
                      "failure_class": out.get("failure_class"), "why": out.get("why")})
        if state in protocol.TERMINAL_STATES:
            break
        if state in protocol.PARKED_STATES:
            break
        time.sleep(1.0)
    summary = _leg_summary(t, rid, direction)
    summary["steps"] = steps
    return rid, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-thread", default="", help="复用已有 Codex 线程号（需配 --reuse）")
    ap.add_argument("--qoder-session", default="", help="复用已有 Qoder 会话号（需配 --reuse）")
    ap.add_argument("--reuse", action="store_true",
                    help="复用上列会话号。**默认不用**：每次真跑新建一条，"
                         "免得对侧回的是上一轮那枚绑定串（归属闸会拒，那是它的职责）")
    ap.add_argument("--workspace", default="/tmp", help="给真 CLI 的工作根（默认 /tmp）")
    ap.add_argument("--only", default="", choices=["", "qoder-to-codex", "codex-to-qoder"])
    ap.add_argument("--from-run", default="", metavar="RUN_DIR",
                    help="不花钱的那条路：从已有跑批根的原件**重建报告**（零新调用、不改原件）。"
                         "跑批件自己改了字段时用它复分，不必再烧一次真腿")
    a = ap.parse_args()

    if a.from_run:
        return rebuild_from_run(os.path.realpath(os.path.expanduser(a.from_run)))

    if _env(None) != "1":
        print("拒绝：真实交接需要显式设 Q2C_LIVE=1（这是花钱动作的开关，也是在册的显式授权闸）")
        return 2

    run_id = time.strftime("%Y%m%d-%H%M%S")
    a_workspace = os.path.realpath(os.path.expanduser(a.workspace or ""))
    home = os.path.join(HERE, "evidence", "real-legs", "run-" + run_id)
    os.makedirs(home, exist_ok=True)
    cfg = dict(config_mod.DEFAULTS)
    cfg["receive_timeout_s"] = 600
    cfg["delivery_attempts_max"] = 2
    cfg["adapters"] = {"codex": {"timeout_s": 600}, "qoder": {"timeout_s": 600}}
    # 工作区白名单是产品的**唯一**根外授权入口（SECURITY.md §4）。
    # 真腿的 /tmp 必须显式写进来才能投——第一跑没写，直接被 `WORKSPACE_NOT_ALLOWED`/
    # `REF_OUTSIDE_HOME` 挡下，这一挡是对的，跑批件不该绕过它去关闸。
    cfg["allowed_workspaces"] = [a_workspace] if a_workspace else []
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=1))

    adapters.import_builtin()
    t = transport.Transport(home)

    # 每个方向、每个角色各用一条会话号 ⇒ 四条（两枚线程、两枚会话）。
    # 这样任何一条会话在整跑里只见过**一笔** q2c 消息，不存在"回上一轮"的空间。
    plan = []
    setup_dir = os.path.join(home, "setup")
    setup = {}
    if a.reuse:
        if not (a.codex_thread and a.qoder_session):
            print("拒绝：--reuse 要同时给 --codex-thread 与 --qoder-session")
            return 2
        ids = {k: (a.codex_thread if "codex" in k else a.qoder_session)
               for k in ("codex-A", "qoder-A", "codex-B", "qoder-B")}
        setup["reused"] = True
    else:
        ids, errs = {}, []
        for label, kind in (("qoder-A", "qoder"), ("codex-A", "codex"),
                            ("codex-B", "codex"), ("qoder-B", "qoder")):
            try:
                sid, cap, rc = _setup_conversation(kind, a_workspace, setup_dir, label)
            except (RuntimeError, ValueError, OSError) as exc:
                errs.append("%s：%s" % (label, exc))
                continue
            ids[label] = sid
            setup[label] = {"kind": kind, "provider_session_id": sid,
                            "raw_capture": os.path.relpath(cap, HERE), "rc": rc,
                            "note": "Setup 调用：只为新建会话，不构成交接（无信封、无绑定串）"}
        setup["reused"] = False
        with open(os.path.join(home, "setup.json"), "w", encoding="utf-8") as fh:
            json.dump(setup, fh, ensure_ascii=False, indent=1)
        if errs:
            print("SETUP_FAILED（新建会话这一步本身失败，不带着半成品往下跑）：")
            for e in errs:
                print("   " + e)
            print("REAL_HANDOFF_VERDICT=SETUP_FAILED")
            return 1
    if a.reuse:
        setup["reused_ids"] = ids

    legs = [("qoder-to-codex", ("qoder", ids["qoder-A"]), ("codex", ids["codex-A"])),
            ("codex-to-qoder", ("codex", ids["codex-B"]), ("qoder", ids["qoder-B"]))]
    report = {"run_id": run_id, "home": home, "setup": setup,
              "cli": {"codex": shutil.which("codex") or "",
                      "qoder": shutil.which(os.environ.get("Q2C_QODER_CMD", "qoderclicn")) or ""},
              "legs": []}

    for direction, (sk, sid_provider), (rk, rid_provider) in legs:
        if a.only and a.only != direction:
            continue
        snd = t.sessions.create("sender", sk, provider_session_id=sid_provider,
                                label="real-%s-sender" % direction)
        rcv = t.sessions.create("receiver", rk, provider_session_id=rid_provider,
                                label="real-%s-receiver" % direction)
        print("== 跑方向：%s（发起 %s／接收 %s）==" % (direction, sid_provider, rid_provider),
              flush=True)
        rid, summary = run_leg(t, home, snd.session_id, rcv.session_id, direction, a_workspace)
        summary["sender_provider"] = sid_provider
        summary["receiver_provider"] = rid_provider
        report["legs"].append(summary)
        print(json.dumps({k: summary[k] for k in
                          ("request_id", "state", "nonce", "delivery", "last_receipt")},
                         ensure_ascii=False, indent=1), flush=True)
        for st in summary["steps"][-3:]:
            print("   step %s → %s %s %s" % (st["step"], st["state"], st.get("failure_class") or "",
                                              (st.get("why") or "")[:120]), flush=True)

    db = ledger.load_for_write(ledger.LedgerPaths(home))
    report["ledger_states"] = {rid: rec.get("state") for rid, rec in db.items()}
    report["artifacts"] = _artifact_manifest(home)
    ok = [l for l in report["legs"] if l["state"] == protocol.ST_ACKED]
    report["verdict"] = ("BOTH_DIRECTIONS_ACKED" if len(ok) == len(report["legs"]) and report["legs"]
                         else "NOT_ACKED")
    out = os.path.join(HERE, "evidence", "real-legs", "real-handoff-%s.json" % run_id)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    ptr = _write_pointer(out, run_id, report["verdict"])
    print("REAL_HANDOFF_VERDICT=%s" % report["verdict"])
    print("REAL_HANDOFF_ARTIFACTS=%d" % len(report["artifacts"]))
    print("REAL_HANDOFF_REUSED=%s" % ("yes" if setup.get("reused") else "no"))
    print("REAL_HANDOFF_RUN=%s" % home)
    print("REAL_HANDOFF_REPORT=%s" % out)
    print("REAL_HANDOFF_POINTER=%s report_sha256=%s" % (LATEST, ptr["report_sha256"][:16]))
    return 0 if report["verdict"] == "BOTH_DIRECTIONS_ACKED" else 1


if __name__ == "__main__":
    sys.exit(main())
