"""IFEval（Instruction-Following Evaluation）：题面 + 若干可验证指令，逐条核对。

为什么收它
----------
现有 9 个分类里完全没有"格式/约束"这个维度：模型会不会数够 300 个词、会不会避开某个词、
输出是不是合法 JSON、有没有用 P.S. 结尾…… 这些和"知不知道答案"是两种能力，
而且判分规则是**确定性的可验证指令**（不需要裁判模型），非常适合本地评测台。

数据与口径
----------
题面与指令参数来自 HF 上的官方副本 `google/IFEval`（文件 ifeval_input_data.jsonl，541 条）。
每条题 = prompt 原文 + instruction_id_list + kwargs，判分逻辑见 app/ifeval_rules/。

两处"我们自己的取舍"（都写进自检和 README，不藏）
1. **language:response_language 的 31 条不收**：这类指令要求"整段回答用 xx 语言"，
   涉及 22 种语言（印地、孟加拉、泰米尔…），官方用 langdetect 判断；标准库做不了同脚本语言
   的区分（hi/mr/ne 都是天城文），强行近似会给出不可信分数 —— 所以直接不收，并在卡片上说明。
2. **分句用近似实现**：官方的 number_sentences 用 nltk punkt 模型（要联网下模型文件，
   和"纯本地"冲突），这里用「缩写保护 + 句末标点」的正则近似，个别边界会和官方不同。
"""
import json
import sys

from .types import Benchmark
from ._util import DATA_DIR, http_get, write_jsonl

IFEval_URL = "https://huggingface.co/datasets/google/IFEval/resolve/main/ifeval_input_data.jsonl"

# 不收的指令：需要语种识别（见模块说明第 1 条）
DROP_INSTRUCTIONS = {"language:response_language"}


def download_ifeval():
    """下载 IFEval 541 条题面 + 指令，过滤掉需要语种识别的题。"""
    raw = http_get(IFEval_URL, timeout=180)
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    items, dropped = [], 0
    for r in rows:
        ids = [str(x) for x in (r.get("instruction_id_list") or [])]
        kws = r.get("kwargs") or []
        if not ids or len(kws) != len(ids):
            dropped += 1
            continue
        if any(i in DROP_INSTRUCTIONS for i in ids):
            dropped += 1
            continue
        items.append({
            "question": str(r.get("prompt") or "").strip(),
            "instructions": ids,
            "kwargs": kws,
            "answer": "满足全部指令",       # 只作展示（判分由 instructions/kwargs 决定）
            "task_id": f"ifeval/{r.get('key')}",
        })
    if not items:
        raise RuntimeError("IFEval 下载结果为空")
    write_jsonl(DATA_DIR / "ifeval.jsonl", items)
    print(f"完成：ifeval.jsonl（共 {len(items)} 题）", file=sys.stderr)
    if dropped:
        print(f"  跳过 {dropped} 题：含 language:* 指令（需要语种识别，标准库做不到）"
              f"或 kwargs 与指令数不匹配", file=sys.stderr)


ENTRIES = [
    Benchmark(order=27, id='ifeval',
        summary='一段自然语言 + 若干可验证指令：字数、段落、必须/禁止词、JSON、大写…逐条核对',
        summary_en="Prompt plus verifiable constraints, each checked deterministically",
        name='IFEval',
        category='指令遵循',
        lang='英文',
        status='仍有区分度',
        status_en="still discriminating",
        description='Instruction-Following Evaluation：每道题是一段自然语言请求，'
                    '里面夹着若干**可验证指令**（至少 300 词 / 恰好 3 个段落 / 不能出现某个词 / '
                    '必须输出合法 JSON / 全文大写 / 用引号包住 / 结尾写 P.S. …），'
                    '共 25 类、510 题。判分不靠人也不靠裁判模型：每条指令都有确定性的核对逻辑'
                    '（规则移植自官方实现），**所有指令都满足**才算这题对 —— 所以它衡量的是'
                    '「读得懂约束并逐条执行」，和知识/推理类基准完全不同的一个维度。'
                    '两点口径：① 官方里 31 条「整段回答用某语言」的题**未收**（要语种识别，'
                    '标准库无法可靠区分同脚本语言）；② 数句子用「缩写保护 + 句末标点」的正则近似，'
                    '个别边界与官方 punkt 分词器可能不同。',
        description_en="Instruction-Following Evaluation: each item is a natural-language request "
                       "with several **verifiable constraints** embedded (at least 300 words / "
                       "exactly 3 paragraphs / never use a given word / output valid JSON / all "
                       "uppercase / wrap in quotes / end with a P.S. …), 25 instruction types "
                       "across 510 items. Scoring is neither human nor judge-model based: every "
                       "constraint has a deterministic checker (ported from the official "
                       "implementation) and **all of them must hold** for the item to count as "
                       "correct — so it measures \"can it read the constraints and follow each "
                       "one\", a completely different dimension from knowledge/reasoning "
                       "benchmarks. Two caveats: (1) the 31 items asking for a whole response in "
                       "another language are **not included** (language ID is needed and the "
                       "standard library cannot reliably separate languages sharing a script); "
                       "(2) sentence counting uses a regex approximation with abbreviation "
                       "guards, so a few edge cases may differ from the official punkt "
                       "tokenizer.",
        source='https://arxiv.org/abs/2311.07911',
        label='IFEval（可验证指令，确定性判分，510 题）',
        label_en="IFEval (verifiable constraints, deterministic scoring, 510 items)",
        download=download_ifeval),
]
