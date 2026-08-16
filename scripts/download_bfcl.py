"""下载 BFCL v4（Berkeley Function Calling Leaderboard）数据集。

只下载 Non-Live 子集（官方用 AST 评分，纯 API 可评测，无需真实环境）：
  simple_python / multiple / parallel / parallel_multiple / irrelevance / format_sensitivity

数据源：GitHub ShishirPatil/gorilla 仓库
  berkeley-function-call-leaderboard/bfcl_eval/data/
  berkeley-function-call-leaderboard/bfcl_eval/data/possible_answer/（标准答案）

用法（在项目根目录执行）：
    python scripts/download_bfcl.py
输出目录：data/bfcl_v4/ 下每个子集两个文件：
    <name>.jsonl      题目（id/question/function）
    <name>_answer.jsonl 标准答案（id/ground_truth）
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/bfcl_eval/data"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "bfcl_v4"

# Non-Live 子集（AST 评分可跑）。
# 注意：format_sensitivity 只是引用其他子集 id 的列表（测格式敏感度），非独立题库，不下载。
SUBSETS = [
    "BFCL_v4_simple_python",
    "BFCL_v4_multiple",
    "BFCL_v4_parallel",
    "BFCL_v4_parallel_multiple",
    "BFCL_v4_irrelevance",  # 特殊：无 possible_answer 文件，标准答案为"拒绝调用"
]


def _http_get_one(url: str):
    """单源 GET（含 429 限流退避），返回 UTF-8 文本。"""
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
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


def http_get(url: str):
    """带镜像回退的 GET（返回 UTF-8 文本）。

    本脚本数据源是 GitHub raw（raw.githubusercontent.com），国内访问常不稳定。
    主源失败后自动回退到 ghproxy.com 加速镜像（https://ghproxy.com/<原URL>），
    提升国内下载成功率。非 GitHub raw 的 URL 不额外回退。
    """
    urls = [url]
    if url.startswith("https://raw.githubusercontent.com/"):
        urls.append("https://ghproxy.com/" + url)
    last = None
    for u in urls:
        if u != url:
            print("  主源失败，回退 ghproxy 镜像…", file=sys.stderr)
        try:
            return _http_get_one(u)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def fetch_jsonl(url: str):
    """BFCL 数据文件是每行一个 JSON 的 jsonl。"""
    raw = http_get(url)
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def save_jsonl(path: Path, items: list):
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def download(subsets=None):
    """下载 BFCL v4 子集。subsets=None 表示全部；否则传子集名列表。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for name in (subsets or SUBSETS):
        q_path = DATA_DIR / f"{name}.jsonl"
        a_path = DATA_DIR / f"{name}_answer.jsonl"
        # 断点续传：两文件都齐了就跳过
        if q_path.exists() and a_path.exists() and q_path.stat().st_size > 0 and a_path.stat().st_size > 0:
            n = sum(1 for _ in open(q_path, encoding="utf-8"))
            print(f"跳过 {name}: 已存在 {n} 题")
            total += n
            continue
        try:
            questions = fetch_jsonl(f"{BASE}/{name}.json")
            if name == "BFCL_v4_irrelevance":
                # 无关性子集：官方无 possible_answer，正确行为是"拒绝调用任何函数"
                answers = [{"id": q["id"], "ground_truth": None} for q in questions]
            else:
                answers = fetch_jsonl(f"{BASE}/possible_answer/{name}.json")
        except Exception as e:  # noqa: BLE001
            print(f"失败 {name}: {e}", file=sys.stderr)
            continue
        # 校验 id 对齐
        q_ids = {q["id"] for q in questions}
        a_ids = {a["id"] for a in answers}
        print(f"{name}: 题目 {len(questions)} 条, 答案 {len(answers)} 条, 交集 {len(q_ids & a_ids)}")
        save_jsonl(q_path, questions)
        save_jsonl(a_path, answers)
        total += len(questions)
        time.sleep(0.5)
    print(f"\n完成：{DATA_DIR} 共 {total} 题")


def main():
    download(SUBSETS)


if __name__ == "__main__":
    main()
