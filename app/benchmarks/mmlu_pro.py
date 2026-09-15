"""MMLU-Pro（10 选 1）。"""

from .types import Benchmark
import ast
from ._util import CHOICES, DATA_DIR, fetch_ds_rows, write_jsonl



def download_mmlu_pro():
    """MMLU-Pro：多选一选择题（多为 10 选 1，个别题目选项数略少），test 集约 1.2 万题。

    注意：datasets-server 返回的 options 已经是 list（非字符串）；个别题目选项数
    可能不是 10（数据本身如此），按实际选项数生成 A-J 前缀，answer 是字母。
    """
    rows = fetch_ds_rows("TIGER-Lab/MMLU-Pro", "default", "test")
    items = []
    for r in rows:
        opts = r.get("options")
        if isinstance(opts, str):
            # 兼容个别把 options 当字符串返回的情况
            try:
                opts = ast.literal_eval(opts)
            except (ValueError, SyntaxError):
                continue
        if not isinstance(opts, list) or not (2 <= len(opts) <= 10):
            continue
        answer = str(r.get("answer", "")).strip().upper()
        if answer not in CHOICES[:len(opts)]:
            continue
        it = {"question": r["question"], "answer": answer, "subject": r.get("category", "")}
        for i, c in enumerate(CHOICES[:len(opts)]):
            it[c] = str(opts[i])
        items.append(it)
    write_jsonl(DATA_DIR / "mmlu_pro.jsonl", items)
    print(f"完成：mmlu_pro.jsonl（共 {len(items)} 题）")


ENTRIES = [
    Benchmark(order=3, id='mmlu_pro',
        name='MMLU-Pro',
        category='通用知识',
        lang='英文',
        status='仍有区分度',
        description='MMLU 的增强版：选项从 4 个增至 10 个，更强调推理而非记忆，难度明显更高，对前沿模型仍有区分度。',
        source='https://arxiv.org/abs/2406.01574',
        label='MMLU-Pro（10 选 1，约 1.2 万题）',
        download=download_mmlu_pro),
]
