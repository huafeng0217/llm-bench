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

from .types import Benchmark

# 统一分类体系（前端按此分组展示；顺序即展示顺序）
CATEGORIES = [
    {"id": "knowledge", "name": "通用知识", "color": "#4a7de0"},
    {"id": "chinese", "name": "中文能力", "color": "#e05a4a"},
    {"id": "science", "name": "科学推理", "color": "#7a5ae0"},
    {"id": "math", "name": "数学推理", "color": "#2fa36b"},
    {"id": "agent", "name": "Agent / 工具调用", "color": "#e0952f"},
    {"id": "code", "name": "代码工程", "color": "#2f9be0"},
    {"id": "custom", "name": "自定义", "color": "#8a94a6"},
]

FALLBACK = {
    "category": "自定义",
    "lang": "-",
    "status": "自定义题库",
    "description": "用户自定义或下载的题库（jsonl 格式：question/A/B/C/D/answer 字段）。",
    "source": "",
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


ENTRIES: list = _discover()

# 派生视图：保持既有调用方（main.py / 前端）用的形态不变
META = {
    e.id: {
        "name": e.name, "category": e.category, "lang": e.lang, "status": e.status,
        "description": e.description, "source": e.source,
        **({"requires_docker": True} if e.requires_docker else {}),
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
    # 附带所属分类 id 与颜色，前端按此分组展示
    for c in CATEGORIES:
        if c["name"] == meta["category"]:
            meta["category_id"] = c["id"]
            meta["category_color"] = c["color"]
            break
    else:
        meta["category_id"] = "custom"
        meta["category_color"] = "#8a94a6"
    return meta


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
