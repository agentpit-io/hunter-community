"""日常投研工作流与五大工作台 API 路由（`apps/api/app/routers/daily_workflows.py`）。

提供以下 REST 接口供前端「市场工作台」「自选股工作台」「个股研究工作台」「量化研究工作台」「组合复盘工作台」与自动化脚本调用：
- `GET /api/workflows/catalog`：列出 22 个金融 SKILL（7 升级 + 8 P0 + 7 P1）、3 条日常工作流与 5 大工作台元数据
- `POST /api/workflows/pre-market`：触发 08:30 盘前工作流（今日盘前观察卡）
- `POST /api/workflows/intraday-anomaly`：触发 09:30–15:00 盘中异动归因（含频率限制与断连诚实告警）
- `POST /api/workflows/after-close`：触发 15:30 盘后复盘与 `fin_memory` 沉淀
- `GET /api/workflows/workbench/market`：市场工作台一站式数据（晨报 + 交易日历 + 指数/行业全景 + 盘后复盘）
- `GET /api/workflows/workbench/watchlist`：自选股工作台一站式数据（自选股排雷 + 公告 PDF 原文页码定位 + 组合风险巡检）
- `GET /api/workflows/workbench/stock`：个股研究工作台一站式数据（56项技术指标看板 + 90项基本面评分卡 + 财报重述比对 + 公告解读）
- `GET /api/workflows/workbench/quant`：量化研究工作台一站式数据（ETF定投计算器 + 因子有效性IC/分层回测 + 回测质量审计 + 自然语言选股器）
- `GET /api/workflows/workbench/portfolio-review`：组合复盘工作台一站式数据（投资论点看板 + 交易与研究日志闭环 + 多源新闻水位线去重摘要）
- `POST /api/workflows/etf-dca/calculate`：ETF定投计算器交互测算接口（正向测算/历史净值回测/目标反推/一次性买入对比）
- `POST /api/workflows/factor/analyze`：因子有效性 IC/Rank IC 与分层回测接口
- `POST /api/workflows/backtest/audit`：回测质量 6 维防坑审计接口
- `POST /api/workflows/screener/build`：自然语言转结构化选股条件接口
- `POST /api/workflows/thesis/check`：投资论点与反证条件对照最新公告核查接口
- `GET /api/workflows/announcement/{announcement_id}`：按页码拉取公告 PDF 原文详情（供右侧 PDF 页码抽屉打开）
- `POST /api/workflows/verify-numbers`：调用数字正则回读校验器（`TC-CALC-04`）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.providers.broker.router import get_default_broker_router
from app.services.etf_dca import calculate_etf_dca_full_plan
from app.services.workflows import (
    build_natural_language_screener,
    run_after_close_workflow,
    run_backtest_quality_audit,
    run_factor_research_analysis,
    run_intraday_anomaly_explainer,
    run_investment_thesis_tracker,
    run_portfolio_review_workbench_bundle,
    run_pre_market_workflow,
    run_quant_workbench_bundle,
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


class EtfDcaCalculateRequest(BaseModel):
    symbol: str = Field(default="510300.SH", description="ETF代码，如 510300.SH / 510050.SH / 159915.SZ")
    periodic_amount: float = Field(default=2000.0, gt=0, description="每期定投金额（元）")
    frequency: str = Field(default="monthly", description="定投频率：monthly / biweekly / weekly")
    invest_day: int = Field(default=15, ge=1, le=28, description="每月扣款日（1-28）或每周星期几（1-5）")
    start_date: str = Field(default="2023-11-01", description="回测起始日期 YYYY-MM-DD")
    end_date: str = Field(default="2026-11-13", description="回测截止日期 YYYY-MM-DD")
    subscription_fee_pct: float = Field(default=0.10, ge=0.0, lt=5.0, description="申购费率(%)")
    dividend_reinvest: bool = Field(default=True, description="是否分红再投资")
    target_amount: float = Field(default=100000.0, gt=0, description="未来目标金额（元）")
    target_years: float = Field(default=3.0, gt=0, description="目标投资年限（年）")
    assumed_annual_return_pct: float = Field(default=6.0, description="目标反推假设年化收益率(%)")


class FactorAnalyzeRequest(BaseModel):
    factor_id: str = Field(default="low_pe_high_div", description="因子ID：low_pe_high_div / roe_quality_growth / low_vol_momentum")
    universe: str = Field(default="000300.SH", description="股票池指数代码")
    neutralize_industry: bool = Field(default=True, description="是否申万一级行业市值中性化")


class BacktestAuditRequest(BaseModel):
    strategy_name: str = Field(default="低市盈率+高股息月度轮动策略")
    use_pit_fundamentals: bool = Field(default=True, description="是否按披露日 published_at 使用 Point-in-Time 财报")
    include_delisted_stocks: bool = Field(default=True, description="是否包含退市股与历史成分股防幸存者偏差")
    adjustment_type: str = Field(default="前复权", description="复权方式")
    commission_bps: float = Field(default=3.0, ge=0.0)
    stamp_duty_bps: float = Field(default=5.0, ge=0.0)
    slippage_bps: float = Field(default=10.0, ge=0.0)
    filter_limit_up_down: bool = Field(default=True, description="是否过滤一字涨跌停不可成交")
    filter_halted_stocks: bool = Field(default=True, description="是否过滤停牌标的")


class ScreenerBuildRequest(BaseModel):
    natural_query: str = Field(
        default="筛选沪深300成分股中市盈率低于15倍、股息率大于3%、ROE大于10%且非ST正常交易的股票",
        description="自然语言选股描述",
    )


class ThesisCheckRequest(BaseModel):
    symbol: str | None = Field(default=None, description="股票代码（可选）")
    custom_thesis: str | None = Field(default=None, description="自定义买入理由（可选）")
    invalidation_rule: str | None = Field(default=None, description="自定义失效反证条件（可选）")


P0_DAILY_SKILLS_META = [
    {
        "name": "daily_market_brief",
        "display_name": "每日市场晨报",
        "tier": "P0",
        "stage": "盘前 08:30",
        "workbench": "market",
        "description": "读取交易日历、核心指数、领涨领跌行业、宏观与合规新闻，生成《今日盘前观察卡》。",
    },
    {
        "name": "watchlist_daily_brief",
        "display_name": "自选股排雷日报",
        "tier": "P0",
        "stage": "盘前/盘中",
        "workbench": "watchlist",
        "description": "逐只扫描用户自选股行情、公告、财报摘要与 ST/停牌风险状态。",
    },
    {
        "name": "announcement_impact",
        "display_name": "公告深度解读",
        "tier": "P0",
        "stage": "全天候",
        "workbench": "watchlist",
        "description": "提取减持/回购/业绩预告/合同核心条款，强制标注 [来源：公告标题 · 第X页] 原文页码定位。",
    },
    {
        "name": "earnings_review",
        "display_name": "财报质量分析",
        "tier": "P0",
        "stage": "财报季/个股",
        "workbench": "stock",
        "description": "检验营收/归母/扣非同比环比、净现比、应收存货异动与存贷双高排雷项。",
    },
    {
        "name": "technical_dashboard",
        "display_name": "56项技术面看板",
        "tier": "P0",
        "stage": "个股研究",
        "workbench": "stock",
        "description": "调用 7 大类 56 项技术指标确定性计算引擎，输出趋势/动量/量价/波动率及多指标冲突诊断。",
    },
    {
        "name": "fundamental_scorecard",
        "display_name": "90项基本面评分卡",
        "tier": "P0",
        "stage": "个股研究",
        "workbench": "stock",
        "description": "调用 6 大维度 90 项基本面计算引擎，输出六维体检得分、同行业分位与会计重述对照。",
    },
    {
        "name": "portfolio_risk_check",
        "display_name": "组合风险巡检",
        "tier": "P0",
        "stage": "盘前/盘后",
        "workbench": "watchlist",
        "description": "核算单票最高占比、前三大行业集中度、历史最大回撤与宽基/行业压力测试情景。",
    },
    {
        "name": "after_close_review",
        "display_name": "15:30 盘后复盘",
        "tier": "P0",
        "stage": "盘后 15:30",
        "workbench": "market",
        "description": "对比早盘观察条件与收盘实际结果，更新投资论点并将复盘证据沉淀至 fin_memory。",
    },
]

P1_ADVANCED_SKILLS_META = [
    {
        "name": "factor_research",
        "display_name": "量化因子有效性研究",
        "tier": "P1",
        "stage": "量化研究",
        "workbench": "quant",
        "description": "计算因子 IC/Rank IC、IR、5层分层回测单调性、申万行业中性化与交易拥挤度分位。",
    },
    {
        "name": "etf_dca_planner",
        "display_name": "ETF定投计算器与计划助手",
        "tier": "P1",
        "stage": "量化研究/定投配置",
        "workbench": "quant",
        "description": "调用确定性 ETF 定投引擎完成正向历史回测（累计份额/IRR/最大回撤）、目标金额反推与一次性买入对比。",
    },
    {
        "name": "investment_thesis_tracker",
        "display_name": "投资论点与反证条件跟踪",
        "tier": "P1",
        "stage": "组合复盘",
        "workbench": "portfolio_review",
        "description": "记录核心买入逻辑、催化剂、反证失效条件与复核期限，自动对照最新公告核查论点是否受损。",
    },
    {
        "name": "stock_screener_builder",
        "display_name": "自然语言结构化选股器",
        "tier": "P1",
        "stage": "量化研究",
        "workbench": "quant",
        "description": "将大白话选股想法转为结构化筛选条件，校验字段可用性（不存在字段绝不静默伪造）。",
    },
    {
        "name": "news_signal_digest",
        "display_name": "多源新闻水位线去重摘要",
        "tier": "P1",
        "stage": "组合复盘/日常跟踪",
        "workbench": "portfolio_review",
        "description": "基于水位线时间戳去重、跨源同事件指纹聚类、关联影响标的映射与情绪倾向客观标注。",
    },
    {
        "name": "trading_journal",
        "display_name": "交易与研究日志闭环",
        "tier": "P1",
        "stage": "组合复盘",
        "workbench": "portfolio_review",
        "description": "建立交易前计划、盘中执行对照与交易后纪律复盘评分闭环（纯模拟演练与复盘，禁实盘指令）。",
    },
    {
        "name": "backtest_quality_auditor",
        "display_name": "量化回测质量防坑审计",
        "tier": "P1",
        "stage": "量化研究",
        "workbench": "quant",
        "description": "专项审计前视偏差/未来函数（PIT财报）、幸存者偏差、复权因子、印花税滑点与一字涨跌停/停牌不可成交问题。",
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
    """返回 M3 阶段全部 22 个金融 SKILL（8 P0 + 7 P1 + 7 升级）、3 条日常工作流与五大工作台目录。"""
    return {
        "ok": True,
        "version": "3.3.0",
        "total_skills_count": len(P0_DAILY_SKILLS_META) + len(P1_ADVANCED_SKILLS_META) + len(UPGRADED_SKILLS_META),
        "p0_daily_skills_count": len(P0_DAILY_SKILLS_META),
        "p1_advanced_skills_count": len(P1_ADVANCED_SKILLS_META),
        "upgraded_skills_count": len(UPGRADED_SKILLS_META),
        "p0_daily_skills": P0_DAILY_SKILLS_META,
        "p1_advanced_skills": P1_ADVANCED_SKILLS_META,
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
                "skills": ["watchlist_daily_brief", "announcement_impact", "investment_thesis_tracker"],
            },
            {
                "id": "after_close",
                "name": "15:30 盘后复盘流（经验沉淀）",
                "endpoint": "/api/workflows/after-close",
                "skills": ["after_close_review", "trading_journal"],
            },
        ],
        "workbenches": [
            {"id": "market", "name": "市场工作台", "endpoint": "/api/workflows/workbench/market"},
            {"id": "watchlist", "name": "自选股工作台", "endpoint": "/api/workflows/workbench/watchlist"},
            {"id": "stock", "name": "个股研究工作台", "endpoint": "/api/workflows/workbench/stock"},
            {"id": "quant", "name": "量化研究工作台", "endpoint": "/api/workflows/workbench/quant"},
            {"id": "portfolio_review", "name": "组合复盘工作台", "endpoint": "/api/workflows/workbench/portfolio-review"},
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


@router.get("/workbench/quant")
def api_get_quant_workbench(
    etf_symbol: str = Query(default="510300.SH", description="ETF代码"),
    periodic_amount: float = Query(default=2000.0, gt=0, description="每期定投金额（元）"),
    frequency: str = Query(default="monthly", description="定投频率：monthly / biweekly / weekly"),
    invest_day: int = Query(default=15, ge=1, le=28),
    start_date: str = Query(default="2023-11-01"),
    end_date: str = Query(default="2026-11-13"),
    subscription_fee_pct: float = Query(default=0.10, ge=0.0, lt=5.0),
    dividend_reinvest: bool = Query(default=True),
    target_amount: float = Query(default=100000.0, gt=0),
    target_years: float = Query(default=3.0, gt=0),
    assumed_annual_return_pct: float = Query(default=6.0),
    factor_id: str = Query(default="low_pe_high_div", description="因子ID"),
) -> dict[str, Any]:
    """量化研究工作台一站式数据：ETF定投计算器 + 因子有效性IC/分层回测 + 回测质量审计 + 自然语言选股器。"""
    return run_quant_workbench_bundle(
        etf_symbol=etf_symbol,
        periodic_amount=periodic_amount,
        frequency=frequency,
        invest_day=invest_day,
        start_date=start_date,
        end_date=end_date,
        subscription_fee_pct=subscription_fee_pct,
        dividend_reinvest=dividend_reinvest,
        target_amount=target_amount,
        target_years=target_years,
        assumed_annual_return_pct=assumed_annual_return_pct,
        factor_id=factor_id,
    )


@router.get("/workbench/portfolio-review")
def api_get_portfolio_review_workbench(
    symbol: str | None = Query(default=None, description="可选筛选股票代码"),
) -> dict[str, Any]:
    """组合复盘工作台一站式数据：投资论点看板 + 交易与研究日志 + 多源新闻去重信号摘要。"""
    return run_portfolio_review_workbench_bundle(symbol=symbol)


@router.post("/etf-dca/calculate")
def api_calculate_etf_dca(req: EtfDcaCalculateRequest) -> dict[str, Any]:
    """ETF 定投计算器交互测算接口（正向测算 + 历史净值回测 + 目标反推 + 定投 vs 一次性买入对比）。"""
    return calculate_etf_dca_full_plan(
        symbol=req.symbol,
        periodic_amount=req.periodic_amount,
        frequency=req.frequency,  # type: ignore[arg-type]
        invest_day=req.invest_day,
        start_date=req.start_date,
        end_date=req.end_date,
        subscription_fee_pct=req.subscription_fee_pct,
        dividend_reinvest=req.dividend_reinvest,
        target_amount=req.target_amount,
        target_years=req.target_years,
        assumed_annual_return_pct=req.assumed_annual_return_pct,
    )


@router.post("/factor/analyze")
def api_analyze_factor(req: FactorAnalyzeRequest) -> dict[str, Any]:
    return run_factor_research_analysis(
        factor_id=req.factor_id,
        universe=req.universe,
        neutralize_industry=req.neutralize_industry,
    )


@router.post("/backtest/audit")
def api_audit_backtest(req: BacktestAuditRequest) -> dict[str, Any]:
    return run_backtest_quality_audit(
        strategy_name=req.strategy_name,
        use_pit_fundamentals=req.use_pit_fundamentals,
        include_delisted_stocks=req.include_delisted_stocks,
        adjustment_type=req.adjustment_type,
        commission_bps=req.commission_bps,
        stamp_duty_bps=req.stamp_duty_bps,
        slippage_bps=req.slippage_bps,
        filter_limit_up_down=req.filter_limit_up_down,
        filter_halted_stocks=req.filter_halted_stocks,
    )


@router.post("/screener/build")
def api_build_screener(req: ScreenerBuildRequest) -> dict[str, Any]:
    return build_natural_language_screener(natural_query=req.natural_query)


@router.post("/thesis/check")
def api_check_thesis(req: ThesisCheckRequest) -> dict[str, Any]:
    return run_investment_thesis_tracker(
        symbol=req.symbol,
        custom_thesis=req.custom_thesis,
        invalidation_rule=req.invalidation_rule,
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
