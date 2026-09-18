"""把评测数据整理成结构化统计，供「AI 总结」使用（也供人工核对）。

为什么不让 AI 直接看原始数据
----------------------------
1. 它一定会算错算术，而且会用非常确定的语气说出来；
2. 更要紧的是，**「这个数字能不能信」必须由代码判断** —— 题量够不够、
   覆盖率是多少、两个模型的差距有没有超过误差范围。这些结论一旦交给 AI，
   它会用流畅的句子把噪声讲成结论。

所以本模块先把结论算好，AI 只负责**解释和归因**（谁强在哪、为什么、
下一步测什么）。即使换一个很弱的模型来写总结，数字也不会错。

四道护栏
--------
- ``small_sample``：题量太小的基准（如 AIME 2025 只有 30 题，一题值 3.3 分）
- ``partial_artifact``：某个「最高分」其实来自只跑了几题的部分评测
  （实测踩过：HumanEval 跑 6 题满分 100% 会盖过跑 164 题的 99.39%）
- ``partial_only``：只有部分评测、没有完整成绩可比 —— 这个分数不应与别人的完整成绩并列
  （实测踩过：某模型在 BFCL 子集上只跑了 2 题却混进排名）
- ``below_random``：选择题基准低于随机猜的水平 —— 这几乎不可能是「能力差」，
  而是答案抽取失败或接口格式不兼容，应该提示去排查而不是当成结论
"""
import hashlib
import json
import math
import re
import time

from . import db, engine, i18n, scoring
from .benchmarks import CATEGORIES, META, get_meta

# 覆盖率与「挑哪一次成绩」的口径统一放在 app/scoring.py ——
# 排行榜、成绩总览、AI 总结三处必须一致，各写一份必然会漂移。
# 本模块只保留**总结特有**的阈值。
# 题量低于这个数就提示「差距很容易被噪声吃掉」
SMALL_SAMPLE = 100
Z95 = 1.96  # 95% 置信

# 演示样例题库（mmlu_sample / ceval_sample）不该进总结：只有 12 题，
# 混进来会把「覆盖度分母」撑大，还会产生「39 个百分点以内差距无法区分」这种
# 技术上正确但毫无意义的提示。
DEMO_BENCHMARKS = {b for b, m in META.items() if m.get("status") == "演示样例"}


def _interval(pct: float, n: int) -> float:
    """95% 置信区间的半宽（百分数），用 Wilson 区间。

    为什么不用朴素二项标准误 `sqrt(p(1-p)/n)`：**满分或全错时它退化成 0**。
    实测踩到过 —— MiniMax-M3 在某个 BFCL 子集上只跑了 2 题全对，
    p=1 算出 se=0，于是「2 题全对」的对比阈值几乎为 0，看起来像是高度显著的结论。
    Wilson 区间在端点处依然给出合理宽度（2 题全对 → 半宽约 ±33 个百分点），
    小样本的正确态度就是「什么都不能断言」。
    """
    if n <= 0:
        return 0.0
    p = max(0.0, min(1.0, pct / 100.0))
    z2 = Z95 ** 2
    half = (Z95 / (1 + z2 / n)) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return half * 100


