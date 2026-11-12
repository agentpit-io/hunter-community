"""小鹿智能体 · 每日流水线 + 面板数据(`GET /api/quant/agent/dashboard?branch=`)。

2026-09-12 接入:策略 = 「VCP 波段交易」(agent_vcp,用户给的 Backtrader 策略移植),
观察列表 = 筛选器内置示例「VCP 波段收缩」当天的结果(近 10 个交易日并集),行情 = 每晚落库的全市场日线
(rs_daily,经 screen_asof 拆股修正与缓存)。纸上交易,美股。

## 三个方向并行(用户 2026-09-12 要求,agent_opt.BRANCHES)

base 基准 v1 规则固定 · buy 固定卖出只调买入 · sell 固定买入只调卖出(止损只许收紧)· c Claude 自设计的三段式(agent_vcp3)。
每个方向各有自己的现金 / 持仓 / 成交 / 净值 / 版本;观察列表(筛选结果)所有方向共用一份(agent_watch)。
引擎按 agent_opt.BRANCHES[branch]["engine"] 选,两个引擎接口相同。

## 一天怎么跑(收盘后,美股在上海时间 06:30 由 rs_history_nightly.sh 接着触发)

1. 收集数据:日线整窗 + 拆股修正(screen_asof.get_store);今天的筛选结果落 agent_watch
2. 扫描与执行:每个方向按自己的参数跑 agent_vcp.run_day(出场 / 加仓 / 开仓,收盘价成交)
3. 复盘总结:规则模板拼的成长总结(数字全部来自成交与日线;**不走 LLM**)
4. 调整策略:agent_opt.step —— 一次只动一个参数、两段都要赢、观察期、冷却期、止损只收紧(防过拟合的门槛见那边)

同一天每个方向只跑一次(agent_day 唯一索引 branch+trade_date),重复触发直接返回已有结果。

## 回填

`python -m app.services.quant.agent_run backfill --from 2026-01-02 --yes`:逐个交易日跑到最新,和当天真跑同一段代码。
优化器的模拟用 agent_sim 的指标缓存,回填过程里缓存在内存中逐日增量。

## 字段口径(契约 docs/agent-dashboard-contract.md)

· 每笔卖出成交算一「笔交易」,盈亏按均价;胜率 = 盈利笔数 ÷ 卖出笔数;买入规则 R-04 的「触发后胜率」
  按整个持仓周期(同一 code + entry_date 的所有卖出加总)算
· 基准 = 标普500(rs_daily 里的 us.INX);组合夏普 = 日收益均值 / 标准差 × √252,无风险利率取 0,
  样本 < 20 天给 null;个股夏普持有 < 10 个交易日给 null
· 「算不出」一律 null,不给 0
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import json
import logging
import math
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone

from app.services.quant import agent_vcp as av, agent_opt as ao, agent_sim, commission as cm
from app.services.quant import agent_store
from app.services.market_time import market_tz

log = logging.getLogger(__name__)

MARKET = "us"
PRESET = "vcp_range"
STRATEGY_NAME = "VCP 波段交易"
BENCH_LABEL = "标普500"
BRANCHES = ao.BRANCH_ORDER

_DDL = """
CREATE TABLE IF NOT EXISTS agent_meta (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_watch (
    trade_date DATE PRIMARY KEY,
    items      JSONB NOT NULL,
    info       JSONB
);
CREATE TABLE IF NOT EXISTS agent_watch_pool (
    pool       TEXT NOT NULL,
    trade_date DATE NOT NULL,
    items      JSONB NOT NULL,
    info       JSONB,
    PRIMARY KEY (pool, trade_date)
);
CREATE TABLE IF NOT EXISTS agent_day (
    trade_date    DATE NOT NULL,
    equity        DOUBLE PRECISION NOT NULL,
    cash          DOUBLE PRECISION NOT NULL,
    bench_close   DOUBLE PRECISION,
    holdings_n    INT,
    watchlist     JSONB,
    pipeline      JSONB,
    lesson        JSONB,
    halt_reason   TEXT,
    consec_losses INT NOT NULL DEFAULT 0,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE agent_day ADD COLUMN IF NOT EXISTS branch TEXT NOT NULL DEFAULT 'base';
ALTER TABLE agent_day ADD COLUMN IF NOT EXISTS opt JSONB;
ALTER TABLE agent_day DROP CONSTRAINT IF EXISTS agent_day_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS agent_day_uq ON agent_day (branch, trade_date);
CREATE TABLE IF NOT EXISTS agent_trade (
    id           SERIAL PRIMARY KEY,
    trade_date   DATE NOT NULL,
    seq          INT  NOT NULL,
    side         TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT,
    shares       INT  NOT NULL,
    price        DOUBLE PRECISION NOT NULL,
    amount       DOUBLE PRECISION,
    position_pct DOUBLE PRECISION,
    pnl_abs      DOUBLE PRECISION,
    pnl_pct      DOUBLE PRECISION,
    hold_days    INT,
    rule_id      TEXT,
    rule_name    TEXT,
    rationale    TEXT,
    entry_date   DATE,
    level        INT,
    followup     TEXT
);
ALTER TABLE agent_trade ADD COLUMN IF NOT EXISTS branch TEXT NOT NULL DEFAULT 'base';
ALTER TABLE agent_trade ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE agent_trade ADD COLUMN IF NOT EXISTS grade_detail TEXT;
CREATE INDEX IF NOT EXISTS agent_trade_date_idx ON agent_trade (branch, trade_date);
CREATE TABLE IF NOT EXISTS agent_position (
    code         TEXT NOT NULL,
    name         TEXT,
    size         INT NOT NULL,
    initial_size INT NOT NULL,
    entry_price  DOUBLE PRECISION NOT NULL,
    entry_date   DATE NOT NULL,
    avg_cost     DOUBLE PRECISION NOT NULL,
    highest      DOUBLE PRECISION NOT NULL,
    level        INT NOT NULL,
    bars_held    INT NOT NULL DEFAULT 0,
    entry_rule   TEXT,
    last_price   DOUBLE PRECISION,
    bench_pct    DOUBLE PRECISION,
    sharpe       DOUBLE PRECISION,
    sharpe_na    TEXT
);
ALTER TABLE agent_position ADD COLUMN IF NOT EXISTS branch TEXT NOT NULL DEFAULT 'base';
ALTER TABLE agent_position ADD COLUMN IF NOT EXISTS stop DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE agent_position ADD COLUMN IF NOT EXISTS risk DOUBLE PRECISION NOT NULL DEFAULT 0;
-- ALTER 必须排在 CREATE 之后:原来 extra 这句在建表前面,线上库表早就有所以不报错,
-- 全新安装整段 DDL 失败,小鹿智能体所有接口 500(2026-09-17 本地 docker 实测)
ALTER TABLE agent_position ADD COLUMN IF NOT EXISTS extra TEXT;
ALTER TABLE agent_position DROP CONSTRAINT IF EXISTS agent_position_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS agent_position_uq ON agent_position (branch, code);
"""


_ddl_done = False


def _conn():
    """连库;DDL **每个进程只跑一次**(和 rs_history.load_stats 同一套路)。

    2026-09-12 事故:原来每次连库都跑 _DDL。ALTER / DROP CONSTRAINT 即便是 IF EXISTS 的空操作也要拿
    ACCESS EXCLUSIVE 锁;一个诊断脚本的连接停在「idle in transaction」(psycopg2 第一次 SELECT 就开事务)
    拿着 agent_day 的共享锁,dashboard() 的第二个连接跑 DDL 在它后面排队,再后面所有读 agent_day 的都排在
    DDL 后面 —— 面板接口和一条 count(*) 一起卡了 6 分钟,pg_terminate_backend 才解开。
    """
    global _ddl_done
    from app.services.database import get_conn
    c = get_conn()
    if not _ddl_done:
        cur = c.cursor()
        cur.execute(_DDL)
        c.commit()
        cur.close()
        _ddl_done = True
    return c


def _meta_get(cur, key, default=None):
    cur.execute("SELECT value FROM agent_meta WHERE key=%s", (key,))
    r = cur.fetchone()
    return r[0] if r else default


def _meta_set(cur, key, value):
    cur.execute("INSERT INTO agent_meta (key, value, updated_at) VALUES (%s, %s, now()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, json.dumps(value)))


def _branch_state(cur, branch: str) -> dict:
    st = _meta_get(cur, f"branch:{branch}")
    if not st:
        st = {"params": dict(ao.engine_of(branch).PARAMS), "version": 1, "versions": [], "observing": None,
              "cooldown_days_left": 0, "days_since_eval": 0}
    return st


# ═══════════════════════════════════════════════════════════════
# 数据
# ═══════════════════════════════════════════════════════════════

def _snapshot(market: str = MARKET):
    """今天的快照:拆股锚点 + 排名池两列 + 名字。→ (rows, perf)"""
    from app.services.quant import screen_source, rs_history, screen_asof
    cols = [x for x in screen_asof.STATIC_COLS if x not in screen_source.ALWAYS_COLS]
    cols += list(rs_history._PERF_COLS) + ["exchange", "market_cap_basic"]
    rows, _ = screen_source.fetch_rows(market, cols)
    return rows, {r["_code"]: r for r in rows}


def _pool_of(eng_name: str) -> str:
    """引擎用哪个池:默认「VCP 波段收缩」(PRESET);引擎声明了 POOL 就用自己的(方向 A 的 SEPA 池)。"""
    return getattr(ao.ENGINES[eng_name], "POOL", PRESET)


def _pool_label(pool: str) -> str:
    if pool == PRESET:
        return "VCP 波段收缩"
    for eng in ao.ENGINES.values():
        if getattr(eng, "POOL", None) == pool:
            return getattr(eng, "POOL_LABEL", pool)
    return pool


def _pool_script(pool: str) -> str:
    from app.services.quant import screen_source
    if pool == PRESET:
        return screen_source.preset(PRESET)["script"]
    for eng in ao.ENGINES.values():
        if getattr(eng, "POOL", None) == pool:
            return eng.POOL_SCRIPT
    raise KeyError(f"没有这个池:{pool}")


def _pool_limit(pool: str) -> int:
    for eng in ao.ENGINES.values():
        if getattr(eng, "POOL", None) == pool:
            return int(getattr(eng, "POOL_LIMIT", 500))
    return 500


def _pool_days(eng_name: str) -> int:
    """观察池并几天的筛选结果:默认 10 天(VCP 筛出来的是还没突破的票);唐奇安只用当天(突破是当天的事)。"""
    return int(getattr(ao.ENGINES[eng_name], "WATCH_POOL_DAYS", av.PARAMS["watch_pool_days"]))


def _pool_market(pool: str) -> str:
    """池子属于哪个市场:声明了这个 POOL 的引擎的 MARKET(A 股线 2026-09-17),默认美股。"""
    for eng in ao.ENGINES.values():
        if getattr(eng, "POOL", None) == pool:
            return getattr(eng, "MARKET", MARKET)
    return MARKET


def _screen(d: date, pool: str = PRESET) -> tuple[list, dict]:
    """回溯到 d 跑池子的筛选脚本 → [[code, name, rs_rating]], 摘要。"""
    from app.services.quant import screen_source
    r = screen_source.run_script(_pool_script(pool), _pool_market(pool), _pool_limit(pool), "rs_rating", True, d)
    items = [[x["code"], x.get("name") or x["code"], x["fields"].get("rs_rating")] for x in r["picks"]]
    if r["matched"] > len(items):
        # 命中比上限多 = 有票被截掉了,不报错的话回测会静默少买
        log.warning("[agent] %s 池 %s 命中 %d 只,超过上限 %d,多出的没进观察列表", d, pool, r["matched"], len(items))
    gate = next((w for w in r["warnings"] if "门槛" in w), None)
    # 命中票当天选股器算出的整行字段:落 screen_hit_field(agent_store),以后分析 / 新规则直接查,不再回溯
    hits = [(x["code"], x["fields"]) for x in r["picks"]]
    return items, {"matched": r["matched"], "scanned": r["scanned"], "skipped": r["skipped_incomplete"],
                   "as_of": r["as_of"], "gate": gate, "_hits": hits}


def _stock_sharpe(bars: list[tuple], since: date) -> tuple[float | None, str | None]:
    closes = [b[1] for b in bars if b[0] >= since]
    if len(closes) < 11:
        return None, f"持有 {max(len(closes) - 1, 0)} 个交易日,不足 10 日,样本太小"
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    sd = statistics.pstdev(rets)
    if sd == 0:
        return None, "持有期价格没有波动"
    return round(statistics.mean(rets) / sd * math.sqrt(252), 2), None


class Ctx:
    """一次运行(当天 / 回填)里跨日期复用的东西:快照、日线缓存、筛选结果、指标缓存。"""

    def __init__(self, market: str = MARKET):
        from app.services.quant import screen_asof
        # 一个 Ctx 只装一个市场的日线(2026-09-17 A 股线起)。run_date 只跑这个市场的方向
        self.market = market
        # 引擎声明 WITH_OPEN(涨停三阴线要判阴线)→ 这个市场的日线元组带第 6 个元素开盘价。
        # 其余引擎只按下标读前 5 个,带不带都一样
        self.with_open = any(getattr(e, "WITH_OPEN", False) and getattr(e, "MARKET", MARKET) == market
                             for e in ao.ENGINES.values())
        self.rows, self.perf = _snapshot(market)
        self.snap = {r["_code"]: r for r in self.rows}
        self.sectors = {c: r.get("sector") for c, r in self.snap.items()}      # 方向 A 的「同板块 ≤2」用
        self.store = screen_asof.get_store(market, self.perf)
        self.screen_of: dict = {}          # date → [[code, name, score]](默认池「VCP 波段收缩」)
        self.screens: dict = {PRESET: self.screen_of}       # 池 → {date → items};方向 A 的 SEPA 池另存
        self.cache: dict = {k: {} for k in ao.ENGINES}      # 引擎 → {(code, date): 指标}
        self.cache_dates: dict = {}        # 引擎 → 已算过的日期
        self.seen: dict = {}               # 引擎 → 已整段算过的代码
        self.earn = None                   # (代码 → 财报日列表, 拉取成功的日子);第一次用到时读库(突破买入 v14)
        self.bars_idx: dict = {}           # 代码 → (整段日线, 日期 → 下标);ind_at 按需算指标用
        self.ind_store: dict = {}          # (引擎, 日期) → {代码: 上一轮落库的压缩指标}(agent_store.agent_ind_cache)
        self.ind_new: dict = {}            # (引擎, 日期) → [(代码, 这一轮新算的指标)],当天跑完写库
        self.ind_stat = {"reused": 0, "computed": 0, "stale": 0}

    def earnings_view(self, d: date, trade_days: list[date]):
        from app.services.quant import earnings_dates as ed
        if self.earn is None:
            try:
                self.earn = ed.load()
            except Exception:                                 # noqa: BLE001
                # 读不到就当一天都没拉到:引擎按「日历缺」不买并写明原因,不当成「没有财报」
                log.exception("[agent] 财报日历读库失败")
                self.earn = ({}, set())
        return ed.EarningsView(d, self.earn[0], self.earn[1], trade_days)

    def bars_of(self, code, upto: date | None = None):
        from app.services.quant import screen_asof
        return screen_asof.bars_upto(self.store, code, upto or self.store["last"], self.with_open)

    def load_screens(self, cur):
        cur.execute("SELECT trade_date, items FROM agent_watch ORDER BY trade_date")
        for td, items in cur.fetchall():
            self.screen_of[td] = items
        cur.execute("SELECT pool, trade_date, items FROM agent_watch_pool ORDER BY trade_date")
        for pool, td, items in cur.fetchall():
            self.screens.setdefault(pool, {})[td] = items

    def ind_at(self, eng_name: str, code: str, d: date):
        """按需算一只票一天的指标,记进同一份缓存(2026-09-15)。

        原来 ensure_cache 给每只票从首次进池起的**每一天**都算 —— 进过一次池的票之后天天算,绝大多数用不上。
        v14 一年实测:回测步骤合计 413 秒,而当天真用到的(观察列表 + 持仓)只是其中一小部分。规则固定、
        不走优化器的方向改成用到才算;可调参方向的优化器 / 模拟器直接读整段缓存,仍走 ensure_cache 提前算。
        `__` 开头的键(市场 / 板块 / 财报视图)由 ensure_cache 提前放好,这里不算。"""
        cache = self.cache[eng_name]
        k = (code, d)
        if k in cache:
            return cache[k]
        if code.startswith("__"):
            return None
        bi = self.bars_idx.get(code)
        if bi is None:
            bars = self.bars_of(code) or []
            bi = self.bars_idx[code] = (bars, {b[0]: i for i, b in enumerate(bars)})
        bars, idx = bi
        i = idx.get(d)
        # 先查库里上一轮存下的(agent_store.agent_ind_cache);收盘价对不上现在的日线(复权基准变了)就重算
        stored = self.ind_store.get((eng_name, d))
        blob = stored.get(code) if stored is not None and i is not None else None
        if blob is not None:
            v = agent_store.undump(blob)
            if agent_store.valid(v, bars[i][1]):
                cache[k] = v
                self.ind_stat["reused"] += 1
                return v
            self.ind_stat["stale"] += 1
        v = ao.ENGINES[eng_name].indicators(bars[:i + 1], bench=self.store["bench"]) if i is not None else None
        cache[k] = v
        self.ind_stat["computed"] += 1
        if stored is not None and v is not None:
            self.ind_new.setdefault((eng_name, d), []).append((code, v))
        return v

    def preload_ind(self, cur, eng_name: str, d: date) -> None:
        """这一天、这个引擎上一轮存下的指标先整批读进来(只读压缩数据,用到哪只解哪只)。引擎没有 ind_version 就不存不读。"""
        fn = getattr(ao.ENGINES[eng_name], "ind_version", None)
        if fn is None or (eng_name, d) in self.ind_store:
            return
        agent_store.ensure_tables()
        self.ind_store[(eng_name, d)] = agent_store.load_day(cur, eng_name, fn(), d)

    def flush_ind(self, cur, d: date) -> int:
        """把这一天新算的指标写库,读进来的压缩数据用完就丢(回填三年时别一直攒在内存里)。"""
        n = 0
        for (en, dd) in [x for x in self.ind_new if x[1] == d]:
            n += agent_store.save_inds(cur, en, ao.ENGINES[en].ind_version(), dd, self.ind_new.pop((en, dd)))
        for x in [x for x in self.ind_store if x[1] == d]:
            self.ind_store.pop(x)
        return n

    def ensure_cache(self, dates: list[date], pool: dict, engine_names: list[str] | None = None, per_code: bool = True):
        """指标缓存按 (code, date) 增量补:每只票从首次进池那天起到最后一天。只算 engine_names 这几个引擎(各池各算)。
        per_code=False:不提前逐票算(用 ind_at 按需算),只放市场 / 板块 / 财报视图这些按日的键。"""
        first: dict = {}
        if per_code:
            for d in dates:
                for code, _n, _s, _since in pool.get(d, []):
                    first.setdefault(code, d)
        bb = self.store.get("bench_bars") or []
        bdates = [b[0] for b in bb]
        for name in (engine_names or list(ao.ENGINES)):
            eng = ao.ENGINES[name]
            cache = self.cache[name]
            seen = self.seen.setdefault(name, set())
            done = self.cache_dates.setdefault(name, set())
            new_dates = [d for d in dates if d not in done]
            for code, d0 in first.items():
                scan = dates if code not in seen else new_dates
                need = [d for d in scan if d >= d0 and (code, d) not in cache]
                if not need:
                    continue
                bars = self.bars_of(code)
                idx = {b[0]: i for i, b in enumerate(bars)}
                for d in need:
                    i = idx.get(d)
                    cache[(code, d)] = eng.indicators(bars[:i + 1], bench=self.store["bench"]) if i is not None else None
                seen.add(code)
            # 方向 A(agent_vcp4)要按日的市场状态与板块表:放在同一份缓存里,模拟器和实盘拿的是同一个东西
            mk = getattr(eng, "MARKET_KEY", None)
            if mk:
                for d in new_dates:
                    if (mk, d) in cache:
                        continue
                    k = bisect_right(bdates, d)
                    cache[(mk, d)] = eng.market_regime(bb[:k])
                    cache[(eng.SECTORS_KEY, d)] = self.sectors
            # 突破买入 v14:按日的财报日视图(P-23 / P-24),同样放进指标缓存
            ek = getattr(eng, "EARNINGS_KEY", None)
            if ek:
                for d in new_dates:
                    if (ek, d) not in cache:
                        cache[(ek, d)] = self.earnings_view(d, bdates)
            done.update(dates)


# ═══════════════════════════════════════════════════════════════
# 跑一天
# ═══════════════════════════════════════════════════════════════

def _load_positions(cur, branch: str) -> list[av.Position]:
    cur.execute("SELECT code, name, size, initial_size, entry_price, entry_date, avg_cost, highest, level, "
                "bars_held, entry_rule, stop, risk, extra FROM agent_position WHERE branch=%s ORDER BY entry_date, code", (branch,))
    return [av.Position(code=r[0], name=r[1], size=r[2], initial_size=r[3], entry_price=r[4],
                        entry_date=str(r[5]), avg_cost=r[6], highest=r[7], level=r[8], bars_held=r[9],
                        entry_rule=r[10] or "R-04", stop=r[11] or 0.0, risk=r[12] or 0.0,
                        extra=(json.loads(r[13]) if r[13] else {})) for r in cur.fetchall()]


def run_date(d: date, ctx: Ctx | None = None, only: list[str] | None = None, market: str | None = None) -> dict:
    """跑 d 这一天(收盘后),**只跑这天还没跑过的方向**。全都跑过 → 直接返回。

    2026-09-13 研究台加新方向时改的:原来按「这天的行数够不够方向数」判断,够不上就把全部方向的
    池子和指标缓存都算一遍(已跑过的方向只是在落库前跳过)。给新方向回填 174 天时,VCP 那几个引擎的
    指标(Volume Profile / 周线 MACD)会被白算 174 遍。现在池子、缓存、循环都只针对 todo 里的方向。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT branch FROM agent_day WHERE trade_date=%s", (d,))
    done_b = {r[0] for r in cur.fetchall()}
    # only:研究线回填只跑那条线的方向。2026-09-13 突破买入线要从 2025-09-12 回测,比其他方向的起点
    # (2026-01-02)早四个月 —— 不限定的话 VCP 那几个方向会在 2025 年那些天被跑出行来,对照组就被污染了
    # 一次只跑一个市场的方向:日线、基准、交易日历都按市场分开(A 股线 2026-09-17)
    market = ctx.market if ctx is not None else (market or (ao.market_of(only[0]) if only else MARKET))
    todo = [b for b in BRANCHES if b not in done_b and (only is None or b in only) and ao.market_of(b) == market]
    if not todo:
        cur.close()
        conn.close()
        return {"date": str(d), "ran": False, "reason": "这天已经跑过"}
    t0 = time.time()
    ctx = ctx or Ctx(market)
    if not ctx.screen_of:
        ctx.load_screens(cur)
    bench = ctx.store["bench"]
    if d not in bench:
        raise RuntimeError(f"{d} 不是交易日或日线还没到那天(基准最新 {ctx.store['last']})")
    collect = {"key": "collect", "name": "收集数据", "status": "ok", "at": _hm(),
               "duration_ms": int((time.time() - t0) * 1000),
               "summary": f"全市场日线 {len(ctx.store['codes'])} 只(拆股已核对)· 基准截至 {ctx.store['last']} · 回溯日 {d}"}

    # 今天的筛选结果:按池算(默认池三个方向共用;方向 A 用自己的 SEPA 池)
    t1 = time.time()
    needed: dict = {}                       # 池 → 用它的引擎(只算今天还没跑的方向)
    for b in todo:
        en = ao.BRANCHES[b]["engine"]
        needed.setdefault(_pool_of(en), []).append(en)
    items_of, ws_of, ok_of = {}, {}, {}
    for pool_key in needed:
        screens = ctx.screens.setdefault(pool_key, {})
        if d in screens:
            items, ws, scan_ok = screens[d], {"matched": len(screens[d]), "cached": True}, True
        else:
            try:
                items, ws = _screen(d, pool_key)
                scan_ok = True
            except Exception as e:                                    # noqa: BLE001
                log.exception("[agent] %s 观察列表扫描失败(池 %s)", d, pool_key)
                items, ws, scan_ok = [], {"error": str(e)[:200]}, False
            # 命中票的整行字段单独落 screen_hit_field;先从 ws 拿出来,别塞进 info(几百行、还可能有 NaN,jsonb 不收)
            hits = ws.pop("_hits", None)
            if hits:
                agent_store.ensure_tables()
                cur.execute("SAVEPOINT hit_fields")       # 失败只撤这一步,不回滚当天整条事务
                try:
                    agent_store.save_hits(cur, pool_key, d, hits)
                    cur.execute("RELEASE SAVEPOINT hit_fields")
                except Exception:                                     # noqa: BLE001
                    log.exception("[agent] %s 命中字段落库失败(池 %s),不影响当天运行", d, pool_key)
                    cur.execute("ROLLBACK TO SAVEPOINT hit_fields")
            if pool_key == PRESET:
                cur.execute("INSERT INTO agent_watch (trade_date, items, info) VALUES (%s, %s, %s) "
                            "ON CONFLICT (trade_date) DO UPDATE SET items=EXCLUDED.items, info=EXCLUDED.info",
                            (d, json.dumps(items), json.dumps(ws, default=str)))
            else:
                cur.execute("INSERT INTO agent_watch_pool (pool, trade_date, items, info) VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT (pool, trade_date) DO UPDATE SET items=EXCLUDED.items, info=EXCLUDED.info",
                            (pool_key, d, json.dumps(items), json.dumps(ws, default=str)))
            screens[d] = items
        items_of[pool_key], ws_of[pool_key], ok_of[pool_key] = items, ws, scan_ok
    # 日期轴按默认池(每个交易日都有;只回填新方向时默认池不在 needed 里,但库里存着)
    # 再并上各池自己的日期:研究线比默认池起步早时(突破买入线 2025-09 起),只按默认池的日期轴,
    # 2025 年那些天的轴只有当天一天 —— 前几天买进、今天不在池里的持仓算不到指标,永远不会被卖出
    dates = sorted({x for x in ctx.screens.get(PRESET, {}) if x <= d}
                   | {x for pk in needed for x in ctx.screens.get(pk, {}) if x <= d} | {d})
    # 规则固定、不走优化器的研究线方向:指标按需算(ctx.ind_at);VCP 线 / 可调参方向的优化器要整段缓存,照旧提前算
    from app.services.quant import agent_research as research_static

    def _lazy_ok(b):
        ln = next((k for k, v in research_static.LINES.items() if b in v["branches"]), None)
        return ln not in (None, "vcp") and not ao.BRANCHES[b]["tunable"]

    lazy_engs = {en for en in {ao.BRANCHES[b]["engine"] for b in todo}
                 if all(_lazy_ok(b) for b in todo if ao.BRANCHES[b]["engine"] == en)}
    pooled: dict = {}
    pool_days: dict = {}
    for pool_key, engs in needed.items():
        engs = list(dict.fromkeys(engs))
        pool_days[pool_key] = max(_pool_days(e) for e in engs)
        pooled[pool_key] = agent_sim.pooled_watch(dates, {k: [tuple(x) for x in v] for k, v in ctx.screens[pool_key].items()},
                                                  pool_days[pool_key])
        eager = [e for e in engs if e not in lazy_engs]
        lazy = [e for e in engs if e in lazy_engs]
        if eager:
            ctx.ensure_cache(dates, pooled[pool_key], eager)
        if lazy:
            ctx.ensure_cache(dates, pooled[pool_key], lazy, per_code=False)
    for en in lazy_engs:                    # 上一轮存下的指标先读进来,ind_at 用得上就不重算(agent_store)
        ctx.preload_ind(cur, en, d)

    out = {"date": str(d), "ran": True, "branches": {}}
    for branch in todo:
        cur.execute("SELECT 1 FROM agent_day WHERE branch=%s AND trade_date=%s", (branch, d))
        if cur.fetchone():
            continue
        st = _branch_state(cur, branch)
        p = st["params"]
        eng = ao.engine_of(branch)
        eng_name = ao.BRANCHES[branch]["engine"]
        pool_key = _pool_of(eng_name)
        pool = pooled[pool_key]
        items, ws, scan_ok = items_of[pool_key], ws_of[pool_key], ok_of[pool_key]
        watch_today = [(c, n, s) for c, n, s, _since in pool[d]]
        since_of = {c: str(since) for c, _n, _s, since in pool[d] if since != d}
        gated = bool(ws.get("gate")) and not items
        positions = _load_positions(cur, branch)
        cur.execute("SELECT equity, consec_losses FROM agent_day WHERE branch=%s ORDER BY trade_date DESC LIMIT 1", (branch,))
        prev = cur.fetchone()
        prev_equity = prev[0] if prev else None
        consec = prev[1] if prev else 0
        cash = _meta_get(cur, f"cash:{branch}", None)
        if cash is None:
            cash = ao.initial_capital(branch)
            if _meta_get(cur, "started") is None:
                _meta_set(cur, "started", str(d))
        res = eng.run_day(str(d), positions, float(cash), lambda c: [], watch_today, prev_equity, consec, p, av.GUARDS,
                          ind_of=((lambda c, _d=d, _e=eng_name: ctx.ind_at(_e, c, _d)) if eng_name in lazy_engs
                                  else (lambda c, _d=d, _e=eng_name: ctx.cache[_e].get((c, _d)))))
        for w in res["watch_items"]:
            w["since"] = since_of.get(w["symbol"], str(d))
            if w["symbol"] in since_of:
                w["gap"] = f"{since_of[w['symbol']][5:]} 入选 · " + (w.get("gap") or "")
        fills = res["fills"]
        n_buy = sum(1 for f in fills if f["side"] == "buy")
        n_sell = len(fills) - n_buy
        realized = sum(f.get("pnl_abs") or 0 for f in fills if f["side"] == "sell")
        unit = ao.currency(branch)["unit"]
        execute = {"key": "backtest", "name": "扫描与执行",
                   "status": ("fail" if not scan_ok else "warn" if gated else "ok"), "at": _hm(),
                   "duration_ms": int((time.time() - t1) * 1000),
                   "summary": (f"「{_pool_label(pool_key)}」今天命中 {ws.get('matched')} 只,{'当天' if pool_days[pool_key] <= 1 else '连同近 ' + str(pool_days[pool_key]) + ' 天入选的'}共 {len(watch_today)} 只 → 观察列表;"
                               f"买入 {n_buy} 笔、卖出 {n_sell} 笔" + (f",已实现 {realized:+.0f} {unit}" if n_sell else "")
                               + (f";护栏:{res['halt_reason']}" if res["halt_reason"] else "")
                               + (f";市场:{res['market']['text']}" if res.get("market") else "")
                               + (f"。⚠ 今天筛选为空是因为 RS 评级被门槛挡下(不是没有候选):{ws['gate'][:60]}…" if gated else ""))
                              if scan_ok else f"筛选失败:{ws.get('error')};持仓照常管理,今天不开新仓"}

        # 落库:持仓 / 成交
        cur.execute("DELETE FROM agent_position WHERE branch=%s", (branch,))
        for pos in res["positions"]:
            bars = ctx.bars_of(pos.code, d)
            last = bars[-1][1] if bars else pos.avg_cost
            entry = date.fromisoformat(pos.entry_date)
            b0 = _bench_at(bench, entry)
            bench_pct = round((bench[d] / b0 - 1) * 100, 2) if b0 else None
            sh, sh_na = _stock_sharpe(bars, entry)
            cur.execute("INSERT INTO agent_position (branch, code, name, size, initial_size, entry_price, entry_date, avg_cost, "
                        "highest, level, bars_held, entry_rule, last_price, bench_pct, sharpe, sharpe_na, stop, risk, extra) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (branch, pos.code, pos.name, pos.size, pos.initial_size, pos.entry_price, entry, pos.avg_cost,
                         pos.highest, pos.level, pos.bars_held, pos.entry_rule, last, bench_pct, sh, sh_na, pos.stop, pos.risk,
                         json.dumps(pos.extra or {}, ensure_ascii=False)))
        for i, f in enumerate(fills):
            cur.execute("INSERT INTO agent_trade (branch, trade_date, seq, side, code, name, shares, price, amount, position_pct, "
                        "pnl_abs, pnl_pct, hold_days, rule_id, rule_name, rationale, entry_date, level, grade, grade_detail) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (branch, d, i, f["side"], f["symbol"], f.get("name"), f["shares"], f["price"], f.get("amount"),
                         f.get("position_pct"), f.get("pnl_abs"), f.get("pnl_pct"), f.get("hold_days"),
                         f["rule_id"], f["rule_name"], f["rationale"], f.get("entry_date"), f.get("level"), f.get("grade"),
                         f.get("grade_detail")))
        followups = _backfill_followups(cur, branch, d, lambda c: ctx.bars_of(c, d), ao.currency(branch)["symbol"])

        # 4. 调整策略(优化器)—— 在今天的成交落库之后跑,它只看历史
        t4 = time.time()
        # 研究线封存 → 优化器冻结(照常纸上跑,当对照组);新研究线规则固定 → 满 30 笔前不开优化器
        from app.services.quant import agent_research as research
        line = research.line_of_branch(cur, branch)
        if line and line.get("status") == "archived":
            opt = {"evaluated": 0, "action": "archived",
                   "text": (f"「{line['label']}」{line.get('archived_at') or ''} 已封存:优化器冻结在 v{st['version']},不再换版;"
                            f"照常纸上跑,作为新研究线的对照组")}
        elif line and not ao.BRANCHES[branch]["tunable"] and line["key"] != "vcp":
            opt = {"evaluated": 0, "action": "fixed",
                   "text": f"研究线「{line['label']}」规则固定:满 {research.KILL_MIN_CYCLES} 笔完整交易前不开优化器"}
        else:
            opt = ao.step(branch, st, d, dates, pool, ctx.cache[eng_name], av.GUARDS)
        _meta_set(cur, f"branch:{branch}", st)
        adjust = {"key": "adjust", "name": "调整策略", "status": "warn" if opt["action"] in ("observe", "observing") else "ok",
                  "at": _hm(), "duration_ms": int((time.time() - t4) * 1000),
                  "summary": f"v{st['version']} · " + opt["text"]}

        # 3. 复盘总结
        t3 = time.time()
        lesson = _lesson(cur, branch, d, fills, res, ws, followups, opt, st, eng)
        review = {"key": "review", "name": "复盘总结", "status": "ok", "at": _hm(),
                  "duration_ms": int((time.time() - t3) * 1000), "summary": lesson["title"]}

        cur.execute("INSERT INTO agent_day (branch, trade_date, equity, cash, bench_close, holdings_n, watchlist, pipeline, lesson, "
                    "halt_reason, consec_losses, opt) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (branch, d, res["equity"], res["cash"], bench[d], len(res["positions"]), json.dumps(res["watch_items"]),
                     json.dumps({"date": str(d), "steps": [collect, execute, review, adjust]}), json.dumps(lesson),
                     res["halt_reason"], res["consec_losses"], json.dumps(opt, default=str)))
        _meta_set(cur, f"cash:{branch}", res["cash"])
        out["branches"][branch] = {"equity": round(res["equity"]), "fills": len(fills), "opt": opt["action"], "v": st["version"]}
    # 这一天新算的指标写库(agent_store);失败只撤这一步,当天的回测结果照常提交
    n_saved = 0
    cur.execute("SAVEPOINT ind_cache")
    try:
        n_saved = ctx.flush_ind(cur, d)
        cur.execute("RELEASE SAVEPOINT ind_cache")
    except Exception:                                                 # noqa: BLE001
        log.exception("[agent] %s 指标落库失败,不影响当天运行", d)
        cur.execute("ROLLBACK TO SAVEPOINT ind_cache")
    if _meta_get(cur, "state") is None:
        _meta_set(cur, "state", "running")
    conn.commit()
    cur.close()
    conn.close()
    log.info("[agent] %s 跑完 %s · 观察 %s · 指标 复用 %d / 现算 %d / 失效重算 %d · 落库 %d · %.1fs", d, out["branches"],
             {k: len(v[d]) for k, v in pooled.items()}, ctx.ind_stat["reused"], ctx.ind_stat["computed"],
             ctx.ind_stat["stale"], n_saved, time.time() - t0)
    return out


