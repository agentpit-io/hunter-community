"""VCP(波动收缩形态,Mark Minervini)· 从日线里数出收缩次数、每次深度、量能是否递减。

2026-09-11 用户要求把「收缩次数 / 每次深度 / 量能是否递减」做成能直接筛选的字段。
在那之前只能用扫描源的快照近似(3月/1月/5日区间),**数不出收缩了几次、每次多深**。
同一天用户给出完整的 VCP 规则(首次 ≤50%、至少 3 次逐次变浅、末次 ≤10% 且低点缩量、
上涨放量下跌缩量、底部 3~12 个月),又补了最低点量比、近 20 日涨跌天数与涨跌日均量比,
并把回看窗口从 6 个月放宽到 12 个月。

纯计算,不连库不联网 —— tests/test_vcp.py 直接测。数据来自 rs_history 每晚落库的
全市场日线(已做拆股修正,最高/最低跟收盘同一个系数,成交量反向)。

## 口径(写死,不开放参数)

1. **摆动高点 / 低点**:比前后各 `SWING_K`=5 个交易日都高(低)的那根。
   一周以内的来回不算一次收缩 —— 否则日内噪音会被数成十几次「收缩」。
   用「时间」而不是「幅度」来定摆动点,是因为 VCP 最后一次收缩常常只有 3%~5%,
   按幅度阈值定摆动点的话,阈值要么大到漏掉它,要么小到被噪音淹没。
2. **一次收缩** = 一个摆动高点 → 其后的摆动低点;深度 = (高 − 低) ÷ 高。
   - 最后一个摆动高点之后,低点按「它之后的最低点」算(还没形成低点的算进行中;
     确认过的低点之后又跌破了的,按跌破后的算)。
   - **中途反抽并进同一次**:反弹没过前一个高点、接着又跌破前一个低点,是同一次下跌中间
     的反抽,不是新的一次收缩。不并的话 12 个月的长底部里,第一次 40% 的大回调会被切成
     20% + 25% 两段,再被第 4 条的「高点明显更低」截断 —— 首次深度和次数都会少算。
3. **只看最近 `BASE_MAX`=252 个交易日(约 12 个月)内开始的收缩**(用户的规则:底部 3~12 个月)。
4. **收缩序列**:从最近一次往前数,前一次必须**明显更深**(至少深 20%,且至少多 1 个百分点),
   而且前一次的高点不能比后一次**低**超过 3% —— 高点一路抬高是阶梯上涨,不是一个底部在收紧;
   反过来,枢轴(最后一次的高点)也不能比序列里任何一个更早的高点**低**超过 10% ——
   高点一路降低是下跌途中的反弹,真正的阻力在左边那个高点,不在枢轴。
   (2026-09-11 真实数据查出来的:TSLA 从 432.86 跌 31% 到 297,反弹到 366.5 又回落 6.5%,
   没有这条会被数成「收缩 2 次、距枢轴 0.8%」,看着像快突破,其实离左侧高点还差 15%。)
   一旦不满足就停。`contractions` = 这个序列的长度。
5. **底部已经走完的不算**:最后一次收缩之后,最高价冲过枢轴 `EXTENDED`=10% 以上 →
   突破早就发生了,现在不在底部里 → 0 次。窗口放宽到 12 个月以后这条是必需的:
   8 个月前走完一个 VCP、之后翻倍的票,不拦的话会带着「收缩 3 次、底部 240 天」通过筛选。
6. **量能递减**:每次收缩(从高点到低点那几天)的日均成交量,都比前一次少 → 1,否则 0。
   只有一次收缩时无从比较 → 空。
7. **最后一次收缩的量比** = 那几天的日均量 ÷ 它开始前 50 天的日均量。< 1 就是缩量。
8. **最低点量比** = 最后一次收缩的最低点当天及之前 2 天(共 `LOW_VOL_DAYS`=3 天)的日均量
   ÷ 同一个 50 天日均量。用户规则「最低点的成交量相对非常小」→ 它很小(如 ≤ 0.6)。
   取 3 天不取 1 天:单日量噪音太大,一次大单就能翻倍。
   ⚠ 别拿它和「最后一次收缩的量比」比大小来判断「越接近低点量越小」:最后一次收缩常常只有
   四五天,低点那 3 天几乎就是整次收缩,两个数差不多,比出来是抛硬币。「收缩时缩量」用量比 < 1。
9. **枢轴点** = 最后一次收缩的起点高点(Minervini 的买点)。距枢轴 = (枢轴 − 收盘) ÷ 枢轴,
   负数表示已经突破。「枢轴附近」写 `vcp_pivot_dist >= -3 and vcp_pivot_dist <= 5`。
   ⚠ 别拿「近 1 月最高价」当枢轴:今天的收盘一定在近 1 月的区间里,
   「收盘 ≤ 枢轴 × 1.03」就永远成立,那条上限等于没写。
10. **低点抬高不单独给字段 —— 收缩次数 ≥ 2 已经保证了。** 相邻两次收缩,后一次低点更低只有两种情况:
   高点也更低 → 第 2 条当成中途反抽,并成同一次;高点更高 → 后一次反而更深,第 4 条的序列在这里断开。
   所以序列里的低点一定逐次抬高(2026-09-11 本想加 `vcp_higher_lows`,写用例时发现它恒为 1)。
   **改第 2 条的合并规则或第 4 条的断开条件时,这条保证可能失效**,tests/test_vcp.py 里有用例盯着。

## 精确交易日窗口(不属于形态本身,搭同一份日线)

`high_5d / low_5d / high_21d / low_21d / high_63d / low_63d` = 最近 5 / 21 / 63 根日线的最高 / 最低。
扫描源的 `High.5D / High.1M / High.3M` 是按日历往回数的固定窗口,**不是这么多个交易日**:
2026-09-11(那周一劳动节休市)拿 16 只票逐个窗口长度比对,5D 实测是最近 **4** 根、
1M 是 21 根、3M 是 **61** 根。脚本里写 `Highest(high, 63)` 也还是映射到 `High.3M`,改写法不改数据。
要严格按交易日算,用这几个字段。

## 量价(不属于形态本身,搭同一份日线)

近 `PV_DAYS`=20 个交易日(约 4 周):收盘高于前一天算上涨日、低于算下跌日(平盘两边都不算)。
`ud_vol_ratio_20d` = 上涨日的日均量 ÷ 下跌日的日均量 —— 用户规则「上升时成交量放大、
下跌时缩小」。用**日均**而不是 IBD 那种总量相除:总量比把「涨的天数多」也混了进去,
而用户把「上涨次数 > 下降次数」单列成了另一条。

## 算不出就是空,不猜

日线不足 `MIN_BARS`、最高最低缺失 → 形态整组为空;成交量缺失 → 量能那几项为空。
涨跌天数只要收盘价(老数据也有);涨跌日均量比要成交量;20 天里没有下跌日 → 比值为空。
"""
from __future__ import annotations

