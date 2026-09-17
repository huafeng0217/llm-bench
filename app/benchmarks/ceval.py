"""C-Eval（含内置演示样例）。"""

from .types import Benchmark
import sys
import time
from ._util import DATA_DIR, LETTERS, REQUEST_GAP, fetch_rows, list_configs, write_jsonl



def map_ceval(row: dict):
    ans = str(row.get("answer", "")).strip().upper()
    if ans not in LETTERS or not all(row.get(c) for c in LETTERS):
        return None
    return {"question": row["question"], "A": str(row["A"]), "B": str(row["B"]),
            "C": str(row["C"]), "D": str(row["D"]), "answer": ans,
            "subject": row.get("subject", "")}


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


SOURCE = {"dataset": "ceval/ceval-exam", "config": None, "split": "val",
          "mapper": map_ceval, "note": "val 划分共 1346 题（test 答案官方未公开）"}


def download_ceval():
    ds_download_rows("ceval", SOURCE, 0)


ENTRIES = [
    Benchmark(order=1, id='ceval_sample',
        summary='12 题中文演示样例：不下载完整题库，也能先跑通流程',
        name='C-Eval 演示样例',
        category='中文能力',
        lang='中文',
        status='演示样例',
        description='内置 12 道 C-Eval 风格中文选择题，用于快速跑通流程。正式评测请用下载脚本拉取完整 C-Eval。',
        source='https://arxiv.org/abs/2305.08322'),
    Benchmark(order=5, id='ceval',
        summary='中文 52 学科考试题，中文能力基线；与 CMMLU 搭配看中文知识广度',
        name='C-Eval',
        category='中文能力',
        lang='中文',
        status='仍有区分度',
        description='约 1.4 万道中文选择题，覆盖 52 个学科、从中学到专业级别，是中文模型知识能力的事实标准。',
        source='https://arxiv.org/abs/2305.08322',
        label='C-Eval（val 划分 1346 题）',
        download=download_ceval),
]
