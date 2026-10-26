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

**删模型**要连成绩一起删干净（evaluations / eval_items / data/results 的导出文件），
而且删之前必须让人看见代价（有成绩时先 409 + 条数，确认了才动）；
没成绩的模型不该拦。原来只删 models 一行，剩下的成绩**界面上看不见但一直躺在库里**，
还让「评测任务」那个数字对不上列表。

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

from app import db, model_colors, summary  # noqa: E402


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
from app.benchmarks import CATEGORIES  # noqa: E402

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


def add_model(name: str, kind: str = "test", base_url: str = "mock://local",
              api_key: str = "k", extra_body: str = "", copy_api_from: int | None = None) -> int:
    """建模型。默认 base_url 是 mock://local（演示模型）——
    注意：mock 模型**不进排行榜**（见本脚本最后一节），要上排行榜的测试模型必须传真实 base_url。"""
    return main.create_model(main.ModelIn(
        name=name, base_url=base_url, api_key=api_key, kind=kind,
        extra_body=extra_body, copy_api_from=copy_api_from))["id"]


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

        # ---- 3b) 别名：同一个 API 名可以建多条记录，显示名必须唯一 ----------
        # 为什么需要：像 deepseek-flash 这种 API 名，不同时间可能指向不同底层版本；
        # 只有 name 时两次评测会被合并成同一条。alias 是显示名，调用仍是 name。
        d = add_model("__同名API__")
        d2 = main.create_model(main.ModelIn(
            name="__同名API__", alias="__同名API__·v2", base_url="mock://local", api_key="k"))["id"]
        labels = {r["id"]: r["label"] for r in main.list_models()}
        check("同 API 名的两条记录可以共存（别名区分）", labels[d2], "__同名API__·v2")
        check("没别名时显示名回到 API 调用名", labels[d], "__同名API__")
        check("列表里同时给出 alias 与 label",
              (main.list_models()[0].get("alias") is not None), True)
        raises("显示名重名 -> 400（排行榜/图例按显示名区分模型）",
               lambda: main.create_model(main.ModelIn(
                   name="__别的名__", alias="__同名API__·v2", base_url="mock://local", api_key="k")), 400)
        raises("别名与别人的 API 调用名撞车 -> 400",
               lambda: main.create_model(main.ModelIn(
                   name="__再一个__", alias="__同名API__", base_url="mock://local", api_key="k")), 400)
        r3 = main.update_model_alias(d2, main.AliasIn(alias="__同名API__·v3"))
        check("可以改别名", r3["label"], "__同名API__·v3")
        raises("改成别人的显示名 -> 400",
               lambda: main.update_model_alias(d2, main.AliasIn(alias="__同名API__")), 400)
        # 清空别名时如果"裸 API 名"已被同名的另一条记录占着，应当被拦住（这正是唯一性的价值）
        raises("清空别名后与另一条同名记录撞名 -> 400",
               lambda: main.update_model_alias(d2, main.AliasIn(alias="")), 400)
        r5 = main.update_model_alias(d2, main.AliasIn(alias="__同名API__·还原"))
        check("改回可区分的别名", r5["label"], "__同名API__·还原")
        # 名字本来就唯一的模型：清空别名应干净地回到 API 调用名
        e = main.create_model(main.ModelIn(
            name="__唯一API名__", alias="__唯一API名__·临时", base_url="mock://local", api_key="k"))["id"]
        r6 = main.update_model_alias(e, main.AliasIn(alias=""))
        check("清空别名 = 回到 API 调用名", r6["label"], "__唯一API名__")
        raises("不存在的模型改别名 -> 404",
               lambda: main.update_model_alias(99999, main.AliasIn(alias="x")), 404)
        # 跑过评测后别名锁定：历史成绩必须一直归属当时那个别名，
        # 否则改个名就把"以前那个版本跑的成绩"重新算到新名字下了。
        f = add_model("__跑过评测__")
        insert_eval(f)
        raises("有评测的模型改别名 -> 409（别名锁定）",
               lambda: main.update_model_alias(f, main.AliasIn(alias="__跑过评测__·改名")), 409)
        labels2 = {r["id"]: r["label"] for r in main.list_models()}
        check("被拒之后别名保持原样", labels2[f], "__跑过评测__")
        g = add_model("__没跑过__")
        r7 = main.update_model_alias(g, main.AliasIn(alias="__没跑过__·改错字"))
        check("没有评测的模型仍然可以改别名（改错字）", r7["label"], "__没跑过__·改错字")

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
        check(f"色板 {len(pal)} 色互不相同", len(set(pal)), len(pal))
        worst_contrast = min(_contrast(c, DOT_BG) for c in pal)
        check_true("色板每色在浅灰轨道上对比度 ≥3:1（WCAG 图形对象要求）",
                   worst_contrast >= 3.0, f"最低 {worst_contrast:.2f}")
        worst_de = min(_de76(pal[i], pal[j])
                       for i in range(len(pal)) for j in range(i + 1, len(pal)))
        check_true("色板两两可区分（ΔE76 ≥20，防明显撞色）", worst_de >= 20,
                   f"最像的一对只有 ΔE {worst_de:.1f}")
        # 模型色和**分类色**也必须分得开：两套颜色会出现在同一屏（排行榜总览每行
        # 左边是分类点、中间是冠军模型点）。上一版色板照分类色挑，最严重的 ΔE=0。
        cat_colors = [c["color"] for c in CATEGORIES]
        worst_vs_cat = min(_de76(c, cc) for c in pal for cc in cat_colors)
        check_true("色板与分类色可区分（ΔE76 ≥16，模型点/分类点不撞）",
                   worst_vs_cat >= 16, f"最像的一对只有 ΔE {worst_vs_cat:.1f}")

        def colors_now() -> dict:
            return {r["id"]: r["color"] for r in db.query("SELECT id, color FROM models")}

        before_new = colors_now()
        check_true("已有模型都带颜色（不给空值，否则前端只能画兜底色）",
                   all(before_new.values()), f"空的：{[k for k, v in before_new.items() if not v]}")
        check_true("颜色都出自色板", set(before_new.values()) <= set(pal))

        # 核心承诺 ①：加模型**不会**改变别人的颜色
        # （前端原来按名字排序取色，这里就是那次 bug 的回归位）
        fresh = add_model("__新模型__", base_url="https://example.test/v1")
        after_new = colors_now()
        check("加一个模型后，已有模型的颜色一个都没变",
              {k: v for k, v in after_new.items() if k != fresh}, before_new)
        check_true("新模型也拿到了颜色", bool(after_new[fresh]))

        # 核心承诺 ②：删模型也不改变别人的颜色（这是「存颜色」比「按创建顺序取色」强的地方）
        victim = add_model("__待删除__")
        with_victim = colors_now()
        asyncio.run(main.delete_model(victim))   # 接口是 async（要等运行中的任务停掉）
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

        # 色板用完（>12 个模型）时按"用得最少"分配，而不是从头再轮一遍
        while len(colors_now()) < len(pal):
            add_model(f"__填满 {len(colors_now())}__")
        full_house = list(colors_now().values())
        check(f"填满色板：{len(pal)} 个模型颜色互不相同", len(set(full_house)), len(pal))
        overflow = add_model("__第 17 个__")
        check(f"第 {len(pal) + 1} 个模型复用用得最少的颜色（不是随便挑一个）",
              colors_now()[overflow], model_colors.pick(full_house))

        # ---- 6) 删模型：成绩必须一起删干净，而且删之前要让人看见代价 ----------
        # 这一节针对的 bug：原来 `DELETE /api/models/{id}` 只删 models 一行，
        # 它的 evaluations / eval_items / data/results 里的导出文件全都留着 ——
        # 界面上看不见（任务列表和排行榜都 JOIN models），但库里越积越多，
        # 而且「评测任务」那个数字数的是原始表 COUNT(*)，于是卡片显示 2、列表只有 1 行。
        doomed = add_model("__要删的__")
        keep = add_model("__别动我__")
        d_eval = db.execute("INSERT INTO evaluations(model_id, benchmark, total, status, done, correct)"
                            " VALUES(?,?,?,?,?,?)", (doomed, "mmlu_sample", 12, "done", 12, 9))
        k_eval = db.execute("INSERT INTO evaluations(model_id, benchmark, total, status, done, correct)"
                            " VALUES(?,?,?,?,?,?)", (keep, "mmlu_sample", 12, "done", 12, 6))
        for eid in (d_eval, k_eval):
            for idx in (0, 1):
                db.execute("INSERT INTO eval_items(eval_id, idx, question, predicted, correct)"
                           " VALUES(?,?,?,?,?)", (eid, idx, "q", "p", 1))
        # 导出的明细文件：用一个临时目录，别碰用户的 data/results。
        # 注意要补 `datasets.RESULTS_DIR`：`engine.delete_items_file` 是从 datasets 导入的
        # **绑定引用**，删文件时读的是 datasets 模块里那个常量（补 engine.RESULTS_DIR 没用）。
        import app.datasets as _datasets  # noqa: PLC0415
        old_results = _datasets.RESULTS_DIR
        _datasets.RESULTS_DIR = _tmp_root / "results"
        _datasets.RESULTS_DIR.mkdir(exist_ok=True)
        export = _datasets.RESULTS_DIR / f"eval_{d_eval}.jsonl"
        export.write_text("{}\n", encoding="utf-8")
        try:
            raises("删有成绩的模型 -> 409（先让人看见要删掉什么）",
                   lambda: asyncio.run(main.delete_model(doomed)), 409)
            check_true("409 里没有把模型删掉（确认前不动数据）",
                       db.query_one("SELECT COUNT(*) AS n FROM models WHERE id=?", (doomed,))["n"] == 1)
            # 提示里必须带条数与题数：只说"要删吗"等于没说
            detail = ""
            try:
                asyncio.run(main.delete_model(doomed))
            except HTTPException as e:
                detail = str(e.detail)
            check_true("409 的说明里带了任务条数与题明细数",
                       "1 条评测任务" in detail and "2 题" in detail, detail)

            res = asyncio.run(main.delete_model(doomed, confirm=True))
            check("确认后真的删掉了这个模型",
                  db.query_one("SELECT COUNT(*) AS n FROM models WHERE id=?", (doomed,))["n"], 0)
            check("它名下的评测任务也删了", res["deleted_evaluations"], 1)
            check("逐题明细也删了",
                  db.query_one("SELECT COUNT(*) AS n FROM eval_items WHERE eval_id=?", (d_eval,))["n"], 0)
            check("主记录也删了",
                  db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE id=?", (d_eval,))["n"], 0)
            check_true("导出的 jsonl 也删了", not export.exists())
            # 别人的东西一根汗毛都不能动
            check("别人的评测任务还在",
                  db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE id=?", (k_eval,))["n"], 1)
            check("别人的逐题明细还在",
                  db.query_one("SELECT COUNT(*) AS n FROM eval_items WHERE eval_id=?", (k_eval,))["n"], 2)
            check_true("别的模型还在",
                       db.query_one("SELECT COUNT(*) AS n FROM models WHERE id=?", (keep,))["n"] == 1)

            # 没有成绩的模型不该被拦（没什么可确认的，别让人白点两次）
            empty_model = add_model("__没成绩的__")
            res2 = asyncio.run(main.delete_model(empty_model))
            check("没成绩的模型直接删掉（不用确认）", res2["deleted_evaluations"], 0)
            check("确实删掉了",
                  db.query_one("SELECT COUNT(*) AS n FROM models WHERE id=?", (empty_model,))["n"], 0)

            # 数字口径：/api/evaluations 的 total 必须等于列表实际条数
            #（原来 total 数的是原始表 COUNT(*)，有孤儿行时会「卡片 2、列表 1」）
            ev = main.list_evaluations()
            check("评测任务数口径一致（total == 列表条数）", ev["total"], len(ev["items"]))
        finally:
            _datasets.RESULTS_DIR = old_results

        raises("删不存在的模型 -> 404",
               lambda: asyncio.run(main.delete_model(999999)), 404)

        # ---- 7) 复用 API（copy_api_from）+ 连接信息分组 ---------------------------
        # 场景：同一套 API（base_url + key）上再挂一个模型名 —— 不用把 key 再粘一遍。
        # 为什么必须在后端复制：API Key 在接口里是脱敏的（sk-w****5VcY），前端拿不到真 key。
        src_id = add_model("__来源模型__", base_url="https://example.test/v1",
                           api_key="sk-src-1234567890", extra_body='{"enable_thinking": false}')
        # api_key 要显式传空串：helper 的默认值是 "k"，非空就按"显式优先"走，
        # 那样测的就不是"留空才补"了（第一版就是这么写错的）。
        copied = add_model("__复用API新模型__", base_url="", api_key="", copy_api_from=src_id)
        crow = db.query_one("SELECT * FROM models WHERE id=?", (copied,))
        check("copy_api_from：base_url 从那一组补上", crow["base_url"], "https://example.test/v1")
        check("copy_api_from：api_key 从那一组补上", crow["api_key"], "sk-src-1234567890")
        check("copy_api_from：额外参数也一起补（界面上没这个入口，只能靠复制）",
              crow["extra_body"], '{"enable_thinking": false}')

        explicit = add_model("__显式优先__", base_url="https://explicit.test/v1",
                             api_key="sk-mine-99999999", copy_api_from=src_id)
        erow = db.query_one("SELECT * FROM models WHERE id=?", (explicit,))
        check("copy_api_from：自己填的 base_url 优先（留空才补）",
              erow["base_url"], "https://explicit.test/v1")
        check("copy_api_from：自己填的 key 优先（留空才补）", erow["api_key"], "sk-mine-99999999")

        raises("copy_api_from 指向不存在的模型 -> 404（不能静默给个空 key 的模型）",
               lambda: add_model("__无来源__", base_url="", copy_api_from=999999), 404)

        listed = {m["id"]: m for m in main.list_models()}
        check_true("copy_api_from 之后接口里的 key 仍然脱敏（不外泄明文）",
                   "****" in listed[copied]["api_key"]
                   and listed[copied]["api_key"] != "sk-src-1234567890",
                   f"列表里是 {listed[copied]['api_key']!r}")

        # 分组接口：按 (base_url, api_key) 分组 —— 分组的单位是**一套 API**，不是模型
        add_model("__同API甲__", base_url="https://example.test/v1", api_key="sk-src-1234567890")
        add_model("__同API乙__", base_url="https://example.test/v1", api_key="sk-src-1234567890")
        groups = main.list_api_groups()
        grp = next((g for g in groups if g["base_url"] == "https://example.test/v1"
                    and g["api_key"] == "sk-s****7890"), None)
        check_true("分组接口：相同 (base_url, key) 的模型合成一项", grp is not None,
                   f"现有分组：{[(g['base_url'], g['api_key']) for g in groups]}")
        if grp:
            check_true("分组接口：这一项里列出全部已添加模型",
                       {"__来源模型__", "__复用API新模型__", "__同API甲__", "__同API乙__"} <= set(grp["models"]),
                       f"{grp['models']}")
            check_true("分组接口：给出代表 model_id（前端回传它就能复用这套 API）",
                       isinstance(grp["model_id"], int) and grp["model_id"] > 0)
            check_true("分组接口：只给脱敏 key（真实 key 不出接口）",
                       "****" in grp["api_key"] and grp["api_key"] != "sk-src-1234567890")

        # 同端点、不同 key 必须是两项（否则"复用 API"会拿错 key）
        add_model("__同端点另一把key__", base_url="https://example.test/v1", api_key="sk-other-0000000000")
        same_url = [g for g in main.list_api_groups() if g["base_url"] == "https://example.test/v1"]
        check_true("分组接口：同 base_url 不同 key 分成两项（不能混成一项）",
                   len(same_url) >= 2, f"{[(g['api_key'], g['models']) for g in same_url]}")

        # 脱敏串撞车：两把 key 只差中间几位、首尾相同 → 脱敏后一模一样。
        # 分组必须按**真 key** 分，否则会把它们错并成一项，并拿错 key 去复制。
        add_model("__撞串甲__", base_url="https://collide.test/v1", api_key="sk-AAAA-mid1-BBBB")
        add_model("__撞串乙__", base_url="https://collide.test/v1", api_key="sk-AAAA-mid2-BBBB")
        collide = [g for g in main.list_api_groups() if g["base_url"] == "https://collide.test/v1"]
        check_true("分组接口：脱敏串相同但真 key 不同的两把 key 不会被并成一项",
                   len(collide) == 2, f"{[(g['api_key'], g['models']) for g in collide]}")

        # ---- 8) 演示/mock 模型的成绩不进排行榜与总览 ------------------------------
        # 口径：app/scoring.is_mock_model（base_url 以 mock:// 开头）。
        # 用户明确要求：**只排 mock 模型，样例题库照旧参与排名** —— 后者刻意不动，
        # 所以这里同时钉住"该排的排掉、不该动的没动"。
        mock_m = add_model("__演示模型__", base_url="mock://local")
        real_m = add_model("__真实模型__", base_url="https://example.test/v2")
        for mid, correct in ((mock_m, 12), (real_m, 6)):
            db.execute("INSERT INTO evaluations(model_id, benchmark, total, status, done, correct)"
                       " VALUES(?,?,?,?,?,?)", (mid, "mmlu_sample", 12, "done", 12, correct))
        lb_models = {r["model_name"] for cat in main.leaderboard() for r in cat["combined"]}
        ov_models = {m["name"] for m in main.overview()["models"]}
        check_true("排行榜里没有 mock 模型（demo 不上榜）", "__演示模型__" not in lb_models,
                   f"榜单模型：{sorted(lb_models)}")
        check_true("成绩总览里也没有 mock 模型", "__演示模型__" not in ov_models,
                   f"总览模型：{sorted(ov_models)}")
        check_true("真实模型照旧上榜（没有误伤）",
                   "__真实模型__" in lb_models and "__真实模型__" in ov_models)
        lb_benches = {b["benchmark"] for cat in main.leaderboard() for b in cat.get("boards", [])}
        check_true("样例题库仍参与排名（用户明确选了不动它）", "mmlu_sample" in lb_benches,
                   f"分项榜基准：{sorted(lb_benches)}")
        stats = summary.collect()
        check_true("AI 总结照旧统计被排除的 mock 成绩条数（它一直排 mock）",
                   stats["skipped"]["mock_models"] >= 1, f"{stats['skipped']}")

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
        print(f"全部 {len(RESULTS)} 项通过：模型用途（被测 / 判别器）、身份色与删除清理的约束都生效。")
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
