"""筛选脚本 DSL —— thinkScript 子集 → 扫描源字段。

用户拿来的是 thinkorswim 的 Stock Hacker 脚本(`def x = ...; plot scan = ...`)。
这里把那套语法解析成 AST,把函数调用映射成扫描源的字段名,
拉一次全市场数据,然后**在本地逐行求值**。

## 为什么本地求值,而不是翻译成上游的 filter

上游的 `filter` 只支持「字段 op 常量/字段」,做不了算术。而 thinkScript
脚本里最常见的一类条件恰恰是算术:

    (high52 - close) / high52 <= 0.10        # 距 52 周高点 10% 以内

翻译不过去。而全市场一次拉全的代价实测很低(A 股 5237 只 · 205KB · 594ms),
拉回来自己算反而**又快又不受 filter 表达能力限制**。
`type=stock` / `is_primary` 这类纯常量条件仍然下推,那是为了把
ETF、优先股份额这些噪音在服务端就去掉(见 screen_source 的 BASE_FILTER)。

## 缺数据的行怎么处理 —— 不猜,也不当成 False

任一操作数是 None,整个表达式求值结果就是 None,该行**不计入命中**,
同时计进 `skipped_incomplete`。返回体里会明说"有多少只因为缺字段没能参与判断"。

这是仓内铁律「空的比假的好」在这里的具体形态。反面写法是把 None 当 0 或当
False —— 那样 `close > 20` 会把所有没有报价的票判成"不满足",用户看到的是
一个**看起来完整、实际漏了几百只**的结果,而且完全无从察觉。

## 周期映射是近似的,必须说出来

扫描源没有"任意窗口最高价"字段,只有 `price_52_week_high` / `High.3M`
这些固定窗口。`Highest(high, 252)` 只能映射到 52 周高点 —— 252 个交易日
和 52 个日历周不是同一个东西。这类近似一律写进返回体的 `notes`,不静默替换。

**映射不上的直接报错,不找"最接近的"顶上。** `Average(volume, 100)` 在
扫描源只有 10/30/60/90 天均量,拿 90 天冒充 100 天就是在编数字。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field


class ScreenError(ValueError):
    """脚本写错了 —— message 直接给用户看,必须说清楚错在哪、能怎么改。"""


@dataclass
class Stmt:
    """一条 `def x = ...;` 或 `plot scan = ...;`。

    `expr_start` / `expr_end` 是**表达式**在源码里的字节跨度(不含 `def x =` 和分号)。
    可视化条件行靠它拿到"这一条的原文",数字的内联编辑靠 num 节点自带的位置。
    """
    name: str
    node: object
    kind: str                 # 'def' | 'plot'
    expr_start: int = 0
    expr_end: int = 0


# ═══════════════════════════════════════════════════════════════
# 词法
# ═══════════════════════════════════════════════════════════════

# 标识符允许带点:上游字段名本身就长这样(MACD.hist / High.All / Perf.Y)
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
    def parse(self) -> tuple[list["Stmt"], str]:
        """→ ([Stmt, ...], 最终 plot 的名字)"""
        stmts: list[Stmt] = []
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
            # 记下表达式在源码里的跨度 —— 可视化条件行要拿它当"这一条的原文"
            expr_start = self._peek().pos if self._peek() is not None else 0
            node = self._expr()
            last = self.toks[self.i - 1] if self.i > 0 else None
            expr_end = (last.pos + len(last.val)) if last is not None else expr_start
            # 分号:thinkScript 要求,但最后一句漏写很常见,容忍脚本结尾那一处
            if self._at_op(";"):
                self._next()
            elif self._peek() is not None:
                t2 = self._peek()
                raise ScreenError(
                    f"第 {_line_of(self.src, t2.pos)} 行:{name} 这一句缺分号 `;`")
            stmts.append(Stmt(name=name, node=node, kind=kind,
                              expr_start=expr_start, expr_end=expr_end))
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
            # 带上源码里的起止位置 —— 前端要按位置把数字换掉做内联编辑
            # (按第 n 个数字做正则替换会在 `Average(close,50) > 50` 这种表达式上认错人)
            return ("num", float(t.val), t.pos, t.pos + len(t.val))
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
# 函数 → 扫描源字段
# ═══════════════════════════════════════════════════════════════

# thinkScript 的价格序列名 → 扫描源当日字段
_PRICE = {"close": "close", "open": "open", "high": "high", "low": "low",
          "volume": "volume", "hlc3": None, "ohlc4": None}

# Highest/Lowest 的窗口:交易日数 → 扫描源固定窗口字段。
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

# Average(volume, N) 能映射的天数 —— 扫描源只有这四个
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
    """把 AST 里的名字和函数调用解析成扫描源字段名。

    `has_field` 由 screen_source 注入(来自 metainfo 实拉),所以周期支持范围
    是**跟着扫描源走**的,不用在代码里维护一份会过期的白名单。
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
        # 直接写扫描源字段名也放行 —— 3777 个字段,不可能都包成函数
        if self.has_field(name):
            return name
        raise ScreenError(
            f"不认识 {name!r}。它既不是 close/open/high/low/volume,"
            f"也不是扫描源的字段名。"
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
                        f"{fn}(volume, {n}) 映射不了 —— 扫描源只提供 "
                        f"{'/'.join(map(str, _VOL_AVG_DAYS))} 天的均量字段。"
                        f"请把周期改成这四个之一。"
                        f"(不拿 90 天冒充 {n} 天:那是在编数字)")
                return f"average_volume_{n}d_calc"
            if src != "close":
                raise ScreenError(
                    f"{fn}({src}, {n}):扫描源的均线只基于收盘价,"
                    f"没有 {src} 的均线字段")
            if n not in self.sma_periods:
                raise ScreenError(
                    f"{fn}(close, {n}) 映射不了 —— 扫描源没有 SMA{n}。"
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
                            f"扫描源只有固定窗口,交易日数与日历窗口存在口径差异")
                    else:
                        self._note(f"{fn}({src}, {n}) → {fld}({label})")
                    return fld
            raise ScreenError(
                f"{fn}({src}, {n}) 映射不了 —— 扫描源没有任意窗口的最高/最低价,"
                f"只有 5 日 / 1 月(≈21) / 3 月(≈63) / 6 月(≈126) / 52 周(≈252)。"
                f"请把周期改成接近这几个的值。")

        if f == "rsi":
            if len(args) == 0:
                return "RSI"
            n = _int_arg(args[0], fn, 1)
            if n == 14:
                self._note("RSI(14) → RSI(扫描源的默认 RSI 就是 14 周期)")
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
            f"其它指标可以直接写扫描源字段名,例如 MACD.hist、ADX、Perf.Y、"
            f"market_cap_basic —— 在「可用字段」里搜。")


