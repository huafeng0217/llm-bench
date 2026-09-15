"""代码执行沙箱：在一次性 Docker 容器里运行模型生成的代码。

为什么需要它
------------
代码类基准（HumanEval / LiveCodeBench 等）的判分方式是**真的把模型生成的代码
跑起来**，所以必须隔离。

安全边界完全由 docker run 的参数实现（不依赖代码层面的字符串拦截，那种
容易被 `__import__` 之类绕过）：

    --network none                  禁网：断掉一切外联
    --memory 256m --memory-swap 256m 内存上限（超了被 OOM 杀）
    --cpus 0.5                      CPU 上限
    --pids-limit 64                 进程/线程数上限（挡 fork 炸弹）
    --read-only                     根文件系统只读：写不了任何系统文件
    --tmpfs /tmp:size=16m           只给 16MB 可写的临时目录
    -v <单文件>:/workspace/xxx:ro    只读挂载题目文件，**不挂项目目录**
    --rm                            跑完即销毁

**关键点**：容器里看不到宿主文件系统（只挂了本次运行生成的那几个文件），
所以模型代码既读不到你的 data/app.db、API Key，也写不了你的项目；
同时不联网，无法外传任何东西。

两类判分接口
------------
- :func:`run_python_tests` —— HumanEval / MBPP 那种「函数补全 + 官方单元测试」，
  判分就是整段程序退出码是否为 0。
- :func:`run_stdio_tests`  —— LiveCodeBench 那种「竞赛题」，逐条用例喂 stdin
  或调用函数，比对输出，按通过条数给分。

用法::

    from app import sandbox
    sandbox.run_code("print(1 + 1)")["passed"]   # True
"""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

TMP_ROOT = Path(__file__).resolve().parent.parent / ".sandbox"

IMAGE = "python:3.11-slim"
MEMORY = "256m"
# stdio 判分要把用例喂进容器：个别题目用例合计数十 MB，给宽一点
STDIO_MEMORY = "512m"
CPUS = "0.5"
PIDS_LIMIT = "64"
TMPFS = "/tmp:size=64m"
DEFAULT_TIMEOUT = 10          # 代码本身的执行上限（秒）
DOCKER_EXTRA_WAIT = 15        # 宿主端兜底等待（容器启动 + 清理）


class SandboxUnavailable(RuntimeError):
    """Docker 不可用（引擎没启动 / 没装 / 没拉镜像）。"""


_DOCKER_CACHE = {"t": 0.0, "ok": None, "msg": ""}


def docker_available(max_age: float = 30.0) -> tuple:
    """检查 Docker 是否可用，返回 (ok, message)。

    给前端/命令行一个明确的失败原因，而不是让评测跑到一半才报错。

    结果缓存 30 秒：探测本身要跑两条 docker 命令（约 250ms），
    而评测时每道题都会调一次，不缓存的话光探测就要多花几十秒。
    """
    now = time.time()
    if _DOCKER_CACHE["ok"] is not None and now - _DOCKER_CACHE["t"] < max_age:
        return _DOCKER_CACHE["ok"], _DOCKER_CACHE["msg"]
    ok, msg = _probe_docker()
    _DOCKER_CACHE.update(t=now, ok=ok, msg=msg)
    return ok, msg


def _probe_docker() -> tuple:
    try:
        r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, "未找到 docker 命令（Docker 未安装或不在 PATH）"
    except subprocess.TimeoutExpired:
        return False, "docker version 超时"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        return False, "Docker 引擎未运行：" + (msg[0] if msg else "未知错误")
    ver = (r.stdout or "").strip()
    try:
        img = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Size}}"],
                             capture_output=True, text=True, timeout=30)
        if img.returncode != 0:
            return False, f"Docker 可用（{ver}）但缺少镜像 {IMAGE}，请先执行：docker pull {IMAGE}"
    except Exception:  # noqa: BLE001
        pass
    return True, f"Docker {ver} · 镜像 {IMAGE} 就绪"


