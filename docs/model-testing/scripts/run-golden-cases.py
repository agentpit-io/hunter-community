#!/usr/bin/env python3
"""hunter-community golden case runner v3

跑 12 golden case(4 单 tool + 5 多 tool + 3 边界)· 抓完整 tool_call 序列 · 生成 JSON + CSV。

用法:
  python3 run-golden-cases.py                     # 默认 DeepSeek v4 pro (hunter-llm provider)
  python3 run-golden-cases.py --model qwen-max --provider hunter-llm
  python3 run-golden-cases.py --preset A          # 只跑 A 类(4 case)
  python3 run-golden-cases.py --case A1,B1,C1     # 指定 case

前置:
  · hunter-community 本机在跑(docker compose ps 全 healthy)
  · .env 里的 LLM_API_KEY / HUNTER_API_KEY / OPENCODE_PASS 有效
  · /api/auth/local-session 可用(HUNTER_SINGLE_USER=1)
"""
import argparse, os, json, time, csv, base64, sys
import urllib.request, urllib.error
from datetime import datetime

BFF = "http://127.0.0.1:3100/api/opencode"
API = "http://127.0.0.1:8100"
OPENCODE = "http://127.0.0.1:3921"
ENV_FILE = os.environ.get("HUNTER_COMMUNITY_ENV") or f"{os.environ.get('HUNTER_COMMUNITY', '.')}/.env"
OUT_DIR = os.environ.get("RESULTS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")

def get_env(k):
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    return ""

