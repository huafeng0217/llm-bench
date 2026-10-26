"""综合评分趋势自检：**每个点 = 一个模型版本的综合评分**。

口径（用户口径）：
- 对每个基准取**最高分**（完整评测里取；只有部分评测的项不算）；
- 分类内：独立基准各 1 项、家族（BFCL）用官方加权总分算 1 项；
- 综合评分 = 分类**等权平均**（"加权后的综合评分"）；
- 一条线 = 同一个 API 名，点按时间排；演示模型/部分评测不参与。

用合成数据钉住算法（不依赖库里有什么），最后对真实库做一次只读冒烟。
用法::

    python scripts/verify_trends.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main, scoring  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def run(bench, acc, done, total=None, t="2026-01-01 10:00:00", eid=1):
    """构造一条评测行。done/total 决定是不是"完整评测"（full_count 来自题库）。"""
    return {"benchmark": bench, "correct": acc / 100 * done, "done": done,
            "total": total if total is not None else done, "t": t, "eid": eid}


def model(api, label, runs, color="#123456"):
    return {"api": api, "label": label, "color": color, "runs": runs}


def main_():
    # ---- 1) 空输入 ----
    empty = main._trend_series({})
    check("空输入：没有点", empty["series"] == [])
    check("分类总数排除「自定义」", empty["categories_total"] == len(main.TREND_CATEGORIES))

    # ---- 2) 每个基准取**最高分**（不是最新、不是平均）----
    d = main._trend_series({
        1: model("api-A", "v1", [
            run("mmlu", 60.0, len(_rows("mmlu")), t="2026-01-01 10:00:00", eid=1),
            run("mmlu", 80.0, len(_rows("mmlu")), t="2026-01-02 10:00:00", eid=2),
            run("mmlu", 70.0, len(_rows("mmlu")), t="2026-01-03 10:00:00", eid=3),
        ]),
    })
    pts = d["series"][0]["points"]
    check("一个模型版本 = 一个点", len(pts) == 1)
    check("同一基准多次评测取最高分（80，不是最新 70）", pts[0]["overall"] == 80.0, str(pts[0]))
    check("点的 x = 该版本最后一次完整评测时间", pts[0]["t"] == "2026-01-03 10:00:00")

    # ---- 3) 跨分类等权平均：(mmlu 60 + gsm8k 90) / 2 = 75 ----
    d = main._trend_series({
        1: model("api-B", "v1", [
            run("mmlu", 60.0, len(_rows("mmlu")), t="2026-01-01 10:00:00"),
            run("gsm8k", 90.0, len(_rows("gsm8k")), t="2026-01-01 11:00:00"),
        ]),
    })
    p0 = d["series"][0]["points"][0]
    check("跨分类等权平均（60 与 90 → 75）", p0["overall"] == 75.0, str(p0))
    check("覆盖 2 个分类", p0["cats_covered"] == 2)
    check("综合评分带分类明细", set(p0["cats"]) == {"knowledge", "math"}, str(p0["cats"]))

    # ---- 4) 部分评测不算（只有部分评测的基准不进综合分，但要数出来）----
    d = main._trend_series({
        1: model("api-C", "v1", [
            run("mmlu", 90.0, len(_rows("mmlu"))),                      # 完整
            run("gsm8k", 100.0, 5, t="2026-01-02 10:00:00"),           # 部分（5 题）
        ]),
    })
    p0 = d["series"][0]["points"][0]
    check("部分评测不进综合分（100% 的 5 题不影响分数）", p0["overall"] == 90.0, str(p0))
    check("部分评测被计数（partials=1）", p0["partials"] == 1)

    # ---- 5) 一条线 = 同一个 API 名；同名不同版本按时间排 ----
    d = main._trend_series({
        1: model("deepseek-flash", "v4.1-flash", [run("mmlu", 70.0, len(_rows("mmlu")), t="2026-01-01 10:00:00")]),
        2: model("deepseek-flash", "v4.1-flash-1017", [run("mmlu", 75.0, len(_rows("mmlu")), t="2026-02-01 10:00:00")]),
        3: model("qwen3", "qwen3-max", [run("mmlu", 50.0, len(_rows("mmlu")), t="2026-01-15 10:00:00")]),
    })
    check("同名不同版本合并成一条线", len(d["series"]) == 2)
    flash = next(s for s in d["series"] if s["api"] == "deepseek-flash")
    check("版本按时间升序", [p["label"] for p in flash["points"]] == ["v4.1-flash", "v4.1-flash-1017"])
    check("点带各自的版本别名", flash["points"][1]["label"] == "v4.1-flash-1017")
    check("线颜色取最新版本的颜色", flash["color"] == "#123456")

    # ---- 6) 家族按官方权重算 1 项，而不是把子集等权 ----
    fam = "BFCL v4"
    subsets = [bid for bid, meta in main.META.items() if meta.get("family") == fam]
    if len(subsets) >= 2:
        r1, r2 = subsets[0], subsets[1]
        d = main._trend_series({1: model("api-D", "v1", [
            run(r1, 100.0, len(_rows(r1))), run(r2, 0.0, len(_rows(r2))),
        ])})
        p0 = d["series"][0]["points"][0]
        expect = scoring.family_composite(
            {r1: 100.0, r2: 0.0}, main.FAMILY_GROUPS.get(fam, []))
        check("家族按官方加权（不是子集等权 50）",
              p0["overall"] == (round(expect["score"], 2) if expect else None),
              f"实际 {p0['overall']} / 期望 {expect}")
    else:
        print("[提示] 没找到家族基准，跳过加权检查")

    # ---- 7) 没有完整评测的版本不画点 ----
    d = main._trend_series({1: model("api-E", "v1", [run("gsm8k", 100.0, 3)])})
    check("只有部分评测的版本不出现", d["series"] == [])

    # ---- 8) 真实库只读冒烟 ----
    real = main.trends()
    if not real["series"]:
        print("[提示] 库里没有完整评测，跳过真实数据冒烟")
    else:
        check("真实数据：每条线按时间升序、每个点分数在 0~100",
              all([p["t"] for p in ser["points"]] == sorted(p["t"] for p in ser["points"])
                  and all(0 <= p["overall"] <= 100 for p in ser["points"])
                  for ser in real["series"]))
        check("真实数据：覆盖率不超过分类总数",
              all(p["cats_covered"] <= p["cats_total"] for ser in real["series"] for p in ser["points"]))
        check("演示模型（mock://local）不出现", all("demo" != ser["api"] for ser in real["series"]))
        check("至少有一条线有多个版本（版本演进才有意义）",
              any(len(ser["points"]) >= 2 for ser in real["series"]),
              f"实际 {[len(ser['points']) for ser in real['series']]}")

    print("\n" + "=" * 72)
    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败 -> {failed}")
        return 1
    print(f"结论：全部 {len(results)} 项通过")
    return 0


def _rows(bench: str):
    """用题库真实题量造 done 数（完整评测）。题库没下载时退回 100。"""
    try:
        n = scoring.full_count(bench)
    except Exception:  # noqa: BLE001
        n = 0
    return list(range(n or 100))


if __name__ == "__main__":
    sys.exit(main_())
