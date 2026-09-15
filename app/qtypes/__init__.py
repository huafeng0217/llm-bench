"""题型注册表：把「这是什么题 / 怎么问 / 怎么判」从评测主循环里拿出来。

为什么要有它
------------
原来 `run_evaluation` 的 `work()` 里是一串硬编码分派：

    code_runner = (run_lcb_item if is_lcb_item(item)
                   else run_code_item if is_code_item(item) else None)
    if code_runner: ...
    elif is_choice_item(item): ...
    else: ...                    # 数值题

每加一种题型都要改这个**核心循环**，而且四种判分逻辑全部 inline 在里面（近 70 行）。
现在改成「注册表 + 统一 runner 签名」，`run_evaluation` 只需要：

    outcome = await qtypes.detect(ctx, item).runner(model_cfg, item, params, ctx)

**加题型 = 在 TYPES 里加一条**，核心循环不再改。

统一 runner 签名
----------------
    async def runner(model_cfg, item, params, ctx) -> Outcome

`ctx`（`types.RunCtx`）承载数据集级的事实（是否 BFCL、是否 multi_turn、标准答案表），
因为这类判断是**每个题库一份**的，不该在每道题上重新算。

模块分工
--------
    types.py              公共类型（Outcome / RunCtx / QuestionType）
    choice.py             选择题
    numeric.py            数值 / 表达式题
    code_unit.py          HumanEval 式（沙箱跑官方单元测试）
    code_stdio.py         LiveCodeBench 式（沙箱跑竞赛用例）
    _code.py              代码题公共层（抽代码 + 额度阶梯）
    bfcl.py               BFCL 单轮函数调用
    bfcl_multi_turn.py    BFCL 多轮对话

本文件只做「注册 + 把各模块的判分接成统一签名」，不含判分逻辑。
"""
from . import bfcl, bfcl_multi_turn, choice, code_stdio, code_unit, numeric
from .types import Outcome, QuestionType, RunCtx

__all__ = ["Outcome", "QuestionType", "RunCtx", "TYPES", "BY_ID", "detect"]


# ---------- 把各题型的判分接成统一签名 ----------
# 这几个函数只做「取输入 → 调对应模块 → 组装 Outcome」，不含判分算法。

async def _run_choice(model_cfg, item, params, ctx) -> Outcome:
    from ..llm import chat_with_retry
    expected = str(item.get("answer", "")).strip().upper()
    resp = await chat_with_retry(model_cfg, choice.build_prompt(item), expected, params)
    raw = resp["content"]
    predicted = choice.extract_answer(raw)
    return Outcome(ok=1 if predicted == expected else 0, predicted=predicted, expected=expected,
                   raw=raw, prompt_tokens=resp["prompt_tokens"],
                   completion_tokens=resp["completion_tokens"], latency_ms=resp["latency_ms"])


async def _run_numeric(model_cfg, item, params, ctx) -> Outcome:
    from ..llm import chat_with_retry
    expected = str(item.get("answer", "")).strip()
    resp = await chat_with_retry(model_cfg, numeric.build_numeric_prompt(item), expected, params)
    raw = resp["content"]
    # 明细展示完整答案（LaTeX/数字），回退到抽取的数字
    predicted = numeric.extract_answer_span(raw) or numeric.extract_numeric_answer(raw)
    return Outcome(ok=1 if numeric.numeric_match(raw, expected) else 0, predicted=predicted,
                   expected=expected, raw=raw, prompt_tokens=resp["prompt_tokens"],
                   completion_tokens=resp["completion_tokens"], latency_ms=resp["latency_ms"])


async def _run_code_unit(model_cfg, item, params, ctx) -> Outcome:
    return Outcome(**await code_unit.run_code_item(model_cfg, item, params))


async def _run_code_stdio(model_cfg, item, params, ctx) -> Outcome:
    return Outcome(**await code_stdio.run_lcb_item(model_cfg, item, params))


async def _run_bfcl(model_cfg, item, params, ctx) -> Outcome:
    from ..llm import chat_with_retry_bfcl
    gt = ctx.answers.get(item.get("id"))
    tools, name_map = bfcl.normalize_tools(item.get("function", []))
    resp = await chat_with_retry_bfcl(model_cfg, bfcl.bfcl_messages(item.get("question")),
                                      tools, gt, params)
    calls = resp["tool_calls"] or []
    # 把 sanitize 后的函数名还原成原始名（如 math_factorial → math.factorial），再评分/展示
    for c in calls:
        if c["name"] in name_map:
            c["name"] = name_map[c["name"]]
    ok, _reason = bfcl.ast_match(calls, gt)
    predicted = bfcl.bfcl_predicted_text(calls)
    return Outcome(ok=1 if ok else 0, predicted=predicted, expected=bfcl.bfcl_expected_text(gt),
                   raw=resp["content"] or predicted, prompt_tokens=resp["prompt_tokens"],
                   completion_tokens=resp["completion_tokens"], latency_ms=resp["latency_ms"])


async def _run_bfcl_multi_turn(model_cfg, item, params, ctx) -> Outcome:
    mr = await bfcl_multi_turn.run_multi_turn(model_cfg, item, ctx.answers.get(item.get("id")), params)
    return Outcome(ok=1 if mr["ok"] else 0,
                   predicted=" | ".join("✓" if o else "✗" for o in mr["turn_oks"]),
                   expected=f"共 {len(mr['turn_oks'])} 轮", raw=mr["detail"],
                   prompt_tokens=mr["prompt_tokens"], completion_tokens=mr["completion_tokens"],
                   latency_ms=mr["latency_ms"])


# ---------- 注册表（顺序即匹配优先级）----------
# 数值题是**兜底**，必须放最后 —— 它的 detect 恒为真。

TYPES: list[QuestionType] = [
    QuestionType(id="bfcl_multi_turn", label="多轮工具调用",
                 detect=lambda ctx, it: ctx.is_bfcl and ctx.is_multi_turn,
                 runner=_run_bfcl_multi_turn),
    QuestionType(id="bfcl", label="函数调用",
                 detect=lambda ctx, it: ctx.is_bfcl,
                 runner=_run_bfcl),
    QuestionType(id="code_stdio", label="竞赛题（stdin/函数调用）",
                 detect=lambda ctx, it: code_stdio.is_lcb_item(it),
                 runner=_run_code_stdio, needs_sandbox=True),
    QuestionType(id="code_unit", label="函数补全 + 单元测试",
                 detect=lambda ctx, it: code_unit.is_code_item(it),
                 runner=_run_code_unit, needs_sandbox=True),
    QuestionType(id="choice", label="选择题",
                 detect=lambda ctx, it: choice.is_choice_item(it),
                 runner=_run_choice),
    QuestionType(id="numeric", label="数值/表达式题",
                 detect=lambda ctx, it: True,          # 兜底
                 runner=_run_numeric),
]

BY_ID = {t.id: t for t in TYPES}


def detect(ctx: RunCtx, item: dict) -> QuestionType:
    """判定一道题属于哪种题型。"""
    for t in TYPES:
        if t.detect(ctx, item):
            return t
    return BY_ID["numeric"]      # 理论上到不了这里（numeric 是兜底）
