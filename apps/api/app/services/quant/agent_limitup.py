"""小鹿智能体 · 涨停后强势整理线(A 股 · 2026-09-17 用户要求新建的研究线)。

用户原话:「针对 A 股市场,扫描那些 4 个交易日前涨停,之后连续 3 天没有再涨停,而且这 3 天的收盘都没跌破
当天的开盘价的股。直接在扫描中的当天买入 1 万人民币,后一天收盘时全部卖出,回测一年的数据」。
立项时用 AskUserQuestion 问清的四个口径(都是用户选的,**改之前先问用户**):

1. 「没跌破」= **这 3 天的收盘都高于涨停那天的收盘价**(用户在「每天自己的开盘价 / 涨停日开盘价」之外自己写的)
2. 涨停按板块:主板 10%、创业板 / 科创板 20%、ST 5%。
   **立项当天核对后更正了 ST 的口径**(给用户的选项文案写的是「ST 5%」,那是错的):创业板 / 科创板的 ST 也是 20%;
   沪深主板 ST 从 2025-07-07 起由 5% 改为 10%(交易所修订交易规则)。一年回测全在这之后,实际只有 10% / 20% 两档,
   这里仍按日期区分,回测往 2025-07-07 之前延伸时不会判错
3. **每个信号独立买 1 万**,不限同时持仓(一天最多 66 个信号,本金 100 万永远不会不够)
4. 手续费按 A 股实际扣(commission.a_share_fee),而且**从现金里扣** —— 这条线的净值曲线是扣费后的

## 规则(L-xx)

买入(信号日 T 收盘后判,T 收盘价成交):
- L-01 涨停:T-3 收盘较 T-4 收盘涨幅 ≥ 涨停幅度 − 0.2 个百分点(主板 9.8% / 创业板科创板 19.8% / ST 4.8%)。
       留 0.2 是给涨停价四舍五入到分的误差:3.33 元涨停 3.66,实际涨幅 9.91%。
- L-02 没再涨停:T-2、T-1、T 三天每天涨幅都低于同一门槛
- L-03 守住:T-2、T-1、T 三天收盘都 **高于** T-3 收盘(严格大于,用户原话「高于」)
- L-07 5 日均线多头(2026-09-17 用户追加,v2):T 收盘 > MA5(T),且 MA5(T) > MA5(T-1)。
       口径是用户在 AskUserQuestion 里选的「收盘 > MA5 且 MA5 向上」(没选 5>10>20 那种排列)。
       MA5 向上等价于 T 收盘 > T-5 收盘,要 6 根日线,不够就算不出、不买。
       v1(没有 L-07)一年结果:3003 笔、每笔净 -40 元、合计 -120,631 元,见仓内 CLAUDE.md 研究台第 9 条
- L-08 只做创业板 / 科创板(2026-09-17 用户追加,v3):代码 300 / 301 / 688 / 689 开头。
       依据是按板块拆 v1:主板每笔 -40 ~ -47 元且显著,双创 -20 ~ -26 元不显著 —— 这是看过结果再挑,要换时间段验证。
       开关 `growth_only`(False = v2 口径)。v2 一年:2957 笔、每笔净 -37 元、合计 -108,754 元
- L-09 整理幅度不大(2026-09-17 用户追加,v4):T-2 ~ T 三天的最高价 − 最低价,除以 T-3 涨停日收盘,≤ 15%。
       门槛是用户让 Claude 按数据定的:双创 533 个信号按 13~18% 逐档看,15% 每笔 -0.05%(不设 -0.22%),
       但 14% 是 -0.45%、16% 是 -0.16% —— 15% 更像落在好点上,不是稳定规律;2025-06~09 检验段 13~18% 各档都好于不设(+1~+1.9%,只有几十笔)。
- L-10 不许三天都缩量(v4):T-2、T-1、T 三天成交量**每天都低于**涨停日成交量的不买。
       用户原本要「涨停日放量 或 三天缩量整理」,研究结果相反:三天每天都比涨停日缩量的 65 笔每笔 -1.50%(t -2.36),
       上下半年和检验段都为负,是这次最一致的信号;涨停日放量(量比 ≥ 2)每笔 -0.50%,比不设还差。用户看过数据后选了本口径。
       成交量是比值,A 股「手 / 股」单位不影响;高低价或成交量缺失 → 算不出、不买
       v3(没有 L-09 / L-10)一年:532 笔、每笔净 -22 元、合计 -11,559 元
- L-04 仓位:1 万元 ÷ T 收盘价,**取整股、不按 100 股一手取整** —— 按手取整的话 100 元以上的票买不了,
       或者被抬成一手后金额远超 1 万,每笔金额不一样,总盈亏就被高价股绑架了。这是替用户做的决定,研究口径优先
卖出:
- L-05 T+1 收盘全部卖出。**T+1 收盘跌停(收盘价 = 最低价且跌幅达到跌停门槛)视为卖不出,顺延到下一个交易日收盘**;
       T+1 停牌(没有日线)同样顺延。这是 A 股的交易约束,不是用户规则的一部分,卖出理由里写明
风控:
- L-06 手续费:佣金万 2.5(最低 5 元)+ 过户费 0.001% 买卖各一次,卖出另收印花税 0.05%,从现金里扣
- 不设护栏(单日熔断 / 连亏暂停):信号彼此独立,暂停开仓会让统计不再是「每个信号」

## 数据口径与已知局限

- 候选池 = 筛选器时间回溯跑 POOL_SCRIPT(宽松预筛:涨幅门槛 9.8%、不涨停门槛按 20%),
  **板块门槛由引擎按代码精确判**。脚本里写不出「按代码分板块」,所以不能把严格规则直接写进池子。
- ST 按**今天**的股票简称判断(名称里有 ST),历史上戴帽摘帽的时点拿不到,会有少量误判。
- 筛选器的 A 股股票池是「市值约 5000 万美元以上」且按今天的快照,有幸存者偏差;北交所没有历史日线,不在池里。
- 日线是复权后的,涨跌幅比例不受除权影响;成交按收盘价,没有滑点。
"""
from __future__ import annotations

