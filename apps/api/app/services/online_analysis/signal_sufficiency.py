"""F5 · 信号充足度兜底

哲学：AI 最危险的不是答错，是答得很笃定但答错。
当采集到的信号质量不足时，主动声明"信号不足"，而不是让 LLM 瞎编。

判断维度：
  - 权威源数量（≥ 1）
  - 总信源数量（≥ 3）
  - 高权重事实数（≥ 2）
  - 信源覆盖度（≥ 0.4）
"""
from dataclasses import dataclass

from .source_registry import SufficiencyThresholds
from .unified_fetcher import FetchResult


@dataclass
class SufficiencyCheck:
    is_sufficient: bool
    reasons: list[str]                       # 失败原因（多条可累加）
    details: dict                            # 完整诊断信息

    def to_dict(self) -> dict:
        return {
            "is_sufficient": self.is_sufficient,
            "reasons":       self.reasons,
            "details":       self.details,
        }


def check_sufficiency(fetch_result: FetchResult) -> SufficiencyCheck:
    """根据采集结果判断信号是否充足

    Returns:
        SufficiencyCheck: is_sufficient + reasons + details
    """
    th = SufficiencyThresholds

    details = {
        "authoritative_count":  fetch_result.authoritative_count,
        "total_sources":        len(fetch_result.successful_sources),
        "high_weight_facts":    fetch_result.high_weight_facts_count,
        "coverage_score":       round(fetch_result.coverage_score, 2),
        "failed_sources":       [f["source"] for f in fetch_result.failed_sources],
    }

    reasons = []
    if fetch_result.authoritative_count < th.MIN_AUTHORITATIVE_SOURCES:
        reasons.append("NO_AUTHORITATIVE")
    if len(fetch_result.successful_sources) < th.MIN_TOTAL_SOURCES:
        reasons.append("TOO_FEW_SOURCES")
    if fetch_result.high_weight_facts_count < th.MIN_HIGH_WEIGHT_FACTS:
        reasons.append("INSUFFICIENT_HIGH_WEIGHT_FACTS")
    if fetch_result.coverage_score < th.MIN_COVERAGE_SCORE:
        reasons.append("LOW_COVERAGE")

    # 行情数据缺失也算（针对个股异动归因场景）
    if not fetch_result.market_data:
        reasons.append("NO_MARKET_DATA")

    return SufficiencyCheck(
        is_sufficient = (len(reasons) == 0),
        reasons       = reasons,
        details       = details,
    )


# ─── 兜底卡片内容（信号不足时给用户的友好提示）──────────────────────────

def build_insufficient_card(stock_name: str, change_pct: float,
                             check: SufficiencyCheck) -> dict:
    """生成"信号不足"推送卡片内容"""
    reason_text = {
        "NO_AUTHORITATIVE":              "✗ 未拉到任何权威源（巨潮/财联社）",
        "TOO_FEW_SOURCES":               f"✗ 仅成功 {check.details['total_sources']} 个信源（最少需要 3 个）",
        "INSUFFICIENT_HIGH_WEIGHT_FACTS":f"✗ 高权重事实仅 {check.details['high_weight_facts']} 条（最少需要 2 条）",
        "LOW_COVERAGE":                  f"✗ 信源覆盖度仅 {check.details['coverage_score']*100:.0f}%",
        "NO_MARKET_DATA":                "✗ 大盘 / 行业数据缺失",
    }

    explanations = [reason_text.get(r, r) for r in check.reasons]

    return {
        "title":   f"🤔 {stock_name} 信号不足，暂无法归因",
        "summary": f"{stock_name} 今日 {change_pct:+.2f}%，但是：",
        "explanations": explanations,
        "fallback_message": (
            f"我们不会在数据不足时强行归因。"
            f"建议等待 4-6 小时后人工判断，或自行核实。\n\n"
            f"如果发现以下情况立即推送：\n"
            f"- 公司发布重大公告\n"
            f"- 媒体出现重大报道\n"
            f"- 信源全部恢复正常"
        ),
        "thesis_status": "INSUFFICIENT",
        "confidence":    0.0,
        "diagnosis":     check.to_dict(),
    }