def collect() -> dict:
    """汇总所有评测，返回结构化统计。"""
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
        " ORDER BY e.id"
    )
    # 排除 mock 模型（base_url 是 mock://local）：它是用来跑通流程的假模型，
    # 混进「谁最强」的分析里只会制造噪声
    mock_ids = {m["id"] for m in db.query("SELECT id FROM models WHERE base_url LIKE 'mock://%'")}
    skipped = {"mock_models": 0, "demo_benchmarks": 0}
    filtered = []
    for r in rows:
        if r["model_id"] in mock_ids:
            skipped["mock_models"] += 1
            continue
        if r["benchmark"] in DEMO_BENCHMARKS:
            skipped["demo_benchmarks"] += 1
            continue
        filtered.append(r)
    rows = filtered

    # 1) (模型, 基准) -> 所有跑过的评测；同时算出题库题量与随机基线
    runs: dict = {}
    full_counts: dict = {}
    baselines: dict = {}
    model_names: dict = {}
    for r in rows:
        bid = r["benchmark"]
        if bid not in full_counts:
            full_counts[bid] = scoring.full_count(bid)
            baselines[bid] = scoring.random_baseline(bid)
        model_names[r["model_id"]] = r["model_name"]
        runs.setdefault((r["model_id"], bid), []).append(r)

    def acc(r):
        return r["correct"] / r["done"] * 100

    # 2) 每个 (模型, 基准) 选一个「代表成绩」。口径来自 app/scoring（与排行榜、总览共用）：
    #    优先完整评测；只有部分评测时才用它，并记下「部分成绩其实更高」这种情况。
    cells: dict = {}   # benchmark -> model_id -> cell
    caveats: list = []
    for (mid, bid), rs in runs.items():
        fc = full_counts.get(bid) or 0
        pick = scoring.pick_best(rs, fc)
        best = pick.best
        cov = (best["done"] / fc) if fc else None
        if pick.artifact:
            # 更高的那个分只跑了几题（实测：HumanEval 6/6 = 100% 压过 164 题的 99.39%）
            hi = pick.best_any
            caveats.append({
                "kind": "partial_artifact",
                "detail": (f"{model_names[mid]} 在 {get_meta(bid)['name']} 上有个更高的分 "
                           f"{acc(hi):.1f}% 只跑了 {hi['done']}/{fc} 题；"
                           f"它完整跑过的最好成绩是 {acc(best):.1f}%（{best['done']} 题）"),
            })
        elif pick.partial:
            # 只有部分评测、没有完整成绩可比：这个分数**不能**和别人的完整成绩并列比较，
            # 否则「跑 2 题全对」就会以 100% 的身份混进排名（实测见过）。
            caveats.append({
                "kind": "partial_only",
                "detail": (f"{model_names[mid]} 在 {get_meta(bid)['name']} 上只跑了 "
                           f"{best['done']}/{fc} 题（{acc(best):.1f}%），题量远少于完整评测，"
                           "该成绩不具可比性，不应与其他模型的完整成绩并列"),
            })
        cell = {
            "model_id": mid,
            "model": model_names[mid],
            "benchmark": bid,
            "benchmark_name": get_meta(bid)["name"],
            "accuracy": round(acc(best), 2),
            "n": best["done"],
            "full_count": fc,
            "coverage": round(cov, 3) if cov is not None else None,
            "partial": pick.partial,
            "eval_id": best["id"],
            "avg_latency_ms": round(best["total_latency_ms"] / best["done"]),
            "ci95": round(_interval(acc(best), best["done"]), 2),
            "baseline": round(baselines[bid] * 100, 1) if baselines.get(bid) else None,
            # 低于随机猜：这几乎不可能是「能力差」，而是答案抽取/接口不兼容。
            # 标在格子上，好让强弱判断与差异分析把它排除掉。
            "below_random": bool(baselines.get(bid) is not None
                                 and acc(best) < baselines[bid] * 100),
        }
        cells.setdefault(bid, {})[mid] = cell

        if cell["baseline"] is not None and cell["accuracy"] < cell["baseline"]:
            caveats.append({
                "kind": "below_random",
                "detail": (f"{model_names[mid]} 在 {get_meta(bid)['name']} 上只有 {cell['accuracy']:.1f}%，"
                           f"低于随机猜的 {cell['baseline']:.1f}% —— 这通常不是能力问题，"
                           "而是答案抽取或接口格式不兼容，建议先核对原始输出"),
            })

    # 3) 逐基准排名 + 两两差距是否显著
    rankings: dict = {}
    comparisons: list = []
    for bid, ms in cells.items():
        order = sorted(ms.values(), key=lambda c: -c["accuracy"])
        rankings[bid] = [{"model": c["model"], "model_id": c["model_id"],
                          "accuracy": c["accuracy"], "n": c["n"],
                          "partial": c["partial"]} for c in order]
        fc = full_counts.get(bid) or 0
        if fc and fc < SMALL_SAMPLE:
            caveats.append({
                "kind": "small_sample",
                "detail": (f"{get_meta(bid)['name']} 只有 {fc} 题，一题价值 {100 / fc:.1f} 个百分点，"
                           f"约 {2 * _interval(order[0]['accuracy'], fc):.0f} 个百分点以内的差距"
                           "无法区分"),
            })
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                a, b = order[i], order[j]
                diff = a["accuracy"] - b["accuracy"]
                # 两个 95% 区间**不重叠**才算差距超出误差范围。
                # 比 sqrt(se_a²+se_b²) 更保守，也更好解释：可以直接说「两者的置信区间没有重叠」。
                thr = a["ci95"] + b["ci95"]
                comparisons.append({
                    "benchmark": bid,
                    "a": a["model"], "b": b["model"],
                    "diff": round(diff, 2),
                    "threshold": round(thr, 2),
                    "significant": bool(diff > thr),
                })

    # 4) 覆盖率：每个模型在多少基准上有（完整）成绩
    all_benchmarks = [b for b in META if full_counts.get(b)]
    model_coverage = {}
    for mid, name in model_names.items():
        tested = [b for b in all_benchmarks if mid in cells.get(b, {})]
        complete = [b for b in tested if not cells[b][mid]["partial"]]
        model_coverage[name] = {
            "tested": len(tested), "complete": len(complete), "total": len(all_benchmarks),
            "missing": [get_meta(b)["name"] for b in all_benchmarks if b not in tested],
        }

    # 5) 置信度：可比数据够不够撑起一份总结
    comparable = sum(1 for b in all_benchmarks
                     if len([c for c in cells.get(b, {}).values() if not c["partial"]]) >= 2)
    if comparable >= 5:
        confidence = "high"
    elif comparable >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    # 去重（同一模型同一基准可能触发多条相同提示）
    seen, uniq = set(), []
    for c in caveats:
        k = (c["kind"], c["detail"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)

    return {
        "benchmarks": [{"id": b, "name": get_meta(b)["name"],
                        "category": get_meta(b)["category"],
                        "full_count": full_counts[b],
                        "baseline": round(baselines[b] * 100, 1) if baselines.get(b) else None,
                        "models_with_data": len(cells.get(b, {}))}
                       for b in all_benchmarks],
        "models": [{"model_id": mid, "name": name, **model_coverage[name]}
                   for mid, name in model_names.items()],
        "cells": cells,
        "rankings": rankings,
        "comparisons": comparisons,
        "caveats": uniq,
        "confidence": confidence,
        "comparable_benchmarks": comparable,
        "skipped": skipped,
    }


def fingerprint(stats: dict) -> str:
    """输入矩阵的指纹：任何分数/题量/任务号变了，指纹就变。

    用来判断「已有的总结是不是过期了」—— 数据没变就不该重新烧一遍 token。
    """
    key = []
    for bid in sorted(stats["cells"]):
        for mid in sorted(stats["cells"][bid]):
            c = stats["cells"][bid][mid]
            key.append(f"{bid}:{mid}:{c['accuracy']}:{c['n']}:{c['eval_id']}")
    return hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16]


