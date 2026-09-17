"""模型用途（被测 / 判别器）的自检。

为什么需要它
-----------
「判别器不能被评测」这条规则有三层，任何一层漏了都会**安静地**出错：

1. **数据库/接口层**：`kind` 要能存下来、老库要能补列、`models.json` 导入要给默认值；
2. **后端拦截**：前端不列出判别器只是 UI 约定，直接调 `POST /api/evaluations` 也得拦住 ——
   否则「裁判自己也在被测之列」这条自偏袒就会悄悄发生（而且分数看起来很正常）；
3. **改类型的后果**：改成判别器**不删历史成绩**（数据是真的），但必须先让人看见有几个历史评测
   再确认。这三点都靠本脚本钉住。

**特别小心**：`sync_models_file()` 会写真实的 `data/models.json`（里面有 API key），
所以这里必须把 `main.MODELS_FILE` 指到临时目录 —— 否则跑一次自检就把用户的 key 文件覆盖了。

全程使用临时数据库 + 临时 models.json，不碰 `data/app.db` 与 `data/models.json`。

用法::

    python scripts/verify_models.py
"""
import asyncio
import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402


def _pick_tmp() -> pathlib.Path:
    """挑一个真的能写文件的临时目录（受限环境里系统 temp 会被沙箱拒绝）。"""
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="llmbench-models-"))
        (d / ".probe").write_text("x", encoding="utf-8")
        return d
    except OSError:
        pass
    d = ROOT / ".models_tmp"
    d.mkdir(exist_ok=True)
    return d


_tmp_root = _pick_tmp()
db.DB_PATH = _tmp_root / "app.db"
db._local.conn = None
db.init_db()

from fastapi import HTTPException  # noqa: E402

from app import main  # noqa: E402

# 关键：别让 sync_models_file 覆盖用户真实的 data/models.json
main.MODELS_FILE = _tmp_root / "models.json"

RESULTS: list = []


def check(name: str, got, want) -> None:
    RESULTS.append((name, got == want, f"期望 {want!r}，实际 {got!r}"))


def check_true(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail or "期望为真，实际为假"))


def raises(name: str, fn, status: int) -> None:
    try:
        fn()
    except HTTPException as e:
        RESULTS.append((name, e.status_code == status,
                        f"期望 HTTP {status}，实际 {e.status_code}（{e.detail}）"))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, f"抛的不是 HTTPException：{type(e).__name__}: {e}"))
    else:
        RESULTS.append((name, False, "居然没抛异常"))


def add_model(name: str, kind: str = "test") -> int:
    return main.create_model(main.ModelIn(name=name, base_url="mock://local",
                                          api_key="k", kind=kind))["id"]


def insert_eval(mid: int, benchmark: str = "mmlu_sample") -> int:
    return db.execute(
        "INSERT INTO evaluations(model_id, benchmark, total, status) VALUES(?,?,?,?)",
        (mid, benchmark, 12, "done"))


