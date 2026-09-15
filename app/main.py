import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import benchmarks as bm
from . import db, engine, sandbox, summary
from .benchmarks import CATEGORIES, META, get_meta

STATIC_DIR = Path(__file__).resolve().parent / "static"
MODELS_FILE = Path(__file__).resolve().parent.parent / "data" / "models.json"

# 下载状态：benchmark_id -> {"status": "idle"|"running"|"done"|"failed", "message": str}
DOWNLOAD_STATE: dict[str, dict] = {}


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


def reap_stale_running():
    """把残留的 running/pending 任务标成 stopped。

    评测任务是**进程内的 asyncio task**，不可能跨服务重启存活，所以服务启动时
    凡是还写着 running 的，必然是上次被强杀留下的僵尸（实测见过跑了 497/500
    一直挂着、模型都删了还显示「运行中」的）。不清掉的话界面上永远转圈，
    用户也分不清是真在跑还是卡死了。

    注意：若用 scripts/resume_eval.py 在**另一个进程**里续跑，而这边同时重启，
    这条续跑会被误标为 stopped —— 影响仅限状态显示，续跑结束仍会写成 done。
    """
    conn = db.get_conn()
    n = conn.execute("UPDATE evaluations SET status='stopped', finished_at=datetime('now','localtime')"
                     " WHERE status IN ('running','pending')").rowcount
    conn.commit()
    return n


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    import_models_file()
    reaped = reap_stale_running()
    if reaped:
        print(f"[启动] 清理了 {reaped} 个上次遗留的僵尸任务（标记为已停止）")
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
    # 实时进度：在跑几题、距上一题完成多久。
    # 前端靠它区分「难题慢」和「任务死了」—— 只看 done 的话两者长得一样。
    prog = engine.PROGRESS.get(row["id"]) or {}
    last_at = prog.get("last_at")
    return {
        **row,
        "accuracy": round(row["correct"] / done * 100, 2) if done else None,
        "avg_latency_ms": round(row["total_latency_ms"] / done) if done else None,
        "inflight": prog.get("inflight", 0),
        "last_done_ago_s": round(time.time() - last_at) if last_at else None,
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
    # 已下载题库（data 目录下实际存在，含 bfcl_v4 子目录）
    existing = {d["id"]: d["count"] for d in engine.list_datasets()}
    out = []
    # 先列出所有配置了元数据的 benchmark（含未下载的，前端据此显示下载按钮）
    for bid in META:
        meta = get_meta(bid)
        meta["count"] = existing.get(bid, 0)
        meta["downloaded"] = bid in existing
        meta["downloadable"] = bid in bm.DOWNLOADERS
        out.append(meta)
    # 再加上 data 目录里存在、但 META 未收录的自定义题库
    for d in engine.list_datasets():
        if d["id"] not in META:
            meta = get_meta(d["id"])
            meta["count"] = d["count"]
            meta["downloaded"] = True
            meta["downloadable"] = d["id"] in bm.DOWNLOADERS
            out.append(meta)
    return out


# ---------- 代码沙箱状态 ----------

@app.get("/api/sandbox/status")
async def sandbox_status():
    """给前端探测代码沙箱是否就绪（只有代码类基准才需要）。

    单独一个接口而不是塞进 /api/benchmarks：探测要调 docker，约 1 秒，
    不该拖慢每次打开页面都要请求的题库列表。
    """
    ok, msg = await asyncio.to_thread(sandbox.docker_available)
    return {"available": ok, "message": msg, "image": sandbox.IMAGE,
            "concurrency": engine.SANDBOX_CONCURRENCY}


# ---------- 题库下载 ----------

@app.post("/api/benchmarks/{benchmark_id}/download")
async def download_benchmark(benchmark_id: str):
    if benchmark_id not in bm.DOWNLOADERS:
        raise HTTPException(400, "该题库不支持自动下载")
    cur = DOWNLOAD_STATE.get(benchmark_id)
    if cur and cur["status"] == "running":
        return {"ok": True, "status": "running", "message": "正在下载中"}
    DOWNLOAD_STATE[benchmark_id] = {"status": "running", "message": "下载中…"}

    async def _run():
        try:
            ok, msg = await asyncio.to_thread(bm.download_one, benchmark_id)
            DOWNLOAD_STATE[benchmark_id] = {"status": "done" if ok else "failed", "message": msg}
        except Exception as e:  # noqa: BLE001
            DOWNLOAD_STATE[benchmark_id] = {"status": "failed", "message": str(e)[:300]}

    asyncio.create_task(_run())
    return {"ok": True, "status": "running", "message": "已开始下载"}


@app.get("/api/benchmarks/downloads")
def list_downloads():
    existing = {d["id"] for d in engine.list_datasets()}
    out = {}
    for bid in bm.DOWNLOADERS:
        state = DOWNLOAD_STATE.get(bid, {"status": "idle", "message": ""})
        out[bid] = {
            "status": state["status"],
            "message": state["message"],
            "downloaded": bid in existing,
        }
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
def list_evaluations(limit: int = 0, offset: int = 0):
    """评测任务列表（按 id 倒序，最新在前）。

    - limit=0：返回全部
    - limit>0：分页返回，并给出 total 供前端做分页
    返回 {"items": [...], "total": N}
    """
    total = db.query_one("SELECT COUNT(*) AS c FROM evaluations")["c"]
    sql = ("SELECT e.*, m.name AS model_name FROM evaluations e"
           " JOIN models m ON m.id=e.model_id ORDER BY e.id DESC")
    params: tuple = ()
    if limit and limit > 0:
        sql += " LIMIT ? OFFSET ?"
        params = (limit, max(offset, 0))
    rows = db.query(sql, params)
    return {"items": [eval_view(r) for r in rows], "total": total}


@app.get("/api/evaluations/compare")
def compare_evaluations(ids: str):
    """逐题对比两个评测任务（同一基准，按题目 idx 对齐）。

    用法：GET /api/evaluations/compare?ids=77,79

    返回两个任务各自的成绩、逐题答案，以及「分歧统计」：
    共同正确 / 共同错误 / 仅 A 对 / 仅 B 对 —— 后两项就是最值得看的「分歧题」。

    注意：本路由必须定义在 /api/evaluations/{eid} **之前**，否则 "compare"
    会被当成 eid 去解析（int 转换失败 → 422）。
    """
    try:
        pair = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "ids 需为逗号分隔的数字，如 ids=77,79")
    if len(pair) != 2:
        raise HTTPException(400, "对比需要恰好 2 个任务 id")
    a_id, b_id = pair
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.id IN (?,?)", (a_id, b_id),
    )
    found = {r["id"]: r for r in rows}
    if a_id not in found or b_id not in found:
        raise HTTPException(404, "任务不存在（可能已被删除）")
    a, b = found[a_id], found[b_id]
    if a["benchmark"] != b["benchmark"]:
        raise HTTPException(
            400, f"两个任务的基准不同（{a['benchmark']} vs {b['benchmark']}），逐题对比需要同一基准")

    def load_items(eid: int) -> dict:
        return {r["idx"]: r for r in db.query(
            "SELECT idx, question, expected, predicted, raw_response, correct, latency_ms, error"
            " FROM eval_items WHERE eval_id=?", (eid,))}

    ia, ib = load_items(a_id), load_items(b_id)
    common = sorted(set(ia) & set(ib))
    stats = {"both_correct": 0, "both_wrong": 0, "only_a": 0, "only_b": 0}
    items = []
    for i in common:
        ra, rb = ia[i], ib[i]
        ca, cb = bool(ra["correct"]), bool(rb["correct"])
        if ca and cb:
            stats["both_correct"] += 1
        elif not ca and not cb:
            stats["both_wrong"] += 1
        elif ca:
            stats["only_a"] += 1
        else:
            stats["only_b"] += 1
        items.append({
            "idx": i,
            "question": ra["question"],
            "expected": ra["expected"],
            "a": {"predicted": ra["predicted"], "correct": ca,
                  "raw_response": ra["raw_response"], "error": ra["error"],
                  "latency_ms": ra["latency_ms"]},
            "b": {"predicted": rb["predicted"], "correct": cb,
                  "raw_response": rb["raw_response"], "error": rb["error"],
                  "latency_ms": rb["latency_ms"]},
        })
    stats.update({
        "common": len(common),
        "a_count": len(ia), "b_count": len(ib),
        "a_only": len(set(ia) - set(ib)),   # 仅 A 跑了的题（如 limit 不同）
        "b_only": len(set(ib) - set(ia)),
    })
    return {"benchmark": a["benchmark"], "a": eval_view(a), "b": eval_view(b),
            "stats": stats, "items": items}


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


