"""关键词匹配:自然语言 → 筛选脚本(**不花 token**)。

绝大多数筛选描述其实是很规整的三段式:**字段 + 比较符 + 数字**
(「成交量大于100万」「市盈率低于15」「RSI 小于 30」),再加上少数几个
成句的行话(「站上50日均线」「均线多头排列」)。这些用规则就能完整覆盖,
没必要每次都掏钱调模型。

## 位置:脚本 → 本地关键词 → (用户点了才)AI

`parse_script` 的顺序是:
  1. 当脚本解析。成功就结束。
  2. 本地关键词匹配。成功就结束 —— **零 token**。
  3. 都不行,返回 `can_try_ai`,前端弹出「AI 识别」按钮。
     **用户点了才花钱**,不点就一分不花。

## 全中或全不中,不做"部分识别"

一句话里有 4 个条件、只认出 3 个的时候,**不产出那 3 个**。
偷偷少一个条件,用户看到的是一份"看起来正常"的结果,而他以为的筛选
和实际跑的筛选不是一回事 —— 这种错最难发现。所以只要有一段没认出来,
就整体判失败,把没认出来的原文列出来,让用户自己决定是改写还是叫 AI。

## 数字单位

「100万」= 1000000,「1亿」= 100000000。
百分号**不换算**:扫描源的百分比字段本身就是以百分数计的
(return_on_equity 给的 15 就是 15%),写成 0.15 反而错。
"""
from __future__ import annotations

import re

from app.services.quant.screen_dsl import ScreenError

# ═══════════════════════════════════════════════════════════════
# 词表
# ═══════════════════════════════════════════════════════════════

# 固定字段:中文/英文说法 → 扫描源字段名。
# 同一个字段可以有多种说法,长的写在前面(匹配时按长度倒序,避免「市盈率」被「市」截胡)。
_FIELD_WORDS: dict[str, str] = {
    "成交量": "volume", "成交额": "volume", "volume": "volume", "量能": "volume",
    "收盘价": "close", "股价": "close", "价格": "close", "现价": "close",
    "close": "close", "price": "close", "收盘": "close",
    "开盘价": "open", "open": "open",
    "最高价": "high", "high": "high",
    "最低价": "low", "low": "low",

    "市盈率ttm": "price_earnings_ttm", "市盈率": "price_earnings_ttm",
    "pe": "price_earnings_ttm", "pettm": "price_earnings_ttm",
    "市净率": "price_book_fq", "pb": "price_book_fq",
    "净资产收益率": "return_on_equity", "roe": "return_on_equity",
    "市值": "market_cap_basic", "总市值": "market_cap_basic",
    "marketcap": "market_cap_basic",
    "股息率": "dividends_yield_current", "分红率": "dividends_yield_current",
    "毛利率": "gross_margin_ttm",
    "营收增长": "total_revenue_yoy_growth_ttm",
    "营收同比": "total_revenue_yoy_growth_ttm",
    "收入增长": "total_revenue_yoy_growth_ttm",
    "负债权益比": "debt_to_equity", "负债率": "debt_to_equity",
    "流动比率": "current_ratio",
    "每股收益": "earnings_per_share_diluted_ttm", "eps": "earnings_per_share_diluted_ttm",
    "贝塔": "beta_1_year", "beta": "beta_1_year",

    "涨跌幅": "change", "涨幅": "change", "跌幅": "change", "change": "change",
    "相对成交量": "relative_volume_10d_calc", "量比": "relative_volume_10d_calc",
    "换手率": "relative_volume_10d_calc",

    "52周最高": "price_52_week_high", "52周新高": "price_52_week_high",
    "52周最低": "price_52_week_low", "52周新低": "price_52_week_low",
    "历史最高": "all_time_high", "历史新高": "all_time_high",

    "rsi": "RSI", "adx": "ADX", "atr": "ATR",
    # RS 相对强度评级(IBD 口径,1–99,见 screen_rs)。
    # "rs" 走英文词边界匹配,所以不会吃掉 "rsi" 里的 rs。
    #
    # **光秃秃的「相对强度」「相对强弱」不收**:中文里 RSI 就叫「相对强弱指数」,
    # 也常译作「相对强度指数」。「相对强度大于80」—— 80 既是常见的 RS 门槛,
    # 也是常见的 RSI 超买线,两种理解都说得通,猜哪个都可能静默出错。
    # 只收带「评级」「指数/指标」后缀、没有歧义的说法;光秃秃的交给用户改写或 AI。
    "rs": "rs_rating", "rs_rating": "rs_rating", "rs评级": "rs_rating",
    "rs值": "rs_rating", "ibd rs": "rs_rating", "rs rating": "rs_rating",
    "相对强度评级": "rs_rating", "rs相对强度": "rs_rating",
    "相对强弱指数": "RSI", "相对强弱指标": "RSI",
    "相对强度指数": "RSI", "相对强度指标": "RSI",

    # 常用技术指标的中文/缩写说法(2026-09-11 补;之前一个都不认)
    "macd柱": "MACD.hist", "macd红柱": "MACD.hist", "macd绿柱": "MACD.hist",
    "macd柱状": "MACD.hist", "macd": "MACD.macd",
    "dif": "MACD.macd", "diff": "MACD.macd", "dea": "MACD.signal",
    "macd信号线": "MACD.signal",
    # KDJ 的 K/D 必须带「值」或「线」—— 裸 k 会和「100k」这类单位撞车
    "k值": "Stoch.K", "d值": "Stoch.D", "k线值": "Stoch.K",
    "kdj的k": "Stoch.K", "kdj的d": "Stoch.D",
    "布林上轨": "BB.upper", "布林带上轨": "BB.upper", "boll上轨": "BB.upper",
    "布林中轨": "BB.basis", "布林带中轨": "BB.basis", "boll中轨": "BB.basis",
    "布林下轨": "BB.lower", "布林带下轨": "BB.lower", "boll下轨": "BB.lower",
    "vwap": "VWAP", "成交量加权均价": "VWAP", "均价线": "VWAP",
    "近1周涨幅": "Perf.W", "近1月涨幅": "Perf.1M", "近3月涨幅": "Perf.3M",
    "近6月涨幅": "Perf.6M", "近1年涨幅": "Perf.Y", "年初至今涨幅": "Perf.YTD",
}

