"""取数口径测试：证明「跑几题」会参与选代表成绩，且三处（排行榜 / 总览 / AI 总结）一致。

为什么需要它
-----------
这是**已经真实踩过的坑**：HumanEval 的 6 题冒烟测试跑出 100%，因为排行榜和成绩总览
只比正确率、完全不看跑了多少题，于是它会顶掉真正的 164 题成绩（当时只能手工删掉那条评测）。
AI 总结里本来有覆盖率判断，另外两处没有 —— 口径分裂成两套，所以修的时候把它们统一到
`app/scoring.py`，并加了本测试锁住行为。

这个测试**不花 API、不联网、不碰你的 data/app.db**（全程临时库），可以随时反复跑。
它断言的是「谁被选中」这种可证伪的行为，而不是实现细节：

  1. 同一模型跑过「6/6 部分」和「163/164 完整」-> 代表成绩必须是 163/164 那条；
  2. 只有 6/6 部分、没有完整评测 -> 仍然显示，但必须带 partial 标记（不隐藏数据）；
  3. 题库题量未知（full=0）-> 保守地**不**判为部分（宁可显示也不隐藏）；
  4. 排行榜与成绩总览这两个接口给出的成绩、题数、partial 完全一致。

用法::

    python scripts/verify_scoring.py
"""
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在建立任何数据库连接之前改掉 DB_PATH
from app import db  # noqa: E402


# 临时目录优先用系统 temp，但**文件沙箱可能禁止写工作区之外**（受限环境里往系统 temp
# 写文件会 PermissionError，sqlite 也因此在那边报 "unable to open database file"）。
# 所以不猜，写个探针文件试一下，不行就退回工作区内的目录（跑完会删）。
def _pick_tmp() -> pathlib.Path:
    try:
        d = pathlib.Path(tempfile.mkdtemp(prefix="llmbench-scoring-"))
        (d / ".probe").write_text("x", encoding="utf-8")
        return d
    except OSError:
        pass
    d = ROOT / ".scoring_tmp"
    # 上一次崩溃留下的库会让断言随机变红/绿（残留数据被当成这次的输入），先清干净
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(exist_ok=True)
    return d


_tmp_root = _pick_tmp()
db.DB_PATH = _tmp_root / "app.db"
db._local.conn = None
db.init_db()

from app import main, scoring  # noqa: E402
from app.benchmarks import FAMILIES, FAMILY_GROUPS  # noqa: E402

BENCH = "humaneval"
FULL = 164          # data/humaneval.jsonl 现有 164 题（读不到时下面的前置检查会报出来）

RESULTS: list = []


def check(name: str, got, want) -> None:
    RESULTS.append((name, got == want, f"期望 {want!r}，实际 {got!r}"))


def check_true(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail or "期望为真，实际为假"))


def add_eval(mid: int, done: int, correct: int, total: int | None = None) -> int:
    """造一条 status=done 的评测记录（不跑模型，直接落库）。"""
    return db.execute(
        "INSERT INTO evaluations(model_id, benchmark, total, done, correct, failed,"
        " status, total_latency_ms, prompt_tokens, completion_tokens, finished_at)"
        " VALUES(?,?,?,?,?,0,'done',?,?,?,datetime('now','localtime'))",
        (mid, BENCH, total or done, done, correct, done * 100, done * 10, done * 20),
    )


def rows_of(board_json: list) -> dict:
    """排行榜里 humaneval 那一榜的 {模型名: row}。"""
    for g in board_json:
        for b in g["boards"]:
            if b["benchmark"] == BENCH:
                return {r["model_name"]: r for r in b["rows"]}
    return {}


def scores_of(ov_json: dict) -> dict:
    """总览里 humaneval 那一行的 {model_id(str): cell}。"""
    for g in ov_json["groups"]:
        for b in g["benchmarks"]:
            if b["id"] == BENCH:
                return b["scores"]
    return {}