from app.services.quant import agent_vcp as av
from app.services.quant import commission as cm

MARKET = "a"
CURRENCY = "CNY"
CURRENCY_SYMBOL = "¥"
CURRENCY_UNIT = "元"
BENCH_LABEL = "沪深300"
INITIAL_CAPITAL = 1_000_000.0

PARAMS = {
    "amount": 10_000.0,          # 每个信号买入金额(元)
    "hold_days": 1,              # 持有几个交易日后收盘卖出
    "lu_main": 0.098, "lu_growth": 0.198, "lu_st": 0.048,
    "st_10pct_from": "2025-07-07",   # 沪深主板 ST 涨跌幅从这天起 5% → 10%
    # 涨幅合理性上限 = 名义涨停 + 1 个百分点:超过就是日线有问题,不算涨停(见 entry_checks)
    "lu_cap_slack": 0.012,
    # 面板「护栏」卡要读这两个键;这条线不限持仓、不限单股占比(每笔 1 万 / 本金 100 万 = 1%)
    "max_holdings": 1000, "max_pos_pct": 0.01,
    "watch_pool_days": 1,
    "growth_only": True,             # L-08 只做创业板 / 科创板(v3)
    "amp_max": 15.0,                 # L-09 三天整理幅度上限(% of 涨停日收盘,v4);None = 不限
    "no_all_shrink": True,           # L-10 三天成交量每天都低于涨停日 → 不买(v4)
    # 买入口径:hold = 涨停后强势整理(L-01 ~ L-10);yin = 涨停 + 三根阴线(Y-01 ~ Y-03,agent_limitup_yin 用)
    "mode": "hold",
}
STOP_KEYS: tuple = ()
MIN_BARS = 5                         # 卖出只要 5 根;买入的 L-07 要 6 根,不够时 ma5 为 None、不买
ENTRY_RULE = "L-01"
WATCH_POOL_DAYS = 1
NO_GUARDS = True                     # 不设单日熔断 / 连亏暂停,面板护栏栏显示「不设」
EXEC_NOTE = "纸上交易 · 日线收盘价成交 · 手续费从现金里扣(净值是扣费后的)"
REBALANCE_SUFFIX = " · 次日收盘卖出(跌停 / 停牌顺延)"