def http(url, method="GET", body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()

def get_jwt():
    _, s = http(f"{API}/api/auth/local-session", "POST", {},
                {"Content-Type": "application/json"}, 10)
    d = json.loads(s)
    return d.get("token") or d.get("access_token")

def get_basic():
    p = get_env("OPENCODE_PASS")
    return "Basic " + base64.b64encode(f"opencode:{p}".encode()).decode()

CASES = [
    ("A1", "查一下 601899.SH 现在最新股价",                   180, ["quote", "quickview"]),
    ("A2", "600519 最近 30 天的 K 线走势",                    240, ["kline", "quickview"]),
    ("A3", "把 601899.SH 加入我的自选",                       180, ["watchlist_add"]),
    ("A4", "601899 最近 24 小时新闻",                          180, ["news"]),
    ("B1", "预测茅台走势",                                     420, ["kpred", "forecast", "kronos", "deep_analysis"]),
    ("B2", "分析贵州茅台近期走势，并结合估值和基本面给出判断", 420, ["deep_analysis", "quickview"]),
    ("C1", "分析一下这只股票",                                 240, []),  # 期望不瞎调 tool 或追问
]

def analyze_full_session(sid, hdr_bff):
    """GET 全部 messages · 提取 tool_call 时间线"""
    try:
        _, body = http(f"{BFF}/session/{sid}/message", "GET", None, hdr_bff, 20)
    except Exception as e:
        return {"messages_error": str(e)[:200], "tools": []}
    try:
        msgs = json.loads(body)
    except Exception:
        return {"messages_parse_error": True, "tools": []}
    tools = []
    total_tokens = {"input": 0, "output": 0, "reasoning": 0}
    total_cost = 0
    text_all = []
    think_leak = False
    for m in msgs:
        # opencodeClient 展平 info 到顶层 · 但 raw 可能保留 info + parts
        info = m.get("info", m)  # 兼容两种
        role = info.get("role") or m.get("role")
        parts = m.get("parts", [])
        tk = info.get("tokens") or {}
        if role == "assistant":
            for k in ("input", "output", "reasoning"):
                total_tokens[k] += (tk.get(k) or 0)
            total_cost += (info.get("cost") or 0)
        for p in parts:
            pt = p.get("type")
            if pt == "tool":
                st = p.get("state") or {}
                tm = st.get("time") or {}
                start = tm.get("start")
                end = tm.get("end")
                dur_ms = (end - start) if (start and end) else None
                tools.append({
                    "name": p.get("tool") or p.get("name"),
                    "status": st.get("status"),
                    "input": st.get("input"),
                    "output_preview": (json.dumps(st.get("output"), ensure_ascii=False)[:200]
                                        if st.get("output") else None),
                    "duration_ms": dur_ms,
                })
            elif pt == "text":
                t = p.get("text", "")
                text_all.append(t)
                if "<think>" in t or "</think>" in t:
                    think_leak = True
    full_text = "\n".join(text_all)
    return {
        "tools": tools,
        "tokens": total_tokens,
        "cost_usd": total_cost,
        "content_len": len(full_text),
        "content_head": full_text[:400],
        "think_leak": think_leak,
        "msg_count": len(msgs),
    }

def run_case(cid, prompt, timeout, expected, hdr_bff, provider, model):
    r = {"case": cid, "prompt": prompt, "expected_tools": expected}
    t0 = time.time()
    # 建 session
    try:
        _, s = http(f"{BFF}/session", "POST", {}, hdr_bff, 15)
        sid = json.loads(s)["id"]
    except Exception as e:
        r.update(error=f"create_session: {e}", elapsed_s=round(time.time()-t0, 2))
        return r
    r["sid"] = sid
    # 发消息
    try:
        code, body = http(f"{BFF}/session/{sid}/message", "POST", {
            "providerID": provider,
            "modelID": model,
            "parts": [{"type": "text", "text": prompt}],
        }, hdr_bff, timeout)
        r["http"] = code
    except urllib.error.HTTPError as e:
        r.update(http=e.code, error=f"HTTPError: {e.read().decode()[:300]}",
                 elapsed_s=round(time.time()-t0, 2))
        # 即使 HTTP 错也尝试拉 messages(可能中间已完成一半)
    except Exception as e:
        r.update(error=f"{type(e).__name__}: {str(e)[:200]}",
                 elapsed_s=round(time.time()-t0, 2))
    r["elapsed_s"] = round(time.time() - t0, 2)
    # 拉全 messages 抓 tool 序列(关键!)
    detail = analyze_full_session(sid, hdr_bff)
    r.update(detail)
    # 命中判定 · 基于真实 tool 序列
    tool_names = [t["name"] or "" for t in detail.get("tools", [])]
    if expected:
        r["hit"] = any(any(exp.lower() in tn.lower() for exp in expected) for tn in tool_names)
    else:
        r["hit"] = len(tool_names) == 0
    r["tool_count"] = len(tool_names)
    r["tool_ok"] = sum(1 for t in detail.get("tools", []) if t.get("status") == "completed")
    r["tool_fail"] = sum(1 for t in detail.get("tools", []) if t.get("status") == "error")
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="hunter-llm")
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--label", default=None, help="输出文件 tag(默认 model 名)")
    ap.add_argument("--case", default=None, help="逗号分隔 case id · 如 A1,B1")
    ap.add_argument("--preset", default=None, choices=["A", "B", "C", "all"])
    args = ap.parse_args()

    jwt = get_jwt()
    print(f"JWT ok · len={len(jwt)}")
    HDR_BFF = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

    selected = CASES
    if args.case:
        ids = set(args.case.split(","))
        selected = [c for c in CASES if c[0] in ids]
    elif args.preset and args.preset != "all":
        selected = [c for c in CASES if c[0].startswith(args.preset)]

    date = datetime.now().strftime("%Y-%m-%d_%H%M")
    label = args.label or args.model.replace("/", "_").replace(":", "_")
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"Model: {args.provider} / {args.model} · {len(selected)} case · label={label}")
    results = []
    for cid, prompt, timeout, expected in selected:
        print(f"\n[{cid}] {prompt[:50]} (t={timeout}s)")
        r = run_case(cid, prompt, timeout, expected, HDR_BFF, args.provider, args.model)
        results.append(r)
        tk = r.get("tokens", {})
        tools = r.get("tools", [])
        tool_names_short = [t["name"] for t in tools][:5]
        print(f"  → http={r.get('http')} t={r.get('elapsed_s')}s hit={r.get('hit')} "
              f"tool_count={r.get('tool_count')} ok={r.get('tool_ok')} fail={r.get('tool_fail')}")
        print(f"     tools: {tool_names_short}")
        print(f"     tok(i/o/r)={tk.get('input',0)}/{tk.get('output',0)}/{tk.get('reasoning',0)} "
              f"cost=${r.get('cost_usd',0)} content_len={r.get('content_len',0)}")
        if r.get("error"):
            print(f"     ⚠ {r['error'][:200]}")
        if r.get("think_leak"):
            print(f"     ⚠ <think> 泄漏!")

    # 输出
    json_out = f"{OUT_DIR}/{date}_{label}_v3_full.json"
    with open(json_out, "w") as f:
        json.dump({
            "model": args.model, "provider": args.provider,
            "date": date, "case_count": len(results),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    csv_out = f"{OUT_DIR}/{date}_{label}_v3_summary.csv"
    with open(csv_out, "w") as f:
        w = csv.writer(f)
        w.writerow(["case","prompt","http","elapsed_s","hit","tool_count","tool_ok",
                    "tool_fail","tool_names","tokens_in","tokens_out","tokens_reasoning",
                    "cost_usd","content_len","think_leak","error"])
        for r in results:
            tools = r.get("tools", [])
            tk = r.get("tokens", {})
            w.writerow([
                r.get("case"), r.get("prompt",""),
                r.get("http"), r.get("elapsed_s"),
                r.get("hit"), r.get("tool_count"),
                r.get("tool_ok"), r.get("tool_fail"),
                ",".join([t["name"] or "" for t in tools]),
                tk.get("input",0), tk.get("output",0), tk.get("reasoning",0),
                r.get("cost_usd",0), r.get("content_len",0),
                r.get("think_leak", False),
                (r.get("error","") or "")[:200],
            ])

    print(f"\n✅ full → {json_out}")
    print(f"✅ csv  → {csv_out}")

if __name__ == "__main__":
    main()
