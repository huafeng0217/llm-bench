"""统一下载 benchmark 题库到项目 data/ 目录。

整合三个下载脚本，提供一个统一入口，既可命令行手动下载，也被后端
（app/main.py 的 /api/benchmarks/{id}/download）调用。

用法（在项目根目录执行）：
    python scripts/download.py                 # 下载全部可下载题库
    python scripts/download.py mmlu ceval      # 下载指定题库
    python scripts/download.py cmmlu gpqa

可下载题库见下方 AVAILABLE 字典。mmlu_sample / ceval_sample 是内置样例题，无需下载。
"""
import sys
from pathlib import Path

# 让本脚本能 import 同目录下的其它下载脚本
sys.path.insert(0, str(Path(__file__).resolve().parent))

import download_bfcl  # noqa: E402
import download_datasets  # noqa: E402
import download_more  # noqa: E402

# benchmark_id -> 说明（与 app/benchmarks.py 的 META 键对应）
AVAILABLE = {
    "mmlu": "MMLU（完整约 1.4 万题）",
    "ceval": "C-Eval（val 划分 1346 题）",
    "cmmlu": "CMMLU（67 学科约 1.1 万题）",
    "gpqa": "GPQA Diamond（198 题）",
    "mmlu_pro": "MMLU-Pro（10 选 1，约 1.2 万题）",
    "gsm8k": "GSM8K（数学应用题，约 7473 题）",
    "math500": "MATH-500（竞赛数学，500 题）",
    "BFCL_v4_simple_python": "BFCL v4 单函数（400 题）",
    "BFCL_v4_multiple": "BFCL v4 多函数选择（200 题）",
    "BFCL_v4_parallel": "BFCL v4 并行调用（200 题）",
    "BFCL_v4_parallel_multiple": "BFCL v4 并行多选（200 题）",
    "BFCL_v4_irrelevance": "BFCL v4 无关拒绝（240 题）",
}

DOWNLOADERS = {
    "mmlu": lambda: download_datasets.download("mmlu", 0),
    "ceval": lambda: download_datasets.download("ceval", 0),
    "cmmlu": download_more.download_cmmlu,
    "gpqa": download_more.download_gpqa,
    "mmlu_pro": download_more.download_mmlu_pro,
    "gsm8k": download_more.download_gsm8k,
    "math500": download_more.download_math500,
    "BFCL_v4_simple_python": lambda: download_bfcl.download(["BFCL_v4_simple_python"]),
    "BFCL_v4_multiple": lambda: download_bfcl.download(["BFCL_v4_multiple"]),
    "BFCL_v4_parallel": lambda: download_bfcl.download(["BFCL_v4_parallel"]),
    "BFCL_v4_parallel_multiple": lambda: download_bfcl.download(["BFCL_v4_parallel_multiple"]),
    "BFCL_v4_irrelevance": lambda: download_bfcl.download(["BFCL_v4_irrelevance"]),
}


def download_one(name: str):
    """下载单个题库，返回 (ok, message)。"""
    fn = DOWNLOADERS.get(name)
    if not fn:
        return False, f"未知或不可下载的题库: {name}"
    try:
        fn()
        return True, "完成"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def download_all():
    """下载全部可下载题库，返回 {name: (ok, message)}。"""
    out = {}
    for name in DOWNLOADERS:
        out[name] = download_one(name)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="统一下载 benchmark 题库到项目 data/ 目录")
    ap.add_argument("names", nargs="*", help="题库名（留空 = 下载全部）。可选: " + ", ".join(AVAILABLE))
    args = ap.parse_args()

    names = args.names or list(DOWNLOADERS)
    bad = [n for n in names if n not in DOWNLOADERS]
    if bad:
        print(f"未知题库: {bad}\n可选: {', '.join(AVAILABLE)}")
        sys.exit(2)

    for n in names:
        print(f"\n===== 下载 {n}（{AVAILABLE[n]}）=====")
        ok, msg = download_one(n)
        print(f"[{'成功' if ok else '失败'}] {n}: {msg}")
