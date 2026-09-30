"""延迟模型验证停止、账号隔离、旧任务释放和超时，不调用真实模型。"""
import asyncio
import importlib
from unittest.mock import patch
import httpx
from fastapi import FastAPI, Request
from app.services.quant import agent_builder as b

r=importlib.import_module('app.routers.agent_builder')
app=FastAPI()
@app.middleware('http')
async def identity(req: Request, next_):
    req.state.user_id=req.headers.get('x-user')
    return await next_(req)
app.include_router(r.router)

async def main():
    # 不只验证页面结束等待，还验证异步模型连接退出。
    transport_started=asyncio.Event();transport_closed=asyncio.Event()
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0
            self.chat=self;self.completions=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):transport_closed.set()
        async def create(self,**kwargs):
            transport_started.set();await asyncio.Event().wait()
    with patch('openai.AsyncOpenAI',Client),patch('app.services.online_analysis.llm_client._resolve',return_value=('http://test','test-key','model')),patch('app.services.quant.screen_nl.model_name',return_value='model'):
        transport=asyncio.create_task(b.recognize_async('止损5%'))
        await asyncio.wait_for(transport_started.wait(),2)
        transport.cancel();await asyncio.gather(transport,return_exceptions=True)
        assert transport_closed.is_set()
    started=asyncio.Event();closed=asyncio.Event()
    async def slow(*args):
        started.set()
        try: await asyncio.Event().wait()
        finally: closed.set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        assert (await c.post('/agent/builder/recognize/cancel')).status_code==401
        with patch('app.services.quant.screen_quota.reserve'),patch.object(b,'recognize_async',slow):
            first=asyncio.create_task(c.post('/agent/builder/recognize',headers={'x-user':'a'},json={'text':'止损5%'}))
            await asyncio.wait_for(started.wait(),2)
            assert (await c.get('/agent/builder/recognize/status',headers={'x-user':'a'})).json()['running']
            assert (await c.post('/agent/builder/recognize',headers={'x-user':'a'},json={'text':'止损5%'})).status_code==429
            await c.post('/agent/builder/recognize/cancel',headers={'x-user':'b'})
            assert not closed.is_set()
            await c.post('/agent/builder/recognize/cancel',headers={'x-user':'a'})
            assert closed.is_set()
            assert (await first).status_code==409
            assert not (await c.get('/agent/builder/recognize/status',headers={'x-user':'a'})).json()['running']
        with patch('app.services.quant.screen_quota.reserve'),patch.object(b,'recognize_async',return_value={'rules':[]}):
            assert (await c.post('/agent/builder/recognize',headers={'x-user':'a'},json={'text':'止损5%'})).status_code==200
        with patch('app.services.quant.screen_quota.reserve'),patch.object(b,'recognize_async',slow),patch.object(r,'_AI_TIMEOUT',.02):
            assert (await c.post('/agent/builder/recognize',headers={'x-user':'a'},json={'text':'止损5%'})).status_code==504
            assert not r._ai_jobs
    print('CANCEL ALL OK：身份、跨账号隔离、重复拒绝、取消关闭、释放后重试、超时释放')
asyncio.run(main())
