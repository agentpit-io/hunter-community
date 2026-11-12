"""多语言仅做输入翻译；简介选填，编辑原文及版本不能丢失。"""
from copy import deepcopy
from unittest.mock import patch
from app.services.quant import agent_builder as b, agent_run as ar

def rejects(fn):
    try: fn()
    except ValueError: return
    raise AssertionError('应拒绝')

assert b.editor_metadata({})['description'] == ''
assert len(b.editor_metadata({'description':'字'*100})['description']) == 100
rejects(lambda:b.editor_metadata({'description':'字'*101}))
rejects(lambda:b.editor_metadata({'description':None}))
rejects(lambda:b.editor_metadata({'source_language':'shell'}))
rejects(lambda:b.editor_metadata({'assistant_prompt':'字'*2001}))
with patch('app.services.online_analysis.llm_client.llm_json_call',return_value=({'rules':[{'type':'stop','params':{'pct':5},'source':'stop_pct = 5'}]},{})) as llm:
    for lang in b.LANGUAGES:
        result=b.recognize('stop_pct = 5',language=lang)
        assert result['rules'][0]['params']=={'pct':5}
        assert not result['rules'][0]['confirmed']
        assert b.LANGUAGES[lang] in llm.call_args.args[0]
    llm.reset_mock()
    rejects(lambda:b.recognize('stop_pct = 5',language='shell'))
    llm.assert_not_called()

records={}
class Cur:
    def execute(self,*args): pass
def get(c,k,default=None):return deepcopy(records.get(k,default))
def put(c,k,v):records[k]=deepcopy(v)
with patch.object(b,'transaction',lambda fn:fn(Cur(),ar)),patch.object(ar,'_meta_get',get),patch.object(ar,'_meta_set',put),patch.object(b,'resolve_pool',return_value={'name':'测试池','market':'us'}):
    body=dict(name='多语言测试',rules=[{'type':'stop','params':{'pct':5},'source':'止损5%'}],source_text='stop_pct = 5',source_language='python',description='',assistant_prompt='保留止损5%')
    first=b.save('owner',body)
    assert first['description']=='' and first['source_text']=='stop_pct = 5'
    second=b.save('owner',dict(body,id=first['id'],version=1,description='新版描述',source_language='javascript'))
    assert second['source_language']=='javascript'
    assert second['history'][0]['source_language']=='python'
    assert second['history'][0]['assistant_prompt']=='保留止损5%'
    legacy=b.save('owner',dict(name='旧请求',rules=body['rules'],source_text='旧版长原文'*100))
    assert legacy['description']=='' and legacy['source_language']=='text'
    assert len(legacy['source_text'])>100
print('EDITOR ALL OK：语言白名单与提示词、选填100字、保存往返、旧数据兼容、版本原文保留')
