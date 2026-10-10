"""时点数据驱动的独立研究规则；不改变生产登记策略或资金账本。"""
from bisect import bisect_right
from collections import defaultdict
from datetime import date,timedelta
import hashlib,json
from pathlib import Path
from app.services import stock_rules as rules

VERSION='tq_reference_research_v3'
SPECS={**rules.SPECS,
    'value_hold':dict(name='已披露盈利与低价格分位',stop=2.5,days=60,positions=4,weight=.20,exposure=.60,risk=.005,cooldown=5),
    'div_lowvol':dict(name='历史分红与低波动',stop=2.5,days=60,positions=4,weight=.15,exposure=.60,risk=.005,cooldown=5),
    'event_driven':dict(name='财报披露后量价确认',stop=2.0,days=10,positions=3,weight=.15,exposure=.45,risk=.005,cooldown=5)}

class Engine:
    SPECS=SPECS
    def __init__(self,records,market,fx=None):
        self.market=market; self.fx=fx or {}
        self.financial=defaultdict(list); self.dividends=defaultdict(list); self.events=defaultdict(list)
        self.fingerprint=hashlib.sha256((Path(__file__).read_text(encoding='utf-8')+json.dumps(records,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
        for r in records:
            if r['market']!=market: continue
            code=r['code']; kind=r['kind']
            if kind=='financial' and r.get('available_after') and r['metric'] in ('eps_basic','net_income'):
                if market=='HK' and r.get('pit_status')!='original_annual_eps_reviewed': continue
                annual=False
                if r.get('period_start'):
                    annual=330<=(date.fromisoformat(r['period_end'])-date.fromisoformat(r['period_start'])).days<=400
                elif market=='CN_A': annual=r['period_end'].endswith('12-31')
                elif market=='HK': annual=True
                if annual: self.financial[code].append(r)
            elif kind=='dividend' and r.get('cash_per_share',0) and r.get('ex_date'):
                if market=='HK' and r.get('currency') not in ('HKD','CNY','USD'): continue
                # 只使用已经发生的除息/支付记录，不把未知宣告日推算为提前可知。
                known=r.get('available_after') or r.get('historical_payment_available_after')
                if not known: continue
                known=max(known,(date.fromisoformat(r['ex_date'])+timedelta(days=1)).isoformat())
                self.dividends[code].append({**r,'known_after':known})
            elif kind=='event' and r.get('available_after') and r.get('earnings_event'):
                self.events[code].append(r)
        self.event_days={c:sorted(set(r['available_after'] for r in rows)) for c,rows in self.events.items()}

    def evaluate(self,family,bars,position=None,*,ind=None,code=None):
        if family not in ('value_hold','div_lowvol','event_driven'):
            return rules.evaluate(family,bars,position,ind=ind)
        if position:
            result=rules.evaluate(family,bars,position,ind=ind)
            if result.get('status')!='data_pending':
                x=ind if ind is not None else rules.indicators(bars)
                entry=next(i for i,b in enumerate(bars) if str(b['ts'])[:10]>=str(position['opened_at'])[:10])
                stop=float(position['avg_cost'])-SPECS[family]['stop']*x['atr'][entry]
                result['stop_price']=stop
                if result['close']<=stop: result.update(action='sell',reason='研究规则ATR保护止损')
            if result.get('holding_trading_days',0)>=SPECS[family]['days']:
                result.update(action='sell',reason='研究规则交易日持仓期限')
            return result
        result=rules.evaluate('quant_rotate',bars,ind=ind)
        if result.get('status')=='data_pending': return result
        today=str(bars[-1]['ts'])[:10]; current=date.fromisoformat(today)
        close=result['close']; x=ind if ind is not None else rules.indicators(bars)
        t=len(bars)-1; buy=False; reason='当前条件未满足'; evidence={}
        if family=='value_hold':
            known=[r for r in self.financial[code] if r['available_after']<=today and (current-date.fromisoformat(r['period_end'])).days<=550]
            if not known:
                reason='当时尚无可用年度盈利证据'
            else:
                latest=max(known,key=lambda r:(r['period_end'],r['available_after'],r.get('accession',''),r['metric']=='net_income'))
                window=x['close'][max(0,t-251):t+1]
                percentile=sum(v<=close for v in window)/len(window)
                buy=latest['value']>0 and percentile<=.40 and close>=x['ma20'][t] and 35<=result['rsi']<=65
                evidence=dict(financial_period=latest['period_end'],financial_available=latest['available_after'],financial_source=latest.get('source'),price_percentile=percentile)
                reason='已披露年度盈利为正，价格低分位且修复确认'
        elif family=='div_lowvol':
            cutoff=(current-timedelta(days=365)).isoformat()
            # 同一除息日跨来源最多取一份，优先较早可知且日期字段完整的记录。
            selected={}
            for r in self.dividends[code]:
                if r['known_after']<=today and cutoff<r['ex_date']<=today:
                    key=r['ex_date']
                    if key not in selected or r['known_after']<selected[key]['known_after']: selected[key]=r
            amount=0
            for r in selected.values():
                rate=1.0
                if self.market=='HK' and r['currency']!='HKD':
                    history=self.fx.get(r['currency'],[])
                    known=[v for v in history if v['available_after']<=today]
                    if not known: continue
                    rate=known[-1]['close']
                amount+=r['cash_per_share']*rate
            yield_proxy=amount/close
            buy=yield_proxy>=.01 and result['volatility']<=.025 and close>=x['ma60'][t] and 35<=result['rsi']<=70
            evidence=dict(trailing_cash_per_share=amount,dividend_yield_price_proxy=yield_proxy,observed_dividend_count=len(selected),
                observed_dividends=[dict(ex_date=r['ex_date'],known_after=r['known_after'],source=r.get('source')) for r in selected.values()])
            reason='已发生历史分红达门槛，低波且趋势确认'
        else:
            stamps=self.event_days.get(code,[]); i=bisect_right(stamps,today)-1
            stamp=stamps[i] if i>=0 else None
            since=sum(stamp<=str(b['ts'])[:10]<=today for b in bars) if stamp else 99
            mean_volume=sum(float(b['volume']) for b in bars[-21:-1])/20
            buy=stamp is not None and since<=5 and close>x['ma20'][t] and close>x['close'][t-1] and mean_volume>0 and float(bars[-1]['volume'])>=1.2*mean_volume
            evidence=dict(earnings_available=stamp,event_trading_days=since)
            reason='真实财报披露后五交易日内放量上涨确认'
        result.update(action='buy' if buy else 'wait',reason=reason if buy else '未触发：'+reason,
            reference_evidence=evidence,stop_distance=SPECS[family]['stop']*result['atr'])
        return result
