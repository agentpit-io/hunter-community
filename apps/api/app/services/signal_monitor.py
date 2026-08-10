"""宏观市场信号监控 — 信号触发后逐用户分析影响，一用户一条聚合推送。"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
import redis as _redis_lib
from loguru import logger

from app.services.database import (
    save_market_signal, signal_triggered_today,
    get_users_subscribed_to, get_stocks_by_user,
    save_signal_report,
)

CST = timezone(timedelta(hours=8))

_CPI_CONSENSUS_YOY = float(os.getenv("CPI_CONSENSUS_YOY", "2.5"))
_OIL_SPIKE_1H_PCT  = float(os.getenv("OIL_SPIKE_1H_PCT",  "3.0"))
_OIL_SPIKE_24H_PCT = float(os.getenv("OIL_SPIKE_24H_PCT", "5.0"))

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis     = _redis_lib.from_url(_REDIS_URL, decode_responses=True)

_last_cpi_period: str  = ""
_oil_baseline_24h: float = 0.0
_oil_baseline_1h:  float = 0.0
_oil_1h_at: datetime   = datetime.min.replace(tzinfo=CST)

SIGNAL_LABEL = {
    "cpi":        "美国 CPI",
    "oil":        "油价异动",
    "fomc":       "FOMC 决议",
    "spacex":     "SpaceX IPO",
    "northbound": "北向资金",
}

SCENARIO_ZH = {
    "HAWKISH_SHOCK":  "通胀超预期，加息叙事强化",
    "DOVISH_RELIEF":  "通胀降温，风险偏好修复",
    "IN_LINE":        "基本符合预期，影响有限",
    "OIL_SPIKE":      "油价急涨",
    "OIL_DROP":       "油价急跌",
}


def _now() -> datetime:
    return datetime.now(CST)


# ── LLM 分析单只股票受信号影响 ──────────────────────────────────────────────

def _analyze_stock_impact_sync(signal_type: str, scenario: str,
                                signal_data: dict,
                                stock_code: str, stock_name: str) -> dict:
    """返回 {affected, direction, impact, reason}"""
    import re as _re
    from app.services.online_analysis.llm_client import get_client

    signal_zh   = SIGNAL_LABEL.get(signal_type, signal_type)
    scenario_zh = SCENARIO_ZH.get(scenario, scenario)

    extra = ""
    if signal_type == "cpi":
        extra = f"实际同比 {signal_data.get('yoy','?')}%，预期 {_CPI_CONSENSUS_YOY}%"
    elif signal_type == "oil":
        extra = f"布伦特 ${signal_data.get('price','?')}/桶，24H {signal_data.get('change_24h','?')}%"

    prompt = f"""你是A股专业分析师。根据以下宏观信号，判断该股票的受影响情况。

宏观信号：{signal_zh} — {scenario_zh}
{extra}
股票：{stock_name}（{stock_code}）