# 比较符。**长的必须排在短的前面**:「大于等于」不能被「大于」先吃掉。
_OP_WORDS: list[tuple[str, str]] = [
    ("大于等于", ">="), ("不小于", ">="), ("不低于", ">="), ("至少", ">="),
    ("小于等于", "<="), ("不大于", "<="), ("不高于", "<="), ("不超过", "<="), ("最多", "<="),
    ("大于", ">"), ("高于", ">"), ("超过", ">"), ("多于", ">"), ("超出", ">"),
    ("小于", "<"), ("低于", "<"), ("少于", "<"), ("不到", "<"), ("低过", "<"),
    ("等于", "=="),
    (">=", ">="), ("<=", "<="), ("=>", ">="), ("=<", "<="),
    (">", ">"), ("<", "<"), ("==", "=="), ("=", "=="),
    ("greater than or equal", ">="), ("less than or equal", "<="),
    ("greater than", ">"), ("more than", ">"), ("above", ">"), ("over", ">"),
    ("less than", "<"), ("below", "<"), ("under", "<"),
    ("equals", "=="), ("equal to", "=="),
]

_UNIT = {"万": 1e4, "亿": 1e8, "k": 1e3, "m": 1e6, "b": 1e9}

_NUM_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(万亿|亿|万|[kmb]|%|％)?", re.I)


def _parse_number(m: re.Match) -> float:
    v = float(m.group(1))
    u = (m.group(2) or "").lower()
    if u == "万亿":
        return v * 1e12
    if u in _UNIT:
        return v * _UNIT[u]
    # 百分号不换算 —— 扫描源的百分比字段本身就是百分数计的
    return v


def _fmt(v: float) -> str:
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10)).rstrip("0").rstrip(".")


# ═══════════════════════════════════════════════════════════════
# 单句解析
# ═══════════════════════════════════════════════════════════════

#  `、` 必须切 —— 漏了它,「毛利率大于40%、营收同比大于20%」会被当成一句,
#  字段匹配只认出后一个,**前一个条件被悄悄丢掉**(实测)。静默少一个条件
#  正是这个模块最要避免的失败方式。
_SPLIT_RE = re.compile(r"[,，;；。、\n]|(?:\s+and\s+)|并且|而且|同时|以及", re.I)
# 「且」单独切一刀 —— 但不能切「而且」(上面已经处理),也不能切在词中间
_AND_RE = re.compile(r"且")

