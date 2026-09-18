"""模型用途（被测 / 判别器）与模型身份色的自检。

为什么需要它
-----------
「判别器不能被评测」这条规则有三层，任何一层漏了都会**安静地**出错：

1. **数据库/接口层**：`kind` 要能存下来、老库要能补列、`models.json` 导入要给默认值；
2. **后端拦截**：前端不列出判别器只是 UI 约定，直接调 `POST /api/evaluations` 也得拦住 ——
   否则「裁判自己也在被测之列」这条自偏袒就会悄悄发生（而且分数看起来很正常）；
3. **改类型的后果**：改成判别器**不删历史成绩**（数据是真的），但必须先让人看见有几个历史评测
   再确认。这三点都靠本脚本钉住。

**模型身份色**（排行榜上「哪个点是哪个模型」）同样在这里钉：颜色必须存在库里、
必须是**加了模型也不变**（前端原来按名字排序临时取色，加一个名字靠前的模型会让
榜上其他模型集体变色），删掉一个模型也不能让别人的颜色变。色板本身也校核：
圆点坐的浅灰轨道上对比度要够（WCAG 对图形对象要求 3:1），两两不能撞色。

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
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, model_colors  # noqa: E402


def _pick_tmp() -> pathlib.Path:
    """挑一个真的能写文件的临时目录（受限环境里系统 temp 会被沙箱拒绝）。"""
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="llmbench-models-"))
        (d / ".probe").write_text("x", encoding="utf-8")
        return d
    except OSError:
        pass
    d = ROOT / ".models_tmp"
    # 上一次崩溃留下的库会让断言随机变红/绿（残留数据被当成这次的输入），先清干净
    shutil.rmtree(d, ignore_errors=True)
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


# ---- 颜色计算（只用来校核色板，不参与业务逻辑）----
# 圆点是画在浅灰轨道上的（.ov-dist 的背景 = var(--bg2) = #eef1f6），所以按这个底色算对比度：
# WCAG 对「图形对象」的要求是 3:1（比正文的 4.5:1 低，因为不承载文字）。
DOT_BG = "#eef1f6"


def _lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(h: str) -> float:
    r, g, b = (_lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _lab(h: str) -> tuple:
    r, g, b = (_lin(c) for c in _rgb(h))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.0
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _de76(a: str, b: str) -> float:
    la, lb = _lab(a), _lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


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

        # ---- 5) 模型身份色：色板本身 + 分配规则 + 加/删模型都不动别人的颜色 -------
        # 色板校核（圆点坐在浅灰轨道 #eef1f6 上；16 色在色相环上只隔 22.5°，
        # 只靠色相必然出现"两个看起来一样的红/蓝"，所以明暗也参与区分）
        pal = model_colors.PALETTE
        check("色板 16 色互不相同", len(set(pal)), len(pal))
        worst_contrast = min(_contrast(c, DOT_BG) for c in pal)
        check_true("色板每色在浅灰轨道上对比度 ≥3:1（WCAG 图形对象要求）",
                   worst_contrast >= 3.0, f"最低 {worst_contrast:.2f}")
        worst_de = min(_de76(pal[i], pal[j])
                       for i in range(len(pal)) for j in range(i + 1, len(pal)))
        check_true("色板两两可区分（ΔE76 ≥20，防明显撞色）", worst_de >= 20,
                   f"最像的一对只有 ΔE {worst_de:.1f}")

        def colors_now() -> dict:
            return {r["id"]: r["color"] for r in db.query("SELECT id, color FROM models")}

        before_new = colors_now()
        check_true("已有模型都带颜色（不给空值，否则前端只能画兜底色）",
                   all(before_new.values()), f"空的：{[k for k, v in before_new.items() if not v]}")
        check_true("颜色都出自色板", set(before_new.values()) <= set(pal))

        # 核心承诺 ①：加模型**不会**改变别人的颜色
        # （前端原来按名字排序取色，这里就是那次 bug 的回归位）
        fresh = add_model("__新模型__")
        after_new = colors_now()
        check("加一个模型后，已有模型的颜色一个都没变",
              {k: v for k, v in after_new.items() if k != fresh}, before_new)
        check_true("新模型也拿到了颜色", bool(after_new[fresh]))

        # 核心承诺 ②：删模型也不改变别人的颜色（这是「存颜色」比「按创建顺序取色」强的地方）
        victim = add_model("__待删除__")
        with_victim = colors_now()
        main.delete_model(victim)
        after_del = colors_now()
        check("删掉一个模型后，其余模型的颜色不变",
              after_del, {k: v for k, v in with_victim.items() if k != victim})
        check_true("被删的模型确实没了", victim not in after_del)

        # 空出来的颜色会被下一个新模型捡回来（pick 挑"用得最少"的那个）
        filler = add_model("__补位__")
        check("被删模型空出来的颜色会被下一个新模型复用",
              colors_now()[filler], with_victim[victim])

        # 排行榜 payload 里的颜色必须就是库里的颜色（前端画点用的就是这个值）
        db.execute("INSERT INTO evaluations(model_id, benchmark, total, status, done, correct)"
                   " VALUES(?,?,?,?,?,?)", (fresh, "mmlu_sample", 12, "done", 12, 9))
        lb = main.leaderboard()
        payload_colors: dict = {}
        dup_across_cats: list = []
        for cat in lb:
            for row in cat["combined"]:
                n = row["model_name"]
                if n in payload_colors and payload_colors[n] != row["color"]:
                    dup_across_cats.append(n)
                payload_colors[n] = row["color"]
        check_true("排行榜 payload 带上了每个模型的颜色", bool(payload_colors))
        check("同一模型在不同分类里的颜色一致", dup_across_cats, [])
        check("排行榜里的颜色 = models.color（前端不再自己排色）",
              payload_colors,
              {r["name"]: r["color"] for r in db.query("SELECT name, color FROM models")
               if r["name"] in payload_colors})

        # 老库补色：把颜色清空再补一次，补出来的必须不撞色、也都来自色板
        db.execute("UPDATE models SET color='' WHERE id IN (?, ?)", (fresh, filler))
        db.backfill_model_colors(db.get_conn())
        db.get_conn().commit()      # 直接调函数时不会有 init_db 末尾那次 commit，得自己补
        repaired = colors_now()
        check_true("老库补色：清空的行被补上了", all(repaired.values()))
        check_true("老库补色：补出来的也不撞色", len(set(repaired.values())) == len(repaired))
        check_true("老库补色：补出来的仍在色板里", set(repaired.values()) <= set(pal))

        # 但上面那条只测了 backfill 函数**本身** —— 老库升级真正会走的路径是
        # 「init_db 补列 + 顺手补色」，接线漏了照样绿（负向验证就是这么发现的）。
        # 所以这里造一个**没有 color 列**的老库，完整跑一遍 init_db()。
        old_path = _tmp_root / "old_schema.db"
        con = sqlite3.connect(str(old_path))
        con.executescript(
            "CREATE TABLE models(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
            " base_url TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '',"
            " kind TEXT NOT NULL DEFAULT 'test', extra_body TEXT NOT NULL DEFAULT '');"
            "INSERT INTO models(name, base_url) VALUES('__老库甲__','u'),('__老库乙__','u');"
        )
        con.commit()
        con.close()
        keep_path, db.DB_PATH = db.DB_PATH, old_path
        try:
            db.get_conn().close()   # 必须关掉旧连接：它未提交的事务会锁住原来的库
        except Exception:  # noqa: BLE001
            pass
        db._local.conn = None
        try:
            db.init_db()
            old_rows = db.query("SELECT name, color FROM models ORDER BY id")
        finally:
            try:
                db.get_conn().close()
            except Exception:  # noqa: BLE001
                pass
            db._local.conn = None
            db.DB_PATH = keep_path
        check_true("老库升级：init_db 会补列并立刻补色（漏了接线，老模型的点就是灰的）",
                   len(old_rows) == 2 and all(r["color"] for r in old_rows)
                   and len({r["color"] for r in old_rows}) == 2,
                   f"{old_rows}")

        # 色板用完（>16 个模型）时按"用得最少"分配，而不是从头再轮一遍
        while len(colors_now()) < len(pal):
            add_model(f"__填满 {len(colors_now())}__")
        full_house = list(colors_now().values())
        check("填满色板：16 个模型颜色互不相同", len(set(full_house)), len(pal))
        overflow = add_model("__第 17 个__")
        check("第 17 个模型复用用得最少的颜色（不是随便挑一个）",
              colors_now()[overflow], model_colors.pick(full_house))

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
        print(f"全部 {len(RESULTS)} 项通过：模型用途（被测 / 判别器）与模型身份色的约束都生效。")
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
