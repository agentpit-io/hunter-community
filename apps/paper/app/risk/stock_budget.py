"""日线v2受理买单前，以真实估值复核25%上限；缺估值拒绝，卖出不拦。"""
from decimal import Decimal


def exceeds_cap(*, equity, symbol_value, order_value, fee, account_cap):
    account_cap = Decimal(str(account_cap))
    if not account_cap.is_finite():return True
    cap = min(Decimal('.25'), account_cap)
    net = Decimal(str(equity))-Decimal(str(fee))
    values = [cap,net,Decimal(str(symbol_value)),Decimal(str(order_value))]
    return any(not x.is_finite() for x in values) or cap<=0 or net<=0 or values[2]+values[3]>net*cap


def reject_reason(cur,req,market,price,fee,now):
    if req['side']!='buy' or not str((req.get('intent_ref') or {}).get('strategy_key','')).startswith('tq_daily_v2_'):
        return None
    from app import ledger,snapshot
    cur.execute('SELECT max_position_pct FROM fin_param WHERE project_id=%s',(req['project_id'],))
    param=cur.fetchone()
    if not param or param['max_position_pct'] is None:
        return '日线策略缺少账户单股上限'
    available,frozen=ledger.cash_balance(cur,req['project_id'],market)
    equity=available+frozen
    symbol_value=Decimal(0)
    for pos in ledger.list_positions(cur,req['project_id'],market):
        if int(pos['qty'])<=0:continue
        try:
            quote=snapshot.capture(cur,pos['code'],now=now)
            px=Decimal(str(quote['last_price']))
            if not px.is_finite() or px<=0:raise ValueError('invalid valuation')
        except (ValueError,TypeError,KeyError):
            return '日线策略持仓估值不足，拒绝买入'
        value=Decimal(pos['qty'])*px
        equity+=value
        if pos['code']==req['code']:symbol_value=value
    if exceeds_cap(equity=equity,symbol_value=symbol_value,order_value=Decimal(req['qty'])*price,
                   fee=fee,account_cap=param['max_position_pct']):
        return '日线策略单股持仓超过25%或账户更严格上限（含费用）'
    return None
