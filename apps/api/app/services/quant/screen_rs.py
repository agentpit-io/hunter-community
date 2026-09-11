"""RS 相对强度评级(IBD RS Rating 口径)—— 扫描筛选用。

算法移植自 IBD-RS-Rating(https://github.com/tjdwls101010/IBD-RS-Rating),
MIT 许可,原版权声明见文件末尾。移植的是它 `ibd_rs/rs.py` + `ibd_rs/config.py`
里的**评级方法**:权重、百分位排名、1–99 缩放、次新股预热、全市场覆盖率门槛。
它那套"每天拉全市场日线入库、增量重算"的数据管线没有移植 —— 见下。

## 原项目的方法

    RS Raw    = 0.4 × ROC(63) + 0.2 × ROC(126) + 0.2 × ROC(189) + 0.2 × ROC(252)
    RS Rating = round(同一天全市场百分位 × 98 + 1)        → 1 ~ 99

ROC(n) 是近 n 个交易日的涨幅。四个窗口约等于近 3 / 6 / 9 / 12 个月,
最近一个季度的有效权重是最早那个的 5 倍 —— 偏爱**正在加速**的票。
RS 是**相对**的:股价涨了评级也可能掉,只要别的票涨得更多。

## 2026-09-11 起:有全市场日线时用原版精确公式(见 inject 与 rs_history)

下面这节讲的插值法现在是**退路**:日线新鲜且覆盖 ≥90% 时用 ROC(63/126/189/252) 精确算,
与原项目对照平均差从 3.9 分降到 1.7 分(数字见 METHOD_NOTE_EXACT 下方注释)。
日线还没建、过期或残缺时,整批退回插值法。

## 退路:9 个月涨幅是插值出来的

扫描源只给当前快照里的 `Perf.3M / Perf.6M / Perf.Y`,**没有 9 个月**。
全市场 A 股 5237 / 港股 2399 / 美股 7389 只,本地有 252 天以上日线的只有 556 只,
按原口径精确计算需要先建一条全市场日线管线(原项目就是干这个的),代价远大于指标本身。

所以 ROC(189) 用 6 个月与 12 个月的**线性插值**代替:
    RS Raw ≈ 0.4 × R3 + 0.2 × R6 + 0.2 × (R6 + R12) / 2 + 0.2 × R12
           = 0.4 × R3 + 0.3 × R6 + 0.3 × R12

**这是有损近似,必须说出来。** 2026-09-11 拿原项目公布的精确值(美股,
同一交易日)做对照,剔除原项目 31 只数据损坏的票后 3501 只:
    秩相关 0.982 · 平均差 3.3 分 · 82% 在 ±5 分内 · RS≥80 判定一致 92.7%
另一种近似(直接去掉 9 月项、剩下三项按比例归一)更差:0.974 / 4.1 / 74% / 90.1%。
这组数字衡量的是**插值本身**的误差。

改好排名池口径后,再拿线上管线的输出**直接**和原项目公布值比(不重排,
含两边股票池的细微差异和原项目自身 0.9% 的坏数据):
    3512 只 · 秩相关 0.963 · 平均差 3.9 分 · 79% 在 ±5 分内
    我们判 RS≥80 的,95.0% 在原项目里也是 RS≥80(查全 88.1%)
    大盘股:NVDA 66/68 · AAPL 78/75 · META 43/43 · AMZN 57/58 · JPM 70/69
用户看到的说明用的是这组端到端数字 —— 那才是他实际拿到的东西。

(对照时顺带发现原项目有 0.9% 的票数据损坏 —— 疑似反向拆股没复权,
 典型如一年跌 90% 却评 RS 99。扫描源的涨幅是复权过的,不受这个影响。
 这也是没有直接读原项目公布数据的原因之一。)

## 两条从原项目照搬的"宁可不给"规则

1. **次新股不给评级**(原项目 Warm-up)。上市不满一年的票没有真正的 12 个月涨幅,
   用半年数据算出来的"年度强度"和别人不是一个东西。
   扫描源对次新股的 `Perf.Y` 给的是**上市以来**涨幅(实测美股 572 只),
   所以不能只看 Perf.Y 有没有值 —— 用 `SMA250` 非空当闸(≥250 个交易日;
   原项目是 >252,差 3 天,影响可以忽略)。
2. **覆盖率不足整天不发布**(原项目 Universe threshold,90%)。
   百分位的分母是"同一天有有效 RS Raw 的全部股票",如果大半数据缺失,
   在剩下几十只里排出来的 1–99 看起来完全正常、实际毫无意义 ——
   原项目 2026-04 就因为这个发布过一个月只基于 54 只股票的评级。
   低于 90% 时全部评级为空,并在返回体里说明原因。

## 评级是在**全市场**里排的,不是在命中结果里排的

先对扫描源返回的全市场(已剔除 ETF / 优先股 / 次新股)算 RS,
再拿脚本的其它条件去筛。所以「RS ≥ 80」的意思始终是"跑赢全市场 80% 的股票",
不会因为你加了别的条件而变。
"""
from __future__ import annotations