# ═══════════════════════════════════════════════════════════════
# 编译 + 求值
# ═══════════════════════════════════════════════════════════════

@dataclass
class Compiled:
    stmts: list[Stmt]
    plot_name: str
    fields: list[str]                     # 需要向扫描源请求的字段
    notes: list[str] = _dc_field(default_factory=list)


def compile_script(src: str, has_field, sma_periods: list[int],
                   ema_periods: list[int], rsi_periods: list[int]) -> Compiled:
    """解析脚本 + 解析出需要哪些扫描源字段。不发网络请求。"""
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

    for st in stmts:
        walk(st.node, st.name)
        defined.add(st.name)

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

    for st in c.stmts:
        walk(st.node)
        defined.add(st.name)
    return cache


def evaluate(c: Compiled, rows: list[dict], resolver_cache: dict) -> tuple[list[dict], int]:
    """→ (命中的行, 因缺字段无法判断的行数)"""
    hits: list[dict] = []
    skipped = 0
    for row in rows:
        env: dict = {}
        for st in c.stmts:
            env[st.name] = _eval(st.node, row, env, resolver_cache)
        verdict = _truthy(env.get(c.plot_name))
        if verdict is None:
            skipped += 1
        elif verdict:
            hits.append(row)
    return hits, skipped


# ═══════════════════════════════════════════════════════════════
# 可视化条件行 —— 脚本 ↔ 界面的双向桥
#
# Chartink 那套界面的核心是「一行 = 一个条件」,可以逐条编辑 / 停用 / 删除。
# 我们的 DSL 天然就是这个形状:**一个 `def` 就是一个条件**,
# `plot scan = a and b and c;` 就是"同时满足以下全部条件"。
# 所以不需要另造一套数据模型 —— 脚本本身就是模型,这里只做展示层翻译。
#
# 为什么条件行要带 `expr`(原文)而不只是 tokens:
# tokens 是给人看的中文,回写不了。改数字靠 num token 自带的 s/e 偏移
# 在 `expr` 上做精确替换 —— 按"第 n 个数字"做正则替换会在
# `Average(close,50) > 50` 这种表达式上认错人。
# ═══════════════════════════════════════════════════════════════

