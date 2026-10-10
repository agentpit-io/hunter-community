from app.risk.stock_occupancy import reject_reason


class Cursor:
    def __init__(self,holders): self.holders=holders; self.calls=[]
    def execute(self,sql,args): self.calls.append((sql,args))
    def fetchall(self): return [{'project_id':p} for p in self.holders]


def test_occupancy_and_duplicate():
    req=dict(side='buy',code='X',project_id='p',intent_ref={'strategy_key':'tq_daily_v1_trend_follow'})
    c=Cursor(['a','b','c'])
    assert reject_reason(c,req,'US')
    assert 'pg_advisory_xact_lock' in c.calls[0][0]
    assert reject_reason(Cursor(['p']),req,'US')
    assert reject_reason(Cursor(['a','b']),req,'US') is None
    assert reject_reason(Cursor(['a','b','c']),{**req,'side':'sell'},'US') is None
    req['intent_ref']['strategy_key']='tq_daily_v2_trend_follow'
    assert reject_reason(Cursor(['a','b','c']),req,'US')
