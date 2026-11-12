"""日线研究策略同股占用：在账本事务里串行核对，避免只读检查的并发竞态。"""


def reject_reason(cur, req, market):
    intent = req.get("intent_ref") or {}
    if req["side"] != "buy" or not str(intent.get("strategy_key","")).startswith(("tq_daily_v1_","tq_daily_v2_")):
        return None
    code, pid = req["code"], req["project_id"]
    # 与订单事务一起释放。持仓、部分成交与挂单同一项目只算一个占用。
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"tq-stock:{market}:{code}",))
    cur.execute("SELECT project_id FROM fin_position WHERE market=%s AND code=%s AND qty>0 "
                "UNION SELECT project_id FROM fin_order WHERE market=%s AND code=%s "
                "AND side='buy' AND status IN ('pending','accepted','partially_filled')",
                (market,code,market,code))
    holders = {str(row["project_id"]) for row in cur.fetchall()}
    if pid in holders:
        return "日线基础策略禁止重复买入或加仓"
    if len(holders)>=3:
        return "同市场同股持仓及挂单已达到三位选手上限"
    return None
