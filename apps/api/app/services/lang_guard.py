"""语言守卫 · app 侧统一入口（实现在 agents/text_sanitizer.py）

为什么要有这层薄封装：
  `agents/` 在仓库根，而 hermes-api 进程的 cwd 是 `api/`（pm2 exec cwd=/opt/hermes-api），
  sys.path 里没有仓库根 —— `api/app/routers/chat_debate.py` 是靠自己手动
  `sys.path.insert(0, <repo 根>)` 才 import 得到 agents 的。
  app 侧其它模块（orchestrator / tool_registry / subagents / llm_client）不能假设
  chat_debate 一定先被加载过，各自复制一份 sys.path 注入又太脏，所以统一走这里。

用法：
    from app.services.lang_guard import ZH_ONLY_RULE, sanitize_llm_text, has_english_prose
"""
import os
import sys

# 逐级向上找 agents/ 的父目录再插 path。不写死层数是因为两仓布局不同：
# SaaS 是 <repo>/agents + <repo>/api/app，开源版是 <apps/api>/agents + <apps/api>/app。
_d = os.path.dirname(os.path.realpath(__file__))
for _ in range(6):
    _d = os.path.dirname(_d)
    if os.path.isdir(os.path.join(_d, "agents")):
        if _d not in sys.path:
            sys.path.insert(0, _d)
        break

from agents.text_sanitizer import (  # noqa: E402
    ZH_ONLY_RULE,
    NO_SANITIZE_KEYS,
    chinese_char_count,
    contains_chinese,
    has_english_prose,
    sanitize_json_values,
    sanitize_llm_text,
    strip_english_prose,
    strip_thinking_preamble,
)

__all__ = [
    "ZH_ONLY_RULE", "NO_SANITIZE_KEYS", "chinese_char_count", "contains_chinese",
    "has_english_prose", "sanitize_json_values", "sanitize_llm_text",
    "strip_english_prose", "strip_thinking_preamble",
]
