"""MBPP 自检：官方参考解法必须能通过我们包装的测试（抽样跑，别把自检拖太慢）。

为什么必须有这一步
------------------
MBPP 的官方测试是「直接 assert 函数名」，我们把它包成 ``def check(candidate):`` 才能在现成的
沙箱判分链路上跑。包装一旦写错（缩进、import、函数名撞名 —— #56 的函数名就叫 check，
一开始就撞出了无限递归），模型分数会整体偏低且看不出原因。所以用**官方参考解法**做对照：
参考解法过不了 = 数据/包装有问题，不是模型的问题。

用法::

    python scripts/verify_mbpp.py          # 抽样 60 题（默认，覆盖各 task_id 段）
    python scripts/verify_mbpp.py 0        # 全量 427 题（约 3~5 分钟）
    python scripts/verify_mbpp.py 10       # 只跑前 10 题
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sandbox  # noqa: E402
from app.benchmarks import get_meta  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "mbpp.jsonl"
DEFAULT_SAMPLE = 60
results: list = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE
    if not DATA.exists():
        print("没找到 data/mbpp.jsonl：先跑 python scripts/download.py mbpp")
        return 1
    rows = [json.loads(l) for l in DATA.open(encoding="utf-8")]
    meta = get_meta("mbpp")

    check("题库已落库且题量对得上", len(rows) == 427, f"实际 {len(rows)} 题")
    check("元数据：分类/需 Docker/题量", meta["category"] == "代码工程" and meta["requires_docker"],
          f"{meta['category']} docker={meta['requires_docker']}")
    check("每题都有 question / test / entry_point",
          all(r.get("question") and r.get("test") and r.get("entry_point") for r in rows))
    check("测试都包成 check 系函数（harness 可配置）",
          all(f"def {r.get('harness') or 'check'}(candidate):" in r["test"] for r in rows))
    check("题面里给了函数签名（测试按名字调用，题面必须说清）",
          all(r["entry_point"] in r["question"] for r in rows))

    sample = rows if n == 0 else rows[:n]
    print(f"\n跑官方参考解法：{len(sample)} / {len(rows)} 题" + ("（全量）" if n == 0 else "（抽样）"))
    with ThreadPoolExecutor(max_workers=4) as ex:
        outs = list(ex.map(
            lambda r: sandbox.run_python_tests(r["answer"], r["test"], r["entry_point"],
                                               harness=r.get("harness") or "check"),
            sample))
    bad = [sample[i]["task_id"] for i, o in enumerate(outs) if not o.get("passed")]
    check(f"官方参考解法全部通过（{len(sample)} 题）", not bad,
          f"未通过 {len(bad)} 题：{bad[:6]}")

    print("\n" + "=" * 72)
    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败 -> {failed}")
        return 1
    print(f"结论：全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())