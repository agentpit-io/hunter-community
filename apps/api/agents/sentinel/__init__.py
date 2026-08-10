"""在线分析 · 抗投毒体系（F1-F10 核心子集）

模块结构（按 demo v10 五层防御组织）：
- source_registry          信源注册表（tier + weight）
- unified_fetcher          F1 多源并发抓取
- fact_extractor           F7 Stage A 事实提取
- attribution_reasoner     F7 Stage B 归因推理
- contrarian_search        F8 对立面主动搜索
- sentiment_analyzer       F9 情绪极性分布检测（简化版）
- template_detector        F10 模板化文本聚类（简化版 difflib）
- signal_sufficiency       F5 信号充足度兜底
- kill_condition_gen       AI 生成 kill condition
- pipeline                 双管道编排器（naive vs defense）
- sse_stream               SSE 推流封装
"""
