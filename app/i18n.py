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
import re

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# 当前请求的语言。用 ContextVar 而不是全局变量：FastAPI 的请求是并发处理的，
# 全局变量会让 A 请求的语言串到 B 请求上（而且这种错只在并发时出现）。
_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT_LANG)

# 英文文案表：key 是**中文原文**（和代码里写的一模一样，改中文时必须同步改这里）。
# 占位符用 {name} 形式，和中文原文保持一致。
EN: dict[str, str] = {
    # ---- 后端消息（stage 3：每次请求现算的那些）----
    "name 和 base_url 不能为空": "name and base_url cannot be empty",
    "模型不存在": "model not found",
    "题库不存在": "dataset not found",
    "题库不存在: {bid}": "dataset not found: {bid}",
    "该题库不支持自动下载": "this dataset cannot be downloaded automatically",
    "没有这个家族: {fid}": "no such family: {fid}",
    "额外请求参数不是合法 JSON：{err}": "extra request parameters are not valid JSON: {err}",
    '额外请求参数必须是 JSON 对象，例如 {"enable_thinking": false}':
        'extra request parameters must be a JSON object, e.g. {"enable_thinking": false}',
    "kind 只能是 {kinds}": "kind must be one of {kinds}",
    "这个基准由裁判模型判分，请先选择一个「判别器」再开始":
        "this benchmark is scored by a judge model; pick a judge before starting",
    "裁判模型不存在": "judge model not found",
    "裁判不能是被测模型自己 —— 自己判自己会让分数失去意义":
        "The judge cannot be the model under test — a model scoring itself makes the number meaningless",
    "ids 需为逗号分隔的数字，如 ids=77,79": "ids must be comma-separated numbers, e.g. ids=77,79",
    "对比需要恰好 2 个任务 id": "comparing needs exactly 2 run ids",
    "任务不存在": "run not found",
    "任务不存在（可能已被删除）": "run not found (it may have been deleted)",
    "该任务正在运行中": "this run is still running",
    "该任务已全部完成，无需续跑": "this run is already complete; nothing to resume",
    "该任务还没有任何已完成题目，请直接新建评测":
        "this run has no finished items yet — start a new run instead",
    "两个任务的基准不同（{a} vs {b}），逐题对比需要同一基准":
        "the two runs use different benchmarks ({a} vs {b}); item-by-item comparison needs the same one",
    # ---- 异常消息（会显示在界面上）----
    "模型返回为空": "the model returned nothing",
    "回复里没有 JSON": "the reply contained no JSON",
    "JSON 不完整（可能被 max_tokens 截断）": "the JSON is incomplete (possibly cut off by max_tokens)",
    "模型没有返回可解析的 JSON：{err}": "the model returned no parseable JSON: {err}",
    # ---- 沙箱状态（/api/sandbox/status）----
    "未找到 docker 命令（Docker 未安装或不在 PATH）":
        "the docker command was not found (Docker is not installed or not on PATH)",
    "docker version 超时": "`docker version` timed out",
    "Docker 引擎未运行：{err}": "the Docker engine is not running: {err}",
    "Docker 可用（{ver}）但缺少镜像 {image}，请先执行：docker pull {image}":
        "Docker is available ({ver}) but the image {image} is missing; run: docker pull {image}",
    "Docker {ver} · 镜像 {image} 就绪": "Docker {ver} · image {image} ready",
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
    """取当前语言的文案。中文原文即 key；英文表里没有就回落中文（宁可显示中文，也别空白）。

    **永不抛异常**：占位符和参数对不上时原样返回中文原文 —— 一条提示写错不该变成 500
    （而且要能在界面上看见它，才知道哪里写错了）。
    """
    s = EN.get(zh, zh) if get_lang() == "en" else zh
    if not params:
        return s
    try:
        return s.format(**params)
    except (KeyError, IndexError, ValueError):
        try:
            return zh.format(**params)
        except (KeyError, IndexError, ValueError):
            return zh


# ==================== 已存文本的显示期翻译 ====================
# 为什么不能靠"写入时用当前语言"：语言是**看的人**的选择，不是跑评测那天的选择；
# 同一条记录今天看中文、明天看英文都可能。所以库里存的永远是**中文原文**，
# 显示时按请求语言翻译 —— 好处是**历史数据也能翻**（不需要迁移、不需要重跑）。
#
# 两种匹配：
#   STORED_EN  固定文案（判定结果、错误说明），原文就是 key；
#   STORED_RE  带参数的（"全部通过（43 条）"、"未通过（exit 1）"），用正则取参数再套模板。
# 都不匹配就**原样返回** —— 明细里的"模型答"经常是模型的原话（A、42、LaTeX…），
# 那些绝不能被翻译。
STORED_EN: dict[str, str] = {
    # ---- predicted ----
    "通过": "passed",
    "无调用": "no call",
    "未通过": "failed",
    "超时未通过": "timed out",
    "未抽到可运行的代码": "no runnable code found",
    "输出被截断": "output truncated",
    "[输出被 max_tokens 截断]": "[output truncated at max_tokens]",
    "判分失败（被测模型没有返回正文）": "not scored (the model returned no content)",
    "判分失败（裁判回复无法解析）": "not scored (the judge reply could not be parsed)",
    # ---- expected ----
    "拒绝调用": "refuse the call",
    "裁判判定：拒绝/未照做": "judge verdict: refusal / not followed",
    "裁判判定：正常回答": "judge verdict: normal answer",
    "裁判判定：未越狱": "judge verdict: not jailbroken",
    "裁判判定：过度拒绝": "judge verdict: over-refusal",
    # ---- error ----
    "模型回复里没有能通过语法检查的 Python 代码":
        "the reply contained no Python that passes a syntax check",
    "被测模型没有产出正文内容（常见原因：思考 token 占满了 max_tokens，或接口返回了空回复）。"
    "空回复不等于拒绝，不能算作安全。":
        "the model produced no content (usually thinking tokens used up max_tokens, or the API returned "
        "an empty reply). An empty reply is not a refusal and must not be treated as safe.",
    "该题没有任何测试用例": "this item has no test cases",
    "测试用例解码失败": "could not decode the test cases",
    "无可用用例": "no usable test cases",
    "没有可用的测试用例": "no usable test cases",
    "容器被终止": "the container was killed",
    "返回值不是合法 JSON": "the return value is not valid JSON",
    # ---- 共享状态 / 落库文案（下载状态、总结状态、任务级错误）----
    "正在下载中": "downloading",
    "下载中…": "Downloading…",
    "已开始下载": "download started",
    "全部子集都在下载中或已开始": "all subsets are downloading or already started",
    "排队中…": "queued…",
    "已开始下载 {0} 个子集": "started downloading {0} subsets",
    "正在生成…": "generating…",
    "正在生成中": "generating",
    "数据没有变化，直接用了上次生成的总结": "the data has not changed, so the previous summary was reused",
    "模型名和 base_url 不能为空": "model name and base_url cannot be empty",
    "代码沙箱不可用": "the code sandbox is unavailable",
    "该任务还没有任何已完成题目，请直接新建评测":
        "this run has no finished items yet — start a new run instead",
}

# (正则, 英文模板)：模板里用 {0} {1} … 对应正则的捕获组
STORED_RE: list[tuple[str, str]] = [
    # 代码题 / 多轮
    (r"^全部通过（(\d+) 条）$", "all {0} cases passed"),
    (r"^通过 (\d+)/(\d+) 条$", "{0}/{1} cases passed"),
    (r"^未通过（exit (\d+)）$", "failed (exit {0})"),
    (r"^共 (\d+) 轮$", "{0} turns"),
    (r"^(\S+) · 通过全部单元测试$", "{0} · pass all unit tests"),
    (r"^(\S+) · 通过全部测试用例$", "{0} · pass all test cases"),
    # 安全类：判定词 + 括号里的裁判原文（可能很长、带换行，所以用 DOTALL）
    (r"(?s)^正常回答（(.+)）$", "normal answer ({0})"),
    (r"(?s)^过度拒绝（(.+)）$", "over-refusal ({0})"),
    (r"(?s)^未越狱（(.+)）$", "not jailbroken ({0})"),
    (r"(?s)^越狱成功（(.+)）$", "jailbroken ({0})"),
    (r"(?s)^判分失败（(.+)）$", "not scored ({0})"),
    # 逐条用例的诊断
    (r"(?s)^第 (\d+) 条用例答案错误：期望 (.*)，实际 (.*)$",
     "case {0} answer mismatch: expected {1}, got {2}"),
    (r"(?s)^第 (\d+) 条用例超时：期望 (.*)，实际 (.*)$",
     "case {0} timed out: expected {1}, got {2}"),
    (r"(?s)^第 (\d+) 条用例运行错误：期望 (.*)，实际 (.*)；(.*)$",
     "case {0} raised an error: expected {1}, got {2}; {3}"),
    (r"^模型在 max_tokens=(\d+) 处仍被截断，思考过程占满额度、没有产出代码$",
     "still truncated at max_tokens={0}: thinking used the whole budget and produced no code"),
    (r"^沙箱执行超过 (\d+)s 被终止（可能是死循环）$",
     "the sandbox run was killed after {0}s (possibly an infinite loop)"),
    (r"(?s)^裁判原文：(.*)$", "judge said: {0}"),
    (r"(?s)^判分器未输出结果：(.*)$", "the grader produced no result: {0}"),
    # 沙箱状态（/api/sandbox/status 会显示给用户）
    (r"^Docker 引擎未运行：(.*)$", "the Docker engine is not running: {0}"),
    (r"^Docker 可用（(.*)）但缺少镜像 (.*)，请先执行：docker pull (.*)$",
     "Docker is available ({0}) but the image {1} is missing; run: docker pull {2}"),
    (r"^Docker (.*) · 镜像 (.*) 就绪$", "Docker {0} · image {1} ready"),
    (r"^已开始下载 (\d+) 个子集$", "started downloading {0} subsets"),
]

_STORED_RE = [(re.compile(p), en) for p, en in STORED_RE]


def t_stored(text: str) -> str:
    """把**库里存的中文原文**翻成当前语言；翻不了就原样返回。

    只用于明细里的 expected / predicted / error 这类"程序生成的短文案"，
    **不要**用在模型原话上（模型答本身就该原样显示）。
    """
    if not text or get_lang() == "zh":
        return text
    hit = STORED_EN.get(text)
    if hit is not None:
        return hit
    for rx, en in _STORED_RE:
        m = rx.match(text)
        if m:
            try:
                return en.format(*m.groups())
            except (IndexError, KeyError, ValueError):
                return text
    return text
