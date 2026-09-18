"""界面语言：**中文原文即 key**，英文在 EN 表里查。

为什么用「中文原文当 key」（而不是给每条文案编 `task.list.header` 这种键）：

1. **中文模式零改动**：查不到就返回原文，所以现有中文行为与全部自检原样有效；
2. **漏译能被自动测出来**：英文模式下界面里不该再出现中文 —— 只要有漏译就会露出来，
   而"键名"方案里漏译只会显示空白或报错，反而更糟；
3. **省掉一半工作量**：这个项目是先有中文再补英文的，编 key 本身就要把几百条文案
   过一遍，等于多做一遍工。

基准的 `name / summary / description / label / status` 是长文本、还会被复用，
不适合当 key —— 那些用 `Benchmark` 上的显式 `*_en` 字段（见 `benchmarks/types.py`）。

语言来源（优先级，见 :func:`detect`）：
    请求头 ``X-Lang``（前端切换后每条请求都带）→ ``Accept-Language``（首次访问）→ 默认中文。

用法::

    from . import i18n
    raise HTTPException(400, i18n.t("这个基准需要裁判模型，请先添加一个「判别器」"))
    msg = i18n.t("已开始下载 {n} 个子集", n=len(todo))
"""
from contextvars import ContextVar

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# 当前请求的语言。用 ContextVar 而不是全局变量：FastAPI 的请求是并发处理的，
# 全局变量会让 A 请求的语言串到 B 请求上（而且这种错只在并发时出现）。
_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT_LANG)

# 英文文案表：key 是**中文原文**（和代码里写的一模一样，改中文时必须同步改这里）。
# 占位符用 {name} 形式，和中文原文保持一致。
EN: dict[str, str] = {
    # ---- 通用 ----
    "已接入模型": "Models",
    "可用基准": "Benchmarks",
    "评测任务": "Runs",
    "最佳正确率": "Best accuracy",
    "模型管理": "Models",
    "选择基准并发起评测": "Pick a benchmark and run",
    "成绩总览": "Overview",
    "排行榜": "Leaderboard",
    "AI 总结": "AI summary",
    "填写任意 OpenAI 兼容接口并对比排名": "Point it at any OpenAI-compatible endpoint and compare models",
}


def normalize(value: str) -> str:
    """把语言标记（``zh-CN`` / ``en-US`` / ``EN``）归一到 ``zh`` / ``en``；认不出返回空串。"""
    v = (value or "").strip().lower().replace("_", "-")
    if not v:
        return ""
    head = v.split(",")[0].split(";")[0].strip()   # "en-US,en;q=0.9" → "en-us"
    if head.startswith("zh"):
        return "zh"
    if head.startswith("en"):
        return "en"
    return ""


def detect(accept_language: str, x_lang: str = "") -> str:
    """决定这次请求用哪种语言。

    ``X-Lang`` 优先（前端切换后每条请求都带，代表用户的明确选择）；
    否则看 ``Accept-Language``，按 q 值从高到低取第一个能识别的语言；
    都不认识就用默认中文。
    """
    explicit = normalize(x_lang)
    if explicit:
        return explicit
    items = []
    for i, part in enumerate((accept_language or "").split(",")):
        bits = part.split(";")
        tag = bits[0].strip()
        q = 1.0
        for b in bits[1:]:
            if b.strip().startswith("q="):
                try:
                    q = float(b.strip()[2:])
                except ValueError:
                    q = 0.0
        if tag:
            items.append((-q, i, tag))
    for _, _, tag in sorted(items):
        got = normalize(tag)
        if got:
            return got
    return DEFAULT_LANG


def set_lang(value: str) -> None:
    _lang.set(value if value in LANGS else DEFAULT_LANG)


def get_lang() -> str:
    return _lang.get()


def t(zh: str, **params) -> str:
    """取当前语言的文案。中文原文即 key；英文表里没有就回落中文（宁可显示中文，也别空白）。"""
    s = EN.get(zh, zh) if get_lang() == "en" else zh
    if params:
        try:
            s = s.format(**params)
        except (KeyError, IndexError, ValueError):
            # 文案里的占位符和调用方给的对不上时，别把整条消息吞掉 —— 返回原文更好排查
            return zh.format(**params) if params else zh
    return s
