"""中英切换的自检：语言识别 / 文案回落 / 漏译（不需要 Docker、不联网）。

为什么需要它
-----------
i18n 的失败模式都很**安静**：

1. **语言识别错**：`Accept-Language: zh-CN,zh;q=0.9,en;q=0.8` 要被识别成中文，
   `fr-FR` 要回落默认语言 —— 认错了页面就整体是另一种语言，但功能一切正常。
2. **漏译**：英文模式下某条文案还是中文。界面上看着"能用"，只是中英混排。
   所以这里断言：**英文表里不许出现中文**（漏成中文等于没翻），
   以及**代码里 `i18n.t(...)` 用到的每条中文都必须在英文表里**。
3. **占位符写错**：`t("已开始下载 {n} 个子集", n=3)` 里占位符和参数对不上时，
   不能让整条消息变成异常（那会把一个提示变成一个 500）。

英文表里长什么样、怎么加：见 `app/i18n.py` 顶部说明（中文原文即 key）。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import i18n, main  # noqa: E402

RESULTS: list = []

# "是不是中文"的判据不能只认汉字：`Agentic（Web Search + Memory）` 里一个汉字都没有，
# 只有全角括号 U+FF08/FF09 —— 曾用它测出过一次假绿（把分组名的英文化改回中文，断言照样通过）。
# 所以判据要连中日韩标点、全角字符一起认。
CJK_ANY = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def check(name: str, got, want) -> None:
    RESULTS.append((name, got == want, f"期望 {want!r}，实际 {got!r}"))


def check_true(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail or "期望为真，实际为假"))


def main_() -> int:
    # ---- 1) 语言识别 ----
    check("zh-CN,zh;q=0.9,en;q=0.8 -> 中文", i18n.detect("zh-CN,zh;q=0.9,en;q=0.8"), "zh")
    check("en-US,en;q=0.9 -> 英文", i18n.detect("en-US,en;q=0.9"), "en")
    check("en;q=0.3,zh;q=0.9 -> 按 q 值取中文", i18n.detect("en;q=0.3,zh;q=0.9"), "zh")
    check("fr-FR（都不认识）-> 回落默认中文", i18n.detect("fr-FR,fr;q=0.9"), i18n.DEFAULT_LANG)
    check("空 Accept-Language -> 默认", i18n.detect(""), i18n.DEFAULT_LANG)
    check("X-Lang 优先于 Accept-Language",
          i18n.detect("zh-CN", "en"), "en")
    check("X-Lang 认不出来时不看它、继续看 Accept-Language",
          i18n.detect("en-US", "fr"), "en")

    # ---- 2) 文案回落与占位符 ----
    i18n.set_lang("zh")
    check("中文模式：返回原文", i18n.t("已接入模型"), "已接入模型")
    i18n.set_lang("en")
    check("英文模式：查英文表", i18n.t("已接入模型"), "Models")
    check("英文模式：表里没有的回落中文（宁可显示中文，也别空白）",
          i18n.t("这句话肯定还没翻译"), "这句话肯定还没翻译")
    check("中文模式的占位符照常替换", i18n.t("已开始下载 {n} 个子集", n=3), "已开始下载 3 个子集")
    check_true("占位符对不上时不抛异常（否则提示会变成 500）",
               isinstance(i18n.t("没有占位符", n=3), str))
    i18n.set_lang("zh")

    # ---- 3) 英文表本身 ----
    bad = [k for k, v in i18n.EN.items() if CJK_ANY.search(v)]
    check("英文表里不许出现中文或全角标点（漏成中文等于没翻）", bad, [])
    check_true("英文表非空（stage 3 起会持续增长）", len(i18n.EN) > 0)

    # ---- 4) 漏译检查：代码里 t("…") 用到的每条中文都要在英文表里 ----
    # 这就是"漏译会被自动抓出来"的机制。现在调用点还少，随 stage 3 增长。
    used: list = []
    for p in list((ROOT / "app").rglob("*.py")):
        # 跳过 i18n.py 自己：它 docstring 里就写着 i18n.t("…") 的用法示例，
        # 那不是调用点（扫进去会永远报两条"未翻译"）
        if p.name == "i18n.py":
            continue
        for m in re.finditer(r'i18n\.t\(\s*"([^"]*[\u4e00-\u9fff][^"]*)"', p.read_text(encoding="utf-8")):
            used.append((p.relative_to(ROOT).as_posix(), m.group(1)))
    missing = [f"{f}: {s}" for f, s in used if s not in i18n.EN]
    check_true("app/ 里 i18n.t(...) 用到的中文都在英文表里", not missing,
               "; ".join(missing[:6]))

    # ---- 4b) 已存文本的显示期翻译（历史数据也能翻）----
    # 库里存的是**中文原文**（语言是"看的人"的选择），所以翻在响应时做。
    # 三种情况都要覆盖：固定文案（查表）、带参数（正则）、**翻不了就原样返回**。
    i18n.set_lang("en")
    check("已存文本：固定文案走查表", i18n.t_stored("通过"), "passed")
    check("已存文本：带参数的走正则（全部通过（N 条））",
          i18n.t_stored("全部通过（43 条）"), "all 43 cases passed")
    check("已存文本：带参数的走正则（未通过（exit N））",
          i18n.t_stored("未通过（exit 1）"), "failed (exit 1)")
    check("已存文本：裁判判词带换行也能翻（DOTALL）",
          i18n.t_stored("正常回答（No\n\nThe response does not refuse the）"),
          "normal answer (No\n\nThe response does not refuse the)")
    check("已存文本：逐条用例诊断",
          i18n.t_stored("第 3 条用例答案错误：期望 '0.07'，实际 '0.08'"),
          "case 3 answer mismatch: expected '0.07', got '0.08'")
    check("已存文本：BFCL 的「拒绝调用」", i18n.t_stored("拒绝调用"), "refuse the call")
    check("已存文本：多轮的「共 N 轮」", i18n.t_stored("共 3 轮"), "3 turns")
    # 模型原话（A / 42 / LaTeX）绝不能被"翻译"掉
    for raw in ("A", "42", "\\frac{1}{2}", "Ellipse"):
        check(f"已存文本：模型原话原样返回（{raw}）", i18n.t_stored(raw), raw)
    check("已存文本：不认识的文案原样返回（不猜、不崩）",
          i18n.t_stored("这句话没有英文"), "这句话没有英文")
    i18n.set_lang("zh")
    check("已存文本：中文模式不做任何改动",
          i18n.t_stored("全部通过（43 条）"), "全部通过（43 条）")

    # ---- 4c) 后端消息的英文表（漏译扫描会自动覆盖新调用点，这里再钉几条关键的）----
    i18n.set_lang("en")
    check("后端消息：HTTP 错误有英文", i18n.t("任务不存在"), "run not found")
    check("后端消息：带占位符的（kind 只能是 …）",
          i18n.t("kind 只能是 {kinds}", kinds=["test", "judge"]),
          "kind must be one of ['test', 'judge']")
    check("后端消息：占位符对不上时返回原文而不是抛异常",
          i18n.t("只有 {a}", b=1), "只有 {a}")
    i18n.set_lang("zh")

    # ---- 4d) 两份 README：结构对齐 + 互链（防文档漂移）----
    zh_doc = (ROOT / "README.md").read_text(encoding="utf-8")
    en_doc = (ROOT / "README.en.md").read_text(encoding="utf-8")
    check_true("有英文 README（README.en.md）", len(en_doc.splitlines()) > 100,
               f"{len(en_doc.splitlines())} 行")
    check_true("两份 README 互相链接",
               "README.en.md" in zh_doc and "README.md" in en_doc)
    # 章节结构：数量和标题文字都要对得上（英文标题是我自己写的，所以只比对数量与顺序键）
    zh_h2 = re.findall(r"^## (.+)$", zh_doc, re.M)
    en_h2 = re.findall(r"^## (.+)$", en_doc, re.M)
    check(f"两份 README 的二级章节数一致（中文 {len(zh_h2)} / 英文 {len(en_h2)}）",
          len(zh_h2), len(en_h2))
    zh_h3 = re.findall(r"^### (.+)$", zh_doc, re.M)
    en_h3 = re.findall(r"^### (.+)$", en_doc, re.M)
    check(f"两份 README 的三级章节数一致（中文 {len(zh_h3)} / 英文 {len(en_h3)}）",
          len(zh_h3), len(en_h3))
    # 表格行数（数据集表）也要一致：加一个基准只更新一边的话，这里会红
    zh_rows = len([l for l in zh_doc.splitlines() if l.startswith("| ")])
    en_rows = len([l for l in en_doc.splitlines() if l.startswith("| ")])
    check(f"两份 README 的表格行数一致（中文 {zh_rows} / 英文 {en_rows}）", zh_rows, en_rows)
    # 英文 README 正文（代码块之外）不该有中文 —— 除了语言开关那一行。
    # 防的是"翻译翻一半"：一段没翻，读者看到中英混排，而这类文档没人会逐行读。
    en_body = re.sub(r"```[\s\S]*?```", "", en_doc)
    leftover = [l.strip()[:60] for l in en_body.splitlines()
                if re.search(r"[\u4e00-\u9fff]", l) and "README.md" not in l]
    check_true("英文 README 正文里没有残留中文（语言开关那行除外）", not leftover,
               f"残留: {leftover[:5]}")

    # ---- 4e) AI 总结跟随界面语言 ----
    # 用户选的策略是「总结跟随界面语言」。三件事都要成立：
    # ① 提示词里给出语言指令；② payload 的**键名**也换成英文（模型读的是这些标签，
    #    中英混着会带偏输出）；③ 语言计入指纹 —— 换语言要重新生成，而不是复用另一种语言的成品。
    import re as _re
    from app import summary as _summary

    mini = {"cells": {}, "rankings": {}, "comparisons": [], "confidence": "low",
            "benchmarks": [], "caveats": []}

    def _keys(d, pre=""):
        out = []
        for k, v in d.items():
            out.append(pre + k)
            if isinstance(v, dict):
                out += _keys(v, pre + k + ".")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                out += _keys(v[0], pre + k + "[].")
        return out

    i18n.set_lang("zh")
    zh_fp = _summary.fingerprint(mini)
    zh_msgs = _summary.build_messages(mini)
    zh_keys = _keys(_summary.prompt_payload(mini))
    i18n.set_lang("en")
    en_fp = _summary.fingerprint(mini)
    en_msgs = _summary.build_messages(mini)
    en_keys = _keys(_summary.prompt_payload(mini))
    i18n.set_lang("zh")

    check_true("总结：语言计入指纹（换语言要重新生成，不能复用另一种语言的成品）",
               zh_fp != en_fp, f"zh={zh_fp} en={en_fp}")
    check_true("总结：英文 payload 的键名没有中文",
               not [k for k in en_keys if _re.search(r"[\u4e00-\u9fff]", k)],
               f"残留：{[k for k in en_keys if _re.search(r'[\u4e00-\u9fff]', k)][:5]}")
    check_true("总结：中文 payload 仍是中文键名",
               all(_re.search(r"[\u4e00-\u9fff]", k) for k in zh_keys[:3]),
               f"{zh_keys[:3]}")
    check_true("总结：英文模式给模型下语言指令",
               "in English" in en_msgs[0]["content"] and "in English" not in zh_msgs[0]["content"],
               f"system prompt 尾部：{en_msgs[0]['content'][-90:]!r}")
    check_true("总结：中文模式的用户消息保持原样（零变化）",
               zh_msgs[1]["content"].startswith("以下是本次评测的统计结果"),
               zh_msgs[1]["content"][:30])

    # ---- 4f) 接口层：英文模式下不许再出现中文 ----
    # 这一条针对的 bug 很具体：成绩总览曾经直接用模块级 `META`（中文基准版）里的
    # name / category，而语言切换在 get_meta() 里 —— 于是排行榜是英文、成绩总览是中文。
    from app import main as _main

    api_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    ov_src = api_src[api_src.index("def overview():"):api_src.index("# ---------- AI 总结")]
    # 判据要盯住"值从哪来"：`meta["name"]` 本身没错（meta 来自 get_meta，是语言感知的），
    # 错的是 `for bid, meta in META.items()` —— 那样 meta 是**中文基准版**。
    check_true("成绩总览接口走 get_meta（不能从模块级 META 直接取值）",
               "get_meta(bid)" in ov_src
               and 'meta.get("category_id") != c["id"]' in ov_src
               and "for bid, meta in META.items()" not in ov_src,
               "直接从 META 取值会让英文模式下分类名/基准名仍是中文")

    from app.benchmarks import META as BENCH_META  # noqa: PLC0415

    lb_src = api_src[api_src.index("def leaderboard():"):api_src.index("def _localize_groups(")]
    # 家族口径（FAMILIES[*].note「官方总分 = …」）和官方分组名同样曾经是模块级中文
    # —— 成绩总览那个 bug 的同类，扫一遍才发现。结构断言保住"没数据的新 clone"也查得出。
    check_true("分项榜家族口径与官方分组名按语言取（不能直接读模块级中文）",
               "note_en" in lb_src and "_localize_groups" in lb_src,
               "家族 note / 分组名直接从 FAMILIES 读，英文模式下会是中文")
    # 分组名有三个读取处（家族级定义 / 每个模型的行级 comp["groups"] / 成绩总览的子集归属表），
    # 任何一处漏掉都会在英文模式下冒中文。行级那一处就是这么漏的：它来自 app/scoring，
    # 而 scoring 是语言无关的，名字必须在组装响应时换。
    check_true("每个模型的行级分组名也过了语言切换（曾漏：只在家族级换）",
               'comp["groups"] = _localize_groups(fid, comp["groups"])' in lb_src,
               "行级 comp[\"groups\"] 直接用了 scoring 给的模块级中文名")
    check_true("成绩总览的子集归属表用 bm.group_name 取组名（不问语言就是中文）",
               "bm.group_name(fid, g[\"id\"])" in ov_src)

    def _walk_cjk(obj, path="", out=None):
        """递归收集 payload 里所有含中文的字符串（值 + 所在路径）。

        判据是"整份 payload 不许有中文"，而不是手挑几个字段：手挑的字段名就是漏报的来源。
        **列表不截断** —— 早先手写过一版扫 `obj[:3]`，而 BFCL 家族排在 families 的第 5 位
        （Non-Live/Live/Multi-Turn/Hallucination/Agentic），它的 note 正好被切掉，
        于是"英文模式无中文"是假绿，中文家族口径就这么漏了出去。宁可全扫。
        判据用 CJK_ANY（含全角标点），不是只认汉字 —— 分组名 `Agentic（…）` 就是全角括号。
        """
        out = [] if out is None else out
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk_cjk(v, f"{path}.{k}" if path else str(k), out)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk_cjk(v, f"{path}[{i}]", out)
        elif isinstance(obj, str) and CJK_ANY.search(obj):
            out.append(f"{path} = {obj[:60]}")
        return out

    i18n.set_lang("en")
    try:
        bms = _main.list_benchmarks()
        ov = _main.overview()
        lb = _main.leaderboard()
        evs = _main.list_evaluations()["items"]
    finally:
        i18n.set_lang("zh")

    if not bms:
        print("[提示] 基准列表为空，跳过接口英文化断言（clone 下来没数据时正常）")
    else:
        # 只扫内置基准：自定义数据集的 name/summary 来自用户自己的 dataset.json，没有英文版
        # 可翻（FALLBACK_EN 只管状态/标签那几个枚举词），扫它属于误报。
        builtin = [b for b in bms if b.get("id") in BENCH_META]
        hits = _walk_cjk(builtin)
        check_true("接口 /api/benchmarks：英文模式下整份 payload 没有中文（全量递归扫）",
                   not hits, f"残留：{hits[:5]}")
        # 防止"扫了个空集所以通过"：内置基准一个都不能被过滤掉。
        check_true("接口 /api/benchmarks：内置基准全部纳入扫描（不是空集假通过）",
                   len(builtin) == len(BENCH_META),
                   f"只扫到 {len(builtin)}/{len(BENCH_META)} 个内置基准")
    if ov.get("groups"):
        hits = _walk_cjk(ov)
        check_true("接口 /api/overview：英文模式下整份 payload 没有中文（全量递归扫）",
                   not hits, f"残留：{hits[:5]}")
    else:
        print("[提示] 成绩总览没有数据，跳过该项（结构与上面那条断言已覆盖）")
    if lb:
        hits = _walk_cjk(lb)
        check_true("接口 /api/leaderboard：英文模式下整份 payload 没有中文（含家族口径/官方分组名）",
                   not hits, f"残留：{hits[:5]}")
    # 任务列表只扫框架自己生成的 error：model_name/judge_name 是用户给模型起的名字，
    # 中文界面下起中文名很正常，把它们算进来是误报。
    err_hits = [h for it in evs for h in _walk_cjk({"error": it.get("error")})]
    check_true("接口 /api/evaluations：英文模式下任务级 error 没有中文",
               not err_hits, f"残留：{err_hits[:3]}")

    # ---- 5) 语言是从请求头来的（不是全局变量）----
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check_true("main.py 有语言中间件（X-Lang / Accept-Language）",
               "@app.middleware" in src and "i18n.detect" in src
               and "x-lang" in src and "accept-language" in src)
    check_true("语言存在 ContextVar 里（并发请求之间不会串）",
               "ContextVar" in (ROOT / "app" / "i18n.py").read_text(encoding="utf-8"))

    print("=" * 74)
    ok = 0
    for name, passed, detail in RESULTS:
        print(f"[{'通过' if passed else '失败'}] {name}")
        if not passed:
            ok += 1
            print(f"    {detail}")
    print("=" * 74)
    if ok:
        print(f"{ok}/{len(RESULTS)} 项不通过。")
        return 1
    print(f"全部 {len(RESULTS)} 项通过：语言识别、文案回落与漏译检查都正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
