"""日常投研工作流与三大工作台 API 路由（`apps/api/app/routers/daily_workflows.py`）。

提供以下 REST 接口供前端「市场工作台」「自选股工作台」「个股研究工作台」与自动化脚本调用：
- `GET /api/workflows/catalog`：列出 15 个金融 SKILL（含 7 升级 + 8 新增 P0）与 3 条日常工作流元数据
- `POST /api/workflows/pre-market`：触发 08:30 盘前工作流（今日盘前观察卡）
- `POST /api/workflows/intraday-anomaly`：触发 09:30–15:00 盘中异动归因（含频率限制与断连诚实告警）
- `POST /api/workflows/after-close`：触发 15:30 盘后复盘与 `fin_memory` 沉淀
- `GET /api/workflows/workbench/market`：市场工作台一站式数据（晨报 + 交易日历 + 指数/行业全景 + 盘后复盘）
- `GET /api/workflows/workbench/watchlist`：自选股工作台一站式数据（自选股排雷 + 公告 PDF 原文页码定位 + 组合风险巡检）
- `GET /api/workflows/workbench/stock`：个股研究工作台一站式数据（56项技术指标看板 + 90项基本面评分卡 + 财报重述比对 + 公告解读）
- `GET /api/workflows/announcement/{announcement_id}`：按页码拉取公告 PDF 原文详情（供右侧 PDF 页码抽屉打开）
- `POST /api/workflows/verify-numbers`：调用数字正则回读校验器（`TC-CALC-04`）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.providers.broker.router import get_default_broker_router
from app.services.workflows import (
    run_after_close_workflow,
    run_intraday_anomaly_explainer,
    run_pre_market_workflow,
    run_stock_research_workbench_bundle,
    run_watchlist_workbench_bundle,
    verify_report_numbers_against_truth,
)

router = APIRouter(prefix="/workflows", tags=["daily_workflows"])


class PreMarketWorkflowRequest(BaseModel):
    market: str = Field(default="CN_A", description="市场代码：CN_A / HK / US")
    trade_date: str = Field(default="2026-11-13", description="交易日期 YYYY-MM-DD")
    watchlist_symbols: list[str] | None = Field(default=None, description="自选股代码列表")
    ai_draft_report: str | None = Field(default=None, description="可选 AI 初稿，用于正则数字回读校验")


class IntradayAnomalyRequest(BaseModel):
    symbol: str = Field(default="600036.SH", description="异动股票代码")
    market: str = Field(default="CN_A", description="市场代码")
    event_type: str = Field(default="price_volume_spike", description="异动事件类型")
    original_thesis: str | None = Field(default=None, description="用户原投资论点")
    cooldown_seconds: int = Field(default=300, description="同事件冷却窗口秒数")
    max_alerts_per_window: int = Field(default=2, description="冷却窗口内最大允许推送次数")
    simulate_disconnect: bool = Field(default=False, description="是否模拟数据源断连（测试诚实停止）")


class AfterCloseWorkflowRequest(BaseModel):
    market: str = Field(default="CN_A", description="市场代码")
    trade_date: str = Field(default="2026-11-13", description="交易日期 YYYY-MM-DD")
    watchlist_symbols: list[str] | None = Field(default=None, description="自选股代码列表")
    ai_draft_report: str | None = Field(default=None, description="可选 AI 初稿，用于正则数字回读校验")


class VerifyNumbersRequest(BaseModel):
    report_text: str = Field(..., description="待校验的 AI 报告正文")
    truth_payload: Any = Field(..., description="底层工具或计算引擎返回的确定性真值 JSON")


P0_DAILY_SKILLS_META = [
    {
        "name": "daily_market_brief",
        "display_name": "每日市场晨报",
        "stage": "盘前 08:30",
        "workbench": "market",
        "description": "读取交易日历、核心指数、领涨领跌行业、宏观与合规新闻，生成《今日盘前观察卡》。",
    },
    {
        "name": "watchlist_daily_brief",
        "display_name": "自选股排雷日报",
        "stage": "盘前/盘中",
        "workbench": "watchlist",
        "description": "逐只扫描用户自选股行情、公告、财报摘要与 ST/停牌风险状态。",
    },
    {
        "name": "announcement_impact",
        "display_name": "公告深度解读",
        "stage": "全天候",
        "workbench": "watchlist",
        "description": "提取减持/回购/业绩预告/合同核心条款，强制标注 [来源：公告标题 · 第X页] 原文页码定位。",
    },
    {
        "name": "earnings_review",
        "display_name": "财报质量分析",
        "stage": "财报季/个股",
        "workbench": "stock",
        "description": "检验营收/归母/扣非同比环比、净现比、应收存货异动与存贷双高排雷项。",
    },
    {
        "name": "technical_dashboard",
        "display_name": "56项技术面看板",
        "stage": "个股研究",
        "workbench": "stock",
        "description": "调用 7 大类 56 项技术指标确定性计算引擎，输出趋势/动量/量价/波动率及多指标冲突诊断。",
    },
    {
        "name": "fundamental_scorecard",
        "display_name": "90项基本面评分卡",
        "stage": "个股研究",
        "workbench": "stock",
        "description": "调用 6 大维度 90 项基本面计算引擎，输出六维体检得分、同行业分位与会计重述对照。",
    },
    {
        "name": "portfolio_risk_check",
        "display_name": "组合风险巡检",
        "stage": "盘前/盘后",
        "workbench": "watchlist",
        "description": "核算单票最高占比、前三大行业集中度、历史最大回撤与宽基/行业压力测试情景。",
    },
    {
        "name": "after_close_review",
        "display_name": "15:30 盘后复盘",
        "stage": "盘后 15:30",
        "workbench": "market",
        "description": "对比早盘观察条件与收盘实际结果，更新投资论点并将复盘证据沉淀至 fin_memory。",
    },
]

UPGRADED_SKILLS_META = [
    {"name": "uzi", "display_name": "UZI 投研总调度（22技能总路由）"},
    {"name": "deep_analysis", "display_name": "UZI 22维深度分析（三栏证据版）"},
    {"name": "investor_panel", "display_name": "66位投资大佬评审团（券商信封版）"},
    {"name": "lhb_analyzer", "display_name": "龙虎榜游资与机构博弈分析"},
    {"name": "risk_profile", "display_name": "用户风险画像与仓位上限约束"},
    {"name": "trap_detector", "display_name": "杀猪盘与庄股8信号检测器"},
    {"name": "market-event-analyst", "display_name": "重大市场事件六步传导链分析师"},
]


@router.get("/catalog")
def get_workflows_and_skills_catalog() -> dict[str, Any]:
    """返回 M2 阶段 8 个 P0 日常核心技能、7 个升级既有技能、3 条日常工作流与三大工作台目录。"""
    return {
        "ok": True,
        "version": "3.2.0",
        "p0_daily_skills_count": len(P0_DAILY_SKILLS_META),
        "upgraded_skills_count": len(UPGRADED_SKILLS_META),
        "p0_daily_skills": P0_DAILY_SKILLS_META,
        "upgraded_skills": UPGRADED_SKILLS_META,
        "daily_workflows": [
            {
                "id": "pre_market",
                "name": "08:30 盘前工作流（今日观察卡）",
                "endpoint": "/api/workflows/pre-market",
                "skills": ["daily_market_brief", "watchlist_daily_brief", "announcement_impact", "portfolio_risk_check"],
            },
            {
                "id": "intraday_anomaly",
                "name": "09:30–15:00 盘中异动解释流",
                "endpoint": "/api/workflows/intraday-anomaly",
                "skills": ["watchlist_daily_brief", "announcement_impact"],
            },
            {
                "id": "after_close",
                "name": "15:30 盘后复盘流（经验沉淀）",
                "endpoint": "/api/workflows/after-close",
                "skills": ["after_close_review"],
            },
        ],
        "workbenches": [
            {"id": "market", "name": "市场工作台", "endpoint": "/api/workflows/workbench/market"},
            {"id": "watchlist", "name": "自选股工作台", "endpoint": "/api/workflows/workbench/watchlist"},
            {"id": "stock", "name": "个股研究工作台", "endpoint": "/api/workflows/workbench/stock"},
        ],
    }


@router.post("/pre-market")
def api_run_pre_market_workflow(req: PreMarketWorkflowRequest) -> dict[str, Any]:
    return run_pre_market_workflow(
        market=req.market,  # type: ignore[arg-type]
        trade_date=req.trade_date,
        watchlist_symbols=req.watchlist_symbols,
        ai_draft_report=req.ai_draft_report,
    )


@router.post("/intraday-anomaly")
def api_run_intraday_anomaly(req: IntradayAnomalyRequest) -> dict[str, Any]:
    return run_intraday_anomaly_explainer(
        symbol=req.symbol,
        market=req.market,  # type: ignore[arg-type]
        event_type=req.event_type,
        original_thesis=req.original_thesis,
        cooldown_seconds=req.cooldown_seconds,
        max_alerts_per_window=req.max_alerts_per_window,
        simulate_disconnect=req.simulate_disconnect,
    )


@router.post("/after-close")
def api_run_after_close_workflow(req: AfterCloseWorkflowRequest) -> dict[str, Any]:
    return run_after_close_workflow(
        market=req.market,  # type: ignore[arg-type]
        trade_date=req.trade_date,
        watchlist_symbols=req.watchlist_symbols,
        ai_draft_report=req.ai_draft_report,
    )


@router.get("/workbench/market")
def api_get_market_workbench(
    market: str = Query(default="CN_A"),
    trade_date: str = Query(default="2026-11-13"),
) -> dict[str, Any]:
    """市场工作台聚合数据：包含 08:30 盘前观察卡与 15:30 盘后复盘卡片。"""
    pre_res = run_pre_market_workflow(market=market, trade_date=trade_date)  # type: ignore[arg-type]
    after_res = run_after_close_workflow(market=market, trade_date=trade_date)  # type: ignore[arg-type]
    return {
        "ok": True,
        "workbench": "market",
        "trade_date": trade_date,
        "market": market,
        "pre_market_card": pre_res,
        "after_close_card": after_res,
    }


@router.get("/workbench/watchlist")
def api_get_watchlist_workbench(
    symbols: str | None = Query(default=None, description="逗号分隔的股票代码列表"),
    market: str = Query(default="CN_A"),
) -> dict[str, Any]:
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return run_watchlist_workbench_bundle(symbols=sym_list, market=market)  # type: ignore[arg-type]


@router.get("/workbench/stock")
def api_get_stock_research_workbench(
    symbol: str = Query(default="600036.SH", description="证券代码"),
    market: str = Query(default="CN_A"),
    adjustment: str = Query(default="前复权", description="复权方式：前复权/后复权/不复权"),
    restated: bool = Query(default=True, description="是否展示会计准则重述后口径及重述对照表"),
) -> dict[str, Any]:
    return run_stock_research_workbench_bundle(
        symbol=symbol,
        market=market,  # type: ignore[arg-type]
        adjustment=adjustment,
        restated=restated,
    )


@router.get("/announcement/{announcement_id}")
def api_get_announcement_pdf_pages(
    announcement_id: str,
    page_range: str = Query(default="1-3", description="页码范围，如 1-3"),
) -> dict[str, Any]:
    rt = get_default_broker_router()
    env = rt.get_announcement(announcement_id, page_range=page_range)
    return {
        "ok": env.ok,
        "announcement_id": announcement_id,
        "page_range": page_range,
        "data": env.data,
        "meta": env.meta.model_dump(),
        "errors": [e.model_dump() for e in env.errors],
    }


@router.post("/verify-numbers")
def api_verify_report_numbers(req: VerifyNumbersRequest) -> dict[str, Any]:
    res = verify_report_numbers_against_truth(req.report_text, req.truth_payload)
    return res.to_dict()
