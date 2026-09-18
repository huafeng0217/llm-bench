"""GSM8K（小学数学应用题）。"""

from .types import Benchmark
import re
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl



def download_gsm8k():
    """GSM8K：数学应用题（数值答案），train 集约 7473 题（test 无公开答案）。"""
    rows = fetch_ds_rows("openai/gsm8k", "main", "train")
    items = []
    for r in rows:
        q = r.get("question", "")
        # 官方 answer 含解题过程，最终答案在「#### 」之后
        m = re.search(r"####\s*(-?\d[\d,]*(?:\.\d+)?)", r.get("answer", ""))
        if not q or not m:
            continue
        items.append({"question": q, "answer": m.group(1).replace(",", ""), "subject": "gsm8k"})
    write_jsonl(DATA_DIR / "gsm8k.jsonl", items)
    print(f"完成：gsm8k.jsonl（共 {len(items)} 题）")


ENTRIES = [
    Benchmark(order=8, id='gsm8k',
        summary='小学数学应用题：看基础推理过不过关，前沿模型已接近饱和，适合当烟雾测试',
        summary_en="Grade-school word problems: a basic-reasoning smoke test, near "
                      "saturation now",
        name='GSM8K',
        category='数学推理',
        lang='英文',
        status='已饱和',
        status_en="saturated",
        description='约 7500 道小学数学应用题（train 集，test 无公开答案），测多步算术推理。前沿模型已接近满分，基本失去区分度。',
        description_en="About 7,500 grade-school math word problems (train split; the test "
                          "split has no public answers), testing multi-step arithmetic. "
                          "Frontier models are near perfect, so discrimination is basically "
                          "gone.",
        source='https://arxiv.org/abs/2110.14168',
        label='GSM8K（数学应用题，约 7473 题）',
        label_en="GSM8K (math word problems, ~7473 items)",
        download=download_gsm8k),
]
