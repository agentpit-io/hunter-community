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

_MA_RE = re.compile(r"(\d+)\s*(?:日|天)?\s*(?:均线|线|ma|sma|移动平均)", re.I)
_EMA_RE = re.compile(r"(\d+)\s*(?:日|天)?\s*ema", re.I)
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
            n = int(m.group(1))
            err = "" if n in self.ema else f"没有 {n} 日 EMA"
            out.append((m.start(), -len(m.group(0)), f"EMA{n}", m.group(0), err))
        for m in _RSI_N_RE.finditer(text):
            n = int(m.group(1))
            fld = "RSI" if n == 14 else f"RSI{n}"
            err = "" if (n == 14 or n in self.rsi) else f"没有 RSI({n})"
            out.append((m.start(), -len(m.group(0)), fld, m.group(0), err))
        for m in _MA_RE.finditer(text):
            n = int(m.group(1))
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


def _clause_to_expr(clause: str, vocab: _Vocab) -> str | None:
    """一小句 → 表达式。认不出来返回 None(**不猜**)。"""
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

    # 站上 / 跌破 均线 —— **只收成句的行话**。
    #
    # 「高于」「低于」这类通用比较符**绝不能**放进来:它们两边是什么由句子决定,
    # 不一定是"收盘价 vs 均线"。2026-09-10 实测,「50日均线高于150日均线」
    # 因为 `高于` 在这张表里,整句被这条模板吞掉,产出 `收盘价 大于 50日均线` ——
    # 和用户想要的完全是两回事。通用比较符一律走下面的三段式。
    #
    # 另外这里必须用 ma_field 而不是 field:「收盘价站上50日均线」里
    # `收盘价` 出现得更靠左,field() 会优先返回它。
    if re.search(r"(站上|站稳|升破|突破|上穿)", t) and re.search(r"均线|ma|ema|线", low):
        ma = vocab.ma_field(t)
        if ma:
            return f"close > {ma}"
    if re.search(r"(跌破|下穿|失守|跌穿)", t) and re.search(r"均线|ma|ema|线", low):
        ma = vocab.ma_field(t)
        if ma:
            return f"close < {ma}"

    # ── 通用三段式:字段 + 比较符 + 数字 ─────────────────
    got = vocab.field(t)
    op = _find_op(t)
    if not got or not op:
        return None
    fld, word = got
    # 数字要取**字段词之后**的那个,否则「50日均线大于20」会把 50 当阈值
    tail_start = t.lower().find(word.lower())
    tail = t[tail_start + len(word):] if tail_start >= 0 else t
    # **先试字段,再试数字。** 反过来的话「20日均线大于50日均线」的尾巴
    # 「大于50日均线」会先被 _NUM_RE 抓到 50,产出 `SMA20 > 50` —— 阈值和均线
    # 完全是两回事,而且看起来还挺像对的(实测踩到)。
    got2 = vocab.field(tail)
    if got2:
        return f"{fld} {op[0]} {got2[0]}"
    mnum = _NUM_RE.search(tail)
    if not mnum:
        return None
    return f"{fld} {op[0]} {_fmt(_parse_number(mnum))}"


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
    text = (text or "").strip()
    if not text:
        raise ScreenError("生成框是空的")

    vocab = _Vocab(has_field, sma, ema, rsi)
    clauses = _split(text)
    if not clauses:
        raise ScreenError("没有可识别的内容")

    matched: list[tuple[str, str]] = []
    unmatched: list[str] = []
    for c in clauses:
        expr = _clause_to_expr(c, vocab)
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
            "matched": [{"text": s, "expr": e} for s, e in matched]}
