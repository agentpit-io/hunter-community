"""Pytest 根 conftest

1. 让 tests/ 能 import app.*
2. 把「脚本式」测试文件收集成一个用例、放进子进程跑(见下面「脚本式测试文件」一节)
"""
import ast
import fnmatch
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

# 确保 api/ 目录在 sys.path 里
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 让 agents/ 顶层包可以被 import（PriceAlertGraph 等）
_HERMES_ROOT = os.path.dirname(_ROOT)
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)


# ── 脚本式测试文件 ────────────────────────────────────────────────
# tests/ 下多数文件是脚本:导入即执行,打印 ALL OK / SOME FAILED 后 sys.exit,
# 文件头写的跑法是 `cd apps/api && PYTHONPATH=. python tests/test_xxx.py`。
# pytest 默认会 import 它们,导入期的 sys.exit 直接让整轮收集 INTERNALERROR。
#
# 所以收集时先用 AST 看一眼(不 import):pytest 在文件里找不到任何用例 —— 没有匹配
# python_functions 的函数、没有匹配 python_classes 的类、没有 unittest.TestCase 子类 ——
# 就当脚本,收集成一个用例,运行时用子进程原样执行,退出码 0 算通过。
# 脚本文件一行不用改,以后新加的脚本式用例也会自动收进来。
# 既有 test_* 函数、又有 `if __name__ == "__main__"` 的混合式文件照常由 pytest 收集。

_API_DIR = Path(_ROOT)
_TIMEOUT_S = 600    # 单个脚本的上限。全部脚本本机合计不到 10 秒,这里只防卡死
_TAIL_LINES = 60    # 失败时报错信息里带输出的最后几行(脚本都在末尾汇总 FAIL)

# 不能在这里原样跑的脚本 → 收集成「跳过」并写明原因(-ra 的汇总里能看到)
_SKIP_SCRIPTS = {
    "test_screen_xlayer_official.py":
        "跨层用例要带参数分三步跑(命令见文件头):第 1 步解析示例时要联网拉扫描源字段表、"
        "查数据库里的全市场日线覆盖,第 2 步要 node。只能在部署环境手动跑",
}


def _name_matches(name, patterns):
    # 与 pytest 判断 python_functions / python_classes 的规则相同:前缀匹配,带通配符时按 glob
    return any(name.startswith(p) or (any(c in p for c in "*?[") and fnmatch.fnmatch(name, p))
               for p in patterns)


def _has_pytest_tests(path, config):
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except SyntaxError:
        return True    # 交给默认收集器,由它报出语法错误
    funcs = config.getini("python_functions")
    classes = config.getini("python_classes")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _name_matches(node.name, funcs):
            return True
        if isinstance(node, ast.ClassDef):
            if _name_matches(node.name, classes):
                return True
            for base in node.bases:
                base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if base_name.endswith("TestCase"):
                    return True
    return False


def pytest_pycollect_makemodule(module_path, parent):
    if _has_pytest_tests(module_path, parent.config):
        return None    # 走 pytest 默认的 Module
    return ScriptModule.from_parent(parent, path=module_path)


class ScriptModule(pytest.Module):
    """脚本式测试文件:不在测试进程里 import,只产出一个子进程用例。"""

    def _getobj(self):
        # pytest.Module 只在 collect() 里读 .obj(找用例、登记 setup_module),collect() 已经改写。
        # 返回空模块是保险:哪个插件读了 .obj,也不会在测试进程里把脚本执行一遍。
        return types.ModuleType(self.path.stem)

    def collect(self):
        item = ScriptItem.from_parent(self, name="script")
        reason = _SKIP_SCRIPTS.get(self.path.name)
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))
        return [item]


class ScriptItem(pytest.Item):
    def reportinfo(self):
        # 行号给 0 而不是 None:pytest 按用例位置报告「跳过」时要求行号非空
        return self.path, 0, f"python {self._rel()}"

    def _rel(self):
        # 只用于显示;relpath 在 Windows 上不区分盘符大小写
        return os.path.relpath(self.path, _API_DIR).replace(os.sep, "/")

    def runtest(self):
        env = dict(os.environ, PYTHONUTF8="1")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_API_DIR), os.environ.get("PYTHONPATH")) if p)
        label = f"python {self._rel()}"
        try:
            proc = subprocess.run([sys.executable, str(self.path)], cwd=_API_DIR, env=env, timeout=_TIMEOUT_S,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            problem = f"{label} 退出码 {proc.returncode}" if proc.returncode else None
            output = proc.stdout
        except subprocess.TimeoutExpired as e:
            problem, output = f"{label} 超过 {_TIMEOUT_S} 秒没有结束,已终止", e.output
        if problem:    # 在 except 外面报失败,报告里不带 TimeoutExpired 的链式回溯
            self._fail(problem, output)

    def _fail(self, headline, output):
        text = (output or b"").decode("utf-8", "replace").rstrip()
        lines = text.splitlines()
        if len(lines) > _TAIL_LINES:
            self.add_report_section("call", "script output", text)
            lines = [f"…(输出共 {len(lines)} 行,下面是最后 {_TAIL_LINES} 行,"
                     f"完整输出见 Captured script output)"] + lines[-_TAIL_LINES:]
        pytest.fail("\n".join([headline, *lines]), pytrace=False)
