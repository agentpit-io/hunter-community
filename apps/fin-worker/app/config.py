"""fin-worker 配置。

**这里没有数据库连接串，而且不许加。** fin-worker 是「推理区 → 执行区」之间的
Runtime Bridge（`01方案 §5.1`）：它把策略意图翻译成 Paper Service 命令，自己
**不碰账本**。账本库（`hunter_fin` / 应用库里的 `fin_*` 表）只有一个写入口 ——
`paper` 的 HTTP 接口（`01方案 §7.4` / §11.3、`08 §二`）。

`tests/test_no_ledger_access.py` 有一条源码守卫盯着这件事：本模块与整个
`app/` 里不许出现 DB 驱动 import、不许出现 `DATABASE_URL` 之类的读取。
「不直连账本」不是一句注释，是一条能被 grep 出来的事实。
"""

from __future__ import annotations

import os

from loguru import logger


# ── Temporal ──────────────────────────────────────────────────────────────
def temporal_address() -> str:
    return (os.getenv("TEMPORAL_ADDRESS") or "temporal:7233").strip()


def temporal_namespace() -> str:
    return (os.getenv("TEMPORAL_NAMESPACE") or "default").strip()


def temporal_task_queue() -> str:
    return (os.getenv("TEMPORAL_TASK_QUEUE") or "fin-trading").strip()


# ── 执行区（只有 HTTP）────────────────────────────────────────────────────
def paper_base_url() -> str:
    """唯一能改账本的服务。fin-worker 的全部写操作都打到这里。"""
    return (os.getenv("PAPER_BASE_URL") or "http://paper:8000").rstrip("/")


def api_base_url() -> str:
    """hunter-community api。用于「谁决定什么时候拉数据」里的取数触发与交易日历。"""
    return (os.getenv("API_BASE_URL") or "http://api:8000").rstrip("/")


# ── 两把钥匙（L06）────────────────────────────────────────────────────────
#   行情 / 数据读取：打 api 的**数据面**（日历 / ETL / 行情 / 经验 …）带它 —— 见
#     `bridge/hunter_api.py`。名字沿用既有的内网口令（api 数据面读的是同一个变量）。
#   下单执行：打 paper 的**执行接口**（下单 / 撤单 …）带它 —— 见 `bridge/paper.py`。
#     paper 的执行门校验的正是这把（`apps/paper/app/security.py`）。
#   ⚠️ 两个常量是「钥匙取自哪个环境变量」的**唯一事实**；改这里要连 paper 那侧一起想。
READ_KEY_ENV = "HUNTER_INTERNAL_KEY"
EXEC_KEY_ENV = "HUNTER_EXEC_KEY"


def read_key() -> str:
    """行情 / 数据读取凭证（`READ_KEY_ENV`）。**没有默认值**。"""
    return (os.getenv(READ_KEY_ENV) or "").strip()


def exec_key() -> str:
    """下单执行凭证（`EXEC_KEY_ENV`）。**没有默认值** —— 与 `read_key()` 取自不同变量。"""
    return (os.getenv(EXEC_KEY_ENV) or "").strip()


# ── 内部 HTTP 端点（给 api / 运维触发）────────────────────────────────────
def http_port() -> int:
    try:
        return int(os.getenv("FIN_WORKER_HTTP_PORT") or "8300")
    except ValueError:
        return 8300


# ── 一期固定示例策略（管道跑通优先，策略做强是后面的事，`08 §二`）────────
def sample_code(market: str = "CN_A") -> str:
    """固定示例策略的标的，**按市场各一个**（N4：市场是一等参数）。

    · `CN_A` 默认 601398（工商银行）：低价 + 高流动性，三个档位（1 万 / 10 万 / 100 万）
      的示例单都买得起，管道能在任何档位跑通；env `FIN_SAMPLE_CODE`（一期口径，逐字不变）。
    · `HK` / `US` 默认 00700 / AAPL：**示例标的**（各市场一只高流动性票），
      不是选股结论 —— 港美股时点只在有对应市场子账户的项目上跑。
    """
    key = (market or "CN_A").strip().upper()
    if key in ("HK",):
        return (os.getenv("FIN_SAMPLE_CODE_HK") or "00700").strip()
    if key in ("US",):
        return (os.getenv("FIN_SAMPLE_CODE_US") or "AAPL").strip()
    return (os.getenv("FIN_SAMPLE_CODE") or "601398").strip()


def sample_strategy_key() -> str:
    return (os.getenv("FIN_SAMPLE_STRATEGY_KEY") or "sample-fixed").strip()


def sample_strategy_version() -> str:
    return (os.getenv("FIN_SAMPLE_STRATEGY_VERSION") or "1.0").strip()


# ── 调度 ──────────────────────────────────────────────────────────────────
def schedules_enabled() -> bool:
    """默认开。关掉只用于「手工触发工作流」的排障场景，生产保持开。"""
    return (os.getenv("FIN_SCHEDULES_ENABLED") or "1").strip() not in ("0", "false", "False")


