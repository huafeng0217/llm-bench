"""BBH 自检：题面/选项/答案三者一致 + 抽答案的边界（18 选 1 与 "I think" 误判）。

为什么要有这一步
----------------
BBH 是在**现有判分链路上**收的（选择题 / 数值题两种），所以风险不在"题会不会做"，
而在"我们整理的数据对不对"：
  - 选择题的 answer 必须是该题真实存在的选项字母（不能指向不存在的选项）；
  - 选项数最多 18（A~R）—— 曾经的实现只支持 A~J，会**系统性**丢掉正确答案在 K 之后的题；
  - 放宽字母范围后，模型回复里的 "I think ..." 的 I 可能被误当成选项 I，所以抽取必须
    只认该题真实存在的字母。这两条都在这里钉住。

用法::

    python scripts/verify_bbh.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.benchmarks import get_meta  # noqa: E402
from app.qtypes import choice  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "bbh.jsonl"
LETTERS = "ABCDEFGHIJKLMNOPQR"
results: list = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f"  {detail}" if detail and not ok else ""))


def main():
    if not DATA.exists():
        print("没找到 data/bbh.jsonl：先跑 python scripts/download.py bbh")
        return 1
    rows = [json.loads(l) for l in DATA.open(encoding="utf-8")]
    meta = get_meta("bbh")

    check("题库已落库、题量与子集数合理", len(rows) == 6007 and len({r.get("subject") for r in rows}) == 25,
          f"实际 {len(rows)} 题 / {len({r.get('subject') for r in rows})} 子集")
    check("元数据：分类=综合推理、不要 Docker / 裁判",
          meta["category"] == "综合推理" and not meta.get("requires_docker") and not meta.get("requires_judge"),
          f"{meta['category']} docker={meta.get('requires_docker')} judge={meta.get('requires_judge')}")
    check("每题都有 question 与 subject",
          all(r.get("question") and r.get("subject") for r in rows))

    choice_rows = [r for r in rows if any(r.get(c) for c in LETTERS)]
    num_rows = [r for r in rows if not any(r.get(c) for c in LETTERS)]
    check("两种题型都有（选择题 + 数值题）", choice_rows and num_rows,
          f"choice={len(choice_rows)} numeric={len(num_rows)}")
    check("选择题：answer 必须是该题真实存在的选项字母",
          all(r["answer"] in [c for c in LETTERS if r.get(c)] for r in choice_rows))
    check("选择题：每题至少 2 个选项",
          all(sum(1 for c in LETTERS if r.get(c)) >= 2 for r in choice_rows))
    check("选择题：选项字母连续（A、B、C… 不跳号）",
          all([c for c in LETTERS if r.get(c)] == list(LETTERS[:sum(1 for c in LETTERS if r.get(c))])
              for r in choice_rows))
    check("数值题：答案都是数字",
          all(str(r["answer"]).lstrip("-").replace(".", "", 1).isdigit() for r in num_rows))

    wide = [r for r in choice_rows if r.get("K")]
    check("确实收到了选项数 >10 的题（否则 A~R 的扩展没意义）", len(wide) > 0, f"实际 {len(wide)} 题")
    check("18 选 1 的题也在（颜色题）", any(r.get("R") for r in choice_rows))

    # 抽答案的边界：放宽字母范围后最容易出的两类错
    check("抽取：18 选 1 能认出 Q/R 这类靠后的字母",
          choice.extract_answer("答案是 (Q)", LETTERS) == "Q"
          and choice.extract_answer("R", LETTERS) == "R")
    check("抽取：只认该题真实存在的字母（K 不在 4 选 1 里就不认）",
          choice.extract_answer("K", "ABCD") is None)
    check("抽取：I think 里的 I 不会被当成选项",
          choice.extract_answer("I think it is B", "ABCD") == "B")

    dist = Counter(r["subject"] for r in rows)
    print("\n各子集题量：" + "、".join(f"{k} {v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])))
    print("\n" + "=" * 72)
    failed = [n for n, ok in results if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败 -> {failed}")
        return 1
    print(f"结论：全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