def _docker_run(files: dict, argv: list, timeout: int, env: dict | None = None,
                mem: str = MEMORY) -> dict:
    """把 files（容器内路径 → 文件内容）只读挂进一次性容器并执行 argv。

    所有文件都写在项目内 .sandbox/ 下的临时目录里（用完即删），
    容器启动后只应看到本函数挂进去的那几个文件。
    """
    ok, msg = docker_available()
    if not ok:
        raise SandboxUnavailable(msg)

    run_dir = TMP_ROOT / f"run-{uuid.uuid4().hex[:12]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    # 唯一的容器名：超时后能精确 kill，不会误伤别的容器
    name = f"llmbench-sbx-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm",
        "--name", name,
        "--network", "none",
        "--memory", mem, "--memory-swap", mem,
        "--cpus", CPUS,
        "--pids-limit", PIDS_LIMIT,
        "--read-only",
        "--tmpfs", TMPFS,
        "-w", "/workspace",
    ]
    try:
        for i, (cpath, content) in enumerate(files.items()):
            local = run_dir / f"f{i}_{Path(cpath).name}"
            local.write_text(content, encoding="utf-8")
            cmd += ["-v", f"{local}:{cpath}:ro"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [
            IMAGE,
            # 容器内再用 coreutils 的 timeout 包一层：能区分「代码超时」与「docker 卡住」。
            # 先发 TERM（退出码规范为 124），给 2 秒宽限；若代码捕获/忽略 TERM，再由 -k 强杀。
            # 注意：-s KILL 会让退出码变成 137，和 OOM 撞码，所以这里坚持用 TERM。
            "timeout", "-s", "TERM", "-k", "2", str(timeout),
        ] + argv
        t0 = time.time()
        timed_out = False
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout + DOCKER_EXTRA_WAIT,
                               encoding="utf-8", errors="replace")
            code_rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired as e:
            # 宿主端兜底：容器内 timeout 没生效（例如卡在启动）时强杀
            timed_out = True
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
            code_rc = -1
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        elapsed = time.time() - t0
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    # 退出码 124 = timeout 正常超时；137 通常是 OOM，但如果跑满了整个时限才被杀，
    # 说明代码捕获/忽略了 TERM，被 timeout 的 -k 宽限强杀 —— 同样算超时。
    if code_rc == 124 or (code_rc == 137 and elapsed >= timeout):
        timed_out = True
    return {
        "ok": True,
        "passed": code_rc == 0,
        "exit_code": code_rc,
        "stdout": out[:8000],
        "stderr": err[:4000],
        "timed_out": timed_out,
        "error": None,
        "duration_ms": round(elapsed * 1000),
    }


