import sqlite3
import threading
from pathlib import Path

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


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS models(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS evaluations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            benchmark TEXT NOT NULL,
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
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_eval ON eval_items(eval_id);
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
