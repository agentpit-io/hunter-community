"""信源注册表（F1 / F2 基础）

每个信源声明:
  - key:        程序内部标识
  - name:       中文名（展示用）
  - tier:       信源等级（决定 prompt 权重指南的处理规则）
  - weight:     0.00 - 1.00（决定能否进入归因事实链）
  - fetch_via:  数据来源（finance-data API / akshare 直连 / 第三方）
  - enabled:    是否启用

权重指南（在 F2 prompt 里注入）:
  >= 0.85    法定披露 / 客观资金数据  → 直接采信为事实
  0.70-0.85  权威媒体                  → 可采信，敏感事项多源交叉
  0.55-0.70  机构观点 / 行业数据       → 数据可信，结论存疑
  0.30-0.55  社交媒体                  → 仅作情绪参考
  < 0.30     论坛 / 营销号             → 仅作信号触发，禁止单独结论
"""

from typing import Literal, TypedDict

SourceTier = Literal[
    "regulatory",      # 法定披露（巨潮）
    "media_premium",   # 权威媒体（财新、财联社）
    "media_general",   # 一般媒体（东方财富新闻、新浪财经）
    "institutional",   # 机构观点（券商研报）
    "capital_flow",    # 资金流向（北向、龙虎榜、大宗）
    "alternative",     # 另类数据（锂价、行业现货）
    "social",          # 社交媒体（雪球、知乎）
    "forum",           # 论坛（股吧、贴吧）
]


class SourceMeta(TypedDict):
    key: str
    name: str
    tier: SourceTier
    weight: float
    fetch_via: str
    enabled: bool
    notes: str


SOURCE_REGISTRY: dict[str, SourceMeta] = {
    # ─── 法定披露层（最高权重）──────────────────────────
    "cninfo": {
        "key": "cninfo",
        "name": "巨潮资讯",
        "tier": "regulatory",
        "weight": 0.95,
        "fetch_via": "finance-data:/api/cninfo/announcements",
        "enabled": True,
        "notes": "证监会指定信息披露平台，所有上市公司公告原始来源",
    },

    # ─── 权威媒体层 ────────────────────────────────────
    "cls": {
        "key": "cls",
        "name": "财联社",
        "tier": "media_premium",
        "weight": 0.75,
        "fetch_via": "finance-data:/api/news/articles",
        "enabled": True,
        "notes": "实时财经电报，时效性强",
    },
    "caixin": {
        "key": "caixin",
        "name": "财新",
        "tier": "media_premium",
        "weight": 0.85,
        "fetch_via": "finance-data:/api/news/articles",
        "enabled": False,  # MVP 阶段暂缓，需付费 API
        "notes": "深度调查报道，权威性高",
    },
    "eastmoney_news": {
        "key": "eastmoney_news",
        "name": "东方财富资讯",
        "tier": "media_general",
        "weight": 0.65,
        "fetch_via": "akshare:stock_news_em",
        "enabled": True,
        "notes": "个股相关新闻聚合",
    },

    # ─── 资金流向层（客观数据）─────────────────────────
    "northbound": {
        "key": "northbound",
        "name": "北向资金",
        "tier": "capital_flow",
        "weight": 0.85,
        "fetch_via": "akshare:stock_hsgt_individual_em",
        "enabled": True,
        "notes": "沪深港通外资净买卖，'聪明钱'指标",
    },
    "longhubang": {
        "key": "longhubang",
        "name": "龙虎榜",
        "tier": "capital_flow",
        "weight": 0.90,
        "fetch_via": "akshare:stock_lhb_stock_detail_em",
        "enabled": True,
        "notes": "异动股席位明细，反映机构/游资真实买卖",
    },
    "block_trade": {
        "key": "block_trade",
        "name": "大宗交易",
        "tier": "capital_flow",
        "weight": 0.85,
        "fetch_via": "akshare:stock_dzjy_mrtj",
        "enabled": True,
        "notes": "大额成交折溢价，识别机构进出场",
    },

    # ─── 行业/大盘联动 ─────────────────────────────────
    "market_indices": {
        "key": "market_indices",
        "name": "大盘指数",
        "tier": "capital_flow",
        "weight": 0.70,
        "fetch_via": "akshare:stock_zh_index_spot_em",
        "enabled": True,
        "notes": "沪深300/创业板等大盘联动数据",
    },
    "industry_board": {
        "key": "industry_board",
        "name": "行业板块",
        "tier": "capital_flow",
        "weight": 0.70,
        "fetch_via": "akshare:stock_board_industry_summary_ths",
        "enabled": True,
        "notes": "板块涨跌幅 + 成份股统计",
    },

    # ─── 对立面（F8 视野层）─────────────────────────────
    "contrarian": {
        "key": "contrarian",
        "name": "对立面搜索",
        "tier": "media_general",
        "weight": 0.65,
        "fetch_via": "akshare:stock_news_em (reverse query)",
        "enabled": True,
        "notes": "主动搜索风险/减持/做空类信息",
    },

    # ─── 社交媒体（参考，不进事实链）─────────────────
    "xueqiu": {
        "key": "xueqiu",
        "name": "雪球",
        "tier": "social",
        "weight": 0.30,
        "fetch_via": "akshare:stock_hot_search_baidu",
        "enabled": False,  # MVP 不接入
        "notes": "仅作情绪参考",
    },
}


# ─── 权重档位工具 ──────────────────────────────────────

def get_weight_guide() -> str:
    """生成 prompt 里要注入的权重指南表（中文）"""
    return """
# 信源权重指南

| 权重区间   | 信源类型       | 处理规则                                |
|-----------|---------------|----------------------------------------|
| ≥ 0.85    | 法定披露/资金 | 直接采信为事实                          |
| 0.70-0.85 | 权威媒体       | 可采信，敏感事项需 2+ 同方向源交叉验证  |
| 0.55-0.70 | 机构观点/行业 | 数据可信，结论存疑                      |
| 0.30-0.55 | 社交媒体       | 仅作情绪参考                            |
| < 0.30    | 论坛/营销号   | 仅作信号触发，禁止单独作为结论依据      |

# 判断规则
1. 权重 ≥ 0.7 的信息当作"事实"
2. 权重 0.4-0.7 需要 2+ 同方向源交叉验证
3. 权重 < 0.4 禁止单独作为结论依据
4. 高 vs 低权重冲突 → 采信高权重
"""


def get_enabled_sources() -> list[SourceMeta]:
    """返回所有启用的信源"""
    return [s for s in SOURCE_REGISTRY.values() if s["enabled"]]


def get_source(key: str) -> SourceMeta | None:
    return SOURCE_REGISTRY.get(key)


def is_authoritative(weight: float) -> bool:
    """权重 ≥ 0.7 算权威源（F5 信号充足度检查用）"""
    return weight >= 0.7


# ─── F5 信号充足度阈值 ─────────────────────────────────

class SufficiencyThresholds:
    MIN_AUTHORITATIVE_SOURCES = 1   # 至少 1 个权威源（权重 ≥ 0.7）
    MIN_TOTAL_SOURCES         = 3   # 至少 3 个不同信源
    MIN_HIGH_WEIGHT_FACTS     = 2   # 至少 2 条高权重事实
    MIN_COVERAGE_SCORE        = 0.4 # 信源覆盖度
