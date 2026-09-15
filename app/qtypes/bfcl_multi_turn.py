"""BFCL multi_turn 题型：多轮对话 + 每轮 AST 匹配。

注意：本实现**不真实执行工具**，只用中性结果维持消息序列合法（见 run_multi_turn 文档）。"""

import asyncio
import json
import re

from ..config import BFCL_DIR
from ..llm import chat_with_retry_bfcl
from .bfcl import normalize_tools


MAX_MT_STEPS = 6  # multi_turn 单轮内最多「调用→执行→再调用」往返次数（防死循环）


MT_CLASS_DOC = {
    "GorillaFileSystem": "gorilla_file_system",
    "VehicleControlAPI": "vehicle_control",
    "TradingBot": "trading_bot",
    "TravelAPI": "travel_booking",
    "MessageAPI": "message_api",
    "TwitterAPI": "posting_api",
    "TicketAPI": "ticket_api",
    "MathAPI": "math_api",
    "MemoryAPI": "memory_kv",
}


FUNC_DOC_DIR = BFCL_DIR / "func_doc"


def is_multi_turn(benchmark: str) -> bool:
    """是否 BFCL multi_turn 子集（多轮对话；工具定义不在题目里，需按 involved_classes 另加载）。"""
    return "multi_turn" in benchmark


def load_mt_tools(involved_classes, excluded_function=None) -> tuple:
    """按 involved_classes 加载 multi_turn 的工具定义（并排除 excluded_function）。

    multi_turn 题目的 `function` 字段是空的，工具定义在 data/bfcl_v4/func_doc/ 下，
    需要按类名映射加载。返回 (tools, name_map)，与 normalize_tools 一致。
    """
    excluded = set(excluded_function or [])
    func_defs = []
    for cls in involved_classes or []:
        doc = MT_CLASS_DOC.get(cls)
        if not doc:
            continue
        p = FUNC_DOC_DIR / f"{doc}.jsonl"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                fd = json.loads(line)
                if fd.get("name") not in excluded:
                    func_defs.append(fd)
    return normalize_tools(func_defs)


def parse_gt_calls(gt_turn) -> list:
    """把 multi_turn 的期望调用字符串解析成 ast_match 需要的格式。

    输入形如 ["cd(folder='document')", "mkdir(dir_name='temp')"]，
    输出形如 [{"cd": {"folder": ["document"]}}, {"mkdir": {"dir_name": ["temp"]}}]。
    """
    out = []
    for s in (gt_turn or []):
        m = re.match(r"([A-Za-z_]\w*)\s*\((.*)\)\s*$", str(s).strip())
        if not m:
            continue
        name, args_s = m.group(1), m.group(2)
        args = {}
        if args_s.strip():
            # 按「逗号 + key=」切分参数，避免值内部逗号被误切
            for part in re.split(r",\s*(?=[A-Za-z_]\w*\s*=)", args_s):
                if "=" in part:
                    k, v = part.split("=", 1)
                    args[k.strip()] = [v.strip().strip("'\"")]
        out.append({name: args})
    return out


def describe_env(item: dict) -> str:
    """把 multi_turn 题的 initial_config 转成环境说明，作为 system 消息喂给模型。

    官方会真实执行工具并把结果反馈给模型；本实现不执行工具，改为**直接把环境初始
    状态告诉模型**，这样模型不必靠 pwd/ls 反复探索就能给出正确调用序列。
    """
    cfg = item.get("initial_config")
    if not cfg:
        return ""
    body = json.dumps(cfg, ensure_ascii=False)
    if len(body) > 8000:  # 防止个别题目的环境过大撑爆上下文
        body = body[:8000] + "…（已截断）"
    return (
        "You are operating in a simulated environment. The tools available to you are given.\n"
        "Initial environment state (JSON):\n" + body + "\n\n"
        "Based on this state, directly call the tool(s) needed for the current request — "
        "you may issue several calls at once. Do not explore (e.g. avoid unnecessary pwd/ls)."
    )


def mock_exec_result(call: dict) -> str:
    """模拟工具执行结果（**不真实执行任何东西**）。

    官方会在内存环境里真实执行工具并把结果反馈给模型；本实现改为返回一个中性的
    成功提示，让模型知道该步已完成、可以继续下一步，从而能在一轮内完成整个序列。
    """
    return json.dumps({"status": "ok", "tool": call.get("name"), "message": "executed successfully"},
                      ensure_ascii=False)


def _call_matches(call: dict, name: str, args: dict) -> bool:
    """判断单个模型调用是否匹配期望的（函数名相同 + 期望参数都在且值相符，允许多余参数）。"""
    if call.get("name") != name:
        return False
    got = call.get("arguments") or {}
    for k, cands in (args or {}).items():
        if k not in got:
            return False
        opts = cands if isinstance(cands, list) else [cands]
        if not any(str(got[k]).strip() == str(c).strip() for c in opts):
            return False
    return True


