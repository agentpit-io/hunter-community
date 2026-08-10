"""微信服务号 OAuth 绑定登录（Component 第三方平台模式）

流程:
  GET  /wx/oauth        -> 构建授权 URL -> 302 跳微信
  GET  /wx/callback     -> code 换 openid -> Redis stateToken -> 302 跳绑定表单
  POST /wx/login/submit -> AgentPit 账号密码 -> 绑定 openid -> 返回 JWT
"""
import asyncio
import os
import secrets
from urllib.parse import quote
import time

import httpx
import psycopg2
import bcrypt
import jwt
import pymysql
import redis as redis_lib
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# ── 配置 ──────────────────────────────────────────────────────────────────────
COMPONENT_APPID  = os.getenv("WECHAT_COMPONENT_APPID",  "wxf179d0c02f8da1cd")
COMPONENT_SECRET = os.getenv("WECHAT_COMPONENT_SECRET", "2dae4bed481190f4e934dd94289dbe9c")
OA_APPID         = os.getenv("WECHAT_OA_APPID",         "wxbf533fc58dc6b296")
OA_DOMAIN        = os.getenv("WECHAT_OA_DOMAIN",        "https://yiqihecheng.net")

YIQIHECHENG_MYSQL = dict(
    host=os.getenv("YIQIHECHENG_MYSQL_HOST", "35.220.204.98"),
    port=int(os.getenv("YIQIHECHENG_MYSQL_PORT", "3306")),
    user=os.getenv("YIQIHECHENG_MYSQL_USER", "root"),
    password=os.getenv("YIQIHECHENG_MYSQL_PASSWORD", "AKPfnHDFCaMHwNxK22*"),
    database=os.getenv("YIQIHECHENG_MYSQL_DB", "myhub3"),
    connect_timeout=10,
)

REDIS_URL       = os.getenv("REDIS_URL", "redis://localhost:6379/0")
AGENTPIT_DB_URL = os.getenv(
    "AGENTPIT_DATABASE_URL",
    "",
)
FINANCE_DATA_URL   = os.getenv("FINANCE_DATA_URL",   "https://finance-data.agentpit.io")
FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")
JWT_SECRET  = os.getenv("JWT_SECRET", "")
JWT_EXPIRE  = 365 * 24 * 3600

_redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
_FD_HEADERS = {"X-Finance-Token": FINANCE_DATA_TOKEN}


# ── Token 工具 ────────────────────────────────────────────────────────────────

def _sync_component_token_from_mysql():
    """从易海报运营端 MySQL 同步 component_access_token 到 Redis。

    坑（2026-08-04 修复）：易海报侧会自主 refresh token，即使 Redis TTL 未到，
    微信服务端也已按新 token 校验。旧 token 生效 → 每次 callback 拿到 40001 →
    强制刷新 + 重试，每次登录额外 500-15000ms。
    对策：Hermes 侧 Redis TTL 最长缓 5 分钟，用 20-50ms 的 MySQL 查换掉一次
    500-13000ms 的微信 API 重试。
    """
    try:
        conn = pymysql.connect(**YIQIHECHENG_MYSQL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT accessToken, expiresIn FROM accesstoken ORDER BY lastModifyDate DESC LIMIT 1"
            )
            row = cur.fetchone()
        conn.close()
        if row:
            token, expires_in = row
            # 关键：最多缓 5 分钟，避免易海报 refresh 后 Hermes 用旧 token 打微信
            ttl = min(300, max(60, int(expires_in) - 300))
            _redis.set("wx_component_access_token", token, ex=ttl)
            logger.info("[wx_oauth] component_access_token 已同步，TTL={}s", ttl)
            return token
    except Exception as e:
        logger.error("[wx_oauth] 从易海报 MySQL 同步 token 失败: {}", e)
    return None


def _get_component_token():
    cached = _redis.get("wx_component_access_token")
    if cached:
        return cached
    token = _sync_component_token_from_mysql()
    if not token:
        raise RuntimeError("无法获取 component_access_token")
    return token