def capabilities(stats: dict) -> list:
    """按**能力分类**汇总 —— AI 总结的一级维度。

    为什么一级维度是分类而不是逐个模型：用户要的是「在不同类型基准上各模型的能力强弱」，
    按模型铺开的话，得自己在脑子里把 10 个基准重组成 6 个分类。

    刻意**不跨基准平均分数**：各基准题量、难度、随机基线都不同，平均出来的数字没有意义。
    这里只汇总「基准内的名次」和「差距是否显著」这些真正可比的事实。

    强/弱的定义刻意收窄，这是为了治「模型把第 2 名写成弱项」那类毛病 ——
    实测三个不同模型都犯同一批错（19~26 条强/弱项里 58%~69% 不成立），
    根因是给了它们两个**必须填**的桶，它们就会硬填：
      强项 = 该基准第 1 且可比模型 ≥2 且领先显著
      弱项 = 该基准末位 且可比模型 ≥2 且落后显著
    其余一律只记成「领先/落后但未达显著」的计数，不叫强项也不叫弱项。
    """
    def blank(name):
        return {"model": name, "first": 0, "last": 0, "comparable": 0,
                "significant_lead": [], "significant_lag": [],
                "lead_not_significant": 0, "lag_not_significant": 0,
                "missing": [], "below_random": [], "coverage": "0/0",
                "only_model_benchmarks": 0, "ranks": []}

    by_cat: dict = {}
    for bid, order in stats["rankings"].items():
        meta = get_meta(bid)
        cells = stats["cells"][bid]
        e = by_cat.setdefault(meta["category"],
                              {"category": meta["category"], "benchmarks": [], "models": {}})
        # 低于随机线的成绩不参与强弱判断：那说明的是接口/抽取不兼容，不是能力差距
        usable = [r for r in order if not cells[r["model_id"]]["below_random"]]
        # 双向索引：comparisons 里是按「排名高的在前」存的，若只按一个方向查，
        # 算「落后」时永远查不到（踩过：显著落后一直为空）。
        sig = {}
        for c in stats["comparisons"]:
            if c["benchmark"] == bid:
                sig[(c["a"], c["b"])] = c["significant"]
                sig[(c["b"], c["a"])] = c["significant"]
        n = len(usable)
        lead_sig = bool(n >= 2 and sig.get((usable[0]["model"], usable[1]["model"])))
        e["benchmarks"].append({
            "id": bid, "name": meta["name"], "comparable_models": n,
            "ranking": [{"model": r["model"], "accuracy": r["accuracy"], "n": r["n"]}
                        for r in usable],
            "all_models_ranked": [r["model"] for r in order],
            "leader": usable[0]["model"] if n else None,
            "lead_significant": lead_sig,
            "below_random": [cells[r["model_id"]]["model"] for r in order
                             if cells[r["model_id"]]["below_random"]],
            "partial": [r["model"] for r in usable if r["partial"]],
        })
        for i, r in enumerate(usable):
            m = e["models"].setdefault(r["model"], blank(r["model"]))
            m["comparable"] += 1
            m["ranks"].append(i + 1)   # 名次（1 = 最好），供跨基准求「平均名次」
            if i == 0:
                m["first"] += 1
            if n >= 2 and i == n - 1:
                m["last"] += 1
            if i == 0 and n >= 2:
                if sig.get((r["model"], usable[1]["model"])):
                    m["significant_lead"].append(meta["name"])
                else:
                    m["lead_not_significant"] += 1
            if n >= 2 and i == n - 1 and sig.get((r["model"], usable[n - 2]["model"])):
                m["significant_lag"].append(meta["name"])

    out = []
    for cat, e in by_cat.items():
        n_bm = len(e["benchmarks"])
        for m in stats["models"]:
            name = m["name"]
            mm = e["models"].setdefault(name, blank(name))
            mm["missing"] = [b["name"] for b in e["benchmarks"]
                             if name not in b["all_models_ranked"]]
            mm["below_random"] = [b["name"] for b in e["benchmarks"] if name in b["below_random"]]
            mm["coverage"] = f"{mm['comparable']}/{n_bm}"
            # 只有它一个模型测过的基准：那不是「第一」，只是「唯一」，不能当强弱依据
            mm["only_model_benchmarks"] = sum(
                1 for b in e["benchmarks"] if b["comparable_models"] == 1
                and name in b["all_models_ranked"])
        e["models"] = sorted(e["models"].values(),
                             key=lambda x: (-x["first"], -x["comparable"], x["model"]))
        out.append(e)
    # 按 CATEGORIES 顺序输出，和界面上其它板块保持一致
    idx = {c["name"]: i for i, c in enumerate(CATEGORIES)}
    out.sort(key=lambda e: idx.get(e["category"], 99))
    return out