# 字段 → 中文标签。查不到的原样显示(3777 个字段不可能全翻,
# 也不该硬翻 —— 翻错比不翻更糟)。
_FIELD_LABEL = {
    "close": "收盘价", "open": "开盘价", "high": "最高价", "low": "最低价",
    "volume": "成交量", "change": "涨跌幅",
    "price_52_week_high": "52周最高", "price_52_week_low": "52周最低",
    "High.5D": "近5日最高", "High.1M": "近1月最高", "High.3M": "近3月最高",
    "High.6M": "近6月最高", "all_time_high": "历史最高",
    "Low.5D": "近5日最低", "Low.1M": "近1月最低", "Low.3M": "近3月最低",
    "Low.6M": "近6月最低", "all_time_low": "历史最低",
    "market_cap_basic": "市值", "price_earnings_ttm": "市盈率TTM",
    "price_book_fq": "市净率", "return_on_equity": "净资产收益率",
    "dividends_yield_current": "股息率", "debt_to_equity": "负债权益比",
    "gross_margin_ttm": "毛利率TTM", "total_revenue_yoy_growth_ttm": "营收同比增长",
    "relative_volume_10d_calc": "相对成交量", "current_ratio": "流动比率",
    "earnings_per_share_diluted_ttm": "每股收益TTM", "beta_1_year": "贝塔",
    "RSI": "RSI(14)", "ADX": "ADX", "ATR": "ATR",
    "MACD.macd": "MACD", "MACD.signal": "MACD信号线", "MACD.hist": "MACD柱",
    "Perf.W": "近1周涨幅", "Perf.1M": "近1月涨幅", "Perf.3M": "近3月涨幅",
    "Perf.6M": "近6月涨幅", "Perf.Y": "近1年涨幅", "Perf.YTD": "年初至今涨幅",
    "Volatility.D": "日波动率", "Volatility.W": "周波动率", "Volatility.M": "月波动率",
    "sector": "板块", "industry": "行业", "currency": "币种",

    # 固定搭配 —— 这些**不能**靠词素拼,必须逐条给准确译名。
    # (price_to_book 拼出来是「价格账面」,free_cash_flow 是「自由现金流量」,
    #  price_target_high 是「价格目标价最高价」—— 都不是中文里的说法。)
    "free_cash_flow": "自由现金流", "free_cash_flow_ttm": "自由现金流TTM",
    "operating_cash_flow_ttm": "经营现金流TTM",
    "price_target_high": "目标价上限", "price_target_low": "目标价下限",
    "price_target_average": "目标价均值", "price_target_median": "目标价中位",
    "price_book_ratio": "市净率", "price_sales_ratio": "市销率",
    "price_free_cash_flow_ttm": "市现率TTM",
    "price_earnings_growth_ttm": "PEG(TTM)",
    "enterprise_value_ebitda_ttm": "EV/EBITDA(TTM)",
    "enterprise_value_current": "企业价值",
    "gross_margin": "毛利率", "operating_margin": "营业利润率",
    "net_margin": "净利率", "pre_tax_margin": "税前利润率",
    "after_tax_margin": "税后利润率",
    "dividend_payout_ratio_ttm": "股息支付率TTM",
    "dividends_per_share_fq": "每股股息(最近季)",
    "total_shares_outstanding_current": "总股本",
    "float_shares_outstanding": "流通股本",
    "number_of_employees": "员工人数",
    "relative_volume_10d_calc": "10日相对成交量",
    "Value.Traded": "成交额", "Volatility.D": "日波动率",
}

_SMA_RE = re.compile(r"^SMA(\d+)$")
_EMA_RE = re.compile(r"^EMA(\d+)$")
_RSI_RE = re.compile(r"^RSI(\d+)$")
_AVGVOL_RE = re.compile(r"^average_volume_(\d+)d_calc$")

