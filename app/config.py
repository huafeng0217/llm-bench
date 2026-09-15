"""全局常量：路径、选项字母、默认参数、沙箱并发。

放在一处是为了让依赖方向干净 —— 其它模块都只向下依赖本模块，不会互相引。"""

import asyncio
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


RESULTS_DIR = DATA_DIR / "results"


BFCL_DIR = DATA_DIR / "bfcl_v4"


CHOICES = [chr(65 + i) for i in range(10)]  # A-J，兼容 4 选 1（MMLU/C-Eval）到 10 选 1（MMLU-Pro）


DEFAULTS = {"max_tokens": 2048, "timeout_s": 90, "concurrency": 8}


MAX_RETRIES = 3


SANDBOX_CONCURRENCY = 4


SANDBOX_SEM = asyncio.Semaphore(SANDBOX_CONCURRENCY)
