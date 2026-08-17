from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from contextlib import asynccontextmanager
from app.routers import quote, kline, news, fundflow, watchlist, alerts, signals, signal_settings
from app.routers import auth as auth_router
from app.middleware.auth import AuthMiddleware
from app.providers.data_source.hunter_tools import HunterKeyRequired
from app.services.collector import start_collector, stop_collector
from app.services.database import init_db, get_stocks
from app.services.finance_data_client import register_stocks
from app.services import signal_monitor
import asyncio
import os

# Hunter Community · when 1, skip background schedulers (collector · signal_monitor
# · gm_alerts · backtest · stocks_catalog seed) that need external creds.
# DDL is ALWAYS run so table-backed features (auth · watchlist · settings)
# work regardless of this flag. Only impacts the polling / scheduled jobs.
HUNTER_MINIMAL_BOOT = os.getenv("HUNTER_MINIMAL_BOOT", "0") == "1"

_signal_task = None
_gm_alert_task = None
_backtest_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _signal_task, _gm_alert_task, _backtest_task

    # ─── Business tables · always ensure (idempotent · no side effects) ───
    try:
        await init_db()
    except Exception as e:
        logger.error("[boot] init_db failed (service will run but table-backed features may 500): {}", e)

    if HUNTER_MINIMAL_BOOT:
        logger.warning("[hunter-community] HUNTER_MINIMAL_BOOT=1 · skipping background schedulers only (tables OK)")
        yield
        return

    # ─── Background jobs · gated by MINIMAL_BOOT ───
    register_stocks(get_stocks())
    await start_collector()
    _signal_task = asyncio.create_task(signal_monitor.run_monitors())
    from app.services.gm import alert_checker as gm_alert_checker
    try:
        # gm_alerts/gm_alert_hits 启动时统一建表, 避免并发首访触发惰性DDL
        from app.routers.gm.alerts import ensure_tables as _gm_ensure_tables
        _gm_ensure_tables()
    except Exception:
        pass
    _gm_alert_task = asyncio.create_task(gm_alert_checker.run_loop())

    # 预测回测流水线(每交易日 16:30 CST): 预测留档 → 事后回测 → 重叠一致性归因
    try:
        from app.services.backtest import scheduler as bt_scheduler
        _backtest_task = asyncio.create_task(bt_scheduler.run_loop())
    except Exception as e:
        logger.warning("[backtest] scheduler 启动失败(非致命): {}", e)

    # 启动时同步一次 stocks_catalog (若表空则从 akshare/baseline 初始化)
    # 每日 03:00 CST 由 APScheduler 触发 seed 保持新鲜
    try:
        import sys as _sys, os as _os
        _script_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "scripts")
        if _script_dir not in _sys.path:
            _sys.path.insert(0, _script_dir)
        from seed_stocks_catalog import seed_stocks_catalog as _seed_catalog

        async def _boot_seed_catalog():
            try:
                res = await asyncio.to_thread(_seed_catalog)
                logger.info("[stocks_catalog] boot seed: {}", res)
            except Exception as e:
                logger.warning("[stocks_catalog] boot seed 失败(非致命): {}", e)
        asyncio.create_task(_boot_seed_catalog())

        # 每日 03:00 CST 定时 reseed
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            _catalog_sched = AsyncIOScheduler(timezone="Asia/Shanghai")
            _catalog_sched.add_job(
                lambda: asyncio.create_task(asyncio.to_thread(_seed_catalog)),
                CronTrigger(hour=3, minute=0),
                id="stocks_catalog_daily_seed", replace_existing=True,
            )
            # B4 · quant 每日 17:00 CST 重算 16 因子 · 复用同一 scheduler
            try:
                from app.services.quant.scheduler import register as _register_quant
                _register_quant(_catalog_sched)
            except Exception as e:
                logger.warning("[quant.scheduler] 注册失败(非致命): {}", e)
            _catalog_sched.start()
            logger.info("[stocks_catalog+quant] APScheduler 已启动 · catalog 03:00 · quant 17:00 CST")
        except Exception as e:
            logger.warning("[stocks_catalog] APScheduler 启动失败(非致命): {}", e)
    except Exception as e:
        logger.warning("[stocks_catalog] seed 模块加载失败(非致命): {}", e)

    logger.info("Hermes API started")
    yield
    if HUNTER_MINIMAL_BOOT:
        return
    await stop_collector()
    if _signal_task:
        _signal_task.cancel()
    if _gm_alert_task:
        _gm_alert_task.cancel()
    if _backtest_task:
        _backtest_task.cancel()

