"""持仓哨兵 · 异动归因引擎（MVP）

MVP 4 条原则：
  1. 不做因子分解（β/α）
  2. 不做 RAG 向量召回
  3. 不做多 Agent 协作
  4. 一次 LLM 调用直接出 JSON 结论

数据输入：
  - 个股名称 / 代码 / 当日涨跌幅
  - 用户的 thesis_text（买入逻辑原文）
  - 近 7 天个股新闻摘要（最多 10 条标题）
  - 大盘 4 个指数当日涨跌（上证/沪深300/创业板/科创50）
  - 个股所属行业 + 当日同步性（上涨家数 / 下跌家数 / 平均涨跌）

输出：
  {
    "thesis_status": "INTACT" | "WEAKENING" | "BROKEN",
    "confidence":    0.0 - 1.0,
    "reasoning":     "中文说明 100 字内",
    "user_message":  "推送给用户的一段话 80 字内",
    "evidence":      ["证据点 1", "证据点 2", ...]
  }

V1 增量计划：因子分解（β/α）、RAG 向量召回、kill_condition 触发、多 agent 协作。
"""
import json
import os
import re
from typing import Optional
from loguru import logger
from openai import OpenAI


CST_OFFSET = 8  # 仅给 prompt 描述时间用
_LLM_TIMEOUT_SECS = 30
_MAX_NEWS_ITEMS = 10

# 关注的大盘指数（akshare 代码 → 显示名）
_KEY_INDICES = {
    "000001": "上证综指",
    "000300": "沪深300",
    "399006": "创业板指",
    "000688": "科创50",
}


def fetch_market_context(stock_code: str) -> dict:
    """拉大盘指数 + 个股所属行业 + 同行业当日同步性。
    失败时返回带 _error 的兜底结构，让 analyze() 仍能跑（缺数据但不挂）。
    """
    out = {"indices": {}, "industry": None, "sector_stats": None, "_errors": []}

    # 1. 大盘指数
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        for _, row in df.iterrows():
            ak_code = str(row.get("代码", ""))
            if ak_code in _KEY_INDICES:
                out["indices"][_KEY_INDICES[ak_code]] = float(row.get("涨跌幅") or 0)
    except Exception as e:
        out["_errors"].append(f"indices: {e}")
        logger.warning("fetch_market_context indices failed: {}", e)

    # 2. 个股所属行业
    try:
        import akshare as ak
        info = ak.stock_individual_info_em(symbol=stock_code)
        # info 是 DataFrame，含 "item" / "value" 两列
        for _, row in info.iterrows():
            if row.get("item") == "行业":
                out["industry"] = str(row.get("value") or "").strip()
                break
    except Exception as e:
        out["_errors"].append(f"industry: {e}")
        logger.warning("fetch_market_context industry({}) failed: {}", stock_code, e)

    # 3. 同行业同步性（基于同花顺板块汇总）
    if out["industry"]:
        try:
            import akshare as ak
            df = ak.stock_board_industry_summary_ths()
            ind = out["industry"]
            # 同花顺板块名跟东财行业名不一定完全相同，做模糊匹配（包含关系）
            match = df[df["板块"].str.contains(ind, na=False)]
            if match.empty:
                # 反向匹配：板块名是个股行业的子串
                match = df[df["板块"].apply(lambda b: b in ind if isinstance(b, str) else False)]
            if not match.empty:
                row = match.iloc[0]
                up   = int(row.get("上涨家数") or 0)
                down = int(row.get("下跌家数") or 0)
                total = up + down
                out["sector_stats"] = {
                    "sector_name":   str(row.get("板块") or ind),
                    "change_pct":    float(row.get("涨跌幅") or 0),
                    "up_count":      up,
                    "down_count":    down,
                    "total":         total,
                    "down_ratio":    round(down / total, 3) if total else None,
                }
        except Exception as e:
            out["_errors"].append(f"sector_stats: {e}")
            logger.warning("fetch_market_context sector_stats({}) failed: {}", out["industry"], e)

    return out