SWING_K = 5
BASE_MAX = 252           # 12 个月
MIN_BARS = 60
TIGHTER_RATIO = 1.2      # 前一次至少比后一次深 20%
TIGHTER_ABS = 1.0        # 且至少多 1 个百分点(2.4% vs 2.0% 不算真的收紧)
HIGH_TOL = 0.03          # 往前数时,前一个高点比后一个低 3% 以上 = 阶梯上涨,序列断开
PIVOT_TOL = 0.10         # 枢轴比更早的某个高点低 10% 以上 = 下跌中的反弹,序列断开
                         # (比累计量而不是相邻两次比:100→91→83 每步都不到 10%,累计已经跌了 17%)
EXTENDED = 0.10          # 最后一次收缩之后冲过枢轴 10% 以上 = 这个底部已经走完
VOL_BASE = 50            # 量比的分母:收缩开始前 50 天的日均量
VOL_BASE_MIN = 20        # 前面不足 20 天就不算量比
LOW_VOL_DAYS = 3         # 最低点量比:低点当天及之前 2 天
PV_DAYS = 20             # 涨跌天数 / 涨跌日均量比的窗口(约 4 周)
WINDOWS = (5, 21, 63)    # 精确交易日窗口的最高 / 最低

# 能直接筛选的字段(数字)。vcp_depths 是展示用的文字(「24.1→11.3→5.0」),不能拿来比大小
WINDOW_FIELDS = tuple(f"{hl}_{n}d" for n in WINDOWS for hl in ("high", "low"))
FIELDS = ("vcp_contractions", "vcp_first_depth", "vcp_last_depth", "vcp_vol_declining",
          "vcp_last_vol_ratio", "vcp_pivot_dist", "vcp_base_days", "vcp_low_vol_ratio",
          "up_days_20d", "down_days_20d", "ud_vol_ratio_20d") + WINDOW_FIELDS
DISPLAY = "vcp_depths"

# 扫描结果里给用户看的口径说明 —— 字段是算出来的,用户得知道它是怎么数的才能判断信不信
NOTE = ("VCP 字段来自 {as_of} 收盘的日线:一次收缩 = 摆动高点(比前后各 5 个交易日都高)"
        "到其后的低点,只看最近约 12 个月;往前数时前一次要**明显更深**(至少深 20%)、"
        "且各次高点大致持平(不能一路抬高,最后的高点也不能比左侧高点低 10% 以上),"
        "才算连续收缩;之后已经冲过枢轴 10% 以上的算底部走完(0 次)。"
        "深度、距枢轴是百分比;量比 < 1 表示缩量;最低点量比 = 低点及前 2 天的日均量 ÷ 收缩前 50 天日均量。"
        "近 20 日涨跌天数按收盘比前一天,涨跌日均量比 = 上涨日日均量 ÷ 下跌日日均量。")