请严格按以下4行格式回答，不要多余内容：
AFFECTED: yes/no
DIRECTION: benefit/pressure/neutral
IMPACT: high/medium/low
REASON: 用2-3句中文说明影响机制和程度，不超过80字"""

    client = get_client()
    if client is None:
        return {"affected": False, "direction": "neutral", "impact": "low", "reason": ""}

    try:
        resp = client.chat.completions.create(
            model=os.getenv("SIGNAL_ANALYSIS_MODEL", "gemini-3.1-pro-preview"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            tools=[{"type": "google_search"}],
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("[signal] llm call failed: {}", e)
        return {"affected": False, "direction": "neutral", "impact": "low", "reason": ""}

    affected_m  = _re.search(r"AFFECTED:\**\s*(yes|no)", raw, _re.IGNORECASE)
    direction_m = _re.search(r"DIRECTION:\**\s*(benefit|pressure|neutral)", raw, _re.IGNORECASE)
    impact_m    = _re.search(r"IMPACT:\**\s*(high|medium|low)", raw, _re.IGNORECASE)
    reason_m    = _re.search(r"REASON:\**\s*(.+)", raw, _re.DOTALL)
    affected  = affected_m.group(1).lower() == "yes" if affected_m else False
    direction = direction_m.group(1).lower() if direction_m else "neutral"
    impact    = impact_m.group(1).lower() if impact_m else "low"
    reason    = reason_m.group(1).strip().rstrip("*").strip()[:150] if reason_m else ""
    return {"affected": affected, "direction": direction, "impact": impact, "reason": reason}


async def _get_stock_impact(signal_type: str, scenario: str,
                            signal_data: dict,
                            stock_code: str, stock_name: str) -> dict:
    """带 Redis 缓存的股票影响分析（同一股票+信号类型+场景只分析一次）"""
    cache_key = f"sig_impact:{signal_type}:{scenario}:{stock_code}"
    cached = _redis.get(cache_key)
    if cached:
        return json.loads(cached)

    impact = await asyncio.to_thread(
        _analyze_stock_impact_sync, signal_type, scenario, signal_data, stock_code, stock_name
    )
    _redis.set(cache_key, json.dumps(impact, ensure_ascii=False), ex=86400)
    return impact


# ── 聚合推送 ─────────────────────────────────────────────────────────────────

def _build_push_content(signal_type: str, scenario: str,
                        signal_data: dict, affected_stocks: list) -> str:
    """生成聚合推送文本。affected_stocks = [{name, code, direction, impact, reason}]"""
    signal_zh   = SIGNAL_LABEL.get(signal_type, signal_type)
    scenario_zh = SCENARIO_ZH.get(scenario, scenario)
    now_date    = _now().strftime("%m/%d")

    # 头部
    if signal_type == "cpi":
        yoy = signal_data.get("yoy", "?")
        consensus = _CPI_CONSENSUS_YOY
        surprise = round(float(yoy) - consensus, 2) if yoy != "?" else 0
        mood = "🔴" if scenario == "HAWKISH_SHOCK" else "🟢" if scenario == "DOVISH_RELIEF" else "🟡"
        header = (
            f"📊 {signal_zh} · {now_date}\n"
            f"同比 {yoy}%  预期 {consensus}%  偏差 {surprise:+.2f}%\n"
            f"{mood} {scenario_zh}"
        )
    elif signal_type == "oil":
        price  = signal_data.get("price", "?")
        chg    = signal_data.get("change_24h", "?")
        chg1h  = signal_data.get("change_1h", "?")
        mood   = "🔴" if scenario == "OIL_SPIKE" else "🟢"
        chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else f"{chg}%"
        chg1h_str = f"  1H {chg1h:+.2f}%" if isinstance(chg1h, (int, float)) else ""
        header = (
            f"🛢️ {signal_zh} · {now_date}\n"
            f"布伦特 ${price}/桶  24H {chg_str}{chg1h_str}\n"
            f"{mood} {scenario_zh}"
        )
    else:
        header = f"📡 {signal_zh} · {now_date}\n{scenario_zh}"

    IMPACT_LABEL = {"high": "高", "medium": "中", "low": "低"}

    # 按方向分组
    benefit  = [s for s in affected_stocks if s["direction"] == "benefit"]
    pressure = [s for s in affected_stocks if s["direction"] == "pressure"]
    neutral  = [s for s in affected_stocks if s["direction"] == "neutral"]

    # 在同方向内按 impact 排序（high→medium→low）
    order = {"high": 0, "medium": 1, "low": 2}
    for grp in (benefit, pressure, neutral):
        grp.sort(key=lambda x: order.get(x.get("impact", "low"), 2))

    lines = [header, "", f"本次信号影响你的 {len(affected_stocks)} 只持仓："]

    def render_group(grp: list, icon: str, label: str):
        lines.append(f"\n{icon} {label}（{len(grp)}只）：")
        for s in grp:
            lvl = IMPACT_LABEL.get(s.get("impact", "low"), "低")
            lines.append(f"\n{icon} {s['name']}（{s['code']}）[{lvl}度{label}]")
            if s.get("reason"):
                lines.append(s["reason"])

    if benefit:
        render_group(benefit, "🟢", "受益")
    if pressure:
        render_group(pressure, "🔴", "承压")
    if neutral:
        render_group(neutral, "🟡", "中性")

    return "\n".join(lines)


_REPORT_CSS = """
  :root{--bg:#F7F3EC;--paper:#FFFDF9;--paper2:#EFE8DC;--ink:#211C18;--ink-soft:#4B423A;--ink-faint:#7A6F63;--copper:#B06A32;--copper-lt:#D4925A;--teal:#127A7E;--teal-lt:#1A9FA4;--red:#A4332B;--green:#3F6B40;--amber:#B8862A;--line:#D8CDBA;--dark:#1A1614;}
  *{box-sizing:border-box;margin:0;padding:0}html{scroll-behavior:smooth}
  body{font-family:"Songti SC","Source Han Serif SC","Noto Serif CJK SC",Georgia,serif;background:var(--bg);color:var(--ink);line-height:1.65;font-size:15px;}
  .wrap{max-width:1180px;margin:0 auto;padding:0 28px}
  .hero{background:var(--dark);color:var(--paper);padding:54px 0 60px;position:relative;overflow:hidden;border-bottom:6px solid var(--copper);}
  .hero::before{content:"";position:absolute;left:0;top:0;bottom:0;width:8px;background:linear-gradient(180deg,var(--copper) 0%,var(--teal) 100%);}
  .hero .kicker{font-family:Georgia,serif;letter-spacing:.32em;font-size:11px;color:var(--copper-lt);text-transform:uppercase;margin-bottom:18px;}
  .hero h1{font-family:Georgia,"Songti SC",serif;font-size:42px;font-weight:700;line-height:1.15;margin-bottom:14px;}
  .hero h1 em{color:var(--copper-lt);font-style:normal}
  .hero .stand{font-size:16px;color:#C8BEB2;max-width:780px;line-height:1.7;margin-top:8px;}
  .hero .meta{margin-top:22px;display:flex;gap:24px;flex-wrap:wrap;font-size:12px;color:var(--ink-faint);}
  .hero .meta span{display:inline-flex;align-items:center;gap:6px}
  .hero .meta strong{color:var(--copper-lt);font-weight:600}
  section{padding:48px 0}
  .sec-tag{display:inline-block;background:var(--copper);color:var(--paper);font-size:10.5px;letter-spacing:.22em;font-weight:700;padding:5px 12px;margin-bottom:16px;}
  .sec-tag.teal{background:var(--teal)}.sec-tag.dark{background:var(--dark)}
  h2{font-family:Georgia,"Songti SC",serif;font-size:30px;font-weight:700;line-height:1.25;margin-bottom:12px;color:var(--ink);}
  h2 .sub{color:var(--ink-faint);font-weight:400;font-size:18px;display:block;margin-top:4px}
  .env-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:24px;margin-top:24px;}
  .env-card{background:var(--paper);border:1px solid var(--line);padding:24px 26px;border-left:4px solid var(--copper);}
  .env-card.teal{border-left-color:var(--teal)}
  .env-card h3{font-size:16px;font-weight:700;margin-bottom:14px;color:var(--ink);}
  .env-card p{font-size:14px;color:var(--ink-soft);margin-bottom:10px;line-height:1.75}
  .env-card p:last-child{margin-bottom:0}
  .data-row{display:grid;grid-template-columns:1fr auto auto;gap:12px;padding:8px 0;border-bottom:1px dashed var(--line);font-size:13.5px}
  .data-row:last-child{border-bottom:none}.data-row .name{color:var(--ink)}.data-row .val{color:var(--ink-soft)}.data-row .chg{font-weight:700}
  .chg.up{color:var(--red)}.chg.dn{color:var(--green)}.chg.usdn{color:var(--red)}.chg.usup{color:var(--green)}
  .stocks{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;margin-top:28px}
  .card{background:var(--paper);border:1px solid var(--line);overflow:hidden;transition:transform .2s ease,box-shadow .2s ease;}
  .card:hover{transform:translateY(-2px);box-shadow:0 6px 22px rgba(33,28,24,.08)}
  .card .head{padding:18px 22px 14px;background:var(--paper2);display:flex;justify-content:space-between;align-items:flex-start;gap:14px;border-bottom:1px solid var(--line);}
  .card .name{font-family:Georgia,"Songti SC",serif;font-size:22px;font-weight:700;color:var(--ink);line-height:1.2}
  .card .code{font-size:11px;color:var(--ink-faint);font-family:Georgia,monospace;letter-spacing:.05em;margin-top:4px}
  .card .tags{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
  .card .tag{font-size:10.5px;padding:2px 8px;border:1px solid var(--line);color:var(--ink-faint);background:var(--paper);}
  .card .price-block{text-align:right;flex-shrink:0}
  .card .price{font-family:Georgia,serif;font-size:24px;font-weight:700;color:var(--ink);line-height:1}
  .card .chg-pct{font-size:13px;font-weight:700;margin-top:6px}
  .action{padding:12px 22px;background:var(--dark);color:var(--paper);display:flex;justify-content:space-between;align-items:center;font-size:13px;}
  .action-label{letter-spacing:.18em;font-size:10px;color:#C8BEB2}
  .action-tag{font-size:13px;font-weight:700;padding:6px 14px;letter-spacing:.05em;background:var(--copper);color:var(--paper);}
  .action-tag.reduce{background:var(--red)}.action-tag.hold{background:var(--amber)}.action-tag.add{background:var(--green)}.action-tag.watch{background:var(--teal)}
  .card .body{padding:18px 22px 20px}.card .row{margin-bottom:14px}.card .row:last-child{margin-bottom:0}
  .card .row .label{font-size:10.5px;letter-spacing:.22em;color:var(--copper);font-weight:700;margin-bottom:6px;}
  .card .row .text{font-size:13.5px;color:var(--ink-soft);line-height:1.7}.card .row .text strong{color:var(--ink);font-weight:600}
  .summary-section{background:var(--dark);color:var(--paper);padding:48px 0;margin-top:20px}
  .summary-section h2{color:var(--paper)}.summary-section h2 .sub{color:#A8A29B}
  .summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:24px}
  .sum-card{background:#251E1A;border:1px solid #3A302A;padding:22px;border-top:3px solid var(--copper);}
  .sum-card.reduce{border-top-color:var(--red)}.sum-card.hold{border-top-color:var(--amber)}.sum-card.add{border-top-color:var(--green)}
  .sum-card .num{font-family:Georgia,serif;font-size:38px;font-weight:700;color:var(--copper-lt);line-height:1}
  .sum-card .lab{font-size:11px;letter-spacing:.22em;color:#A8A29B;margin-top:8px;text-transform:uppercase}
  .sum-card .list{margin-top:14px;font-size:13.5px;line-height:2;color:#D8CDBA}.sum-card .list b{color:var(--paper);font-weight:600}
  .plan{margin-top:32px}.plan h3{font-size:18px;color:var(--paper);margin-bottom:14px;font-family:Georgia,"Songti SC",serif}
  .plan ol{list-style:none;counter-reset:plan;padding:0}
  .plan li{counter-increment:plan;padding:14px 20px 14px 60px;background:#251E1A;margin-bottom:10px;position:relative;border-left:3px solid var(--copper);font-size:14px;color:#D8CDBA;line-height:1.7;}
  .plan li::before{content:counter(plan,decimal-leading-zero);position:absolute;left:20px;top:14px;font-family:Georgia,serif;font-size:18px;font-weight:700;color:var(--copper-lt);}
  .plan li b{color:var(--paper);font-weight:600}
  .disclaim{background:var(--paper2);padding:22px 28px;margin:36px 0 0;font-size:12px;color:var(--ink-soft);line-height:1.8;border-left:3px solid var(--amber);}
  footer{padding:30px 0 36px;text-align:center;font-size:12px;color:var(--ink-faint);border-top:1px solid var(--line);margin-top:28px;background:var(--paper);}
  footer .brand{font-family:Georgia,serif;font-weight:700;color:var(--copper);font-size:14px}
  @media(max-width:820px){.hero h1{font-size:28px}.env-grid,.stocks,.summary-grid{grid-template-columns:1fr}.wrap{padding:0 18px}}
"""

_REPORT_BASE_URL = os.getenv("REPORT_BASE_URL", "https://hunter.agentpit.io/api/v1/signal/report")


def _load_skill() -> str:
    """加载 market-event-analyst.skill 作为分析方法论上下文，失败时返回空字符串。
    优先读 SIGNAL_ANALYST_SKILL_PATH 环境变量，否则按相对路径推断。"""
    candidates = []
    env_path = os.getenv("SIGNAL_ANALYST_SKILL_PATH", "")
    if env_path:
        candidates.append(env_path)
    # 仓库内路径：api/skills/market-event-analyst.skill（相对 signal_monitor.py 上 2 级到 api/）
    repo_skill = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "skills", "market-event-analyst.skill")
    )
    candidates.append(repo_skill)

    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # 去掉 frontmatter（--- ... ---）
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    content = content[end + 3:].lstrip()
            logger.info("[signal] skill loaded from {}, chars={}", path, len(content))
            return content
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("[signal] load skill failed at {}: {}", path, e)
    logger.warning("[signal] skill file not found, using fallback system prompt")
    return ""


# 应用范围：仅 A 股。gm 端港/美股走独立信号链路（本函数不覆盖）。
def _fetch_prices_for_stocks(stocks: list) -> dict:
    """为 affected_stocks 批量拉取当前价快照（A 股）。
    返回 {code: {"price", "change_pct", "prev_close", "ts"}}；拉不到的 code 不进 dict。
    """
    from app.services.finance_data_client import get_quote, get_reliable_close
    today = _now().strftime("%Y-%m-%d")
    result: dict[str, dict] = {}
    for s in stocks:
        code = s.get("code")
        if not code:
            continue
        try:
            q = get_quote(code)
            if q and (q.get("ts") or "")[:10] != today:
                fresh = get_reliable_close(code, today)
                if fresh:
                    q = fresh
            if q and q.get("price"):
                result[code] = {
                    "price":      q.get("price"),
                    "change_pct": q.get("change_pct"),
                    "prev_close": q.get("prev_close"),
                    "ts":         q.get("ts", ""),
                }
        except Exception as e:
            logger.warning("[signal] fetch price failed code={} err={}", code, e)
    logger.info("[signal] price snapshot: {}/{} succeeded", len(result), len(stocks))
    return result


def _generate_html_report_sync(
    signal_type: str, scenario: str, signal_data: dict,
    affected_stocks: list, all_user_stocks: list,
    signal_id: int,
) -> str:
    """用 LLM 生成个性化持仓影响分析 HTML 报告，返回完整 HTML 字符串"""
    signal_zh   = SIGNAL_LABEL.get(signal_type, signal_type)
    scenario_zh = SCENARIO_ZH.get(scenario, scenario)
    now_str     = _now().strftime("%Y-%m-%d %H:%M")

    # 构建信号数据描述
    if signal_type == "oil":
        chg24 = signal_data.get('change_24h', None)
        chg1h = signal_data.get('change_1h', None)
        chg24_str = f"{float(chg24):+.2f}%" if chg24 is not None else "N/A"
        chg1h_str = f"{float(chg1h):+.2f}%" if chg1h is not None else "N/A"
        signal_desc = (f"布伦特原油 ${signal_data.get('price','?')}/桶，"
                       f"24H {chg24_str}，1H {chg1h_str}")
    elif signal_type == "cpi":
        yoy = signal_data.get('yoy', _CPI_CONSENSUS_YOY)
        try:
            dev = round(float(yoy) - _CPI_CONSENSUS_YOY, 2)
            dev_str = f"{dev:+.2f}%"
        except (TypeError, ValueError):
            dev_str = "N/A"
        signal_desc = (f"美国CPI同比 {yoy}%，"
                       f"预期 {_CPI_CONSENSUS_YOY}%，偏差 {dev_str}")
    elif signal_type == "fomc":
        rate     = signal_data.get("rate", "?")
        prev     = signal_data.get("prev_rate", "?")
        delta    = signal_data.get("change", 0)
        delta_str = f"{delta:+.2f}%" if isinstance(delta, (int, float)) else str(delta)
        signal_desc = f"联邦基金利率 {prev}% → {rate}%（变动 {delta_str}）"
    elif signal_type == "northbound":
        net = signal_data.get("net_flow", "?")
        net_str = f"{float(net):+.1f}" if isinstance(net, (int, float)) else str(net)
        signal_desc = f"北向资金净流入 {net_str}亿元"
    else:
        signal_desc = str(signal_data)

    # 受影响持仓数据
    IMPACT_ZH = {"high": "高", "medium": "中", "low": "低"}
    DIR_ZH    = {"benefit": "受益", "pressure": "承压", "neutral": "中性"}

    # 拉取当前价快照，注入 prompt 防止 LLM 幻觉出偏离现价的建议价（仅 A 股）
    price_map = _fetch_prices_for_stocks(affected_stocks)
    snapshot_time = ""
    for _p in price_map.values():
        _ts = (_p.get("ts") or "")[:16]
        if _ts:
            snapshot_time = _ts
            break
    if not snapshot_time:
        snapshot_time = _now().strftime("%Y-%m-%d %H:%M")

    affected_lines = []
    for s in affected_stocks:
        p = price_map.get(s["code"])
        if p and p.get("price"):
            chg = p.get("change_pct")
            chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "N/A"
            prev = p.get("prev_close")
            prev_str = f"{prev:.2f}" if isinstance(prev, (int, float)) else "N/A"
            ts_str = (p.get("ts") or "")[:16]
            price_seg = (f" | 当前价:{p['price']:.2f}元"
                         f"(今涨跌{chg_str},昨收{prev_str},快照{ts_str})")
        else:
            price_seg = " | 当前价:暂无"
        affected_lines.append(
            f"- {s['name']}（{s['code']}）| 方向:{DIR_ZH.get(s['direction'],'?')} "
            f"| 影响度:{IMPACT_ZH.get(s['impact'],'?')} | 分析:{s.get('reason','')}{price_seg}"
        )

    unaffected = [s for s in all_user_stocks if s["code"] not in {a["code"] for a in affected_stocks}]
    unaffected_names = "、".join(f"{s['name']}({s['code']})" for s in unaffected) or "无"

    # 加载分析方法论 skill
    skill_context = _load_skill()

    system_prompt = skill_context if skill_context else (
        "你是专业A股/港股投资分析师，擅长将宏观信号转化为对具体持仓的结构化、可操作影响分析。"
    )

    user_prompt = f"""## 本次触发信号
- 类型：{signal_zh}
- 场景：{scenario_zh}
- 数据：{signal_desc}
- 触发时间：{now_str}

## 受影响的持仓（{len(affected_stocks)}只）
{chr(10).join(affected_lines)}

## 未受影响的持仓（{len(unaffected)}只）
{unaffected_names}

## 输出要求
请严格只输出<body>...</body>标签内的HTML内容（不含<html>/<head>/<style>标签，CSS已在外部处理）。

报告结构：
1. **Hero头部** (.hero > .wrap)：
   - .kicker：信号类型标签
   - h1：{len(affected_stocks)}只持仓的<em>信号影响分析</em>
   - .stand：2-3句精准描述这次信号的性质和对该用户持仓的核心影响（含传导链关键节点）
   - .meta：触发时间、信号数据的关键数字

2. **宏观解读** (section > .wrap)：
   - .sec-tag.teal：01 · 信号性质解读
   - h2 + .sub
   - .env-grid：2个.env-card，每个分析一条关键传导链（结合该用户持仓方向具体说，有数字）
   - 用.data-row展示关键数据

3. **逐只股票卡片** (section > .wrap > .stocks)：
   - .sec-tag：02 · 分股建议
   - 每只股票一个.card：
     - .head：.name + .code（如该股"当前价"有数字，在.price-block内展示当前价和今涨跌幅）
     - .action：.action-label"信号建议" + .action-tag（class选reduce/hold/add/watch）
     - .body：3个.row分别是.label"核心理由"/.label"传导路径"/.label"操作建议"
       内容要具体、有数字、有逻辑，基于该股票行业和商业模式，不写通用模板

【操作建议价格硬规则 — 违反视为严重错误】
- 操作建议禁止出现任何具体的加仓价/目标价/止损价数字（无论"当前价"是否已知）
  原因：具体百分比区间缺乏严谨的量化依据，容易误导用户
- 一律改用条件式/相对表述，例如：
  * "若跌破关键支撑再考虑加仓"
  * "回踩 20 日均线附近关注"
  * "若继续上行突破前期高点可确认趋势"
  * "关注下一交易日成交量能否放大配合"
- 若该股"当前价"有具体数字：可在操作建议开头以"现价 XX 元"客观陈述，但后续不可给出建议价格数字
- 仓位建议（如"仓位控制在 5-10%"、"减仓 1/3"）不受此限制，可继续使用

4. **组合汇总** (.summary-section > .wrap)：
   - .sec-tag：03 · 组合总览
   - .summary-grid：3个.sum-card（.add受益、.hold中性、.reduce承压）
   - .plan：3-5条具体操作步骤（.plan ol > li > b标注关键词）

5. **免责声明** (.disclaim) + **页脚** (footer)
   - .disclaim 末尾必须追加一句："本报告价格快照采集于 {snapshot_time}，当前市场价格可能已发生变化，请以实时行情为准。"

只输出body内HTML，不含任何说明文字。"""

    from openai import OpenAI
    _api_key = os.getenv("ONE_API_KEY", "")
    _base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    if not _api_key:
        logger.warning("[signal] html report: ONE_API_KEY 未配置")
        return ""
    # 使用 Flash 模型：Pro 模型的 Extended Thinking 会把推理链输出在 HTML 前污染报告
    _html_model = os.getenv("SIGNAL_ANALYSIS_MODEL", os.getenv("ONE_API_MODEL", "gemini-2.0-flash"))
    client = OpenAI(api_key=_api_key, base_url=_base_url, timeout=120)
    try:
        resp = client.chat.completions.create(
            model=_html_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            extra_body={"thinking_budget": -1},  # 关闭 Gemini Extended Thinking
            tools=[{"type": "google_search"}],
        )
        body_html = (resp.choices[0].message.content or "").strip()
        # 清理 markdown 代码块
        if body_html.startswith("```"):
            body_html = body_html.split("\n", 1)[1] if "\n" in body_html else body_html[3:]
        if body_html.endswith("```"):
            body_html = body_html[:-3]
        body_html = body_html.strip()
        # 如果 LLM 输出了完整 <body>...</body>，只取 body 内容
        import re as _re2
        _bm = _re2.search(r'<body[^>]*>(.*)</body>', body_html, _re2.DOTALL | _re2.IGNORECASE)
        if _bm:
            body_html = _bm.group(1).strip()
        # 精确找 <div class="hero"> 跳过 Gemini 思考链文字；fallback 找第一个块级标签
        _hero2 = _re2.search(r'<div\s+class=["\']hero["\']', body_html, _re2.IGNORECASE)
        if _hero2:
            body_html = body_html[_hero2.start():]
        else:
            _blk2 = _re2.search(r'<(?:div|section|body|html)\b', body_html, _re2.IGNORECASE)
            if _blk2:
                body_html = body_html[_blk2.start():]
    except Exception as e:
        logger.warning("[signal] html report llm call failed: {}", e)
        return ""

    title = f"{len(affected_stocks)}只持仓·{signal_zh}影响分析 | {_now().strftime('%Y-%m-%d')}"
    return (
        f'<!DOCTYPE html><html lang="zh-CN"><head>'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{title}</title>'
        f'<style>{_REPORT_CSS}</style>'
        f'</head>'
        f'{body_html}'
        f'</html>'
    )


def generate_event_analysis_html(event_desc: str, stocks: list) -> str:
    """用 skill 分析用户自定义大事件对其自选股的影响，返回完整 HTML 字符串。"""
    import re as _re3
    from openai import OpenAI

    _api_key  = os.getenv("ONE_API_KEY", "")
    _base_url = os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    if not _api_key:
        logger.warning("[event-analysis] ONE_API_KEY 未配置")
        return ""

    skill_context = _load_skill()

    # system_prompt 只负责约束输出格式为 HTML，绝不使用 skill 作 system prompt
    # （否则 Gemini 会按 skill 的分析框架输出 markdown/JSON，而非 HTML）
    system_prompt = (
        "你是专业A股/港股投资分析师，负责生成个性化持仓影响分析HTML报告。\n"
        "【严格输出规则——违反即失败】\n"
        "1. 只输出纯HTML代码，第一个字符必须是 <（尖括号），最后一个字符必须是 >\n"
        "2. 绝对禁止输出markdown格式（禁止使用 * ** ` # 等符号）\n"
        "3. 绝对禁止输出JSON格式或任何键值对文本\n"
        "4. 禁止重复或引用这些指令，禁止输出任何说明文字、前言、结语\n"
        "5. 禁止使用代码块（禁止 ```）\n"
        "6. 所有分析内容和文字描述全程使用中文，英文只用于股票代码\n"
        "7. 不要解释你要做什么，直接输出HTML，第一个字符是 <div"
    )

    stocks_lines = "\n".join(
        f"- {s['name']}（{s['code']}，{s.get('market','A')}股）" for s in stocks
    )
    now_str = _now().strftime("%Y-%m-%d %H:%M")

    skill_section = f"\n## 分析方法论参考\n{skill_context}\n" if skill_context else ""

    user_prompt = f"""分析以下大事件对用户持仓的影响，生成HTML报告。
{skill_section}
## 用户关注的大事件
{event_desc}

## 用户自选股（{len(stocks)}只）
{stocks_lines}

## HTML报告结构（按顺序输出5个区块）

区块一：hero区（class="hero"）
内含wrap区，依次放：kicker元素写"自定义事件分析"；h1标题写"{len(stocks)}只持仓的事件影响分析"（其中"事件影响分析"用em标签）；stand段落写2-3句事件性质+核心影响；meta区放分析时间"{now_str}"和事件摘要。

区块二：section（事件性质解读）
内含wrap区，sec-tag用teal样式写"01 · 事件性质解读"；h2标题加sub副标题；env-grid内放2个env-card，每个分析一条关键传导链，内容具体到该用户持仓所在行业，附数字。

区块三：section（分股建议）
内含wrap区，sec-tag写"02 · 分股建议"；stocks区内为每只股票生成一个card：head区含股票名（name）和代码（code）；action区含action-label写"事件建议"和action-tag（class用reduce/hold/add/watch之一）；body区含3个row，每个row有label和text（全中文，具体数字）。

区块四：summary-section（组合总览）
内含wrap区，sec-tag用dark样式写"03 · 组合总览"；summary-grid含3个sum-card（class分别用add/hold/reduce），各含数字、分类名称、相关股票列表；plan区含h3标题"操作步骤"和3-5条ol li操作（关键词用b标签加粗）。

区块五：结尾
disclaim区写免责声明；footer含wrap区，brand写"Hermes · 持仓影响分析"。

所有文字内容全部用中文。英文只允许出现在股票代码中。"""

    # 使用 Flash 模型：Pro 模型的 Extended Thinking 会把推理链输出在 HTML 前污染报告
    _html_model = os.getenv("SIGNAL_ANALYSIS_MODEL", os.getenv("ONE_API_MODEL", "gemini-2.0-flash"))
    client = OpenAI(api_key=_api_key, base_url=_base_url, timeout=120)
    try:
        resp = client.chat.completions.create(
            model=_html_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            extra_body={"thinking_budget": -1},  # 关闭 Gemini Extended Thinking
            tools=[{"type": "google_search"}],
        )
        body_html = (resp.choices[0].message.content or "").strip()
        # 清理 markdown 代码块
        if body_html.startswith("```"):
            body_html = body_html.split("\n", 1)[1] if "\n" in body_html else body_html[3:]
        if body_html.endswith("```"):
            body_html = body_html[:-3]
        body_html = body_html.strip()
        # 如果 LLM 包了完整 <body>...</body>，取 body 内容
        _bm = _re3.search(r'<body[^>]*>(.*)</body>', body_html, _re3.DOTALL | _re3.IGNORECASE)
        if _bm:
            body_html = _bm.group(1).strip()
        # 先剥离任何纯文字前言（跳到第一个 < 字符）
        _lt = body_html.find('<')
        if _lt > 0:
            body_html = body_html[_lt:]
        # 严格匹配"真实"的 hero 区块：必须紧跟 wrap 子 div，防止匹配到模型回显的指令文字
        _hero = _re3.search(
            r'<div\s+class=["\']hero["\'][^>]*>\s*(?:\n\s*)?<div\s+class=["\']wrap["\']',
            body_html, _re3.IGNORECASE
        )
        if _hero:
            body_html = body_html[_hero.start():]
        else:
            # fallback：找任意第一个 div/section 块级标签
            _blk = _re3.search(r'<(?:div|section)\b', body_html, _re3.IGNORECASE)
            if _blk:
                body_html = body_html[_blk.start():]
    except Exception as e:
        logger.warning("[event-analysis] llm call failed: {}", e)
        return ""

    title = f"事件影响分析 | {now_str}"
    return (
        f'<!DOCTYPE html><html lang="zh-CN"><head>'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{title}</title>'
        f'<style>{_REPORT_CSS}</style>'
        f'</head>'
        f'{body_html}'
        f'</html>'
    )


async def _generate_html_report(
    signal_type: str, scenario: str, signal_data: dict,
    affected_stocks: list, all_user_stocks: list,
    signal_id: int, user_id: str,
) -> int:
    """生成 HTML 报告并写入数据库，返回 report_id（失败返回 0）"""
    try:
        html = await asyncio.to_thread(
            _generate_html_report_sync,
            signal_type, scenario, signal_data, affected_stocks, all_user_stocks, signal_id,
        )
        if not html:
            return 0
        report_id = await asyncio.to_thread(save_signal_report, signal_id, user_id, html)
        logger.info("[signal] html report saved report_id={} user={}", report_id, user_id[:8])
        return report_id
    except Exception as e:
        logger.warning("[signal] html report failed user={}: {}", user_id[:8], e)
        return 0


async def _dispatch_signal(signal_type: str, scenario: str, signal_data: dict, sig_id: int = 0, *, target_users: list | None = None) -> None:
    """信号触发后：逐用户分析自选股影响 → 每人发一条聚合推送 + HTML报告。
    target_users: 若传入，只推给指定用户列表（测试模式）。"""
    from app.services import push_scheduler as ps

    users = await asyncio.to_thread(get_users_subscribed_to, signal_type)
    if not users:
        logger.info("[signal] no subscribed users for {}", signal_type)
        return

    if target_users is not None:
        users = [u for u in users if u in target_users]
        if not users:
            logger.info("[signal] target_users not in subscriber list, skip")
            return
        logger.info("[signal] test mode: restricted to {} user(s)", len(users))

    # 收集所有用户的自选股（去重）
    all_stocks: dict[str, str] = {}  # code → name
    user_stocks: dict[str, list] = {}
    for uid in users:
        stocks = await asyncio.to_thread(get_stocks_by_user, uid)
        user_stocks[uid] = stocks
        for s in stocks:
            all_stocks[s["code"]] = s["name"]

    # 并发分析影响（跨用户共享 Redis 缓存）
    logger.info("[signal] analyzing {} unique stocks for {} users", len(all_stocks), len(users))
    stock_items = list(all_stocks.items())
    tasks = [
        _get_stock_impact(signal_type, scenario, signal_data, code, name)
        for code, name in stock_items
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    impact_map: dict[str, dict] = {}
    for (code, _), res in zip(stock_items, results):
        impact_map[code] = res if isinstance(res, dict) else {"affected": False, "direction": "neutral", "impact": "low", "reason": ""}

    # 逐用户构建推送
    task_name = SIGNAL_LABEL.get(signal_type, signal_type)
    for uid in users:
        affected = []
        for s in user_stocks[uid]:
            imp = impact_map.get(s["code"], {})
            if imp.get("affected"):
                affected.append({
                    "code":      s["code"],
                    "name":      s["name"],
                    "direction": imp.get("direction", "neutral"),
                    "impact":    imp.get("impact", "low"),
                    "reason":    imp.get("reason", ""),
                })

        if not affected:
            logger.info("[signal] user={} no affected stocks, skip push", uid[:8])
            continue

        # 并发：生成推送文本 + HTML 报告
        content = _build_push_content(signal_type, scenario, signal_data, affected)
        report_id = await _generate_html_report(
            signal_type, scenario, signal_data,
            affected, user_stocks[uid], sig_id, uid,
        )

        report_url = ""
        if report_id:
            report_url = f"{_REPORT_BASE_URL}/{report_id}"
            content = content + f"\n\n📊 完整持仓分析报告\n{report_url}"

        await ps._broadcast_and_log(
            f"{signal_type}_alert", content, [],
            task_name=task_name, user_id=uid,
            detail_url=report_url,  # 微信「详情」按钮直接打开 HTML 报告
        )
        logger.info("[signal] pushed to user={} affected={} report_id={}", uid[:8], len(affected), report_id)


# ── CPI ──────────────────────────────────────────────────────────────────────

async def _fetch_cpi() -> Optional[dict]:
    try:
        async with httpx.AsyncClient(timeout=20, verify=False) as c:
            r = await c.post(
                "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                json={"seriesid": ["CUSR0000SA0"], "startyear": "2025", "endyear": "2026"},
                headers={"Content-Type": "application/json"},
            )
        series = r.json().get("Results", {}).get("series", [{}])[0].get("data", [])
        if len(series) < 13:
            return None
        latest   = series[0]
        prev_mon = series[1]
        year_ago = next((d for d in series
                         if d["year"] == str(int(latest["year"]) - 1)
                         and d["period"] == latest["period"]), None)
        if not year_ago:
            return None
        val = float(latest["value"])
        return {
            "period": f"{latest['year']}-{latest['period'].replace('M','')}",
            "value":  round(val, 3),
            "yoy":    round((val / float(year_ago["value"]) - 1) * 100, 2),
            "mom":    round((val / float(prev_mon["value"]) - 1) * 100, 2),
        }
    except Exception as e:
        logger.warning("[signal/CPI] fetch failed: {}", e)
        return None


async def check_cpi() -> None:
    global _last_cpi_period
    now = _now()
    if not (20 <= now.hour < 22):
        return
    data = await _fetch_cpi()
    if not data or data["period"] == _last_cpi_period:
        return
    if signal_triggered_today("cpi"):
        _last_cpi_period = data["period"]
        return

    _last_cpi_period = data["period"]
    yoy      = data["yoy"]
    surprise = round(yoy - _CPI_CONSENSUS_YOY, 2)
    scenario = "HAWKISH_SHOCK" if surprise > 0.2 else "DOVISH_RELIEF" if surprise < -0.2 else "IN_LINE"

    sig_id = save_market_signal("cpi", scenario, data)
    logger.info("[signal/CPI] triggered scenario={} yoy={}% id={}", scenario, yoy, sig_id)
    await _dispatch_signal("cpi", scenario, data, sig_id)


# ── Oil ───────────────────────────────────────────────────────────────────────

async def _fetch_brent() -> Optional[float]:
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; HermesBot/1.0)"},
        ) as c:
            r = await c.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1h&range=2d"
            )
        closes = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [v for v in closes if v is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        logger.warning("[signal/Oil] fetch failed: {}", e)
        return None


async def check_oil() -> None:
    global _oil_baseline_24h, _oil_baseline_1h, _oil_1h_at
    price = await _fetch_brent()
    if price is None:
        return

    now = _now()
    if _oil_baseline_24h == 0.0:
        _oil_baseline_24h = price
        _oil_baseline_1h  = price
        _oil_1h_at        = now
        return

    change_1h  = (price - _oil_baseline_1h)  / _oil_baseline_1h  * 100
    change_24h = (price - _oil_baseline_24h) / _oil_baseline_24h * 100

    if (now - _oil_1h_at).total_seconds() >= 3600:
        _oil_baseline_1h = price
        _oil_1h_at       = now

    triggered = False
    change    = 0.0
    if abs(change_1h) >= _OIL_SPIKE_1H_PCT:
        triggered, change = True, change_1h
    elif abs(change_24h) >= _OIL_SPIKE_24H_PCT:
        triggered, change = True, change_24h

    if not triggered or signal_triggered_today("oil"):
        return

    scenario = "OIL_SPIKE" if change > 0 else "OIL_DROP"
    data     = {"price": price, "change_1h": round(change_1h, 2), "change_24h": round(change_24h, 2)}

    sig_id = save_market_signal("oil", scenario, data)
    logger.info("[signal/Oil] triggered scenario={} change={}% id={}", scenario, round(change, 2), sig_id)
    await _dispatch_signal("oil", scenario, data, sig_id)


# ── 北向资金 ──────────────────────────────────────────────────────────────────

_NORTHBOUND_THRESHOLD = float(os.getenv("NORTHBOUND_THRESHOLD", "80"))  # 亿元，净流入/出超过此值才触发


async def _fetch_northbound_flow() -> Optional[float]:
    """返回今日北向资金净流入（亿元），正=流入，负=流出。使用 akshare 东方财富接口。"""
    try:
        import akshare as ak
        df = await asyncio.to_thread(ak.stock_hsgt_hist_em, symbol="北上")
        if df is None or df.empty:
            return None
        today = _now().strftime("%Y-%m-%d")
        row = df.iloc[-1]
        row_date = str(row.get("日期", ""))[:10]
        if row_date != today:
            logger.debug("[signal/Northbound] latest data is {} not today {}", row_date, today)
            return None
        # 列名可能是"当日资金流入"或"当日净买入"
        for col in ("当日资金流入", "当日净买入", "净买入"):
            if col in row.index:
                return float(row[col])
        logger.warning("[signal/Northbound] unknown columns: {}", list(df.columns))
        return None
    except Exception as e:
        logger.warning("[signal/Northbound] fetch failed: {}", e)
        return None


async def check_northbound() -> None:
    now = _now()
    # 只在 A 股收盘后检查（15:30—22:00 CST），避免盘中数据不完整
    h, m = now.hour, now.minute
    if not ((h == 15 and m >= 30) or (16 <= h <= 22)):
        return
    if signal_triggered_today("northbound"):
        return

    net_flow = await _fetch_northbound_flow()
    if net_flow is None:
        return
    if abs(net_flow) < _NORTHBOUND_THRESHOLD:
        logger.debug("[signal/Northbound] net_flow={}亿 below threshold {}亿", round(net_flow, 1), _NORTHBOUND_THRESHOLD)
        return

    scenario = "INFLOW" if net_flow > 0 else "OUTFLOW"
    data = {"net_flow": round(net_flow, 2), "unit": "亿元", "threshold": _NORTHBOUND_THRESHOLD}
    sig_id = save_market_signal("northbound", scenario, data)
    logger.info("[signal/Northbound] triggered scenario={} net_flow={}亿 id={}", scenario, round(net_flow, 2), sig_id)
    await _dispatch_signal("northbound", scenario, data, sig_id)


# ── FOMC ──────────────────────────────────────────────────────────────────────

# 2026 FOMC 决策日（北京时间 = 美东时间宣布日 +1 天，凌晨约 03:00 CST）
_FOMC_CST_DATES = {
    "2026-01-29", "2026-03-19", "2026-04-30",
    "2026-06-18", "2026-07-30", "2026-09-17",
    "2026-10-29", "2026-12-10",
}
_FOMC_RATE_KEY = "fomc:last_rate"  # Redis key 存上次利率


async def _fetch_fed_rate() -> Optional[float]:
    """从 FRED 免费接口获取联邦基金利率上限目标 (DFEDTARU)。"""
    try:
        async with httpx.AsyncClient(timeout=15, verify=False,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU"
            )
        lines = [l for l in r.text.strip().split("\n")
                 if l and not l.startswith("DATE") and "." in l]
        if not lines:
            return None
        last_val = lines[-1].split(",")[1].strip()
        return float(last_val)
    except Exception as e:
        logger.warning("[signal/FOMC] fetch rate failed: {}", e)
        return None


async def check_fomc() -> None:
    now = _now()
    today = now.strftime("%Y-%m-%d")
    if today not in _FOMC_CST_DATES:
        return
    # 凌晨 03:00 后再检查，确保 FRED 数据已更新
    if now.hour < 3:
        return
    if signal_triggered_today("fomc"):
        return

    rate = await _fetch_fed_rate()
    if rate is None:
        return

    last_rate_str = _redis.get(_FOMC_RATE_KEY)
    last_rate = float(last_rate_str) if last_rate_str else None

    if last_rate is None:
        # 首次运行：只初始化基准，不触发推送
        _redis.set(_FOMC_RATE_KEY, str(rate))
        logger.info("[signal/FOMC] initialized last_rate={}%", rate)
        return

    delta = round(rate - last_rate, 2)
    if delta > 0:
        scenario = "HAWKISH_SHOCK"
    elif delta < 0:
        scenario = "DOVISH_RELIEF"
    else:
        scenario = "IN_LINE"  # 按兵不动

    data = {"rate": rate, "prev_rate": last_rate, "change": delta}
    _redis.set(_FOMC_RATE_KEY, str(rate))
    sig_id = save_market_signal("fomc", scenario, data)
    logger.info("[signal/FOMC] triggered scenario={} rate={}->{}% id={}", scenario, last_rate, rate, sig_id)
    await _dispatch_signal("fomc", scenario, data, sig_id)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_monitors() -> None:
    await asyncio.sleep(60)
    logger.info("[signal_monitor] started")
    while True:
        try:
            await check_cpi()
            await check_oil()
            await check_northbound()
            await check_fomc()
        except Exception as e:
            logger.error("[signal_monitor] loop error: {}", e)
        await asyncio.sleep(300)
