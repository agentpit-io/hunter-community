"""
发现 Tab — 画像匹配机会 API
GET /api/discover/opportunities
  → 根据用户画像 + 真源信号，返回 AI算力产业链方向卡片，按匹配度排序
"""
import os
import httpx
from fastapi import APIRouter, Request, HTTPException
from loguru import logger

router = APIRouter()

TRUESOURCE_BASE = os.environ.get("TRUESOURCE_API_URL", "http://34.92.72.140:8000")

# 前端 focus 标识符 → 中文 sector 集合映射
# 用于把画像里的英文 focus_sectors（tech/consumer/...）展开成 chain.sectors 里的中文标签
_FOCUS_TO_SECTORS: dict = {
    "tech":     {"AI", "科技", "半导体", "基础设施", "制造"},
    "consumer": {"消费", "白酒", "食品饮料", "家电", "免税", "医美", "服装", "乳制品"},
    "energy":   {"新能源", "光伏", "储能", "电力", "电池", "能源"},
    "finance":  {"金融", "银行", "保险", "证券", "地产"},
    "medical":  {"医药", "创新药", "CXO", "器械", "中药"},
    "balanced": set(),
}

# 板块元数据（前端横向 filter 使用）
SECTOR_META = [
    {"id": "tech",     "name": "科技"},
    {"id": "consumer", "name": "消费"},
    {"id": "energy",   "name": "能源"},
    {"id": "finance",  "name": "金融"},
    {"id": "medical",  "name": "医药"},
]


def _classify_category(sectors: list[str]) -> str:
    """由 chain.sectors 反查所属板块（用于前端 filter），取第一个命中"""
    sec_set = set(sectors)
    for focus_id, cn_secs in _FOCUS_TO_SECTORS.items():
        if focus_id == "balanced":
            continue
        if sec_set.intersection(cn_secs):
            return focus_id
    return "tech"  # 兜底