def _sync_authorizer_token_from_mysql():
    """同上，authorizer_token 也走短 TTL 兜底"""
    try:
        conn = pymysql.connect(**YIQIHECHENG_MYSQL)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT authorizerAccessToken, expiresIn FROM authorize WHERE authorizerAppid = %s",
                (OA_APPID,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            token, expires_in = row
            ttl = min(300, max(60, int(expires_in) - 300))
            _redis.set("wx_authorizer_access_token", token, ex=ttl)
            logger.info("[wx_oauth] authorizer_access_token 已同步，TTL={}s", ttl)
            return token
    except Exception as e:
        logger.error("[wx_oauth] 从 MySQL 同步 authorizer_token 失败: {}", e)
    return None


def _get_authorizer_token():
    cached = _redis.get("wx_authorizer_access_token")
    if cached:
        return cached
    return _sync_authorizer_token_from_mysql()


def _make_jwt(user_id, email, role):
    return jwt.encode(
        {"sub": user_id, "email": email, "role": role,
         "iat": int(time.time()), "exp": int(time.time()) + JWT_EXPIRE},
        JWT_SECRET, algorithm="HS256",
    )


# ── Step 1: 构建 OAuth URL ────────────────────────────────────────────────────

@router.get("/wx/oauth")
async def wx_oauth(source: str = ""):
    # source=adventurex：AdventureX 展位活动入口（服务号菜单「活动注册」），
    # 经 state 透传到回调，走活动注册流程（不自动建 @test 临时号）
    state = "adventurex" if source == "adventurex" else "bind"
    callback = quote(f"{OA_DOMAIN}/api/wx/callback", safe="")
    url = (
        "https://open.weixin.qq.com/connect/oauth2/authorize"
        f"?appid={OA_APPID}"
        f"&redirect_uri={callback}"
        "&response_type=code"
        "&scope=snsapi_userinfo"
        f"&state={state}"
        f"&component_appid={COMPONENT_APPID}"
        "&connect_redirect=1"
        "#wechat_redirect"
    )
    return RedirectResponse(url)


# ── Step 2: 微信回调，code -> openid -> stateToken ───────────────────────────

@router.get("/wx/callback")
async def wx_callback(code: str = "", state: str = ""):
    logger.info("[wx_oauth] callback ENTER state={} code={}...", state, code[:8])
    if not code:
        logger.warning("[wx_oauth] callback 无 code，重定向 login")
        return RedirectResponse(f"{OA_DOMAIN}/wx/login?error=no_code")
    try:
        component_token = _get_component_token()
        # 分段 timeout（2026-08-04 修复）：老 timeout=10 只保护 read，
        # 实测过 13s 的极端 case。改成 connect=3/read=5/write=3/pool=3 硬 cap 11s。
        _wx_timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
        async with httpx.AsyncClient(timeout=_wx_timeout) as c:
            r = await c.post(
                "https://api.weixin.qq.com/sns/oauth2/component/access_token",
                params={
                    "appid": OA_APPID,
                    "code": code,
                    "grant_type": "authorization_code",
                    "component_appid": COMPONENT_APPID,
                    "component_access_token": component_token,
                },
            )
        data = r.json()
        # 40001 = token 已被 WeChat 刷新，清缓存重试一次
        if data.get("errcode") == 40001:
            logger.warning("[wx_oauth] token 过期，强制刷新后重试")
            _redis.delete("wx_component_access_token")
            component_token = _sync_component_token_from_mysql()
            if not component_token:
                return RedirectResponse(f"{OA_DOMAIN}/wx/login?error=token_fail")
            async with httpx.AsyncClient(timeout=_wx_timeout) as c2:
                r = await c2.post(
                    "https://api.weixin.qq.com/sns/oauth2/component/access_token",
                    params={
                        "appid": OA_APPID,
                        "code": code,
                        "grant_type": "authorization_code",
                        "component_appid": COMPONENT_APPID,
                        "component_access_token": component_token,
                    },
                )
            data = r.json()
        openid  = data.get("openid", "")
        unionid = data.get("unionid", "")
        if not openid:
            logger.error("[wx_oauth] code 换 openid 失败: {}", data)
            return RedirectResponse(f"{OA_DOMAIN}/wx/login?error=oauth_fail")

        # nickname/headimgurl 只在展示层用,不阻塞主流程:先给占位符,再后台补
        # (P0 优化:老逻辑同步 await /sns/userinfo 每次 +0.5-2s,现在异步化)
        nickname = ""
        headimgurl = ""
        access_token = data.get("access_token", "")

        state_token = secrets.token_hex(24)
        _redis.set(
            f"wx_state:{state_token}",
            f"{openid}|{unionid}|{nickname}|{headimgurl}",
            ex=3600,  # 1h：给活动新用户从容填股票+邮箱的时间
        )

        # 后台异步拉真实 nickname/头像,拉到后更新 apbase_User + Redis state
        async def _bg_fetch_userinfo():
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    ur = await c.get(
                        "https://api.weixin.qq.com/sns/userinfo",
                        params={"access_token": access_token, "openid": openid,
                                "lang": "zh_CN"},
                    )
                ud = ur.json()
                nick = ud.get("nickname", "") or ""
                head = ud.get("headimgurl", "") or ""
                if not nick and not head:
                    return
                # 更新 Redis state (若还没被消费)
                try:
                    raw = _redis.get(f"wx_state:{state_token}")
                    if raw:
                        parts = raw.split("|", 3)
                        _redis.set(
                            f"wx_state:{state_token}",
                            f"{parts[0]}|{parts[1] if len(parts) > 1 else ''}|{nick}|{head}",
                            ex=3600,
                        )
                except Exception:
                    pass
                # 更新 apbase_User(若该 openid 已绑定)
                try:
                    conn_u = psycopg2.connect(AGENTPIT_DB_URL)
                    cur_u = conn_u.cursor()
                    cur_u.execute(
                        'UPDATE "apbase_User" SET "wechatNickname"=%s,"wechatHeadimgurl"=%s,'
                        '"updatedAt"=NOW() WHERE "wechatOpenid"=%s AND '
                        '("wechatNickname" IS NULL OR "wechatNickname"=%s)',
                        (nick, head, openid, ""),
                    )
                    conn_u.commit()
                    conn_u.close()
                except Exception as e:
                    logger.warning("[wx_oauth] bg 更新 nickname 失败(非致命): {}", e)
            except Exception as e:
                logger.warning("[wx_oauth] bg /sns/userinfo 失败(非致命): {}", e)
        asyncio.create_task(_bg_fetch_userinfo())
        is_ax = (state == "adventurex")  # AdventureX 展位活动来源
        logger.info(
            "[wx_oauth] openid 解析成功 openid={}*** unionid={}*** state_token={}... "
            "TTL=3600s source={}", openid[:8], unionid[:8] if unionid else '',
            state_token[:8], "adventurex" if is_ax else "login",
        )
        # 检查 openid 是否已绑定 (P0-a 优化: Redis 命中/miss 都只查一次 DB)
        bound_user_id = _redis.get(f"wx_uid_by_openid:{openid}")
        uid = uemail = urole = None
        try:
            conn_u = psycopg2.connect(AGENTPIT_DB_URL)
            cur_u  = conn_u.cursor()
            if bound_user_id:
                # Redis 命中: 按 uid 查 email/role
                cur_u.execute(
                    'SELECT id, email, role FROM "apbase_User" WHERE id = %s',
                    (bound_user_id,),
                )
            else:
                # Redis miss: 按 openid 兜底查 (合并了原来的 2 次连接为 1 次)
                cur_u.execute(
                    'SELECT id, email, role FROM "apbase_User" WHERE "wechatOpenid" = %s',
                    (openid,),
                )
            urow = cur_u.fetchone()
            conn_u.close()
            if urow:
                uid, uemail, urole = urow
                if not bound_user_id:
                    # 兜底命中: 回填 Redis 反查键 (双向)
                    _redis.set(f"wx_uid_by_openid:{openid}", uid, ex=365*24*3600)
                    _redis.set(f"wx_openid:{uid}", openid, ex=365*24*3600)
                    logger.info("[wx_oauth] openid 绑定经 DB 兜底命中 user_id={}", uid)
        except Exception as db_err:
            logger.error("[wx_oauth] 自动登录查库失败: {}", db_err)

        if uid:
            if is_ax and uemail.endswith("@test"):
                # 活动来源 + 只有 @test 临时号：仍需留邮箱（注册接口会原地升级账号）
                logger.info(
                    "[wx_oauth] AX 分支：已绑 @test 临时号 uid={} email={} → intake",
                    uid, uemail,
                )
                return RedirectResponse(f"{OA_DOMAIN}/wx/ax?state={state_token}")
            auto_token = _make_jwt(uid, uemail, urole or "USER")
            if is_ax:
                # 活动来源 + 已有真实账号：免注册直接进活动
                logger.info(
                    "[wx_oauth] AX 分支：老用户直进活动 uid={} email={}",
                    uid, uemail,
                )
                return RedirectResponse(f"{OA_DOMAIN}/wx/ax?t={auto_token}")
            # 老 @test 临时号(无真实邮箱): 引导进 /wx/ax 完成邮箱注册,
            # 避免现场活动用户误点「登陆注册」后跳过通关送礼流程。
            if uemail.endswith("@test"):
                logger.info(
                    "[wx_oauth] LOGIN 分支：老 @test 临时号 → 跳 /wx/ax 补邮箱 uid={}",
                    uid,
                )
                # 同时带 state:前端强制 intake + intake 表单可用 state 反查 openid 注册
                return RedirectResponse(
                    f"{OA_DOMAIN}/wx/ax?t={auto_token}&state={state_token}"
                )
            # P0-b 优化: 若已知用户偏好 (user_pref:{uid}:end),直接 302 到对应工作台,
            # 跳过 /entry 中转页那一跳 (省 300-800ms 的客户端 replace)
            pref_end = _redis.get(f"user_pref:{uid}:end")
            if pref_end in ("wx", "gm"):
                target = "/wx/home" if pref_end == "wx" else "/gm/home"
                logger.info(
                    "[wx_oauth] LOGIN 分支：openid 已绑 + 偏好命中 → 直进 {} uid={}",
                    target, uid,
                )
                return RedirectResponse(f"{OA_DOMAIN}{target}?t={auto_token}")
            logger.info(
                "[wx_oauth] LOGIN 分支：openid 已绑，自动登录 uid={} email={}",
                uid, uemail,
            )
            return RedirectResponse(f"{OA_DOMAIN}/entry?t={auto_token}")
        if is_ax:
            # ── AdventureX：不自动建号，先去邮箱注册页 ──
            logger.info(
                "[wx_oauth] AX 分支：新用户 → intake 页 openid={}*** state_token={}...",
                openid[:8], state_token[:8],
            )
            return RedirectResponse(f"{OA_DOMAIN}/wx/ax?state={state_token}")
        # ── 未绑定：自动注册 0000x@test 账号 ──
        try:
            import uuid as _uuid
            conn_reg = psycopg2.connect(AGENTPIT_DB_URL)
            cur_reg  = conn_reg.cursor()
            cur_reg.execute(
                """SELECT email FROM "apbase_User"
                   WHERE email ~ '^[0-9]+@test$'
                   ORDER BY CAST(split_part(email,'@',1) AS INTEGER) DESC LIMIT 1"""
            )
            row_num = cur_reg.fetchone()
            next_num = (int(row_num[0].split("@")[0]) + 1) if row_num else 1
            new_email = f"{next_num:05d}@test"
            # 防并发冲突
            cur_reg.execute('SELECT id FROM "apbase_User" WHERE email = %s', (new_email,))
            if cur_reg.fetchone():
                next_num += 1
                new_email = f"{next_num:05d}@test"

            auto_uid  = "c" + _uuid.uuid4().hex[:24]
            rand_pw   = secrets.token_hex(16)
            pw_hash   = bcrypt.hashpw(rand_pw.encode(), bcrypt.gensalt()).decode()
            disp_name = nickname or new_email

            cur_reg.execute(
                """INSERT INTO "apbase_User"
                   (id, email, password, name, role, "createdAt", "updatedAt",
                    "wechatOpenid", "wechatUnionid", "wechatNickname", "wechatHeadimgurl")
                   VALUES (%s,%s,%s,%s,'USER',NOW(),NOW(),%s,%s,%s,%s)""",
                (auto_uid, new_email, pw_hash, disp_name,
                 openid, unionid or None, nickname or None, headimgurl or None),
            )
            conn_reg.commit()
            conn_reg.close()

            # 写 finance-data 绑定 (P0 优化:异步化,不阻塞主流程 302 跳转)
            # 老逻辑 await httpx.timeout=10s 最坏 +10 秒;推送侧 5min+ 延迟,晚 1s
            # 到达不影响用户体验
            async def _bg_bind_finance_data():
                try:
                    async with httpx.AsyncClient(timeout=5) as hc:
                        await hc.post(
                            f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
                            headers=_FD_HEADERS,
                            json={"user_id": auto_uid, "email": new_email,
                                  "openid": openid, "unionid": unionid,
                                  "nickname": nickname, "headimgurl": headimgurl},
                        )
                except Exception as fd_err:
                    logger.warning("[wx_oauth] finance-data 绑定失败(非致命,后台): {}",
                                   fd_err)
            asyncio.create_task(_bg_bind_finance_data())

            # 写 Redis
            _redis.set(f"wx_uid_by_openid:{openid}", auto_uid, ex=365*24*3600)
            _redis.set(f"wx_openid:{auto_uid}", openid, ex=90*24*3600)
            _redis.sadd("wx_bound_users", auto_uid)

            auto_token = _make_jwt(auto_uid, new_email, "USER")
            logger.info(
                "[wx_oauth] LOGIN 分支：新建 @test 号 → 跳 /wx/ax 引导补邮箱 email={} uid={}",
                new_email, auto_uid,
            )
            # 新 @test 号无真实邮箱,引导进通关领礼补邮箱。同时带 state:
            # 前端强制 intake, intake 表单可用 state 反查 openid 完成注册
            return RedirectResponse(
                f"{OA_DOMAIN}/wx/ax?t={auto_token}&state={state_token}"
            )
        except Exception as reg_err:
            logger.exception("[wx_oauth] LOGIN 分支：自动注册失败，降级到登录表单: {}", reg_err)
            return RedirectResponse(f"{OA_DOMAIN}/wx/login?state={state_token}")
    except Exception as e:
        logger.exception("[wx_oauth] callback 异常: {}", e)
        return RedirectResponse(f"{OA_DOMAIN}/wx/login?error=server_error")


# ── Step 3: 提交 -> 验证 -> 绑定 -> 返回 JWT ─────────────────────────────────

class SubmitRequest(BaseModel):
    state: str
    email: str
    password: str


@router.post("/wx/login/submit")
async def wx_submit(body: SubmitRequest):
    email = body.email.lower().strip()

    # ① Redis 取 openid
    raw = _redis.get(f"wx_state:{body.state}")
    if not raw:
        return JSONResponse({"error": "微信授权已过期，请重新进入页面"}, status_code=401)
    parts    = raw.split("|", 3)
    openid   = parts[0]
    unionid  = parts[1] if len(parts) > 1 else ""
    nickname = parts[2] if len(parts) > 2 else ""
    headimg  = parts[3] if len(parts) > 3 else ""

    # ② 验证 AgentPit 账号密码（共享数据库服务器 10.128.0.7）
    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur  = conn.cursor()
        cur.execute(
            'SELECT id, email, password, name, role FROM "apbase_User" WHERE email = %s',
            (email,),
        )
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        logger.error("[wx_oauth] AgentPit DB 查询失败: {}", e)
        return JSONResponse({"error": "服务器错误，请稍后重试"}, status_code=500)

    if not row:
        return JSONResponse({"error": "账号不存在，请先前往 AgentPit 注册"}, status_code=404)

    user_id, user_email, pw_hash, user_name, role = row
    try:
        ok = bcrypt.checkpw(body.password.encode(), pw_hash.encode())
    except Exception:
        ok = False
    if not ok:
        return JSONResponse({"error": "密码错误"}, status_code=403)

    # ③ 绑定 openid -> finance-data 数据库服务器
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
                headers=_FD_HEADERS,
                json={
                    "user_id": user_id, "email": user_email,
                    "openid": openid, "unionid": unionid,
                    "nickname": nickname, "headimgurl": headimg,
                },
            )
        if r.status_code != 200:
            logger.error("[wx_oauth] finance-data 绑定失败: {} {}", r.status_code, r.text)
            return JSONResponse({"error": "绑定存储失败，请联系管理员"}, status_code=500)
    except Exception as e:
        logger.error("[wx_oauth] finance-data 请求异常: {}", e)
        return JSONResponse({"error": "绑定服务暂时不可用"}, status_code=500)

    # ④ 核销 stateToken，缓存 openid 供推送使用，返回 JWT
    _redis.delete(f"wx_state:{body.state}")
    _redis.set(f"wx_openid:{user_id}", openid, ex=90 * 24 * 3600)
    _redis.sadd("wx_bound_users", user_id)
    token = _make_jwt(user_id, user_email, role or "USER")
    logger.info("[wx_oauth] 绑定成功 user_id={} openid={}***", user_id, openid[:8])
    return JSONResponse({
        "ok": True,
        "token": token,
        "user": {"id": user_id, "email": user_email, "name": user_name},
        "msg": "绑定成功，欢迎使用 Hermes 财经聚合",
    })


