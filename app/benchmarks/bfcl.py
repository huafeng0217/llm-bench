"""BFCL v4（伯克利函数调用排行榜）。

同一份数据源、同一套下载与判分逻辑，所以全部子集放在一个文件里 ——
拆成 16 个文件只会把同一段代码抄 16 遍。

**官方总分是加权复合，不是子集平均分**：

    Overall = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10% + Hallucination×10%

组内先取子集平均，再按组权重加权（权重与口径见下面的 ``FAMILY_DEFS``）。
本项目目前提供 Non-Live / Live / Multi-Turn / Hallucination 四组，合计官方权重 **60%**；
**Agentic（Web Search + Memory）那 40% 暂时没有** —— Web Search 要 SerpAPI 付费服务、
Memory 要先跑预处理并拉 embedding 模型，都跟「纯本地、可复现」冲突。
所以界面上会把「权重覆盖 60%」显式标出来，绝不假装这是完整的 BFCL 总分。

``live_relevance`` 没有收录：官方明确它**不计入总分**，而且它没有 possible_answer，
判分口径与调用类子集不同（要判「该函数是否相关」），硬套现有 runner 只会得到假分数。
"""
from .types import Benchmark, Family, Group
import json
import sys
import time
from ._util import DATA_DIR, http_get, write_jsonl

from functools import partial
from pathlib import Path


# BFCL 的题库落在 data/bfcl_v4/ 子目录（别的基准都是 data/<id>.jsonl）
BFCL_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "bfcl_v4"


BASE = "https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/bfcl_eval/data"


# 官方分组与权重。**按官方顺序**声明 order，不靠字母序 —— 界面折叠后就是按它排的。
FAMILY_DEFS = [
    Family(
        id="BFCL v4",
        groups=(
            Group(id="non_live", name="Non-Live", weight=0.10, order=1),
            Group(id="live", name="Live", weight=0.10, order=2),
            Group(id="multi_turn", name="Multi-Turn", weight=0.30, order=3),
            Group(id="hallucination", name="Hallucination", weight=0.10, order=4),
            Group(id="agentic", name="Agentic（Web Search + Memory）", weight=0.40, order=5),
        ),
        note="官方总分 = Agentic×40% + Multi-Turn×30% + Live×10% + Non-Live×10%"
             " + Hallucination×10%（组内先取子集平均，再按组权重加权）",
        source="https://gorilla.cs.berkeley.edu/blogs/16_bfcl_v4_memory.html",
    ),
]