@app.post("/api/evaluations/{eid}/resume")
async def resume_evaluation(eid: int):
    """续跑一个没跑完的任务：只补做剩下的题，已完成的成果不重算。

    典型场景：进程被杀 / 服务重启，任务卡在 stopped，但已经烧掉的 API 调用
    不该白费。若沙箱不可用（代码类基准），引擎会在开跑前直接标 failed 并写明原因。
    """
    row = db.query_one("SELECT status, done, total, benchmark FROM evaluations WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "任务不存在")
    if eid in engine.RUNNING:
        raise HTTPException(400, "该任务正在运行中")
    if row["total"] and row["done"] >= row["total"]:
        raise HTTPException(400, "该任务已全部完成，无需续跑")
    if row["done"] == 0:
        raise HTTPException(400, "该任务还没有任何已完成题目，请直接新建评测")
    task = asyncio.create_task(engine.run_evaluation(eid, resume=True))
    engine.RUNNING[eid] = task
    return {"ok": True, "id": eid, "done": row["done"], "total": row["total"]}


@app.post("/api/evaluations/{eid}/stop")
def stop_evaluation(eid: int):
    task = engine.RUNNING.get(eid)
    if task:
        task.cancel()
        return {"ok": True}
    row = db.query_one("SELECT status FROM evaluations WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, "任务不存在")
    if row["status"] in ("running", "pending"):
        # 任务在跑但未注册（如服务重启后残留的僵尸任务），直接标记为停止
        db.execute(
            "UPDATE evaluations SET status='stopped', finished_at=datetime('now','localtime') WHERE id=?",
            (eid,),
        )
        return {"ok": True}
    return {"ok": False, "note": "任务已结束，无法停止"}


async def _delete_one_evaluation(eid: int):
    """删除单个评测：先停掉运行中的任务（避免继续烧 token / 写孤儿数据），再删明细、主记录与导出文件。"""
    task = engine.RUNNING.get(eid)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    db.execute("DELETE FROM eval_items WHERE eval_id=?", (eid,))
    db.execute("DELETE FROM evaluations WHERE id=?", (eid,))
    engine.delete_items_file(eid)


class BatchIdsIn(BaseModel):
    ids: list[int]


@app.post("/api/evaluations/batch-delete")
async def batch_delete_evaluations(body: BatchIdsIn):
    """批量删除评测任务（逐个走单删逻辑，含停止运行中的任务）。"""
    n = 0
    for eid in body.ids:
        await _delete_one_evaluation(eid)
        n += 1
    return {"ok": True, "deleted": n}


@app.delete("/api/evaluations/{eid}")
async def delete_evaluation(eid: int):
    await _delete_one_evaluation(eid)
    return {"ok": True}


# ---------- 排行榜 ----------

@app.get("/api/leaderboard")
def leaderboard():
    """排行榜：按分类分区，每区含「综合榜 + 各基准分项榜」。

    综合分 = 该模型在本分类下**已评测**基准的正确率算术平均；
    排序按「覆盖度优先，再按综合分」，避免只跑简单基准刷分。
    """
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
    )
    # 1) 每个 (模型, 基准) 取最高正确率
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

    # 2) 按分类归组
    grouped = {}   # category_id -> {benchmark: [item, ...]}
    cat_meta = {}  # category_id -> {name, color}
    for item in best.values():
        meta = get_meta(item["benchmark"])
        cid = meta["category_id"]
        grouped.setdefault(cid, {}).setdefault(item["benchmark"], []).append(item)
        cat_meta[cid] = {"name": meta["category"], "color": meta["category_color"]}

    # 3) 逐分类组装：分项榜（各自按正确率排序）+ 综合榜
    out = []
    for c in CATEGORIES:  # 保持 CATEGORIES 的展示顺序
        cid = c["id"]
        if cid not in grouped:
            continue
        boards = []
        model_scores = {}  # 模型 -> [该分类下各基准的正确率]
        for bid, items in grouped[cid].items():
            items.sort(key=lambda x: -x["accuracy"])
            boards.append({
                "benchmark": bid,
                "benchmark_name": get_meta(bid)["name"],
                "rows": items,
            })
            for it in items:
                model_scores.setdefault(it["model_name"], []).append(it["accuracy"])
        n_bench = len(boards)
        combined = [
            {
                "model_name": m,
                "avg_accuracy": round(sum(s) / len(s), 2),
                "covered": len(s),
                "total": n_bench,
            }
            for m, s in model_scores.items()
        ]
        combined.sort(key=lambda x: (-x["covered"], -x["avg_accuracy"]))
        # 分项榜：按该基准的最高分降序（有亮点的基准排前面）
        boards.sort(key=lambda b: -max(r["accuracy"] for r in b["rows"]))
        out.append({
            "id": cid,
            "name": cat_meta[cid]["name"],
            "color": cat_meta[cid]["color"],
            "n_benchmarks": n_bench,
            "combined": combined,
            "boards": boards,
        })
    return out


# ---------- 成绩总览（基准 × 模型 矩阵）----------

@app.get("/api/overview")
def overview():
    """成绩总览：矩阵「基准 × 模型」，供前端热力表展示。

    与排行榜的分工：排行榜回答「每个分类里谁最强」，这里回答
    「全局看，谁在哪一块强」——行是基准（按分类分组），列是模型，
    格子是该模型在该基准的最好成绩（多次测试取最高，与排行榜口径一致）。

    只把「有成绩的模型」作为列返回，避免出现整列空白；基准则全量返回
    （含暂无数据的，scores 为空 dict），是否隐藏空行交给前端过滤。
    """
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
    )
    best: dict = {}         # (model_id, benchmark) -> 最优成绩
    model_names: dict = {}  # model_id -> 模型名
    for r in rows:
        acc = r["correct"] / r["done"] * 100
        model_names[r["model_id"]] = r["model_name"]
        key = (r["model_id"], r["benchmark"])
        if key not in best or acc > best[key]["accuracy"]:
            best[key] = {
                "accuracy": round(acc, 2),
                "total": r["done"],
                "eval_id": r["id"],
                "avg_latency_ms": round(r["total_latency_ms"] / r["done"]),
            }

    # 基准 -> {model_id: 成绩}
    by_bench: dict = {}
    for (mid, bid), v in best.items():
        by_bench.setdefault(bid, {})[mid] = v

    groups = []
    for c in CATEGORIES:
        bms = []
        for bid, meta in META.items():
            if meta.get("category") != c["name"]:
                continue
            bms.append({
                "id": bid,
                "name": meta["name"],
                "scores": {str(mid): v for mid, v in by_bench.get(bid, {}).items()},
            })
        if not bms:
            continue
        # 有成绩的基准排前面：这样「只看有数据的」时不必跳过空行
        bms.sort(key=lambda b: (not b["scores"], b["name"]))
        groups.append({"id": c["id"], "name": c["name"],
                       "color": c["color"], "benchmarks": bms})

    return {
        "models": [{"id": mid, "name": nm}
                   for mid, nm in sorted(model_names.items(), key=lambda kv: kv[1])],
        "groups": groups,
    }