# 均线要认**两种语序**:
#   中文习惯  数字在前  20日均线 / 20日线 / 20日MA
#   技术分析  数字在后  MA20 / SMA20 / EMA20 / MA(20)
# 2026-09-11 之前只认前一种,「EMA20大于EMA50」整句识别不了;更糟的是
# 「收盘价大于MA20」会把 MA20 里的 20 当成阈值,静默产出 `close > 20`。
#
# `(?<![a-z])s?ma(?![a-z])` 两侧的断言不能省:
#   左边 —— 挡住 EMA 里的 ma(否则 EMA20 会同时被当成 SMA20)
#   右边 —— 挡住 MACD 里的 ma
_MA_RE = re.compile(
    r"(?:(\d+)\s*(?:日|天)?\s*(?:均线|线|(?<![a-z])s?ma(?![a-z])|移动平均))"
    r"|(?:(?<![a-z])s?ma\s*\(?\s*(\d+)\s*\)?)", re.I)
_EMA_RE = re.compile(
    r"(?:(\d+)\s*(?:日|天)?\s*ema)|(?:ema\s*\(?\s*(\d+)\s*\)?)", re.I)
_AVGVOL_RE = re.compile(r"(\d+)\s*(?:日|天)\s*(?:均量|平均成交量|均成交量)", re.I)
_RSI_N_RE = re.compile(r"rsi\s*\(?\s*(\d+)\s*\)?", re.I)


class _Vocab:
    """把可用周期带进来 —— 能不能用 SMA37 由扫描源说了算,不在这里硬编码。"""

    def __init__(self, has_field, sma, ema, rsi):
        self.has_field = has_field
        self.sma, self.ema, self.rsi = set(sma), set(ema), set(rsi)
        # 按长度倒序,保证「市盈率ttm」先于「市盈率」、「52周最高」先于「最高价」
        self.words = sorted(_FIELD_WORDS.items(), key=lambda kv: -len(kv[0]))

    def _candidates(self, text: str) -> list[tuple[int, int, str, str, str]]:
        """文本里所有可能的字段命中 → [(起点, -长度, 字段名, 原文, 错误)]。

        `错误` 非空表示"认出来了但用不了"(如 37 日均线),排序时照样参与 ——
        它排在最左就该报错,而不是被右边一个能用的字段顶掉。
        """
        low = text.lower()
        low_ns = low.replace(" ", "")
        out: list[tuple[int, int, str, str, str]] = []

        for m in _AVGVOL_RE.finditer(text):
            n = int(m.group(1))
            err = "" if n in (10, 30, 60, 90) else \
                f"「{m.group(0)}」映射不了 —— 扫描源只有 10/30/60/90 天均量"
            out.append((m.start(), -len(m.group(0)),
                        f"average_volume_{n}d_calc", m.group(0), err))
        for m in _EMA_RE.finditer(text):
            n = int(m.group(1) or m.group(2))     # 两种语序,数字在不同分组
            err = "" if n in self.ema else f"没有 {n} 日 EMA"
            out.append((m.start(), -len(m.group(0)), f"EMA{n}", m.group(0), err))
        for m in _RSI_N_RE.finditer(text):
            n = int(m.group(1))
            fld = "RSI" if n == 14 else f"RSI{n}"
            err = "" if (n == 14 or n in self.rsi) else f"没有 RSI({n})"
            out.append((m.start(), -len(m.group(0)), fld, m.group(0), err))
        for m in _MA_RE.finditer(text):
            n = int(m.group(1) or m.group(2))
            err = "" if n in self.sma else \
                f"「{m.group(0)}」映射不了 —— 扫描源没有 SMA{n}"
            out.append((m.start(), -len(m.group(0)), f"SMA{n}", m.group(0), err))

        for w, fld in self.words:
            if not self.has_field(fld):
                continue
            if w.isascii():
                # 纯英文词必须按**词边界**匹配。裸 `in` 会让 "RSI below 30"
                # 里的 "be(low)" 命中字段 low —— 实测真踩到,产出 `low < 30`。
                m = re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)
                if m:
                    out.append((m.start(), -len(w), fld, w, ""))
            else:
                i = low.find(w)
                if i >= 0:
                    out.append((i, -len(w), fld, w, ""))
                elif w in low_ns:
                    # 原文里夹了空格(「市 盈 率」)。位置没法精确还原,
                    # 排到最后 —— 只在没有别的候选时才用它。
                    out.append((len(text), -len(w), fld, w, ""))
        return out

    def field(self, text: str) -> tuple[str, str] | None:
        """在文本里找字段 —— **取最左出现的那个**,同位置取更长的。

        2026-09-10 修:原来是按"模式类别"顺序返回(均线正则整体排在词表前面),
        于是「收盘价高于50日均线」的左操作数被判成 SMA50 而不是 close。
        句子里谁先出现谁就是左操作数,这是唯一说得通的规则。
        """
        cands = self._candidates(text)
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]))
        start, _neg, fld, word, err = cands[0]
        if err:
            raise ScreenError(err)
        return fld, word

    def ma_field(self, text: str) -> str | None:
        """只找均线类字段 —— 「站上/跌破 X 日均线」这类模板专用。

        不能用 field():那句话里 `收盘价` 出现在更左边,会被优先返回。
        """
        for _s, _n, fld, _w, err in sorted(self._candidates(text),
                                           key=lambda c: (c[0], c[1])):
            if err:
                raise ScreenError(err)
            if fld.startswith("SMA") or fld.startswith("EMA"):
                return fld
        return None

    def fields_in(self, text: str) -> list[tuple[int, int, str, str]]:
        """句子里**全部**字段,从左到右、互不重叠 → [(起, 止, 字段, 原文)]。

        贪心取最左最长:「macd柱」与「macd」同在 0 位时取前者,
        然后跳到它的末尾再找下一个。重叠的候选(EMA20 里的 ma)自然被跳过。
        """
        cands = sorted(self._candidates(text), key=lambda c: (c[0], c[1]))
        out: list[tuple[int, int, str, str]] = []
        pos = -1
        for start, neg, fld, word, err in cands:
            if start < pos:
                continue
            if err:
                raise ScreenError(err)
            end = start + (-neg)
            out.append((start, end, fld, word))
            pos = end
        return out


