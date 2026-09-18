"""基准注册表：一个基准一条记录，元数据与下载器写在同一个文件里。

为什么这样组织
--------------
以前加一个基准要同时改**四处**：`app/benchmarks.py` 的 `META`、
`scripts/download.py` 的 `AVAILABLE` 和 `DOWNLOADERS`、以及三个 `download_*.py` 里的下载函数。
实测某个基准 id 散落在 10 个文件里。

现在每个基准（或同一数据源的一组基准）在自己的模块里声明 `ENTRIES`，
本模块自动收集，`META` / `DOWNLOADERS` / `AVAILABLE` 全部由它派生。

**加一个基准 = 加一个文件**（或往同源模块的 `ENTRIES` 里加一条），不用改别处。

模块一览
--------
    types.py        Benchmark 条目类型
    _util.py        下载公共工具（HTTP / gzip / parquet / 写 jsonl）
    mmlu.py         MMLU + 演示样例
    ceval.py        C-Eval + 演示样例
    cmmlu.py gpqa.py mmlu_pro.py gsm8k.py math500.py
    aime.py truthfulqa.py humaneval.py livecodebench.py
    bfcl.py         BFCL v4 的 11 个子集（同源共用下载逻辑，故合在一个文件）
"""
import importlib
import pkgutil

from .types import Benchmark, Family, Group  # noqa: F401  (Family/Group 供各基准模块声明)

from .. import i18n

# 统一分类体系（前端按此分组展示；顺序即展示顺序）
CATEGORIES = [
    {"id": "knowledge", "name": "通用知识", "name_en": "Knowledge", "color": "#4a7de0"},
    {"id": "chinese", "name": "中文能力", "name_en": "Chinese", "color": "#e05a4a"},
    {"id": "science", "name": "科学推理", "name_en": "Science", "color": "#7a5ae0"},
    {"id": "commonsense", "name": "常识推理", "name_en": "Commonsense", "color": "#0f9b9b"},
    {"id": "math", "name": "数学推理", "name_en": "Math", "color": "#2fa36b"},
    {"id": "agent", "name": "Agent / 工具调用", "name_en": "Agent / tool use", "color": "#e0952f"},
    {"id": "code", "name": "代码工程", "name_en": "Code", "color": "#2f9be0"},
    {"id": "safety", "name": "安全 / 对齐", "name_en": "Safety / alignment", "color": "#c44b8a"},
    {"id": "custom", "name": "自定义", "name_en": "Custom", "color": "#8a94a6"},
]

FALLBACK = {
    "category": "自定义",
    "lang": "-",
    "status": "自定义题库",
    # 自定义题库（data/ 里存在但 META 未收录）没有官方口径可写，卡片上要**如实说明这一点**，
    # 而不是显示一段和所有自定义题库都一样的格式说明 —— 那对区分它们毫无帮助。
    # 摘要写「这是什么」，完整说明留给「怎么写一个」（选中后展开）。
    "summary": "自己放进 data/ 的题库：没有官方口径说明，卡片只按文件名与题量显示",
    "description": "用户自定义或下载的题库（jsonl 格式：question/A/B/C/D/answer 字段）。",
    "source": "",
}

# 自定义题库的英文兜底。**不能塞进 FALLBACK**：FALLBACK 是所有基准的底座，
# 一旦里面有 status_en，那些"还没写英文"的基准就会被它顶掉（实测 humaneval 变成 custom dataset）。
# 枚举式文案的英文（lang / 「有判定但结果不利」那一档的措辞）
LANG_EN = {"英文": "English", "中文": "Chinese", "多语": "Multilingual", "-": "-"}
ADVERSE_EN = {"越狱成功": "jailbroken", "过度拒绝": "over-refusal"}

FALLBACK_EN = {
    "category": "Custom",
    "status": "custom dataset",
    "summary": "A dataset you put in data/ yourself: no official rubric, so the card shows only "
               "the file name and item count",
    "description": "A user-supplied or downloaded dataset (jsonl with question/A/B/C/D/answer "
                   "fields).",
}


