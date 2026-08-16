"""下载 CMMLU 与 GPQA Diamond 题库，转为项目统一 jsonl 格式。

数据源（均为非 gated、可直接下载）：
  - CMMLU：GitHub haonan-li/CMMLU 仓库 data/test/ 下 67 个学科 csv
  - GPQA Diamond：HuggingFace 镜像 dongboklee/GPQA-diamond（198 题）

用法（在项目根目录执行）：
    python scripts/download_more.py cmmlu
    python scripts/download_more.py gpqa
    python scripts/download_more.py all

输出文件（每行一个 JSON，字段与现有题库一致）：
    data/cmmlu.jsonl   {"question","A","B","C","D","answer","subject"}
    data/gpqa.jsonl    {"question","A","B","C","D","answer","subject"}
"""
import argparse
import ast
import csv
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

try:  # 让中文/特殊字符打印不因 GBK 控制台报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _http_get_one(url: str, timeout: int = 60) -> bytes:
    """单源 GET（含 429 限流退避），返回 bytes。"""
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
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


def http_get(url: str, timeout: int = 60) -> bytes:
    """带镜像回退的 GET（返回 bytes）。

    CMMLU 的数据源是 GitHub raw（raw.githubusercontent.com），国内访问常不稳定，
    主源失败后自动回退到 ghproxy.com 加速镜像（https://ghproxy.com/<原URL>）。

    注意：GPQA 走 datasets-server 的 rows API，而 hf-mirror 只镜像 HF 文件、
    不提供 rows API 等价端点（datasets-server.hf-mirror.com 不存在），
    故 datasets-server 暂不做域名回退，仅靠 429 退避重试。
    """
    urls = [url]
    if url.startswith("https://raw.githubusercontent.com/"):
        urls.append("https://ghproxy.com/" + url)
    last = None
    for u in urls:
        if u != url:
            print("  主源失败，回退 ghproxy 镜像…", file=sys.stderr)
        try:
            return _http_get_one(u, timeout)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def http_json(url: str, timeout: int = 60):
    return json.loads(http_get(url, timeout).decode("utf-8"))