# 字段的"单位"。字段对字段比较时两边必须同单位,否则就是在比苹果和橘子。
#
# 2026-09-11 实测:「成交量大于50日均线」产出 `volume > SMA50` ——
# 成交量是股数(千万量级),均线是价格(几十块),这个比较对所有股票都成立,
# 等于一条废条件混进了筛选里,而且看起来还挺像回事。
def _unit(fld: str) -> str | None:
    if fld in ("close", "open", "high", "low", "VWAP", "price_52_week_high",
               "price_52_week_low", "all_time_high", "all_time_low") \
            or fld.startswith(("SMA", "EMA", "BB.", "High.", "Low.",
                               "KltChnl.", "DonchCh", "HullMA")):
        return "价格"
    if fld == "volume" or fld.startswith("average_volume_"):
        return "成交量"
    if fld.startswith("MACD."):
        return "MACD"
    if fld.startswith("Stoch."):
        return "随机指标"
    return None


def _check_comparable(a: tuple, b: tuple) -> None:
    """字段对字段:单位不同 / 单位未知 → 拒绝,并尽量给出"你是不是想写…"。"""
    ua, ub = _unit(a[2]), _unit(b[2])
    if ua and ua == ub:
        return
    hint = ""
    # 最常见的手误:把「均量」写成「均线」
    if {ua, ub} == {"成交量", "价格"}:
        ma = a if ua == "价格" else b
        n = re.search(r"\d+", ma[3] or "")
        hint = (f"是不是想写「{n.group(0)}日均量」?" if n else "")
    raise ScreenError(
        f"「{a[3]}」和「{b[3]}」不能直接比较"
        f"({ua or '单位未知'} 对 {ub or '单位未知'})。{hint}")


def _find_op(text: str) -> tuple[str, str] | None:
    low = text.lower()
    best = None
    for w, op in _OP_WORDS:
        i = low.find(w)
        if i < 0:
            continue
        # 取最靠前的那个;同位置取更长的(_OP_WORDS 已按长度组织)
        if best is None or i < best[0]:
            best = (i, op, w)
    return (best[1], best[2]) if best else None


