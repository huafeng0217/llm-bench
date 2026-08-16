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
BFCL_DIR = DATA_DIR / "bfcl_v4"
CHOICES = [chr(65 + i) for i in range(10)]  # A-J，兼容 4 选 1（MMLU/C-Eval）到 10 选 1（MMLU-Pro）
DEFAULTS = {"max_tokens": 2048, "timeout_s": 90, "concurrency": 8}
MAX_RETRIES = 3
RUNNING: dict[int, asyncio.Task] = {}  # eval_id -> 运行中的评测任务，供「停止」功能 cancel


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
    """扫描 data 目录下所有 .jsonl 题库（含 bfcl_v4 子目录）。"""
    out = []
    paths = sorted(DATA_DIR.glob("*.jsonl")) + sorted(BFCL_DIR.glob("*.jsonl"))
    for p in paths:
        if p.name.endswith("_answer.jsonl"):
            continue  # BFCL 标准答案文件不是题库
        n = sum(1 for _ in open(p, encoding="utf-8"))
        out.append({"id": p.stem, "name": p.stem, "count": n})
    return out


def _dataset_path(benchmark: str) -> Path:
    """定位题库文件：data/<id>.jsonl 或 data/bfcl_v4/<id>.jsonl。"""
    for p in (DATA_DIR / f"{benchmark}.jsonl", BFCL_DIR / f"{benchmark}.jsonl"):
        if p.exists():
            return p
    raise FileNotFoundError(f"题库不存在: {benchmark}")


def load_dataset(benchmark: str, limit: int | None = None):
    path = _dataset_path(benchmark)
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    if limit and limit > 0:
        items = items[:limit]
    return items


def load_bfcl_answers(benchmark: str):
    """加载 BFCL 标准答案文件（与题目 id 对应）。"""
    path = BFCL_DIR / f"{benchmark}_answer.jsonl"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["id"]] = d.get("ground_truth")
    return out


# ---------- prompt 与判分 ----------

def is_choice_item(item: dict) -> bool:
    """判断题目是否选择题（含 A/B/C… 选项字段）。数值题只有 question/answer，无选项。"""
    return any(item.get(c) for c in CHOICES)


def build_prompt(item: dict) -> str:
    """选择题 prompt：选项按题目实际有的（4 选 1 到 10 选 1 均可）。"""
    lines = [
        "以下是一道单项选择题。请只回答正确选项的字母，不要输出任何其他内容。",
        "",
        f"题目：{item['question']}",
    ]
    for c in CHOICES:
        if item.get(c):
            lines.append(f"{c}. {item[c]}")
    lines += ["", "答案："]
    return "\n".join(lines)


def build_numeric_prompt(item: dict) -> str:
    """数值题 prompt（GSM8K / MATH-500 等，答案不是选项字母）。"""
    return (
        "请解答下面的数学题，只输出最终答案（数字或最简表达式），不要输出解题过程。\n\n"
        f"题目：{item['question']}\n\n答案："
    )


def extract_answer(text: str | None) -> str | None:
    """从模型回复中抽取选项字母（A-J）。先看显式'答案'，再找首个独立字母。"""
    if not text:
        return None
    head = text.strip()[:120]
    m = re.search(r"(?:答案是|答案[:：为]|选项是?|answer is|answer[:：])\s*\(?([A-J])\b", head, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<![A-Za-z])([A-J])(?![A-Za-z])", head)
    return m.group(1) if m else None


# ---------- 数值判分（GSM8K / MATH-500） ----------

def extract_numeric_answer(text: str | None) -> str | None:
    """从模型回复中抽取最终数值答案。

    策略：优先看显式「答案」标记后的内容，再取末尾的数字。
    支持小数、负号、千分位逗号、美元符号、百分号、科学计数法。
    """
    if not text:
        return None
    s = text.strip()
    # 聚焦「答案」标记之后的部分（若有）。注意：答案词后面可能跟冒号/等号，需一并消费，
    # 否则「答案是?」会只匹配「答案」二字，把冒号留在捕获组里。
    m = re.search(r"(?:答案是?|答案|answer(?:\s*is)?)\s*[:：为=]?\s*(.+)$", s, re.IGNORECASE | re.DOTALL)
    if m:
        s = m.group(1).strip()
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?%?", s)
    if not nums:
        return None
    return nums[-1]  # 取最后一个数字（GSM8K 惯例）


