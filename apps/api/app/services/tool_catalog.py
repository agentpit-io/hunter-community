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
    # **必需**依赖 —— 少一个这工具就出不了活
    needs_data: list[str] = field(default_factory=list)
    # **可选**依赖 —— 少了只是内容不全,工具照样能用。
    # 分开是必要的:深度分析要 7 类数据,其中股东/治理/龙虎榜三张上游表未 seed。
    # 不分的话整个工具被判"不可用",连带 19 个 SKILL 全标灰 —— 而它明明照常出报告,
    # 只是那三段写着"数据未 seed"。把"内容不全"说成"用不了"是另一种谎。
    optional_data: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)      # 支持的市场 · 空 = 与市场无关
    slow: bool = False             # 长任务(>30s)· UI 上提示用户要等
    note: str = ""
    # ── 入口(`_22`)────────────────────────────────────────
    # 用户点这个工具时,替他填进输入框的那句话。
    #
    # **它不是把工具变成 SKILL。**工具还是工具、还是模型调用的那个函数,
    # 没有 SKILL.md、不进 skills/ 目录、opencode 不会把它当 skill 加载。
    # 这个字段只解决一件事:**用户点得到它**。
    # 现在 13 个工具里 5 个连点都点不到,就是因为缺这一句话。
    #
    # 空 = 不出现在能力列表里。不是隐藏功能,是"还没想好用户会怎么说" ——
    # 编一句烂模板比留空更糟:用户点了得到答非所问的结果,
    # 然后再也不点这一类了。scripts/check_tool_prompts.py 盯着还剩几个没写。
    prompt_tpl: str = ""
    # 给模型用而不是给人用的工具(如 hunter_user_invoke:得先知道自己接了
    # 什么源才谈得上调用)。即使写了 prompt_tpl 也不进能力列表。
    internal_only: bool = False
    # 合并后的能力列表按**用途**分组,用的就是 SKILL 那 7 个类目 ——
    # 不另立一套。"我想估值"和"这是工具还是 SKILL"是两个问题,
    # 只有前者是用户会问的,所以分组维度必须是用途。
    #
    # 原来的 server 分组(行情与资讯/组合管理/…)保留在 `/catalog/toolbox`,
    # 那是给开发者看"哪个 MCP server 提供了什么"的,两个视角都有用。
    category: str = ""


# ══════════════════════════════════════════════════════════════
# 全部条目取自**容器现读**(2026-08-15),不是照文档抄的:
#   watchlist(4) portfolio(3) uzi(1) hunter_user(2) hunter_cap(3) = 13
# scripts/check_tools.py 负责持续核对这份表与容器是否还一致。
# ══════════════════════════════════════════════════════════════

