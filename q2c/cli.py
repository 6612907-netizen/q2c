#!/usr/bin/env python3
"""`q2c` 命令行：11 个子命令，全是传输动作，不含项目管理（任务书 §10）。

退出码约定（写进 README 与 SECURITY.md，脚本可以照着分支）：

    0  成功
    1  运维性失败（投递没送上、取消没成、记录不在）
    2  **拒绝**：协议不认、适配器不认、路径/凭据/配置越界（fail-closed，零副作用）
    3  读不动：台账／跟踪／配置存在但解析不了（不等于"没有"，也不等于"没问题"）

只读子命令（`inspect`／`list`／`trace`／`doctor`／`adapters`／`version`）
一个字节都不写：不建目录、不取写锁。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, ack, adapters, artifacts, config as config_mod, ledger
from . import protocol, security, trace, transport as transport_mod
from .sessions import ROLES, SessionError, SessionStore

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_REJECT = 2
EXIT_UNREADABLE = 3

READ_ONLY = ("inspect", "list", "trace", "doctor", "adapters", "version", "sessions-list")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="q2c", description="q2c — AI 编码 Agent 之间的可靠交接")
    p.add_argument("--home", default="", help="状态根（默认 ~/.q2c，或环境变量 Q2C_HOME）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建状态根并写一份默认配置")
    sub.add_parser("doctor", help="只读体检：环境、配置、适配器、凭据可用性、台账可读性")
    sub.add_parser("adapters", help="列出在册适配器与各自能力（含明写的限制）")
    sub.add_parser("version", help="版本号与协议版本")

    sp = sub.add_parser("send", help="发一条 HANDOFF_REQUEST")
    sp.add_argument("--from", dest="sender", required=True, help="发起方的 q2c 逻辑会话号")
    sp.add_argument("--to", dest="receiver", required=True, help="接收方的 q2c 逻辑会话号")
    sp.add_argument("--type", dest="handoff_type", default="handoff")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--payload", default="")
    g.add_argument("--payload-file", default="")
    sp.add_argument("--workspace", default="")
    sp.add_argument("--repo", default="")
    sp.add_argument("--commit", default="")
    sp.add_argument("--artifact", action="append", default=[],
                    help="type=ref[@digest]，可重复；例：git_commit=1875b95e")
    sp.add_argument("--idem", default="", help="幂等键（同键只投一次）")
    sp.add_argument("--expires", default="", help="ISO-8601 过期时刻")
    sp.add_argument("--request-id", default="")
    sp.add_argument("--no-wait", action="store_true", help="只入队，不推这一拍")

    ip = sub.add_parser("inspect", help="看一笔交接（读数，不判定）")
    ip.add_argument("request_id")

    sub.add_parser("list", help="列出台账里的传输状态分布与在途笔数")

    tp = sub.add_parser("trace", help="回答协议 §8 的九问")
    tp.add_argument("request_id")

    rp = sub.add_parser("retry-delivery", help="重投**消息**（绝不重跑对侧任务）")
    rp.add_argument("request_id")
    rp.add_argument("--leg", default="", choices=["", "request", "result"])

    cp = sub.add_parser("cancel", help="取消投递（不撤销对侧已开始的工作）")
    cp.add_argument("request_id")
    cp.add_argument("--reason", default="")

    sub.add_parser("expire", help="把到点未确认的笔转 EXPIRED（漏单恢复用）")
    sub.add_parser("recover", help="重启后的处置：未知与确认没跑分开")

    ss = sub.add_parser("sessions", help="逻辑会话：create／bind／list")
    ss.add_argument("action", choices=["create", "bind", "list"])
    ss.add_argument("--adapter", default="")
    ss.add_argument("--role", default="receiver", choices=list(ROLES))
    ss.add_argument("--provider", default="")
    ss.add_argument("--label", default="")
    ss.add_argument("--session", default="")
    ss.add_argument("--reason", default="")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    home = security.real_home(args.home or None)
    if args.cmd not in READ_ONLY:
        os.makedirs(home, exist_ok=True)
    try:
        return _dispatch(args, home)
    except protocol.ProtocolError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT
    except security.PathError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT
    except adapters.base.AdapterError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT
    except config_mod.ConfigError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT if "UNREADABLE" not in exc.code else EXIT_UNREADABLE
    except SessionError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT if "UNREADABLE" not in exc.code else EXIT_UNREADABLE
    except ledger.LedgerUnreadable as exc:
        _print_err("LEDGER_UNREADABLE", str(exc), home)
        return EXIT_UNREADABLE
    except ledger.LedgerAbsent as exc:
        _print_err("LEDGER_ABSENT", str(exc), home)
        return EXIT_UNREADABLE
    except transport_mod.TransportError as exc:
        _print_err(exc.code, exc.detail, home)
        return EXIT_REJECT if "NO_SUCH" in exc.code or "NOT_ALLOWED" in exc.code else EXIT_FAIL


def _dispatch(args, home: str) -> int:
    cmd = args.cmd
    if cmd == "version":
        print(json.dumps({"q2c": __version__, "protocol_version": protocol.PROTOCOL_VERSION,
                          "home": home}, ensure_ascii=False))
        return EXIT_OK

    if cmd == "init":
        p = config_mod.write_default(home) if not os.path.isfile(config_mod.path_for(home)) else \
            config_mod.path_for(home)
        for d in ("state", "trace", "results", "mailbox", "notify"):
            os.makedirs(os.path.join(home, d), exist_ok=True)
        print(json.dumps({"status": "ok", "config": p, "home": home,
                          "existing": os.path.isfile(config_mod.path_for(home))}, ensure_ascii=False))
        return EXIT_OK

    if cmd == "adapters":
        names = adapters.import_builtin()
        out = []
        for n in names:
            a = adapters.build(n, {"home": home})
            out.append(dict(a.capabilities(), configured=_configured(a, home)))
        print(json.dumps({"adapters": out}, ensure_ascii=False, indent=1))
        return EXIT_OK

    if cmd == "doctor":
        return _doctor(home)

    if cmd == "sessions":
        return _sessions(args, home)

    t = transport_mod.Transport(home)

    if cmd == "send":
        payload = args.payload
        if args.payload_file:
            with open(args.payload_file, encoding="utf-8") as fh:
                payload = fh.read()
        refs = [_artifact_spec(s) for s in args.artifact]
        meta = {"cli": True}
        env = protocol.make_request(
            sender=args.sender, receiver=args.receiver,
            session_id=args.sender, handoff_type=args.handoff_type, payload=payload,
            request_id=args.request_id or None,
            idempotency_key=args.idem or None,
            workspace_ref=args.workspace, repo_ref=args.repo, commit_ref=args.commit,
            artifact_refs=refs, expires_at=args.expires, metadata=meta)
        res = t.send(env, wait=not args.no_wait)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if res.get("status") == "duplicate_suppressed":
            # 抑制不是失败：这一笔早就投过了， rc=0 并把撞上的号报回去
            return EXIT_OK
        if args.no_wait:
            # `--no-wait` 的语义就是"收到并入队了"，此时状态必然是 QUEUED。
            # 把"没等 ACK"报成 rc=1，会让脚本以为入队失败而重试，反而造出重复投递。
            return EXIT_OK if res.get("state") == protocol.ST_QUEUED else EXIT_FAIL
        st = (res.get("pump") or {}).get("state") or res.get("state")
        return EXIT_OK if st == protocol.ST_ACKED else EXIT_FAIL

    if cmd == "inspect":
        out = t.inspect(args.request_id)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        # 读不到这一笔 ⇒ rc=1。过去照样 rc=0，于是脚本会以为"查到了"，
        # 而屏幕上其实只有 `"record": null`。
        return EXIT_OK if out.get("record") else EXIT_FAIL
    if cmd == "list":
        print(json.dumps(t.list_records(), ensure_ascii=False, indent=1))
        return EXIT_OK
    if cmd == "trace":
        q = t.trace.nine_questions(args.request_id)
        print(json.dumps(q, ensure_ascii=False, indent=1))
        # 跟踪件不在／这一笔没事件 ⇒ rc=3：与"查到了但某一问没答案"是两档，
        # 不许都被读成"查过了，没问题"
        if q.get("trace") in (trace.ABSENT, trace.NOT_OBSERVED):
            return EXIT_UNREADABLE
        return EXIT_OK
    if cmd == "retry-delivery":
        res = t.retry_delivery(args.request_id, leg=args.leg)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return EXIT_OK if res.get("state") == protocol.ST_ACKED else EXIT_FAIL
    if cmd == "cancel":
        res = t.cancel(args.request_id, reason=args.reason)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return EXIT_OK if res.get("status") == "cancelled" else EXIT_FAIL
    if cmd == "expire":
        print(json.dumps(t.expire_due(), ensure_ascii=False, indent=1))
        return EXIT_OK
    if cmd == "recover":
        print(json.dumps(t.recover(), ensure_ascii=False, indent=1))
        return EXIT_OK
    raise protocol.ProtocolError("UNKNOWN_COMMAND", repr(cmd))


def _doctor(home: str) -> int:
    """只读体检。每一项都给读数，测不到就写测不到——不折成"干净"。"""
    out = {"home": home, "q2c": __version__, "protocol_version": protocol.PROTOCOL_VERSION}
    out["home_writable"] = os.access(home, os.W_OK) if os.path.isdir(home) else "absent"
    cfg, cfg_err = None, None
    try:
        cfg = config_mod.load(home)
        out["config"] = {"source": cfg.source or "defaults", "unknown_keys": cfg.unknown_keys,
                         "delivery_attempts_max": cfg["delivery_attempts_max"]}
    except config_mod.ConfigError as exc:
        cfg_err = exc
        out["config"] = {"error": exc.code, "detail": exc.detail}
    try:
        paths = ledger.LedgerPaths(home)
        if not os.path.isfile(paths.ledger):
            out["ledger"] = {"state": "ABSENT", "path": paths.ledger,
                             "note": "还没发过东西，或 Q2C_HOME 指错了根——这不是「零条记录」"}
            raise ledger.LedgerAbsent(paths.ledger)
        db = ledger.load(paths, read_only=True)
        out["ledger"] = {"records": len(db), "rev": db.rev, "notes": db.notes}
    except ledger.LedgerAbsent:
        out["ledger"] = {"state": "ABSENT", "note": "还没发过东西，或 Q2C_HOME 指错了根"}
    except ledger.LedgerUnreadable as exc:
        out["ledger"] = {"state": "UNREADABLE", "why": str(exc)}
    tr = transport_mod.Trace(home)
    out["trace"] = {"present": tr.present(), "path": tr.path}
    out["adapters"] = {}
    adapters.import_builtin()
    for name in adapters.known():
        try:
            one = cfg.adapter_config(name) if cfg is not None else {"home": home}
            a = adapters.build(name, one)
            caps = a.capabilities()
            ok_cred, why_cred = a.check_credential()
            out["adapters"][name] = {"live": caps.get("live"), "terminal_evidence":
                                     caps.get("terminal_evidence"),
                                     "credential": "READY" if ok_cred else "UNAVAILABLE",
                                     "credential_detail": security.redact(why_cred),
                                     "limits": caps.get("limits", [])}
        except Exception as exc:
            out["adapters"][name] = {"error": type(exc).__name__, "detail": security.redact(str(exc))}
    out["secret_scan"] = _scan_config_for_secrets(home)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    bad = out.get("ledger", {}).get("state") == "UNREADABLE" or \
        out["secret_scan"] or out.get("config", {}).get("error")
    return EXIT_UNREADABLE if out.get("ledger", {}).get("state") == "UNREADABLE" else \
        (EXIT_REJECT if bad else EXIT_OK)


def _sessions(args, home: str) -> int:
    store = SessionStore(home)
    if args.action == "list":
        print(json.dumps([{"session_id": s.session_id, "role": s.role, "adapter": s.adapter,
                           "provider_session_id": s.current(), "bindings": s.bindings,
                           "label": s.label} for s in store.list_all()],
                         ensure_ascii=False, indent=1))
        return EXIT_OK
    if args.action == "create":
        # 控制入口就地拒：登记一条不存在的适配器不该等到投递那一刻才炸。
        # 这一格是 CLI 判据抓出来的 fail-open（过去 rc=0，账面上留下一条永远投不出去的死会话）。
        names = adapters.import_builtin()
        if args.adapter not in names:
            raise adapters.base.AdapterError(
                "UNKNOWN_ADAPTER", "=%r（在册：%s）" % (args.adapter, ", ".join(names)))
        s = store.create(args.role, args.adapter, args.provider, args.label)
        print(json.dumps({"session_id": s.session_id, "role": s.role, "adapter": s.adapter,
                          "provider_session_id": s.current()}, ensure_ascii=False, indent=1))
        return EXIT_OK
    if args.action == "bind":
        if not args.session:
            raise SessionError("SESSION_REQUIRED", "bind 要 --session <q2c 逻辑号>")
        s = store.rebind(args.session, args.provider, args.reason or "manual bind")
        print(json.dumps({"session_id": s.session_id, "provider_session_id": s.current(),
                          "bindings": len(s.bindings)}, ensure_ascii=False, indent=1))
        return EXIT_OK
    raise SessionError("UNKNOWN_SESSION_ACTION", args.action)


def _artifact_spec(text: str) -> protocol.ArtifactRef:
    if "=" not in text:
        raise protocol.ProtocolError("BAD_ARTIFACT_SPEC", text)
    kind, rest = text.split("=", 1)
    if "@" in rest:
        ref, digest = rest.rsplit("@", 1)
    else:
        ref, digest = rest, ""
    return protocol.ArtifactRef(type=kind.strip(), ref=ref.strip(), digest=digest.strip())


def _configured(adapter, home: str) -> str:
    """这一腿现在走的是哪档：内置演示车 / 默认公开 CLI / 被显式换掉的接缝。

    不许把"非 live"一律报成 override：loopback 天生不叫外部进程，
    报错会让人以为有人在配置里动了手脚。
    """
    caps = adapter.capabilities()
    if caps.get("name") == "loopback":
        return "builtin-demo"
    override = (os.environ.get("Q2C_%s_CMD" % adapter.name.upper(), "")
                or (adapter.config.get("cmd") or "")).strip()
    return "override（%s）" % security.redact(override) if override else "default（公开 CLI）"


def _scan_config_for_secrets(home: str) -> list:
    hits = []
    p = config_mod.path_for(home)
    if not os.path.isfile(p):
        return hits
    try:
        with open(p, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return ["config.json 读不动（这不算「没问题」）"]
    if security.contains_secret_shape(raw):
        hits.append("config.json 里有凭据形状的内容")
    return hits


def _print_err(code: str, detail: str, home: str) -> None:
    print(json.dumps({"status": "rejected", "code": code, "detail": security.redact(detail or ""),
                      "home": home, "side_effects": 0}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