# 子集清单：(官方文件名, order, 显示名后缀, 下载按钮后缀, 官方分组, 题量, 卡片简介, 完整说明)
#
# 一句话加一个子集：往这张表里加一行。order 只影响同组内的展示位次，
# 组别与官方权重由 family/group 决定（见 FAMILY_DEFS），界面上的「组别」标签也从那里来。
# summary 是卡片上显示的两行简介 —— 写「差在哪 / 低分说明什么」，不要复述名字已经说过的机制，
# 也不要重复题量（卡片标签上有）；description 是选中后展开的完整说明。
# 长度控制在 40 字以内：最窄卡片两行放得下，超出会被省略（有 scripts/verify_datasets.py 盯着）。
# summary 是卡片上显示的那一行（选基准时扫一眼用），description 是选中后展开的完整说明。
_SUBSET_META = [
    ("BFCL_v4_simple_python", 12, "单函数", "单函数", "non_live", 400,
     "最基础的函数调用：给一个工具，看会不会选错函数、漏参数或类型不对",
     "伯克利函数调用排行榜（BFCL）v4 单函数子集：给一个问题与一个工具定义，模型须选择正确函数并填对参数。官方用 AST 匹配评分。"),
    ("BFCL_v4_multiple", 13, "多函数选择", "多函数选择", "non_live", 200,
     "同时给多个工具、只有一个是对的：看模型在候选堆里会不会挑错",
     "BFCL v4 多函数子集：同时给出多个工具，模型须在候选集中挑出正确的那一个，考验函数辨识能力。"),
    ("BFCL_v4_parallel", 14, "并行调用", "并行调用", "non_live", 200,
     "一个问题要同时调多个函数（如查两个城市天气），看会不会漏掉一个",
     "BFCL v4 并行子集：单轮请求中须同时调用多个不同函数，考验并行工具调用的编排能力。"),
    ("BFCL_v4_parallel_multiple", 15, "并行多选", "并行多选", "non_live", 200,
     "多个问题 × 多个相似函数，须并行且各自选对 —— Non-Live 里最难的一项",
     "BFCL v4 并行+多函数组合子集：多个问题×多个候选函数，须并行调用且各自选对函数，难度最高。"),
    ("BFCL_v4_irrelevance", 16, "无关拒绝", "无关拒绝", "hallucination", 240,
     "给的工具跟问题无关，正确做法是不调；专测「明明不该调却硬调」",
     "BFCL v4 无关性子集：问题与给出的工具毫无关系，正确行为是拒绝调用任何函数，测模型是否过度调用工具（幻觉防御）。"),
    ("BFCL_v4_simple_java", 17, "单函数(Java)", "单函数-Java", "non_live", 100,
     "单函数的 Java 版：工具定义换成 Java，看参数写法会不会跟着跑偏",
     "BFCL v4 单函数子集的 Java 版本：给一个问题与一个 Java 工具定义，模型须选对函数并填对参数，测跨语言的函数调用能力。"),
    ("BFCL_v4_simple_javascript", 18, "单函数(JS)", "单函数-JS", "non_live", 50,
     "单函数的 JS 版：换成 JavaScript 工具定义，看参数写法会不会跑偏",
     "BFCL v4 单函数子集的 JavaScript 版本：给一个问题与一个 JS 工具定义，模型须选对函数并填对参数，测跨语言的函数调用能力。"),
    ("BFCL_v4_multi_turn_base", 19, "多轮对话", "多轮对话", "multi_turn", 200,
     "多轮里每轮都要选对函数与参数；官方要求各轮全对才算通过，判定偏严",
     "BFCL v4 多轮基础子集：一次评测包含多轮用户请求（文件系统、交易、差旅、消息等场景），模型须在每轮选对函数与参数。判分按每轮 AST 匹配，整题各轮全对才算通过。"),
    ("BFCL_v4_multi_turn_long_context", 20, "多轮长上下文", "多轮长上下文", "multi_turn", 200,
     "多轮 + 更长的工具文档：看长上下文里还记不记得该调哪个函数",
     "BFCL v4 多轮长上下文子集：多轮对话 + 更长的工具文档与上下文，考验长上下文下保持工具调用正确性。"),
    ("BFCL_v4_multi_turn_miss_func", 21, "多轮缺函数", "多轮缺函数", "multi_turn", 200,
     "某轮要用的函数不在列表里，正确做法是不调或反问，别硬编一个",
     "BFCL v4 多轮缺函数子集：某轮所需的函数不在可用工具列表里，正确行为是不调用（或按需澄清），测模型是否会硬调不存在的工具。"),
    ("BFCL_v4_multi_turn_miss_param", 22, "多轮缺参数", "多轮缺参数", "multi_turn", 200,
     "某轮所需参数压根没给，正确做法是先追问，别硬编一个参数把事办了",
     "BFCL v4 多轮缺参数子集：某轮所需参数缺失，正确行为是先向用户追问而非硬调，测多轮交互中的澄清能力。"),
    # ---- Live：真实用户提问 + 真实 API 文档（官方 Live 组，权重 10%）----
    ("BFCL_v4_live_simple", 23, "Live 单函数", "Live 单函数", "live", 258,
     "真实用户提问配真实 API 文档（不是人造题），提问还夹着中文",
     "BFCL v4 Live 单函数子集：真实用户提问配真实 API 文档（不是人造题），模型须选对函数并填对参数。"
     "提问语言混杂（含中文），比 Non-Live 更贴近实际使用。"),
    ("BFCL_v4_live_multiple", 24, "Live 多函数选择", "Live 多函数选择", "live", 1053,
     "真实提问 + 同一 API 的一堆相似端点：看从真实文档里挑函数的能力",
     "BFCL v4 Live 多函数子集：真实提问 + 大量相似候选函数（同一 API 的多个端点），"
     "考验在真实文档里的函数辨识能力。题量是 Live 组里最大的。"),
    ("BFCL_v4_live_parallel", 25, "Live 并行调用", "Live 并行调用", "live", 16,
     "真实提问里要同时调多个函数；题量太小，只看趋势、别排名次",
     "BFCL v4 Live 并行子集：一个真实提问里要同时调多个函数（如同时查两个城市的天气）。"
     "只有 16 题，一题就值 6 个百分点 —— 看趋势可以，别拿它排名次。"),
    ("BFCL_v4_live_parallel_multiple", 26, "Live 并行多选", "Live 并行多选", "live", 24,
     "真实提问 × 多个相似端点，须并行且各自选对；题量太小，别排名次",
     "BFCL v4 Live 并行+多函数子集：多个真实提问、多个相似候选函数，须并行调用且各自选对。"
     "只有 24 题，一题约值 4 个百分点。"),
    ("BFCL_v4_live_irrelevance", 27, "Live 无关拒绝", "Live 无关拒绝", "hallucination", 884,
     "真实提问跟给的函数其实无关，正确做法是不调；该不调就不调的依据",
     "BFCL v4 Live 无关拒绝子集：真实提问与给的函数无关（或给的函数不是该提问的正确解法），"
     "正确行为是拒绝调用。这是 Hallucination 组里题量最大的一份，也是「该不调就不调」的主要依据。"),
]


