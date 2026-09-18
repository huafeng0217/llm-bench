"""ARC（AI2 Reasoning Challenge）：小学科学题，4 选 1。

收录 ARC-Challenge 的 test 划分（1172 题）。ARC-Easy 不收 —— 前沿模型已接近满分，
放进来只会拉低整个分类的区分度。
"""
from .types import Benchmark
import sys
from ._util import DATA_DIR, fetch_ds_rows, write_jsonl


# 选项字母上限用 **A~J**（与选择题 runner 一致），不要用 _util.LETTERS ——
# 那个常量是 A~D，只够 MMLU 那种四选一。ARC 里有 5 选 1 的题，
# 用错常量会让它们**被静默丢掉**（第一次下载时正是如此：1172 题只落库 1169 题，
# 少的 3 题是 TIMSS 的五选一，日志里只留了三行「解析失败」）。
CHOICES = [chr(65 + i) for i in range(10)]


DS = "allenai/ai2_arc"
CONFIG = "ARC-Challenge"
SPLIT = "test"          # test 的答案官方已公开；train/validation 是训练/验证用途


def map_arc(row: dict):
    """一行 ARC → 统一题目格式。

    两个坑：
    1. 选项标签不一定是 A/B/C/D —— 有一部分题用的是 1/2/3/4，而 ``answerKey`` 是跟着
       label 走的。要是直接假设 label 就是 A/B/C/D，这些题会全部判错
       （而且错得很安静：分数偏低但看不出原因）。所以按 ``choices.label`` 里
       answerKey 的**下标**来定答案字母。
    2. 也有 5 选 1 的题（A~E），所以选项上限是 A~J 而不是 A~D。
    """
    choices = row.get("choices") or {}
    texts = [str(t).strip() for t in (choices.get("text") or [])]
    labels = [str(x).strip() for x in (choices.get("label") or [])]
    key = str(row.get("answerKey", "")).strip()
    if not texts or len(texts) != len(labels) or key not in labels:
        return None
    if len(texts) > len(CHOICES):        # 我们的 prompt 只支持 A~J
        return None
    idx = labels.index(key)
    question = str(row.get("question", "")).strip()
    if not question or not all(texts):
        return None
    item = {"question": question, "answer": CHOICES[idx],
            "subject": str(row.get("id", ""))}
    for i, t in enumerate(texts):
        item[CHOICES[i]] = t
    return item


def download_arc():
    path = DATA_DIR / "arc_challenge.jsonl"
    rows = fetch_ds_rows(DS, CONFIG, SPLIT)
    items, failed, odd_label, five_plus = [], 0, 0, 0
    for row in rows:
        labels = [str(x).strip() for x in ((row.get("choices") or {}).get("label") or [])]
        if labels and labels != list(CHOICES[:len(labels)]):
            odd_label += 1     # 用 1/2/3/4 当标签的题（映射逻辑必须按 label 下标走）
        if len(labels) > 4:
            five_plus += 1     # 五选一及以上的题（选项上限必须是 A~J）
        item = map_arc(row)
        if not item:
            failed += 1
            print(f"  解析失败，跳过: {row.get('id')}", file=sys.stderr)
            continue
        items.append(item)
    write_jsonl(path, items)
    print(f"完成：{path}（共 {len(items)} 题"
          + (f"，非 ABCD 标签 {odd_label} 题" if odd_label else "")
          + (f"，其中 {five_plus} 题选项超过 4 个" if five_plus else "")
          + (f"，解析失败 {failed}" if failed else "") + "）")


ENTRIES = [
    Benchmark(order=28, id='arc_challenge',
        name='ARC-Challenge',
        summary='小学科学 4 选 1：要靠常识与推理选出解释，不是查知识',
        summary_en="Grade-school science, 4-way: you have to reason to the explanation, not "
                      "recall a fact",
        category='科学推理',
        lang='英文',
        status='接近饱和',
        status_en="near saturation",
        description='AI2 Reasoning Challenge 的 Challenge 子集 1172 题，小学科学选择题，'
                    '需要把科学常识与题目情境结合起来推理（不是背知识点）。前沿模型已到 90% 上下，'
                    '适合当科学推理的基本盘；想要区分度请看 GPQA。'
                    '注意：部分题目的选项标签在原始数据里是 1/2/3/4，下载时已按标签下标对齐答案字母。',
        description_en="1,172 items from the Challenge subset of the AI2 Reasoning "
                          "Challenge: grade-school science questions that require combining "
                          "everyday science knowledge with the situation described (not fact "
                          "recall). Frontier models sit around 90%, so treat it as a baseline; "
                          "for discrimination use GPQA. Note: some items label their options "
                          "1/2/3/4 in the raw data; the downloader maps them onto answer "
                          "letters by index.",
        source='https://arxiv.org/abs/1803.05457',
        label='ARC-Challenge（1172 题）',
        label_en="ARC-Challenge (1172 items)",
        download=download_arc),
]
