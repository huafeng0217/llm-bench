"""LiveCodeBench v6（竞赛题，沙箱逐条跑用例）。"""

from .types import Benchmark
import json
import sys
from pathlib import Path
from ._util import DATA_DIR, _stream_lines



LCB_BASE = "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/"


def download_livecodebench():
    """LiveCodeBench 代码生成（竞赛题，stdin/stdout 判分）。

    **只取 v6**：test6.jsonl 是**增量**文件，只含 v6 新增的题（约 175 道，
    2025 年）。选它的理由：题目足够新，几乎不可能进过训练数据 —— 这正是 LCB
    的核心价值。全量 release_v6 要 6 个文件、约 4.3GB，性价比低得多。

    两个刻意的存储决定：
      1. 私有测试用例**保持官方编码**（base64+zlib）原样存。解成明文 JSON 会膨胀
         3~5 倍，v6 从 130MB 变成 400MB+；评测时再解码只是 CPU 开销。
      2. 解码用 app/lcb.py 里的**受限 Unpickler**，而不是官方的裸 pickle.loads
         （裸反序列化会在本进程执行任意代码，而数据来自公网）。
    """
    from ..lcb import decode_tests   # 相对导入：本模块已在 app.benchmarks 包内

    url = LCB_BASE + "test6.jsonl"
    out_path = DATA_DIR / "livecodebench.jsonl"
    tmp_path = out_path.with_suffix(".jsonl.part")

    rows = 0
    nbytes = 0
    stat = {"platform": {}, "difficulty": {}, "testtype": {}, "func_name": 0, "cases": 0}
    with open(tmp_path, "w", encoding="utf-8") as f:
        for line in _stream_lines(url):
            nbytes += len(line)
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            meta = json.loads(d.get("metadata") or "{}")
            tests = decode_tests(d.get("private_test_cases"))
            for t in tests:
                tt = t.get("testtype", "?")
                stat["testtype"][tt] = stat["testtype"].get(tt, 0) + 1
            stat["cases"] += len(tests)
            if meta.get("func_name"):
                stat["func_name"] += 1
            stat["platform"][d["platform"]] = stat["platform"].get(d["platform"], 0) + 1
            stat["difficulty"][d["difficulty"]] = stat["difficulty"].get(d["difficulty"], 0) + 1

            title = (d.get("question_title") or "").strip()
            body = (d.get("question_content") or "").strip()
            f.write(json.dumps({
                "question": (title + "\n\n" + body).strip(),
                "title": title,
                "public_tests": json.loads(d.get("public_test_cases") or "[]"),
                "private_tests": d.get("private_test_cases") or "",
                "starter_code": d.get("starter_code") or "",
                "metadata": meta,
                "question_id": d.get("question_id"),
                "platform": d.get("platform"),
                "difficulty": d.get("difficulty"),
                "contest_date": d.get("contest_date"),
            }, ensure_ascii=False) + "\n")
            rows += 1
            if rows % 25 == 0:
                print(f"  已处理 {rows} 题 / {nbytes / 1e6:.1f} MB…")

    if not rows:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("LiveCodeBench 下载结果为空")
    tmp_path.replace(out_path)
    print(f"完成：livecodebench.jsonl（v6，{rows} 题，{out_path.stat().st_size / 1e6:.1f} MB）")
    print(f"  平台分布: {stat['platform']}")
    print(f"  难度分布: {stat['difficulty']}")
    print(f"  用例类型: {stat['testtype']}  私有用例共 {stat['cases']} 条"
          f"（平均 {stat['cases'] / rows:.1f} 条/题）")
    if stat["func_name"]:
        print(f"  需要按函数调用判分的题: {stat['func_name']}")


ENTRIES = [
    Benchmark(order=24, id='livecodebench',
        name='LiveCodeBench v6',
        category='代码工程',
        lang='Python',
        status='仍有区分度',
        requires_docker=True,
        description='竞赛编程题（AtCoder 112 道 + LeetCode 63 道，共 175 道，2025 年新增），全部在沙箱里**逐条跑官方测试用例**判分，全部用例通过才算做对。两种题型：AtCoder 是标准输入/输出式，LeetCode 是函数调用式（须保持类名 Solution 与方法名）。本系统只取 v6：题目足够新、几乎不可能进过训练数据，这正是它作为「无污染」基准的核心价值。注意：单条用例 6 秒超时、单题总预算 90 秒，超出的用例记为未通过。',
        source='https://arxiv.org/abs/2403.07974',
        label='LiveCodeBench v6（竞赛编程，需 Docker 沙箱，约 175 题，约 130MB）',
        download=download_livecodebench),
]
