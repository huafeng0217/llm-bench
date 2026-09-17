"""评测口径：题库派生事实 + 「一次成绩算不算数」的取数规则。

为什么单独成模块
----------------
这套口径**必须三处一致**：排行榜、成绩总览、AI 总结。以前只有 AI 总结里写了覆盖率判断，
排行榜与总览是裸的「只比正确率」，于是「跑 6 题全对」会盖过「跑 164 题 99.39%」而上榜
（实测踩过：HumanEval 的 6 题冒烟测试必须手动删掉，否则它会成为排行榜上的成绩）。
现在规则只在这里定义一次，三处都调它，不会再各自漂移。

两块内容
--------
1. **题库派生事实**（``full_count`` / ``random_baseline``）：从题库文件算出来。
   ``full_count`` 直接用 ``datasets.count_lines``（行数缓存只有一份，见那边的说明）；
   ``random_baseline`` 在本模块内按 mtime 缓存。
2. **取数规则**：``pick_best``（同一 (模型, 基准) 跑过多次，挑哪一次作代表）
   与 ``family_composite``（家族子集 → 官方加权总分）。
"""
import json
from dataclasses import dataclass
from pathlib import Path

from . import datasets, engine
from .config import CHOICES

# 覆盖率低于这个比例就算「部分评测」，不与其他完整评测直接比较
COVERAGE_OK = 0.9

# 采样多少条题目来估算随机基线（不读全量，MMLU 有 1.2 万行）
BASELINE_SAMPLE = 200


# 随机基线的缓存：路径 -> (mtime_ns, 文件大小, 计算结果)
#
# 为什么必须缓存：这个值来自题库文件、几乎不变，但每次取统计都要用，
# 而 AI 总结的统计会被前端在生成期间每 2.5 秒轮询一轮。
# 实测不缓存时单次读取要 238ms（重读 10 个题库的全部行数 + 每个采样 200 行），
# 折合生成期间约 **11 秒/分钟** 的纯 CPU 空转。
# 用 mtime+size 做 key：重新下载题库后会自动失效，不需要手工清缓存。
_FILE_CACHE: dict = {}


def _cached_by_file(kind: str, path: Path, producer):
    """按文件的 mtime+size 缓存 producer() 的结果。kind 用来区分同一文件的不同派生值。"""
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return producer()          # 文件不可用就不缓存，免得把失败结果钉死
    ck = (kind, str(path))
    hit = _FILE_CACHE.get(ck)
    if hit and hit[0] == key:
        return hit[1]
    val = producer()
    _FILE_CACHE[ck] = (key, val)
    return val


def full_count(benchmark: str) -> int:
    """题库总题量（按文件行数）。取不到返回 0 —— 调用方据此判断「无法算覆盖率」。"""
    try:
        path = datasets._dataset_path(benchmark)
    except (FileNotFoundError, OSError):
        return 0
    # 用 datasets.count_lines：题库列表显示的题数和这里算覆盖率用的题量**必须是同一个数**
    # （否则覆盖率会出现 >100% 这种莫名其妙的值），而且行数缓存只有一份，
    # 不会「列一次题库读一遍、算一次覆盖率又读一遍」。
    return datasets.count_lines(path)


def random_baseline(benchmark: str):
    """估算选择题基准的「随机猜」正确率（0~1），非选择题返回 None。

    做法是采样前若干题、算 1/选项数的平均，而不是写死一张表 —— 这样
    MMLU（4 选 1 = 25%）、MMLU-Pro（10 选 1 = 10%）、TruthfulQA（选项数不固定）
    都能自动得到合理基线，以后加新基准也不用维护。
    """
    try:
        path = engine._dataset_path(benchmark)
    except (FileNotFoundError, OSError):
        return None

    def compute():
        if engine.is_bfcl(benchmark):
            return None  # 函数调用题没有「猜中」的概念
        ks = []
        try:
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= BASELINE_SAMPLE:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if engine.is_code_item(item) or engine.is_lcb_item(item):
                        return None
                    k = sum(1 for c in CHOICES if item.get(c))
                    if k:
                        ks.append(k)
        except OSError:
            return None
        if not ks:
            return None
        return sum(1.0 / k for k in ks) / len(ks)

    return _cached_by_file("random_baseline", path, compute)


def accuracy_of(run: dict) -> float:
    """一次评测的正确率（百分数）。"""
    done = run.get("done") or 0
    return (run.get("correct") or 0) / done * 100 if done else 0.0


def coverage_of(run: dict, full: int):
    """这次评测覆盖了题库的多少（0~1）；题库题量未知时返回 None。"""
    if not full:
        return None
    return (run.get("done") or 0) / full


def is_partial(run: dict, full: int) -> bool:
    """是否为「部分评测」。题库题量未知时保守地**不**判为部分（宁可显示也不隐藏）。"""
    cov = coverage_of(run, full)
    return bool(cov is not None and cov < COVERAGE_OK)


