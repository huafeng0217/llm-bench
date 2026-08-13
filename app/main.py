import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, engine
from .benchmarks import get_meta

STATIC_DIR = Path(__file__).resolve().parent / "static"
MODELS_FILE = Path(__file__).resolve().parent.parent / "data" / "models.json"


def import_models_file():
    """启动时从本地文档 data/models.json 导入模型配置（含 key，仅存本机）。"""
    if not MODELS_FILE.exists():
        return
    try:
        items = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    for it in items:
        name = str(it.get("name", "")).strip()
        base_url = str(it.get("base_url", "")).strip().rstrip("/")
        if not name or not base_url:
            continue
        if not db.query_one(
            "SELECT id FROM models WHERE name=? AND base_url=?", (name, base_url)
        ):
            db.execute(
                "INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
                (name, base_url, str(it.get("api_key", "")).strip()),
            )


def sync_models_file():
    """把模型配置同步到本地文档，方便用户查看/备份/手动编辑。"""
    rows = db.query("SELECT name, base_url, api_key FROM models ORDER BY id")
    MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    import_models_file()
    yield


app = FastAPI(title="LLM Bench", lifespan=lifespan)


class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""


class EvalIn(BaseModel):
    model_id: int
    benchmark: str
    limit: int = 0  # 0 = 全部题目
    max_tokens: int = 2048   # 输出 token 预算（思考型模型需含推理 token）
    timeout_s: int = 90      # 单题请求超时（秒）
    concurrency: int = 8     # 并发请求数


@app.post("/api/models/test")
async def test_model(m: ModelIn):
    """保存前测试连接：用最小 prompt 实际调一次模型接口。"""
    cfg = {
        "name": m.name.strip(),
        "base_url": m.base_url.strip().rstrip("/"),
        "api_key": m.api_key.strip(),
    }
    if not cfg["name"] or not cfg["base_url"]:
        return {"ok": False, "error": "模型名和 base_url 不能为空"}
    try:
        r = await engine.chat_once(cfg, "请只回复两个字：正常", "A", engine.DEFAULTS)
        return {"ok": True, "latency_ms": r["latency_ms"], "sample": r["content"][:60]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:400]}


def mask_key(k: str) -> str:
    if not k:
        return ""
    return k[:4] + "****" + k[-4:] if len(k) > 8 else "****"


def eval_view(row: dict) -> dict:
    done = row["done"] or 0
    return {
        **row,
        "accuracy": round(row["correct"] / done * 100, 2) if done else None,
        "avg_latency_ms": round(row["total_latency_ms"] / done) if done else None,
    }


# ---------- 模型管理 ----------

@app.get("/api/models")
def list_models():
    rows = db.query("SELECT * FROM models ORDER BY id DESC")
    for r in rows:
        r["api_key"] = mask_key(r["api_key"])
    return rows


@app.post("/api/models", status_code=201)
def create_model(m: ModelIn):
    if not m.name.strip() or not m.base_url.strip():
        raise HTTPException(400, "name 和 base_url 不能为空")
    mid = db.execute(
        "INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
        (m.name.strip(), m.base_url.strip().rstrip("/"), m.api_key.strip()),
    )
    sync_models_file()
    return {"id": mid}


@app.delete("/api/models/{mid}")
def delete_model(mid: int):
    db.execute("DELETE FROM models WHERE id=?", (mid,))
    sync_models_file()
    return {"ok": True}


# ---------- 题库 ----------

@app.get("/api/benchmarks")
def list_benchmarks():
    out = []
    for d in engine.list_datasets():
        meta = get_meta(d["id"])
        meta["count"] = d["count"]
        out.append(meta)
    return out


# ---------- 评测任务 ----------

@app.post("/api/evaluations", status_code=201)
async def create_evaluation(e: EvalIn):
    if not db.query_one("SELECT id FROM models WHERE id=?", (e.model_id,)):
        raise HTTPException(404, "模型不存在")
    try:
        items = engine.load_dataset(e.benchmark, e.limit or None)
    except FileNotFoundError:
        raise HTTPException(404, "题库不存在")
    max_tokens = min(max(e.max_tokens, 16), 32768)
    timeout_s = min(max(e.timeout_s, 5), 600)
    concurrency = min(max(e.concurrency, 1), 32)
    eid = db.execute(
        "INSERT INTO evaluations(model_id, benchmark, total, max_tokens, timeout_s, concurrency)"
        " VALUES(?,?,?,?,?,?)",
        (e.model_id, e.benchmark, len(items), max_tokens, timeout_s, concurrency),
    )
    asyncio.create_task(engine.run_evaluation(eid))
    return {"id": eid, "total": len(items)}


@app.get("/api/evaluations")
def list_evaluations():
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id ORDER BY e.id DESC LIMIT 100"
    )
    return [eval_view(r) for r in rows]


@app.get("/api/evaluations/{eid}")
def get_evaluation(eid: int):
    row = db.query_one(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.id=?", (eid,),
    )
    if not row:
        raise HTTPException(404, "任务不存在")
    return eval_view(row)


@app.get("/api/evaluations/{eid}/items")
def get_items(eid: int, offset: int = 0, limit: int = 50):
    return db.query(
        "SELECT idx, question, expected, predicted, raw_response, correct, latency_ms, error"
        " FROM eval_items WHERE eval_id=? ORDER BY idx LIMIT ? OFFSET ?",
        (eid, limit, offset),
    )


@app.delete("/api/evaluations/{eid}")
def delete_evaluation(eid: int):
    db.execute("DELETE FROM eval_items WHERE eval_id=?", (eid,))
    db.execute("DELETE FROM evaluations WHERE id=?", (eid,))
    engine.delete_items_file(eid)
    return {"ok": True}


# ---------- 排行榜 ----------

@app.get("/api/leaderboard")
def leaderboard():
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
    )
    best = {}
    for r in rows:
        acc = r["correct"] / r["done"] * 100
        key = (r["model_name"], r["benchmark"])
        if key not in best or acc > best[key]["accuracy"]:
            best[key] = {
                "model_name": r["model_name"],
                "benchmark": r["benchmark"],
                "benchmark_name": get_meta(r["benchmark"])["name"],
                "accuracy": round(acc, 2),
                "total": r["done"],
                "avg_latency_ms": round(r["total_latency_ms"] / r["done"]),
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
            }
    return sorted(best.values(), key=lambda x: -x["accuracy"])


# ---------- 前端 ----------

@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
