#!/usr/bin/env python3
"""协议层判据：枚举冻结、fail-closed、状态机、前向兼容。

全部用标准库 unittest（新用户不需要装任何东西）。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from q2c import protocol as P  # noqa: E402


def ok_env(**over):
    base = dict(
        sender="qs-sender0000000001",
        receiver="qs-receiver000000001",
        session_id="qs-session00000000001",
        handoff_type="review-request",
        payload="请核读这份提交并回一句话。",
    )
    base.update(over)
    return P.make_request(**base)


class TestEnvelope(unittest.TestCase):
    def test_01_roundtrip(self):
        env = ok_env(commit_ref="1875b95e", workspace_ref="/tmp/w")
        doc = env.to_dict()
        again = P.Envelope.from_dict(doc)
        self.assertEqual(again.to_dict(), doc, "往返必须逐字相同（桥不许在中间改别人的信）")

    def test_02_missing_required_rejected(self):
        doc = ok_env().to_dict()
        doc.pop("correlation_id")
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "MISSING_FIELDS")
        self.assertIn("correlation_id", cm.exception.detail)

    def test_03_empty_string_required_rejected(self):
        doc = ok_env().to_dict()
        doc["sender"] = "   "
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "MISSING_FIELDS")

    def test_04_payload_may_be_empty(self):
        """空正文是合法消息（ping 那一类）。必填的是"有这个字段且是字符串"，不是"有内容"。"""
        env = P.Envelope.from_dict(ok_env(payload="").to_dict())
        self.assertEqual(env.payload, "")

    def test_05_payload_is_opaque(self):
        """桥不解析正文：里面写着 TASK_COMPLETED 也照样投（那是发送方的话，不是协议的状态）。"""
        env = ok_env(payload="结论：TASK_COMPLETED，可以 READY_TO_RELEASE")
        P.Envelope.from_dict(env.to_dict())

    def test_06_handoff_type_namespace(self):
        env = ok_env(handoff_type="notify-human")
        self.assertEqual(P.Envelope.from_dict(env.to_dict()).handoff_type, "notify-human")

    def test_07_handoff_type_must_not_be_project_truth(self):
        with self.assertRaises(P.ProtocolError) as cm:
            ok_env(handoff_type="TASK_COMPLETED")
        self.assertEqual(cm.exception.code, "FORBIDDEN_VALUE")


class TestFailClosed(unittest.TestCase):
    def test_10_unknown_protocol_version(self):
        doc = ok_env().to_dict()
        doc["protocol_version"] = "q2c/2"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "UNSUPPORTED_PROTOCOL_VERSION")
        self.assertEqual(cm.exception.side_effects, 0)

    def test_11_missing_protocol_version_is_not_v1(self):
        doc = ok_env().to_dict()
        doc.pop("protocol_version")
        with self.assertRaises(P.ProtocolError):
            P.Envelope.from_dict(doc)

    def test_12_unknown_message_type(self):
        doc = ok_env().to_dict()
        doc["message_type"] = "HANDOFF_APPROVE_RELEASE"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "UNKNOWN_MESSAGE_TYPE")

    def test_13_unknown_artifact_type(self):
        doc = ok_env().to_dict()
        doc["artifact_refs"] = [{"type": "signoff", "ref": "x"}]
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "UNKNOWN_ARTIFACT_TYPE")

    def test_14_bad_digest_shape(self):
        doc = ok_env().to_dict()
        doc["artifact_refs"] = [{"type": "file", "ref": "/tmp/a", "digest": "deadbeef"}]
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "BAD_ARTIFACT_DIGEST")

    def test_15_bad_commit_ref(self):
        with self.assertRaises(P.ProtocolError) as cm:
            ok_env(commit_ref="not-a-sha")
        self.assertEqual(cm.exception.code, "BAD_COMMIT_REF")

    def test_16_bad_timestamp(self):
        doc = ok_env().to_dict()
        doc["created_at"] = "2026-10-04 14:00"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "BAD_TIMESTAMP")

    def test_17_naive_timestamp_rejected(self):
        """没有时区偏移＝两个机器会读成两个时刻。不猜。"""
        doc = ok_env().to_dict()
        doc["created_at"] = "2026-10-04T14:00:00"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "BAD_TIMESTAMP")

    def test_18_expires_before_created(self):
        doc = ok_env().to_dict()
        doc["expires_at"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "EXPIRES_BEFORE_CREATED")

    def test_19_envelope_must_be_object(self):
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(["not", "a", "dict"])
        self.assertEqual(cm.exception.code, "BAD_ENVELOPE")

    def test_20_bad_id_rejected(self):
        doc = ok_env().to_dict()
        doc["request_id"] = "含 空格 的号"
        with self.assertRaises(P.ProtocolError) as cm:
            P.Envelope.from_dict(doc)
        self.assertEqual(cm.exception.code, "BAD_ID")


class TestForwardCompat(unittest.TestCase):
    def test_30_unknown_fields_preserved(self):
        doc = ok_env().to_dict()
        doc["routing_hint"] = {"lane": "slow"}
        doc["x_sender_experiment"] = "42"
        again = P.Envelope.from_dict(doc)
        self.assertEqual(again.extra["routing_hint"], {"lane": "slow"})
        self.assertEqual(again.to_dict()["x_sender_experiment"], "42",
                         "前向兼容＝别人加的字段不许被我这一跳丢掉")

    def test_31_json_roundtrip(self):
        env = ok_env(artifact_refs=[{"type": "git_commit", "ref": "1875b95e",
                                     "digest": "a" * 64, "size_bytes": 7}])
        again = P.Envelope.from_dict(json.loads(json.dumps(env.to_dict())))
        self.assertEqual(again.artifact_refs[0].size_bytes, 7)
        self.assertEqual(again.artifact_refs[0].digest, "a" * 64)

    def test_32_negative_size_rejected(self):
        with self.assertRaises(P.ProtocolError) as cm:
            ok_env(artifact_refs=[{"type": "file", "ref": "/tmp/a", "size_bytes": -1}])
        self.assertEqual(cm.exception.code, "BAD_ARTIFACT_REF")

    def test_33_bool_size_rejected(self):
        """True 是 1 的实例：不挡住就会安静地把 size_bytes 写成 1。"""
        with self.assertRaises(P.ProtocolError) as cm:
            ok_env(artifact_refs=[{"type": "file", "ref": "/tmp/a", "size_bytes": True}])
        self.assertEqual(cm.exception.code, "BAD_ARTIFACT_REF")


class TestStateMachine(unittest.TestCase):
    def test_40_happy_path(self):
        s = P.ST_CREATED
        for t in (P.ST_QUEUED, P.ST_DELIVERING, P.ST_DELIVERED, P.ST_STARTED,
                  P.ST_RESPONDED, P.ST_ACKED):
            s = P.next_state(s, t)
        self.assertEqual(s, P.ST_ACKED)

    def test_41_delivery_and_ack_in_one_call(self):
        """通知腿：适配器一次调用就给出合格回执 ⇒ DELIVERING 直接到 ACKED。"""
        self.assertEqual(P.next_state(P.ST_DELIVERING, P.ST_ACKED), P.ST_ACKED)

    def test_42_every_state_listed(self):
        self.assertEqual(len(P.TRANSPORT_STATES), 11)
        for st in P.TRANSPORT_STATES:
            P.allowed_transitions(st)

    def test_43_no_completed_state(self):
        self.assertNotIn("COMPLETED", P.TRANSPORT_STATES)
        self.assertNotIn("TASK_COMPLETED", P.TRANSPORT_STATES)
        self.assertNotIn("READY_TO_RELEASE", P.TRANSPORT_STATES)

    def test_44_forbidden_values_disjoint_from_enums(self):
        for name, group in (("TRANSPORT_STATES", P.TRANSPORT_STATES),
                            ("MESSAGE_TYPES", P.MESSAGE_TYPES)):
            leak = set(group) & set(P.FORBIDDEN_VALUES)
            self.assertFalse(leak, "%s 里混进了项目真相词：%s" % (name, sorted(leak)))

    def test_45_illegal_transition_rejected(self):
        with self.assertRaises(P.ProtocolError) as cm:
            P.next_state(P.ST_CREATED, P.ST_ACKED)
        self.assertEqual(cm.exception.code, "ILLEGAL_TRANSITION")

    def test_46_terminal_states_immutable(self):
        for term in P.TERMINAL_STATES:
            for t in P.TRANSPORT_STATES:
                with self.assertRaises(P.ProtocolError) as cm:
                    P.next_state(term, t)
                self.assertEqual(cm.exception.code, "STALE_EVENT",
                                 "终态 %s 被允许迁到 %s＝迟到事件能翻案" % (term, t))

    def test_47_unknown_state_rejected(self):
        with self.assertRaises(P.ProtocolError) as cm:
            P.next_state(P.ST_CREATED, "DONE")
        self.assertEqual(cm.exception.code, "UNKNOWN_STATE")
        with self.assertRaises(P.ProtocolError) as cm:
            P.next_state("DONE", P.ST_QUEUED)
        self.assertEqual(cm.exception.code, "UNKNOWN_STATE")

    def test_48_retry_path_exists_for_both_legs(self):
        """投递失败只能进 DELIVERY_RETRY/FAILED，不能进"未知"，也不能回到 QUEUED 之外的历史态。"""
        self.assertIn(P.ST_DELIVERY_RETRY, P.allowed_transitions(P.ST_DELIVERING))
        self.assertIn(P.ST_DELIVERY_RETRY, P.allowed_transitions(P.ST_RESPONDED))
        self.assertIn(P.ST_DELIVERING, P.allowed_transitions(P.ST_DELIVERY_RETRY))
        self.assertNotIn(P.ST_QUEUED, P.allowed_transitions(P.ST_DELIVERY_RETRY))

    def test_49_expiry_and_cancel(self):
        self.assertIn(P.ST_EXPIRED, P.allowed_transitions(P.ST_QUEUED))
        self.assertIn(P.ST_CANCELLED, P.allowed_transitions(P.ST_DELIVERING))
        self.assertTrue(P.is_terminal(P.ST_EXPIRED))
        self.assertFalse(P.is_terminal(P.ST_RESPONDED))


class TestIds(unittest.TestCase):
    def test_50_ids_unique_and_shaped(self):
        a, b = P.new_request_id(), P.new_request_id()
        self.assertNotEqual(a, b)
        doc = ok_env().to_dict()
        for f in ("message_id", "request_id", "correlation_id", "session_id", "idempotency_key"):
            self.assertTrue(P._ID_RE.fullmatch(doc[f]), "%s=%r" % (f, doc[f]))

    def test_51_idempotency_key_derivable(self):
        """同种子⇒同键：重投与重复收单靠这个判定，不许每拍换新。"""
        self.assertEqual(P.new_idempotency_key("task-1+1875b95e"),
                         P.new_idempotency_key("task-1+1875b95e"))
        self.assertNotEqual(P.new_idempotency_key("task-1+1875b95e"),
                            P.new_idempotency_key("task-2+1875b95e"))


class TestTransitionTableIntegrity(unittest.TestCase):
    """迁移表本身的完整性，与"迁移是否合法"是两件事。

    这一格不是装饰：加 `DELIVERY_FAILED` 出边时我在同一个字典字面量里留下了两次
    `ST_DELIVERY_FAILED:`，后写覆盖前写 ⇒ 停放态又被当成终态，整套重投判据当场失效，
    而**所有绿测试照绿**。字面量重复键只能从源码层抓，所以这里用 ast 抓。
    """

    SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "q2c", "protocol.py")

    def _literal_keys(self):
        import ast
        with open(self.SRC, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_TRANSITIONS":
                out = []
                for k in node.value.keys:
                    if isinstance(k, ast.Name):
                        out.append(getattr(P, k.id, "<%s>" % k.id))
                    else:
                        out.append(ast.literal_eval(k))
                return out
        self.fail("_TRANSITIONS 赋值找不到（改名要同步这格）")

    def test_52_no_duplicate_literal_keys(self):
        keys = self._literal_keys()
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertFalse(dupes, "迁移表有重复字面键：%s（后写会静默覆盖前写）" % dupes)

    def test_53_every_state_has_a_row(self):
        keys = set(self._literal_keys())
        self.assertEqual(keys, set(P.TRANSPORT_STATES),
                         "状态名单与迁移表不齐：%s" % (keys ^ set(P.TRANSPORT_STATES)))

    def test_54_parked_state_can_move(self):
        for st in P.PARKED_STATES:
            self.assertTrue(P.allowed_transitions(st),
                            "%s 写成停放态却没有出边，等于还是终态" % st)
            self.assertIn(P.ST_DELIVERY_RETRY, P.allowed_transitions(st))
            self.assertIn(P.ST_ACKED, P.allowed_transitions(st),
                          "迟到答复被收回这一格没了")

    def test_55_terminal_states_have_no_outgoing(self):
        for st in P.TERMINAL_STATES:
            self.assertEqual(P.allowed_transitions(st), ())

    def test_56_fail_returns_both_status_and_state(self):
        """同值两键：调用方拿 `status` 还是 `state` 不该读到不同的话。"""
        self.assertEqual(P.ST_ACKED, "ACKED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
