"""模拟股票策略只读输入；日期、板块、待成交与冷却均显式核对。"""
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg2.extras

from app.services.fin import store
from app.services import stock_rules as rules

ZONES = {"CN_A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}


def read_inputs(project_id, market, key, now):
    family = rules.family_for(key)
    if family is None or market not in ZONES:
        raise ValueError("未知策略或市场")
    local_date = now.astimezone(ZoneInfo(ZONES[market])).date()
    conn = store.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT playbook,sector FROM fin_horse WHERE project_id=%s AND market=%s",
                        (project_id, market))
            horse = cur.fetchone()
            if not horse or horse["playbook"] != family:
                raise ValueError("策略族与选手档案不匹配")
            cur.execute("SELECT MAX(trade_date) AS day FROM fin_market_calendar "
                        "WHERE market=%s AND is_trading=true AND trade_date<%s", (market, local_date))
            cutoff = cur.fetchone()["day"]
            if cutoff is None:
                raise ValueError("缺少上一交易日日历")
            cur.execute("SELECT * FROM fin_position WHERE project_id=%s AND market=%s AND qty>0",
                        (project_id, market))
            positions = list(cur.fetchall())
            cur.execute("SELECT code,side,status,created_at FROM fin_order WHERE project_id=%s "
                        "AND market=%s AND status IN ('pending','accepted','partially_filled')",
                        (project_id, market))
            pending = list(cur.fetchall())
            # 冷却从实际最后一次卖出成交开始，日期按所属市场转换。
            cur.execute("SELECT code,MAX(traded_at) AS last_sell FROM fin_trade WHERE project_id=%s "
                        "AND market=%s AND side='sell' GROUP BY code", (project_id, market))
            sells = {r["code"]: r["last_sell"].astimezone(ZoneInfo(ZONES[market])).date()
                     for r in cur.fetchall()}
            cur.execute("SELECT MIN(trade_date) AS day FROM fin_market_calendar WHERE market=%s "
                        "AND is_trading=true AND trade_date>=%s AND trade_date<=%s",
                        (market, local_date-timedelta(days=local_date.weekday()), local_date))
            first_week_day = cur.fetchone()["day"]
            cur.execute("SELECT code FROM fin_candidate WHERE project_id=%s AND market=%s "
                        "AND trade_date=%s AND as_of<=%s AND detail->>'sector'=%s ORDER BY rank,code LIMIT 50",
                        (project_id, market, local_date, now, horse["sector"]))
            codes = [r["code"] for r in cur.fetchall()]
            held = {p["code"]: p for p in positions}
            all_codes = sorted(set(codes) | set(held))
            cur.execute("SELECT code,ts,open,high,low,close,volume FROM (SELECT code,trade_date AS ts,"
                        "open,high,low,close,volume,ROW_NUMBER() OVER(PARTITION BY code ORDER BY trade_date DESC) n "
                        "FROM rs_daily WHERE market=%s AND code=ANY(%s) AND trade_date<=%s) s "
                        "WHERE n<=320 ORDER BY code,ts", ({'CN_A':'a','HK':'hk','US':'us'}[market],all_codes,cutoff))
            histories = {c: [] for c in all_codes}
            for bar in cur.fetchall():
                histories[bar["code"]].append({k: str(v) if k == "ts" else
                    (float(v) if v is not None else None) for k,v in bar.items() if k != "code"})
            records = []
            for code in all_codes:
                bars = histories[code]
                if not bars or bars[-1]["ts"] != cutoff.isoformat():
                    signal = dict(action="wait", status="data_pending", reason="上一交易日行情缺失")
                else:
                    try:
                        pos = held.get(code)
                        if pos:
                            pos = {**pos, "opened_at": pos["opened_at"].astimezone(
                                ZoneInfo(ZONES[market])).date().isoformat()}
                        signal = rules.evaluate(family, bars, pos)
                        if code in sells:
                            cur.execute("SELECT COUNT(*) AS n FROM fin_market_calendar WHERE market=%s "
                                        "AND is_trading=true AND trade_date>%s AND trade_date<=%s",
                                        (market, sells[code], cutoff))
                            signal["cooldown_remaining"] = max(0,rules.SPECS[family].get("cooldown",0)-cur.fetchone()["n"])
                    except (ValueError, TypeError, KeyError, ArithmeticError):
                        signal = dict(action="wait", status="data_pending", reason="日线或持仓输入无法计算")
                records.append(dict(code=code, candidate=code in codes, held=code in held, **signal))
            ranked = sorted([r for r in records if r.get("momentum") is not None],
                            key=lambda r: (-r["momentum"], r["code"]))
            for rank, row in enumerate(ranked,1):
                row["momentum_rank"] = rank
                if family == "leader_break" and row["action"] == "buy" and (
                        len(ranked)<5 or rank>max(1,int(len(ranked)*.2))):
                    row.update(action="wait", reason="未进入候选动量前20%")
            # 新轮动标的只在当前可选股票中排名；持有票仍参与保留区判断。
            if family == "quant_rotate":
                rotation_ranks = sorted([r for r in records if r.get("above_trend")],
                                        key=lambda r:(-r["momentum"],r["code"]))
                for rank,row in enumerate(rotation_ranks,1):
                    row["momentum_rank"] = rank
            return dict(family=family, key=key, version=rules.VERSION, fingerprint=rules.FINGERPRINT,
                        spec=rules.SPECS[family], signal_date=cutoff.isoformat(),
                        rotation_day=first_week_day == local_date, records=records,
                        pending_codes=sorted({p["code"] for p in pending}),
                        blocked=rules.SPECS[family].get("blocked"))
    finally:
        conn.close()