# 产业链定义（覆盖 5 大板块，共约 30 条）
CHAIN_DEFS = [
    # ── 科技板块（9 条：AI 算力产业链） ─────────────────────────
    {
        "chain": "光模块/光互联",
        "desc": "AI数据中心高速互联核心器件，需求随算力扩张持续增长",
        "symbols": ["300308", "300502", "300394", "000988", "002281"],
        "rep_stocks": [{"symbol": "300308", "name": "中际旭创"}, {"symbol": "300502", "name": "新易盛"}, {"symbol": "300394", "name": "天孚通信"}],
        "sectors": ["科技", "AI"],
        "risk_level": "medium",
    },
    {
        "chain": "算力芯片",
        "desc": "国产GPU/AI芯片，受益于国产替代与AI训练推理需求",
        "symbols": ["688041", "688256", "688047", "300474"],
        "rep_stocks": [{"symbol": "688041", "name": "海光信息"}, {"symbol": "688256", "name": "寒武纪"}],
        "sectors": ["科技", "AI", "半导体"],
        "risk_level": "high",
    },
    {
        "chain": "AI服务器/整机",
        "desc": "大模型算力基础设施集成商，受益于云厂商资本开支扩张",
        "symbols": ["601138", "000977", "603019", "000938"],
        "rep_stocks": [{"symbol": "601138", "name": "工业富联"}, {"symbol": "000977", "name": "浪潮信息"}],
        "sectors": ["科技", "AI"],
        "risk_level": "medium",
    },
    {
        "chain": "IDC/算力运营",
        "desc": "算力中心建设与运营，政策+需求双轮驱动，稳定现金流",
        "symbols": ["300442", "300383", "603881"],
        "rep_stocks": [{"symbol": "300442", "name": "润泽科技"}, {"symbol": "300383", "name": "光环新网"}],
        "sectors": ["科技", "AI", "基础设施"],
        "risk_level": "low",
    },
    {
        "chain": "存储芯片/模组",
        "desc": "AI训练推理对HBM/大容量存储需求激增，国产替代加速",
        "symbols": ["603986", "688008", "301308", "688525", "001309"],
        "rep_stocks": [{"symbol": "603986", "name": "兆易创新"}, {"symbol": "688008", "name": "澜起科技"}],
        "sectors": ["科技", "半导体"],
        "risk_level": "high",
    },
    {
        "chain": "先进封装/HBM",
        "desc": "AI芯片封测升级，CoWoS/HBM封装国产化进程加速",
        "symbols": ["600584", "002156", "002185", "000021"],
        "rep_stocks": [{"symbol": "600584", "name": "长电科技"}, {"symbol": "002156", "name": "通富微电"}],
        "sectors": ["科技", "半导体"],
        "risk_level": "medium",
    },
    {
        "chain": "液冷/温控/电源",
        "desc": "高功耗AI芯片推动液冷需求，算力能源管理核心受益",
        "symbols": ["002837", "300499", "002335", "002364"],
        "rep_stocks": [{"symbol": "002837", "name": "英维克"}, {"symbol": "300499", "name": "高澜股份"}],
        "sectors": ["科技", "新能源"],
        "risk_level": "medium",
    },
    {
        "chain": "PCB/覆铜板",
        "desc": "AI服务器高速PCB核心材料，量价齐升逻辑清晰",
        "symbols": ["002463", "300476", "002916", "002384", "600183", "002938"],
        "rep_stocks": [{"symbol": "002463", "name": "沪电股份"}, {"symbol": "002916", "name": "深南电路"}],
        "sectors": ["科技", "制造"],
        "risk_level": "low",
    },
    {
        "chain": "光芯片/光器件",
        "desc": "光模块上游核心器件，AI算力光互联产业链关键环节",
        "symbols": ["688498", "688090", "688048", "601869"],
        "rep_stocks": [{"symbol": "688498", "name": "源杰科技"}, {"symbol": "688090", "name": "光库科技"}],
        "sectors": ["科技", "AI"],
        "risk_level": "high",
    },

    # ── 消费板块（7 条） ─────────────────────────
    {
        "chain": "白酒",
        "desc": "高端白酒稳健增长，次高端受消费复苏带动",
        "symbols": ["600519", "000858", "000568", "600809", "002304"],
        "rep_stocks": [{"symbol": "600519", "name": "贵州茅台"}, {"symbol": "000858", "name": "五粮液"}, {"symbol": "000568", "name": "泸州老窖"}],
        "sectors": ["消费", "白酒", "食品饮料"],
        "risk_level": "low",
    },
    {
        "chain": "调味品",
        "desc": "餐饮复苏叠加提价，龙头集中度持续提升",
        "symbols": ["603288", "600872", "603027"],
        "rep_stocks": [{"symbol": "603288", "name": "海天味业"}, {"symbol": "600872", "name": "中炬高新"}, {"symbol": "603027", "name": "千禾味业"}],
        "sectors": ["消费", "食品饮料"],
        "risk_level": "low",
    },
    {
        "chain": "家电",
        "desc": "白电龙头受益渠道优化和以旧换新政策",
        "symbols": ["000333", "000651", "600690", "002508"],
        "rep_stocks": [{"symbol": "000333", "name": "美的集团"}, {"symbol": "000651", "name": "格力电器"}, {"symbol": "600690", "name": "海尔智家"}],
        "sectors": ["消费", "家电"],
        "risk_level": "low",
    },
    {
        "chain": "免税",
        "desc": "海南离岛免税政策叠加中国中免龙头地位",
        "symbols": ["601888", "600859"],
        "rep_stocks": [{"symbol": "601888", "name": "中国中免"}, {"symbol": "600859", "name": "王府井"}],
        "sectors": ["消费", "免税"],
        "risk_level": "medium",
    },
    {
        "chain": "乳制品",
        "desc": "行业库存改善，龙头竞争格局稳固",
        "symbols": ["600887", "600597", "002946"],
        "rep_stocks": [{"symbol": "600887", "name": "伊利股份"}, {"symbol": "600597", "name": "光明乳业"}, {"symbol": "002946", "name": "新乳业"}],
        "sectors": ["消费", "食品饮料", "乳制品"],
        "risk_level": "low",
    },
    {
        "chain": "医美",
        "desc": "轻医美渗透率提升，龙头产品迭代驱动增长",
        "symbols": ["688363", "300896", "688366"],
        "rep_stocks": [{"symbol": "688363", "name": "华熙生物"}, {"symbol": "300896", "name": "爱美客"}, {"symbol": "688366", "name": "昊海生科"}],
        "sectors": ["消费", "医美"],
        "risk_level": "high",
    },
    {
        "chain": "服装家纺",
        "desc": "户外与运动服饰高景气，功能性服装扩张",
        "symbols": ["002563", "603877", "600177"],
        "rep_stocks": [{"symbol": "002563", "name": "森马服饰"}, {"symbol": "603877", "name": "太平鸟"}, {"symbol": "600177", "name": "雅戈尔"}],
        "sectors": ["消费", "服装"],
        "risk_level": "medium",
    },

    # ── 能源板块（5 条） ─────────────────────────
    {
        "chain": "动力电池",
        "desc": "新能源车渗透率持续提升，电池龙头受益规模效应",
        "symbols": ["300750", "300014", "002074", "300207"],
        "rep_stocks": [{"symbol": "300750", "name": "宁德时代"}, {"symbol": "300014", "name": "亿纬锂能"}, {"symbol": "002074", "name": "国轩高科"}],
        "sectors": ["新能源", "电池"],
        "risk_level": "medium",
    },
    {
        "chain": "光伏",
        "desc": "全球装机需求增长，产业链价格触底反弹预期",
        "symbols": ["601012", "600438", "002129", "300274"],
        "rep_stocks": [{"symbol": "601012", "name": "隆基绿能"}, {"symbol": "600438", "name": "通威股份"}, {"symbol": "300274", "name": "阳光电源"}],
        "sectors": ["新能源", "光伏"],
        "risk_level": "high",
    },
    {
        "chain": "储能",
        "desc": "大储需求高速增长，电化学储能商业化加速",
        "symbols": ["300274", "688063", "300750"],
        "rep_stocks": [{"symbol": "300274", "name": "阳光电源"}, {"symbol": "688063", "name": "派能科技"}, {"symbol": "300750", "name": "宁德时代"}],
        "sectors": ["新能源", "储能"],
        "risk_level": "medium",
    },
    {
        "chain": "传统能源",
        "desc": "煤炭油气龙头高分红，能源保供政策支持",
        "symbols": ["601088", "601225", "600985", "600028"],
        "rep_stocks": [{"symbol": "601088", "name": "中国神华"}, {"symbol": "601225", "name": "陕西煤业"}, {"symbol": "600028", "name": "中国石化"}],
        "sectors": ["能源"],
        "risk_level": "low",
    },
    {
        "chain": "电力设备",
        "desc": "电网投资持续加码，智能化和特高压双轮驱动",
        "symbols": ["600406", "600089", "600312"],
        "rep_stocks": [{"symbol": "600406", "name": "国电南瑞"}, {"symbol": "600089", "name": "特变电工"}, {"symbol": "600312", "name": "平高电气"}],
        "sectors": ["电力", "能源"],
        "risk_level": "low",
    },

    # ── 金融板块（4 条） ─────────────────────────
    {
        "chain": "银行",
        "desc": "股份行息差稳定，估值处于历史低位",
        "symbols": ["600036", "601166", "601398", "000001"],
        "rep_stocks": [{"symbol": "600036", "name": "招商银行"}, {"symbol": "601166", "name": "兴业银行"}, {"symbol": "601398", "name": "工商银行"}],
        "sectors": ["金融", "银行"],
        "risk_level": "low",
    },
    {
        "chain": "保险",
        "desc": "储蓄型产品需求旺盛，投资端预期改善",
        "symbols": ["601318", "601601", "601336"],
        "rep_stocks": [{"symbol": "601318", "name": "中国平安"}, {"symbol": "601601", "name": "中国太保"}, {"symbol": "601336", "name": "新华保险"}],
        "sectors": ["金融", "保险"],
        "risk_level": "low",
    },
    {
        "chain": "券商",
        "desc": "并购重组预期升温，市场活跃度回升",
        "symbols": ["600030", "601688", "300059"],
        "rep_stocks": [{"symbol": "600030", "name": "中信证券"}, {"symbol": "601688", "name": "华泰证券"}, {"symbol": "300059", "name": "东方财富"}],
        "sectors": ["金融", "证券"],
        "risk_level": "medium",
    },
    {
        "chain": "地产",
        "desc": "行业深度调整后，龙头房企集中度提升",
        "symbols": ["600048", "000002", "001979"],
        "rep_stocks": [{"symbol": "600048", "name": "保利发展"}, {"symbol": "000002", "name": "万科A"}, {"symbol": "001979", "name": "招商蛇口"}],
        "sectors": ["金融", "地产"],
        "risk_level": "high",
    },

    # ── 医药板块（4 条） ─────────────────────────
    {
        "chain": "创新药",
        "desc": "国内创新药出海放量，医保支付端边际改善",
        "symbols": ["600276", "600196", "688180"],
        "rep_stocks": [{"symbol": "600276", "name": "恒瑞医药"}, {"symbol": "600196", "name": "复星医药"}, {"symbol": "688180", "name": "君实生物"}],
        "sectors": ["医药", "创新药"],
        "risk_level": "high",
    },
    {
        "chain": "CXO",
        "desc": "全球医药研发外包需求恢复，龙头订单回暖",
        "symbols": ["603259", "002821", "300347"],
        "rep_stocks": [{"symbol": "603259", "name": "药明康德"}, {"symbol": "002821", "name": "凯莱英"}, {"symbol": "300347", "name": "泰格医药"}],
        "sectors": ["医药", "CXO"],
        "risk_level": "medium",
    },
    {
        "chain": "医疗器械",
        "desc": "国产化率提升，高值耗材集采后龙头量增",
        "symbols": ["300760", "688271", "300595"],
        "rep_stocks": [{"symbol": "300760", "name": "迈瑞医疗"}, {"symbol": "688271", "name": "联影医疗"}, {"symbol": "300595", "name": "欧普康视"}],
        "sectors": ["医药", "器械"],
        "risk_level": "medium",
    },
    {
        "chain": "中药",
        "desc": "基药目录扩容与提价预期，龙头品牌壁垒稳固",
        "symbols": ["600436", "000538", "600085"],
        "rep_stocks": [{"symbol": "600436", "name": "片仔癀"}, {"symbol": "000538", "name": "云南白药"}, {"symbol": "600085", "name": "同仁堂"}],
        "sectors": ["医药", "中药"],
        "risk_level": "low",
    },
]