_SYSTEM_PROMPT = """你是「持仓哨兵」AI 投研助手。用户持有一只股票，并在买入时写下了买入逻辑（thesis）。
今天这只股票发生了明显涨跌异动。你的任务：基于用户的买入逻辑 + 当日异动 + 近期新闻，判断
**用户的核心投资逻辑是否依然成立**。

判断标准：
  - INTACT：买入逻辑完全未受影响，仅技术性波动 / 板块联动 → 用户不必恐慌
  - WEAKENING：部分子论据出现挑战（例如新闻面有逆风信号，但还没成为定局）→ 用户需关注
  - BROKEN：核心论据被否定（例如公司基本面恶化、行业格局逆转、出现 kill condition）→ 建议复盘

**输出格式（极其严格）**：
你的回复**第一个字符必须是 `{`，最后一个字符必须是 `}`**。**不要任何前后文本、不要解释、不要 markdown 包裹**。
直接输出以下 JSON 结构：

{
  "thesis_status": "INTACT" 或 "WEAKENING" 或 "BROKEN",
  "confidence":    0.0 到 1.0 之间的小数,
  "reasoning":     一段 100 字内的中文 reasoning,
  "user_message":  80 字内的中文推送语,
  "evidence":      ["证据 1", "证据 2"]
}
"""


def _build_user_prompt(
    code: str, name: str, change_pct: float,
    thesis_text: str,
    news_titles: list[str],
    market_context: dict | None = None,
    shares: Optional[int] = None,
    cost_price: Optional[float] = None,
) -> str:
    """组装给 LLM 的用户消息。"""
    direction = "下跌" if change_pct < 0 else "上涨"

    parts = [
        f"# 异动股票",
        f"  {name}（{code}）  今日{direction} {change_pct:+.2f}%",
    ]
    if shares is not None:
        parts.append(f"  用户持仓 {shares} 股")
        if cost_price:
            parts.append(f"  买入均价 ¥{cost_price}")

    # 大盘指数
    if market_context and market_context.get("indices"):
        parts += ["", "# 大盘当日涨跌"]
        for n, pct in market_context["indices"].items():
            parts.append(f"  {n}: {pct:+.2f}%")

    # 同行业同步性
    if market_context and market_context.get("sector_stats"):
        s = market_context["sector_stats"]
        parts += [
            "",
            f"# 同行业当日同步性（{s['sector_name']}）",
            f"  板块整体: {s['change_pct']:+.2f}%",
            f"  上涨 {s['up_count']} 家 / 下跌 {s['down_count']} 家 / 共 {s['total']} 家"
            + (f"（下跌占比 {s['down_ratio']*100:.0f}%）" if s.get("down_ratio") is not None else ""),
        ]
    elif market_context and market_context.get("industry"):
        parts += ["", f"# 所属行业: {market_context['industry']}（同步性数据缺失）"]

    parts += [
        "",
        "# 用户买入时的核心投资逻辑（thesis）",
        f"  {thesis_text.strip()}",
        "",
        "# 近 7 天个股相关新闻标题（按时间倒序）",
    ]
    if not news_titles:
        parts.append("  （无相关新闻）")
    else:
        for i, t in enumerate(news_titles[:_MAX_NEWS_ITEMS], 1):
            parts.append(f"  {i}. {t}")

    parts += [
        "",
        f"# 任务",
        f"  对照用户的买入逻辑，结合大盘 / 行业同步性 / 新闻面，判断本次异动是否动摇核心论据。",
        f"  - 大盘 + 行业 同向下跌 → 系统性回调，倾向 INTACT",
        f"  - 个股逆板块独立下跌 + 新闻有负面 → WEAKENING 或 BROKEN",
        f"  必须严格按 system prompt 里的 JSON schema 返回，不要包裹任何 markdown。",
    ]
    return "\n".join(parts)


