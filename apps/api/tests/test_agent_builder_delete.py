"""删除只影响目标账号；公共研究账本、历史快照和运行中任务受保护。"""
from copy import deepcopy
from unittest.mock import patch
from app.services.quant import agent_builder as b, agent_run as ar, agent_research as research

ident='00000000-0000-0000-0000-000000000001'
owner='owner'; other='other'
records={b.key(owner,ident):dict(id=ident,name='测试策略',version=1,runs=[dict(id='test-run',status='done',result={'return_pct':1})])}
class Cur:
    def execute(self,*args):pass
    def fetchone(self):return [False]
cur=Cur()
def transaction(fn):return fn(cur,ar)
def get(c,k,default=None):return deepcopy(records.get(k,default))
def put(c,k,v):records[k]=deepcopy(v)
def rejects(fn):
    try:fn()
    except ValueError:return
    raise AssertionError('应拒绝')
with patch.object(b,'transaction',transaction),patch.object(ar,'_meta_get',get),patch.object(ar,'_meta_set',put),patch.object(research,'all_lines',return_value=[{'key':'vcp'},{'key':'idea-test'}]):
    rejects(lambda:b.delete(other,ident))
    assert not records[b.key(owner,ident)].get('deleted')
    records[b.key(owner,ident)]['runs'][0]['status']='running'
    rejects(lambda:b.delete(owner,ident))
    records[b.key(owner,ident)]['runs'][0]['status']='done'
    assert b.delete(owner,ident)=={'deleted':True}
    assert records[b.key(owner,ident)]['runs'][0]['result']=={'return_pct':1}
    rejects(lambda:b.read(owner,ident))
    board={'lines':[{'key':'vcp'},{'key':'idea-test'}],'common':{'days':10}}
    b.delete_research(owner,'idea-test')
    assert len(b.filter_research(owner,board)['lines'])==1
    assert len(b.filter_research(other,board)['lines'])==2
    assert len(b.filter_research(None,board)['lines'])==2
    b.delete_research(owner,'idea-test')
    assert records['research-hidden:owner']==['idea-test']
    rejects(lambda:b.delete_research(owner,'not-exist'))
    assert len(board['lines'])==2
print('DELETE ALL OK：跨账号拒绝、运行中拒绝、软删除保留快照、看板隔离、重复删除幂等')
