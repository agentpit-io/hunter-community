"""Pytest 根 conftest — 让 tests/ 能 import app.*
"""
import os
import sys

# 确保 api/ 目录在 sys.path 里
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 让 agents/ 顶层包可以被 import（PriceAlertGraph 等）
_HERMES_ROOT = os.path.dirname(_ROOT)
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)
