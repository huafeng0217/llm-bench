"""BBH（BIG-Bench Hard）：23 个「连大模型都做不好」的高难任务，考的是硬推理。

为什么收它
----------
现有推理类（ARC / GPQA）偏"科学知识型"推理，BBH 是另一档：逻辑演绎、因果判断、日期推理、
追踪物体、多步算术…… 这些题**不靠知识量**，而靠多步推理是否严密 —— 正好补上「综合推理」这块。

数据源与题量口径
----------------
用 ``lukaemon/bbh``（BIG-Bench Hard 的社区镜像，test 划分，27 个 config）。
**不是所有子集都能用同一套判分**，所以按题型分成三档（都在这里处理掉，不新写判分器）：

1. **自带选项**的子集（选项就写在 input 里，``Options:`` 或 ``(A) ...`` 两种形态）
   → 解析成 A~J 选项，走现成的选择题判分（与 MMLU 同一条链路）。
2. **二选一**的子集（boolean_expressions 的 True/False、web_of_lies 的 Yes/No 等，
   input 里没写选项）→ 由题目语义补出两个选项再走选择题判分。这是题目本身的取值域，不是编造。
3. **数值题**（object_counting / multistep_arithmetic_two）→ 只给题干，走现成的数值题判分。

``word_sorting``（要输出排好序的整串词）和 ``dyck_languages``（要输出括号序列）没有对应的
判分方式，**明确不收** —— 与其绕过判分，不如少收两个子集。

每条题目的 ``subject`` 记着它来自哪个子集（明细里能看到），方便按任务类型回看错题。
"""
import json
import re
import sys

from .types import Benchmark
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl

DS = "lukaemon/bbh"
SPLIT = "test"

# 有选项的（先按 input 实际形态解析，解析不出来的再落到下面的兜底表）
ALL_SUBSETS = [
    "boolean_expressions", "causal_judgement", "date_understanding", "disambiguation_qa",
    "formal_fallacies", "geometric_shapes", "hyperbaton",
    "logical_deduction_five_objects", "logical_deduction_seven_objects",
    "logical_deduction_three_objects", "movie_recommendation", "navigate",
    "penguins_in_a_table", "reasoning_about_colored_objects", "ruin_names",
    "salient_translation_error_detection", "snarks", "sports_understanding",
    "temporal_sequences", "tracking_shuffled_objects_five_objects",
    "tracking_shuffled_objects_seven_objects", "tracking_shuffled_objects_three_objects",
    "web_of_lies",
    # 数值题：题干是文字、答案是一个数，走数值判分
    "object_counting", "multistep_arithmetic_two",
]
# 明确不收：输出形态没有对应判分方式（见模块说明）
SKIP_SUBSETS = {"word_sorting", "dyck_languages"}
# 数值题：只给题干，走数值判分
NUMERIC_SUBSETS = {"object_counting", "multistep_arithmetic_two"}
# 二选一：input 里没写选项时，用题目本身的取值域补两个选项（不是编造答案）
BINARY_SUBSETS = {
    "boolean_expressions": ("True", "False"),
    "sports_understanding": ("yes", "no"),
    "web_of_lies": ("Yes", "No"),
    "causal_judgement": ("Yes", "No"),
}

LETTERS = [chr(65 + i) for i in range(18)]   # A~R：有的子集真有 18 个选项（18 种颜色）

_DASH_LINE = re.compile(r"^\s*-\s+(.*\S)\s*$", re.M)
_PAREN_LINE = re.compile(r"^\s*\(([A-R])\)\s*(.*\S)\s*$", re.M)
_OPT_HEADER = re.compile(r"^\s*(options?|选项)\s*[:：]?\s*$", re.I)


def parse_options(inp: str):
    """把 input 拆成 (题干, 选项列表)。

    BBH 各子集的题面形态不统一，实测两种：
      ① 末尾一行 ``Options:``，后面每条 ``- xxx``；
      ② 直接 ``(A) xxx`` / ``(B) xxx`` 逐行。
    两种都试，取能解析出 2~10 个选项的那种；都不行就返回 (原文, [])。
    """
    lines = str(inp or "").splitlines()
    for i, ln in enumerate(lines):
        if _OPT_HEADER.match(ln):
            opts = [m.group(1).strip() for m in _DASH_LINE.finditer("\n".join(lines[i + 1:]))]
            if 2 <= len(opts) <= len(LETTERS):
                return "\n".join(lines[:i]).strip(), opts
            break
    found = [(m.start(), m.group(1), m.group(2).strip()) for m in _PAREN_LINE.finditer(str(inp or ""))]
    if 2 <= len(found) <= len(LETTERS):
        return str(inp)[:found[0][0]].strip(), [t for _, _, t in found]
    return str(inp or "").strip(), []


def answer_index(target, opts):
    """把官方 target 映射到选项下标。

    target 可能是 ``(B)`` / ``B`` 这种字母，也可能是选项原文（``No`` / ``True``）——
    后者按**忽略大小写的整串相等**匹配（不做包含匹配，避免 "No" 命中 "Nothing"）。
    """
    t = str(target or "").strip()
    m = re.match(r"^\(?([A-R])\)?$", t)
    if m:
        idx = ord(m.group(1)) - 65
        return idx if idx < len(opts) else None
    for i, o in enumerate(opts):
        if o.strip().lower() == t.lower():
            return i
    return None


