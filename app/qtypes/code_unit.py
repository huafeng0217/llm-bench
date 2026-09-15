"""代码题型（HumanEval 式）：函数补全 + 沙箱跑官方单元测试。"""

import asyncio

from ..config import SANDBOX_SEM
from .. import sandbox
from ._code import _ask_for_code, _no_code_result


def is_code_item(item: dict) -> bool:
    """代码题：同时带 entry_point 与 test 字段（HumanEval / MBPP 属此类）。

    与前两类题的根本区别在**判分方式**：选择题比字符串、数值题做容错比对，
    代码题则是把模型写的代码真的跑起来，看官方单元测试是否全过。
    """
    return bool(item.get("entry_point")) and bool(item.get("test"))


def build_code_prompt(item: dict) -> str:
    """代码题 prompt：给出签名与 docstring，要求补全成完整可运行的函数。

    刻意要求「必须包含函数定义本身」：官方评测是补全式（prompt 直接接模型续写），
    而这里走对话接口，模型很容易只给函数体，导致拼出来的程序没有函数名。
    """
    return (
        "请补全下面的 Python 函数，使其满足 docstring 中的要求。\n"
        "只输出完整的 Python 代码（必须包含函数定义本身），不要输出任何解释文字。\n\n"
        "```python\n" + str(item["question"]).rstrip() + "\n```\n"
    )


async def run_code_item(model_cfg: dict, item: dict, params: dict) -> dict:
    """HumanEval 式代码题：问模型 → 抽代码 → 沙箱跑官方单元测试（全过才算对）。"""
    prompt = build_code_prompt(item)
    expected = f"{item['entry_point']} · 通过全部单元测试"
    code, content, budget, tok = await _ask_for_code(model_cfg, prompt, expected, item, params)
    if not code:
        predicted, err = _no_code_result(content, budget)
        return {"ok": 0, "predicted": predicted, "err": err, "raw": content,
                "sbx_ms": 0, "expected": expected, **tok}

    # 用独立信号量限流：容器比 HTTP 请求重得多，不能按 concurrency 放
    async with SANDBOX_SEM:
        sr = await asyncio.to_thread(sandbox.run_python_tests, code, item["test"], item["entry_point"])
    if sr["passed"]:
        predicted, err = "通过", None
    elif sr["timed_out"]:
        predicted = "超时未通过"
        err = f"沙箱执行超过 {sandbox.DEFAULT_TIMEOUT}s 被终止（可能是死循环）"
    else:
        predicted = f"未通过（exit {sr['exit_code']}）"
        err = ((sr["stderr"] or "").strip()[-400:] or (sr["stdout"] or "").strip()[-200:] or None)
    return {"ok": 1 if sr["passed"] else 0, "predicted": predicted, "err": err,
            "raw": code,  # 明细里存抽出来的代码，比带围栏的原始回复更好看
            "sbx_ms": sr["duration_ms"], "expected": expected, **tok}