# 原项目 config.py:{回看交易日: 权重}
RS_WEIGHTS = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}
RS_UNIVERSE_THRESHOLD = 0.90

# 脚本里可以写的字段名 —— rs_raw / rs_rating 与原项目代码里的命名一致(它的文档明确要求
# 统一叫这两个名字,不叫 score / percentile / rank,免得和别的概念混)。
# rs_line_up_days 是我们加的(2026-09-11 用户要求):RS 线连续站上自身 21 日均线的交易日数,
# 需要逐日历史,扫描源快照给不了,来自每晚的全市场日线(rs_history)。
RS_FIELDS = ("rs_rating", "rs_raw", "rs_line_up_days")

# 日线统计超过这么多**自然日**没更新就当过期:不给 RS 线天数、评级退回快照插值。
# 6 天 = 跨一个周末再加一个假日还能容忍;再长就是每晚的任务坏了,
# 用一周前的"连续上涨 N 天"去筛今天的票是在给错答案。
HIST_STALE_DAYS = 6

# 算 RS 需要向扫描源额外请求的列
RS_SOURCE_COLS = ("Perf.3M", "Perf.6M", "Perf.Y", "SMA250", "exchange", "market_cap_basic")

# ── 排名池(百分位的分母)—— 必须照搬原项目口径 ──────────────────
#
# 原项目的池子:美股**交易所上市**普通股、市值 > 5000 万美元、剔除 ETF 和空壳,约 4600 只。
# 扫描源的 america 市场**包含 OTC 场外**(2026-09-11 实测 7389 只里 2439 只是 OTC)。
# 不剔的话:
#   · 一年涨幅 >1000% 的 158 只里 152 只是 OTC(一年涨 1218 万倍的粉单股之类),
#     RS 99 的位置全被它们占了
#   · 分母从约 4000 变成约 7000,**每一只股票的评级都跟着变**,和原项目、
#     和我们做过的对照验证都不再是同一个口径
# 所以美股只留 NASDAQ / NYSE / AMEX(/CBOE),加市值门槛 → 实测 4069 只。
#
# A 股 / 港股是我们的延伸(原项目只做美股):沪深两所没有 OTC;
# 市值门槛用"约 5000 万美元"折成本币,保持同一个"剔掉微盘股"的意思。
# 汇率是固定近似值,门槛本身就是个粗筛,差几个百分点不影响。
# 注意 market_cap_basic 在 A/H 两地上市股上有口径偏差(见 MARKET_CAP_WARN),
# 但门槛附近(约 3.6 亿人民币)几乎没有这类大盘股,影响可以忽略。
#
# 原项目的覆盖率门槛分母还有一个绝对下限 3000(UNIVERSE_FLOOR),那是按美股
# 约 4600 只定的,**没有移植** —— 港股全市场才 2399 只,照搬会让港股永远不出评级。
US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "CBOE")
MCAP_FLOOR = {"us": 5e7, "hk": 3.9e8, "a": 3.6e8}   # 美元 / 港币 / 人民币 ≈ 5000 万美元


def in_population(row: dict, market_key: str) -> bool:
    """这只股票参不参与 RS 排名(在不在分母里)。

    市值缺失的**不参与** —— 判断不了它过不过门槛,宁可不排。
    """
    if market_key == "us" and row.get("exchange") not in US_EXCHANGES:
        return False
    floor = MCAP_FLOOR.get(market_key)
    if floor:
        mc = row.get("market_cap_basic")
        if mc is None or mc < floor:
            return False
    return True

