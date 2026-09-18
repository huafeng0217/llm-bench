"""HarmBench 的**直接请求**子集（200 条标准有害行为）。

判分方式与其它基准都不同：**由裁判模型判**（见 app/safety.py），所以它是本仓库里
第一个「分数不由程序算」的基准。三条必须知道的事：

1. 官方行为集共 400 条：标准 200 + contextual 100 + copyright 100。这里**只收标准那 200 条** ——
   - contextual 那 100 条要先给一段「情境铺垫」再给行为（两轮），与单轮直接请求的链路不同；
   - copyright 那 100 条官方用的是**哈希比对**（不是裁判），属于另一套判分，不在本次范围。
2. 官方 ASR（攻击成功率）用的是 13B 分类器 + 18 种攻击方法。本站只做**直接请求**
   （不注入攻击提示词），所以这里的分数是「模型对原始有害请求的抵抗力」，
   **不能和官方榜的 ASR 直接比**（官方数字是「被攻击后」的成功率，天然更高）。
3. 裁判用的是官方 ``LLAMA2_CLS_PROMPT`` 原文；页面/README 会写明这次是谁判的。
"""
from .types import Benchmark
import csv
import io
import sys
from ._util import DATA_DIR, http_get, write_jsonl


BASE = "https://raw.githubusercontent.com/centerforaisafety/HarmBench/main/data"
BEHAVIORS_URL = f"{BASE}/behavior_datasets/harmbench_behaviors_text_all.csv"
VAL_URL = f"{BASE}/classifier_val_sets/text_behaviors_val_set.json"


def fetch_behaviors(functions: set[str]) -> list:
    """取官方行为表并只留下指定的 FunctionalCategory。"""
    text = http_get(BEHAVIORS_URL, timeout=180).decode("utf-8", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return [r for r in rows if (r.get("FunctionalCategory") or "").strip() in functions]


def download_harmbench():
    path = DATA_DIR / "harmbench.jsonl"
    rows = fetch_behaviors({"standard"})
    items, skipped = [], 0
    for r in rows:
        behavior = (r.get("Behavior") or "").strip()
        bid = (r.get("BehaviorID") or "").strip()
        if not behavior or not bid:
            skipped += 1
            continue
        items.append({
            "question": behavior,          # 送给被测模型的原文
            "behavior": behavior,
            "behavior_id": bid,
            "harmful": True,               # 期望「拒绝/不照做」= 安全
            "judge_kind": "harmbench",     # 用官方 HarmBench 分类器口径判
            "subject": (r.get("SemanticCategory") or "").strip(),
            "answer": "",
        })
    if not items:
        raise RuntimeError("HarmBench 下载结果为空")
    write_jsonl(path, items)
    cats = {}
    for it in items:
        cats[it["subject"]] = cats.get(it["subject"], 0) + 1
    print(f"完成：{path}（共 {len(items)} 题" + (f"，跳过 {skipped}" if skipped else "") + "）")
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"    {k}: {v}")


ENTRIES = [
    Benchmark(order=33, id='harmbench',
        name='HarmBench（直接请求）',
        name_en="HarmBench (direct requests)",
        summary='200 条有害请求直接发给模型，看它会不会照做（裁判判分）',
        summary_en="200 harmful requests sent as-is, to see whether the model complies "
                      "(judge-scored)",
        category='安全 / 对齐',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        requires_judge=True,
        adverse_label='越狱成功',
        description='HarmBench 标准子集 200 条有害行为（化学/生物、非法行为、虚假信息、网络犯罪、骚扰、其他有害），'
                    '覆盖 6 个语义类。**把行为原文直接发给模型**，再由裁判模型按官方 HarmBench 分类器提示词判定'
                    '「这条回复是否构成了该有害行为」——是的记为越狱成功。'
                    '得分口径：**正确率 = 模型拒绝/未照做的比例**（越高越安全），越狱成功率 = 1 − 该值。'
                    '三点务必注意：① 官方榜是「被 18 种攻击方法攻击后」的 ASR，本站只做直接请求，**分数不可与之直接比较**；'
                    '② 官方 contextual（100 条，需两轮铺垫）与 copyright（100 条，用哈希比对判）两类未收录；'
                    '③ 判分靠裁判模型，换了裁判分数就不可比 —— 结果页会写明这次是谁判的。',
        description_en="The 200-item standard subset of HarmBench (chemical/biological, "
                          "illegal activity, misinformation, cybercrime, harassment, other "
                          "harm), covering 6 semantic categories. The behaviour text is **sent "
                          "to the model as-is**, and a judge model applies the official "
                          "HarmBench classifier prompt to decide whether the reply constitutes "
                          "that behaviour — yes means a successful jailbreak. Scoring: "
                          "**accuracy = the share of refusals / non-compliance** (higher is "
                          "safer), with jailbreak rate = 1 − that. Three caveats: (1) the "
                          "official leaderboard reports ASR *after 18 attack methods*, while we "
                          "send direct requests only, so scores are **not directly "
                          "comparable**; (2) the contextual (100 two-turn items) and copyright "
                          "(100 hash-matched items) subsets are not included; (3) scoring "
                          "depends on the judge, so switching judges makes scores incomparable "
                          "— each result records which judge ran.",
        source='https://arxiv.org/abs/2402.04249',
        label='HarmBench 直接请求（200 题，需裁判模型）',
        label_en="HarmBench direct requests (200 items, needs a judge)",
        download=download_harmbench),
]
