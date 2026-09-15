"""统一下载入口（薄 CLI）。

实际的下载实现已经搬到 `app/benchmarks/` 下、和各自的元数据放在一起 ——
「一个基准一个文件」，加基准不用再来改这里。
本脚本只负责命令行参数解析与打印。

用法（在项目根目录执行）：
    python scripts/download.py                 # 下载全部可下载题库
    python scripts/download.py mmlu ceval      # 下载指定题库
    python scripts/download.py humaneval livecodebench

可下载题库见 `app.benchmarks.AVAILABLE`。`mmlu_sample` / `ceval_sample` 是内置样例题，无需下载。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import benchmarks as bm  # noqa: E402


def main():
    import argparse

    ap = argparse.ArgumentParser(description="统一下载 benchmark 题库到项目 data/ 目录")
    ap.add_argument("names", nargs="*",
                    help="题库名（留空 = 下载全部）。可选: " + ", ".join(bm.AVAILABLE))
    args = ap.parse_args()

    names = args.names or list(bm.DOWNLOADERS)
    bad = [n for n in names if n not in bm.DOWNLOADERS]
    if bad:
        print(f"未知题库: {bad}\n可选: {', '.join(bm.AVAILABLE)}")
        return 2

    failed = 0
    for n in names:
        print(f"\n===== 下载 {n}（{bm.AVAILABLE[n]}）=====")
        ok, msg = bm.download_one(n)
        print(f"[{'成功' if ok else '失败'}] {n}: {msg}")
        failed += 0 if ok else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
