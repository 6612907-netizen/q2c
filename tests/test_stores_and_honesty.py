#!/usr/bin/env python3
"""状态件判据：台账并发／原子、逻辑会话、产物引用、跟踪九问、读数诚实性。

迁移自实验根的 U1–U4／K46／K11／K8／K77／K80／K40／K36／K38 一族
（坐标见 Q2C-BOUNDARY-AUDIT.md §B 第 2 节 G／D／H／I 组）。
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from q2c import (artifacts, ledger, protocol, security, sessions as sessions_mod,  # noqa: E402
                 trace as trace_mod, transport)  # noqa: E402
from tests import helpers  # noqa: E402


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")
        self.paths = ledger.LedgerPaths(self.h.home)

    def tearDown(self):
        self.h.close()

    def _write(self, records, rev=1):
        security.atomic_write(self.paths.ledger,
                              json.dumps({"rev": rev, "records": records}, ensure_ascii=False))

    def test_01_absent_versus_unreadable_versus_empty(self):
        with self.assertRaises(ledger.LedgerAbsent):
            ledger.load(self.paths)
        self._write({})
        db = ledger.load(self.paths)
        self.assertEqual(len(db), 0, "读到空表这一档必须能用")
        self._write("{坏了")
        with self.assertRaises(ledger.LedgerUnreadable):
            ledger.load(self.paths)

    def test_02_stale_write_is_loud_not_silent(self):
        self._write({"r1": {"request_id": "r1", "state": protocol.ST_QUEUED}})
        db = ledger.load(self.paths)
        self._write({"r1": {"request_id": "r1", "state": protocol.ST_DELIVERING},
                     "r2": {"request_id": "r2", "state": protocol.ST_QUEUED}}, rev=2)  # 第三者直写
        db["r1"]["state"] = protocol.ST_ACKED
        out = ledger.save(self.paths, db)
        codes = [e["ev"] for e in out["events"]]
        self.assertIn("stale_write_detected", codes, "绕锁直写却没响")
        merged = ledger.load(self.paths)
        self.assertIn("r2", merged, "第三者那笔被我整块覆盖掉了")

    def test_03_field_conflict_keeps_mine_but_alarms(self):
        base = {"r1": {"request_id": "r1", "state": protocol.ST_QUEUED, "history": []}}
        disk = {"r1": {"request_id": "r1", "state": protocol.ST_DELIVERING, "history": []}}
        mine = {"r1": {"request_id": "r1", "state": protocol.ST_ACKED, "history": []}}
        notes = []
        merged = ledger.merge_ledger(base, disk, mine, notes)
        self.assertEqual(merged["r1"]["state"], protocol.ST_ACKED)
        self.assertIn("field_conflict_keep_mine", [n["ev"] for n in notes])

    def test_04_history_is_union_not_winner(self):
        base = {"r1": {"request_id": "r1", "history": [{"a": 1}]}}
        disk = {"r1": {"request_id": "r1", "history": [{"a": 1}, {"b": 2}]}}
        mine = {"r1": {"request_id": "r1", "history": [{"a": 1}, {"c": 3}]}}
        merged = ledger.merge_ledger(base, disk, mine, [])
        hist = merged["r1"]["history"]
        self.assertIn({"b": 2}, hist, "对方的轨迹被我丢了")
        self.assertIn({"c": 3}, hist)

    def test_05_delete_loses_against_others_update(self):
        base = {"r1": {"request_id": "r1", "state": protocol.ST_QUEUED}}
        disk = {"r1": {"request_id": "r1", "state": protocol.ST_ACKED}}
        merged = ledger.merge_ledger(base, disk, {}, [])
        self.assertIn("r1", merged, "我删了，但对方改过 ⇒ 保盘上值并响")

    def test_06_two_processes_no_lost_transition(self):
        """两个进程各推一笔 ⇒ 两笔都在（QA-DEBT-1 本体：真并发，不是模拟）。"""
        t, sender, receiver = self.h.sessions()
        envs = [self.h.request(sender=sender, receiver=receiver, session_id=sender,
                               idempotency_key="idem-p%d" % i) for i in (1, 2)]
        worker = ("import os,sys,json;sys.path.insert(0,%r);"
                  "from q2c import transport, protocol;"
                  "t=transport.Transport(os.environ['Q2C_HOME']);"
                  "d=json.loads(sys.argv[1]);"
                  "e=protocol.Envelope.from_dict(d);"
                  "r=t.create(e);t.pump(r['request_id'])" % HERE)
        rs = [subprocess.run([sys.executable, "-c", worker, json.dumps(e.to_dict())],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(os.environ, Q2C_HOME=self.h.home), cwd=HERE, timeout=120)
              for e in envs]
        for r in rs:
            self.assertEqual(r.returncode, 0, r.stdout)
        got = transport.Transport(self.h.home).read()
        self.assertEqual(len(got["records"]), 2, "并发写丢了一笔：%s" % got["records"])
        self.assertTrue(all(v != protocol.ST_CREATED for v in got["records"].values()),
                        "有记录被整块盖回了起点")

class TestSessions(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")
        self.store = sessions_mod.SessionStore(self.h.home)

    def tearDown(self):
        self.h.close()

    def test_07_logical_id_stable_across_provider_change(self):
        s = self.store.create("receiver", "loopback", provider_session_id="thread-A")
        s2 = self.store.rebind(s.session_id, "thread-B", "provider 重启，线程号换了")
        self.assertEqual(s.session_id, s2.session_id, "换绑改了逻辑号＝交接身份被 provider 带着走")
        self.assertEqual(s2.current(), "thread-B")

    def test_08_history_binding_is_kept(self):
        s = self.store.create("receiver", "loopback", provider_session_id="thread-A")
        at_a = protocol.utc_now()
        self.store.rebind(s.session_id, "thread-B", "重启")
        got = self.store.get(s.session_id)
        self.assertEqual(got.provider_at(at_a), "thread-A", "重放旧消息要能取到当时那一枚")

    def test_09_rebind_requires_reason(self):
        s = self.store.create("receiver", "loopback", provider_session_id="t-A")
        with self.assertRaises(sessions_mod.SessionError) as cm:
            self.store.rebind(s.session_id, "t-B", "")
        self.assertEqual(cm.exception.code, "REBIND_REASON_REQUIRED")

    def test_10_empty_provider_id_refused(self):
        s = self.store.create("receiver", "loopback")
        with self.assertRaises(sessions_mod.SessionError) as cm:
            self.store.rebind(s.session_id, "   ", "手滑")
        self.assertEqual(cm.exception.code, "EMPTY_PROVIDER_ID")

    def test_11_different_roles_are_different_sessions(self):
        a = self.store.create("receiver", "loopback", label="核读")
        b = self.store.create("observer", "loopback", label="核读")
        self.assertNotEqual(a.session_id, b.session_id)

    def test_12_unknown_role_rejected(self):
        with self.assertRaises(sessions_mod.SessionError) as cm:
            self.store.create("boss", "loopback")
        self.assertEqual(cm.exception.code, "UNKNOWN_ROLE")

    def test_13_unreadable_store_is_not_empty_table(self):
        p = os.path.join(self.h.home, "state", "sessions.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{坏")
        with self.assertRaises(sessions_mod.SessionError) as cm:
            self.store.list_all()
        self.assertEqual(cm.exception.code, "SESSIONS_UNREADABLE")

    def test_14_reverse_lookup_by_provider(self):
        s = self.store.create("receiver", "loopback", provider_session_id="t-9")
        self.assertEqual(self.store.which("loopback", "t-9").session_id, s.session_id)
        self.store.rebind(s.session_id, "t-10", "重启")
        self.assertEqual(self.store.which("loopback", "t-9").session_id, s.session_id,
                         "旧绑定查不到 ⇒ 历史账断了")


class TestArtifacts(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")

    def tearDown(self):
        self.h.close()

    def _file(self, name="a.txt", text="内容\n"):
        p = os.path.join(self.h.home, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_15_digest_is_over_bytes(self):
        ref = artifacts.describe_file("file", self._file())
        self.assertEqual(ref.digest, artifacts.sha256_file(ref.ref))

    def test_16_missing_and_diverged_and_unverifiable(self):
        p = self._file()
        ref = artifacts.describe_file("file", p)
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_OK)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("换了内容\n")
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_DIVERGED)
        os.remove(p)
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_MISSING)
        bare = protocol.ArtifactRef(type="file", ref=p)
        self.assertEqual(artifacts.verify(bare)[0], artifacts.VERIFY_MISSING,
                         "文件确实不在 ⇒ MISSING（判过），不是「我判不了」")
        p2 = self._file("b.txt", "另一份\n")
        self.assertEqual(artifacts.verify(protocol.ArtifactRef(type="file", ref=p2))[0],
                         artifacts.VERIFY_UNVERIFIABLE, "没摘要只能说可达，不能说一致")

    def test_17_package_excludes_runtime(self):
        src = os.path.join(self.h.home, "src")
        os.makedirs(os.path.join(src, "state"), exist_ok=True)
        with open(os.path.join(src, "state", "ledger.json"), "w") as fh:
            fh.write("{}")
        with open(os.path.join(src, "relay.py"), "w") as fh:
            fh.write("print(1)\n")
        with open(os.path.join(src, "wake.lock"), "w") as fh:
            fh.write("x")
        cand, fp = artifacts.export_package(src, os.path.join(self.h.home, "packages"))
        self.assertFalse(os.path.exists(os.path.join(cand, "state")))
        self.assertFalse(os.path.exists(os.path.join(cand, "wake.lock")))
        self.assertTrue(os.path.exists(os.path.join(cand, "relay.py")))
        ref = artifacts.describe_file("evidence_package", cand)
        self.assertEqual(ref.digest, fp)
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_OK)

    def test_18_snapshot_fingerprint_is_reproducible(self):
        src = os.path.join(self.h.home, "src2")
        os.makedirs(src)
        for n in ("b.py", "a.py"):
            with open(os.path.join(src, n), "w") as fh:
                fh.write(n)
        self.assertEqual(artifacts.manifest_fingerprint(src),
                         artifacts.manifest_fingerprint(src))
        rows = artifacts.manifest_rows(src)
        self.assertEqual(rows, sorted(rows), "清单必须按路径排序，否则同输入不同指纹")

    def test_19_credential_shaped_refs_are_refused(self):
        p = os.path.join(self.h.home, "credentials")
        with open(p, "w") as fh:
            fh.write("x")
        with self.assertRaises(protocol.ProtocolError) as cm:
            artifacts.describe_file("file", p)
        self.assertEqual(cm.exception.code, "ARTIFACT_IS_CREDENTIAL_PATH")

    def test_20_generic_uri_is_not_verified_as_bytes(self):
        ref = protocol.ArtifactRef(type="generic_uri", ref="https://example/x")
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_UNVERIFIABLE)

    def test_21_git_commit_needs_a_repo_to_verify(self):
        ref = artifacts.commit_ref("1875b95e", "/nope")
        self.assertEqual(ref.digest, "", "把提交号再哈希一遍是假装有摘要")
        self.assertEqual(artifacts.verify(ref)[0], artifacts.VERIFY_UNVERIFIABLE)


class TestTrace(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")

    def tearDown(self):
        self.h.close()

    def test_22_nine_questions_are_answered(self):
        t, sender, receiver = self.h.sessions()
        env = self.h.request(sender=sender, receiver=receiver, session_id=sender)
        rid = t.create(env)["request_id"]
        for _ in range(6):
            if t.pump(rid).get("state") == protocol.ST_ACKED:
                break
        q = t.trace.nine_questions(rid)
        for key in ("who_sent", "who_received", "when_delivered", "when_started",
                    "which_session", "what_response", "when_acknowledged"):
            self.assertNotIn(q.get(key), (None, trace_mod.ABSENT), "%s 没答案" % key)
        self.assertEqual(q["when_acknowledged"] != trace_mod.NOT_OBSERVED, True)

    def test_23_absent_trace_says_so(self):
        tr = trace_mod.Trace(os.path.join(self.h.home, "never-written"))
        out = tr.nine_questions("req-none")
        self.assertEqual(out["trace"], trace_mod.ABSENT)
        self.assertIn("没读到", out["why"])

    def test_24_forbidden_project_fields_are_refused(self):
        tr = trace_mod.Trace(self.h.home)
        for key in ("acceptance", "grade", "release_ready", "task_completed"):
            with self.assertRaises(trace_mod.TraceError) as cm:
                tr.emit({"ev": "x", key: "y"})
            self.assertEqual(cm.exception.code, "FORBIDDEN_EVENT_KEY")

    def test_25_redaction_before_write(self):
        tr = trace_mod.Trace(self.h.home)
        tr.emit({"ev": "note", "detail": "Bearer abcdef1234567890 and sk-ABCDEFGHIJ0123456789"})
        with open(tr.path, encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn("abcdefghij", blob.lower())
        self.assertIn("<redacted>", blob)

    def test_26_append_only_and_orderable(self):
        tr = trace_mod.Trace(self.h.home)
        tr.emit({"ev": "a", "request_id": "r"})
        tr.emit({"ev": "b", "request_id": "r"})
        evs = tr.events("r")
        self.assertEqual([e["ev"] for e in evs], ["a", "b"])

    def test_27_illegal_transition_never_reaches_the_log(self):
        tr = trace_mod.Trace(self.h.home)
        with self.assertRaises(protocol.ProtocolError):
            tr.state_change({"request_id": "r", "message_id": "m", "correlation_id": "c",
                             "session_id": "s"}, protocol.ST_CREATED, protocol.ST_ACKED)
        self.assertEqual(tr.events("r"), [], "非法迁移也留了一行＝写在前、判在后")


class TestReadHonesty(unittest.TestCase):
    def setUp(self):
        self.h = helpers.TempHome(mode="ack")

    def tearDown(self):
        self.h.close()

    def test_28_read_only_commands_write_nothing(self):
        t, sender, receiver = self.h.sessions()
        env = self.h.request(sender=sender, receiver=receiver, session_id=sender)
        rid = t.create(env)["request_id"]
        t.pump(rid)
        before = self.h.tree_sha()
        t.read(rid)
        t.list_records()
        t.trace.nine_questions(rid)
        t.inspect(rid)
        self.assertEqual(self.h.tree_sha(), before, "只读动作改写了被观测的对象")

    def test_29_malformed_record_is_dropped_not_guessed(self):
        p = os.path.join(self.h.home, "state", "ledger.json")
        t, sender, receiver = self.h.sessions()
        t.create(self.h.request(sender=sender, receiver=receiver, session_id=sender))
        with open(p, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
        doc["records"]["ghost"] = {"no_request_id": 1}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        db = ledger.load(ledger.LedgerPaths(self.h.home), read_only=True)
        self.assertNotIn("ghost", db)
        self.assertTrue(any(n.get("ev") == "dropped_malformed_record" for n in db.notes))

    def test_30_receipt_without_rc_is_not_zero(self):
        """退出码侧件没写出来 ⇒ None，不折成 0（读不到 ≠ 成功）。"""
        from q2c.adapters import _spawn
        cap = os.path.join(self.h.home, "state", "x.out")
        os.makedirs(os.path.dirname(cap), exist_ok=True)
        with open(cap, "w") as fh:
            fh.write("有正文")
        self.assertIsNone(_spawn.read_rc(cap))
        with open(cap + ".rc", "w") as fh:
            fh.write("不是整数")
        self.assertIsNone(_spawn.read_rc(cap))

    def test_31_notify_without_channel_says_file_only(self):
        t, sender, receiver = self.h.sessions()
        out = t.notify("req-x", "CREDENTIAL_UNAVAILABLE", "测一下没配通道")
        self.assertFalse(out["notified"])
        self.assertIn("file-only", out["detail"])
        self.assertFalse(t.notify_delivered("req-x"), "全局通道开着不等于这一笔送到")
        self.assertTrue(os.path.isfile(os.path.join(self.h.home, "notify", "req-x.txt")))

    def test_32_notify_receipt_is_per_record(self):
        t, sender, receiver = self.h.sessions()
        t._record_notify_ack("req-1", True, "delivered")
        t._record_notify_ack("req-2", False, "没送到")
        self.assertTrue(t.notify_delivered("req-1"))
        self.assertFalse(t.notify_delivered("req-2"))
        self.assertFalse(t.notify_delivered("req-3"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