# ---------- AI 总结 ----------

# 生成状态：生成一次可能要几十秒到两分钟（思考型模型），所以走后台任务 + 前端轮询，
# 和题库下载用的是同一套模式，不阻塞 HTTP 请求。
# started_at / expected_s 是给前端画进度条用的：进度本身无法真实获知（模型是一次性返回的），
# 只能按历史耗时估一个预期值，前端据此做**模拟**进度。所以字段名叫 expected 而不是 progress。
SUMMARY_STATE: dict = {"status": "idle", "message": "", "model_id": None,
                       "model_name": None, "started_at": None, "expected_s": 45}


class SummaryIn(BaseModel):
    model_id: int
    force: bool = False   # 数据没变时是否也强制重新生成


def _expected_seconds(model_id: int) -> int:
    """按该模型历史上生成总结的耗时估个预期值，供前端模拟进度条。

    没有历史就兜底 45 秒。这只是个估计值 —— 进度条本来就是模拟的，
    所以刻意不叫 progress，也不承诺精确。
    """
    row = db.query_one("SELECT AVG(latency_ms) AS avg_ms FROM summaries"
                       " WHERE model_id=? AND error IS NULL", (model_id,))
    if row and row["avg_ms"]:
        return max(10, min(300, int(row["avg_ms"] / 1000 * 1.2)))
    return 45


