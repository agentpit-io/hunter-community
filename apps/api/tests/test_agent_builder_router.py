"""鉴权、额度失败与请求边界；全部后端依赖隔离，不写数据库。"""
from unittest.mock import patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app.routers.agent_builder import router
from app.services.quant import agent_builder as b

app=FastAPI()
@app.middleware('http')
async def identity(req: Request, next_):
    if req.headers.get('x-test-user'):
        req.state.user_id=req.headers['x-test-user']
        req.state.user_role='admin'
    return await next_(req)
app.include_router(router)
c=TestClient(app)
h={'x-test-user':'test-user'}
for path in ['/agent/builder','/agent/builder/00000000-0000-0000-0000-000000000001']:
    assert c.get(path).status_code==401
assert c.post('/agent/builder/recognize',json={'text':'止损5%'}).status_code==401
assert c.post('/agent/builder',headers=h,json=[]).status_code==400
with patch.object(b,'list_for',return_value=[]),patch.object(b,'data_range',return_value={'first':None,'last':None}):
    r=c.get('/agent/builder',headers=h)
    assert r.status_code==200 and r.json()['schema']
with patch('app.services.quant.screen_quota.reserve') as reserve, patch('app.services.quant.screen_quota.refund') as refund:
    assert c.post('/agent/builder/recognize',headers=h,json={'text':''}).status_code==400
    reserve.assert_not_called()
    with patch.object(b,'recognize',side_effect=ValueError('尚未配置 AI 模型')):
        assert c.post('/agent/builder/recognize',headers=h,json={'text':'止损5%'}).status_code==400
        reserve.assert_called_once();refund.assert_called_once()
print('ROUTER ALL OK：鉴权、对象校验、目录、空输入不扣次、模型未配置退次')