CATALOG: list[ToolEntry] = [
    # ── 行情与资讯 · watchlist server ──────────────────────────
    ToolEntry("watchlist_stock_quickview", "行情速查", "watchlist", ToolOrigin.IMAGE,
              "实时行情富卡片:价格、涨跌、成交、盘口",
              needs_data=["a.quote"], markets=["a"],
              prompt_tpl="查 {股票} 最新股价",
              category="快速判断"),
    ToolEntry("watchlist_stock_news", "个股新闻", "watchlist", ToolOrigin.IMAGE,
              "拉取该股最近的新闻条目",
              needs_data=["a.news"], markets=["a"],
              prompt_tpl="看 {股票} 最近的新闻",
              category="事件与筛选"),
    ToolEntry("watchlist_watchlist_digest", "自选股速览", "watchlist", ToolOrigin.IMAGE,
              "一次拿到自选股全部最新行情与异动",
              needs_data=["a.quote"], markets=["a"],
              prompt_tpl="我的自选股今天怎么样",
              category="组合级"),
    ToolEntry("watchlist_watchlist_add", "加自选股", "watchlist", ToolOrigin.IMAGE,
              "把一只股票加进自选列表",
              needs_data=[], note="纯本地写库 · 不依赖任何数据源",
              prompt_tpl="把 {股票} 加入我的自选",
              category="组合级"),
    # 2026-08-18 补登记。MCP 侧(watchlist_mcp.py:89)与 SKILL
    # (skills/watchlist_rank/)都加了,但这份注册表漏了 —— 后果不是"少一行":
    # SKILL 的 needs_tools 指向一个"未注册"的工具,于是它在能力库里被判成
    # **broken**(灰的、点不了),而工具本身其实是好的。
    # scripts/check_tools.py 抓到了这处漂移。
    # 依赖读 watchlist_rank_agent.py:366/368 核实:get_quote + get_kline_with_fallback
    ToolEntry("watchlist_watchlist_rank", "自选股排序", "watchlist", ToolOrigin.IMAGE,
              "N 只自选 × 3M/6M/1Y/3Y 横向打分排序 · 一次调用出表",
              needs_data=["a.quote", "a.kline"], markets=["a"],
              note="替代「逐只跑深度分析」—— 那样串行 5+ 分钟且不能横向对比。"
                   "1Y/3Y 因长期基本面字段未接入,只出定性标签并显式声明方法学",
              prompt_tpl="把我的自选股按 3 个月 / 6 个月 / 1 年 / 3 年前景从好到坏排一下",
              category="组合级"),

    # ── 组合级 · portfolio server ─────────────────────────────
    ToolEntry("portfolio_portfolio_rebalance", "组合再平衡", "portfolio", ToolOrigin.IMAGE,
              "按目标权重算调仓清单与换手成本",
              needs_data=["a.quote"], markets=["a"],
              prompt_tpl="按目标权重帮我算一份调仓清单",
              category="组合级"),
    ToolEntry("portfolio_portfolio_stress", "组合压力测试", "portfolio", ToolOrigin.IMAGE,
              "情景冲击下的组合回撤模拟",
              needs_data=["a.quote", "a.kline"], markets=["a"],
              prompt_tpl="模拟一次大跌,看我的组合会回撤多少",
              category="组合级"),
    ToolEntry("portfolio_update_risk_profile", "更新风险画像", "portfolio", ToolOrigin.IMAGE,
              "记录用户风险偏好,后续建议据此调整",
              needs_data=[], note="纯本地写库",
              prompt_tpl="记录我的风险偏好:我能接受的最大回撤是 {比例}",
              category="组合级"),

    # ── 深度分析 · uzi server ─────────────────────────────────
    ToolEntry("uzi_stock_deep_analysis", "深度分析", "uzi", ToolOrigin.IMAGE,
              "行情+K线+财务+龙虎榜+股东+治理+新闻 七类数据合成结构化研报",
              # 前四类缺了就没什么可分析的;后三类缺了只是少三段
              needs_data=["a.quote", "a.kline", "a.financial", "a.news"],
              optional_data=["a.lhb", "a.fund_holders", "a.governance"],
              markets=["a", "hk", "us"], slow=True,
              note="薄代理转发到 /api/internal/uzi/* · 5-10 秒 · "
                   "股东/治理表上游未 seed,那两段会显示'数据未 seed'",
              prompt_tpl="对 {股票} 做一次全面分析",
              category="综合分析"),

    # ── 平台自有能力 · hunter_cap server(我们在 Step 3 加的)──
    ToolEntry("hunter_cap_kpred", "K线预测", "hunter_cap", ToolOrigin.PLATFORM,
              "Kronos 模型预测未来 N 日开高低收与涨跌幅",
              needs_data=["global.kronos"], markets=["a"], slow=True,
              note="经 hunter 网关 · 同一把 key",
              prompt_tpl="预测 {股票} 未来 5 日走势",
              category="快速判断"),
    ToolEntry("hunter_cap_truesource_brief", "情报简报", "hunter_cap", ToolOrigin.PLATFORM,
              "多标的情报摘要与预警级别",
              needs_data=["global.truesource_brief"],
              prompt_tpl="给我 {股票} 的情报摘要与预警",
              category="事件与筛选"),
    ToolEntry("hunter_cap_truesource_scout", "主动情报采集", "hunter_cap", ToolOrigin.PLATFORM,
              "针对单只标的现场搜集情报(Gemini 搜索,耗时较长)",
              needs_data=["global.truesource_scout"], slow=True,
              prompt_tpl="现场搜集 {股票} 的最新情报",
              category="事件与筛选"),

    # ── 从 GitHub 导入 SKILL(`_23`)· hunter_cap server ───────
    # 四个工具里**只有 repo_open 露给用户**:它是入口,用户说
    # 「请按照 <地址> 安装」就是调它。其余三个是模型自己编排时用的中间步骤,
    # 用户不会主动点"读一个文件"或"暂存一个 SKILL"。
    ToolEntry("hunter_cap_skill_repo_open", "导入 SKILL", "hunter_cap", ToolOrigin.PLATFORM,
              "从 GitHub 装别人写的分析方法 · 按作者自己写的说明装",
              needs_data=[],
              note="读仓库的 README 与 .opencode/INSTALL.md,按作者的说明装。"
                   "装什么由模型编排,**落盘前需要你确认一次**",
              # 措辞刻意贴近 UZI README 里那句「请按照 <链接> 安装并分析…」——
              # 用户很可能直接从别人的 README 复制那句话过来,两边对得上不会出错
              prompt_tpl="请按照 {GitHub地址} 安装这个 SKILL",
              category="接入与自查"),
    ToolEntry("hunter_cap_skill_repo_read", "读仓库文件", "hunter_cap", ToolOrigin.PLATFORM,
              "读导入过程中指定仓库里的某个文件",
              needs_data=[], internal_only=True, category="接入与自查",
              note="导入 SKILL 的中间步骤 · 模型编排时用"),
    ToolEntry("hunter_cap_skill_stage", "暂存 SKILL", "hunter_cap", ToolOrigin.PLATFORM,
              "把一个待安装的 SKILL 放进暂存区(不写磁盘)",
              needs_data=[], internal_only=True, category="接入与自查",
              note="导入 SKILL 的中间步骤 · 真正落盘由用户在确认卡上点"),
    ToolEntry("hunter_cap_skill_staged", "查看暂存", "hunter_cap", ToolOrigin.PLATFORM,
              "查这个会话已经暂存了哪些 SKILL",
              needs_data=[], internal_only=True, category="接入与自查",
              note="导入 SKILL 的中间步骤 · 模型自查用"),

    # ── 用户自接数据源的通道 · hunter_user server ─────────────
    ToolEntry("hunter_user_list_my_sources", "列出我接的数据源", "hunter_user", ToolOrigin.IMAGE,
              "查看用户自己在设置里接入的第三方 MCP/API",
              needs_data=[], note="与平台 key 无关 · 用户自建",
              prompt_tpl="我接了哪些自己的数据源",
              category="接入与自查"),
    ToolEntry("hunter_user_invoke", "调用我接的数据源", "hunter_user", ToolOrigin.IMAGE,
              "调用用户自建数据源的某个端点",
              needs_data=[], note="与平台 key 无关 · 用户自建",
              internal_only=True,
              category="接入与自查"),
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

    def _scan(keys: list[str]):
        blocked, need_key, unknown = [], [], []
        for k in keys:
            src = sc.get(k)
            if src is None:
                unknown.append(k)     # 注册表漂移 —— check_tools.py 会报
                continue
            st = sc.status_of(src)
            if st == "unavailable":
                blocked.append(k)
            elif st == "need_key":
                need_key.append(k)
        return blocked, need_key, unknown

    blocked, missing_key, unknown_src = _scan(t.needs_data)
    opt_blocked, opt_need_key, _ = _scan(t.optional_data)

    if blocked:
        state = "unavailable"
    elif missing_key:
        state = "need_key"
    elif opt_blocked or opt_need_key:
        # 能用,但内容不全 —— 这一档必须存在,否则只能在"完全可用"和"完全不可用"
        # 之间二选一,两个都不是事实
        state = "partial"
    else:
        state = "ready"
    return {"state": state, "blocked_by": blocked, "need_key_for": missing_key,
            "degraded_by": opt_blocked + opt_need_key, "unknown_sources": unknown_src}


def to_dict(t: ToolEntry) -> dict:
    d = asdict(t)
    d["origin"] = t.origin.value
    d["origin_label"] = ORIGIN_LABEL[t.origin]
    d["server_label"] = SERVER_LABEL.get(t.server, t.server)
    st = status_of(t)
    d["status"] = st["state"]
    d["blocked_by"] = st["blocked_by"]
    d["need_key_for"] = st["need_key_for"]
    d["degraded_by"] = st["degraded_by"]
    # 能不能出现在「能力」列表里(`_22` §3)。
    # 前端不必自己判 prompt_tpl 是否为空 + internal_only —— 那个判断
    # 一旦抄到第二处就会漂,判据只留在这一处
    d["pickable"] = bool(t.prompt_tpl) and not t.internal_only
    return d


def pickable() -> list[ToolEntry]:
    """能出现在能力列表里的工具 —— 有模板、且不是给模型用的。"""
    return [t for t in CATALOG if t.prompt_tpl and not t.internal_only]


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
            # partial 计入 ready —— 它是"能用但内容不全",不是"用不了"
            "ready": sum(1 for i in items if i["status"] in ("ready", "partial")),
            "tools": items,
        })
    return out