def ast_match_subset(calls: list[dict], expected: list) -> tuple:
    """宽松 AST 匹配（multi_turn 专用）：期望的每个调用都能在模型调用中找到即可。

    与 ast_match 的区别：**不要求调用数量一致**，允许模型多出探索性调用（pwd/ls 等）；
    只要期望的调用都被执行了就算通过。官方按真实执行后的状态判分，探索调用无害。
    """
    if not expected:
        return True, "该轮无需调用"
    missing = []
    for exp in expected:
        name = list(exp.keys())[0]
        args = list(exp.values())[0]
        if not any(_call_matches(c, name, args) for c in calls):
            missing.append(name)
    if missing:
        got = ", ".join(c["name"] for c in calls) or "无调用"
        return False, f"缺少期望调用 {', '.join(missing)}（实际: {got}）"
    return True, "覆盖期望调用"


async def run_multi_turn(model_cfg: dict, item: dict, ground_truth, params: dict) -> dict:
    """BFCL multi_turn 评测：逐轮调用模型并验证每轮的函数调用。

    每轮上下文 = 之前各轮的 user 请求 + 模型自己产出的 assistant 调用。
    说明：本实现**不真实执行工具**（不模拟文件系统/API 状态），仅用占位 tool 结果
    维持 OpenAI 消息序列合法，因此判分只针对「每轮该调什么函数」，不校验环境最终状态。
    """
    tools, name_map = load_mt_tools(item.get("involved_classes"), item.get("excluded_function"))
    if not tools:
        raise RuntimeError(
            f"multi_turn 工具定义缺失（involved_classes={item.get('involved_classes')}），请先下载 func_doc")
    turns = item.get("question") or []
    # 把环境初始状态作为 system 消息喂给模型，避免它靠 pwd/ls 反复探索猜环境
    env = describe_env(item)
    messages: list = [{"role": "system", "content": env}] if env else []
    turn_oks, details = [], []
    latency = ptok = ctok = 0
    for ti, turn_msgs in enumerate(turns):
        messages.extend({"role": m.get("role"), "content": m.get("content")}
                        for m in turn_msgs if isinstance(m, dict))
        # 本轮期望调用（解析后既用于 mock 生成，也用于判分）
        gt_turn = ground_truth[ti] if isinstance(ground_truth, list) and ti < len(ground_truth) else []
        parsed_gt = parse_gt_calls(gt_turn)
        # 一轮内允许多次「调用→执行→再调用」往返：模型分步执行时也能在一轮里走完整条序列
        turn_calls: list = []
        for step in range(MAX_MT_STEPS):
            resp = await chat_with_retry_bfcl(model_cfg, messages, tools, parsed_gt, params)
            calls = resp["tool_calls"] or []
            latency += resp["latency_ms"]
            ptok += resp["prompt_tokens"]
            ctok += resp["completion_tokens"]
            if not calls:
                break  # 模型不再调用工具 → 本轮任务结束
            for c in calls:
                if c["name"] in name_map:
                    c["name"] = name_map[c["name"]]
            turn_calls.extend(calls)
            # 把调用作为 assistant 消息 + 模拟执行结果加入上下文，供模型继续下一步。
            # 优先用 API 返回的**原始 message 对象**：思考型模型（DeepSeek 等）要求把
            # reasoning_content 原样回传，而自己拼 dict 会被 SDK 丢掉该字段导致下一轮 400。
            raw_msg = resp.get("_msg")
            if raw_msg is not None:
                # 用原始 message：tool 结果必须回填 API 生成的真实 tool_call_id
                messages.append(raw_msg)
                ids = [tc.id for tc in (getattr(raw_msg, "tool_calls", None) or [])]
            else:
                ids = [f"call_{ti}_{step}_{i}" for i in range(len(calls))]
                messages.append({
                    "role": "assistant",
                    "content": resp["content"] or None,
                    "tool_calls": [
                        {"id": ids[i], "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                        for i, c in enumerate(calls)
                    ],
                })
            for i, c in enumerate(calls):
                messages.append({"role": "tool",
                                 "tool_call_id": ids[i] if i < len(ids) else f"call_{ti}_{step}_{i}",
                                 "content": mock_exec_result(c)})
        ok_turn, reason = ast_match_subset(turn_calls, parsed_gt)
        turn_oks.append(ok_turn)
        details.append(f"轮{ti + 1}{'✓' if ok_turn else '✗(' + reason + ')'}")
    return {
        "ok": bool(turn_oks) and all(turn_oks),
        "turn_oks": turn_oks,
        "detail": " | ".join(details),
        "latency_ms": latency,
        "prompt_tokens": ptok,
        "completion_tokens": ctok,
    }