POOL = "limitup"
POOL_LIMIT = 500
POOL_LABEL = "涨停后强势整理预筛池(T-3 涨幅 ≥ 9.8% · 之后三天每天涨幅 < 20% · 三天收盘都高于 T-3 收盘;板块涨停门槛由引擎精确判)"
POOL_SCRIPT = """# ===== 涨停后强势整理 · 宽松预筛(小鹿 · A 股研究线)=====
# 脚本里分不出板块,这里按最宽的门槛圈池:涨停按 9.8%、不涨停按创业板的 20%;
# 主板 10% / 创业板科创板 20% 由引擎按代码精确判。
# ⚠ 2025-07-07 前主板 ST 是 5% 涨停,这个池子圈不到 —— 回测往那之前延伸要先放宽成 0.048。
#   2026-09-17 第一次全年回填用的就是 0.048,结果 4 天命中 548~726 只,超过筛选器单次 500 只上限被截掉,才收紧
def up_t3  = (close[3] - close[4]) / close[4] >= 0.098;
def not_lu = (close[2] - close[3]) / close[3] < 0.198 and (close[1] - close[2]) / close[2] < 0.198 and (close - close[1]) / close[1] < 0.198;
def hold   = close[2] > close[3] and close[1] > close[3] and close > close[3];

plot scan = up_t3 and not_lu and hold;
"""

RULE_NAME = {"L-01": "涨停后强势整理买入", "L-05": "次日收盘卖出"}
RULE_PARAM_KEY: dict = {}          # 规则固定,不进优化器


def limit_of(code: str, name: str | None, p: dict = PARAMS, on: str | None = None) -> tuple[float, str]:
    """→ (涨停判定门槛, 板块说明)。on = 那根 K 线的日期(ISO),决定主板 ST 用 5% 还是 10%;不给按现行规则。
    创业板 / 科创板先判:那边 ST 也是 20%。北交所(4 / 8 / 92 开头)不在池里,这里按主板兜底不会被用到。"""
    if str(code).startswith(("300", "301", "688", "689")):
        return p["lu_growth"], "创业板 / 科创板 20%"
    if "ST" in (name or "").upper():
        if on is not None and str(on) < p["st_10pct_from"]:
            return p["lu_st"], "主板 ST 5%(2025-07-07 前)"
        return p["lu_main"], "主板 ST 10%"
    return p["lu_main"], "主板 10%"


def is_growth(code: str) -> bool:
    """创业板(300 / 301)或科创板(688 / 689)。和 limit_of 里判 20% 涨停的前缀是同一组。"""
    return str(code).startswith(("300", "301", "688", "689"))


def is_main(code: str) -> bool:
    """沪深主板:60 / 00 开头(600 / 601 / 603 / 605 · 000 / 001 / 002 / 003)。双创、北交所都不算。"""
    return str(code).startswith(("60", "00"))


def _rules_yin(p: dict) -> list[dict]:
    amt = f"{p['amount']:,.0f}"
    return [
        {"id": "Y-01", "kind": "buy", "condition": (f"涨停:4 个交易日前(T-3)收盘较前一天涨幅达到涨停 —— 主板 ≥ {p['lu_main'] * 100:.1f}%;"
                                                   f"主板 ST {p['st_10pct_from']} 起同主板,之前 ≥ {p['lu_st'] * 100:.1f}%")},
        {"id": "Y-02", "kind": "buy", "condition": "三根阴线:之后三天(T-2、T-1、T)每天收盘都低于当天开盘价;开盘价缺失算不出、不买"},
        {"id": "Y-03", "kind": "buy", "condition": "只做主板:代码 60 / 00 开头,创业板、科创板、北交所不买"},
        {"id": "L-04", "kind": "risk", "condition": (f"仓位:每个信号买入 {amt} 元(按 {amt} ÷ 收盘价取整股,不按 100 股一手取整),"
                                                    f"信号当天收盘价成交;不限同时持仓,不设熔断 / 连亏暂停")},
        {"id": "L-05", "kind": "sell", "condition": (f"卖出:买入后第 {p['hold_days']} 个交易日收盘全部卖出(收益率按那天收盘价算);"
                                                    "那天收盘跌停(收盘 = 最低且跌幅到跌停)或停牌卖不出,顺延到下一个交易日收盘")},
        {"id": "L-06", "kind": "risk", "condition": "手续费:佣金万 2.5(最低 5 元)+ 过户费 0.001% 买卖各一次,卖出另收印花税 0.05%;从现金里扣"},
    ]


def rules_for(p: dict = PARAMS) -> list[dict]:
    if p.get("mode") == "yin":
        return _rules_yin(p)
    amt = f"{p['amount']:,.0f}"
    return [
        {"id": "L-01", "kind": "buy", "condition": (f"涨停:4 个交易日前(T-3)收盘较前一天涨幅达到涨停 —— 主板 ≥ {p['lu_main'] * 100:.1f}%、"
                                                   f"创业板 / 科创板(含 ST)≥ {p['lu_growth'] * 100:.1f}%;主板 ST {p['st_10pct_from']} 起同主板,"
                                                   f"之前 ≥ {p['lu_st'] * 100:.1f}%")},
        {"id": "L-02", "kind": "buy", "condition": "没再涨停:之后三天(T-2、T-1、T)每天涨幅都没到同一门槛"},
        {"id": "L-03", "kind": "buy", "condition": "守住:这三天的收盘都高于涨停那天(T-3)的收盘价"},
        {"id": "L-07", "kind": "buy", "condition": ("5 日均线多头:信号当天收盘高于 5 日均线,且 5 日均线比前一天高" if p.get("require_ma5", True) else "不要求 5 日均线多头")},
        {"id": "L-08", "kind": "buy", "condition": ("只做创业板 / 科创板:代码 300 / 301 / 688 / 689 开头,主板不买" if p.get("growth_only")
                                                   else ("只做沪深主板" if p.get("main_only") else "板块不限(主板、创业板、科创板都做)"))},
        {"id": "L-09", "kind": "buy", "condition": (f"整理幅度不大:涨停后三天的最高价 − 最低价,不超过涨停日收盘的 {p['amp_max']:g}%"
                                                   if p.get("amp_max") is not None else "整理幅度不限")},
        {"id": "L-10", "kind": "buy", "condition": ("不许三天都缩量:涨停后三天成交量每天都低于涨停日的,不买" if p.get("no_all_shrink")
                                                   else "量能不限")},
        {"id": "L-04", "kind": "risk", "condition": (f"仓位:每个信号买入 {amt} 元(按 {amt} ÷ 收盘价取整股,不按 100 股一手取整),"
                                                    f"信号当天收盘价成交;不限同时持仓,不设熔断 / 连亏暂停")},
        {"id": "L-05", "kind": "sell", "condition": (f"卖出:买入后第 {p['hold_days']} 个交易日收盘全部卖出;"
                                                    "那天收盘跌停(收盘 = 最低且跌幅到跌停)或停牌卖不出,顺延到下一个交易日收盘")},
        {"id": "L-06", "kind": "risk", "condition": "手续费:佣金万 2.5(最低 5 元)+ 过户费 0.001% 买卖各一次,卖出另收印花税 0.05%;从现金里扣"},
    ]


RULES = rules_for(PARAMS)            # agent_run 的复盘 / 规则手册按 RULES 取规则种类(默认参数的文案)


def summary(p: dict = PARAMS) -> str:
    if p.get("mode") == "yin":
        return (f"A 股主板涨停 + 三根阴线:4 个交易日前涨停、之后连续三天收盘都低于开盘的票,信号当天收盘买入 {p['amount']:,.0f} 元,"
                f"第 {p['hold_days']} 个交易日收盘卖出。只做主板,手续费按 A 股实际扣。")
    return ((f"只做创业板 / 科创板。" if p.get("growth_only") else "") + f"A 股涨停后强势整理:4 个交易日前涨停、之后三天没再涨停且收盘都高于涨停日收盘、当天收盘站上向上的 5 日均线的票,"
            f"信号当天收盘买入 {p['amount']:,.0f} 元,第 {p['hold_days']} 个交易日收盘卖出。"
            "涨停按板块区分(主板 10% / 创业板科创板 20%;主板 ST 2025-07-07 前 5%),手续费按 A 股实际扣。")


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def indicators(bars: list[tuple], p: dict = PARAMS, bench: dict | None = None) -> dict | None:
    """bars = [(d, 收, 高, 低, 量)] 升序,最后一根是今天。最近 5 根收盘 + 今天的最低价;有第 6 根才算 MA5 两天。"""
    if len(bars) < MIN_BARS:
        return None
    c = [b[1] for b in bars[-5:]]            # c[0]=T-4 · c[1]=T-3 · c[2]=T-2 · c[3]=T-1 · c[4]=T
    if any(x is None or x <= 0 for x in c):
        return None
    # L-09 / L-10:涨停后三天的高低价与成交量(T-3 = bars[-4])。缺一个就 None,判定时算不出不买
    amp = all_shrink = None
    hs, ls = [b[2] for b in bars[-3:]], [b[3] for b in bars[-3:]]
    if all(x is not None for x in hs + ls):
        amp = (max(hs) - min(ls)) / c[1] * 100
    vs, vlu = [b[4] for b in bars[-3:]], bars[-4][4]
    if vlu is not None and vlu > 0 and all(x is not None and x == x for x in vs) and vlu == vlu:
        all_shrink = all(x < vlu for x in vs)
    # Y-02:涨停后三天是不是都收阴(收盘 < 开盘)。只有带开盘价的日线元组(第 6 个元素)才算得出
    yin3 = None
    if all(len(b) > 5 for b in bars[-3:]):
        os_ = [b[5] for b in bars[-3:]]
        if all(o is not None and o > 0 for o in os_):
            yin3 = all(b[1] < b[5] for b in bars[-3:])
    ma5 = ma5_prev = None
    if len(bars) >= 6 and bars[-6][1] is not None and bars[-6][1] > 0:
        ma5 = sum(c) / 5
        ma5_prev = (bars[-6][1] + sum(c[:4])) / 5
    return {"date": str(bars[-1][0]), "close": c[4], "low": bars[-1][3], "closes": c,
            "chg": [c[i] / c[i - 1] - 1 for i in range(1, 5)],     # chg[0] = T-3 涨幅 … chg[3] = T 涨幅
            "ma5": ma5, "ma5_prev": ma5_prev, "amp": amp, "all_shrink": all_shrink, "yin3": yin3,
            "opens": [b[5] for b in bars[-3:]] if all(len(b) > 5 for b in bars[-3:]) else None}


def entry_checks(ind: dict, code: str, name: str | None, p: dict = PARAMS) -> dict:
    lim, board = limit_of(code, name, p, ind.get("date"))
    c, chg = ind["closes"], ind["chg"]
    # 上限:主板一天涨不到 11%、双创涨不到 21%。2026-09-17 全年核对发现 screen_asof 的拆股修正会把少数 A 股
    # 前一根收盘改错,造出假涨停(600508 原始 9.43 → 8.86 跌 6%,修正后 7.42 → 8.86「涨 19.4%」被买入)
    cap = lim + p["lu_cap_slack"] + 0.002
    l01 = lim <= chg[0] <= cap
    if p.get("mode") == "yin":
        y02 = ind.get("yin3") is True
        y03 = is_main(code)
        return {"Y-01": l01, "Y-02": y02, "Y-03": y03, "ok": l01 and y02 and y03,
                "yin_na": ind.get("yin3") is None, "limit": lim, "board": board, "over_cap": chg[0] > cap, "cap": cap}
    l02 = all(x < lim for x in chg[1:])
    l03 = all(x > c[1] for x in c[2:])
    ma5, ma5p = ind.get("ma5"), ind.get("ma5_prev")
    l07 = not p.get("require_ma5", True) or (ma5 is not None and ma5p is not None and c[4] > ma5 and ma5 > ma5p)
    l08 = ((not p.get("growth_only")) or is_growth(code)) and (not p.get("main_only") or is_main(code))
    amp, shr = ind.get("amp"), ind.get("all_shrink")
    l09 = p.get("amp_max") is None or (amp is not None and amp <= p["amp_max"] + 1e-9)     # 1e-9:(13.8 − 12) / 12 算出 15.000000000000002
    l10 = (not p.get("no_all_shrink")) or shr is False
    return {"L-01": l01, "L-02": l02, "L-03": l03, "L-07": l07, "L-08": l08, "L-09": l09, "L-10": l10,
            "ok": l01 and l02 and l03 and l07 and l08 and l09 and l10,
            "ma5_na": ma5 is None or ma5p is None, "limit": lim, "board": board,
            "over_cap": chg[0] > cap, "cap": cap}


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


# ═══════════════════════════════════════════════════════════════
# 观察列表
# ═══════════════════════════════════════════════════════════════

def watch_item(code, name, ind, held, blocked_reason, score=None, p: dict = PARAMS) -> dict:
    it = {"symbol": code, "name": name, "score": score, "rule_id": "L-01", "rule_text": rules_for(p)[0]["condition"]}
    if ind is None:
        it.update({"price": None, "progress_pct": None, "gap": "当天没有日线或不足 5 根,判不了"})
        return it
    f = entry_checks(ind, code, name, p)
    it["price"] = round(ind["close"], 2)
    if p.get("mode") == "yin":
        return _watch_yin(it, ind, f, held, blocked_reason)
    keys = ("L-01", "L-02", "L-03", "L-07", "L-08", "L-09", "L-10")
    it["progress_pct"] = int(sum(1 for k in keys if f[k]) / len(keys) * 100)
    it["fails"] = [k for k in keys if not f[k]]
    c, chg = ind["closes"], ind["chg"]
    detail = (f"{f['board']}:T-3 涨 {_pct(chg[0])},之后三天 {' / '.join(_pct(x) for x in chg[1:])},"
              f"三天收盘 {' / '.join(f'{x:.2f}' for x in c[2:])} 对涨停日收盘 {c[1]:.2f}"
              + (f";5 日均线 {ind['ma5']:.2f}(前一天 {ind['ma5_prev']:.2f})" if not f["ma5_na"] else ";5 日均线算不出(日线不足 6 根)"))
    if held:
        it["gap"] = "已持仓 · 等卖出"
    elif f["ok"]:
        it["gap"] = "买入条件全满足 —— 今日收盘买入。" + detail
    else:
        why = []
        if f["over_cap"]:
            why.append(f"L-01 T-3 涨幅超过 {f['cap'] * 100:.1f}%,不是涨停(新股上市头几天没有涨跌幅限制,或日线有误)")
        elif not f["L-01"]:
            why.append(f"L-01 T-3 涨幅没到 {f['limit'] * 100:.1f}%(按{f['board']})")
        if not f["L-02"]:
            why.append("L-02 之后三天里又涨停了")
        if not f["L-03"]:
            why.append("L-03 有一天收盘没高于涨停日收盘")
        if not f["L-07"]:
            why.append("L-07 5 日均线算不出(日线不足 6 根)" if f["ma5_na"] else
                       ("L-07 收盘没站上 5 日均线" if c[4] <= ind["ma5"] else "L-07 5 日均线没有向上"))
        if not f["L-08"]:
            why.append("L-08 主板不做(只做创业板 / 科创板)")
        if not f["L-09"]:
            why.append("L-09 高低价缺失,整理幅度算不出" if ind.get("amp") is None else
                       f"L-09 三天整理幅度 {ind['amp']:.1f}% 超过 {p['amp_max']:g}%")
        if not f["L-10"]:
            why.append("L-10 成交量缺失,量能算不出" if ind.get("all_shrink") is None else "L-10 三天成交量每天都低于涨停日")
        it["gap"] = "不买:" + ";".join(why) + "。" + detail
    if blocked_reason:
        it["blocked"] = True
        it["blocked_reason"] = blocked_reason
    return it


def _yin_detail(ind: dict, f: dict) -> str:
    c, chg, os_ = ind["closes"], ind["chg"], ind.get("opens")
    days = ""
    if os_:
        days = " / ".join(("开 —" if o is None else f"开 {o:.2f}") + f" 收 {x:.2f}" for o, x in zip(os_, c[2:]))
    return f"{f['board']}:T-3 涨 {_pct(chg[0])};之后三天 " + (days or "没有开盘价")


def _watch_yin(it: dict, ind: dict, f: dict, held, blocked_reason) -> dict:
    keys = ("Y-01", "Y-02", "Y-03")
    it["rule_id"] = "Y-01"
    it["progress_pct"] = int(sum(1 for k in keys if f[k]) / len(keys) * 100)
    it["fails"] = [k for k in keys if not f[k]]
    detail = _yin_detail(ind, f)
    if held:
        it["gap"] = "已持仓 · 等卖出"
    elif f["ok"]:
        it["gap"] = "买入条件全满足 —— 今日收盘买入。" + detail
    else:
        why = []
        if f["over_cap"]:
            why.append(f"Y-01 T-3 涨幅超过 {f['cap'] * 100:.1f}%,不是涨停(新股上市头几天没有涨跌幅限制,或日线有误)")
        elif not f["Y-01"]:
            why.append(f"Y-01 T-3 涨幅没到 {f['limit'] * 100:.1f}%(按{f['board']})")
        if not f["Y-02"]:
            why.append("Y-02 开盘价缺失,阴线算不出" if f["yin_na"] else "Y-02 三天里有一天不是阴线")
        if not f["Y-03"]:
            why.append("Y-03 不是主板(只做主板)")
        it["gap"] = "不买:" + ";".join(why) + "。" + detail
    if blocked_reason:
        it["blocked"] = True
        it["blocked_reason"] = blocked_reason
    return it