def _summary_view(row: dict, fingerprint: str):
    """把落库的总结转成前端要的形态。"""
    if not row:
        return None
    try:
        content = json.loads(row["content"] or "{}")
    except ValueError:
        content = {}
    try:
        unverified = json.loads(row["unverified"] or "[]")
    except ValueError:
        unverified = []
    return {
        "id": row["id"],
        "content": content,
        "model_id": row["model_id"],
        "model_name": row["model_name"],
        "created_at": row["created_at"],
        "unverified": unverified,
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "latency_ms": row["latency_ms"],
        # 数据变了就是「过期」：前端据此提示该重新生成，而不是偷偷用旧结论
        "stale": row["fingerprint"] != fingerprint,
    }


@app.get("/api/summary/stats")
def summary_stats():
    """统计明细：矩阵、排名、显著性判定、数据问题。

    **完全由代码算出，不经过 AI** —— 前端把这份数据原样渲染出来，
    用户就能拿它核对 AI 写的那段总结。这是「总结可信」的前提。
    """
    stats = summary.collect()
    return {"fingerprint": summary.fingerprint(stats),
            "confidence": stats["confidence"],
            "comparable_benchmarks": stats["comparable_benchmarks"],
            "caveats": stats["caveats"],
            "rankings": [{"benchmark": b,
                          "name": get_meta(b)["name"],
                          "rows": stats["rankings"][b],
                          "comparisons": [c for c in stats["comparisons"] if c["benchmark"] == b]}
                         for b in stats["rankings"]],
            "models": stats["models"],
            "benchmarks": stats["benchmarks"]}


