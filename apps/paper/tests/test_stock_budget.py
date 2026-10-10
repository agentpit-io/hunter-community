from unittest.mock import patch
from decimal import Decimal
from app.risk.stock_budget import exceeds_cap,reject_reason


def test_cap_includes_existing_holdings_and_fees():
    k=dict(equity=100000,symbol_value=10000,order_value=15000,fee=0,account_cap=.4)
    assert not exceeds_cap(**k)
    assert exceeds_cap(**{**k,'fee':10})
    assert exceeds_cap(**{**k,'account_cap':.2})
    assert exceeds_cap(**{**k,'equity':float('nan')})
    assert exceeds_cap(**{**k,'account_cap':float('nan')})


def test_market_floor_is_versioned_without_changing_legacy():
    from app.ledger import order_limits
    row={'min_order_amount':Decimal('20000'),'strategies':[dict(active=True,key='tq_daily_v2_trend_follow')]}
    with patch('app.ledger._fetchone',return_value=row):
        assert order_limits(None,'p','CN_A')==5000
        assert order_limits(None,'p','US') is None
        assert order_limits(None,'p','HK') is None
        row['strategies'][0]['key']='tq_daily_v1_trend_follow'
        assert order_limits(None,'p','CN_A')==20000


class Cursor:
    def execute(self,*args):pass
    def fetchone(self):return {'max_position_pct':Decimal('.4')}


def test_real_budget_uses_market_cash_and_fails_missing_valuation():
    req=dict(side='buy',project_id='p',code='X',qty=30,intent_ref={'strategy_key':'tq_daily_v2_trend_follow'})
    with patch('app.ledger.cash_balance',return_value=(Decimal(10000),Decimal(0))), \
         patch('app.ledger.list_positions',return_value=[]):
        assert reject_reason(Cursor(),req,'US',Decimal(100),Decimal(1),None)
        assert reject_reason(Cursor(),{**req,'qty':20},'US',Decimal(100),Decimal(1),None) is None
    with patch('app.ledger.cash_balance',return_value=(Decimal(10000),Decimal(0))), \
         patch('app.ledger.list_positions',return_value=[dict(code='Y',qty=10)]), \
         patch('app.snapshot.capture',return_value=None):
        assert '估值不足' in reject_reason(Cursor(),req,'US',Decimal(100),Decimal(1),None)
    assert reject_reason(None,{**req,'side':'sell'},'US',Decimal(100),Decimal(1),None) is None
