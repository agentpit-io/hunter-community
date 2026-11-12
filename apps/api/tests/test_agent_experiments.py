"""实验助手边界：真实计算用小型输入；数据库状态测试不连接生产库。"""
import copy
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from app.services.quant import agent_experiments as e
from app.services.quant import agent_limitup as engine, agent_manual as manual, agent_run as ar


def baseline():
    return dict(engine.PARAMS, require_ma5=True, main_only=False)


def plan(ident="amp-tight", reason="较紧的整理范围可能筛出更稳定的结构，需与固定基准比较"):
    return dict(candidates=[dict(id=ident, reason=reason)])


def test_candidates_only_change_one_buy_field():
    base = baseline()
    for item in e.menu(base):
        candidate = e.validate_plan(plan(item["id"]), base)[0]
        changed = [k for k in base if candidate["params"][k] != base[k]]
        assert changed == [item["field"]]
        for key in ("amount", "hold_days", "lu_main", "lu_growth", "growth_only", "mode"):
            assert candidate["params"][key] == base[key]
        assert candidate["rule_hash"] == e.fingerprint(candidate["params"])
    assert base == baseline()


@pytest.mark.parametrize("data", [None, [], {}, dict(candidates=[]),
    dict(candidates=[dict(id="hold_days",reason="改变卖出")]),
    dict(candidates=[dict(id="amp-tight",reason="收益增加20%")]),
    dict(candidates=[dict(id="amp-tight",reason="Expected annual return will improve significantly")]),
    dict(candidates=[dict(id="amp-tight",reason="<script>" )]),
    dict(candidates=[dict(id="amp-tight",reason="研究",params=dict(amount=1))]),
    dict(candidates=[dict(id="amp-tight",reason="研究")]*2),
    dict(candidates=[dict(id="ma-on",reason="已经启用，没有变化")]),
    dict(candidates=[dict(id=i,reason="研究") for i in ("amp-tight","amp-wide","ma-off","volume-off")]),
])
def test_rejects_code_unknown_duplicates_numbers_and_noops(data):
    with pytest.raises(ValueError):
        e.validate_plan(data, baseline())


def test_dates_and_owner_keys():
    assert e.dates(dict(start="2025-01-01", end="2025-12-31")) == (date(2025,1,1),date(2025,12,31))
    for body in (dict(start="2025-01-01",end="2027-01-01"),dict(start="bad",end="2025-01-01"),
                 dict(start="2025-01-02",end="2025-01-01"),dict(start=1,end="2025-01-01")):
        with pytest.raises(ValueError): e.dates(body)
    ident="00000000-0000-0000-0000-000000000001"
    assert e.key_for("alice",ident) != e.key_for("bob",ident)
    with pytest.raises(ValueError): e.key_for("alice","../../config")


def test_dates_use_shared_market_timezone():
    from app.services import market_time
    with patch.object(market_time,'market_tz',wraps=market_time.market_tz) as lookup:
        e.dates(dict(start='2025-01-01',end='2025-01-02'))
    lookup.assert_called_once_with('a')


def test_snapshot_roundtrip_hash_and_real_engine_reproducibility(tmp_path):
    days=[date(2025,1,1)+timedelta(days=i) for i in range(9)]
    prices=[20,10,11,11.1,11.2,11.3,11.4,11.5,11.6]
    arr=np.array([[c,c,c,100,c] for c in prices],dtype=float)
    ctx=SimpleNamespace(store=dict(bench=dict.fromkeys(days,1.0),last=days[-1],codes={"600000":(days,arr)}),snap={})
    path=tmp_path/'input.jsonl.gz'
    frozen,digest=e.freeze(ctx,days[5],days[-1],path)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    with gzip.open(path,'rt',encoding='utf-8') as f:
        records=[json.loads(line) for line in f]
    assert records[0]["format"] == "hunter-experiment-input-v1"
    assert len(records[1]["dates"]) == 9
    reconstructed=SimpleNamespace(store=dict(bench={date.fromisoformat(d):v for d,v in records[0]["bench"].items()},
        last=date.fromisoformat(records[0]["last"]), codes={"600000":([date.fromisoformat(d) for d in records[1]["dates"]],
        np.array(records[1]["bars"],dtype=float))}),snap={})
    p=dict(engine.PARAMS,growth_only=False,main_only=True,require_ma5=False,amp_max=None,no_all_shrink=False)
    first=manual.calculate(frozen,p,days[5],days[-1],lambda _:None)
    arr[:]=99  # 原始缓存改变，已冻结输入不变。
    second=manual.calculate(reconstructed,p,days[5],days[-1],lambda _:None)
    assert first == second
    assert first["closed"] == 1
    assert first["trades"][-1]["net_pnl"] < first["trades"][-1]["pnl_abs"]
    with pytest.raises(FileExistsError): e.freeze(ctx,days[5],days[-1],path)
    assert not frozen.store["codes"]["600000"][1].flags.writeable


