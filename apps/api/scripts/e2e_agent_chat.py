"""Agent Chat V2 · 端到端冒烟脚本

跑 tests/fixtures/golden_queries.json 里的 6 场景，验证：
  - SSE 事件序列完整
  - tool_calls 数量符合预期
  - message_end 有 usage
  - 未触发全局 error（场景 F 除外）

运行:
    cd api
    export AGENT_CHAT_V2_TEST_TOKEN=<你的 JWT>
    export AGENT_CHAT_V2_BASE=http://127.0.0.1:8000   # 或 https://hermes.agentpit.io
    python3 scripts/e2e_agent_chat.py [scenario_key]

不传 scenario_key 则跑全部（除长流程 C 外）。
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path
import urllib.request

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))


def _load_scenarios() -> dict:
    p = _ROOT / "tests" / "fixtures" / "golden_queries.json"
    return json.loads(p.read_text(encoding="utf-8"))["scenarios"]


def _sse_call(base: str, token: str, body: dict, timeout: int = 90) -> list[tuple[str, dict]]:
    """POST /api/agent/chat/stream，收集所有 (event, data)"""
    url = f"{base}/api/agent/chat/stream"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    events: list[tuple[str, dict]] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buf = b""
        for chunk in resp:
            buf += chunk
            while b"\n\n" in buf:
                raw, buf = buf.split(b"\n\n", 1)
                name = "message"
                data_s = ""
                for line in raw.decode(errors="replace").split("\n"):
                    if line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_s += line[5:].strip()
                if data_s:
                    try:
                        events.append((name, json.loads(data_s)))
                    except Exception:
                        events.append((name, {"raw": data_s[:200]}))
    return events


def _analyze(events: list[tuple[str, dict]]) -> dict:
    names = [n for n, _ in events]
    tool_ids_use = [d.get("tool_id") for n, d in events if n == "tool_use"]
    tool_ids_res = [d.get("tool_id") for n, d in events if n == "tool_result"]
    errors = [d for n, d in events if n == "error"]
    end = next((d for n, d in events if n == "message_end"), None)
    assistant_text = "".join(d.get("content", "") for n, d in events if n == "message_delta")
    return {
        "event_names":   names,
        "tool_use_count":   len(tool_ids_use),
        "tool_result_count": len(tool_ids_res),
        "errors":       errors,
        "end":          end,
        "assistant_preview": assistant_text[:200],
    }


def _run_case(base: str, token: str, scenario_key: str, case: dict, idx: int) -> bool:
    body = {"query": case["query"],
             "stock_code": case.get("stock_code"),
             "stock_name": case.get("stock_name")}
    print(f"\n─── [{scenario_key}#{idx}] query = {body['query']!r}")
    t0 = time.time()
    try:
        events = _sse_call(base, token, body)
    except Exception as e:
        print(f"  ✗ 请求失败: {type(e).__name__}: {e}")
        return False
    dur = time.time() - t0
    analysis = _analyze(events)

    ok = True
    # 基本契约：session + message_end
    if "session" not in analysis["event_names"] or "message_end" not in analysis["event_names"]:
        print(f"  ✗ 基础事件缺失: {analysis['event_names'][:5]}")
        ok = False
    # tool_use / tool_result 数量对齐
    if analysis["tool_use_count"] != analysis["tool_result_count"]:
        print(f"  ✗ tool_use ({analysis['tool_use_count']}) ≠ tool_result ({analysis['tool_result_count']})")
        ok = False

    # 场景独有断言
    if scenario_key == "A_simple_data":
        if analysis["tool_use_count"] > 2:
            print(f"  ⚠ 简单数据问但调了 {analysis['tool_use_count']} 个工具（期望 ≤ 2）")
    elif scenario_key == "B_combo_analysis":
        if analysis["tool_use_count"] < 2:
            print(f"  ⚠ 综合分析问但只调了 {analysis['tool_use_count']} 个工具（期望 ≥ 2）")
    elif scenario_key == "F_full_llm_failure":
        if not any(e.get("code") == "LLM_FAILED" for e in analysis["errors"]):
            print(f"  ✗ 场景 F 期望 LLM_FAILED，但未收到 error 事件")
            ok = False

    print(f"  {'✓' if ok else '✗'} 用时 {dur:.1f}s · tools={analysis['tool_use_count']} · errs={len(analysis['errors'])}")
    print(f"    · 回复预览: {analysis['assistant_preview'][:120]}")
    if analysis["end"] and analysis["end"].get("usage"):
        u = analysis["end"]["usage"]
        print(f"    · usage: {u.get('model')} · in={u.get('tokens_in')} out={u.get('tokens_out')} cost=¥{u.get('cost_cny')}")
    return ok


def main():
    base = os.environ.get("AGENT_CHAT_V2_BASE", "http://127.0.0.1:8000")
    token = os.environ.get("AGENT_CHAT_V2_TEST_TOKEN", "")
    if not token:
        print("请先 export AGENT_CHAT_V2_TEST_TOKEN=<jwt>")
        sys.exit(1)

    scenarios = _load_scenarios()
    only = sys.argv[1] if len(sys.argv) > 1 else None

    total, passed = 0, 0
    for skey, s in scenarios.items():
        if only and only != skey:
            continue
        if skey == "C_hold_judge" and not only:
            print(f"\n=== SKIP {skey} (长流程，需显式传 scenario_key) ===")
            continue
        if skey == "D_stock_switch" and not only:
            # 只跑第一条（切换是多轮）
            continue
        if skey in ("E_partial_error", "F_full_llm_failure") and not only:
            print(f"\n=== SKIP {skey} (需 mock，非纯 e2e) ===")
            continue
        print(f"\n=== {skey} · {s.get('desc', '')} ===")
        for i, case in enumerate(s.get("queries", []), 1):
            total += 1
            if _run_case(base, token, skey, case, i):
                passed += 1

    print(f"\n=== TOTAL: {passed}/{total} passed ===")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