def schedule_timezone() -> str:
    return (os.getenv("FIN_SCHEDULE_TZ") or "Asia/Shanghai").strip()


def activity_workers() -> int:
    """同步 Activity 的线程池大小。并发时点很少（六个时点 × 活跃项目数），8 足够。"""
    try:
        return max(1, int(os.getenv("FIN_WORKER_ACTIVITY_WORKERS") or "8"))
    except ValueError:
        return 8


def contract_timeout_seconds() -> int:
    """策略意图有效期（秒）。过了这个点，意图不再补单（`01方案 §11.2` 信号过期）。"""
    try:
        return int(os.getenv("FIN_INTENT_TTL_SECONDS") or "1800")
    except ValueError:
        return 1800


# ── 复核（复盘）回路（R3 · Q4 已拍板）─────────────────────────────────────
# 触发时点 = 该市场**时段末点** + 这个延迟。**延迟可配**（拍板的就是「可配」）——
# 所以具体分钟数不许硬编码进调度代码；这里只提供默认值。
DEFAULT_REVIEW_DELAY_MINUTES = 30


def review_delay_minutes() -> int:
    """复核工作流的触发延迟（分钟）。**默认 30**（Q4 拍板值）。

    读不到 / 非数字 / 负数 → 用默认 30，并**在日志里写明「用了默认」**
    （不静默、不猜别的数）。「配置写错」不该变成「保护悄悄变了」——
    与 `SCREEN_SCAN_GAP_S` 那条同一个口径（那边缺值一律按 5，不按 0）。

    异常值**不报错退出**：调度是每晚的例行环节，一个拼错的 env 不该让整个 worker 起不来；
    但必须留痕，所以每次读到就用 `logger.warning` 打一行。
    """
    raw = (os.getenv("FIN_REVIEW_DELAY_MINUTES") or "").strip()
    if not raw:
        logger.info("[config] FIN_REVIEW_DELAY_MINUTES 未设置，复核延迟用默认 {} 分钟",
                    DEFAULT_REVIEW_DELAY_MINUTES)
        return DEFAULT_REVIEW_DELAY_MINUTES
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[config] FIN_REVIEW_DELAY_MINUTES={!r} 不是整数，复核延迟用默认 {} 分钟",
                       raw, DEFAULT_REVIEW_DELAY_MINUTES)
        return DEFAULT_REVIEW_DELAY_MINUTES
    if value < 0:
        logger.warning("[config] FIN_REVIEW_DELAY_MINUTES={} 为负数，复核延迟用默认 {} 分钟",
                       value, DEFAULT_REVIEW_DELAY_MINUTES)
        return DEFAULT_REVIEW_DELAY_MINUTES
    return value


# ── 影子验证（R7 · `plan/R7.md` §一.3）─────────────────────────────────────
# `fin-shadow-<market>` 的触发时点 = 该市场**时段末点** + 复核延迟 + 这个影子延迟
# （排在 `fin-review-<market>` 之后，不抢它的时点）。**可配** —— 所以具体分钟数
# 不许硬编码进调度代码，这里只给默认值。
DEFAULT_SHADOW_DELAY_MINUTES = 15


def shadow_delay_minutes() -> int:
    """影子验证相对「时段末点 + 复核延迟」再往后推多少分钟。**默认 15**。

    读不到 / 非数字 / 负数 → 用默认 15，并**在日志里写明用了默认**
    （与 `review_delay_minutes` 同一口径：配置写错不该让保护悄悄变）。
    """
    raw = (os.getenv("FIN_SHADOW_DELAY_MINUTES") or "").strip()
    if not raw:
        return DEFAULT_SHADOW_DELAY_MINUTES
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[config] FIN_SHADOW_DELAY_MINUTES={!r} 不是整数，影子延迟用默认 {} 分钟",
                       raw, DEFAULT_SHADOW_DELAY_MINUTES)
        return DEFAULT_SHADOW_DELAY_MINUTES
    if value < 0:
        logger.warning("[config] FIN_SHADOW_DELAY_MINUTES={} 为负数，影子延迟用默认 {} 分钟",
                       value, DEFAULT_SHADOW_DELAY_MINUTES)
        return DEFAULT_SHADOW_DELAY_MINUTES
    return value


# ── 自动盯盘 · 观察（R13 · `plan/R13.md` §A）───────────────────────────────
# `fin-observe-<market>` 的触发时点 = 该市场**时段末点** + 复核延迟 + 影子延迟 +
# 这个观察延迟（排在 `fin-shadow-<market>` 之后，不抢它的时点）。**可配** ——
# 所以具体分钟数不许硬编码进调度代码，这里只给默认值。
DEFAULT_OBSERVE_DELAY_MINUTES = 30


