"""主编排器 · 跑完整 4 列分析流程

并发跑：
  - 列 2 naive_pipeline（套壳 AI）
  - 列 3 五层防御（F1 → F9/F10 → F7 → F8 → F5）

实时通过 SSE 推送每个阶段状态给前端。
"""
import asyncio
import time
from datetime import datetime
from typing import Callable, Awaitable, Optional

from loguru import logger

from .unified_fetcher import UnifiedFetcher, FetchResult
from .signal_sufficiency import check_sufficiency, build_insufficient_card
from .contrarian_search import search_contrarian
from .sentiment_analyzer import detect_sentiment_anomaly, adjust_weights_for_anomaly
from .template_detector import detect_templates, apply_template_penalty, detect_pr_phrases
from .fact_extractor import extract_facts
from .attribution_reasoner import reason_attribution
from .naive_pipeline import run_naive_pipeline
from .llm_client import estimate_cost_cny


# SSE 事件类型
EventEmitter = Callable[[str, dict], Awaitable[None]]


async def _noop_emit(event_type: str, data: dict) -> None:
    """默认空 emitter（无 SSE 时也能跑）"""
    pass


# ─── 列 3 五层防御 ─────────────────────────────────────────────────────

async def run_defense_pipeline(stock_name: str, change_pct: float,
                                fetch_result: FetchResult,
                                thesis_text: str, kill_conditions: list[dict],
                                emit: EventEmitter = _noop_emit) -> dict:
    """五层防御管道，按 demo v10 严格分层

    Returns:
        {
          "steps": [...],
          "conclusion": {...},    // F7 Stage B 结果
          "facts":      {...},    // F7 Stage A 结果
          "metadata":   {...}     // 各层统计
        }
    """
    steps: list[dict] = []
    metadata: dict = {}

    # ─── 第 1 层 · 信源层 (F1 + F2 + F6) ──────────────────────────────
    # 按信源类型分组统计（让 detail 更具体）
    src_counter: dict[str, int] = {}
    for n in fetch_result.news_items:
        src_counter[n.source_name] = src_counter.get(n.source_name, 0) + 1
    top_sources = sorted(src_counter.items(), key=lambda x: -x[1])[:5]
    src_summary = "、".join(f"{name}({cnt})" for name, cnt in top_sources)

    # 分级统计权威/一般/低质（让用户看到我们抓了三档）
    n_premium = sum(1 for n in fetch_result.news_items if n.source_weight >= 0.7)
    n_low     = sum(1 for n in fetch_result.news_items if n.source_weight <= 0.35)
    n_general = len(fetch_result.news_items) - n_premium - n_low
    layer1 = {
        "name":   "🛡 第 1 层 · 信源层",
        "status": "done",
        "detail_lines": [
            f"总抓取 {len(fetch_result.news_items)} 条新闻｜权威源 {n_premium} 条（权重≥0.7）｜一般源 {n_general} 条｜低质源 {n_low} 条（权重≤0.35）",
            f"主要来源：{src_summary}" if src_summary else "暂无新闻",
            "ℹ 所有新闻都参与分析。权重越高，越优先被 AI 采信为「事实」；权重越低，越可能进入「已拦截」Tab",
        ],
    }
    if not fetch_result.has_authoritative:
        layer1["status"] = "warn"
    steps.append(layer1)
    await emit("layer_done", {"layer": 1, "step": layer1})
    metadata["layer1"] = {
        "successful_sources": fetch_result.successful_sources,
        "failed_sources":     fetch_result.failed_sources,
        "authoritative_count":fetch_result.authoritative_count,
    }

    # ─── 第 2 层 · 内容层 (F9 + F10) ────────────────────────────────
    sentiment_result = detect_sentiment_anomaly(fetch_result.news_items)
    template_result  = detect_templates(fetch_result.news_items)
    pr_phrase_result = detect_pr_phrases(fetch_result.news_items)   # 词级 PR 话术检测

    # 应用降权
    if sentiment_result.is_anomaly:
        adjust_weights_for_anomaly(fetch_result.news_items, sentiment_result)
    template_penalty_count = apply_template_penalty(fetch_result.news_items, template_result)

    pos_pct = sentiment_result.distribution.get('positive', 0) * 100
    neg_pct = sentiment_result.distribution.get('negative', 0) * 100
    neu_pct = sentiment_result.distribution.get('neutral', 0) * 100
    # 情绪基线：正常财经新闻 25% 正 / 20% 负 / 55% 中
    sentiment_baseline_ok = (
        pos_pct <= 65 and  # 不极端正面
        neg_pct <= 55 and  # 不极端负面
        neu_pct >= 15      # 至少有一定中性
    )

    detail_2 = []
    # 情绪检测结果 — 总是带产品价值标识（✓ 通过 / ⚠ 异常）
    if sentiment_result.is_anomaly:
        detail_2.append(f"⚠ F9 情绪一边倒：正面{pos_pct:.0f}% 负面{neg_pct:.0f}% 中性{neu_pct:.0f}%（偏离正常基线 {sentiment_result.deviation_factor:.1f} 倍 — 疑似 PR 集中投放）")
    elif neu_pct >= 95:
        # 100% 中性时给友好解释（说明都是公告/财报类客观陈述）
        detail_2.append(f"✓ F9 情绪客观：正面{pos_pct:.0f}% 负面{neg_pct:.0f}% 中性{neu_pct:.0f}%（多为公告/财报客观陈述，无情绪化语言 — 这是好事）")
    else:
        detail_2.append(f"✓ F9 情绪分布正常：正面{pos_pct:.0f}% 负面{neg_pct:.0f}% 中性{neu_pct:.0f}%（已与正常基线 25/20/55 比对）")

    # 模板化相似度检测
    if template_result.is_template_attack:
        detail_2.append(f"⚠ F10 模板化批量投放：{len(template_result.clusters)} 组聚类，{template_result.affected_count} 条相似文本（已自动降权）")
    else:
        detail_2.append(f"✓ F10 模板化扫描：对 {len(fetch_result.news_items)} 条标题做相似度聚类（阈值 0.8），未发现复制粘贴式批量")

    # 词级 PR 话术检测 + 自动降权（让低权重投毒在「原始」Tab 一目了然）
    if pr_phrase_result["count"] > 0:
        heavy = pr_phrase_result.get("heavy_count", 0)
        light = pr_phrase_result.get("light_count", 0)
        detail_2.append(f"⚠ F10 PR 话术检测：命中 {pr_phrase_result['count']} 条 PR 话术（重度 {heavy} 条｜轻度 {light} 条）")
        detail_2.append("   下调权重的含义：F7 LLM 提取事实时会优先采信高权重源，权重 ≤0.40 的内容会被列入「已拦截」")
        # 列全部命中的内容（不再只显 3 条 — 用户要求看到全部）
        for h in pr_phrase_result["hits"]:
            kws = "、".join(h["keywords"])
            sev = "🔴 重度" if h["severity"] == "heavy" else "🟠 轻度"
            detail_2.append(f"   {sev}「{h['title'][:60]}」← 触发词「{kws}」  权重 {h['weight_before']:.2f} → {h['weight_after']:.2f}")
    else:
        detail_2.append(f"✓ F10 PR 话术检测：扫描 50+ 营销词库，无典型话术命中")

    layer2 = {
        "name":   "🔍 第 2 层 · 内容层",
        "status": "warn" if (sentiment_result.is_anomaly or template_result.is_template_attack) else "done",
        "detail_lines": detail_2,
    }
    steps.append(layer2)
    await emit("layer_done", {"layer": 2, "step": layer2})
    metadata["layer2"] = {
        "sentiment": sentiment_result.to_dict(),
        "template":  template_result.to_dict(),
        "template_penalty_count": template_penalty_count,
        "pr_phrase": pr_phrase_result,
    }

    # ─── 第 4 层 · 视野层 (F8 对立面)（先于第 3 层 → 让对立面也进事实提取）───
    contrarian_items = await search_contrarian(stock_name, per_query_limit=5, max_total=10)
    fetch_result.news_items.extend(contrarian_items)
    fetch_result.news_items.sort(key=lambda n: (n.source_weight, n.publish_time), reverse=True)

    # ─── 第 3 层 · 推理层 (F7 Stage A) ──────────────────────────────
    facts_result = await extract_facts(stock_name, fetch_result)
    stats = facts_result["stats"]

    # ★ 把 F10 PR 命中的也合并进 rejected_as_opinion，让"已拦截"Tab 显示完整
    # 用户反馈：F10 命中 4 条但已拦截只显示 3 条 — 因为 F7 LLM 跟 F10 词检测是两套系统
    pr_hits = pr_phrase_result.get("hits", [])
    if pr_hits:
        # 用现有新闻列表回查 url
        news_url_by_idx = {i: (n.url or "") for i, n in enumerate(fetch_result.news_items)}
        news_title_by_idx = {i: (n.title or "") for i, n in enumerate(fetch_result.news_items)}
        if "rejected_as_opinion" not in facts_result:
            facts_result["rejected_as_opinion"] = []
        for h in pr_hits:
            # F10 hits 用的是 fetch_result.news_items 原始 index（在加入对立面之前的）
            idx = h.get("index", -1)
            kws = "、".join(h.get("keywords", []))
            sev_label = "重度 PR 话术" if h.get("severity") == "heavy" else "轻度营销话术"
            facts_result["rejected_as_opinion"].append({
                "text":         h.get("title", "")[:200],
                "reason":       f"F10 词级拦截 · {sev_label}：触发词「{kws}」→ 权重 {h.get('weight_before', 0):.2f} 降至 {h.get('weight_after', 0):.2f}",
                "source_index": idx + 1 if idx >= 0 else 0,
                "source_url":   news_url_by_idx.get(idx, ""),
                "source_title": news_title_by_idx.get(idx, ""),
                "from_layer":   "F10",
            })
        # 重算 stats 反映新口径
        stats["rejected_count"] = len(facts_result["rejected_as_opinion"])
        stats["drop_rate"]      = stats["rejected_count"] / max(1, stats["input_count"])

    # 把全部拦截内容直接展开列出
    all_rejected = facts_result.get("rejected_as_opinion") or []
    rejected_sample_lines = []
    if all_rejected:
        rejected_sample_lines.append("")  # 空行视觉间隔
        for r in all_rejected:
            text = (r.get("text") or "")[:80]
            reason = (r.get("reason") or "")[:100]
            rejected_sample_lines.append(f"  ✗「{text}」")
            if reason:
                rejected_sample_lines.append(f"     ↳ {reason}")

    layer3 = {
        "name":   "⚛ 第 3 层 · 推理层（事实提取）",
        "status": "done",
        "detail_lines": [
            "强制剥离形容词、目标价、模糊预测 — 仅保留可验证事实",
            f"输入 {stats['input_count']} 条 → 保留 {stats['kept_count']} 条事实 / 剔除 {stats['rejected_count']} 条主观",
            f"剔除率 {stats['drop_rate']*100:.0f}%（越高说明投毒越多，抗投毒效果越显著）",
            *rejected_sample_lines,
        ],
    }
    if stats["kept_count"] == 0:
        layer3["status"] = "error"
    steps.append(layer3)
    # 把 F7 提取的事实清单 + 剔除清单一起 emit，让前端列 1 可以分区展示
    await emit("layer_done", {
        "layer":    3,
        "step":     layer3,
        "facts":    facts_result.get("verifiable_facts", []),
        "rejected": facts_result.get("rejected_as_opinion", []),
    })
    metadata["layer3"] = facts_result

    # ─── 第 4 层 · 视野层（呈现）────────────────────────────────────
    # F8 加对立面示例（让用户看到具体找到了什么风险信号）
    cf_sample = contrarian_items[:2]
    cf_lines = [f"  ⚠ {n.title[:55]}" for n in cf_sample]

    # 列出实际搜了哪些关键词（让用户看到我们干了活）
    from .contrarian_search import CONTRARIAN_QUERIES
    all_kws_searched = []
    for cat, kws in CONTRARIAN_QUERIES.items():
        all_kws_searched.extend(kws[:3])
    kws_display = "、".join(all_kws_searched)

    layer4_lines = [
        f"主动搜索 5 个维度共 {len(all_kws_searched)} 个利空关键词：",
        f"  {kws_display}",
    ]
    if contrarian_items:
        layer4_lines.append(f"✓ 找到 {len(contrarian_items)} 条潜在利空（已并入事实清单参与归因）")
        layer4_lines.extend(cf_lines)
    else:
        # 0 条时给更具产品价值的解释
        layer4_lines.append(f"✓ 已搜索全部 {len(all_kws_searched)} 个关键词，未在 24h 内匹配到负面新闻")
        layer4_lines.append("   → 这是好事：通常意味着稳健白马 / 真实利好场景。如果换成造假股或质押爆雷股，这里会列出一长串")

    layer4 = {
        "name":   "🔄 第 4 层 · 视野层（对立面搜索）",
        "status": "done",   # 0 条不算 warn — 找不到利空是好事
        "detail_lines": layer4_lines,
    }
    steps.append(layer4)
    await emit("layer_done", {"layer": 4, "step": layer4})
    metadata["layer4"] = {"contrarian_count": len(contrarian_items)}

    # ─── 第 5 层 · 输出层 (F5 信号充足度) ────────────────────────────
    sufficiency = check_sufficiency(fetch_result)
    layer5 = {
        "name":   "✅ 第 5 层 · 输出层（信号充足度）",
        "status": "done" if sufficiency.is_sufficient else "error",
        "detail_lines": (
            [
                f"输出前最后一道关：信号充足度检查",
                f"权威信源 {sufficiency.details['authoritative_count']} ≥ 1 ✓",
                f"总信源 {sufficiency.details['total_sources']} ≥ 3 ✓",
                f"高权重事实 {sufficiency.details['high_weight_facts']} ≥ 2 ✓",
                "→ 信号充足，允许输出原因分析结论",
            ] if sufficiency.is_sufficient else
            [
                f"输出前最后一道关：信号充足度 — 不通过",
                *[f"✗ {r}" for r in sufficiency.reasons],
                "→ 信号不足，降级为「我不知道」兜底（拒绝瞎编）",
            ]
        ),
    }
    steps.append(layer5)
    await emit("layer_done", {"layer": 5, "step": layer5})
    metadata["layer5"] = sufficiency.to_dict()

    # ─── F7 Stage B 归因（如果信号充足）─────────────────────────────
    if not sufficiency.is_sufficient:
        insufficient = build_insufficient_card(stock_name, change_pct, sufficiency)
        return {
            "steps":      steps,
            "conclusion": insufficient,
            "facts":      facts_result,
            "metadata":   metadata,
        }

    # F7 LLM 失败（数据足但 AI 挂了）— 不走 INSUFFICIENT，返回明确的「AI 临时不可用」
    if facts_result.get("stats", {}).get("llm_failure"):
        ai_failure_card = {
            "title":            "⚠ AI 处理临时不可用",
            "thesis_status":    "INSUFFICIENT",
            "confidence":       0.0,
            "summary":          f"{stock_name} 今日 {change_pct:+.2f}%，数据采集完整（{len(fetch_result.news_items)} 条新闻 / {fetch_result.authoritative_count} 条权威），但 AI 处理这一步暂时失败。",
            "explanations":     [
                f"✓ 数据采集成功（{len(fetch_result.successful_sources)} 个信源）",
                f"✓ 权威源充足（{fetch_result.authoritative_count} 条）",
                f"✗ F7 事实提取阶段 LLM 输出异常（{facts_result['stats'].get('error', 'unknown')}）",
            ],
            "fallback_message": "建议稍后重试一次。如多次失败，可联系管理员。",
            "diagnosis":        {"llm_failure": True, "error": facts_result['stats'].get('error')},
        }
        return {
            "steps":      steps,
            "conclusion": ai_failure_card,
            "facts":      facts_result,
            "metadata":   metadata,
        }

    attribution = await reason_attribution(
        stock_name      = stock_name,
        change_pct      = change_pct,
        thesis_text     = thesis_text,
        kill_conditions = kill_conditions,
        fact_extraction_result = facts_result,
    )

    # ─── 综合归因步骤（展示用）─────────────────────────────────────
    validation = attribution["validation"]
    if validation["passed"]:
        validation_line = "引用校验: ✓ 全部来自事实链"
    else:
        unauthorized_count = len(validation["unauthorized_numbers"])
        validation_line = f"引用校验: ⚠ {unauthorized_count} 个数字越界"

    status_cn = {"INTACT": "买入逻辑未破", "WEAKENING": "部分动摇", "BROKEN": "买入逻辑被破坏"}.get(
        attribution["thesis_status"], attribution["thesis_status"]
    )
    final_step = {
        "name":   "⚙ 综合原因分析（基于前 5 层的最终判断）",
        "status": "done" if validation["passed"] else "warn",
        "detail_lines": [
            f"买入逻辑评估: {status_cn} (置信度 {attribution['confidence']*100:.0f}%)",
            f"利好证据 {len(attribution['positive_evidence'])} 条 / 风险证据 {len(attribution['risk_evidence'])} 条",
            validation_line,
            f"行动建议: {attribution['action_recommendation']}",
        ],
    }
    steps.append(final_step)
    await emit("layer_done", {"layer": 6, "step": final_step})

    return {
        "steps":      steps,
        "conclusion": attribution,
        "facts":      facts_result,
        "metadata":   metadata,
    }


