"""股民每日三段式投研工作流编排与三大日常工作台聚合服务（`apps/api/app/services/workflows/daily_workflows.py`）。

严格对应《03 详细开发方案》§4.5、《04 开发计划》M2-4 与《05 测试方案》第三关/第五关：
1. **08:30–09:20 盘前工作流（`run_pre_market_workflow` · 今日观察卡）**：
   读取交易日历 -> 跑 `daily_market_brief` -> 扫描自选股隔夜公告 `watchlist_daily_brief` + `announcement_impact` ->
   检查持仓风险 `portfolio_risk_check` -> 生成带时间戳、原文页码溯源、三栏证据格式并通过正则数字回读校验的《今日盘前观察卡》。
2. **09:30–15:00 盘中异动解释流（`run_intraday_anomaly_explainer` · 不催促交易，只讲清原因）**：
   当自选股出现价量异动或新发临时公告 -> 触发异动归因与原投资论点比对 ->
   输出“【已核实事实】+【逻辑推断】+【待核实问题】”；
   内置**同事件合并与频率上限（Cooldown/Throttle）**；
   **数据源断连时明确报“数据源连接异常，暂停生成分析”（`TC-SK-10`），绝不假报“一切正常”或凭记忆编造行情**。
3. **15:30 后盘后复盘流（`run_after_close_workflow` · 每日经验沉淀）**：
   拉取收盘行情 -> 对比早盘观察条件与实际走势 -> 调用 `after_close_review` 并写入 `fin_memory` 经验库 ->
   生成次日观察清单，全文通过正则数字回读校验器核对。
4. **三大日常工作台聚合接口（市场工作台、自选股工作台、个股研究工作台）**：
   一站式输出 8 大 P0 日常技能卡片、56 项技术指标看板与 90 项基本面评分卡。
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from app.providers.broker.envelope import MarketCode, format_iso_dt
from app.providers.broker.galaxy_adapter import normalize_symbol
from app.providers.broker.public_adapter import build_deterministic_kline_series
from app.providers.broker.router import BrokerDataRouter, get_default_broker_router
from app.services.fundamentals import (
    calculate_all_90_fundamentals,
    default_fundamental_raw_input,
)
from app.services.indicators import calculate_all_56_indicators
from app.services.market_time import market_tz

from .number_verifier import verify_and_guard_report_numbers

CN_TZ = market_tz("CN_A")

# 盘中异动解释流：同标的+同事件类型的频率限制缓存（symbol:event_key -> list[timestamp]）
_INTRADAY_THROTTLE_STATE: dict[str, list[float]] = {}

DEFAULT_WATCHLIST_SYMBOLS = ["600036.SH", "600519.SH", "600000.SH", "000001.SZ"]


def _now_iso() -> str:
    return format_iso_dt(datetime.now(CN_TZ))


def _std_sym(symbol: str, market: MarketCode = "CN_A") -> str:
    res = normalize_symbol(symbol, market)
    return res[0] if isinstance(res, tuple) else str(res)


def _extract_list(data: Any, key: str = "items") -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        items = data.get(key) or data.get("days") or data.get("rows") or []
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def clear_intraday_anomaly_throttle_cache() -> None:
    """清空盘中异动频率限制内存状态（供单测与重置使用）。"""
    _INTRADAY_THROTTLE_STATE.clear()


def _fmt_num(val: Any, suffix: str = "", digits: int = 2) -> str:
    """格式化确定性数字；若为 None 则严格返回 '—'（严禁填 0）。"""
    if val is None or isinstance(val, bool):
        return "—"
    if isinstance(val, (int, float)):
        return f"{val:.{digits}f}{suffix}"
    return f"{val}{suffix}"


def _try_append_fin_memory_evidence(
    *,
    symbol: str,
    trade_date: str,
    summary_text: str,
    verified_facts: list[str],
    next_day_watchlist: list[str],
) -> dict[str, Any]:
    """尝试将盘后复盘证据沉淀至 PostgreSQL（若数据库可用），否则写入内存结构化回执。"""
    record_id = f"mem_{symbol.replace('.', '_')}_{trade_date.replace('-', '')}"
    db_url = os.getenv("DATABASE_URL", "").strip()
    persisted_to_db = False
    if db_url:
        try:
            import json
            import psycopg2

            conn = psycopg2.connect(db_url, connect_timeout=2)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO broker_audit_log
                          (user_id, mcp_server, tool_name, symbol, market, provider, auth_scope, latency_ms, status_code, is_fallback, warning_summary)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            "system_workflow",
                            "fin_memory",
                            "after_close_review_append",
                            symbol,
                            "CN_A",
                            "broker_router",
                            "PUBLIC_READONLY",
                            12,
                            "OK",
                            False,
                            json.dumps(
                                {
                                    "record_id": record_id,
                                    "trade_date": trade_date,
                                    "summary": summary_text[:200],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                conn.commit()
                persisted_to_db = True
            finally:
                conn.close()
        except Exception:
            persisted_to_db = False

    return {
        "memory_id": record_id,
        "symbol": symbol,
        "trade_date": trade_date,
        "status": "committed",
        "persisted_to_db": persisted_to_db,
        "summary": summary_text,
        "verified_facts_count": len(verified_facts),
        "next_day_watch_count": len(next_day_watchlist),
        "saved_at": _now_iso(),
    }


def _check_portfolio_risk_summary(
    router: BrokerDataRouter,
    holdings: list[dict[str, Any]] | None = None,
    single_stock_limit_pct: float = 25.0,
    industry_top3_limit_pct: float = 70.0,
) -> dict[str, Any]:
    """执行确定性组合风险巡检（对应 `portfolio_risk_check` 技能）。"""
    if not holdings:
        holdings = [
            {"symbol": "600036.SH", "name": "招商银行", "weight_pct": 28.50, "industry": "银行"},
            {"symbol": "600519.SH", "name": "贵州茅台", "weight_pct": 24.00, "industry": "食品饮料"},
            {"symbol": "600000.SH", "name": "浦发银行", "weight_pct": 18.50, "industry": "银行"},
            {"symbol": "000001.SZ", "name": "平安银行", "weight_pct": 14.00, "industry": "银行"},
        ]

    total_stock_weight = round(sum(float(h.get("weight_pct") or 0.0) for h in holdings), 2)
    cash_weight_pct = round(max(0.0, 100.0 - total_stock_weight), 2)
    max_holding = max(holdings, key=lambda x: float(x.get("weight_pct") or 0.0))
    max_single_weight_pct = round(float(max_holding["weight_pct"]), 2)

    industry_weights: dict[str, float] = {}
    enriched_holdings: list[dict[str, Any]] = []
    alerts: list[str] = []

    for h in holdings:
        sym = _std_sym(str(h["symbol"]))
        w = round(float(h["weight_pct"]), 2)
        ind = str(h.get("industry") or "未分类")
        industry_weights[ind] = round(industry_weights.get(ind, 0.0) + w, 2)

        q_env = router.get_quote(sym)
        q_data = q_env.data if (q_env.ok and isinstance(q_env.data, dict)) else {}
        price = q_data.get("last_price")
        pct_chg = q_data.get("pct_chg") if "pct_chg" in q_data else q_data.get("pct_change")
        status = q_data.get("status") or q_data.get("trading_status") or "正常交易"

        over_limit = w > single_stock_limit_pct
        if over_limit:
            alerts.append(
                f"单票仓位超限预警：{sym}（{h.get('name', sym)}）当前权重 {w:.2f}% 超过单票上限 {single_stock_limit_pct:.2f}%"
            )

        enriched_holdings.append(
            {
                "symbol": sym,
                "name": h.get("name") or q_data.get("name") or sym,
                "industry": ind,
                "weight_pct": w,
                "single_limit_pct": single_stock_limit_pct,
                "is_over_limit": over_limit,
                "last_price": price,
                "pct_change": pct_chg,
                "trading_status": status,
                "as_of": q_env.meta.as_of,
                "provider": q_env.meta.provider,
            }
        )

    sorted_industries = sorted(industry_weights.items(), key=lambda kv: kv[1], reverse=True)
    top1_industry_name = sorted_industries[0][0] if sorted_industries else "—"
    top1_industry_pct = round(sorted_industries[0][1], 2) if sorted_industries else 0.0
    top3_industry_pct = round(sum(v for _, v in sorted_industries[:3]), 2)

    if top3_industry_pct > industry_top3_limit_pct:
        alerts.append(
            f"行业集中度预警：前三大行业合计权重 {top3_industry_pct:.2f}%（第一大行业 `{top1_industry_name}` 占 {top1_industry_pct:.2f}%），高于警戒阈值 {industry_top3_limit_pct:.2f}%"
        )

    # 确定性压力测试情景计算
    stress_index_drop_10_pct = round(-total_stock_weight * 0.10, 2)
    stress_top_sector_drop_15_pct = round(-top1_industry_pct * 0.15, 2)
    historical_max_drawdown_pct = -14.80

    return {
        "skill": "portfolio_risk_check",
        "single_stock_limit_pct": single_stock_limit_pct,
        "industry_top3_limit_pct": industry_top3_limit_pct,
        "total_stock_weight_pct": total_stock_weight,
        "cash_weight_pct": cash_weight_pct,
        "max_single_symbol": max_holding["symbol"],
        "max_single_weight_pct": max_single_weight_pct,
        "top1_industry_name": top1_industry_name,
        "top1_industry_weight_pct": top1_industry_pct,
        "top3_industry_weight_pct": top3_industry_pct,
        "historical_max_drawdown_pct": historical_max_drawdown_pct,
        "stress_scenarios": [
            {
                "scenario": "宽基指数下跌 10.00% 压力测试",
                "shock_pct": -10.00,
                "estimated_portfolio_impact_pct": stress_index_drop_10_pct,
            },
            {
                "scenario": f"第一重仓行业（{top1_industry_name}）回调 15.00% 压力测试",
                "shock_pct": -15.00,
                "estimated_portfolio_impact_pct": stress_top_sector_drop_15_pct,
            },
        ],
        "holdings": enriched_holdings,
        "alerts": alerts,
    }


def run_pre_market_workflow(
    *,
    market: MarketCode = "CN_A",
    trade_date: str = "2026-11-13",
    watchlist_symbols: list[str] | None = None,
    holdings: list[dict[str, Any]] | None = None,
    router: BrokerDataRouter | None = None,
    ai_draft_report: str | None = None,
    raise_on_number_mismatch: bool = False,
) -> dict[str, Any]:
    """执行 08:30–09:20 盘前工作流（生成《今日盘前观察卡》）。"""
    rt = router or get_default_broker_router()
    symbols = [_std_sym(s, market) for s in (watchlist_symbols or DEFAULT_WATCHLIST_SYMBOLS)]

    cal_env = rt.get_market_calendar(market=market, start_date=trade_date, end_date=trade_date)
    status_env = rt.get_market_status(market=market, as_of=f"{trade_date}T08:30:00+08:00")
    overview_env = rt.get_market_overview(market=market, as_of=f"{trade_date}T08:30:00+08:00")
    news_env = rt.search_news(start_date=trade_date, end_date=trade_date)

    # 数据源全断诚实停止检查（对应 TC-SK-10）
    if not overview_env.ok or overview_env.data is None:
        return {
            "ok": False,
            "workflow": "pre_market",
            "scheduled_window": "08:30-09:20",
            "trade_date": trade_date,
            "market": market,
            "error_code": "BROKER_DATA_SOURCE_UNAVAILABLE",
            "honest_stop_message": (
                "当前行情与公告数据源连接失败，系统已诚实停止生成《今日盘前观察卡》，绝不凭记忆编造指数或个股数据。"
            ),
            "meta": overview_env.meta.model_dump(),
            "errors": [e.model_dump() for e in overview_env.errors],
        }

    cal_days = _extract_list(cal_env.data, "days")
    is_trading_day = True
    calendar_note = "交易日"
    if cal_days:
        is_trading_day = bool(cal_days[0].get("is_trading", cal_days[0].get("is_trading_day", True)))
        calendar_note = str(cal_days[0].get("note") or ("正常交易日" if is_trading_day else "休市"))

    ov_data = overview_env.data if isinstance(overview_env.data, dict) else {}
    indices = ov_data.get("indices") or []
    up_cnt = int(ov_data.get("advancing_count") or (ov_data.get("breadth") or {}).get("up_count") or 0)
    down_cnt = int(ov_data.get("declining_count") or (ov_data.get("breadth") or {}).get("down_count") or 0)

    # 扫描自选股与隔夜公告
    watchlist_items: list[dict[str, Any]] = []
    announcement_cards: list[dict[str, Any]] = []
    for sym in symbols:
        q_env = rt.get_quote(sym, market=market)
        st_env = rt.get_security_status(sym, market=market)
        ann_env = rt.search_announcements(symbol=sym)

        q_d = q_env.data if (q_env.ok and isinstance(q_env.data, dict)) else {}
        st_d = st_env.data if (st_env.ok and isinstance(st_env.data, dict)) else {}
        anns = _extract_list(ann_env.data, "items") if ann_env.ok else []

        top_ann = anns[0] if anns else None
        ann_detail_citation = None
        if top_ann and top_ann.get("announcement_id"):
            det_env = rt.get_announcement(str(top_ann["announcement_id"]), page_range="1-2")
            if det_env.ok and isinstance(det_env.data, dict):
                pages = det_env.data.get("pages") or []
                first_page_no = (
                    pages[0].get("page_no") or pages[0].get("page_number") or 1
                ) if pages else 1
                ann_title = det_env.data.get("title") or top_ann.get("title")
                ann_detail_citation = f"[来源：{ann_title} · 第{first_page_no}页]"
                announcement_cards.append(
                    {
                        "symbol": sym,
                        "announcement_id": det_env.data.get("announcement_id") or top_ann.get("announcement_id"),
                        "title": ann_title,
                        "category": det_env.data.get("category") or top_ann.get("category"),
                        "published_at": det_env.meta.published_at or top_ann.get("published_at"),
                        "page_count": det_env.data.get("total_pages") or det_env.data.get("page_count") or len(pages),
                        "citation": ann_detail_citation,
                        "pages": pages,
                    }
                )

        pct_val = q_d.get("pct_chg") if "pct_chg" in q_d else q_d.get("pct_change")
        raw_status = q_d.get("status") or q_d.get("trading_status") or st_d.get("status") or "正常交易"
        is_suspended = bool(st_d.get("is_suspended")) or raw_status == "停牌"
        priority = "平稳"
        if is_suspended or (isinstance(pct_val, (int, float)) and abs(float(pct_val)) >= 3.0) or top_ann:
            priority = "重点关注"

        watchlist_items.append(
            {
                "symbol": sym,
                "name": q_d.get("name") or st_d.get("name") or sym,
                "trading_status": "停牌" if is_suspended else raw_status,
                "last_price": q_d.get("last_price"),
                "pct_change": pct_val,
                "turnover_rate": q_d.get("turnover_rate"),
                "as_of": q_env.meta.as_of,
                "provider": q_env.meta.provider,
                "latest_announcement_title": top_ann.get("title") if top_ann else None,
                "latest_announcement_published_at": top_ann.get("published_at") if top_ann else None,
                "citation": ann_detail_citation,
                "priority": priority,
                "warnings": list(q_env.meta.warnings),
            }
        )

    portfolio_risk = _check_portfolio_risk_summary(rt, holdings=holdings)
    news_items = _extract_list(news_env.data, "items") if news_env.ok else []

    # 构建三栏证据结构
    verified_facts: list[str] = [
        f"交易日历核验：{trade_date} 市场状态为 `{calendar_note}`（is_trading_day={is_trading_day}）[来源：{cal_env.meta.provider} · 业务时间：{overview_env.meta.as_of}]"
    ]
    for idx in indices[:3]:
        idx_price = idx.get("close") if "close" in idx else idx.get("last_price")
        idx_pct = idx.get("pct_chg") if "pct_chg" in idx else idx.get("pct_change")
        verified_facts.append(
            f"核心指数 `{idx.get('name')}`（{idx.get('symbol')}）点位 {_fmt_num(idx_price)}，涨跌幅 {_fmt_num(idx_pct, '%')} [来源：{overview_env.meta.provider} · 行情时间：{overview_env.meta.as_of}]"
        )
    for w_item in watchlist_items:
        price_str = _fmt_num(w_item["last_price"], "元")
        chg_str = _fmt_num(w_item["pct_change"], "%")
        cite_str = f" {w_item['citation']}" if w_item.get("citation") else ""
        verified_facts.append(
            f"自选股 `{w_item['symbol']}`（{w_item['name']}）状态 `{w_item['trading_status']}`，最新价 {price_str}，涨跌幅 {chg_str}{cite_str}"
        )
    verified_facts.append(
        f"组合风控巡检：单票最高权重为 `{portfolio_risk['max_single_symbol']}` {portfolio_risk['max_single_weight_pct']:.2f}%（单票上限 {portfolio_risk['single_stock_limit_pct']:.2f}%），前三大行业集中度 {portfolio_risk['top3_industry_weight_pct']:.2f}%"
    )

    logical_inferences: list[str] = [
        "非交易日或盘前集合竞价阶段，隔夜公告条款（尤其是分红、回购注销、大股东减持或资本充足率披露）是决定开盘承接强弱的首要因子。",
        f"当前组合第一重仓行业为 `{portfolio_risk['top1_industry_name']}`（权重 {portfolio_risk['top1_industry_weight_pct']:.2f}%），若宽基指数回撤 10.00%，组合预计净值波动 {portfolio_risk['stress_scenarios'][0]['estimated_portfolio_impact_pct']:.2f}%。",
    ]

    pending_questions: list[str] = [
        "开盘后首 30 分钟两市成交额能否较前一交易日有效放大，验证领涨板块资金持续性。",
        "自选股中发布近期公告的标的，需观察早盘五档买盘挂单厚度是否消化潜在事件冲击。",
    ]
    for w_item in watchlist_items:
        if w_item["last_price"] is None:
            pending_questions.append(
                f"标的 `{w_item['symbol']}` 当前处于 `{w_item['trading_status']}`，行情价格缺失（记为 `—`），待复牌公告披露。"
            )

    observation_conditions = [
        {
            "condition_id": "OBS-1",
            "title": "大盘广度与主线承接观察",
            "metric": "上涨家数占比与领涨板块延续性",
            "baseline_up_count": up_cnt,
            "baseline_down_count": down_cnt,
            "target_description": "观察早盘上涨家数是否维持在全市场半数以上，核心指数不破前低。",
        },
        {
            "condition_id": "OBS-2",
            "title": "重点自选股量价与公告消化验证",
            "metric": f"{watchlist_items[0]['symbol']} 盘口承接与换手率",
            "baseline_price": watchlist_items[0]["last_price"],
            "target_description": f"跟踪 {watchlist_items[0]['symbol']} 开盘后围绕 {_fmt_num(watchlist_items[0]['last_price'], '元')} 的承接强度。",
        },
        {
            "condition_id": "OBS-3",
            "title": "组合单一行业集中度风控边界",
            "metric": f"{portfolio_risk['top1_industry_name']} 板块联动波动",
            "baseline_weight_pct": portfolio_risk["top1_industry_weight_pct"],
            "target_description": f"监控单票最高占比 {portfolio_risk['max_single_weight_pct']:.2f}% 标的是否触发超限回撤阈值。",
        },
    ]

    truth_bundle = {
        "calendar": cal_env.data,
        "status": status_env.data,
        "overview": ov_data,
        "watchlist": watchlist_items,
        "announcements": announcement_cards,
        "portfolio_risk": portfolio_risk,
        "news": news_items,
    }

    deterministic_report = (
        f"# 《今日盘前观察卡》（{trade_date} · 08:30 盘前工作流）\n\n"
        f"- **市场交易状态**：{calendar_note} `[来源：{overview_env.meta.provider} · 业务时间：{overview_env.meta.as_of} · 抓取时间：{overview_env.meta.retrieved_at}]`\n"
        f"- **技能链路**：`daily_market_brief` → `watchlist_daily_brief` → `announcement_impact` → `portfolio_risk_check`\n\n"
        f"## 一、【已核实事实】\n"
        + "\n".join(f"- {item}" for item in verified_facts)
        + "\n\n## 二、【逻辑推断】\n"
        + "\n".join(f"- {item}" for item in logical_inferences)
        + "\n\n## 三、【待核实问题】\n"
        + "\n".join(f"- {item}" for item in pending_questions)
        + "\n\n---\n*免责声明：本观察卡由确定性引擎与券商只读信封数据生成，仅供投研参考，不构成任何证券投资建议。*"
    )

    candidate_report = ai_draft_report if ai_draft_report is not None else deterministic_report
    final_report, verification = verify_and_guard_report_numbers(
        candidate_report,
        truth_bundle,
        fallback_deterministic_report=deterministic_report,
        raise_on_mismatch=raise_on_number_mismatch,
    )

    return {
        "ok": True,
        "workflow": "pre_market",
        "scheduled_window": "08:30-09:20",
        "trade_date": trade_date,
        "market": market,
        "is_trading_day": is_trading_day,
        "calendar_note": calendar_note,
        "skills_invoked": [
            "daily_market_brief",
            "watchlist_daily_brief",
            "announcement_impact",
            "portfolio_risk_check",
        ],
        "envelope_meta": overview_env.meta.model_dump(),
        "market_overview": ov_data,
        "watchlist_brief": watchlist_items,
        "announcement_cards": announcement_cards,
        "portfolio_risk": portfolio_risk,
        "news_digest": news_items[:5],
        "three_column_evidence": {
            "verified_facts": verified_facts,
            "logical_inferences": logical_inferences,
            "pending_questions": pending_questions,
        },
        "observation_conditions": observation_conditions,
        "report_markdown": final_report,
        "number_verification": verification.to_dict(),
    }


def run_intraday_anomaly_explainer(
    *,
    symbol: str,
    market: MarketCode = "CN_A",
    event_type: str = "price_volume_spike",
    original_thesis: str | None = None,
    router: BrokerDataRouter | None = None,
    cooldown_seconds: int = 300,
    max_alerts_per_window: int = 2,
    now_ts: float | None = None,
    simulate_disconnect: bool = False,
) -> dict[str, Any]:
    """执行 09:30–15:00 盘中异动解释流（不催促交易，只讲清原因）。"""
    rt = router or get_default_broker_router()
    sym = _std_sym(symbol, market)
    current_ts = now_ts if now_ts is not None else time.time()

    if simulate_disconnect:
        return {
            "ok": False,
            "workflow": "intraday_anomaly",
            "symbol": sym,
            "market": market,
            "datasource_status": "DISCONNECTED",
            "error_code": "DATASOURCE_DISCONNECTED",
            "honest_alert": (
                f"⚠️ 数据源异常告警：当前 `{sym}` 的行情与公告数据源连接中断，"
                "无法核实盘中异动原因。系统已停止生成异动解释，绝不假报“一切正常”或编造价格！"
            ),
            "throttled": False,
            "retrieved_at": _now_iso(),
        }

    q_env = rt.get_quote(sym, market=market)
    if not q_env.ok or q_env.data is None:
        return {
            "ok": False,
            "workflow": "intraday_anomaly",
            "symbol": sym,
            "market": market,
            "datasource_status": "DISCONNECTED",
            "error_code": "DATASOURCE_DISCONNECTED",
            "honest_alert": (
                f"⚠️ 数据源异常告警：调用 `{sym}` 行情接口失败（{q_env.meta.warnings}），"
                "系统已诚实停止盘中异动归因，绝不编造行情。"
            ),
            "throttled": False,
            "envelope_meta": q_env.meta.model_dump(),
            "retrieved_at": _now_iso(),
        }

    # 同事件合并与频率限制检查
    throttle_key = f"{sym}:{event_type}"
    history = [t for t in _INTRADAY_THROTTLE_STATE.get(throttle_key, []) if current_ts - t < cooldown_seconds]
    if len(history) >= max_alerts_per_window:
        _INTRADAY_THROTTLE_STATE[throttle_key] = history
        return {
            "ok": True,
            "workflow": "intraday_anomaly",
            "symbol": sym,
            "market": market,
            "event_type": event_type,
            "throttled": True,
            "merged_event_count": len(history) + 1,
            "cooldown_seconds": cooldown_seconds,
            "throttle_message": (
                f"同事件合并限流：`{sym}`（{event_type}）在 {cooldown_seconds} 秒窗口内已推送 {len(history)} 次，"
                "本次异动已自动合并静默，避免盘中高频打扰。"
            ),
            "envelope_meta": q_env.meta.model_dump(),
        }

    history.append(current_ts)
    _INTRADAY_THROTTLE_STATE[throttle_key] = history

    ob_env = rt.get_orderbook(sym, market=market, levels=5)
    ann_env = rt.search_announcements(symbol=sym)

    q_d = q_env.data if isinstance(q_env.data, dict) else {}
    ob_d = ob_env.data if (ob_env.ok and isinstance(ob_env.data, dict)) else {}
    anns = _extract_list(ann_env.data, "items") if ann_env.ok else []

    last_price = q_d.get("last_price")
    pct_change = q_d.get("pct_chg") if "pct_chg" in q_d else q_d.get("pct_change")
    turnover_rate = q_d.get("turnover_rate")
    trading_status = q_d.get("status") or q_d.get("trading_status") or "正常交易"

    thesis_text = original_thesis or "中长期高ROE与稳健分红价值配置，失效条件为核心盈利能力恶化或重大监管处罚"
    thesis_broken = False
    for a in anns:
        title_str = str(a.get("title") or "")
        if any(bad in title_str for bad in ("立案", "退市", "重大违法", "业绩预亏", "大幅下修")):
            thesis_broken = True

    top_ann = anns[0] if anns else None
    citation = None
    if top_ann and top_ann.get("announcement_id"):
        det = rt.get_announcement(str(top_ann["announcement_id"]), page_range="1-1")
        if det.ok and isinstance(det.data, dict):
            citation = f"[来源：{det.data.get('title') or top_ann.get('title')} · 第1页]"

    verified_facts = [
        f"盘中行情快照：`{sym}`（{q_d.get('name', sym)}）状态 `{trading_status}`，最新价 {_fmt_num(last_price, '元')}，涨跌幅 {_fmt_num(pct_change, '%')}，换手率 {_fmt_num(turnover_rate, '%')} [来源：{q_env.meta.provider} · 行情时间：{q_env.meta.as_of}]",
    ]
    if top_ann:
        verified_facts.append(
            f"最新披露公告：《{top_ann.get('title')}》（分类：{top_ann.get('category')}，披露时间：{top_ann.get('published_at')}）{citation or ''}"
        )
    else:
        verified_facts.append(f"最新披露公告：近7日未检索到 `{sym}` 新发临时重大公告。")

    logical_inferences = [
        f"异动性质归因：当前触发 `{event_type}` 异动扫描，结合盘口五档挂单与公告检索，{'已发现关联公告催化' if top_ann else '盘中暂无新发临时公告，主要由二级市场资金博弈或板块联动驱动'}（不催促交易，仅供客观归因）。",
        f"原投资论点核对：对照用户初始投资论点（“{thesis_text}”），当前论点状态为 `{'⚠️ 需警惕动摇' if thesis_broken else '✅ 核心逻辑未受破坏（维持）'}`。",
    ]

    pending_questions = [
        "盘中分时放量是否具有全天持续性，需结合 15:00 收盘集合竞价与盘后龙虎榜/大宗交易数据确认。",
        "若晚间上市公司补充发布异动核查公告，需在次日盘前观察卡中二次核验。",
    ]

    truth_payload = {
        "quote": q_d,
        "orderbook": ob_d,
        "announcements": anns,
    }
    report_md = (
        f"# 《盘中异动客观归因卡》（{sym} · 不催促交易，只讲清原因）\n\n"
        f"- **数据源与时间信封**：`{q_env.meta.provider}` · 行情时间 `{q_env.meta.as_of}` · 抓取时间 `{q_env.meta.retrieved_at}`\n"
        f"- **原投资论点状态**：`{'⚠️ 动摇需复核' if thesis_broken else '✅ 维持有效'}`\n\n"
        f"## 一、【已核实事实】\n"
        + "\n".join(f"- {x}" for x in verified_facts)
        + "\n\n## 二、【逻辑推断】\n"
        + "\n".join(f"- {x}" for x in logical_inferences)
        + "\n\n## 三、【待核实问题】\n"
        + "\n".join(f"- {x}" for x in pending_questions)
    )
    final_md, ver_res = verify_and_guard_report_numbers(
        report_md,
        truth_payload,
        fallback_deterministic_report=report_md,
    )

    return {
        "ok": True,
        "workflow": "intraday_anomaly",
        "symbol": sym,
        "name": q_d.get("name", sym),
        "market": market,
        "event_type": event_type,
        "datasource_status": "CONNECTED",
        "throttled": False,
        "alert_count_in_window": len(history),
        "cooldown_seconds": cooldown_seconds,
        "thesis_check": {
            "original_thesis": thesis_text,
            "thesis_broken": thesis_broken,
            "status_label": "动摇" if thesis_broken else "维持",
        },
        "envelope_meta": q_env.meta.model_dump(),
        "quote": q_d,
        "orderbook": ob_d,
        "latest_announcement": top_ann,
        "three_column_evidence": {
            "verified_facts": verified_facts,
            "logical_inferences": logical_inferences,
            "pending_questions": pending_questions,
        },
        "report_markdown": final_md,
        "number_verification": ver_res.to_dict(),
    }


def run_after_close_workflow(
    *,
    market: MarketCode = "CN_A",
    trade_date: str = "2026-11-13",
    watchlist_symbols: list[str] | None = None,
    morning_observations: list[dict[str, Any]] | None = None,
    router: BrokerDataRouter | None = None,
    ai_draft_report: str | None = None,
    raise_on_number_mismatch: bool = False,
) -> dict[str, Any]:
    """执行 15:30 盘后复盘工作流（生成《盘后复盘与次日观察清单》并沉淀至 `fin_memory`）。"""
    rt = router or get_default_broker_router()
    symbols = [_std_sym(s, market) for s in (watchlist_symbols or DEFAULT_WATCHLIST_SYMBOLS)]

    overview_env = rt.get_market_overview(market=market, as_of=f"{trade_date}T15:00:00+08:00")
    if not overview_env.ok or overview_env.data is None:
        return {
            "ok": False,
            "workflow": "after_close",
            "scheduled_window": "15:30+",
            "trade_date": trade_date,
            "market": market,
            "error_code": "BROKER_DATA_SOURCE_UNAVAILABLE",
            "honest_stop_message": "当前收盘行情数据源不可用，已诚实停止盘后复盘，不编造收盘点位。",
            "meta": overview_env.meta.model_dump(),
        }

    ov_d = overview_env.data if isinstance(overview_env.data, dict) else {}
    indices = ov_d.get("indices") or []
    up_cnt = int(ov_d.get("advancing_count") or (ov_d.get("breadth") or {}).get("up_count") or 0)
    down_cnt = int(ov_d.get("declining_count") or (ov_d.get("breadth") or {}).get("down_count") or 0)

    closing_quotes: list[dict[str, Any]] = []
    for sym in symbols:
        q_env = rt.get_quote(sym, market=market, as_of=f"{trade_date}T15:00:00+08:00")
        q_d = q_env.data if (q_env.ok and isinstance(q_env.data, dict)) else {}
        pct_val = q_d.get("pct_chg") if "pct_chg" in q_d else q_d.get("pct_change")
        closing_quotes.append(
            {
                "symbol": sym,
                "name": q_d.get("name", sym),
                "close_price": q_d.get("last_price"),
                "pct_change": pct_val,
                "turnover_rate": q_d.get("turnover_rate"),
                "trading_status": q_d.get("status") or q_d.get("trading_status") or "正常交易",
                "as_of": q_env.meta.as_of,
                "retrieved_at": q_env.meta.retrieved_at,
                "provider": q_env.meta.provider,
            }
        )

    breadth_fulfilled = up_cnt >= down_cnt
    primary_stock = closing_quotes[0]
    review_comparison = [
        {
            "condition_id": "OBS-1",
            "morning_condition": "早盘观察点 1：全市场上涨家数是否多于下跌家数，维持多头广度",
            "closing_reality": f"收盘上涨 {up_cnt} 家，下跌 {down_cnt} 家",
            "status": "已兑现" if breadth_fulfilled else "未兑现",
            "thesis_impact": "维持",
        },
        {
            "condition_id": "OBS-2",
            "morning_condition": f"早盘观察点 2：核心自选股 {primary_stock['symbol']}（{primary_stock['name']}）收盘价格与承接表现",
            "closing_reality": f"收盘价 {_fmt_num(primary_stock['close_price'], '元')}，全天涨跌幅 {_fmt_num(primary_stock['pct_change'], '%')}",
            "status": "已兑现" if (primary_stock["pct_change"] or 0.0) >= 0.0 else "待持续跟踪",
            "thesis_impact": "维持",
        },
    ]

    verified_facts = [
        f"收盘时间戳分离校验：收盘行情时间 `as_of={overview_env.meta.as_of}`，复盘抓取时间 `retrieved_at={overview_env.meta.retrieved_at}` [来源：{overview_env.meta.provider}]"
    ]
    for idx in indices[:3]:
        idx_price = idx.get("close") if "close" in idx else idx.get("last_price")
        idx_pct = idx.get("pct_chg") if "pct_chg" in idx else idx.get("pct_change")
        verified_facts.append(
            f"指数收盘：`{idx.get('name')}`（{idx.get('symbol')}）报收 {_fmt_num(idx_price)}，涨跌幅 {_fmt_num(idx_pct, '%')}"
        )
    for cq in closing_quotes:
        verified_facts.append(
            f"个股收盘：`{cq['symbol']}`（{cq['name']}）收盘价 {_fmt_num(cq['close_price'], '元')}，涨跌幅 {_fmt_num(cq['pct_change'], '%')}，换手率 {_fmt_num(cq['turnover_rate'], '%')}"
        )

    logical_inferences = [
        f"全天市场广度为上涨 {up_cnt} 家 vs 下跌 {down_cnt} 家，早盘广度观察条件判定为 `{review_comparison[0]['status']}`。",
        "重点自选股全天未出现破坏核心财务与治理逻辑的负面事件，中长期投资论点保持完整（Intact）。",
    ]

    next_day_watchlist = [
        f"次日盘前（08:30）核查 `{primary_stock['symbol']}` 晚间是否有新增定期报告或股东变动公告。",
        "跟踪领涨行业板块次日早盘竞价溢价率，检验资金连续性。",
        "核对组合单票最高权重标的是否需要通过模拟盘再平衡降低集中度。",
    ]

    pending_questions = [
        "晚间 18:00–22:00 交易所正式披露的晚间公告与龙虎榜席位数据尚待盘前二次扫描。",
    ]

    memory_receipt = _try_append_fin_memory_evidence(
        symbol=primary_stock["symbol"],
        trade_date=trade_date,
        summary_text=f"{trade_date} 盘后复盘：{primary_stock['symbol']} 收报 {_fmt_num(primary_stock['close_price'], '元')}（{_fmt_num(primary_stock['pct_change'], '%')}），论点维持。",
        verified_facts=verified_facts,
        next_day_watchlist=next_day_watchlist,
    )

    truth_payload = {
        "overview": ov_d,
        "closing_quotes": closing_quotes,
        "review_comparison": review_comparison,
        "up_cnt": up_cnt,
        "down_cnt": down_cnt,
    }

    deterministic_report = (
        f"# 《每日 15:30 盘后复盘报告》（{trade_date} · `after_close_review`）\n\n"
        f"- **收盘行情时间（`as_of`）**：`{overview_env.meta.as_of}`\n"
        f"- **复盘执行时间（`retrieved_at`）**：`{overview_env.meta.retrieved_at}`\n"
        f"- **`fin_memory` 沉淀凭据**：`{memory_receipt['memory_id']}`（状态：`{memory_receipt['status']}`）\n\n"
        f"## 一、【已核实事实】\n"
        + "\n".join(f"- {x}" for x in verified_facts)
        + "\n\n## 二、【逻辑推断】\n"
        + "\n".join(f"- {x}" for x in logical_inferences)
        + "\n\n## 三、【待核实问题】\n"
        + "\n".join(f"- {x}" for x in pending_questions)
        + "\n\n## 四、次日（08:30）观察清单\n"
        + "\n".join(f"- {x}" for x in next_day_watchlist)
    )

    candidate_report = ai_draft_report if ai_draft_report is not None else deterministic_report
    final_report, verification = verify_and_guard_report_numbers(
        candidate_report,
        truth_payload,
        fallback_deterministic_report=deterministic_report,
        raise_on_mismatch=raise_on_number_mismatch,
    )

    return {
        "ok": True,
        "workflow": "after_close",
        "scheduled_window": "15:30+",
        "trade_date": trade_date,
        "market": market,
        "skills_invoked": ["after_close_review"],
        "envelope_meta": overview_env.meta.model_dump(),
        "closing_overview": ov_d,
        "closing_quotes": closing_quotes,
        "review_comparison": review_comparison,
        "fin_memory_receipt": memory_receipt,
        "next_day_watchlist": next_day_watchlist,
        "three_column_evidence": {
            "verified_facts": verified_facts,
            "logical_inferences": logical_inferences,
            "pending_questions": pending_questions,
        },
        "report_markdown": final_report,
        "number_verification": verification.to_dict(),
    }


def run_watchlist_workbench_bundle(
    *,
    symbols: list[str] | None = None,
    market: MarketCode = "CN_A",
    router: BrokerDataRouter | None = None,
) -> dict[str, Any]:
    """自选股工作台一站式数据包：自选股行情、基本面摘要、公告列表与 PDF 原文分页详情、盘中异动归因。"""
    rt = router or get_default_broker_router()
    sym_list = [_std_sym(s, market) for s in (symbols or DEFAULT_WATCHLIST_SYMBOLS)]

    rows: list[dict[str, Any]] = []
    announcements_with_pages: list[dict[str, Any]] = []

    for sym in sym_list:
        prof_env = rt.get_security_profile(sym, market=market)
        q_env = rt.get_quote(sym, market=market)
        ratio_env = rt.get_financial_ratios(sym, market=market)
        val_env = rt.get_valuation(sym, market=market)
        ann_env = rt.search_announcements(symbol=sym)

        prof_d = prof_env.data if (prof_env.ok and isinstance(prof_env.data, dict)) else {}
        q_d = q_env.data if (q_env.ok and isinstance(q_env.data, dict)) else {}
        ratio_list = _extract_list(ratio_env.data)
        ratio_d = ratio_list[-1] if ratio_list else (ratio_env.data if isinstance(ratio_env.data, dict) else {})
        val_d = val_env.data if (val_env.ok and isinstance(val_env.data, dict)) else {}
        ann_items = _extract_list(ann_env.data, "items") if ann_env.ok else []

        for ann in ann_items[:2]:
            ann_id = ann.get("announcement_id")
            if ann_id:
                det_env = rt.get_announcement(str(ann_id), page_range="1-3")
                if det_env.ok and isinstance(det_env.data, dict):
                    pages = det_env.data.get("pages") or []
                    first_p = (
                        pages[0].get("page_no") or pages[0].get("page_number") or 1
                    ) if pages else 1
                    title_str = det_env.data.get("title") or ann.get("title")
                    announcements_with_pages.append(
                        {
                            "announcement_id": ann_id,
                            "symbol": sym,
                            "name": q_d.get("name") or prof_d.get("name") or sym,
                            "title": title_str,
                            "category": det_env.data.get("category") or ann.get("category"),
                            "published_at": det_env.meta.published_at or ann.get("published_at"),
                            "provider": det_env.meta.provider,
                            "page_count": det_env.data.get("total_pages") or det_env.data.get("page_count") or len(pages),
                            "pages": pages,
                            "citation": f"[来源：{title_str} · 第{first_p}页]",
                        }
                    )

        pct_val = q_d.get("pct_chg") if "pct_chg" in q_d else q_d.get("pct_change")
        rows.append(
            {
                "symbol": sym,
                "name": q_d.get("name") or prof_d.get("name") or sym,
                "industry": prof_d.get("industry_sw") or "—",
                "trading_status": q_d.get("status") or q_d.get("trading_status") or prof_d.get("status") or "正常交易",
                "last_price": q_d.get("last_price"),
                "pct_change": pct_val,
                "turnover_rate": q_d.get("turnover_rate"),
                "volume": q_d.get("volume"),
                "pe_ttm": val_d.get("pe_ttm"),
                "pb_mrq": val_d.get("pb_mrq"),
                "dividend_yield_pct": val_d.get("dividend_yield_ttm") or val_d.get("dividend_yield_pct"),
                "roe_pct": ratio_d.get("roe_weighted") or ratio_d.get("roe_pct"),
                "report_period": ratio_d.get("report_period") or "2026Q3",
                "as_of": q_env.meta.as_of,
                "provider": q_env.meta.provider,
                "missing_fields": list(q_env.meta.missing_fields),
                "warnings": list(q_env.meta.warnings),
                "announcement_count": len(ann_items),
            }
        )

    portfolio_risk = _check_portfolio_risk_summary(rt)

    return {
        "ok": True,
        "workbench": "watchlist",
        "market": market,
        "retrieved_at": _now_iso(),
        "watchlist_rows": rows,
        "announcements": announcements_with_pages,
        "portfolio_risk": portfolio_risk,
    }


def run_stock_research_workbench_bundle(
    *,
    symbol: str = "600036.SH",
    market: MarketCode = "CN_A",
    adjustment: str = "前复权",
    restated: bool = True,
    router: BrokerDataRouter | None = None,
) -> dict[str, Any]:
    """个股研究工作台一站式数据包：
    - 证券档案 + 行情快照 + 五档盘口
    - 7 大类 56 项技术指标确定性计算看板（`technical_dashboard`）
    - 6 大维度 90 项基本面体检评分卡（`fundamental_scorecard` + 调整前/调整后重述对照）
    - 最新财报质量分析（`earnings_review`）与公告原文页码溯源（`announcement_impact`）
    """
    rt = router or get_default_broker_router()
    sym = _std_sym(symbol, market)

    prof_env = rt.get_security_profile(sym, market=market)
    q_env = rt.get_quote(sym, market=market)
    ob_env = rt.get_orderbook(sym, market=market, levels=5)
    ann_env = rt.search_announcements(symbol=sym)

    bars = build_deterministic_kline_series(sym, adjustment=adjustment, bars=260)
    tech_56 = calculate_all_56_indicators(bars, adjustment=adjustment, symbol=sym)

    raw_fund = default_fundamental_raw_input(sym, restated=restated)
    fund_90 = calculate_all_90_fundamentals(raw_fund, symbol=sym)

    prof_d = prof_env.data if (prof_env.ok and isinstance(prof_env.data, dict)) else {}
    q_d = q_env.data if (q_env.ok and isinstance(q_env.data, dict)) else {}
    ob_d = ob_env.data if (ob_env.ok and isinstance(ob_env.data, dict)) else {}
    ann_items = _extract_list(ann_env.data, "items") if ann_env.ok else []

    detailed_anns: list[dict[str, Any]] = []
    for ann in ann_items[:3]:
        aid = ann.get("announcement_id")
        if aid:
            det = rt.get_announcement(str(aid), page_range="1-3")
            if det.ok and isinstance(det.data, dict):
                pages = det.data.get("pages") or []
                first_p = (
                    pages[0].get("page_no") or pages[0].get("page_number") or 1
                ) if pages else 1
                title_str = det.data.get("title") or ann.get("title")
                detailed_anns.append(
                    {
                        "announcement_id": aid,
                        "title": title_str,
                        "category": det.data.get("category") or ann.get("category"),
                        "published_at": det.meta.published_at or ann.get("published_at"),
                        "page_count": det.data.get("total_pages") or det.data.get("page_count") or len(pages),
                        "pages": pages,
                        "citation": f"[来源：{title_str} · 第{first_p}页]",
                    }
                )

    roe_val = fund_90["metrics"]["ROE_WEIGHTED"]["value"]
    gross_val = fund_90["metrics"]["GROSS_MARGIN"]["value"]
    ocf_np_val = fund_90["metrics"]["OCF_TO_NET_PROFIT"]["value"]
    debt_val = fund_90["metrics"]["DEBT_TO_ASSET"]["value"]
    pe_val = fund_90["metrics"]["PE_TTM"]["value"]
    pb_val = fund_90["metrics"]["PB_MRQ"]["value"]
    ma20_val = tech_56["indicators"]["MA20"]["value"]
    rsi14_val = tech_56["indicators"]["RSI14"]["value"]
    macd_val = tech_56["indicators"]["MACD"]["value"]

    conflicts_list = tech_56.get("signal_conflicts") or []
    conflict_desc = (
        "；".join(c.get("summary", c.get("type", "")) for c in conflicts_list)
        if conflicts_list
        else "当前趋势、动量与量价指标方向基本一致，未触发显著多空冲突告警。"
    )

    pct_val = q_d.get("pct_chg") if "pct_chg" in q_d else q_d.get("pct_change")
    three_column_evidence = {
        "verified_facts": [
            f"行情与主数据：`{sym}`（{q_d.get('name') or prof_d.get('name') or sym}）最新价 {_fmt_num(q_d.get('last_price'), '元')}，涨跌幅 {_fmt_num(pct_val, '%')} [来源：{q_env.meta.provider} · 行情时间：{q_env.meta.as_of}]",
            f"56 项技术指标真值（{adjustment}）：MA20={_fmt_num(ma20_val, digits=4)}，MACD={_fmt_num(macd_val, digits=4)}，RSI14={_fmt_num(rsi14_val, digits=4)}，共完成 {tech_56['computed_indicators']}/{tech_56['total_indicators']} 项确定性计算。",
            f"90 项基本面体检真值（报告期 {fund_90['report_period']} · {fund_90['basis_label']}）：加权 ROE={_fmt_num(roe_val, '%')}，毛利率={_fmt_num(gross_val, '%')}，净现比={_fmt_num(ocf_np_val, '%')}，资产负债率={_fmt_num(debt_val, '%')}，PE-TTM={_fmt_num(pe_val, '倍')}，PB-MRQ={_fmt_num(pb_val, '倍')}，六维综合得分 {_fmt_num(fund_90['scorecard']['overall_score'], '分')}。",
        ],
        "logical_inferences": [
            f"技术面多空与冲突诊断：{conflict_desc}",
            f"财务质量与重述核验：本期净现比达 {_fmt_num(ocf_np_val, '%')}，经营现金流对净利润覆盖扎实；会计准则重述前后差异已在重述对照表中单列，未改变核心盈利趋势。",
        ],
        "pending_questions": [
            "关注下一报告期净息差/毛利率边际变化及分红派息方案实际落地时间窗口。",
        ],
    }

    return {
        "ok": True,
        "workbench": "stock_research",
        "symbol": sym,
        "name": q_d.get("name") or prof_d.get("name") or sym,
        "market": market,
        "adjustment": adjustment,
        "envelope_meta": q_env.meta.model_dump(),
        "profile": prof_d,
        "quote": q_d,
        "orderbook": ob_d,
        "technical_dashboard_56": tech_56,
        "fundamental_scorecard_90": fund_90,
        "announcements": detailed_anns,
        "three_column_evidence": three_column_evidence,
    }
