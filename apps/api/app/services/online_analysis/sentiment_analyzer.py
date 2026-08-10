"""F9 · 情绪极性分布异常检测（简化版）

简化版策略（MVP）：
  - 关键词词典分类 → 正/中/负三类
  - 与基线分布比较，识别异常
  - 异常时降权（× 0.5）

真实基线（财经新闻人工标注）：正面 25% / 负面 20% / 中性 55%
异常阈值：
  - 极度正面 80%+ → 疑似 PR 投放
  - 极度负面 70%+ → 疑似做空集中攻击
  - 中性 < 10%   → 真实新闻不可能（必然异常）
"""
import logging
from dataclasses import dataclass
from typing import Literal

from .unified_fetcher import NewsItem

log = logging.getLogger(__name__)


Polarity = Literal["positive", "negative", "neutral"]


# 简化词典（生产可换 BERT/jieba+TF-IDF）
_POSITIVE_KEYWORDS = [
    "增长", "涨", "突破", "新高", "看好", "优秀", "领先", "强势", "买入", "推荐",
    "前景", "利好", "受益", "提升", "上调", "提速", "扩张", "成长", "龙头",
    "稳健", "亮眼", "超预期", "复苏", "回暖", "积极",
]
_NEGATIVE_KEYWORDS = [
    "下跌", "下滑", "暴跌", "新低", "亏损", "承压", "减持", "卖出", "下调",
    "风险", "警示", "调查", "立案", "处罚", "ST", "停牌", "退市", "诉讼",
    "做空", "质疑", "造假", "违规", "失信", "破产", "重整", "减值",
]


def classify_polarity(title: str, content: str = "") -> Polarity:
    """简化版情绪分类"""
    text = (title or "") + " " + (content or "")
    pos = sum(1 for k in _POSITIVE_KEYWORDS if k in text)
    neg = sum(1 for k in _NEGATIVE_KEYWORDS if k in text)
    if pos > neg + 1:
        return "positive"
    if neg > pos + 1:
        return "negative"
    return "neutral"


# 基线分布（真实财经新闻人工标注）
_BASELINE = {"positive": 0.25, "negative": 0.20, "neutral": 0.55}

# 异常阈值
_EXTREME_POSITIVE = 0.80
_EXTREME_NEGATIVE = 0.70
_MISSING_NEUTRAL  = 0.10


@dataclass
class SentimentAnomaly:
    is_anomaly: bool
    distribution: dict[str, float]
    deviation_factor: float           # 偏离基线倍数
    anomaly_type: str | None          # 'extreme_positive' / 'extreme_negative' / 'missing_neutral'
    affected_count: int               # 与异常方向一致的新闻数

    def to_dict(self) -> dict:
        return {
            "is_anomaly":       self.is_anomaly,
            "distribution":     self.distribution,
            "deviation_factor": round(self.deviation_factor, 2),
            "anomaly_type":     self.anomaly_type,
            "affected_count":   self.affected_count,
        }


def detect_sentiment_anomaly(news_items: list[NewsItem]) -> SentimentAnomaly:
    """检测情绪分布是否异常"""
    if not news_items:
        return SentimentAnomaly(False, {}, 0.0, None, 0)

    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for n in news_items:
        polarity = classify_polarity(n.title, n.content or "")
        counts[polarity] += 1

    total = sum(counts.values())
    dist = {k: v / total for k, v in counts.items()}

    # 异常判断
    anomaly_type = None
    deviation = 0.0
    affected_count = 0
    if dist["positive"] >= _EXTREME_POSITIVE:
        anomaly_type = "extreme_positive"
        deviation = dist["positive"] / _BASELINE["positive"]
        affected_count = counts["positive"]
    elif dist["negative"] >= _EXTREME_NEGATIVE:
        anomaly_type = "extreme_negative"
        deviation = dist["negative"] / _BASELINE["negative"]
        affected_count = counts["negative"]
    elif dist["neutral"] <= _MISSING_NEUTRAL:
        anomaly_type = "missing_neutral"
        deviation = _BASELINE["neutral"] / max(0.01, dist["neutral"])
        affected_count = total - counts["neutral"]

    return SentimentAnomaly(
        is_anomaly       = anomaly_type is not None,
        distribution     = {k: round(v, 3) for k, v in dist.items()},
        deviation_factor = deviation,
        anomaly_type     = anomaly_type,
        affected_count   = affected_count,
    )


def adjust_weights_for_anomaly(news_items: list[NewsItem],
                                 anomaly: SentimentAnomaly) -> dict:
    """如检测到情绪异常，对低权重源（< 0.7）的同方向新闻降权 × 0.5

    高权重源（巨潮、财联社）不受影响（事实就是事实）。
    返回降权统计。
    """
    if not anomaly.is_anomaly:
        return {"adjusted_count": 0}

    dominant: Polarity = {
        "extreme_positive": "positive",
        "extreme_negative": "negative",
        "missing_neutral":  "positive",   # 缺中性时通常是正面集中
    }.get(anomaly.anomaly_type, "positive")

    adjusted = 0
    for n in news_items:
        polarity = classify_polarity(n.title, n.content or "")
        if polarity != dominant:
            continue
        if n.source_weight >= 0.7:
            # 权威源不降权
            continue
        n.source_weight = round(n.source_weight * 0.5, 2)
        adjusted += 1

    log.info(
        "F9 anomaly={} type={} affected={} adjusted={} (weighted * 0.5)",
        anomaly.is_anomaly, anomaly.anomaly_type, anomaly.affected_count, adjusted,
    )
    return {"adjusted_count": adjusted, "dominant_polarity": dominant}