_OP_LABEL = {
    ">": "大于", ">=": "大于等于", "<": "小于", "<=": "小于等于",
    "==": "等于", "!=": "不等于",
    "and": "且", "or": "或",
    "+": "+", "-": "−", "*": "×", "/": "÷",
}


def field_label(name: str) -> str:
    """扫描源字段名 → 中文标签(查不到就原样返回)。"""
    if name in _FIELD_LABEL:
        return _FIELD_LABEL[name]
    m = _SMA_RE.match(name)
    if m:
        return f"{m.group(1)}日均线"
    m = _EMA_RE.match(name)
    if m:
        return f"{m.group(1)}日EMA"
    m = _RSI_RE.match(name)
    if m:
        return f"RSI({m.group(1)})"
    m = _AVGVOL_RE.match(name)
    if m:
        return f"{m.group(1)}日均量"
    return name


def _fmt_num(v: float) -> str:
    """去掉浮点尾巴 —— 用户写的 0.10 不该显示成 0.1 的同时又变 0.10000000001。"""
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(round(v, 10)).rstrip("0").rstrip(".")


# 二元运算优先级 —— 只用来决定要不要补括号
_PREC = {"or": 1, "and": 2, "==": 3, "!=": 3, ">": 3, ">=": 3, "<": 3, "<=": 3,
         "+": 4, "-": 4, "*": 5, "/": 5}


def _tok_stream(node, rs, base: int, out: list, defined: dict, parent_prec: int = 0):
    """AST → 展示 token。`base` 是表达式在源码里的起点,用来把数字位置归一化。"""
    k = node[0]
    if k == "num":
        out.append({"k": "num", "t": _fmt_num(node[1]),
                    "s": node[2] - base, "e": node[3] - base})
        return
    if k == "bool":
        out.append({"k": "kw", "t": "真" if node[1] else "假"})
        return
    if k == "name":
        nm = node[1]
        if nm in defined:
            # 引用了上面某个 def —— 显示它的中文别名(条件行的标题)
            out.append({"k": "ref", "t": defined[nm], "ref": nm})
            return
        out.append({"k": "field", "t": field_label(rs.field_of_name(nm)), "raw": nm})
        return
    if k == "call":
        fld = rs.field_of_call(node[1], node[2])
        out.append({"k": "field", "t": field_label(fld), "raw": fld})
        return
    if k == "un":
        out.append({"k": "op", "t": "非" if node[1] == "not" else "−"})
        _tok_stream(node[2], rs, base, out, defined, 99)
        return
    # bin
    op = node[1]
    prec = _PREC.get(op, 0)
    need_paren = prec < parent_prec
    if need_paren:
        out.append({"k": "paren", "t": "("})
    _tok_stream(node[2], rs, base, out, defined, prec)
    out.append({"k": "logic" if op in ("and", "or") else "op",
                "t": _OP_LABEL.get(op, op)})
    # 右子树用 prec+1:同优先级的右结合要补括号(a - (b - c) 不能显示成 a - b - c)
    _tok_stream(node[3], rs, base, out, defined, prec + 1)
    if need_paren:
        out.append({"k": "paren", "t": ")"})


def decompose(src: str, c: Compiled, has_field, sma_periods, ema_periods,
              rsi_periods) -> dict:
    """把编译结果拆成可视化条件行。

    返回 conditions + combine:
      · combine='all'    plot 是若干 def 名字的纯 and 链 → "同时满足以下全部"
      · combine='custom' plot 里有 or / 算术 / 直接写的表达式 → 原样保留,界面上只读
    """
    rs = _FieldResolver(has_field, sma_periods, ema_periods, rsi_periods)
    defined: dict[str, str] = {}
    conditions = []
    plot_stmt = None

    for st in c.stmts:
        if st.kind == "plot":
            plot_stmt = st
            continue
        toks: list = []
        _tok_stream(st.node, rs, st.expr_start, toks, defined)
        expr = src[st.expr_start:st.expr_end]
        # 数字用**源码原文**显示,不用格式化后的值:用户写 0.10,界面上就该是 0.10。
        # 归一化成 0.1 之后再回写,会在他没改任何东西的情况下把脚本改掉。
        for t in toks:
            if t["k"] == "num":
                t["t"] = expr[t["s"]:t["e"]]
        # 条件行的中文标题:纯展示,给 plot 里引用它时用
        label = "".join(t["t"] for t in toks if t["k"] != "paren")
        defined[st.name] = label if len(label) <= 24 else st.name
        conditions.append({
            "name": st.name,
            "expr": expr,
            "tokens": toks,
            # 布尔条件才能独立开关;中间变量(如 def sma50 = Average(close,50))
            # 不是条件,界面上要区别对待 —— 停用它会让引用它的条件直接报错
            "is_bool": _is_boolean(st.node),
        })

    combine = "custom"
    plot_names: list[str] = []
    if plot_stmt is not None:
        plot_names = _flatten_and_names(plot_stmt.node)
        if plot_names:
            combine = "all"

    return {
        "conditions": conditions,
        "combine": combine,
        "plot_name": c.plot_name,
        "plot_expr": src[plot_stmt.expr_start:plot_stmt.expr_end] if plot_stmt else "",
        "plot_refs": plot_names,
        "notes": rs.notes,
    }