def _bench_at(bench: dict, d: date):
    ds = [x for x in bench if x <= d]
    return bench[max(ds)] if ds else None


def _hm() -> str:
    return datetime.now(market_tz("CN_A")).strftime("%H:%M")


def _backfill_followups(cur, branch: str, d: date, bars_of, sym: str = "$") -> list[dict]:
    """卖出 5 个交易日后:那只票又走了多少 → 写进 agent_trade.followup。→ 今天回填的那些。"""
    cur.execute("SELECT id, code, price, trade_date, rule_id, rule_name FROM agent_trade "
                "WHERE branch=%s AND side='sell' AND followup IS NULL AND trade_date < %s", (branch, d))
    out = []
    for tid, code, price, td, rule, rname in cur.fetchall():
        bars = bars_of(code)
        after = [b for b in bars if b[0] > td]
        if len(after) < 5:
            continue
        p5 = after[4][1]
        chg = (p5 / price - 1) * 100
        verdict = "卖早了" if chg >= 3 else ("躲过了" if chg <= -3 else "差不多")
        text = f"事后跟踪 · 卖出后 5 个交易日({after[4][0]})收 {sym}{p5:.2f},较卖出价 {chg:+.1f}% —— {verdict}"
        cur.execute("UPDATE agent_trade SET followup=%s WHERE id=%s", (text, tid))
        out.append({"code": code, "rule_id": rule, "rule_name": rname, "chg": chg, "verdict": verdict, "sold": str(td)})
    return out


