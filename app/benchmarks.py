"""Benchmark 元数据：网站上展示每个基准的性质与作用。

key 为 data/ 目录下 jsonl 文件名（不含后缀）。
"""
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
        "description": "448 道研究生级理化生选择题，由领域专家编写且无法通过搜索作弊，是当前区分前沿模型推理能力的主力基准。",
        "source": "https://arxiv.org/abs/2311.12022",
    },
    "gsm8k": {
        "name": "GSM8K",
        "category": "数学推理",
        "lang": "英文",
        "status": "已饱和",
        "description": "8500 道小学数学应用题，测多步算术推理。前沿模型已接近满分，基本失去区分度。",
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
    return meta
