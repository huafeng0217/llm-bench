"""续跑一个没跑完的评测任务（进程被杀 / 服务重启后用）。

评测跑到一半进程没了，数据库里那条任务会残留 status='running'，
但已经跑完的题都还在 eval_items 里。这个脚本会**跳过已完成的题**，
只补跑剩下的，并把计数接上去 —— 已经花掉的 API 调用不用重来。

用法::

    python scripts/resume_eval.py 115        # 续跑任务 115
    python scripts/resume_eval.py            # 列出所有卡住的任务
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, engine  # noqa: E402


def list_stuck():
    rows = db.query(
        "SELECT e.id, e.benchmark, e.status, e.done, e.total, m.name AS model"
        " FROM evaluations e LEFT JOIN models m ON m.id = e.model_id"
        " WHERE e.status IN ('running', 'pending') ORDER BY e.id")
    if not rows:
        print("没有卡住的任务。")
        return
    print("卡住的任务（status 还是 running/pending，但可能已经没有进程在跑了）：")
    for r in rows:
        print(f"  #{r['id']}  {r['model']} × {r['benchmark']}  {r['done']}/{r['total']}  [{r['status']}]")


def main():
    if len(sys.argv) < 2:
        list_stuck()
        print("\n用法：python scripts/resume_eval.py <任务号>")
        return 0
    eid = int(sys.argv[1])
    ev = db.query_one("SELECT * FROM evaluations WHERE id=?", (eid,))
    if not ev:
        print(f"任务 {eid} 不存在")
        return 2
    done = db.query_one("SELECT COUNT(*) AS n FROM eval_items WHERE eval_id=?", (eid,))["n"]
    print(f"任务 #{eid}：{ev['benchmark']}，库里已有 {done}/{ev['total']} 题，开始续跑…")
    asyncio.run(engine.run_evaluation(eid, resume=True))
    after = db.query_one("SELECT status, done, correct, total FROM evaluations WHERE id=?", (eid,))
    print(f"完成：status={after['status']}  {after['correct']}/{after['done']} 题正确"
          f"（共 {after['total']} 题）"
          + (f"  正确率 {after['correct'] / max(after['done'], 1) * 100:.2f}%" if after["done"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
