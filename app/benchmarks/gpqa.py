"""GPQA Diamond（研究生级理化生）。"""

from .types import Benchmark
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from ._util import DATA_DIR, http_json, write_jsonl



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


ENTRIES = [
    Benchmark(order=7, id='gpqa',
        name='GPQA Diamond',
        category='科学推理',
        lang='英文',
        status='仍有区分度',
        description='198 道研究生级理化生选择题（GPQA Diamond 子集），由领域专家编写且无法通过搜索作弊，是当前区分前沿模型推理能力的主力基准。',
        source='https://arxiv.org/abs/2311.12022',
        label='GPQA Diamond（198 题）',
        download=download_gpqa),
]
