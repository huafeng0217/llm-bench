"""评测引擎（向后兼容门面）。

历史：这个模块曾经装下整个评测引擎（1100+ 行、71 个顶层定义）。
现按职责拆分为：

    config.py            常量（路径 / 选项字母 / 默认参数 / 沙箱并发）
    datasets.py          题库定位与加载、逐题明细导出
    llm.py               模型调用层（OpenAI 封装 / 重试 / mock）
    qtypes/              题型：prompt、答案抽取、判分
      choice.py            选择题
      numeric.py           数值 / 表达式题
      code_unit.py         HumanEval 式（沙箱跑单元测试）
      code_stdio.py        LiveCodeBench 式（沙箱跑竞赛用例）
      bfcl.py              BFCL 单轮函数调用
      bfcl_multi_turn.py   BFCL 多轮对话
      _code.py             代码题公共层（抽代码 + 额度阶梯）
    runner.py            评测任务编排（run_evaluation / 进度 / 续跑）

本模块只做 re-export，让 `engine.xxx` 这种旧写法继续可用
（main.py / summary.py / scripts/verify_*.py 都在用）。
**新代码请直接从上面的模块 import。**
"""

from .config import (  # noqa: F401
    DATA_DIR, RESULTS_DIR, BFCL_DIR, CHOICES,
    DEFAULTS, MAX_RETRIES, SANDBOX_CONCURRENCY, SANDBOX_SEM,
)

from .datasets import (  # noqa: F401
    export_items, delete_items_file, list_datasets, _dataset_path,
    load_dataset, load_bfcl_answers,
)

from .llm import (  # noqa: F401
    parse_tool_calls, _mock_response, _mock_bfcl, chat_once,
    chat_once_bfcl, chat_with_retry, chat_with_retry_bfcl, CODE_TOKEN_LADDER,
)

from .qtypes.choice import (  # noqa: F401
    is_choice_item, build_prompt, extract_answer,
)

from .qtypes.numeric import (  # noqa: F401
    build_numeric_prompt, extract_numeric_answer, normalize_answer, _to_float,
    extract_answer_span, numeric_match,
)

from .qtypes._code import (  # noqa: F401
    code_from_completion, extract_code, _ask_for_code, _no_code_result,
)

from .qtypes.code_unit import (  # noqa: F401
    is_code_item, build_code_prompt, run_code_item,
)

from .qtypes.code_stdio import (  # noqa: F401
    LCB_CASE_TIMEOUT, LCB_TOTAL_BUDGET, LCB_SANDBOX_TIMEOUT, is_lcb_item,
    build_lcb_prompt, run_lcb_item,
)

from .qtypes.bfcl import (  # noqa: F401
    is_bfcl, _TYPE_MAP, _norm_schema, normalize_tools,
    bfcl_messages, item_question_text, ast_match, bfcl_expected_text,
    bfcl_predicted_text,
)

from .qtypes.bfcl_multi_turn import (  # noqa: F401
    MAX_MT_STEPS, MT_CLASS_DOC, FUNC_DOC_DIR, is_multi_turn,
    load_mt_tools, parse_gt_calls, describe_env, mock_exec_result,
    _call_matches, ast_match_subset, run_multi_turn,
)

from .runner import (  # noqa: F401
    RUNNING, PROGRESS, CODE_PARAM_FLOOR, LCB_PARAM_FLOOR,
    run_evaluation,
)

from . import qtypes  # noqa: F401  (题型注册表)