def _discover() -> list:
    """收集各基准模块的 ENTRIES。

    只按 `order` 排序 —— 分类分组是**前端**的事（它按 category 过滤后原样渲染），
    这里若再按分类排一遍，只会让 META 的键顺序与拆分前不一致，没有任何收益。
    `order` 由各条目自己声明，不靠模块名的字母序：否则改个文件名就会让界面顺序跳变。
    """
    found: dict = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "types":
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        for e in getattr(mod, "ENTRIES", []) or []:
            if e.id in found:
                raise RuntimeError(f"基准 id 重复: {e.id}（{info.name} 与其它模块）")
            found[e.id] = e
    return sorted(found.values(), key=lambda e: (e.order, e.id))


def _discover_families() -> dict:
    """收集各模块声明的 FAMILY_DEFS（家族 = 同一数据源的多个子集 + 官方分组权重）。"""
    found: dict = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "types":
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        for f in getattr(mod, "FAMILY_DEFS", []) or []:
            if f.id in found:
                raise RuntimeError(f"家族 id 重复: {f.id}（{info.name} 与其它模块）")
            found[f.id] = f
    return found


ENTRIES: list = _discover()
FAMILIES: dict = _discover_families()

# 家族的合法性检查放在这里一次性做完：条目写错 group、家族忘了声明、权重没配平，
# 都直接在 import 时炸掉 —— 而不是等界面上少一块、或者加权总分悄悄算错才发现。
for _e in ENTRIES:
    if _e.family:
        _f = FAMILIES.get(_e.family)
        if _f is None:
            raise RuntimeError(f"{_e.id} 声明了未定义的家族: {_e.family}")
        if _e.group not in {g.id for g in _f.groups}:
            raise RuntimeError(f"{_e.id} 的 group={_e.group!r} 不在家族 {_e.family} 的分组里")
    elif _e.group:
        raise RuntimeError(f"{_e.id} 没有 family 却声明了 group={_e.group!r}")
for _f in FAMILIES.values():
    if abs(sum(g.weight for g in _f.groups) - 1.0) > 1e-9:
        raise RuntimeError(f"家族 {_f.id} 的官方权重之和不是 1.0")
    if len({g.order for g in _f.groups}) != len(_f.groups):
        raise RuntimeError(f"家族 {_f.id} 的分组 order 有重复")

# 家族 -> [分组 dict]（按官方顺序），每个分组带自己的子集 id。
# 这是**唯一**一处算「哪个子集属于哪个组」的地方：UI 折叠与加权总分都用它。
FAMILY_GROUPS: dict = {}
for _f in FAMILIES.values():
    _groups = []
    for _g in sorted(_f.groups, key=lambda g: g.order):
        _subs = [e.id for e in ENTRIES if e.family == _f.id and e.group == _g.id]
        _groups.append({"id": _g.id, "name": _g.name, "name_en": _g.name_en,
                        "weight": _g.weight, "order": _g.order, "subsets": _subs})
    FAMILY_GROUPS[_f.id] = _groups

# 派生视图：保持既有调用方（main.py / 前端）用的形态不变
META = {
    e.id: {
        "name": e.name, "category": e.category, "lang": e.lang, "status": e.status,
        "summary": e.summary, "description": e.description, "source": e.source,
        "label": e.label,
        **({"requires_docker": True} if e.requires_docker else {}),
        **({"requires_judge": True} if e.requires_judge else {}),
        # 明细里「有判定但结果不利」那一档的措辞（安全类不是「答错」，见 types.Benchmark）
        **({"adverse_label": e.adverse_label} if e.adverse_label else {}),
        # 英文文案（只有写了才带上；META 本身仍是中文基准版，语言切换在 get_meta 里做）
        **({f"{k}_en": v for k, v in (("name", e.name_en), ("summary", e.summary_en),
                                      ("description", e.description_en), ("label", e.label_en),
                                      ("status", e.status_en)) if v}),
        **({"family": e.family, "group": e.group} if e.family else {}),
    }
    for e in ENTRIES
}
DOWNLOADERS = {e.id: e.download for e in ENTRIES if e.download}
AVAILABLE = {e.id: e.label for e in ENTRIES if e.download}
BY_ID = {e.id: e for e in ENTRIES}


