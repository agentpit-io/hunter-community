"""F10 · 模板化文本相似度检测（简化版，用 difflib）

识别"复制粘贴式投毒" — 水军/软文常用模板批量生成，文本相似度极高。

简化策略（MVP）：
  - difflib.SequenceMatcher 计算 ratio
  - 不依赖 embedding 模型（避免引入 sentence-transformers）
  - 单聚类阈值 0.8，单聚类至少 3 条算"模板攻击"

V1 升级：接 BAAI/bge-small-zh embedding 或 Cohere 云端 API
"""
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .unified_fetcher import NewsItem

log = logging.getLogger(__name__)


_SIMILARITY_THRESHOLD = 0.8       # 相似度 ≥ 0.8 算同模板
_MIN_CLUSTER_SIZE     = 3         # 至少 3 条相似才算模板攻击
_TEMPLATE_PENALTY     = 0.1       # 模板化新闻权重降到 0.1


# 财经 PR 话术词库（命中即可识别为营销套话，即使没批量复制）
PR_PHRASE_KEYWORDS = [
    # 估值/价格预期类
    "前景广阔", "前景一片光明", "前景可期", "潜力无限", "潜力巨大",
    "目标价", "上看", "翻倍", "倍空间", "万亿市场", "千亿赛道",
    "百亿级", "黄金赛道",
    # 评级/推荐类
    "强烈推荐", "强推", "首选", "首推", "核心受益", "深度受益",
    "重磅利好", "重大利好",
    # 龙头/护城河类
    "龙头地位", "无可撼动", "稳如泰山", "护城河深", "绝对龙头",
    "霸主地位", "王者地位",
    # 必涨/必买类
    "必涨", "必买", "齐声看多", "齐声看好", "全面看好",
    # 业内/专家类（模糊主张）
    "业内人士", "业内专家", "市场人士", "知情人士", "消息人士",
    # 信息蹭热点类
    "独家爆料", "重磅消息", "突发利好", "罕见信号",
    # 模糊预测
    "有望", "将迎来", "或将", "预计大幅",
    # 拉升/冲高/突破类
    "拉升", "冲高", "暴涨", "暴跌", "井喷", "飙升", "狂飙",
    "突破新高", "再创新高", "强势涨停",
    # 模糊评价
    "亮眼", "超预期", "景气向上", "高景气",
    # 价值/赛道类（无数据支撑）
    "价值洼地", "黄金坑", "战略机遇", "时代红利",
    # 散户情绪类
    "牛市", "起飞", "上车", "赶紧", "抓紧",
]


def detect_pr_phrases(news_items: list) -> dict:
    """词级 PR 话术检测 + 自动降权（独立于相似度聚类）

    哪怕 PR 软文没批量复制，只要标题里命中典型营销词，就降权 + 标记。

    降权规则:
      - 命中 ≥ 2 个营销词 → 权重直接 = 0.15（重度投毒）
      - 命中 1 个营销词   → 权重不超过 0.40（轻度可疑）
      - 权威源（≥ 0.85）即使命中也不降权（巨潮公告偶尔用"重磅"等词，但内容真实）
    """
    hits = []
    for i, n in enumerate(news_items):
        title = getattr(n, "title", "") or ""
        content = getattr(n, "content", "") or ""
        text = title + " " + content[:200]
        matched = [kw for kw in PR_PHRASE_KEYWORDS if kw in text]
        if not matched:
            continue

        orig_weight = getattr(n, "source_weight", 0.5)
        # 权威源（≥ 0.85）即使命中关键词也不降权
        if orig_weight >= 0.85:
            severity = "ignored_authoritative"
            new_weight = orig_weight
        elif len(matched) >= 2:
            severity = "heavy"
            new_weight = 0.15
        else:
            severity = "light"
            new_weight = min(orig_weight, 0.40)

        # 实际降权
        if new_weight < orig_weight and hasattr(n, "source_weight"):
            n.source_weight = new_weight

        hits.append({
            "index":     i,
            "title":     title[:80],
            "keywords":  matched[:3],
            "source":    getattr(n, "source_name", "未知"),
            "weight_before": orig_weight,
            "weight_after":  new_weight,
            "severity":  severity,
        })

    heavy_count = sum(1 for h in hits if h["severity"] == "heavy")
    light_count = sum(1 for h in hits if h["severity"] == "light")
    return {
        "is_pr_phrase_found": len(hits) > 0,
        "hits":               hits,
        "count":              len(hits),
        "heavy_count":        heavy_count,
        "light_count":        light_count,
    }


@dataclass
class TemplateCluster:
    indices: list[int]
    representative: str
    avg_similarity: float


@dataclass
class TemplateDetectionResult:
    is_template_attack:  bool
    clusters:            list[TemplateCluster] = field(default_factory=list)
    affected_count:      int = 0
    template_ratio:      float = 0.0    # 被识别为模板的新闻 / 总数

    def to_dict(self) -> dict:
        return {
            "is_template_attack": self.is_template_attack,
            "clusters": [
                {
                    "indices":        c.indices,
                    "representative": c.representative,
                    "avg_similarity": round(c.avg_similarity, 3),
                    "size":           len(c.indices),
                }
                for c in self.clusters
            ],
            "affected_count":     self.affected_count,
            "template_ratio":     round(self.template_ratio, 3),
        }


def _similarity(a: str, b: str) -> float:
    """字符串相似度（normalized 0-1）"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def detect_templates(news_items: list[NewsItem]) -> TemplateDetectionResult:
    """聚类相似标题，识别模板批量投毒"""
    if len(news_items) < _MIN_CLUSTER_SIZE:
        return TemplateDetectionResult(is_template_attack=False)

    n = len(news_items)
    visited = [False] * n
    clusters: list[TemplateCluster] = []

    for i in range(n):
        if visited[i]:
            continue
        cluster_indices = [i]
        sim_sum = 0.0
        sim_count = 0
        for j in range(i+1, n):
            if visited[j]:
                continue
            s = _similarity(news_items[i].title, news_items[j].title)
            if s >= _SIMILARITY_THRESHOLD:
                cluster_indices.append(j)
                visited[j] = True
                sim_sum += s
                sim_count += 1

        if len(cluster_indices) >= _MIN_CLUSTER_SIZE:
            visited[i] = True
            clusters.append(TemplateCluster(
                indices        = cluster_indices,
                representative = news_items[i].title,
                avg_similarity = sim_sum / max(1, sim_count),
            ))

    affected = sum(len(c.indices) for c in clusters)
    ratio    = affected / n
    is_attack = ratio >= 0.20    # 20%+ 是模板就算攻击

    log.info(
        "F10 template detection: {} items, {} clusters, {} affected, ratio={:.2f}, attack={}",
        n, len(clusters), affected, ratio, is_attack,
    )
    return TemplateDetectionResult(
        is_template_attack = is_attack,
        clusters           = clusters,
        affected_count     = affected,
        template_ratio     = ratio,
    )


def apply_template_penalty(news_items: list[NewsItem],
                            result: TemplateDetectionResult) -> int:
    """对识别为模板的新闻降权到 0.1（不删除，仍保留可追溯）"""
    if not result.is_template_attack:
        return 0

    affected_indices = set()
    for c in result.clusters:
        for idx in c.indices:
            affected_indices.add(idx)

    count = 0
    for idx in affected_indices:
        # 权威源不降权（巨潮公告即使长得像也是真的）
        if news_items[idx].source_weight >= 0.85:
            continue
        news_items[idx].source_weight = _TEMPLATE_PENALTY
        count += 1
    return count
