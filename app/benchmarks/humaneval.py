"""HumanEval（函数补全 + 沙箱跑官方单元测试）。"""

from .types import Benchmark
import json
import sys
from ._util import DATA_DIR, http_get, read_parquet, write_jsonl



HUMANEVAL_GH = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"


HUMANEVAL_HF = ("openai/openai_humaneval", "openai_humaneval/test-00000-of-00001.parquet")


def download_humaneval():
    """HumanEval（OpenAI，164 题）：第一个**真正执行模型代码**的基准。

    字段说明（存进 jsonl 的形态）：
      question     —— 函数签名 + docstring（模型要补全的就是它）
      test         —— 官方单元测试，形如 `def check(candidate): assert ...`
      entry_point  —— 函数名，拼程序时用来调 check(entry_point)
      answer       —— 官方参考解法，仅作展示/自检，不参与判分

    数据源优先用 GitHub 上的原始 jsonl.gz：gzip 是标准库，
    不用像 AIME 那样去折腾 pyarrow wheel。
    注意 HF 仓库（openai/openai_humaneval）里**只有自动转的 parquet**，
    没有原始 jsonl.gz，所以那里只能当回退源（需 pyarrow）。
    """
    import gzip
    try:
        raw = http_get(HUMANEVAL_GH, timeout=180)
        rows = [json.loads(l) for l in gzip.decompress(raw).decode("utf-8").splitlines() if l.strip()]
    except Exception as e:  # noqa: BLE001
        print(f"  GitHub 源失败（{e}），回退 HF parquet…", file=sys.stderr)
        rows = read_parquet(*HUMANEVAL_HF)
    items = [{
        "question": d["prompt"],
        "test": d["test"],
        "entry_point": d["entry_point"],
        "answer": d.get("canonical_solution", ""),
        "task_id": d.get("task_id", ""),
    } for d in rows]
    if not items:
        raise RuntimeError("HumanEval 下载结果为空")
    write_jsonl(DATA_DIR / "humaneval.jsonl", items)
    print(f"完成：humaneval.jsonl（共 {len(items)} 题）")


ENTRIES = [
    Benchmark(order=23, id='humaneval',
        name='HumanEval',
        category='代码工程',
        lang='Python',
        status='仍有区分度',
        requires_docker=True,
        description='OpenAI 的 164 道 Python 函数补全题：给函数签名和 docstring，模型写出完整实现，然后**在沙箱里真实运行官方单元测试**判分（这是本系统第一个真正执行模型代码的基准）。题目短、依赖少，适合快速看代码能力；前沿模型已到 90%+，区分度集中在中小模型。注意：个别题（如 HumanEval/47）docstring 示例与官方测试互相矛盾，照着 docstring 写反而会失败。',
        source='https://arxiv.org/abs/2107.03374',
        label='HumanEval（代码补全，需 Docker 沙箱，164 题）',
        download=download_humaneval),
]