def run_code(code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """在隔离容器里执行一段 Python 代码。

    返回 ``ok / passed / exit_code / stdout / stderr / timed_out / error / duration_ms``。
    其中 ``passed`` 表示代码正常退出（退出码 0）。
    """
    return _docker_run({"/workspace/main.py": code},
                       ["python", "/workspace/main.py"], timeout=timeout)


def run_python_tests(solution: str, test_code: str, entry_point: str,
                     timeout: int = DEFAULT_TIMEOUT) -> dict:
    """把「模型写的解法」和「官方单元测试」拼成一个程序，在沙箱里跑。

    HumanEval / MBPP 的官方测试都是 ``def check(candidate): ...`` 的形式，
    末尾再调一次 ``check(entry_point)`` 即可。判分就是 ``exit_code == 0``。

    返回值和 :func:`run_code` 相同，另外多一个 ``program`` 字段（完整程序源码），
    方便把失败的用例原样存进 eval_items 排查。
    """
    program = "\n".join([
        solution.rstrip(),
        "",
        test_code.rstrip(),
        "",
        f"check({entry_point})",
        "",
    ])
    r = run_code(program, timeout=timeout)
    r["program"] = program
    return r


# ---------- stdin / 函数调用式判分（LiveCodeBench 等竞赛题） ----------

# 容器内的判分器。逐条用例跑，输出一行 __LCB__ 开头的 JSON 结果。
# 设计要点（都是踩过的坑）：
#   1. cases.jsonl **逐行读**而不是整份 JSON —— 个别题目的用例合计数十 MB，
#      一次性载入会在 512MB 的容器里 OOM。
#   2. 每个用例起**独立子进程**：既能真正超时杀掉死循环，也避免用例之间的
#      全局状态互相污染（竞赛题里很常见）。
#   3. 设**全局时间预算**：跑超了剩余用例记 SKIP，但仍然把结果打印出来 ——
#      否则容器被外层 timeout 杀掉就什么都拿不到，无法诊断。
STDIO_HARNESS = r'''"""容器内判分器：逐条跑测试用例。不依赖任何第三方库。

判定口径**逐条对齐官方 lcb_runner/evaluation/testing_util.py**：
  - 函数式（LeetCode）：input 按换行拆开、**逐行** JSON 解析成参数列表，再 fn(*args)；
    比较用 prediction == expected（仅把最外层 tuple 当 list，与官方一致）。
  - stdin 式（AtCoder）：先按行 strip 比较行数，逐行先比字符串，
    不等则把该行按空白切成 token、逐个转 Decimal 再比（官方就是这么做的：
    用 Decimal 而不是浮点容差，避免 np.isclose(5e16, 5e16+1) 这类误判为相等）。

设计要点（都是踩过的坑）：
  1. cases.jsonl **逐行读**而不是整份 JSON —— 个别题目的用例合计数十 MB，
     一次性载入会在容器里 OOM。
  2. 每个用例起**独立子进程**：既能真正超时杀掉死循环，也避免用例之间的
     全局状态互相污染（竞赛题里很常见）。
  3. 参数**走 stdin 而不是命令行**：Linux 单个参数上限 128KB，LeetCode 的大数组
     输入直接 argv 会 E2BIG（实测 63 道函数式题里有 2 道因此失败）。
  4. 设**全局时间预算**：跑超了剩余用例记 SKIP，但仍把结果打印出来 ——
     否则容器被外层 timeout 杀掉就什么都拿不到，无法诊断。
"""
import json
import os
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation

WORKSPACE = "/workspace"
SOLUTION = os.path.join(WORKSPACE, "solution.py")
CASES = os.path.join(WORKSPACE, "cases.jsonl")
RUNNER = "/tmp/lcb_runner.py"
CASE_TIMEOUT = float(os.environ.get("CASE_TIMEOUT", "6"))
TOTAL_BUDGET = float(os.environ.get("TOTAL_BUDGET", "90"))
FN_NAME = os.environ.get("FN_NAME", "")

# 函数调用式（LeetCode）用例的调用器。
# 参数从 **stdin** 读，不塞进 argv（大数组会超过 Linux 单参数 128KB 上限）。
RUNNER_SRC = """
import importlib.util, json, sys
sys.dont_write_bytecode = True
name = sys.argv[1]
spec = importlib.util.spec_from_file_location("solution", "/workspace/solution.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# 官方口径：代码里有 class Solution 就实例化它，否则用模块本身（允许顶层函数）
cands = [mod.Solution()] if hasattr(mod, "Solution") else []
cands.append(mod)
fn = None
for cand in cands:
    got = getattr(cand, name, None)
    if callable(got):
        fn = got
        break
if fn is None:
    print("solution has no callable " + name, file=sys.stderr)
    sys.exit(2)
# 官方口径：input 按换行拆开、逐行 JSON 解析，每行是一个位置参数
args = [json.loads(ln) for ln in sys.stdin.read().split("\\n") if ln.strip()]
json.dump(fn(*args), sys.stdout, ensure_ascii=False, default=str)
"""


def get_stripped_lines(val):
    """官方同款：整体 strip，再逐行 strip（空行不会被造出来）。"""
    return [ln.strip() for ln in (val or "").strip().split("\n")]


def same_text(out, exp):
    """stdin 式输出比对，逐条对齐官方 grade_stdio。"""
    a, b = get_stripped_lines(out), get_stripped_lines(exp)
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        try:
            if [Decimal(t) for t in x.split()] == [Decimal(t) for t in y.split()]:
                continue
        except (InvalidOperation, ValueError):
            return False
        return False
    return True


def deep_eq(a, b):
    """函数式返回值比对，对齐官方：只把最外层 tuple 当 list，其余用 ==。"""
    if isinstance(a, tuple):
        a = list(a)
    return a == b


def run_case(case, timeout):
    """跑一条用例，返回 (判定, 实际输出片段, 错误输出片段)。"""
    tt = case.get("testtype") or ("functional" if FN_NAME else "stdin")
    feed = case.get("input") or ""
    if tt == "functional":
        argv = [sys.executable, RUNNER, FN_NAME]
    else:
        argv = [sys.executable, SOLUTION]
    try:
        p = subprocess.run(argv, input=feed, capture_output=True, text=True,
                           timeout=timeout, cwd=WORKSPACE)
    except subprocess.TimeoutExpired:
        return "TLE", "", ""
    except OSError as e:
        # 起不了子进程（内存/进程数/参数超限）—— 明确报出来，别伪装成答案错误
        return "RE", "", "spawn failed: " + repr(e)
    if p.returncode != 0:
        return "RE", p.stdout or "", p.stderr or ""
    if tt == "functional":
        try:
            got, exp = json.loads(p.stdout), json.loads(case.get("output") or "null")
        except ValueError:
            return "PE", p.stdout or "", "返回值不是合法 JSON"
        return ("OK" if deep_eq(got, exp) else "WA"), (p.stdout or ""), ""
    return ("OK" if same_text(p.stdout, case.get("output")) else "WA"), (p.stdout or ""), ""


def main():
    with open(RUNNER, "w", encoding="utf-8") as f:
        f.write(RUNNER_SRC)
    deadline = time.monotonic() + TOTAL_BUDGET
    verdicts, fail, total = [], None, 0
    with open(CASES, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            total += 1
            case = json.loads(line)
            left = deadline - time.monotonic()
            if left <= 0:
                verdicts.append("SKIP")
                continue
            v, got, err = run_case(case, min(CASE_TIMEOUT, left))
            verdicts.append(v)
            if v != "OK" and fail is None:
                fail = {"idx": i, "verdict": v,
                        "input": (case.get("input") or "")[:400],
                        "expected": (case.get("output") or "")[:400],
                        "got": (got or "")[:400],
                        "stderr": (err or "").strip()[-300:]}
    print("__LCB__" + json.dumps({"verdicts": verdicts, "fail": fail, "total": total},
                                 ensure_ascii=False))


main()
'''

# 判题环境的公共前置。**照抄官方 lcb_runner 的 import_string**，不是自己拼的：
# LeetCode 题面直接用 List/deque/defaultdict/inf 等名字而不写 import，
# 少一个都会在定义函数时就 NameError。官方还额外拉了 recursionlimit。
# 注意：这段拼在解法前面会让报错行号整体偏移，排查时记得减去这里的行数。
CODE_PREAMBLE = (
    "from string import *\n"
    "from re import *\n"
    "from datetime import *\n"
    "from collections import *\n"
    "from heapq import *\n"
    "from bisect import *\n"
    "from copy import *\n"
    "from math import *\n"
    "from random import *\n"
    "from statistics import *\n"
    "from itertools import *\n"
    "from functools import *\n"
    "from operator import *\n"
    "from io import *\n"
    "from sys import *\n"
    "from json import *\n"
    "from builtins import *\n"
    "from typing import *\n"
    "import string\n"
    "import re\n"
    "import datetime\n"
    "import collections\n"
    "import heapq\n"
    "import bisect\n"
    "import copy\n"
    "import math\n"
    "import random\n"
    "import statistics\n"
    "import itertools\n"
    "import functools\n"
    "import operator\n"
    "import io\n"
    "import sys\n"
    "import json\n"
    "sys.setrecursionlimit(50000)\n"
)


def run_stdio_tests(solution: str, cases: list, timeout: int = 120, case_timeout: float = 6,
                    budget: float = 90, fn_name: str = "", mem: str = STDIO_MEMORY) -> dict:
    """竞赛题判分：逐条用例喂 stdin（或调用函数），比对输出。

    返回 ``run_code`` 的那几个字段，另加：

    - ``n_pass`` / ``n_total``：通过的用例数 / 总用例数
    - ``verdicts``：每条用例的判定（OK / WA / TLE / RE / PE / SKIP）
    - ``fail``：第一条失败用例的输入、期望、实际（各截断 400 字），便于排查

    ``passed`` 只有在**全部用例通过**时才为真（与 LiveCodeBench 的 pass@1 口径一致）。
    """
    if not cases:
        return {"ok": True, "passed": False, "exit_code": None, "stdout": "", "stderr": "",
                "timed_out": False, "error": "没有可用的测试用例", "duration_ms": 0,
                "n_pass": 0, "n_total": 0, "verdicts": [], "fail": None}
    body = CODE_PREAMBLE + "\n" + solution.rstrip() + "\n"
    cases_jsonl = "\n".join(json.dumps(c, ensure_ascii=False) for c in cases)
    r = _docker_run(
        {"/workspace/solution.py": body,
         "/workspace/cases.jsonl": cases_jsonl,
         "/workspace/harness.py": STDIO_HARNESS},
        ["python", "/workspace/harness.py"],
        timeout=timeout, mem=mem,
        env={"CASE_TIMEOUT": str(case_timeout), "TOTAL_BUDGET": str(budget),
             "FN_NAME": fn_name, "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONIOENCODING": "utf-8"},
    )

    payload = None
    for line in (r["stdout"] or "").splitlines():
        if line.startswith("__LCB__"):
            try:
                payload = json.loads(line[len("__LCB__"):])
            except ValueError:
                payload = None
    if payload is None:
        # 判分器自己都没跑完（多半是容器被 OOM 或超时杀掉）：拿不到逐条判定
        r["n_pass"] = r["n_total"] = 0
        r["verdicts"] = []
        r["fail"] = None
        r["error"] = "判分器未输出结果：" + ((r["stderr"] or "").strip()[-200:] or "容器被终止")
        r["passed"] = False
        return r

    verdicts = payload.get("verdicts") or []
    n_pass = sum(1 for v in verdicts if v == "OK")
    r["n_pass"] = n_pass
    r["n_total"] = payload.get("total") or len(verdicts)
    r["verdicts"] = verdicts
    r["fail"] = payload.get("fail")
    r["passed"] = bool(verdicts) and n_pass == len(verdicts)
    return r


def cleanup() -> None:
    """清掉遗留的临时目录（异常退出时可能残留）。"""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    # 快速自检：python -m app.sandbox
    print(docker_available())
    print(run_code("print('hello from', __import__('sys').version.split()[0])"))
    demo = [{"input": "1\n2\n", "output": "3", "testtype": "stdin"}]
    print(run_stdio_tests("a, b = map(int, sys.stdin.read().split())\nprint(a + b)", demo))