# ═══════════════════════════════════════════════════════════════
# 复盘总结(规则模板,不走 LLM)
# ═══════════════════════════════════════════════════════════════

def _cum_stats(cur, branch: str, upto: date) -> dict:
    cur.execute("SELECT pnl_abs, rule_id FROM agent_trade WHERE branch=%s AND side='sell' AND trade_date<=%s", (branch, upto))
    rows = cur.fetchall()
    n = len(rows)
    win = sum(1 for p, _ in rows if p is not None and p > 0)
    gains = sum(p for p, _ in rows if p and p > 0)
    losses = -sum(p for p, _ in rows if p and p < 0)
    by_rule: dict = {}
    for p, r in rows:
        by_rule[r] = by_rule.get(r, 0) + 1
    return {"n": n, "win": win, "win_rate": (win / n * 100) if n else None,
            "pf": (gains / losses) if losses > 0 else None, "by_rule": by_rule}


def _lesson(cur, branch: str, d: date, fills: list[dict], res: dict, ws: dict, followups: list[dict],
            opt: dict, st: dict, eng=av) -> dict:
    sells = [f for f in fills if f["side"] == "sell"]
    buys = [f for f in fills if f["side"] == "buy"]
    realized = sum(f.get("pnl_abs") or 0 for f in sells)
    ccy = ao.currency(branch)
    sym, unit = ccy["symbol"], ccy["unit"]
    today = _cum_stats(cur, branch, d)
    yday = _cum_stats(cur, branch, d - timedelta(days=1))
    n_watch = len(res["watch_items"])
    n_blocked = sum(1 for w in res["watch_items"] if w.get("blocked"))
    miss: dict = {}
    entry_ids = [r["id"] for r in eng.RULES if r["kind"] == "buy"]
    for w in res["watch_items"]:
        for rid in entry_ids:
            if rid in (w.get("gap") or "") and "还差" in (w.get("gap") or ""):
                miss[rid] = miss.get(rid, 0) + 1
    top_miss = max(miss.items(), key=lambda kv: kv[1]) if miss else None

    if not fills and ws.get("gate") and not n_watch:
        title, kind = "观察列表为空:RS 评级被覆盖率门槛挡下,筛选整批算不出(数据边界,不是没有候选)", "validated"
    elif not fills:
        title = (f"空仓观望:候选 {n_watch} 只无一触发买入" if not res["positions"]
                 else f"持仓不动:{len(res['positions'])} 只都没到出场线,候选 {n_watch} 只没有新突破")
        kind = "validated"
    else:
        title = f"今日买 {len(buys)} 笔、卖 {len(sells)} 笔" + (f",已实现 {realized:+.0f} {unit}" if sells else "")
        kind = "loss" if realized < 0 else "validated"
    if opt["action"] == "promoted":
        title = "换版:" + opt["text"].split("→", 1)[-1].strip()[:60]
    pool_key = _pool_of(ao.BRANCHES[branch]["engine"])
    pdays = _pool_days(ao.BRANCHES[branch]["engine"])
    what = (f"观察列表 {n_watch} 只(「{_pool_label(pool_key)}」今天命中 {ws.get('matched')} 只"
            + (f",其余是近 {pdays} 天入选的)" if pdays > 1 else ")")
            + (f",其中 {n_blocked} 只被护栏或上限挡下" if n_blocked else "")
            + f";成交 {len(fills)} 笔" + (";".join(
                f"{f['symbol']} {'买' if f['side'] == 'buy' else '卖'} {f['shares']} 股 @ {sym}{f['price']:.2f}({f['rule_id']})"
                for f in fills[:6]) if fills else "")
            + f"。收盘后权益 {sym}{res['equity']:,.0f},现金 {sym}{res['cash']:,.0f},持仓 {len(res['positions'])} 只。")
    why_parts = []
    if sells:
        by = {}
        for f in sells:
            by[f["rule_id"]] = by.get(f["rule_id"], 0) + 1
        why_parts.append("卖出触发的规则:" + "、".join(f"{k} {eng.RULE_NAME.get(k, k)} ×{v}" for k, v in by.items()))
    if top_miss:
        cond = {r["id"]: r["condition"] for r in eng.RULES}[top_miss[0]].split(":")[0]
        why_parts.append(f"候选里最常差的一条是 {top_miss[0]}({cond}),{top_miss[1]} 只卡在这里")
    if res["halt_reason"]:
        why_parts.append("护栏:" + res["halt_reason"])
    why = ";".join(why_parts) + "。" if why_parts else "今天没有规则被触发。"
    learned_parts = []
    if yday["n"] != today["n"] and today["n"]:
        learned_parts.append(
            f"累计卖出 {today['n']} 笔,胜率 {today['win_rate']:.0f}%"
            + (f"(昨天 {yday['win_rate']:.0f}%)" if yday["win_rate"] is not None else "")
            + (f",盈亏比 {today['pf']:.2f}" if today["pf"] else ""))
    for fu in followups[:3]:
        learned_parts.append(f"{fu['sold']} 按 {fu['rule_id']} 卖出的 {fu['code']},5 日后 {fu['chg']:+.1f}% —— {fu['verdict']}")
    if today["by_rule"]:
        k, v = max(today["by_rule"].items(), key=lambda kv: kv[1])
        learned_parts.append(f"到今天为止触发最多的出场规则是 {k}({eng.RULE_NAME.get(k, k)},{v} 次)")
    learned_parts.append("优化器:" + opt["text"])
    learned = ";".join(learned_parts) + "。"
    if opt["action"] == "promoted":
        landed = {"status": "landed", "text": f"已换版为 v{st['version']}:{opt['text'].split('→', 1)[-1].strip()}"}
    elif opt["action"] in ("observe", "observing"):
        landed = {"status": "pending", "text": "候选在观察期,期末不比当前差才换版:" + (st.get("observing") or {}).get("change", "")}
    elif branch == "base":
        landed = {"status": "pending", "text": "基准方向规则固定,不落地改动(对照组)"}
    else:
        landed = {"status": "pending", "text": "本次没有通过防过拟合门槛的候选,规则不变:" + opt["text"][:80]}
    return {"date": d.strftime("%m-%d"), "title": title, "kind": kind, "what": what, "why": why, "learned": learned,
            "landed": landed}