def _clause_to_expr(clause: str, vocab: _Vocab, notes: list[str] | None = None) -> str | None:
    """一小句 → 表达式。认不出来返回 None(**不猜**)。

    `notes` 收集"认出来了但有损"的说明(如上穿按"当前在上方"处理),
    最终会进返回体给用户看。
    """
    if notes is None:
        notes = []
    t = clause.strip()
    if not t:
        return None
    low = t.lower()

    # ── 成句的行话,先于通用规则 ──────────────────────────
    if re.search(r"多头排列|均线多头|多头趋势", t):
        for n in (20, 50, 200):
            if n not in vocab.sma:
                return None
        return "SMA20 > SMA50 and SMA50 > SMA200"

    # 距离/接近 52 周最高 X%
    # 数字必须从「最高/新高」**之后**找 —— 直接在整句上搜会先抓到「52周」的 52,
    # 「距离52周最高不到10%」被算成 52%(实测)。
    m = re.search(r"(最高|新高)", t)
    if m and re.search(r"距离?|接近|靠近|以内|之内|不到", t):
        mm = _NUM_RE.search(t[m.end():])
        if mm:
            pct = _parse_number(mm) / 100.0
            return ("(price_52_week_high - close) / price_52_week_high <= "
                    + _fmt(pct))

    fs = vocab.fields_in(t)

    # ── 本地处理不了的句型:直接拒绝,交给用户或 AI ─────────────
    #
    # 这些句型**认得出字段和比较符**,所以不拦的话会走到下面的通用分支,
    # 产出一个"少了一半意思"的条件 —— 比拒绝危险得多(2026-09-11 对抗测试实测):
    #   「收盘价大于20日均线的1.05倍」 → close > SMA20       (1.05 倍被静默丢掉)
    #   「市盈率大于10小于20」         → pe > 10             (上限被静默丢掉)
    # 用户看到的是一个看起来很正常的条件,完全不会意识到少了东西。
    if re.search(r"\d\s*倍|倍数|百分之", t):
        return None
    if len(re.findall(r"大于|小于|高于|低于|超过|不到|不低于|不高于|不超过|[<>]=?", t)) >= 2 \
            and len(fs) <= 1:
        return None                     # 一个字段两个比较 = 区间,本地不拆
    if re.search(r"之间|区间|介于|到\s*\d", t):
        return None

    # 「A比B高/低/大/小」—— 中文最常见的比较句式,比较符在句尾
    m = re.search(r"比.+?(高|低|大|小|多|少)\s*$", t)
    if m and len(fs) >= 2:
        _check_comparable(fs[0], fs[1])
        op = ">" if m.group(1) in ("高", "大", "多") else "<"
        return f"{fs[0][2]} {op} {fs[1][2]}"

    # ── 上穿 / 下穿 / 金叉 / 死叉 / 站上 / 跌破 ────────────────
    #
    # 两种句型,**按句中字段个数区分**,不能按关键词区分:
    #   一个字段  「站上50日均线」          主语省略 = 收盘价 vs 那条线
    #   两个字段  「5日均线上穿20日均线」    就是这两条线互相比
    #
    # 2026-09-10 修过一次同类 bug(「高于」被当成站上),当时只把「高于」挪出去,
    # 没有堵住句型本身 —— 于是「5日均线上穿20日均线」仍然被当成一个字段的句型,
    # 产出 `close > SMA5`,和用户说的毫无关系。这次按字段个数分流,整类封死。
    #
    # 快照没有历史,**判断不了"刚刚交叉"**,只能判"现在在上方/下方"。
    # 这是有损近似,必须写进 notes 让用户看见,不能静默当成金叉处理。
    up = re.search(r"站上|站稳|升破|突破|上穿|金叉", t)
    dn = re.search(r"跌破|下穿|失守|跌穿|死叉", t)
    if up or dn:
        word = (up or dn).group(0)
        op = ">" if up else "<"
        if len(fs) >= 2:
            _check_comparable(fs[0], fs[1])
            # 「股价突破52周新高」同样要 >=(见下方单字段分支的说明)
            if up and fs[1][2] in ("price_52_week_high", "all_time_high"):
                op = ">="
            if word in ("上穿", "下穿", "金叉", "死叉"):
                notes.append(f"「{t}」按「{fs[0][3]} 当前在 {fs[1][3]} "
                             f"{'之上' if up else '之下'}」处理 —— "
                             f"快照数据判断不了是不是**刚刚**发生交叉")
            return f"{fs[0][2]} {op} {fs[1][2]}"
        if len(fs) == 1 and _unit(fs[0][2]) == "价格" \
                and fs[0][2] not in ("close", "open", "high", "low"):
            tgt = fs[0][2]
            # 「突破 52 周新高」要用 >=:收盘价**等于**52 周最高正是创新高的那一天,
            # 写成 > 永远是 0 只 —— 一条静默失效的条件
            if up and tgt in ("price_52_week_high", "all_time_high"):
                op = ">="
            return f"close {op} {tgt}"
        return None

    # ── 通用:字段 + 比较符 + (字段 | 数字) ─────────────────
    op = _find_op(t)
    if not fs or not op:
        return None
    left = fs[0]
    if len(fs) >= 2:
        # 右边也是字段(「20日均线大于50日均线」)—— 必须同单位
        _check_comparable(left, fs[1])
        return f"{left[2]} {op[0]} {fs[1][2]}"

    tail = t[left[1]:]
    mnum = _NUM_RE.search(tail)
    if not mnum:
        return None
    # **标识符里的数字绝不能当阈值。**
    # 「收盘价大于MA20」在 MA20 还不认识的年代产出了 `close > 20` ——
    # 词表总有漏的(CCI20、MA(20) 的各种变体),漏认时宁可拒绝也别编一个阈值。
    a = left[1] + mnum.start()
    b = left[1] + mnum.end()
    before = t[a - 1] if a > 0 else ""
    after = t[b] if b < len(t) else ""
    if re.match(r"[A-Za-z(]", before) or re.match(r"[日天周线均]", after):
        return None
    return f"{left[2]} {op[0]} {_fmt(_parse_number(mnum))}"


