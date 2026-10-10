"""日期隔离、缺失数据和分红去重的行为检查。直接运行，不需数据库。"""
from datetime import date,timedelta
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.stock_reference_signals import Engine
from app.services import stock_rules as r

def main():
    bars=[]
    for i in range(270):
        value=100+(i%4)*.5 if i<200 else 70+(i%4)*.5+.03*i
        bars.append(dict(ts=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=value,high=value+1,low=value-1,close=value,volume=1000))
    today=bars[-1]['ts']; x=r.indicators(bars)
    financial=dict(kind='financial',market='US',code='TEST',metric='net_income',value=123,
        period_start='2024-01-01',period_end='2024-12-31',available_after='2025-10-01')
    assert Engine([financial],'US').evaluate('value_hold',bars,ind=x,code='TEST')['action']=='wait'
    known={**financial,'available_after':'2025-05-02'}
    assert Engine([known],'US').evaluate('value_hold',bars,ind=x,code='TEST')['action']=='buy'
    assert Engine([{**known,'value':-1}],'US').evaluate('value_hold',bars,ind=x,code='TEST')['action']=='wait'
    cash=dict(kind='dividend',market='US',code='TEST',cash_per_share=2,currency='USD',ex_date='2025-07-01',available_after='2025-06-01')
    engine=Engine([cash,{**cash,'source':'duplicate'}],'US')
    signal=engine.evaluate('div_lowvol',bars,ind=x,code='TEST')
    assert signal['reference_evidence']['trailing_cash_per_share']==2
    future=Engine([{**cash,'available_after':'2025-10-01'}],'US').evaluate('div_lowvol',bars,ind=x,code='TEST')
    assert future['reference_evidence']['trailing_cash_per_share']==0
    hk={**known,'market':'HK','metric':'eps_basic','pit_status':'missing_disclosure_date'}
    assert not Engine([hk],'HK').financial['TEST']
    event=dict(kind='event',market='US',code='TEST',earnings_event=True,available_after='2025-10-01')
    assert Engine([event],'US').evaluate('event_driven',bars,ind=x,code='TEST')['action']=='wait'
    bars[-1]['volume']=1500
    e=Engine([{**event,'available_after':today}],'US').evaluate('event_driven',bars,ind=x,code='TEST')
    assert e['action']=='buy'
    print('Reference strategy date, dividend deduplication and event checks passed')

if __name__=='__main__': main()