def decode_text(b: bytes) -> str:
    """优先 UTF-8，失败回退 GB18030，保证中文题库不被解坏。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def write_jsonl(path: Path, items: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


# ---------- CMMLU ----------

CMMLU_RAW = "https://raw.githubusercontent.com/haonan-li/CMMLU/master"
CMMLU_TREE = "https://api.github.com/repos/haonan-li/CMMLU/git/trees/master?recursive=1"


def cmmlu_subjects() -> list:
    """从 GitHub API 获取 data/test/ 下的学科 csv 文件名列表（自动适配增删）。"""
    data = http_json(CMMLU_TREE)
    subs = []
    for t in data.get("tree", []):
        p = t.get("path", "")
        if p.startswith("data/test/") and p.endswith(".csv"):
            subs.append(p[len("data/test/"):-len(".csv")])
    return sorted(subs)


def download_cmmlu():
    subs = cmmlu_subjects()
    path = DATA_DIR / "cmmlu.jsonl"
    done_subjects = set()
    out = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    it = json.loads(line)
                    done_subjects.add(it.get("subject"))
                    out.append(it)
        print(f"检测到已有 {len(out)} 题（{len(done_subjects)} 个学科），将增量续传")
    print(f"CMMLU：共 {len(subs)} 个学科（test 集）")
    total = 0
    for i, sub in enumerate(subs, 1):
        if sub in done_subjects:
            continue
        url = f"{CMMLU_RAW}/data/test/{urllib.parse.quote(sub)}.csv"
        try:
            raw = http_get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  失败 {sub}: {e}", file=sys.stderr)
            continue
        text = decode_text(raw)
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            print(f"  空文件 {sub}", file=sys.stderr)
            continue
        # 按表头定位列（兼容有无索引列的不同版本）
        header = {h.strip(): idx for idx, h in enumerate(rows[0])}
        need = ("Question", "A", "B", "C", "D", "Answer")
        if not all(k in header for k in need):
            print(f"  表头不符 {sub}: {rows[0]}", file=sys.stderr)
            continue
        added = 0
        for r in rows[1:]:
            try:
                question = r[header["Question"]].strip()
                A = r[header["A"]].strip()
                B = r[header["B"]].strip()
                C = r[header["C"]].strip()
                D = r[header["D"]].strip()
                answer = r[header["Answer"]].strip().upper()
            except IndexError:
                continue
            if not question or not all((A, B, C, D)) or answer not in ("A", "B", "C", "D"):
                continue
            out.append({"question": question, "A": A, "B": B, "C": C, "D": D,
                        "answer": answer, "subject": sub})
            added += 1
        total += added
        print(f"  [{i}/{len(subs)}] {sub}: +{added} 题")
        time.sleep(0.2)
    write_jsonl(path, out)
    print(f"完成：{path}（共 {len(out)} 题）")


# ---------- GPQA Diamond ----------

GPQA_DS = "dongboklee/GPQA-diamond"
GPQA_DS_Q = urllib.parse.quote(GPQA_DS, safe="")


def parse_gpqa(text: str):
    """解析 'Question: xxx\\nA. a\\nB. b\\nC. c\\nD. d' → 题目 + 四个选项。"""
    text = (text or "").strip()
    parts = re.split(r"\n(?=[A-D]\.\s)", text)
    if len(parts) != 5:
        return None
    q = re.sub(r"^Question:\s*", "", parts[0]).strip()
    opts = []
    for i, p in enumerate(parts[1:5]):
        m = re.match(rf"^{chr(65 + i)}\.\s*(.*)$", p, re.DOTALL)
        if not m:
            return None
        opts.append(m.group(1).strip())
    if not q or not all(opts):
        return None
    return {"question": q, "A": opts[0], "B": opts[1], "C": opts[2], "D": opts[3]}


def download_gpqa():
    path = DATA_DIR / "gpqa.jsonl"
    size = http_json(f"https://datasets-server.huggingface.co/size?dataset={GPQA_DS_Q}")
    total = size["size"]["dataset"]["num_rows"]
    print(f"GPQA Diamond：共 {total} 题")
    out = []
    offset = 0
    length = 100  # datasets-server 单次 rows 上限
    failed = 0
    while offset < total:
        url = (f"https://datasets-server.huggingface.co/rows?dataset={GPQA_DS_Q}"
               f"&config=default&split=train&offset={offset}&length={length}")
        data = http_json(url)
        rows = data.get("rows", [])
        if not rows:
            break
        for r in rows:
            row = r["row"]
            parsed = parse_gpqa(row.get("question"))
            if not parsed:
                failed += 1
                print(f"  解析失败，跳过: {row.get('q_id')}", file=sys.stderr)
                continue
            parsed["answer"] = str(row.get("answer", "")).strip().upper()
            parsed["subject"] = "diamond"
            out.append(parsed)
        offset += len(rows)
        time.sleep(0.2)
    write_jsonl(path, out)
    print(f"完成：{path}（共 {len(out)} 题" + (f"，解析失败 {failed}" if failed else "") + "）")


# ---------- MMLU-Pro / GSM8K / MATH-500 ----------

CHOICES = [chr(65 + i) for i in range(10)]  # A-J


def fetch_ds_rows(dataset: str, config: str, split: str) -> list:
    """从 datasets-server rows API 分页拉取全部行，返回 list[dict]。"""
    ds_q = urllib.parse.quote(dataset, safe="")
    size = http_json(f"https://datasets-server.huggingface.co/size?dataset={ds_q}")
    total = size["size"]["dataset"]["num_rows"]
    print(f"{dataset}：共 {total} 行")
    out = []
    offset = 0
    length = 100  # datasets-server 单次 rows 上限
    while offset < total:
        url = (f"https://datasets-server.huggingface.co/rows?dataset={ds_q}"
               f"&config={config}&split={split}&offset={offset}&length={length}")
        data = http_json(url)
        rows = data.get("rows", [])
        if not rows:
            break
        out.extend(r["row"] for r in rows)
        offset += len(rows)
        time.sleep(0.2)
    return out


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


def download_gsm8k():
    """GSM8K：数学应用题（数值答案），train 集约 7473 题（test 无公开答案）。"""
    rows = fetch_ds_rows("openai/gsm8k", "main", "train")
    items = []
    for r in rows:
        q = r.get("question", "")
        # 官方 answer 含解题过程，最终答案在「#### 」之后
        m = re.search(r"####\s*(-?\d[\d,]*(?:\.\d+)?)", r.get("answer", ""))
        if not q or not m:
            continue
        items.append({"question": q, "answer": m.group(1).replace(",", ""), "subject": "gsm8k"})
    write_jsonl(DATA_DIR / "gsm8k.jsonl", items)
    print(f"完成：gsm8k.jsonl（共 {len(items)} 题）")


def download_math500():
    """MATH-500：竞赛数学（答案多为 LaTeX 表达式），500 题。

    注意：LaTeX 答案的判分是近似判分（engine 的 numeric_match 对复杂表达式
    覆盖有限），对简单数值/分数答案较准，复杂代数/几何表达式可能误判。
    """
    rows = fetch_ds_rows("HuggingFaceH4/MATH-500", "default", "test")
    items = []
    for r in rows:
        q = r.get("problem", "")
        if not q:
            continue
        items.append({"question": q, "answer": r.get("answer", "").strip(), "subject": r.get("subject", "")})
    write_jsonl(DATA_DIR / "math500.jsonl", items)
    print(f"完成：math500.jsonl（共 {len(items)} 题）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark", choices=["cmmlu", "gpqa", "mmlu_pro", "gsm8k", "math500", "all"])
    args = ap.parse_args()
    if args.benchmark in ("cmmlu", "all"):
        download_cmmlu()
    if args.benchmark in ("gpqa", "all"):
        download_gpqa()
    if args.benchmark in ("mmlu_pro", "all"):
        download_mmlu_pro()
    if args.benchmark in ("gsm8k", "all"):
        download_gsm8k()
    if args.benchmark in ("math500", "all"):
        download_math500()
