"""AIME（数学竞赛真题，两个年份）。

分两个题库是刻意的：2025 年的题更可能是「模型没见过」的，对比两者能粗略看出训练数据污染。"""

from .types import Benchmark
import sys
from ._util import DATA_DIR, read_parquet, write_jsonl

from functools import partial


def download_aime(only: str | None = None):
    """AIME（美国数学邀请赛）：整数答案，复用数值题判分链路。

    分两个题库是刻意的——2025 年的题更可能是「模型没见过」的：
      - aime       2022~2024，共 90 题（AI-MO/aimo-validation-aime）
      - aime2025   2025 年，共 30 题（yentinglin/aime_2025），污染最少
    对比两者分数能粗略看出训练数据污染的影响。

    only 传 "aime" / "aime2025" 可只下一个（网页上点单个下载按钮时用）。
    """
    src = [
        ("aime", "AI-MO/aimo-validation-aime", "data/train-00000-of-00001.parquet"),
        ("aime2025", "yentinglin/aime_2025", "data/train-00000-of-00001-243207c6c994e1bd.parquet"),
    ]
    if only:
        src = [s for s in src if s[0] == only]
    for name, ds, path in src:
        try:
            rows = read_parquet(ds, path)
        except Exception as e:  # noqa: BLE001
            print(f"  失败 {ds}: {e}", file=sys.stderr)
            continue
        items = []
        for r in rows:
            q = (r.get("problem") or "").strip()
            a = str(r.get("answer", "")).strip()
            if not q or not a.lstrip("-").isdigit():
                continue
            # 去掉前导零（"033" → "33"）；引擎按数值比较，不受影响
            items.append({"question": q, "answer": str(int(a)), "subject": name})
        write_jsonl(DATA_DIR / f"{name}.jsonl", items)
        print(f"完成：{name}.jsonl（共 {len(items)} 题）")


ENTRIES = [
    Benchmark(order=10, id='aime',
        summary='美国数学邀请赛真题、答案都是整数；难度高，但年份早、可能已进训练数据',
        summary_en="Real AIME problems, integer answers; hard, but old enough to risk "
                      "contamination",
        name='AIME 2022-2024',
        category='数学推理',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        description='美国数学邀请赛（AIME）2022~2024 年真题共 90 题，答案都是 0~999 的整数。难度远高于 GSM8K / MATH-500，当前前沿模型正确率通常只有 10%~30%，是最能拉开差距的数学基准之一。注意：题目年份较早，可能已被部分纳入训练数据。',
        description_en="90 real AIME problems from 2022–2024; every answer is an integer "
                          "from 0 to 999. Far harder than GSM8K / MATH-500 — frontier models "
                          "typically score 10%–30%, making it one of the most discriminative "
                          "math benchmarks. Note: the years are early enough that some items "
                          "may have entered training data.",
        source='https://huggingface.co/datasets/AI-MO/aimo-validation-aime',
        label='AIME 2022-2024（数学竞赛真题，90 题）',
        label_en="AIME 2022-2024 (real contest problems, 90 items)",
        download=partial(download_aime, "aime")),
    Benchmark(order=11, id='aime2025',
        summary='2025 年 AIME 新题，几乎不可能在训练数据里；最能反映真实数学水平',
        summary_en="2025 AIME problems, almost certainly unseen; the cleanest read on real "
                      "math",
        name='AIME 2025',
        category='数学推理',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        description='AIME 2025 年真题共 30 题（I 卷 + II 卷），同样要求整数答案。因为年份最新、几乎不可能出现在训练数据里，这个分数最能反映模型的真实数学推理水平。题量少（30 题），每对一题准确率跳 3.3%，建议与 AIME 2022-2024 对比看。',
        description_en="All 30 problems from AIME 2025 (papers I and II), again with integer "
                          "answers. Being the newest set it is the least likely to appear in "
                          "training data, so this score best reflects real math reasoning. Only "
                          "30 items — each is worth 3.3 points — so read it alongside AIME "
                          "2022-2024.",
        source='https://huggingface.co/datasets/yentinglin/aime_2025',
        label='AIME 2025（最新真题，30 题）',
        label_en="AIME 2025 (latest set, 30 items)",
        download=partial(download_aime, "aime2025")),
]
