"""校验已生成的 AI 总结是否与统计事实一致。

为什么需要它：总结「感觉乱」很难自动发现，但**乱的具体形态是可机检的** ——
实测三个不同模型都犯同一批错（把第 2 名写成弱项、把唯一模型写成强项、
拿低于随机线的分数比差距），说明这类毛病是**结构性**的、一旦结构松掉就会回归。

检查项：
  1. **分类覆盖**：categories 必须正好覆盖统计层给出的分类，不漏不多
  2. **不许自造名字**：分类名、模型名必须真实存在（实测出现过强项里填 "无" 这种占位）
  3. **模型与分类要对得上**：写进某分类的模型，必须在那个分类下真的有成绩
  4. **differences 必须是显著差异**：只能填统计层判定为「显著」的组合，
     不许把未达显著的对比写进去（这是「把噪声讲成结论」的入口）
  5. **数字可追溯**：文中出现的数字必须能在给模型的数据里找到

未覆盖：模型在 note 里提到别的分类的基准名（这种表述本身可能合法，做严格检查误报太多）。

用法::

    python scripts/verify_summaries.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, summary  # noqa: E402


def check_entry(content: dict, payload: dict, stats: dict) -> list:
    """返回这条总结的问题列表（空 = 通过）。"""
    problems = []

    valid_cats, cat_models = set(), {}
    valid_models = set()
    for e in payload["按能力分类的事实"]:
        cat = e["分类"]
        valid_cats.add(cat)
        # 「覆盖」为 0/0 表示该模型在这个分类下完全没数据
        ms = {m["模型"] for m in e["各模型在该分类的汇总"] if m.get("覆盖") != "0/0"}
        cat_models[cat] = ms
        valid_models |= ms

    sig = {(d["基准"], d["领先方"], d["落后方"])
           for d in payload["全部显著的模型间差异"]}

    cats = content.get("categories") or []
    if not isinstance(cats, list) or not cats:
        return ["categories 为空或格式不对"]

    got = {c.get("category") for c in cats}
    if got != valid_cats:
        miss, extra = valid_cats - got, got - valid_cats
        if miss:
            problems.append(f"漏了分类：{sorted(miss)}")
        if extra:
            problems.append(f"自造或多出的分类：{sorted(extra)}")

    for c in cats:
        cat = c.get("category")
        if cat not in valid_cats:
            continue  # 上面已经报过
        for m in (c.get("models") or []):
            name = m.get("model")
            if name not in valid_models:
                problems.append(f"自造模型名「{name}」（分类 {cat}）")
            elif name not in cat_models.get(cat, set()):
                problems.append(f"「{name}」在分类「{cat}」下没有成绩，却被写进该分类")

    for d in (content.get("differences") or []):
        key = (d.get("benchmark"), d.get("a"), d.get("b"))
        if key not in sig:
            problems.append(f"differences 含非显著或虚构的对比：{key}")

    for n in summary.verify_numbers(content, stats):
        problems.append(f"数字回查失败：{n} 在给模型的数据里找不到")
    return problems


def main():
    stats = summary.collect()
    payload = summary.prompt_payload(stats)
    rows = db.query("SELECT * FROM summaries ORDER BY id")
    if not rows:
        print("库里还没有总结。")
        return 0

    all_ok = True
    for r in rows:
        try:
            content = json.loads(r["content"] or "{}")
        except ValueError:
            print(f"[失败] 总结 #{r['id']}（{r['model_name']}）：content 不是合法 JSON")
            all_ok = False
            continue
        problems = check_entry(content, payload, stats)
        status = "通过" if not problems else f"失败（{len(problems)} 项）"
        print(f"[{status}] 总结 #{r['id']} 由 {r['model_name']} 生成  "
              f"分类 {len(content.get('categories') or [])} 个 / "
              f"差异 {len(content.get('differences') or [])} 条")
        for p in problems[:8]:
            print(f"        ✗ {p}")
        all_ok = all_ok and not problems

    print("=" * 74)
    print("全部总结与统计事实一致。" if all_ok else "有总结与统计事实不一致，见上。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