def differences(stats: dict) -> list:
    """只保留**显著**的两两差异，并剔除涉及低于随机线格子的对比。

    低于随机线的成绩（如 6% vs 82%）产生的「差距显著」是**无意义**的 ——
    它说明的是接口不兼容，不是能力差距。实测它们会刷出一大片「落后 69.7 个百分点、
    差距显著」，把真正有价值的结论整个淹没。剔掉之后这一节才会短而有信息量。
    """
    out = []
    for c in stats["comparisons"]:
        if not c["significant"]:
            continue
        cells = stats["cells"].get(c["benchmark"], {})
        by_name = {v["model"]: v for v in cells.values()}
        if by_name.get(c["a"], {}).get("below_random") or by_name.get(c["b"], {}).get("below_random"):
            continue
        meta = get_meta(c["benchmark"])
        out.append({"benchmark": meta["name"], "category": meta["category"],
                    "a": c["a"], "b": c["b"], "diff": c["diff"], "threshold": c["threshold"]})
    out.sort(key=lambda x: -x["diff"])
    return out


def model_overview(stats: dict) -> list:
    """每个模型的全局总览：覆盖度 + 平均名次 + 显著领先/落后。

    「平均名次」是**跨基准唯一站得住的聚合量** —— 分数不能跨基准平均
    （题量与随机基线都不同，AIME 的 50 分远强于 MMLU 的 50 分），但名次可以：
    第 1 名在哪都是第 1 名。即便如此也要标注它是在几个基准上算出来的，
    只测过 1 个基准的模型不能靠这个判高低。
    """
    caps = capabilities(stats)
    total = len(stats["rankings"])
    agg: dict = {}
    for e in caps:
        for m in e["models"]:
            a = agg.setdefault(m["model"], {
                "model": m["model"], "ranks": [], "benchmarks": 0, "first": 0, "last": 0,
                "significant_lead": [], "significant_lag": [],
                "lead_not_significant": 0, "lag_not_significant": 0,
                "missing": [], "below_random": [], "only_model": 0, "by_category": {}})
            a["ranks"] += m["ranks"]
            a["benchmarks"] += m["comparable"]
            a["first"] += m["first"]
            a["last"] += m["last"]
            a["significant_lead"] += m["significant_lead"]
            a["significant_lag"] += m["significant_lag"]
            a["lead_not_significant"] += m["lead_not_significant"]
            a["lag_not_significant"] += m["lag_not_significant"]
            a["missing"] += m["missing"]
            a["below_random"] += m["below_random"]
            a["only_model"] += m["only_model_benchmarks"]
            if m["comparable"]:
                a["by_category"][e["category"]] = m["coverage"]
    out = []
    for a in agg.values():
        n = len(a["ranks"])
        a["ranked_benchmarks"] = n
        a["avg_rank"] = round(sum(a["ranks"]) / n, 2) if n else None
        a["total_benchmarks"] = total
        a["coverage"] = f"{a['benchmarks']}/{total}"
        del a["ranks"]
        out.append(a)
    # 覆盖多的排前面，其次平均名次好的
    out.sort(key=lambda x: (-x["benchmarks"], x["avg_rank"] if x["avg_rank"] is not None else 99))
    return out