def _parse_llm_json(raw: str) -> dict:
    """鲁棒提取 LLM 返回的 JSON。常见杂质：
    - markdown 包裹 ```json ... ```
    - JSON 前后有解释性文字（Gemini 容易这样）
    - JSON 中嵌套对象的大括号
    """
    raw = raw.strip()
    # 1. 剥 markdown 包裹
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # 2. 直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 3. 找第一个 `{` 到最后一个 `}`（懒方案，应对前后有废话）
    first = raw.find("{")
    last  = raw.rfind("}")
    if first >= 0 and last > first:
        snippet = raw[first:last+1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass
    # 4. 再试：贪婪匹配第一个完整的对象
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return json.loads(m.group(0))
    raise json.JSONDecodeError("No JSON object found", raw, 0)


def analyze(
    code: str, name: str, change_pct: float,
    thesis_text: str,
    news_titles: list[str],
    market_context: dict | None = None,
    shares: Optional[int] = None,
    cost_price: Optional[float] = None,
) -> dict:
    """跑一次归因，返回结构化结论。失败时返回带 status=ERROR 的兜底结构。

    market_context 可由 fetch_market_context(code) 提前拉好传入；不传则不带大盘/板块证据
    （LLM 仍能基于新闻 + thesis 给结论，质量较弱）。
    """
    if not thesis_text.strip():
        return {
            "thesis_status": "INTACT",
            "confidence":    0.0,
            "reasoning":     "用户未填写买入逻辑，无法判断核心论据是否动摇",
            "user_message":  f"{name} 今日{('下跌' if change_pct<0 else '上涨')} {change_pct:+.2f}%",
            "evidence":      [],
            "_no_thesis":    True,
        }

    base_url = os.environ.get("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
    api_key  = os.environ.get("ONE_API_KEY",  "")
    model    = os.environ.get("ONE_API_MODEL", "gemini-3-flash-preview")

    if not api_key:
        logger.warning("attribution: ONE_API_KEY 未配置，跳过归因")
        return {
            "thesis_status": "INTACT",
            "confidence":    0.0,
            "reasoning":     "LLM 网关未配置，无法跑归因",
            "user_message":  f"{name} 今日 {change_pct:+.2f}%",
            "evidence":      [],
            "_no_llm":       True,
        }

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=_LLM_TIMEOUT_SECS)
        # 先尝试 OpenAI 兼容的 JSON mode；网关不支持时回退到普通模式
        common_kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": _build_user_prompt(
                    code, name, change_pct, thesis_text, news_titles, market_context, shares, cost_price,
                )},
            ],
            max_tokens=2000,
            temperature=0.3,
            tools=[{"type": "google_search"}],
        )
        try:
            completion = client.chat.completions.create(
                **common_kwargs,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            # 网关 / 模型不支持 response_format 时回退
            logger.info("attribution {} json_mode unsupported ({}), fallback", code, e)
            completion = client.chat.completions.create(**common_kwargs)
        raw = completion.choices[0].message.content or ""
        logger.info("attribution {} raw_len={} usage={}", code, len(raw),
                    getattr(completion, "usage", None))
        data = _parse_llm_json(raw)

        # 容错：补默认字段
        data.setdefault("thesis_status", "INTACT")
        data.setdefault("confidence", 0.5)
        data.setdefault("reasoning", "")
        data.setdefault("user_message", f"{name} 今日 {change_pct:+.2f}%")
        data.setdefault("evidence", [])

        # 校验 status 取值
        if data["thesis_status"] not in ("INTACT", "WEAKENING", "BROKEN"):
            data["thesis_status"] = "INTACT"

        return data
    except Exception as e:
        logger.exception("attribution {} failed: {}", code, e)
        return {
            "thesis_status": "INTACT",
            "confidence":    0.0,
            "reasoning":     f"归因调用失败：{e}",
            "user_message":  f"{name} 今日 {change_pct:+.2f}%（归因调用异常，请人工查看）",
            "evidence":      [],
            "_error":        str(e),
        }
