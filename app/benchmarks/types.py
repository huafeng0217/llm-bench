"""基准的注册条目类型。

一条 `Benchmark` = 一个基准的**全部**信息：元数据 + 怎么下载。
以前它们分散在四处（benchmarks.py 的 META、download.py 的 AVAILABLE / DOWNLOADERS、
以及 scripts/download_*.py 里的下载函数），加一个基准要同时改这些地方。
"""
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Benchmark:
    id: str
    order: int               # 展示位次：只用于同分类内排序，不靠模块名字母序
    name: str
    category: str            # 取值必须出现在 registry.CATEGORIES 的 name 里
    lang: str
    status: str
    description: str
    source: str = ""
    requires_docker: bool = False     # 需要沙箱执行模型代码（如 HumanEval）
    label: str = ""                   # 下载按钮/列表里的说明文字
    download: Optional[Callable[[], None]] = None   # None = 不支持自动下载