# ═══════════════════════════════════════════════════════════════
# 面板
# ═══════════════════════════════════════════════════════════════

def _sharpe(equities: list[float]) -> float | None:
    if len(equities) < 21:
        return None
    rets = [b / a - 1 for a, b in zip(equities, equities[1:])]
    sd = statistics.pstdev(rets)
    return round(statistics.mean(rets) / sd * math.sqrt(252), 2) if sd > 0 else None


def _max_dd(equities: list[float]) -> tuple[float, float, int, int]:
    peak, peak_i, best = equities[0], 0, (0.0, 0.0, 0, 0)
    for i, e in enumerate(equities):
        if e > peak:
            peak, peak_i = e, i
        dd = (e / peak - 1) * 100
        if dd < best[0]:
            best = (dd, e - peak, peak_i, i)
    return best


def _fmt_sh(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(market_tz("CN_A")).strftime("%m-%d %H:%M 沪")


def _next_run_text(market: str = MARKET) -> str:
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%m-%d") + (" 06:30 沪(美股收盘后)" if market == "us" else " 06:30 沪(跑上一个交易日的 A 股收盘)")


def _branch_summary(cur, branch: str, active: bool) -> dict:
    cur.execute("SELECT equity, bench_close FROM agent_day WHERE branch=%s ORDER BY trade_date", (branch,))
    rows = cur.fetchall()
    st = _branch_state(cur, branch)
    meta = ao.BRANCHES[branch]
    out = {"key": branch, "label": meta["label"], "direction": meta["direction"], "version": f"v{st['version']}",
           "active": active, "pnl_pct": None, "benchmark_pct": None, "excess_pt": None, "trades_total": None,
           "win_rate": None, "max_dd_pct": None}
    if not rows:
        return out
    init = ao.initial_capital(branch)
    eq = [r[0] for r in rows]
    b0 = next((r[1] for r in rows if r[1]), None)
    pnl_pct = (eq[-1] / init - 1) * 100
    bench_pct = (rows[-1][1] / b0 - 1) * 100 if (b0 and rows[-1][1]) else None
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_abs > 0 THEN 1 ELSE 0 END) FROM agent_trade WHERE branch=%s AND side='sell'", (branch,))
    n, w = cur.fetchone()
    out.update({"pnl_pct": round(pnl_pct, 2), "benchmark_pct": round(bench_pct, 2) if bench_pct is not None else None,
                "excess_pt": round(pnl_pct - bench_pct, 2) if bench_pct is not None else None,
                "trades_total": n or 0, "win_rate": round((w or 0) / n * 100, 1) if n else None,
                "max_dd_pct": round(_max_dd(eq)[0], 2)})
    return out


def dashboard(branch: str = "base") -> dict:
    if branch not in ao.BRANCHES:
        branch = "base"
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT trade_date, equity, cash, bench_close, holdings_n, watchlist, pipeline, lesson, halt_reason, "
                "computed_at, opt FROM agent_day WHERE branch=%s ORDER BY trade_date", (branch,))
    days = cur.fetchall()
    state = _meta_get(cur, "state", "never_started")
    st = _branch_state(cur, branch)
    p = st["params"]
    eng = ao.engine_of(branch)
    # 迭代方向卡只列同一条研究线里的方向(VCP 线 4 张、唐奇安线 1 张)
    from app.services.quant import agent_research as research
    line = research.line_of_branch(cur, branch)
    line_block = ({"key": line["key"], "label": line.get("label"), "status": line.get("status"),
                   "status_text": research.STATUS_TEXT.get(line.get("status"), line.get("status")),
                   "archived_at": line.get("archived_at"), "archive_tag": line.get("archive_tag"),
                   "archive_reason": line.get("archive_reason"), "verdict": line.get("verdict"),
                   "kill_text": line.get("kill_text")} if line else None)
    in_line = line["branches"] if line else [branch]
    branches = [_branch_summary(cur, b, b == branch) for b in BRANCHES if b in in_line]
    if not days:
        cur.close()
        conn.close()
        return {"enabled": False, "state": "never_started", "paper": True, "version": f"v{st['version']}",
                "branch": branch, "branches": branches, "line": line_block,
                "strategy": _strategy_block(None, st, branch), "guardrails": _guard_block(None, p, branch),
                "currency": ao.currency(branch),
                "rules": _rules_block([], [], [], p, None, eng)}
    L = days[-1]
    init = ao.initial_capital(branch)
    ccy = ao.currency(branch)
    eq = [r[1] for r in days]
    b0 = next((r[3] for r in days if r[3]), None)
    dd_pct, dd_abs, dd_from, dd_to = _max_dd(eq)
    cur.execute("SELECT trade_date, seq, side, code, name, shares, price, amount, position_pct, pnl_abs, pnl_pct, "
                "hold_days, rule_id, rule_name, rationale, entry_date, level, followup, grade FROM agent_trade "
                "WHERE branch=%s ORDER BY trade_date, seq", (branch,))
    trades = cur.fetchall()
    sells = [t for t in trades if t[2] == "sell"]
    wins = [t for t in sells if t[9] is not None and t[9] > 0]
    gains = sum(t[9] for t in wins)
    losses = -sum(t[9] for t in sells if t[9] is not None and t[9] < 0)
    cur.execute("SELECT code, name, size, entry_price, entry_date, avg_cost, bars_held, entry_rule, last_price, "
                "bench_pct, sharpe, sharpe_na FROM agent_position WHERE branch=%s ORDER BY entry_date, code", (branch,))
    poss = cur.fetchall()
    rules = eng.rules_for(p)
    rule_cond = {r["id"]: r["condition"] for r in rules}
    watch_items = list(L[5] or [])
    # 观察列表不足 5 条就补 RS 最强的 —— 补位项带 filler,和真候选不是一回事
    fillers = []
    if len(watch_items) < WATCH_MIN and ccy["market"] == "us":      # 补位读的是美股 RS 排名,别的市场不补
        try:
            held = {x[0] for x in poss} | {w.get("symbol") for w in watch_items}
            fillers = _rs_fillers(cur, WATCH_MIN - len(watch_items), held)
        except Exception:                                     # noqa: BLE001
            log.exception("[agent] RS 补位失败,观察列表按原样返回")
            fillers = []
    try:
        scan_of = _scan_hits(cur, _pool_of(ao.BRANCHES[branch]["engine"]))
    except Exception:                                         # noqa: BLE001
        log.exception("[agent] 读扫描命中日失败,悬停日K 上就没有蓝线,其余照常")
        scan_of = {}
    setup_rules = getattr(eng, "SETUP_RULES", None)
    try:
        layers = _scan_layers(cur, branch, sorted({t[3] for t in trades}), setup_rules)
    except Exception:                                         # noqa: BLE001
        log.exception("[agent] 读形态就绪 / 全满足日失败,悬停日K 只画候选池那层,其余照常")
        conn.rollback()
        layers = {}
    history = _trade_rounds(trades, rule_cond, scan_of, layers, market=ccy["market"])
    cur.close()
    conn.close()

    holdings = []
    for x in poss:
        holdings.append({"symbol": x[0], "name": x[1], "cost": round(x[5], 2),
                         "price": round(x[8], 2) if x[8] is not None else None,
                         "pnl_pct": round((x[8] / x[5] - 1) * 100, 2) if x[8] and x[5] else None,
                         "hold_days": x[6], "bench_pct": x[9], "sharpe": x[10], "sharpe_na_reason": x[11],
                         "entry_rule": x[7] or eng.ENTRY_RULE, "entry_rule_text": rule_cond.get(x[7] or eng.ENTRY_RULE)})
    today_trades = [_trade_item(t, ccy["market"]) for t in trades if t[0] == L[0]]
    lessons = [r[7] for r in reversed(days[-7:]) if r[7]]
    started = days[0][0]
    date_index = {str(r[0]): i for i, r in enumerate(days)}
    versions, marks = _versions_block(st, started, days, branch)
    from app.services.quant import agent_overfit
    return {
        "overfit": agent_overfit.public_report(history, [r[0] for r in days], line, branch),
        "enabled": True, "state": state, "paper": True, "version": f"v{st['version']}",
        "branch": branch, "branches": branches, "line": line_block,
        "day_count": len(days), "iteration_count": st["version"] - 1,
        "last_run_text": (_fmt_sh(L[9]) or "") + f" · 按 {L[0].strftime('%m-%d')} {ccy['market_label']}收盘",
        "next_run_text": _next_run_text(ccy["market"]),
        "currency": ccy,
        "strategy": _strategy_block(len(L[5] or []), st, branch),
        "guardrails": _guard_block(L[8], p, branch),
        "pipeline": L[6],
        "overview": {
            "pnl_abs": round(L[1] - init, 2), "pnl_pct": round((L[1] / init - 1) * 100, 2), "equity": round(L[1], 2),
            "benchmark_symbol": ccy["bench_label"],
            "benchmark_pct": round((L[3] / b0 - 1) * 100, 2) if (b0 and L[3]) else None,
            "excess_pt": round((L[1] / init - 1) * 100 - (L[3] / b0 - 1) * 100, 2) if (b0 and L[3]) else None,
            "max_dd_pct": round(dd_pct, 2), "max_dd_abs": round(dd_abs, 2),
            "dd_from": days[dd_from][0].strftime("%m-%d"), "dd_to": days[dd_to][0].strftime("%m-%d"),
            "trades_total": len(sells), "trades_win": len(wins),
            "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else None,
            "profit_factor": round(gains / losses, 2) if losses > 0 else None,
            "sharpe": _sharpe(eq), "risk_free_pct": 0,
            "holdings_count": len(poss), "max_holdings": p["max_holdings"],
            "invested_pct": round((L[1] - L[2]) / L[1] * 100, 1) if L[1] else None, "cash": round(L[2], 2),
        },
        "nav": {
            "benchmark_symbol": ccy["bench_label"],
            "points": [{"date": str(r[0]), "agent_pct": round((r[1] / init - 1) * 100, 3),
                        "benchmark_pct": round((r[3] / b0 - 1) * 100, 3) if (b0 and r[3]) else None} for r in days],
            "version_marks": [{"index": date_index[m["date"]], "version": m["version"]} for m in marks if m["date"] in date_index],
            "drawdown": {"from_index": dd_from, "to_index": dd_to, "pct": round(dd_pct, 2)} if dd_pct < 0 else None,
        },
        "rules": _rules_block(trades, poss, days, p, st, eng),
        "holdings": {"as_of": f"{L[0].strftime('%m-%d')} 收盘", "quote_delay_min": None, "items": holdings},
        "watchlist": {"items": watch_items + fillers, "matched": len(watch_items), "filled": len(fillers)},
        "trades": {"date": str(L[0]), "items": today_trades},
        "history": {
            "items": history,
            # 悬停日K 三层蓝线的名字(2026-09-15)。setup 为 None = 这个引擎没声明「形态就绪」规则,前端不画那层 ——
            # VCP 那几个引擎的买入条件把趋势 / 枢轴 / 量能混在一起,替用户拆出「就绪」就是替他定口径
            "layers": {"pool": "进候选池", "setup": getattr(eng, "SETUP_LABEL", None) if setup_rules else None,
                       "setup_note": getattr(eng, "SETUP_NOTE", None) if setup_rules else None,
                       "entry": "买入条件全满足"},
            "fee_total": round(sum(r["fee"] for r in history), 2),
            "pnl_gross_total": round(sum(r["pnl_gross"] for r in history), 2),
            "pnl_net_total": round(sum(r["pnl_abs"] for r in history), 2),
            "fee_note": ("手续费按阶梯式(当月累计 ≤30 万股 0.0035 美元/股,更高量级逐档降到 0.0005;"
                         "每笔最低 0.35 美元、最高为成交金额的 1%)买卖各收一次" if ccy["market"] == "us" else
                         "A 股手续费:佣金万 2.5(每笔最低 5 元)+ 过户费 0.001% 买卖各收一次,卖出另收印花税 0.05%"),
            "scope_note": ("这一列只进这张表 —— 上面的总览、净值曲线、胜率仍是引擎的零费用口径" if ccy["market"] == "us" else
                           "这条线的引擎已经从现金里扣了手续费:总览和净值曲线是扣费后的;胜率按每笔卖出的扣费前盈亏计"),
        },
        "versions": versions,
        "lessons": lessons,
    }


_V1 = {
    "vcp": ("VCP 波段交易 —— 移植用户提供的 Backtrader 策略(5 条买入过滤 / 9 条出场 / 倒三角加仓)",
            "用户 2026-09-12 指定;观察列表 = 筛选器「VCP 波段收缩」近 10 天并集"),
    "vcp3": ("VCP 三段式 —— 枢轴 + ATR 触发、3 天内放量确认、底部低点止损、1R 后移动止损、15 天时间止损",
             "Claude 2026-09-12 按全年回测的三个事实自设计,用户同意后实现;同一份观察列表"),
    "vcp4": ("VCP · SEPA 优化 —— 市场过滤 + 趋势模板 + 加权评分与一票否决 + 风险定仓 + S 级加仓 + 盘中止损 + 分段跟踪",
             "用户 2026-09-13 按 Minervini SEPA 五根柱子拆解方向 C 后给的 v4 草案;没有基本面 / 行业数据源,那两块没做"),
    "donchian": ("唐奇安通道突破 —— 收盘第一次突破 55 日最高进,跌破 20 日最低或 2 ATR 止损出,单笔风险 1% 定仓",
                 "研究台方案(2026-09-13 用户批准)的第一条新研究线;和 VCP 差得最远,规则固定,满 30 笔前不优化"),
    "breakout": ("突破买入 v14 —— v13(前一天收盘要有资金逆势买入)+ 财报风控:财报前 5 个交易日内不买不加仓,"
                 "离财报 2 个交易日时浮盈不超过 10% 清仓、超过 10% 卖一半;以下为 v9 起的主体:"
                 "入场筛选同 v4 + 五项评分只记录(追高超过 4% 不买,走廊 < 1R 不买)+ 统一仓位 + 两次加仓;"
                 "出场同 v5:1 ATR 初始止损(≤ 8%)+ 固定 6% + Base 低点 2% + 5% 保本 + 20% 减半 + 破 EMA10 再减半 / 破 EMA20 清仓",
                 "用户 2026-09-13 给的 Patrick Walker 风格完整脚本逐条移植;v2 枢轴改密集成交区、RS 降到 70(一年 +0.63%、48 笔);"
                 "v3(2026-09-14)缩量门槛 0.9 → 1.0。v1 枢轴 = 前 21 日最高、RS ≥ 80:一年 -4.72%、21 笔;没有开盘价,阳线条件没做"),
    "limitup": ("涨停后强势整理(A 股)v4 —— 三天整理幅度 ≤ 15%、三天不能每天都比涨停日缩量(v4 加);只做创业板 / 科创板(v3 加);4 个交易日前涨停(主板 10% / 创业板科创板 20%)、之后三天没再涨停且收盘都高于涨停日收盘、"
                "当天收盘站上 5 日均线且 5 日均线向上(v2 加),"
                "信号当天收盘买 1 万元、次日收盘卖(跌停 / 停牌顺延),A 股手续费从现金里扣",
                "用户 2026-09-17 给的规则;立项时问清四个口径:守住 = 收盘高于涨停日收盘、涨停按板块、每个信号独立买 1 万、手续费按 A 股实际扣"),
    "limitup_yin": ("涨停 + 三根阴线(A 股主板)—— 4 个交易日前涨停,之后连续三天阴线(收盘 < 开盘),信号当天收盘买 1 万元、次日收盘卖",
                    "用户 2026-09-18 在涨停后强势整理线上新开的迭代方向;只做主板,买卖、手续费、仓位口径与原方向相同"),
}


