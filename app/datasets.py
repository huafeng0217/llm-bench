"""题库定位、加载与明细导出。"""

import json
from pathlib import Path

from . import db
from .config import BFCL_DIR, DATA_DIR, RESULTS_DIR


def export_items(eval_id: int) -> Path:
    """把一次评测的逐题明细（含模型原始输出）导出为本地 JSONL 文件。"""
    rows = db.query(
        "SELECT idx, question, expected, predicted, raw_response, correct,"
        " latency_ms, error FROM eval_items WHERE eval_id=? ORDER BY idx",
        (eval_id,),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval_{eval_id}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def delete_items_file(eval_id: int):
    path = RESULTS_DIR / f"eval_{eval_id}.jsonl"
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        # 某些受控运行环境禁止直接删除文件，退而求其次移入 .trash 目录
        trash = RESULTS_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        path.replace(trash / path.name)


def list_datasets():
    """扫描 data 目录下所有 .jsonl 题库（含 bfcl_v4 子目录）。"""
    out = []
    paths = sorted(DATA_DIR.glob("*.jsonl")) + sorted(BFCL_DIR.glob("*.jsonl"))
    for p in paths:
        if p.name.endswith("_answer.jsonl"):
            continue  # BFCL 标准答案文件不是题库
        n = sum(1 for _ in open(p, encoding="utf-8"))
        out.append({"id": p.stem, "name": p.stem, "count": n})
    return out


def _dataset_path(benchmark: str) -> Path:
    """定位题库文件：data/<id>.jsonl 或 data/bfcl_v4/<id>.jsonl。"""
    for p in (DATA_DIR / f"{benchmark}.jsonl", BFCL_DIR / f"{benchmark}.jsonl"):
        if p.exists():
            return p
    raise FileNotFoundError(f"题库不存在: {benchmark}")


def load_dataset(benchmark: str, limit: int | None = None):
    path = _dataset_path(benchmark)
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    if limit and limit > 0:
        items = items[:limit]
    return items


def load_bfcl_answers(benchmark: str):
    """加载 BFCL 标准答案文件（与题目 id 对应）。"""
    path = BFCL_DIR / f"{benchmark}_answer.jsonl"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["id"]] = d.get("ground_truth")
    return out
