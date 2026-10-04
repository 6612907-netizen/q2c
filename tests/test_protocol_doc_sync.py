#!/usr/bin/env python3
"""反双真源：PROTOCOL.md 里列的名单必须与 q2c/protocol.py 逐字一致。

为什么单独一格：文档与代码一旦各写一份枚举，漂掉的那一份永远不会自己红。
这一格把"文档说的"当断言对象读，所以：
  · 代码加了取值、文档没写 ⇒ 红（文档过期，读者会按旧契约实现适配器）
  · 文档加了取值、代码没有 ⇒ 红（协议被纸面扩大）
  · 顺序漂了 ⇒ 红（§2 的字段顺序就是信封的冻结顺序）
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from q2c import protocol as P  # noqa: E402

DOC = os.path.join(ROOT, "PROTOCOL.md")


def _read_doc():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def _section(md, prefix):
    """取 "## <prefix>" 这一节，到下一个 "## " 之前（三级标题算同一节，除非另给 cut）。"""
    lines = md.splitlines()
    out, inside = [], False
    for ln in lines:
        if ln.startswith("## "):
            if inside:
                break
            inside = ln[3:].startswith(prefix)
            continue
        if inside:
            out.append(ln)
    if not inside:
        raise AssertionError("PROTOCOL.md 里找不到小节 %r" % prefix)
    return "\n".join(out)


def _sub(md, prefix, stop):
    """取 "### <prefix>" 起、到下一个 "## " 或 "### <stop>" 之前。"""
    pat = re.compile(r"^### %s" % re.escape(prefix))
    lines = md.splitlines()
    out, inside = [], False
    for ln in lines:
        if inside and (ln.startswith("## ") or ln.startswith("### ")):
            break
        if pat.match(ln):
            inside = True
            continue
        if inside:
            out.append(ln)
    if not inside:
        raise AssertionError("PROTOCOL.md 里找不到子节 %r" % prefix)
    return "\n".join(out)


def _fenced_blocks(text):
    """配对取代码块，允许 ```lang 开头（不带这个兼容时，带语言的块会让后面所有块错位）。"""
    return re.findall(r"```[A-Za-z0-9_+.\-]*\n(.*?)```", text, re.S)


def _all_upper_tokens(block):
    out = []
    for ln in block.splitlines():
        for tok in re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", ln):
            if tok not in out:
                out.append(tok)
    return out


def _line_head_tokens(block):
    """逐行取**行首**那枚大写常量。

    名单里的每一行都长这样：`TOKEN     一句中文说明`。只取行首，说明里出现的
    别的缩写（例如 `SECURITY.md`、`CLI`）就不会被当成协议取值混进名单。
    """
    out = []
    for ln in block.splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9_]{2,})\b", ln)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _backtick_tokens(text):
    return re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text)


class TestDocSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = _read_doc()

    def test_01_protocol_version(self):
        m = re.search(r"^\s*```\n\s*(q2c/\d+)", _section(self.md, "1."), re.S | re.M)
        self.assertTrue(m, "§1 没把版本号单独放在代码块里")
        self.assertEqual(m.group(1), P.PROTOCOL_VERSION)

    def test_02_envelope_fields_exact_order(self):
        sec = _section(self.md, "2.")
        rows = re.findall(r"^\|\s*`([a-z_]+)`\s*\|", sec, re.M)
        self.assertEqual(rows, list(P.ENVELOPE_FIELDS),
                         "§2 表格的字段名单/顺序与代码不一致（doc=%d code=%d）"
                         % (len(rows), len(P.ENVELOPE_FIELDS)))

    def test_03_required_subset(self):
        sec = _section(self.md, "2.")
        required_rows = re.findall(r"^\|\s*`([a-z_]+)`\s*\|\s*✅\s*\|", sec, re.M)
        self.assertEqual(sorted(required_rows), sorted(P.REQUIRED_ENVELOPE_FIELDS),
                         "§2 标了必填的字段集合与代码不一致")
        self.assertTrue(set(required_rows) <= set(P.ENVELOPE_FIELDS))

    def test_04_message_types(self):
        sec = _section(self.md, "3.")
        listed = []
        for tok in re.findall(r"^\|\s*`(HANDOFF_[A-Z]+)`\s*\|", sec, re.M):
            if tok not in listed:
                listed.append(tok)
        self.assertEqual(listed, list(P.MESSAGE_TYPES))

    def test_05_transport_states(self):
        sec = _section(self.md, "4.")
        # 只取 §4 正文（不含 §4.1 失败归类）的第一枚代码块
        head = sec.split("### ")[0]
        blocks = _fenced_blocks(head)
        self.assertTrue(blocks, "§4 没给出状态清单的代码块")
        listed = _line_head_tokens(blocks[0])
        self.assertEqual(listed, list(P.TRANSPORT_STATES),
                         "§4 的状态名单与代码不一致（doc=%s code=%s）" % (listed, P.TRANSPORT_STATES))
        self.assertEqual(len(listed), 11, "任务书 §4 冻的就是 11 枚，加一枚要升主版本")

    def test_06_terminal_states_agree(self):
        sec = _section(self.md, "4.")
        m = re.search(r"终态只有三枚：`([^`\n]+)`／`([^`\n]+)`／`([^`\n]+)`", sec)
        self.assertTrue(m, "§4 没写清哪几枚是终态")
        listed = [m.group(i) for i in range(1, 4)]
        self.assertEqual(sorted(listed), sorted(P.TERMINAL_STATES))
        self.assertIn("DELIVERY_FAILED", P.PARKED_STATES,
                      "停放态名单漂了：文档说它是停放态，代码得跟着")
        self.assertNotIn("DELIVERY_FAILED", P.TERMINAL_STATES)

    def test_07_failure_classes(self):
        sec = _sub(self.md, "4.1", "5")
        blocks = _fenced_blocks(sec)
        self.assertTrue(blocks, "§4.1 没给出失败归类的代码块")
        listed = _line_head_tokens(blocks[0])
        self.assertEqual(listed, list(P.FAILURE_CLASSES))
        # 行数与名单数必须相等：只靠"读得到的那些行"来断言，等于给死判据开门
        body = [ln for ln in blocks[0].splitlines() if ln.strip()]
        self.assertEqual(len(body), len(listed),
                         "§4.1 有 %d 行但只认出 %d 枚取值（说明行首约定被破坏，取值在漂出名单）"
                         % (len(body), len(listed)))

    def test_08_forbidden_values(self):
        sec = _section(self.md, "0.")
        blocks = _fenced_blocks(sec)
        self.assertTrue(blocks, "§0 没列出被排除的取值")
        listed = _all_upper_tokens(blocks[0])
        self.assertEqual(sorted(set(listed)), sorted(P.FORBIDDEN_VALUES))
        # 反向再钉一次：这些词不许以任何形式活进枚举里
        for tok in listed:
            self.assertNotIn(tok, P.TRANSPORT_STATES)
            self.assertNotIn(tok, P.MESSAGE_TYPES)

    def test_09_artifact_types(self):
        sec = _section(self.md, "7.")
        blocks = _fenced_blocks(sec)
        self.assertTrue(blocks, "§7 没列出产物类型")
        listed = [t for t in re.split(r"\s+", blocks[0].strip()) if t]
        self.assertEqual(listed, list(P.ARTIFACT_TYPES))

    def test_10_ack_rules_counted(self):
        """§4.2 列了几道，实现就得有几道；这里只钉文档侧的条数与判据名。"""
        sec = _sub(self.md, "4.2", "5")
        items = re.findall(r"^\d+\.\s", sec, re.M)
        self.assertEqual(len(items), 6, "ACK 五道闸＋绑定串一道＝6 条，条数变了要同步实现")

    def test_11_trace_questions_counted(self):
        sec = _section(self.md, "8.")
        blocks = _fenced_blocks(sec)
        self.assertTrue(blocks)
        q = [t for t in re.split(r"[·\s]+", blocks[0].strip()) if t]
        self.assertEqual(len(q), 9, "trace 要能回答的是九问")

    def test_12_no_stale_project_words_in_doc_enums(self):
        """文档正文里出现 COMPLETED／ACCEPTED 只许在"排除"与"历史对照"两处，不许当状态用。"""
        sec4 = _section(self.md, "4.")
        head = sec4.split("### ")[0]
        for line in head.splitlines():
            if line.startswith("```") or not line.strip():
                continue
            if "COMPLETED" in line:
                self.assertIn("没有", line,
                              "§4 里 COMPLETED 只许出现在「没有 COMPLETED」那句：%r" % line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