def _versions_block(st: dict, started: date, days, branch: str) -> tuple[list[dict], list[dict]]:
    """策略演进 + 换版竖线。每一版都带 reason(优化器的两段数字)和 effect(换版后到今天的净值变化)。"""
    eq_at = {str(r[0]): r[1] for r in days}
    change, reason = _V1[ao.BRANCHES[branch]["engine"]]
    versions = [{"label": "v1", "date": started.strftime("%m-%d"), "change": change, "reason": reason,
                 "effect": None, "status": "current" if st["version"] == 1 else "released"}]
    marks = [{"date": str(started), "version": "v1"}]
    last_eq = days[-1][1]
    for v in st.get("versions", []):
        e0 = eq_at.get(v["date"])
        eff = (f"换版后到今天净值 {(last_eq / e0 - 1) * 100:+.2f}%(观察期多赚 ${v.get('obs_gain', 0):+.0f})"
               if e0 else None)
        versions.append({"label": f"v{v['version'] - 1} → v{v['version']}", "date": v["date"][5:],
                         "change": v["change"], "reason": v["reason"], "effect": eff,
                         "status": "current" if v["version"] == st["version"] else "released"})
        marks.append({"date": v["date"], "version": f"v{v['version']}"})
    obs = st.get("observing")
    if obs:
        versions.append({"label": f"v{st['version']} → v{st['version'] + 1}?", "date": f"观察期自 {obs['since'][5:]}",
                         "change": obs["change"], "reason": obs["reason"],
                         "effect": f"观察 {ao.OBS_DAYS} 个交易日,期末不比当前差才并版", "status": "observing"})
    return versions, marks


WATCH_MIN = 5          # 观察列表至少凑满 5 条(前端一屏正好 5 张卡片)


def _rs_fillers(cur, need: int, exclude: set) -> list[dict]:
    """观察列表不足 5 条时,补几只**全美股 RS 排名最高**的进来(用户 2026-09-12 要求)。

    这些票**不是**智能体的候选 —— 它们没过任何一条买入规则,只是"现在最强的票"。
    所以每条都带 filler=True,前端必须把它和真候选在视觉上分开,
    否则用户会以为智能体在等它们(那就是用排版编造了一个不存在的结论)。

    口径:rs_line_stat.rs_raw_exact = 0.4·ROC(63) + 0.2·ROC(126) + 0.2·ROC(189) + 0.2·ROC(252),
    需要 252 个以上有效收盘价,次新股没有这个值、自然排除。百分位是在
    "同一天有精确 RS Raw 的全部美股"里算的,分母写进文案,不让人以为是全市场。
    """
    if need <= 0:
        return []
    cur.execute("SELECT max(as_of) FROM rs_line_stat WHERE market='us'")
    row = cur.fetchone()
    as_of = row[0] if row else None
    if not as_of:
        return []
    cur.execute("SELECT code, rs_raw_exact, "
                "       100 * percent_rank() OVER (ORDER BY rs_raw_exact) "
                "FROM rs_line_stat WHERE market='us' AND as_of=%s AND rs_raw_exact IS NOT NULL "
                "ORDER BY rs_raw_exact DESC LIMIT %s", (as_of, need + len(exclude) + 10))
    cand = [r for r in cur.fetchall() if r[0] not in exclude][:need]
    if not cand:
        return []
    cur.execute("SELECT COUNT(*) FROM rs_line_stat WHERE market='us' AND as_of=%s AND rs_raw_exact IS NOT NULL", (as_of,))
    total = cur.fetchone()[0]
    codes = [c[0] for c in cand]
    cur.execute("SELECT code, close FROM rs_daily WHERE market='us' AND trade_date=%s AND code = ANY(%s)", (as_of, codes))
    px = dict(cur.fetchall())
    out = []
    for i, (code, raw, pct) in enumerate(cand):
        out.append({
            "symbol": code, "name": None, "filler": True,
            "price": round(px[code], 2) if px.get(code) else None,
            "score": None, "rule_id": None, "progress_pct": None,
            "rs_rank": i + 1, "rs_pct": round(pct, 1), "rs_raw_pct": round(raw * 100, 1),
            "gap": f"不是今天的候选 —— 它没过任何一条买入规则。近一年加权涨幅 {raw * 100:+.0f}%,"
                   f"在 {total} 只有完整一年日线的美股里排第 {i + 1}",
        })
    return out


def _scan_hits(cur, pool: str = PRESET) -> dict:
    """每只票被扫描筛选命中过的日子 —— {代码: [YYYY-MM-DD, …]}。

    悬停日K 上那几条半透明蓝线就是它:用户要看的是「扫描什么时候盯上这只票、
    盯了多久、第几天才买」。只给买入日和卖出日的话,看不出这一笔等了多久。

    agent_watch 一天一行、items 是 [代码, 名称, 评分] 的数组,
    实测 174 天一共 41 kB,全表读进来建映射比按代码去 JSONB 里查便宜得多。
    """
    # 按方向自己的池读(2026-09-13 用户看突破买入的悬停日K 发现:原来一律读默认池 agent_watch,
    # 突破买入看板上的「扫描命中」蓝线其实是 VCP 波段收缩池的命中日,和这条线的筛选没关系)
    if pool == PRESET:
        cur.execute("SELECT trade_date, items FROM agent_watch ORDER BY trade_date")
    else:
        cur.execute("SELECT trade_date, items FROM agent_watch_pool WHERE pool=%s ORDER BY trade_date", (pool,))
    out: dict = {}
    for d, items in cur.fetchall():
        for it in (items or []):
            code = it[0] if isinstance(it, (list, tuple)) and it else (
                it.get("symbol") if isinstance(it, dict) else None)
            if code:
                out.setdefault(code, []).append(str(d))
    return out


def scan_layers(rows, setup_rules=None) -> dict:
    """观察列表逐日记录 → {代码: {"setup": [日期], "entry": [日期]}}(悬停日K 的中蓝 / 深蓝两层)。

    2026-09-15 用户问「为什么点开每只票都显示命中了很多天」:原来只画一层 = 进了这条线的候选池,
    池子只做粗筛(突破买入每天约 224 只),KRYS 在池里 105 天,真正买入条件全满足只有买入那 1 天。

    rows = [(日期, 代码, progress_pct, fails)],来自 agent_day.watchlist —— 就是引擎**当天**判过的结果,不重算,
    口径与成交逐日一致(当天用 KRYS / STT / CVS / FLNG 逐日核对:全满足的日子 = 买入日,CVS 多一天是被挡下没买)。
    - entry:progress_pct == 100,那天买入条件全满足(可能被持仓上限 / 护栏挡下没买)
    - setup:引擎声明了 setup_rules,且那天记了 fails、里面没有任何一条 setup 规则 —— 只差突破 / 市场。
      没有 fails 字段的老记录不算(不猜),用 `agent_run watch-fails --branch <方向>` 回填
    """
    rules = set(setup_rules or ())
    out: dict = {}
    for d, code, prog, fails in rows:
        if not code:
            continue
        slot = None
        if str(prog) == "100":
            slot = out.setdefault(code, {"setup": [], "entry": []})
            slot["entry"].append(str(d))
        if rules and isinstance(fails, list) and not (rules & set(fails)):
            slot = slot or out.setdefault(code, {"setup": [], "entry": []})
            slot["setup"].append(str(d))
    return out


def _scan_layers(cur, branch: str, codes: list[str], setup_rules=None) -> dict:
    """只取成交过的票(历史交易记录只给它们画日K)。watchlist 每天几百条、每条带大段 gap 文字,
    整列读回来太重 —— 在库里拆 JSONB,只拿日期 / 代码 / 进度 / fails 四样。"""
    if not codes:
        return {}
    cur.execute("SELECT d.trade_date, e->>'symbol', e->>'progress_pct', e->'fails' "
                "FROM agent_day d, jsonb_array_elements(d.watchlist::jsonb) e "
                "WHERE d.branch=%s AND e->>'symbol' = ANY(%s) ORDER BY d.trade_date", (branch, list(codes)))
    return scan_layers(cur.fetchall(), setup_rules)