# 没有 possible_answer 文件的子集：官方标准答案就是「不调用任何函数」。
# 漏掉这个特例会直接报错退出，而不是判错 —— 所以写成一个集合，新增同类子集时只要加进来。
NO_ANSWER_REFUSE = {"BFCL_v4_irrelevance", "BFCL_v4_live_irrelevance"}

SUBSETS = [m[0] for m in _SUBSET_META]


MULTI_TURN_SUBSETS = {m[0] for m in _SUBSET_META if m[4] == "multi_turn"}


FUNC_DOC_NAMES = [
    "gorilla_file_system", "math_api", "memory_kv", "memory_rec_sum", "memory_vector",
    "message_api", "posting_api", "ticket_api", "trading_bot", "travel_booking",
    "vehicle_control", "web_search",
]


FUNC_DOC_DIR = BFCL_DATA_DIR / "func_doc"


def fetch_jsonl(url: str):
    """BFCL 数据文件是每行一个 JSON 的 jsonl。"""
    raw = http_get(url)
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def save_jsonl(path: Path, items: list):
    # 走公共 write_jsonl：它带 `.part` + 原子改名，且整个项目只有这一处写题库的实现
    write_jsonl(path, items)


def download_func_docs():
    """下载 multi_turn 子集用到的工具定义文档（JSONL）到 data/bfcl_v4/func_doc/。"""
    FUNC_DOC_DIR.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for name in FUNC_DOC_NAMES:
        p = FUNC_DOC_DIR / f"{name}.jsonl"
        if p.exists() and p.stat().st_size > 0:
            continue
        try:
            items = fetch_jsonl(f"{BASE}/multi_turn_func_doc/{name}.json")
        except Exception as e:  # noqa: BLE001
            print(f"工具文档失败 {name}: {e}", file=sys.stderr)
            continue
        save_jsonl(p, items)
        n_new += 1
        time.sleep(0.3)
    print(f"工具文档：{FUNC_DOC_DIR}（{len(FUNC_DOC_NAMES)} 个文件，本次新增 {n_new}）")


def download(subsets=None):
    """下载 BFCL v4 子集。subsets=None 表示全部；否则传子集名列表。"""
    BFCL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    names = subsets or SUBSETS
    # 涉及 multi_turn 子集时，一并准备工具定义文档
    if any(n in MULTI_TURN_SUBSETS for n in names):
        download_func_docs()
    total = 0
    for name in names:
        q_path = BFCL_DATA_DIR / f"{name}.jsonl"
        a_path = BFCL_DATA_DIR / f"{name}_answer.jsonl"
        # 断点续传：两文件都齐了就跳过
        if q_path.exists() and a_path.exists() and q_path.stat().st_size > 0 and a_path.stat().st_size > 0:
            n = sum(1 for _ in open(q_path, encoding="utf-8"))
            print(f"跳过 {name}: 已存在 {n} 题")
            total += n
            continue
        try:
            questions = fetch_jsonl(f"{BASE}/{name}.json")
            if name in NO_ANSWER_REFUSE:
                # 无关性子集：官方无 possible_answer，正确行为是"拒绝调用任何函数"
                answers = [{"id": q["id"], "ground_truth": None} for q in questions]
            else:
                answers = fetch_jsonl(f"{BASE}/possible_answer/{name}.json")
        except Exception as e:  # noqa: BLE001
            print(f"失败 {name}: {e}", file=sys.stderr)
            continue
        # 校验 id 对齐
        q_ids = {q["id"] for q in questions}
        a_ids = {a["id"] for a in answers}
        print(f"{name}: 题目 {len(questions)} 条, 答案 {len(answers)} 条, 交集 {len(q_ids & a_ids)}")
        save_jsonl(q_path, questions)
        save_jsonl(a_path, answers)
        total += len(questions)
        time.sleep(0.5)
    print(f"\n完成：{DATA_DIR} 共 {total} 题")


ENTRIES = [
    Benchmark(
        order=order,
        id=sid,
        name=f"BFCL v4 · {name_cn}",
        category="Agent / 工具调用",
        lang="多语" if group == "live" else "英文",   # Live 用的是真实用户提问，语言混杂
        status="仍有区分度",
        description=desc,
        summary=summary,
        source="https://gorilla.cs.berkeley.edu/leaderboard",
        label=f"BFCL v4 {label_cn}（{n} 题）",
        download=partial(download, [sid]),
        family="BFCL v4",
        group=group,
    )
    for sid, order, name_cn, label_cn, group, n, summary, desc in _SUBSET_META
]
