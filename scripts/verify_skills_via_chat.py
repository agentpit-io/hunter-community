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
import re
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


def _refresh_token() -> str:
    """重新签一把 token。

    ⚠️ **跑满 23 条要二十多分钟,而 JWT 的 TTL 是 1 小时** —— 实测跑到
    第 16 条就开始 401,后面 8 条全废,而错误信息是 INVALID_TOKEN /
    claim_failed,看起来像会话服务坏了,跟被测的 SKILL 毫无关系。

    长跑的脚本必须自己会续签,不然测出来的失败一半是环境噪音。
    """
    import subprocess
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c",
         "import sys; sys.path.insert(0,'/app');"
         "from app.routers.auth import _sign_access;"
         "print(_sign_access('" + os.getenv("HUNTER_UID", "") + "','user','local@hunter.local'))"],
        capture_output=True, text=True, timeout=90)
    tok = (out.stdout or "").strip().splitlines()[-1].strip() if out.stdout else ""
    return tok


def req(method: str, url: str, body=None, timeout=240):
    global TOKEN
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", f"Bearer {TOKEN}")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 401 = token 过期。续签一次再重试 —— 见 _refresh_token 的说明
        if e.code != 401 or not os.getenv("HUNTER_UID"):
            raise
        fresh = _refresh_token()
        if not fresh:
            raise
        TOKEN = fresh
        r2 = urllib.request.Request(url, data=data, method=method)
        r2.add_header("Content-Type", "application/json")
        r2.add_header("Authorization", f"Bearer {TOKEN}")
        with op.open(r2, timeout=timeout) as resp:
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


# opencode 报"加载了哪个 SKILL"有**两种写法**,都要认:
#   · 老的:state.input.filePath = ".../skills/lhb_analyzer/SKILL.md"
#   · 新的:state.title          = "Loaded skill: lhb_analyzer"
#
# ⚠️ 我第一版只认路径那种 —— 而 opencode 重启后换成了 title 那种,
# 结果 23 条**全部**报"没加载这个 SKILL"。整整齐齐的全失败本身就是
# 信号:不是 23 个都坏了,是判据错了。
_SKILL_PATH = re.compile(r"/skills/([A-Za-z0-9_\-]+)/SKILL\.md")
_SKILL_LOADED = re.compile(r"Loaded skill:\s*([A-Za-z0-9_\-]+)", re.I)


def _skills_touched(part: dict) -> set:
    """这次工具调用**读到了哪几个 SKILL**。

    opencode 的 skill 工具把 SKILL.md 的绝对路径放进 `state.input.filePath`,
    输出里也会带 `<path>…</path>` —— 两处都扫,有的调用只在输出里有。

    这是"真的调了它"的**硬证据**。上一轮只看"有没有调工具",
    结果 23/23 全过 —— 但模型完全可能调了别的工具,或干脆凭训练数据
    答一段像模像样的话。
    """
    st = part.get("state") or {}
    blob = " ".join(str(x) for x in (
        (st.get("input") or {}).get("filePath", ""),
        st.get("title", ""),
        str(st.get("output", ""))[:400],
    ))
    return set(_SKILL_PATH.findall(blob)) | set(_SKILL_LOADED.findall(blob))


def _dir_name(item: dict) -> str:
    """卡片对应的 SKILL **目录名**。

    `key` 就是目录名;display_name 带连字符、目录名带下划线,
    所以不能拿显示名去比。
    """
    return str(item.get("key") or "").strip()


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
        loaded = set()          # 这一轮真正读到的 SKILL 目录名
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
                    loaded |= _skills_touched(p)
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
        # ⚠️ **必须确认它真的读了这一个 SKILL**,而不只是"调了某个工具"。
        want = _dir_name(item)
        if want and want not in loaded:
            return {"ok": False,
                    "why": "没加载这个 SKILL(读到的是:"
                           + (", ".join(sorted(loaded)) or "无") + ")",
                    "answer": answer, "tools": tools,
                    "loaded": sorted(loaded), "cost": cost, "sid": sid}

        # 加载了但只是在**介绍自己** —— 上一轮 deep-analysis 就是这样:
        # 「I will explain the core workflow of my ... skill」。
        # 那不算能用,用户要的是它干活。
        low = answer[:180].lower()
        if any(w in low for w in ("explain the core workflow", "工作流如下",
                                  "该技能用于", "这个 skill 的作用",
                                  "我已经加载了")):
            return {"ok": False, "why": "只是在介绍这个 SKILL,没有执行",
                    "answer": answer, "tools": tools,
                    "loaded": sorted(loaded), "cost": cost, "sid": sid}

        return {"ok": True, "answer": answer, "tools": tools,
                "loaded": sorted(loaded), "cost": cost, "sid": sid}
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
                  f"加载了 {', '.join(r.get('loaded') or []) or '(无)'}")
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
