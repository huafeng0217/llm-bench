"""沙箱安全验证：用「攻击性代码」逐项验证 Docker 隔离是否真的生效。

思路：不信任配置，直接跑攻击载荷。每一项都故意做危险动作：
  - 读宿主项目的数据库 / API Key 文件
  - 联网外连
  - 往根文件系统写文件
  - fork 炸弹
  - 死循环
  - 吃爆内存

**全部被挡住**才说明沙箱可用；只要有任意一项得手，就不该把代码类基准接进评测。

判定方式不靠退出码（各内核信号/解释器行为不一致），而是看代码自己打印的
标记：`SUCCEEDED` = 攻击得手，`BLOCKED` = 被挡住。

用法::

    python scripts/verify_sandbox.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sandbox  # noqa: E402

HOST_DB = str(Path(__file__).resolve().parent.parent / "data" / "app.db")
HOST_KEYS = str(Path(__file__).resolve().parent.parent / "data" / "models.json")

# 1) 读宿主的数据库 / 密钥文件，并试探几个常见的「逃逸出口」
READ_HOST = r'''
import sys
paths = [
    HOST_DB,
    HOST_KEYS,
    "/workspace/../data/app.db",
    "/workspace/../../data/app.db",
    "/data/app.db",
    "/host/data/app.db",
    "/proc/1/root" + HOST_DB,
]
for p in paths:
    try:
        with open(p, "rb") as f:
            blob = f.read(64)
        print("SUCCEEDED: 读到宿主文件", p, len(blob), "字节")
        sys.exit(0)
    except OSError:
        pass
print("BLOCKED: 宿主路径在容器内全部不可见")
sys.exit(7)
'''.replace("HOST_DB", repr(HOST_DB)).replace("HOST_KEYS", repr(HOST_KEYS))

# 2) 联网
NETWORK = r'''
import socket, sys
try:
    s = socket.create_connection(("8.8.8.8", 53), timeout=4)
    print("SUCCEEDED: 连上了 8.8.8.8:53")
    s.close()
    sys.exit(0)
except OSError as e:
    print("BLOCKED: 无法建连 ->", e)
    sys.exit(7)
'''

# 3) 写根文件系统
WRITE_ROOT = r'''
import sys
for p in ["/etc/evil", "/usr/lib/evil.so", "/workspace/evil", "/main.py"]:
    try:
        with open(p, "w") as f:
            f.write("pwned")
        print("SUCCEEDED: 写成功", p)
        sys.exit(0)
    except OSError as e:
        pass
print("BLOCKED: 只读根文件系统，任何位置都写不了")
sys.exit(7)
'''

# 4) 可写的 /tmp（这一项**应当成功**，证明 tmpfs 限额挂载正常）
WRITE_TMP = r'''
import os, sys
try:
    with open("/tmp/probe.txt", "w") as f:
        f.write("ok")
    size = os.path.getsize("/tmp/probe.txt")
    print("SUCCEEDED: /tmp 可写，", size, "字节")
    sys.exit(0)
except OSError as e:
    print("BLOCKED: /tmp 不可写 ->", e)
    sys.exit(7)
'''

# 5) fork 炸弹
FORK_BOMB = r'''
import os, sys, time
n = 0
try:
    for _ in range(600):
        pid = os.fork()
        if pid == 0:
            time.sleep(60)      # 子进程赖着不走，逼近进程数上限
            os._exit(0)
        n += 1
except OSError as e:
    print("BLOCKED: 第", n + 1, "次 fork 被拒 ->", e)
    sys.exit(7)
print("SUCCEEDED: 连开", n, "个进程未被拦")
sys.exit(0)
'''

# 6) 死循环
INFINITE_LOOP = r'''
import sys
print("开跑死循环", flush=True)
sys.stdout.flush()
while True:
    pass
'''

# 7) 吃爆内存
MEMORY_BOMB = r'''
import sys
try:
    s = "x" * (10 ** 9)          # 约 1GB
    print("SUCCEEDED: 申请到", len(s), "字节")
    sys.exit(0)
except MemoryError as e:
    print("BLOCKED: MemoryError ->", e)
    sys.exit(7)
'''

# 8) 工作区里有没有多出来别的东西（应当只有挂载进去的那一个文件）
WORKSPACE_VIEW = r'''
import os, sys
items = sorted(os.listdir("/workspace"))
print("容器内 /workspace =", items)
if items == ["main.py"]:
    print("SUCCEEDED: 只暴露了挂载的单个文件")
    sys.exit(0)
print("BLOCKED: 工作区出现了额外内容")
sys.exit(7)
'''

CASES = [
    ("读宿主项目文件/密钥", READ_HOST, "blocked", 15),
    ("容器内联网外连", NETWORK, "blocked", 15),
    ("写系统文件（只读根）", WRITE_ROOT, "blocked", 15),
    ("写 /tmp（tmpfs 应可用）", WRITE_TMP, "allowed", 15),
    ("fork 炸弹（pids-limit）", FORK_BOMB, "blocked", 20),
    ("死循环（timeout 兜底）", INFINITE_LOOP, "timeout", 5),
    ("吃爆内存（memory 上限）", MEMORY_BOMB, "blocked", 20),
    ("工作区挂载范围", WORKSPACE_VIEW, "allowed", 15),
]


def judge(expect, r):
    out = r["stdout"] or ""
    if expect == "timeout":
        return r["timed_out"], "容器被 timeout 强杀（退出码 %s）" % r["exit_code"]
    if expect == "blocked":
        if "SUCCEEDED" in out:
            return False, "攻击得手！"
        for line in out.splitlines():
            if line.startswith("BLOCKED"):
                return True, line
        tail = (r["stderr"] or "").strip().splitlines()
        return True, "已被拦截（退出码 %s）%s" % (r["exit_code"], tail[-1] if tail else "")
    if expect == "allowed":
        for line in out.splitlines():
            if line.startswith("SUCCEEDED"):
                return True, line
        return False, "预期可用但被拦：%s" % ((r["stderr"] or "").strip()[:120])
    raise ValueError(expect)


def run_functional():
    """功能验证：沙箱不只是「能挡攻击」，还得能正确判分。

    用 HumanEval/0 真题走一遍完整链路：正确解法应当通过，错误解法应当失败，
    语法错误应当失败 —— 三种结果都要准。
    """
    ref = (
        "from typing import List\n\n\n"
        "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
        "    for i, a in enumerate(numbers):\n"
        "        for b in numbers[i + 1:]:\n"
        "            if abs(a - b) < threshold:\n"
        "                return True\n"
        "    return False\n"
    )
    test = (
        "def check(candidate):\n"
        "    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True\n"
        "    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05) == False\n"
        "    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.95) == True\n"
        "    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.8) == False\n"
    )
    entry = "has_close_elements"
    wrong = ref.replace("            if abs(a - b) < threshold:", "            if abs(a - b) < threshold / 2:")
    broken = ref.replace("return False", "return False (")

    cases = [
        ("正确解法（应判通过）", ref, True),
        ("错误解法（应判失败）", wrong, False),
        ("语法错误（应判失败）", broken, False),
    ]
    print("\n功能验证：用 HumanEval/0 真题跑完整判分链路\n" + "=" * 72)
    results = []
    for name, solution, want_pass in cases:
        r = sandbox.run_python_tests(solution, test, entry)
        good = r["passed"] is want_pass
        results.append((name, good))
        detail = "exit=%s" % r["exit_code"]
        if not r["passed"]:
            err = (r["stderr"] or "").strip().splitlines()
            detail += " | " + (err[-1][:60] if err else "")
        print(f"[{'通过' if good else '失败'}] {name:<22} {r['duration_ms']:>6}ms  {detail}")
    return results


def main():
    ok, msg = sandbox.docker_available()
    print("Docker 状态:", msg)
    if not ok:
        print("\n[中止] 沙箱不可用，请先启动 Docker Desktop。")
        return 1

    print("\n开始验证隔离边界（每项都会真的尝试攻击）\n" + "=" * 72)
    results = []
    for name, code, expect, timeout in CASES:
        r = sandbox.run_code(code, timeout=timeout)
        passed, detail = judge(expect, r)
        results.append((name, passed))
        flag = "通过" if passed else "失败"
        print(f"[{flag}] {name:<26} {r['duration_ms']:>6}ms  {detail}")

    results += run_functional()

    print("=" * 72)
    bad = [n for n, p in results if not p]
    if bad:
        print(f"结论：沙箱**不可用**，{len(bad)} 项未达预期 -> {', '.join(bad)}")
        print("在这些问题解决前，不要接入任何会执行模型代码的基准。")
        return 1
    print(f"结论：{len(results)} 项全部符合预期，隔离边界有效、判分链路正确。")
    print("模型生成的代码无法读取本项目数据、无法联网、无法写文件、无法拖垮本机。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