def normalize_answer(s) -> str | None:
    """规范化答案字符串，用于容错比对。

    处理：转小写、去掉 LaTeX \\text{...} 包装、去美元/逗号/空格、百分号转小数。
    这样模型的 "Evelyn" 能与标准答案 "\\text{Evelyn}" 匹配。
    """
    if s is None:
        return None
    s = str(s).strip().lower()
    # 去掉 \text{...} 包装（如 \text{evelyn} → evelyn），便于纯文本答案比对
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.endswith("%"):
        try:
            return f"{float(s[:-1]) / 100:g}"
        except ValueError:
            return s[:-1]
    return s


def _to_float(s):
    """尝试把字符串转 float，支持分数 a/b；失败返回 None。

    注意：s 可能是 None（模型回复抽不到数字时），需先判空，否则
    re.fullmatch(None) 会抛 "expected string or bytes-like object"。
    """
    if s is None:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        m = re.fullmatch(r"(-?\d+)\s*/\s*(-?\d+)", s)
        if m:
            try:
                return float(m.group(1)) / float(m.group(2))
            except ZeroDivisionError:
                return None
    return None


def extract_answer_span(text: str | None) -> str | None:
    """抽取「答案」标记之后的完整文本（保留表达式结构，供 LaTeX 答案比对）。"""
    if not text:
        return None
    m = re.search(r"(?:答案是?|答案|answer(?:\s*is)?)\s*[:：为=]?\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def numeric_match(predicted_text: str | None, expected: str) -> bool:
    """数值/表达式判分（三层容错）。

    1) 先抽「答案」后的完整文本，规范化后字符串相等（覆盖 LaTeX 表达式答案）；
    2) 再抽数字做规范化字符串相等（覆盖纯数值答案）；
    3) 最后数值容错比对（±1e-4 相对误差，支持分数 a/b 与小数互转）。
    """
    e = normalize_answer(expected)
    if not e:
        return False
    # 1) 完整答案文本（含 LaTeX 结构）
    span = normalize_answer(extract_answer_span(predicted_text))
    if span and span == e:
        return True
    # 2) 抽取数字后的字符串相等
    p = normalize_answer(extract_numeric_answer(predicted_text))
    if p and p == e:
        return True
    # 3) 数值容错
    pf, ef = _to_float(p), _to_float(e)
    if pf is not None and ef is not None:
        return abs(pf - ef) <= 1e-4 * max(1.0, abs(ef))
    return False


# ---------- BFCL 函数调用评测 ----------

def is_bfcl(benchmark: str) -> bool:
    return (BFCL_DIR / f"{benchmark}.jsonl").exists()


_TYPE_MAP = {"dict": "object", "tuple": "array", "list": "array", "float": "number", "int": "integer"}


def _norm_schema(node):
    """递归把 BFCL 的非标准 JSON Schema 类型转成 OpenAI 兼容的。

    - float → number、int → integer、dict → object、tuple/list → array
    - any → 删掉 type（等价于不限制类型）
    """
    if isinstance(node, dict):
        t = node.get("type")
        if isinstance(t, str):
            if t == "any":
                node.pop("type", None)
            elif t in _TYPE_MAP:
                node["type"] = _TYPE_MAP[t]
        for v in node.values():
            _norm_schema(v)
    elif isinstance(node, list):
        for v in node:
            _norm_schema(v)
    return node


def normalize_tools(func_defs: list) -> tuple:
    """把 BFCL 的 function 定义转成 OpenAI tools 参数格式。

    - 递归把 BFCL 的非标准 schema 类型（dict/float/tuple/any…）转成 OpenAI 兼容类型。
    - 函数名可能是 Python 风格带点号（如 math.factorial），OpenAI 只允许
      ^[a-zA-Z0-9_-]+$，需 sanitize 为下划线，并返回 {sanitized: 原名} 映射
      供评分时还原。

    返回 (tools, name_map)。
    """
    tools, name_map = [], {}
    for f in func_defs:
        original = f["name"]
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", original)
        name_map[safe] = original
        params = _norm_schema(dict(f.get("parameters", {})))
        if not isinstance(params.get("type"), str):  # 空/无类型兜底
            params = {"type": "object", "properties": {}}
        tools.append({
            "type": "function",
            "function": {
                "name": safe,
                "description": f.get("description", ""),
                "parameters": params,
            },
        })
    return tools, name_map


def bfcl_messages(question) -> list:
    """BFCL question 是 [[{role, content}, ...]]，取第一条对话链。"""
    msgs = question[0] if isinstance(question, list) and question else question
    return [{"role": m["role"], "content": m["content"]} for m in msgs if isinstance(m, dict)]


def ast_match(calls: list[dict], ground_truth) -> tuple[bool, str]:
    """AST 匹配：模型调用的 (函数名, 参数) 与标准答案对比。

    ground_truth 格式: [{"func": {"param": [候选值, ...]}}, ...]
    返回 (是否全对, 简要说明)。
    """
    if ground_truth is None:
        # irrelevance：正确行为是拒绝调用任何函数
        ok = len(calls) == 0
        return ok, "拒绝调用" if ok else f"应拒绝但调用了 {len(calls)} 个函数"
    expected = [list(g.keys())[0] for g in ground_truth]
    expected_args = [list(g.values())[0] for g in ground_truth]
    # 调用数量必须一致
    if len(calls) != len(expected):
        got = ", ".join(c["name"] for c in calls) or "无调用"
        return False, f"调用数不符：期望 {len(expected)} 个 ({', '.join(expected)})，实际 {len(calls)} 个 ({got})"

    def val_match(got, cands):
        if not isinstance(cands, list):
            cands = [cands]
        # 值可能是 int/float/str/bool，统一转字符串比较（忽略空白）
        gs = str(got).strip()
        return any(str(c).strip() == gs for c in cands)

    for i, call in enumerate(calls):
        if call["name"] != expected[i]:
            return False, f"函数名不符：期望 {expected[i]}，实际 {call['name']}"
        got_args = call.get("arguments", {})
        # 期望参数是子集即可（多余参数可容忍，官方对 simple 只查必要字段）
        for k, cands in expected_args[i].items():
            if k not in got_args:
                return False, f"缺少参数 {k}"
            if not val_match(got_args[k], cands):
                return False, f"参数 {k} 不符：期望 {cands}，实际 {got_args[k]}"
    return True, "AST 匹配通过"


def parse_tool_calls(msg) -> list[dict]:
    """从模型返回消息解析 tool_calls → [{name, arguments(dict)}]。"""
    out = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        if not fn:
            continue
        try:
            args = json.loads(fn.arguments) if fn.arguments else {}
        except (json.JSONDecodeError, TypeError):
            args = {"_raw": fn.arguments}
        out.append({"name": fn.name, "arguments": args})
    return out


def bfcl_expected_text(ground_truth) -> str:
    """标准答案的简要文本（用于 eval_items 展示）。"""
    if ground_truth is None:
        return "拒绝调用"
    return "; ".join(f"{list(g.keys())[0]}({', '.join(list(g.values())[0].keys())})" for g in ground_truth)


def bfcl_predicted_text(calls: list[dict]) -> str:
    if not calls:
        return "无调用"
    return "; ".join(f"{c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in calls)


# ---------- 模型调用 ----------

async def _mock_response(prompt: str, expected: str):
    """本地模拟模型：约 70% 答对（按题目 hash 确定），用于无 key 演示。

    兼容两类题型：
    - 选择题：expected 是 A-J 字母，答错时给一个别的字母；
    - 数值题：expected 是数字/表达式，答错时给一个错误数字。
    """
    await asyncio.sleep(random.uniform(0.03, 0.12))
    h = int(hashlib.md5(prompt.encode()).hexdigest(), 16)
    correct = h % 10 < 7
    if correct:
        ans = expected
    elif expected in CHOICES:
        # 选择题：给一个别的字母
        wrong = [c for c in CHOICES if c != expected]
        ans = wrong[h % len(wrong)]
    else:
        # 数值题：给一个错误数字（无法转 float 的表达式则给随机整数）
        try:
            ans = str(float(expected) + (h % 5 + 1))
        except ValueError:
            ans = str(h % 100)
    return {
        "content": f"答案：{ans}",
        "prompt_tokens": len(prompt) // 2,
        "completion_tokens": 4,
        "latency_ms": 0,
    }


async def _mock_bfcl(messages: list, tools: list, ground_truth):
    """本地模拟 BFCL：约 70% 按标准答案构造 tool_calls（用于无 key 演示）。"""
    await asyncio.sleep(random.uniform(0.03, 0.12))
    h = int(hashlib.md5(json.dumps(messages, ensure_ascii=False).encode()).hexdigest(), 16)
    correct = h % 10 < 7
    calls = []
    if correct and ground_truth is not None:
        for g in ground_truth:
            for fname, params in g.items():
                args = {k: v[0] if isinstance(v, list) and v else v for k, v in params.items()}
                calls.append({"name": fname, "arguments": args})
    calls = [{"name": c["name"], "arguments": c["arguments"]} for c in calls]
    # 与真实 API 路径一致：tool_calls 是解析后的 [{name, arguments}]
    return {
        "content": None,
        "tool_calls": calls,
        "prompt_tokens": len(json.dumps(messages)) // 2,
        "completion_tokens": len(calls) * 8,
        "latency_ms": 0,
    }


async def chat_once(model_cfg: dict, prompt: str, expected: str, params: dict):
    if model_cfg["base_url"].startswith("mock://"):
        return await _mock_response(prompt, expected)
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


async def chat_once_bfcl(model_cfg: dict, messages: list, tools: list, ground_truth, params: dict):
    """BFCL 评测：带 tools 参数调用，返回 tool_calls。"""
    if model_cfg["base_url"].startswith("mock://"):
        return await _mock_bfcl(messages, tools, ground_truth)
    client = AsyncOpenAI(
        base_url=model_cfg["base_url"],
        api_key=model_cfg["api_key"] or "EMPTY",
        timeout=params["timeout_s"],
        max_retries=0,
    )
    t0 = time.time()
    r = await client.chat.completions.create(
        model=model_cfg["name"],
        messages=messages,
        tools=tools,
        temperature=0,
        max_tokens=params["max_tokens"],
    )
    latency = int((time.time() - t0) * 1000)
    usage = r.usage
    msg = r.choices[0].message
    return {
        "content": (msg.content or "").strip(),
        "tool_calls": parse_tool_calls(msg),
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


async def chat_with_retry_bfcl(model_cfg: dict, messages: list, tools: list, ground_truth, params: dict):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return await chat_once_bfcl(model_cfg, messages, tools, ground_truth, params)
        except Exception as e:  # noqa: BLE001
            last_err = e
            await asyncio.sleep(2 ** attempt)
    raise last_err


# ---------- 评测任务 ----------

async def run_evaluation(eval_id: int):
    task = asyncio.current_task()
    RUNNING[eval_id] = task
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

        conn.execute("UPDATE evaluations SET status='running', total=? WHERE id=?", (len(items), eval_id))
        conn.commit()

        params = {k: (ev.get(k) or d) for k, d in DEFAULTS.items()}
        sem = asyncio.Semaphore(params["concurrency"])
        counters = {"done": 0, "correct": 0, "failed": 0, "ptok": 0, "ctok": 0, "lat": 0}
        lock = asyncio.Lock()

        async def work(idx: int, item: dict):
            raw, predicted, expected, err, latency, ok = None, None, None, None, 0, 0
            try:
                async with sem:
                    if fc:
                        ground_truth = answers.get(item.get("id"), None)
                        messages = bfcl_messages(item.get("question"))
                        tools, name_map = normalize_tools(item.get("function", []))
                        resp = await chat_with_retry_bfcl(model_cfg, messages, tools, ground_truth, params)
                        calls = resp["tool_calls"] or []
                        # 把 sanitize 后的函数名还原为原始名（如 math_factorial → math.factorial），再评分/展示
                        for c in calls:
                            if c["name"] in name_map:
                                c["name"] = name_map[c["name"]]
                        raw = resp["content"] or bfcl_predicted_text(calls)
                        predicted = bfcl_predicted_text(calls)
                        expected = bfcl_expected_text(ground_truth)
                        ok, reason = ast_match(calls, ground_truth)
                        ok = 1 if ok else 0
                    else:
                        if is_choice_item(item):
                            # 选择题（4 选 1 到 10 选 1）
                            prompt = build_prompt(item)
                            expected = str(item.get("answer", "")).strip().upper()
                            resp = await chat_with_retry(model_cfg, prompt, expected, params)
                            raw = resp["content"]
                            predicted = extract_answer(raw)
                            ok = 1 if predicted == expected else 0
                        else:
                            # 数值题（GSM8K / MATH-500）
                            prompt = build_numeric_prompt(item)
                            expected = str(item.get("answer", "")).strip()
                            resp = await chat_with_retry(model_cfg, prompt, expected, params)
                            raw = resp["content"]
                            # 明细展示完整答案（LaTeX/数字），回退到抽取的数字
                            predicted = extract_answer_span(raw) or extract_numeric_answer(raw)
                            ok = 1 if numeric_match(raw, expected) else 0
                    latency = resp["latency_ms"]
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
                    (eval_id, idx,
                     (bfcl_messages(item.get("question"))[-1]["content"] if fc else item.get("question", ""))[:2000],
                     expected, predicted, (raw or "")[:4000] if raw else None, ok, latency, err),
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
