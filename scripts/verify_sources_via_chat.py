#!/usr/bin/env python3
"""端到端验证:**在对话里问**,看模型能不能真的用上你接的数据源。

## 为什么不用 `/user_sources/{id}/test`

那个端点只证明「地址通、映射对」。但用户真正在意的是
**「我在对话框里问一句,能不能拿到数」** —— 这中间还隔着三段:

    对话框 → 模型决定调哪个工具 → MCP hunter_user_invoke → 你的源

这三段里任何一段断了,`/test` 都还是绿的,而用户问一句得到的是
"抱歉我无法获取实时数据"。`_24` §9 铁律 2 说的就是这个:
**一处通不代表整条通。**

所以这个脚本走的是和浏览器完全一样的路:创建会话 → 发消息 →
等模型跑完 → 读回复,再去日志里核对它到底调没调工具。

## 用法

    python scripts/verify_sources_via_chat.py                # 跑全部
    python scripts/verify_sources_via_chat.py --only 腾讯     # 只跑名字含"腾讯"的
    python scripts/verify_sources_via_chat.py --keep         # 不删测试会话(想在浏览器里看)

在宿主机跑(它打的是 web 的 BFF,和浏览器同一个入口):

    WEB=http://localhost:3100 python scripts/verify_sources_via_chat.py

## ⚠️ 它比 `/test` 慢得多

每条要等模型真的跑完(含工具调用),一条 30-90 秒。这是必然的 ——
我们测的就是"模型用起来什么感觉"。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Windows 控制台默认 GBK,打 ✅ 会 UnicodeEncodeError 直接崩 ——
# 而那个崩溃发生在**测试已经跑完之后**,看起来像测试失败,
# 实际结果早就拿到了。强制 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WEB = os.getenv("WEB", "http://localhost:3100").rstrip("/")
API = os.getenv("API", "http://localhost:8100").rstrip("/")
TOKEN = os.getenv("HUNTER_TOKEN", "")

# 每个数据源问一句**用户真会问的话**,而不是"调用 xx 工具"。
#
# 问法很重要:说"用腾讯的接口查"会把工具选择的难题绕过去,而那恰恰是
# 我们要测的一环。所以问的是自然问题,让模型自己决定用什么。
#
# `expect` 是**回答里必须出现的东西**。不要求精确数值(它每分钟都在变),
# 只要求"确实是一个数",以及关键的事实锚点(股票名)。
CASES = [
    {
        "name": "A股行情",
        "ask": "600519 现在多少钱?",
        "expect": [r"茅台", r"1[,\d]{3}(\.\d+)?"],
        "sources": ["腾讯财经", "东方财富", "新浪财经"],
    },
    {
        "name": "A股K线",
        "ask": "把 600519 最近 5 个交易日的收盘价列出来",
        "expect": [r"茅台|600519", r"\d{4}-\d{2}-\d{2}"],
        "sources": ["腾讯财经"],
    },
    {
        "name": "A股新闻",
        "ask": "600519 最近有什么新闻?列 3 条,带日期",
        "expect": [r"茅台|600519", r"\d{4}-\d{2}-\d{2}|\d+ ?月"],
        "sources": ["东方财富"],
    },
    {
        "name": "A股公告",
        "ask": "600519 最近发布了哪些公告?",
        "expect": [r"茅台|600519|公告|报告"],
        "sources": ["巨潮资讯"],
    },
    {
        "name": "港股行情",
        "ask": "港股 00700 现在多少钱?",
        "expect": [r"腾讯", r"\d{3}(\.\d+)?"],
        "sources": ["腾讯财经", "Yahoo Finance"],
    },
    {
        "name": "美股行情",
        "ask": "AAPL 现在多少钱?",
        "expect": [r"苹果|AAPL|Apple", r"\d{3}(\.\d+)?"],
        "sources": ["Yahoo Finance"],
    },
    {
        "name": "美股备案",
        "ask": "苹果公司最近向 SEC 提交了哪些文件?",
        "expect": [r"10-[KQ]|8-K|苹果|Apple"],
        "sources": ["SEC EDGAR"],
    },
]


def req(method: str, url: str, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    # 绕开系统代理 —— 本机服务走代理会 502(实测)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def text_of(msg: dict) -> str:
    """把一条消息的所有 text part 拼起来。"""
    out = []
    for p in msg.get("parts") or []:
        if p.get("type") == "text" and p.get("text"):
            out.append(p["text"])
    return "\n".join(out)


def tools_of(msg: dict) -> list[str]:
    """这条消息调了哪些工具 —— **这是本脚本最关键的一列**。

    模型完全可能不调任何工具,凭训练数据编一个价格出来,而那个回答
    读起来和真的一模一样。只看回答文本是验不出来的,必须看它到底
    调没调工具。
    """
    names = []
    for p in msg.get("parts") or []:
        if p.get("type") == "tool":
            n = p.get("tool") or (p.get("state") or {}).get("title") or "?"
            names.append(str(n))
    return names


def user_source_calls() -> dict:
    """读一次每条用户源的 call_count —— 问之前问之后各读一次,
    **差值才是"模型有没有真的用上你配的源"的硬证据**。

    只看工具名不够:`watchlist_stock_quickview` 这类内置工具底下
    可能走用户源,也可能走 provider 兜底,名字上看不出来。
    """
    try:
        d = req("GET", f"{API}/api/user_sources", timeout=20)
        return {s["name"]: s.get("call_count", 0) for s in d.get("sources", [])}
    except Exception:                                          # noqa: BLE001
        return {}


def run_case(case: dict, keep: bool) -> dict:
    sid = None
    try:
        sess = req("POST", f"{WEB}/api/opencode/session",
                   {"title": f"[verify] {case['name']}"})
        sid = sess.get("id") or (sess.get("info") or {}).get("id")
        if not sid:
            return {"ok": False, "why": f"建会话失败: {str(sess)[:120]}"}

        t0 = time.time()
        req("POST", f"{WEB}/api/opencode/session/{sid}/message",
            {"parts": [{"type": "text", "text": case["ask"]}]}, timeout=300)
        cost = time.time() - t0

        msgs = req("GET", f"{WEB}/api/opencode/session/{sid}/message")
        if isinstance(msgs, dict):
            msgs = msgs.get("data") or []
        replies = [m for m in msgs
                   if (m.get("info") or m).get("role") == "assistant"]
        if not replies:
            return {"ok": False, "why": "模型没有回复", "cost": cost}

        # ⚠️ **必须扫全部 assistant 消息,不能只看最后一条。**
        #
        # 模型的一轮回答会拆成多条:第 1 条是「调工具 + 中间说明」,
        # 第 2 条才是最终文字。只读最后一条的话工具列表永远是空的 ——
        # 第一次跑就是这样,输出「⚠️ 一个都没调(答案可能是编的)」,
        # 而实际上它调了 watchlist_stock_quickview,数据也是真的。
        #
        # 这种误报比漏报更糟:它会让人去查一个根本不存在的问题。
        answer_parts, tools = [], []
        for m in replies:
            mm = {**(m.get("info") or {}), "parts": m.get("parts") or []} if "info" in m else m
            t = text_of(mm)
            if t.strip():
                answer_parts.append(t)
            tools.extend(tools_of(mm))
        answer = chr(10).join(answer_parts)
        missing = [p for p in case["expect"]
                   if not re.search(p, answer, re.I)]
        return {
            "ok": not missing,
            "why": ("回答里没出现:" + " / ".join(missing)) if missing else "",
            "tools": tools,
            "answer": answer,
            "cost": cost,
            "sid": sid,
        }
    except urllib.error.HTTPError as e:
        return {"ok": False, "why": f"HTTP {e.code}: {e.read()[:160].decode('utf-8','replace')}"}
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "why": f"{type(e).__name__}: {str(e)[:160]}"}
    finally:
        if sid and not keep:
            try:
                req("DELETE", f"{WEB}/api/opencode/session/{sid}", timeout=30)
            except Exception:                                  # noqa: BLE001
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑名字含这个词的用例")
    ap.add_argument("--keep", action="store_true", help="保留测试会话(想在浏览器里看)")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.only or args.only in c["name"]
             or any(args.only in s for s in c["sources"])]
    if not cases:
        print(f"没有匹配 {args.only!r} 的用例", file=sys.stderr)
        return 1

    print(f"在对话里验证 {len(cases)} 个场景 · 走 {WEB}/api/opencode")
    print("每条要等模型真跑完(含工具调用),30-90 秒一条\n")
    print("=" * 76)

    ok = 0
    for i, c in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {c['name']} —— 「{c['ask']}」")
        print(f"        期望用到:{' / '.join(c['sources'])}")
        before = user_source_calls()
        r = run_case(c, args.keep)
        after = user_source_calls()
        used = [k for k, v in after.items() if v > before.get(k, 0)]
        if r.get("ok"):
            ok += 1
            print(f"        ✅ {r.get('cost', 0):.0f}s")
        else:
            print(f"        ❌ {r.get('why')}")
        # **工具那一行永远打印**,通过与否都要看 ——
        # "通过但没调工具"是最危险的情况:模型编了一个看起来对的答案
        tools = r.get("tools")
        if tools is not None:
            print(f"        调用工具:{', '.join(tools) if tools else '⚠️ 一个都没调(答案可能是编的)'}")
        print(f"        用到你的源:{', '.join(used) if used else '⚠️ 一个都没用到'}")
        ans = (r.get("answer") or "").strip().replace("\n", " ")
        if ans:
            print(f"        回答:{ans[:150]}")
        if args.keep and r.get("sid"):
            print(f"        会话:{WEB}/chat?session={r['sid']}")

    print("\n" + "=" * 76)
    print(f"通过 {ok}/{len(cases)}")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