# ══════════════════════════════════════════════════════════════
# 用户自接的 MCP
#
# 用户在 /mcp-config 里接的第三方 MCP,**模型早就能调到了**
# (走 hunter_user_invoke 那条通道),但侧栏「工具箱」里一直看不见 ——
# 那份表只列了 13 个静态工具。用户接完看不到,会以为没生效。
#
# 这里把 user_mcp_registrations 读出来合进同一个列表,标 origin=user。
# 侧栏那个「你接的」标签是早就留好的。
# ══════════════════════════════════════════════════════════════

def user_tools(user_id: str | None = None) -> list[dict]:
    """读用户自接的 MCP,转成与内置工具同一个形状。

    **失败一律返回空列表**:工具箱面板挂掉不该影响聊天,
    而且用户没接过任何 MCP 是最常见的情况,不是异常。
    """
    if not user_id:
        return []
    try:
        from app.services.database import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT r.id, r.name, r.slug, r.endpoint, r.enabled,
                   r.last_ok_at, r.last_err, r.call_count, r.error_count,
                   c.tools
            FROM user_mcp_registrations r
            LEFT JOIN user_mcp_tools_cache c ON c.mcp_id = r.id
            WHERE r.user_id = %s ORDER BY r.created_at DESC
        """, (str(user_id),))
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.debug("[tool_catalog] 读用户 MCP 失败(当作没有): {}", e)
        return []

    out: list[dict] = []
    for (rid, name, slug, endpoint, enabled, last_ok, last_err,
         calls, errors, tools) in rows:
        # tools 缓存是「刷新」时抓的。没刷过就只知道这个 MCP 存在、
        # 不知道它提供哪些工具 —— 如实说,别编一个数字
        names = [t.get("name") for t in (tools or []) if isinstance(t, dict)]
        out.append({
            "key": f"user:{slug}",
            "name": name,
            "server": "user_mcp",
            "server_label": SERVER_LABEL["hunter_user"],
            "origin": ToolOrigin.USER.value,
            "origin_label": ORIGIN_LABEL[ToolOrigin.USER],
            "summary": (f"{len(names)} 个工具:" + "、".join(names[:4])
                        if names else "未刷新过工具清单 —— 到「我的工具」里点一次刷新"),
            "needs_data": [], "optional_data": [], "markets": [],
            "slow": False,
            "note": f"{endpoint} · 调用 {calls} 次 · 失败 {errors} 次"
                    + (f" · 最近错误:{last_err[:60]}" if last_err else ""),
            # 用户自接的**不参与依赖计算** —— 它连的是谁家的服务我们不知道,
            # 拿我们的数据源状态去判断它可不可用是错的
            "status": "ready" if enabled else "unavailable",
            "blocked_by": [], "need_key_for": [], "degraded_by": [],
            "unavailable_reason": "" if enabled else "已在「我的工具」里停用",
            "mcp_id": rid,
        })
    return out


def grouped_with_user(user_id: str | None = None) -> list[dict]:
    """内置分组 + 用户自接的那一组。"""
    groups = grouped()
    mine = user_tools(user_id)
    if mine:
        # 用户自己的放最后 —— 前面几组是"我们提供的",这一组是"你加的",
        # 混在一起用户分不清哪些是自己接的
        groups.append({
            "server": "user_mcp",
            "label": "你接的工具",
            "total": len(mine),
            "ready": sum(1 for t in mine if t["status"] == "ready"),
            "tools": mine,
        })
    return groups