def _is_numberish(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", str(s).strip()))


def _fetch_subset(sub: str, attempts: int = 4):
    """拉一个子集；datasets-server 偶发 502/超时，重试几次再认命。

    重试放在这里而不是 _util：只有 BBH 要连续打 25 个子集，被一次瞬时故障打断最亏。
    """
    import time as _time
    last = None
    for i in range(attempts):
        try:
            return fetch_ds_rows(DS, sub, SPLIT)
        except Exception as e:      # noqa: BLE001 - 网络层什么都可能抛
            last = e
            if i + 1 < attempts:
                wait = 2 ** i
                print(f"  {sub}: 第 {i + 1} 次失败（{e}），{wait}s 后重试", file=sys.stderr)
                _time.sleep(wait)
    # 错误文案要能被 t_stored 翻（会落库并被展示），所以用固定句式 + 占位；
    # 具体异常打到 stderr 便于排查，但不进库（它可能很长且不可控）
    # 具体是哪个子集、什么异常：打到 stderr 供下载 CLI 展示；
    # 异常文案本身用**固定句**（会落库并被 t_stored 翻译，动态文本翻不了）
    print(f"  {sub}: 连续 {attempts} 次拉取失败，最后一次：{last}", file=sys.stderr)
    raise RuntimeError("BBH 子集下载失败")


def download_bbh():
    """下载 BBH 的可用子集，整理成统一 jsonl：选择题带 A~R 字段，数值题只有 question/answer。"""
    items, per_subset, skipped_rows = [], {}, 0
    for sub in ALL_SUBSETS:
        rows = _fetch_subset(sub)
        kept = 0
        for r in rows:
            q, opts = parse_options(r.get("input"))
            target = r.get("target")
            if not q:
                skipped_rows += 1
                continue
            # 二选一子集：input 没写选项时按题目取值域补出来
            if len(opts) < 2 and sub in BINARY_SUBSETS:
                opts = list(BINARY_SUBSETS[sub])
            if len(opts) >= 2:
                idx = answer_index(target, opts)
                if idx is None:
                    skipped_rows += 1
                    continue
                item = {"question": q, "answer": LETTERS[idx], "subject": sub}
                for i, text in enumerate(opts):
                    item[LETTERS[i]] = text
            elif sub in NUMERIC_SUBSETS and _is_numberish(target):
                # 数值题：只给题干。答案是数字，交给数值题判分（它有自己的容错比对）
                item = {"question": q, "answer": str(target).strip(), "subject": sub}
            else:
                skipped_rows += 1
                continue
            items.append(item)
            kept += 1
        if kept:
            per_subset[sub] = kept
    if not items:
        raise RuntimeError("BBH 下载结果为空")
    write_jsonl(DATA_DIR / "bbh.jsonl", items)
    detail = "、".join(f"{k} {v}" for k, v in per_subset.items())
    print(f"完成：bbh.jsonl（共 {len(items)} 题，{len(per_subset)} 个子集）", file=sys.stderr)
    print(f"  各子集题量：{detail}", file=sys.stderr)
    print(f"  跳过 {skipped_rows} 行（选项/答案形态无法归一到现有判分）", file=sys.stderr)


ENTRIES = [
    Benchmark(order=26, id='bbh',
        summary='23 个高难推理子集：逻辑演绎、因果、日期、多步算术，靠推理不靠知识量',
        summary_en="Logic, causality, dates, arithmetic — reasoning, not knowledge",
        name='BBH（BIG-Bench Hard）',
        name_en='BBH (BIG-Bench Hard)',
        category='综合推理',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        description='BIG-Bench Hard：从 BIG-Bench 里挑出的 23 个「大模型明显低于人类」的任务，'
                    '涵盖逻辑演绎（logical_deduction）、因果判断（causal_judgement）、日期推理'
                    '（date_understanding）、形式谬误（formal_fallacies）、追踪物体'
                    '（tracking_shuffled_objects）、多步算术（multistep_arithmetic_two）、'
                    '布尔表达式（boolean_expressions）等。和 ARC / GPQA 的区别：那些偏「知道不知道」，'
                    'BBH 考的是**多步推理是否步骤严密**，所以更难靠知识量堆分。'
                    '收录方式：自带选项的子集走选择题判分，True/False、Yes/No 这类二选一按题目取值域'
                    '补两个选项，纯数字题（数数、多步算术）走数值判分；word_sorting（要输出排序后的'
                    '整串词）和 dyck_languages（要输出括号序列）没有对应判分方式，**未收录**。'
                    '每题在明细里标注它来自哪个子集。',
        description_en="BIG-Bench Hard: the 23 BIG-Bench tasks where models were clearly below "
                       "human level, covering logical deduction, causal judgement, date "
                       "understanding, formal fallacies, tracking shuffled objects, multi-step "
                       "arithmetic, boolean expressions and more. Unlike ARC / GPQA, which lean on "
                       "\"do you know it\", BBH tests whether **multi-step reasoning is airtight**, "
                       "so it is harder to buy with knowledge. How it is scored here: subsets that "
                       "ship options use the multiple-choice grader; binary ones (True/False, "
                       "Yes/No) get their two options from the task's own answer domain; pure "
                       "numeric ones (counting, multi-step arithmetic) use the numeric grader. "
                       "word_sorting (emit a sorted word list) and dyck_languages (emit a bracket "
                       "sequence) have no matching grader and are **not included**. Each item is "
                       "tagged with the subset it came from.",
        source='https://arxiv.org/abs/2210.09261',
        label='BBH（23 个高难推理子集，选择题 + 数值题）',
        label_en="BBH (23 hard reasoning subsets, multiple-choice + numeric)",
        download=download_bbh),
]
