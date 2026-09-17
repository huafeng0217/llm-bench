"""用官方人工标注集校准裁判：**换裁判之前，先量出它有多准。**

为什么必须先做这一步
-------------------
安全类基准的分数是裁判给的，而不同裁判之间的一致性并不可观 ——
JailbreakBench 专门发布了 ``judge_comparison`` 数据集就是为了测这件事。
所以「换个 API 模型当裁判」不能靠感觉，得先回答两个问题：

1. 它和**人工标注**的一致率是多少？
2. 它和**官方裁判**（HarmBench 的 13B 分类器 / JBB 的 harmbench_cf）像不像？

两份标注集都是官方给的、带人工标签的：

    HarmBench  data/classifier_val_sets/text_behaviors_val_set.json
               301 个行为 × 2 条生成 = 602 条，每条有 human_0/1/2 三个人工判定，
               外加官方分类器（cls）与多个 LLM 裁判的判定 → 「真值」取人工多数
    JBB        HF JailbreakBench/JBB-Behaviors 的 judge_comparison 配置
               300 条，含 human1/2/3 + human_majority + 四个 LLM 裁判判定

**这个脚本会真的调用裁判模型（花 token）**，所以默认只取 40 条小样本、并支持 --dry-run
先看预估花费。跑完把结果写成 JSON 存到 data/（该目录不进版本库）。

用法::

    python scripts/calibrate_judge.py --dry-run                 # 只看预估，不调用
    python scripts/calibrate_judge.py --judge 15 --n 40         # 两个数据集各 40 条
    python scripts/calibrate_judge.py --judge 14 --which jbb    # 只测 JBB
"""
import argparse
import asyncio
import csv
import io
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, safety  # noqa: E402
from app.benchmarks.harmbench import BEHAVIORS_URL, VAL_URL, fetch_behaviors  # noqa: E402

JBB_DS = "JailbreakBench/JBB-Behaviors"


def _api(path_query: str):
    url = "https://datasets-server.huggingface.co" + path_query
    req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(8 * (a + 1))
                continue
            raise
    raise RuntimeError("datasets-server 一直限流")


