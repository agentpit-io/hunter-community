"""AdventureX 活动 V4：待体验功能清单。

设计（2026-07-22 起）：
  - 每个用户被分配 4 项任务 = 2 基础必做 + 2 随机
  - 基础任务：所有用户共同必做，覆盖产品最小完整闭环（自选股 + 偏好）
  - 随机任务：从 8 项可选池中按 openid hash 稳定抽 2 项
  - 阈值：4 项全部完成 → 解锁股民礼

前端展示的唯一真源。字段：
  id     — 稳定 id，写入 ax_event.features_used JSONB 数组
  icon   — 前端展示 emoji
  title  — 4 字功能名（与 WxHome 二级 tab 名称对齐）
  desc   — 一句话说明
  route  — 前端 ?nav= 参数的 value，主 app 解析后跳到对应位置
             格式 "<tab>:<sub>"：
               watchlist:overview  持仓中心-综合概览
               watchlist:list      持仓中心-自选股清单
               watchlist:position  持仓中心-持仓报告
               watchlist:alert     持仓中心-提醒设置
               kpred:research      研究-深度研究
               kpred:scout         研究-一手情报
               kpred:kpred         研究-量化择时
               kpred:hold          研究-持仓研判
               kpred:event         研究-事件解读
               discover:main       发现
               profile:main        我的板块偏好
"""

import hashlib
import random as _random

# ── 基础必做任务（2 项，所有用户共同）─────────────────────────────────────────
AX_BASE_FEATURES = [
    {"id": "watchlist_add",     "icon": "➕", "title": "添加自选股",
     "desc": "输入你关注的股票 · 建立观察池",   "route": "watchlist:list"},
    {"id": "sector_preference", "icon": "🎯", "title": "板块偏好",
     "desc": "设置板块偏好获取个性化推荐",       "route": "profile:main"},
]

# ── 可选任务池（8 项，按 openid 随机抽 2）─────────────────────────────────────
AX_OPTIONAL_FEATURES = [
    {"id": "portfolio_overview",  "icon": "📰", "title": "综合概览",
     "desc": "AI 分级 · 每股一手情报",       "route": "watchlist:overview"},
    {"id": "portfolio_report",    "icon": "📈", "title": "持仓报告",
     "desc": "成本盈亏 · 持仓明细",           "route": "watchlist:position"},
    {"id": "portfolio_alert",     "icon": "🔔", "title": "提醒设置",
     "desc": "定时推送 · 消息触发",           "route": "watchlist:alert"},
    {"id": "research_deep",       "icon": "📊", "title": "深度研究",
     "desc": "AI 分析师 · 3 分钟建立认知",    "route": "kpred:research"},
    {"id": "research_scout",      "icon": "🔍", "title": "一手情报",
     "desc": "机构调研 · Gemini 汇总",        "route": "kpred:scout"},
    {"id": "research_kpred",      "icon": "🎯", "title": "量化择时",
     "desc": "Kronos · 清华金融大模型",       "route": "kpred:kpred"},
    {"id": "research_hold",       "icon": "🛡", "title": "持仓研判",
     "desc": "Bull/Bear 辩论决策",            "route": "kpred:hold"},
    {"id": "research_event",      "icon": "⚡", "title": "事件解读",
     "desc": "突发事件对持仓的影响分析",       "route": "kpred:event"},
    {"id": "discover",            "icon": "🔎", "title": "板块发现",
     "desc": "板块偏好 · 产业链匹配",         "route": "discover:main"},
]

AX_RANDOM_COUNT = 2
AX_UNLOCK_THRESHOLD = len(AX_BASE_FEATURES) + AX_RANDOM_COUNT  # = 4

# 全量白名单：任何已知 feature_id 都允许 track（老用户可能已上报可选项之外的），
# 但仅"分配集合"内的完成计入进度。
AX_ALL_FEATURES = AX_BASE_FEATURES + AX_OPTIONAL_FEATURES
AX_FEATURE_IDS = {f["id"] for f in AX_ALL_FEATURES}


def _stable_seed(openid: str) -> int:
    """SHA256(openid) 前 8 字节做种子 —— 同一用户永远拿到同样的随机任务。"""
    if not openid:
        return 0
    h = hashlib.sha256(openid.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def get_assigned_features(openid: str) -> list[dict]:
    """返回该用户被分配的 4 项任务：2 基础 + 2 随机（顺序：基础在前）。"""
    if not openid:
        # 非活动参与者兜底：给前 2 项可选，仅用于展示，无实际计分意义
        return list(AX_BASE_FEATURES) + list(AX_OPTIONAL_FEATURES[:AX_RANDOM_COUNT])
    rng = _random.Random(_stable_seed(openid))
    chosen = rng.sample(AX_OPTIONAL_FEATURES, AX_RANDOM_COUNT)
    return list(AX_BASE_FEATURES) + chosen


def get_assigned_ids(openid: str) -> set[str]:
    return {f["id"] for f in get_assigned_features(openid)}
