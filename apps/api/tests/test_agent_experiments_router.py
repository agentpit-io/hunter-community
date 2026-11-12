from unittest.mock import patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app.routers.agent_experiments import router
from app.services.quant import agent_experiments as e

app=FastAPI()
@app.middleware('http')
async def identity(request: Request,next_):
    if request.headers.get('x-test-user'): request.state.user_id=request.headers['x-test-user']
    return await next_(request)
app.include_router(router)
client=TestClient(app)


def test_all_routes_require_identity():
    for method,path in [('get',''),('get','/id'),('post',''),('post','/id/run'),('post','/id/cancel')]:
        assert getattr(client,method)('/agent/experiments'+path).status_code==401


def test_create_validates_and_launches_only_saved_task():
    headers={'x-test-user':'alice'}
    assert client.post('/agent/experiments',headers=headers,content='{').status_code==400
    with patch.object(e,'create',side_effect=ValueError('不支持的字段')),patch.object(e,'launch') as launch:
        assert client.post('/agent/experiments',headers=headers,json=[]).status_code==400
        launch.assert_not_called()
    with patch.object(e,'create',return_value=dict(id='abc',status='planning')) as create,patch.object(e,'launch') as launch:
        assert client.post('/agent/experiments',headers=headers,json={}).status_code==200
        create.assert_called_once_with('alice',{})
        launch.assert_called_once_with('alice','abc','plan')


def test_owner_from_session_and_rejected_start_never_launches():
    with patch.object(e,'read',return_value={}) as read:
        assert client.get('/agent/experiments/abc',headers={'x-test-user':'bob'}).status_code==200
        read.assert_called_once_with('bob','abc')
    with patch.object(e,'start',side_effect=ValueError('实验不存在')),patch.object(e,'launch') as launch:
        assert client.post('/agent/experiments/abc/run',headers={'x-test-user':'bob'}).status_code==400
        launch.assert_not_called()
