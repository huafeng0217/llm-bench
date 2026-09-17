"""TruthfulQA（真实性 / 抗幻觉）。"""

from .types import Benchmark
import json
from ._util import CHOICES, DATA_DIR, http_get, write_jsonl



TRUTHFULQA_MC = "https://raw.githubusercontent.com/sylinrl/TruthfulQA/main/data/mc_task.json"


def download_truthfulqa():
    """TruthfulQA（MC1）：测模型是否会复述常见误解/伪科学，790 题。

    处理了两个坑：
      1. 原始 mc1_targets 里**正确项恒定排在第 0 位**（实测 790/790），
         必须打乱选项，否则模型无脑选 A 就能满分。
      2. 选项数 2~13 个不等，超过 10 个的超出引擎字母表（A-J），跳过。
    打乱用固定随机种子，保证每次下载结果一致、可复现。
    """
    import random
    data = json.loads(http_get(TRUTHFULQA_MC, timeout=120).decode("utf-8"))
    rng = random.Random(42)
    items, skipped = [], 0
    for row in data:
        q = (row.get("question") or "").strip()
        pairs = list((row.get("mc1_targets") or {}).items())  # [(选项文本, 0/1)]
        # 只收「恰好一个正确项」且选项数落在 A-J 内的题
        if not q or sum(v for _, v in pairs) != 1 or not (2 <= len(pairs) <= len(CHOICES)):
            skipped += 1
            continue
        rng.shuffle(pairs)
        it = {"question": q, "subject": "truthfulqa"}
        for i, (text, label) in enumerate(pairs):
            it[CHOICES[i]] = str(text)
            if label == 1:
                it["answer"] = CHOICES[i]
        items.append(it)
    write_jsonl(DATA_DIR / "truthfulqa.jsonl", items)
    print(f"完成：truthfulqa.jsonl（共 {len(items)} 题"
          + (f"，跳过 {skipped}（无正确项/选项超 10）" if skipped else "") + "）")


ENTRIES = [
    Benchmark(order=4, id='truthfulqa',
        summary='判断常见说法是真是假，选项数不固定；专测会不会复述网上的流行误解',
        name='TruthfulQA',
        category='通用知识',
        lang='英文',
        status='仍有区分度',
        description='776 道真实性选择题（4~10 选 1），专门针对人类常见的误解与伪科学（如「吃西瓜籽会在肚子里长西瓜」）。题目里混入多个听起来合理的错误说法，选对说明模型没有被预训练语料里的错误信息带偏，是衡量幻觉倾向的经典基准。',
        source='https://arxiv.org/abs/2109.07958',
        label='TruthfulQA（真实性/抗幻觉，776 题）',
        download=download_truthfulqa),
]
