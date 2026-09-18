"""题库与基准元数据的自检：行数缓存 / 原子写 / 卡片文案放不放得下。

为什么需要它
-----------
这里盯的三件事都有一个**安静**的失败模式：

1. **行数缓存**（`datasets.count_lines`）：题库列表显示的题数、覆盖率用的题库总量都来自它。
   如果缓存 key 漏了 size 或 mtime，**重新下载题库后题数不会更新** —— 页面继续显示旧题数，
   而覆盖率会按旧题量算，于是「完整评测」的判断跟着错。这类错误不会报错，只会一直错下去。
2. **原子写**（`benchmarks/_util.write_jsonl`）：下载中断若留下一个**被截断但看起来正常**的
   `data/xxx.jsonl`，它会被当成一份完整题库 —— 「14042 题的 MMLU 只下到 5000 题」
   会让跑满 5000 题的评测显示成 100% 覆盖。所以必须验证失败时**不留下半成品**。
3. **卡片文案**（`Benchmark.summary`）：卡片只给两行（最窄 320px 的卡片约 44 个中文字），
   超了会被 CSS 省略 —— 而「被省略」在页面上看起来完全正常，只有认真读才会发现少了半句。
   实测过一次：30 个基准里 24 个（80%）看不全，最长的只显示得下 23%。

用临时目录（或纯计算），不碰 `data/`。

用法::

    python scripts/verify_datasets.py
"""
import json
import pathlib
import re
import shutil
import sys
import tempfile
import time
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import datasets, scoring  # noqa: E402
from app.benchmarks import ENTRIES, get_meta  # noqa: E402
from app.benchmarks._util import write_jsonl  # noqa: E402


def _pick_tmp() -> pathlib.Path:
    """挑一个**真的能写文件**的临时目录。

    优先系统 temp，但文件沙箱可能禁止写工作区之外（受限环境里往系统 temp 写会
    PermissionError，sqlite 也因此在那边打不开库）。所以不猜，写个探针文件试一下，
    不行就退回工作区内的目录（跑完会删）。
    """
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="llmbench-datasets-"))
        (d / ".probe").write_text("x", encoding="utf-8")
        return d
    except OSError:
        pass
    d = ROOT / ".datasets_tmp"
    d.mkdir(exist_ok=True)
    return d

RESULTS = []


def check(name, got, want):
    RESULTS.append((name, got == want, f"期望 {want!r}，实际 {got!r}"))


def check_true(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail or "期望为真，实际为假"))


