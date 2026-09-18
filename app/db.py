import sqlite3
import threading
from pathlib import Path

from . import model_colors

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def backfill_model_colors(conn) -> None:
    """给还没有颜色的模型补上颜色（按创建顺序，和新建模型走同一套分配规则）。

    单独成一个函数是为了**可测**：老库升级、以及「有人手改过库」这两种情况都要能查，
    所以自检可以直接把某行的 color 清空、再调一次它，验证补出来的颜色不撞色。
    """
    rows = conn.execute("SELECT id FROM models WHERE color IS NULL OR color = '' ORDER BY id").fetchall()
    if not rows:
        return
    used = [r["color"] for r in conn.execute(
        "SELECT color FROM models WHERE color IS NOT NULL AND color <> ''")]
    for r in rows:
        c = model_colors.pick(used)
        conn.execute("UPDATE models SET color=? WHERE id=?", (c, r["id"]))
        used.append(c)


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS models(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            -- 用途：test = 被测模型（可发起评测），judge = 判别器（只用来判分，不能被评测）。
            -- 做成数据层的约束而不是 UI 约定：安全评测里「裁判自己也在被测之列」会带来自偏袒，
            -- 而且裁判换了分数就不可比，所以必须能一路查到「这次是谁判的」。
            kind TEXT NOT NULL DEFAULT 'test',
            -- 额外请求参数（JSON 对象字符串，空 = 不带）。
            -- 用途举例：DashScope 上的 Qwen3 思考模型传 {"enable_thinking": false} 可以关掉思考 ——
            -- 当裁判时这很关键：实测思考型裁判每条要吐 ~700 个思考 token，
            -- 而判分只需要一个词，白花的钱是判分成本的 3 倍。
            -- 做成**模型级**配置而不是代码里写死 provider：不同厂商的参数名不一样，
            -- 而且不能给不认识的 provider 乱发字段（严格校验的接口会直接 400）。
            extra_body TEXT NOT NULL DEFAULT '',
            -- 排行榜上的身份色（见 app/model_colors.py）。落库而不是前端临时算：
            -- 前端按名字排序取色时，加一个名字靠前的模型会让榜上其他模型集体变色。
            -- 空串 = 老库还没补（迁移里会按名字顺序补上）。
            color TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS evaluations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            benchmark TEXT NOT NULL,
            -- 裁判模型（只有安全类基准用得到）：哪一次评测是谁判的，必须能查回来 ——
            -- 换了裁判分数就不可比，所以这个字段是结果的一部分，不是运行参数。
            judge_model_id INTEGER,
            total INTEGER DEFAULT 0,
            done INTEGER DEFAULT 0,
            correct INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_latency_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS eval_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_id INTEGER NOT NULL,
            idx INTEGER,
            question TEXT,
            expected TEXT,
            predicted TEXT,
            raw_response TEXT,
            correct INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            error TEXT,
            -- 「这题没得到有效结果」（超时 / 服务商拦截 / 空正文 / 判不出）记 1；
            -- **答错记 0**（答错也会写 error 当诊断，所以光看 error 分不出这两种）。
            -- 老数据留 NULL = 当年没记这个信息，前端会退回「有诊断文本就算失败」的老判据。
            failed INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_items_eval ON eval_items(eval_id);
        -- AI 总结：落库是为了可追溯（谁生成的、依据哪份数据、什么时候），
        -- 不是为了缓存 —— fingerprint 才是缓存判据：数据没变就不该重新烧 token。
        CREATE TABLE IF NOT EXISTS summaries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL,      -- 输入矩阵指纹
            model_id INTEGER,               -- 用哪个模型生成的
            model_name TEXT,
            content TEXT,                   -- 模型返回的 JSON 原文
            unverified TEXT,                -- 回查不到的数字（防幻觉）
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_summaries_fp ON summaries(fingerprint);
        """
    )
    # 老库补列：评测运行参数
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(evaluations)")}
    for col, ddl in [
        ("max_tokens", "INTEGER DEFAULT 2048"),
        ("timeout_s", "INTEGER DEFAULT 90"),
        ("concurrency", "INTEGER DEFAULT 8"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE evaluations ADD COLUMN {col} {ddl}")
    # 老库补列：模型用途（老库里的模型一律当被测模型，不给它们安判别器的身份）
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(models)")}
    if "kind" not in mcols:
        conn.execute("ALTER TABLE models ADD COLUMN kind TEXT NOT NULL DEFAULT 'test'")
    # 老库补列：裁判模型（老评测都是程序判分的，这一列留空即正确）
    if "judge_model_id" not in cols:
        conn.execute("ALTER TABLE evaluations ADD COLUMN judge_model_id INTEGER")
    # 老库补列：模型级额外请求参数（老模型留空 = 行为和以前一样）
    if "extra_body" not in mcols:
        conn.execute("ALTER TABLE models ADD COLUMN extra_body TEXT NOT NULL DEFAULT ''")
    # 老库补列：模型身份色。补列之后立刻补色，否则老模型的点在排行榜上是灰的
    # （前端拿不到颜色就只能画兜底色）。补色规则和新建模型共用 model_colors.pick。
    if "color" not in mcols:
        conn.execute("ALTER TABLE models ADD COLUMN color TEXT NOT NULL DEFAULT ''")
        backfill_model_colors(conn)
    # 老库补列：逐题的「没得到有效结果」标记。**故意不给默认值**：老行留 NULL，
    # 表示「当年没记」，前端据此退回老判据；给 0 会把老任务里真正的失败题显示成答错。
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(eval_items)")}
    if "failed" not in icols:
        conn.execute("ALTER TABLE eval_items ADD COLUMN failed INTEGER")
    conn.commit()


def query(sql: str, params=()):
    cur = get_conn().execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=()):
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid
