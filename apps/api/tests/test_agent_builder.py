"""个人策略的关键行为回归，测试数据不落生产库。"""
import copy
import math
from unittest.mock import patch
from app.services.quant import agent_builder as b
from app.services.quant.agent_builder_engine import indicator_rule, simulate

checks = 0
def check(value, name):
    global checks
    assert value, name
    checks += 1
    print('PASS', name)

def reject(fn, name):
    try: fn()
    except ValueError: check(True, name)
    else: raise AssertionError(name)

def rule(k, **p): return dict(type=k, params=p, source='用户手动设置', confirmed=True)
rules = [rule('ma_above', days=2), rule('stop', pct=5), rule('position', pct=20), rule('risk', pct=1), rule('holdings', count=2)]
check(len(b.validate_rules(rules, True))==5, '完整规则可确认')
reject(lambda: b.validate_rules(rules[:-1], True), '遗漏持仓限制不可执行')
reject(lambda: b.validate_rules(rules+[rule('stop', pct=3)], True), '重复风控不悄悄覆盖')
reject(lambda: b.validate_rules([dict(r, confirmed=False) for r in rules], True), '未经确认不能执行')
for value in [True, float('nan'), float('inf'), -1, 9]:
    reject(lambda: b.clean_rule(rule('stop', pct=value)), '非法止损 '+str(value))
reject(lambda: b.clean_rule(rule('ma_above', days=2.5)), '交易日必须为整数')
check(indicator_rule(rule('breakout', days=2), [[10,12,9,100,10],[11,13,10,100,11],[14,99,13,100,14]]) is True, '突破不使用信号当日最高价作为枢轴')
check(indicator_rule(rule('volume', days=2, ratio=2), [[10,10,10,100,10],[10,10,10,100,10],[10,10,10,200,10]]) is True, '均量不包含放量当日')
check(indicator_rule(rule('volume', days=2, ratio=2), [[10,10,10,math.nan,10],[10,10,10,100,10],[10,10,10,200,10]]) is None, '缺失成交量不给假信号')
check(indicator_rule(rule('ma_above', days=3), [[10,10,10,100,10]]) is None, '预热不足返回未知')

def ai(text, rows, single=False):
    with patch('app.services.online_analysis.llm_client.llm_json_call', return_value=({'rules':rows}, {})), patch('app.services.quant.screen_nl.model_name', return_value='test'):
        return b.recognize(text,single)['rules']
check(ai('止损5%', [dict(type='stop',params={'pct':5},source='止损5%')])[0]['type']=='stop','中文紧邻数字仍能核对')
check(not ai('止损5%', [dict(type='stop',params={'pct':5},source='止损5%',confirmed=True)])[0]['confirmed'],'模型不能替用户确认')
check(ai('止损5%', [dict(type='stop',params={'pct':7},source='止损5%')])[0]['type']=='pending','模型添加数字转为待确认')
reject(lambda:ai('止损5%', [dict(type='stop',params={'pct':5},source='止损8%')]), '伪造原文拒绝')
reject(lambda:ai('止损5%', [dict(type='stop',params={'pct':5},source='止损5%')]*2,True), '单条识别不能返回多条覆盖其他规则')

snap=dict(initial=10000,slippage_bps=0,rules=rules)
days=['2026-01-02','2026-01-05','2026-01-06','2026-01-07']
prices=[100,102,90,80]
def bars(day):
    i=days.index(day)
    return {'X':[[99,100,98,100,99]]+[[v,v,v,100,v] for v in prices[:i+1]]}
result=simulate(snap,days,bars,lambda d:({'X'},0))
buys=[t for t in result['trades'] if t['side']=='buy']; sells=[t for t in result['trades'] if t['side']=='sell']
check(buys[0]['signal_date']==days[0] and buys[0]['date']==days[1] and buys[0]['price']==102,'前日信号次日收盘成交')
check(sells[0]['signal_date']==days[2] and sells[0]['date']==days[3] and sells[0]['price']==80,'止损延迟与跳空如实计亏，不按理想止损价')
check(buys[0]['shares']*102<=2000,'单票资金上限生效')
check(buys[0]['shares']*102*.05<=100,'单笔风险上限生效')
check(result['closed']==1 and result['win_rate']==0,'完成周期统计真实亏损')
check(sells[0]['pnl']<(80-102)*buys[0]['shares'],'双边费用计入净损益')
check(result['max_drawdown_pct']<0,'净值回撤包含亏损和费用')
changed=copy.deepcopy(snap);changed['rules'][1]['params']['pct']=8
check(snap['rules'][1]['params']['pct']==5,'修改副本不改变确认快照')
check(b.key('user-a','00000000-0000-0000-0000-000000000001')!=b.key('user-b','00000000-0000-0000-0000-000000000001'),'账号隔离键')
reject(lambda:b.key('u','../x'),'非法策略编号拒绝')
check(result['pending_orders']==0,'卖出订单成交后清除')
print('ALL OK',checks)
