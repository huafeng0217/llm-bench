"""MMLU（含内置演示样例）。"""

from .types import Benchmark
import sys
import time
from ._util import DATA_DIR, LETTERS, REQUEST_GAP, fetch_rows, list_configs, write_jsonl



def map_mmlu(row: dict):
    choices = row.get("choices", [])
    ans = row.get("answer")
    if len(choices) != 4 or ans is None:
        return None
    item = {"question": row["question"], "answer": LETTERS[int(ans)],
            "subject": row.get("subject", "")}
    for i, c in enumerate(choices):
        item[LETTERS[i]] = str(c)
    return item


def ds_download_rows(name: str, src: dict, n: int):
    """通过 datasets-server 的 rows API 拉取题库（mmlu / ceval 共用）。"""
    import json
    path = DATA_DIR / f"{name}.jsonl"
    out, seen = [], set()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    item = json.loads(line)
                    seen.add(item["question"][:80])
                    out.append(item)
        if out:
            print(f"检测到已有 {len(out)} 题，将增量续传")
    configs = [src["config"]] if src["config"] else list_configs(src["dataset"])
    print(f"{name}: {src['note']}，共 {len(configs)} 个配置")
    single_cfg = len(configs) == 1
    for cfg in configs:
        offset = len(out) if single_cfg else 0
        while n <= 0 or len(out) < n:
            try:
                rows = fetch_rows(src["dataset"], cfg, src["split"], offset)
            except Exception as e:  # noqa: BLE001
                print(f"  {cfg}@{offset} 拉取失败，跳过: {e}", file=sys.stderr)
                break
            if not rows:
                break
            for row in rows:
                item = src["mapper"](row)
                if item:
                    key = item["question"][:80]
                    if key not in seen:
                        seen.add(key)
                        item["subject"] = item["subject"] or cfg
                        out.append(item)
            offset += len(rows)
            print(f"\r  {cfg}: 已累积 {len(out)} 题", end="", flush=True)
            if n > 0 and len(out) >= n:
                break
            time.sleep(REQUEST_GAP)
        if n > 0 and len(out) >= n:
            break
    print()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    final = out[:n] if n > 0 else out
    write_jsonl(path, final)     # 原子写：中断不会留下「看起来完整的截断题库」
    print(f"完成：{path}（{len(final)} 题）")


SOURCE = {"dataset": "cais/mmlu", "config": "all", "split": "test",
          "mapper": map_mmlu, "note": "完整集约 14042 题"}


def download_mmlu():
    ds_download_rows("mmlu", SOURCE, 0)


ENTRIES = [
    Benchmark(order=0, id='mmlu_sample',
        summary='12 题演示样例：不下载任何题库，也能把整条评测流程先跑通',
        summary_en="12-item sample: dry-run the whole pipeline without downloading a dataset",
        name='MMLU 演示样例',
        name_en="MMLU sample",
        category='通用知识',
        lang='英文',
        status='演示样例',
        status_en="sample",
        description='内置 12 道 MMLU 风格选择题，用于快速跑通流程。正式评测请用下载脚本拉取完整 MMLU。',
        description_en="12 MMLU-style multiple-choice questions built in, so you can try the "
                          "whole pipeline end to end. For real numbers, download the full MMLU "
                          "with the script.",
        source='https://arxiv.org/abs/2009.03300'),
    Benchmark(order=2, id='mmlu',
        summary='57 学科 4 选 1 的通识基线；前沿模型已 88%+，适合筛查、不适合拉开差距',
        summary_en="57 subjects, 4-way; frontier models pass 88% now, so it screens rather "
                      "than separates",
        name='MMLU',
        category='通用知识',
        lang='英文',
        status='已饱和',
        status_en="saturated",
        description='57 个学科约 1.4 万道选择题，衡量模型的通用知识广度，是使用最广泛的基准。前沿模型已普遍超过 88%，区分度低，适合作基线筛查。',
        description_en="About 14k multiple-choice questions across 57 subjects — the most "
                          "widely used measure of general knowledge breadth. Frontier models "
                          "now exceed 88%, so discrimination is low and it works best as a "
                          "baseline screen.",
        source='https://arxiv.org/abs/2009.03300',
        label='MMLU（完整约 1.4 万题）',
        label_en="MMLU (~14k items in full)",
        download=download_mmlu),
]