def prompt_payload(stats: dict) -> dict:
    """给 LLM 的结构化输入。

    一级维度是**能力分类**，而且每个分类下的「事实」都已算好：名次、领先/落后是否显著、
    谁没测、哪些低于随机线。模型只负责把这些组织成语言，不负责判断。
    """
    caps = capabilities(stats)
    diffs = differences(stats)
    n_cmp = len(stats["comparisons"])
    bm_meta = {b["id"]: b for b in stats["benchmarks"]}
    return {
        "本次数据的置信度": stats["confidence"],
        "模型总览": [{
            "模型": m["model"],
            "覆盖": m["coverage"],
            "平均名次": m["avg_rank"],
            "参评基准数": m["ranked_benchmarks"],
            "第一名次数": m["first"],
            "末位次数": m["last"],
            "显著领先的基准": m["significant_lead"],
            "显著落后的基准": m["significant_lag"],
            "领先但未达显著": m["lead_not_significant"],
            "落后但未达显著": m["lag_not_significant"],
            "各分类覆盖": m["by_category"],
            **({"未测基准": m["missing"]} if m["missing"] else {}),
            **({"低于随机线": m["below_random"]} if m["below_random"] else {}),
            **({"只有它一个模型测过的基准数": m["only_model"]} if m["only_model"] else {}),
        } for m in model_overview(stats)],
        "按能力分类的事实": [{
            "分类": e["category"],
            "基准": [{
                "基准": b["name"],
                "题量": bm_meta.get(b["id"], {}).get("full_count"),
                # 「参与比较的模型」= 在这个基准上有成绩的模型。比较以这个集合为准，
                # 没测的模型只是"没参加"，不代表这个基准"数据不足"。
                "参与比较的模型数": b["comparable_models"],
                "参与比较的模型": [r["model"] for r in b["ranking"]],
                "排名": b["ranking"],
                **({"第一名是否显著领先": b["lead_significant"]}
                   if b["comparable_models"] >= 2
                   else {"说明": "只有 1 个模型有成绩，缺少其他模型的对照，无法判断它是否突出"}),
                "两两对比": [{"A": c["a"], "B": c["b"], "分差": c["diff"],
                              "显著性阈值": c["threshold"], "差距显著": c["significant"]}
                             for c in stats["comparisons"] if c["benchmark"] == b["id"]],
                "未测的模型（能力未知）": [m["model"] for m in e["models"]
                                          if m["model"] not in b["all_models_ranked"]],
                **({"成绩低于随机线，不参与比较": b["below_random"]} if b["below_random"] else {}),
                **({"部分评测": b["partial"]} if b["partial"] else {}),
            } for b in e["benchmarks"]],
            "各模型在该分类的汇总": [{
                "模型": m["model"],
                "覆盖": m["coverage"],
                "第一名次数": m["first"],
                "末位次数": m["last"],
                "显著领先的基准": m["significant_lead"],
                "显著落后的基准": m["significant_lag"],
                "领先但未达显著": m["lead_not_significant"],
                "落后但未达显著": m["lag_not_significant"],
                **({"未测基准（能力未知）": m["missing"]} if m["missing"] else {}),
                **({"低于随机线": m["below_random"]} if m["below_random"] else {}),
                **({"只有它一个模型测过的基准数": m["only_model_benchmarks"]}
                   if m["only_model_benchmarks"] else {}),
            } for m in e["models"] if m["comparable"]],
            "该分类未测的模型（能力未知，不代表能力弱）":
                [m["model"] for m in e["models"] if not m["comparable"]],
            # 「数据不足」只指**缺少对照**：整个分类下没有任何一个基准有 ≥2 个模型参与比较。
            # 不能因为"某个模型没参加"就说这个分类数据不足 —— 那是两回事。
            "该分类是否缺乏对照": not any(b["comparable_models"] >= 2 for b in e["benchmarks"]),
        } for e in caps],
        "全部显著的模型间差异": [{
            "分类": d["category"], "基准": d["benchmark"],
            "领先方": d["a"], "落后方": d["b"],
            "领先多少个百分点": d["diff"], "显著性阈值": d["threshold"],
        } for d in diffs],
        "对比总数": n_cmp,
        "其中未达显著（不能区分）": n_cmp - len(diffs),
        "数据问题": [f"[{c['kind']}] {c['detail']}" for c in stats["caveats"]],
    }


