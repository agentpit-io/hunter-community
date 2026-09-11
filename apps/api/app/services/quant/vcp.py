"""VCP(波动收缩形态,Mark Minervini)· 从日线里数出收缩次数、每次深度、量能是否递减。

2026-09-11 用户要求把「收缩次数 / 每次深度 / 量能是否递减」做成能直接筛选的字段。
在那之前只能用扫描源的快照近似(3月/1月/5日区间),**数不出收缩了几次、每次多深**。

纯计算,不连库不联网 —— tests/test_vcp.py 直接测。数据来自 rs_history 每晚落库的
全市场日线(已做拆股修正,最高/最低跟收盘同一个系数,成交量反向)。

## 口径(写死,不开放参数)

1. **摆动高点 / 低点**:比前后各 `SWING_K`=5 个交易日都高(低)的那根。
   一周以内的来回不算一次收缩 —— 否则日内噪音会被数成十几次「收缩」。
   用「时间」而不是「幅度」来定摆动点,是因为 VCP 最后一次收缩常常只有 3%~5%,
   按幅度阈值定摆动点的话,阈值要么大到漏掉它,要么小到被噪音淹没。
2. **一次收缩** = 一个摆动高点 → 其后的摆动低点;深度 = (高 − 低) ÷ 高。
   最后一个摆动高点之后还没形成低点的,算「进行中的收缩」,深度按它之后的最低点算。
3. **只看最近 `BASE_MAX`=130 个交易日(约 6 个月)内开始的收缩。**
4. **收缩序列**:从最近一次往前数,前一次必须**明显更深**(至少深 20%,且至少多 1 个百分点),
   而且前一次的高点不能比后一次**低**超过 3% —— 高点一路抬高是阶梯上涨,不是一个底部在收紧;
   反过来,枢轴(最后一次的高点)也不能比序列里任何一个更早的高点**低**超过 10% ——
   高点一路降低是下跌途中的反弹,真正的阻力在左边那个高点,不在枢轴。
   (2026-09-11 真实数据查出来的:TSLA 从 432.86 跌 31% 到 297,反弹到 366.5 又回落 6.5%,
   没有这条会被数成「收缩 2 次、距枢轴 0.8%」,看着像快突破,其实离左侧高点还差 15%。)
   一旦不满足就停。`contractions` = 这个序列的长度。
5. **量能递减**:每次收缩(从高点到低点那几天)的日均成交量,都比前一次少 → 1,否则 0。
   只有一次收缩时无从比较 → 空。
6. **最后一次收缩的量比** = 那几天的日均量 ÷ 它开始前 50 天的日均量。< 1 就是缩量。
7. **枢轴点** = 最后一次收缩的起点高点(Minervini 的买点)。距枢轴 = (枢轴 − 收盘) ÷ 枢轴,
   负数表示已经突破。

## 算不出就是空,不猜

日线不足 `MIN_BARS`、最高最低缺失 → 整组为空;成交量缺失 → 只有量能那两个为空。
"""
from __future__ import annotations

SWING_K = 5
BASE_MAX = 130
MIN_BARS = 60
TIGHTER_RATIO = 1.2      # 前一次至少比后一次深 20%
TIGHTER_ABS = 1.0        # 且至少多 1 个百分点(2.4% vs 2.0% 不算真的收紧)
HIGH_TOL = 0.03          # 往前数时,前一个高点比后一个低 3% 以上 = 阶梯上涨,序列断开
PIVOT_TOL = 0.10         # 枢轴比更早的某个高点低 10% 以上 = 下跌中的反弹,序列断开
                         # (比累计量而不是相邻两次比:100→91→83 每步都不到 10%,累计已经跌了 17%)
VOL_BASE = 50            # 量比的分母:收缩开始前 50 天的日均量
VOL_BASE_MIN = 20        # 前面不足 20 天就不算量比

