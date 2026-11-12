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
    """一条 `def x = ...;` / `plot scan = ...;` / `input n = 5;`。

    `expr_start` / `expr_end` 是**表达式**在源码里的字节跨度(不含 `def x =` 和分号)。
    可视化条件行靠它拿到"这一条的原文",数字的内联编辑靠 num 节点自带的位置。

    kind = 'input' 是 ThinkScript 的参数声明(常量);`rec x = …` 记成 'def' 且 rec=True。
    """
    name: str
    node: object
    kind: str                 # 'def' | 'plot' | 'input'
    expr_start: int = 0
    expr_end: int = 0
    rec: bool = False
    line: int = 0             # 语句所在行 —— 时间序列引擎的报错要能指到"第几行"


# ═══════════════════════════════════════════════════════════════
# 词法
# ═══════════════════════════════════════════════════════════════

# 标识符允许带点:上游字段名本身就长这样(MACD.hist / High.All / Perf.Y)
#
# 2026-09-15 加了 `[` `]`:ThinkScript 的 K 线偏移(`close[1]` = 前一根收盘)。以前没有它,
# 一份标准的选股脚本在**语法分析之前**就死在这里,报「第 18 行:看不懂的字符 '['」——
# 用户以为是自己打错了字,其实是整类写法没支持;AI 修错只会做局部替换,当然也修不了。
# 用到偏移的脚本走时间序列引擎(screen_series),见 compile_script。
#
# 同时补齐 ThinkScript 表达式里其它常见记号:`!`(逻辑非)、`&&` `||`、字符串(只用于
# `RSI("length" = 9)` 这种带引号的参数名)、`.5` 这种省掉前导 0 的小数、以及
# `BollingerBands().UpperBand` 的属性点(`.` 单独成 op;`MACD.hist` 这种字段名仍是一个整词)。
_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>\#[^\n]*)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"[^"\n]*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><=|>=|==|!=|<>|&&|\|\||[<>+\-*/(),;=\[\]!.])
""", re.VERBOSE)

# 字段名能不能**原样写进脚本** —— 和上面 ident 分组同一个口径,改一处必须改另一处。
#
# 2026-09-17 用户:从「可用字段」点进生成框的字段,生成时却说不认识。全量探针(美股 3809 个字段名)实测:
# 带 `|`(多周期,2656 个)、带 `[`(26 个)的原本就不进列表;但 27 个带 `-` `+` 或数字开头的
# (ADX+DI_14、daily-bar.time、24h_vol_to_market_cap …)照样列出来,点进去词法阶段就断成几段,
# 怎么写都用不了。**列表里出现的,必须写得进脚本** —— screen_source.field_search 按它过滤。
_WRITABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def is_writable_name(name: str) -> bool:
    return isinstance(name, str) and bool(_WRITABLE_NAME_RE.fullmatch(name)) and "|" not in name


# input / rec / declare / if / then / else 是 ThinkScript 的语句与表达式关键字(2026-09-15)。
# `rec` 就是显式的递归 def;`declare lower;` 这类研究用的声明整句跳过;
# yes / no 是 ThinkScript 的布尔字面量(input showX = yes;);crosses / within 是它的中缀运算
# (`close crosses above sma20`、`cond within 5 bars`)。
_KEYWORDS = {"def", "plot", "and", "or", "not", "true", "false", "yes", "no",
             "input", "rec", "declare", "if", "then", "else", "crosses", "within"}

# 词法阶段就能说清楚的"不支持"—— 比「看不懂的字符」有用得多
_CHAR_HINT = {
    "{": "花括号:ThinkScript 的枚举型 input(input x = {default A, B})这里不支持,改成数字或 true/false",
    "}": "花括号:ThinkScript 的枚举型 input 这里不支持",
    "'": "单引号:参数名要用双引号(RSI(\"length\" = 9));画图 / 标签语句是研究用的,选股脚本里删掉即可",
    "?": "问号:这里不支持 ?: 三目写法,请写 if 条件 then 值1 else 值2",
    ":": "冒号:这里不支持 ?: 三目写法,请写 if 条件 then 值1 else 值2",
    "%": "百分号:百分比请直接写小数(5% 写 0.05)",
    "&": "单个 &:逻辑与请写 and(或 &&)",
    "|": "单个 |:逻辑或请写 or(或 ||)",
    "^": "^:乘方请写 Power(x, n)",
    "$": "$:脚本里不能出现货币符号,金额直接写数字",
}
_FULLWIDTH = {"；": ";", "，": ",", "（": "(", "）": ")", "＝": "=", "＜": "<", "＞": ">",
              "＋": "+", "－": "-", "＊": "*", "／": "/", "［": "[", "］": "]", "　": " "}

# ThinkScript 里有、这里明确不支持的语法关键字 —— 在它出现的位置就说清,别让它变成「缺分号」
_UNSUPPORTED_SYNTAX = {
    "fold": "fold 循环这里不支持,请改写成 Sum / Highest / Lowest / CompoundValue 这类窗口或递归写法",
    "while": "while 循环这里不支持",
    "switch": "switch / case 这里不支持,请用 if … then … else if …",
    "case": "switch / case 这里不支持,请用 if … then … else if …",
    "script": "自定义 script 块这里不支持,把里面的定义直接写在主脚本里",
}

# 研究(study)里才有的画图 / 标签 / 告警语句 —— 用户把整个研究粘进选股器时会带着它们。
# 这些语句对选股没有意义,**整句跳过并记一条提示**,而不是让整份脚本报错。
_STUDY_STMTS = {
    "addlabel", "addchartbubble", "addcloud", "addverticalline", "assignpricecolor",
    "assignbackgroundcolor", "alert", "addorder", "definecolor", "setdefaultcolor",
    "hidebubble", "hidetitle", "hidepriceplot", "definecolor", "addchart",
}


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
            ch = src[i]
            hint = _CHAR_HINT.get(ch)
            # 全角标点是中文输入法最常见的坑:`；` `，` `（` 看着和半角一模一样
            if ch in _FULLWIDTH:
                hint = f"这是全角字符,请换成半角的 {_FULLWIDTH[ch]!r}"
            raise ScreenError(
                f"第 {_line_of(src, i)} 行:看不懂的字符 {ch!r}"
                + (f" —— {hint}" if hint else
                   "(这套脚本认 ThinkScript 的 def / plot / input / rec、算术与比较、and / or / not、"
                   "if 条件 then 值 else 值、K 线偏移 x[n]、Sum / Highest / Average 等函数)"))
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


# ═══════════════════════════════════════════════════════════════
# 名字不分大小写(2026-09-19 用户:编辑框把 SMA20 > SMA50 改成 sma10 > sma20,报「不认识 'sma10'」)
#
# ThinkScript 本身不分大小写。原来只有 close/open/…、函数名、关键字不分,扫描源字段名
# (SMA20 / RSI / market_cap_basic / MACD.macd / Perf.W)和自己 def 的名字(def Up … plot scan = up)
# 都是逐字比对,换个大小写就「不认识」。按类别探针实测这两类全中。
# 做法:编译前把**标识符**按原名改写。逐字就是字段名或 def 名的不动(字段逐字优先,见下面 09-22 事故);
# 其余先对自己的 def / input / rec 名字,再对扫描源字段(不分大小写唯一对上才改,对上多个就不动,照旧报不认识)。只改大小写,长度不变,所以 decompose / 编辑框 / 数字框
# 用的源码位置全都照旧成立。函数调用(后面紧跟 `(`)、价格名、关键字本来就不分大小写,不动。
# 词法不过(大白话、半截输入)原样返回,交给后面的流程报错或走本地识别。
def fix_case(src: str, names, extra_src: str = "") -> tuple[str, list[str]]:
    """→ (改写后的脚本, ["sma10 → SMA10", …])。names = 这个市场扫描源字段名全集。"""
    try:
        toks = _tokenize(src)
    except ScreenError:
        return src, []
    try:
        ctx_toks = _tokenize(extra_src) if extra_src else []
    except ScreenError:
        ctx_toks = []
    defs: dict[str, str] = {}
    for tl in (ctx_toks, toks):
        for i, t in enumerate(tl[:-1]):
            if t.kind in ("def", "input", "rec", "plot") and tl[i + 1].kind == "ident":
                defs.setdefault(tl[i + 1].val.lower(), tl[i + 1].val)
    by_low: dict[str, list[str]] = {}
    for n in names or ():
        by_low.setdefault(n.lower(), []).append(n)
    out = list(src)
    changes: list[str] = []
    for i, t in enumerate(toks):
        if t.kind != "ident":
            continue
        v = t.val
        if i + 1 < len(toks) and toks[i + 1].kind == "op" and toks[i + 1].val == "(":
            continue                       # 函数调用,本来就不分大小写
        low = v.lower()
        # 已经逐字是字段名 / 自己的 def 名的一律不动 —— **字段逐字匹配优先于别处 def 的不分大小写匹配**。
        # 2026-09-22 线上事故:当前条件是「上升趋势」(def sma50 = Average(close, 50)),追加「VCP 波段收缩」,
        # VCP 里写得完全正确的 SMA50 被改成上下文里的 sma50,报「不认识 'sma50'」。
        if low in _PRICE or v in defs.values() or v in (names or ()):
            continue
        target = defs.get(low)
        if target is None:
            cand = by_low.get(low) or []
            target = cand[0] if len(cand) == 1 else None
        if not target or target == v or len(target) != len(v):
            continue
        out[t.pos:t.pos + len(v)] = list(target)
        msg = f"{v} → {target}"
        if msg not in changes:
            changes.append(msg)
    return "".join(out), changes


CASE_NOTE = "名字不分大小写,已按原名改写:{changes}。"


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
        self.skipped: list[str] = []       # 跳过的研究用语句(AddLabel 之类),给用户一条提示

    # ── 基础动作 ────────────────────────────────────────────
    def _peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _peek2(self) -> _Tok | None:
        return self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None

    def _expect_kw(self, kind: str, what: str) -> _Tok:
        t = self._peek()
        if t is None:
            raise ScreenError(f"脚本在该出现 {kind} 的地方结束了({what})")
        if t.kind != kind:
            raise ScreenError(
                f"第 {_line_of(self.src, t.pos)} 行:这里应该是 {kind},实际是 {t.val!r}({what})")
        return self._next()

    def _skip_stmt(self) -> None:
        """跳到下一个分号之后(含)。"""
        while self._peek() is not None and not self._at_op(";"):
            self._next()
        if self._at_op(";"):
            self._next()

    def _is_study_stmt(self) -> str | None:
        """当前位置是不是研究用的语句 → 语句名 | None。

        两种形状:`AddLabel(...)` 直接调用;`scan.SetDefaultColor(...)` 对 plot 的属性调用。
        """
        t = self._peek()
        if t is None or t.kind != "ident":
            return None
        t2 = self._peek2()
        if t2 is not None and t2.kind == "op" and t2.val == "(" and t.val.lower() in _STUDY_STMTS:
            return t.val
        # `scan.SetDefaultColor(...)`:词法器把带点的名字整个当一个标识符(字段名 MACD.hist 就长这样),
        # 所以「名字.方法(」到这里是 ident("scan.SetDefaultColor") + "(" —— 语句开头出现这种形状只能是
        # 对 plot 的属性调用,整句跳过
        if t2 is not None and t2.kind == "op" and t2.val == "(" and "." in t.val:
            return t.val
        if t2 is not None and t2.kind == "op" and t2.val == ".":
            t3 = self.toks[self.i + 2] if self.i + 2 < len(self.toks) else None
            t4 = self.toks[self.i + 3] if self.i + 3 < len(self.toks) else None
            if t3 is not None and t3.kind == "ident" and t4 is not None and t4.kind == "op" and t4.val == "(":
                return f"{t.val}.{t3.val}"
        return None

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
    def parse(self, require_plot: bool = True) -> tuple[list["Stmt"], str]:
        """→ ([Stmt, ...], 最终 plot 的名字)。require_plot=False 只给「粘进来的片段」用(见 complete_fragment)"""
        stmts: list[Stmt] = []
        plot_name: str | None = None
        seen: set[str] = set()
        while self._peek() is not None:
            t = self._peek()
            if t.kind == "declare":
                # declare lower; / declare once_per_bar; —— 研究的声明,对选股没意义
                self._skip_stmt()
                continue
            study = self._is_study_stmt()
            if study:
                self._skip_stmt()
                if study not in self.skipped:
                    self.skipped.append(study)
                continue
            if t.kind not in ("def", "plot", "input", "rec"):
                extra = ""
                if t.kind == "ident" and self._peek2() is not None and self._peek2().kind == "op" \
                        and self._peek2().val == "=":
                    extra = (f"(ThinkScript 里先 `def {t.val};` 再 `{t.val} = …;` 的两段写法这里不支持,"
                             f"合成一句 `def {t.val} = …;`)")
                raise ScreenError(
                    f"第 {_line_of(self.src, t.pos)} 行:每一句都要以 def / plot / input 开头,"
                    f"这里是 {t.val!r}。{extra}"
                    f"写法:def 名字 = 表达式;   最后一句 plot scan = 综合条件;")
            kind = self._next().kind
            rec = kind == "rec"
            if rec:
                kind = "def"
            nt = self._peek()
            if nt is None or nt.kind != "ident":
                raise ScreenError(f"{kind} 后面要跟一个名字,例如 `{kind} cond_price = close > 20;`")
            name = self._next().val
            if name in seen:
                raise ScreenError(f"名字 {name!r} 定义了两次")
            seen.add(name)
            if self._at_op(";"):
                raise ScreenError(
                    f"第 {_line_of(self.src, t.pos)} 行:`{kind} {name};` 只声明不赋值 —— "
                    f"ThinkScript 里先 `def {name};` 再 `{name} = …;` 的两段写法这里不支持,"
                    f"合成一句 `def {name} = …;`(递归定义直接写 {name}[1] 即可)")
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
                              expr_start=expr_start, expr_end=expr_end, rec=rec,
                              line=_line_of(self.src, t.pos)))
            if kind == "plot":
                plot_name = name
        if not stmts:
            raise ScreenError("脚本是空的。至少要有一句 `plot scan = <条件>;`")
        if plot_name is None and require_plot:
            raise ScreenError(
                "脚本里没有 plot 语句 —— 少了最终的筛选条件。"
                "最后加一句,例如:plot scan = cond_price and cond_volume;")
        return stmts, plot_name

    # ── 表达式 ──────────────────────────────────────────────
    #
    # 2026-09-15 补的 ThinkScript 写法(节点形状,时间序列引擎 screen_series 按这些求值):
    #   ("if", 条件, 值1, 值2)          if c then a else b(else 后面可以再接 if)
    #   ("idx", 基, 偏移)               x[n] —— n 根 K 线之前的值
    #   ("prop", 基, 属性名)            BollingerBands().UpperBand
    #   ("cross", 方向, 左, 右)         a crosses above / below / (不写=任一方向) b
    #   ("within", 条件, n)             cond within n bars —— 最近 n 根里出现过
    #   ("named", 参数名, 值)           只出现在函数参数里:RSI("length" = 9) / Average(data = close, length = 20)
    def _expr(self):
        if self._at("if"):
            t2 = self._peek2()
            if not (t2 is not None and t2.kind == "op" and t2.val == "("):
                return self._if_expr()
        return self._or()

    def _if_expr(self):
        self._next()                                # if
        cond = self._expr()
        self._expect_kw("then", "if 后面要有 then")
        a = self._expr()
        self._expect_kw("else", "if … then … 后面要有 else(ThinkScript 的 if 表达式必须给 else)")
        b = self._expr()
        return ("if", cond, a, b)

    def _or(self):
        node = self._and()
        while self._at("or") or self._at_op("||"):
            self._next()
            node = ("bin", "or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._at("and") or self._at_op("&&"):
            self._next()
            node = ("bin", "and", node, self._not())
        return node

    def _not(self):
        if self._at("not") or self._at_op("!"):
            self._next()
            return ("un", "not", self._not())
        return self._cmp()

    def _cmp(self):
        node = self._add()
        if self._at("crosses"):
            self._next()
            dirn = "any"
            t = self._peek()
            if t is not None and t.kind == "ident" and t.val.lower() in ("above", "below"):
                dirn = t.val.lower()
                self._next()
            node = ("cross", dirn, node, self._add())
        elif self._at_op("<", ">", "<=", ">=", "==", "!=", "<>"):
            op = self._next().val
            if op == "<>":
                op = "!="
            node = ("bin", op, node, self._add())
        elif self._at_op("="):
            t = self._peek()
            raise ScreenError(
                f"第 {_line_of(self.src, t.pos)} 行:比较相等要写 `==`,单个 `=` 是赋值")
        if self._at("within"):
            self._next()
            n = self._add()
            t = self._peek()
            if t is not None and t.kind == "ident" and t.val.lower() in ("bar", "bars"):
                self._next()
            node = ("within", node, n)
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
            return self._postfix(("num", float(t.val), t.pos, t.pos + len(t.val)))
        if t.kind in ("true", "false", "yes", "no"):
            self._next()
            return ("bool", t.kind in ("true", "yes"))
        if t.kind == "str":
            raise ScreenError(
                f"第 {_line_of(self.src, t.pos)} 行:字符串 {t.val} 只能用作函数的参数名"
                f"(例如 RSI(\"length\" = 9)),不能当值用")
        if t.kind == "op" and t.val == "(":
            self._next()
            node = self._expr()
            self._expect_op(")", "括号")
            return self._postfix(node)
        if t.kind == "if":
            # if(条件, 值1, 值2) 的函数写法 —— 与 if … then … else 同义
            self._next()
            return self._postfix(("call", "if", self._args("if")))
        if t.kind == "crosses":
            # Crosses(a, b, CrossingDirection.ABOVE) 的函数写法 —— crosses 同时是中缀关键字
            t2 = self._peek2()
            if t2 is not None and t2.kind == "op" and t2.val == "(":
                self._next()
                return self._postfix(("call", "crosses", self._args("Crosses")))
        if t.kind == "ident":
            low = t.val.lower()
            if low in _UNSUPPORTED_SYNTAX:
                raise ScreenError(f"第 {_line_of(self.src, t.pos)} 行:{_UNSUPPORTED_SYNTAX[low]}")
            self._next()
            if self._at_op("("):
                return self._postfix(("call", t.val, self._args(t.val)))
            return self._postfix(("name", t.val))
        raise ScreenError(f"第 {_line_of(self.src, t.pos)} 行:这里不该出现 {t.val!r}")

    def _args(self, fn: str) -> list:
        """`(` 已经看到但还没吃掉。参数可以是 `名字 = 值` / `"名字" = 值`(命名参数)。"""
        self._expect_op("(", f"函数 {fn}")
        args = []
        if not self._at_op(")"):
            args.append(self._arg())
            while self._at_op(","):
                self._next()
                args.append(self._arg())
        self._expect_op(")", f"函数 {fn}")
        return args

    def _arg(self):
        t, t2 = self._peek(), self._peek2()
        if t is not None and t.kind in ("ident", "str") and t2 is not None \
                and t2.kind == "op" and t2.val == "=":
            self._next(); self._next()
            name = t.val.strip('"').lower()
            return ("named", name, self._expr())
        return self._expr()

    def _postfix(self, node):
        """x[n](K 线偏移)与 x.prop(研究函数的输出属性),可以连着写。"""
        while True:
            if self._at_op("["):
                self._next()
                off = self._expr()
                self._expect_op("]", "K 线偏移 x[n] 的右方括号")
                node = ("idx", node, off)
                continue
            if self._at_op("."):
                t2 = self._peek2()
                if t2 is None or t2.kind != "ident":
                    t = self._peek()
                    raise ScreenError(f"第 {_line_of(self.src, t.pos)} 行:`.` 后面要跟属性名,例如 MACD().Diff")
                self._next(); self._next()
                node = ("prop", node, t2.val)
                continue
            return node


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

# 扫描源只有上面四个均量周期;其余周期(2026-09-14 用户:「只有 50 天均量而已,自己实现」)
# 由自家日线(rs_daily)算,字段名沿用 average_volume_{N}d_calc,时间回溯 / 命中日那条路本来就按任意 N 算。
# 上限 250:A 股日线只保留约 365 根,再长就有一大批票算不出。
# 这不是「拿近似周期顶替」—— 50 天就是真 50 天,只是数据来自自家日线、不含今天,返回体里写明。
OWN_VOL_MAX = 250
OWN_VOL_NOTE = "{n}日均量:扫描源没有这个周期,由自家日线计算(截至最近一次每晚更新的收盘,不含今天)"
_OWN_VOL_RE = re.compile(r"^average_volume_(\d+)d_calc$")


def own_avgvol_days(field: str) -> int | None:
    """扫描源没有、要由自家日线算的 N 日均量字段 → N;其余返回 None。"""
    m = _OWN_VOL_RE.match(field or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if n not in _VOL_AVG_DAYS and 2 <= n <= OWN_VOL_MAX else None


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
        if low.startswith("color."):
            raise ScreenError(f"{name} 是画图用的颜色,选股条件里用不上(AddLabel / AssignPriceColor 这类语句会被整句跳过)")
        if low.startswith("aggregationperiod."):
            raise ScreenError(f"这里只有日线,不支持 {name}(周线 / 月线 / 分钟线)")
        # 直接写扫描源字段名也放行 —— 3777 个字段,不可能都包成函数
        if self.has_field(name):
            return name
        # TradingView / ThinkScript 用户常写 average_volume_50d_calc —— 格式对、周期没有。
        # 泛泛的「不认识」会让人以为整份脚本写法不被支持(2026-09-13 用户原话:「ThinkScript 风格为什么识别不出」)。
        m = re.match(r"^average_volume_(\d+)d_calc$", name, re.I)
        if m:
            n = int(m.group(1))
            if 2 <= n <= OWN_VOL_MAX:
                self._note(OWN_VOL_NOTE.format(n=n))
                return f"average_volume_{n}d_calc"
            raise ScreenError(
                f"算不了 {name}({n} 天均量)—— 扫描源提供 "
                f"{'/'.join(map(str, _VOL_AVG_DAYS))} 天,其余周期由自家日线计算,最长 {OWN_VOL_MAX} 天。"
                f"(不拿相近周期冒充 {n} 天:那是在编数字)")
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
                    if not 2 <= n <= OWN_VOL_MAX:
                        raise ScreenError(
                            f"{fn}(volume, {n}) 算不了 —— 扫描源提供 "
                            f"{'/'.join(map(str, _VOL_AVG_DAYS))} 天均量,其余周期由自家日线计算,"
                            f"最长 {OWN_VOL_MAX} 天。(不拿相近周期冒充 {n} 天:那是在编数字)")
                    self._note(OWN_VOL_NOTE.format(n=n))
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
                    # 2026-09-11 逐个窗口长度实测(那周一休市):5D 是最近 4 根、1M 21 根、3M 61 根 ——
                    # 扫描源按日历往回数,**写 5 / 63 也不是 5 / 63 个交易日**。原来对这几个整数不提示,
                    # 用户会以为自己拿到的就是精确窗口
                    exact = f"{want}_{n}d" if n in (5, 21, 63) else None
                    self._note(
                        f"{fn}({src}, {n}) → {fld}({label})· 扫描源按日历往回数的固定窗口,"
                        f"不一定正好 {n} 个交易日(碰上节假日会少几根)"
                        + (f";要严格 {n} 个交易日,改用 {exact}(自家日线,每晚更新)" if exact else ""))
                    return fld
            raise ScreenError(
                f"{fn}({src}, {n}) 映射不了 —— 扫描源没有任意窗口的最高/最低价,"
                f"只有 5 日 / 1 月(≈21) / 3 月(≈63) / 6 月(≈126) / 52 周(≈252)。"
                f"请把周期改成接近这几个的值。")

        if f in ("rs", "rsrating", "rs_rating"):
            # IBD 口径的 RS 相对强度评级(1–99),在全市场快照上现算,见 screen_rs。
            # 不带参数 —— 它的回看窗口(3/6/9/12 月)是方法本身定死的
            if args:
                raise ScreenError("RS() 不带参数:它的回看窗口(3/6/9/12 个月)是固定的")
            return "rs_rating"

        if f in ("rslineupdays", "rsline_up_days", "rs_line_up_days", "rslinedays"):
            # RS 线(收盘 ÷ 基准指数)连续站在自身 21 日均线之上的交易日数,
            # 来自每晚更新的全市场日线(rs_history)。口径 2026-09-11 用户选定,不开放参数 ——
            # 开放了就得为每个周期每晚多算一遍,而且"均线周期"一变,和别人说的 RS 线上涨就不是一回事
            if args:
                raise ScreenError("RSLineUpDays() 不带参数:口径固定为 RS 线站上自身 21 日均线")
            return "rs_line_up_days"

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
            f"目前支持:Average(close|volume, N) · ExpAverage(close, N) · RS() · RSLineUpDays() · "
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
    # 时间序列模式(screen_series.Plan):脚本用到了 K 线偏移 / 递归 / if / 滚动窗口函数,
    # 要按自家全市场日线逐根求值,而不是查一行快照。None = 老的横截面模式
    series: object = None
    skipped: list[str] = _dc_field(default_factory=list)   # 跳过的研究用语句


# 横截面模式认识的函数 —— 这些名字、且参数是「裸价格序列 + 数字字面量」时,直接映射成扫描源字段
# (Average(close, 50) → SMA50)。参数一旦不是这个形状(Average(volume[1], 20) / Highest(ret, n)),
# 或者用了别的函数(Sum / StDev / CompoundValue …),就得逐根算 → 时间序列模式。
_CROSS_FUNCS = {"average", "simplemovingavg", "movavg", "sma", "expaverage", "ema", "movingaverage",
                "highest", "lowest", "rs", "rsrating", "rs_rating", "rslineupdays", "rsline_up_days",
                "rs_line_up_days", "rslinedays", "rsi"}
_SERIES_NAMES = {"hl2", "hlc3", "ohlc4", "hl2c4"}


def _needs_series(stmts: list[Stmt]) -> bool:
    """这份脚本能不能靠一行快照算出来?不能就走时间序列引擎。判据只看语法,不看数据。"""
    names = {st.name for st in stmts}

    def walk(node, owner: str) -> bool:
        if not isinstance(node, tuple):
            return False
        k = node[0]
        if k in ("idx", "if", "prop", "cross", "within", "named", "str"):
            return True
        if k == "name":
            nm = node[1]
            return nm == owner or nm.lower() in _SERIES_NAMES
        if k == "call":
            fn, args = node[1].lower(), node[2]
            if fn not in _CROSS_FUNCS:
                return True
            if fn == "rsi":
                # 横截面只认 RSI() / RSI(14) 这种纯数字参数;带 price 参数(RSI(14, close))或表达式就逐根算。
                # 必须排在下面 startswith("rs") 之前 —— "rsi" 也以 rs 开头
                return any(walk(a, owner) or a[0] != "num" for a in args)
            if fn.startswith("rs"):
                return any(walk(a, owner) for a in args)
            # Average / Highest 这类:横截面只认 (裸价格名, 数字),而且价格名要是扫描源**有对应字段**的那种:
            # 均线只有 close / 均量只有 volume、Highest 只有 high、Lowest 只有 low。
            # `Highest(close, 5)` 在 ThinkScript 里完全合法,横截面映射不了 —— 不能报「第 1 个参数应该是 high」
            # 把用户往回推,该按逐根算
            if len(args) != 2 or args[0][0] != "name" or args[1][0] != "num":
                return True
            src = args[0][1].lower()
            if fn == "highest":
                return src != "high"
            if fn == "lowest":
                return src != "low"
            if fn in ("expaverage", "ema", "movingaverage"):
                return src != "close"
            return src not in ("close", "volume")
        if k == "un":
            return walk(node[2], owner)
        if k == "bin":
            return walk(node[2], owner) or walk(node[3], owner)
        return False

    for st in stmts:
        if st.kind == "input" or st.rec:
            return True
        if walk(st.node, st.name):
            return True
    return False


_STMT_KINDS = ("def", "input", "rec", "declare")


def _split_fragment(text: str):
    """→ (语句部分原文, 结尾的裸表达式或 None, tokens);有 plot / 词法不通 → None。"""
    try:
        toks = _tokenize(text)
    except ScreenError:
        return None
    if not toks or any(t.kind == "plot" for t in toks):
        return None
    semis = [k for k, t in enumerate(toks) if t.kind == "op" and t.val == ";"]
    after = toks[semis[-1] + 1:] if semis else toks
    if not after or after[0].kind in _STMT_KINDS:
        return text, None, toks
    cut = after[0].pos
    tail = "\n".join(line.split("#", 1)[0] for line in text[cut:].split("\n")).strip().rstrip(";").strip()
    return text[:cut], (tail or None), toks


def _close_last_stmt(body: str) -> str:
    """最后一句漏写分号时补上(单独一份脚本里解析器容忍,但后面要接补上的 plot,不补就成了「缺分号」)。
    分号补在那一行**代码**的末尾,不补进行尾注释里。"""
    lines = body.split("\n")
    for k in range(len(lines) - 1, -1, -1):
        code, sep, comment = lines[k].partition("#")
        if code.strip():
            if not code.rstrip().endswith(";"):
                lines[k] = code.rstrip() + ";" + ((" " + sep + comment) if sep else "")
            break
    return "\n".join(lines)


def complete_fragment(text: str, context: str | None, compile_fn) -> dict | None:
    """「复制这一条」复制出来的片段 → 能编译的完整脚本。不是片段返回 None(调用方照原流程走)。

    2026-09-17 用户:条件行上「复制这一条」(⎘)复制出 `def c_price_2 = close > 10;`,粘回生成框报
    「脚本里没有 plot 语句」—— **自家复制出来的东西自家不认**。copySnippet 的产出有三种形状,都没有 plot:
      · `def 名 = 表达式;` / `rec 名 = …;`            (条件行是 def)
      · `input 参数 = 值; … 表达式`                    (条件行是 plot 里的一项,前面带它用到的参数)
      · 上面两种里的表达式还会引用原脚本的中间定义(rng1m / bullStreak),单独粘贴时根本不认识
    做法:
      · 片段里没被别的语句引用的**布尔** def + 结尾的裸表达式,按 and 补成 plot(数值定义、input、rec 不算条件)
      · 追加模式前端把当前脚本当 context 传进来:拼在片段前面编译,片段就能引用原脚本的定义和参数;
        和原脚本重名的,input 同值直接沿用原来的,其余改名 `名_2`(与前端 mergeAppend 同规则)。
        返回的 frag_names / plot_name 让调用方只把片段自己的条件行交给前端
      · 片段里一条布尔条件都没有 → 报清楚,不猜
    纯表达式(没有任何语句)且没有 context 时不算片段 —— 那是「close > 10」这种,交给本地关键词识别。
    """
    sp = _split_fragment(text)
    if sp is None:
        return None
    stmts_src, tail, _toks = sp
    ctx_stmts: list = []
    if context and context.strip():
        try:
            ctx_stmts, _ = _Parser(context).parse()
        except ScreenError:
            context, ctx_stmts = None, []
    else:
        context = None
    ctx_names = {st.name for st in ctx_stmts}
    if not stmts_src.strip():
        # 纯表达式:只有在追加、且确实用到了原脚本里的名字时才按片段处理
        if not context or not tail:
            return None
        tail_ids = {t.val for t in (_tokenize(tail) or []) if t.kind == "ident"}
        if not (tail_ids & ctx_names):
            return None

    notes: list[str] = []
    if context:
        fst, _ = _Parser(stmts_src).parse(require_plot=False) if stmts_src.strip() else ([], None)
        ctx_src = {st.name: (st.kind, st.rec, " ".join(context[st.expr_start:st.expr_end].split()))
                   for st in ctx_stmts}
        taken = ctx_names | {st.name for st in fst}
        # 片段内被别的语句引用的 = 依赖(参数 / 中间定义);没被引用的布尔 def = 用户复制的那条条件本身
        fref: set[str] = set()
        for st in fst:
            fref |= _names_in(st.node) - {st.name}
        if tail:
            fref |= {t.val for t in _tokenize(tail) if t.kind == "ident"}
        rename: dict[str, str] = {}
        drops: set[str] = set()
        for st in fst:
            if st.name not in ctx_names:
                continue
            same = ctx_src[st.name] == (st.kind, st.rec, " ".join(stmts_src[st.expr_start:st.expr_end].split()))
            is_cond = st.kind == "def" and not st.rec and _is_boolean(st.node) and st.name not in fref
            # 依赖和原脚本里一字不差 → 沿用原来的,不复制一份 `rng1m_2`;条件本身重名照样改名追加(用户要的就是再加一条)
            if same and not is_cond:
                drops.add(st.name)
                continue
            k = 2
            while f"{st.name}_{k}" in taken:
                k += 1
            rename[st.name] = f"{st.name}_{k}"
            taken.add(rename[st.name])
        if rename or drops:
            toks = _tokenize(text)
            edits: list[tuple[int, int, str]] = []
            for i, t in enumerate(toks):
                if t.kind in ("input", "def", "rec") and i + 1 < len(toks) and toks[i + 1].val in drops:
                    end = next((u.pos + 1 for u in toks[i + 2:] if u.kind == "op" and u.val == ";"), None)
                    if end is not None:
                        edits.append((t.pos, end, ""))
                elif t.kind == "ident" and t.val in rename and not any(a <= t.pos < b for a, b, _ in edits):
                    edits.append((t.pos, t.pos + len(t.val), rename[t.val]))
            for a, b, rep in sorted(edits, reverse=True):
                text = text[:a] + rep + text[b:]
            if not "\n".join(line.split("#", 1)[0] for line in text.split("\n")).strip():
                raise ScreenError(
                    f"粘进来的这段({'、'.join(sorted(drops))})和当前脚本里已有的一字不差,而且不是一条筛选条件"
                    f"(是参数或数值定义),追加进来什么也不会多。要改它的值,直接在条件区下面的参数 / 中间定义里改。")
            sp = _split_fragment(text)
            if sp is None:
                return None
            stmts_src, tail, _toks = sp
            for old, new in rename.items():
                notes.append(f"粘进来的 {old} 和当前脚本里的重名,追加时改名为 {new}")
            if drops:
                notes.append(f"{'、'.join(sorted(drops))} 当前脚本里已有且一字不差,沿用原来的定义")

    fst, _ = _Parser(stmts_src).parse(require_plot=False) if stmts_src.strip() else ([], None)
    referenced: set[str] = set()
    for st in fst:
        referenced |= _names_in(st.node) - {st.name}
    if tail:
        referenced |= {t.val for t in _tokenize(tail) if t.kind == "ident"}
    cands = [st.name for st in fst
             if st.kind == "def" and not st.rec and st.name not in referenced and _is_boolean(st.node)]
    if not cands and not tail:
        names = "、".join(st.name for st in fst) or "(空)"
        raise ScreenError(
            f"粘进来的这段只有参数或数值定义({names}),没有筛选条件 —— 条件要是比大小 / 真假判断,"
            f"例如 `def c = close > 10;`。要整份脚本请用条件区上方的「复制脚本」。")
    items = list(cands)
    if tail:
        items.append(f"({tail})" if cands and re.search(r"\bor\b|\|\|", tail) else tail)
    plot_name = "scan"
    if context:
        plot_name = "pasted"
        while plot_name in ctx_names or plot_name in {st.name for st in fst}:
            plot_name += "_"
    body = _close_last_stmt(stmts_src.rstrip())
    src = ((context.rstrip() + "\n") if context else "") + (body + "\n" if body else "") + \
        f"plot {plot_name} = " + " and ".join(items) + ";"
    c = compile_fn(src)
    if not context:
        notes.insert(0, f"粘进来的是一段没有 plot(最终筛选条件)的片段,已补上「plot {plot_name} = "
                        + " and ".join(items) + ";」")
    return {"src": src, "c": c, "frag_names": {st.name for st in fst}, "plot_name": plot_name,
            "context": bool(context), "notes": notes}


def compile_script(src: str, has_field, sma_periods: list[int],
                   ema_periods: list[int], rsi_periods: list[int]) -> Compiled:
    """解析脚本 + 解析出需要哪些扫描源字段。不发网络请求。

    用到 K 线偏移 / 递归 / if / 滚动窗口函数的脚本走时间序列引擎(screen_series.compile),
    其余照旧走横截面 —— 老脚本的行为一个字都不变。
    """
    p = _Parser(src)
    stmts, plot_name = p.parse()
    if _needs_series(stmts):
        from app.services.quant import screen_series
        return screen_series.compile(src, stmts, plot_name, has_field, sma_periods, ema_periods,
                                     rsi_periods, skipped=p.skipped)
    rs = _FieldResolver(has_field, sma_periods, ema_periods, rsi_periods)
    fields: list[str] = []
    defined: set[str] = set()
    all_names = {st.name: st for st in stmts}

    def walk(node, owner: str):
        if not isinstance(node, tuple):
            return
        k = node[0]
        if k in ("num", "bool"):
            return
        if k == "name":
            if node[1] in defined:
                return
            if node[1] in all_names:
                st = all_names[owner]
                raise ScreenError(
                    f"第 {st.line} 行:{owner} 用到了在它后面才定义的 {node[1]} —— "
                    f"定义要写在使用之前;两个定义互相引用(互递归)这里不支持")
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

    notes = list(rs.notes)
    if p.skipped:
        notes.append("已跳过研究用的画图 / 标签语句(对选股没有影响):" + "、".join(p.skipped))
    return Compiled(stmts=stmts, plot_name=plot_name, fields=fields, notes=notes, skipped=p.skipped)


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
    # **三值逻辑(Kleene)**:
    #   假 且 未知 = 假      —— 已经有一条明确不满足,缺什么都不可能命中了
    #   真 或 未知 = 真      —— 已经有一条满足,或的另一边是什么都无所谓
    # 其余含未知的组合才是真正的"算不出"。两边对称,与左右顺序无关,
    # 所以和上面"不做短路"的要求不冲突。
    #
    # 2026-09-11 修:原来只要有一边是 None 就整体 None。实测一套 10 条的美股脚本,
    # 页面报「2547 只算不出」,其中 2537 只(99.6%)早被别的条件判了不满足,
    # 真正"可能命中但缺数据"的只有 10 只 —— 那个数字被放大了 250 倍,
    # 用户据此去怀疑数据源,而问题根本不在那里。
    # 命中集合不受影响(命中要求整体为真,改前改后都一样);`or` 脚本会多出
    # "一边满足、另一边缺数据"的票,那本来就该算命中。
    if op == "and":
        a, b = _truthy(lv), _truthy(rv)
        if a is False or b is False:
            return False
        return None if (a is None or b is None) else True
    if op == "or":
        a, b = _truthy(lv), _truthy(rv)
        if a is True or b is True:
            return True
        return None if (a is None or b is None) else False
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
    hits, skipped, _ = evaluate_detail(c, rows, resolver_cache)
    return hits, skipped


def evaluate_detail(c: Compiled, rows: list[dict],
                    resolver_cache: dict) -> tuple[list[dict], int, dict[str, int]]:
    """→ (命中的行, 算不出的行数, {字段: 在算不出的行里为空的次数})

    只报"算不出"那部分行里缺的字段 —— 已经被别的条件判不满足的行,
    缺什么都无关紧要,统计进来只会误导用户去怀疑一个与结果无关的字段。
    """
    hits: list[dict] = []
    skipped = 0
    missing: dict[str, int] = {}
    for row in rows:
        env: dict = {}
        for st in c.stmts:
            env[st.name] = _eval(st.node, row, env, resolver_cache)
        verdict = _truthy(env.get(c.plot_name))
        if verdict is None:
            skipped += 1
            for f in c.fields:
                if row.get(f) is None:
                    missing[f] = missing.get(f, 0) + 1
        elif verdict:
            hits.append(row)
    return hits, skipped, missing


def missing_reason(field: str) -> str:
    """字段为空的**常见**原因。只写有把握的,拿不准就不写。"""
    if field in ("rs_rating", "rs_raw"):
        return "次新股(不足 250 个交易日)或不在 RS 排名池里(美股 OTC、市值约 5000 万美元以下)"
    if field in ("high_5d", "low_5d", "high_21d", "low_21d", "high_63d", "low_63d"):
        return ("日线里还没有最高/最低价(老数据只存了收盘,每晚任务整窗重拉后才有)、"
                "日线根数不够、不在 RS 排名池里,或这个市场的日线已过期")
    if field in ("up_days_20d", "down_days_20d"):
        return "日线不足 21 根、不在 RS 排名池里,或这个市场的日线已过期"
    if field == "ud_vol_ratio_20d":
        return ("日线里还没有成交量(老数据只存了收盘,每晚任务整窗重拉后才有)、"
                "近 20 日没有下跌日(除不了),或日线不足 21 根 / 已过期")
    if field.startswith("vcp_"):
        return ("日线不足 60 根、日线里还没有最高/最低价(老数据只存了收盘,每晚任务整窗重拉后才有)、"
                "不在 RS 排名池里,或这个市场的日线已过期;量能递减另有一种:只有一次收缩,无从比较")
    if field == "rs_line_up_days":
        return ("不在 RS 排名池里、上市不足 21 个交易日、停牌,"
                "或这个市场的日线还没建好 / 已过期(见上方提示)")
    m = re.match(r"^(?:SMA|EMA)(\d+)$", field)
    if m:
        return f"上市不足 {m.group(1)} 个交易日,均线算不出来"
    m = re.match(r"^average_volume_(\d+)d_calc$", field)
    if m:
        if own_avgvol_days(field):
            return (f"上市不足 {m.group(1)} 个交易日、不在自家日线覆盖范围(美股剔 OTC 与微盘)、"
                    f"当天停牌,或日线已过期")
        return f"上市不足 {m.group(1)} 天"
    if field in ("price_52_week_high", "price_52_week_low"):
        return "上市不足一年"
    if field.startswith("price_earnings"):
        return "亏损公司没有市盈率"
    if field.startswith("return_on_equity"):
        return "股东权益为负(此时 ROE 无意义)或小盘股未披露"
    if re.search(r"_(ttm|fq|fy|fh)$", field) or field.startswith(
            ("total_", "net_", "gross_", "dividend", "debt_", "earnings_")):
        return "财报数据未披露"
    return ""


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
    # 2026-09-11 实测(拿自家日线反推 6 只票):Volatility.* 是**日均振幅**
    # (每天最高最低价差占股价的 %,再对窗口取平均),不是这段时间的价格区间。
    # 原来叫「月波动率」,用户照字面写 `Volatility.M < 20` 想筛「一个月波动不超过 20%」,
    # 实际全市场 90% 都满足(中位数 3.8%),等于没筛。要一个月的价格区间用 High.1M / Low.1M。
    "Volatility.D": "当日振幅", "Volatility.W": "近1周日均振幅", "Volatility.M": "近1月日均振幅",
    "sector": "板块", "industry": "行业", "currency": "币种",
    "rs_rating": "RS相对强度评级", "rs_raw": "RS原始分",
    "rs_line_up_days": "RS线上涨天数",
    # VCP(每晚日线算出,口径见 services/quant/vcp.py)
    "vcp_contractions": "VCP收缩次数", "vcp_first_depth": "VCP首次收缩深度%",
    "vcp_last_depth": "VCP最后一次收缩深度%", "vcp_vol_declining": "VCP量能逐次递减(1是0否)",
    "vcp_last_vol_ratio": "VCP最后一次收缩量比", "vcp_pivot_dist": "距VCP枢轴%",
    "vcp_base_days": "VCP底部天数", "vcp_depths": "VCP各次深度%",
    "vcp_low_vol_ratio": "VCP最低点量比", "up_days_20d": "近20日上涨天数",
    "down_days_20d": "近20日下跌天数", "ud_vol_ratio_20d": "近20日涨跌日均量比",
    "high_5d": "近5个交易日最高", "low_5d": "近5个交易日最低",
    "high_21d": "近21个交易日最高", "low_21d": "近21个交易日最低",
    "high_63d": "近63个交易日最高", "low_63d": "近63个交易日最低",
    # 资金逆势买入(每晚日线 + 基准算出,口径见 services/quant/accum.py)
    "acc_dn_days_42d": "近42日大盘下跌时逆势放量超额上涨天数",
    "acc_dn_excess_42d": "近42日大盘下跌日扣beta平均超额%",

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
    "Value.Traded": "成交额", "Volatility.D": "当日振幅",
    # 「成交量+变动」是两个头词,会被 heads>1 那道闸拦下,但它拼起来是对的
    "volume_change": "成交量变动", "volume_change_abs": "成交量变动(绝对值)",
    "price_change": "价格变动", "market_cap_diluted_calc": "稀释市值",
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


_SERIES_LABEL = {"open": "开盘价", "high": "最高价", "low": "最低价", "close": "收盘价",
                 "volume": "成交量", "hl2": "(高+低)/2", "hlc3": "(高+低+收)/3", "ohlc4": "(开+高+低+收)/4"}
_CROSS_LABEL = {"above": "上穿", "below": "下穿", "any": "交叉"}


def _tok_stream(node, rs, base: int, out: list, defined: dict, parent_prec: int = 0,
                series: bool = False):
    """AST → 展示 token。`base` 是表达式在源码里的起点,用来把数字位置归一化。

    series=True(时间序列模式):函数不再映射成扫描源字段,按原样显示 `Sum(阴线, 5)`;
    K 线偏移显示成 `收盘价[1]`;if / crosses / within 显示成中文关键字。
    """
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
        if series:
            low = nm.lower()
            if low in _SERIES_LABEL:
                out.append({"k": "field", "t": _SERIES_LABEL[low], "raw": nm})
                return
            try:
                out.append({"k": "field", "t": field_label(rs.field_of_name(nm)), "raw": nm})
            except ScreenError:
                out.append({"k": "field", "t": nm, "raw": nm})      # 枚举常量(Double.NaN)之类
            return
        out.append({"k": "field", "t": field_label(rs.field_of_name(nm)), "raw": nm})
        return
    if k == "call":
        if series:
            out.append({"k": "fn", "t": node[1]})
            out.append({"k": "paren", "t": "("})
            for i, a in enumerate(node[2]):
                if i:
                    out.append({"k": "op", "t": ","})
                _tok_stream(a, rs, base, out, defined, 0, series)
            out.append({"k": "paren", "t": ")"})
            return
        fld = rs.field_of_call(node[1], node[2])
        out.append({"k": "field", "t": field_label(fld), "raw": fld})
        return
    if k == "named":
        out.append({"k": "kw", "t": node[1] + " ="})
        _tok_stream(node[2], rs, base, out, defined, 0, series)
        return
    if k == "idx":
        _tok_stream(node[1], rs, base, out, defined, 99, series)
        if node[2][0] == "num":
            # K 线偏移是「第几根之前」,不是阈值 —— 显示成一个 `[1]`,不给内联编辑框。
            # 2026-09-15 用户截图:`成交量 [ 1 ] 且 成交量 [ 1 ] 大于 成交量 [ 2 ]` 三个深色数字框,看着像要填的参数
            out.append({"k": "op", "t": "[" + _fmt_num(node[2][1]) + "]"})
            return
        out.append({"k": "op", "t": "["})
        _tok_stream(node[2], rs, base, out, defined, 0, series)
        out.append({"k": "op", "t": "]"})
        return
    if k == "prop":
        _tok_stream(node[1], rs, base, out, defined, 99, series)
        out.append({"k": "op", "t": "." + node[2]})
        return
    if k == "if":
        out.append({"k": "kw", "t": "如果"})
        _tok_stream(node[1], rs, base, out, defined, 0, series)
        out.append({"k": "kw", "t": "则"})
        _tok_stream(node[2], rs, base, out, defined, 0, series)
        out.append({"k": "kw", "t": "否则"})
        _tok_stream(node[3], rs, base, out, defined, 0, series)
        return
    if k == "cross":
        _tok_stream(node[2], rs, base, out, defined, 3, series)
        out.append({"k": "op", "t": _CROSS_LABEL.get(node[1], "交叉")})
        _tok_stream(node[3], rs, base, out, defined, 4, series)
        return
    if k == "within":
        out.append({"k": "paren", "t": "("})
        _tok_stream(node[1], rs, base, out, defined, 0, series)
        out.append({"k": "paren", "t": ")"})
        out.append({"k": "kw", "t": "在最近"})
        _tok_stream(node[2], rs, base, out, defined, 0, series)
        out.append({"k": "kw", "t": "根K线内出现"})
        return
    if k == "un":
        out.append({"k": "op", "t": "非" if node[1] == "not" else "−"})
        _tok_stream(node[2], rs, base, out, defined, 99, series)
        return
    # bin
    op = node[1]
    prec = _PREC.get(op, 0)
    need_paren = prec < parent_prec
    if need_paren:
        out.append({"k": "paren", "t": "("})
    _tok_stream(node[2], rs, base, out, defined, prec, series)
    out.append({"k": "logic" if op in ("and", "or") else "op",
                "t": _OP_LABEL.get(op, op)})
    # 右子树用 prec+1:同优先级的右结合要补括号(a - (b - c) 不能显示成 a - b - c)
    _tok_stream(node[3], rs, base, out, defined, prec + 1, series)
    if need_paren:
        out.append({"k": "paren", "t": ")"})


def decompose(src: str, c: Compiled, has_field, sma_periods, ema_periods,
              rsi_periods) -> dict:
    """把编译结果拆成可视化条件行。

    界面上的「条件」= plot 顶层 and 链的每一项(2026-09-15 起,见仓内 CLAUDE.md「条件行 = plot 的 and 项」):
      · 裸名字、指向一个 def            → 那条 def 就是条件行(plot_refs 里有它 = 启用)
      · 其余表达式(bullStreak >= 3 …) → kind='term' 的条件行,expr 是 plot 里那一段原文
    combine='all' 表示 plot 能按 and 拆开;拆不开(`a within 3 bars` 这类优先级比 and 低的写法)才是
    'custom',plot 原样只读。

    **被别的语句引用、自己又不是 plot 的一项的布尔 def 是中间定义,不是条件**(is_bool=False):
    猎杀 FOMO 脚本里 `isBull = close > open` / `isBear = close < open` 只是给 bullStreak / bearCount 用的,
    旧逻辑把它们和真正的条件并排列在「同时满足」下面,界面上就成了两条互斥的条件。
    """
    rs = _FieldResolver(has_field, sma_periods, ema_periods, rsi_periods)
    defined: dict[str, str] = {}
    conditions = []
    plot_stmt = None

    series = c.series is not None
    for st in c.stmts:
        if st.kind == "plot":
            plot_stmt = st
    # 前面的 plot(扫描只看最后一个):不是条件,但要原样留在脚本里 —— 原来直接丢,用到的定义还被当成「停用条件」
    extra_plots = [st for st in c.stmts if st.kind == "plot" and st is not plot_stmt]
    body = [st for st in c.stmts if st.kind != "plot"]
    def_names = {st.name for st in body}

    # plot 顶层 and 项。原文按词法切(节点上没有位置),切出来的段数必须与语法树一致,否则不拆
    terms: list[tuple] = []          # (node, 原文起点, 原文终点)
    if plot_stmt is not None:
        nodes = _and_terms(plot_stmt.node)
        spans = _split_top_and(src, plot_stmt.expr_start, plot_stmt.expr_end)
        if spans is not None and len(spans) == len(nodes):
            terms = [(n, s, e) for n, (s, e) in zip(nodes, spans)]
    # plot 是常量:`plot scan = false / no` 是「一条都没启用」(前端全停用时就这么写),不是一条叫 false 的条件 ——
    # 原来它被拆成一条启用的条件行,再打开任意一条就变成 `a and false`,永远 0 命中(审计 2026-09-15 实跑确认)。
    # `yes / true` 等于「全部放行」,前端没法用开关表达,按自定义组合原样保留
    const_plot = None
    if plot_stmt is not None and plot_stmt.node[0] == "bool":
        const_plot = bool(plot_stmt.node[1])
        terms = []
    plot_refs = [n[1] for n, _, _ in terms if n[0] == "name" and n[1] in def_names]

    # plot 只写了一个名字、条件都在那条 def 里(2026-09-15 用户第二份猎杀 FOMO:
    # `def FOMO_Setup = greenStreak >= minStreak and … and isGreen; plot scan = FOMO_Setup;`)。
    # 按上面的规则它只有一项 → 界面「同时满足 1 个条件」一整行,和脚本里写明的 7 个条件对不上。
    # 这种 def 叫「条件宿主」(term_host):条件行 = 它的顶层 and 项,规则与 plot 的项完全相同;
    # 宿主本身不进条件列表,前端回写时按原结构写回 `def 宿主 = 启用项 and …; plot scan = 宿主;`。
    # 只在宿主**没被别的语句引用**、不是递归 / input、至少拆出 2 项时才展开 —— 被引用的话它是个中间量,拆开会改含义
    term_host = ""
    if len(terms) == 1 and terms[0][0][0] == "name" and terms[0][0][1] in def_names:
        hn = terms[0][0][1]
        hst = next(st for st in body if st.name == hn)
        others: set[str] = set()
        for st in body:
            if st.name != hn:
                others |= _names_in(st.node)
        if hst.kind != "input" and not hst.rec and hn not in others and hst.node[0] == "bool" and not hst.node[1]:
            # 宿主全停用时前端写成 `def 宿主 = false;`:仍是宿主,只是没有启用的项(不能当成一条叫 false 的条件)
            term_host = hn
            terms = []
            plot_refs = []
        elif hst.kind != "input" and not hst.rec and hn not in others and _is_boolean(hst.node):
            hnodes = _and_terms(hst.node)
            hspans = _split_top_and(src, hst.expr_start, hst.expr_end)
            if len(hnodes) >= 2 and hspans is not None and len(hspans) == len(hnodes):
                term_host = hn
                terms = [(n, s, e) for n, (s, e) in zip(hnodes, hspans)]
                plot_refs = [n[1] for n, _, _ in terms if n[0] == "name" and n[1] in def_names]

    # 谁被别人用到了:别的 def 里 + plot 里非裸名字的项里
    used: set[str] = set()
    for st in body:
        used |= _names_in(st.node) - {st.name}
    for n, _, _ in terms:
        if not (n[0] == "name" and n[1] in def_names):
            used |= _names_in(n)
    if plot_stmt is not None and not terms:
        used |= _names_in(plot_stmt.node)
    for ep in extra_plots:
        used |= _names_in(ep.node)

    def _toks_of(node, start: int, end: int) -> tuple[list, str]:
        toks: list = []
        _tok_stream(node, rs, start, toks, defined, 0, series)
        expr = src[start:end]
        # 数字用**源码原文**显示,不用格式化后的值:用户写 0.10,界面上就该是 0.10。
        # 归一化成 0.1 之后再回写,会在他没改任何东西的情况下把脚本改掉。
        for t in toks:
            if t["k"] == "num":
                t["t"] = expr[t["s"]:t["e"]]
        return toks, expr

    for st in body:
        if st.name == term_host:
            continue                             # 宿主的内容已经拆成条件行,它自己不再单列
        toks, expr = _toks_of(st.node, st.expr_start, st.expr_end)
        if series:
            # 时间序列脚本里引用就显示**名字**:bullStreak / ret 这些是用户自己起的名,比内联展开好认;
            # 内联会把 `Highest(ret, lookback)` 摊成一串没括号的字(2026-09-15 截图里的「Highest收盘价÷收盘价[1]−1,5」)。
            # 参数带上当前值,条件行一眼能看出阈值是多少
            defined[st.name] = f"{st.name}({expr})" if st.kind == "input" else st.name
        else:
            # 条件行的中文标题:纯展示,给 plot 里引用它时用
            label = "".join(t["t"] for t in toks if t["k"] != "paren")
            defined[st.name] = label if len(label) <= 24 else st.name
        is_bool = st.kind != "input" and not st.rec and _is_boolean(st.node)
        if is_bool and st.name in used and st.name not in plot_refs:
            is_bool = False                      # 中间定义(isBull),见函数说明
        conditions.append({
            "name": st.name,
            "expr": expr,
            "tokens": toks,
            # 能独立开关的条件才是 True;中间变量(如 def sma50 = Average(close,50))、中间布尔定义、
            # input(参数)、递归定义(计数器)都不是 —— 停用它们会让引用方直接报错
            "is_bool": is_bool,
            # 'input' 要原样回写成 input(前端 buildScript 按它选关键字);'rec' 回写成 rec
            "kind": st.kind if st.kind == "input" else ("rec" if st.rec else "def"),
        })

    plot_order: list[str] = []
    k = 0
    for n, s, e in terms:
        if n[0] == "name" and n[1] in def_names:
            plot_order.append(n[1])
            continue
        k += 1
        toks, expr = _toks_of(n, s, e)
        name = f"{term_host or c.plot_name}#{k}"   # 带 # 不可能是标识符,不会和 def 撞名、也不会被当成引用
        first = next((t.get("ref") for t in toks if t["k"] == "ref"), None)
        conditions.append({
            "name": name,
            "title": first or "",
            "expr": expr,
            "tokens": toks,
            "is_bool": True,
            "kind": "term",
            # 回写进 `a and b` 时要不要补括号:只有 `x or y` 这一种(crosses / within 在比较那层,比 and 紧)。
            # 原文已经是 `(x or y)` 就不补 —— 否则每往返一次多包一层
            "paren": n[0] == "bin" and n[1] == "or" and not _wrapped(expr),
        })
        plot_order.append(name)

    return {
        "conditions": conditions,
        "combine": "all" if (terms or term_host or const_plot is False) else "custom",
        "plot_name": c.plot_name,
        "plot_expr": src[plot_stmt.expr_start:plot_stmt.expr_end] if plot_stmt else "",
        "plot_refs": plot_refs,
        "plot_order": plot_order,
        "term_host": term_host,
        "extra_plots": [{"name": ep.name, "expr": src[ep.expr_start:ep.expr_end]} for ep in extra_plots],
        "notes": rs.notes,
    }


def _and_terms(node) -> list:
    """`a and b and c` → [a, b, c](左结合的 bin-and 摊平);不是 and 就是一项。"""
    if node[0] == "bin" and node[1] == "and":
        return _and_terms(node[2]) + _and_terms(node[3])
    return [node]


def _split_top_and(src: str, start: int, end: int) -> list[tuple[int, int]] | None:
    """plot 原文按括号外的 and / && 切段 → [(起, 止), ...](绝对位置,去掉首尾空白与注释)。

    `if … then … else` 里的 and 也在括号外,切出来段数会比语法树多 —— 调用方比段数,对不上就不拆。
    """
    try:
        toks = _tokenize(src[start:end])
    except ScreenError:
        return None
    spans: list[tuple[int, int]] = []
    depth = 0
    seg: list[_Tok] = []
    for t in toks + [None]:
        if t is None or (depth == 0 and (t.kind == "and" or (t.kind == "op" and t.val == "&&"))):
            if not seg:
                return None
            spans.append((start + seg[0].pos, start + seg[-1].pos + len(seg[-1].val)))
            seg = []
            continue
        if t.kind in ("if", "then", "else") and depth == 0:
            return None
        if t.kind == "op" and t.val in ("(", "["):
            depth += 1
        elif t.kind == "op" and t.val in (")", "]"):
            depth -= 1
        seg.append(t)
    # 整段被括号包住、里面还是 and 链的项继续拆(`a and (b and (c or d))`):语法树不记括号,_and_terms 会摊平成 3 项,
    # 这里不拆的话段数对不上,整个 plot 退成 custom、失去逐条开关(审计 2026-09-15 实跑确认)
    out: list[tuple[int, int]] = []
    for s, e in spans:
        if _wrapped(src[s:e]):
            inner = _split_top_and(src, s + 1, e - 1)
            if inner is not None and len(inner) >= 2:
                out.extend(inner)
                continue
        out.append((s, e))
    return out


def _wrapped(text: str) -> bool:
    """整段被一对最外层括号包住?`(a) or (b)` 不算。"""
    t = text.strip()
    if not (t.startswith("(") and t.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(t):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(t) - 1:
            return False
    return True


def _names_in(node) -> set[str]:
    """表达式里出现的所有名字(递归定义里的 x[1] 也算)。"""
    out: set[str] = set()
    if isinstance(node, tuple) and node and isinstance(node[0], str):
        if node[0] == "name":
            out.add(node[1])
            return out
        for ch in node[1:]:
            out |= _names_in(ch)
    elif isinstance(node, list):
        for ch in node:
            out |= _names_in(ch)
    return out


_BOOL_FUNCS = {"isnan", "between", "crosses", "isascending", "isdescending"}


def _is_boolean(node) -> bool:
    """这条 def 产出的是真假值还是数字?决定界面上能不能单独停用。"""
    k = node[0]
    if k == "bool":
        return True
    if k == "un":
        return node[1] == "not"
    if k == "bin":
        return node[1] in ("and", "or", ">", ">=", "<", "<=", "==", "!=")
    if k in ("cross", "within"):
        return True
    if k == "if":
        return _is_boolean(node[2]) and _is_boolean(node[3])
    if k == "idx":
        return _is_boolean(node[1])
    if k == "call":
        return node[1].lower() in _BOOL_FUNCS
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
                 plot_expr: str | None = None, term_host: str | None = None,
                 plot_order: list[str] | None = None) -> str:
    """条件行 → 脚本。界面改完之后回写用。

    停用的条件**保留在脚本里**(仍然是 `def`),只是不进 plot ——
    这样用户重新启用时原文一字不差地回来。删除才是真删。
    """
    lines = []
    enabled = []
    # 启用项按 plot_order 排(与前端 condRows 同规则);不在里面的排后面、保持原顺序
    pos = {n: k for k, n in enumerate(plot_order or [])}
    ordered = sorted(range(len(conditions)), key=lambda i: pos.get(conditions[i].get("name"), 10 ** 9 + i))
    rank = {i: r for r, i in enumerate(ordered)}
    keyed: list[tuple[int, str]] = []
    for idx, c in enumerate(conditions):
        if not c.get("expr"):
            continue
        if c.get("kind") == "term":
            # plot 里直接写的一项(decompose 的 kind='term'),不是 def
            if c.get("enabled", True):
                keyed.append((rank[idx], f"({c['expr']})" if c.get("paren") else c["expr"]))
            continue
        kw = c.get("kind") if c.get("kind") in ("input", "rec") else "def"
        lines.append(f"{kw} {c['name']} = {c['expr']};")
        if c.get("enabled", True) and c.get("is_bool"):
            keyed.append((rank[idx], c["name"]))
    enabled = [t for _, t in sorted(keyed)]
    if plot_expr:
        lines.append(f"plot {plot_name} = {plot_expr};")
    elif term_host:
        # 条件宿主(decompose 的 term_host):按原结构写回,plot 仍只写宿主的名字
        lines.append(f"def {term_host} = " + (" and ".join(enabled) if enabled else "false") + ";")
        lines.append(f"plot {plot_name} = {term_host};")
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
    "volatility": "波动率", "turnover": "换手率",
    "ratio": "比率", "value": "价值", "rating": "评级", "target": "目标价",
    "gap": "跳空", "range": "区间", "performance": "涨幅", "perf": "涨幅",
}

# 后缀/限定(拼在括号里或直接接在后面)
_MOR_SUF = {
    "ttm": "TTM", "fq": "最近季", "fy": "最近年", "fh": "最近半年",
    "yoy": "同比", "qoq": "环比", "abs": "绝对值", "percent": "百分比",
    "pct": "百分比", "usd": "美元",
    # growth 总是附着在别的科目上(净利润**同比增长**),当头词会让
    # heads 变成 2 个而整体放弃 —— 它是后缀不是核心概念
    "growth": "增长",
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
    if name in _TECH_LABEL:      # 技术指标是专有名词,不参与拼装
        return _TECH_LABEL[name]
    cand = _candle_label(name)
    if cand:
        return cand
    if not re.fullmatch(r"[A-Za-z0-9_.+\-]+", name):
        return None

    mods: list[str] = []
    periods: list[str] = []
    heads: list[str] = []
    sufs: list[str] = []
    toks = [t for t in re.split(r"[_.]", name.lower()) if t]

    # 多词词干优先(取最长)。金融术语大多是固定搭配,
    # return_on_equity 逐词素拼是「回报权益」,整体才是「净资产收益率」。
    stem_at = stem_len = -1
    stem_cn = ""
    for i in range(len(toks)):
        for j in range(len(toks), i, -1):
            key = "_".join(toks[i:j])
            if key in _MOR_STEM and (j - i) > stem_len:
                stem_at, stem_len, stem_cn = i, j - i, _MOR_STEM[key]
                break
    if stem_at >= 0:
        heads.append(stem_cn)
        toks = toks[:stem_at] + toks[stem_at + stem_len:]

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
    # 同比 + 增长 合成一个词 —— 拆开写成「(同比·增长)」不像中文
    if "同比" in sufs and "增长" in sufs:
        sufs = ["同比增长"] + [x for x in sufs if x not in ("同比", "增长")]
    elif "环比" in sufs and "增长" in sufs:
        sufs = ["环比增长"] + [x for x in sufs if x not in ("环比", "增长")]
    if sufs:
        core += "(" + "·".join(sufs) + ")"
    return core


# ── 多词词干 ───────────────────────────────────────────────────
#
# 金融术语大多是固定搭配,逐词素拼必错(free + cash + flow 拼出「自由现金流量」,
# return + on + equity 拼出「回报权益」)。这里把它们**整体**给译名,
# 剩下的修饰/周期/后缀照旧由拼装逻辑处理 —— 于是
#   free_cash_flow_per_share_fq → 每股 + 自由现金流 + (最近季)
# 不用为每个周期变体单独列一条。
#
# 键是下划线连接的 token 序列,匹配时**取最长**。
_MOR_STEM = {
    # 利润表
    "total_revenue": "营业总收入", "net_revenue": "营业收入净额",
    "gross_profit": "毛利润", "oper_income": "营业利润",
    "net_income": "净利润", "cost_of_goods": "营业成本",
    "sell_gen_admin_exp_total": "销售管理费用",
    "research_and_dev": "研发支出", "income_from_cont_ops": "持续经营利润",
    "net_revenue_after_provision": "拨备后营业收入",
    "pre_tax_income": "税前利润", "after_tax_income": "税后利润",
    # 现金流
    "free_cash_flow": "自由现金流", "operating_cash_flow": "经营现金流",
    "cash_f_operating_activities": "经营活动现金流",
    "cash_f_investing_activities": "投资活动现金流",
    "cash_f_financing_activities": "筹资活动现金流",
    "capital_expenditures": "资本开支",
    "total_cash_dividends_paid": "现金分红总额",
    # 资产负债表
    "total_assets": "总资产", "total_debt": "总负债",
    "total_liabilities": "负债合计", "total_equity": "股东权益",
    "long_term_debt": "长期负债", "net_debt": "净负债",
    "total_current_assets": "流动资产合计",
    "total_current_liabilities": "流动负债合计",
    "cash_n_equivalents": "现金及等价物",
    "cash_n_short_term_invest": "现金及短期投资",
    "working_capital": "营运资金", "book_value_per_share": "每股净资产",
    "book_tangible_per_share": "每股有形净资产",
    # 比率
    "quick_ratio": "速动比率", "current_ratio": "流动比率",
    "debt_to_equity": "产权比率", "debt_to_asset": "资产负债率",
    "debt_to_revenue": "负债收入比",
    "long_term_debt_to_assets": "长期负债资产比",
    "return_on_equity": "净资产收益率", "return_on_assets": "总资产收益率",
    "return_on_invested_capital": "投入资本回报率",
    "return_on_capital_employed": "已动用资本回报率",
    "return_on_common_equity": "普通股权益回报率",
    "return_on_tang_equity": "有形权益回报率",
    "return_on_tang_assets": "有形资产回报率",
    "return_on_total_capital": "总资本回报率",
    "asset_turnover": "总资产周转率", "fixed_assets_turnover": "固定资产周转率",
    "invent_turnover": "存货周转率", "receivables_turnover": "应收账款周转率",
    "interst_cover": "利息保障倍数",
    "ebitda_interst_cover": "EBITDA利息保障倍数",
    "ebitda_less_capex_interst_cover": "EBITDA减资本开支利息保障倍数",
    "net_debt_to_ebitda": "净负债/EBITDA",
    "effective_interest_rate_on_debt": "债务实际利率",
    "research_and_dev_ratio": "研发费用率",
    "dividend_payout_ratio": "股息支付率",
    "cash_dividend_coverage_ratio": "现金股息保障倍数",
    "ebitda_margin": "EBITDA利润率", "free_cash_flow_margin": "自由现金流利润率",
    # 每股 / 股本 / 股东
    "earnings_per_share_diluted": "稀释每股收益",
    "earnings_per_share_basic": "基本每股收益",
    "eps_diluted_growth_percent": "稀释每股收益增长率",
    "dps_common_stock_prim_issue": "普通股每股股息",
    "number_of_shareholders": "股东户数", "number_of_employees": "员工人数",
    "dividends_yield": "股息率",
    # 估值 / 评分
    "price_sales": "市销率", "altman_z_score": "Altman Z 值",
    "piotroski_f_score": "Piotroski F 值", "graham_numbers": "格雷厄姆数",
    "ncavps_ratio": "净流动资产每股比",
    # 预测
    "earnings_per_share_forecast": "每股收益预测", "revenue_forecast": "营收预测",
}

# 技术指标 —— 这些是专有名词,不参与拼装,逐条给名
_TECH_LABEL = {
    "ADX": "ADX 趋向指标", "ADX+DI": "ADX +DI", "ADX-DI": "ADX -DI",
    "ADR": "平均日波幅ADR", "ADRP": "平均日波幅%",
    "ATR": "真实波幅ATR", "ATRP": "真实波幅%",
    "AO": "动量震荡AO", "BBPower": "牛熊力量",
    "CCI20": "CCI(20)", "ChaikinMoneyFlow": "蔡金资金流",
    "MoneyFlow": "资金流MFI", "Mom": "动量", "Mom_14": "动量(14)",
    "ROC": "变动率ROC", "UO": "终极震荡UO",
    "VWAP": "成交量加权均价VWAP", "VWMA": "成交量加权均线VWMA",
    "W.R": "威廉指标%R",
    "HullMA9": "赫尔均线(9)", "HullMA20": "赫尔均线(20)", "HullMA200": "赫尔均线(200)",
    "BB.upper": "布林带上轨", "BB.lower": "布林带下轨", "BB.basis": "布林带中轨",
    "BB.upper_50": "布林带上轨(50)", "BB.lower_50": "布林带下轨(50)",
    "BB.basis_50": "布林带中轨(50)",
    "KltChnl.upper": "肯特纳通道上轨", "KltChnl.lower": "肯特纳通道下轨",
    "KltChnl.basis": "肯特纳通道中轨",
    "DonchCh20.Upper": "唐奇安通道上轨", "DonchCh20.Lower": "唐奇安通道下轨",
    "DonchCh20.Middle": "唐奇安通道中轨",
    "Stoch.K": "随机指标K", "Stoch.D": "随机指标D",
    "Stoch.RSI.K": "随机RSI K", "Stoch.RSI.D": "随机RSI D",
    "Aroon.Up": "Aroon 上升", "Aroon.Down": "Aroon 下降",
    "P.SAR": "抛物线SAR",
    "Ichimoku.CLine": "一目均衡·转换线", "Ichimoku.BLine": "一目均衡·基准线",
    "Ichimoku.Lead1": "一目均衡·先行带A", "Ichimoku.Lead2": "一目均衡·先行带B",
    "Recommend.All": "综合技术评级", "Recommend.MA": "均线评级",
    "Recommend.Other": "震荡指标评级",
    "High.All": "历史最高", "Low.All": "历史最低",
    "High.All.Calc": "历史最高(计算)", "Low.All.Calc": "历史最低(计算)",
    "Perf.All": "上市以来涨幅", "Perf.5D": "近5日涨幅",
    "Value.Traded": "成交额",
    "AvgValue.Traded_10d": "10日均成交额", "AvgValue.Traded_30d": "30日均成交额",
    "AvgValue.Traded_60d": "60日均成交额", "AvgValue.Traded_90d": "90日均成交额",
}

# K 线形态 —— Candle.<形态>[.Bullish|.Bearish]
_CANDLE = {
    "3blackcrows": "三只乌鸦", "3whitesoldiers": "红三兵",
    "abandonedbaby": "弃婴", "darkcloudcover": "乌云盖顶",
    "doji": "十字星", "doji.dragonfly": "蜻蜓十字", "doji.gravestone": "墓碑十字",
    "dojistar": "十字星孕育", "downsidetasukigap": "下降跳空并列阴线",
    "upsidetasukigap": "上升跳空并列阳线",
    "engulfing": "吞没形态", "eveningdojistar": "黄昏十字星",
    "eveningstar": "黄昏之星", "morningdojistar": "早晨十字星",
    "morningstar": "早晨之星",
    "fallingthreemethods": "下降三法", "risingthreemethods": "上升三法",
    "fallingwindow": "向下跳空缺口", "risingwindow": "向上跳空缺口",
    "hammer": "锤子线", "hangingman": "上吊线", "invertedhammer": "倒锤子线",
    "shootingstar": "流星线",
    "harami": "孕线", "haramicross": "十字孕线",
    "kicking": "反冲形态", "longshadow.lower": "长下影线",
    "longshadow.upper": "长上影线",
    "marubozu.black": "光头光脚阴线", "marubozu.white": "光头光脚阳线",
    "onneck": "颈上线", "piercing": "刺透形态",
    "spinningtop.black": "纺锤线(阴)", "spinningtop.white": "纺锤线(阳)",
    "tristar": "三星形态", "tweezerbottom": "平底", "tweezertop": "平顶",
}
_DIR = {"bullish": "看涨", "bearish": "看跌"}


def _candle_label(name: str) -> str | None:
    """`Candle.Engulfing.Bullish` → 「K线·吞没形态(看涨)」。

    形态名后面可能跟 Bullish/Bearish,也可能没有(Doji、Hammer 这类不分方向)。
    形态本身认不出来就返回 None —— 不硬翻,免得把「三只乌鸦」译成别的东西。
    """
    if not name.startswith("Candle."):
        return None
    rest = name[len("Candle."):].lower()
    direction = ""
    for d, cn in _DIR.items():
        if rest.endswith("." + d):
            direction = cn
            rest = rest[: -(len(d) + 1)]
            break
    cn = _CANDLE.get(rest)
    if not cn:
        return None
    return "K线·" + cn + (f"({direction})" if direction else "")