SYSTEM_PROMPT = """你是一个严谨的大模型评测分析师。你会拿到一份**已经算好的**评测数据统计，\
请按**能力分类**写一份中文总结。

**这份总结描述的是「参与本次评测的这几个模型之间的相对表现」**，不是对模型能力的绝对判定。
这个定位在 overview 里说明一次就够了，后面**不要再反复声明**。

你的职责是把已经算好的事实组织成通顺、有信息量的语言。判断（谁强谁弱、差距算不算差距）
已经由程序做完了，你不需要自己算，但**你要敢把结论说出来** —— 通篇只会说"无法判断"
的总结是没有价值的。

硬性规则：

1. **只能使用给定数据里出现的数字**。不得推算、不得引用你对这些模型的外部印象或训练知识。
   引用分数时**写精确值**（如 22.5、24.5），不要写成「20% 出头」这种约数 ——
   约数会让人没法核对，也会被数字回查标为可疑。
2. **描述名次和分差是事实陈述，直接写，不要加免责。** 例如
   「GLM-5.3-Flash 在 GPQA Diamond 上以 75.76 居首，领先 deepseek-flash 7.07 个百分点」
   —— 这句话完全正确，不需要任何限定词。**不要**写成"领先但无法判断是否更强"这种把话堵死的句子。
3. **显著性只用来限定「结论强度」，不是用来禁止描述**：
   - `差距显著` 为 true → 可以说「明显领先」「优势明显」「差距拉开」。
   - `差距显著` 为 false → 说「领先幅度较小」「优势不明显」「名次靠前但优势有限」。
     **只有当分差小于该基准「显著性阈值」的三分之一时**，才说「两者表现接近」。
   - **不要**把未达显著写成"无法判断谁更强"。名次是确定的，只是优势幅度不足以说是能力差距。
   - **这一类说明只在 overview 里集中交代一次**，不要每个基准、每个模型都重复一遍显著性口径。
4. **一个基准只有 1 个模型有成绩时**：说「缺少其他模型的对照数据，无法判断它在这个基准上是否突出」。
   **不要**把这个基准说成"数据不足" —— 这个基准本身没问题，只是没有对照。
5. **未测的模型**：一句话说明「未测，能力未知」即可，**不要当成缺陷反复强调**，
   更不要因此说某个分类"数据不足"。
   **「数据不足 / 缺乏对照」只指参与比较的模型太少**（看「参与比较的模型数」与「该分类是否缺乏对照」），
   **不指某个模型没参加**。一个分类里有 4 个模型参与比较，就是数据充分，不要喊数据不足。
6. **没有有效成绩的格子**（数据里标了「成绩低于随机线，不参与比较」的那些）：
   正文里**只当它没有成绩**来处理，写「未纳入比较」或干脆不提。
   **不要**在正文里解释成"疑似接口不兼容 / 答案抽取失败 / 低于随机线" ——
   那属于内部数据检查（由终端脚本单独输出），**不写进这份要分享出去的总结**。
   `next_steps` 里也只用中性的表述，例如「补测该基准」「复核该项成绩」。
7. `differences` 只能填「全部显著的模型间差异」里**已经存在**的组合，
   不得自己新增对比，也不得把未达显著的对比写进去 —— 这一节是"有把握的差距"，只放确凿的。
8. 分类名、模型名、基准名**必须原样照抄**输入里的字符串，不要简写、不要翻译、不要自造。
   用不到的字段宁可不写，也不要填 "无" 之类占位。
9. **不要给出跨基准的"综合最强模型"排名**。各基准题量、难度、随机基线都不同，
   算术平均没有意义；只能在**单个基准内**比强弱。（「平均名次」是允许引用的聚合量。）
10. 只输出 JSON，不要输出任何解释文字、不要加 markdown 代码围栏。

输出 JSON 结构：

{
  "headline": "一句话结论（不超过 40 字）。**必须点名具体的模型名或分类名**，不要写成「多数差距不显著」这种没有信息量的元评论",
  "overview": "整体情况，3~5 句：参与评测的模型与基准范围、整体格局、本次最值得注意的一条是什么。关于「差距显著与否」的口径说明**只在这一段交代一次**",
  "models": [
    {
      "model": "模型名（原样照抄）",
      "note": "2~3 句：覆盖度、平均名次、在各分类的相对表现、明确领先或落后的基准。领先幅度小的地方说「优势不明显」即可，不要写成无法判断"
    }
  ],
  "categories": [
    {
      "category": "分类名（原样照抄输入里的「分类」）",
      "summary": "该分类的整体格局，2~4 句。可以直接说谁在这个分类里更突出、谁落后，带上数字",
      "benchmarks": [
        {
          "benchmark": "基准名（原样照抄）",
          "note": "1~2 句：写出该基准的排名与分数（带题量），说明第一名的领先幅度是明显还是有限；若只有 1 个模型有成绩，说明缺少对照"
        }
      ],
      "insufficient": "**只在「该分类是否缺乏对照」为 true 时才写**（即该分类下没有任何基准有 ≥2 个模型参与比较）；否则留空字符串"
    }
  ],
  "differences": [
    {"a": "领先方（照抄）", "b": "落后方（照抄）", "benchmark": "基准名（照抄）",
     "note": "一句话解释这个差距意味着什么"}
  ],
  "next_steps": ["下一步建议测什么、为什么（基于数据里的缺口，不要泛泛而谈）"]
}

关于「写全」的要求（这份总结的价值就在于覆盖完整，不要自作精简）：

- **models 必须给「模型总览」里的每一个模型都写一条，一个都不许漏。**
- **categories[].benchmarks 必须给该分类下的每一个基准都写一条，一个都不许跳过。**
  哪怕该基准上大家差距不大，也要把名次和分数写出来 —— 那是事实，不是没话说。
- **不要把多个基准合并成一句话**。
- 按上面两条写全（每个模型一条 + 每个基准一条），整篇自然会有 800 字以上；
  如果你写出来很短，几乎一定是漏了基准或漏了模型，请回头补齐。

关于「models」数组里每个分类那部分的填写要求：
- 只列**真的有话可说**的模型（通常 1~3 个）：在该分类下有显著领先/显著落后，
  或有值得提醒的覆盖度问题。
- **不要**给每个模型都写一条。特别是「该分类完全未测的模型」，
  它们只需要在 insufficient 里一句话带过，**不要**逐个列成条目。
"""


