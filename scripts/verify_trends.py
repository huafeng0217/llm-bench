"""综合评分趋势自检：滚动快照口径 + 按 API 名合并 + 覆盖率。

口径（用户已确认）：
- 一条线 = 一个 API 名（同名不同版本按时间串起来）；
- 每个点 = "截至该时刻，该 API 名下各基准最新完整成绩"的**跨分类等权平均**；
- 部分评测不进分子分母；演示模型不参与。

这里用合成数据钉住算法本身（不依赖库里有什么），最后再对真实库做一次只读冒烟。
用法::

    python scripts/verify_trends.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def row(api, bench, acc, t, label=None, color="#123456", eid=1, model_id=1):
    return {"api": api, "label": label or api, "color": color, "benchmark": bench,
            "t": t, "accuracy": acc, "eid": eid, "model_id": model_id}


def main_():
    # ---- 1) 空输入 ----
    empty = main._trend_series([])
    check("空输入：没有折线",
          empty["series"] == [] and empty["categories_total"] == len(main.TREND_CATEGORIES))

    # ---- 2) 单基准两次评测：后一次覆盖前一次（不是两条线、也不是求和）----
    d = main._trend_series([
        row("api-A", "mmlu", 60.0, "2026-01-01 10:00:00", eid=1),
        row("api-A", "mmlu", 80.0, "2026-01-02 10:00:00", eid=2),
    ])
    s0 = d["series"][0]
    check("同一 API 名只有一条线", len(d["series"]) == 1 and s0["api"] == "api-A")
    check("两个时间点 = 两个快照", len(s0["points"]) == 2)
    check("后一次评测覆盖前一次（综合分取自最新成绩）", s0["points"][1]["overall"] == 80.0)
    check("时间升序", s0["points"][0]["t"] < s0["points"][1]["t"])

    # ---- 3) 跨分类等权平均：两个分类各一项 → 综合分 = (60+90)/2 ----
    d = main._trend_series([
        row("api-B", "mmlu", 60.0, "2026-01-01 10:00:00", eid=1),        # knowledge
        row("api-B", "gsm8k", 90.0, "2026-01-01 11:00:00", eid=2),       # math
    ])
    last = d["series"][0]["points"][-1]
    check("跨分类等权平均（60 与 90 → 75）", last["overall"] == 75.0, str(last))
    check("覆盖率随评测推进而增长（1 → 2）",
          [p["cats_covered"] for p in d["series"][0]["points"]] == [1, 2])
    check("分类总数取注册表（排除自定义）", last["cats_total"] == len(main.TREND_CATEGORIES))

    # ---- 4) 版本：同一 API 名下不同版本记录合并成一条线，点上带各自的别名 ----
    d = main._trend_series([
        row("deepseek-flash", "mmlu", 70.0, "2026-01-01 10:00:00", label="v4.1-flash", eid=1, model_id=9),
        row("deepseek-flash", "mmlu", 75.0, "2026-02-01 10:00:00", label="v4.1-flash-1017", eid=2, model_id=23),
    ])
    pts = d["series"][0]["points"]
    check("同名不同版本 → 一条线两个点", len(pts) == 2)
    check("点上带版本别名", [p["label"] for p in pts] == ["v4.1-flash", "v4.1-flash-1017"])
    check("线颜色取最新版本记录的颜色", d["series"][0]["color"] == "#123456")

    # ---- 5) 综合分永远落在 0~100 ----
    d = main._trend_series([
        row("api-C", "mmlu", 0.0, "2026-01-01 10:00:00", eid=1),
        row("api-C", "gsm8k", 100.0, "2026-01-01 11:00:00", eid=2),
    ])
    check("综合分在 0~100 内", all(0 <= p["overall"] <= 100 for p in d["series"][0]["points"]))

    # ---- 6) 真实库只读冒烟（没数据时跳过，不算失败）----
    real = main.trends()
    if not real["series"]:
        print("[提示] 库里没有完整评测，跳过真实数据冒烟")
    else:
        check("真实数据：每条线的时间升序、分数在 0~100",
              all(all(0 <= p["overall"] <= 100 for p in ser["points"])
                  and [p["t"] for p in ser["points"]] == sorted(p["t"] for p in ser["points"])
                  for ser in real["series"]))
        check("真实数据：覆盖率不超过分类总数",
              all(p["cats_covered"] <= p["cats_total"] for ser in real["series"] for p in ser["points"]))
        check("演示模型（mock://local）不出现", all("demo" != ser["api"] for ser in real["series"]))
        check("至少有一条线有 2 个以上时间点（折线才有意义）",
              any(len(ser["points"]) >= 2 for ser in real["series"]),
              f"实际 {[len(ser['points']) for ser in real['series']]}")

    print("\n" + "=" * 72)
    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败 -> {failed}")
        return 1
    print(f"结论：全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
