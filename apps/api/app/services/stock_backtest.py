"""日线策略组合研究回测。固定当前候选池，不能声称历史成分无偏。"""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP

from app.services import stock_rules as rules


def fee(side,amount,model,stress=1):
    """与paper费用模型逐项同口径；测试逐值比对，未知模型拒绝。"""
    amount=Decimal(str(amount))
    def money(x):
        return x.quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)
    commission=max(money(amount*Decimal(str(model['commission_pct']))),Decimal(str(model['commission_min'])))
    total=commission
    for field,direction in [('stamp_tax_pct','stamp_side'),('transfer_fee_pct','transfer_fee_side')]:
        if model[direction] in (side,'both'):
            total+=money(amount*Decimal(str(model[field])))
    return float(money(total)*stress)


def equal_weight_baseline(histories,*,capital,lot_sizes,fee_model,start,slippage=.001,stress=1):
    """同候选池等权买入持有对照；整交易单位与费用后剩余资金留现金。"""
    valid={}
    for code,bars in histories.items():
        try:
            rules.validate_bars(bars)
            chosen=[b for b in bars if start is None or str(b['ts'])>start]
            if chosen and lot_sizes.get(code):
                valid[code]=chosen
        except (ValueError,TypeError,KeyError,ArithmeticError):
            continue
    if not valid:
        return None
    cash=float(capital)
    value=0
    count=0
    for code,bars in valid.items():
        lot=int(lot_sizes[code])
        px=float(bars[0]['open'])*(1+slippage)
        qty=int(capital/len(valid)/px/lot)*lot
        if qty<=0:
            continue
        charge=fee('buy',px*qty,fee_model,stress)
        if px*qty+charge>cash:
            continue
        cash-=px*qty+charge
        value+=qty*float(bars[-1]['close'])
        count+=1
    return dict(total_return=(cash+value)/capital-1,bought_symbols=count,
                note='同当前候选池整单位等权买入持有，末日按收盘估值未强平')