def _swings(highs: list[float], lows: list[float], k: int = SWING_K) -> list[tuple[int, str, float]]:
    """→ [(下标, 'H'|'L', 价格)],按时间排序、高低交替。右端 k 根以内的不确认。"""
    n = len(highs)
    pts: list[tuple[int, str, float]] = []
    for i in range(k, n - k):
        win_h = highs[i - k:i + k + 1]
        win_l = lows[i - k:i + k + 1]
        if highs[i] >= max(win_h):
            pts.append((i, "H", highs[i]))
        if lows[i] <= min(win_l):
            pts.append((i, "L", lows[i]))
    pts.sort(key=lambda p: (p[0], 0 if p[1] == "H" else 1))
    out: list[tuple[int, str, float]] = []
    for p in pts:
        if out and out[-1][1] == p[1]:
            # 连着两个高点取更高的,连着两个低点取更低的(平顶/平底取先出现的那个)
            if (p[1] == "H" and p[2] > out[-1][2]) or (p[1] == "L" and p[2] < out[-1][2]):
                out[-1] = p
            continue
        out.append(p)
    return out


def _depth(c) -> float:
    return (c[1] - c[3]) / c[1] * 100.0


def _mean(xs: list) -> float | None:
    return sum(xs) / len(xs) if xs and all(x is not None for x in xs) else None


def _contractions(highs: list[float], lows: list[float]) -> list[tuple[int, float, int, float]]:
    """→ [(高点下标, 高, 低点下标, 低)],按时间排序,已合并中途反抽。"""
    n = len(highs)
    sw = _swings(highs, lows)
    raw = [(a[0], a[2], b[0], b[2]) for a, b in zip(sw, sw[1:]) if a[1] == "H" and b[1] == "L"]
    last_h = next((p for p in reversed(sw) if p[1] == "H"), None)
    if last_h is not None and last_h[0] < n - 1:
        # 最后一个摆动高点之后的真实低点:还没确认的(进行中)、确认后又跌破的,都按它之后的最低点
        if raw and raw[-1][0] == last_h[0]:
            raw.pop()
        h = last_h[0]
        li = min(range(h + 1, n), key=lambda i: lows[i])
        if lows[li] < highs[h]:
            raw.append((h, highs[h], li, lows[li]))
    cons: list[tuple[int, float, int, float]] = []
    for c in raw:
        if cons and c[1] <= cons[-1][1] and c[3] < cons[-1][3]:
            m = cons[-1]
            cons[-1] = (m[0], m[1], c[2], c[3])     # 反弹没过前高又跌破前低 = 同一次下跌中间的反抽
        else:
            cons.append(c)
    return cons