# 风险偏好 × 链风险等级 → 加分
_RISK_BONUS = {
    "conservative": {"low": 1, "medium": 0, "high": -2},
    "balanced":     {"low": 0, "medium": 1, "high": 0},
    "aggressive":   {"low": -1, "medium": 1, "high": 2},
}

# 持仓期偏好 → 加分（长期更适合基本面驱动的链）
_HORIZON_BONUS = {"short": 0, "medium": 1, "long": 1}


def _compute_score(chain_def: dict, alert_level: str, signal_count: int, profile: dict) -> int:
    score = 3

    # 信号质量
    if alert_level == "green":
        score += 1
    elif alert_level == "red":
        score -= 1

    # 信号数量
    if signal_count >= 3:
        score += 1
    elif signal_count == 0:
        score -= 1

    # 风险偏好匹配
    risk = profile.get("risk_tolerance") or "balanced"
    chain_risk = chain_def.get("risk_level", "medium")
    score += _RISK_BONUS.get(risk, {}).get(chain_risk, 0)

    # 持仓期加成
    horizon = profile.get("holding_period") or "medium"
    score += _HORIZON_BONUS.get(horizon, 0)

    # 偏好方向匹配（focus 英文标识符 → 展开中文 sector 集合 → 求交集）
    focus_ids = profile.get("focus_sectors") or []
    focus_secs: set = set()
    for fid in focus_ids:
        focus_secs.update(_FOCUS_TO_SECTORS.get(fid, set()))
    chain_sectors = set(chain_def.get("sectors", []))
    if focus_secs and focus_secs.intersection(chain_sectors):
        score += 2  # 用户明确偏好的板块加大权重

    return max(1, min(5, score))


