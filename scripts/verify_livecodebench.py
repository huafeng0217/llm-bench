"""LiveCodeBench 自检：验证「测试用例解码 + 容器内判分器」这一整套是否可信。

注意：LiveCodeBench **不提供参考解法**（不像 HumanEval 有 canonical_solution），
所以没法做「官方解法全过一遍」那种自检。这里改为**喂已知对错的解法**，
逐项验证判分器能不能正确区分 通过 / 答案错误 / 超时 / 运行错误，
并且两种题型（AtCoder 的 stdin 式、LeetCode 的函数调用式）都要覆盖。

判分器一旦有偏差（比如把超时当成通过），后面所有模型的分数都是错的，
所以这个自检比 HumanEval 那份更重要。

用法::

    python scripts/verify_livecodebench.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engine, sandbox  # noqa: E402
from app.lcb import all_tests, decode_tests  # noqa: E402

# ---------- 真实题目：AtCoder abc387_b「9x9 Sum」（stdin 式）----------
# 求 9x9 乘法表中所有不等于 X 的项之和（81 项，重复值按格子重复计）。
GOOD_STDIN = (
    "x = int(input())\n"
    "print(sum(i * j for i in range(1, 10) for j in range(1, 10) if i * j != x))\n"
)
BAD_STDIN = "x = int(input())\nprint(2025)\n"          # 只有 X=11 时碰巧对
LOOP_STDIN = "while True:\n    pass\n"
RAISE_STDIN = "x = int(input())\nraise ValueError('boom')\n"

# ---------- 合成题目：LeetCode 式函数调用判分（真实数据里 63 道 LeetCode 都是这种）----------
# 注意官方的参数约定：input 按**换行**拆开、每行 JSON 解析后作为一个位置参数。
# 所以两参数函数写成 "1\n2"，而「单个嵌套列表参数」写成一行 "[[1, 2], [3, 4]]"。
# 后者正是真实题 3708 的形状 —— 一开始我按「整体解包」实现，这里就全挂了。
FAKE_FN_ITEM = {
    "question": "给定两个整数 a 和 b，返回它们的和。",
    "starter_code": "class Solution:\n    def addTwo(self, a: int, b: int) -> int:\n        ",
    "metadata": {"func_name": "addTwo"},
    "public_tests": [
        {"input": "1\n2", "output": "3", "testtype": "functional"},
        {"input": "-5\n5", "output": "0", "testtype": "functional"},
        {"input": "100\n250", "output": "350", "testtype": "functional"},
    ],
    "private_tests": "",
}
GOOD_FN = "class Solution:\n    def addTwo(self, a, b):\n        return a + b\n"
GOOD_FN_TOPLEVEL = "def addTwo(a, b):\n    return a + b\n"   # 没写在 class 里，也应能调到
BAD_FN = "class Solution:\n    def addTwo(self, a, b):\n        return a - b\n"
BAD_FN_NAME = "class Solution:\n    def addTwoWrong(self, a, b):\n        return a + b\n"

# 单参数 + 嵌套列表（复现真实题 3708 的形状）
FAKE_LIST_ITEM = {
    "question": "给定二维数组，返回所有元素之和。",
    "starter_code": "class Solution:\n    def total(self, grid: List[List[int]]) -> int:\n        ",
    "metadata": {"func_name": "total"},
    "public_tests": [
        {"input": "[[1, 2], [3, 4]]", "output": "10", "testtype": "functional"},
        {"input": "[[5]]", "output": "5", "testtype": "functional"},
    ],
    "private_tests": "",
}
GOOD_LIST = "class Solution:\n    def total(self, grid):\n        return sum(sum(r) for r in grid)\n"


def check_decode():
    """全部 175 题都要能解码，且用例类型只有 stdin / functional 两种。"""
    print("测试用例解码自检\n" + "=" * 74)
    items = engine.load_dataset("livecodebench")
    bad, n_cases, kinds = [], 0, {}
    for it in items:
        try:
            ts = all_tests(it)
        except Exception as e:  # noqa: BLE001
            bad.append((it.get("question_id"), str(e)[:60]))
            continue
        if not ts:
            bad.append((it.get("question_id"), "没有任何用例"))
            continue
        n_cases += len(ts)
        for t in ts:
            k = t.get("testtype", "?")
            kinds[k] = kinds.get(k, 0) + 1
    print(f"题库 {len(items)} 题，用例合计 {n_cases} 条，类型分布 {kinds}")
    if bad:
        print(f"[失败] {len(bad)} 题有问题，例如 {bad[:3]}")
        return False
    print("[通过] 全部题目解码正常、都带用例")
    return True


def run_case(name, code, cases, fn_name, want_pass, want_verdict=None, **kw):
    r = sandbox.run_stdio_tests(code, cases, fn_name=fn_name, **kw)
    verdicts = r["verdicts"]
    ok = r["passed"] is want_pass
    if ok and want_verdict:
        ok = want_verdict in verdicts
    detail = f"通过 {r['n_pass']}/{r['n_total']} 条"
    if verdicts:
        first_bad = next((v for v in verdicts if v != "OK"), None)
        if first_bad:
            detail += f"，首个异常判定 {first_bad}"
    print(f"[{'通过' if ok else '失败'}] {name:<26} {r['duration_ms']:>6}ms  {detail}")
    return ok


def check_judge():
    items = engine.load_dataset("livecodebench")
    real = next(i for i in items if i.get("question_id") == "abc387_b")
    real_cases = all_tests(real)
    print(f"\n真实题 abc387_b（stdin 式，{len(real_cases)} 条用例）")
    print("=" * 74)
    results = [
        run_case("正确解法（应全过）", GOOD_STDIN, real_cases, "", True),
        run_case("错误解法（应判 WA）", BAD_STDIN, real_cases, "", False, "WA"),
        run_case("死循环（应判 TLE）", LOOP_STDIN, real_cases, "", False, "TLE",
                 case_timeout=2, budget=6, timeout=60),
        run_case("抛异常（应判 RE）", RAISE_STDIN, real_cases, "", False, "RE"),
    ]

    print(f"\n合成题 addTwo（函数调用式，{len(FAKE_FN_ITEM['public_tests'])} 条用例）")
    print("=" * 74)
    fc = all_tests(FAKE_FN_ITEM)
    results += [
        run_case("class 内实现（应全过）", GOOD_FN, fc, "addTwo", True),
        run_case("顶层函数（应全过）", GOOD_FN_TOPLEVEL, fc, "addTwo", True),
        run_case("逻辑写错（应判 WA）", BAD_FN, fc, "addTwo", False, "WA"),
        run_case("方法名不对（应判 RE）", BAD_FN_NAME, fc, "addTwo", False, "RE"),
    ]

    print(f"\n合成题 total（单参数 + 嵌套列表，{len(FAKE_LIST_ITEM['public_tests'])} 条用例）")
    print("=" * 74)
    lc_cases = all_tests(FAKE_LIST_ITEM)
    results += [
        run_case("单参数解包正确（应全过）", GOOD_LIST, lc_cases, "total", True),
    ]
    return all(results)


def check_prompt():
    """两种题型必须给出不同的作答要求，否则模型会写错形状的代码。"""
    items = engine.load_dataset("livecodebench")
    stdin_item = next(i for i in items if i.get("question_id") == "abc387_b")
    fn_item = next(i for i in items if (i.get("metadata") or {}).get("func_name"))
    a, b = engine.build_lcb_prompt(stdin_item), engine.build_lcb_prompt(fn_item)
    ok = "标准输入" in a and "Solution" not in a and "Solution" in b and "方法名不变" in b
    print(f"[{'通过' if ok else '失败'}] 题型 prompt 区分（stdin 式 / 函数式）")
    return ok


def main():
    ok, msg = sandbox.docker_available()
    print("沙箱状态:", msg)
    if not ok:
        print("\n[中止] 沙箱不可用，请先启动 Docker Desktop。")
        return 1
    good = check_decode()
    good = check_judge() and good
    good = check_prompt() and good
    print("=" * 74)
    print("结论：判分器可信 —— 两种题型与四类失败判定均正确。"
          if good else "结论：有项目不符预期，先修判分器再接模型。")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
