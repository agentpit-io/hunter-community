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
from app.services.data import klines_etl
from datetime import datetime, timezone, timedelta
import asyncio
import os

# Hunter Community · when 1, skip background schedulers (collector · signal_monitor
# · gm_alerts · backtest · stocks_catalog seed) that need external creds.
# DDL is ALWAYS run so table-backed features (auth · watchlist · settings)
# work regardless of this flag. Only impacts the polling / scheduled jobs.
HUNTER_MINIMAL_BOOT = os.getenv("HUNTER_MINIMAL_BOOT", "0") == "1"

# HUNTER_API_KEY 缺失是最常见的"静默失败"根因:调 Kronos / 工具 / 数据全 403 ·
# 用户看不出为何 · 排查半天。启动阶段直接 log 醒目 ERROR + /api/health 暴露状态。
# 不阻塞启动 —— 用户仍可在 UI 左下角「解锁全部工具」现填现用。
_HUNTER_API_KEY_CONFIGURED = bool(os.getenv("HUNTER_API_KEY", "").strip())
if not _HUNTER_API_KEY_CONFIGURED:
    logger.error(
        "═" * 60 + "\n"
        "❌ HUNTER_API_KEY 未配置 · 走 hunter 网关的功能(Kronos 预测 / "
        "K 线 / 财报 / 新闻 / 工具 / SKILL) 将全部返回 403\n"
        "   申请: https://hunter.agentpit.io/dev/api-keys  (免费 · 约 30 秒)\n"
        "   填到: .env 里 HUNTER_API_KEY=hunt_tools_xxxx · 然后 docker compose up -d api\n"
        "   或在 UI 左下角「解锁全部工具」现填现用\n"
        + "═" * 60
    )

_signal_task = None
_gm_alert_task = None
_backtest_task = None
_klines_etl_task = None

# 东八区 —— A 股/港股收盘 + 美股次日凌晨的触发窗口都按 CST 算
CST = timezone(timedelta(hours=8))


async def _klines_etl_loop():
    """每 10 min 检查一次 · 在收盘后的触发窗口内跑 klines_etl.

    ⚠ 适配说明:klines_etl.py(小王 bc19dc0)对外只暴露同步的
    ``run_market(market=...)`` 和 ``health()``,并**没有** ``daily_etl`` /
    ``target_date`` —— run_market 每次直接从三源(腾讯/akshare/新浪)拉最近
    MAX_BARS(800)根日线并 UPSERT,天然覆盖当日最新一根,所以不需要按日期取。
    该文件属小王代码,此处只 import 不改,故按其真实签名调用。

    run_market 是阻塞函数(逐只 sleep + 网络 IO),放线程里跑,别堵事件循环。
    """
    _last_trigger = {}  # 防止同一触发窗口内重复跑(键: 市场 → 日期)
    while True:
        try:
            now = datetime.now(CST)
            hour_min = now.hour * 60 + now.minute
            weekday = now.weekday()  # 0-6 · 中国节假日暂不判断(P2)
            today = now.date()

            is_trading_day = weekday < 5

            # 15:25-15:40 CST → A 股当日 ETL
            if is_trading_day and 925 <= hour_min <= 940 and _last_trigger.get('cn') != today:
                await asyncio.to_thread(klines_etl.run_market, 'cn')
                _last_trigger['cn'] = today

            # 16:40-16:55 CST → 港股当日 ETL
            elif is_trading_day and 1000 <= hour_min <= 1015 and _last_trigger.get('hk') != today:
                await asyncio.to_thread(klines_etl.run_market, 'hk')
                _last_trigger['hk'] = today

            # 03:25-03:40 CST 次日 → 美股(dedup 键按前一交易日算)
            elif 205 <= hour_min <= 220:
                yesterday = today - timedelta(days=1)
                if _last_trigger.get('us') != yesterday:
                    await asyncio.to_thread(klines_etl.run_market, 'us')
                    _last_trigger['us'] = yesterday

            await asyncio.sleep(600)  # 10 min 检查一次
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("_klines_etl_loop 错误 · 60s 后重试")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _signal_task, _gm_alert_task, _backtest_task, _klines_etl_task

    # ─── Business tables · always ensure (idempotent · no side effects) ───
    try:
        await init_db()
    except Exception as e:
        logger.error("[boot] init_db failed (service will run but table-backed features may 500): {}", e)

    # 上次崩掉留下的下载任务 —— worker 是进程内的线程,容器一重启它就没了,
    # 而库里状态还是 running。不处理的话页面上永远显示"进行中"然后不动。
    # 标成 paused,用户点一下续跑就接着下(已下载的不会重来)。
    try:
        from app.services.quant.data_job import reclaim_orphans as _reclaim
        _n = _reclaim()
        if _n:
            logger.warning("[data_job] {} 个任务因服务重启中断 · 已标为可续跑", _n)
    except Exception as e:
        logger.warning("[data_job] 回收中断任务失败(非致命): {}", e)

    # ─── 不需要任何凭据的任务 · 在 MINIMAL_BOOT 之前就挂上 ───
    #
    # MINIMAL_BOOT 的本意是"跳过需要外部凭据的后台任务",但它是个一刀切的开关,
    # 把量化的每日因子重算也一起关掉了。后果:开源实例的因子数据停在最后一次
    # 手动回填,**永远不会更新**,而界面上没有任何地方提示(实测停在
    # 2026-07-15,过期一个多月)。
    #
    # 而这条流水线的四步 —— 成分股 / 个股日线 / 指数日线 / 技术因子 ——
    # 全部走 AKShare 直连或腾讯直连,不需要任何 key。它不该被这个开关关掉。
    _local_sched = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler as _AS
        from app.services.quant.scheduler import register_local as _reg_local
        _local_sched = _AS(timezone="Asia/Shanghai")
        _reg_local(_local_sched)
        _local_sched.start()
        logger.info("[quant.local] 每日 17:10 CST 本地流水线已挂载(不需要任何 key)")

        # ⚠ **不要在这里自动下载数据。**
        #
        # 曾经这里有一段"库是空的就立刻跑一遍"。老板的意见是:
        #   「刚下载启动容器自动跑不太好,用户都不知道你就占用他的资源
        #     很不好」
        #
        # 一个用户 docker compose up 只是想看看这东西长什么样,而我们
        # 直接开始下 800 只股票、跑 25 分钟、吃他的带宽和 CPU —— 他既不
        # 知道在跑什么,也没法叫停。
        #
        # 现在改成:什么都不做。用户到「数据」页自己选范围和时长再下。
        # 库是空的时候前端会提示他去下载(见 GET /api/quant/data/overview)。
        #
        # 定时任务仍然挂着,但它只更新**用户已经下过的**股票
        # (以 data_coverage 为准)—— 一只都没下过的实例,它什么都不做。
    except Exception as e:
        logger.warning("[quant.local] 本地流水线挂载失败(非致命): {}", e)

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

    # klines 每日 ETL(15:30 CST A股 · 17:00 CST 港股 · 03:30 CST 次日美股)
    # 走三源直连不需要 key · 但会拉全股票池下载数据 · 故只在非 MINIMAL_BOOT 挂载
    try:
        _klines_etl_task = asyncio.create_task(_klines_etl_loop())
        logger.info("[klines.etl] 每日 ETL loop 已挂载(A股 15:30 · 港股 17:00 · 美股次日 03:30 CST)")
    except Exception as e:
        logger.warning("[klines.etl] loop 挂载失败(非致命): {}", e)

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
    if _klines_etl_task:
        _klines_etl_task.cancel()
        await asyncio.gather(_klines_etl_task, return_exceptions=True)

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
from app.routers import admin_etl
app.include_router(admin_etl.router, prefix="/api")

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

