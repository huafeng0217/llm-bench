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
from . import datasets, db, engine, i18n, sandbox, scoring, summary
from .benchmarks import CATEGORIES, FAMILIES, FAMILY_GROUPS, META, get_meta

STATIC_DIR = Path(__file__).resolve().parent / "static"
MODELS_FILE = Path(__file__).resolve().parent.parent / "data" / "models.json"

# 下载状态：benchmark_id -> {"status": "idle"|"running"|"done"|"failed", "message": str}
DOWNLOAD_STATE: dict[str, dict] = {}


def import_models_file():
    """启动时从本地文档 data/models.json 导入模型配置（含 key，仅存本机）。

    老文件里没有 ``kind`` 字段，一律按「被测模型」导入 —— 不给谁凭空安一个判别器的身份。
    """
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
        kind = it.get("kind") if it.get("kind") in MODEL_KINDS else "test"
        if not db.query_one(
            "SELECT id FROM models WHERE name=? AND base_url=?", (name, base_url)
        ):
            try:
                eb = valid_extra_body(str(it.get("extra_body") or ""))
            except HTTPException:
                eb = ""      # 文件里填错了就忽略这一项，不让启动挂掉
            db.execute(
                "INSERT INTO models(name, base_url, api_key, kind, extra_body) VALUES(?,?,?,?,?)",
                (name, base_url, str(it.get("api_key", "")).strip(), kind, eb),
            )


def sync_models_file():
    """把模型配置同步到本地文档，方便用户查看/备份/手动编辑。"""
    rows = db.query("SELECT name, base_url, api_key, kind, extra_body FROM models ORDER BY id")
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
    # 预热题库行数缓存：第一次算它要把 data/ 里所有题库读一遍（约 0.3 秒，
    # 其中 134MB 的 livecodebench 占大头）。放在后台线程里做，
    # 这样「打开页面」这一下也不会撞上这笔开销。
    # 存进全局变量是为了持住强引用 —— asyncio 只对任务持弱引用，
    # 不保存的话这个任务可能在跑完之前就被 GC 掉。
    global _PREWARM
    _PREWARM = asyncio.create_task(asyncio.to_thread(datasets.list_datasets))
    yield
    if _PREWARM and not _PREWARM.done():
        _PREWARM.cancel()


# 见 lifespan：持住预热任务的强引用，别让它被 GC
_PREWARM = None


app = FastAPI(title="LLM Bench", lifespan=lifespan)


@app.middleware("http")
async def _lang_middleware(request, call_next):
    """每次请求先定语言：X-Lang（前端切换后带的）→ Accept-Language（首次访问）→ 中文。

    放在中间件里而不是每个路由上加参数：语言要能被**很深**的代码用到
    （runner 的进度消息、题型的判定文案、判分错误），一路传参下去会污染所有签名。
    """
    i18n.set_lang(i18n.detect(request.headers.get("accept-language", ""),
                              request.headers.get("x-lang", "")))
    return await call_next(request)


# 模型用途：被测 / 判别器。**互斥**，不是可叠加的角色 ——
# 现在只解决「裁判不能被拿去被测」这一件事，等真出现「一个裁判复用到多个基准」
# 或「裁判兼任总结生成器」再升级成多角色。
MODEL_KINDS = {"test": "被测模型", "judge": "判别器"}