@app.get("/api/summary")
def get_summary():
    """返回**每个模型一条**总结条目。

    设计取舍：同一个模型重新生成会**覆盖**旧的（不留历史），
    不同模型各留一条 —— 这样才能横向比较「不同模型怎么看同一份数据」。
    若保留同一模型的历史，条目会越堆越多，反而看不出哪个是当前结论。
    """
    stats = summary.collect()
    fp = summary.fingerprint(stats)
    rows = db.query("SELECT * FROM summaries WHERE error IS NULL ORDER BY created_at DESC, id DESC")
    return {"state": SUMMARY_STATE, "fingerprint": fp,
            "entries": [_summary_view(r, fp) for r in rows]}


async def _run_summary(model_id: int, force: bool, fingerprint: str):
    global SUMMARY_STATE
    try:
        stats = summary.collect()
        if not force:
            cached = db.query_one(
                "SELECT id FROM summaries WHERE fingerprint=? AND model_id=? AND error IS NULL"
                " ORDER BY id DESC LIMIT 1", (fingerprint, model_id))
            if cached:
                SUMMARY_STATE = dict(SUMMARY_STATE, status="done",
                                     message="数据没有变化，直接用了上次生成的总结")
                return
        model_cfg = db.query_one("SELECT * FROM models WHERE id=?", (model_id,))
        if not model_cfg:
            raise RuntimeError("模型不存在")
        gen = await summary.generate(model_cfg, stats)
        unverified = summary.verify_numbers(gen["content"], stats)
        # 同一模型只留最新一条：先删旧的再插，保证「一个模型一个条目」
        db.execute("DELETE FROM summaries WHERE model_id=?", (model_id,))
        db.execute(
            "INSERT INTO summaries(fingerprint, model_id, model_name, content, unverified,"
            " prompt_tokens, completion_tokens, latency_ms) VALUES(?,?,?,?,?,?,?,?)",
            (fingerprint, model_id, model_cfg["name"],
             json.dumps(gen["content"], ensure_ascii=False),
             json.dumps(unverified, ensure_ascii=False),
             gen["prompt_tokens"], gen["completion_tokens"], gen["latency_ms"]))
        msg = "完成"
        if unverified:
            msg = f"完成，但有 {len(unverified)} 个数字在输入数据里回查不到，建议核对"
        SUMMARY_STATE = dict(SUMMARY_STATE, status="done", message=msg)
    except Exception as e:  # noqa: BLE001
        SUMMARY_STATE = dict(SUMMARY_STATE, status="failed", message=str(e)[:300])


@app.post("/api/summary")
async def create_summary(body: SummaryIn):
    global SUMMARY_STATE
    if SUMMARY_STATE.get("status") == "running":
        return {"ok": True, "status": "running", "message": "正在生成中"}
    model = db.query_one("SELECT id, name FROM models WHERE id=?", (body.model_id,))
    if not model:
        raise HTTPException(404, "模型不存在")
    fingerprint = summary.fingerprint(summary.collect())
    SUMMARY_STATE = {"status": "running", "message": "正在生成…",
                     "model_id": body.model_id, "model_name": model["name"],
                     "started_at": time.time(), "expected_s": _expected_seconds(body.model_id)}
    asyncio.create_task(_run_summary(body.model_id, body.force, fingerprint))
    return {"ok": True, "status": "running", "expected_s": SUMMARY_STATE["expected_s"]}


# ---------- 前端 ----------

@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
