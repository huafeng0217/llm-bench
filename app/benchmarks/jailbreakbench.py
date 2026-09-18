"""JailbreakBench（JBB）：100 条有害行为 + 100 条良性请求，各成一个基准。

**为什么良性那 100 条一定要有**：只测「有害请求有没有被照做」会奖励「什么都拒绝」的模型；
配上良性请求（正常提问必须正常回答）才能看出「对齐过头」——
两个数字一起看才有意义，所以这里是**两个独立基准**而不是混在一起算一个正确率
（混在一起的平均分既不是 ASR 也不是过度拒绝率，什么都说明不了）。

判分同样由裁判完成，用 JBB 官方的两个判官提示词（safe/unsafe 与 拒答 Yes/No）。
"""
from .types import Benchmark
import sys
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl


DS = "JailbreakBench/JBB-Behaviors"
CONFIG = "behaviors"          # 另一个 config 是 judge_comparison（用来校准裁判的）


def _fetch(split: str) -> list:
    return fetch_ds_rows(DS, CONFIG, split)


def download_jbb_harmful():
    path = DATA_DIR / "jbb_harmful.jsonl"
    rows = _fetch("harmful")
    items = []
    for i, r in enumerate(rows):
        goal = str(r.get("Goal") or "").strip()
        if not goal:
            continue
        items.append({
            "question": goal,
            "behavior": goal,
            "behavior_id": f"jbb_harmful_{r.get('Index', i)}",
            "harmful": True,                    # 期望拒绝
            "judge_kind": "jbb_jailbreak",      # 官方越狱判官（safe / unsafe）
            "subject": str(r.get("Category") or r.get("Behavior") or ""),
            "answer": "",
        })
    if not items:
        raise RuntimeError("JBB 有害行为下载结果为空")
    write_jsonl(path, items)
    print(f"完成：{path}（共 {len(items)} 题）")


def download_jbb_benign():
    path = DATA_DIR / "jbb_benign.jsonl"
    rows = _fetch("benign")
    items = []
    for i, r in enumerate(rows):
        goal = str(r.get("Goal") or "").strip()
        if not goal:
            continue
        items.append({
            "question": goal,
            "behavior": goal,
            "behavior_id": f"jbb_benign_{r.get('Index', i)}",
            "harmful": False,                  # 期望**正常回答**（拒绝就是过度对齐）
            "judge_kind": "jbb_refusal",       # 官方拒答判官（Yes / No）
            "subject": str(r.get("Category") or r.get("Behavior") or ""),
            "answer": "",
        })
    if not items:
        raise RuntimeError("JBB 良性请求下载结果为空")
    write_jsonl(path, items)
    print(f"完成：{path}（共 {len(items)} 题）")


_JBB_NOTE = ('判分由裁判模型完成（{judge}），得分口径：**正确率 = 未被判为越狱的比例**。'
             'JBB 官方面向的是各种越狱攻击方法，本站只做直接请求，分数不可与官方榜直接比较；'
             '换裁判会导致分数不可比，结果页会写明这次是谁判的。')


ENTRIES = [
    Benchmark(order=34, id='jbb_harmful',
        name='JailbreakBench 有害行为',
        name_en="JailbreakBench harmful",
        summary='100 条有害请求直接发问，测有没有被照做（裁判判分）',
        summary_en="100 harmful requests asked directly, to see whether the model complies "
                      "(judge-scored)",
        category='安全 / 对齐',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        requires_judge=True,
        adverse_label='越狱成功',
        description='JailbreakBench 的 100 条有害行为（源自 AdvBench，覆盖骚扰歧视、恶意软件、人身伤害、'
                    '欺诈等类别），把请求原文直接发给模型，再用官方越狱判官（safe / unsafe）判定是否越狱成功。'
                    + _JBB_NOTE.format(judge='官方越狱判官提示词') +
                    '注意：它和 HarmBench 有重叠（JBB 的有害行为就取自 AdvBench/HarmBench 一族），'
                    '两者一起看时别把重叠当成「两个独立证据」。',
        description_en="JailbreakBench's 100 harmful behaviours (from AdvBench: harassment "
                          "and discrimination, malware, physical harm, fraud…), sent as-is and "
                          "then judged by the official jailbreak judge (safe / unsafe). "
                          "Scoring: **accuracy = the share not judged a jailbreak**. JBB "
                          "targets jailbreak *attacks*; we do direct requests only, so scores "
                          "are not comparable with the official leaderboard, and switching "
                          "judges also makes them incomparable — each result records which "
                          "judge ran. Note: it overlaps HarmBench (JBB's behaviours come from "
                          "the AdvBench/HarmBench family), so do not treat the two as "
                          "independent evidence.",
        source='https://arxiv.org/abs/2404.01318',
        label='JBB 有害行为（100 题，需裁判模型）',
        label_en="JBB harmful behaviours (100 items, needs a judge)",
        download=download_jbb_harmful),
    Benchmark(order=35, id='jbb_benign',
        name='JailbreakBench 良性请求',
        name_en="JailbreakBench benign",
        summary='100 条正常请求，测会不会「对齐过头」什么都不敢答',
        summary_en="100 ordinary requests, to see whether alignment overshoots and it "
                      "refuses everything",
        category='安全 / 对齐',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        requires_judge=True,
        adverse_label='过度拒绝',
        description='JailbreakBench 的 100 条**良性**请求（正常提问，理应正常回答）。用官方拒答判官判定'
                    '模型是否拒绝：**拒绝就是过度对齐**，正确率 = 正常回答的比例。'
                    '必须和「有害行为」那个基准一起看：只看有害请求的抵抗力，会把「什么都不敢答」的模型评得最高；'
                    '只看这一项，又会奖励「什么都敢答」。两个数字合起来才是对齐的实际状态。'
                    + _JBB_NOTE.format(judge='官方拒答判官提示词'),
        description_en="JailbreakBench's 100 **benign** requests (normal questions that "
                          "deserve normal answers). The official refusal judge decides whether "
                          "the model refused: **a refusal is over-alignment**, and accuracy = "
                          "the share of normal answers. It must be read together with the "
                          "harmful set: looking only at resistance to harmful requests rewards "
                          "a model that refuses everything, while looking only at this one "
                          "rewards a model that answers anything. Only the two numbers together "
                          "describe the alignment state. Scoring: **accuracy = the share not "
                          "judged a refusal**. JBB targets jailbreak attacks; we do direct "
                          "requests only, so scores are not comparable with the official "
                          "leaderboard, and switching judges also makes them incomparable — "
                          "each result records which judge ran.",
        source='https://arxiv.org/abs/2404.01318',
        label='JBB 良性请求（100 题，需裁判模型）',
        label_en="JBB benign requests (100 items, needs a judge)",
        download=download_jbb_benign),
]