class MobileLoginRequest(BaseModel):
    email: str
    password: str


@router.post("/wx/mobile/login")
async def wx_mobile_login(body: MobileLoginRequest):
    """手机端直接登录（不走微信 OAuth），返回 JWT 供手机版主页使用。"""
    email = body.email.lower().strip()
    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur  = conn.cursor()
        cur.execute(
            'SELECT id, email, password, name, role FROM "apbase_User" WHERE email = %s',
            (email,),
        )
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        logger.error("[mobile_login] DB 查询失败: {}", e)
        return JSONResponse({"error": "服务器错误，请稍后重试"}, status_code=500)

    if not row:
        return JSONResponse({"error": "账号不存在"}, status_code=404)

    user_id, user_email, pw_hash, user_name, role = row
    try:
        ok = bcrypt.checkpw(body.password.encode(), pw_hash.encode())
    except Exception:
        ok = False
    if not ok:
        return JSONResponse({"error": "密码错误"}, status_code=403)

    token = _make_jwt(user_id, user_email, role or "USER")
    logger.info("[mobile_login] 登录成功 user_id={}", user_id)
    return JSONResponse({
        "ok": True,
        "token": token,
        "user": {"id": user_id, "email": user_email, "name": user_name or email.split("@")[0]},
    })


# ── 绑定真实账号（将微信自动注册的 0000x@test 合并到真实 AgentPit 账号）────────