def _truth_summary(alert_level: str, signal_count: int) -> tuple[bool, str]:
    """返回 (truth_verified, summary_text)"""
    if alert_level == "green" and signal_count >= 2:
        return True, "真实数据向好"
    elif alert_level == "yellow" and signal_count >= 1:
        return True, "有正向信号"
    elif alert_level == "red":
        return False, "存在风险信号"
    else:
        return False, "信号数据不足"


@router.get("/discover/opportunities")
async def get_opportunities(request: Request):
    """
    根据用户画像 + 真源信号，返回 AI算力产业链方向卡片。
    无画像时也返回（按信号质量排序，不做个性化加权）。
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "需要登录")

    # 1. 读用户画像
    profile: dict = {}
    profile_complete = False
    try:
        from app.services.database import get_user_preference
        pref = get_user_preference(user_id)
        if pref.get("risk_tolerance"):
            profile = {
                "risk_tolerance": pref["risk_tolerance"],
                "holding_period": pref.get("holding_period", ""),
                "focus_sectors": pref.get("focus_sectors") or [],
            }
            profile_complete = True
    except Exception as e:
        logger.warning("读取用户画像失败 user_id={}: {}", user_id, e)

    # 2. 拉取所有核心股票的 TrueSource daily-brief
    all_symbols = [s for c in CHAIN_DEFS for s in c["symbols"]]
    brief_by_sym: dict = {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{TRUESOURCE_BASE}/api/hunter/daily-brief",
                params={"symbols": ",".join(all_symbols)},
            )
            if r.status_code == 200:
                for stk in r.json().get("stocks", []):
                    brief_by_sym[stk["symbol"]] = stk
    except Exception as e:
        logger.warning("TrueSource daily-brief 拉取失败: {}", e)

    # 3. 按产业链聚合
    opportunities = []
    for chain_def in CHAIN_DEFS:
        symbols = chain_def["symbols"]
        chain_stocks = [brief_by_sym[s] for s in symbols if s in brief_by_sym]

        # 链级 alert_level：取成员里最强的（red > yellow > green > grey）
        level_order = {"red": 3, "yellow": 2, "green": 1, "grey": 0}
        if chain_stocks:
            best_level = max(chain_stocks, key=lambda s: level_order.get(s.get("alert_level", "grey"), 0))
            alert_level = best_level.get("alert_level", "grey")
        else:
            alert_level = "grey"

        # 信号总数（去重，只数非空）
        total_sigs = sum(s.get("signal_count", 0) for s in chain_stocks)

        # 计算匹配分
        match_score = _compute_score(chain_def, alert_level, total_sigs, profile)
        truth_verified, truth_summary = _truth_summary(alert_level, total_sigs)

        opportunities.append({
            "chain": chain_def["chain"],
            "desc": chain_def["desc"],
            "match_score": match_score,
            "alert_level": alert_level,
            "signal_count": total_sigs,
            "truth_verified": truth_verified,
            "truth_summary": truth_summary,
            "rep_stocks": chain_def["rep_stocks"],
            "sectors": chain_def["sectors"],
            "category": _classify_category(chain_def["sectors"]),
        })

    # 4. 排序：匹配分降序，同分 green > yellow > grey > red
    level_sort = {"green": 0, "yellow": 1, "grey": 2, "red": 3}
    opportunities.sort(key=lambda x: (-x["match_score"], level_sort.get(x["alert_level"], 2)))

    # 5. 按板块统计（前端横向 filter 使用）
    sector_stats = [
        {
            **s,
            "count": sum(1 for o in opportunities if o["category"] == s["id"]),
        }
        for s in SECTOR_META
    ]

    return {
        "profile": profile,
        "profile_complete": profile_complete,
        "opportunities": opportunities,
        "sectors": sector_stats,
    }