# FastAPI Swagger + OpenAPI closed by default · they leak full API surface.
# Ops who want them can set HUNTER_ENABLE_DOCS=1 (behind their own auth layer).
_ENABLE_DOCS = os.getenv("HUNTER_ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Hunter 财经聚合 API",
    lifespan=lifespan,
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

# 「需要 Hunter key」是可预期的业务状态,不是服务器错误。统一在这里翻译成结构化
# 响应,免得每个用到取数的路由各写一遍 try/except —— 漏一个就是一个裸 500,
# 而 MCP 把 500 交给模型,模型只会说"服务异常",用户永远不知道差一把免费 key。
@app.exception_handler(HunterKeyRequired)
async def _hunter_key_required(request, exc: HunterKeyRequired):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=200,   # 200 是有意的:这是给模型看的"结果",不是传输失败
        content={
            "error": "hunter_key_required",
            "message": str(exc),
            "apply_url": exc.apply_url,
            # 冗余一份禁令:模型有时只读 message、有时扫全字段,两边都放命中率更高
            "must_not": "禁止编造任何价格/涨跌幅/成交额/时间戳,你没有这只股票的数据",
        },
    )


app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(quote.router, prefix="/api")
app.include_router(kline.router, prefix="/api")
app.include_router(news.router, prefix="/api")
app.include_router(fundflow.router, prefix="/api")
app.include_router(watchlist.router, prefix="/api")
app.include_router(auth_router.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(signal_settings.router, prefix="/api")
from app.routers import signal_report
app.include_router(signal_report.router, prefix="/api")
from app.routers import online_analysis
app.include_router(online_analysis.router, prefix="/api")
from app.routers import kpred
app.include_router(kpred.router, prefix="/api")
from app.routers import preference
app.include_router(preference.router, prefix="/api")
from app.routers import portfolio
app.include_router(portfolio.router, prefix="/api")
from app.routers import truesource
app.include_router(truesource.router, prefix="/api")
from app.routers import discover
app.include_router(discover.router, prefix="/api")
from app.routers import research_assistant
app.include_router(research_assistant.router, prefix="/api")

# P4 · Per-user SaaS accelerator config
from app.routers import settings as settings_router
app.include_router(settings_router.router, prefix="/api")

# ── Agent Chat V2 · 主对话多智能体调度 ──
from app.routers import agent_chat
app.include_router(agent_chat.router, prefix="/api")

# ── 美港股独立端（gm = Global Markets） ──
from app.routers.gm import watchlist as gm_watchlist
from app.routers.gm import quote as gm_quote
from app.routers.gm import kline as gm_kline
from app.routers.gm import discover as gm_discover
from app.routers.gm import kpred as gm_kpred
from app.routers.gm import news as gm_news
from app.routers.gm import research as gm_research
from app.routers.gm import scout as gm_scout
from app.routers.gm import messages as gm_messages
from app.routers.gm import recap as gm_recap
from app.routers.gm import portfolio as gm_portfolio
from app.routers.gm import guardian as gm_guardian
from app.routers.gm import alerts as gm_alerts
from app.routers.gm import assistant as gm_assistant
app.include_router(gm_watchlist.router, prefix="/api/gm")
app.include_router(gm_quote.router, prefix="/api/gm")
app.include_router(gm_kline.router, prefix="/api/gm")
app.include_router(gm_discover.router, prefix="/api/gm")
app.include_router(gm_kpred.router, prefix="/api/gm")
app.include_router(gm_news.router, prefix="/api/gm")
app.include_router(gm_research.router, prefix="/api/gm")
app.include_router(gm_scout.router, prefix="/api/gm")
app.include_router(gm_messages.router, prefix="/api/gm")
app.include_router(gm_recap.router, prefix="/api/gm")
app.include_router(gm_portfolio.router, prefix="/api/gm")
app.include_router(gm_guardian.router, prefix="/api/gm")
app.include_router(gm_alerts.router, prefix="/api/gm")
app.include_router(gm_assistant.router, prefix="/api/gm")

# ── 地缘冲突数据(geo, 与gm平级) ──
from app.routers.geo import overview as geo_overview
app.include_router(geo_overview.router, prefix="/api/geo")

# ── 预测回测(准确性 + 重叠一致性归因) ──
from app.routers import backtest as backtest_router
app.include_router(backtest_router.router, prefix="/api")

# ── 量化策略(因子/选股/回测) · Phase A · doc/11量化策略/quant-strategy-tech-plan.md ──
from app.routers import quant as quant_router
app.include_router(quant_router.router, prefix="/api")

# ── /chat 会话归属(用户隔离的权威数据源, 供 web BFF 调用) ──
from app.routers import chat_session as chat_session_router
app.include_router(chat_session_router.router, prefix="/api")

# ── /chat 能力面板(内置能力 + 用户自定义) ──
from app.routers import chat_skill as chat_skill_router
app.include_router(chat_skill_router.router, prefix="/api")

# ── 持仓建议 Phase 0 · MCP 桥接（暴露给 /opt/opencode-mcp/*_mcp.py 反调）──
from app.routers import internal_tools as internal_tools_router
app.include_router(internal_tools_router.router, prefix="/api")

# ── 用户自定义 MCP · P0 MVP · 用户可自加 Polygon/Alpha Vantage/自研 MCP ──
from app.routers import user_mcp as user_mcp_router
from app.routers import internal_user_mcp as internal_user_mcp_router
app.include_router(user_mcp_router.router, prefix="/api")
app.include_router(internal_user_mcp_router.router, prefix="/api")

# ── hunter-UZI-Skill 集成 · Sprint 3 P2 · chat 深度分析 tool ──
# 平台自有能力的 MCP 桥接(_12 Step 3)· K线预测/情报 等原来只有 HTTP 接口、
# 模型够不着的能力,经这里暴露成 /api/internal/cap/* 再包成 MCP
from app.routers import internal_capabilities as internal_cap_router
app.include_router(internal_cap_router.router, prefix="/api")

from app.routers import internal_uzi as internal_uzi_router
app.include_router(internal_uzi_router.router, prefix="/api")

# ── 用户画像与记忆体 + admin 用户洞察后台 ──
# ── 平台 key 门控 · 开源版解锁全部工具与 SKILL ──
from app.routers import hunter_unlock as hunter_unlock_router
app.include_router(hunter_unlock_router.router, prefix="/api")

from app.routers import user_profile as user_profile_router
app.include_router(user_profile_router.router, prefix="/api")

# ── Artifact 发布 · 公开链接系统 (Claude 风) ──
from app.routers import artifact as artifact_router
app.include_router(artifact_router.router, prefix="/api")

# ── Chat 多专家辩论 SKILL · TradingAgents 移植 · 复用 agents/ ──
from app.routers import chat_debate as chat_debate_router
app.include_router(chat_debate_router.router, prefix="/api")

# ── Chat Kronos 预测 SKILL · HTML 富可视化(Sprint E) ──
from app.routers import chat_kpred as chat_kpred_router
app.include_router(chat_kpred_router.router, prefix="/api")

# ── 能力目录 · 三层模型(数据源/工具箱/SKILL)的查询入口 ──
from app.routers import catalog as catalog_router
app.include_router(catalog_router.router, prefix="/api")

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "hunter"}
