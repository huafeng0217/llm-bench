"""WinoGrande：代词消解 / 常识填空，**2 选 1**。

题干是一句话，中间有下划线（如「Sarah 是比 Maria 好得多的外科医生，所以 _ 总是接到更简单的病例」），
两个候选（Sarah / Maria）里选一个填进去。

它是本仓库里第一个 2 选 1 的基准 —— 选择题 runner 是按字母 A~J 通用的，
选项少不会影响判分，但**随机猜的基线从 25% 变成 50%**，看分数时必须记住这一点。
"""
from .types import Benchmark
import sys
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl


DS = "allenai/winogrande"
CONFIG = "winogrande_xl"
SPLIT = "validation"    # test 划分的 answer 官方是空的（答案未公开）


def map_winogrande(row: dict):
    """一行 WinoGrande → 统一题目格式。``answer`` 是 1/2（从 1 开始），转成 A/B。"""
    sentence = str(row.get("sentence", "")).strip()
    o1 = str(row.get("option1", "")).strip()
    o2 = str(row.get("option2", "")).strip()
    ans = str(row.get("answer", "")).strip()
    if not sentence or not o1 or not o2 or ans not in {"1", "2"}:
        return None
    return {"question": sentence, "A": o1, "B": o2,
            "answer": "A" if ans == "1" else "B"}


def download_winogrande():
    path = DATA_DIR / "winogrande.jsonl"
    rows = fetch_ds_rows(DS, CONFIG, SPLIT)
    items, failed = [], 0
    for row in rows:
        item = map_winogrande(row)
        if not item:
            failed += 1
            print("  解析失败，跳过（可能是 test 划分，答案未公开）", file=sys.stderr)
            continue
        items.append(item)
    write_jsonl(path, items)
    print(f"完成：{path}（共 {len(items)} 题" + (f"，解析失败 {failed}" if failed else "") + "）")


ENTRIES = [
    Benchmark(order=30, id='winogrande',
        name='WinoGrande',
        summary='2 选 1 的代词消解：随机猜就有一半，看分数要记住这条基线',
        category='常识推理',
        lang='英文',
        status='接近饱和',
        description='1267 道代词消解题：一句话里挖掉一个代词，从两个候选里选一个填进去，'
                    '必须先理解句子在说什么才选得对（如「Sarah 是比 Maria 好得多的外科医生，'
                    '所以 _ 总是接到更简单的病例」）。**只有两个选项，随机猜就有 50%**，'
                    '所以 60% 和 90% 的差别比在四选一里大得多。前沿模型已到 85% 上下。'
                    '注意：官方 test 划分没有公开答案，这里用的是 validation 划分（1267 题）。',
        source='https://arxiv.org/abs/1907.10641',
        label='WinoGrande（1267 题）',
        download=download_winogrande),
]
