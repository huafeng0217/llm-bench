"""评测引擎：加载题库 -> 并发调用 OpenAI 兼容 API -> 抽取答案 -> 判分。

支持 mock://local 作为 base_url，无需真实 API key 即可端到端演示。
"""
import asyncio
import hashlib
import json
import random
import re
import time
from pathlib import Path

from openai import AsyncOpenAI

from . import db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESULTS_DIR = DATA_DIR / "results"
CHOICES = ["A", "B", "C", "D"]
DEFAULTS = {"max_tokens": 2048, "timeout_s": 90, "concurrency": 8}
MAX_RETRIES = 3


def export_items(eval_id: int) -> Path:
    """把一次评测的逐题明细（含模型原始输出）导出为本地 JSONL 文件。"""
    rows = db.query(
        "SELECT idx, question, expected, predicted, raw_response, correct,"
        " latency_ms, error FROM eval_items WHERE eval_id=? ORDER BY idx",
        (eval_id,),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval_{eval_id}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def delete_items_file(eval_id: int):
    path = RESULTS_DIR / f"eval_{eval_id}.jsonl"
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        # 某些受控运行环境禁止直接删除文件，退而求其次移入 .trash 目录
        trash = RESULTS_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        path.replace(trash / path.name)


# ---------- 题库 ----------

def list_datasets():
    """扫描 data 目录下所有 .jsonl 题库。"""
    out = []
    for p in sorted(DATA_DIR.glob("*.jsonl")):
        n = sum(1 for _ in open(p, encoding="utf-8"))
        out.append({"id": p.stem, "name": p.stem, "count": n})
    return out


def load_dataset(benchmark: str, limit: int | None = None):
    path = DATA_DIR / f"{benchmark}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"题库不存在: {benchmark}")
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    if limit and limit > 0:
        items = items[:limit]
    return items


# ---------- prompt 与判分 ----------

def build_prompt(item: dict) -> str:
    lines = [
        "以下是一道单项选择题。请只回答正确选项的字母（A/B/C/D），不要输出任何其他内容。",
        "",
        f"题目：{item['question']}",
    ]
    for c in CHOICES:
        if item.get(c):
            lines.append(f"{c}. {item[c]}")
    lines += ["", "答案："]
    return "\n".join(lines)


def extract_answer(text: str | None) -> str | None:
    """从模型回复中抽取 A/B/C/D。先看显式'答案'，再找首个独立字母。"""
    if not text:
        return None
    head = text.strip()[:120]
    m = re.search(r"(?:答案是|答案[:：为]|选项是?|answer is|answer[:：])\s*\(?([A-D])\b", head, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<![A-Za-z])([A-D])(?![A-Za-z])", head)
    return m.group(1) if m else None


# ---------- 模型调用 ----------

def _mock_response(prompt: str, expected: str):
    """本地模拟模型：约 70% 答对（按题目 hash 确定），用于无 key 演示。"""
    time.sleep(random.uniform(0.03, 0.12))
    h = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
    correct = h % 10 < 7
    if correct:
        ans = expected
    else:
        wrong = [c for c in CHOICES if c != expected]
        ans = wrong[h % len(wrong)]
    return {
        "content": f"答案：{ans}",
        "prompt_tokens": len(prompt) // 2,
        "completion_tokens": 4,
        "latency_ms": 0,
    }


async def chat_once(model_cfg: dict, prompt: str, expected: str, params: dict):
    if model_cfg["base_url"].startswith("mock://"):
        return _mock_response(prompt, expected)
    client = AsyncOpenAI(
        base_url=model_cfg["base_url"],
        api_key=model_cfg["api_key"] or "EMPTY",
        timeout=params["timeout_s"],
        max_retries=0,
    )
    t0 = time.time()
    r = await client.chat.completions.create(
        model=model_cfg["name"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=params["max_tokens"],  # 思考型模型的 reasoning tokens 也占额度，预算要给足
    )
    latency = int((time.time() - t0) * 1000)
    usage = r.usage
    content = (r.choices[0].message.content or "").strip()
    finish = getattr(r.choices[0], "finish_reason", None)
    if finish == "length":
        content += "\n[输出被 max_tokens 截断]"
    return {
        "content": content,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
        "latency_ms": latency,
    }


async def chat_with_retry(model_cfg: dict, prompt: str, expected: str, params: dict):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return await chat_once(model_cfg, prompt, expected, params)
        except Exception as e:  # noqa: BLE001
            last_err = e
            await asyncio.sleep(2 ** attempt)
    raise last_err


# ---------- 评测任务 ----------

async def run_evaluation(eval_id: int):
    ev = db.query_one("SELECT * FROM evaluations WHERE id=?", (eval_id,))
    model_cfg = db.query_one("SELECT * FROM models WHERE id=?", (ev["model_id"],))
    conn = db.get_conn()
    try:
        items = load_dataset(ev["benchmark"], ev["total"] or None)
    except Exception as e:  # noqa: BLE001
        conn.execute("UPDATE evaluations SET status='failed', error=? WHERE id=?", (str(e), eval_id))
        conn.commit()
        return

    conn.execute("UPDATE evaluations SET status='running', total=? WHERE id=?", (len(items), eval_id))
    conn.commit()

    params = {k: (ev.get(k) or d) for k, d in DEFAULTS.items()}
    sem = asyncio.Semaphore(params["concurrency"])
    counters = {"done": 0, "correct": 0, "failed": 0, "ptok": 0, "ctok": 0, "lat": 0}
    lock = asyncio.Lock()

    async def work(idx: int, item: dict):
        prompt = build_prompt(item)
        expected = str(item.get("answer", "")).strip().upper()
        raw, predicted, err, latency = None, None, None, 0
        try:
            async with sem:
                resp = await chat_with_retry(model_cfg, prompt, expected, params)
            raw = resp["content"]
            predicted = extract_answer(raw)
            latency = resp["latency_ms"]
            ok = 1 if predicted == expected else 0
            async with lock:
                counters["correct"] += ok
                counters["ptok"] += resp["prompt_tokens"]
                counters["ctok"] += resp["completion_tokens"]
                counters["lat"] += latency
        except Exception as e:  # noqa: BLE001
            err = str(e)[:500]
            ok = 0
            async with lock:
                counters["failed"] += 1
        async with lock:
            counters["done"] += 1
            conn.execute(
                "INSERT INTO eval_items(eval_id, idx, question, expected, predicted, raw_response, correct, latency_ms, error)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (eval_id, idx, item.get("question", "")[:2000], expected, predicted,
                 (raw or "")[:4000] if raw else None, ok, latency, err),
            )
            conn.execute(
                "UPDATE evaluations SET done=?, correct=?, failed=?, prompt_tokens=?,"
                " completion_tokens=?, total_latency_ms=? WHERE id=?",
                (counters["done"], counters["correct"], counters["failed"],
                 counters["ptok"], counters["ctok"], counters["lat"], eval_id),
            )
            conn.commit()

    await asyncio.gather(*(work(i, it) for i, it in enumerate(items)))
    final = "done" if counters["failed"] < len(items) else "failed"
    err_msg = None if final == "done" else "所有请求均失败，请检查 base_url / api_key / 模型名"
    conn.execute(
        "UPDATE evaluations SET status=?, error=?, finished_at=datetime('now','localtime') WHERE id=?",
        (final, err_msg, eval_id),
    )
    conn.commit()
    export_items(eval_id)
