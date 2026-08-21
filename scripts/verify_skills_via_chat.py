#!/usr/bin/env python3
"""逐个在对话框里试用户装的 SKILL,看哪些真能用。

## 为什么需要

`portability()` 只看 SKILL.md 的**文本**里有没有"调作者的脚本""读作者的
缓存目录"这类耦合模式 —— 它是**静态检查**,能判断"大概率跑不通",
判断不了"实际跑起来什么样"。

而用户关心的是:我装了 23 个,**在对话框里点一下,哪些真给我干活了**。

## 判定标准

一条 SKILL 算「能用」要同时满足:

  1. 模型有回复,且不是错误
  2. 回复里**没有出现"我做不到"这类话** —— 见 `_REFUSAL`
  3. 回复长度像样(太短多半是"抱歉我无法…")

**不要求它调工具**。有些 SKILL 就是纯方法论(给分析框架、写作模板),
不取数也算正常工作。要求调工具会把它们全判成失败。

## 用法

    python scripts/verify_skills_via_chat.py                 # 全部
    python scripts/verify_skills_via_chat.py --only UZI      # 只跑名字含 UZI 的
    python scripts/verify_skills_via_chat.py --limit 5       # 只跑前 5 个
    python scripts/verify_skills_via_chat.py --keep          # 保留会话

一条约 15-40 秒,23 条要十几分钟 —— 建议放后台。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

WEB = os.getenv("WEB", "http://localhost:3100").rstrip("/")
API = os.getenv("API", "http://localhost:8100").rstrip("/")
TOKEN = os.getenv("HUNTER_TOKEN", "")

# 提问模板里的占位符 —— 用一只**有充足数据**的票,免得"没数据"被误判成
# "SKILL 坏了"。茅台在我们接的每一个 A 股源里都查得到。
FILL = {
    "{股票}": "贵州茅台(600519)",
    "{stock}": "贵州茅台(600519)",
    "{代码}": "600519",
    "{symbol}": "600519",
    "{公司}": "贵州茅台",
}

# 模型"我干不了"的说法。命中任意一条就判失败。
#
# 这些词只在**模型放弃**时才会出现 —— 正常回答里不会说"我无法访问"。
# 但也要小心:回答里引用用户原话时可能带上这些词,所以只在**前 200 字**
# 里找(放弃总是发生在开头,而不是分析了半天最后才说做不到)。
_REFUSAL = [
    "无法访问", "无法获取", "不支持", "暂无法", "抱歉",
    "没有权限", "未能找到", "无法执行", "cannot access",
    "I don't have access", "unable to",
    # 依赖作者脚本的那批典型报错
    "找不到脚本", "脚本不存在", "缓存目录", "no such file",
]


def req(method: str, url: str, body=None, timeout=240):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(r, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def installed_skills() -> list[dict]:
    """用户自己装的 SKILL(不含内置与工具)。"""
    d = req("GET", f"{API}/api/catalog/capabilities", timeout=60)
    out = []
    for g in d.get("groups", []):
        for it in g.get("items", []):
            if it.get("kind") == "skill" and not it.get("builtin", True):
                out.append(it)
    return out


def ask_text(tpl: str, name: str) -> str:
    """把提问模板填成一句真问题。模板为空就用名字兜底。"""
    q = (tpl or "").strip()
    if not q:
        # 没有模板的 SKILL(网上下载的多数没写 hunter.prompt_tpl)——
        # 用它的名字造一句,这也正是用户会怎么用它
        return f"用「{name}」分析一下贵州茅台(600519)"
    for k, v in FILL.items():
        q = q.replace(k, v)
    return q


def run_one(item: dict, keep: bool) -> dict:
    sid = None
    name = item.get("name") or item.get("key")
    try:
        s = req("POST", f"{WEB}/api/opencode/session",
                {"title": f"[skill] {name}"})
        sid = s.get("id") or (s.get("info") or {}).get("id")
        if not sid:
            return {"ok": False, "why": "建会话失败"}

        t0 = time.time()
        req("POST", f"{WEB}/api/opencode/session/{sid}/message",
            {"parts": [{"type": "text", "text": ask_text(item.get("prompt_tpl"), name)}]},
            timeout=300)
        cost = time.time() - t0

        msgs = req("GET", f"{WEB}/api/opencode/session/{sid}/message", timeout=60)
        if isinstance(msgs, dict):
            msgs = msgs.get("data") or []
        text, tools = [], []
        for m in msgs:
            mm = ({**(m.get("info") or {}), "parts": m.get("parts") or []}
                  if "info" in m else m)
            if mm.get("role") != "assistant":
                continue
            for p in mm.get("parts") or []:
                if p.get("type") == "text" and p.get("text"):
                    text.append(p["text"])
                elif p.get("type") == "tool":
                    tools.append(str(p.get("tool") or "?"))
        answer = "\n".join(text).strip()

        if not answer:
            return {"ok": False, "why": "模型没有回复", "cost": cost, "sid": sid}
        head = answer[:200]
        hit = [w for w in _REFUSAL if w.lower() in head.lower()]
        if hit:
            return {"ok": False, "why": f"模型说做不到({hit[0]})",
                    "answer": answer, "tools": tools, "cost": cost, "sid": sid}
        if len(answer) < 60:
            return {"ok": False, "why": f"回复过短({len(answer)} 字)",
                    "answer": answer, "tools": tools, "cost": cost, "sid": sid}
        return {"ok": True, "answer": answer, "tools": tools,
                "cost": cost, "sid": sid}
    except urllib.error.HTTPError as e:
        return {"ok": False,
                "why": f"HTTP {e.code}: {e.read()[:120].decode('utf-8','replace')}"}
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "why": f"{type(e).__name__}: {str(e)[:120]}"}
    finally:
        if sid and not keep:
            try:
                req("DELETE", f"{WEB}/api/opencode/session/{sid}", timeout=30)
            except Exception:                                  # noqa: BLE001
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    skills = installed_skills()
    if args.only:
        low = args.only.lower()
        skills = [s for s in skills
                  if low in (s.get("name") or "").lower()
                  or low in (s.get("key") or "").lower()
                  or low in ((s.get("origin") or "").lower())]
    if args.limit:
        skills = skills[:args.limit]
    if not skills:
        print("没有匹配的 SKILL", file=sys.stderr)
        return 1

    print(f"逐个试用 {len(skills)} 个 SKILL · 一条 15-40 秒\n" + "=" * 78)
    ok = 0
    fails = []
    for i, s in enumerate(skills, 1):
        origin = (s.get("origin") or "").replace("github:", "").split("@")[0]
        print(f"\n[{i}/{len(skills)}] {s.get('name')}  ({origin or '—'})")
        r = run_one(s, args.keep)
        if r.get("ok"):
            ok += 1
            print(f"    OK  {r.get('cost', 0):.0f}s · "
                  f"{len(r.get('answer') or '')} 字 · "
                  f"工具 {', '.join(r.get('tools') or []) or '(未调)'}")
        else:
            fails.append((s.get("name"), origin, r.get("why")))
            print(f"    X   {r.get('why')}")
        ans = (r.get("answer") or "").replace("\n", " ")
        if ans:
            print(f"        {ans[:130]}")
        if args.keep and r.get("sid"):
            print(f"        {WEB}/chat?session={r['sid']}")

    print("\n" + "=" * 78)
    print(f"能用 {ok}/{len(skills)}")
    if fails:
        print("\n没通过的:")
        for n, o, w in fails:
            print(f"  · {n}  ({o})  —— {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