def _trade_rounds(trades, rule_cond: dict, scan_of: dict | None = None, layers: dict | None = None,
                  market: str = MARKET) -> list[dict]:
    """逐笔成交 → 一个持仓周期一条记录(历史交易记录卡片用)。

    一个周期 = 同一只票从建仓到清仓的一整段,中间可能有多次买(倒三角加仓 level 1/2/3)
    和多次卖(5 日不涨减半 → 10 日不涨清仓)。**不是 1 买 1 卖的配对**:
    强行配成 1:1 会把"加了两次仓"显示成三笔独立交易,净损益和回报全错。

    只输出**已经平掉**的周期(买入股数 = 卖出股数)。没平完的还在持仓明细里,
    它的最终盈亏没发生,写进"历史交易记录"就是提前写结论。

    手续费(用户 2026-09-12 要求)按 commission.py 的阶梯算,**买卖各收一次**:
    档位取决于当月在这笔之前已经成交了多少股,所以必须拿**整个 branch 的成交**
    按时间顺序过一遍才能定 —— 不能只在一个周期内部算。

    净损益 = 该周期所有卖出的 pnl_abs 之和 **减去这一笔全部腿的手续费**;
    pnl_gross 是扣费前的数,两个都给 —— 只给净的,用户对不上引擎算的毛利。
    回报   = 净损益 ÷ 该周期买入总金额 —— 加仓过的票必须用总投入当分母,
             用第一笔的成本会把回报算大。分母不含买入手续费,和「大小」那一列对得上。

    ⚠ 手续费只进这张表。上面的总览、净值曲线、胜率仍是**引擎的零费用口径** ——
      引擎按收盘价成交、不扣现金,要让净值曲线也扣费得改引擎并重跑全部历史
      (现金变少会买不起同样股数 → 仓位变 → 整条曲线和每一笔成交都会变)。
      两个口径的差额写在卡片脚注里,不许闷着。
    """
    if market == "a":
        # A 股按笔算,不分月档位(commission.a_share_fee);引擎已经从现金里扣过同一个数,这里是给成交表看的
        fee_of = {(t[0], t[1]): cm.a_share_fee(t[2], t[5], t[6]) for t in trades}
    else:
        fills = []
        for t in sorted(trades, key=lambda x: (x[0], x[1])):
            fills.append(((t[0], t[1]), t[0].strftime("%Y-%m"), t[5], t[6]))
        fee_of = cm.fees_by_month(fills)

    groups: dict = {}
    for t in trades:
        key = (t[3], t[15])                      # code + entry_date
        if key[1] is None:
            continue
        groups.setdefault(key, []).append(t)
    rounds = []
    for (code, entry_date), ts in groups.items():
        ts.sort(key=lambda x: (x[0], x[1]))
        buys = [t for t in ts if t[2] == "buy"]
        sells = [t for t in ts if t[2] == "sell"]
        if not buys or not sells:
            continue
        if sum(t[5] for t in buys) != sum(t[5] for t in sells):
            continue                             # 还没平完 —— 归持仓明细管
        cost = sum(t[7] or (t[5] * t[6]) for t in buys)
        gross = sum(t[9] for t in sells if t[9] is not None)
        fee = sum(fee_of.get((t[0], t[1]), 0.0) for t in ts)
        pnl = gross - fee
        legs = []
        for t in ts:
            # rationale / followup 原来只在「今日操作报告」里露面,那张卡片 2026-09-12 撤掉了,
            # 这两样必须跟到这里来 —— 它们是"为什么买/卖"和"卖完之后怎么样了",
            # 卡片没了不等于信息该没
            leg = {"kind": "entry" if t[2] == "buy" else "exit", "date": str(t[0]),
                   "rule_id": t[12], "rule_name": t[13], "rationale": t[14],
                   "rule_text": rule_cond.get(t[12]), "price": t[6], "shares": t[5],
                   "fee": round(fee_of.get((t[0], t[1]), 0.0), 4)}
            if t[2] == "sell":
                leg.update({"pnl_abs": t[9], "pnl_pct": t[10], "followup": t[17]})
            legs.append(leg)
        rounds.append({
            "symbol": code, "name": buys[0][4], "side": "long",
            "entry_date": str(entry_date), "exit_date": str(sells[-1][0]),
            "shares": sum(t[5] for t in buys), "amount": round(cost, 2),
            "pnl_abs": round(pnl, 2), "pnl_pct": round(pnl / cost * 100, 2) if cost else None,
            "pnl_gross": round(gross, 2), "fee": round(fee, 4),
            "hold_days": sells[-1][11], "adds": len(buys) - 1, "legs": legs,
            # 进场当天评定的档位(C-08),用户 2026-09-12 要求标在代号下方;方向 A/B/base 的引擎不评分 → None,前端不画
            "grade": (buys[0][18] if len(buys[0]) > 18 else None),
            "scan_dates": (scan_of or {}).get(code, []),          # 淡蓝:进了这条线的候选池
            "setup_dates": ((layers or {}).get(code) or {}).get("setup", []),   # 中蓝:形态就绪(只有声明了 SETUP_RULES 的引擎有)
            "entry_dates": ((layers or {}).get(code) or {}).get("entry", []),   # 深蓝:买入条件全满足
        })
    # 按平仓日排;编号按时间正序给(1 = 第一笔),前端倒序显示,和券商对账单一个习惯
    rounds.sort(key=lambda r: (r["exit_date"], r["entry_date"], r["symbol"]))
    for i, r in enumerate(rounds):
        r["no"] = i + 1
    rounds.reverse()
    return rounds


def _trade_item(t, market: str = MARKET) -> dict:
    it = ({"ts_market": "16:00", "ts_market_tz": "ET", "ts_local": "收盘(次日 04:00 沪)"} if market == "us" else
          {"ts_market": "15:00", "ts_market_tz": "CST", "ts_local": "收盘(15:00 沪)"})
    it.update({"side": t[2], "symbol": t[3], "name": t[4], "shares": t[5], "price": t[6],
               "rule_id": t[12], "rule_name": t[13], "rationale": t[14], "followup": None, "adjustment": None})
    if t[2] == "buy":
        it.update({"amount": t[7], "position_pct": t[8]})
        if len(t) > 18 and t[18]:
            it["rule_name"] = f"{t[13]} · {t[18]} 级"
    else:
        it.update({"pnl_abs": t[9], "pnl_pct": t[10], "hold_days": t[11],
                   "followup": t[17] or "事后跟踪 · 卖出后 T+5 会自动回填,判断这次是「卖早了」还是「躲过了」"})
    return it


def _strategy_block(universe_size, st: dict, branch: str = "base") -> dict:
    p = st["params"]
    eng = ao.engine_of(branch)
    names = {"vcp": STRATEGY_NAME, "vcp3": "VCP 三段式(方向 C)", "vcp4": "VCP · SEPA 优化(方向 A)",
             "donchian": "唐奇安通道突破", "breakout": "突破买入(Patrick Walker 风格)", "limitup": "涨停后强势整理(A 股)",
             "limitup_yin": "涨停 + 三根阴线(A 股主板)"}
    days_ = _pool_days(ao.BRANCHES[branch]["engine"])
    span = "当天筛选结果" if days_ <= 1 else f"近 {days_} 天并集"
    return {"name": names.get(ao.BRANCHES[branch]["engine"], STRATEGY_NAME), "version": f"v{st['version']}",
            "summary": eng.summary(p),
            "market_label": ao.currency(branch)["market_label"], "market_note": getattr(eng, "EXEC_NOTE", "纸上交易 · 日线收盘价成交"),
            "universe": (f"{getattr(eng, 'POOL_LABEL')} · {span}" if getattr(eng, "POOL", None)
                         else f"筛选器「VCP 波段收缩」{span}"), "universe_size": universe_size,
            "rebalance": "每个交易日收盘后跑一次 · 信号当天收盘价成交" + getattr(eng, "REBALANCE_SUFFIX", ""),
            "data_source": f"自家全市场日线(每晚落库,拆股已核对)+ {ao.currency(branch)['bench_label']} 基准 · 不含盘中"}


def _guard_block(halt_reason, p: dict, branch: str | None = None) -> dict:
    g = av.GUARDS
    # 引擎声明 NO_GUARDS(涨停后强势整理线:信号彼此独立)→ 熔断 / 连亏两项给 null 并带 guards_off,
    # 前端显示「不设」—— 照抄 GUARDS 的 -3% / 3 笔会让人以为这条线有护栏
    off = bool(branch and getattr(ao.engine_of(branch), "NO_GUARDS", False))
    return {"initial_capital": ao.initial_capital(branch) if branch else g["initial_capital"],
            "max_position_pct": int(p.get("max_single_stock_pct", p.get("max_pos_pct", 0)) * 100),
            "max_holdings": p["max_holdings"], "daily_loss_halt_pct": None if off else g["daily_loss_halt_pct"],
            "consecutive_loss_pause": None if off else g["consecutive_loss_pause"], "guards_off": off, "long_only": True,
            "triggered_today": bool(halt_reason) if halt_reason is not None else False,
            "triggered_text": halt_reason}


def _rules_block(trades, poss, days, p: dict, st: dict | None = None, eng=av) -> list[dict]:
    by_rule: dict = {}
    for t in trades:
        by_rule.setdefault(t[12], []).append(t)
    cycles: dict = {}
    for t in trades:
        if t[2] == "sell" and t[15]:
            k = (t[3], str(t[15]))
            cycles[k] = cycles.get(k, 0) + (t[9] or 0)
    open_keys = {(x[0], str(x[4])) for x in poss}
    done = {k: v for k, v in cycles.items() if k not in open_keys}
    entry_ids = [r["id"] for r in eng.RULES if r["kind"] == "buy"]
    all_ids = [r["id"] for r in eng.RULES]
    blocked: dict = {}
    for r in days:
        for w in (r[5] or []):
            gap = w.get("gap") or ""
            if "还差" in gap:
                for rid in entry_ids:
                    if rid in gap:
                        blocked[rid] = blocked.get(rid, 0) + 1
            br = w.get("blocked_reason") or ""
            for rid in all_ids:
                if rid in br:
                    blocked[rid] = blocked.get(rid, 0) + 1
    changed = {v["key"]: v for v in (st or {}).get("versions", [])}
    key_of_rule = eng.RULE_PARAM_KEY
    entry_rule = eng.ENTRY_RULE
    add_rule = getattr(eng, "ADD_RULE", "R-16")          # 加仓规则:统计「触发」次数
    grade_rule = getattr(eng, "GRADE_RULE", "C-08")      # 评分规则:统计每档的完整周期结果
    out = []
    for r in eng.rules_for(p):
        rid = r["id"]
        stats = []
        if rid == entry_rule:
            stats.append({"label": "触发(开仓)", "value": len(by_rule.get(entry_rule, []))})
            if done:
                w = sum(1 for v in done.values() if v > 0)
                stats.append({"label": "触发后胜率", "value": f"{w / len(done) * 100:.0f}%"})
                stats.append({"label": "样本", "value": f"{len(done)} 个完整持仓周期" + ("(不足 15)" if len(done) < 15 else ""), "dim": True})
            else:
                stats.append({"label": "触发后胜率", "value": None})
                stats.append({"label": "样本", "value": "还没有走完的持仓周期", "dim": True})
        elif rid == grade_rule:
            # 每档的完整周期结果:评分记在买入那笔上,周期盈亏按 (code, entry_date) 加总
            g_of = {(t[3], str(t[15])): t[18] for t in trades if t[2] == "buy" and len(t) > 18 and t[18]}
            for gk in ("S", "A", "B", "C"):
                pn = [v for k, v in done.items() if g_of.get(k) == gk]
                stats.append({"label": f"{gk} 级", "value": (f"{len(pn)} 笔 · 胜率 {sum(1 for x in pn if x > 0) / len(pn) * 100:.0f}% · "
                                                              f"均 {sum(pn) / len(pn):+,.0f} 美元") if pn else "0 笔"})
            stats.append({"label": "挡下(D 级或否决)", "value": blocked.get(grade_rule, 0)})
        elif r["kind"] == "buy" and rid != add_rule:
            stats.append({"label": "挡下候选", "value": blocked.get(rid, 0)})
            stats.append({"label": "说明", "value": "买入条件要同时满足,单条不单独产生交易", "dim": True})
        elif r["kind"] == "sell" or rid == add_rule:
            ts = by_rule.get(rid, [])
            stats.append({"label": "触发", "value": len(ts)})
            if rid != add_rule:
                pn = [t[10] for t in ts if t[10] is not None]
                stats.append({"label": "平均盈亏", "value": f"{sum(pn) / len(pn):+.1f}%" if pn else None})
                stats.append({"label": "触发后胜率", "value": f"{sum(1 for x in pn if x > 0) / len(pn) * 100:.0f}%" if pn else None})
        elif r["kind"] == "risk" or rid in ("R-18", "R-19", "C-07", "C-03", "C-09", "C-04"):
            stats.append({"label": "挡下候选", "value": blocked.get(rid, 0)})
        else:
            stats.append({"label": "说明", "value": "仓位算法,每次开仓 / 加仓都经过", "dim": True})
        k = key_of_rule.get(rid)
        since = f"v{changed[k]['version']} 改" if k and k in changed else "v1 起"
        obs = (st or {}).get("observing")
        status = "observing" if obs and obs.get("key") == k else "active"
        out.append({"id": rid, "kind": r["kind"], "condition": r["condition"], "since_text": since,
                    "status": status, "stats": stats})
    return out


# ═══════════════════════════════════════════════════════════════
# 控制
# ═══════════════════════════════════════════════════════════════

def set_state(state: str) -> str:
    conn = _conn()
    cur = conn.cursor()
    _meta_set(cur, "state", state)
    conn.commit()
    cur.close()
    conn.close()
    return state


