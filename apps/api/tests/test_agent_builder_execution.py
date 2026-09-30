"""执行能力回归：合成行情只用于测试，不写入账户。"""
from app.services.quant import agent_builder as b
from app.services.quant.agent_builder_engine import indicator_rule, exit_candidates, simulate

def rule(t, **p): return dict(type=t, params=p, source='测试规则', confirmed=True)
def bars(prices): return [[p,p,p,100,p] for p in prices]
def check(ok, label):
    assert ok, label
    print('PASS',label)

check(indicator_rule(rule('ema_compare',fast=2,slow=3),bars([1,2,3,4])) is True,'EMA比较')
check(indicator_rule(rule('price_ema',days=3),bars([1,2,3,4])) is True,'价格高于EMA')
check(indicator_rule(rule('ema_compare',fast=2,slow=3),bars([1,2])) is None,'预热不足')
check(indicator_rule(rule('breakout_distance',days=2,max_pct=5),bars([100,100,105])) is True,'追高边界含等号')
check(indicator_rule(rule('breakout_distance',days=2,max_pct=5),bars([100,100,106])) is False,'超过追高上限')
check(indicator_rule(rule('market_trend',fast=2,slow=3),bars([4,3,2,1])) is False,'逆风市场拦开仓')
p=dict(entry=100,high=105,age=4,fired=set())
check(bool(exit_candidates([rule('breakeven',trigger_pct=5)],bars([100]),p)),'盈利后保本')
check(not exit_candidates([rule('breakeven',trigger_pct=5)],bars([100]),dict(p,high=104)),'尚未盈利不保本')
rules=[dict(rule('reduce_loss',pct=5,size_pct=50),priority=0),dict(rule('stop',pct=8),priority=100)]
x=exit_candidates(rules,bars([90]),p)
check(x[0]['type']=='stop' and len(x)==2,'跳跌时硬止损优先且保留其他命中')
check(not exit_candidates([rules[0]],bars([94]),dict(p,fired={0})),'阶段不重复减仓')
x=exit_candidates([dict(rule('reduce_profit',pct=5,size_pct=50),priority=20),dict(rule('take_profit',pct=5),priority=10)],bars([110]),p)
check(x[0]['type']=='take_profit','优先级控制退出')
pending=b.clean_rule(dict(type='pending',params={},source='放量40%',proposal=dict(type='volume',params={'ratio':1.4})))
check(pending['category']=='buy' and any('均量' in q for q in pending['questions']),'缺周期明确提问')
check(not pending['confirmed'],'待澄清不能确认')
check(b.preview(rule('breakeven',trigger_pct=5))['steps'][-1]['remaining']==0,'保本演示使用同一执行函数')
check([x['remaining'] for x in b.preview(rule('reduce_loss',pct=5,size_pct=50))['steps']]==[100,50,50],'减仓演示仅执行一次')
base=[rule('ma_above',days=2),rule('stop',pct=8),rule('position',pct=20),rule('risk',pct=2),rule('holdings',count=1)]
rs=base+[rule('reduce_profit',pct=5,size_pct=50),rule('reduce_profit',pct=10,size_pct=100)]
days=[str(i) for i in range(7)]; prices=[100,100,105,106,110,111,112]
def data(d): return {'X':bars([99]+prices[:int(d)+1])}
a=simulate(dict(initial=10000,slippage_bps=0,rules=rs),days,data,lambda d:({'X'},0))
sells=[t for t in a['trades'] if t['side']=='sell']
check(len(sells)==2 and sells[0]['remaining']==10 and sells[1]['remaining']==0,'分阶段按剩余持仓成交')
check(a['closed']==1,'分批卖出只统计一个完整交易')
check(sells[0]['date']=='3' and sells[0]['signal_date']=='2','分批信号次日成交')
check(abs(a['final']-10000-sum(t['pnl'] for t in sells))<0.011,'分批费用及净值守恒')
market=base+[rule('market_trend',fast=2,slow=3)]
a=simulate(dict(initial=10000,slippage_bps=0,rules=market),days,data,lambda d:({'X'},0))
check(a['signals']==0 and a['unknown']==len(days),'缺基准不能默认顺风')
print('ALL OK execution')

for source,kind,params in [('盘中止损8%','stop',{'pct':8}),('再跌5%卖出一半','reduce_loss',{'pct':5,'size_pct':50})]:
    r=b.recognition_result({'rules':[dict(type=kind,params=params,source=source)]},{},source)['rules'][0]
    check(r['type']=='pending','含糊口径不能直接执行')
