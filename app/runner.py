"""评测任务编排：并发跑题、进度跟踪、续跑、落库。"""

import asyncio
import json
import time

from . import db, qtypes, sandbox
from .benchmarks import get_meta
from .config import DEFAULTS, SANDBOX_CONCURRENCY
from .datasets import export_items, load_bfcl_answers, load_dataset
from .qtypes.bfcl import is_bfcl, item_question_text
from .qtypes.bfcl_multi_turn import is_multi_turn
from .qtypes.code_stdio import is_lcb_item


RUNNING: dict[int, asyncio.Task] = {}  # eval_id -> 运行中的评测任务，供「停止」功能 cancel


PROGRESS: dict[int, dict] = {}


CODE_PARAM_FLOOR = {"max_tokens": 8192, "timeout_s": 180}


LCB_PARAM_FLOOR = {"timeout_s": 300}


# 安全类基准的参数**下限**：思考型模型（DeepSeek V4 等）的 reasoning token 也算在
# max_tokens 里，给太小会让它**一个字的正文都没吐出来**（实测：512 时 20/20 题 content 为空，
# 只有一行「[输出被 max_tokens 截断]」）—— 而空回复会被裁判判成「没有有害内容」，
# 于是整场显示 100% 安全，什么都没测到。这是最危险的一类假成功。
SAFETY_PARAM_FLOOR = {"max_tokens": 1024}

# 喂给裁判的正文长度上限：**省裁判额度靠这个**，而不是靠压低被测模型的输出。
# 多数有害/拒绝的判定在前 2000 字符内就能看清楚，而裁判额度是稀缺资源。
SAFETY_JUDGE_CHARS = 2000