def _split(text: str) -> list[str]:
    parts: list[str] = []
    for seg in _SPLIT_RE.split(text or ""):
        if not seg:
            continue
        for sub in _AND_RE.split(seg):
            sub = sub.strip(" 　的了呢吧啊·、")
            if sub:
                parts.append(sub)
    return parts


# ═══════════════════════════════════════════════════════════════
# 对外
# ═══════════════════════════════════════════════════════════════

def translate(text: str, has_field, sma: list[int], ema: list[int],
              rsi: list[int]) -> dict:
    """关键词匹配。→ {script, matched:[(原文, 表达式)]}

    有任何一段没认出来就抛 ScreenError(带上没认出来的原文),**不产出半份脚本**。
    """
    text = _normalize(text)
    if not text:
        raise ScreenError("生成框是空的")

    vocab = _Vocab(has_field, sma, ema, rsi)
    clauses = _split(text)
    if not clauses:
        raise ScreenError("没有可识别的内容")

    matched: list[tuple[str, str]] = []
    unmatched: list[str] = []
    notes: list[str] = []
    for c in clauses:
        expr = _clause_to_expr(c, vocab, notes)
        if expr:
            matched.append((c, expr))
        else:
            unmatched.append(c)

    if unmatched:
        raise ScreenError(
            "本地识别没看懂这几句:" + " / ".join(f"「{u}」" for u in unmatched[:4])
            + (f" 等 {len(unmatched)} 处" if len(unmatched) > 4 else ""))
    if not matched:
        raise ScreenError("没认出任何筛选条件")

    lines, names = [], []
    for i, (src, expr) in enumerate(matched, 1):
        # 名字用序号,不用中文 —— DSL 的标识符只允许英文
        name = f"cond_{i}"
        lines.append(f"def {name} = {expr};")
        names.append(name)
    lines.append("plot scan = " + " and ".join(names) + ";")
    return {"script": "\n".join(lines),
            "matched": [{"text": s, "expr": e} for s, e in matched],
            "notes": notes}


# ═══════════════════════════════════════════════════════════════
# 输入归一化 —— 在任何匹配之前做
# ═══════════════════════════════════════════════════════════════

# 中文数字 → 阿拉伯数字。只转**后面紧跟周期/单位**的,
# 「统一」「一致」这类词里的"一"不能碰。
_CN_DIG = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM_RE = re.compile(
    r"([零〇一二两三四五六七八九十百]+)(?=\s*(?:日|天|周|个交易日|年|月|倍))")
# 「涨幅超过三成」—— 成 = 10%
_CN_CHENG_RE = re.compile(r"([一二两三四五六七八九十]+)成")


def _cn2int(s: str) -> int:
    total = cur = 0
    for ch in s:
        if ch in _CN_DIG:
            cur = _CN_DIG[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def _normalize(text: str) -> str:
    """全角转半角 + 中文数字转阿拉伯数字。

    · NFKC:中文输入法打出来的 `＞` `２０` `ＲＳＩ` 全是全角,
      不归一的话一个比较符都认不出(2026-09-11 实测,三条全角用例全挂)。
    · 中文数字:「五日均线」「二十日均线」在中文里很常见。
    """
    import unicodedata
    t = unicodedata.normalize("NFKC", text or "").strip()
    t = _CN_NUM_RE.sub(lambda m: str(_cn2int(m.group(1))), t)
    t = _CN_CHENG_RE.sub(lambda m: str(_cn2int(m.group(1)) * 10) + "%", t)
    return t