def main_() -> int:
    try:
        real_full = scoring.full_count(BENCH)
        if real_full != FULL:
            print(f"[前置检查失败] {BENCH} 题量实际为 {real_full}，脚本按 {FULL} 写死了断言。")
            print("  题库变了就更新本脚本里的 FULL（或改成不依赖具体数字的断言）。")
            return 1
        print(f"题库 {BENCH}: {real_full} 题（覆盖率阈值 {scoring.COVERAGE_OK:.0%}）")
        print("=" * 78)

        # ---- 单元层：pick_best 的规则本体 -------------------------------------
        # 故意让「部分评测」的正确率更高，看它会不会被误选
        partial_run = {"done": 6, "correct": 6, "id": 900}       # 100%
        complete_run = {"done": 163, "correct": 162, "id": 901}  # 99.39%

        p = scoring.pick_best([partial_run, complete_run], FULL)
        check("完整成绩被选中（部分成绩正确率更高也不选它）", p.best["id"], 901)
        check("best 正确率 = 完整那条", round(scoring.accuracy_of(p.best), 2), 99.39)
        check("best_complete 指向完整那条", p.best_complete["id"], 901)
        check("best_any 仍记录更高的部分成绩（供总结解释）", p.best_any["id"], 900)
        check_true("artifact=True（存在更高分的部分评测 = 刷分假象）", p.artifact)
        check_true("partial=False（代表成绩本身是完整的）", not p.partial)

        p2 = scoring.pick_best([partial_run], FULL)
        check("只有部分评测时仍然给出成绩（不隐藏数据）", p2.best["id"], 900)
        check_true("partial=True（要显式标注「部分」）", p2.partial)
        check_true("best_complete=None（确实没有完整评测）", p2.best_complete is None)
        check_true("artifact=False（无从比较，不算刷分）", not p2.artifact)

        p3 = scoring.pick_best([partial_run], 0)
        check_true("题库题量未知时不判为部分（保守显示）", not p3.partial)
        check_true("is_partial(full=0) 为假", not scoring.is_partial(partial_run, 0))

        check("没有评测时返回 None", scoring.pick_best([], FULL), None)

        # ---- 接口层：排行榜与总览必须给出同一答案 ---------------------------
        # 模型 A：先跑了 6 题冒烟（100%），后跑完整 163/164（99.39%）
        # 模型 B：只有 6 题冒烟（100%）—— 必须仍然可见，但带 partial
        mid_a = db.execute("INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
                           ("__scoring_complete__", "mock://local", ""))
        mid_b = db.execute("INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
                           ("__scoring_partial__", "mock://local", ""))
        add_eval(mid_a, 6, 6)          # 部分
        add_eval(mid_a, 163, 162)      # 完整
        add_eval(mid_b, 6, 6)          # 只有部分

        bm = main.leaderboard()
        ov = main.overview()

        r = rows_of(bm)
        check_true("排行榜包含模型 A", "__scoring_complete__" in r)
        check_true("排行榜包含模型 B", "__scoring_partial__" in r)
        if "__scoring_complete__" in r:
            ra = r["__scoring_complete__"]
            check("排行榜选中的是完整那次（正确率）", ra["accuracy"], 99.39)
            check("排行榜显示题数 = 163（不是 6）", ra["total"], 163)
            check_true("排行榜不标 partial", not ra["partial"])
            check("排行榜带出题库题量供前端显示覆盖率", ra["full_count"], FULL)
        if "__scoring_partial__" in r:
            rb = r["__scoring_partial__"]
            check("只有部分评测时排行榜仍显示成绩", rb["accuracy"], 100.0)
            check("题数如实显示 6", rb["total"], 6)
            check_true("并标 partial=True", rb["partial"])
            # 部分评测要排在完整评测后面，不能和完整成绩平起平坐
            order = [x["model_name"] for x in
                     next(b["rows"] for g in bm for b in g["boards"]
                          if b["benchmark"] == BENCH)]
            check_true("部分评测排在完整评测之后",
                       order.index("__scoring_partial__") > order.index("__scoring_complete__"),
                       f"实际顺序 {order}")

        s = scores_of(ov)
        da, db_ = s.get(str(mid_a)), s.get(str(mid_b))
        check_true("总览包含模型 A", da is not None)
        check_true("总览包含模型 B", db_ is not None)
        if da:
            check("总览与排行榜口径一致（正确率）", da["accuracy"], 99.39)
            check("总览与排行榜口径一致（题数）", da["total"], 163)
            check_true("总览不标 partial", not da["partial"])
        if db_:
            check("总览显示部分成绩", db_["accuracy"], 100.0)
            check_true("总览标 partial=True（供前端排除最高分高亮）", db_["partial"])

        # ---- 三处一致性：AI 总结也走同一个 pick_best ------------------------
        # summary 里的口径若和上面两处分裂，这里会红
        check_true("summary 复用 scoring.pick_best（不是自己那份拷贝）",
                   getattr(__import__("app.summary", fromlist=["x"]), "scoring", None) is scoring)

        # ---- 家族官方加权总分（BFCL v4）-----------------------------------
        # 这一段的目的是把**官方口径**本身钉住：权重写错、把子集平均当总分、
        # 缺组时不归一化 —— 都会让「总分」和官方榜对不上，而且页面看起来很正常。
        fid = "BFCL v4"
        gs = FAMILY_GROUPS[fid]
        check("家族：BFCL v4 的分组就是官方那 5 组",
              [g["id"] for g in gs], ["non_live", "live", "multi_turn", "hallucination", "agentic"])
        check("家族：各组官方权重与官方文档一致",
              [g["weight"] for g in gs], [0.10, 0.10, 0.30, 0.10, 0.40])
        check("家族：权重之和为 1", round(sum(g["weight"] for g in gs), 6), 1.0)
        check("家族：本项目提供 4 组（Agentic 那 40% 没有）",
              sum(1 for g in gs if g["subsets"]), 4)
        check("家族：Live 组有 4 个子集（官方也是 4 个）",
              len(next(g for g in gs if g["id"] == "live")["subsets"]), 4)
        check("家族：Agentic 组本项目没有子集",
              len(next(g for g in gs if g["id"] == "agentic")["subsets"]), 0)

        def score_of(gid, pct):
            """构造「某组的每个子集都跑了且都得 pct 分」的输入。"""
            return {s: pct for s in next(g for g in gs if g["id"] == gid)["subsets"]}

        # 四组都跑满：组内平均 → 组间按权重加权（缺 Agentic 40% → 按已跑组归一化）
        all4 = {}
        for gid, pct in [("non_live", 50.0), ("live", 80.0), ("multi_turn", 60.0), ("hallucination", 90.0)]:
            all4.update(score_of(gid, pct))
        comp = scoring.family_composite(all4, gs)
        hand = (50 * .10 + 80 * .10 + 60 * .30 + 90 * .10) / 0.60
        check("加权总分 = 组内平均后按官方权重复合（手算核对）", comp["score"], round(hand, 2))
        check("加权总分 ≠ 子集平均分（这正是要修的口径）",
              comp["score"] != round(sum(all4.values()) / len(all4), 2), True)
        check("权重覆盖 = 已跑组权重之和（60%）", comp["weight_covered"], 0.6)
        check("组覆盖 = 4/4（不把没提供的 Agentic 算进来）",
              (comp["groups_covered"], comp["groups_total"]), (4, 4))
        check("子集覆盖 = 16/16", (comp["subsets_run"], comp["subsets_total"]), (16, 16))
        check_true("四组都跑满时不算 partial / incomplete",
                   not comp["partial"] and not comp["incomplete"])

        # 只跑一组：分数就是那组的分，但权重覆盖必须如实报成 10%
        only_non_live = scoring.family_composite(score_of("non_live", 50.0), gs)
        check("只跑 Non-Live：分数 = 50", only_non_live["score"], 50.0)
        check("只跑 Non-Live：权重覆盖 10%", only_non_live["weight_covered"], 0.1)
        check_true("只跑一组算 incomplete", only_non_live["incomplete"] is True)

        # 组内只跑一部分子集：分数按已跑子集估，并标 partial
        hg = next(g for g in gs if g["id"] == "hallucination")["subsets"]
        part = scoring.family_composite({hg[0]: 80.0}, gs)
        check("只跑 Hallucination 的一半子集：分数 = 80", part["score"], 80.0)
        check("只跑一半子集时 run/total 如实", (part["groups"][3]["run"], part["groups"][3]["total"]), (1, 2))
        check_true("只跑一半子集时标 partial", part["partial"] is True)

        check("没有任何子集时返回 None", scoring.family_composite({}, gs), None)

        # ---- 排行榜 / 总览：家族折叠后的口径 ---------------------------------
        mid_f = db.execute("INSERT INTO models(name, base_url, api_key) VALUES(?,?,?)",
                           ("__family__", "mock://local", ""))
        non_live = next(g for g in gs if g["id"] == "non_live")["subsets"]
        live = next(g for g in gs if g["id"] == "live")["subsets"]
        for sid in non_live[:2]:                     # 只跑 Non-Live 的两个子集，都拿 50%
            db.execute("INSERT INTO evaluations(model_id, benchmark, total, done, correct, failed,"
                       " status, total_latency_ms) VALUES(?,?,?,?,?,0,'done',100)",
                       (mid_f, sid, 10, 10, 5))
        lb2 = main.leaderboard()
        agent = next(c for c in lb2 if c["id"] == "agent")
        check("排行榜：家族子集不再逐个进「分项」榜", len(agent["boards"]), 0)
        check("排行榜：家族进 families 折叠块", [f["id"] for f in agent["families"]], [fid])
        check("排行榜：家族在综合榜里算「一项」", agent["n_benchmarks"], 1)
        frow = next(r for r in agent["families"][0]["rows"] if r["model_name"] == "__family__")
        check("排行榜：家族行给出官方加权总分（只跑了一组 → 就是那组的分）", frow["score"], 50.0)
        check("排行榜：家族行给出权重覆盖（10%）", frow["weight_covered"], 0.1)
        comb = next(m for m in agent["combined"] if m["model_name"] == "__family__")
        check("排行榜：综合榜的有效覆盖量 = 家族权重覆盖（排序用）", comb["coverage"], 0.1)
        check("排行榜：综合榜把家族的权重覆盖也带出来给前端显示",
              [f["weight_covered"] for f in comb["families"]], [0.1])
        # 总览：家族子集仍逐行显示（矩阵的行就是子集），但要带官方分组标签
        ov2 = main.overview()
        ovb = {b["id"]: b for g in ov2["groups"] for b in g["benchmarks"]}
        check("总览：家族子集带官方分组名", ovb[non_live[0]]["group_name"], "Non-Live")
        check("总览：家族子集带官方权重", ovb[non_live[0]]["group_weight"], 0.1)
        check("总览：Live 子集标 Live / 10%",
              (ovb[live[0]]["group_name"], ovb[live[0]]["group_weight"]), ("Live", 0.1))

        print(f"{'检查项':<52}{'结果'}")
        print("-" * 78)
        bad = 0
        for name, ok, detail in RESULTS:
            print(f"{name:<52}{'OK' if ok else 'FAIL'}")
            if not ok:
                bad += 1
                print(f"    {detail}")
        print("=" * 78)
        if bad:
            print(f"{bad}/{len(RESULTS)} 项不通过。")
            return 1
        print(f"全部 {len(RESULTS)} 项通过：取数口径正确，且排行榜/总览/AI 总结一致。")
        return 0
    finally:
        # Windows 上文件被连接占着就删不掉，先关连接再删目录
        try:
            db.get_conn().close()
            db._local.conn = None
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(_tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_())
