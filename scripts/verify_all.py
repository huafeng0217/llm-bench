"""一键跑完全部自检脚本。

为什么需要它
------------
自检脚本现在有 11 个，靠人记着逐个跑并不现实 —— 我自己就差点在改完核心分派后
只跑了其中一个。这个入口按依赖分档、逐个跑、最后给一张汇总表：

  - **纯计算**（不需要 Docker / 网络）：verify_imports、verify_code_assembly、verify_datasets、
    verify_scoring、verify_summary、verify_summaries
  - **需要 Node**（只有前端模板一项）：verify_web_templates.mjs
  - **需要 Docker**：verify_dispatch、verify_sandbox、verify_humaneval、verify_livecodebench

Docker / Node 不可用时后者标成 SKIP 而不是 FAIL —— 那说明环境不具备，不是代码坏了。

用法::

    python scripts/verify_all.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPTS = [
    # (脚本, 是否需要 Docker, 一句话说明)
    ("verify_imports.py", False, "静态检查：用到但未定义/未 import 的名字"),
    ("verify_datasets.py", False, "题库与元数据：行数缓存 / 原子写 / 卡片文案 / 题量对账"),
    ("verify_code_assembly.py", False, "代码题拼接：题目自带辅助函数 / 竞赛题不拼题面"),
    ("verify_models.py", False, "模型用途：判别器不能被评测 / 改类型二次确认（临时库）"),
    ("verify_scoring.py", False, "取数口径：完整/部分评测 + 家族官方加权总分（临时库）"),
    ("verify_safety.py", False, "安全评测：判分方向 / 裁判约束 / 判分失败处理（临时库，mock 裁判）"),
    ("verify_summary.py", False, "统计层：刷分假象护栏 / 显著性 / 数据一致性"),
    ("verify_summaries.py", False, "AI 总结：分类覆盖 / 自造名字 / 显著差异 / 数字可追溯"),
    ("verify_dispatch.py", True, "判分分派：10 条用例覆盖 6 种题型（含 2 选 1 / 5 选 1）"),
    ("verify_sandbox.py", True, "沙箱隔离：8 项攻击载荷 + 判分链路"),
    ("verify_humaneval.py", True, "HumanEval：代码抽取 + 164 道官方参考解法"),
    ("verify_livecodebench.py", True, "LiveCodeBench：测试用例解码 + 判分器"),
]

NODE_SCRIPTS = [
    # 前端是纯拼字符串、没有构建步骤，模板分支只有渲染一遍才验证得了
    ("verify_web_templates.mjs", "前端模板：部分评测标记 / BFCL 家族折叠与加权总分（不需要浏览器）"),
]


def docker_ok() -> bool:
    try:
        from app import sandbox
        ok, _ = sandbox.docker_available()
        return ok
    except Exception:  # noqa: BLE001
        return False


def main():
    has_docker = docker_ok()
    node = shutil.which("node")
    print(f"Docker: {'可用' if has_docker else '不可用（需要 Docker 的项会跳过）'}")
    print(f"Node:   {node or '不可用（前端模板那项会跳过）'}")
    print(f"Python: {sys.executable}")
    print("=" * 78)

    results = []
    for name, needs_docker, desc in SCRIPTS:
        if needs_docker and not has_docker:
            print(f"\n[SKIP] {name} —— {desc}（需要 Docker）")
            results.append((name, "SKIP", desc))
            continue
        print(f"\n{'#' * 78}\n# {name} —— {desc}\n{'#' * 78}")
        rc = subprocess.run([sys.executable, str(ROOT / "scripts" / name)]).returncode
        results.append((name, "PASS" if rc == 0 else "FAIL", desc))

    for name, desc in NODE_SCRIPTS:
        if not node:
            print(f"\n[SKIP] {name} —— {desc}（需要 Node）")
            results.append((name, "SKIP", desc))
            continue
        print(f"\n{'#' * 78}\n# {name} —— {desc}\n{'#' * 78}")
        rc = subprocess.run([node, str(ROOT / "scripts" / name)]).returncode
        results.append((name, "PASS" if rc == 0 else "FAIL", desc))

    print("\n" + "=" * 78)
    print(f"{'脚本':<28}{'结果':<7}说明")
    print("-" * 78)
    for name, status, desc in results:
        print(f"{name:<28}{status:<7}{desc}")

    failed = [n for n, s, _ in results if s == "FAIL"]
    skipped = [n for n, s, _ in results if s == "SKIP"]
    print("=" * 78)
    if failed:
        print(f"结论：{len(failed)} 个脚本失败 -> {', '.join(failed)}")
        return 1
    missing = []
    if skipped and not has_docker:
        missing.append("Docker")
    if skipped and not node:
        missing.append("Node")
    tail = f"（{len(skipped)} 个因缺 {'/'.join(missing) or '依赖'} 跳过）" if skipped else ""
    print(f"结论：全部通过{tail}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