# 能直接筛选的字段(数字)。vcp_depths 是展示用的文字(「24.1→11.3→5.0」),不能拿来比大小
FIELDS = ("vcp_contractions", "vcp_first_depth", "vcp_last_depth", "vcp_vol_declining",
          "vcp_last_vol_ratio", "vcp_pivot_dist", "vcp_base_days")
DISPLAY = "vcp_depths"

# 扫描结果里给用户看的口径说明 —— 字段是算出来的,用户得知道它是怎么数的才能判断信不信
NOTE = ("VCP 字段来自 {as_of} 收盘的日线:一次收缩 = 摆动高点(比前后各 5 个交易日都高)"
        "到其后的低点,只看最近约 6 个月;往前数时前一次要**明显更深**(至少深 20%)、"
        "且各次高点大致持平(不能一路抬高,最后的高点也不能比左侧高点低 10% 以上),"
        "才算连续收缩。深度、距枢轴是百分比;量比 < 1 表示缩量。")


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


def vcp_stats(bars: list[tuple]) -> dict | None:
    """bars = [(日期, 收盘, 最高, 最低, 成交量)],按日期升序,已做拆股修正。

    → {as_of, contractions, depths, first_depth, last_depth, vol_declining,
       last_vol_ratio, pivot, pivot_dist, base_days};日线不够或缺最高最低 → None。
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
           "pivot": None, "pivot_dist": None, "base_days": None}

    sw = _swings(highs, lows)
    cons: list[tuple[int, float, int, float]] = []          # (高点下标, 高, 低点下标, 低)
    for a, b in zip(sw, sw[1:]):
        if a[1] == "H" and b[1] == "L":
            cons.append((a[0], a[2], b[0], b[2]))
    if sw and sw[-1][1] == "H" and sw[-1][0] < n - 1:
        # 进行中的收缩:最后一个高点之后还没确认低点
        h = sw[-1][0]
        li = min(range(h + 1, n), key=lambda i: lows[i])
        if lows[li] < highs[h]:
            cons.append((h, highs[h], li, lows[li]))
    cons = [c for c in cons if c[0] >= n - BASE_MAX]
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

    depths = [_depth(c) for c in seq]
    out["contractions"] = len(seq)
    out["depths"] = "→".join(f"{d:.1f}" for d in depths)
    out["first_depth"] = round(depths[0], 2)
    out["last_depth"] = round(depths[-1], 2)
    out["base_days"] = n - 1 - seq[0][0]

    pivot = seq[-1][1]
    out["pivot"] = pivot
    out["pivot_dist"] = round((pivot - closes[-1]) / pivot * 100.0, 2)

    seg_vol = [_mean(vols[c[0]:c[2] + 1]) for c in seq]
    if len(seq) >= 2 and all(v is not None for v in seg_vol):
        out["vol_declining"] = int(all(b < a for a, b in zip(seg_vol, seg_vol[1:])))
    last_h = seq[-1][0]
    base_vol = _mean(vols[max(0, last_h - VOL_BASE):last_h])
    if seg_vol[-1] is not None and base_vol and last_h >= VOL_BASE_MIN:
        out["last_vol_ratio"] = round(seg_vol[-1] / base_vol, 3)
    return out


def inject(rows: list[dict], hist: dict | None, stale: bool) -> int:
    """给扫描行补上 VCP 字段(没有的补 None)。→ 有值的只数。

    hist = rs_history.load_stats 的结果;只认「全市场最新那一天」的统计,
    停牌/没拉到的票拿几天前的形态和别人今天的比,不是一回事。
    """
    hist = hist or {}
    as_of = max((v["as_of"] for v in hist.values()), default=None)
    fresh = {} if stale or as_of is None else {c: v for c, v in hist.items() if v["as_of"] == as_of}
    n = 0
    for r in rows:
        st = fresh.get(r.get("_code")) or {}
        for f in FIELDS:
            r[f] = st.get(f)
        r[DISPLAY] = st.get(DISPLAY) or None
        n += r["vcp_contractions"] is not None
    return n
