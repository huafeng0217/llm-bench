"""基准的注册条目类型。

一条 `Benchmark` = 一个基准的**全部**信息：元数据 + 怎么下载。
以前它们分散在四处（benchmarks.py 的 META、download.py 的 AVAILABLE / DOWNLOADERS、
以及 scripts/download_*.py 里的下载函数），加一个基准要同时改这些地方。

`Family` / `Group` 是「同一份数据源下的多个子集」用的：BFCL v4 有 22 个子集，
当 22 个独立基准平铺会把整个 agent 分类占满；而官方总分也不是子集平均分，
而是「组内平均 → 组间按官方权重加权」。声明成 family 之后：
  - 界面上折叠成一张卡 / 一块榜，子集用切换器选；
  - 能按官方口径算加权总分（见 ``scoring.family_composite``），并显示权重覆盖率。
"""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Group:
    """家族内的一个官方分组（如 BFCL v4 的 Non-Live / Live / Multi-Turn …）。"""
    id: str
    name: str
    weight: float      # 官方权重：同一家族内各组合计为 1.0
    order: int         # 展示顺序：按官方顺序显式声明，不靠字母序
    name_en: str = ""  # 英文组名（界面语言 = en 时用；空则回落 name）


@dataclass(frozen=True)
class Family:
    """一个家族：同一份数据源、官方按组给加权总分的一组子集。"""
    id: str                                  # 同时也是展示名
    groups: tuple = ()                       # (Group, ...)
    note: str = ""                           # 权重口径的出处，免得以后自己都不记得
    source: str = ""
    note_en: str = ""                        # 英文口径说明（空则回落 note）


@dataclass(frozen=True)
class Benchmark:
    id: str
    order: int               # 展示位次：只用于同分类内排序，不靠模块名字母序
    name: str
    category: str            # 取值必须出现在 registry.CATEGORIES 的 name 里
    lang: str
    status: str
    description: str
    summary: str = ""                 # 卡片简介（≤ 约 40 字，两行内）：写「差在哪 / 低分说明什么」，完整 description 选中后再展开
    source: str = ""
    requires_docker: bool = False     # 需要沙箱执行模型代码（如 HumanEval）
    requires_judge: bool = False      # 需要裁判模型判分（安全类基准，如 HarmBench）
    adverse_label: str = ""           # 「拿到了判定但结果不利」这档在明细里叫什么；空 = 答错
                                      # （安全类里它不是「答错」而是「越狱成功 / 过度拒绝」）
    # ---- 英文文案（界面语言 = en 时用；留空则回落中文）----
    # 一个基准一个文件：中英并排写在同一条目里，改文案时不会漏掉另一半。
    name_en: str = ""
    summary_en: str = ""
    description_en: str = ""
    label_en: str = ""
    status_en: str = ""
    label: str = ""                   # 下载按钮/列表里的说明文字
    download: Optional[Callable[[], None]] = None   # None = 不支持自动下载
    family: str = ""                  # 所属家族 id；空 = 独立基准
    group: str = ""                   # 家族内的分组 id；空 = 不分组