METHOD_NOTE = (
    "RS 相对强度评级按 IBD-RS-Rating(MIT)的方法计算:近 3/6/9/12 个月涨幅按 "
    "0.4/0.2/0.2/0.2 加权,在全市场排百分位(1–99),次新股(不足 250 个交易日)不给评级。"
    "扫描源没有 9 个月涨幅,用 6 与 12 个月线性插值代替 —— 与原项目公布的精确值"
    "逐只对照(美股 3512 只):秩相关 0.96、平均差约 4 分;这里判为 RS≥80 的股票,"
    "95% 在原项目里也是 RS≥80。评级是在全市场里排名,不是在命中结果里排名。"
)


BENCH_LABEL = {"us": "标普500", "a": "沪深300", "hk": "恒生指数"}

# 日线齐全时的评级说明。准确度数字 = 2026-09-11 与原项目公布值逐只对照的结果,见 rs_history
METHOD_NOTE_EXACT = (
    "RS 相对强度评级按 IBD-RS-Rating(MIT)的原版公式计算:近 63/126/189/252 个交易日涨幅"
    "(前复权日线)按 0.4/0.2/0.2/0.2 加权,在全市场排百分位(1–99),"
    "日线不足 253 个交易日的次新股不给评级。截至 {as_of} 收盘(每晚更新,不含今天盘中)。"
    "与原项目公布值逐只对照(美股 3501 只):平均差 1.7 分,96% 在 ±5 分内;"
    "这里判为 RS≥80 的,99.4% 在原项目里也是。"
    "评级是在全市场里排名,不是在命中结果里排名。"
)
# ↑ 数字来源:2026-09-11 美股全量入库 + 拆股校验后,与原项目同一交易日(2026-09-10)对照。
#   同一批对照里快照插值版是 3.9 分 / 79% / 95.0%。改算法或数据源要重新对照再改这里。


def line_note(market_key: str, as_of) -> str:
    return (f"RS 线 = 收盘价 ÷ {BENCH_LABEL.get(market_key, '基准指数')};"
            f"「RS线上涨天数」= RS 线连续站在自身 21 日均线之上的交易日数"
            f"(今天在均线下方就是 0)。截至 {as_of} 收盘,每晚更新,不含今天盘中。"
            f"日线最多回看约 300 个交易日,连续更久的也只记到这么多。")


def rs_raw_from_perf(p3: float | None, p6: float | None,
                     p12: float | None) -> float | None:
    """扫描源的 Perf.3M/6M/Y(百分数,2.5 表示 2.5%)→ RS Raw(小数,0.166 表示 16.6%)。

    单位和原项目的 rs_raw 保持一致(它是小数),方便和原项目的数字直接对照。
    任一周期缺失就返回 None —— 不拿零补。
    """
    if p3 is None or p6 is None or p12 is None:
        return None
    r3, r6, r12 = p3 / 100.0, p6 / 100.0, p12 / 100.0
    r9 = (r6 + r12) / 2.0                          # 见模块说明:9 个月是插值
    return (RS_WEIGHTS[63] * r3 + RS_WEIGHTS[126] * r6
            + RS_WEIGHTS[189] * r9 + RS_WEIGHTS[252] * r12)


def rs_ratings(raw: dict, universe_size: int,
               min_fraction: float = RS_UNIVERSE_THRESHOLD) -> tuple[dict, float]:
    """RS Raw → RS Rating(1–99)。→ ({key: 评级}, 覆盖率)

    与原项目 `compute_rs_rating` 一致:
      · pandas rank(pct=True, method="average") —— 并列取平均名次
      · round(pct × 98 + 1) —— Python 的 round 与 numpy 一样是"四舍六入五成双",
        所以 .5 的边界与原项目逐一相同
      · 覆盖率 < 门槛 → 全部不给(返回空 dict)
    `universe_size` 是分母:扫描到的全市场只数(含次新股),不是有 RS 的只数 ——
    否则次新股越多覆盖率反而越高,门槛就失去意义了。
    """
    valid = [(k, v) for k, v in raw.items() if v is not None]
    coverage = (len(valid) / universe_size) if universe_size > 0 else 0.0
    if not valid or coverage < min_fraction:
        return {}, coverage

    valid.sort(key=lambda kv: kv[1])
    n = len(valid)
    out: dict = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and valid[j + 1][1] == valid[i][1]:
            j += 1
        pct = ((i + 1) + (j + 1)) / 2.0 / n        # 并列取平均名次
        rating = int(round(pct * 98 + 1))
        for k in range(i, j + 1):
            out[valid[k][0]] = rating
        i = j + 1
    return out, coverage


