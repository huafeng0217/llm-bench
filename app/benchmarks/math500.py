"""MATH-500（竞赛数学）。"""

from .types import Benchmark
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl



def download_math500():
    """MATH-500：竞赛数学（答案多为 LaTeX 表达式），500 题。

    注意：LaTeX 答案的判分是近似判分（engine 的 numeric_match 对复杂表达式
    覆盖有限），对简单数值/分数答案较准，复杂代数/几何表达式可能误判。
    """
    rows = fetch_ds_rows("HuggingFaceH4/MATH-500", "default", "test")
    items = []
    for r in rows:
        q = r.get("problem", "")
        if not q:
            continue
        items.append({"question": q, "answer": r.get("answer", "").strip(), "subject": r.get("subject", "")})
    write_jsonl(DATA_DIR / "math500.jsonl", items)
    print(f"完成：math500.jsonl（共 {len(items)} 题）")


ENTRIES = [
    Benchmark(order=9, id='math500',
        name='MATH-500',
        category='数学推理',
        lang='英文',
        status='接近饱和',
        description='竞赛数学 benchmark MATH 的 500 题子集，难度高于 GSM8K，常用于快速评估数学推理。',
        source='https://arxiv.org/abs/2103.03874',
        label='MATH-500（竞赛数学，500 题）',
        download=download_math500),
]
