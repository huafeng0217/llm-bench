"""BFCL 单轮函数调用题型：工具 schema 规范化、AST 匹配、展示文本。"""

import json
import re

from ..config import BFCL_DIR


def is_bfcl(benchmark: str) -> bool:
    return (BFCL_DIR / f"{benchmark}.jsonl").exists()


_TYPE_MAP = {"dict": "object", "tuple": "array", "list": "array", "float": "number", "int": "integer"}


def _norm_schema(node):
    """递归把 BFCL 的非标准 JSON Schema 类型转成 OpenAI 兼容的。

    - float → number、int → integer、dict → object、tuple/list → array
    - any → 删掉 type（等价于不限制类型）
    """
    if isinstance(node, dict):
        t = node.get("type")
        if isinstance(t, str):
            if t == "any":
                node.pop("type", None)
            elif t in _TYPE_MAP:
                node["type"] = _TYPE_MAP[t]
        for v in node.values():
            _norm_schema(v)
    elif isinstance(node, list):
        for v in node:
            _norm_schema(v)
    return node


def normalize_tools(func_defs: list) -> tuple:
    """把 BFCL 的 function 定义转成 OpenAI tools 参数格式。

    - 递归把 BFCL 的非标准 schema 类型（dict/float/tuple/any…）转成 OpenAI 兼容类型。
    - 函数名可能是 Python 风格带点号（如 math.factorial），OpenAI 只允许
      ^[a-zA-Z0-9_-]+$，需 sanitize 为下划线，并返回 {sanitized: 原名} 映射
      供评分时还原。

    返回 (tools, name_map)。
    """
    tools, name_map = [], {}
    for f in func_defs:
        original = f["name"]
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", original)
        name_map[safe] = original
        params = _norm_schema(dict(f.get("parameters", {})))
        if not isinstance(params.get("type"), str):  # 空/无类型兜底
            params = {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": safe,
                "description": f.get("description", ""),
                "parameters": params,
            },
        })
    return tools, name_map


def bfcl_messages(question) -> list:
    """BFCL question 是 [[{role, content}, ...]]，取第一条对话链。"""
    msgs = question[0] if isinstance(question, list) and question else question
    return [{"role": m["role"], "content": m["content"]} for m in msgs if isinstance(m, dict)]


def item_question_text(item: dict, fc: bool, mt: bool) -> str:
    """提取用于明细展示的题目文本。multi_turn 会把各轮的 user 请求拼起来。"""
    q = item.get("question")
    if not fc:
        return str(q or "")
    if mt and isinstance(q, list):
        parts = []
        for turn in q:
            if isinstance(turn, list):
                for m in turn:
                    if isinstance(m, dict) and m.get("role") == "user":
                        parts.append(str(m.get("content", "")))
        return " / ".join(parts)
    return bfcl_messages(q)[-1]["content"]


def ast_match(calls: list[dict], ground_truth) -> tuple[bool, str]:
    """AST 匹配：模型调用的 (函数名, 参数) 与标准答案对比。

    ground_truth 格式: [{"func": {"param": [候选值, ...]}}, ...]
    返回 (是否全对, 简要说明)。
    """
    if ground_truth is None:
        # irrelevance：正确行为是拒绝调用任何函数
        ok = len(calls) == 0
        return ok, "拒绝调用" if ok else f"应拒绝但调用了 {len(calls)} 个函数"
    expected = [list(g.keys())[0] for g in ground_truth]
    expected_args = [list(g.values())[0] for g in ground_truth]
    # 调用数量必须一致
    if len(calls) != len(expected):
        got = ", ".join(c["name"] for c in calls) or "无调用"
        return False, f"调用数不符：期望 {len(expected)} 个 ({', '.join(expected)})，实际 {len(calls)} 个 ({got})"

    def val_match(got, cands):
        if not isinstance(cands, list):
            cands = [cands]
        # 值可能是 int/float/str/bool，统一转字符串比较（忽略空白）
        gs = str(got).strip()
        return any(str(c).strip() == gs for c in cands)

    for i, call in enumerate(calls):
        if call["name"] != expected[i]:
            return False, f"函数名不符：期望 {expected[i]}，实际 {call['name']}"
        got_args = call.get("arguments", {})
        # 期望参数是子集即可（多余参数可容忍，官方对 simple 只查必要字段）
        for k, cands in expected_args[i].items():
            if k not in got_args:
                return False, f"缺少参数 {k}"
            if not val_match(got_args[k], cands):
                return False, f"参数 {k} 不符：期望 {cands}，实际 {got_args[k]}"
    return True, "AST 匹配通过"


def bfcl_expected_text(ground_truth) -> str:
    """标准答案的简要文本（用于 eval_items 展示）。"""
    if ground_truth is None:
        return "拒绝调用"
    return "; ".join(f"{list(g.keys())[0]}({', '.join(list(g.values())[0].keys())})" for g in ground_truth)


def bfcl_predicted_text(calls: list[dict]) -> str:
    if not calls:
        return "无调用"
    return "; ".join(f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in calls)
