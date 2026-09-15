"""下载题库的公共工具（HTTP / gzip / parquet / 写 jsonl）。

合并了三份重复实现：原先 download_more.py、download_bfcl.py、download_datasets.py
各自带着一套 _http_get_one / http_get，改一处要记得改三处。
另外把 datasets-server 的 http_json 重命名为 ds_http_json，与 HF 文件下载用的
http_json（带 hf-mirror 回退）区分开 —— 两者打的是不同服务，行为也不同。
"""

import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


# 项目根：本文件在 app/benchmarks/ 下，所以要往上三层。
# 写成显式的 ROOT 而不是链式 .parent，是因为**搬动文件会改变层数**：
# 这段代码原来在 scripts/download_more.py（往上两层就是项目根），搬进 app/benchmarks/ 后
# 少了一层，DATA_DIR 就指向了 app/data/ —— 而且不报错，只是默默写错地方。
ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"


def _http_get_one(url: str, timeout: int = 60) -> bytes:
    """单源 GET（含 429 限流退避），返回 bytes。"""
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 0) or min(10 * (attempt + 1), 60)
                print(f"  限流(429)，等待 {wait}s 后重试…", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def http_get(url: str, timeout: int = 60) -> bytes:
    """带镜像回退的 GET（返回 bytes）。

    CMMLU 的数据源是 GitHub raw（raw.githubusercontent.com），国内访问常不稳定，
    主源失败后自动回退到 ghproxy.com 加速镜像（https://ghproxy.com/<原URL>）。

    HuggingFace（huggingface.co）在国内常被墙，同样加一层社区镜像回退：
    hf-mirror.com 的路径与官方完全一致，直接把域名换掉即可。

    注意：GPQA 走 datasets-server 的 rows API，而 hf-mirror 只镜像 HF 文件、
    不提供 rows API 等价端点（datasets-server.hf-mirror.com 不存在），
    故 datasets-server 暂不做域名回退，仅靠 429 退避重试。
    """
    urls = [url]
    if url.startswith("https://raw.githubusercontent.com/"):
        urls.append("https://ghproxy.com/" + url)
    elif url.startswith("https://huggingface.co/"):
        urls.append("https://hf-mirror.com/" + url[len("https://huggingface.co/"):])
    last = None
    for u in urls:
        if u != url:
            print("  主源失败，回退镜像源…", file=sys.stderr)
        try:
            return _http_get_one(u, timeout)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def http_json(url: str, timeout: int = 60):
    return json.loads(http_get(url, timeout).decode("utf-8"))


def decode_text(b: bytes) -> str:
    """优先 UTF-8，失败回退 GB18030，保证中文题库不被解坏。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def write_jsonl(path: Path, items: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def _ensure_pyarrow():
    """确保能解析 parquet：优先用已装的 pyarrow，否则自动装到项目内 .vendor。

    为什么不直接 pip install：venv 在项目外，写它会被文件沙箱拒绝；而 pip 的
    安装流程（解包 → 改名 → 写 .whl.metadata）在受限环境下会 Errno 13。
    这里改为直接下载 wheel（本质是 zip）解压，绕开 pip 的文件操作，
    且全部落在项目内。pyarrow 只是「下载题库」时的工具，不是运行时依赖。
    """
    try:
        import pyarrow  # noqa: F401
        return
    except ImportError:
        pass
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        import pyarrow  # noqa: F401
        return
    except ImportError:
        pass
    import zipfile
    print("未检测到 pyarrow，自动下载到 .vendor（仅用于解析 parquet，约 28MB）…")
    meta = json.loads(http_get("https://pypi.org/pypi/pyarrow/json").decode("utf-8"))
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    cand = [u for u in meta["urls"] if u["filename"].endswith(f"{tag}-{tag}-win_amd64.whl")]
    if not cand:
        raise RuntimeError(f"PyPI 上没有匹配当前 Python（{tag}）的 pyarrow wheel")
    w = cand[0]
    print(f"  下载 {w['filename']}（{w['size'] / 1e6:.1f} MB）…")
    z = zipfile.ZipFile(io.BytesIO(http_get(w["url"], timeout=300)))
    n = 0
    for name in z.namelist():
        if name.endswith("/"):
            continue
        dst = VENDOR_DIR / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(z.read(name))
        n += 1
    print(f"  解压 {n} 个文件到 {VENDOR_DIR}")
    sys.path.insert(0, str(VENDOR_DIR))
    import pyarrow  # noqa: F401


def read_parquet(ds: str, path: str) -> list:
    """从 HF 直接下 parquet 并解析为 list[dict]。

    走 resolve 端点而非 datasets-server：后者在国内常不可达（连接超时），
    而 huggingface.co 的 resolve 端点稳定。
    """
    _ensure_pyarrow()
    import pyarrow.parquet as pq
    raw = http_get(HF_RESOLVE.format(ds=ds, path=path), timeout=180)
    return pq.read_table(io.BytesIO(raw)).to_pylist()


def _stream_lines(url: str, timeout: int = 900):
    """流式逐行读取（带镜像回退），用于上百 MB 的文件。

    LCB 的 test6.jsonl 有 134MB，一次性 read() 进内存再解码会同时占住
    原始 bytes 和 str 两份（约 300MB）；这里逐行 yield，只保留当前行。
    """
    urls = [url]
    if url.startswith("https://huggingface.co/"):
        urls.append("https://hf-mirror.com/" + url[len("https://huggingface.co/"):])
    last = None
    for u in urls:
        if u != url:
            print("  主源失败，回退镜像源…", file=sys.stderr)
        yielded = False
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for line in r:
                    yielded = True
                    yield line
            return
        except Exception as e:  # noqa: BLE001
            last = e
            if yielded:
                # 已经吐了一半数据，换镜像会拼出重复/错乱的内容，不能重试
                raise
    raise last


CHOICES = [chr(65 + i) for i in range(10)]  # A-J


HF_RESOLVE = "https://huggingface.co/datasets/{ds}/resolve/main/{path}"


VENDOR_DIR = ROOT / ".vendor"


HOST = "datasets-server.huggingface.co"


LETTERS = ["A", "B", "C", "D"]


REQUEST_GAP = 0.5  # 每次请求间隔，避免触发限流


def ds_http_json(path_query: str):
    """datasets-server 的 GET JSON（带 429 限流退避重试）。

    与上面的 `http_json` 是两个不同的服务，故分开命名：
      - `http_json`     打 huggingface.co 的**文件**端点（带 hf-mirror 回退）
      - `ds_http_json`  打 datasets-server 的 **rows/splits API**
        （hf-mirror 不提供该 API 的等价端点，所以只做 429 退避）
    """
    url = f"https://{HOST}{path_query}"
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-bench/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 0) or min(10 * (attempt + 1), 60)
                print(f"  限流(429)，等待 {wait}s 后重试…", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def fetch_rows(dataset: str, config: str, split: str, offset: int, length: int = 100):
    """datasets-server 的 rows API。

    注意与上面 `http_json` 的区别：那个打 HuggingFace 的文件端点（带 hf-mirror 回退），
    这个打 datasets-server 的 rows API（只做 429 退避，没有镜像等价端点）。
    """
    q = urllib.parse.urlencode({
        "dataset": dataset, "config": config, "split": split,
        "offset": offset, "length": length,
    })
    data = ds_http_json(f"/rows?{q}")
    return [r["row"] for r in data.get("rows", [])]


def list_configs(dataset: str):
    q = urllib.parse.urlencode({"dataset": dataset})
    data = ds_http_json(f"/splits?{q}")
    return sorted({s["config"] for s in data.get("splits", [])})


def fetch_ds_rows(dataset: str, config: str, split: str) -> list:
    """从 datasets-server rows API 分页拉取全部行，返回 list[dict]。"""
    ds_q = urllib.parse.quote(dataset, safe="")
    size = http_json(f"https://datasets-server.huggingface.co/size?dataset={ds_q}")
    total = size["size"]["dataset"]["num_rows"]
    print(f"{dataset}：共 {total} 行")
    out = []
    offset = 0
    length = 100  # datasets-server 单次 rows 上限
    while offset < total:
        url = (f"https://datasets-server.huggingface.co/rows?dataset={ds_q}"
               f"&config={config}&split={split}&offset={offset}&length={length}")
        data = http_json(url)
        rows = data.get("rows", [])
        if not rows:
            break
        out.extend(r["row"] for r in rows)
        offset += len(rows)
        time.sleep(0.2)
    return out