# 数据中心 · /api/quant/data/* · 单独一个 router(quant.py 已 600+ 行)
from app.routers import quant_data as quant_data_router
app.include_router(quant_data_router.router, prefix="/api")

# ── /chat 会话归属(用户隔离的权威数据源, 供 web BFF 调用) ──
from app.routers import chat_session as chat_session_router
app.include_router(chat_session_router.router, prefix="/api")

# ── /chat 能力面板(内置能力 + 用户自定义) ──
from app.routers import chat_skill as chat_skill_router
app.include_router(chat_skill_router.router, prefix="/api")

# ── 持仓建议 Phase 0 · MCP 桥接（暴露给 /opt/opencode-mcp/*_mcp.py 反调）──
from app.routers import internal_tools as internal_tools_router
app.include_router(internal_tools_router.router, prefix="/api")

# ── OCR 桥接 · 供 web BFF 拦截用户上传截图后抽文本(批量加自选走这条链)──
from app.routers import internal_ocr as internal_ocr_router
app.include_router(internal_ocr_router.router, prefix="/api")

# ── 用户自定义 MCP · P0 MVP · 用户可自加 Polygon/Alpha Vantage/自研 MCP ──
from app.routers import user_mcp as user_mcp_router
from app.routers import internal_user_mcp as internal_user_mcp_router
app.include_router(user_mcp_router.router, prefix="/api")
app.include_router(internal_user_mcp_router.router, prefix="/api")

# ── 用户自定义数据源 · `_21` 步 2 —— 「用户脱离我们也能玩转」的落点 ──
# 与 user_mcp 并列注册:两者是三层能力模型里相邻的两层
# (数据源层 / 工具层),用户自带能力的入口也应该挨在一起
from app.routers import user_sources as user_sources_router
app.include_router(user_sources_router.router, prefix="/api")

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
    # hunter_api_key 字段让 docker healthcheck 和运维一眼看出 SaaS 功能可不可用
    # missing 时不返 503(不阻塞 web depends_on) · 但明确暴露状态
    return {
        "status": "ok",
        "service": "hunter",
        "hunter_api_key": "configured" if _HUNTER_API_KEY_CONFIGURED else "missing",
    }
