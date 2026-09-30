"""模型可达不等于响应合法；错误分类不能泄漏上游正文、密钥或用户输入。"""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch
import httpx
from openai import APIConnectionError, APITimeoutError, APIStatusError
from app.services.quant import agent_builder as b

req=httpx.Request('POST','http://test/v1/chat/completions')
good=NS(choices=[NS(message=NS(content='{"rules":[{"type":"stop","params":{"pct":5},"source":"止损5%"}]}'),finish_reason='stop')],usage=None)
def status(code,message='sensitive-upstream-body'):
    return APIStatusError(message,response=httpx.Response(code,request=req),body={'message':message})

async def run(events, expected=None):
    calls=[]
    class Client:
        def __init__(self,**kw): self.chat=self;self.completions=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**kw):
            calls.append(kw)
            out=events.pop(0)
            if isinstance(out,BaseException):raise out
            return out
    with patch('openai.AsyncOpenAI',Client),patch('app.services.online_analysis.llm_client._resolve',return_value=('http://test','fake','model')),patch('app.services.quant.screen_nl.model_name',return_value='model'):
        try: result=await b.recognize_async('止损5%')
        except ValueError as e:
            assert expected and expected in str(e),str(e)
            assert 'sensitive-upstream-body' not in str(e)
        else:
            assert expected is None
            assert result['rules'][0]['params']['pct']==5
    return calls

async def main():
    for code,word in [(401,'凭据'),(403,'权限'),(402,'额度不足'),(404,'模型名称'),(429,'限流'),(400,'兼容性')]:
        assert len(await run([status(code)],word))==1
    assert len(await run([APIConnectionError(request=req),good]))==2
    await run([APIConnectionError(request=req),APIConnectionError(request=req)],'无法连接')
    assert len(await run([APITimeoutError(request=req)],'响应超时'))==1
    await run([status(503),status(503)],'暂时不可用')
    calls=await run([status(400,'response_format unsupported'),good])
    assert 'response_format' in calls[0] and 'response_format' not in calls[1]
    await run([NS(choices=[])],'没有返回')
    await run([NS(choices=[NS(message=NS(content='[]'),finish_reason='stop')])],'格式无效')
    await run([NS(choices=[NS(message=NS(content='unfinished'),finish_reason='length')])],'被截断')
    print('MODEL_ERRORS ALL OK：状态分类、连接有限重试、超时不重试、JSON兼容、空内容、截断、正文不泄漏')
asyncio.run(main())
