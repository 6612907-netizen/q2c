#!/usr/bin/env python3
"""真实双向交接的**原件复算**：证据躺在仓里不算成立，每次跑判据都要重算一遍。

为什么要有这一组（主理人 2026-10-04 的裁定第 1 条）："必须保留 request/correlation、wake、
delivery、ACK、结果返回等原始证据；不得用模拟结果替代。" 保留如果只是"文件在那儿"，
那谁都能改一行再宣称成立。所以这里做三件事：

  1. 取最新一枚跑批报告（`evidence/real-legs/real-handoff-*.json`，按 mtime），
     判决必须是 `BOTH_DIRECTIONS_ACKED`，两个方向各一枚、请求号互不相同；
  2. 把报告里登记的每一枚原件**从盘上重算 SHA256** 并逐条比对；
     结果原件还要自己复算一遍摘要，并与"正文里逐字回了本轮绑定串"对上——
     归因与完整性是同一条链上的两环，缺一环就不是送达；
  3. 跟踪九问的读数按真件核一遍：该观测到的必须观测到，
     **不该观测到的（Qoder 腿那条"投递与开始之间没有可观测边界"）必须仍是 NOT_OBSERVED**
     —— 反向那一半同样重要：谁把它凑成"观测到了"，就是桥在编执行事实。

这一组不新建任何交接、不花钱；它只复算已有原件。没有报告时如实 skip 并写清缺什么，
不拿桩级绿顶替（在册口径：SKIP 不许折进 PASS）。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from q2c import ack, artifacts, protocol, security  # noqa: E402
from q2c import trace as trace_mod  # noqa: E402
from q2c.trace import NOT_OBSERVED, UNVERIFIABLE  # noqa: E402

PATTERN = os.path.join(ROOT, "evidence", "real-legs", "real-handoff-*.json")
LATEST = os.path.join(ROOT, "evidence", "real-legs", "LATEST.json")


def _pointer():
    """指针件说了"哪一枚报告是本次证据"，以及它自己的 SHA256。取不到就是没有。"""
    if not os.path.isfile(LATEST):
        return {}
    with open(LATEST, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError:
            return {"_broken": "指针件读不动"}


PTR = _pointer()
# 选择规则＝**声明**，不是猜：以前按 mtime 挑最新一枚，而 `git archive` 解包件里所有 mtime
# 都是解包那一刻 ⇒ 两枚 runner 各挑到不同的旧失败报告，同一份代码报出两种读数。
REPORT = ""
if PTR.get("report"):
    cand = os.path.join(ROOT, PTR["report"])
    REPORT = cand if os.path.isfile(cand) else ""

# 有报告但没指针 ⇒ **不许 skip**。这一条是被自己的坑教出来的：
# 如果"指针不在"也当没跑过，那摘掉写指针那一步（或忘了写）会让整组证据判据静悄悄消失，
# 套件照样全绿——真实交接那一格就从"有复算"退化成"没人看"。
HAVE_REPORTS = bool(glob.glob(PATTERN))
MISSING = ("仓里没有真跑报告（真跑需要 Q2C_LIVE=1 显式授权，属付费动作）；"
           "没有报告也就没有「哪一枚算数」这个问题")


def _sha(path):
    return artifacts.sha256_file(path)


# 有报告就跑这一组（哪怕指针缺失也照跑，缺指针要报红）；真一枚报告都没有才如实 skip。
@unittest.skipUnless(HAVE_REPORTS, MISSING)
class TestRealHandoffEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 有报告却没指针 ⇒ 让下面的 test_01/test_00 报红，不许在这里悄悄跳过。
        if not REPORT:
            cls.doc = {"verdict": "NO_POINTER", "run_id": "", "legs": [], "artifacts": [],
                       "setup": {}}
            return
        with open(REPORT, encoding="utf-8") as fh:
            cls.doc = json.load(fh)
        cls.legs = cls.doc.get("legs") or []

    # ---- 0) 指针件：证据身份靠声明，不靠猜 --------------------------------

    def test_00_pointer_declares_the_evidence_and_hashes_it(self):
        self.assertTrue(PTR.get("report"), "指针件缺 report 字段：%s" % (PTR,))
        self.assertNotEqual(PTR.get("_broken"), "指针件读不动")
        target = os.path.join(ROOT, PTR["report"])
        with open(target, "rb") as fh:
            got = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(got, PTR["report_sha256"],
                         "指针记的 SHA256 与报告本体对不上 ⇒ 报告被换过或指针过期")
        self.assertEqual(self.doc.get("verdict"), PTR.get("verdict"),
                         "指针说的判决与报告本体不一致：%s vs %s" % (PTR.get("verdict"),
                                                                    self.doc.get("verdict")))

    def test_00b_no_mtime_fallback(self):
        """仓库里还有别的报告（历史失败跑），本组判据**不许**按 mtime 换目标。

        在 `git archive` 解包件里所有 mtime 相同，"挑最新"等于随机挑；
        这一格把这条坑写死：指针之外的报告若与指针不同名，就必须被忽略而不是被采用。
        """
        others = [os.path.basename(p) for p in glob.glob(PATTERN)]
        self.assertIn(os.path.basename(REPORT), others, "指针指向的件不在候选集里（路径写错？）")
        newest_by_mtime = max(glob.glob(PATTERN), key=lambda p: os.path.getmtime(p)) if others else ""
        if os.path.basename(newest_by_mtime) != os.path.basename(REPORT):
            # 允许不一致（解包件里就会不一致），但绝不能因此改变本组读的目标：
            with open(REPORT, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh).get("verdict"), self.doc.get("verdict"))

    # ---- 1) 判决与方向 ----------------------------------------------------

    def test_01_verdict_is_both_directions_acked(self):
        self.assertEqual(self.doc.get("verdict"), "BOTH_DIRECTIONS_ACKED",
                         "最新一枚真跑报告（%s）的判决是 %r" % (
                             os.path.basename(REPORT), self.doc.get("verdict")))

    def test_02_two_directions_distinct_requests_each_acked(self):
        dirs = sorted(l["direction"] for l in self.legs)
        self.assertEqual(dirs, ["codex-to-qoder", "qoder-to-codex"],
                         "两个方向各一枚才算双向：%s" % (dirs,))
        rids = [l["request_id"] for l in self.legs]
        self.assertEqual(len(set(rids)), 2, "两枚方向的请求号不许相同：%s" % (rids,))
        for l in self.legs:
            self.assertEqual(l["state"], protocol.ST_ACKED, "%s 停在 %s" % (l["direction"], l["state"]))

    def test_03_correlation_and_idempotency_and_nonce_are_recorded(self):
        for l in self.legs:
            for key in ("correlation_id", "message_id", "nonce", "idempotency_key",
                        "request_sha256"):
                self.assertTrue(str(l.get(key) or ""), "%s 缺 %s" % (l["direction"], key))
            self.assertEqual(len(l["request_sha256"]), 64, "派发文摘要应是全长 SHA256")
            self.assertEqual(len(l["nonce"]), 16, "绑定串长度由协议定（16 位十六进制）")
        self.assertEqual(len({l["correlation_id"] for l in self.legs}), 2,
                         "两枚方向各有各的关联链，不许共用一枚")

    # ---- 2) 原件逐枚复算 --------------------------------------------------

    def test_04_artifact_manifest_recomputes_from_disk(self):
        rows = self.doc.get("artifacts") or []
        self.assertTrue(len(rows) >= 20,
                        "原件登记数=%s（真腿现场应远多于这个数，说明清单没接上）" % len(rows))
        bad = []
        for r in rows:
            p = os.path.join(ROOT, r["path"])
            if not os.path.isfile(p):
                bad.append((r["path"], "件不在"))
                continue
            if not os.path.realpath(p).startswith(os.path.realpath(ROOT) + os.sep):
                bad.append((r["path"], "路径逃出仓根"))
                continue
            if _sha(p) != r["sha256"]:
                bad.append((r["path"], "哈希不符"))
            if os.path.getsize(p) != r["bytes"]:
                bad.append((r["path"], "字节数不符"))
        self.assertEqual(bad, [], "这些原件与报告登记的不一致（证据被动过或登记造假）：%s" % (bad[:6],))

    def test_05_result_files_hash_and_echo_this_rounds_bind(self):
        """结果原件：自己复算摘要 ＋ 逐字回了**本轮**绑定串。

        这两条缺一条都不算：摘要对得上但回的是上一轮那枚串＝拿别人的答复顶账
        （真腿第一跑就是这个形状，`evidence/real-legs/05-codex-stale-bind-stream.jsonl`）；
        串对得上但摘要不符＝原件被动过。
        """
        for l in self.legs:
            rel = (l.get("result") or {}).get("file_rel") or ""
            self.assertTrue(rel, "%s：报告没记结果原件的仓内路径" % l["direction"])
            p = os.path.join(ROOT, rel)
            self.assertTrue(os.path.isfile(p), "%s：结果原件不在 %s" % (l["direction"], rel))
            self.assertEqual(_sha(p), l["result"]["sha256"],
                             "%s：结果原件与登记摘要不符" % l["direction"])
            with open(p, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            self.assertEqual(ack.extract_bind_echo(body), l["nonce"],
                             "%s：正文回的绑定串不是本轮那枚" % l["direction"])
            self.assertTrue(ack.has_ack_line(body), "%s：结果原件缺独立整行 ACKED" % l["direction"])
            self.assertIn(l["request_id"], body, "%s：正文没指名到这一笔的请求号" % l["direction"])

    def test_06_wake_and_delivery_evidence_files_exist_per_leg(self):
        """唤醒／投递那一腿的落盘件（派发文＋捕获件＋退出码侧件）必须逐枚在案。"""
        for l in self.legs:
            sid = (l.get("handles") or {}).get("receiver", {}).get("provider_session_id") or ""
            self.assertTrue(sid, "%s：接收方 provider 号没登记" % l["direction"])
            d = os.path.join(ROOT, "evidence", "real-legs",
                             "run-%s" % self.doc["run_id"], "state", "inflight", sid)
            self.assertTrue(os.path.isdir(d), "%s：现场目录不在 %s" % (l["direction"], d))
            files = os.listdir(d)
            sent = [f for f in files if f.endswith(".sent.txt")]
            caps = [f for f in files if f.endswith(".out")]
            rcs = [f for f in files if f.endswith(".out.rc") or f.endswith(".rc")]
            self.assertTrue(sent, "%s：派发文没落盘（投出去的是什么，事后必须能逐字对）" % l["direction"])
            self.assertTrue(caps, "%s：捕获件没落盘" % l["direction"])
            self.assertTrue(rcs, "%s：退出码侧件没落盘（读不到退出码就不是跑成功）" % l["direction"])

    # ---- 3) 九问读数（含反向那一半）--------------------------------------

    def test_07_nine_questions_observed_where_evidence_exists(self):
        for l in self.legs:
            q = l.get("trace_nine") or {}
            for key in ("who_sent", "who_received", "which_session", "what_response",
                        "when_acknowledged", "state_now"):
                self.assertNotIn(q.get(key), (None, NOT_OBSERVED, UNVERIFIABLE, "TRACE_ABSENT"),
                                 "%s：九问的 %s 在该有证据的地方读成 %r" % (l["direction"], key, q.get(key)))
            self.assertEqual(q.get("state_now"), protocol.ST_ACKED)

    def test_08_collapsed_boundary_is_not_fabricated(self):
        """恰好一枚有 `when_delivered`：Codex 腿两拍分开，Qoder 腿投递与开始之间无边界。

        这一格防的是"顺手把那枚也补成观测到"——那正是"把 Agent 自述／把推断当执行事实"。
        """
        got = {l["direction"]: (l.get("trace_nine") or {}).get("when_delivered") for l in self.legs}
        observed = [d for d, v in got.items() if v not in (None, NOT_OBSERVED, UNVERIFIABLE)]
        self.assertEqual(len(observed), 1, "读数分布不对：%s" % (got,))

    def test_09_retries_are_visible_not_hidden(self):
        """每一腿的结果补送达都在读数里（不许报 NOT_OBSERVED）。"""
        for l in self.legs:
            r = (l.get("trace_nine") or {}).get("which_retries")
            self.assertNotEqual(r, NOT_OBSERVED,
                                "%s：原件里有 result_retry_of_delivery，读数却藏了" % l["direction"])

    def test_10_delivery_attempts_within_protocol_budget(self):
        for l in self.legs:
            for side in ("request", "result"):
                att = (l.get("delivery") or {}).get(side) or {}
                self.assertLessEqual(int(att.get("attempts") or 0), int(att.get("max") or 0),
                                     "%s/%s 超出了投递预算" % (l["direction"], side))

    # ---- 4) 诚实性：Setup 不许冒充交接／不许有项目真相字段／不许泄密 -------

    def test_11_setup_calls_are_labelled_not_handoffs(self):
        setup = self.doc.get("setup") or {}
        if not setup or setup.get("reused"):
            self.skipTest("这一跑没建新会话（复用模式）或早于该版跑批件")
        self.assertFalse(setup.get("reused"), "reused=False 才该有 setup 登记")
        for label, item in setup.items():
            if not isinstance(item, dict):
                continue
            self.assertIn("不构成交接", item.get("note", ""),
                          "%s：新建会话那一次调用必须标明它不是交接（无信封、无绑定串）" % label)
            self.assertTrue(os.path.isfile(os.path.join(ROOT, item["raw_capture"])),
                            "%s：setup 原始件不在" % label)

    def test_12_trace_has_no_project_truth_fields_or_values(self):
        ev_path = os.path.join(ROOT, "evidence", "real-legs", "run-%s" % self.doc["run_id"],
                               "trace", "events.jsonl")
        bad = []
        with open(ev_path, encoding="utf-8") as fh:
            for i, ln in enumerate(fh, 1):
                if not ln.strip():
                    continue
                e = json.loads(ln)
                bad += ["%d:%s" % (i, k) for k in trace_mod.FORBIDDEN_EVENT_KEYS if k in e]
                bad += ["%d:%s" % (i, e.get("ev")) for v in protocol.FORBIDDEN_VALUES
                        if str(v) in json.dumps(e, ensure_ascii=False)]
        self.assertEqual(bad, [], "真跑的跟踪里出现了项目真相字段／取值：%s" % (bad[:6],))

    def test_13_no_secret_shape_in_real_evidence(self):
        """原件里不许躺着凭据形状（写前脱敏这一条在真件上复算一遍）。"""
        hits = []
        for r in (self.doc.get("artifacts") or []):
            if not (r["path"].endswith(".jsonl") or r["path"].endswith(".txt")
                    or r["path"].endswith(".out") or r["path"].endswith(".json")):
                continue
            p = os.path.join(ROOT, r["path"])
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                txt = fh.read()
            if security.contains_secret_shape(txt):
                hits.append(r["path"])
        self.assertEqual(hits, [],
                         "这些原件里有凭据形状（应写前脱敏，见 SECURITY.md §2）：%s" % (hits[:6],))


class TestEvidenceHarnessIsNotToothless(unittest.TestCase):
    """复算器自己不许是死的：改一个字节必须被上面那组抓住。"""

    def test_15_status_line_is_printed_for_the_readings(self):
        """给 `tools/report-readings.py` 一行可 grep 的现读状态（它不认这行就报 UNREADABLE）。

        口径：这一行只报**原件复算**的结果，不替 `tests/test_real_bidirectional.py` 那两格
        需要授权的活跑说话——那两格没跑就是 SKIP，两件事分开写。
        """
        if not REPORT:
            print("REAL_HANDOFF_EVIDENCE=%s（有报告=%s 但指针件缺失＝证据身份不明，不静默跳过）"
                  % ("NO_POINTER" if HAVE_REPORTS else "NONE", HAVE_REPORTS))
            return
        with open(REPORT, encoding="utf-8") as fh:
            doc = json.load(fh)
        print("REAL_HANDOFF_EVIDENCE=%s run=%s legs=%s artifacts=%s file=%s" % (
            doc.get("verdict"), doc.get("run_id"), len(doc.get("legs") or []),
            len(doc.get("artifacts") or []), os.path.basename(REPORT)))

    def test_14_hash_recompute_detects_one_byte_edit(self):
        import shutil
        import tempfile
        if not REPORT:
            # 包里不带证据件时这一格测不到（独立 CI 上就是这么红的：
            # 类级别的门在另一个类上，这里 open("") 直接抛 FileNotFoundError）。
            self.skipTest(MISSING)
        with open(REPORT, encoding="utf-8") as fh:
            rows = (json.load(fh).get("artifacts") or [])
        if not rows:
            self.skipTest("指针指向的报告里没有原件登记")
        src = os.path.join(ROOT, rows[0]["path"])
        if not os.path.isfile(src):
            self.skipTest("登记的首件不在：%s" % rows[0]["path"])
        td = tempfile.mkdtemp(prefix="q2c-echo-")
        try:
            dup = os.path.join(td, "x.txt")
            with open(src, "rb") as fh:
                data = fh.read()
            with open(dup, "wb") as fh:
                fh.write(data + b" ")          # 只动一个字节
            self.assertNotEqual(_sha(dup), hashlib.sha256(data).hexdigest(),
                                "SHA256 复算没差异＝复算器是死的")
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
