"""Benchmark 元数据：网站上展示每个基准的性质与作用。

key 为 data/ 目录下 jsonl 文件名（不含后缀）。
"""
# 统一分类体系（前端按此分组展示）
CATEGORIES = [
    {"id": "knowledge", "name": "通用知识", "color": "#4a7de0"},
    {"id": "chinese", "name": "中文能力", "color": "#e05a4a"},
    {"id": "science", "name": "科学推理", "color": "#7a5ae0"},
    {"id": "math", "name": "数学推理", "color": "#2fa36b"},
    {"id": "agent", "name": "Agent / 工具调用", "color": "#e0952f"},
    {"id": "code", "name": "代码工程", "color": "#2f9be0"},
    {"id": "custom", "name": "自定义", "color": "#8a94a6"},
]

META = {
    "mmlu_sample": {
        "name": "MMLU 演示样例",
        "category": "通用知识",
        "lang": "英文",
        "status": "演示样例",
        "description": "内置 12 道 MMLU 风格选择题，用于快速跑通流程。正式评测请用下载脚本拉取完整 MMLU。",
        "source": "https://arxiv.org/abs/2009.03300",
    },
    "ceval_sample": {
        "name": "C-Eval 演示样例",
        "category": "中文能力",
        "lang": "中文",
        "status": "演示样例",
        "description": "内置 12 道 C-Eval 风格中文选择题，用于快速跑通流程。正式评测请用下载脚本拉取完整 C-Eval。",
        "source": "https://arxiv.org/abs/2305.08322",
    },
    "mmlu": {
        "name": "MMLU",
        "category": "通用知识",
        "lang": "英文",
        "status": "已饱和",
        "description": "57 个学科约 1.4 万道选择题，衡量模型的通用知识广度，是使用最广泛的基准。前沿模型已普遍超过 88%，区分度低，适合作基线筛查。",
        "source": "https://arxiv.org/abs/2009.03300",
    },
    "mmlu_pro": {
        "name": "MMLU-Pro",
        "category": "通用知识",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "MMLU 的增强版：选项从 4 个增至 10 个，更强调推理而非记忆，难度明显更高，对前沿模型仍有区分度。",
        "source": "https://arxiv.org/abs/2406.01574",
    },
    "ceval": {
        "name": "C-Eval",
        "category": "中文能力",
        "lang": "中文",
        "status": "仍有区分度",
        "description": "约 1.4 万道中文选择题，覆盖 52 个学科、从中学到专业级别，是中文模型知识能力的事实标准。",
        "source": "https://arxiv.org/abs/2305.08322",
    },
    "cmmlu": {
        "name": "CMMLU",
        "category": "中文能力",
        "lang": "中文",
        "status": "仍有区分度",
        "description": "67 个学科约 1.1 万道中文选择题，包含大量中国本土知识（法律、饮食、习俗等），与 C-Eval 互补。",
        "source": "https://arxiv.org/abs/2306.09212",
    },
    "gpqa": {
        "name": "GPQA Diamond",
        "category": "科学推理",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "198 道研究生级理化生选择题（GPQA Diamond 子集），由领域专家编写且无法通过搜索作弊，是当前区分前沿模型推理能力的主力基准。",
        "source": "https://arxiv.org/abs/2311.12022",
    },
    "gsm8k": {
        "name": "GSM8K",
        "category": "数学推理",
        "lang": "英文",
        "status": "已饱和",
        "description": "约 7500 道小学数学应用题（train 集，test 无公开答案），测多步算术推理。前沿模型已接近满分，基本失去区分度。",
        "source": "https://arxiv.org/abs/2110.14168",
    },
    "math500": {
        "name": "MATH-500",
        "category": "数学推理",
        "lang": "英文",
        "status": "接近饱和",
        "description": "竞赛数学 benchmark MATH 的 500 题子集，难度高于 GSM8K，常用于快速评估数学推理。",
        "source": "https://arxiv.org/abs/2103.03874",
    },
    # ---------- BFCL v4（Agent / 工具调用）----------
    "BFCL_v4_simple_python": {
        "name": "BFCL v4 · 单函数",
        "category": "Agent / 工具调用",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "伯克利函数调用排行榜（BFCL）v4 单函数子集：给一个问题与一个工具定义，模型须选择正确函数并填对参数。官方用 AST 匹配评分。",
        "source": "https://gorilla.cs.berkeley.edu/leaderboard",
    },
    "BFCL_v4_multiple": {
        "name": "BFCL v4 · 多函数选择",
        "category": "Agent / 工具调用",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "BFCL v4 多函数子集：同时给出多个工具，模型须在候选集中挑出正确的那一个，考验函数辨识能力。",
        "source": "https://gorilla.cs.berkeley.edu/leaderboard",
    },
    "BFCL_v4_parallel": {
        "name": "BFCL v4 · 并行调用",
        "category": "Agent / 工具调用",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "BFCL v4 并行子集：单轮请求中须同时调用多个不同函数，考验并行工具调用的编排能力。",
        "source": "https://gorilla.cs.berkeley.edu/leaderboard",
    },
    "BFCL_v4_parallel_multiple": {
        "name": "BFCL v4 · 并行多选",
        "category": "Agent / 工具调用",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "BFCL v4 并行+多函数组合子集：多个问题×多个候选函数，须并行调用且各自选对函数，难度最高。",
        "source": "https://gorilla.cs.berkeley.edu/leaderboard",
    },
    "BFCL_v4_irrelevance": {
        "name": "BFCL v4 · 无关拒绝",
        "category": "Agent / 工具调用",
        "lang": "英文",
        "status": "仍有区分度",
        "description": "BFCL v4 无关性子集：问题与给出的工具毫无关系，正确行为是拒绝调用任何函数，测模型是否过度调用工具（幻觉防御）。",
        "source": "https://gorilla.cs.berkeley.edu/leaderboard",
    },
}

FALLBACK = {
    "category": "自定义",
    "lang": "-",
    "status": "自定义题库",
    "description": "用户自定义或下载的题库（jsonl 格式：question/A/B/C/D/answer 字段）。",
    "source": "",
}


def get_meta(benchmark_id: str) -> dict:
    meta = dict(FALLBACK)
    meta.update(META.get(benchmark_id, {}))
    meta.setdefault("name", benchmark_id)
    meta["id"] = benchmark_id
    # 附带所属分类 id 与颜色，前端按此分组展示
    for c in CATEGORIES:
        if c["name"] == meta["category"]:
            meta["category_id"] = c["id"]
            meta["category_color"] = c["color"]
            break
    else:
        meta["category_id"] = "custom"
        meta["category_color"] = "#8a94a6"
    return meta