def main() -> int:
    tmp = _pick_tmp()
    try:
        # ---- 行数缓存 ----------------------------------------------------
        p = tmp / "demo.jsonl"
        write_jsonl(p, [{"i": i} for i in range(5)])
        check("count_lines 数出 5 行", datasets.count_lines(p), 5)
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n")           # 追加空行不改 size 之外的语义，但 size 变了会失效重算
        check("空行不计入题数（追加空行后仍是 5）", datasets.count_lines(p), 5)

        def opened_by(fn):
            """跑 fn()，返回 (结果, 这次真正 open 了几次)。用来验证缓存是否真的命中。"""
            # count_lines 用的是内建 open；模块里没有这个名字，先记下再临时替换
            real_open = datasets.__dict__.get("open", open)
            n = []
            datasets.open = lambda *a, **k: (n.append(a), real_open(*a, **k))[1]
            try:
                return fn(), len(n)
            finally:
                datasets.open = real_open

        r, n = opened_by(lambda: datasets.count_lines(p))
        check("缓存命中：结果仍然是 5", r, 5)
        check("缓存命中：没有再去读文件", n, 0)

        # 文件变大 -> size 变了，必须重算
        write_jsonl(p, [{"i": i} for i in range(9)])
        r, n = opened_by(lambda: datasets.count_lines(p))
        check("文件变大后缓存失效（重数出 9 行）", r, 9)
        check("文件变大后确实重读了文件", n, 1)

        # 长度相同、只是内容/时间不同 -> 靠 mtime 失效。
        # sleep 一点点：文件系统 mtime 有分辨率，改写太快可能落在同一个刻度上。
        time.sleep(0.02)
        write_jsonl(p, [{"i": i * 2} for i in range(9)])
        r, n = opened_by(lambda: datasets.count_lines(p))
        check("等长重写后仍然重算", r, 9)
        check("等长重写后确实重读了文件（mtime 生效）", n, 1)

        # 不存在的文件返回 0，而不是抛异常
        check("文件不存在时返回 0", datasets.count_lines(tmp / "nope.jsonl"), 0)

        # ---- 题数定义必须三处一致 ---------------------------------------
        # 页面显示的题数（list_datasets）、覆盖率用的题量（scoring.full_count）、
        # 数行函数本身，三者不能各算一套
        real_data, real_bfcl = datasets.DATA_DIR, datasets.BFCL_DIR
        try:
            datasets.DATA_DIR = tmp
            datasets.BFCL_DIR = tmp / "bfcl_v4"
            datasets.BFCL_DIR.mkdir(exist_ok=True)
            write_jsonl(datasets.BFCL_DIR / "sub.jsonl", [{"i": i} for i in range(3)])
            write_jsonl(tmp / "sub_answer.jsonl", [{"i": i} for i in range(99)])
            listed = {d["id"]: d["count"] for d in datasets.list_datasets()}
            check("list_datasets 只收题库、排除 *_answer.jsonl", sorted(listed), ["demo", "sub"])
            check("list_datasets 的题数与 count_lines 一致", listed["demo"], datasets.count_lines(p))
            check("子目录题库也在列表里", listed["sub"], 3)
        finally:
            datasets.DATA_DIR, datasets.BFCL_DIR = real_data, real_bfcl

        # 真实题库上对一次账（不联网：只读本地已有文件）
        for bid in ("humaneval", "truthfulqa", "gpqa"):
            try:
                path = datasets._dataset_path(bid)
            except FileNotFoundError:
                continue
            check(f"{bid}: 覆盖率用的题量 = 行数", scoring.full_count(bid), datasets.count_lines(path))

        # ---- 原子写 ------------------------------------------------------
        target = tmp / "atomic.jsonl"
        write_jsonl(target, [{"a": 1}])
        check("写完目标文件存在", target.exists(), True)
        check_true("写完不留 .part 临时文件", not list(tmp.glob("*.part")), "残留了 .part 文件")
        check("内容正确且是 jsonl", json.loads(target.read_text(encoding="utf-8").strip()), {"a": 1})

        # 失败场景：内容不可序列化 -> 抛异常，但**旧文件必须原样保留**、不留半成品
        before = target.read_text(encoding="utf-8")
        try:
            write_jsonl(target, [{"a": 2}, {"bad": object()}])
            check_true("不可序列化时应当抛异常", False, "居然没抛异常")
        except TypeError:
            check_true("不可序列化时抛异常", True)
        check("失败后旧文件内容原样保留", target.read_text(encoding="utf-8"), before)
        check_true("失败后不留 .part 临时文件", not list(tmp.glob("*.part")), "残留了 .part 文件")

        # 父目录不存在时自动创建（bfcl 子目录第一次下载就靠这个）
        deep = tmp / "newdir" / "x.jsonl"
        write_jsonl(deep, [{"a": 1}])
        check("父目录不存在时自动创建", deep.exists(), True)

        # ---- 卡片文案：summary 必须真的能在卡片里显示完 ----------------------
        # 卡片最窄 320px：减左右 padding 32px 与左侧色条 3px，正文宽约 285px。
        # 按字宽折算（CJK 12.5px、ASCII 7px，卡片字号 12.5px），两行 ≈ 44 个中文字。
        # 超了就会被 CSS 省略 —— 而「被省略」在页面上看起来完全正常，所以在这里拦。
        card_w = 320 - 32 - 3

        def text_w(s):
            return sum(12.5 if unicodedata.east_asian_width(c) in "WF" else 7.0 for c in s)

        no_summary = [e.id for e in ENTRIES if not e.summary]
        check_true("每个基准都有卡片简介 summary", not no_summary, f"缺: {no_summary}")
        too_long = [f"{e.id}({text_w(e.summary)/card_w:.1f} 行)"
                    for e in ENTRIES if text_w(e.summary) > 2 * card_w]
        check_true("简介都在两行以内（超出会被 CSS 省略）", not too_long, f"超长: {too_long}")
        # 简介不该比完整说明还长（写反了就是字段用错）
        backwards = [e.id for e in ENTRIES if e.summary and len(e.summary) >= len(e.description)]
        check_true("简介是 description 的缩略版（不是反过来）", not backwards, f"异常: {backwards}")
        # 简介不该复述名字：把名字整段抄进简介说明这一行没提供新信息
        dup = [e.id for e in ENTRIES if e.name and e.name.rstrip("（）) ") in e.summary]
        check_true("简介不复述基准名", not dup, f"复述名字: {dup}")
        # 简介也不该重复题量（卡片标签上已有 `N 题`，重复会出现「16 题（16 题）」这种）
        with_count = []
        for e in ENTRIES:
            m = re.search(r"（(\d+) 题）", e.label or "")
            if m and f"{m.group(1)} 题" in e.summary:
                with_count.append(e.id)
        check_true("简介里不写题量（卡片标签上已有）", not with_count, f"重复题量: {with_count}")

        # 需要裁判的基准，必须声明「有判定但结果不利」这一档在明细里叫什么：
        # 安全类的 ok=0 不是「答错」而是「越狱成功 / 过度拒绝」，没声明就会显示成答错
        # （实测 jbb_benign 的 10 条过度拒绝全被写成「越狱成功」）。措辞随 benchmark
        # 元数据给前端，而不是在前端硬编码基准名。
        no_adverse = [e.id for e in ENTRIES if e.requires_judge and not e.adverse_label]
        check_true("需要裁判的基准都声明了 adverse_label（明细里不能显示成「答错」）",
                   not no_adverse, f"缺: {no_adverse}")
        wrong_adverse = {e.id: e.adverse_label for e in ENTRIES
                         if e.requires_judge and e.adverse_label not in ("越狱成功", "过度拒绝")}
        check_true("adverse_label 用既定措辞（越狱成功 / 过度拒绝）", not wrong_adverse,
                   f"异常: {wrong_adverse}")
        # 元数据要真的传到前端：漏了 passthrough，前端只能退回「答错」。
        # 用 get_meta（就是 /api/benchmarks 走的那个函数），而不是直接读 META。
        not_exposed = {bid: get_meta(bid).get("adverse_label") for bid in
                       ("harmbench", "jbb_harmful", "jbb_benign")}
        expect = {"harmbench": "越狱成功", "jbb_harmful": "越狱成功", "jbb_benign": "过度拒绝"}
        check("adverse_label 随元数据暴露给前端", not_exposed, expect)

        # 自定义题库（data/ 里存在但 META 未收录）走 FALLBACK，也必须有能放下的简介 ——
        # 否则那些卡片会退回显示一段「和所有自定义题库都一样」的格式说明，等于没有信息。
        fb = get_meta("__这个题库不存在__")
        check_true("自定义题库的卡片也有简介（走 FALLBACK）", bool(fb.get("summary")),
                   f"FALLBACK 没给 summary，卡片会退回显示 description：{fb.get('description')!r}")
        check_true("自定义题库的简介也在两行以内",
                   text_w(fb.get("summary") or "") <= 2 * card_w,
                   f"折行 {text_w(fb.get('summary') or '')/card_w:.1f}")

        # ---- 下载按钮上的题量必须和实际落库的题数一致 ------------------------
        # label 里的「（N 题）」是**对用户的承诺**，卡片上显示的题数则来自文件行数。
        # 两者不一致时页面看起来完全正常（一个说 1172、一个说 1169），只有对比才发现 ——
        # 实测踩过：ARC 的映射漏掉了 3 道五选一的题，标签写着 1172 而实际只有 1169。
        for e in ENTRIES:
            m = re.search(r"（(\d+) 题）", e.label or "")
            if not m:
                continue
            try:
                path = datasets._dataset_path(e.id)
            except FileNotFoundError:
                continue        # 题库没下载就跳过（data/ 不进版本库，clone 下来是空的）
            check(f"{e.id}: 下载按钮上的题量 = 实际题数", datasets.count_lines(path), int(m.group(1)))

        print(f"临时目录: {tmp}")
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
        print(f"全部 {len(RESULTS)} 项通过：行数缓存、原子写与卡片文案都正确。")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