# ─── 主入口 ───────────────────────────────────────────────────────────

async def run_full_analysis(stock_code: str, stock_name: str, change_pct: float,
                              thesis_text: str, kill_conditions: list[dict],
                              emit: EventEmitter = _noop_emit) -> dict:
    """跑完整 4 列分析

    Args:
        stock_code:        finance-data symbol（如 "300750.SZ"）
        stock_name:        股票中文名
        change_pct:        当日波动 %
        thesis_text:       用户 thesis
        kill_conditions:   [{text, type, source}, ...]
        emit:              SSE 推送回调

    Returns:
        完整报告 dict，结构与 online_analysis_report 表对齐
    """
    t0 = time.time()
    await emit("started", {"stock_code": stock_code, "stock_name": stock_name})

    # ─── 阶段 1: 数据采集（共用）──────────────────────────────────
    await emit("stage", {"name": "数据采集", "status": "running"})
    fetcher = UnifiedFetcher()
    fetch_result = await fetcher.fetch_all(stock_code, hours=72, stock_name=stock_name)
    # 推流每条新闻让左列流式渲染
    for n in fetch_result.news_items:
        await emit("news_arrived", n.to_dict())
    await emit("stage", {"name": "数据采集", "status": "done", "count": len(fetch_result.news_items)})

    # ─── 阶段 2 + 3: 双管道并发 ───────────────────────────────────
    async def _naive():
        await emit("stage", {"name": "naive_pipeline", "status": "running"})
        r = await run_naive_pipeline(stock_name, change_pct, fetch_result)
        await emit("naive_done", r)
        return r

    async def _defense():
        await emit("stage", {"name": "defense_pipeline", "status": "running"})
        r = await run_defense_pipeline(stock_name, change_pct, fetch_result,
                                       thesis_text, kill_conditions, emit)
        await emit("defense_done", r)
        return r

    naive_result, defense_result = await asyncio.gather(_naive(), _defense())

    # ─── 汇总 token 成本 ─────────────────────────────────────────
    total_in  = naive_result.get("llm_meta", {}).get("tokens_in", 0)
    total_out = naive_result.get("llm_meta", {}).get("tokens_out", 0)

    # defense 里有 fact + attribution 2 次 LLM
    facts_meta = defense_result.get("facts", {}).get("llm_meta", {})
    attr_meta  = defense_result.get("conclusion", {}).get("llm_meta", {})
    for m in (facts_meta, attr_meta):
        total_in  += m.get("tokens_in", 0)
        total_out += m.get("tokens_out", 0)

    cost_cny = estimate_cost_cny(total_in, total_out)
    duration_ms = int((time.time() - t0) * 1000)

    # ─── 组装最终报告 ────────────────────────────────────────────
    final_conclusion = defense_result["conclusion"]
    thesis_status = final_conclusion.get("thesis_status", "INSUFFICIENT")

    report = {
        "stock_code":       stock_code,
        "stock_name":       stock_name,
        "thesis_text":      thesis_text,
        "kill_conditions":  kill_conditions,

        "news_items":       fetch_result.to_dict(),
        "naive_pipeline":   naive_result,
        "defense_pipeline": defense_result,
        "final_conclusion": final_conclusion,

        "thesis_status":    thesis_status,
        "confidence":       final_conclusion.get("confidence", 0.0),
        "poison_rate":      defense_result.get("facts", {}).get("stats", {}).get("drop_rate", 0.0),
        "llm_tokens":       total_in + total_out,
        "cost_cny":         cost_cny,
        "duration_ms":      duration_ms,
    }

    await emit("done", {
        "thesis_status": thesis_status,
        "confidence":    final_conclusion.get("confidence", 0.0),
        "cost_cny":      cost_cny,
        "duration_ms":   duration_ms,
    })

    logger.info(
        "online_analysis {} {}: status={} tokens={} cost=¥{} duration={}ms",
        stock_code, stock_name, thesis_status, total_in + total_out, cost_cny, duration_ms,
    )
    return report
