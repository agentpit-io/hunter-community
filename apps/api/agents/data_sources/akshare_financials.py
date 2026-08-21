"""A 股财报双通道 · 同花顺 abstract_ths → 东财 abstract · 一处封装

内部原来分散在 2 个位置:
  · app/routers/internal_uzi._akshare_financials(stock_financial_abstract · 单期 dict · UZI 用)
  · app/services/subagents/watchlist_rank_agent._fetch_financials_akshare(stock_financial_abstract_ths · 多期 list · rank agent 用)

两处字段映射几乎一样,但 akshare 接口不同、返回结构不同 —— §9 铁律 3 的
"同一件事两处实现,一处改另一处不跟"再次发生。这里合到一处:

  · 主路径:同花顺 abstract_ths(indicator="按报告期")· 25-80 行季报 · 字段全
  · Fallback:东财 abstract · 单期 · 覆盖 abstract_ths 不通时
  · 返 list[dict] 按报告期升序 · UZI 消费时取 [-1] 拿最新期

港股 / 美股当前无独立数据源 · 返 None(坦白说不支持,不硬装 mock)。

字段映射(消费方约定的键):
  m_timetag             报告期(YYYYMMDD 或 YYYY-MM-DD 字符串)
  s_fa_eps_basic        基本每股收益 (元)
  s_fa_bps              每股净资产 (元)
  du_return_on_equity   ROE (%)
  sales_gross_profit    销售毛利率 (%)
  inc_revenue_rate      营业总收入同比增长率 (%)
  inc_net_profit_rate   净利润同比增长率 (%)
"""
from __future__ import annotations

from loguru import logger

# 同花顺 abstract_ths 列名 → 消费方字段
_THS_MAP = {
    "基本每股收益":            "s_fa_eps_basic",
    "每股净资产":              "s_fa_bps",
    "净资产收益率":            "du_return_on_equity",
    "营业总收入同比增长率":    "inc_revenue_rate",
    "净利润同比增长率":        "inc_net_profit_rate",
    "销售毛利率":              "sales_gross_profit",
}

# 东财 abstract 指标名 → 消费方字段(fallback 只提供 4 个 · 缺增速)
_EM_MAP = {
    "基本每股收益":            "s_fa_eps_basic",
    "每股净资产":              "s_fa_bps",
    "净资产收益率(ROE)":       "du_return_on_equity",
    "毛利率":                  "sales_gross_profit",
}


def _pct_str(v) -> float | None:
    """同花顺返 '28.17%' / 'False' / '6.98亿' / '--' / '' · 抽 float · 百分号去掉

    带单位(亿/万)的字段对打分无关,只处理纯数字/百分比,其余返 None。
    """
    if v is None or v is False:
        return None
    s = str(v).strip()
    if not s or s.lower() == "false" or s == "--":
        return None
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_ths(bare: str) -> list[dict] | None:
    """通道 1 · 同花顺 stock_financial_abstract_ths · 多期 · 字段全"""
    try:
        import akshare as ak
        df = ak.stock_financial_abstract_ths(symbol=bare, indicator="按报告期")
    except Exception as e:
        logger.warning("[akshare_financials] ths {} 失败: {}", bare, e)
        return None
    if df is None or df.empty:
        return None
    rows: list[dict] = []
    for _, r in df.iterrows():
        period = str(r.get("报告期", "")).strip()
        if not period:
            continue
        row: dict = {"m_timetag": period}
        for col, key in _THS_MAP.items():
            v = _pct_str(r.get(col))
            if v is not None:
                row[key] = v
        rows.append(row)
    # 按报告期升序 · 与消费方(_score_1y[-5:]、_score_3y[-12:]、UZI [-1])对齐
    rows.sort(key=lambda x: x["m_timetag"])
    return rows if rows else None


def _fetch_em(bare: str) -> list[dict] | None:
    """通道 2 · 东财 stock_financial_abstract · 行=指标 列=日期 · 只提取所有期"""
    try:
        import akshare as ak
        df = ak.stock_financial_abstract(symbol=bare)
    except Exception as e:
        logger.warning("[akshare_financials] em {} 失败: {}", bare, e)
        return None
    if df is None or df.empty:
        return None
    date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    if not date_cols:
        return None
    date_cols.sort()  # 升序 · 与 ths 通道对齐
    rows: list[dict] = []
    for period in date_cols:
        row: dict = {"m_timetag": period}
        for _, r in df.iterrows():
            name = str(r.get("指标", "")).strip()
            if name in _EM_MAP:
                v = r.get(period)
                if v is not None and str(v).strip() and str(v) != "nan":
                    try:
                        row[_EM_MAP[name]] = round(float(v), 4)
                    except (TypeError, ValueError):
                        pass
        if len(row) > 1:
            rows.append(row)
    return rows if rows else None


def fetch_financials(bare: str, market: str = "A") -> list[dict] | None:
    """返 list[dict] 按报告期升序 · None = 全部通道失败或不支持

    通道:
      1. 同花顺 stock_financial_abstract_ths · 主路径 · 25-80 行季报 · 字段全
      2. 东财 stock_financial_abstract · fallback · 缺增速字段

    港股 / 美股:AKShare 这条没有独立数据源,但**用户可能自己接了**
    (比如 SEC EDGAR 的 XBRL 财务)—— 先问他的源,没有才返 None。
    """
    # ── 用户自己的 financial 源优先 ────────────────────────────
    #
    # 加在最前面而不是只补美股分支:用户接了 A 股财报源(Tushare 之类)
    # 时也该优先走他的。没配就返回 None,后面逐字节走原路径。
    try:
        from app.services import source_resolver
        mk = (market or "A").lower()
        hit = source_resolver.try_user(mk, "financial", bare)
        rows = (hit or {}).get("rows") or []
        if rows:
            logger.info("[akshare_financials] {} 走用户自己的源 · {} 行",
                        bare, len(rows))
            return rows
    except Exception as e:                                     # noqa: BLE001
        logger.warning("[akshare_financials] 用户源失败(回落): {}", e)

    if (market or "A").upper() != "A":
        return None
    rows = _fetch_ths(bare)
    if rows:
        return rows
    logger.info("[akshare_financials] {} 同花顺失败 · 落东财", bare)
    return _fetch_em(bare)


def latest(bare: str, market: str = "A") -> dict | None:
    """便捷:只要最新一期(给 UZI 深度分析这种"只关心最近一季"的消费方)"""
    rows = fetch_financials(bare, market)
    return rows[-1] if rows else None
