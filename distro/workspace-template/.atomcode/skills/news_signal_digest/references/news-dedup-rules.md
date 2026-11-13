# 多源新闻水位线去重与事件聚类规范（`news_signal_digest` 参考规范）

## 一、水位线去重机制（Watermark Deduplication）

1. **输入水位线（`watermark_in`）**：仅保留 `published_at >= watermark_in` 的增量资讯。
2. **事件聚类指纹（`event_fingerprint`）**：同一上市公司同一交易日针对同一财报/公告的多家媒体报道合并为 1 条独立事件，记录 `duplicate_count` 与 `merged_sources`。
3. **输出水位线（`watermark_out`）**：更新为本批有效事件中最新的 `published_at`，供下一轮增量扫描使用。
