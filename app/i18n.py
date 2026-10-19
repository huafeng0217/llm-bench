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
    "要复用的模型不存在": "the model whose connection info is being copied does not exist",
    # 「先让人看见后果」的两处确认/拦截文案（响应时翻：前端把 detail 直接拿给用户看）
    "{name} 已有 {n} 条历史评测。改成「判别器」后不能再发起新评测，"
    "但已有的成绩仍然保留在排行榜上（数据是真的，不该因为角色变了就消失）。确认要改吗？":
        "{name} already has {n} run(s). After switching it to a judge it can no longer start "
        "new runs, but the existing results stay on the leaderboard (the data is real and should "
        "not vanish just because the role changed). Change it anyway?",
    "{name} 是「判别器」，不能作为被测模型。判别器只用于给安全类基准判分；"
    "如果确实要测它，请先在模型管理里把它改回「被测模型」。":
        "{name} is a judge model, so it cannot be used as a model under test. Judges only score "
        "safety benchmarks; to evaluate it, switch it back to a model under test first.",
    "{name} 的用途是「被测模型」，不能当裁判。请先在模型管理里把要当裁判的模型设为「判别器」。":
        "{name} is a model under test, so it cannot act as the judge. Switch the model you want "
        "to use as a judge to a judge model first.",
    # 注意占位符风格：`t()` 用 {n} 这种具名参数，而 STORED_RE 的模板用 {0}（正则捕获组）。
    # 同一句话两条路都要有，且各自风格要对 —— 写错不会报错，只会静默回落中文。
    "已开始下载 {n} 个子集": "started downloading {n} subsets",
    # 统计层的数据问题说明（caveats）：进 /api/summary/stats 的响应体（界面上「本次依据的数据」）
    # 与 AI 总结的 prompt，所以响应时按语言取。
    "{model} 在 {bench} 上有个更高的分 {hi}% 只跑了 {n}/{fc} 题；"
    "它完整跑过的最好成绩是 {best}%（{bn} 题）":
        "{model} has a higher score on {bench} — {hi}% from only {n}/{fc} items; "
        "its best complete run is {best}% ({bn} items)",
    "{model} 在 {bench} 上只跑了 {n}/{fc} 题（{acc}%），题量远少于完整评测，"
    "该成绩不具可比性，不应与其他模型的完整成绩并列":
        "{model} only ran {n}/{fc} items on {bench} ({acc}%), far fewer than a full run, so the "
        "score is not comparable and must not be ranked alongside complete results",
    "{model} 在 {bench} 上只有 {acc}%，低于随机猜的 {base}% —— "
    "这通常不是能力问题，而是答案抽取或接口格式不兼容，建议先核对原始输出":
        "{model} scored only {acc}% on {bench}, below the {base}% random-guess baseline — this is "
        "usually not an ability problem but an answer-extraction or API-format mismatch; "
        "check the raw output first",
    "{bench} 只有 {fc} 题，一题价值 {per} 个百分点，"
    "约 {thr} 个百分点以内的差距无法区分":
        "{bench} has only {fc} items, so each item is worth {per} percentage points; "
        "gaps under about {thr} points cannot be told apart",
    "{name} 有 {n} 条评测任务（共 {m} 题逐题明细，含模型原始输出）。"
    "删除模型会一并删掉这些成绩与明细文件（无法恢复）。确认要删除吗？":
        "{name} has {n} run(s) with {m} item records in total (raw model output included). "
        "Deleting the model also deletes those results and their detail files "
        "(this cannot be undone). Delete anyway?",
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
    "选择基准并发起评测": "Pick and Run",
    "成绩总览": "Overview",
    "排行榜": "Leaderboard",
    "AI 总结": "AI summary",
    "填写任意 OpenAI 兼容接口并对比排名": "Point it at any OpenAI-compatible endpoint and compare models",
    # ---- 成绩总览：视图切换 + 能力雷达图 ----
    "矩阵": "Matrix",
    "能力雷达": "Radar",
    "准确率 × 延迟": "Accuracy × Latency",
    "雷达图看能力结构：哪个分类强、哪个分类是短板。":
        "The radar shows the shape of ability: which categories are strong and which are weak.",
    "散点图看性价比：谁又快又准（默认一个点 = 一个模型的一个分类，可切到基准下钻）。":
        "The scatter shows value: who is both fast and accurate (by default one dot = one model × "
        "one category; switch to per-benchmark to drill down).",
    "模型": "Model",
    "至少要有 3 个分类有完整成绩才能画雷达图":
        "A radar needs at least 3 categories with a complete run",
    "能力雷达图": "Ability radar chart",
    "{0}：本次参评模型的区间 {1}% – {2}%（这根轴按这个区间归一）":
        "{0}: participating models span {1}% – {2}% (this axis is normalized to that range)",
    "（同分）": " (tied)",
    "{0} · {1}：{2}%（完整评测 {3}/{4} 项{5} · 这根轴的区间 {6}% – {7}%，位置按区间归一，不代表绝对分数）":
        "{0} · {1}: {2}% (complete runs {3}/{4}{5} · this axis spans {6}% – {7}%; "
        "the position is normalized to that range, not an absolute score)",
    "{0} · {1}：{2}%（完整评测 {3}/{4} 项{5}）":
        "{0} · {1}: {2}% (complete runs {3}/{4}{5})",
    "，另有 {0} 项只有部分评测（不计入均分）":
        ", plus {0} item(s) with partial runs only (excluded from the average)",
    "点一下显示 {0}": "Click to show {0}",
    "点一下隐藏 {0}": "Click to hide {0}",
    "半径 = 该分类在所有参评模型里的相对位置：每根轴各自按自己的区间归一，":
        "Radius = the model's position among participating models in that category; "
        "each axis is normalized to its own range — ",
    "不是 0–100 的绝对分数": "not an absolute 0–100 score",
    "（轴下方的两个数字是这根轴的区间，用来判断它有多宽）。":
        " (the two numbers under each axis are that axis's range, showing how wide it is).",
    "区间窄的轴，同样长的一段半径只对应很小的分数差 —— 不同轴之间的半径长度不能互相比较。":
        "On a narrow axis the same visual distance covers a much smaller score gap "
        "— never compare radius lengths across axes.",
    "虚线 = 该模型在两轴之间没有完整成绩（形状直接跨过，不要把虚线当成分数）。":
        "Dashed edge = the model has no complete run between those two axes "
        "(the shape jumps across; never read a dashed edge as a score).",
    "部分评测不参与：只跑了部分题库的成绩不算完整成绩。":
        "Partial runs do not count: a run that covered only part of the benchmark is not a complete run.",
    "点下面的模型名可以把某个模型从图里摘掉，方便看清剩下几条线。":
        "Click a model below to drop it from the chart and see the remaining lines more clearly.",
    "图中的绝对分数（雷达图只画相对位置，数字以这张表为准）：":
        "Absolute scores behind the chart (the radar only shows relative positions; trust this table for numbers):",
    # ---- 成绩总览：准确率×延迟散点 ----
    "一个点 = 一次（模型 × 基准）的成绩，不做任何聚合：数字就是排行榜分项榜里的那个成绩。":
        "One dot = one model × benchmark result, with no aggregation at all: the numbers are exactly "
        "the ones in the leaderboard's per-benchmark tables.",
    "实心点 = 完整评测（共 {0} 个）；空心点 = 部分评测（没跑完题库，画出来但不参与均值与中位数）。":
        "Filled dots = complete runs ({0} in total); hollow dots = partial runs (benchmark not finished; "
        "drawn but excluded from the mean and median).",
    "◆ = 该模型所有完整评测的均值（悬停看用了几个基准）：越靠左上 = 又快又准。":
        "◆ = that model's mean over its complete runs (hover to see how many benchmarks); "
        "upper-left is both fast and accurate.",
    "虚线 = 所有点的中位数参考线（摘掉模型不会让参考线移动）。":
        "Dashed lines = median reference over all dots (hiding a model does not move it).",
    "按分类": "By category",
    "按基准": "By benchmark",
    "一个点 = 一个模型在一个分类上的成绩：正确率取该分类综合分，延迟取同一批基准的等权平均（都只用完整评测）。":
        "One dot = one model's score in one category: the accuracy is that category's combined "
        "score and the latency is the equal-weighted average over the same benchmarks "
        "(both from complete runs only).",
    "共 {0} 个点（{1} 个模型 × 各自跑过的分类）；延迟是分类内各基准等权，不是按题量加权。":
        "{0} dots in total ({1} models × the categories each one ran); the latency weights "
        "every benchmark in the category equally, not by question count.",
    "◆ = 该模型各分类的等权平均（悬停看用了几个分类）：越靠左上 = 又快又准。":
        "◆ = that model's equal-weighted average across categories (hover to see how many "
        "categories); upper-left is both fast and accurate.",
    "不同模型跑过的分类可能不一样，跨模型比较时先看点数与悬停里的覆盖项数。":
        "Models may have run different categories; before comparing them, check the number of "
        "dots and the coverage shown on hover.",
    "{0} · {1}：{2}%（分类综合分 · 完整评测 {3}/{4} 项 · 各基准等权平均每题 {5} ms）{6}":
        "{0} · {1}: {2}% (category combined score · complete runs {3}/{4} · equal-weighted "
        "{5} ms per question){6}",
    "{0}：{1} 个分类的等权平均 {2}%（平均 {3} ms）":
        "{0}: equal-weighted average over {1} categories {2}% ({3} ms avg)",
    "平均每题延迟（毫秒）→ 越左越快": "Average latency per question (ms) → further left is faster",
    "平均每题延迟（毫秒，对数刻度）→ 越左越快":
        "Average latency per question (ms, log scale) → further left is faster",
    "正确率（%）→ 越上越准": "Accuracy (%) → higher is up",
    "延迟中位数 {0} ms": "Median latency {0} ms",
    "正确率中位数 {0}%": "Median accuracy {0}%",
    "{0} · {1}：{2}%（{3} 题 · 平均 {4} ms）":
        "{0} · {1}: {2}% ({3} questions · {4} ms avg)",
    "{0} · {1}：{2}%（部分评测 {3}/{4} 题 · 平均 {5} ms）—— 空心点，不参与均值与中位数":
        "{0} · {1}: {2}% (partial run {3}/{4} questions · {5} ms avg) — hollow dot, "
        "excluded from the mean and median",
    "{0}：{1} 个完整评测的均值 {2}%（平均 {3} ms）":
        "{0}: mean of {1} complete runs {2}% ({3} ms avg)",
    "准确率与延迟散点图": "Accuracy vs. latency scatter plot",
    "正在读取成绩…": "Loading scores…",
    "分类第一 = 该项目在后端榜单里、该分类第一名的模型；不跨分类求平均，所以不叫「综合分」。":
        "A category win = this model is the category's #1 in the backend leaderboard; no cross-category average is computed, which is why it is not called an overall score.",
    "个分类第一": "category wins",
    "最高单基准正确率": "Best single-benchmark accuracy",
    "<b>{0}</b> 拿下 {1} 个分类第一": "<b>{0}</b> wins {1} categories",
    "{0} 个模型 · {1} 个基准 · {2} 场评测 · 分类共 {3} 个":
        "{0} models · {1} benchmarks · {2} runs · {3} categories",
    "{0} 个第一": "{0} win(s)",
    "所有模型都被图例摘掉了 —— 点下面的模型名可以把它们放回来。":
        "Every model is hidden by the legend — click a model name below to bring it back.",
    "当前显示 {0}/{1} 个模型；点一下某个模型，会列出它由哪些分类等权平均出来。":
        "Showing {0}/{1} models; click one to see which categories make up its average.",
    "当前显示 {0}/{1} 个模型；点一下某个模型，数字会钉在下面。":
        "Showing {0}/{1} models; click one to pin its numbers below.",
    "位置按各轴自己的区间归一（不是绝对分数）；分类分由该分类下已完整评测的项等权平均。":
        "Positions are normalized to each axis's own range (not absolute scores); a category score is the equal-weighted average of that category's complete runs.",
    "均值 = 该模型各分类的等权平均（只算完整评测）；它不是任何一种「成绩」，别当成某个分类的分数。":
        "Mean = the equal-weighted average over this model's categories (complete runs only); it is not a score for any single category.",
    "数字是一次完整评测的原始成绩，没做任何聚合；空心点是部分评测。":
        "The numbers are one complete run as-is, with no aggregation; hollow dots are partial runs.",
    "正确率是分类综合分（只算完整评测），延迟是同一批基准的等权平均。":
        "Accuracy is the category's combined score (complete runs only); the latency is the equal-weighted average over the same benchmarks.",
    "越靠左上 = 又快又准。": "Upper-left is both fast and accurate.",
    "虚线 = 当前这些点的中位数参考线（摘掉模型不会让它移动）。":
        "Dashed lines = median reference of the dots currently shown (hiding a model does not move them).",
    "点一下离光标最近的标记，数字就钉在下面；均值模式还会列出它由哪些分类算出来。":
        "Click the marker nearest the pointer to pin its numbers below; the mean mode also lists which categories it averages.",
    "明细点": "Detail dots",
    "只看均值": "Means only",
    "一个点 = 一个模型：各分类的等权平均（正确率 × 平均每题延迟，只用完整评测）。":
        "One dot = one model: the equal-weighted average over its categories (accuracy × average latency per question, complete runs only).",
    "共 {0} 个模型；点一下某个模型，会列出它由哪些分类等权平均出来。":
        "{0} models; click one to see which categories make up its average.",
    "一个点 = 一个模型：它所有完整评测的均值（正确率 × 平均每题延迟）。":
        "One dot = one model: the mean over all its complete runs (accuracy × average latency per question).",
    "共 {0} 个模型；点一下某个模型，数字会钉在下面。": "{0} models; click one to pin its numbers below.",
    "共 {0} 个点，其中 {1} 个是完整评测；空心点 = 部分评测（画出来但不参与均值与中位数）。":
        "{0} dots, {1} of them complete runs; hollow dots = partial runs (drawn but excluded from the mean and median).",
    "各分类等权平均": "Equal-weighted average across categories",
    "均值正确率": "Mean accuracy",
    "均值延迟": "Mean latency",
    "参与分类数": "Categories counted",
    "均值": "Means",
    "该模型各分类的等权平均（只算完整评测）—— 均值不是任何一种「成绩」，别拿它当某个分类的分数。":
        "Equal-weighted average over this model's categories (complete runs only) — the mean is not a score for any single category.",
    "由这些分类等权平均：{0}": "Equal-weighted average of these categories: {0}",
    "显示/隐藏各模型的均值菱形（◆）": "Show/hide each model's mean diamond (◆)",
    "已选点": "Pinned points",
    "最多 {0} 个；再点一次那个点可以取消": "Up to {0}; click the same dot again to unpin",
    "全部取消": "Clear all",
    "取消这个点": "Unpin this dot",
    "家族算一项": "family counts as one item",
    "分类分": "Category score",
    "这根轴区间": "Axis range",
    "平均每题": "Per question",
    "位置是按这根轴的区间归一的，不代表绝对分数。":
        "The position is normalized to this axis's range, not an absolute score.",
    "这个分类分由这些等权平均：{0}": "This category score is the equal-weighted average of: {0}",
    "部分评测：没跑完题库，画出来但不参与均值与中位数。":
        "Partial run: the benchmark was not finished; it is drawn but excluded from the mean and median.",
    "一次完整评测的原始数字，没做任何聚合。": "Raw numbers from one complete run, with no aggregation.",
    "分类综合分；延迟是与它同一批基准的等权平均（都只用完整评测）。":
        "Category combined score; the latency is the equal-weighted average over the same benchmarks (complete runs only).",
    "由这些等权平均：{0}": "Equal-weighted average of: {0}",
    "另有 {0} 项只有部分评测（不计入均分）。": "{0} more item(s) have partial runs only (excluded from the average).",
    "点一下图上的顶点，数字会钉在下面。": "Click a vertex to pin its numbers below.",
    "点一下点，数字会钉在下面。": "Click a dot to pin its numbers below.",
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

    查表顺序：``EN`` → ``STORED_EN``。为什么也查后者：有一批文案（下载/总结状态）
    既会落进共享状态被 ``t_stored`` 翻，也会直接出现在响应体里 —— 同一句话两处维护
    迟早只改一边。两张表都是我们自己写的译文，互为兜底是安全的；
    **反过来不行**：``t_stored`` 还会收到模型原话，绝不能去查 UI 文案表。
    """
    s = (EN.get(zh) or STORED_EN.get(zh) or zh) if get_lang() == "en" else zh
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
    # 「完成」既是下载成功的状态、也是总结生成成功的状态；以前两张表都没有它，
    # 于是英文界面里下载完显示的是「完成」——实测探针抓到的既有漏译。
    "完成": "Done",
    "模型不存在": "model not found",
    # 下载失败的原因（会作为下载状态显示给用户）
    "HarmBench 下载结果为空": "HarmBench returned no data when downloading",
    "HumanEval 下载结果为空": "HumanEval returned no data when downloading",
    "JBB 有害行为下载结果为空": "JBB harmful behaviors returned no data when downloading",
    "JBB 良性请求下载结果为空": "JBB benign requests returned no data when downloading",
    "LiveCodeBench 下载结果为空": "LiveCodeBench returned no data when downloading",
    # 判分链路的兜底（正常路径已被 /api/evaluations 拦住，这里防的是绕过接口的调用）
    "这个基准需要裁判模型，但这次评测没有配置裁判（judge_model_id 为空）":
        "this benchmark needs a judge model, but this run has no judge configured"
        " (judge_model_id is empty)",
    # BFCL 两种判分口径的诊断词（作为逐题 error 落库，明细里显示）。
    # 「AST 匹配通过」「覆盖期望调用」是**通过**时的诊断（不是错误），但同样会显示在明细里。
    "AST 匹配通过": "AST match passed",
    "覆盖期望调用": "all expected calls covered",
    "该轮无需调用": "no call needed for this turn",
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
    # 下载 / 判分 / 解析失败的原因（这些文案由异常带出来、落进状态或逐题错误，读时翻）
    (r"^PyPI 上没有匹配当前 Python（(.+)）的 pyarrow wheel$",
     "no pyarrow wheel on PyPI matches this Python ({0})"),
    (r"(?s)^multi_turn 工具定义缺失（involved_classes=(.+)），请先下载 func_doc$",
     "multi-turn tool definitions are missing (involved_classes={0}); download func_doc first"),
    (r"^未知的判分口径: (.+)（可选 (.+)）$", "unknown judging rubric: {0} (choose from {1})"),
    (r"^未知的判分口径: (.+)$", "unknown judging rubric: {0}"),
    (r"^完成，但有 (\d+) 个数字在输入数据里回查不到，建议核对$",
     "Done, but {0} number(s) could not be traced back to the input data — please double-check"),
    # download_one 对未知 id 的返回值（正常路径进不来：调用方只会传 DOWNLOADERS 里的 id，
    # 但它同样是"会落进下载状态"的文案，所以按同一套规则翻掉）
    (r"^未知或不可下载的题库: (.+)$", "unknown or non-downloadable dataset: {0}"),
    # BFCL 判分诊断（落进逐题 error，明细里显示）
    (r"^应拒绝但调用了 (\d+) 个函数$", "should have refused but called {0} function(s)"),
    (r"^调用数不符：期望 (\d+) 个 \((.*)\)，实际 (\d+) 个 \((.*)\)$",
     "call count mismatch: expected {0} ({1}), got {2} ({3})"),
    (r"^函数名不符：期望 (.*)，实际 (.*)$", "function name mismatch: expected {0}, got {1}"),
    (r"^缺少参数 (.*)$", "missing argument {0}"),
    (r"^参数 (.*) 不符：期望 (.*)，实际 (.*)$", "argument {0} mismatch: expected {1}, got {2}"),
    (r"^缺少期望调用 (.*)（实际: (.*)）$", "missing expected call(s): {0} (actual: {1})"),
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