def main_() -> int:
    try:
        # ---- 1) 新建模型默认是被测；可以指定判别器 --------------------------
        a = add_model("__被测__")
        j = add_model("__裁判__", kind="judge")
        rows = {r["id"]: r for r in main.list_models()}
        check("新建模型默认 kind = test", rows[a]["kind"], "test")
        check("可以建成判别器", rows[j]["kind"], "judge")
        check_true("列表带出历史评测条数（供二次确认用）", rows[a]["evaluations"] == 0)
        raises("非法 kind 被拒", lambda: add_model("__x__", kind="admin"), 400)

        # ---- 2) 后端硬拦：判别器不能发起评测 --------------------------------
        before = db.query_one("SELECT COUNT(*) AS n FROM evaluations")["n"]
        raises("判别器发起评测 -> 400",
               lambda: asyncio.run(main.create_evaluation(main.EvalIn(
                   model_id=j, benchmark="mmlu_sample", limit=1))), 400)
        after = db.query_one("SELECT COUNT(*) AS n FROM evaluations")["n"]
        check("被拒时不会留下任务记录", after, before)

        # 被测模型可以发起（用 mock 模型 + 内置样例，不花钱、不联网）
        asyncio.run(main.create_evaluation(main.EvalIn(
            model_id=a, benchmark="mmlu_sample", limit=1)))
        check("被测模型可以发起评测",
              db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE model_id=?", (a,))["n"], 1)
        raises("模型不存在 -> 404",
               lambda: asyncio.run(main.create_evaluation(main.EvalIn(
                   model_id=99999, benchmark="mmlu_sample", limit=1))), 404)

        # ---- 3) 改用途：没有历史评测直接改；有历史评测要先确认 ---------------
        b = add_model("__有历史__")
        r = main.update_model_kind(b, main.KindIn(kind="judge"))
        check("没有历史评测的模型改用途直接生效（不打扰用户）", r["kind"], "judge")
        check_true("且 changed=True", r["changed"] is True)

        c = add_model("__很多历史__")
        insert_eval(c)
        insert_eval(c)
        raises("有历史评测时改用途 -> 409（先让人看见）",
               lambda: main.update_model_kind(c, main.KindIn(kind="judge")), 409)
        r2 = main.update_model_kind(c, main.KindIn(kind="judge", confirm=True))
        check("确认后可以改成判别器", r2["kind"], "judge")
        check("改类型不删历史评测（数据是真的，留着）",
              db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE model_id=?", (c,))["n"], 2)
        check("返回里带上历史条数，供提示", r2["evaluations"], 2)

        same = main.update_model_kind(c, main.KindIn(kind="judge"))
        check_true("已经是该用途时 changed=False（幂等，不报错）", same["changed"] is False)

        # 改回被测之后又能发起评测了（改类型是可逆的，不是单向门）
        main.update_model_kind(c, main.KindIn(kind="test"))
        asyncio.run(main.create_evaluation(main.EvalIn(
            model_id=c, benchmark="mmlu_sample", limit=1)))
        check("改回被测后可以再发起评测",
              db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE model_id=?", (c,))["n"], 3)

        raises("不存在的模型改用途 -> 404",
               lambda: main.update_model_kind(99999, main.KindIn(kind="judge")), 404)
        raises("改用途时非法 kind -> 400",
               lambda: main.update_model_kind(a, main.KindIn(kind="judge2")), 400)

        # ---- 4) models.json：写出带 kind / 老文件按被测导入 ------------------
        main.sync_models_file()
        written = json.loads(main.MODELS_FILE.read_text(encoding="utf-8"))
        check_true("models.json 里带 kind 字段", all("kind" in w for w in written))
        check("导出的 kind 与库里一致",
              {w["name"]: w["kind"] for w in written}["__裁判__"], "judge")

        # 老格式（没有 kind）应按「被测」导入，不能凭空变成判别器
        main.MODELS_FILE.write_text(json.dumps(
            [{"name": "__老文件模型__", "base_url": "mock://local", "api_key": "k"}],
            ensure_ascii=False), encoding="utf-8")
        main.import_models_file()
        old = db.query_one("SELECT kind FROM models WHERE name='__老文件模型__'")
        check("老 models.json 按「被测模型」导入", old["kind"], "test")

        print(f"临时目录: {_tmp_root}")
        print("=" * 74)
        bad = 0
        for name, ok, detail in RESULTS:
            print(f"[{'通过' if ok else '失败'}] {name}")
            if not ok:
                bad += 1
                print(f"    {detail}")
        print("=" * 74)
        if bad:
            print(f"{bad}/{len(RESULTS)} 项不通过。")
            return 1
        print(f"全部 {len(RESULTS)} 项通过：模型用途（被测 / 判别器）的三层约束都生效。")
        return 0
    finally:
        try:
            db.get_conn().close()
            db._local.conn = None
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(_tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_())
