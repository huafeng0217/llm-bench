"""代码题型（LiveCodeBench 式）：竞赛题，沙箱逐条跑 stdin / 函数调用用例。"""

import asyncio

from ..config import SANDBOX_SEM
from .. import sandbox
from ._code import _ask_for_code, _no_code_result


LCB_CASE_TIMEOUT = 6       # 单条用例超时（秒）。对齐官方 lcb_runner 的 6 秒


LCB_TOTAL_BUDGET = 90      # 单题所有用例的总预算，跑超的剩余用例记 SKIP：个别题 45 条用例，


LCB_SANDBOX_TIMEOUT = 130  # 容器级兜底，要比总预算大，好让判分器先把结果打印出来


def is_lcb_item(item: dict) -> bool:
    """LiveCodeBench 题：带公开/私有测试用例字段的竞赛题。"""
    return "private_tests" in item or "public_tests" in item


def build_lcb_prompt(item: dict) -> str:
    """竞赛题 prompt。两种题型要求完全不同，必须分开写：

    - stdin 式（AtCoder）：要一个完整的、从标准输入读到标准输出的程序；
    - 函数式（LeetCode）：题面给了 class Solution 与方法签名，**必须保持名字不变**，
      否则判分时按 func_name 找不到这个函数。
    """
    fn = (item.get("metadata") or {}).get("func_name")
    starter = (item.get("starter_code") or "").strip()
    lines = ["请解决下面的编程竞赛题目，用 Python 3 编写代码。", "", "题目：",
             str(item.get("question") or "").strip(), ""]
    if fn and starter:
        lines += ["题目已给出函数签名，请补全实现。**必须保持类名 Solution 与方法名不变。**", "",
                  "```python", starter, "```", "",
                  "只输出完整的 Python 代码（包含 class Solution 的定义），不要输出任何解释文字。"]
    else:
        lines += ["程序需要从标准输入读取数据，把答案输出到标准输出。", "",
                  "只输出完整的 Python 代码，不要输出任何解释文字。"]
    return "\n".join(lines)


async def run_lcb_item(model_cfg: dict, item: dict, params: dict) -> dict:
    """LiveCodeBench 式代码题：问模型 → 抽代码 → 沙箱逐条跑测试用例。

    判分口径与官方一致：**全部用例通过**才算这题做对。
    """
    from ..lcb import all_tests  # 局部导入：只有真跑 LCB 时才需要

    qid = item.get("question_id") or "?"
    prompt = build_lcb_prompt(item)
    expected = f"{qid} · 通过全部测试用例"
    code, content, budget, tok = await _ask_for_code(model_cfg, prompt, expected, item, params)
    if not code:
        predicted, err = _no_code_result(content, budget)
        return {"ok": 0, "predicted": predicted, "err": err, "raw": content,
                "sbx_ms": 0, "expected": expected, **tok}

    try:
        cases = all_tests(item)
    except Exception as e:  # noqa: BLE001
        return {"ok": 0, "predicted": "测试用例解码失败", "err": str(e)[:300],
                "raw": code, "sbx_ms": 0, "expected": expected, **tok}
    if not cases:
        return {"ok": 0, "predicted": "无可用用例", "err": "该题没有任何测试用例",
                "raw": code, "sbx_ms": 0, "expected": expected, **tok}

    fn_name = (item.get("metadata") or {}).get("func_name") or ""
    async with SANDBOX_SEM:
        sr = await asyncio.to_thread(
            sandbox.run_stdio_tests, code, cases,
            timeout=LCB_SANDBOX_TIMEOUT, case_timeout=LCB_CASE_TIMEOUT,
            budget=LCB_TOTAL_BUDGET, fn_name=fn_name)

    n_pass, n_total = sr["n_pass"], sr["n_total"]
    if sr["passed"]:
        predicted, err = f"全部通过（{n_total} 条）", None
    else:
        predicted = f"通过 {n_pass}/{n_total} 条"
        f = sr["fail"]
        if f:
            kind = {"TLE": "超时", "RE": "运行错误", "WA": "答案错误",
                    "PE": "输出格式错误", "SKIP": "超出总时间预算未执行"}.get(f["verdict"], f["verdict"])
            err = (f"第 {f['idx'] + 1} 条用例{kind}：期望 {f['expected'][:120]!r}，"
                   f"实际 {f['got'][:120]!r}")
            if f.get("stderr"):
                err += f"；{f['stderr'][:150]}"
        else:
            err = sr.get("error") or "判分器未给出逐条结果"
    return {"ok": 1 if sr["passed"] else 0, "predicted": predicted, "err": err,
            "raw": code, "sbx_ms": sr["duration_ms"], "expected": expected, **tok}