def load_harmbench_val(n: int, only_standard: bool = True) -> list:
    """取 HarmBench 官方验证集的样本：(行为原文, 生成, 人工多数标签, 官方分类器标签)。

    ``only_standard=True`` 时**只取 standard 行为** —— 因为基准真正跑的就是那 200 条
    （contextual 要两轮铺垫、copyright 用哈希比对），拿它们来校准等于在量一个我们不会跑的分部，
    而且用「无上下文」的提示词去判 contextual 题本来就会偏低，会误导结论。
    """
    val = json.loads(urllib.request.urlopen(
        urllib.request.Request(VAL_URL, headers={"User-Agent": "llm-bench/0.1"}),
        timeout=120).read().decode("utf-8"))
    rows = fetch_behaviors({"standard"} if only_standard
                           else {"standard", "contextual", "copyright"})
    behaviors = {r["BehaviorID"]: r["Behavior"] for r in rows}
    flat = []
    for bid, items in val.items():
        if bid not in behaviors:            # 被上面的过滤条件排除掉了
            continue
        for it in items:
            humans = [it.get(f"human_{i}") for i in range(3)]
            humans = [int(h) for h in humans if h is not None]
            if not humans:
                continue
            flat.append({
                "behavior": behaviors.get(bid, ""),
                "behavior_id": bid,
                "response": it.get("generation") or "",
                "human_majority": int(sum(humans) / len(humans) >= 0.5),
                "official": it.get("cls"),          # HarmBench 的 13B 分类器判定
                "method": it.get("method"),
            })
    flat.sort(key=lambda x: (x["behavior_id"], x["response"]))   # 固定顺序，样本可复现
    step = max(1, len(flat) // n)
    return flat[::step][:n]


def load_jbb_comparison(n: int) -> list:
    """取 JBB judge_comparison 的样本：(goal, 回复, 人工多数标签, 官方 harmbench_cf 标签)。"""
    import urllib.parse
    q = urllib.parse.quote(JBB_DS, safe="")
    out, offset = [], 0
    while len(out) < n:
        d = _api(f"/rows?dataset={q}&config=judge_comparison&split=test&offset={offset}&length=100")
        rows = d.get("rows", [])
        if not rows:
            break
        for r in rows:
            row = r["row"]
            if row.get("human_majority") is None:
                continue
            out.append({
                "behavior": str(row.get("goal") or ""),
                "behavior_id": f"jbb_{row.get('Index')}",
                "response": str(row.get("target_response") or ""),
                "human_majority": int(row["human_majority"]),
                "official": row.get("harmbench_cf"),
                "method": None,
            })
        offset += len(rows)
        time.sleep(0.3)
    step = max(1, len(out) // n)
    return out[::step][:n]


def est_tokens(cases: list, kind: str) -> int:
    tmpl = len(safety.JUDGE_KINDS[kind].prompt)
    return sum((tmpl + len(c["behavior"]) + len(c["response"])) // 3 + 20 for c in cases)


async def run_set(name: str, cases: list, kind: str, judge_cfg: dict, params: dict,
                  concurrency: int = 4) -> dict:
    """并发跑完一个数据集（串行 80 次要十几分钟，并发 4 路约几分钟，花费一样）。"""
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(c):
        nonlocal done
        async with sem:
            try:
                r = await safety.judge(kind, c["behavior"], c["response"], judge_cfg, params)
            except Exception as e:  # noqa: BLE001
                # 单条调用失败（限流 / 网络）不该让整场校准挂掉，记为判分失败
                r = {"bad": None, "raw": f"调用失败：{type(e).__name__}: {str(e)[:120]}",
                     "prompt_tokens": 0, "completion_tokens": 0}
        async with lock:
            done += 1
            if done % 10 == 0:
                print(f"    {name}: {done}/{len(cases)}…", flush=True)
        return r

    results = await asyncio.gather(*(one(c) for c in cases))
    agree_h = agree_o = official_n = fails = tok = 0
    details = []
    for c, r in zip(cases, results):
        tok += r["prompt_tokens"] + r["completion_tokens"]
        got = r["bad"]
        # 逐条明细也要存：只看总一致率没法定位分歧，而分歧里往往藏着「哪一类题判不准」
        details.append({"behavior_id": c.get("behavior_id"), "human": c["human_majority"],
                        "official": c.get("official"), "ours": None if got is None else int(got),
                        "raw": (r.get("raw") or "")[:200],
                        "tokens": r["prompt_tokens"] + r["completion_tokens"]})
        if got is None:
            fails += 1
            continue
        agree_h += int(int(got) == c["human_majority"])
        if c["official"] is not None:
            official_n += 1
            agree_o += int(int(got) == int(c["official"]))
    n = len(cases)
    # 偏差方向比总一致率更有诊断价值：一个「系统性宽松」的裁判比随机出错的裁判危险得多 ——
    # 它会把不安全判成安全，让模型显得比实际更安全（安全评测里最糟的方向）。
    lenient = strict = 0
    for c, r in zip(cases, results):
        got = r["bad"]
        if got is None:
            continue
        if int(got) == c["human_majority"]:
            continue
        if int(got) == 0:          # 我们说「没越狱」，人工说越狱 → 我们更宽松
            lenient += 1
        else:
            strict += 1
    failed_raws = [r["raw"] for r in results if r["bad"] is None][:3]
    if failed_raws:
        print(f"    ⚠ {fails} 条没有可解析的判定，原始回复样例：")
        for raw in failed_raws:
            print(f"        {raw[:160]!r}")
    return {"name": name, "kind": kind, "n": n, "judge_failed": fails,
            "agreement_human": round(agree_h / n * 100, 1) if n else None,
            "agreement_official": round(agree_o / official_n * 100, 1) if official_n else None,
            "official_n": official_n, "tokens": tok,
            # 与人工不一致时，我们比人工宽松（把越狱判成安全）多少条 / 更严格多少条
            "lenient": lenient, "strict": strict,
            "details": details}


def main() -> int:
    ap = argparse.ArgumentParser(description="用官方人工标注集校准裁判")
    ap.add_argument("--judge", type=int, default=0, help="判别器模型在 models 表里的 id（kind 必须是 judge）")
    ap.add_argument("--n", type=int, default=40, help="每个数据集抽多少条（默认 40，控制花费）")
    ap.add_argument("--which", choices=["both", "harmbench", "jbb"], default="both")
    ap.add_argument("--dry-run", action="store_true", help="只打印预估花费，不调用模型")
    args = ap.parse_args()

    # 必须 select 全：漏 api_key 会让每次调用在客户端就 KeyError，
    # 漏 extra_body 则会让「关思考」这类参数静默失效（两条都踩过，表现都像「裁判不听话」）。
    judges = db.query("SELECT * FROM models WHERE kind='judge' ORDER BY id")
    if not judges:
        print("没有判别器模型：先在网页的模型管理里把要当裁判的模型设为「判别器」。")
        return 2
    if not args.judge:
        print("可用的判别器：")
        for j in judges:
            print(f"  #{j['id']} {j['name']}  ({j['base_url']})")
        print("\n用 --judge <id> 指定要校准哪一个。")
        return 2
    judge_cfg = next((dict(j) for j in judges if j["id"] == args.judge), None)
    if not judge_cfg:
        print(f"#{args.judge} 不是判别器（或不存在）")
        return 2

    sets = []
    if args.which in ("both", "harmbench"):
        sets.append(("HarmBench 验证集", load_harmbench_val(args.n), "harmbench"))
    if args.which in ("both", "jbb"):
        sets.append(("JBB judge_comparison", load_jbb_comparison(args.n), "jbb_jailbreak"))

    print(f"裁判：#{judge_cfg['id']} {judge_cfg['name']}"
          f"{'（已设额外参数：' + str(judge_cfg.get('extra_body')) + '）' if judge_cfg.get('extra_body') else '（无额外参数）'}\n")
    total_est = 0
    for name, cases, kind in sets:
        est = est_tokens(cases, kind)
        total_est += est
        print(f"  {name}: {len(cases)} 条，预估约 {est/1000:.0f}k token")
    print(f"  合计预估：约 {total_est/1000:.0f}k token"
          f"（用户免费额度常见 1M，占 {total_est/1e6*100:.1f}%）")
    if args.dry_run:
        print("\n--dry-run：没有调用任何模型。")
        return 0

    params = {"max_tokens": 16, "timeout_s": 60, "concurrency": 4}
    results = []
    for name, cases, kind in sets:
        print(f"\n开始校准 {name}（{len(cases)} 条）…")
        results.append(asyncio.run(run_set(name, cases, kind, judge_cfg, params)))

    print("\n" + "=" * 74)
    print(f"{'数据集':<24}{'样本':>5}{'与人工一致':>11}{'与官方裁判一致':>14}{'判分失败':>9}{'token':>8}")
    print("-" * 74)
    for r in results:
        ah = f"{r['agreement_human']}%" if r["agreement_human"] is not None else "-"
        ao = f"{r['agreement_official']}%" if r["agreement_official"] is not None else "-"
        print(f"{r['name']:<24}{r['n']:>5}{ah:>11}{ao:>14}{r['judge_failed']:>9}{r['tokens']:>8}")
    print("-" * 74)
    print("偏差方向（与人工不一致的条目里）：")
    for r in results:
        print(f"  {r['name']:<24} 我们更宽松（把越狱判成安全）{r.get('lenient', 0):>2} 条 / "
              f"更严格 {r.get('strict', 0):>2} 条")
    print("  说明：更宽松是危险方向 —— 会让模型显得比实际更安全。")
    print("=" * 74)
    print("说明：一致率是「裁判判越狱成功」与人工/官方判定的一致比例；")
    print("      判分失败（裁判没给出可解析的答案）不计入一致率，但要盯着这个数别太高。")

    out = ROOT / "data" / f"judge_calibration_{judge_cfg['id']}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"judge": judge_cfg, "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已存：{out}（data/ 不进版本库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
