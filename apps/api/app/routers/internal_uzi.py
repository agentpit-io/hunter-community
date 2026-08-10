"""hunter-UZI-Skill 集成 · Phase 1 MVP · chat 深度分析 tool 桥接端点

用户在 hunter.agentpit.io/chat 里说"深度分析 601899"或"帮我深度看看紫金矿业"时：
  opencode LLM → uzi_mcp.stock_deep_analysis → POST 本端点 → finance-data 7 数据 → Gemini 合成 markdown

Phase 1（本文件）不调 SG UZI worker · 直接在 hermes-api 里拉数据 + LLM 合成 · 秒级返回。
Phase 2（Sprint 3 P2 后续）会加 /uzi/full_analysis 走 SG 完整 22 dim pipeline · 后台任务 + poll。

对应文档：
- hermes-1/doc/codex/自定义MCP/UZI/Sprint-1-2-完成报告.md §四 P2
- hermes-1/doc/codex/自定义MCP/UZI/04-UZI-Skill集成到hunter-chat方案.md
"""
from __future__ import annotations
import asyncio
import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services import finance_data_client as fd
from app.services.online_analysis.llm_client import get_client

router = APIRouter(prefix="/internal", tags=["mcp-bridge"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "hunter-internal-2026")
_MODEL = os.getenv("AGENT_SUB_UZI_MODEL", "gemini-3.5-flash")


def _auth(request: Request) -> str:
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    user_id = request.headers.get("X-Hunter-User-Id", "").strip()
    logger.info("[uzi] path={} user_id={}", request.url.path, user_id or "(missing)")
    return user_id


class DeepAnalysisIn(BaseModel):
    code: str
    depth: str = "lite"  # 保留字段 · Phase 1 只支持 lite


def _fmt_price_block(quote: dict | None) -> str:
    if not quote:
        return "行情：数据缺失"
    return (
        f"现价 {quote.get('price')} 元 · "
        f"涨跌 {quote.get('change_pct')}% · "
        f"成交额 {quote.get('amount', 0) / 1e8:.2f} 亿 · "
        f"截止 {quote.get('ts', '?')}"
    )


def _fmt_kline_summary(kline: list[dict]) -> str:
    if not kline:
        return "K 线：数据缺失"
    if len(kline) < 2:
        return f"K 线：仅 {len(kline)} 根"
    first, last = kline[0], kline[-1]
    change = ((last.get("close", 0) - first.get("close", 0)) / first.get("close", 1)) * 100
    highs = [b.get("high", 0) for b in kline]
    lows = [b.get("low", 0) for b in kline if b.get("low")]
    return (
        f"近 {len(kline)} 根日线 · "
        f"从 {first.get('ts', first.get('date', '?'))[:10]} {first.get('close')} → "
        f"{last.get('ts', last.get('date', '?'))[:10]} {last.get('close')}（{change:+.2f}%）· "
        f"区间 {min(lows) if lows else '?'} - {max(highs)}"
    )


def _fmt_financials(fin) -> str:
    """finance-data /financial 返回 list of 25 季度报 · 最后一项是最新。"""
    if not fin:
        return "财务：数据缺失"
    if isinstance(fin, list):
        if not fin:
            return "财务：空列表"
        fin = fin[-1]  # 取最新季度
    if not isinstance(fin, dict):
        return "财务：格式异常"
    lines = []
    period = fin.get("m_timetag")
    if period:
        lines.append(f"报告期={period}")
    for key, label in [
        ("s_fa_eps_basic", "EPS"),
        ("s_fa_bps", "BPS"),
        ("du_return_on_equity", "ROE(%)"),
        ("sales_gross_profit", "毛利率(%)"),
        ("inc_revenue_rate", "营收同比(%)"),
        ("inc_net_profit_rate", "净利同比(%)"),
    ]:
        v = fin.get(key)
        if v is not None:
            lines.append(f"{label}={v}")
    return "、".join(lines) if lines else "财务：关键字段缺失"


def _fmt_lhb(lhb: list[dict]) -> str:
    if not lhb:
        return "龙虎榜：近 30 日无上榜（或数据未 seed）"
    lines = [f"共 {len(lhb)} 次上榜"]
    for record in lhb[:5]:
        net = record.get("net_buy_amount") or 0
        try:
            net_str = f"{float(net) / 1e8:+.2f} 亿"
        except (TypeError, ValueError):
            net_str = "?"
        lines.append(
            f"  · {record.get('trade_date')} · 原因={record.get('reason')} · "
            f"净买入={net_str} · 涨跌={record.get('change_pct')}%"
        )
    return "\n".join(lines)


def _fmt_research(reports: list[dict]) -> str:
    """近期研报（评级 / 目标价 / 标题）· 显示前 5 篇 · 帮 LLM 感知机构一致预期。"""
    if not reports:
        return "研报：近期无（或数据未 seed）"
    lines = [f"共 {len(reports)} 篇研报（显示最新 5 篇）"]
    for r in reports[:5]:
        date = str(r.get("publish_date") or "?")[:10]
        org = r.get("org_name") or "?"
        rating = r.get("rating") or ""
        tp = r.get("target_price")
        tp_str = f" · 目标价={tp}" if tp else ""
        lines.append(f"  · [{date}] {org} {rating}{tp_str} · {r.get('title', '')[:60]}")
    return "\n".join(lines)


def _fmt_governance(g) -> str:
    if not g:
        return "治理：数据未 seed"
    if isinstance(g, list):
        if not g: return "治理：数据未 seed"
        g = g[0]
    if not isinstance(g, dict):
        return "治理：格式异常"
    parts = []
    for k, label in [
        ("top_shareholder_pct", "第一大股东"),
        ("top5_pct", "前五合计"),
        ("top10_pct", "前十合计"),
        ("pledge_pct", "质押率"),
    ]:
        v = g.get(k)
        if v is not None:
            parts.append(f"{label}={v}%")
    return "、".join(parts) if parts else "治理：字段缺失"


def _fmt_fund_holders(holders: list[dict]) -> str:
    if not holders:
        return "十大股东：数据未 seed"
    top3 = holders[:3]
    lines = [f"前三大：" + " / ".join(
        f"{h.get('holder_name', '?')}({h.get('shares_pct')}%)" for h in top3
    )]
    return "\n".join(lines)


def _fmt_news(news: list[dict], limit: int = 5) -> str:
    if not news:
        return "近期新闻：0 条"
    lines = []
    for n in news[:limit]:
        title = n.get("title", "?")
        date = (n.get("publish_date") or n.get("published") or "?")[:10]
        lines.append(f"  · [{date}] {title}")
    return "\n".join(lines)


def _build_llm_context(code: str, bundle: dict) -> str:
    """把 8 数据组织成给 Gemini 的上下文 · 尽量密集不冗余。"""
    return f"""基于以下真实数据（全部来自内部 finance-data 平台 · 只用这些数据 · 不要外推），生成结构化"深度分析卡片"markdown 摘要（500-800 字）。

标的：{code}
分析深度：lite

⚠️ **严格要求**：
1. 直接从 "### 一、多空核心观点" 开始输出 · 不要任何前言 / 元描述 / 草稿
2. 全部使用中文 · 除标的代码/百分号外
3. 数据未 seed 的维度直接注明"数据未 seed"或跳过 · **绝对不要编造数据**
4. **合规硬约束（不可违反）**：
   - **不给"买入 / 卖出 / 增持 / 减持"评级** · 用"值得关注 / 需观察 / 暂时旁观"这类研究性表述
   - **匿名化游资/席位名**：龙虎榜里如出现"章盟主 / 赵老哥 / 佛山无影脚"等游资/席位真名 · 改用"A 席位 / B 席位"或"活跃席位"泛化描述
   - **不做投资建议** · 只做数据观察与研究判断
   - **不写免责声明** · 已由平台侧统一处理

# 数据

## 1. 实时行情
{_fmt_price_block(bundle.get("quote"))}

## 2. K 线（近 30 日）
{_fmt_kline_summary(bundle.get("kline") or [])}

## 3. 财务（TTM · 最新季）
{_fmt_financials(bundle.get("financials"))}

## 4. 龙虎榜（近 30 日）
{_fmt_lhb(bundle.get("lhb") or [])}

## 5. 十大流通股东（最新季度）
{_fmt_fund_holders(bundle.get("fund_holders") or [])}

## 6. 治理指标
{_fmt_governance(bundle.get("governance"))}

## 7. 近期公告 / 新闻
{_fmt_news(bundle.get("news") or [])}

## 8. 近期研报（券商一致预期）
{_fmt_research(bundle.get("research") or [])}

# 输出要求

严格按以下 markdown 结构：

### 一、多空核心观点（各 2 句）
- **多头**：...
- **空头**：...

### 二、技术面（1 段 · 2-3 句）
（结合 K 线趋势 / 高低点位置 / 成交额）

### 三、基本面（1 段 · 2-3 句）
（结合财务 · ROE · 增速 · 治理）

### 四、资金/情绪信号（1 段 · 2-3 句）
（结合龙虎榜 · 十大股东 · 新闻主线 · 研报评级）

### 五、催化 / 风险（各 2 条 bullet）
- 催化 1: ...
- 催化 2: ...
- 风险 1: ...
- 风险 2: ...

### 六、结论（1 句）
一句话说清"当前性价比 / 关注度"（研究性表述 · 不做投资建议）。

⚠️ 数据缺失的维度直接注明"数据未 seed"或跳过 · 不编造。
"""


def _clean_llm_markdown(md: str) -> str:
    """剥 Gemini 的 draft / review 元话唠 · 保守策略：
    1. 找**最后一次** '### 一、多空核心观点' 出现的行作为正文起点（跳过前面的 outline plan）
    2. 从 start 往后扫 · 只在遇到明确的英文元话唠时截断（Let's / Reviewing / Checking / Note:）
       · 不因 '- ' bullet 截断 · 结论段可能就是列表
    3. 或遇到第二次 '### 一、多空核心观点'（LLM 重开一遍）时截断
    """
    if not md:
        return md
    lines = md.split("\n")
    # 找最后一次 (line 内容真的以 "### 一、" 开头 · 无缩进无 bullet marker)
    start_idx = None
    for i, line in enumerate(lines):
        if line.startswith("### 一、"):
            start_idx = i
    if start_idx is None:
        return md.strip()

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        # 只在明确英文元话唠时截断
        if stripped.startswith(("Let's", "Let me", "Reviewing", "Checking",
                                "Note:", "Now, let", "```")):
            end_idx = i
            break
        # 或 LLM 重新开一份报告
        if line.startswith("### 一、"):
            end_idx = i
            break

    return "\n".join(lines[start_idx:end_idx]).strip()


def _stock_name(code: str) -> str:
    """从 STOCK_MAP / dynamic_map / DB watchlist 拿股票中文名。"""
    from app.config import STOCK_MAP
    bare = code.split(".")[0]
    s = STOCK_MAP.get(bare) or fd._dynamic_map.get(bare)
    if s and s.get("name"):
        return s["name"]
    # DB 兜底
    try:
        from app.services.database import get_conn
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM stocks WHERE code = %s AND deleted = FALSE LIMIT 1",
                (bare,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
    except Exception as _e:
        pass
    return bare


@router.post("/uzi/stock_deep_analysis")
async def deep_analysis(body: DeepAnalysisIn, request: Request):
    """chat 里深度分析入口 · Phase 1 · 秒级返回 markdown。"""
    _auth(request)
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "code 不能为空")

    t0 = datetime.now()
    # 同步 httpx 客户端 · 在 async endpoint 里必须走 to_thread 避免阻塞事件循环
    # （从 async 直接 sync 调 httpx.get 会遇到 connection pool 或事件循环冲突 · 表现为 None 返回）
    sym = fd.to_symbol(code)
    try:
        results = await asyncio.gather(
            asyncio.to_thread(fd.get_quote, code),
            asyncio.to_thread(fd.get_kline, code, "daily", 30),
            asyncio.to_thread(fd._get, f"/api/v1/financial/{sym}") if sym else asyncio.sleep(0, result=None),
            asyncio.to_thread(fd.get_lhb, code, 30),
            asyncio.to_thread(fd.get_fund_holders, code),
            asyncio.to_thread(fd.get_governance, code),
            asyncio.to_thread(fd.get_news, code, 8),
            asyncio.to_thread(fd.get_research_reports, code, 10),
            return_exceptions=True,
        )
        quote, kline, financials, lhb, fund_holders, governance, news, research = [
            (None if isinstance(r, Exception) else r) for r in results
        ]
    except Exception as e:
        logger.exception("[uzi] 拉数失败 code=%s", code)
        raise HTTPException(502, f"拉取数据失败: {e}")

    bundle = {
        "quote": quote,
        "kline": kline,
        "financials": financials,
        "lhb": lhb,
        "fund_holders": fund_holders,
        "governance": governance,
        "news": news,
        "research": research,
    }

    # 走 OneAPI Gemini 合成
    client = get_client()
    if client is None:
        raise HTTPException(503, "LLM 客户端不可用 · 检查 OPENAI_BASE_URL / OPENAI_API_KEY")

    system_msg = (
        "你是一位专业的 A 股 / 港股 / 美股深度分析师。"
        "**只输出最终 markdown 报告本体**，不做任何思考过程 / 草稿 / 数据罗列 / 分析步骤说明。"
        "不写 'Let me analyze' / 'I will analyze' / 'Analysis of' / 'Draft Structure' / '### Draft' 等元描述。"
        "从 '### 一、多空核心观点' 开头 · 到 '### 六、结论' 结束 · 中间只保留正文。"
        "全程使用中文（除股票代码外）· 不做免责声明 · 不给'买入/卖出'评级。"
    )
    user_msg = _build_llm_context(code, bundle)
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.35,
            max_tokens=1200,
        )
        markdown = (resp.choices[0].message.content or "").strip()
        markdown = _clean_llm_markdown(markdown)
    except Exception as e:
        logger.exception("[uzi] LLM 失败 code=%s", code)
        raise HTTPException(502, f"LLM 合成失败: {e}")

    duration_ms = int((datetime.now() - t0).total_seconds() * 1000)
    dims_covered = [k for k, v in bundle.items() if v not in (None, [], {})]
    dims_missing = [k for k in bundle if k not in dims_covered]

    return {
        "ok": True,
        "code": code,
        "name": _stock_name(code),
        "depth": body.depth,
        "markdown": markdown,
        "dims_covered": dims_covered,
        "dims_missing": dims_missing,
        "duration_ms": duration_ms,
        "model": _MODEL,
        "note": "Phase 1 MVP · 数据源 finance-data · LLM=OneAPI Gemini · 完整 22 dim 报告见 SG UZI worker（后续 phase）",
    }
