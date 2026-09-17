"""AI 总结的「统计层」自检。

这个模块只做一件事：把评测数据算成结论（排名、覆盖率、显著性、异常），
再交给 AI 去解释。**如果它算错了，AI 会把这个错误讲得非常流畅可信** ——
所以统计层必须能单独验证，而且要在接模型之前验证（这一步不烧 token）。

覆盖四类检查：
  1. 刷分假象护栏（取数规则 `scoring.pick_best`）：最高分来自部分评测时必须能识别出来
     —— 更完整的行为测试在 `scripts/verify_scoring.py`
  2. 显著性判定：真实分数代入，看结论符不符合常识
  3. 数据清理：mock 模型与演示样例题库不能进入总结
  4. 一致性：每个格子的正确率必须和数据库里的 correct/done 对得上

用法::

    python scripts/verify_summary.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, scoring, summary  # noqa: E402


def run(correct, done, eid=1):
    """构造一条评测记录（_pick_best 只关心 correct/done）。"""
    return {"correct": correct, "done": done, "id": eid, "total_latency_ms": 0}


def check_pick_best():
    """刷分假象护栏：界面上显示的「最高分」是不是靠只跑几题刷出来的。"""
    print("刷分假象护栏（pick_best）\n" + "=" * 74)
    fc = 164  # 用 HumanEval 的真实题量
    cases = [
        ("只有一次完整评测", [run(163, 164)], False, 164),
        ("6 题满分 vs 164 题 99.39%（假象）", [run(6, 6), run(163, 164)], True, 164),
        ("部分评测但分数更低（不是假象）", [run(5, 6), run(163, 164)], False, 164),
        ("只有部分评测，没有完整可比", [run(6, 6)], False, None),
        ("多次完整评测取最高", [run(150, 164), run(163, 164)], False, 164),
    ]
    ok = True
    for name, runs, want_art, want_bc in cases:
        pick = scoring.pick_best(runs, fc)
        bc = pick.best_complete
        art = pick.artifact
        got_bc = bc["done"] if bc else None
        good = (art == want_art) and (got_bc == want_bc)
        ok = ok and good
        print(f"[{'通过' if good else '失败'}] {name:<32} 判为假象={art!s:<5} 完整最好={got_bc}")
    return ok


def check_significance():
    """显著性：真实数字代入，结论要符合常识。"""
    print("\n显著性判定（Wilson 95% 区间不重叠才算显著）\n" + "=" * 74)
    cases = [
        # (说明, 分数A, 题量A, 分数B, 题量B, 期望显著)
        ("同一模型两次 LCB 重跑（89.14 vs 88.57）", 89.14, 175, 88.57, 175, False),
        ("LCB: deepseek-flash vs qwen3.8-flash", 88.57, 175, 52.00, 175, True),
        ("TruthfulQA: deepseek-flash vs MiniMax-M3", 82.86, 776, 6.31, 776, True),
        ("GPQA 上差 7 个点（题量 198，应为噪声）", 75.76, 198, 68.69, 198, False),
        ("AIME 2025 上差 13 个点（题量 30，应为噪声）", 83.33, 30, 70.00, 30, False),
        # 端点案例：满分时朴素标准误会退化成 0，Wilson 不会 —— 这正是换算法的原因
        ("2 题全对 vs 200 题 77.5%（应判不显著）", 100.0, 2, 77.5, 200, False),
    ]
    ok = True
    for name, pa, na, pb, nb, want in cases:
        thr = summary._interval(pa, na) + summary._interval(pb, nb)
        diff = abs(pa - pb)
        sig = diff > thr
        good = sig == want
        ok = ok and good
        print(f"[{'通过' if good else '失败'}] {name:<38} 差 {diff:>5.2f} 阈值 {thr:>5.2f} "
              f"-> {'显著' if sig else '不显著'}")
    return ok


def check_hygiene(stats):
    """mock 模型与演示样例题库不能进入总结。"""
    print("\n数据清理\n" + "=" * 74)
    names = {m["name"] for m in stats["models"]}
    bms = {b["id"] for b in stats["benchmarks"]}
    checks = [
        ("mock 模型（demo）已排除", "demo" not in names),
        ("演示样例题库已排除", not (bms & summary.DEMO_BENCHMARKS)),
        ("至少还有真实模型", len(names) >= 1),
        ("至少还有真实基准", len(bms) >= 1),
    ]
    for name, good in checks:
        print(f"[{'通过' if good else '失败'}] {name}")
    print(f"      排除了 rows: {stats['skipped']}")
    return all(g for _, g in checks)


def check_consistency(stats):
    """每个格子的正确率必须和数据库里的原始记录对得上（防算错）。"""
    print("\n与数据库的一致性\n" + "=" * 74)
    bad = []
    for bid, ms in stats["cells"].items():
        for mid, cell in ms.items():
            row = db.query_one("SELECT correct, done FROM evaluations WHERE id=?", (cell["eval_id"],))
            if not row:
                bad.append((bid, mid, "评测不存在"))
                continue
            want = round(row["correct"] / row["done"] * 100, 2)
            if abs(want - cell["accuracy"]) > 0.01 or row["done"] != cell["n"]:
                bad.append((bid, mid, f"库里 {want}%/{row['done']} 题，统计里 {cell['accuracy']}%/{cell['n']} 题"))
    if bad:
        for b in bad[:5]:
            print(f"[失败] {b[0]} × {b[1]}: {b[2]}")
        return False
    n = sum(len(m) for m in stats["cells"].values())
    print(f"[通过] {n} 个格子的分数与题量全部与数据库一致")
    return True


def check_partial_invariant(stats):
    """不变式：凡是「部分评测」的格子，都必须有对应的数据问题提示。

    这条比逐个断言具体文案更耐用：不管数据怎么变，只要有部分评测漏了提示就是 bug。
    """
    print("\n部分评测护栏（不变式检查）\n" + "=" * 74)
    bad = []
    n_partial = 0
    for bid, ms in stats["cells"].items():
        for mid, c in ms.items():
            if not c["partial"]:
                continue
            n_partial += 1
            hit = any(("partial" in x["kind"]) and c["model"] in x["detail"]
                      and c["benchmark_name"] in x["detail"] for x in stats["caveats"])
            if not hit:
                bad.append(f"{c['model']} × {c['benchmark_name']}（{c['n']}/{c['full_count']} 题）")
    if bad:
        for b in bad[:5]:
            print(f"[失败] 部分评测未给出提示: {b}")
        return False
    print(f"[通过] {n_partial} 个部分评测格子全部有对应提示")
    if n_partial:
        ex = next(c for c in stats["caveats"] if c["kind"] == "partial_only")
        print(f"       例如: {ex['detail'][:100]}")
    return True


def report_caveats(stats):
    """把程序判定的「数据问题」完整打出来。

    **这里才是它的归属**：这些是评测自身的质量信号（某项成绩低于随机线、
    最高分来自只跑了几题的部分评测…），属于内部事务，不该出现在给读者看的总结里 ——
    那份总结是要能分享出去的，写「我们的接口可能有问题」既不相关也不合适。
    所以网页上刻意不展示，需要时跑这个脚本。

    永远返回 True：这一节是**报告**，不是校验（有问题不代表统计层算错了）。
    """
    print("\n数据问题清单（内部检查，不展示在网页上）\n" + "=" * 74)
    caveats = stats.get("caveats") or []
    if not caveats:
        print("  无。")
        return True
    label = {"below_random": "低于随机线", "partial_artifact": "刷分假象",
             "partial_only": "只有部分评测", "small_sample": "题量偏小"}
    for c in sorted(caveats, key=lambda x: x["kind"]):
        print(f"  [{label.get(c['kind'], c['kind'])}] {c['detail']}")
    return True


def main():
    good = check_pick_best()
    good = check_significance() and good
    stats = summary.collect()
    good = check_hygiene(stats) and good
    good = check_partial_invariant(stats) and good
    good = check_consistency(stats) and good
    report_caveats(stats)
    print("=" * 74)
    print(f"统计层结论：{'可信' if good else '有问题，先修统计再接模型'}"
          f"（置信度 {stats['confidence']}，可比较基准 {stats['comparable_benchmarks']} 个）")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
