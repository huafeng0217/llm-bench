"""IFEval 自检：题库完整性 + 25 类指令的规则可用性 + 两处"近似"的行为边界。

为什么这么测
------------
IFEval 的判分不是"答案对不对"，而是 25 类**可验证指令**的逐条核对：
只要有一类规则实现错了，模型分数就会整体偏。
我们移植规则时改了三处外部依赖（nltk / langdetect / immutabledict），
这里要钉住的是：
  1. 题库里出现的每个指令 id 都能构造、能跑 check_following（不会一跑就抛异常）；
  2. 各类指令的**正例/反例**行为正确（尤其注意数字类：字数、句数、段落数）；
  3. 两处近似（分句正则、英文判定）的边界与文档一致。

用法::

    python scripts/verify_ifeval.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.benchmarks import get_meta  # noqa: E402
from app.ifeval_rules import instructions_registry, instructions_util  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "ifeval.jsonl"
results: list = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def build(iid: str, kw: dict):
    """按官方用法构造：cls(instruction_id) + build_description(**非空 kwargs)。"""
    inst = instructions_registry.INSTRUCTION_DICT[iid](iid)
    inst.build_description(**{k: v for k, v in (kw or {}).items() if v})
    return inst


def follows(iid: str, kw: dict, text: str) -> bool:
    return bool(build(iid, kw).check_following(text))


def main():
    if not DATA.exists():
        print("没找到 data/ifeval.jsonl：先跑 python scripts/download.py ifeval")
        return 1
    rows = [json.loads(l) for l in DATA.open(encoding="utf-8")]
    meta = get_meta("ifeval")
    used = sorted({i for r in rows for i in r["instructions"]})

    check("题库已落库：510 题（官方 541 条，去掉 31 条语言题）", len(rows) == 510, f"实际 {len(rows)}")
    check("元数据：分类=指令遵循、不要 Docker / 裁判、英文",
          meta["category"] == "指令遵循" and not meta.get("requires_docker")
          and not meta.get("requires_judge") and meta["lang"] == "英文",
          f"{meta['category']} docker={meta.get('requires_docker')} lang={meta['lang']}")
    check("每题都有 question / instructions / kwargs，且两者等长",
          all(r.get("question") and r.get("instructions") and len(r["instructions"]) == len(r["kwargs"])
              for r in rows))
    check("不含 language:* 指令（需要语种识别，明确不收）",
          not any(i.startswith("language:") for i in used), f"实际 {[i for i in used if i.startswith('language:')]}")
    check("题库用到的指令类型都已在规则表里注册",
          all(i in instructions_registry.INSTRUCTION_DICT for i in used))
    check("注册了 25 类指令（与官方一致）", len(instructions_registry.INSTRUCTION_DICT) == 25,
          f"实际 {len(instructions_registry.INSTRUCTION_DICT)}")

    # 每个 id：用题库里真实 kwargs 构造 + 跑一次 check_following（不抛异常即通过）
    crashes = []
    for iid in used:
        sample = next(r for r in rows if iid in r["instructions"])
        kw = sample["kwargs"][sample["instructions"].index(iid)]
        try:
            inst = build(iid, kw)
            desc = getattr(inst, "_description_pattern", "") or "ok"
            inst.check_following("This is a sample response. It has two sentences.")
            if not desc:
                crashes.append((iid, "描述为空"))
        except Exception as e:                     # noqa: BLE001
            crashes.append((iid, f"{type(e).__name__}: {e}"))
    check("每类指令都能用真实 kwargs 构造并跑通（不抛异常）", not crashes, str(crashes[:3]))

    # ---- 各类指令的正例 / 反例（覆盖关键字、长度、格式、标点、首尾）----
    cases = [
        ("keywords:existence", {"keywords": ["apple"]}, "I ate an apple today.", "I ate a pear today."),
        ("keywords:forbidden_words", {"forbidden_words": ["apple"]}, "I ate a pear today.", "I ate an apple today."),
        ("keywords:frequency", {"relation": "at least", "keyword": "apple", "frequency": 2},
         "apple and apple again", "only one apple here"),
        ("keywords:letter_frequency", {"letter": "a", "let_relation": "at least", "let_frequency": 3},
         "banana", "banan"),
        ("length_constraints:number_words", {"relation": "at least", "num_words": 5},
         "one two three four five", "one two three four"),
        ("length_constraints:number_sentences", {"relation": "less than", "num_sentences": 3},
         "One. Two.", "One. Two. Three."),
        # 官方规定段落用 markdown 分隔符 *** 划分（不是空行！）
        ("length_constraints:number_paragraphs", {"num_paragraphs": 2},
         "first para***second para", "single paragraph only"),
        ("punctuation:no_comma", {}, "no commas in this response", "a, b and c"),
        ("detectable_format:json_format", {}, '{"a": 1, "b": [2, 3]}', "not a json document"),
        ("detectable_format:title", {}, "<<My Title>> then the body", "body without a title"),
        ("detectable_format:number_highlighted_sections", {"num_highlights": 2},
         "*first* and *second*", "only *one* highlight"),
        ("startend:quotation", {}, '"wrapped in quotes"', "not wrapped"),
        ("startend:end_checker", {"end_phrase": "Goodbye"}, "some text. Goodbye", "some text. Farewell"),
        ("change_case:english_capital", {}, "THIS IS ALL CAPS", "This is not all caps"),
        ("change_case:english_lowercase", {}, "this is all lowercase", "This is not all lowercase"),
        ("combination:two_responses", {}, "first response\n******\nsecond response", "only one response"),
        ("detectable_content:postscript", {"postscript_marker": "P.S."},
         "body text. P.S. more text", "body text, no postscript"),
        ("detectable_content:number_placeholders", {"num_placeholders": 2},
         "hello [name] and [age]", "hello [name] only"),
        ("detectable_format:number_bullet_lists", {"num_bullets": 3},
         "* one\n* two\n* three", "* one\n* two"),
        ("detectable_format:multiple_sections", {"section_spliter": "SECTION", "num_sections": 2},
         "SECTION 1 text SECTION 2 text", "SECTION 1 only"),
    ]
    bad = []
    for iid, kw, pos, neg in cases:
        try:
            if not follows(iid, kw, pos):
                bad.append((iid, "正例被判未遵守"))
            if follows(iid, kw, neg):
                bad.append((iid, "反例被判遵守"))
        except Exception as e:                     # noqa: BLE001
            bad.append((iid, f"异常 {type(e).__name__}: {e}"))
    check(f"指令规则的正例/反例行为正确（{len(cases)} 类）", not bad, str(bad[:4]))

    # ---- 两处"近似"的边界（文档里对用户承诺过）----
    check("近似1：数词按空白/标点切分（与官方 RegexpTokenizer 一致）",
          instructions_util.count_words("one two-three four!") == 4,
          f"实际 {instructions_util.count_words('one two-three four!')}")
    check("近似2：分句跳过缩写（Dr. / e.g. 不算句尾）",
          instructions_util.count_sentences("Dr. Smith went home. He slept well.") == 2,
          f"实际 {instructions_util.count_sentences('Dr. Smith went home. He slept well.')}")
    check("近似3：英文判定（拉丁字母占比）——英文通过、中文不通过",
          follows("language:response_language", {"language": "en"}, "This is plain English text.")
          and not follows("language:response_language", {"language": "en"}, "这是一段中文回答，没有拉丁字母。"))

    # 用一条真实题目端到端跑一遍：构造 → build_description → check_following
    r = rows[0]
    checks = [(iid, build(iid, r["kwargs"][r["instructions"].index(iid)])) for iid in r["instructions"]]
    check("真实题目能端到端构造并核对（不抛异常）",
          all(c.check_following("Sample answer.") in (True, False) for _, c in checks))

    print("\n题库使用的指令类型：" + "、".join(used))
    print("\n" + "=" * 72)
    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败 -> {failed}")
        return 1
    print(f"结论：全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
