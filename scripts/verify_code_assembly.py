"""代码题拼接自检：题目 + 模型续写怎么拼成一份可运行代码（不需要 Docker / 网络）。

为什么需要它
-----------
HumanEval 是**补全式**评测：题目给「函数签名 + docstring」，模型给函数体，
两者拼起来才是一份完整答案。拼接规则有两个**安静**的坑，都会伪装成「模型答错」：

1. **题目自带辅助函数**（HumanEval/38 的 ``encode_cyclic`` 之类，官方测试要调用它）。
   模型如果把 ``def decode_cyclic`` 的签名也重写一遍，老规则「续写里已经有目标函数
   就不前置题目」就不再拼题目 —— 拼出来的文件里**没有 encode_cyclic**，官方测试
   直接 ``NameError``，一个正确答案被判成错。实测在用户的 #137 任务上踩到：
   164 题报 162，其中 1 题是这里的锅。
2. **竞赛题（LiveCodeBench）没有 entry_point**，要的是完整程序；把题面拼上去会毁掉代码。

这两条读代码看不出来（拼接逻辑看着都对），只有把**真实题库**扫一遍才暴露，
所以由断言钉住。跑一次不花钱、不依赖 Docker。

用法::

    python scripts/verify_code_assembly.py
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402
from app.qtypes._code import code_from_completion, extract_code  # noqa: E402

RESULTS: list = []


def check(name: str, got, want) -> None:
    RESULTS.append((name, got == want, f"期望 {want!r}，实际 {got!r}"))


def check_true(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail or "期望为真，实际为假"))


def _defs(code: str) -> list:
    """代码里定义的顶层函数名（按出现顺序）。"""
    return re.findall(r"^def (\w+)\s*\(", code or "", re.MULTILINE)


# HumanEval/38 的形状：题目里先定义一个**辅助函数**，再给出目标函数的签名。
# 官方测试两个都要用 —— 只要目标函数在，辅助函数就绝不能丢。
ITEM_38 = {
    "task_id": "HumanEval/38",
    "entry_point": "decode_cyclic",
    "question": '''def encode_cyclic(s: str):
    """
    returns encoded string by cycling groups of three characters.
    """
    groups = [s[(3 * i):min((3 * i + 3), len(s))] for i in range((len(s) + 2) // 3)]
    groups = [(group[1:] + group[0]) if len(group) == 3 else group for group in groups]
    return "".join(groups)


def decode_cyclic(s: str):
    """
    takes as input string encoded with encode_cyclic function. Returns decoded string.
    """
''',
    "answer": '''    groups = [s[(3 * i):min((3 * i + 3), len(s))] for i in range((len(s) + 2) // 3)]
    groups = [(group[-1] + group[:-1]) if len(group) == 3 else group for group in groups]
    return "".join(groups)
''',
    "test": '''def check(candidate):
    for i in range(100):
        s = "".join(chr(ord("a") + (i * 7 + j) % 26) for j in range(i % 11))
        assert candidate(encode_cyclic(s)) == s
''',
}


def main() -> int:
    # ---- 1) 真实踩过的形态：模型把入口函数的签名也重写了一遍 ----------------
    # 这次 #137 的回复就是这样：题目里已经有 encode_cyclic，模型却从 def decode_cyclic 写起。
    rewritten = ITEM_38["question"][ITEM_38["question"].index("def decode_cyclic"):] + ITEM_38["answer"]
    code = code_from_completion(ITEM_38, rewritten)
    names = _defs(code)
    check_true("题目自带的辅助函数被完整前置（HumanEval/38 的真实形态）",
               names.count("encode_cyclic") == 1,
               "丢了它官方测试直接 NameError、正确答案被判成错；"
               f"实际拼出 {names}")
    check_true("入口函数先由题目给壳、再由续写覆盖（后者生效，与官方 prompt+completion 一致）",
               names.count("decode_cyclic") == 2 and code.rstrip().endswith(ITEM_38["answer"].rstrip()),
               f"实际拼出 {names}")
    try:
        compile(code, "<assembled>", "exec")
        syntax_ok = True
    except SyntaxError as e:  # noqa: BLE001
        syntax_ok = False
        RESULTS.append(("拼出来的代码能编译", False, f"{e}"))
    else:
        RESULTS.append(("拼出来的代码能编译", syntax_ok, ""))
    check_true("模型重写的定义在后（后者生效），入口函数体来自续写",
               code.rstrip().endswith(ITEM_38["answer"].rstrip()),
               "入口函数体必须排在题目里的空壳之后，否则跑的还是题目里的空函数")

    # 用户那次的回复确实不带代码围栏 —— 抽取路径要吃得下
    check_true("无围栏的回复也能抽到代码（用户 #137 的回复就没有围栏）",
               (extract_code(rewritten, ITEM_38) or "").count("def encode_cyclic") == 1,
               f"抽到：{_defs(extract_code(rewritten, ITEM_38) or '')}")

    # ---- 2) 官方参考解法的形态：续写只有函数体（缩进必须原样保留） ----------
    body_only = code_from_completion(ITEM_38, ITEM_38["answer"])
    check("只给函数体时，题目（含辅助函数）照样前置", _defs(body_only),
          ["encode_cyclic", "decode_cyclic"])
    check_true("函数体的缩进没被 strip 掉（strip 会拼出语法错误）",
               "\n    groups = [s[(3 * i)" in body_only)
    try:
        compile(body_only, "<assembled>", "exec")
    except SyntaxError as e:  # noqa: BLE001
        RESULTS.append(("只给函数体时拼出来也能编译", False, f"{e}"))
    else:
        RESULTS.append(("只给函数体时拼出来也能编译", True, ""))

    # ---- 3) 竞赛题（无 entry_point）不能被掺进题面 ------------------------
    lcb = {"question_id": "abc123", "question": "Write a program that reads n and prints n*2",
           "public_tests": [], "starter_code": ""}
    full_program = "import sys\nn = int(sys.stdin.read())\nprint(n * 2)\n"
    check("没有 entry_point 的竞赛题：原样返回，不拼题面",
          code_from_completion(lcb, full_program), full_program)

    # ---- 4) 扫一遍真实题库：所有「题目自带辅助函数」的题都要拼得全 ----------
    path = ROOT / "data" / "humaneval.jsonl"
    if not path.exists():
        print(f"[SKIP] 全量扫描 {path.name} 不存在（题库没下载）—— 只跑了上面的合成用例")
    else:
        missing, syntax_bad, helper_items = [], [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                ep = item.get("entry_point") or ""
                q = item.get("question") or ""
                defs = _defs(q)
                if len(defs) > 1:
                    helper_items.append(item.get("task_id"))
                # 模拟「模型重写入口函数签名」：题目里入口函数那段 + 官方函数体
                m = re.search(rf"^def {re.escape(ep)}\s*\(", q, re.MULTILINE)
                if not (ep and m):
                    continue
                rebuilt = code_from_completion(item, q[m.start():] + (item.get("answer") or ""))
                for name in defs:
                    if f"def {name}(" not in rebuilt:
                        missing.append(f"{item.get('task_id')}:{name}")
                try:
                    compile(rebuilt, "<assembled>", "exec")
                except SyntaxError as e:  # noqa: BLE001
                    syntax_bad.append(f"{item.get('task_id')}: {e}")
        check_true("题库里确实有「题目自带辅助函数」的题（否则这项扫描是空跑）",
                   len(helper_items) > 0,
                   "一道都没有说明题库换了，得重新确认这条断言还有没有意义")
        check("全量扫描：题目里的每个函数在「模型重写签名」时都还在",
              missing, [])
        check("全量扫描：拼出来的代码都能编译", syntax_bad, [])
        print(f"扫描 {path.name}：{len(helper_items)} 道题自带辅助函数"
              f"（{', '.join(helper_items[:6])}{'…' if len(helper_items) > 6 else ''}）")

    print("=" * 74)
    bad = 0
    for name, ok, detail in RESULTS:
        print(f"[{'通过' if ok else '失败'}] {name}")
        if not ok:
            bad += 1
            print(f"    {detail}")
    print("=" * 74)
    if bad:
        print(f"{bad}/{len(RESULTS)} 项不通过。")
        return 1
    print(f"全部 {len(RESULTS)} 项通过：题目与续写的拼接没有丢代码，也没有多拼题面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
