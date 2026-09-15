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


def extract_answer(text: str | None) -> str | None:
    """从模型回复中抽取选项字母（A-J）。先看显式'答案'，再找首个独立字母。"""
    if not text:
        return None
    head = text.strip()[:120]
    m = re.search(r"(?:答案是|答案[:：为]|选项是?|answer is|answer[:：])\s*\(?([A-J])\b", head, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<![A-Za-z])([A-J])(?![A-Za-z])", head)
    return m.group(1) if m else None
