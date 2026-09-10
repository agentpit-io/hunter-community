"""筛选脚本 DSL —— thinkScript 子集 → TradingView scanner 字段。

用户拿来的是 thinkorswim 的 Stock Hacker 脚本(`def x = ...; plot scan = ...`)。
这里把那套语法解析成 AST,把函数调用映射成 TradingView 的字段名,
拉一次全市场数据,然后**在本地逐行求值**。

## 为什么本地求值,而不是翻译成 TradingView 的 filter

TradingView 的 `filter` 只支持「字段 op 常量/字段」,做不了算术。而 thinkScript
脚本里最常见的一类条件恰恰是算术:

    (high52 - close) / high52 <= 0.10        # 距 52 周高点 10% 以内

翻译不过去。而全市场一次拉全的代价实测很低(A 股 5237 只 · 205KB · 594ms),
拉回来自己算反而**又快又不受 filter 表达能力限制**。
`type=stock` / `is_primary` 这类纯常量条件仍然下推,那是为了把
ETF、优先股份额这些噪音在服务端就去掉(见 tv_screener 的 BASE_FILTER)。

## 缺数据的行怎么处理 —— 不猜,也不当成 False

任一操作数是 None,整个表达式求值结果就是 None,该行**不计入命中**,
同时计进 `skipped_incomplete`。返回体里会明说"有多少只因为缺字段没能参与判断"。

这是仓内铁律「空的比假的好」在这里的具体形态。反面写法是把 None 当 0 或当
False —— 那样 `close > 20` 会把所有没有报价的票判成"不满足",用户看到的是
一个**看起来完整、实际漏了几百只**的结果,而且完全无从察觉。

## 周期映射是近似的,必须说出来

TradingView 没有"任意窗口最高价"字段,只有 `price_52_week_high` / `High.3M`
这些固定窗口。`Highest(high, 252)` 只能映射到 52 周高点 —— 252 个交易日
和 52 个日历周不是同一个东西。这类近似一律写进返回体的 `notes`,不静默替换。

**映射不上的直接报错,不找"最接近的"顶上。** `Average(volume, 100)` 在
TradingView 只有 10/30/60/90 天均量,拿 90 天冒充 100 天就是在编数字。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field


class ScreenError(ValueError):
    """脚本写错了 —— message 直接给用户看,必须说清楚错在哪、能怎么改。"""


# ═══════════════════════════════════════════════════════════════
# 词法
# ═══════════════════════════════════════════════════════════════

# 标识符允许带点:TradingView 字段名本身就长这样(MACD.hist / High.All / Perf.Y)
_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>\#[^\n]*)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><=|>=|==|!=|<>|[<>+\-*/(),;=])
""", re.VERBOSE)

_KEYWORDS = {"def", "plot", "and", "or", "not", "true", "false"}


@dataclass
class _Tok:
    kind: str
    val: str
    pos: int