def build_messages(stats: dict) -> list:
    """组装给总结模型的对话消息。"""
    payload = prompt_payload(stats)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            "以下是本次评测的统计结果（JSON）：\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
            + "\n\n请按系统提示里的 JSON 结构输出总结。"},
    ]


def parse_result(text: str) -> dict:
    """从模型回复里取出 JSON（容忍代码围栏和前后废话）。"""
    if not text:
        raise ValueError(i18n.t("模型返回为空"))
    s = text.strip()
    fence = re.search(r"```(?:json)?[ \t]*\n(.*?)(?:```|\Z)", s, re.DOTALL | re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    # 退而求其次：找第一段平衡的 {...}
    start = s.find("{")
    if start < 0:
        raise ValueError(i18n.t("回复里没有 JSON"))
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError(i18n.t("JSON 不完整（可能被 max_tokens 截断）"))


def _numbers(obj, out: set):
    """递归收集一个结构里出现的所有数字。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in re.finditer(r"\d+(?:\.\d+)?", obj):
            out.add(float(m.group()))


def _texts(obj, out: list):
    """递归收集一个结构里所有的字符串。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _texts(v, out)
    elif isinstance(obj, str):
        out.append(obj)


# 模糊约数的标志词。模型偶尔会把精确值说成约数（「两者均只有 20% 出头」，
# 实际是 22.5 与 24.5）—— 那不算编造数字，检查器不该报成幻觉。
VAGUE_MARKERS = ("约", "多", "出头", "左右", "上下", "余", "近", "略超", "不到", "以上", "以下")


def verify_numbers(result: dict, stats: dict) -> list:
    """回查总结里出现的数字，找出输入数据里没有的。

    这是防幻觉最有效的一招，成本极低：模型编造的数字必然在输入里找不到。
    只查「可能被编造的」数字：
      - 小数一律查（正确率几乎都是小数形式）
      - 大于等于 20 的整数查（题量、任务号）
      - 小于 20 的整数放过（名次、「3 个模型」这类用法太普遍，查了全是误报）
    容差 0.15 是为了放过正常四舍五入（88.57 → 88.6）。
    """
    allowed = set()
    _numbers(prompt_payload(stats), allowed)
    texts = []
    _texts(result, texts)
    bad = set()
    for t in texts:
        for m in re.finditer(r"\d+(?:\.\d+)?", t):
            raw = m.group()
            val = float(raw)
            if "." not in raw and val < 20:
                continue
            if any(abs(val - a) <= 0.15 for a in allowed):
                continue
            # 模糊约数放过：窗口里有「约/多/出头/左右」这类词，且确有允许值落在 [val, val+15%]。
            # 这不是编造，而是把精确值说成约数。
            window = t[max(0, m.start() - 8): m.end() + 8]
            if (any(w in window for w in VAGUE_MARKERS)
                    and any(val <= a <= val + max(1.0, val * 0.15) for a in allowed)):
                continue
            bad.add(raw)
    return sorted(bad)


# 生成时的 token 额度阶梯。思考型模型的 reasoning 也占 max_tokens：
# 实测代码类基准上单题推理能烧掉 3 万 token，8192 很可能还没开始写 JSON 就被截断。
SUMMARY_MAX_TOKENS = (8192, 32768)


async def generate(model_cfg: dict, stats: dict, timeout_s: int = 300) -> dict:
    """调选定模型生成总结。

    额度阶梯：先按 8192 问，**只在「解析不出 JSON」且被截断时**才放大到 32768 重问。
    若没被截断却仍解析不出，说明模型没按格式输出，再给额度也没用，直接报错。

    返回 {content(解析后的 dict), prompt_tokens, completion_tokens, latency_ms, budget}。
    """
    from openai import AsyncOpenAI  # 局部导入：只有真的要生成时才需要

    client = AsyncOpenAI(base_url=model_cfg["base_url"],
                         api_key=model_cfg["api_key"] or "EMPTY",
                         timeout=timeout_s, max_retries=1)
    messages = build_messages(stats)
    ptok = ctok = lat = 0
    last_err = None
    for budget in SUMMARY_MAX_TOKENS:
        t0 = time.time()
        r = await client.chat.completions.create(
            model=model_cfg["name"], messages=messages, temperature=0, max_tokens=budget)
        lat += int((time.time() - t0) * 1000)
        usage = getattr(r, "usage", None)
        ptok += getattr(usage, "prompt_tokens", 0) or 0
        ctok += getattr(usage, "completion_tokens", 0) or 0
        text = (r.choices[0].message.content or "").strip()
        finish = getattr(r.choices[0], "finish_reason", None)
        try:
            content = parse_result(text)
            return {"content": content, "prompt_tokens": ptok, "completion_tokens": ctok,
                    "latency_ms": lat, "budget": budget}
        except ValueError as e:  # noqa: PERF203
            last_err = e
            if finish != "length":
                break  # 没被截断还解析不出 → 模型没按格式走，加大额度也没用
    raise RuntimeError(i18n.t("模型没有返回可解析的 JSON：{err}", err=last_err)
                       + ("（已放大额度仍未成功）" if ctok else ""))