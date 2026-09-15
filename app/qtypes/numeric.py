"""数值 / 表达式题型：prompt、答案抽取与容错比对。"""

import re


def build_numeric_prompt(item: dict) -> str:
    """数值题 prompt（GSM8K / MATH-500 等，答案不是选项字母）。"""
    return (
        "请解答下面的数学题，只输出最终答案（数字或最简表达式），不要输出解题过程。\n\n"
        f"题目：{item['question']}\n\n答案："
    )


def extract_numeric_answer(text: str | None) -> str | None:
    """从模型回复中抽取最终数值答案。

    策略：优先看显式「答案」标记后的内容，再取末尾的数字。
    支持小数、负号、千分位逗号、美元符号、百分号、科学计数法。
    """
    if not text:
        return None
    s = text.strip()
    # 聚焦「答案」标记之后的部分（若有）。注意：答案词后面可能跟冒号/等号，需一并消费，
    # 否则「答案是?」会只匹配「答案」二字，把冒号留在捕获组里。
    m = re.search(r"(?:答案是?|答案|answer(?:\s*is)?)\s*[:：为=]?\s*(.+)$", s, re.IGNORECASE | re.DOTALL)
    if m:
        s = m.group(1).strip()
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?%?", s)
    if not nums:
        return None
    return nums[-1]  # 取最后一个数字（GSM8K 惯例）


def normalize_answer(s) -> str | None:
    """规范化答案字符串，用于容错比对。

    处理：转小写、去掉 LaTeX \\text{...} 包装、去美元/逗号/空格、百分号转小数。
    这样模型的 "Evelyn" 能与标准答案 "\\text{Evelyn}" 匹配。
    """
    if s is None:
        return None
    s = str(s).strip().lower()
    # 去掉 \text{...} 包装（如 \text{evelyn} → evelyn），便于纯文本答案比对
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("%"):
        try:
            return f"{float(s[:-1]) / 100:g}"
        except ValueError:
            return s[:-1]
    return s


def _to_float(s):
    """尝试把字符串转 float，支持分数 a/b；失败返回 None。

    注意：s 可能是 None（模型回复抽不到数字时），需先判空，否则
    re.fullmatch(None) 会抛 "expected string or bytes-like object"。
    """
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        m = re.fullmatch(r"(-?\d+)\s*/\s*(-?\d+)", s)
        if m:
            try:
                return float(m.group(1)) / float(m.group(2))
            except ZeroDivisionError:
                return None
    return None


def extract_answer_span(text: str | None) -> str | None:
    """抽取「答案」标记之后的完整文本（保留表达式结构，供 LaTeX 答案比对）。"""
    if not text:
        return None
    m = re.search(r"(?:答案是?|答案|answer(?:\s*is)?)\s*[:：为=]?\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def numeric_match(predicted_text: str | None, expected: str) -> bool:
    """数值/表达式判分（三层容错）。

    1) 先抽「答案」后的完整文本，规范化后字符串相等（覆盖 LaTeX 表达式答案）；
    2) 再抽数字做规范化字符串相等（覆盖纯数值答案）；
    3) 最后数值容错比对（±1e-4 相对误差，支持分数 a/b 与小数互转）。
    """
    e = normalize_answer(expected)
    if not e:
        return False
    # 1) 完整答案文本（含 LaTeX 结构）
    span = normalize_answer(extract_answer_span(predicted_text))
    if span and span == e:
        return True
    # 2) 抽取数字后的字符串相等
    p = normalize_answer(extract_numeric_answer(predicted_text))
    if p and p == e:
        return True
    # 3) 数值容错
    pf, ef = _to_float(p), _to_float(e)
    if pf is not None and ef is not None:
        return abs(pf - ef) <= 1e-4 * max(1.0, abs(ef))
    return False
