"""回测数据质量过滤 —— 量化回测的标准第一步:股票池清洗。

不做这层清洗,统计出来的命中率是失真的:
  · 停牌日没有真实成交,拿去对比会算出假误差
  · 涨跌停当天买不进/卖不出,预测再准也无法交易
  · ST 股涨跌停限制 5%(普通股 10%),混在一起统计规则不一致
  · 次新股 K 线太短,MA60/MACD 等因子根本算不出来
  · 低流动性股价格易被操纵,预测本就不可靠
"""
import logging
from datetime import date, timedelta

from app.services.backtest.store import conn

log = logging.getLogger(__name__)

# A股涨跌停幅度(按板块)
LIMIT_MAIN = 10.0      # 主板
LIMIT_GEM = 20.0       # 创业板(300/301)、科创板(688)
LIMIT_ST = 5.0         # ST 股
LIMIT_BJ = 30.0        # 北交所(4/8开头)
EPS = 0.3              # 判定容差(百分点), 避免浮点误差与四舍五入误判


def limit_pct(symbol: str, is_st: bool = False) -> float:
    """该股当日涨跌停幅度"""
    code = symbol.split(".")[0]
    if is_st:
        return LIMIT_ST
    if code.startswith(("300", "301", "688")):
        return LIMIT_GEM
    if code.startswith(("4", "8", "9")):
        return LIMIT_BJ
    return LIMIT_MAIN


def market_symbol(code: str) -> str:
    """六位代码 → 带后缀(daily_close 用带后缀格式)"""
    code = code.strip()
    if "." in code:
        return code
    return code + (".SH" if code.startswith(("6", "9")) else
                   (".BJ" if code.startswith(("4", "8")) else ".SZ"))


def screen_pool(codes: list[str], cfg: dict) -> tuple[list[str], dict]:
    """入池前过滤:上市天数、流动性、ST。

    返回 (通过的代码列表, {被剔除代码: 原因})
    """
    if not codes:
        return [], {}
    syms = [market_symbol(c) for c in codes]
    rej: dict[str, str] = {}
    keep: list[str] = []

    min_days = int(cfg.get("min_list_days") or 0)
    min_amt = float(cfg.get("min_amount_wan") or 0) * 10000  # 万元→元
    skip_st = bool(cfg.get("skip_st"))

    c = conn(); cur = c.cursor()
    # 一次查出:历史交易日数、近20日平均成交额
    cur.execute("""
        SELECT symbol, count(*) AS days,
               avg(amount) FILTER (WHERE trade_date > CURRENT_DATE - 30) AS avg_amt
        FROM daily_close WHERE symbol = ANY(%s) GROUP BY symbol
    """, (syms,))
    stat = {r[0]: {"days": r[1], "avg_amt": float(r[2]) if r[2] else 0.0} for r in cur.fetchall()}

    # ST 判定:从 company_master 名称里看
    st_set: set[str] = set()
    if skip_st:
        try:
            cur.execute("""SELECT stock_code FROM company_master
                           WHERE stock_code = ANY(%s) AND (name LIKE %s OR name LIKE %s)""",
                        ([c.split(".")[0] for c in syms], "%ST%", "%退%"))
            st_set = {r[0] for r in cur.fetchall()}
        except Exception as e:
            log.warning("ST 名单查询失败(跳过该过滤): %s", e)
    c.close()

    for raw, sym in zip(codes, syms):
        bare = sym.split(".")[0]
        s = stat.get(sym)
        if not s:
            rej[raw] = "无历史K线数据"
            continue
        if min_days and s["days"] < min_days:
            rej[raw] = f"上市/数据不足{min_days}个交易日(现{s['days']})"
            continue
        if min_amt and s["avg_amt"] < min_amt:
            rej[raw] = f"近30日均成交额{s['avg_amt']/1e4:.0f}万 低于{min_amt/1e4:.0f}万"
            continue
        if bare in st_set:
            rej[raw] = "ST/退市风险股"
            continue
        keep.append(raw)
    return keep, rej