class ModelIn(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    kind: str = "test"
    # 额外请求参数（JSON 对象字符串，可选）。例：关掉 Qwen3 的思考 {"enable_thinking": false}
    extra_body: str = ""


class KindIn(BaseModel):
    kind: str
    confirm: bool = False   # 该模型已有历史评测时，必须显式确认才允许改类型


class EvalIn(BaseModel):
    model_id: int
    benchmark: str
    limit: int = 0  # 0 = 全部题目
    max_tokens: int = 2048   # 输出 token 预算（思考型模型需含推理 token）
    timeout_s: int = 90      # 单题请求超时（秒）
    concurrency: int = 8     # 并发请求数
    judge_model_id: int | None = None   # 裁判模型（只有安全类基准需要）


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
        # 任务级错误也是落库的中文原文，同样在响应时翻
        "error": i18n.t_stored(row["error"]),
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
        # 顺带给出该模型有多少条历史评测：前端在「改成判别器」时要拿它做二次确认，
        # 而改类型不会删掉历史成绩（数据是真的，出现在过就留着），所以要先让人知道。
        r["evaluations"] = db.query_one(
            "SELECT COUNT(*) AS n FROM evaluations WHERE model_id=?", (r["id"],))["n"]
    return rows


def valid_extra_body(raw: str) -> str:
    """校验 extra_body：必须是空、或一个 JSON 对象。返回规范化后的字符串。

    在**入口处**校验而不是等到调用时：填错的 JSON 会让每一次请求都失败，而错误信息
    要等跑起来才看到（甚至只表现为「任务全失败」）。这里直接拒绝，用户当场就知道。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise HTTPException(400, i18n.t("额外请求参数不是合法 JSON：{err}", err=e))
    if not isinstance(obj, dict):
        raise HTTPException(400, i18n.t('额外请求参数必须是 JSON 对象，例如 {"enable_thinking": false}'))
    return json.dumps(obj, ensure_ascii=False)


@app.post("/api/models", status_code=201)
def create_model(m: ModelIn):
    if not m.name.strip() or not m.base_url.strip():
        raise HTTPException(400, i18n.t("name 和 base_url 不能为空"))
    if m.kind not in MODEL_KINDS:
        raise HTTPException(400, i18n.t("kind 只能是 {kinds}", kinds=sorted(MODEL_KINDS)))
    mid = db.execute(
        "INSERT INTO models(name, base_url, api_key, kind, extra_body) VALUES(?,?,?,?,?)",
        (m.name.strip(), m.base_url.strip().rstrip("/"), m.api_key.strip(), m.kind,
         valid_extra_body(m.extra_body)),
    )
    sync_models_file()
    return {"id": mid, "kind": m.kind}


@app.patch("/api/models/{mid}")
def update_model_kind(mid: int, body: KindIn):
    """改模型用途（被测 ⇄ 判别器）。

    已有历史评测时要求 ``confirm=true`` 才放行 —— 不是禁止，而是**先让人看见**：
    改成判别器之后它就不能再发起新评测了，但以前跑出来的成绩仍然留在榜上
    （数据是真的，不该因为角色变了就消失）。
    """
    if body.kind not in MODEL_KINDS:
        raise HTTPException(400, i18n.t("kind 只能是 {kinds}", kinds=sorted(MODEL_KINDS)))
    row = db.query_one("SELECT id, name, kind FROM models WHERE id=?", (mid,))
    if not row:
        raise HTTPException(404, i18n.t("模型不存在"))
    n = db.query_one("SELECT COUNT(*) AS n FROM evaluations WHERE model_id=?", (mid,))["n"]
    if row["kind"] == body.kind:
        return {"ok": True, "kind": body.kind, "evaluations": n, "changed": False}
    # 只在**收窄用途**时打扰用户：改成判别器意味着以后不能再评测它，所以要确认；
    # 改回被测是放开限制，没什么可确认的。
    if body.kind == "judge" and n and not body.confirm:
        raise HTTPException(
            409,
            f"{row['name']} 已有 {n} 条历史评测。改成「判别器」后不能再发起新评测，"
            f"但已有的成绩仍然保留在排行榜上（数据是真的，不该因为角色变了就消失）。确认要改吗？",
        )
    db.execute("UPDATE models SET kind=? WHERE id=?", (body.kind, mid))
    return {"ok": True, "kind": body.kind, "evaluations": n, "changed": True}


@app.delete("/api/models/{mid}")
def delete_model(mid: int):
    db.execute("DELETE FROM models WHERE id=?", (mid,))
    sync_models_file()
    return {"ok": True}


# ---------- 题库 ----------

@app.get("/api/benchmarks")
def list_benchmarks():
    # 已下载题库（data 目录下实际存在，含 bfcl_v4 子目录）
    # 只调一次：list_datasets() 要扫整个 data/ 目录并数每份题库的行数（有大文件，代价不低），
    # 之前这里调了两次（下面「自定义题库」那段又调一次），等于把这份开销翻倍 ——
    # 而它正是「切换分类卡一下」的全部原因，所以行数也做了 mtime 缓存（见 datasets.count_lines）。
    datasets_now = datasets.list_datasets()
    existing = {d["id"]: d["count"] for d in datasets_now}
    out = []
    # 先列出所有配置了元数据的 benchmark（含未下载的，前端据此显示下载按钮）
    for bid in META:
        meta = get_meta(bid)
        meta["count"] = existing.get(bid, 0)
        meta["downloaded"] = bid in existing
        meta["downloadable"] = bid in bm.DOWNLOADERS
        out.append(meta)
    # 再加上 data 目录里存在、但 META 未收录的自定义题库
    for d in datasets_now:
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
        raise HTTPException(400, i18n.t("该题库不支持自动下载"))
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


@app.post("/api/families/{family_id}/download")
async def download_family(family_id: str):
    """下载一个家族的全部子集（如 BFCL v4 的 16 个）。

    为什么需要：家族在界面上是一张卡，逐个点子集的下载按钮太笨；
    子集之间是同一份数据源，串行下载还能共用同一份工具文档（multi_turn 需要）。
    每个子集仍有自己的进度状态，前端按子集显示。
    """
    subsets = [e.id for e in bm.ENTRIES if e.family == family_id]
    if not subsets:
        raise HTTPException(404, i18n.t("没有这个家族: {fid}", fid=family_id))
    todo = [s for s in subsets if s in bm.DOWNLOADERS
            and not (DOWNLOAD_STATE.get(s) or {}).get("status") == "running"]
    if not todo:
        return {"ok": True, "status": "running", "message": "全部子集都在下载中或已开始"}
    for s in todo:
        DOWNLOAD_STATE[s] = {"status": "running", "message": "排队中…"}

    async def _run():
        for s in todo:
            DOWNLOAD_STATE[s] = {"status": "running", "message": "下载中…"}
            try:
                ok, msg = await asyncio.to_thread(bm.download_one, s)
                DOWNLOAD_STATE[s] = {"status": "done" if ok else "failed", "message": msg}
            except Exception as e:  # noqa: BLE001
                DOWNLOAD_STATE[s] = {"status": "failed", "message": str(e)[:300]}

    asyncio.create_task(_run())
    return {"ok": True, "status": "running", "count": len(todo),
            "message": f"已开始下载 {len(todo)} 个子集"}


@app.get("/api/benchmarks/downloads")
def list_downloads():
    # 只要 id（下没下过），用 list_dataset_ids：它不数题量，
    # 不会为了一份 id 列表去把几百 MB 题库整个读一遍。
    existing = datasets.list_dataset_ids()
    out = {}
    for bid in bm.DOWNLOADERS:
        state = DOWNLOAD_STATE.get(bid, {"status": "idle", "message": ""})
        out[bid] = {
            "status": state["status"],
            # 共享状态里存的是中文原文（谁触发的下载不一定等于谁在看），响应时按当前语言翻
            "message": i18n.t_stored(state["message"]),
            "downloaded": bid in existing,
        }
    return out


# ---------- 评测任务 ----------

@app.post("/api/evaluations", status_code=201)
async def create_evaluation(e: EvalIn):
    m = db.query_one("SELECT id, name, kind FROM models WHERE id=?", (e.model_id,))
    if not m:
        raise HTTPException(404, i18n.t("模型不存在"))
    # 判别器不能被评测：前端不会把它列进下拉，但直接调 API 也得拦住
    # （不信任 UI 的约定，和后端沙箱自检是同一个思路）。
    if m["kind"] == "judge":
        raise HTTPException(
            400,
            f"{m['name']} 是「判别器」，不能作为被测模型。"
            f"判别器只用于给安全类基准判分；如果确实要测它，请先在模型管理里把它改回「被测模型」。",
        )
    try:
        items = engine.load_dataset(e.benchmark, e.limit or None)
    except FileNotFoundError:
        raise HTTPException(404, i18n.t("题库不存在"))

    # 安全类基准要选裁判。三条约束都在这儿拦住（前端也会拦，但不信任 UI）：
    #   ① 必须选；② 必须是 kind='judge' 的模型；③ 不能是被测模型自己（自偏袒）。
    judge_id = None
    if get_meta(e.benchmark).get("requires_judge"):
        if not e.judge_model_id:
            raise HTTPException(400, i18n.t("这个基准由裁判模型判分，请先选择一个「判别器」再开始"))
        j = db.query_one("SELECT id, name, kind FROM models WHERE id=?", (e.judge_model_id,))
        if not j:
            raise HTTPException(404, i18n.t("裁判模型不存在"))
        if j["kind"] != "judge":
            raise HTTPException(
                400, f"{j['name']} 的用途是「被测模型」，不能当裁判。"
                     f"请先在模型管理里把要当裁判的模型设为「判别器」。")
        if e.judge_model_id == e.model_id:
            raise HTTPException(400, i18n.t("裁判不能是被测模型自己 —— 自己判自己会让分数失去意义"))
        judge_id = e.judge_model_id
    max_tokens = min(max(e.max_tokens, 16), 32768)
    timeout_s = min(max(e.timeout_s, 5), 600)
    concurrency = min(max(e.concurrency, 1), 32)
    eid = db.execute(
        "INSERT INTO evaluations(model_id, benchmark, total, max_tokens, timeout_s, concurrency,"
        " judge_model_id) VALUES(?,?,?,?,?,?,?)",
        (e.model_id, e.benchmark, len(items), max_tokens, timeout_s, concurrency, judge_id),
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
    # LEFT JOIN 裁判模型：安全类基准的任务列表要显示「谁判的」（换了裁判分数不可比，
    # 所以裁判名必须和分数一起出现在列表里，而不是藏进详情）。
    sql = ("SELECT e.*, m.name AS model_name, j.name AS judge_name FROM evaluations e"
           " JOIN models m ON m.id=e.model_id"
           " LEFT JOIN models j ON j.id=e.judge_model_id ORDER BY e.id DESC")
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
        raise HTTPException(400, i18n.t("ids 需为逗号分隔的数字，如 ids=77,79"))
    if len(pair) != 2:
        raise HTTPException(400, i18n.t("对比需要恰好 2 个任务 id"))
    a_id, b_id = pair
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.id IN (?,?)", (a_id, b_id),
    )
    found = {r["id"]: r for r in rows}
    if a_id not in found or b_id not in found:
        raise HTTPException(404, i18n.t("任务不存在（可能已被删除）"))
    a, b = found[a_id], found[b_id]
    if a["benchmark"] != b["benchmark"]:
        raise HTTPException(400, i18n.t("两个任务的基准不同（{a} vs {b}），逐题对比需要同一基准",
                                      a=a["benchmark"], b=b["benchmark"]))

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
            "expected": i18n.t_stored(ra["expected"]),
            "a": {"predicted": i18n.t_stored(ra["predicted"]), "correct": ca,
                  "raw_response": ra["raw_response"], "error": i18n.t_stored(ra["error"]),
                  "latency_ms": ra["latency_ms"]},
            "b": {"predicted": i18n.t_stored(rb["predicted"]), "correct": cb,
                  "raw_response": rb["raw_response"], "error": i18n.t_stored(rb["error"]),
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
        raise HTTPException(404, i18n.t("任务不存在"))
    return eval_view(row)


@app.get("/api/evaluations/{eid}/items")
def get_items(eid: int, offset: int = 0, limit: int = 50):
    # failed 一并给前端：明细里要区分「没得到有效结果」（failed=1）和「答错」（failed=0）。
    # 答错也会写 error 当诊断（代码题的 traceback 就是），光看 error 分不出来。
    # 老数据这一列是 NULL —— 前端据此退回老判据（有诊断文本就算失败）。
    rows = db.query(
        "SELECT idx, question, expected, predicted, raw_response, correct, latency_ms, error, failed"
        " FROM eval_items WHERE eval_id=? ORDER BY idx LIMIT ? OFFSET ?",
        (eid, limit, offset),
    )
    # 库里存的是**中文原文**（语言是"看的人"的选择，不是跑评测那天的选择），
    # 所以响应时按当前语言翻一遍 —— 好处是**历史数据也能翻**，不需要迁移或重跑。
    # 只翻 expected/predicted/error 这类程序生成的短文案；question 与 raw_response 是原文，不动。
    if i18n.get_lang() != "zh":
        for r in rows:
            for k in ("expected", "predicted", "error"):
                r[k] = i18n.t_stored(r[k])
    return rows


@app.post("/api/evaluations/{eid}/resume")
async def resume_evaluation(eid: int):
    """续跑一个没跑完的任务：只补做剩下的题，已完成的成果不重算。

    典型场景：进程被杀 / 服务重启，任务卡在 stopped，但已经烧掉的 API 调用
    不该白费。若沙箱不可用（代码类基准），引擎会在开跑前直接标 failed 并写明原因。
    """
    row = db.query_one("SELECT status, done, total, benchmark FROM evaluations WHERE id=?", (eid,))
    if not row:
        raise HTTPException(404, i18n.t("任务不存在"))
    if eid in engine.RUNNING:
        raise HTTPException(400, i18n.t("该任务正在运行中"))
    if row["total"] and row["done"] >= row["total"]:
        raise HTTPException(400, i18n.t("该任务已全部完成，无需续跑"))
    if row["done"] == 0:
        raise HTTPException(400, i18n.t("该任务还没有任何已完成题目，请直接新建评测"))
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
        raise HTTPException(404, i18n.t("任务不存在"))
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

    综合分 = 该模型在本分类下**已评测**项目的正确率算术平均（家族算作**一项**，
    用它的官方加权总分参与，而不是让 16 个子集各投一票）；
    排序按「有效覆盖度优先，再按综合分」，避免只跑简单基准刷分。

    家族（如 BFCL v4）额外返回 ``families``：官方加权总分 + 每组得分 + 权重覆盖率，
    供前端折叠展示（见 app/benchmarks/bfcl.py 的 FAMILY_DEFS）。
    """
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
    )
    # 1) 每个 (模型, 基准) 取「代表成绩」——
    #    口径来自 app/scoring，与成绩总览、AI 总结共用：
    #    **优先完整评测**，只有部分评测时才用它并标记 partial。
    #    以前这里只比正确率、完全不看跑了多少题，于是「跑 6 题全对」会盖过
    #    「跑 164 题 99.39%」而成为榜上成绩（实测踩过：HumanEval 的 6 题冒烟测试必须手动删）。
    runs: dict = {}
    for r in rows:
        runs.setdefault((r["model_name"], r["benchmark"]), []).append(r)
    full_cache: dict = {}

    def full_of(bid: str) -> int:
        if bid not in full_cache:
            full_cache[bid] = scoring.full_count(bid)
        return full_cache[bid]

    best = {}
    for (mname, bid), rs in runs.items():
        pick = scoring.pick_best(rs, full_of(bid))
        b = pick.best
        best[(mname, bid)] = {
            "model_name": mname,
            "benchmark": bid,
            "benchmark_name": get_meta(bid)["name"],
            "accuracy": round(scoring.accuracy_of(b), 2),
            "total": b["done"],
            "full_count": full_of(bid),
            "partial": pick.partial,
            "avg_latency_ms": round(b["total_latency_ms"] / b["done"]),
            "prompt_tokens": b["prompt_tokens"],
            "completion_tokens": b["completion_tokens"],
        }

    # 2) 按分类归组
    grouped = {}   # category_id -> {benchmark: [item, ...]}
    cat_meta = {}  # category_id -> {name, color}
    for item in best.values():
        meta = get_meta(item["benchmark"])
        cid = meta["category_id"]
        grouped.setdefault(cid, {}).setdefault(item["benchmark"], []).append(item)
        cat_meta[cid] = {"name": meta["category"], "color": meta["category_color"]}

    # 3) 逐分类组装：分项榜（各自按正确率排序）+ 家族块 + 综合榜
    out = []
    for c in CATEGORIES:  # 保持 CATEGORIES 的展示顺序
        cid = c["id"]
        if cid not in grouped:
            continue
        boards = []
        # 综合得分只平均**完整评测**：部分评测的分数不该和完整成绩等权地算进平均分，
        # 但也不能把这项藏起来 —— 所以单独数出几项是部分评测（partial），交前端提示。
        item_scores = {}    # 模型 -> [参与综合分的分数]（独立基准逐个计，家族算一项）
        item_cover = {}     # 模型 -> 有效覆盖量（独立基准 1.0 / 家族 = 已跑组权重之和）
        model_fams = {}     # 模型 -> [家族明细]（综合榜那行要显示「权重覆盖 60%」这类信息）
        model_cov = {}      # 模型 -> 该分类下跑过的基准数（含部分，供显示）
        model_partial = {}  # 模型 -> 其中「只有部分评测」的基准数
        # 家族：先把每个模型在该家族各子集上的代表成绩收集起来，再按官方口径加权
        fam_runs = {}       # family_id -> {model_name: {subset_id: item}}
        fam_boards = []     # 家族的 boards（子集明细，折叠块里用）
        for bid, items in grouped[cid].items():
            # 部分评测排在完整评测后面：它不该和完整成绩平起平坐（但不隐藏）
            items.sort(key=lambda x: (x["partial"], -x["accuracy"]))
            meta = get_meta(bid)
            board = {
                "benchmark": bid,
                "benchmark_name": meta["name"],
                "rows": items,
                "family": meta.get("family", ""),
                "group": meta.get("group", ""),
                "group_name": meta.get("group_name", ""),
                "group_weight": meta.get("group_weight"),
            }
            if meta.get("family"):
                fam_boards.append(board)
                for it in items:
                    fam_runs.setdefault(meta["family"], {}).setdefault(
                        it["model_name"], {})[bid] = it
            else:
                boards.append(board)
                for it in items:
                    m = it["model_name"]
                    model_cov[m] = model_cov.get(m, 0) + 1
                    if it["partial"]:
                        model_partial[m] = model_partial.get(m, 0) + 1
                    else:
                        item_scores.setdefault(m, []).append(it["accuracy"])
                        item_cover[m] = item_cover.get(m, 0.0) + 1.0

        # 家族折叠块：官方加权总分（缺组按已跑组归一化，并显式给出权重覆盖）
        families = []
        for fid, per_model in fam_runs.items():
            groups_def = FAMILY_GROUPS.get(fid, [])
            group_rank = {g["id"]: g["order"] for g in groups_def}
            fam_rows = []
            for m, by_subset in per_model.items():
                comp = scoring.family_composite(
                    {bid: it["accuracy"] for bid, it in by_subset.items()}, groups_def)
                if not comp:
                    continue
                comp["model_name"] = m
                fam_rows.append(comp)
                # 家族在综合榜里算「一项」：分数用官方加权总分，
                # 覆盖量用已跑组的官方权重之和（跑满 4 组 = 0.60；只跑 Non-Live = 0.10）
                item_scores.setdefault(m, []).append(comp["score"])
                item_cover[m] = item_cover.get(m, 0.0) + comp["weight_covered"]
                model_cov[m] = model_cov.get(m, 0) + 1
                model_fams.setdefault(m, []).append({
                    "id": fid, "score": comp["score"],
                    "weight_covered": comp["weight_covered"],
                    "groups_covered": comp["groups_covered"],
                    "groups_total": comp["groups_total"],
                    "subsets_run": comp["subsets_run"],
                    "subsets_total": comp["subsets_total"],
                    "partial": comp["partial"] or comp["incomplete"],
                })
                if comp["partial"] or comp["incomplete"]:
                    model_partial[m] = model_partial.get(m, 0) + 1
            # 排序同综合榜：先看权重覆盖（跑得全的在前），再看分
            fam_rows.sort(key=lambda x: (-x["weight_covered"], -x["score"]))
            families.append({
                "id": fid,
                "name": fid,
                "note": FAMILIES[fid].note if fid in FAMILIES else "",
                "source": FAMILIES[fid].source if fid in FAMILIES else "",
                "groups": groups_def,
                "rows": fam_rows,
                # 子集明细按官方分组顺序排（折叠块里就是按这个顺序列出来的）
                "boards": sorted(fam_boards,
                                 key=lambda b: (group_rank.get(b["group"], 0), b["benchmark"])),
            })
        n_bench = len(boards) + len(families)   # 家族算一项（不是 16 项）
        combined = []
        for m, cov in model_cov.items():
            s = item_scores.get(m, [])
            combined.append({
                "model_name": m,
                "avg_accuracy": round(sum(s) / len(s), 2) if s else 0,
                "covered": cov,
                "total": n_bench,
                # 排序真正用的是「有效覆盖量」：独立基准 1.0，家族按官方权重覆盖率折算，
                # 否则「只跑了一个 easy 子集」会和「跑满四组」被同等看待。
                "coverage": round(item_cover.get(m, 0.0), 4),
                "partial": model_partial.get(m, 0),
                "families": model_fams.get(m, []),
            })
        combined.sort(key=lambda x: (-x["coverage"], -x["avg_accuracy"], -x["covered"]))
        # 分项榜：按该基准的最高分降序（有亮点的基准排前面）。
        # 算最高分时忽略部分评测 —— 否则「6 题满分」会把一个基准顶到最前面。
        boards.sort(key=lambda b: -max((r["accuracy"] for r in b["rows"] if not r["partial"]),
                                       default=max(r["accuracy"] for r in b["rows"])))
        out.append({
            "id": cid,
            "name": cat_meta[cid]["name"],
            "color": cat_meta[cid]["color"],
            "n_benchmarks": n_bench,
            "combined": combined,
            "boards": boards,
            "families": families,
        })
    return out