class BindAccountRequest(BaseModel):
    email: str
    password: str

@router.post("/wx/bind-account")
async def bind_account(body: BindAccountRequest, request: Request):
    """验证真实 AgentPit 账号，将微信绑定从临时账号迁移过去。"""
    from fastapi import Request as _Req
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "未登录"}, status_code=401)
    try:
        payload = jwt.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return JSONResponse({"error": "token 无效"}, status_code=401)

    auto_user_id = payload.get("sub", "")
    auto_email   = payload.get("email", "")
    if not auto_email.endswith("@test"):
        return JSONResponse({"error": "当前账号已是正式账号，无需绑定"}, status_code=400)

    real_email = body.email.lower().strip()
    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur  = conn.cursor()
        cur.execute(
            'SELECT id, email, password, name, role FROM "apbase_User" WHERE email = %s',
            (real_email,),
        )
        row = cur.fetchone()
    except Exception as e:
        return JSONResponse({"error": "数据库错误"}, status_code=500)

    if not row:
        conn.close()
        return JSONResponse({"error": "账号不存在"}, status_code=404)

    real_uid, real_email_db, pw_hash, real_name, real_role = row
    try:
        ok = bcrypt.checkpw(body.password.encode(), pw_hash.encode())
    except Exception:
        ok = False
    if not ok:
        conn.close()
        return JSONResponse({"error": "密码错误"}, status_code=403)

    # 读取自动账号的微信信息
    cur.execute(
        'SELECT "wechatOpenid","wechatUnionid","wechatNickname","wechatHeadimgurl" FROM "apbase_User" WHERE id = %s',
        (auto_user_id,),
    )
    wx_row = cur.fetchone() or (None, None, None, None)
    wx_openid, wx_unionid, wx_nickname, wx_headimgurl = wx_row

    # 先清除自动账号的微信字段（unique constraint 要求先释放再写入）
    cur.execute(
        'UPDATE "apbase_User" SET "wechatOpenid"=NULL,"wechatUnionid"=NULL,"updatedAt"=NOW() WHERE id=%s',
        (auto_user_id,),
    )
    # 再把微信字段写到真实账号
    cur.execute(
        '''UPDATE "apbase_User"
           SET "wechatOpenid"=%s,"wechatUnionid"=%s,"wechatNickname"=%s,
               "wechatHeadimgurl"=%s,"updatedAt"=NOW()
           WHERE id=%s''',
        (wx_openid, wx_unionid, wx_nickname, wx_headimgurl, real_uid),
    )
    conn.commit()
    conn.close()

    # 更新 Redis
    if wx_openid:
        _redis.set(f"wx_uid_by_openid:{wx_openid}", real_uid, ex=365*24*3600)
        _redis.set(f"wx_openid:{real_uid}", wx_openid, ex=90*24*3600)
        _redis.delete(f"wx_openid:{auto_user_id}")
    _redis.sadd("wx_bound_users", real_uid)
    _redis.srem("wx_bound_users", auto_user_id)

    # 迁移 hermes DB：自选股 + 推送任务
    try:
        HERMES_DB = "postgresql://hermes:Hermes2026DB!@localhost:5432/hermes"
        conn_h = psycopg2.connect(HERMES_DB)
        cur_h = conn_h.cursor()
        # stocks: 复合主键 (code, user_id)，先 INSERT 不冲突的，再删旧记录
        cur_h.execute(
            "INSERT INTO stocks (code, name, market, exchange, enabled, asset_type, user_id) "
            "SELECT code, name, market, exchange, enabled, asset_type, %s "
            "FROM stocks WHERE user_id = %s "
            "ON CONFLICT (code, user_id) DO NOTHING",
            (real_uid, auto_user_id)
        )
        cur_h.execute("DELETE FROM stocks WHERE user_id = %s", (auto_user_id,))
        # push_tasks: id 为主键，直接改 user_id
        cur_h.execute("UPDATE push_tasks SET user_id = %s WHERE user_id = %s", (real_uid, auto_user_id))
        conn_h.commit()
        conn_h.close()
        logger.info("[wx_oauth] hermes DB 迁移完成: {}→{}", auto_user_id, real_uid)
    except Exception as e:
        logger.warning("[wx_oauth] hermes DB 迁移失败: {}", e)

    new_token = _make_jwt(real_uid, real_email_db, real_role or "USER")
    logger.info("[wx_oauth] 账号合并: {}→{}", auto_email, real_email_db)
    return JSONResponse({"ok": True, "token": new_token, "email": real_email_db, "name": real_name})