def exclude_reason_for_backtest(symbol: str, target: date, cfg: dict,
                                real_row: dict | None) -> str:
    """回测某条预测时判断该样本是否该剔除, 返回原因(空串=正常计入)。

    real_row: {open, high, low, close, pre_close, amount} 当日真实行情
    """
    if real_row is None:
        return "停牌或无行情数据"
    if cfg.get("skip_suspended"):
        amt = float(real_row.get("amount") or 0)
        if amt <= 0:
            return "停牌(成交额为0)"
    if cfg.get("skip_limit"):
        pre = float(real_row.get("pre_close") or 0)
        close = float(real_row.get("close") or 0)
        if pre > 0 and close > 0:
            chg = (close - pre) / pre * 100
            lim = limit_pct(symbol)
            if abs(chg) >= lim - EPS:
                return f"{'涨停' if chg > 0 else '跌停'}(±{lim}%)"
    return ""


def real_rows_for(symbols: list[str], d: date) -> dict:
    """取指定交易日的完整行情(含前收/成交额), 用于回测与过滤。
    复权:daily_close 存的是不复权价, 但用 pre_close 算涨跌幅可规避除权跳变
    (除权日的 pre_close 已是除权后价格)。"""
    if not symbols:
        return {}
    syms = [market_symbol(s) for s in symbols]
    c = conn(); cur = c.cursor()
    cur.execute("""
        SELECT d.symbol, d.open, d.high, d.low, d.close, d.amount,
               (SELECT close FROM daily_close p
                 WHERE p.symbol = d.symbol AND p.trade_date < d.trade_date
                 ORDER BY p.trade_date DESC LIMIT 1) AS pre_close
        FROM daily_close d WHERE d.trade_date = %s AND d.symbol = ANY(%s)
    """, (d, syms))
    out = {}
    for r in cur.fetchall():
        bare = r[0].split(".")[0]
        out[bare] = {"open": r[1], "high": r[2], "low": r[3], "close": float(r[4]) if r[4] else None,
                     "amount": r[5], "pre_close": float(r[6]) if r[6] else None}
    c.close()
    return out


def benchmark_change(d: date, cfg: dict) -> float | None:
    """基准指数当日涨跌%(用于算超额命中率:扣掉跟随大盘就能猜对的部分)"""
    code = (cfg.get("benchmark_code") or "").strip()
    if not code:
        return None
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT close FROM daily_close WHERE symbol = %s AND trade_date <= %s
                   ORDER BY trade_date DESC LIMIT 2""", (code, d))
    rows = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
    c.close()
    if len(rows) < 2 or rows[1] == 0:
        return None
    return (rows[0] - rows[1]) / rows[1] * 100


def clip_prediction(chg: float, cfg: dict) -> tuple[float, bool]:
    """极端预测值处理。返回 (处理后的值, 是否被改动)。

    A股主板日涨跌停 10%、创业板 20%, 预测出 -16.8% 这类值多半是模型异常,
    clip=截断到上限 / exclude=标记后由调用方剔除 / keep=原样保留。
    """
    cap = float(cfg.get("max_pred_pct") or 0)
    if cap <= 0:
        return chg, False
    if abs(chg) <= cap:
        return chg, False
    mode = (cfg.get("outlier_mode") or "clip").lower()
    if mode == "keep":
        return chg, False
    return (cap if chg > 0 else -cap), True


def purge_old(cfg: dict) -> dict:
    """按保留期清理历史数据(P2 运维)"""
    days = int(cfg.get("retain_days") or 0)
    if days <= 0:
        return {"purged": 0}
    cutoff = date.today() - timedelta(days=days)
    c = conn(); cur = c.cursor()
    n = 0
    for tbl, col in (("pred_snapshot", "run_date"), ("pred_backtest", "pred_date"),
                     ("pred_consistency", "curr_run")):
        cur.execute(f"DELETE FROM {tbl} WHERE {col} < %s", (cutoff,))
        n += cur.rowcount
    c.commit(); c.close()
    if n:
        log.info("[backtest] 清理 %s 之前的数据 %d 行", cutoff, n)
    return {"purged": n, "cutoff": cutoff.isoformat()}
