#!/usr/bin/env python3
"""HCA 预算 hook · Stop / StopFailure 记账 + UserPromptSubmit 拦 · **默认关闭**。

## 为什么默认关闭

计划 v0.2 TP-05 写的是「budget（P1，默认关，与现状一致）」。
"与现状一致"= opencode 那套线上跑的时候**没有**任何按会话/按天掐 token 的东西
（公开的 hunter-community 仓里 `scripts/opencode-mcp/plugins/` 只有 hunter-lang 与
hunter-mcp-context 两个插件，全仓 grep `budget` 没有第三个；私有仓的代码按总控
「只能导入公开来源」不看、不抄）。所以这里新写一个、并且**默认 `HCA_BUDGET_ENABLED=0`**，
开了才记账、才会拦 —— 把默认行为改成"会拒绝用户"的那种，属于偷偷改产品语义。

## token 从哪来（两个源，都真取，谁都可能拿不到）

1. **会话 meta 的 `turn_stats`**（首选，按会话精确）：Stop 的 payload 里带
   `transcript_path`（`parts.rs:1033`，指向 `<sessions>/<项目hash>/<sid>.jsonl`），
   同目录同名的 `.meta` 就是 `SessionMeta`，里面每个完成的 turn 有
   `tool_call_count` / `duration_ms` / `used_tokens` / `model_usage[].tokens{input,output,cached_input}`。
   **实测（2026-09-22，测试机主部署的真实会话 fdc426fd）**：`tool_call_count` 与
   `duration_ms` 可靠；`total_tokens` **恒 0**（待办池 P1-12 的同一个根因）；
   `model_usage` **只有部分 turn 有** —— 那次会话 turn 1 有（input 17038 / output 40），
   turn 2 整个字段都不在。所以这里会同时报"有 usage 的 turn 数 / 总 turn 数"，
   覆盖不全就如实写覆盖率，不拿部分值当全量。
2. **网关配额差值**（兜底，按 key 全局）：`GET $HCA_QUOTA_URL` 的 `used_today`，
   两次 Stop 之间的差值。这是 M2 评测用来算成本的那个口子，是**真实计量**，
   但它统计的是**这把 key 的全部消耗** —— 单工作区私有化部署（总控已拍板决策 5）下
   就是这套栈的用量；如果 key 还被别处共用，差值会偏大。记录里写明 `tokens_src`。

两个源都拿不到 → token 一栏写 `null`，并且**不按 token 拦**（拿不到就不拦，
而不是当成 0 或当成超限）。工具调用次数一路都拿得到，所以那条闸是稳的。

## 阈值与拦截

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `HCA_BUDGET_ENABLED` | `0` | 总开关 |
| `HCA_BUDGET_DAILY_TOKENS` | `0` | 当天 token 上限，0 = 不限 |
| `HCA_BUDGET_DAILY_TOOL_CALLS` | `0` | 当天工具调用次数上限，0 = 不限 |
| `HCA_BUDGET_SESSION_TOOL_CALLS` | `0` | 单会话工具调用上限，0 = 不限 |
| `HCA_QUOTA_URL` | `https://hunter.agentpit.io/api/saas/llm/quota` | 配额接口 |

超阈值时在 **UserPromptSubmit** 输出 `{"decision":"block","reason":…}`，
上游 `user_prompt_submit` 会把这一轮直接退回（`cc_hooks.rs:645-651`）——
拦在开口之前，而不是等模型跑完再说。"天"按上海时间算（总控：时间一律上海时间）。

状态落 `.atomcode/budget.json`（当天累计）+ `.atomcode/budget.jsonl`（每轮一行，可回溯）。
恒 exit 0：记账失败不能拖垮对话；**只有"确实超了"才输出 block**。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

# `urllib.request` 延迟导入：只有开了预算闸、且真要查网关配额时才需要
# （容器内实测多花约 416 ms，见 guard.py 文件头同一段说明）。

SH_TZ = datetime.timezone(datetime.timedelta(hours=8))
QUOTA_URL = (os.environ.get("HCA_QUOTA_URL")
             or "https://hunter.agentpit.io/api/saas/llm/quota")
QUOTA_TIMEOUT_S = float(os.environ.get("HCA_BUDGET_QUOTA_TIMEOUT_S") or 4)


def enabled() -> bool:
    v = (os.environ.get("HCA_BUDGET_ENABLED") or "0").strip().lower()
    return v in ("1", "true", "on", "yes")


def limit(name: str) -> int:
    try:
        return max(0, int((os.environ.get(name) or "0").strip()))
    except ValueError:
        return 0


def today() -> str:
    return datetime.datetime.now(SH_TZ).strftime("%Y-%m-%d")


def state_path(ws: str) -> str:
    return os.path.join(ws, ".atomcode", "budget.json")


def load_state(ws: str) -> dict:
    try:
        with open(state_path(ws), encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and st.get("day") == today():
            return st
    except Exception:  # noqa: BLE001
        pass
    return {"day": today(), "tokens": None, "tokens_src": "—", "tool_calls": 0,
            "turns": 0, "quota_last": None, "sessions": {}}


def save_state(ws: str, st: dict) -> None:
    try:
        d = os.path.join(ws, ".atomcode")
        os.makedirs(d, exist_ok=True)
        tmp = state_path(ws) + ".tmp{}".format(os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, state_path(ws))
    except Exception:  # noqa: BLE001
        pass


def api_key() -> str:
    k = (os.environ.get("HCA_LLM_API_KEY") or "").strip()
    if k:
        return k
    f = (os.environ.get("HCA_LLM_API_KEY_FILE") or "").strip()
    if f:
        try:
            with open(f, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""
    return ""


def quota_used():
    """网关已用配额。取不到返回 None —— 不猜、不填 0（与 tools/eval 同一口径）。"""
    key = api_key()
    if not key:
        return None
    import urllib.request  # noqa: PLC0415
    try:
        req = urllib.request.Request(QUOTA_URL, headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=QUOTA_TIMEOUT_S) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for k in ("used_today", "used", "used_tokens", "consumed"):
        if isinstance(d.get(k), int):
            return d[k]
    lim, rem = d.get("limit_daily") or d.get("limit"), d.get("remaining")
    return lim - rem if isinstance(lim, int) and isinstance(rem, int) else None


def meta_path_of(transcript_path: str) -> str:
    """`<sid>.jsonl` → `<sid>.meta`（同目录同名，见 session/manager.rs 的 path_for）。"""
    if not transcript_path:
        return ""
    base = transcript_path
    for suf in (".jsonl",):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return base + ".meta"


def read_meta(transcript_path: str):
    """返回 (tool_calls, tokens|None, turns, turns_with_usage)。读不到返回全 None。"""
    p = meta_path_of(transcript_path)
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    stats = meta.get("turn_stats")
    if not isinstance(stats, list):
        return None
    tool_calls = 0
    tokens = 0
    turns = 0
    with_usage = 0
    for t in stats:
        if not isinstance(t, dict):
            continue
        turns += 1
        tc = t.get("tool_call_count")
        if isinstance(tc, int):
            tool_calls += tc
        mu = t.get("model_usage")
        if isinstance(mu, list) and mu:
            got = False
            for one in mu:
                tk = (one or {}).get("tokens") or {}
                vals = [tk.get("input"), tk.get("output"), tk.get("cached_input")]
                if any(isinstance(v, int) for v in vals):
                    tokens += sum(v for v in vals if isinstance(v, int))
                    got = True
            if got:
                with_usage += 1
    return tool_calls, (tokens if with_usage else None), turns, with_usage


def append_jsonl(ws: str, rec: dict) -> None:
    try:
        d = os.path.join(ws, ".atomcode")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "budget.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


# ── Stop / StopFailure：记账 ────────────────────────────────────────────────

def on_stop(ev: dict, ws: str) -> None:
    st = load_state(ws)
    sid = ev.get("session_id") or ""
    m = read_meta(ev.get("transcript_path") or "")

    rec = {
        "ts": datetime.datetime.now(SH_TZ).isoformat(timespec="seconds"),
        "event": ev.get("hook_event_name"),
        "session_id": sid or None,
        "stop_reason": ev.get("stop_reason"),
    }

    # ① 会话 meta（按会话精确，工具次数可靠、token 覆盖可能不全）
    if m:
        tool_calls, tokens, turns, with_usage = m
        prev = (st["sessions"].get(sid) or {}) if sid else {}
        d_calls = max(0, tool_calls - int(prev.get("tool_calls") or 0))
        st["tool_calls"] = int(st.get("tool_calls") or 0) + d_calls
        st["turns"] = int(st.get("turns") or 0) + max(0, turns - int(prev.get("turns") or 0))
        if sid:
            st["sessions"][sid] = {"tool_calls": tool_calls, "turns": turns,
                                   "tokens": tokens, "turns_with_usage": with_usage}
        rec.update({"meta_tool_calls_total": tool_calls, "meta_tool_calls_delta": d_calls,
                    "meta_turns": turns, "meta_turns_with_usage": with_usage,
                    "meta_tokens": tokens})
    else:
        rec["meta"] = "—（读不到 .meta）"

    # ② 网关配额差值（按 key 全局，兜底）
    used = quota_used()
    rec["quota_used"] = used
    if isinstance(used, int):
        last = st.get("quota_last")
        if isinstance(last, int) and used >= last:
            delta = used - last
            rec["quota_delta"] = delta
            st["tokens_quota"] = int(st.get("tokens_quota") or 0) + delta
        else:
            # 第一次读到 / 配额被重置：差值没有意义，只记基线
            rec["quota_delta"] = None
        st["quota_last"] = used
    else:
        rec["quota_delta"] = None

    # ③ 选 token 源：meta 覆盖全了才用 meta，否则用配额差值，都没有就 None
    sessions = st.get("sessions") or {}
    meta_full = bool(sessions) and all(
        isinstance(v, dict) and v.get("turns") and v.get("turns_with_usage") == v.get("turns")
        and isinstance(v.get("tokens"), int)
        for v in sessions.values())
    if meta_full:
        st["tokens"] = sum(int(v["tokens"]) for v in sessions.values())
        st["tokens_src"] = "session-meta"
    elif isinstance(st.get("tokens_quota"), int):
        st["tokens"] = int(st["tokens_quota"])
        st["tokens_src"] = "gateway-quota-delta(按 key 全局，见文件头)"
    else:
        st["tokens"] = None
        st["tokens_src"] = "—"

    rec["day_tokens"] = st.get("tokens")
    rec["day_tokens_src"] = st.get("tokens_src")
    rec["day_tool_calls"] = st.get("tool_calls")
    save_state(ws, st)
    append_jsonl(ws, rec)


# ── UserPromptSubmit：超了就拦 ──────────────────────────────────────────────

def on_prompt(ev: dict, ws: str) -> dict | None:
    st = load_state(ws)
    lim_tok = limit("HCA_BUDGET_DAILY_TOKENS")
    lim_calls = limit("HCA_BUDGET_DAILY_TOOL_CALLS")
    lim_sess = limit("HCA_BUDGET_SESSION_TOOL_CALLS")
    reset = (datetime.datetime.now(SH_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
             + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")

    tok = st.get("tokens")
    if lim_tok and isinstance(tok, int) and tok >= lim_tok:
        return {"decision": "block", "reason": (
            "今日 token 预算已用满：{} / {}（计量来源 {}，上海时间 {} 重置）。"
            "本轮没有发给模型。需要继续请让管理员调高 HCA_BUDGET_DAILY_TOKENS "
            "或关掉 HCA_BUDGET_ENABLED。".format(tok, lim_tok, st.get("tokens_src"), reset))}

    calls = st.get("tool_calls")
    if lim_calls and isinstance(calls, int) and calls >= lim_calls:
        return {"decision": "block", "reason": (
            "今日工具调用次数已用满：{} / {} 次（上海时间 {} 重置）。本轮没有发给模型。"
            "需要继续请让管理员调高 HCA_BUDGET_DAILY_TOOL_CALLS。".format(calls, lim_calls, reset))}

    sid = ev.get("session_id") or ""
    if lim_sess and sid:
        sc = ((st.get("sessions") or {}).get(sid) or {}).get("tool_calls")
        if isinstance(sc, int) and sc >= lim_sess:
            return {"decision": "block", "reason": (
                "本会话工具调用次数已用满：{} / {} 次。请开一个新会话，"
                "或让管理员调高 HCA_BUDGET_SESSION_TOOL_CALLS。".format(sc, lim_sess))}
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw)
        if not isinstance(ev, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        return 0

    if not enabled():
        return 0  # 默认关：读完 stdin 立刻退，什么都不写、什么都不拦

    ws = os.path.normpath(os.environ.get("HCA_WORKSPACE") or ev.get("cwd") or os.getcwd())
    event = ev.get("hook_event_name")
    try:
        if event in ("Stop", "StopFailure"):
            on_stop(ev, ws)
        elif event == "UserPromptSubmit":
            d = on_prompt(ev, ws)
            if d:
                sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        # 记账/判定自己出错绝不能拖垮对话：说一声（stderr 不参与判定）然后 exit 0
        print("[budget] {}：{}".format(type(e).__name__, e), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
