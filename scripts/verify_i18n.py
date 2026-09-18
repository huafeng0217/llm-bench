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
    bad = [k for k, v in i18n.EN.items() if re.search(r"[\u4e00-\u9fff]", v)]
    check("英文表里不许出现中文（漏成中文等于没翻）", bad, [])
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
