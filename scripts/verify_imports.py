"""静态检查：找出「用到但没定义、也没 import」的名字。

为什么需要它
------------
Python 只在**执行到那一行**时才抛 `NameError`。大范围搬代码（比如把 1157 行的
engine.py 拆成多个模块）时，漏一个 import 往往要等到某个具体功能被调用才暴露 ——
实测拆分后就漏了三处，其中 `datasets.export_items` 里的 `db` 会让**每次评测结束时崩**，
而 import 阶段完全看不出来。

这个脚本补上那个空档：不运行代码，纯静态地列出所有可疑名字。

作用域处理
----------
会正确地把「闭包读外层函数的变量」算作已定义，否则满屏误报（`work()` 读 `run_evaluation`
的局部变量就属于这一类）。模块级绑定、函数参数、`for`/`with`/`except as`、嵌套 def 名都会收集。

用法::

    python scripts/verify_imports.py
"""
import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILTINS = set(dir(builtins)) | {"__path__", "__package__", "__spec__", "__loader__"}


def collect_bindings(node, into: set):
    """收集 node 这一层作用域里的绑定名（不进入嵌套的函数/类体）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            into.add(child.name)
            continue                       # 它们各自的作用域单独算
        if isinstance(child, ast.Import):
            for a in child.names:
                into.add((a.asname or a.name).split(".")[0])
        elif isinstance(child, ast.ImportFrom):
            for a in child.names:
                into.add(a.asname or a.name)
        elif isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            into.add(child.id)
        elif isinstance(child, ast.arg):
            into.add(child.arg)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            into.add(child.name)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            into.update(child.names)
        collect_bindings(child, into)      # 递归进 if / for / with / try


def loaded_names(fn, out: set):
    """收集 fn 自身代码里读取的名字，**不进入嵌套函数/类**（它们单独检查）。"""
    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            out.add(child.id)
        loaded_names(child, out)


def walk_fn(fn, enclosing: set, module_names: set, path: Path, problems: list):
    scope = set(module_names) | set(enclosing)
    collect_bindings(fn, scope)            # 参数 + 函数体直接绑定（含嵌套 def 名）
    used = set()
    loaded_names(fn, used)
    # 推导式/生成器里的变量也在这个作用域内
    collect_bindings(fn, scope)
    unknown = sorted(n for n in used if n not in scope and n not in BUILTINS
                     and n not in ("__name__", "__file__", "__doc__"))
    if unknown:
        problems.append((path, fn.lineno, fn.name, unknown))
    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walk_fn(child, scope, module_names, path, problems)
        elif isinstance(child, ast.ClassDef):
            walk_fn(child, scope, module_names, path, problems)


def module_package(path: Path) -> tuple:
    """文件对应的包路径元组。app/qtypes/code_stdio.py -> ('app','qtypes')"""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()          # 去掉模块名本身，留下它所在的包
    return tuple(parts)


def target_exists(parts: tuple) -> bool:
    if not parts:
        return False
    p = ROOT.joinpath(*parts)
    return p.with_suffix(".py").exists() or (p / "__init__.py").exists()


def check_relative_imports(path: Path, problems: list):
    """检查相对导入能不能解析到真实模块。

    为什么必须查这个：**搬动文件会改变相对导入的含义**。
    把 `engine.py` 里的 `from .lcb import all_tests`（原本指 app.lcb）搬进 `app/qtypes/` 之后，
    它变成了指 `app.qtypes.lcb` —— 一个不存在的模块。这种错误静态看不出来（不会 NameError），
    只在真正执行到那一行时才炸。实测拆分 engine.py 时正是这样漏掉一处。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    pkg = module_package(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        base = list(pkg)
        for _ in range(node.level - 1):
            if not base:
                problems.append((path, node.lineno, "<相对导入>",
                                 [f"层级超出根包: {'..' * node.level}{node.module or ''}"]))
                break
            base.pop()
        else:
            if node.module:
                # from .config import X  —— 查 config 这个**模块**是否存在
                target = tuple(base) + tuple(node.module.split("."))
                if not target_exists(target):
                    problems.append((path, node.lineno, "<相对导入>",
                                     [f"{'.' * node.level}{node.module} -> "
                                      f"{'.'.join(target)} 不存在"]))
            else:
                # from . import db, sandbox —— 查每个**被导入的名字**是不是该包的子模块。
                # （不能去查「包本身」，那当然存在。）
                for a in node.names:
                    target = tuple(base) + (a.name,)
                    if not target_exists(target):
                        problems.append((path, node.lineno, "<相对导入>",
                                         [f"{'.' * node.level} import {a.name} -> "
                                          f"{'.'.join(target)} 不存在"]))


def check_file(path: Path, problems: list, check_module_level=False):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_names = set()
    collect_bindings(tree, module_names)
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            walk_fn(child, set(), module_names, path, problems)
    if check_module_level:
        used = set()
        loaded_names(tree, used)
        unknown = sorted(n for n in used if n not in module_names and n not in BUILTINS
                         and n not in ("__name__", "__file__", "__doc__"))
        if unknown:
            problems.append((path, 1, "<模块级>", unknown))


def check_paths() -> list:
    """检查关键路径常量是否都落在**项目根**下。

    为什么单独查这个：**搬动文件会改变 `Path(__file__).parent...` 的层数**，
    而且错得很安静。实测拆分基准注册时踩到过：`DATA_DIR` 从 `scripts/download_more.py`
    搬进 `app/benchmarks/_util.py` 后少了一层，指向了 `app/data/` ——
    下载器不报错，只是默默把题库写到错误的新目录里，
    而"验证下载成功"看到的也是那个错目录里的文件。

    这类 bug 静态解析不出来，只能靠断言常量解析结果。
    """
    import importlib

    # 以脚本方式运行时 sys.path[0] 是 scripts/，import app.* 会失败
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    problems = []
    root = ROOT.resolve()
    # (模块, 属性名, 期望相对项目根的路径)
    expected = [
        ("app.benchmarks._util", "ROOT", ""),
        ("app.benchmarks._util", "DATA_DIR", "data"),
        ("app.benchmarks._util", "VENDOR_DIR", ".vendor"),
        ("app.config", "DATA_DIR", "data"),
        ("app.config", "RESULTS_DIR", "data/results"),
        ("app.config", "BFCL_DIR", "data/bfcl_v4"),
        ("app.db", "DB_PATH", "data/app.db"),
        ("app.sandbox", "TMP_ROOT", ".sandbox"),
    ]
    for mod_name, attr, rel in expected:
        try:
            mod = importlib.import_module(mod_name)
            got = getattr(mod, attr)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{mod_name}.{attr} 取不到：{e}")
            continue
        want = root.joinpath(*rel.split("/")) if rel else root
        if got.resolve() != want:
            problems.append(f"{mod_name}.{attr} = {got}\n            应为 {want}")
    # 结构性检查：app/ 下不该冒出 data / .vendor / .sandbox 这类项目级目录
    for stray in ("data", ".vendor", ".sandbox"):
        p = ROOT / "app" / stray
        if p.exists():
            problems.append(f"app/{stray}/ 不该存在（说明某个 __file__ 相对路径少写了一层）")
    return problems


def main():
    problems = []
    files = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    for p in files:
        # 只跳过 scripts/ 下的一次性脚本（如 _split_engine.py）。
        # **app/ 下的下划线模块是正式代码，必须检查** —— 曾经因为把 app/benchmarks/_util.py
        # 一起跳过，漏掉了它没 import json 的 bug：静态检查报 0 问题，真下载却 NameError。
        if p.name.startswith("_") and p.parent.name == "scripts":
            continue
        try:
            check_file(p, problems, check_module_level=True)
            check_relative_imports(p, problems)
        except SyntaxError as e:
            problems.append((p, e.lineno or 1, "<语法错误>", [str(e.msg)]))

    path_problems = check_paths()

    if not problems:
        print(f"检查了 {len(files)} 个文件：没有「用到但未定义」的名字。")
    else:
        for path, line, fn, names in problems:
            rel = path.relative_to(ROOT)
            print(f"[失败] {rel}:{line} {fn}() -> {names}")
        print(f"\n共 {len(problems)} 处可疑，见上。")

    if path_problems:
        print("\n[失败] 路径常量检查：")
        for p in path_problems:
            print(f"  ✗ {p}")
    else:
        print("路径常量：8 个关键路径都指向项目根下正确的位置。")

    return 0 if (not problems and not path_problems) else 1


if __name__ == "__main__":
    sys.exit(main())