# ═══════════════════════════════════════════════════════════════
# 一天的决策
# ═══════════════════════════════════════════════════════════════

def _fill(side, pos: av.Position, shares, price, rule, rationale, **extra) -> dict:
    d = {"side": side, "symbol": pos.code, "name": pos.name, "shares": int(shares), "price": round(price, 2),
         "rule_id": rule, "rule_name": RULE_NAME.get(rule, rule), "rationale": rationale,
         "entry_date": pos.entry_date, "level": pos.level}
    d.update(extra)
    return d


def manage_position(pos: av.Position, ind: dict, state: dict, p: dict = PARAMS, want_text: bool = True) -> list[dict]:
    pos.bars_held += 1
    if pos.bars_held < p["hold_days"]:
        return []
    px, ep = ind["close"], pos.entry_price
    lim, _board = limit_of(pos.code, pos.name, p, ind.get("date"))
    # 跌停封板卖不出:收盘就是最低价,且较上一根日线的跌幅达到跌停门槛(停牌复牌也按上一根比)
    if ind.get("low") is not None and px <= ind["low"] and ind["chg"][3] <= -lim:
        pos.extra["last_close"] = px
        pos.extra["deferred"] = int(pos.extra.get("deferred", 0)) + 1
        return []
    fee = cm.a_share_fee("sell", pos.size, px)
    gross = (px - pos.avg_cost) * pos.size
    state["cash"] += pos.size * px - fee
    state["closed_pnl"].append(gross - fee - float(pos.extra.get("buy_fee", 0.0)))
    n_def = int(pos.extra.get("deferred", 0))
    why = ""
    if want_text:
        why = (f"{pos.entry_date} 收盘 ¥{ep:.2f} 买入 {pos.size} 股,持有 {pos.bars_held} 个交易日,今收 ¥{px:.2f}"
               f"({(px / ep - 1) * 100:+.2f}%)—— 按 L-05 收盘卖出。"
               + (f"之前 {n_def} 天收盘跌停(或停牌)卖不出,顺延到今天。" if n_def else "")
               + f"卖出费用 ¥{fee:.2f}(佣金 + 过户费 + 印花税),买入费用 ¥{float(pos.extra.get('buy_fee', 0.0)):.2f}。")
    fill = _fill("sell", pos, pos.size, px, "L-05", why,
                 pnl_abs=round(gross, 2), pnl_pct=round((px / pos.avg_cost - 1) * 100, 2), hold_days=pos.bars_held)
    pos.size = 0
    state["closed"].append(pos)
    return [fill]