@dataclass
class Pick:
    """从同一 (模型, 基准) 的多次评测里挑出来的结果。

    字段分清楚是为了让三个调用方各取所需，而不是各自猜：
      排行榜 / 总览  用 ``best`` + ``partial``（决定显示哪个分、要不要标「部分」）
      AI 总结        还需要 ``best_any`` 来解释「更高的那个分其实只跑了几题」
    """
    best: dict                 # 代表成绩：优先完整评测
    best_any: dict             # 不分完整与否的最高分
    best_complete: dict | None # 完整评测里的最高分；没有完整评测时为 None
    partial: bool              # best 本身是部分评测（即「只有部分评测」的情况）
    artifact: bool             # 最高分来自部分评测，而同一模型另有完整成绩可比


def pick_best(runs: list, full: int) -> Pick | None:
    """从同一个 (模型, 基准) 的所有评测里挑「代表成绩」。

    规则（**排行榜 / 成绩总览 / AI 总结共用这一条**）：

    1. **优先在「完整评测」里取最高分** —— 覆盖率 ≥ ``COVERAGE_OK`` 的那些。
    2. 只有在**完全没有完整评测**时，才退回用部分评测的最高分，
       并置 ``partial=True``，让调用方显式展示「部分 6/164」。
       这样既不会把部分成绩当成真实水平，也不会悄悄把数据藏起来。

    ``artifact=True`` 表示「更高的那个分其实来自部分评测」——
    这正是要提示用户的刷分假象（HumanEval 跑 6 题 100% 盖过 164 题的 99.39%）。
    """
    if not runs:
        return None
    best_any = max(runs, key=accuracy_of)
    complete = [r for r in runs if not is_partial(r, full)]
    if not complete:
        return Pick(best=best_any, best_any=best_any, best_complete=None,
                    partial=True, artifact=False)
    best_complete = max(complete, key=accuracy_of)
    return Pick(best=best_complete, best_any=best_any, best_complete=best_complete,
                partial=False, artifact=accuracy_of(best_any) > accuracy_of(best_complete))


def family_composite(scores_by_subset: dict, groups: list) -> dict | None:
    """家族（同一数据源的一组子集）的**官方加权总分**：组内先平均，组间再加权。

    为什么不能直接用子集平均分：BFCL v4 的官方口径是

        Overall = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%

    组内先取该组各子集的平均，再按组权重加权。子集平均分等于把官方权重 10% 的
    Non-Live（6 个子集）和 30% 的 Multi-Turn（4 个子集）等权看待，还把题量大的子集
    放大成多数票 —— 算出来的「总分」跟官方榜对不上。

    ``scores_by_subset``: {子集 id: 该子集上的**代表分数**（百分数）}
        —— 只收分数，不收评测行：调用方（排行榜）手上已经是 ``pick_best`` 之后的
        展示项，字段是 ``accuracy`` 而不是 ``correct/done``；早期版本在这里按
        ``correct/done`` 重算，结果整族分数全变 0（实测踩到，已由 verify_scoring 锁住）。
    ``groups``: ``benchmarks.FAMILY_GROUPS[家族]``，含每组的官方权重与子集清单

    **缺组时的处理**：只对「跑过的组」加权，再按这些组的权重之和归一化
    （所以分数本身仍然可比），同时把 ``weight_covered`` 报出来 ——
    本项目的 BFCL 只覆盖官方权重的 60%（缺 Agentic 40%），界面必须显示这一点，
    否则这个「总分」会被误当成完整 BFCL 成绩。
    """
    if not scores_by_subset:
        return None
    rows = []
    for g in groups:
        subset_ids = g["subsets"]
        ran = [scores_by_subset[s] for s in subset_ids if s in scores_by_subset]
        rows.append({
            "id": g["id"], "name": g["name"], "weight": g["weight"],
            "order": g.get("order", 0),
            "run": len(ran), "total": len(subset_ids),
            "score": round(sum(ran) / len(ran), 2) if ran else None,
        })
    covered = [r for r in rows if r["run"]]
    if not covered:
        return None
    wsum = sum(r["weight"] for r in covered)
    return {
        "score": round(sum(r["score"] * r["weight"] for r in covered) / wsum, 2),
        "weight_covered": round(wsum, 4),
        "weight_total": round(sum(g["weight"] for g in groups), 4),
        "groups_covered": len(covered),
        "groups_total": sum(1 for g in groups if g["subsets"]),   # 本项目没提供的组不算
        "groups": rows,
        "subsets_run": sum(r["run"] for r in rows),
        "subsets_total": sum(r["total"] for r in rows),
        # partial：有组只跑了一部分子集（分数仍按已跑子集估），或本项目本来就没提供全
        "partial": any(r["run"] < r["total"] for r in covered),
        "incomplete": len(covered) < sum(1 for g in groups if g["subsets"]),
    }
