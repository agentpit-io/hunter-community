# -*- coding: utf-8 -*-
"""登录中间件 · 「token 签名对、用户已不在库里」要回 401 —— 不连库。

    容器里跑(本机没有 fastapi):
    cd /app && PYTHONPATH=/app python tests/test_auth_user_gone.py     # 必须 ALL OK

## 为什么要有它

2026-09-25 本机重装:数据卷全清,启动器沿用了 JWT_SECRET,浏览器里还留着上一轮的 token。
中间件只验签名就放行,各接口查不到用户回 404「用户不存在」;前端只认 401 才清 token 重登,
于是模型选择器、合规弹层全挂,界面上看不出原因。
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.middleware import auth as mw  # noqa: E402
from app.services import database  # noqa: E402

FAILS: list[str] = []
N_OK = 0


def check(name, ok, detail=""):
    global N_OK
    ok = bool(ok)
    print(("OK   " if ok else "FAIL ") + name + (("  · " + str(detail)[:300]) if (detail and not ok) else ""))
    if ok:
        N_OK += 1
    else:
        FAILS.append(name)


# ── 假 token 与假库 ──────────────────────────────────────────────────
USERS = {"u-alive"}
DB = {"calls": 0, "broken": False}


def fake_verify(token):
    return {"sub": token, "type": "access", "role": "user"} if token.startswith("u-") else None


class _Cur:
    def execute(self, sql, args):
        self.row = (1,) if args[0] in USERS else None

    def fetchone(self):
        return self.row


class _Conn:
    def cursor(self):
        return _Cur()

    def close(self):
        pass


def fake_get_conn():
    DB["calls"] += 1
    if DB["broken"]:
        raise RuntimeError("db down")
    return _Conn()


mw._verify = fake_verify
database.get_conn = fake_get_conn

app = FastAPI()
app.add_middleware(mw.AuthMiddleware)


@app.get("/api/private")
async def private(request: Request):
    return {"uid": request.state.user_id}


@app.get("/api/catalog/x")
async def public(request: Request):
    return {"uid": getattr(request.state, "user_id", None)}


c = TestClient(app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731

# 1) 用户在库里 → 放行
r = c.get("/api/private", headers=H("u-alive"))
check("用户存在 → 200", r.status_code == 200 and r.json()["uid"] == "u-alive", r.text)

# 2) 签名对、用户不在 → 401 + needLogin(前端靠它清 token 重登)
r = c.get("/api/private", headers=H("u-gone"))
check("用户不存在 → 401", r.status_code == 401, r.status_code)
check("401 带 needLogin", r.json().get("needLogin") is True and r.json().get("error") == "INVALID_TOKEN", r.text)

# 3) 存在的用户走缓存,不每次连库
DB["calls"] = 0
for _ in range(5):
    c.get("/api/private", headers=H("u-alive"))
check("存在的用户命中缓存 · 不再连库", DB["calls"] == 0, DB["calls"])

# 4) 库挂了 → 放行(不能因为库抖一下把所有人踢回登录页)
mw._USER_OK.clear()
DB["broken"] = True
r = c.get("/api/private", headers=H("u-alive"))
check("查库失败 → 按存在放行", r.status_code == 200, r.status_code)
DB["broken"] = False

# 5) 公开路径:不存在的用户当匿名,不拒绝
r = c.get("/api/catalog/x", headers=H("u-gone"))
check("公开路径 · 用户不存在 → 匿名放行", r.status_code == 200 and r.json()["uid"] is None, r.text)
r = c.get("/api/catalog/x", headers=H("u-alive"))
check("公开路径 · 用户存在 → 认出身份", r.json()["uid"] == "u-alive", r.text)

# 6) 没带 token / 签名不对 → 行为不变
check("无 token → 401", c.get("/api/private").status_code == 401)
check("签名不对 → 401", c.get("/api/private", headers=H("bad")).status_code == 401)

print(f"\n{N_OK} OK · {len(FAILS)} FAIL")
if FAILS:
    print("FAILED:", FAILS)
    sys.exit(1)
print("ALL OK")
