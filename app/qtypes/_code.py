"""代码题公共层：从模型回复里抽可运行代码，以及带额度阶梯的「问代码」。

单独成模块是为了断开一个环：`_ask_for_code` 既要调模型（llm）又要抽代码（code_unit），
把它塞进任一侧都会形成 llm <-> code_unit 的循环依赖。"""

import re

from ..llm import CODE_TOKEN_LADDER, chat_with_retry  # noqa: F401


def code_from_completion(item: dict, completion: str) -> str:
    """把一段「续写」补成完整可运行代码。

    HumanEval 是**补全式**评测：题目 question 是「函数签名 + docstring」，
    模型（和官方参考解法）给出的只是**缩进的函数体**，两者拼起来才是一个函数。
    所以这里若发现续写里没有目标函数的定义，就把题目里的签名前置。

    注意：**不能对续写做 strip()** —— 那会把函数体的缩进一起剥掉，
    拼出来就成了顶层的 for/return，直接语法错误或语义全错。
    """
    ep = item.get("entry_point") or ""
    code = completion or ""
    # 只有「函数补全式」的题（HumanEval / MBPP）才需要补签名。
    # 竞赛题（LiveCodeBench）要的是完整程序，把题面拼上去只会毁掉代码 ——
    # 所以这里必须先判断 entry_point 是否存在，不能无条件前置。
    if ep and not re.search(rf"^def\s+{re.escape(ep)}\s*\(", code, re.MULTILINE):
        code = str(item.get("question") or "").rstrip() + "\n" + code.lstrip("\n")
    return code


def extract_code(text: str | None, item: dict) -> str | None:
    """从模型回复里抽出可运行的 Python 代码。

    逐级放宽，每个候选都用 compile() 做语法校验，取第一个能编译过的：
      1) ```python 围栏内的内容（含目标函数的优先、较长的优先）
      2) 未闭合的围栏 —— 模型被 max_tokens 截断时会漏掉收尾的 ```
      3) 完全没有围栏时，从第一个 def/class/import/from 截到最后

    只有「模型只给了函数体」时会补上题目里的签名（见 code_from_completion）。
    """
    if not text:
        return None
    ep = item.get("entry_point") or ""
    # 围栏里的内容才是模型明确给出的代码，优先试；
    # 「从第一个 def/import 截到最后」只是兜底 —— 它常常把收尾的解释文字
    # 甚至另一个围栏的 ``` 一起带进来。
    fences = [m.group(1) for m in
              re.finditer(r"```[ \t]*(?:python|py)?[0-9]?[ \t]*\n(.*?)(?:```|\Z)", text, re.DOTALL | re.IGNORECASE)]
    # 含目标函数的候选优先，其次取更长的（更可能是完整实现而非片段）
    fences.sort(key=lambda c: (bool(ep) and f"def {ep}" not in c, -len(c)))
    others = []
    m = re.search(r"^[ \t]*(?:def |class |import |from )", text, re.MULTILINE)
    if m:
        others.append(text[m.start():])
    for c in fences + others:
        code = code_from_completion(item, c.strip("\n"))  # 只去空行，保留缩进
        if not code.strip():
            continue
        try:
            compile(code, "<candidate>", "exec")
            return code
        except SyntaxError:
            continue
    return None


async def _ask_for_code(model_cfg: dict, prompt: str, expected: str, item: dict,
                        params: dict) -> tuple:
    """带额度阶梯地问模型要代码。

    先按基准额度问；**只有被截断且没抽到代码时**才放大重问（见 CODE_TOKEN_LADDER）。
    放大后若服务商不接受该 max_tokens（400），退回上一次的结果，不让整题失败。

    返回 (code, content, 用到的最大额度, token 统计)。
    """
    base = params["max_tokens"]
    budgets = []
    for mult in CODE_TOKEN_LADDER:
        b = base * mult
        if b not in budgets:
            budgets.append(b)

    code, content = None, None
    ptok = ctok = lat = 0
    for budget in budgets:
        try:
            resp = await chat_with_retry(model_cfg, prompt, expected, dict(params, max_tokens=budget))
        except Exception:  # noqa: BLE001
            if content is None:
                raise  # 第一次就失败 → 交给上层记为题目错误
            break      # 放大额度后失败（例如服务商不支持）→ 用上一次的结果
        ptok += resp["prompt_tokens"]
        ctok += resp["completion_tokens"]
        lat += resp["latency_ms"]
        content = resp["content"]
        code = extract_code(content, item)
        if code or "max_tokens 截断" not in (content or ""):
            break  # 抽到代码，或压根没被截断（模型就是没写代码）→ 再加预算也没用
    return code, content, budgets[-1], {"prompt_tokens": ptok, "completion_tokens": ctok,
                                        "latency_ms": lat}


def _no_code_result(content, budget) -> tuple:
    """没抽到代码时的统一说明：区分「额度被思考吃光」和「模型就是没写代码」。"""
    if "max_tokens 截断" in (content or ""):
        return "输出被截断", f"模型在 max_tokens={budget} 处仍被截断，思考过程占满额度、没有产出代码"
    return "未抽到可运行的代码", "模型回复里没有能通过语法检查的 Python 代码"
