"""交易语义测试，人工行情仅用于断言，不能当作研究收益。"""
import unittest
from datetime import date,timedelta
from app.services import stock_rules as r
from app.services import stock_backtest as bt


def bars(prices):
    return [dict(ts=str(date(2024,1,1)+timedelta(days=i)),open=p,high=p+1,low=p-1,
                 close=p,volume=1000) for i,p in enumerate(prices)]


class RulesTest(unittest.TestCase):
    def test_missing_and_blocked(self):
        self.assertEqual(r.evaluate('trend_follow',bars([100]*100))['status'],'data_pending')
        self.assertEqual(r.evaluate('event_driven',bars([100]*140))['action'],'wait')
        bad=bars([100]*140)
        bad[-1]['close']=float('nan')
        with self.assertRaises(ValueError):
            r.evaluate('trend_follow',bad)
        bad=bars([100]*140)
        bad[-1]['ts']=bad[-2]['ts']
        with self.assertRaises(ValueError):
            r.indicators(bad)

    def test_atr_and_rsi_seed(self):
        x=r.indicators(bars([100]*140))
        self.assertEqual(x['atr'][-1],2)
        self.assertEqual(x['rsi'][-1],50)
        self.assertEqual(x['ema10'][-1],100)

    def test_channel_uses_prior_bars(self):
        prices=[100+i*.03 for i in range(140)]
        data=bars(prices)
        data[-1].update(open=104,high=109,low=103,close=108,volume=3000)
        self.assertEqual(r.evaluate('leader_break',data)['action'],'buy')
        data[-1]['volume']=0
        self.assertEqual(r.evaluate('leader_break',data)['action'],'wait')

    def test_prefix_is_causal(self):
        data=bars([100+i*.1 for i in range(180)])
        prefix=data[:150]
        before=r.evaluate('quant_rotate',prefix)
        data[-1]['close']=10000
        data[-1]['high']=10001
        after=r.evaluate('quant_rotate',prefix)
        self.assertEqual(before,after)
        cached=r.indicators(data)
        self.assertEqual(before,r.evaluate('quant_rotate',prefix,ind=cached))

    def test_holding_counts_bars(self):
        data=bars([100]*140)
        data[-2]['ts']='2024-08-30'
        data[-1]['ts']='2024-09-02'
        pos=dict(avg_cost=110,opened_at='2024-08-30')
        s=r.evaluate('contrarian',data,pos)
        self.assertEqual(s['holding_trading_days'],1)
        self.assertEqual(s['action'],'sell')

    def test_trailing_stop_never_retreats(self):
        data=bars([100]*140+[110,115,114,113,112])
        pos=dict(avg_cost=100,opened_at=data[139]['ts'])
        stops=[r.evaluate('trend_follow',data[:i],pos)['stop_price'] for i in range(141,146)]
        self.assertEqual(stops,sorted(stops))

    def test_risk_quantity_and_minimum(self):
        spec=r.SPECS['trend_follow']
        kwargs=dict(equity=100000,available=100000,exposure=0,symbol_value=0,price=100,
                    distance=10,lot=1,param=dict(max_position_pct=.25,min_order_amount=0))
        self.assertEqual(r.size_order(spec,**kwargs),75)
        kwargs['param']['min_order_amount']=20000
        self.assertEqual(r.size_order(spec,**kwargs),75)
        self.assertEqual(r.size_order(spec,**kwargs,research_minimum=20000),0)
        kwargs['lot']=None
        self.assertEqual(r.size_order(spec,**kwargs),0)

    def test_market_minimum_and_hard_cap(self):
        k=dict(equity=15000,available=15000,exposure=0,symbol_value=0,price=100,
               distance=1,lot=1,param=dict(max_position_pct=.8,min_order_amount=20000))
        spec={**r.SPECS['trend_follow'],'weight':.8}
        self.assertEqual(r.size_order(spec,**k,market='US'),37)
        self.assertEqual(r.size_order(spec,**k,market='HK'),37)
        self.assertEqual(r.size_order(spec,**k,market='CN_A'),0)
        k.update(equity=100000,available=100000,lot=100,distance=5)
        self.assertEqual(r.size_order(r.SPECS['mean_revert'],**k,market='CN_A'),100)
        k['distance']=100
        self.assertEqual(r.size_order(spec,**k,market='CN_A'),0)
        with self.assertRaises(ValueError):r.size_order(spec,**k,market='MULTI')

    def test_fee_and_next_open(self):
        model=dict(version='test',commission_pct=.00025,commission_min=5,
                   stamp_tax_pct=.0005,stamp_side='sell',transfer_fee_pct=.00001,
                   transfer_fee_side='both')
        self.assertEqual(bt.fee('buy',10000,model),5.1)
        self.assertEqual(bt.fee('sell',10000,model),10.1)
        data=bars([100+i*.1 for i in range(165)])
        result=bt.run('quant_rotate',{'TEST':data},capital=100000,lot_sizes={'TEST':1},
                      fee_model=model,param=dict(max_positions=4,max_position_pct=.25,
                                                 min_order_amount=0,stop_loss_pct=-.1))
        self.assertTrue(result['trades'])
        index={b['ts']:b for b in data}
        for t in result['trades']:
            self.assertGreater(t['fill_date'],t['signal_date'])
            multiplier=1.001 if t['side']=='buy' else .999
            self.assertAlmostEqual(t['price'],index[t['fill_date']]['open']*multiplier)


if __name__=='__main__':
    unittest.main()