def test_missing_bars_saved_as_null(tmp_path):
    day=date(2025,1,1)
    ctx=SimpleNamespace(store=dict(bench={day:1},last=day,codes={"600000":([day],np.array([[1,np.nan,1,0,np.nan]]))}),snap={})
    target=tmp_path/'nulls.gz'
    e.freeze(ctx,day,day,target)
    with gzip.open(target,'rt',encoding='utf-8') as f: text=f.read()
    assert 'NaN' not in text
    assert json.loads(text.splitlines()[1])["bars"][0][1] is None


def test_comparison_is_evidence_not_probability():
    def row(id_,net,dd,closed=30,days=120):
        return dict(id=id_,result=dict(net_pnl=net,max_drawdown_pct=dd,closed=closed,nav=[{}]*days))
    result=e.comparison([row('baseline',100,-5),row('a',200,-4),row('b',300,-8),row('c',400,-1,closed=2),dict(id='failed')])
    assert [r['verdict'] for r in result] == ['可继续研究','本区间未通过对照','证据不足','证据不足']
    assert result[-1]['delta_net_pnl'] is None
    assert result[0]['delta_net_pnl'] == 100
    assert e.comparison([]) == []


class FakeCursor:
    def __init__(self): self.available=True; self.sql=[]; self.one=None
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def execute(self, sql, values=()):
        self.sql.append((sql,values))
        if 'pg_try_advisory' in sql: self.one=(self.available,)
        elif 'MIN(trade_date)' in sql: self.one=(date(2024,1,1),date(2025,12,31))
    def fetchone(self): return self.one


@pytest.fixture
def repo():
    data={};cur=FakeCursor()
    def transact(fn):
        before=copy.deepcopy(data)
        try: return fn(cur,ar)
        except Exception:
            data.clear();data.update(before);raise
    with patch.object(e,'transaction',side_effect=transact), \
         patch.object(ar,'_meta_get',side_effect=lambda c,k,default=None:copy.deepcopy(data.get(k,default))), \
         patch.object(ar,'_meta_set',side_effect=lambda c,k,v:data.__setitem__(k,copy.deepcopy(v))), \
         patch.object(ar,'_branch_state',return_value=dict(params=baseline())):
        yield data,cur


def make_ready(repo):
    data,_=repo
    run=e.create('alice',dict(goal='研究量能',failure_rule='不改善则放弃',start='2025-01-01',end='2025-06-01'))
    k=e.key_for('alice',run['id'])
    data[k]['status']='ready'
    data[k]['candidates']=e.validate_plan(plan(),run['baseline'])
    return run['id'],k


def test_account_isolation_and_no_public_writes(repo):
    ident,k=make_ready(repo)
    with pytest.raises(ValueError): e.read('bob',ident)
    with pytest.raises(ValueError): e.start('bob',ident)
    with pytest.raises(ValueError): e.cancel('bob',ident)
    run=e.start('alice',ident)
    assert run['status']=='queued' and run['trials_used']==2
    assert all(not key.startswith(('branch:','manual-rule:','line:')) for key in repo[0])
    with pytest.raises(ValueError): e.start('alice',ident)
    assert repo[0]['experiment-index:alice']['trials']==2


def test_budget_reserved_once_and_never_refunded_by_stop(repo):
    ident,k=make_ready(repo)
    e.start('alice',ident)
    stopped=e.cancel('alice',ident)
    assert stopped['cancel_requested'] is True
    assert repo[0]['experiment-index:alice']['trials']==2
    repo[0][k]['status']='failed'
    assert e.listing('alice')['items'][0]['status']=='failed'
    assert e.read('alice',ident)['candidates']


def test_budget_and_code_change_block_execution(repo):
    ident,k=make_ready(repo)
    repo[0]['experiment-index:alice']['trials']=19
    with pytest.raises(ValueError,match='剩余预算'): e.start('alice',ident)
    repo[0]['experiment-index:alice']['trials']=0
    repo[0][k]['implementation']={}
    with pytest.raises(ValueError,match='执行代码'): e.start('alice',ident)


def test_ready_cancel_and_orphan_recovery(repo):
    ident,k=make_ready(repo)
    assert e.cancel('alice',ident)['status']=='cancelled'
    repo[0][k].update(status='running',updated_at=0,results=[dict(id='baseline',status='done')])
    recovered=e.read('alice',ident)
    assert recovered['status']=='failed' and recovered['results'][0]['status']=='done'
    repo[0][k].update(status='running',updated_at=0)
    repo[1].available=False
    assert e.read('alice',ident)['status']=='running'


def test_busy_lock_and_ai_budget(repo):
    repo[1].available=False
    with pytest.raises(ValueError,match='已有实验'): make_ready(repo)
    repo[1].available=True
    repo[0]['experiment-index:alice']=dict(ids=[],trials=0,ai_requests=e.AI_BUDGET)
    with pytest.raises(ValueError,match='预算'): make_ready(repo)