def get_meta(benchmark_id: str) -> dict:
    meta = dict(FALLBACK)
    meta.update(META.get(benchmark_id, {}))
    meta.setdefault("name", benchmark_id)
    meta["id"] = benchmark_id
    # 自定义题库（data/ 里有、META 未收录）在英文模式下换成专门的英文兜底
    if benchmark_id not in META and i18n.get_lang() == "en":
        meta.update(FALLBACK_EN)
    # 附带所属分类 id 与颜色，前端按此分组展示（**先用中文名定位分类**，
    # 分类 id/颜色与语言无关；下面的英文化只改展示文案）
    cat = None
    for c in CATEGORIES:
        if c["name"] == meta["category"]:
            cat = c
            meta["category_id"] = c["id"]
            meta["category_color"] = c["color"]
            break
    else:
        meta["category_id"] = "custom"
        meta["category_color"] = "#8a94a6"
    # 枚举式文案：取值有限，用映射表比逐条加字段省事，也不会漏
    if i18n.get_lang() == "en":
        meta["lang"] = LANG_EN.get(meta.get("lang"), meta.get("lang"))
        if meta.get("adverse_label"):
            meta["adverse_label"] = ADVERSE_EN.get(meta["adverse_label"], meta["adverse_label"])
    # 界面语言 = 英文时换上英文文案；**哪一条没写就回落中文**（宁可显示中文，也别空白）
    if i18n.get_lang() == "en":
        for zh_key, en_key in (("name", "name_en"), ("summary", "summary_en"),
                               ("description", "description_en"), ("status", "status_en"),
                               ("label", "label_en")):
            if meta.get(en_key):
                meta[zh_key] = meta[en_key]
        if cat and cat.get("name_en"):
            meta["category"] = cat["name_en"]
    # 家族成员的标签信息一并给前端：卡片上要标出组别与官方权重
    fam = FAMILIES.get(meta.get("family") or "")
    if fam:
        # 家族口径说明也是模块级中文，读取处按语言取
        meta["family_note"] = (fam.note_en or fam.note) if i18n.get_lang() == "en" else fam.note
        meta["family_source"] = fam.source
        for g in FAMILY_GROUPS.get(fam.id, []):
            if g["id"] == meta.get("group"):
                meta["group_name"] = (g.get("name_en") or g["name"]) if i18n.get_lang() == "en" \
                    else g["name"]
                meta["group_weight"] = g["weight"]
                meta["group_subsets"] = len(g["subsets"])
                break
    return meta


def group_name(family_id: str, group_id: str) -> str:
    """官方分组名按当前语言取（`FAMILY_GROUPS` 是模块级中文版）。

    为什么要有这个函数：分组名在**三个地方**被读出来 —— 家族级的分组定义、
    每个模型的 comp["groups"] 行（`app/scoring` 是语言无关的，名字到组装响应时才换）、
    成绩总览的子集归属表 —— 只要有一处直接读 `g["name"]`，英文模式下就会冒出中文。
    实测漏过一处：`Agentic（Web Search + Memory）` 里**没有汉字**，只有全角括号，
    用"找汉字"扫是扫不出来的（这条就是这么漏出去的），所以这里给一个统一入口。
    """
    for g in FAMILY_GROUPS.get(family_id, []):
        if g["id"] == group_id:
            return (g.get("name_en") or g["name"]) if i18n.get_lang() == "en" else g["name"]
    return ""


def download_one(name: str):
    """下载单个题库，返回 (ok, message)。"""
    fn = DOWNLOADERS.get(name)
    if not fn:
        return False, f"未知或不可下载的题库: {name}"
    try:
        fn()
        return True, "完成"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def download_all():
    """下载全部可下载题库，返回 {name: (ok, message)}。"""
    return {name: download_one(name) for name in DOWNLOADERS}