async def run_evaluation(eval_id: int, resume: bool = False):
    """跑一个评测任务。

    ``resume=True`` 表示**续跑**：跳过该任务里已经跑完的题，只补剩下的，
    计数器从数据库里已有的进度接着累加。用于进程被杀 / 服务重启后接着跑，
    不必把已经花掉的 API 调用全部重来。
    """
    task = asyncio.current_task()
    RUNNING[eval_id] = task
    PROGRESS[eval_id] = {"inflight": 0, "last_at": None}
    try:
        ev = db.query_one("SELECT * FROM evaluations WHERE id=?", (eval_id,))
        model_cfg = db.query_one("SELECT * FROM models WHERE id=?", (ev["model_id"],))
        conn = db.get_conn()
        benchmark = ev["benchmark"]
        fc = is_bfcl(benchmark)
        try:
            items = load_dataset(benchmark, ev["total"] or None)
            answers = load_bfcl_answers(benchmark) if fc else {}
        except Exception as e:  # noqa: BLE001
            conn.execute("UPDATE evaluations SET status='failed', error=? WHERE id=?", (str(e), eval_id))
            conn.commit()
            return

        # 续跑：idx 是题在题库里的原始下标，必须原样保留，否则明细会对不上
        done_idx = ({r["idx"] for r in db.query("SELECT idx FROM eval_items WHERE eval_id=?", (eval_id,))}
                    if resume else set())
        todo = [(i, it) for i, it in enumerate(items) if i not in done_idx]
        if resume:
            print(f"[续跑] 已完成 {len(done_idx)} 题，本次补跑 {len(todo)} 题", flush=True)

        # 数据集级上下文：是否 BFCL、是否 multi_turn、标准答案表 —— 这些每题都一样，
        # 算一次交给题型注册表，避免在每道题上重新判断。
        # 安全类基准还要带上裁判模型（整场共用，所以也放在 ctx 里）。
        need_judge = bool(get_meta(benchmark).get("requires_judge"))
        judge_cfg = None
        if need_judge:
            jid = ev.get("judge_model_id")
            judge_cfg = db.query_one("SELECT * FROM models WHERE id=?", (jid,)) if jid else None
            if not judge_cfg or judge_cfg.get("kind") != "judge":
                # 正常路径上创建评测时就拦掉了；这里兜底，免得跑到一半每题都失败
                conn.execute("UPDATE evaluations SET status='failed', error=? WHERE id=?",
                             ("这个基准需要裁判模型，但评测里没有配置有效的判别器"
                              "（judge_model_id 为空或该模型不是「判别器」）", eval_id))
                conn.commit()
                return
        qctx = qtypes.RunCtx(benchmark=benchmark, is_bfcl=fc,
                             is_multi_turn=is_multi_turn(benchmark) if fc else False,
                             answers=answers, judge_cfg=judge_cfg)

        # 代码题要真跑容器：先确认沙箱可用，否则会全军覆没，
        # 而且报错会是一堆看不懂的 docker 异常。这里提前失败，并给出人能看懂的提示。
        # 「哪些题型需要沙箱」由注册表声明，不在这里写死题型名单。
        has_code = any(qtypes.detect(qctx, it).needs_sandbox for i, it in todo)
        has_lcb = any(is_lcb_item(it) for i, it in todo)
        if has_code:
            docker_ok, docker_msg = await asyncio.to_thread(sandbox.docker_available)
            if not docker_ok:
                conn.execute("UPDATE evaluations SET status='failed', error=? WHERE id=?",
                             ("代码沙箱不可用：" + docker_msg, eval_id))
                conn.commit()
                return

        # 续跑时**不能**把 total 改成本次要跑的数量，否则进度条和最终判定都会错
        if resume:
            conn.execute("UPDATE evaluations SET status='running', error=NULL WHERE id=?", (eval_id,))
        else:
            conn.execute("UPDATE evaluations SET status='running', total=? WHERE id=?", (len(items), eval_id))
        conn.commit()

        params = {k: (ev.get(k) or d) for k, d in DEFAULTS.items()}
        if has_code:
            for k, floor in CODE_PARAM_FLOOR.items():
                params[k] = max(params[k], floor)
        if has_lcb:
            for k, floor in LCB_PARAM_FLOOR.items():
                params[k] = max(params[k], floor)
        if need_judge:
            for k, floor in SAFETY_PARAM_FLOOR.items():
                params[k] = max(params[k], floor)
        sem = asyncio.Semaphore(params["concurrency"])
        if resume:
            # 计数器接着上次的累计值走，避免续跑后统计被清零
            prev = ev
            counters = {"done": prev.get("done") or 0, "correct": prev.get("correct") or 0,
                        "failed": prev.get("failed") or 0, "ptok": prev.get("prompt_tokens") or 0,
                        "ctok": prev.get("completion_tokens") or 0, "lat": prev.get("total_latency_ms") or 0}
        else:
            counters = {"done": 0, "correct": 0, "failed": 0, "ptok": 0, "ctok": 0, "lat": 0}
        lock = asyncio.Lock()

        async def work(idx: int, item: dict):
            raw, predicted, expected, err, latency, ok = None, None, None, None, 0, 0
            # 「这题没得到有效结果」= 抛异常，或题型自己声明 failed。答错**不算**失败，
            # 所以这个标记必须单独记：答错也会写 err 当诊断，光靠 error 分不出来。
            item_failed = 0
            sbx_ms = 0  # 沙箱执行耗时，单列出来累加进 latency，便于看出哪步慢
            prog = PROGRESS.setdefault(eval_id, {"inflight": 0, "last_at": None})
            try:
                async with sem:
                    prog["inflight"] += 1  # 真正占住并发槽位才算「在跑」，排队的不算
                    # 题型分派统一走注册表（app/qtypes.py）：
                    # 以前这里是一串硬编码 if/elif，四种判分逻辑全部 inline，加题型就得改核心循环。
                    res = await qtypes.detect(qctx, item).runner(model_cfg, item, params, qctx)
                    ok, predicted, err, raw = res.ok, res.predicted, res.err, res.raw
                    expected, sbx_ms = res.expected, res.sbx_ms
                    resp = {"prompt_tokens": res.prompt_tokens,
                            "completion_tokens": res.completion_tokens,
                            "latency_ms": res.latency_ms}
                    latency = resp["latency_ms"] + sbx_ms
                async with lock:
                    counters["correct"] += ok
                    # 题型自己声明的「跑失败」（如裁判判分失败）也要计入 failed：
                    # 只靠异常计数的话，整场判分失败会被显示成「已完成」。
                    if getattr(res, "failed", False):
                        counters["failed"] += 1
                        item_failed = 1
                    counters["ptok"] += resp["prompt_tokens"]
                    counters["ctok"] += resp["completion_tokens"]
                    counters["lat"] += latency
            except Exception as e:  # noqa: BLE001
                err = str(e)[:500]
                ok = 0
                item_failed = 1
                async with lock:
                    counters["failed"] += 1
            async with lock:
                # 无论成功还是异常都会走到这里，所以在此收尾「在跑题数」最稳。
                # 用 max(0,...) 兜底：被「停止」取消时可能没走完计数配对。
                prog["inflight"] = max(0, prog["inflight"] - 1)
                prog["last_at"] = time.time()
                counters["done"] += 1
                conn.execute(
                    "INSERT INTO eval_items(eval_id, idx, question, expected, predicted, raw_response, correct, latency_ms, error, failed)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (eval_id, idx,
                     item_question_text(item, fc, is_multi_turn(benchmark))[:2000],
                     expected, predicted, (raw or "")[:4000] if raw else None, ok, latency, err,
                     item_failed),
                )
                conn.execute(
                    "UPDATE evaluations SET done=?, correct=?, failed=?, prompt_tokens=?,"
                    " completion_tokens=?, total_latency_ms=? WHERE id=?",
                    (counters["done"], counters["correct"], counters["failed"],
                     counters["ptok"], counters["ctok"], counters["lat"], eval_id),
                )
                conn.commit()

        await asyncio.gather(*(work(i, it) for i, it in todo))
        # items 始终是完整题库，所以这里用 len(items) 判定对全量与续跑都成立
        final = "done" if counters["failed"] < len(items) else "failed"
        if final == "done":
            err_msg = None
        elif need_judge:
            # 安全类基准全失败，最可能的原因不是被测模型，而是裁判：
            # 裁判模型看到有害内容很可能直接拒答（那就解析不出判定）。
            err_msg = ("所有题都判分失败。先检查裁判模型：它要读有害内容，"
                       "安全对齐强的模型常会拒答 —— 换一个裁判、或改用它更严格遵循指令的版本。"
                       "（被测模型的请求本身可能是正常的，可看逐题明细里的原始回复）")
        else:
            err_msg = "所有请求均失败，请检查 base_url / api_key / 模型名"
        conn.execute(
            "UPDATE evaluations SET status=?, error=?, finished_at=datetime('now','localtime') WHERE id=?",
            (final, err_msg, eval_id),
        )
        conn.commit()
        export_items(eval_id)
    except asyncio.CancelledError:
        # 「停止」：保留已完成题目的进度，标记为 stopped 并导出
        conn = db.get_conn()
        conn.execute(
            "UPDATE evaluations SET status='stopped', finished_at=datetime('now','localtime') WHERE id=?",
            (eval_id,),
        )
        conn.commit()
        try:
            export_items(eval_id)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        RUNNING.pop(eval_id, None)
        PROGRESS.pop(eval_id, None)
