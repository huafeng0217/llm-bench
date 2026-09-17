"""CMMLU（中文，67 学科）。"""

from .types import Benchmark
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from ._util import DATA_DIR, decode_text, http_get, http_json, write_jsonl



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


ENTRIES = [
    Benchmark(order=6, id='cmmlu',
        summary='中文多学科知识，题型比 C-Eval 更贴中文语境；看中文知识是否偏科',
        name='CMMLU',
        category='中文能力',
        lang='中文',
        status='仍有区分度',
        description='67 个学科约 1.1 万道中文选择题，包含大量中国本土知识（法律、饮食、习俗等），与 C-Eval 互补。',
        source='https://arxiv.org/abs/2306.09212',
        label='CMMLU（67 学科约 1.1 万题）',
        download=download_cmmlu),
]