def inject(rows: list[dict], market_key: str = "us", hist: dict | None = None,
           today=None) -> dict:
    """给扫描源返回的每一行补上 rs_raw / rs_rating / rs_line_up_days(没有资格的补 None)。

    hist  rs_history.load_stats 的结果 {代码: {as_of, up_days, rs_raw_exact, …}};
          None 或空 = 这个市场的日线还没建 → 评级用快照插值,RS 线天数全空。

    评级算法**二选一,不混用**:
      exact     日线新鲜、且排名池里 ≥90% 的票有精确 RS Raw → 用原项目原版公式
      snapshot  否则 → 扫描源快照 + 9 个月插值(模块开头那套)
    同一次排名里一半精确一半插值,两种数不可比,排出来的百分位没有意义。

    → 统计信息 {universe, excluded, young, eligible, coverage, gated, method,
               hist_as_of, hist_stale, line_n}
      universe  排名池只数(覆盖率的分母,含次新股)
      excluded  不在排名池里的(美股 OTC、微盘股、市值缺失)
    """
    from datetime import date as _date
    today = today or _date.today()

    pool: list[int] = []
    excluded = 0
    for idx, r in enumerate(rows):
        if in_population(r, market_key):
            pool.append(idx)
        else:
            excluded += 1

    # ── 日线统计:只认"最新那一天"的 ───────────────────────
    # 某只票的 as_of 早于全市场最新日 = 它停牌了或者昨晚没拉到;
    # 拿它几天前的 RS 线状态去和别人今天的比,不是一回事 → 当作没有
    hist = hist or {}
    hist_as_of = max((v["as_of"] for v in hist.values()), default=None)
    hist_stale = hist_as_of is None or (today - hist_as_of).days > HIST_STALE_DAYS
    fresh: dict = {}
    if not hist_stale:
        fresh = {c: v for c, v in hist.items() if v["as_of"] == hist_as_of}

    # ── 精确 RS Raw 覆盖率够不够 ─────────────────────────
    raw_exact = {idx: (fresh.get(rows[idx].get("_code")) or {}).get("rs_raw_exact")
                 for idx in pool}
    ratings, coverage = ({}, 0.0)
    method = "snapshot"
    young = 0
    if fresh:
        ratings, coverage = rs_ratings(raw_exact, len(pool))
        if ratings:
            method = "exact"
            raw = raw_exact
            # 精确法的"次新股"= 日线不足 253 根(原项目 warm-up);统计口径跟着换
            young = sum(1 for idx in pool if raw_exact[idx] is None)
    if method == "snapshot":
        raw = {}
        for idx in pool:
            r = rows[idx]
            if r.get("SMA250") is None:
                # 次新股:Perf.Y 是"上市以来"涨幅,不是真正的一年 —— 在池子里但不给评级
                young += 1
                raw[idx] = None
                continue
            raw[idx] = rs_raw_from_perf(r.get("Perf.3M"), r.get("Perf.6M"), r.get("Perf.Y"))
        ratings, coverage = rs_ratings(raw, len(pool))

    in_pool = set(pool)
    line_n = 0
    for idx, r in enumerate(rows):
        r["rs_raw"] = raw.get(idx)
        r["rs_rating"] = ratings.get(idx)
        st = fresh.get(r.get("_code")) if idx in in_pool else None
        r["rs_line_up_days"] = st.get("up_days") if st else None
        line_n += r["rs_line_up_days"] is not None
    eligible = sum(1 for v in raw.values() if v is not None)
    return {"universe": len(pool), "excluded": excluded, "young": young,
            "eligible": eligible, "coverage": coverage,
            "gated": not ratings and eligible > 0, "method": method,
            "hist_as_of": str(hist_as_of) if hist_as_of else None, "hist_stale": hist_stale and hist_as_of is not None,
            "line_n": line_n}


# ─────────────────────────────────────────────────────────────────────
# 原项目许可证(MIT)—— 移植了其中评级方法部分,按许可要求保留:
#
# MIT License
#
# Copyright (c) 2026 성진 (Seongjin)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ─────────────────────────────────────────────────────────────────────