def test_worker_launch_failure_persisted(repo):
    ident,k=make_ready(repo)
    repo[0][k]['status']='planning'
    with patch.object(e.sys,'platform','linux'),patch.object(e.subprocess,'Popen',side_effect=OSError('not available')):
        e.launch('alice',ident,'plan')
    assert repo[0][k]['status']=='failed'


@pytest.fixture
def worker_env(repo, monkeypatch):
    from app.services import database
    import sys
    signal=SimpleNamespace(SIGALRM=14,signal=lambda *_:None,alarm=lambda *_:None)
    monkeypatch.setitem(sys.modules,'signal',signal)
    closed=[]
    conn=SimpleNamespace(cursor=lambda:repo[1],commit=lambda:None,rollback=lambda:None,close=lambda:closed.append(True))
    monkeypatch.setattr(database,'get_conn',lambda:conn)
    monkeypatch.setattr(ar,'_ddl_done',False)
    return closed


def test_worker_planning_records_safe_candidates(repo,worker_env):
    from app.services.online_analysis import llm_client
    from app.services.quant import screen_nl
    ident,k=make_ready(repo)
    repo[0][k]['status']='planning'
    with patch.object(llm_client,'llm_json_call',return_value=(plan(),dict(model='gemini-3.5-flash',tokens_in=10))), \
         patch.object(screen_nl,'model_name',return_value='gemini-3.5-flash'):
        e.worker('alice',ident,'plan')
    run=repo[0][k]
    assert run['status']=='ready'
    assert run['model']['tokens_in']==10
    assert run['candidates'][0]['field']=='amp_max'
    assert worker_env==[True]


def test_worker_serial_baseline_and_candidates_share_snapshot(repo,worker_env,tmp_path):
    ident,k=make_ready(repo)
    e.start('alice',ident)
    d=date(2025,1,1)
    days=[d+timedelta(days=i) for i in range(152)]
    ctx=SimpleNamespace(store=dict(bench=dict.fromkeys(days,1),last=days[-1],codes={"300001":(days,np.array([[10,11,9,100,10]]*len(days)))}),snap={})
    contexts=[]
    def calc(frozen,params,start,end,progress):
        contexts.append(frozen)
        progress(100)
        return dict(net_pnl=10,max_drawdown_pct=-1,closed=30,nav=[{}]*len(frozen.store['bench']))
    with patch.object(ar,'Ctx',return_value=ctx),patch.object(manual,'calculate',side_effect=calc), \
         patch.dict(e.os.environ,{'HUNTER_EXPERIMENT_DIR':str(tmp_path)}):
        e.worker('alice',ident,'run')
    run=repo[0][k]
    assert run['status']=='done' and run['progress']==100
    assert len(contexts)==2 and contexts[0] is contexts[1]
    assert [x['id'] for x in run['results']]==['baseline','amp-tight']
    assert [x['trial_number'] for x in run['results']]==[1,2]
    assert run['data_snapshot']['sha256']
    assert repo[0]['experiment-index:alice']['trials']==2
    assert worker_env==[True]


def test_worker_stop_retains_partial_and_unstarted_results(repo,worker_env,tmp_path):
    ident,k=make_ready(repo)
    e.start('alice',ident)
    def calc(ctx,params,start,end,progress):
        repo[0][k]['cancel_requested']=True
        progress(50)
    with patch.object(ar,'Ctx',return_value=None), \
         patch.object(manual,'calculate',side_effect=calc),patch.dict(e.os.environ,{'HUNTER_EXPERIMENT_DIR':str(tmp_path)}):
        # freeze 的测试替身也提供快照摘要需要的字段。
        with patch.object(e,'freeze',return_value=(SimpleNamespace(store={'codes':{},'bench':{}}),'test-hash')):
            e.worker('alice',ident,'run')
    assert repo[0][k]['status']=='cancelled'
    assert [r['status'] for r in repo[0][k]['results']]==['cancelled','not_started']
    assert repo[0]['experiment-index:alice']['trials']==2
    assert worker_env==[True]


def test_baseline_failure_cannot_report_comparison_success(repo,worker_env,tmp_path):
    ident,k=make_ready(repo)
    e.start('alice',ident)
    good=dict(net_pnl=10,max_drawdown_pct=-1,closed=30,nav=[{}]*120)
    with patch.object(ar,'Ctx',return_value=None), \
         patch.object(e,'freeze',return_value=(SimpleNamespace(store={'codes':{},'bench':{}}),'test-hash')), \
         patch.object(manual,'calculate',side_effect=[RuntimeError('test calculation failure'),good]), \
         patch.dict(e.os.environ,{'HUNTER_EXPERIMENT_DIR':str(tmp_path)}):
        e.worker('alice',ident,'run')
    run=repo[0][k]
    assert run['status']=='failed'
    assert run['results'][0]['status']=='failed'
    assert run['results'][1]['status']=='done'
    assert run['comparison'][0]['verdict']=='证据不足'
    assert run['comparison'][0]['delta_net_pnl'] is None