def observe_delay_minutes() -> int:
    """自动观察相对「时段末点 + 复核延迟 + 影子延迟」再往后推多少分钟。**默认 30**。

    读不到 / 非数字 / 负数 → 用默认 30，并**在日志里写明用了默认**
    （与 `review_delay_minutes` / `shadow_delay_minutes` 同一口径：配置写错不该让保护悄悄变）。
    """
    raw = (os.getenv("FIN_OBSERVE_DELAY_MINUTES") or "").strip()
    if not raw:
        logger.info("[config] FIN_OBSERVE_DELAY_MINUTES 未设置，观察延迟用默认 {} 分钟",
                    DEFAULT_OBSERVE_DELAY_MINUTES)
        return DEFAULT_OBSERVE_DELAY_MINUTES
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[config] FIN_OBSERVE_DELAY_MINUTES={!r} 不是整数，观察延迟用默认 {} 分钟",
                       raw, DEFAULT_OBSERVE_DELAY_MINUTES)
        return DEFAULT_OBSERVE_DELAY_MINUTES
    if value < 0:
        logger.warning("[config] FIN_OBSERVE_DELAY_MINUTES={} 为负数，观察延迟用默认 {} 分钟",
                       value, DEFAULT_OBSERVE_DELAY_MINUTES)
        return DEFAULT_OBSERVE_DELAY_MINUTES
    return value


# ── 自动提案（L01 · `plan/L01.md` §3.1 / §3.2）─────────────────────────────
# `fin-propose-<market>` 的触发时点 = 该市场**时段末点** + 复核延迟 + 这个提案延迟
# （排在 `fin-review-<market>` 产经验**之后**、`fin-shadow-<market>` 验提案**之前**，
# 不抢它们的时点）。**可配** —— 所以具体分钟数不许硬编码进调度代码，这里只给默认值。
#
# 默认 2：复核（时段末点 +30）之后 `fin-review` 立刻开始写经验；提案只依赖「复盘已写下的
# 经验」，所以排在它后面留一点点余量即可。**样本 / 证据不足时提案自然为「不提」**（红线 13），
# 这个延迟不改变判据，只决定「早看还是晚看」。
DEFAULT_PROPOSE_DELAY_MINUTES = 2


def propose_delay_minutes() -> int:
    """自动提案相对「时段末点 + 复核延迟」再往后推多少分钟。**默认 2**。

    读不到 / 非数字 / 负数 → 用默认 2，并**在日志里写明用了默认**
    （与 `review_delay_minutes` / `shadow_delay_minutes` 同一口径：配置写错不该让保护悄悄变）。
    """
    raw = (os.getenv("FIN_PROPOSE_DELAY_MINUTES") or "").strip()
    if not raw:
        return DEFAULT_PROPOSE_DELAY_MINUTES
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[config] FIN_PROPOSE_DELAY_MINUTES={!r} 不是整数，提案延迟用默认 {} 分钟",
                       raw, DEFAULT_PROPOSE_DELAY_MINUTES)
        return DEFAULT_PROPOSE_DELAY_MINUTES
    if value < 0:
        logger.warning("[config] FIN_PROPOSE_DELAY_MINUTES={} 为负数，提案延迟用默认 {} 分钟",
                       value, DEFAULT_PROPOSE_DELAY_MINUTES)
        return DEFAULT_PROPOSE_DELAY_MINUTES
    return value


# ── 采集补齐（L09 · 新闻 / 基本面）─────────────────────────────────────────
# `fin-news-<market>` / `fin-fundamental-<market>` 的触发时点 = 该市场**时段末点** +
# 这个采集延迟（排在收盘之后，当天数据已就绪）。**可配** —— 具体分钟数不许硬编码进
# 调度代码，这里只给默认值。
#
# 默认值取「时段末点之后一段合理余量」：新闻 90 分钟（收盘后媒体稿基本沉淀）、
# 财报 120 分钟（A 股财报是季度数据、akshare 按年逐个请求，晚一点更稳）。
DEFAULT_NEWS_DELAY_MINUTES = 90
DEFAULT_FUNDAMENTAL_DELAY_MINUTES = 120


def _delay_minutes(env: str, default: int) -> int:
    """读一个「延迟分钟数」env：缺省 / 非数字 / 负数 → 默认值，并**在日志里写明用了默认**。

    与 `review_delay_minutes` 等同口径：配置写错不该让保护悄悄变（缺值按默认，不按 0）。
    """
    raw = (os.getenv(env) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("[config] {}={!r} 不是整数，用默认 {} 分钟", env, raw, default)
        return default
    if value < 0:
        logger.warning("[config] {}={} 为负数，用默认 {} 分钟", env, value, default)
        return default
    return value


def news_delay_minutes() -> int:
    """新闻采集相对「时段末点」再往后推多少分钟。**默认 90**（`FIN_NEWS_DELAY_MINUTES`）。"""
    return _delay_minutes("FIN_NEWS_DELAY_MINUTES", DEFAULT_NEWS_DELAY_MINUTES)


def fundamental_delay_minutes() -> int:
    """财报采集相对「时段末点」再往后推多少分钟。**默认 120**（`FIN_FUNDAMENTAL_DELAY_MINUTES`）。"""
    return _delay_minutes("FIN_FUNDAMENTAL_DELAY_MINUTES", DEFAULT_FUNDAMENTAL_DELAY_MINUTES)
