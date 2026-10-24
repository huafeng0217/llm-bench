"""选择题题型：prompt 与答案抽取。"""

import re

from ..config import CHOICES  # noqa: F401


def is_choice_item(item: dict) -> bool:
    """判断题目是否选择题（含 A/B/C… 选项字段）。数值题只有 question/answer，无选项。"""
    return any(item.get(c) for c in CHOICES)


def build_prompt(item: dict) -> str:
    """选择题 prompt：选项按题目实际有的（4 选 1 到 10 选 1 均可）。"""
    lines = [
        "以下是一道单项选择题。请只回答正确选项的字母，不要输出任何其他内容。",
        "",
        f"题目：{item['question']}",
    ]
    for c in CHOICES:
        if item.get(c):
            lines.append(f"{c}. {item[c]}")
    lines += ["", "答案："]
    return "\n".join(lines)


def extract_answer(text: str | None, allowed: str | None = None) -> str | None:
    """从模型回复中抽取选项字母。

    ``allowed`` 是这道题**真实存在**的选项字母（由调用方按题面给出）。
    必须传入的原因：选项数最多到 18（A~R），放宽字母范围后，模型回复里
    "I think ..." 的 I 会被当成选项 I —— 那是误判，不是模型答错。
    不传时按旧行为只认 A~J（直接调用/单测用）。
    """
    if not text:
        return None
    pool = "".join(dict.fromkeys((allowed or "ABCDEFGHIJ").upper()))
    if not pool:
        return None
    head = text.strip()[:120]
    cls = f"[{re.escape(pool)}]"
    m = re.search(r"(?:答案是|答案[:：为]|选项是?|answer is|answer[:：])\s*\(?" + cls + r"\b", head, re.IGNORECASE)
    if m:
        return m.group(0)[-1].upper()
    m = re.search(r"(?<![A-Za-z])(" + cls + r")(?![A-Za-z])", head)
    return m.group(1).upper() if m else None
