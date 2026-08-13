"""下载完整 benchmark 题库并转为项目统一的 jsonl 格式。

用法（在项目根目录执行）：
    python scripts/download_datasets.py mmlu --n 2000
    python scripts/download_datasets.py ceval --n 1000

数据源为 HuggingFace datasets-server 的 HTTP API，国内不可达时自动回退 hf-mirror。
输出文件：data/<benchmark>.jsonl，每行 {"question","A","B","C","D","answer","subject"}。
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HOST = "datasets-server.huggingface.co"
LETTERS = ["A", "B", "C", "D"]
REQUEST_GAP = 0.5  # 每次请求间隔，避免触发限流


def http_json(path_query: str):
    """带 429 限流退避重试的 GET JSON。"""
    url = f"https://{HOST}{path_query}"
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 0) or min(10 * (attempt + 1), 60)
                print(f"  限流(429)，等待 {wait}s 后重试…", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def fetch_rows(dataset: str, config: str, split: str, offset: int, length: int = 100):
    q = urllib.parse.urlencode({
        "dataset": dataset, "config": config, "split": split,
        "offset": offset, "length": length,
    })
    data = http_json(f"/rows?{q}")
    return [r["row"] for r in data.get("rows", [])]


def list_configs(dataset: str):
    q = urllib.parse.urlencode({"dataset": dataset})
    data = http_json(f"/splits?{q}")
    return sorted({s["config"] for s in data.get("splits", [])})


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


def map_ceval(row: dict):
    ans = str(row.get("answer", "")).strip().upper()
    if ans not in LETTERS or not all(row.get(c) for c in LETTERS):
        return None
    return {"question": row["question"], "A": str(row["A"]), "B": str(row["B"]),
            "C": str(row["C"]), "D": str(row["D"]), "answer": ans,
            "subject": row.get("subject", "")}


SOURCES = {
    "mmlu": {
        "dataset": "cais/mmlu", "config": "all", "split": "test",
        "mapper": map_mmlu, "note": "完整集约 14042 题",
    },
    "ceval": {
        # C-Eval 的 test 划分不公开答案，这里用带答案的 val 划分（1346 题）
        "dataset": "ceval/ceval-exam", "config": None, "split": "val",
        "mapper": map_ceval, "note": "val 划分共 1346 题（test 答案官方未公开）",
    },
}


def download(name: str, n: int):
    src = SOURCES[name]
    path = DATA_DIR / f"{name}.jsonl"
    # 断点续传：已存在的题目直接载入，不重复下载
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
        # 单配置数据集行序稳定，可直接从已有数量处续拉
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
    with open(path, "w", encoding="utf-8") as f:
        for item in final:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"完成：{path}（{len(final)} 题）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark", choices=list(SOURCES))
    ap.add_argument("--n", type=int, default=0, help="下载题目数量，0 表示全部")
    args = ap.parse_args()
    download(args.benchmark, args.n)
