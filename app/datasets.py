"""题库定位、加载与明细导出。"""

import json
from pathlib import Path

from . import db, i18n
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


# 题库行数缓存：路径 -> ((mtime_ns, size), 行数)
#
# 为什么必须缓存：行数是两个高频读数的来源 —— 题库列表的「N 题」和覆盖率要用的题库总量，
# 而算它要**把整个文件读一遍**。livecodebench.jsonl 是 134MB 却只有 175 行
# （一行一个含全部测试用例的大 JSON），数一遍约 190ms，整个 data/ 约 230ms。
# 前端每切换一次分类就要一次题库列表，所以实测「切分类」每次约 470ms，全耗在这里；
# 而且下载中页面每秒轮询一次，同样的钱要反复付。
# 用 mtime+size 做 key：重新下载题库后自动失效，不需要手工清缓存。
_LINE_COUNT_CACHE: dict = {}


def count_lines(path: Path) -> int:
    """题库文件的行数（只数非空行）。文件不可读时返回 0。

    只数非空行 —— 空行不是一道题，而 ``/api/benchmarks`` 显示题数、
    ``scoring.full_count`` 算覆盖率都用这个值，两边必须是同一个定义
    （以前一边数全部行、一边只数非空行，只要题库里出现空行，覆盖率就会算出 >100%）。
    """
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return 0
    hit = _LINE_COUNT_CACHE.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip())
    except OSError:
        return 0
    _LINE_COUNT_CACHE[str(path)] = (key, n)
    return n


def _dataset_files() -> list:
    """data 目录下所有题库文件（含 bfcl_v4 子目录），排除 BFCL 标准答案文件。"""
    paths = sorted(DATA_DIR.glob("*.jsonl")) + sorted(BFCL_DIR.glob("*.jsonl"))
    return [p for p in paths if not p.name.endswith("_answer.jsonl")]


def list_dataset_ids() -> set:
    """只列题库 id，**不数题量**。

    给「这个基准下载过没有」这类调用方用（如 /api/benchmarks/downloads）——
    它不需要题数，而数题量要把文件整读一遍，没理由为了一份 id 列表付这个钱。
    """
    return {p.stem for p in _dataset_files()}


def list_datasets():
    """扫描 data 目录下所有 .jsonl 题库（含 bfcl_v4 子目录），带题量。"""
    return [{"id": p.stem, "name": p.stem, "count": count_lines(p)}
            for p in _dataset_files()]


def _dataset_path(benchmark: str) -> Path:
    """定位题库文件：data/<id>.jsonl 或 data/bfcl_v4/<id>.jsonl。"""
    for p in (DATA_DIR / f"{benchmark}.jsonl", BFCL_DIR / f"{benchmark}.jsonl"):
        if p.exists():
            return p
    raise FileNotFoundError(i18n.t("题库不存在: {bid}", bid=benchmark))


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
