"""工具箱注册表 —— 三层能力模型里的**工具箱层**。

`_14` §5 / §6 Step C。用户原话:「mcp 和 tools 算一类」—— 所以这里不区分
"平台自有工具"和"MCP 工具",它们对用户是同一种东西:**模型能直接调的能力**。
区别只体现在 `origin` 字段上,供 UI 标个来源。

**为什么要有它**:模型手上有 13 个工具,但侧栏上一个都看不到 —— 用户只看到
29 张 SKILL 卡,不知道底下靠什么在跑。更要命的是,工具能不能用取决于数据源
通不通(比如 `uzi_stock_deep_analysis` 要 finance-data 的 7 类数据),
而这个依赖关系过去**根本没写下来**,只存在于代码调用链里。

**`needs_data` 是这份表的核心**:它把工具箱层和数据源层连起来,让
"当前这个工具能不能用"变成可计算的,而不是等用户点了才发现不行。

命名规则(opencode 的):完整工具名 = `{MCP server 名}_{工具名}`。
所以 uzi server 里的 `stock_deep_analysis` 对模型呈现为 `uzi_stock_deep_analysis`。
这份表里的 `key` 一律用**完整名**,因为 SKILL 的 `needs_tools` 引用的就是它。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class ToolOrigin(str, Enum):
    IMAGE = "image"        # opencode 镜像自带 · 随镜像发布
    PLATFORM = "platform"  # 我们在 community 里加的
    USER = "user"          # 用户自己接的 MCP(运行时动态,不在这份静态表里)


ORIGIN_LABEL = {
    ToolOrigin.IMAGE: "内置",
    ToolOrigin.PLATFORM: "平台",
    ToolOrigin.USER: "你接的",
}


@dataclass
class ToolEntry:
    key: str                       # 完整工具名 · SKILL 的 needs_tools 引用这个
    name: str                      # 中文显示名
    server: str                    # 所属 MCP server
    origin: ToolOrigin
    summary: str                   # 一句话:它干什么
    needs_data: list[str] = field(default_factory=list)   # 依赖的 source_catalog key
    markets: list[str] = field(default_factory=list)      # 支持的市场 · 空 = 与市场无关
    slow: bool = False             # 长任务(>30s)· UI 上提示用户要等
    note: str = ""


# ══════════════════════════════════════════════════════════════
# 全部条目取自**容器现读**(2026-08-15),不是照文档抄的:
#   watchlist(4) portfolio(3) uzi(1) hunter_user(2) hunter_cap(3) = 13
# scripts/check_tools.py 负责持续核对这份表与容器是否还一致。
# ══════════════════════════════════════════════════════════════

CATALOG: list[ToolEntry] = [
    # ── 行情与资讯 · watchlist server ──────────────────────────
    ToolEntry("watchlist_stock_quickview", "行情速查", "watchlist", ToolOrigin.IMAGE,
              "实时行情富卡片:价格、涨跌、成交、盘口",
              needs_data=["a.quote"], markets=["a"]),
    ToolEntry("watchlist_stock_news", "个股新闻", "watchlist", ToolOrigin.IMAGE,
              "拉取该股最近的新闻条目",
              needs_data=["a.news"], markets=["a"]),
    ToolEntry("watchlist_watchlist_digest", "自选股速览", "watchlist", ToolOrigin.IMAGE,
              "一次拿到自选股全部最新行情与异动",
              needs_data=["a.quote"], markets=["a"]),
    ToolEntry("watchlist_watchlist_add", "加自选股", "watchlist", ToolOrigin.IMAGE,
              "把一只股票加进自选列表",
              needs_data=[], note="纯本地写库 · 不依赖任何数据源"),

    # ── 组合级 · portfolio server ─────────────────────────────
    ToolEntry("portfolio_portfolio_rebalance", "组合再平衡", "portfolio", ToolOrigin.IMAGE,
              "按目标权重算调仓清单与换手成本",
              needs_data=["a.quote"], markets=["a"]),
    ToolEntry("portfolio_portfolio_stress", "组合压力测试", "portfolio", ToolOrigin.IMAGE,
              "情景冲击下的组合回撤模拟",
              needs_data=["a.quote", "a.kline"], markets=["a"]),
    ToolEntry("portfolio_update_risk_profile", "更新风险画像", "portfolio", ToolOrigin.IMAGE,
              "记录用户风险偏好,后续建议据此调整",
              needs_data=[], note="纯本地写库"),

    # ── 深度分析 · uzi server ─────────────────────────────────
    ToolEntry("uzi_stock_deep_analysis", "深度分析", "uzi", ToolOrigin.IMAGE,
              "行情+K线+财务+龙虎榜+股东+治理+新闻 七类数据合成结构化研报",
              needs_data=["a.quote", "a.kline", "a.financial", "a.lhb",
                          "a.fund_holders", "a.governance", "a.news"],
              markets=["a", "hk", "us"], slow=True,
              note="薄代理转发到 /api/internal/uzi/* · 5-10 秒 · "
                   "龙虎榜/股东/治理三张表未 seed 时会显示'数据未 seed'"),

    # ── 平台自有能力 · hunter_cap server(我们在 Step 3 加的)──
    ToolEntry("hunter_cap_kpred", "K线预测", "hunter_cap", ToolOrigin.PLATFORM,
              "Kronos 模型预测未来 N 日开高低收与涨跌幅",
              needs_data=["global.kronos"], markets=["a"], slow=True,
              note="经 hunter 网关 · 同一把 key"),
    ToolEntry("hunter_cap_truesource_brief", "情报简报", "hunter_cap", ToolOrigin.PLATFORM,
              "多标的情报摘要与预警级别",
              needs_data=["global.truesource_brief"]),
    ToolEntry("hunter_cap_truesource_scout", "主动情报采集", "hunter_cap", ToolOrigin.PLATFORM,
              "针对单只标的现场搜集情报(Gemini 搜索,耗时较长)",
              needs_data=["global.truesource_scout"], slow=True),

    # ── 用户自接数据源的通道 · hunter_user server ─────────────
    ToolEntry("hunter_user_list_my_sources", "列出我接的数据源", "hunter_user", ToolOrigin.IMAGE,
              "查看用户自己在设置里接入的第三方 MCP/API",
              needs_data=[], note="与平台 key 无关 · 用户自建"),
    ToolEntry("hunter_user_invoke", "调用我接的数据源", "hunter_user", ToolOrigin.IMAGE,
              "调用用户自建数据源的某个端点",
              needs_data=[], note="与平台 key 无关 · 用户自建"),
]

_BY_KEY = {t.key: t for t in CATALOG}

# UI 分组 · 按 server 分,因为 server 本身就是按能力域切的
SERVER_LABEL = {
    "watchlist": "行情与资讯",
    "portfolio": "组合管理",
    "uzi": "深度分析",
    "hunter_cap": "平台能力",
    "hunter_user": "你自接的数据源",
}
SERVER_ORDER = ["watchlist", "uzi", "hunter_cap", "portfolio", "hunter_user"]


# ── 运行时状态 ────────────────────────────────────────────────

def status_of(t: ToolEntry) -> dict:
    """算这个工具**现在**能不能用 —— 靠它声明的 needs_data 去问数据源层。

    三种结果对应三种用户动作,跟数据源层保持同一套语义:
      ready       —— 依赖的数据源都就绪
      need_key    —— 有依赖缺 key,去申请一把就能用
      unavailable —— 有依赖在开源版根本没通道,申请 key 也没用
    没有任何依赖的工具(纯本地写库)恒为 ready。
    """
    from app.services import source_catalog as sc

    missing_key, blocked, unknown_src = [], [], []
    for k in t.needs_data:
        src = sc.get(k)
        if src is None:
            unknown_src.append(k)     # 注册表漂移 —— check_tools.py 会报
            continue
        st = sc.status_of(src)
        if st == "unavailable":
            blocked.append(k)
        elif st == "need_key":
            missing_key.append(k)

    if blocked:
        state = "unavailable"
    elif missing_key:
        state = "need_key"
    else:
        state = "ready"
    return {"state": state, "blocked_by": blocked, "need_key_for": missing_key,
            "unknown_sources": unknown_src}


def to_dict(t: ToolEntry) -> dict:
    d = asdict(t)
    d["origin"] = t.origin.value
    d["origin_label"] = ORIGIN_LABEL[t.origin]
    d["server_label"] = SERVER_LABEL.get(t.server, t.server)
    st = status_of(t)
    d["status"] = st["state"]
    d["blocked_by"] = st["blocked_by"]
    d["need_key_for"] = st["need_key_for"]
    return d


def get(key: str) -> ToolEntry | None:
    return _BY_KEY.get(key)


def all_tools() -> list[ToolEntry]:
    return list(CATALOG)


def grouped() -> list[dict]:
    """按 MCP server 分组 —— `/api/catalog/toolbox` 的返回结构。"""
    out = []
    seen = set()
    for srv in SERVER_ORDER + sorted({t.server for t in CATALOG}):
        if srv in seen:
            continue
        seen.add(srv)
        items = [to_dict(t) for t in CATALOG if t.server == srv]
        if not items:
            continue
        out.append({
            "server": srv,
            "label": SERVER_LABEL.get(srv, srv),
            "total": len(items),
            "ready": sum(1 for i in items if i["status"] == "ready"),
            "tools": items,
        })
    return out