def run_latest() -> dict:
    """「立即跑一次」:跑基准最新那一天(还没跑过才真跑)。暂停状态下不跑。"""
    conn = _conn()
    cur = conn.cursor()
    st = _meta_get(cur, "state", "never_started")
    cur.close()
    conn.close()
    if st == "paused":
        return {"ran": False, "reason": "已暂停,先恢复"}
    # 一行都没有的方向不由每晚任务起步,要先 research-backfill(2026-09-15 加「突破买入 · 三年」时加的):
    # 否则回填还没跑 / 中途失败时,每晚任务会让它从今天起步,三年那条的起点就被占了。全都没有行 = 全新安装,照常起步
    started = set(_with_cur(lambda c: (c.execute("SELECT DISTINCT branch FROM agent_day"), [r[0] for r in c.fetchall()])[1]))
    only = [b for b in BRANCHES if b in started] if started else None
    if started and len(only) < len(BRANCHES):
        log.info("[agent] 这些方向还没回填过,每晚任务不替它们起步:%s", [b for b in BRANCHES if b not in started])
    ctx = Ctx(MARKET)
    if not ctx.store["last"]:
        return {"ran": False, "reason": "还没有日线"}
    earn = _ensure_earnings(ctx.store["last"] - timedelta(days=14), ctx.store["last"])
    out = run_date(ctx.store["last"], ctx, only)
    if earn is not None:
        out["earnings"] = earn
    # 其他市场的方向(A 股线 2026-09-17):各自装一份日线,跑那个市场最新的交易日。
    # 全新安装(started 为空)不替它们起步 —— 美股以外的线都要先 research-backfill
    others = sorted({ao.market_of(b) for b in (only or [])} - {MARKET})
    for mk in others:
        try:
            cx = Ctx(mk)
            if cx.store["last"]:
                out.setdefault("markets", {})[mk] = run_date(cx.store["last"], cx, only)
        except Exception as e:                                # noqa: BLE001
            # 一个市场失败不挡美股那边已经跑完的结果
            log.exception("[agent] %s 市场的方向每日运行失败", mk)
            out.setdefault("markets", {})[mk] = {"ran": False, "error": str(e)[:200]}
    out["research"] = research_evaluate()
    return out


# ═══════════════════════════════════════════════════════════════
# 研究台(agent_research)
# ═══════════════════════════════════════════════════════════════

def _with_cur(fn, commit: bool = False):
    conn = _conn()
    cur = conn.cursor()
    try:
        r = fn(cur)
        if commit:
            conn.commit()
        return r
    finally:
        cur.close()
        conn.close()


def research_evaluate() -> list[dict]:
    """回测关 / 30 笔判定,状态有变就落库。每日跑完、研究线回填完各调一次。失败只记日志,不影响当天的运行结果。"""
    from app.services.quant import agent_research as research
    try:
        return _with_cur(research.evaluate, commit=True)
    except Exception:                                         # noqa: BLE001
        log.exception("[agent] 研究线判定失败")
        return []


def research_board() -> dict:
    from app.services.quant import agent_research as research
    return _with_cur(research.board)


def research_create(uid, body: dict) -> dict:
    from app.services.quant import agent_research as research
    return _with_cur(lambda cur: research.create_idea(cur, uid, body), commit=True)


def research_archive(key: str, archived: bool, uid) -> dict:
    from app.services.quant import agent_research as research
    return _with_cur(lambda cur: research.set_archived(cur, key, archived, uid), commit=True)


def _ensure_earnings(start: date, last: date, only: list[str] | None = None) -> dict | None:
    """突破买入 v14:跑之前把财报日历补到 max(最新交易日, 今天) + FUTURE_DAYS;未来的日子重拉(日期会改)。
    没有方向用财报日 → None。拉取失败只记日志:没拉到的日子引擎按「日历缺」不买,不在这里挡住整轮。"""
    from app.services.quant import earnings_dates as ed
    if not any(getattr(ao.engine_of(b), "EARNINGS_KEY", None) for b in (only or BRANCHES)):
        return None
    today = date.today()
    try:
        return ed.ensure(start, max(last, today) + timedelta(days=ed.FUTURE_DAYS), refresh_from=min(last, today))
    except Exception as e:                                    # noqa: BLE001
        log.exception("[agent] 财报日历补拉失败")
        return {"error": str(e)}


def backfill(start: date, end: date | None = None, only: list[str] | None = None) -> dict:
    markets = {ao.market_of(b) for b in (only or BRANCHES)}
    if len(markets) > 1:
        raise ValueError(f"一次回填只能跑一个市场的方向,现在混了 {sorted(markets)};用 --line / --branch 分开跑")
    ctx = Ctx(markets.pop())
    _ensure_earnings(start, ctx.store["last"], only)
    days = sorted(d for d in ctx.store["bench"] if d >= start and (end is None or d <= end))
    out = {"days": 0, "fills": 0}
    for d in days:
        r = run_date(d, ctx, only)
        out["days"] += r.get("ran", False)
        out["fills"] += sum(b["fills"] for b in r.get("branches", {}).values())
        log.info("[agent] 回填 %s → %s", d, r)
    return out


def backfill_watch_fails(branch: str) -> dict:
    """给老的观察列表记录补 fails(悬停日K「形态就绪」那层要用),只补**成交过的票**。

    watch_item 从 2026-09-15 起才输出 fails,之前落库的 agent_day.watchlist 没有。重算口径与 run_date 相同:
    指标 = eng.indicators(截到那天的日线, bench=…)(同 Ctx.ensure_cache)、市场 = eng.market_regime(基准截到那天)、
    参数 = 这个方向的现行参数、score = 那条记录当时存的 RS。
    - 方向换过版(versions 非空)不回填:按今天的参数判过去会画错,宁可那层空着
    - 每条重算的 progress_pct 都和库里存的比一次,对不上的计数并给样例 —— 那是口径漂移的信号,不是小事
    """
    eng = ao.engine_of(branch)
    if not getattr(eng, "SETUP_RULES", None):
        return {"branch": branch, "skipped": "这个引擎没声明形态就绪规则(SETUP_RULES),不用补"}
    conn = _conn()
    cur = conn.cursor()
    st = _branch_state(cur, branch)
    if st.get("versions"):
        cur.close()
        conn.close()
        return {"branch": branch, "skipped": f"这个方向换过 {len(st['versions'])} 版参数,按现行参数回填会判错过去,不补"}
    p = st["params"]
    cur.execute("SELECT DISTINCT code FROM agent_trade WHERE branch=%s", (branch,))
    codes = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT trade_date, watchlist FROM agent_day WHERE branch=%s ORDER BY trade_date", (branch,))
    days = cur.fetchall()
    ctx = Ctx(ao.market_of(branch))
    bb = ctx.store.get("bench_bars") or []
    bdates = [b[0] for b in bb]
    bars_of: dict = {}
    stats = {"branch": branch, "codes": len(codes), "days": len(days), "filled": 0, "already": 0,
             "no_indicator": 0, "progress_mismatch": 0, "mismatch_samples": []}
    for d, wl in days:
        items = json.loads(wl) if isinstance(wl, str) else (wl or [])
        todo = [it for it in items if isinstance(it, dict) and it.get("symbol") in codes
                and it.get("progress_pct") is not None]
        if not todo:
            continue
        market = eng.market_regime(bb[:bisect_right(bdates, d)]) if hasattr(eng, "market_regime") else None
        changed = False
        for it in todo:
            if "fails" in it:
                stats["already"] += 1
                continue
            code = it["symbol"]
            if code not in bars_of:
                bars = ctx.bars_of(code) or []
                bars_of[code] = (bars, {b[0]: i for i, b in enumerate(bars)})
            bars, idx = bars_of[code]
            i = idx.get(d)
            ind = eng.indicators(bars[:i + 1], bench=ctx.store["bench"]) if i is not None else None
            if ind is None:
                stats["no_indicator"] += 1
                continue
            checks = eng.entry_checks(ind, p, it.get("score"), market)
            it["fails"] = [c["rule"] for c in checks if not c["ok"]]
            prog = int(sum(1 for c in checks if c["ok"]) / len(checks) * 100)
            if prog != it.get("progress_pct"):
                stats["progress_mismatch"] += 1
                if len(stats["mismatch_samples"]) < 10:
                    stats["mismatch_samples"].append([str(d), code, it.get("progress_pct"), prog])
            stats["filled"] += 1
            changed = True
        if changed:
            cur.execute("UPDATE agent_day SET watchlist=%s WHERE branch=%s AND trade_date=%s",
                        (json.dumps(items), branch, d))
            conn.commit()
    cur.close()
    conn.close()
    return stats


def reset() -> None:
    conn = _conn()
    cur = conn.cursor()
    for t in ("agent_trade", "agent_position", "agent_day", "agent_meta", "agent_watch"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit()
    cur.close()
    conn.close()


def _main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(prog="agent_run")
    p.add_argument("cmd", choices=["daily", "backfill", "reset", "dashboard", "research-backfill", "research", "watch-fails", "earnings"])
    p.add_argument("--line", default=None)
    p.add_argument("--branch", default=None)
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--yes", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "daily":
        print(run_latest())
    elif a.cmd == "backfill":
        if not a.start:
            print("要 --from YYYY-MM-DD")
            return 2
        print(backfill(date.fromisoformat(a.start), date.fromisoformat(a.end) if a.end else None))
    elif a.cmd == "research-backfill":
        # 给一条研究线的方向补跑全年:run_date 只跑这天还没跑过的方向,VCP 那几个不会被碰
        from app.services.quant import agent_research as research
        lines = {x["key"]: x for x in _with_cur(research.all_lines)}
        if a.line not in lines or not lines[a.line]["branches"]:
            print(f"要 --line,且那条线得有方向:{[k for k, v in lines.items() if v['branches']]}")
            return 2
        start = date.fromisoformat(a.start) if a.start else date.fromisoformat(_with_cur(lambda c: _meta_get(c, "started")))
        # --branch:一条线有几个方向、起点不同时只回填其中一个(2026-09-15 突破买入线加了三年方向,
        # 不限定的话回填一年方向会连带把三年方向从 2025-09 起跑出行来,三年那条的起点就被占了)
        only = lines[a.line]["branches"]
        if a.branch:
            if a.branch not in only:
                print(f"--branch 要是这条线的方向之一:{only}")
                return 2
            only = [a.branch]
        print(backfill(start, date.fromisoformat(a.end) if a.end else None, only=only))
        print(research_evaluate())
    elif a.cmd == "earnings":
        # 突破买入 v14:单独补拉财报日历(回填会自动补;这里给先拉好再回填、或排查某几天用)
        from app.services.quant import earnings_dates as ed
        if not a.start:
            print("要 --from YYYY-MM-DD(--to 默认今天 + 21 天)")
            return 2
        end = date.fromisoformat(a.end) if a.end else date.today() + timedelta(days=ed.FUTURE_DAYS)
        print(json.dumps(ed.ensure(date.fromisoformat(a.start), end, refresh_from=date.today()), ensure_ascii=False))
    elif a.cmd == "watch-fails":
        # 悬停日K「形态就绪」那层要的 fails,给 2026-09-15 之前落库的观察列表补上(只补成交过的票)
        if not a.branch:
            print("要 --branch(比如 breakout)")
            return 2
        print(json.dumps(backfill_watch_fails(a.branch), ensure_ascii=False, default=str))
    elif a.cmd == "research":
        b = research_board()
        for ln in b["lines"]:
            print(ln["key"], ln["status"], json.dumps(ln["metrics"], ensure_ascii=False, default=str), (ln.get("verdict") or {}).get("text"))
    elif a.cmd == "reset":
        if not a.yes:
            print("会清空小鹿的全部交易记录,确认加 --yes")
            return 2
        reset()
        print("已清空")
    elif a.cmd == "dashboard":
        for b in BRANCHES:
            d = dashboard(b)
            print(b, json.dumps({k: d.get(k) for k in ("state", "day_count", "version", "overview")}, ensure_ascii=False, default=str)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(_main())
