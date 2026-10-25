"""指令遵循题型（IFEval）：题面 = 一段自然语言 + 若干「可验证指令」，判分逐条核对。

和别的题型有什么不同
--------------------
选择题比字符串、数值题做容错比对、代码题跑沙箱 —— 这三类都是"答案对不对"。
IFEval 判的是**格式/约束有没有遵守**：字数、段落数、必须/禁止出现的词、JSON、大写、
引号包裹、P.S. 结尾…… 所以判分逻辑来自官方规则（见 app/ifeval_rules/），
每道题带 `instructions`（指令 id 列表）和 `kwargs`（每条指令的参数）。

为什么不在 prompt 里加"请遵守以下指令"
------------------------------------
官方做法就是**只发原始 prompt**（指令本身写在 prompt 里），模型要自己读懂并遵守。
加一句额外的提醒会改变题面、让分数和官方不可比，所以这里保持原样。
"""
from .. import i18n
from ..ifeval_rules import instructions_registry
from ..llm import chat_with_retry
from .types import Outcome


def is_instruction_item(item: dict) -> bool:
    """指令遵循题：带 `instructions`（指令 id 列表）字段。

    判定放在最前面（比选择题/数值题都先）：这个字段是 IFEval 独有的，不会误伤别的题型。
    """
    return bool(item.get("instructions"))


def build_prompt(item: dict) -> str:
    """题面就是官方 prompt 原文 —— 指令已经写在里面了，别再加任何提醒。"""
    return str(item.get("question") or "").strip()


def build_checks(item: dict):
    """按 `instructions` + `kwargs` 构造出可逐条核对的指令对象。

    kwargs 列表与 instructions 列表**按下标对齐**（官方数据的约定）；
    长度不一致时按空参数补，构造失败的那条会被记成"判分失败"而不是静默放过。
    """
    ids = list(item.get("instructions") or [])
    kws = list(item.get("kwargs") or [])
    checks = []
    for i, iid in enumerate(ids):
        kw = kws[i] if i < len(kws) and isinstance(kws[i], dict) else {}
        # 官方用法：先 cls(instruction_id)，再 build_description(**非空 kwargs)。
        # 空值要丢掉 —— 否则会撞上「unexpected keyword argument」或把 None 当参数
        inst = instructions_registry.INSTRUCTION_DICT[iid](iid)
        inst.build_description(**{k: v for k, v in kw.items() if v})
        checks.append((iid, inst))
    return checks


async def run_instruction_item(model_cfg: dict, item: dict, params: dict) -> Outcome:
    """问模型 → 逐条核对指令是否遵守（全部满足才算对）。"""
    prompt = build_prompt(item)
    n = len(item.get("instructions") or [])
    expected = i18n.t("满足全部指令")
    resp = await chat_with_retry(model_cfg, prompt, expected, params)
    raw = resp["content"] or ""
    tok = {"prompt_tokens": resp["prompt_tokens"],
           "completion_tokens": resp["completion_tokens"],
           "latency_ms": resp["latency_ms"]}

    broken, failed_ids = [], []
    try:
        for iid, checker in build_checks(item):
            if not checker.check_following(raw):
                failed_ids.append(iid)
    except Exception as e:                        # noqa: BLE001 - 规则实现或数据有问题
        return Outcome(ok=0, predicted=i18n.t("判分失败"), expected=expected, raw=raw,
                       err=f"{type(e).__name__}: {e}"[:300], failed=True, **tok)

    if not failed_ids:
        return Outcome(ok=1, predicted=i18n.t("全部满足"), expected=expected,
                       raw=raw, **tok)
    return Outcome(ok=0, predicted=i18n.t("未满足部分指令"), expected=expected, raw=raw,
                   err=("未满足：" + "、".join(failed_ids))[:300] + f"（共 {n} 条）", **tok)
