"""BFCL v4（伯克利函数调用排行榜），11 个 Non-Live 子集。

它们是同一份数据源的不同子集，共用一套下载逻辑，所以放在同一个文件里 ——
强行拆成 11 个文件只会把同一段代码抄 11 遍。"""

from .types import Benchmark
import json
import sys
import time
from ._util import DATA_DIR, http_get

from functools import partial
from pathlib import Path


# BFCL 的题库落在 data/bfcl_v4/ 子目录（别的基准都是 data/<id>.jsonl）
BFCL_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "bfcl_v4"


BASE = "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/bfcl_eval/data"


SUBSETS = [
    "BFCL_v4_simple_python",
    "BFCL_v4_simple_java",         # 多语言：Java 版单函数
    "BFCL_v4_simple_javascript",   # 多语言：JavaScript 版单函数
    "BFCL_v4_multiple",
    "BFCL_v4_parallel",
    "BFCL_v4_parallel_multiple",
    "BFCL_v4_irrelevance",  # 特殊：无 possible_answer 文件，标准答案为"拒绝调用"
    "BFCL_v4_multi_turn_base",           # 多轮对话：基础
    "BFCL_v4_multi_turn_long_context",   # 多轮：长上下文
    "BFCL_v4_multi_turn_miss_func",      # 多轮：缺少函数
    "BFCL_v4_multi_turn_miss_param",     # 多轮：缺少参数
]


MULTI_TURN_SUBSETS = {
    "BFCL_v4_multi_turn_base", "BFCL_v4_multi_turn_long_context",
    "BFCL_v4_multi_turn_miss_func", "BFCL_v4_multi_turn_miss_param",
}


FUNC_DOC_NAMES = [
    "gorilla_file_system", "math_api", "memory_kv", "memory_rec_sum", "memory_vector",
    "message_api", "posting_api", "ticket_api", "trading_bot", "travel_booking",
    "vehicle_control", "web_search",
]


FUNC_DOC_DIR = BFCL_DATA_DIR / "func_doc"


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


def download_func_docs():
    """下载 multi_turn 子集用到的工具定义文档（JSONL）到 data/bfcl_v4/func_doc/。"""
    FUNC_DOC_DIR.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for name in FUNC_DOC_NAMES:
        p = FUNC_DOC_DIR / f"{name}.jsonl"
        if p.exists() and p.stat().st_size > 0:
            continue
        try:
            items = fetch_jsonl(f"{BASE}/multi_turn_func_doc/{name}.json")
        except Exception as e:  # noqa: BLE001
            print(f"工具文档失败 {name}: {e}", file=sys.stderr)
            continue
        save_jsonl(p, items)
        n_new += 1
        time.sleep(0.3)
    print(f"工具文档：{FUNC_DOC_DIR}（{len(FUNC_DOC_NAMES)} 个文件，本次新增 {n_new}）")


def download(subsets=None):
    """下载 BFCL v4 子集。subsets=None 表示全部；否则传子集名列表。"""
    BFCL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    names = subsets or SUBSETS
    # 涉及 multi_turn 子集时，一并准备工具定义文档
    if any(n in MULTI_TURN_SUBSETS for n in names):
        download_func_docs()
    total = 0
    for name in names:
        q_path = BFCL_DATA_DIR / f"{name}.jsonl"
        a_path = BFCL_DATA_DIR / f"{name}_answer.jsonl"
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


ENTRIES = [
    Benchmark(order=16, id='BFCL_v4_irrelevance',
        name='BFCL v4 · 无关拒绝',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 无关性子集：问题与给出的工具毫无关系，正确行为是拒绝调用任何函数，测模型是否过度调用工具（幻觉防御）。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 无关拒绝（240 题）',
        download=partial(download, ["BFCL_v4_irrelevance"])),
    Benchmark(order=19, id='BFCL_v4_multi_turn_base',
        name='BFCL v4 · 多轮对话',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 多轮基础子集：一次评测包含多轮用户请求（文件系统、交易、差旅、消息等场景），模型须在每轮选对函数与参数。判分按每轮 AST 匹配，整题各轮全对才算通过。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 多轮对话（200 题）',
        download=partial(download, ["BFCL_v4_multi_turn_base"])),
    Benchmark(order=20, id='BFCL_v4_multi_turn_long_context',
        name='BFCL v4 · 多轮长上下文',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 多轮长上下文子集：多轮对话 + 更长的工具文档与上下文，考验长上下文下保持工具调用正确性。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 多轮长上下文（200 题）',
        download=partial(download, ["BFCL_v4_multi_turn_long_context"])),
    Benchmark(order=21, id='BFCL_v4_multi_turn_miss_func',
        name='BFCL v4 · 多轮缺函数',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 多轮缺函数子集：某轮所需的函数不在可用工具列表里，正确行为是不调用（或按需澄清），测模型是否会硬调不存在的工具。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 多轮缺函数（200 题）',
        download=partial(download, ["BFCL_v4_multi_turn_miss_func"])),
    Benchmark(order=22, id='BFCL_v4_multi_turn_miss_param',
        name='BFCL v4 · 多轮缺参数',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 多轮缺参数子集：某轮所需参数缺失，正确行为是先向用户追问而非硬调，测多轮交互中的澄清能力。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 多轮缺参数（200 题）',
        download=partial(download, ["BFCL_v4_multi_turn_miss_param"])),
    Benchmark(order=13, id='BFCL_v4_multiple',
        name='BFCL v4 · 多函数选择',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 多函数子集：同时给出多个工具，模型须在候选集中挑出正确的那一个，考验函数辨识能力。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 多函数选择（200 题）',
        download=partial(download, ["BFCL_v4_multiple"])),
    Benchmark(order=14, id='BFCL_v4_parallel',
        name='BFCL v4 · 并行调用',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 并行子集：单轮请求中须同时调用多个不同函数，考验并行工具调用的编排能力。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 并行调用（200 题）',
        download=partial(download, ["BFCL_v4_parallel"])),
    Benchmark(order=15, id='BFCL_v4_parallel_multiple',
        name='BFCL v4 · 并行多选',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 并行+多函数组合子集：多个问题×多个候选函数，须并行调用且各自选对函数，难度最高。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 并行多选（200 题）',
        download=partial(download, ["BFCL_v4_parallel_multiple"])),
    Benchmark(order=17, id='BFCL_v4_simple_java',
        name='BFCL v4 · 单函数(Java)',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 单函数子集的 Java 版本：给一个问题与一个 Java 工具定义，模型须选对函数并填对参数，测跨语言的函数调用能力。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 单函数-Java（100 题）',
        download=partial(download, ["BFCL_v4_simple_java"])),
    Benchmark(order=18, id='BFCL_v4_simple_javascript',
        name='BFCL v4 · 单函数(JS)',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='BFCL v4 单函数子集的 JavaScript 版本：给一个问题与一个 JS 工具定义，模型须选对函数并填对参数，测跨语言的函数调用能力。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 单函数-JS（50 题）',
        download=partial(download, ["BFCL_v4_simple_javascript"])),
    Benchmark(order=12, id='BFCL_v4_simple_python',
        name='BFCL v4 · 单函数',
        category='Agent / 工具调用',
        lang='英文',
        status='仍有区分度',
        description='伯克利函数调用排行榜（BFCL）v4 单函数子集：给一个问题与一个工具定义，模型须选择正确函数并填对参数。官方用 AST 匹配评分。',
        source='https://gorilla.cs.berkeley.edu/leaderboard',
        label='BFCL v4 单函数（400 题）',
        download=partial(download, ["BFCL_v4_simple_python"])),
]