def try_entry(code, name, ind, state: dict, p: dict = PARAMS, want_text: bool = True, score=None):
    f = entry_checks(ind, code, name, p)
    if not f["ok"]:
        return None, None
    px = ind["close"]
    size = int(p["amount"] // px)
    if size <= 0:
        return None, f"信号成立,但股价 ¥{px:.2f} 超过每笔金额 {p['amount']:,.0f} 元,买不了 1 股"
    cost = size * px
    fee = cm.a_share_fee("buy", size, px)
    if cost + fee > state["cash"]:
        return None, f"信号成立,但现金只剩 ¥{state['cash']:,.0f},不够买入"
    state["cash"] -= cost + fee
    pos = av.Position(code=code, name=name or code, size=size, initial_size=size, entry_price=px,
                      entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0,
                      entry_rule=ENTRY_RULE, extra={"buy_fee": fee, "last_close": px})
    state["positions"].append(pos)
    extra = {"amount": round(cost, 2), "position_pct": round(cost / state["equity"] * 100, 2) if state["equity"] else None}
    if not want_text:
        return _fill("buy", pos, size, px, ENTRY_RULE, "", **extra), None
    c, chg = ind["closes"], ind["chg"]
    if p.get("mode") == "yin":
        rationale = (f"按{f['board']}判涨停(门槛 {f['limit'] * 100:.1f}%):T-3 收盘 ¥{c[1]:.2f},较前一天 {_pct(chg[0])}(Y-01 涨停);"
                     f"之后三天都是阴线(Y-02):" + " / ".join(f"开 ¥{o:.2f} 收 ¥{x:.2f}" for o, x in zip(ind["opens"], c[2:]))
                     + f";主板(Y-03)。今日收盘 ¥{px:.2f} 买入 {size} 股 = ¥{cost:,.2f}(L-04:{p['amount']:,.0f} 元 ÷ 收盘价取整股),"
                     f"买入费用 ¥{fee:.2f}。")
        fill = _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra)
        fill["rule_name"] = "涨停三阴买入"
        return fill, None
    rationale = (f"按{f['board']}判涨停(门槛 {f['limit'] * 100:.1f}%):T-3 收盘 ¥{c[1]:.2f},较前一天 {_pct(chg[0])}(L-01 涨停);"
                 f"之后三天涨幅 {' / '.join(_pct(x) for x in chg[1:])},都没到门槛(L-02);"
                 f"三天收盘 {' / '.join(f'¥{x:.2f}' for x in c[2:])} 都高于涨停日收盘 ¥{c[1]:.2f}(L-03);"
                 f"收盘 ¥{px:.2f} 高于 5 日均线 ¥{ind['ma5']:.2f},5 日均线较前一天 ¥{ind['ma5_prev']:.2f} 向上(L-07)。"
                 + (f"三天整理幅度 {ind['amp']:.1f}%(L-09 ≤ {p['amp_max']:g}%)。" if ind.get("amp") is not None and p.get("amp_max") is not None else "")
                 + ("三天里至少有一天成交量不低于涨停日(L-10)。" if ind.get("all_shrink") is False and p.get("no_all_shrink") else "")
                 + f"今日收盘 ¥{px:.2f} 买入 {size} 股 = ¥{cost:,.2f}(L-04:{p['amount']:,.0f} 元 ÷ 收盘价取整股),"
                 f"买入费用 ¥{fee:.2f}。")
    return _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra), None


def run_day(date_iso: str, positions, cash: float, bars_of, watch, prev_equity, consec_losses: int,
            p: dict = PARAMS, g: dict = av.GUARDS, ind_of=None, want_text: bool = True) -> dict:
    """接口与 agent_donchian 相同。g(护栏)不用:信号彼此独立,见模块开头。"""
    state = {"date": date_iso, "cash": cash, "positions": list(positions), "closed": [], "closed_pnl": [], "equity": None}
    if ind_of is None:
        def ind_of(code):
            return indicators(bars_of(code) or [], p)
    ind_cache = {pos.code: ind_of(pos.code) for pos in state["positions"]}

    def mark(pos):
        ind = ind_cache.get(pos.code)
        # 当天没日线(停牌)按上一次收盘估值,不按成本 —— 成本会把停牌前的涨跌抹掉
        return ind["close"] if ind else float(pos.extra.get("last_close") or pos.avg_cost)

    state["equity"] = cash + sum(pos.size * mark(pos) for pos in state["positions"])
    fills = []
    for pos in list(state["positions"]):
        ind = ind_cache[pos.code]
        if ind is None:                     # 停牌:当天没有日线,卖不出,顺延
            pos.bars_held += 1
            pos.extra["deferred"] = int(pos.extra.get("deferred", 0)) + 1
            continue
        fills += manage_position(pos, ind, state, p, want_text)
        if pos.size > 0:
            pos.extra["last_close"] = ind["close"]
    state["positions"] = [x for x in state["positions"] if x.size > 0]
    held = {x.code for x in state["positions"]}
    watch_items = []
    for code, name, score in watch:
        ind = ind_cache[code] if code in ind_cache else ind_of(code)
        ind_cache[code] = ind
        blocked = None
        bought = False
        if code not in held and ind is not None:
            f, blocked = try_entry(code, name, ind, state, p, want_text, score)
            if f:
                fills.append(f)
                held.add(code)
                bought = True
        if want_text:
            watch_items.append(watch_item(code, name, ind, code in held and not bought, blocked, score, p))
    for pnl in state["closed_pnl"]:
        consec_losses = consec_losses + 1 if pnl < 0 else 0
    equity = state["cash"] + sum(pos.size * mark(pos) for pos in state["positions"])
    watch_items.sort(key=lambda x: (bool(x.get("blocked")), -(x.get("progress_pct") or 0)))
    return {"fills": fills, "positions": state["positions"], "cash": state["cash"], "equity": equity,
            "watch_items": watch_items, "halt_reason": None, "consec_losses": consec_losses, "closed": state["closed"]}
