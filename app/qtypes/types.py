"""题型的公共类型。

单独一个模块是为了让「注册表」和「各题型实现」都能引用它而不成环：
    types.py  <- __init__.py（注册表）
    types.py  <- choice.py / numeric.py / code_unit.py / ...
"""
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass
class Outcome:
    """一次判分的统一结果。字段与 `run_code_item` / `run_lcb_item` 的返回字典一致。"""

    ok: int = 0
    predicted: str | None = None
    expected: str | None = None
    raw: str | None = None
    err: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    sbx_ms: int = 0


@dataclass
class RunCtx:
    """数据集级上下文：每道题都一样的那部分判断，算一次就够了。"""

    benchmark: str
    is_bfcl: bool = False
    is_multi_turn: bool = False
    answers: dict = field(default_factory=dict)   # BFCL 标准答案：id -> ground_truth


@dataclass(frozen=True)
class QuestionType:
    id: str
    label: str
    detect: Callable[[RunCtx, dict], bool]
    runner: Callable[..., Awaitable[Outcome]]
    needs_sandbox: bool = False
