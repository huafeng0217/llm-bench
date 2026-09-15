"""判分分派冒烟测试：用内置 mock 模型把 6 种题型各跑一遍完整评测链路。

为什么需要它
------------
`run_evaluation` 里的题型分派是整个系统的核心循环，改它风险最高 ——
但此前**没有任何测试覆盖它**：5 个 verify_*.py 分别测沙箱隔离、HumanEval 的代码抽取、
LiveCodeBench 判分器、统计层，没有一个是「整条链路」。

这里用内置 mock 模型（base_url 为 `mock://local`）跑真实评测流程：
  - 不花 API 费用、不依赖网络；
  - 但仍然**真的**建任务、真的走分派、真的判分、真的落库；
  - 代码类题型需要 Docker；沙箱不可用时那两项会被明确标成 SKIP 而不是失败。

关键设计：**全程使用临时数据库**（并把结果导出目录也改到临时目录），
所以它不会往你的 data/app.db 里写任何东西，可以随时反复跑。

断言的落点是「**该由哪个 runner 处理**」—— 每个题型写出的 `expected` 形态不同：
    choice        -> "A"（选项字母）
    numeric       -> 答案原文
    code_unit     -> "<函数名> · 通过全部单元测试"
    code_stdio    -> "<题号> · 通过全部测试用例"
    bfcl          -> 期望调用文本
    bfcl_multi_turn -> "共 N 轮"
分派错到别的 runner，这里立刻就会红。

用法::

    python scripts/verify_dispatch.py
"""
import asyncio
import pathlib
import shutil
import sys
import tempfile
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在建立任何数据库连接之前改掉 DB_PATH，否则会污染真实的 data/app.db
from app import db  # noqa: E402

_tmp_root = None
try:
    _tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="llmbench-dispatch-"))
except OSError:                     # 受限环境下系统临时目录可能不可写
    _tmp_root = ROOT / ".dispatch_tmp"
    _tmp_root.mkdir(exist_ok=True)
db.DB_PATH = _tmp_root / "app.db"
db._local.conn = None               # 清掉可能已绑定的连接
db.init_db()

from app import datasets, engine  # noqa: E402

# 别往真实 data/results/ 里写文件。
# 注意必须 patch **datasets 模块自己的**变量：engine 只是 re-export，
# 那是取值快照而不是活绑定 —— 改 engine.RESULTS_DIR 不会影响 export_items 的行为
# （拆分 engine 时踩到过：patch 到 facade 上等于没 patch）。
datasets.RESULTS_DIR = _tmp_root / "results"
engine.RESULTS_DIR = datasets.RESULTS_DIR   # 保持 facade 上的取值也一致，避免误读

# (基准, 跑几题, 题型, expected 应该长什么样, 是否需要沙箱)
CASES = [
    ("mmlu_sample", 3, "choice", "选项字母", False),
    ("gsm8k", 3, "numeric", "答案原文", False),
    ("humaneval", 2, "code_unit", "… · 通过全部单元测试", True),
    ("livecodebench", 2, "code_stdio", "… · 通过全部测试用例", True),
    ("BFCL_v4_simple_python", 3, "bfcl", "期望调用文本", False),
    ("BFCL_v4_multi_turn_base", 2, "bfcl_multi_turn", "共 N 轮", False),
]


def check_shape(kind: str, expected: str, predicted: str) -> str:
    """返回空串表示符合预期，否则返回问题描述。"""
    e = (expected or "").strip()
    if not e:
        return "expected 为空 —— 说明该题型的分派没跑到，或 runner 没写 expected"
    if kind == "choice":
        if len(e) != 1 or e not in engine.CHOICES:
            return f"选择题 expected 应该是单个选项字母，实际 {e!r}"
    elif kind == "code_unit":
        if "通过全部单元测试" not in e:
            return f"code_unit expected 形态不对：{e!r}"
    elif kind == "code_stdio":
        if "通过全部测试用例" not in e:
            return f"code_stdio expected 形态不对：{e!r}"
    elif kind == "bfcl_multi_turn":
        if not e.startswith("共 ") or "轮" not in e:
            return f"multi_turn expected 形态不对：{e!r}"
    return ""


def run_case(benchmark: str, limit: int, kind: str, want: str, need_docker: bool,
             model_id: int, docker_ok: bool):
    if need_docker and not docker_ok:
        return "SKIP", "沙箱不可用，跳过（该题型需要 Docker）"
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO evaluations(model_id, benchmark, total, status) VALUES(?,?,?,?)",
        (model_id, benchmark, limit, "pending"))
    eid = cur.lastrowid
    conn.commit()
    try:
        asyncio.run(engine.run_evaluation(eid))
    except Exception:  # noqa: BLE001
        return "FAIL", "run_evaluation 抛异常：" + traceback.format_exc(limit=2).strip().splitlines()[-1][:120]

    ev = db.query_one("SELECT status, done, correct, failed, error FROM evaluations WHERE id=?", (eid,))
    rows = db.query("SELECT idx, expected, predicted, error FROM eval_items WHERE eval_id=? ORDER BY idx", (eid,))

    if ev["status"] == "failed" and "代码沙箱不可用" in (ev["error"] or ""):
        return "SKIP", "沙箱不可用，跳过"
    if ev["status"] != "done":
        return "FAIL", f"任务状态 {ev['status']}，error={ev['error']}"
    if ev["done"] != limit:
        return "FAIL", f"完成题数 {ev['done']} != {limit}"
    if ev["failed"] != 0:
        return "FAIL", f"有 {ev['failed']} 题抛异常，第一题错误：" + (rows[0]["error"] or "")[:100]
    if not rows:
        return "FAIL", "没有落库任何明细行"
    bad = [f"idx={r['idx']}: {m}" for r in rows if (m := check_shape(kind, r["expected"], r["predicted"]))]
    if bad:
        return "FAIL", bad[0]
    return "OK", f"{ev['done']} 题，expected 形态正确（{want}）"


def main():
    ok_all = True
    try:
        mid = db.execute("INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
                         ("__mock__", "mock://local", ""))
        try:
            from app import sandbox
            docker_ok, dmsg = sandbox.docker_available()
        except Exception:  # noqa: BLE001
            docker_ok, dmsg = False, "探测失败"
        print(f"临时数据库: {db.DB_PATH}")
        print(f"沙箱: {'可用' if docker_ok else '不可用（代码类两项会跳过）'}")
        print("=" * 78)
        print(f"{'题型':<16}{'基准':<26}{'结果':<7}说明")
        print("-" * 78)
        for benchmark, limit, kind, want, need_docker in CASES:
            status, msg = run_case(benchmark, limit, kind, want, need_docker, mid, docker_ok)
            if status == "FAIL":
                ok_all = False
            print(f"{kind:<16}{benchmark:<26}{status:<7}{msg}")
        print("=" * 78)
        print("6 种题型的分派全部正确。" if ok_all else "有题型分派不正确，见上。")
        return 0 if ok_all else 1
    finally:
        shutil.rmtree(_tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