def run(family,histories,*,capital,lot_sizes,fee_model,param,start=None,slippage=.001,stress=1,
        market="CN_A",research_minimum=None):
    """t完整收盘信号→t+1实际日线开盘。模拟不填缺失价格，不强行补足订单下限。"""
    spec=rules.SPECS[family]
    if 'blocked' in spec:
        return dict(status='data_pending',reason=spec['blocked'],trades=[],metrics=None)
    prepared={}
    invalid={}
    for code,bars in histories.items():
        try:
            x=rules.indicators(bars)
            prepared[code]=(bars,x,{str(b['ts'])[:10]:i for i,b in enumerate(bars)})
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
            invalid[code]=type(exc).__name__
    days=sorted({d for _,_,idx in prepared.values() for d in idx})
    days=[d for d in days if start is None or d>=start]
    cash=float(capital)
    positions={}
    last_sell={}
    pending=[]
    equity_curve=[]
    trades=[]
    blocked=defaultdict(int)
    previous_week=None
    for day in days:
        # 只撮合上一交易日决定的目标，不拿同日收盘价做同日成交。
        for code,side,qty,signal in pending:
            bars,x,idx=prepared[code]
            if day not in idx:
                blocked['missing_next_open']+=1
                continue
            bar=bars[idx[day]]
            if float(bar['volume'])<=0:
                blocked['zero_volume']+=1
                continue
            px=float(bar['open'])*(1+slippage if side=='buy' else 1-slippage)
            charge=fee(side,px*qty,fee_model,stress)
            if side=='buy':
                if cash<px*qty+charge or code in positions:
                    blocked['budget_or_duplicate']+=1
                    continue
                if px*qty<rules.effective_minimum(market,research_minimum=research_minimum):
                    blocked['opening_min_order']+=1
                    continue
                opening_equity=cash
                missing=False
                for held,p in positions.items():
                    hb,_,hi=prepared[held]
                    if day not in hi:
                        missing=True
                        break
                    opening_equity+=p['qty']*float(hb[hi[day]]['open'])
                cap=min(.25,float(param['max_position_pct']))
                if missing or px*qty>cap*(opening_equity-charge):
                    blocked['opening_position_cap']+=1
                    continue
                cash-=px*qty+charge
                positions[code]=dict(qty=qty,avg_cost=px,opened_at=day)
            elif code in positions:
                cash+=px*qty-charge
                positions.pop(code)
                last_sell[code]=day
            else:
                continue
            trades.append(dict(code=code,side=side,qty=qty,price=px,fee=charge,
                               signal_date=signal['signal_date'],fill_date=day,reason=signal['reason']))
        pending=[]
        values={}
        signals={}
        for code,(bars,x,idx) in prepared.items():
            if day not in idx:
                continue
            i=idx[day]
            values[code]=float(bars[i]['close'])
            if i<130:
                continue
            signal=rules.evaluate(family,bars[:i+1],positions.get(code),ind=x)
            signals[code]=signal
        # 无当天估值的持仓使这一轮不能交易；净值明确缺失。
        if any(code not in values for code in positions):
            blocked['missing_position_valuation']+=1
            equity_curve.append(dict(date=day,equity=None))
            continue
        exposure=sum(p['qty']*values[c] for c,p in positions.items())
        equity=cash+exposure
        equity_curve.append(dict(date=day,equity=equity))
        ranked=sorted([c for c,s in signals.items() if s.get('above_trend')],
                      key=lambda c:(-signals[c]['momentum'],c))
        week=date.fromisoformat(day)-timedelta(days=date.fromisoformat(day).weekday())
        rotation=week!=previous_week
        previous_week=week
        keep=set(ranked[:6])
        exits=[]
        for code,p in positions.items():
            s=signals.get(code)
            if s is None:
                continue
            old_stop=param.get('stop_loss_pct')
            if old_stop is not None and values[code]/p['avg_cost']-1<=-abs(float(old_stop)):
                s={**s,'action':'sell','reason':'账户保护止损'}
            elif param.get('take_profit_pct') is not None and values[code]/p['avg_cost']-1>=abs(float(param['take_profit_pct'])):
                s={**s,'action':'sell','reason':'账户止盈上限'}
            elif param.get('hold_days_max') and s.get('holding_trading_days',0)>=int(param['hold_days_max']):
                s={**s,'action':'sell','reason':'账户交易日持仓期限'}
            if family=='quant_rotate' and rotation and code not in keep and s['action']=='hold':
                s={**s,'action':'sell','reason':'周度排名退出保留区'}
            if s['action']=='sell':
                exits.append((code,'sell',p['qty'],s))
        if exits:
            pending=exits
            continue
        limit=min(spec['positions'],int(param['max_positions']))
        if len(positions)>=limit or (family=='quant_rotate' and not rotation):
            continue
        entries=[c for c,s in signals.items() if s['action']=='buy' and c not in positions]
        if family=='leader_break':
            leaders=sorted(signals,key=lambda c:(-signals[c]['momentum'],c))
            entries=[c for c in entries if len(leaders)>=5 and c in leaders[:max(1,int(len(leaders)*.2))]]
        if family=='quant_rotate':
            entries=[c for c in ranked[:4] if c in entries]
        entries=sorted(entries,key=lambda c:(-signals[c]['momentum'],c))
        for code in entries:
            s=signals[code]
            cooldown=spec['cooldown']
            if code in last_sell and sum(last_sell[code]<d<=day for d in days)<cooldown:
                continue
            entry_spec=spec
            if family=='quant_rotate':
                inv=sum(1/signals[c]['volatility'] for c in entries if signals[c]['volatility']>0)
                if inv<=0 or s['volatility']<=0:
                    continue
                entry_spec={**spec,'weight':min(spec['weight'],spec['exposure']/s['volatility']/inv)}
            qty=rules.size_order(entry_spec,equity=equity,available=cash,exposure=exposure,
                                symbol_value=0,price=values[code],distance=s['stop_distance'],
                                lot=lot_sizes.get(code),param=param,market=market,
                                research_minimum=research_minimum)
            if qty>0:
                pending=[(code,'buy',qty,s)]
                break
            blocked['risk_budget_or_minimum']+=1
    valid=[r['equity'] for r in equity_curve if r['equity'] is not None]
    peak=float(capital)
    drawdown=0
    for value in valid:
        peak=max(peak,value)
        drawdown=max(drawdown,1-value/peak)
    metrics=None if not valid else dict(total_return=valid[-1]/capital-1,max_drawdown=drawdown,
        fees=sum(t['fee'] for t in trades),completed_exits=sum(t['side']=='sell' for t in trades),
        turnover=sum(t['price']*t['qty'] for t in trades)/capital,days=len(valid))
    return dict(status='research_only',metrics=metrics,trades=trades,equity_curve=equity_curve,
                blocked=dict(blocked),invalid=invalid,open_positions=positions,
                assumptions=dict(slippage=slippage,fee_stress=stress,fee_version=fee_model['version'],
                    universe='current_sector_snapshot',fills='next_open_daily_approximation',
                    corporate_actions='not_independently_reconciled',occupancy='isolated_account',
                    intraday_protective_exit='not_modelled',fingerprint=rules.FINGERPRINT))
