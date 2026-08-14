"""/chat 能力面板 —— 让用户知道"这东西能干什么"。

为什么不直接把 MCP 工具名列给用户看:
    技术上我们有 5 个 MCP 工具(truesource_get_quote / kronos_kronos_forecast …),
    但用户看到 `truesource_get_quote` 既看不懂、也不知道能拿它干嘛。
    所以这里展示的是**能力卡片** —— 用用户的语言描述用途,点一下把提问模板填进输入框。
    底层是 MCP 工具、opencode SKILL、还是一段提示词,用户不需要知道。

三类数据合成一个列表返回:
    ① 内置能力(BUILTINS)—— 我们维护,所有人一样
    ② 用户对内置能力的覆盖 —— 改名/改模板/关掉(builtin_key 非空)
    ③ 用户自建能力 —— builtin_key 为空
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()

MAX_CUSTOM = 20          # 每人自建上限,防滥用
MAX_TPL_LEN = 500        # 模板长度上限

# 内置能力。key 一旦发布不要改(用户的覆盖记录靠它关联)。
#
# ⚠️ tools 字段只能填**本部署实际存在**的 MCP 工具名。开源版镜像当前有 10 个:
#   watchlist_stock_quickview / _stock_news / _watchlist_digest / _watchlist_add
#   portfolio_portfolio_rebalance / _portfolio_stress / _update_risk_profile
#   uzi_stock_deep_analysis
#   hunter_user_list_my_sources / _invoke
# 2026-08-14 清理过一次:曾有 6 个 SKILL 写着 truesource_* / kronos_* / debate_*,
# 那是**生产环境**的命名被原样抄了过来,开源版根本没有 —— 模型照着调必然失败。
# 校验脚本见 scripts/check_skill_tools.py。
# {股票} 是占位符,前端填入后光标停在这里。
#
# 2026-08-10 扩展：加 brand / source_url / long_desc / tools 4 字段
# 前端 /skills/[key] 详情页读它渲染；SkillManager 列表也可选择显示 brand。
# 有独立品牌来源(Kronos/UZI/TradingAgents)的显示名前缀带品牌 " · " 分隔。
BUILTINS: list[dict] = [
    {
        "key": "quote",
        "icon": "📊",
        "name": "行情速查",
        "brand": "finance-data",
        "source_url": "https://finance-data.agentpit.io",
        "prompt_tpl": "查 {股票} 最新股价",
        "hint": "实时开高低收 · 成交量 · 五档盘口",
        "long_desc": "调用 finance-data 数据源获取 A 股/港股实时行情，包含开高低收、涨跌幅、成交量与五档盘口。数据延迟 <3 秒，交易时段每 30 秒滚动更新。适合决策前快速核对当前价位。",
        "tools": ["watchlist_stock_quickview"],
        "category": "快速判断",
    },
    {
        "key": "forecast",
        "icon": "📈",
        "name": "Kronos · 走势预测",
        "brand": "Kronos",
        "source_url": "https://github.com/shiyu-coder/Kronos",
        "prompt_tpl": "用 Kronos 预测 {股票} 未来 5 天走势",
        "hint": "清华 Kronos 金融时序大模型",
        "long_desc": "清华大学开源的金融时序 Transformer 模型，直接对 K 线序列建模，输出未来 N 天开高低收预测与置信区间。擅长捕捉短期动量与技术图形的续接概率，作辅助判断而非交易信号。",
        "tools": [],
        "category": "快速判断",
    },
    {
        "key": "report",
        "icon": "📋",
        "name": "财报要点",
        "brand": "finance-data",
        "source_url": "https://finance-data.agentpit.io",
        "prompt_tpl": "{股票} 最新财报要点,3 条",
        "hint": "利润表 · 资产负债表 · 现金流量表",
        "long_desc": "拉取最新披露的三大报表关键指标（营收/净利润/毛利率/资产负债率/经营现金流），LLM 提炼 3 条要点。适合快速了解近期基本面变化，不做深度财务模型。",
        "tools": ["uzi_stock_deep_analysis"],
        "category": "投研报告",
    },
    {
        "key": "compare",
        "icon": "🔍",
        "name": "同业对比",
        "brand": "finance-data",
        "source_url": "https://finance-data.agentpit.io",
        "prompt_tpl": "对比 {股票A} 和 {股票B} 的最新股价与 30 日振幅",
        "hint": "多只股票横向比较",
        "long_desc": "多只股票横向比较：最新价、涨跌幅、成交量、30 日振幅、市值。适合做同业挑选或替代品判断。",
        "tools": ["watchlist_stock_quickview"],
        "category": "快速判断",
    },
    {
        "key": "thesis",
        "icon": "📝",
        "name": "论点起草",
        "brand": "Hunter 内置",
        "source_url": "",
        "prompt_tpl": "帮我起草 {股票} 的投资论点,并列出 3 条关键假设",
        "hint": "结构化投资逻辑 · 可跟踪验证",
        "long_desc": "纯 LLM 结构化输出投资论点：核心逻辑 + 3 条关键假设 + 反证信号。方便日后跟踪验证假设是否成立，形成可复盘的决策记录。",
        "tools": [],
        "category": "投研报告",
    },
    {
        "key": "debate",
        "icon": "⚖️",
        "name": "TradingAgents · 多专家辩论",
        "brand": "TradingAgents",
        "source_url": "https://github.com/TauricResearch/TradingAgents",
        "prompt_tpl": "对 {股票} 做多空辩论 · 给出买卖决策",
        "hint": "6 位分析师 · 2 轮辩论 · 60-90s 出深度报告",
        "long_desc": "TauricResearch 开源的多智能体金融辩论框架，模拟 6 位分析师从技术面/基本面/资金面/新闻/风险等角度多轮对话，汇聚成买/卖/持决策与置信度。适合争议股或大仓位决策前作压力测试。",
        "tools": [],
        "category": "综合分析",
    },
    # ─── UZI 深度分析（Sprint 3 P2 · Phase 1 MVP · 2026-08-10 补入能力管理） ───
    {
        "key": "stock_deep_analysis",
        "icon": "🎯",
        "name": "UZI · 深度分析",
        "brand": "UZI",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "深度分析 {股票}",
        "hint": "22 维度融合 · 8 数据源 · 约 7s 出 LITE 报告",
        "long_desc": "UZI-Skill 提供的综合深度分析：融合行情/K线/财务/龙虎榜/十大股东/治理/新闻/研报 8 大数据源共 22 维度，LLM 一次性合成多空核心观点 + 技术面 + 基本面 + 风险提示的结构化报告。适合决策前的全景扫查。",
        "tools": ["uzi_stock_deep_analysis"],
        "category": "综合分析",
    },
    # ─── 自选股整合（P0-P2）· 4 张新 SKILL 卡 ───
    {
        "key": "stock_news",
        "icon": "📰",
        "name": "关键新闻",
        "brand": "finance-data",
        "source_url": "https://finance-data.agentpit.io",
        "prompt_tpl": "{股票} 最近有什么关键新闻?",
        "hint": "5 条精选 · 每条 AI 影响短评（利好/利空/中性/强利好）",
        "long_desc": "抓取近 7 天该股相关新闻，去重后 LLM 精选 5 条并逐条打影响标签（利好/利空/中性/强利好）。适合快速摸清市场情绪与近期催化。",
        "tools": ["watchlist_stock_news"],
        "category": "事件与筛选",
    },
    {
        "key": "watchlist_daily",
        "icon": "🌅",
        "name": "自选股日报",
        "brand": "Hunter 内置",
        "source_url": "",
        "prompt_tpl": "我的自选股今天谁最强、谁最弱?",
        "hint": "自选涨跌排序 + Top 3 AI 归因 · 前置：先加自选",
        "long_desc": "读取你的自选股清单，按当日涨跌幅排序，对涨/跌 Top 3 用 LLM 做归因（板块/消息/资金面）。适合每日盘后 3 分钟摸清自选整体状况。前置：需先在自选页添加股票。",
        "tools": ["watchlist_watchlist_digest"],
        "category": "组合级",
    },
    {
        "key": "portfolio_advice",
        "icon": "🎯",
        "name": "组合级建议",
        "brand": "Hunter 内置",
        "source_url": "",
        "prompt_tpl": "帮我看看我的持仓怎么调仓",
        "hint": "当前 vs 目标权重 + 加/减仓动作 · 前置：/portfolio 录 shares+cost",
        "long_desc": "对比当前持仓权重与目标配置（按风险画像动态计算），给出具体加/减仓动作与量级。前置：需在 /portfolio 页录入 shares + cost。",
        "tools": ["portfolio_portfolio_rebalance"],
        "category": "组合级",
    },
    {
        "key": "portfolio_stress",
        "icon": "🌊",
        "name": "情景模拟",
        "brand": "Hunter 内置",
        "source_url": "",
        "prompt_tpl": "如果 {股票} 跌 20% · 我组合会亏多少?",
        "hint": "含板块联动 + 减半建议 · 前置：/portfolio 录 shares+cost",
        "long_desc": "压力测试：假设某股跌 X%，通过板块联动系数估算组合整体回撤，给出可选的减仓/对冲建议。前置：需在 /portfolio 页录入 shares + cost。",
        "tools": ["portfolio_portfolio_stress"],
        "category": "组合级",
    },
    # ─── 持仓建议 Sprint 1 · 风险画像 ───
    {
        "key": "risk_profile",
        "icon": "🎚",
        "name": "风险画像",
        "brand": "Hunter 内置",
        "source_url": "",
        "prompt_tpl": "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%",
        "hint": "读/改风险偏好 + 现金 + 单票/HK 上限 · 供组合建议自动应用",
        "long_desc": "用自然语言更新你的风险偏好、可投现金、单票上限、HK 敞口上限。参数存 user_memory，供后续组合建议自动应用。",
        "tools": ["portfolio_update_risk_profile"],
        "category": "组合级",
    },
    # ─── UZI-Skill 子命令扩展（2026-08-10 · v3.8 · 17 条）───
    # 所有子命令都以 /stock-deep-analyzer: 前缀交给 UZI worker · prefix 不可省
    # 底层实际调用同一个 uzi_stock_deep_analysis tool · 由 UZI worker 内部路由
    {
        "key": "uzi_dcf",
        "icon": "💰",
        "name": "UZI · DCF 估值",
        "brand": "UZI",
        "category": "估值建模",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:dcf {股票}",
        "hint": "WACC + 5×5 敏感性表",
        "long_desc": "对 {股票} 做 DCF 现金流折现估值：估算 WACC、构建 10 年预测期 + 永续期模型，输出内在价值、隐含 upside/downside，附 5×5 敏感性表（永续增速 × WACC）。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_comps",
        "icon": "⚖️",
        "name": "UZI · 同行对标",
        "brand": "UZI",
        "category": "估值建模",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:comps {股票}",
        "hint": "PE/PB 分位分析",
        "long_desc": "选取可比公司，按 PE/PB/PS/EV-EBITDA 多倍数横向对比，给出 {股票} 在同业中的分位数与合理估值区间。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_lbo",
        "icon": "🏦",
        "name": "UZI · LBO 测试",
        "brand": "UZI",
        "category": "估值建模",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:lbo {股票}",
        "hint": "PE 买方能赚多少 IRR",
        "long_desc": "杠杆收购 (LBO) 情景测试：模拟 PE 买方在合理杠杆结构下的进入/退出，输出 5 年期 IRR 与 MoM，判断该标的对 PE 买方的吸引力。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_segmental_model",
        "icon": "🧩",
        "name": "UZI · 分部建模",
        "brand": "UZI",
        "category": "估值建模",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:segmental-model {股票}",
        "hint": "分业务线建模 · 波动分析",
        "long_desc": "按公司业务分部（主营/其他）分别建收入预测模型，并做各分部对整体估值的贡献与波动敏感度分析。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_model_update",
        "icon": "🔄",
        "name": "UZI · 模型增量更新",
        "brand": "UZI",
        "category": "估值建模",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:model-update {股票}",
        "hint": "v3.8 · 新财报/指引 delta → DCF/thesis 影响",
        "long_desc": "v3.8 新增。基于最新披露的财报或管理层指引，识别关键假设的 delta，快速更新 DCF 与投资论点，产出增量影响报告。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_initiate",
        "icon": "📖",
        "name": "UZI · 机构首次覆盖",
        "brand": "UZI",
        "category": "投研报告",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:initiate {股票}",
        "hint": "JPM/GS 格式 · 完整首覆",
        "long_desc": "按 JPM/GS 卖方研究员标准格式，产出机构首次覆盖报告：公司概况、投资亮点、估值模型、风险因素、评级/目标价。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_ic_memo",
        "icon": "📄",
        "name": "UZI · 投委会备忘录",
        "brand": "UZI",
        "category": "投研报告",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:ic-memo {股票}",
        "hint": "三情景回报 · IC 汇报格式",
        "long_desc": "投资委员会 (IC) 汇报格式的备忘录：base/bull/bear 三情景对应回报分布、概率、关键假设与风险预案。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_thesis",
        "icon": "🧭",
        "name": "UZI · 投资逻辑追踪",
        "brand": "UZI",
        "category": "投研报告",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:thesis {股票}",
        "hint": "5 支柱监控 · 假设跟踪",
        "long_desc": "为 {股票} 建立 5 支柱投资论点框架（增长/护城河/管理层/财务/催化），后续每次调用做支柱状态更新与假设漂移预警。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_earnings",
        "icon": "💹",
        "name": "UZI · 财报解读",
        "brand": "UZI",
        "category": "投研报告",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:earnings {股票}",
        "hint": "beat/miss 检测 · 逐条对齐",
        "long_desc": "深度解读最新财报：对齐一致预期做 beat/miss 检测，拆解 EPS 驱动因子，管理层指引质量评分，一图看清季度表现。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_catalysts",
        "icon": "🗓️",
        "name": "UZI · 催化剂日历",
        "brand": "UZI",
        "category": "事件与筛选",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:catalysts {股票}",
        "hint": "未来 60 天关键事件",
        "long_desc": "整理未来 60 天该股相关的关键事件日历：财报、指引、政策窗口、行业会议、股东大会、解禁、除权。附对股价的潜在影响判断。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_screen",
        "icon": "🧮",
        "name": "UZI · 量化筛选",
        "brand": "UZI",
        "category": "事件与筛选",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:screen {股票}",
        "hint": "5 套筛选 · value/growth/quality",
        "long_desc": "5 套量化筛选（价值/成长/质量/动量/低波），报告 {股票} 在每套筛选下的排名与被选/落选原因，快速定位股票的风格属性。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_dd",
        "icon": "🔎",
        "name": "UZI · 尽调清单",
        "brand": "UZI",
        "category": "尽调风控",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:dd {股票}",
        "hint": "5 工作流 · 21 项检查",
        "long_desc": "完整尽调清单：5 大工作流（财务/法律/运营/管理层/行业）覆盖 21 项检查点，逐条给出结论 + 证据链接 + 风险等级。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_scan_trap",
        "icon": "⚠️",
        "name": "UZI · 杀猪盘排查",
        "brand": "UZI",
        "category": "尽调风控",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:scan-trap {股票}",
        "hint": "拉高出货 / 财务造假信号",
        "long_desc": "扫描杀猪盘典型模式：异常拉升 vs 基本面脱节、股东减持时点、财务造假特征、社交平台推票热度等，给出预警等级。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_quick_scan",
        "icon": "⚡",
        "name": "UZI · 30 秒速判",
        "brand": "UZI",
        "category": "快速判断",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:quick-scan {股票}",
        "hint": "30 秒结论",
        "long_desc": "30 秒得出对 {股票} 的极简结论：买/卖/持 + 一句话理由 + 关键关注点。适合盘中决策或初筛。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_panel_only",
        "icon": "🗳️",
        "name": "UZI · 66 评委投票",
        "brand": "UZI",
        "category": "快速判断",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:panel-only {股票}",
        "hint": "只看专家评委结果",
        "long_desc": "跳过分析过程，直接产出 66 个专家评委的投票结果与共识度：买/持/卖分布、置信度、少数派意见摘要。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_returns",
        "icon": "📊",
        "name": "UZI · 组合收益归因",
        "brand": "UZI",
        "category": "组合级",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:returns",
        "hint": "v3.8 · 按持仓/行业拆解",
        "long_desc": "v3.8 新增。对当前组合做收益归因：按持仓、行业、风格因子多维度拆解累计收益，输出 Top 贡献与 Top 拖累。前置：需在 /portfolio 页录入持仓。",
        "tools": ["uzi_stock_deep_analysis"],
    },
    {
        "key": "uzi_rebalance",
        "icon": "🔀",
        "name": "UZI · 逐持仓再平衡",
        "brand": "UZI",
        "category": "组合级",
        "source_url": "https://github.com/wbh604/UZI-Skill",
        "prompt_tpl": "/stock-deep-analyzer:rebalance",
        "hint": "v3.8 · 漂移 + 换手成本",
        "long_desc": "v3.8 新增。逐持仓生成再平衡建议：目标 vs 当前权重漂移 + 交易清单 + 考虑 A 股印花税/佣金后的净换手成本。前置：需在 /portfolio 页录入持仓。",
        "tools": ["uzi_stock_deep_analysis"],
    },
]

# 分类展示顺序 · 前端 SkillManager 按此顺序渲染分组标题
CATEGORY_ORDER: list[str] = [
    "综合分析",
    "估值建模",
    "投研报告",
    "事件与筛选",
    "尽调风控",
    "快速判断",
    "组合级",
]

# 详情页公开可查（不需要登录）· 只对 builtin 开放 · custom skill 属于用户私有不公开。
_BUILTIN_BY_KEY = {b["key"]: b for b in BUILTINS}

_BUILTIN_KEYS = {b["key"] for b in BUILTINS}


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


def _rows(user_id: str) -> list[dict]:
    c = get_conn(); cur = c.cursor()
    cur.execute("""SELECT id, name, icon, prompt_tpl, enabled, sort_order, builtin_key
                   FROM chat_user_skill WHERE user_id = %s
                   ORDER BY sort_order, id""", (user_id,))
    out = [{"id": r[0], "name": r[1], "icon": r[2], "prompt_tpl": r[3],
            "enabled": r[4], "sort_order": r[5], "builtin_key": r[6]}
           for r in cur.fetchall()]
    c.close()
    return out


@router.get("/chat/skills")
async def list_skills(request: Request):
    """合成后的能力列表(内置 + 用户覆盖 + 自建),按 sort_order 排。"""
    uid = _uid(request)
    try:
        rows = _rows(uid)
    except Exception as e:
        log.warning("[chat] 读能力列表失败: %s", e)
        rows = []

    override = {r["builtin_key"]: r for r in rows if r["builtin_key"]}
    custom = [r for r in rows if not r["builtin_key"]]

    items: list[dict] = []
    for i, b in enumerate(BUILTINS):
        ov = override.get(b["key"])
        items.append({
            "key": b["key"],
            "builtin": True,
            "icon": (ov or {}).get("icon") or b["icon"],
            "name": (ov or {}).get("name") or b["name"],
            "prompt_tpl": (ov or {}).get("prompt_tpl") or b["prompt_tpl"],
            "hint": b["hint"],
            "brand": b.get("brand", ""),
            "source_url": b.get("source_url", ""),
            "category": b.get("category", "其他"),
            "enabled": ov["enabled"] if ov else True,
            "sort_order": ov["sort_order"] if ov else i,
            "id": ov["id"] if ov else None,
        })
    for r in custom:
        items.append({
            "key": f"custom:{r['id']}", "builtin": False,
            "icon": r["icon"], "name": r["name"], "prompt_tpl": r["prompt_tpl"],
            "hint": "", "brand": "", "source_url": "", "category": "我的能力",
            "enabled": r["enabled"],
            "sort_order": r["sort_order"] or (100 + r["id"]), "id": r["id"],
        })

    items.sort(key=lambda x: (x["sort_order"], x["name"]))
    return {
        "items": items,
        "custom_count": len(custom),
        "max_custom": MAX_CUSTOM,
        "category_order": CATEGORY_ORDER,
    }


@router.get("/chat/skills/detail/{key}")
async def get_skill_detail(key: str):
    """内置能力详情（公开无需登录 · 供 /skills/[key] 页 SSR/CSR 读）。

    自定义能力属于用户私有 · 不在这里公开 · 前端不做详情页入口。
    """
    b = _BUILTIN_BY_KEY.get(key)
    if not b:
        raise HTTPException(404, "能力不存在或非公开")
    return {
        "key": b["key"],
        "icon": b["icon"],
        "name": b["name"],
        "brand": b.get("brand", ""),
        "category": b.get("category", ""),
        "source_url": b.get("source_url", ""),
        "prompt_tpl": b["prompt_tpl"],
        "hint": b["hint"],
        "long_desc": b.get("long_desc", ""),
        "tools": b.get("tools", []),
    }


class SkillIn(BaseModel):
    name: str
    icon: str = "⭐"
    prompt_tpl: str


def _validate(name: str, tpl: str) -> tuple[str, str]:
    name = (name or "").strip()
    tpl = (tpl or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if not tpl:
        raise HTTPException(400, "提问模板不能为空")
    if len(name) > 20:
        raise HTTPException(400, "名称最多 20 字")
    if len(tpl) > MAX_TPL_LEN:
        raise HTTPException(400, f"模板最多 {MAX_TPL_LEN} 字")
    return name, tpl


@router.post("/chat/skills")
async def create_skill(body: SkillIn, request: Request):
    """新建自定义能力。"""
    uid = _uid(request)
    name, tpl = _validate(body.name, body.prompt_tpl)
    if len([r for r in _rows(uid) if not r["builtin_key"]]) >= MAX_CUSTOM:
        raise HTTPException(400, f"最多创建 {MAX_CUSTOM} 个自定义能力,请先删除不用的")
    c = get_conn(); cur = c.cursor()
    cur.execute("""INSERT INTO chat_user_skill (user_id, name, icon, prompt_tpl, sort_order)
                   VALUES (%s,%s,%s,%s, COALESCE(
                     (SELECT max(sort_order)+1 FROM chat_user_skill WHERE user_id=%s), 100))
                   RETURNING id""",
                (uid, name, (body.icon or "⭐")[:4], tpl, uid))
    new_id = cur.fetchone()[0]
    c.commit(); c.close()
    return {"ok": True, "id": new_id}


class SkillPatch(BaseModel):
    name: str | None = None
    icon: str | None = None
    prompt_tpl: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None


@router.patch("/chat/skills/{key}")
async def update_skill(key: str, body: SkillPatch, request: Request):
    """改能力。key 为内置 key(quote/forecast…)或 custom:{id}。

    内置能力没有行时先补一条覆盖记录 —— 用户第一次关掉/改写内置项时走这里。
    """
    uid = _uid(request)
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        return {"ok": True}
    if "name" in patch or "prompt_tpl" in patch:
        n, t = _validate(patch.get("name") or "占位", patch.get("prompt_tpl") or "占位")
        if "name" in patch:
            patch["name"] = n
        if "prompt_tpl" in patch:
            patch["prompt_tpl"] = t
    if "icon" in patch:
        patch["icon"] = patch["icon"][:4]

    c = get_conn(); cur = c.cursor()
    if key.startswith("custom:"):
        try:
            rid = int(key.split(":", 1)[1])
        except ValueError:
            raise HTTPException(400, "无效的能力 id")
        sets = ", ".join(f"{k} = %s" for k in patch)
        cur.execute(f"""UPDATE chat_user_skill SET {sets}, updated_at = NOW()
                        WHERE id = %s AND user_id = %s""",
                    list(patch.values()) + [rid, uid])
        n = cur.rowcount
        c.commit(); c.close()
        if not n:
            raise HTTPException(404, "能力不存在")
        return {"ok": True}

    if key not in _BUILTIN_KEYS:
        c.close()
        raise HTTPException(404, "能力不存在")

    base = next(b for b in BUILTINS if b["key"] == key)
    cur.execute("""INSERT INTO chat_user_skill
                     (user_id, name, icon, prompt_tpl, enabled, sort_order, builtin_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, builtin_key) WHERE builtin_key <> ''
                   DO NOTHING""",
                (uid, base["name"], base["icon"], base["prompt_tpl"], True,
                 BUILTINS.index(base), key))
    sets = ", ".join(f"{k} = %s" for k in patch)
    cur.execute(f"""UPDATE chat_user_skill SET {sets}, updated_at = NOW()
                    WHERE user_id = %s AND builtin_key = %s""",
                list(patch.values()) + [uid, key])
    c.commit(); c.close()
    return {"ok": True}


@router.delete("/chat/skills/{key}")
async def delete_skill(key: str, request: Request):
    """删自定义能力。内置能力不能删,只能关(PATCH enabled=false)。"""
    uid = _uid(request)
    if not key.startswith("custom:"):
        raise HTTPException(400, "内置能力不能删除,可在管理里关闭")
    try:
        rid = int(key.split(":", 1)[1])
    except ValueError:
        raise HTTPException(400, "无效的能力 id")
    c = get_conn(); cur = c.cursor()
    cur.execute("DELETE FROM chat_user_skill WHERE id = %s AND user_id = %s", (rid, uid))
    n = cur.rowcount
    c.commit(); c.close()
    if not n:
        raise HTTPException(404, "能力不存在")
    return {"ok": True}


@router.post("/chat/skills/reset")
async def reset_skills(request: Request):
    """恢复内置能力的默认状态(只删覆盖记录,自建的保留)。"""
    uid = _uid(request)
    c = get_conn(); cur = c.cursor()
    cur.execute("DELETE FROM chat_user_skill WHERE user_id = %s AND builtin_key <> ''", (uid,))
    n = cur.rowcount
    c.commit(); c.close()
    return {"ok": True, "reset": n}
