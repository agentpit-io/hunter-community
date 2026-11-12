"""日线策略到现有paper契约的适配器；无账本连接、无模型调用。"""
from datetime import timedelta
from decimal import Decimal

from app.bridge.contracts import CONTRACT_VERSION
from app.strategy import memory_gate
from app.strategy.stock_rules import family_for, size_order


def build(*, req, view, api, paper, strategy_key, strategy_version, now, market, mos, ttl):
    project = view["project"]
    pid = req["project_id"]
    def wait(reason):
        return dict(halted=True, project_id=pid, reason=reason, decision=None,
                    idempotency_key=None, command=None)
    try:
        resp = api._request("GET", "/api/internal/fin/stock-strategy/inputs", params={
            "project_id":pid, "market":market, "key":strategy_key, "now":now.isoformat()})
        if resp.status_code != 200:
            return wait("日线策略输入不可用，等待核对")
        data = resp.json()
        if data["key"] != strategy_key or data["family"] != family_for(strategy_key):
            return wait("日线策略输入版本不匹配")
        from app.strategy.stock_rules import FINGERPRINT
        if data["fingerprint"] != FINGERPRINT:
            return wait("研究与执行器规则指纹不匹配")
        entry = next((s for s in (view.get("param") or {}).get("strategies",[])
                      if s.get("key")==strategy_key and s.get("active")),{})
        if (entry.get("params") or {}).get("rule_fingerprint") != FINGERPRINT:
            return wait("已登记策略与执行代码指纹不匹配")
        rows = data["records"]
        pending = set(data["pending_codes"])
        positions = {p["code"]:p for p in view.get("positions",[]) if int(p["qty"])>0}
        # 原有账户止损继续生效；策略改版不放宽已有保护。
        param = view.get("param") or {}
        for row in rows:
            if row["code"] not in positions:
                continue
            pos = positions[row["code"]]
            px = api.quote(row["code"]).get("last_price")
            if px is None or float(px)<=0 or float(pos["avg_cost"])<=0:
                continue
            hit_rule = row.get("stop_price") is not None and float(px)<=row["stop_price"]
            old_stop = param.get("stop_loss_pct")
            hit_account = old_stop is not None and float(px)/float(pos["avg_cost"])-1<=-abs(float(old_stop))
            if hit_rule or hit_account:
                row.update(action="sell",reason="ATR保护止损" if hit_rule else "账户保护止损")
            elif param.get("take_profit_pct") is not None and float(px)/float(pos["avg_cost"])-1>=abs(float(param["take_profit_pct"])):
                row.update(action="sell",reason="账户止盈上限")
            elif param.get("hold_days_max") and row.get("holding_trading_days",0)>=int(param["hold_days_max"]):
                row.update(action="sell",reason="账户交易日持仓期限")
        # 原有持仓与策略保护退出优先；买入经验不得阻拦卖出。
        candidates = [r for r in rows if r["action"] == "sell" and r["code"] in positions]
        if data["family"] == "quant_rotate" and data["rotation_day"]:
            ranked = [r for r in rows if r.get("above_trend") and r.get("momentum_rank")]
            keep = {r["code"] for r in sorted(ranked,key=lambda r:r["momentum_rank"])[:6]}
            candidates += [{**r,"action":"sell","reason":"周度排名退出保留区"} for r in rows
                           if r["code"] in positions and r["code"] not in keep
                           and r["action"] == "hold"]
        for row in sorted(candidates,key=lambda r:(r.get("reason")!="ATR保护止损",r["code"])):
            code = row["code"]
            if code in pending:
                continue
            pos = positions[code]
            qty = int(pos["sellable_qty"] if market == "CN_A" else pos["qty"])
            if qty>0:
                px = api.quote(code).get("last_price")
                if px is not None and Decimal(str(px))>0:
                    return _decision(req,project,strategy_key,strategy_version,now,ttl,market,
                                     code,"sell",qty,px,mos,row,data)
        if candidates:
            return wait("退出信号已触发，等待可卖数量、行情或在途委托")
        if data.get("blocked"):
            return wait(data["blocked"])
        # 有任何在途订单则本轮等待，避免重复入场及低估现金占用。
        if pending:
            return wait("已有在途委托，等待成交或撤单")
        spec = data["spec"]
        if param.get("max_positions") is None:
            return wait("账户持仓上限缺失")
        if len(positions)>=min(spec["positions"],int(param["max_positions"])):
            return wait("已达到策略或账户持仓上限")
        exposure = 0.0
        for code,pos in positions.items():
            px = api.quote(code).get("last_price")
            if px is None or float(px)<=0:
                return wait("持仓估值行情不足")
            exposure += int(pos["qty"])*float(px)
        cash = view.get("cash") or {}
        if cash.get("available") is None or cash.get("frozen") is None:
            return wait("账户现金数据不足")
        equity = float(cash["available"])+float(cash["frozen"])+exposure
        occupied = api.get_occupied_positions(current_project_id=pid,market=market)
        holders = {}
        for occ in occupied:
            holders.setdefault(occ["code"],set()).add(occ["project_id"])
        entries = [r for r in rows if r["action"] == "buy" and r["candidate"]
                   and r["code"] not in positions and not r.get("cooldown_remaining")]
        if data["family"] == "quant_rotate":
            if not data["rotation_day"]:
                return wait("等待本周首个交易日轮动")
            entries = sorted(entries,key=lambda r:(-r["momentum"],r["code"]))[:4]
        else:
            entries = sorted(entries,key=lambda r:(-r.get("momentum",0),r["code"]))
        blocked_memory = False
        for row in entries:
            code = row["code"]
            if len(holders.get(code,set()))>=3:
                continue
            if memory_gate.blocking_experience((req.get("memory") or {}).get("items") or [],
                                               market=market,code=code):
                blocked_memory = True
                continue
            instrument = paper.get_instrument(code) or {}
            px = api.quote(code).get("last_price")
            entry_spec = spec
            if data["family"] == "quant_rotate":
                total = sum(1/r["volatility"] for r in entries if r["volatility"]>0)
                if total<=0 or row["volatility"]<=0:
                    continue
                entry_spec = {**spec,"weight":min(spec["weight"],spec["exposure"]/row["volatility"]/total)}
            qty = size_order(entry_spec,equity=equity,available=cash["available"],exposure=exposure,
                             symbol_value=0,price=px,distance=row.get("stop_distance"),
                             lot=instrument.get("lot_size"),param=param,market=market)
            if qty>0:
                return _decision(req,project,strategy_key,strategy_version,now,ttl,market,
                                 code,"buy",qty,px,mos,row,data)
        reason = "入场被经验或同股占用限制" if blocked_memory else (
            "信号满足，但资金、交易单位、最小金额或占用限制不允许入场" if entries else
            next((r["reason"] for r in rows if r["action"]=="wait"),"没有符合条件的板块候选"))
        return wait(reason)
    except (KeyError,ValueError,TypeError,ArithmeticError) as exc:
        return wait(f"日线策略输入无法计算：{type(exc).__name__}")


def _decision(req,project,key,version,now,ttl,market,code,side,qty,px,mos,row,data):
    return dict(contract_version=CONTRACT_VERSION,
        decision_id=f"{key}-{data['signal_date']}-{side}-{code}", strategy_key=key,
        strategy_version=version, account_version=int(project["version"]),mode="PAPER",
        portfolio_version=int(project["version"]),decision_as_of=now.isoformat(),
        data_snapshot=dict(kind="tq_daily",project_id=req["project_id"],market=market,
                           signal_date=data["signal_date"],rule_fingerprint=data["fingerprint"],
                           signal=row,note=row["reason"],spec=data["spec"],reference_price=str(px)),
        intent=dict(code=code,side=side,qty=qty,price_type="market" if mos else "limit",
                    **({} if mos else {"limit_price":str(px)})),
        valid_until=(now+timedelta(seconds=ttl)).isoformat(), memory=req.get("memory") or {})
