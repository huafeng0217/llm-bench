"""模型调用层：OpenAI 客户端封装、重试、本地 mock。"""

import asyncio
import hashlib
import json
import random
import re
import sys
import time

from openai import AsyncOpenAI

from .config import CHOICES, MAX_RETRIES


def model_extra_body(model_cfg: dict):
    """取模型级「额外请求参数」（models.extra_body，JSON 对象字符串）。

    为什么要有它：不同厂商控制「关掉思考」的参数名不一样（DashScope 是
    ``enable_thinking``），而**不能给不认识的 provider 乱发字段**（严格校验的接口会 400）。
    所以做成模型级配置：谁需要谁自己填，没填就跟以前完全一样。
    JSON 非法或不是对象时返回 None 并打印一行提示 —— 配置错误不该把整场评测打断。
    """
    raw = (model_cfg or {}).get("extra_body") or ""
    if isinstance(raw, dict):
        return raw or None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print(f"  警告：模型 {model_cfg.get('name')} 的 extra_body 不是合法 JSON，已忽略：{raw[:80]}",
              file=sys.stderr)
        return None
    if not isinstance(obj, dict):
        print(f"  警告：模型 {model_cfg.get('name')} 的 extra_body 必须是 JSON 对象，已忽略", file=sys.stderr)
        return None
    return obj or None


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
    # multi_turn 的多步往返：若最后一条是「工具执行结果」，说明本轮已经调过工具，
    # mock 返回空调用表示「做完了」，免得在循环里反复生成同一批调用。
    # （注意只看最后一条：上下文里本来就可能有上一轮的 assistant 调用。）
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "tool":
        return {"content": "done", "tool_calls": [], "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}
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
    extra = model_extra_body(model_cfg)
    r = await client.chat.completions.create(
        model=model_cfg["name"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=params["max_tokens"],  # 思考型模型的 reasoning tokens 也占额度，预算要给足
        **({"extra_body": extra} if extra else {}),
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
    extra = model_extra_body(model_cfg)
    r = await client.chat.completions.create(
        model=model_cfg["name"],
        messages=messages,
        tools=tools,
        temperature=0,
        max_tokens=params["max_tokens"],
        **({"extra_body": extra} if extra else {}),
    )
    latency = int((time.time() - t0) * 1000)
    usage = r.usage
    msg = r.choices[0].message
    # DeepSeek 等思考型模型在多轮工具调用时要求把 reasoning_content 原样回传，
    # 否则下一轮请求会 400（"The reasoning_content in the thinking mode must be passed back"）。
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning is None:
        reasoning = (getattr(msg, "model_extra", None) or {}).get("reasoning_content")
    return {
        "content": (msg.content or "").strip(),
        "tool_calls": parse_tool_calls(msg),
        "reasoning_content": reasoning,
        "_msg": msg,  # 原始 message 对象：多轮拼上下文时直接回传，才不会丢 reasoning_content
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


CODE_TOKEN_LADDER = (1, 4, 16)