def _is_boolean(node) -> bool:
    """这条 def 产出的是真假值还是数字?决定界面上能不能单独停用。"""
    k = node[0]
    if k == "bool":
        return True
    if k == "un":
        return node[1] == "not"
    if k == "bin":
        return node[1] in ("and", "or", ">", ">=", "<", "<=", "==", "!=")
    return False


def _flatten_and_names(node) -> list[str]:
    """`a and b and c` → ['a','b','c']。只要出现别的东西就返回 [] (走 custom)。"""
    if node[0] == "name":
        return [node[1]]
    if node[0] == "bin" and node[1] == "and":
        left = _flatten_and_names(node[2])
        right = _flatten_and_names(node[3])
        if left and right:
            return left + right
    return []


def build_script(conditions: list[dict], plot_name: str = "scan",
                 plot_expr: str | None = None) -> str:
    """条件行 → 脚本。界面改完之后回写用。

    停用的条件**保留在脚本里**(仍然是 `def`),只是不进 plot ——
    这样用户重新启用时原文一字不差地回来。删除才是真删。
    """
    lines = []
    enabled = []
    for c in conditions:
        if not c.get("expr"):
            continue
        lines.append(f"def {c['name']} = {c['expr']};")
        if c.get("enabled", True) and c.get("is_bool"):
            enabled.append(c["name"])
    if plot_expr:
        lines.append(f"plot {plot_name} = {plot_expr};")
    elif enabled:
        lines.append(f"plot {plot_name} = " + " and ".join(enabled) + ";")
    else:
        # 一条都没启用 —— 不要生成 `plot scan = ;`(语法错),
        # 让调用方拿到一个能解析、但注定 0 命中的脚本,前端好给提示
        lines.append(f"plot {plot_name} = false;")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 字段中文名 —— 词素拼装
#
# 扫描源的字段名是高度组合化的:
#   average_volume_10d_calc = average + volume + 10d + calc
#   postmarket_volume       = postmarket + volume
#   total_revenue_yoy_growth_ttm = total + revenue + yoy + growth + ttm
# 所以不用逐个翻 3777 个,按词素拼既准又不用维护。
#
# **只在所有词素都认识时才拼**。有一个不认识就整体返回 None,界面上照旧显示
# 英文原名 —— 半吊子翻译(「盘后 volume」「average 成交量」)比不翻更误导,
# 而且会让人以为这个字段的含义已经被确认过了。
# ═══════════════════════════════════════════════════════════════

# 修饰词(可多个,按出现顺序拼在头词前面)
_MOR_MOD = {
    "average": "平均", "avg": "平均", "relative": "相对", "total": "总",
    "net": "净", "gross": "毛", "operating": "经营", "free": "自由",
    "premarket": "盘前", "postmarket": "盘后", "after": "盘后", "pre": "盘前",
    "basic": "基本", "diluted": "稀释", "forward": "预期", "fwd": "预期",
    "enterprise": "企业", "book": "账面",
    "continuing": "持续经营", "discontinued": "终止经营",
    "common": "普通", "preferred": "优先", "long": "长期", "short": "短期",
    "tangible": "有形", "intangible": "无形", "goodwill": "商誉",
    # 「每股」「人均」是修饰,要拼在头词**前面**(每股收益),
    # 当后缀会拼出「盈利(每股)」这种不像话的中文
    "share": "每股", "employee": "人均",
}

