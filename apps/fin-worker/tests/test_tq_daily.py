from datetime import datetime,timezone
from unittest import TestCase
from app.strategy import tq_daily
from app.strategy.stock_rules import strategy_key,FINGERPRINT,SPECS


class Response:
    status_code=200
    def __init__(self,data): self.data=data
    def json(self): return self.data


class Api:
    def __init__(self,data): self.data=data
    def _request(self,*args,**kw): return Response(self.data)
    def quote(self,code): return {'last_price':100}
    def get_occupied_positions(self,**kw): return []


class Paper:
    def get_instrument(self,code): return {'lot_size':1}


class AdapterTest(TestCase):
    def invoke(self,family='trend_follow',rows=None,positions=None,pending=None,memory=None):
        key=strategy_key(family)
        data=dict(key=key,family=family,fingerprint=FINGERPRINT,spec=SPECS[family],
                  signal_date='2026-10-08',rotation_day=True,pending_codes=pending or [],
                  records=rows or [],blocked=SPECS[family].get('blocked'))
        req=dict(project_id='p',trade_date='2026-10-09',point='open',memory=memory)
        view=dict(project=dict(version=2),param=dict(max_positions=4,max_position_pct=.25,
                  min_order_amount=0,stop_loss_pct=-.1,strategies=[dict(key=key,active=True,
                  params=dict(rule_fingerprint=FINGERPRINT))]),positions=positions or [],
                  cash=dict(available=100000,frozen=0))
        return tq_daily.build(req=req,view=view,api=Api(data),paper=Paper(),strategy_key=key,
                    strategy_version='registered',now=datetime(2026,10,9,tzinfo=timezone.utc),
                    market='CN_A',mos=True,ttl=1800)

    def test_wait_never_becomes_sample(self):
        self.assertTrue(self.invoke()['halted'])
        self.assertTrue(self.invoke(family='event_driven')['halted'])

    def test_size_and_audit(self):
        row=dict(code='X',action='buy',candidate=True,reason='确认',momentum=.1,stop_distance=10)
        result=self.invoke(rows=[row])
        self.assertEqual(result['intent']['qty'],75)
        self.assertEqual(result['data_snapshot']['rule_fingerprint'],FINGERPRINT)

    def test_pending_and_cooldown(self):
        row=dict(code='X',action='buy',candidate=True,reason='确认',momentum=.1,
                 stop_distance=10,cooldown_remaining=2)
        self.assertTrue(self.invoke(rows=[row])['halted'])
        row.pop('cooldown_remaining')
        self.assertTrue(self.invoke(rows=[row],pending=['X'])['halted'])

    def test_t1_and_protective_sell(self):
        pos=dict(code='X',qty=100,sellable_qty=0,avg_cost=120)
        row=dict(code='X',action='hold',candidate=False,reason='持仓',stop_price=110)
        self.assertTrue(self.invoke(rows=[row],positions=[pos])['halted'])
        pos['sellable_qty']=100
        result=self.invoke(rows=[row],positions=[pos],memory={'items':[{'code':'X'}]})
        self.assertEqual(result['intent']['side'],'sell')
        self.assertEqual(result['intent']['qty'],100)

    def test_rule_parity(self):
        from pathlib import Path
        worker=Path(__file__).resolve().parents[1]/'app/strategy/stock_rules.py'
        api=Path(__file__).resolve().parents[3]/'api/app/services/stock_rules.py'
        self.assertEqual(worker.read_bytes(),api.read_bytes())
