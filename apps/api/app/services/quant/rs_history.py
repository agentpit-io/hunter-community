"""全市场日线管线 —— RS 线上涨天数 + 精确 RS 评级的数据来源。

    # 服务器上跑(容器里没有这个脚本就先 docker compose cp 进去):
    docker compose exec -T api python -m app.services.quant.rs_history run --market us
    docker compose exec -T api python -m app.services.quant.rs_history run --market a
    docker compose exec -T api python -m app.services.quant.rs_history run --market hk

## 为什么要有它

扫描源只给**当前快照**,而「RS 线连续上涨 N 天」需要每只股票逐日的收盘价。
本地原有日线只覆盖 559 只 A 股,港股美股一只没有(2026-09-11 实测)。
2026-09-11 用户选定:建全市场日线管线;RS 线趋势口径 = RS 线站上自身 21 日均线。

设计上照搬 IBD-RS-Rating(MIT)数据管线的几条教训(见其 docs/wiki/Concepts.md):

1. **没有全局游标,每次都整窗重拉。** 它 2026-04 出过事故:用"从我已有的最新日期往后拉"
   的全局游标,一只票跑在前面就把所有人的起点推后,落后的票永远补不上,静默饿死一个多月。
   腾讯一次请求本来就返回 320 根,**整窗覆盖重写**和只补几天的代价完全一样,
   而且天然**自愈**:昨天失败的票今天整段补回来。
2. **整窗重写顺带解决拆股接缝。** 前复权在拆股后会改写整段历史;只补尾巴的做法会让
   新旧两段复权基准拼在一起,RS 线凭空跳一截 —— 原项目约 0.9% 的坏数据就是这么来的。
3. **跑完检查完整性。** 最新交易日有收盘价的比例 < 90% 就判这次失败(非零退出),
   不能"跑完了、日志绿、实际只更新了几十只"。

## 数据源

腾讯 `ifzq.gtimg.cn/appstock/app/fqkline/get`,**三个市场一个接口,都是前复权**。
2026-09-11 服务器实测单次约 180ms、连打 30 次零失败 —— 但持续约 13 次/秒就被 WAF 封了,
见下方 `_KLINE` 处的事故说明:批量任务限速 1 次/秒,且不用站内按需取数的 web.ifzq 域名。
  A 股  sh600519 / sz000858           按扫描源给的交易所 SSE→sh、SZSE→sz
  港股  hk00700                       5 位补零
  美股  usAAPL.OQ / usJPM.N / usSPY.AM  **必须带交易所后缀**,不带只给 2 根
基准指数:沪深300 sh000300 · 恒生 hkHSI · 标普500 us.INX(均 320 根)。

⚠ 腾讯日线字段顺序是 [日期, 开, **收**, 高, 低, 量] —— 收盘价排在最高价前面。
  搞错会把收盘价当最高价,而且数字都在合理范围,图上看不出来(market_source 同样注释过)。

## 表

    rs_daily      (market, code, trade_date) → close          逐日前复权收盘价
    rs_line_stat  (market, code) → as_of, rs_line, rs_ma21, up_days, rs_raw_exact …
                  每天算一次,扫描时只读这张小表(不在每次扫描时扫几百万行)
DDL 随代码走(`_ensure_tables`),见仓内 CLAUDE.md「db/migrations 对已有部署不生效」。
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

log = logging.getLogger("rs_history")

# ⚠ 2026-09-11 事故:美股首轮 3 线程、每请求新建 TLS 连接,约 13 次/秒,
#   把 fin-r1 的 IP 打进了腾讯 WAF 黑名单 —— `web.ifzq.gtimg.cn` 的 fqkline 全部返回 501 跳转页。
#   **站内其它功能也在用这个域名**(market_source.hk_daily / index_kline / local_kline /
#   klines_etl),一起挂了。当时 `ifzq.gtimg.cn`(不带 web.)的同一个接口没被封。
#   由此定下三条:
#   1. 批量任务走 `ifzq.gtimg.cn`,和站内按需取数的 `web.ifzq.gtimg.cn` **分开**——
#      批量真把自己打封了,也不连累站内功能。
#   2. 全局限速(所有线程加起来)`_RATE` 次/秒,不是每线程 sleep。
#   3. 一见 WAF 页(501 / 返回 HTML)**立刻熔断整轮**,不重试。首轮没有熔断,
#      被封后每只票还重试 3 次、退避 4.5 秒,在 501 里空转了 40 多分钟。
_KLINE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
_UA = {"User-Agent": "Mozilla/5.0"}
_BARS = 320                 # 腾讯日线单次上限附近;252 天 ROC + 余量
_TIMEOUT = 20
_RETRY = 3
_WORKERS = 2                # 两个线程只为让网络延迟重叠,吞吐由 _RATE 决定
_RATE = 1.0                 # 次/秒,全局。美股 4069 只 ≈ 70 分钟,A 股 5227 ≈ 90 分钟


class WafBlocked(RuntimeError):
    """腾讯 WAF 拦截(501 跳转页)。整轮中止,别重试 —— 重试只会延长封禁。"""


_rate_lock = threading.Lock()
_next_slot = [0.0]


def _throttle() -> None:
    """全局令牌:所有线程共用一个"下一次允许发请求的时刻"。"""
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + 1.0 / _RATE
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)

BENCH_CODE = "__BENCH__"
BENCH = {"us": "us.INX", "a": "sh000300", "hk": "hkHSI"}
BENCH_NAME = {"us": "标普500", "a": "沪深300", "hk": "恒生指数"}

# RS 线口径(2026-09-11 用户选定):RS 线 = 收盘价 ÷ 基准;站上自身 21 日均线算"向上"
RS_LINE_MA = 21
COMPLETENESS_MIN = 0.90

# 扫描源的交易所 → 腾讯后缀。2026-09-11 每个交易所随机抽 6 只逐个试过四种写法,
# 只有这一种给满 320 根;带点号的 ticker(BRK.A / BF.A)原样加后缀即可。
# CBOE 池子里只有 1 只(CBOE 自己),.AM 和 .OQ 都有数据,取 .AM。
_US_SUFFIX = {"NASDAQ": ".OQ", "NYSE": ".N", "AMEX": ".AM", "CBOE": ".AM"}

_DDL = """
CREATE TABLE IF NOT EXISTS rs_daily (
    market      TEXT             NOT NULL,
    code        TEXT             NOT NULL,
    trade_date  DATE             NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (market, code, trade_date)
);
CREATE TABLE IF NOT EXISTS rs_line_stat (
    market           TEXT    NOT NULL,
    code             TEXT    NOT NULL,
    as_of            DATE    NOT NULL,
    n_days           INT     NOT NULL,
    rs_line          DOUBLE PRECISION,
    rs_ma21          DOUBLE PRECISION,
    up_days          INT,
    up_days_censored BOOLEAN NOT NULL DEFAULT FALSE,
    rs_raw_exact     DOUBLE PRECISION,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market, code)
);
-- 2026-09-11 · VCP 字段要用最高/最低/成交量。腾讯每根 K 线本来就带着
-- [日期, 开, 收, 高, 低, 量],原来只存了收盘 —— 同一次响应多存三列,**零新增请求**。
-- 老行这三列为空,今晚那一轮整窗重拉后自然补齐(_upsert 是整窗覆盖)。
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS high   DOUBLE PRECISION;
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS low    DOUBLE PRECISION;
ALTER TABLE rs_daily ADD COLUMN IF NOT EXISTS volume DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_contractions   INT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_depths         TEXT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_first_depth    DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_last_depth     DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_vol_declining  SMALLINT;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_last_vol_ratio DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_pivot          DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_pivot_dist     DOUBLE PRECISION;
ALTER TABLE rs_line_stat ADD COLUMN IF NOT EXISTS vcp_base_days      INT;
"""


def _ensure_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute(_DDL)
    conn.commit()
    cur.close()


# ═══════════════════════════════════════════════════════════════
# 纯计算(不碰数据库、不联网 —— tests/test_rs_line.py 直接测)
# ═══════════════════════════════════════════════════════════════

def rs_line_stats(stock: list[tuple], bench: dict, ma: int = RS_LINE_MA) -> dict | None:
    """一只股票的 RS 线统计。

    stock  [(trade_date, close), …] 按日期升序
    bench  {trade_date: close}

    RS 线 = 收盘价 ÷ 基准,只在两边**同一天都有收盘价**的日子上算 ——
    港股/美股和指数偶有不同步的假日,错位相除会凭空造出一个尖峰。

    up_days = 从最新一天往回数,RS 线**连续**站在自身 ma 日均线之上的交易日数;
              最新一天没站上就是 0。
    censored = 连续站上的区间一直延伸到能算均线的最早一天 ——
              真实天数只会更长,我们只知道"至少这么多"。

    → None 表示数据不够算均线(少于 ma 天)。
    """
    pts = [(d, c / bench[d]) for d, c in stock
           if c and c > 0 and d in bench and bench[d] and bench[d] > 0]
    if len(pts) < ma:
        return None
    vals = [v for _, v in pts]
    # 滚动均线 —— 第 i 天(i ≥ ma-1)的均线是 [i-ma+1, i] 这 ma 天的均值
    above: list[bool] = []
    s = sum(vals[:ma])
    mas: list[float] = []
    for i in range(ma - 1, len(vals)):
        if i > ma - 1:
            s += vals[i] - vals[i - ma]
        m = s / ma
        mas.append(m)
        above.append(vals[i] > m)
    up = 0
    for flag in reversed(above):
        if not flag:
            break
        up += 1
    return {
        "as_of": pts[-1][0],
        "n_days": len(pts),
        "rs_line": vals[-1],
        "rs_ma21": mas[-1],
        "up_days": up,
        "up_days_censored": up == len(above),
    }


def rs_raw_exact(closes: list[float]) -> float | None:
    """精确 RS Raw —— 原项目公式,ROC 按这只票自己的**有效交易日**数(Concepts.md):

        0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252)

    需要 > 252 个有效收盘价(原项目的 warm-up 规则),不够返回 None。
    """
    c = [x for x in closes if x is not None and x > 0]
    if len(c) <= 252:
        return None
    last = c[-1]
    tot = 0.0
    for days, w in ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)):
        tot += w * (last / c[-1 - days] - 1.0)
    return tot


# ═══════════════════════════════════════════════════════════════
# 拆股校验 —— 腾讯美股的"前复权"并不总是复权了拆股
#
# 2026-09-11 前 250 只实测:最新收盘与扫描源逐只一致(p99 差 0.05%),
# 但 3 个月涨幅有 1.5%、1 年涨幅有约 7% 的票对不上,全是拆股/合股没复权:
#   APH(1 拆 2)腾讯推出来 3 个月 -47%,扫描源 +4%;ACET(合股)一年 +1087%,实际 -25%。
# 这种票的 RS 线会在拆股那天凭空跳一截,"连续上涨天数"和精确评级都会静默算错。
#
# 修法:扫描源的 Perf.W/1M/3M/6M/YTD/Y **是复权过的**,等于免费给了 6 个锚点 ——
# "从锚点那天到今天真实涨了多少"。逐只拿腾讯的序列去对:
#   · 对得上 → 原样用
#   · 对不上 → 在两个锚点之间找当天涨跌恰好等于那个倍数的一天(拆股日),
#     把它之前的价格乘上这个倍数;修完**重新核对全部锚点**
#   · 还对不上 → 这只票不给数(算不出,不是编一个)
# 不需要额外请求任何接口,也不依赖一份拆股日历。
# ═══════════════════════════════════════════════════════════════

_PERF_COLS = ("Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.YTD", "Perf.Y")
_LOG_TOL = math.log(1.25)     # 锚点允许的偏差:25%(锚点日可能差一两天,妖股一天就能动 10%+)
# 拆股日当天的涨跌与 mismatch 的容差。1.25 太紧(2026-09-11 实测):合股当天股价本身常大涨大跌,
# ALIT 锚点差 18.5 倍、合股日跳 24.5 倍,AZI 45 倍对 58 倍,都被误判"找不到拆股日"。
# 放宽到 1.6 不会放过错的:这里只负责**定位**是哪一天,修完所有锚点还要在 25% 内重新核对
_LOG_SPLIT_FIT = math.log(1.6)


def _shift_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 - months, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def perf_anchors(last: date, perf: dict) -> list[tuple[date, float]]:
    """扫描源的 Perf.*(百分数)→ [(锚点日, 真实涨幅倍数 = 今天/锚点日)],缺的跳过。"""
    spec = [("Perf.W", last - timedelta(days=7)),
            ("Perf.1M", _shift_months(last, 1)),
            ("Perf.3M", _shift_months(last, 3)),
            ("Perf.6M", _shift_months(last, 6)),
            ("Perf.YTD", date(last.year - 1, 12, 31)),
            ("Perf.Y", _shift_months(last, 12))]
    out = []
    for key, d in spec:
        v = perf.get(key)
        if v is None or v <= -100:
            continue
        out.append((d, 1.0 + v / 100.0))
    return out


def _mismatch(series, anchor: date, ratio: float) -> float | None:
    """log(腾讯推出来的倍数 / 真实倍数)。锚点日前后几天里取最接近的一根(对齐误差)。"""
    last = series[-1][1]
    best = None
    for d, c in series:
        if d > anchor:
            break
        if (anchor - d).days <= 5 and c > 0:
            m = math.log(last / c) - math.log(ratio)
            if best is None or abs(m) < abs(best):
                best = m
    return best


_LOG_WILD = math.log(1.8)


def _cut_unverified_head(s: list[tuple], anchors) -> list[tuple]:
    """最老的锚点之前那段没法核对(腾讯给约 15 个月,扫描源最远的锚点是 1 年)。
    那段里有单日 ±80% 以上的跳变 → 从跳变之后截断,不赌它是不是没复权的拆股。
    代价只是少几个月头部数据;用错了拆股的数,RS 线会凭空跳一截。"""
    used = [d for d, _ in anchors if d >= s[0][0]]
    oldest = min(used) if used else s[-1][0]
    cut = 0
    for i in range(1, len(s)):
        if s[i][0] > oldest:
            break
        if s[i - 1][1] > 0 and abs(math.log(s[i][1] / s[i - 1][1])) > _LOG_WILD:
            cut = i
    return s[cut:]


def repair_splits(series: list[tuple], anchors: list[tuple[date, float]],
                  max_fix: int = 3) -> tuple[list[tuple] | None, int]:
    """→ (修好的序列 | None, 修了几处)。None = 对不上又修不好,这只票不给数。

    series 按日期升序 [(date, close)];anchors 见 perf_anchors。
    锚点早于序列起点的(次新股)跳过 —— 那段没有数据可对。
    """
    s = list(series)
    fixed = 0
    for _ in range(max_fix + 1):
        bad = None
        hi = s[-1][0]                       # 上一个"对得上"的锚点(从近往远走)
        for d, ratio in sorted(anchors, key=lambda a: a[0], reverse=True):
            if d < s[0][0]:
                continue
            m = _mismatch(s, d, ratio)
            if m is None:
                continue
            if abs(m) > _LOG_TOL:
                bad = (d, hi, m)
                break
            hi = d
        if bad is None:
            return _cut_unverified_head(s, anchors), fixed
        if fixed == max_fix:
            return None, fixed
        lo, hi, m = bad
        # 拆股日 t ∈ (lo, hi]:当天涨跌 close_t/close_{t-1} 最接近 exp(m) 的那天
        # (未复权的 1 拆 2:拆股日"跌"一半,而腾讯推的倍数恰好也差一半)
        best = None
        for i in range(1, len(s)):
            d = s[i][0]
            if d <= lo - timedelta(days=5) or d > hi:
                continue
            if s[i - 1][1] <= 0:
                continue
            # 1 拆 2:拆股前 200、后 100 → 当天 log(100/200) = log 0.5,
            # 锚点处 m = log(腾讯倍数 0.5 / 真实倍数 1) 也是 log 0.5 —— 两者应当相等
            err = abs(math.log(s[i][1] / s[i - 1][1]) - m)
            if best is None or err < best[0]:
                best = (err, i)
        if best is None or best[0] > _LOG_SPLIT_FIT:
            return None, fixed               # 找不到能解释这个差的单日跳变 —— 不硬修
        i = best[1]
        # 修正倍数:拆股日之前的锚点里,取 mismatch 最接近当天跳变的那个。
        # 不取中位数 —— 连着两次合股时(AZI),更早的锚点叠加了两次的效应,
        # 两个锚点取"中间"可能正好取到叠加的那个
        jump = math.log(s[i][1] / s[i - 1][1])
        ms = [mm for d, r in anchors if s[0][0] <= d < s[i][0]
              for mm in [_mismatch(s, d, r)] if mm is not None and abs(mm) > _LOG_TOL]
        if ms:
            m = min(ms, key=lambda x: abs(x - jump))
        k = math.exp(m)                      # 拆股日之前的价格统一乘 k(上例 200×0.5 = 100)
        s = [(d, c * k) for d, c in s[:i]] + s[i:]
        fixed += 1
    return None, fixed


def adjust_bars(raw: dict, series: list[tuple]) -> list[tuple]:
    """把拆股修正同步到最高/最低/成交量 → [(日期, 收, 高, 低, 量)]。

    raw    = {日期: (原始收, 原始高, 原始低, 原始量)}
    series = repair_splits 修好的 [(日期, 收)](可能截掉了开头一段)

    收盘被乘了 k 的那段,高低也乘 k、成交量除以 k。不同步的话,拆股那天
    高低还是拆股前的价、收盘已经是拆股后的,VCP 会凭空算出一次「暴跌收缩」;
    量不反向调整,拆股前后的量能对比也是错的。
    """
    out = []
    for d, c in series:
        c0, h0, l0, v0 = raw[d]
        f = c / c0 if c0 else 1.0
        out.append((d, c, h0 * f if h0 else None, l0 * f if l0 else None,
                    v0 / f if (v0 and f) else None))
    return out


# ═══════════════════════════════════════════════════════════════
# 取数
# ═══════════════════════════════════════════════════════════════

def tx_symbol(market: str, code: str, exchange: str | None) -> str | None:
    """扫描源的 (市场, 代码, 交易所) → 腾讯日线代码。拿不准返回 None(不瞎猜)。"""
    code = (code or "").strip()
    if not code:
        return None
    if market == "a":
        pre = {"SSE": "sh", "SZSE": "sz"}.get(exchange or "")
        return (pre + code) if pre and code.isdigit() and len(code) == 6 else None
    if market == "hk":
        return ("hk" + code.zfill(5)) if code.isdigit() else None
    if market == "us":
        suf = _US_SUFFIX.get(exchange or "")
        # 腾讯美股代码里分隔符是点(BRK.B);扫描源偶尔用斜杠
        return ("us" + code.upper().replace("/", ".") + suf) if suf else None
    return None


_tls = threading.local()


def _session():
    """每个线程一个 keep-alive 连接。2026-09-11 美股首轮用裸 requests.get,
    每个请求都重新握一次 TLS,4069 只跑了十几分钟;复用连接省掉的就是握手。"""
    s = getattr(_tls, "s", None)
    if s is None:
        import requests      # 放函数里:纯计算部分(rs_line_stats)的测试不必装 requests
        s = _tls.s = requests.Session()
    return s


def fetch_bars(sym: str, n: int = _BARS) -> list[tuple] | None:
    """→ [(trade_date, close, high, low, volume), …];None = 请求失败(与"确实没数据"区分开,后者返回 [])

    high / low / volume 某根缺了就是 None —— 下游 VCP 算不出就给空,不拿收盘价顶替。

    注意:腾讯对**代码写错**的请求不返回空,而是返回 1–2 根(2026-09-11 实测
    AAPL 配 .N 后缀给 1 根)。所以后缀必须按交易所映射(tx_symbol),不能靠试。
    """
    sess = _session()
    for attempt in range(_RETRY):
        _throttle()
        try:
            r = sess.get(_KLINE, params={"param": f"{sym},day,,,{n},qfq"},
                         headers=_UA, timeout=_TIMEOUT)
        except Exception as e:                                # noqa: BLE001  网络错误才重试
            if attempt == _RETRY - 1:
                log.warning("[rs_history] %s 取数失败:%s", sym, e)
                return None
            time.sleep(1.5 * (2 ** attempt))                  # 1.5s / 3s 退避
            continue
        if r.status_code == 501 or r.text.lstrip()[:1] == "<":
            raise WafBlocked(f"腾讯 WAF 拦截({r.status_code}):{r.text[:120]!r}")
        try:
            data = (r.json() or {}).get("data") or {}
            node = data.get(sym) if isinstance(data, dict) else None
            if not node and isinstance(data, dict) and data:
                node = next(iter(data.values()))
            bars = (node or {}).get("qfqday") or (node or {}).get("day") or []
            out = []
            for b in bars:
                try:
                    # [日期, 开, 收, 高, 低, 量] —— b[2] 才是收盘价
                    out.append((date.fromisoformat(str(b[0])[:10]), float(b[2]),
                                _num(b, 3), _num(b, 4), _num(b, 5)))
                except (ValueError, IndexError, TypeError):
                    continue
            return out
        except (ValueError, AttributeError) as e:
            log.warning("[rs_history] %s 返回体解析失败:%s · %r", sym, e, r.text[:120])
            return None
    return None


def _num(b, i: int) -> float | None:
    try:
        v = float(b[i])
        return v if v > 0 else None
    except (ValueError, IndexError, TypeError):
        return None


def universe(market: str) -> list[tuple[str, str]]:
    """要拉的股票 = 当前 RS 排名池(含次新股)→ [(code, 腾讯代码)]。

    和 RS 评级同一个池子(screen_rs.in_population):美股剔 OTC 与微盘,
    A/港股剔微盘。池外的票不参与 RS,没必要拉。
    """
    from app.services.quant import screen_rs, screen_source
    rows, _ = screen_source.fetch_rows(market, ["exchange", "market_cap_basic"])
    out = []
    for r in rows:
        if not screen_rs.in_population(r, market):
            continue
        sym = tx_symbol(market, r.get("_code") or "", r.get("exchange"))
        if sym:
            out.append((r["_code"], sym))
    return out


def _upsert(conn, market: str, code: str, bars: list[tuple]) -> None:
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    # 整窗覆盖:先删这只票窗口内的旧行再插 —— 复权基准变了的话旧值必须全部作废
    if bars:
        cur.execute("DELETE FROM rs_daily WHERE market=%s AND code=%s AND trade_date >= %s",
                    (market, code, bars[0][0]))
        execute_values(cur,
                       "INSERT INTO rs_daily (market, code, trade_date, close, high, low, volume) VALUES %s "
                       "ON CONFLICT (market, code, trade_date) DO UPDATE SET close=EXCLUDED.close, "
                       "high=EXCLUDED.high, low=EXCLUDED.low, volume=EXCLUDED.volume",
                       [(market, code) + tuple(b[:5]) + (None,) * (5 - len(b)) for b in bars])
    conn.commit()
    cur.close()


def fetch_market(market: str, limit: int | None = None) -> dict:
    """拉一个市场的全部日线(含基准)。→ 统计。"""
    from app.services.database import get_conn
    conn = get_conn()
    _ensure_tables(conn)

    t0 = time.time()
    bench = fetch_bars(BENCH[market])              # 被封会在这里直接抛 WafBlocked
    if not bench:
        conn.close()
        raise RuntimeError(f"基准 {BENCH[market]} 拉取失败 —— 没有基准就没有 RS 线,本次中止")
    _upsert(conn, market, BENCH_CODE, bench)
    log.info("[rs_history] %s 基准 %s %d 根", market, BENCH[market], len(bench))

    uni = universe(market)
    if limit:
        uni = uni[:limit]
    log.info("[rs_history] %s 待拉 %d 只", market, len(uni))

    stat = {"ok": 0, "empty": 0, "fail": 0}
    stop = threading.Event()

    def work(item):
        code, sym = item
        if stop.is_set():
            return code, "skip"
        try:
            return code, fetch_bars(sym)
        except WafBlocked as e:
            stop.set()
            return code, e

    # 线程只负责取数;写库在主线程**边收边写** —— 拉到一半进程挂了,已拉的也落了库,
    # 下次整窗重拉自然补齐(不需要断点续传)
    blocked = None
    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        for done, (code, bars) in enumerate(ex.map(work, uni), 1):
            if isinstance(bars, WafBlocked):
                blocked = blocked or bars
                continue
            if bars == "skip":
                continue
            if bars is None:
                stat["fail"] += 1
            elif not bars:
                stat["empty"] += 1
            else:
                stat["ok"] += 1
                _upsert(conn, market, code, bars)
            if done % 250 == 0:
                log.info("[rs_history] %s 进度 %d/%d · 成功 %d 空 %d 失败 %d · %.0fs",
                         market, done, len(uni), stat["ok"], stat["empty"], stat["fail"],
                         time.time() - t0)
    if blocked:
        conn.close()
        raise WafBlocked(f"{blocked} —— 已拉 {stat['ok']} 只并落库,本轮中止。"
                         f"等几小时再跑;别调高 _RATE")

    # 留 ~480 个自然日(>320 个交易日)—— 更早的对 252 天 ROC 和 21 日均线都没用了
    cur = conn.cursor()
    cur.execute("DELETE FROM rs_daily WHERE market=%s AND trade_date < now()::date - 480",
                (market,))
    conn.commit()
    cur.close()
    conn.close()

    stat.update({"market": market, "universe": len(uni),
                 "bench_last": str(bench[-1][0]), "seconds": round(time.time() - t0)})
    return stat


# ═══════════════════════════════════════════════════════════════
# 计算 rs_line_stat
# ═══════════════════════════════════════════════════════════════

def compute_market(market: str) -> dict:
    from app.services.database import get_conn
    conn = get_conn()
    _ensure_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT trade_date, close FROM rs_daily WHERE market=%s AND code=%s",
                (market, BENCH_CODE))
    bench = {d: c for d, c in cur.fetchall()}
    if not bench:
        conn.close()
        raise RuntimeError(f"{market} 没有基准数据 —— 先跑 fetch")
    bench_last = max(bench)

    cur.close()

    # 拆股校验的锚点:扫描源复权过的 Perf.*。拿不到就整轮不算 ——
    # 不校验的话约 7% 的美股会带着未复权的拆股进统计(见 repair_splits 上方说明);
    # 昨天的统计还在,6 天内不会判过期
    from app.services.quant import screen_source
    tv_rows, _ = screen_source.fetch_rows(market, list(_PERF_COLS))
    perf = {r["_code"]: r for r in tv_rows}

    rows = []
    fresh = 0
    total = 0
    split = {"fixed": 0, "bad": 0, "no_anchor": 0, "vcp": 0}
    bad_eg: list[str] = []

    def flush(code, full):
        nonlocal fresh
        p = perf.get(code)
        if p is None:
            split["no_anchor"] += 1            # 扫描源里已经没有这只(退市/改代码)—— 没法核对,不给
            return
        raw = {d: (c, h, lo, v) for d, c, h, lo, v in full}
        series = [(d, c) for d, c, _h, _l, _v in full]
        series, n_fix = repair_splits(series, perf_anchors(series[-1][0], p))
        if series is None:
            split["bad"] += 1
            if len(bad_eg) < 12:
                bad_eg.append(code)
            return
        split["fixed"] += n_fix > 0
        st = rs_line_stats(series, bench)
        if st is None:
            return
        if st["as_of"] == bench_last:
            fresh += 1
        bars = adjust_bars(raw, series)
        from app.services.quant import vcp     # 函数内 import:本文件的惯例,tests/ 能不带 app 包单独加载
        vs = vcp.vcp_stats(bars) or {}
        rows.append((market, code, st["as_of"], st["n_days"], st["rs_line"],
                     st["rs_ma21"], st["up_days"], st["up_days_censored"],
                     rs_raw_exact([c for _, c in series]),
                     vs.get("contractions"), vs.get("depths") or None, vs.get("first_depth"),
                     vs.get("last_depth"), vs.get("vol_declining"), vs.get("last_vol_ratio"),
                     vs.get("pivot"), vs.get("pivot_dist"), vs.get("base_days")))
        split["vcp"] += vs.get("contractions") is not None

    # 服务端游标逐只流式算 —— 美股一个市场就是 4000 只 × 320 天 ≈ 130 万行,
    # 一次 fetchall 进内存要两三百 MB,和 api 进程抢同一个容器的内存
    scan = conn.cursor(name="rs_scan")
    scan.itersize = 20000
    scan.execute("SELECT code, trade_date, close, high, low, volume FROM rs_daily "
                 "WHERE market=%s AND code<>%s ORDER BY code, trade_date",
                 (market, BENCH_CODE))
    cur_code, series = None, []
    for code, d, c, h, lo, v in scan:
        if code != cur_code:
            if cur_code is not None:
                flush(cur_code, series)
                total += 1
            cur_code, series = code, []
        series.append((d, c, h, lo, v))
    if cur_code is not None:
        flush(cur_code, series)
        total += 1
    scan.close()

    from psycopg2.extras import execute_values
    cur = conn.cursor()
    cur.execute("DELETE FROM rs_line_stat WHERE market=%s", (market,))
    if rows:
        execute_values(cur,
                       "INSERT INTO rs_line_stat (market, code, as_of, n_days, rs_line, rs_ma21, "
                       "up_days, up_days_censored, rs_raw_exact, vcp_contractions, vcp_depths, "
                       "vcp_first_depth, vcp_last_depth, vcp_vol_declining, vcp_last_vol_ratio, "
                       "vcp_pivot, vcp_pivot_dist, vcp_base_days) VALUES %s", rows)
    conn.commit()
    cur.close()
    conn.close()
    # 完整性的分母 = 最近 10 天内还有数据的票(退市/移出池子几个月的旧票不算,
    # 否则分母只增不减,门槛迟早误报);分子 = 最新交易日有收盘价的
    active = sum(1 for r in rows if (bench_last - r[2]).days <= 10) + split["bad"]
    log.info("[rs_history] %s 拆股校验:修正 %d 只 · 对不上不给数 %d 只 %s · 扫描源已无 %d 只",
             market, split["fixed"], split["bad"], bad_eg, split["no_anchor"])
    return {"market": market, "codes": total, "computed": len(rows), "active": active,
            "split_fixed": split["fixed"], "split_bad": split["bad"], "vcp_computed": split["vcp"],
            "fresh_on_bench_last": fresh, "bench_last": str(bench_last),
            "completeness": (fresh / active) if active else 0.0}


_ddl_checked = False


def load_stats(market: str) -> tuple[dict, dict]:
    """扫描时用:→ ({code: 统计行}, 概况 {as_of, n, stale_days})。表不存在返回空。"""
    global _ddl_checked
    from app.services.database import get_conn
    try:
        conn = get_conn()
        if not _ddl_checked:
            # 2026-09-11 加了 VCP 列。部署之后、今晚任务第一次跑之前,老库上还没有这些列 ——
            # 不先补的话下面的 SELECT 直接报错,被 except 吞成「没有统计」,
            # **连本来好好的 RS 线天数也一起变空**。每个进程只补一次:ALTER 要拿表锁,
            # 每次扫描都跑会和每晚的写入互相等
            _ensure_tables(conn)
            _ddl_checked = True
        cur = conn.cursor()
        cur.execute("SELECT code, as_of, up_days, up_days_censored, rs_raw_exact, rs_line, rs_ma21, "
                    "vcp_contractions, vcp_depths, vcp_first_depth, vcp_last_depth, "
                    "vcp_vol_declining, vcp_last_vol_ratio, vcp_pivot_dist, vcp_base_days "
                    "FROM rs_line_stat WHERE market=%s", (market,))
        out = {r[0]: {"as_of": r[1], "up_days": r[2], "censored": r[3],
                      "rs_raw_exact": r[4], "rs_line": r[5], "rs_ma21": r[6],
                      "vcp_contractions": r[7], "vcp_depths": r[8], "vcp_first_depth": r[9],
                      "vcp_last_depth": r[10], "vcp_vol_declining": r[11],
                      "vcp_last_vol_ratio": r[12], "vcp_pivot_dist": r[13], "vcp_base_days": r[14]}
               for r in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception as e:                                    # noqa: BLE001
        log.warning("[rs_history] 读 rs_line_stat 失败(表可能还没建):%s", e)
        return {}, {"as_of": None, "n": 0}
    if not out:
        return {}, {"as_of": None, "n": 0}
    latest = max(v["as_of"] for v in out.values())
    return out, {"as_of": latest, "n": len(out)}


# ═══════════════════════════════════════════════════════════════
# 命令行
# ═══════════════════════════════════════════════════════════════

def _main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(prog="rs_history")
    p.add_argument("cmd", choices=["fetch", "compute", "run"])
    p.add_argument("--market", required=True, choices=["us", "a", "hk"])
    p.add_argument("--limit", type=int, default=None, help="只拉前 N 只(试跑用)")
    a = p.parse_args(argv)

    if a.cmd in ("fetch", "run"):
        # 和数据页的美股下载、每晚美股刷新共用一把锁(us_kline.tencent_lock)——
        # 三者都打腾讯 ifzq,同时跑就是各 1 次/秒叠加,限速等于白设(2026-09-11 WAF 事故)。
        # 宿主机 crontab 的 flock 只管得住本脚本自己,管不到 api 进程里的下载任务
        from app.services.quant import us_kline
        lock, waited = us_kline.tencent_lock(), 0
        while lock is None and waited < 90 * 60:
            if waited == 0:
                log.info("[rs_history] 腾讯通道被占用(数据页下载或每晚美股刷新在跑)· 排队等")
            time.sleep(60)
            waited += 60
            lock = us_kline.tencent_lock()
        if lock is None:
            log.error("[rs_history] 等了 90 分钟腾讯通道仍被占用 · 本轮不跑")
            return 4
        try:
            st = fetch_market(a.market, a.limit)
        except WafBlocked as e:
            log.error("[rs_history] %s", e)
            return 3
        finally:
            us_kline.release(lock)
        log.info("[rs_history] 拉取完成 %s", st)
    if a.cmd in ("compute", "run"):
        st = compute_market(a.market)
        log.info("[rs_history] 计算完成 %s", st)
        # 完整性检查:最新交易日有数据的比例 < 90% → 非零退出,让 cron 报出来
        if not a.limit and st["completeness"] < COMPLETENESS_MIN:
            log.error("[rs_history] %s 最新交易日 %s 只有 %.0f%% 的票有收盘价,低于 %.0f%% —— 判失败",
                      a.market, st["bench_last"], 100 * st["completeness"], 100 * COMPLETENESS_MIN)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main())