# ---------- 成绩总览（基准 × 模型 矩阵）----------

@app.get("/api/overview")
def overview():
    """成绩总览：矩阵「基准 × 模型」，供前端热力表展示。

    与排行榜的分工：排行榜回答「每个分类里谁最强」，这里回答
    「全局看，谁在哪一块强」——行是基准（按分类分组），列是模型，
    格子是该模型在该基准的代表成绩：**优先完整评测**，多次完整评测取最高，
    没有完整评测时才退回部分评测并标记 partial（口径见 app/scoring，与排行榜一致）。

    只把「有成绩的模型」作为列返回，避免出现整列空白；基准则全量返回
    （含暂无数据的，scores 为空 dict），是否隐藏空行交给前端过滤。
    """
    rows = db.query(
        "SELECT e.*, m.name AS model_name FROM evaluations e"
        " JOIN models m ON m.id=e.model_id WHERE e.status='done' AND e.done>0"
    )
    runs: dict = {}         # (model_id, benchmark) -> 该模型在该基准的所有 done 评测
    model_names: dict = {}  # model_id -> 模型名
    for r in rows:
        model_names[r["model_id"]] = r["model_name"]
        runs.setdefault((r["model_id"], r["benchmark"]), []).append(r)

    # 格子口径同样来自 app/scoring：优先完整评测，只有部分评测时标记 partial。
    # 前端据此把部分评测排除在「该基准最高分」高亮之外，并单独打标。
    full_cache: dict = {}
    best: dict = {}         # (model_id, benchmark) -> 代表成绩
    for (mid, bid), rs in runs.items():
        if bid not in full_cache:
            full_cache[bid] = scoring.full_count(bid)
        pick = scoring.pick_best(rs, full_cache[bid])
        b = pick.best
        best[(mid, bid)] = {
            "accuracy": round(scoring.accuracy_of(b), 2),
            "total": b["done"],
            "full_count": full_cache[bid],
            "partial": pick.partial,
            "eval_id": b["id"],
            "avg_latency_ms": round(b["total_latency_ms"] / b["done"]),
        }

    # 基准 -> {model_id: 成绩}
    by_bench: dict = {}
    for (mid, bid), v in best.items():
        by_bench.setdefault(bid, {})[mid] = v

    groups = []
    # 家族内的子集按**官方分组顺序**排，而不是按名字排 ——
    # 否则「Live 单函数」会因为字母序跑到「多轮对话」后面，读起来毫无头绪。
    # 顺带把组名与官方权重带出来（卡片/行标题上要显示），META 里没有这两项。
    subset_group = {}
    for fid, gs in FAMILY_GROUPS.items():
        for g in gs:
            for sid in g["subsets"]:
                subset_group[sid] = {"order": g["order"], "name": g["name"],
                                     "weight": g["weight"], "family": fid}
    for c in CATEGORIES:
        bms = []
        for bid, meta in META.items():
            if meta.get("category") != c["name"]:
                continue
            gi = subset_group.get(bid, {})
            bms.append({
                "id": bid,
                "name": meta["name"],
                "family": gi.get("family", ""),
                "group_name": gi.get("name", ""),
                "group_weight": gi.get("weight"),
                "scores": {str(mid): v for mid, v in by_bench.get(bid, {}).items()},
            })
        if not bms:
            continue
        # 有成绩的基准排前面：这样「只看有数据的」时不必跳过空行
        bms.sort(key=lambda b: (not b["scores"], b["family"],
                                subset_group.get(b["id"], {}).get("order", 0), b["name"]))
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
SUMMARY_STATE: dict = {"status": "idle", "message": "", "model_id": None, "cached": False,
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
    return {"state": {**SUMMARY_STATE, "message": i18n.t_stored(SUMMARY_STATE.get("message") or "")},
            "fingerprint": fp,
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
                SUMMARY_STATE = dict(SUMMARY_STATE, status="done", cached=True,
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
        raise HTTPException(404, i18n.t("模型不存在"))
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