# 头词(必须至少命中一个,否则不拼)
_MOR_HEAD = {
    "volume": "成交量", "price": "价格", "close": "收盘价", "open": "开盘价",
    "high": "最高价", "low": "最低价", "change": "变动", "cap": "市值",
    "earnings": "盈利", "revenue": "营收", "income": "利润", "profit": "利润",
    "margin": "利润率", "yield": "收益率", "debt": "负债", "equity": "权益",
    "assets": "资产", "liabilities": "负债", "cash": "现金", "flow": "流量",
    "dividends": "股息", "dividend": "股息", "shares": "股本",
    "eps": "每股收益", "ebitda": "EBITDA", "ebit": "EBIT",
    "employees": "员工数", "sales": "销售额", "inventory": "存货",
    "receivables": "应收款", "payables": "应付款", "capex": "资本开支",
    "buyback": "回购", "float": "流通股", "beta": "贝塔",
    "volatility": "波动率", "turnover": "换手率", "growth": "增长",
    "ratio": "比率", "value": "价值", "rating": "评级", "target": "目标价",
    "gap": "跳空", "range": "区间", "performance": "涨幅", "perf": "涨幅",
}

# 后缀/限定(拼在括号里或直接接在后面)
_MOR_SUF = {
    "ttm": "TTM", "fq": "最近季", "fy": "最近年", "fh": "最近半年",
    "yoy": "同比", "qoq": "环比", "abs": "绝对值", "percent": "百分比",
    "pct": "百分比", "usd": "美元",
}

# 纯噪音,翻译时直接跳过(不影响"是否全部认识"的判定)
_MOR_SKIP = {"calc", "per", "the"}

_PERIOD_RE2 = re.compile(r"^(\d+)([dwmy])$")
_MOR_UNIT = {"d": "日", "w": "周", "m": "月", "y": "年"}


def field_label_cn(name: str) -> str | None:
    """字段名 → 中文标签。**拿不准就返回 None**(界面照旧显示英文)。"""
    if not name or not isinstance(name, str):
        return None
    exact = field_label(name)
    if exact != name:            # 精选表 / 技术指标模式已经认得
        return exact
    if not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        return None

    mods: list[str] = []
    periods: list[str] = []
    heads: list[str] = []
    sufs: list[str] = []
    toks = [t for t in re.split(r"[_.]", name.lower()) if t]
    for idx, tok in enumerate(toks):
        # `current` 位置不同意思不同,不能一概而论:
        #   total_current_liabilities → 流动负债(会计科目)
        #   dividends_yield_current   → 最新一期
        # 词中当「流动」、结尾当「最新」。一律译成「当前」会得到
        # 「总当前负债」这种既不是流动负债、也没人这么说的东西。
        if tok == "current":
            if idx == len(toks) - 1:
                sufs.append("最新")
            else:
                mods.append("流动")
            continue
        if tok in _MOR_SKIP:
            continue
        m = _PERIOD_RE2.match(tok)
        if m:
            periods.append(m.group(1) + _MOR_UNIT[m.group(2)])
            continue
        if tok in _MOR_MOD:
            mods.append(_MOR_MOD[tok]); continue
        if tok in _MOR_HEAD:
            heads.append(_MOR_HEAD[tok]); continue
        if tok in _MOR_SUF:
            sufs.append(_MOR_SUF[tok]); continue
        return None              # 有一个词素不认识 → 整体放弃
    if len(heads) != 1:
        # 0 个头词 = 没认出核心概念;**2 个以上 = 固定搭配**,不能逐词拼。
        # 实测反例:price_target_high 三个头词拼出「价格目标价最高价」,
        # price_free_cash_flow_current 拼出「自由当前价格现金流量」——
        # 每个词素都认识,拼起来却是胡话。认识词素 ≠ 拼得对。
        # 这类术语要么进上面的精选表,要么就老老实实显示英文。
        return None

    # 「每股」「人均」在中文里是最外层的量词,必须排在其它修饰之前:
    # total + 每股 + 负债 逐字拼是「总每股负债」,正确语序是「每股总负债」。
    _OUTER = ("每股", "人均")
    mods.sort(key=lambda w: 0 if w in _OUTER else 1)
    core = "".join(periods) + "".join(mods) + "".join(heads)
    if sufs:
        core += "(" + "·".join(sufs) + ")"
    return core