def _tokenize(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(src)
    while i < n:
        m = _TOKEN_RE.match(src, i)
        if not m:
            raise ScreenError(f"第 {_line_of(src, i)} 行:看不懂的字符 {src[i]!r}")
        i = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        val = m.group()
        if kind == "ident" and val.lower() in _KEYWORDS:
            toks.append(_Tok(val.lower(), val.lower(), m.start()))
        else:
            toks.append(_Tok(kind, val, m.start()))
    return toks


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


# ═══════════════════════════════════════════════════════════════
# 语法  ——  递归下降
#
#   program := stmt*
#   stmt    := ('def' | 'plot') IDENT '=' expr ';'
#   expr    := or_ ;  or_ := and_ ('or' and_)* ;  and_ := not_ ('and' not_)*
#   not_    := 'not' not_ | cmp
#   cmp     := add (('>'|'>='|'<'|'<='|'=='|'!=') add)?
#   add     := mul (('+'|'-') mul)* ;  mul := unary (('*'|'/') unary)*
#   unary   := '-' unary | primary
#   primary := NUM | 'true' | 'false' | IDENT | IDENT '(' args ')' | '(' expr ')'
# ═══════════════════════════════════════════════════════════════

class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = _tokenize(src)
        self.i = 0

    # ── 基础动作 ────────────────────────────────────────────
    def _peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> _Tok | None:
        t = self._peek()
        if t is not None:
            self.i += 1
        return t

    def _at(self, *kinds: str) -> bool:
        t = self._peek()
        return t is not None and t.kind in kinds

    def _at_op(self, *vals: str) -> bool:
        t = self._peek()
        return t is not None and t.kind == "op" and t.val in vals

    def _expect_op(self, val: str, what: str) -> _Tok:
        t = self._peek()
        if t is None:
            raise ScreenError(f"脚本在该出现 {val!r} 的地方结束了({what})")
        if t.kind != "op" or t.val != val:
            raise ScreenError(
                f"第 {_line_of(self.src, t.pos)} 行:这里应该是 {val!r},"
                f"实际是 {t.val!r}({what})")
        return self._next()

    # ── 程序 ────────────────────────────────────────────────
    def parse(self) -> tuple[list[tuple[str, object]], str]:
        """→ ([(名字, AST), ...], 最终 plot 的名字)"""
        stmts: list[tuple[str, object]] = []
        plot_name: str | None = None
        seen: set[str] = set()
        while self._peek() is not None:
            t = self._peek()
            if t.kind not in ("def", "plot"):
                raise ScreenError(
                    f"第 {_line_of(self.src, t.pos)} 行:每一句都要以 def 或 plot 开头,"
                    f"这里是 {t.val!r}。"
                    f"写法:def 名字 = 表达式;   最后一句 plot scan = 综合条件;")
            kind = self._next().kind
            nt = self._peek()
            if nt is None or nt.kind != "ident":
                raise ScreenError(f"{kind} 后面要跟一个名字,例如 `{kind} cond_price = close > 20;`")
            name = self._next().val
            if name in seen:
                raise ScreenError(f"名字 {name!r} 定义了两次")
            seen.add(name)
            self._expect_op("=", f"{kind} {name}")
            node = self._expr()
            # 分号:thinkScript 要求,但最后一句漏写很常见,容忍脚本结尾那一处
            if self._at_op(";"):
                self._next()
            elif self._peek() is not None:
                t2 = self._peek()
                raise ScreenError(
                    f"第 {_line_of(self.src, t2.pos)} 行:{name} 这一句缺分号 `;`")
            stmts.append((name, node))
            if kind == "plot":
                plot_name = name
        if not stmts:
            raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
        if plot_name is None:
            raise ScreenError(
                "脚本里没有 plot 语句 —— 少了最终的筛选条件。"
                "最后加一句,例如:plot scan = cond_price and cond_volume;")
        return stmts, plot_name

    # ── 表达式 ──────────────────────────────────────────────
    def _expr(self):
        return self._or()

    def _or(self):
        node = self._and()
        while self._at("or"):
            self._next()
            node = ("bin", "or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._at("and"):
            self._next()
            node = ("bin", "and", node, self._not())
        return node

    def _not(self):
        if self._at("not"):
            self._next()
            return ("un", "not", self._not())
        return self._cmp()

    def _cmp(self):
        node = self._add()
        if self._at_op("<", ">", "<=", ">=", "==", "!=", "<>"):
            op = self._next().val
            if op == "<>":
                op = "!="
            return ("bin", op, node, self._add())
        if self._at_op("="):
            t = self._peek()
            raise ScreenError(
                f"第 {_line_of(self.src, t.pos)} 行:比较相等要写 `==`,单个 `=` 是赋值")
        return node

    def _add(self):
        node = self._mul()
        while self._at_op("+", "-"):
            op = self._next().val
            node = ("bin", op, node, self._mul())
        return node

    def _mul(self):
        node = self._unary()
        while self._at_op("*", "/"):
            op = self._next().val
            node = ("bin", op, node, self._unary())
        return node

    def _unary(self):
        if self._at_op("-"):
            self._next()
            return ("un", "neg", self._unary())
        if self._at_op("+"):
            self._next()
            return self._unary()
        return self._primary()

    def _primary(self):
        t = self._peek()
        if t is None:
            raise ScreenError("表达式在这里断掉了(可能是括号没配对,或者比较号后面没写东西)")
        if t.kind == "num":
            self._next()
            return ("num", float(t.val))
        if t.kind in ("true", "false"):
            self._next()
            return ("bool", t.kind == "true")
        if t.kind == "op" and t.val == "(":
            self._next()
            node = self._expr()
            self._expect_op(")", "括号")
            return node
        if t.kind == "ident":
            self._next()
            if self._at_op("("):
                self._next()
                args = []
                if not self._at_op(")"):
                    args.append(self._expr())
                    while self._at_op(","):
                        self._next()
                        args.append(self._expr())
                self._expect_op(")", f"函数 {t.val}")
                return ("call", t.val, args)
            return ("name", t.val)
        raise ScreenError(f"第 {_line_of(self.src, t.pos)} 行:这里不该出现 {t.val!r}")


# ═══════════════════════════════════════════════════════════════
# 函数 → TradingView 字段
# ═══════════════════════════════════════════════════════════════

# thinkScript 的价格序列名 → TradingView 当日字段
_PRICE = {"close": "close", "open": "open", "high": "high", "low": "low",
          "volume": "volume", "hlc3": None, "ohlc4": None}

# Highest/Lowest 的窗口:交易日数 → TradingView 固定窗口字段。
# 键是**允许的交易日区间**(闭区间),因为 252/250/251 说的都是"一年"。
_HIGH_WINDOWS = [
    ((4, 6),     "High.5D",            "近 5 日最高"),
    ((19, 23),   "High.1M",            "近 1 月最高"),
    ((60, 65),   "High.3M",            "近 3 月最高"),
    ((123, 128), "High.6M",            "近 6 月最高"),
    ((248, 254), "price_52_week_high", "52 周最高"),
]
_LOW_WINDOWS = [
    ((4, 6),     "Low.5D",            "近 5 日最低"),
    ((19, 23),   "Low.1M",            "近 1 月最低"),
    ((60, 65),   "Low.3M",            "近 3 月最低"),
    ((123, 128), "Low.6M",            "近 6 月最低"),
    ((248, 254), "price_52_week_low", "52 周最低"),
]

# Average(volume, N) 能映射的天数 —— TradingView 只有这四个
_VOL_AVG_DAYS = (10, 30, 60, 90)


def _int_arg(node, fn: str, idx: int) -> int:
    if not (isinstance(node, tuple) and node[0] == "num"):
        raise ScreenError(f"{fn}() 的第 {idx} 个参数必须是数字常量,不能是表达式或字段")
    v = node[1]
    if abs(v - round(v)) > 1e-9:
        raise ScreenError(f"{fn}() 的周期必须是整数,收到 {v}")
    return int(round(v))


def _price_arg(node, fn: str) -> str:
    if not (isinstance(node, tuple) and node[0] == "name"):
        raise ScreenError(f"{fn}() 的第 1 个参数要写 close / high / low / volume 之一")
    nm = node[1].lower()
    if nm not in _PRICE or _PRICE[nm] is None:
        raise ScreenError(
            f"{fn}() 第 1 个参数只支持 close / open / high / low / volume,收到 {node[1]!r}")
    return nm


class _FieldResolver:
    """把 AST 里的名字和函数调用解析成 TradingView 字段名。

    `has_field` 由 tv_screener 注入(来自 metainfo 实拉),所以周期支持范围
    是**跟着 TradingView 走**的,不用在代码里维护一份会过期的白名单。
    """

    def __init__(self, has_field, sma_periods: list[int], ema_periods: list[int],
                 rsi_periods: list[int]):
        self.has_field = has_field
        self.sma_periods = sma_periods
        self.ema_periods = ema_periods
        self.rsi_periods = rsi_periods
        self.notes: list[str] = []

    def _note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)

    # ── 裸名字 ──────────────────────────────────────────────
    def field_of_name(self, name: str) -> str:
        low = name.lower()
        if low in _PRICE and _PRICE[low]:
            return _PRICE[low]
        # 直接写 TradingView 字段名也放行 —— 3777 个字段,不可能都包成函数
        if self.has_field(name):
            return name
        raise ScreenError(
            f"不认识 {name!r}。它既不是 close/open/high/low/volume,"
            f"也不是 TradingView 的字段名。"
            f"如果想用自定义变量,要先 `def {name} = ...;` 定义。")

    # ── 函数 ────────────────────────────────────────────────
    def field_of_call(self, fn: str, args: list) -> str:
        f = fn.lower()

        if f in ("average", "simplemovingavg", "movavg", "sma"):
            if len(args) != 2:
                raise ScreenError(f"{fn}(序列, 周期) 需要 2 个参数,收到 {len(args)} 个")
            src = _price_arg(args[0], fn)
            n = _int_arg(args[1], fn, 2)
            if src == "volume":
                if n not in _VOL_AVG_DAYS:
                    raise ScreenError(
                        f"{fn}(volume, {n}) 映射不了 —— TradingView 只提供 "
                        f"{'/'.join(map(str, _VOL_AVG_DAYS))} 天的均量字段。"
                        f"请把周期改成这四个之一。"
                        f"(不拿 90 天冒充 {n} 天:那是在编数字)")
                return f"average_volume_{n}d_calc"
            if src != "close":
                raise ScreenError(
                    f"{fn}({src}, {n}):TradingView 的均线只基于收盘价,"
                    f"没有 {src} 的均线字段")
            if n not in self.sma_periods:
                raise ScreenError(
                    f"{fn}(close, {n}) 映射不了 —— TradingView 没有 SMA{n}。"
                    f"可用周期:{', '.join(map(str, self.sma_periods))}")
            return f"SMA{n}"

        if f in ("expaverage", "ema", "movingaverage"):
            if len(args) != 2:
                raise ScreenError(f"{fn}(序列, 周期) 需要 2 个参数")
            src = _price_arg(args[0], fn)
            n = _int_arg(args[1], fn, 2)
            if src != "close":
                raise ScreenError(f"{fn} 只支持 close")
            if n not in self.ema_periods:
                raise ScreenError(
                    f"{fn}(close, {n}) 映射不了 —— 可用周期:"
                    f"{', '.join(map(str, self.ema_periods))}")
            return f"EMA{n}"

        if f in ("highest", "lowest"):
            if len(args) != 2:
                raise ScreenError(f"{fn}(序列, 周期) 需要 2 个参数")
            src = _price_arg(args[0], fn)
            n = _int_arg(args[1], fn, 2)
            table = _HIGH_WINDOWS if f == "highest" else _LOW_WINDOWS
            want = "high" if f == "highest" else "low"
            if src != want:
                raise ScreenError(f"{fn}() 的第 1 个参数应该是 {want}")
            for (lo, hi), fld, label in table:
                if lo <= n <= hi:
                    if not self.has_field(fld):
                        raise ScreenError(f"这个市场没有 {fld} 字段")
                    if n not in (5, 21, 63, 126, 252):
                        self._note(
                            f"{fn}({src}, {n}) → {fld}({label})· "
                            f"TradingView 只有固定窗口,交易日数与日历窗口存在口径差异")
                    else:
                        self._note(f"{fn}({src}, {n}) → {fld}({label})")
                    return fld
            raise ScreenError(
                f"{fn}({src}, {n}) 映射不了 —— TradingView 没有任意窗口的最高/最低价,"
                f"只有 5 日 / 1 月(≈21) / 3 月(≈63) / 6 月(≈126) / 52 周(≈252)。"
                f"请把周期改成接近这几个的值。")

        if f == "rsi":
            if len(args) == 0:
                return "RSI"
            n = _int_arg(args[0], fn, 1)
            if n == 14:
                self._note("RSI(14) → RSI(TradingView 的默认 RSI 就是 14 周期)")
                return "RSI"
            if n not in self.rsi_periods:
                raise ScreenError(
                    f"RSI({n}) 映射不了 —— 可用周期:14(写 RSI() 即可)、"
                    f"{', '.join(map(str, self.rsi_periods))}")
            return f"RSI{n}"

        raise ScreenError(
            f"不支持的函数 {fn}()。"
            f"目前支持:Average(close|volume, N) · ExpAverage(close, N) · "
            f"Highest(high, N) · Lowest(low, N) · RSI(N)。"
            f"其它指标可以直接写 TradingView 字段名,例如 MACD.hist、ADX、Perf.Y、"
            f"market_cap_basic —— 在「可用字段」里搜。")


# ═══════════════════════════════════════════════════════════════
# 编译 + 求值
# ═══════════════════════════════════════════════════════════════

@dataclass
class Compiled:
    stmts: list[tuple[str, object]]
    plot_name: str
    fields: list[str]                     # 需要向 TradingView 请求的字段
    notes: list[str] = _dc_field(default_factory=list)


def compile_script(src: str, has_field, sma_periods: list[int],
                   ema_periods: list[int], rsi_periods: list[int]) -> Compiled:
    """解析脚本 + 解析出需要哪些 TradingView 字段。不发网络请求。"""
    stmts, plot_name = _Parser(src).parse()
    rs = _FieldResolver(has_field, sma_periods, ema_periods, rsi_periods)
    fields: list[str] = []
    defined: set[str] = set()

    def walk(node, owner: str):
        if not isinstance(node, tuple):
            return
        k = node[0]
        if k in ("num", "bool"):
            return
        if k == "name":
            if node[1] in defined:
                return
            fld = rs.field_of_name(node[1])
            if fld not in fields:
                fields.append(fld)
            return
        if k == "call":
            for a in node[2]:
                # 参数里的 close/volume 是"序列名",不是要请求的字段,别递归下去
                if isinstance(a, tuple) and a[0] == "name" and a[1].lower() in _PRICE:
                    continue
                walk(a, owner)
            fld = rs.field_of_call(node[1], node[2])
            if fld not in fields:
                fields.append(fld)
            return
        if k == "un":
            walk(node[2], owner)
            return
        if k == "bin":
            walk(node[2], owner)
            walk(node[3], owner)
            return

    for name, node in stmts:
        walk(node, name)
        defined.add(name)

    return Compiled(stmts=stmts, plot_name=plot_name, fields=fields, notes=rs.notes)


# ── 求值 · None 一路传播 ────────────────────────────────────

def _truthy(v):
    """→ True / False / None。数字按 thinkScript 惯例 !=0 为真。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return v != 0


def _eval(node, row: dict, env: dict, resolver_cache: dict):
    k = node[0]
    if k == "num":
        return node[1]
    if k == "bool":
        return node[1]
    if k == "name":
        nm = node[1]
        if nm in env:
            return env[nm]
        return row.get(resolver_cache[("name", nm)])
    if k == "call":
        return row.get(resolver_cache[("call", id(node))])
    if k == "un":
        v = _eval(node[2], row, env, resolver_cache)
        if node[1] == "neg":
            return None if v is None else -v
        t = _truthy(v)
        return None if t is None else (not t)
    # bin
    op, ln, rn = node[1], node[2], node[3]
    # and / or 不做短路:短路会让 None 的传播依赖左右顺序,
    # 同一个脚本换个写法结果不同,排查起来极难。宁可都算。
    lv = _eval(ln, row, env, resolver_cache)
    rv = _eval(rn, row, env, resolver_cache)
    if op == "and":
        a, b = _truthy(lv), _truthy(rv)
        return None if (a is None or b is None) else (a and b)
    if op == "or":
        a, b = _truthy(lv), _truthy(rv)
        return None if (a is None or b is None) else (a or b)
    if lv is None or rv is None:
        return None
    if op == "+":
        return lv + rv
    if op == "-":
        return lv - rv
    if op == "*":
        return lv * rv
    if op == "/":
        return None if rv == 0 else lv / rv       # 除零不是 0,也不是错误,是"算不出"
    if op == ">":
        return lv > rv
    if op == ">=":
        return lv >= rv
    if op == "<":
        return lv < rv
    if op == "<=":
        return lv <= rv
    if op == "==":
        return lv == rv
    if op == "!=":
        return lv != rv
    raise ScreenError(f"内部错误:未知运算符 {op}")


def build_resolver_cache(c: Compiled, has_field, sma_periods, ema_periods,
                         rsi_periods) -> dict:
    """把每个 name/call 节点预先解析成字段名,避免逐行重复解析。"""
    rs = _FieldResolver(has_field, sma_periods, ema_periods, rsi_periods)
    cache: dict = {}
    defined: set[str] = set()

    def walk(node):
        if not isinstance(node, tuple):
            return
        k = node[0]
        if k == "name":
            if node[1] not in defined:
                cache[("name", node[1])] = rs.field_of_name(node[1])
            return
        if k == "call":
            cache[("call", id(node))] = rs.field_of_call(node[1], node[2])
            return
        if k == "un":
            walk(node[2])
        elif k == "bin":
            walk(node[2]); walk(node[3])

    for name, node in c.stmts:
        walk(node)
        defined.add(name)
    return cache


def evaluate(c: Compiled, rows: list[dict], resolver_cache: dict) -> tuple[list[dict], int]:
    """→ (命中的行, 因缺字段无法判断的行数)"""
    hits: list[dict] = []
    skipped = 0
    for row in rows:
        env: dict = {}
        for name, node in c.stmts:
            env[name] = _eval(node, row, env, resolver_cache)
        verdict = _truthy(env.get(c.plot_name))
        if verdict is None:
            skipped += 1
        elif verdict:
            hits.append(row)
    return hits, skipped
