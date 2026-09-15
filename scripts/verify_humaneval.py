"""HumanEval 自检：把 164 道题的**官方参考解法**全部送进沙箱跑一遍。

这是接真实模型之前的必要一步。如果连官方参考解法都不能全过，说明
题库、程序拼装或沙箱本身有问题 —— 此时测出来的模型分数毫无意义。
（官方的 openai/human-eval 里也有同款 sanity check。）

用法::

    python scripts/verify_humaneval.py          # 全量 164 题
    python scripts/verify_humaneval.py 20       # 只跑前 20 题（快速冒烟）
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engine, sandbox  # noqa: E402

# 代码抽取自检用的迷你题（结构与人机评测题完全一致）
ITEM = {
    "entry_point": "add",
    "question": 'def add(a: int, b: int) -> int:\n    """Return the sum of a and b."""\n',
    "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n    assert candidate(-1, 1) == 0\n",
    "answer": "    return a + b\n",
}

# (说明, 模型回复, 是否应抽到代码, 抽到的代码是否应通过测试)
EXTRACT_CASES = [
    ("完整函数（带围栏）", "```python\ndef add(a, b):\n    return a + b\n```", True, True),
    ("只有函数体（带围栏）", "```python\n    return a + b\n```", True, True),
    ("无围栏 + 前置解释", "好的，实现如下：\n\ndef add(a, b):\n    return a + b\n", True, True),
    ("被 max_tokens 截断", "```python\ndef add(a, b):\n    return a + b\n", True, True),
    ("围栏后还跟了解释", "```python\ndef add(a, b):\n    return a + b\n```\n\n这里用了加法运算符。", True, True),
    ("围栏内是错的逻辑", "```python\ndef add(a, b):\n    return a - b\n```", True, False),
    ("纯文字、没有代码", "抱歉，我无法完成这个任务。", False, False),
    ("代码有语法错误", "```python\ndef add(a, b)\n    return a + b\n```", False, False),
]


def check_extraction():
    """验证「从模型回复抽代码」这一步：模型输出形式千奇百怪，这里是最容易静默出错的地方。"""
    print("代码抽取自检\n" + "=" * 72)
    results = []
    for name, reply, want_code, want_pass in EXTRACT_CASES:
        code = engine.extract_code(reply, ITEM)
        if not want_code:
            good = code is None
            detail = "未抽到代码（符合预期）" if good else "本不该抽到却抽到了"
        elif code is None:
            good, detail = False, "应当抽到代码，实际未抽到"
        else:
            r = sandbox.run_python_tests(code, ITEM["test"], ITEM["entry_point"])
            good = r["passed"] is want_pass
            detail = f"判{'通过' if r['passed'] else '未通过'}"
        results.append((name, good))
        print(f"[{'通过' if good else '失败'}] {name:<18} {detail}")
    return results


def check_one(item: dict):
    """把参考解法当模型答案走一遍判分链路。

    参考解法和模型续写一样只是**缩进的函数体**，必须先用引擎的
    code_from_completion 补上签名，否则拼出来是缩进错误。
    """
    code = engine.code_from_completion(item, item["answer"])
    r = sandbox.run_python_tests(code, item["test"], item["entry_point"])
    return item.get("task_id") or item["entry_point"], r


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    ok, msg = sandbox.docker_available()
    print("沙箱状态:", msg)
    if not ok:
        print("\n[中止] 沙箱不可用，请先启动 Docker Desktop。")
        return 1

    items = engine.load_dataset("humaneval", limit or None)

    results = check_extraction()
    print()
    t0 = time.time()
    fails = []
    print(f"官方参考解法自检 {len(items)} 题，并发 {engine.SANDBOX_CONCURRENCY}…\n" + "=" * 72)
    with ThreadPoolExecutor(max_workers=engine.SANDBOX_CONCURRENCY) as ex:
        for i, (tid, r) in enumerate(ex.map(check_one, items), 1):
            if not r["passed"]:
                fails.append((tid, r))
            if i % 40 == 0 or i == len(items):
                print(f"  已跑 {i}/{len(items)}…")

    dt = time.time() - t0
    print("=" * 72)
    print(f"参考解法通过 {len(items) - len(fails)}/{len(items)}，用时 {dt:.1f}s"
          f"（平均 {dt / max(len(items), 1) * 1000:.0f}ms/题）")
    for tid, r in fails[:10]:
        tail = (r["stderr"] or "").strip().splitlines()
        print(f"  [失败] {tid} exit={r['exit_code']} {tail[-1][:100] if tail else ''}")
    bad = [n for n, g in results if not g]
    if bad:
        print(f"\n结论：代码抽取有 {len(bad)} 项不符预期 -> {', '.join(bad)}")
        return 1
    if fails:
        print("\n结论：参考解法都过不了，先排查题库/拼装/沙箱，不要接模型。")
        return 1
    print("\n结论：抽取与判分链路正常，参考解法全过 —— 可以接真实模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