def vcp_stats(bars: list[tuple]) -> dict | None:
    """bars = [(日期, 收盘, 最高, 最低, 成交量)],按日期升序,已做拆股修正。

    → {as_of, contractions, depths, first_depth, last_depth, vol_declining,
       last_vol_ratio, low_vol_ratio, pivot, pivot_dist, base_days};日线不够或缺最高最低 → None。
    """
    if not bars or len(bars) < MIN_BARS:
        return None
    closes = [b[1] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    vols = [b[4] for b in bars]
    if any(x is None or x <= 0 for x in highs + lows + closes):
        return None                         # 最高最低缺失(老数据只存了收盘)—— 不拿收盘价顶替
    n = len(bars)
    out = {"as_of": bars[-1][0], "contractions": 0, "depths": "", "first_depth": None,
           "last_depth": None, "vol_declining": None, "last_vol_ratio": None,
           "low_vol_ratio": None, "pivot": None, "pivot_dist": None, "base_days": None,
           "last_low": None,      # 最后一次收缩的低点(小鹿方向 C 的止损基准,不进筛选字段)
           "last_depth_close": None}   # 末次收缩按收盘口径的深度 %(小鹿方向 A 用;盘中口径的 last_depth 多数在 6~12%,收盘口径小得多)

    cons = [c for c in _contractions(highs, lows) if c[0] >= n - BASE_MAX]
    if not cons:
        return out                           # 窗口里没有收缩(一路新高 / 一路下跌)—— 0 次,不是空

    seq = [cons[-1]]
    for c in reversed(cons[:-1]):
        cur = seq[0]
        dp, dc = _depth(c), _depth(cur)
        if dp < dc * TIGHTER_RATIO or dp - dc < TIGHTER_ABS:
            break                            # 前一次没有明显更深 —— 收紧到此为止
        if c[1] < cur[1] * (1 - HIGH_TOL):
            break                            # 前一个高点明显更低 = 阶梯上涨,不是同一个底部
        if seq[-1][1] < c[1] * (1 - PIVOT_TOL):
            break                            # 枢轴离左边的高点还差一大截 = 下跌中的反弹
        seq.insert(0, c)

    pivot = seq[-1][1]
    if max(highs[seq[-1][2]:]) > pivot * (1 + EXTENDED):
        return out                           # 早就突破走远了 —— 现在不在底部里

    depths = [_depth(c) for c in seq]
    out["contractions"] = len(seq)
    out["depths"] = "→".join(f"{d:.1f}" for d in depths)
    out["first_depth"] = round(depths[0], 2)
    out["last_depth"] = round(depths[-1], 2)
    out["base_days"] = n - 1 - seq[0][0]
    out["pivot"] = pivot
    out["last_low"] = seq[-1][3]
    seg_c = [x for x in closes[seq[-1][0]:seq[-1][2] + 1] if x is not None]
    if seg_c and max(seg_c) > 0:
        out["last_depth_close"] = round((max(seg_c) - min(seg_c)) / max(seg_c) * 100.0, 2)
    out["pivot_dist"] = round((pivot - closes[-1]) / pivot * 100.0, 2)

    seg_vol = [_mean(vols[c[0]:c[2] + 1]) for c in seq]
    if len(seq) >= 2 and all(v is not None for v in seg_vol):
        out["vol_declining"] = int(all(b < a for a, b in zip(seg_vol, seg_vol[1:])))
    last_h, last_l = seq[-1][0], seq[-1][2]
    base_vol = _mean(vols[max(0, last_h - VOL_BASE):last_h])
    if base_vol and last_h >= VOL_BASE_MIN:
        if seg_vol[-1] is not None:
            out["last_vol_ratio"] = round(seg_vol[-1] / base_vol, 3)
        low_vol = _mean(vols[max(last_h, last_l - LOW_VOL_DAYS + 1):last_l + 1])
        if low_vol is not None:
            out["low_vol_ratio"] = round(low_vol / base_vol, 3)
    return out


def pv_stats(bars: list[tuple], days: int = PV_DAYS) -> dict:
    """近 days 个交易日的涨跌天数与涨跌日均量比。→ {up_days, down_days, ud_vol_ratio},算不出的为 None。"""
    out = {"up_days": None, "down_days": None, "ud_vol_ratio": None}
    if not bars or len(bars) < days + 1:
        return out
    closes = [b[1] for b in bars[-(days + 1):]]
    if any(c is None or c <= 0 for c in closes):
        return out
    vols = [b[4] if len(b) > 4 else None for b in bars[-days:]]
    up = [i for i in range(days) if closes[i + 1] > closes[i]]
    dn = [i for i in range(days) if closes[i + 1] < closes[i]]
    out["up_days"], out["down_days"] = len(up), len(dn)
    uv, dv = _mean([vols[i] for i in up]), _mean([vols[i] for i in dn])
    if uv is not None and dv:
        out["ud_vol_ratio"] = round(uv / dv, 3)
    return out


def window_stats(bars: list[tuple]) -> dict:
    """最近 5 / 21 / 63 根日线的最高 / 最低 → {high_5d, low_5d, …};根数不够或缺高低的窗口为 None。"""
    out: dict = {}
    for n in WINDOWS:
        seg = bars[-n:] if bars and len(bars) >= n else []
        ok = bool(seg) and all(len(b) > 3 and b[2] and b[3] for b in seg)
        out[f"high_{n}d"] = max(b[2] for b in seg) if ok else None
        out[f"low_{n}d"] = min(b[3] for b in seg) if ok else None
    return out


def inject(rows: list[dict], hist: dict | None, stale: bool, used=None) -> int:
    """给扫描行补上本模块的字段(没有的补 None)。→ 条件里用到的字段**全部**有值的只数。

    hist = rs_history.load_stats 的结果;只认「全市场最新那一天」的统计,
    停牌/没拉到的票拿几天前的形态和别人今天的比,不是一回事。
    used = 条件里用到的本模块字段;按它数覆盖 —— 只用涨跌天数(老数据就有)时,
    不能按收缩次数(要等整窗重拉)去报「只有 16 只算得出」。
    """
    hist = hist or {}
    need = [f for f in (used or ("vcp_contractions",)) if f in FIELDS] or ["vcp_contractions"]
    as_of = max((v["as_of"] for v in hist.values()), default=None)
    fresh = {} if stale or as_of is None else {c: v for c, v in hist.items() if v["as_of"] == as_of}
    n = 0
    for r in rows:
        st = fresh.get(r.get("_code")) or {}
        for f in FIELDS:
            r[f] = st.get(f)
        r[DISPLAY] = st.get(DISPLAY) or None
        n += all(r[f] is not None for f in need)
    return n
