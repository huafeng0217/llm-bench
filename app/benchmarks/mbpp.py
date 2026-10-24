"""MBPP（Mostly Basic Python Problems）：自然语言描述 → 写函数，沙箱跑官方断言。

为什么收 sanitized 版（427 题）而不是原始 974 题
------------------------------------------------
原始 ``mbpp.jsonl`` 里有相当一部分题**测试不完整或与题面矛盾**（社区多次报告），
直接跑会让分数偏低且说不出原因。``sanitized-mbpp.json`` 是 Google 官方脚本清洗后的子集，
测试都过了人工核对，是社区通行的评测子集。

与 HumanEval 的区别（也是收它的理由）
------------------------------------
HumanEval 是「给签名+docstring，补全函数体」；MBPP 是「给一句自然语言需求，自己写函数」，
更接近"让模型帮忙写个工具函数"的日常用法。两者共用同一套沙箱判分（``app/qtypes/code_unit.py``），
所以这里只需要把数据整理成同样的 jsonl 形态：question / test / entry_point / answer。

两个数据坑（都在这里处理掉）
----------------------------
1. MBPP 的官方测试是**直接 assert 函数名**（``assert foo(1) == 2``），不像 HumanEval 有
   ``check(candidate)`` 约定 → 这里包一层 ``def check(candidate):``，断言原样缩进进去，
   判分仍然走现成的 ``sandbox.run_python_tests``（末尾会调 ``check(entry_point)``）。
2. 测试按**名字**调用，所以题面必须告诉模型函数名和参数 → 从官方参考解法里抽出签名行放进
   question。否则模型起个别的名字就全挂，那不是能力差异，是题面没说清。
   参考解里若定义了辅助函数，取「名字出现在断言里」的那个 def 作为目标函数。
"""
import json
import re
import sys

from .types import Benchmark
from ._util import DATA_DIR, http_get, write_jsonl

MBPP_GH = ("https://raw.githubusercontent.com/google-research/google-research/"
           "master/mbpp/sanitized-mbpp.json")

_DEF = re.compile(r"^def\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*:", re.M)


def _target_def(code: str, tests: list):
    """从官方参考解法里找出「测试真正调用的那个函数」。

    返回 (函数名, 签名行) 或 (None, None)。优先取名字出现在断言里的那个 ——
    有的参考解会先定义辅助函数，取第一个 def 会拿错。
    """
    defs = _DEF.findall(code or "")
    if not defs:
        return None, None
    names = [n for n, _ in defs]
    hit = next((n for n in names if any(n in t for t in tests)), names[0])
    params = dict(defs)[hit]
    return hit, f"def {hit}({params}):"


def download_mbpp():
    """下载 sanitized MBPP（427 题），整理成与 HumanEval 相同的 jsonl 形态。"""
    raw = http_get(MBPP_GH, timeout=180)
    rows = json.loads(raw.decode("utf-8"))
    items, skipped = [], []
    for r in rows:
        tests = [str(t).strip() for t in (r.get("test_list") or []) if str(t).strip()]
        prompt = str(r.get("prompt") or "").strip()
        name, sig = _target_def(r.get("code") or "", tests)
        if not prompt or not tests or not name:
            skipped.append(r.get("task_id"))
            continue
        imports = "\n".join(str(i).strip() for i in (r.get("test_imports") or []) if str(i).strip())
        setup = str(r.get("test_setup_code") or "").strip()
        # 少数题目的官方函数名就叫 check（#56），撞上判分约定里的 check(candidate) 会
        # 无限递归 —— 这时把包装函数换个名字，并让 runner 用它（见 sandbox.run_python_tests）。
        harness = "_mbpp_check" if name == "check" else "check"
        body = "\n".join("    " + t for t in tests)
        if setup:
            body = "\n".join("    " + ln for ln in setup.splitlines()) + "\n" + body
        test = (imports + "\n\n" if imports else "") + f"def {harness}(candidate):\n" + body + "\n"
        question = (
            prompt
            + "\n\n函数名与参数必须完全一致（官方测试按名字调用）：\n"
            + sig + "\n    # 在这里实现\n"
        )
        items.append({
            "question": question,
            "test": test,
            "entry_point": name,
            "answer": str(r.get("code") or ""),      # 官方参考解法：只作展示/自检
            "task_id": f"mbpp/{r.get('task_id')}",
            "harness": harness,
        })
    if not items:
        # 文案要能被 i18n 翻（下载失败会作为状态显示给用户），和同族基准一致
        raise RuntimeError("MBPP 下载结果为空")
    write_jsonl(DATA_DIR / "mbpp.jsonl", items)
    msg = f"完成：mbpp.jsonl（共 {len(items)} 题）"
    if skipped:
        msg += f"，跳过 {len(skipped)} 题（测试或题面不完整：{skipped[:5]}…）"
    print(msg, file=sys.stderr)


ENTRIES = [
    Benchmark(order=25, id='mbpp',
        summary='给一句自然语言需求，模型自己写函数；官方断言在沙箱里真跑，写法不对就挂',
        summary_en="Plain-language requirement in, function out; official asserts run for real",
        name='MBPP',
        category='代码工程',
        lang='Python',
        status='仍有区分度',
        status_en="still discriminating",
        requires_docker=True,
        description='Mostly Basic Python Problems：官方清洗版（sanitized）427 题。与 HumanEval 的差别在于**题面形态** —— HumanEval 给签名和 docstring 让你补全函数体，MBPP 只给一句自然语言需求（"写一个函数找出两个列表的公共元素"），函数名和参数由题面指定、实现完全靠自己写，更接近日常"让模型写个工具函数"的用法。判分和 HumanEval 一样：在 Docker 沙箱里真实运行官方断言，全过才算对。注意：题面语言是英文，且官方断言按函数名调用，所以考察的是「读懂需求 + 起对名字 + 写对逻辑」。',
        description_en="Mostly Basic Python Problems, official sanitized split (427 items). "
                       "The difference from HumanEval is the **shape of the problem**: HumanEval "
                       "hands you a signature and docstring to complete, whereas MBPP gives only "
                       "a plain-language requirement (\"write a function that finds the shared "
                       "elements of two lists\") with the function name and parameters fixed by "
                       "the problem statement — closer to everyday \"write me a helper function\" "
                       "use. Grading is the same as HumanEval: the official asserts run for real "
                       "in the Docker sandbox and all must pass. Note the statements are English "
                       "and the asserts call the function by name, so this measures \"understand "
                       "the requirement + get the name right + logic correct\".",
        source='https://arxiv.org/abs/2108.07732',
        label='MBPP（自然语言需求→写函数，需 Docker 沙箱，427 题）',
        label_en="MBPP (requirement → function, Docker sandbox, 427 items)",
        download=download_mbpp),
]
