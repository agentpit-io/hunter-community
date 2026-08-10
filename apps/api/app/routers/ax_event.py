"""AdventureX 展位活动 API。

流程（V3 文档 + 菜单入口版）：
  服务号菜单「活动注册」 → GET /api/wx/oauth?source=adventurex
    → 回调识别 state=adventurex → 302 /wx/ax?state=xxx（未注册）或 /wx/ax?t=JWT（已注册）
  POST /api/ax/register   邮箱注册：建号/升级临时号 + 发密码邮件 + 绑 openid + 免密登录
  POST /api/ax/level1     第一级完成（AI 分析 · 通关送礼）→ 体验礼核销码 + 3 个月 pro 会员
  POST /api/ax/level2     第二级完成（导入持仓）→ 加自选股+成本价 + 股民礼核销码 + 3 个月会员
  GET  /api/ax/me         我的活动状态（奖励领取页数据源）
  展位后台（仅 ADMIN / 白名单）：
  GET  /api/ax/admin/list?q=   参与用户列表/搜索
  POST /api/ax/admin/redeem    按核销码核销
  GET  /api/ax/admin/stats     统计看板
"""
import asyncio
import os
import re
import secrets
import uuid

import bcrypt
import jwt as jwt_lib
import psycopg2
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from app.routers.wx_oauth import (
    AGENTPIT_DB_URL, FINANCE_DATA_URL, _FD_HEADERS, JWT_SECRET,
    _make_jwt, _redis,
)
from app.services import ax_service
from app.services.ax_features import (
    AX_ALL_FEATURES, AX_FEATURE_IDS, AX_UNLOCK_THRESHOLD,
    get_assigned_features, get_assigned_ids,
)
from app.services.database import add_stock_by_user, upsert_thesis_by_user
from app.services.finance_data_client import get_kline_with_fallback
from app.services.finance_data_client import subscribe as fd_subscribe
from app.services import wx_push

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ADMIN_EMAILS = set(
    e.strip().lower()
    for e in os.getenv("AX_ADMIN_EMAILS", "hangeaiagent@gmail.com").split(",")
    if e.strip()
)
_AX_PAGE_URL = os.getenv("WECHAT_OA_DOMAIN", "https://yiqihecheng.net") + "/wx/ax"


def _get_openid(user_id: str) -> str:
    openid = _redis.get(f"wx_openid:{user_id}")
    if openid:
        return openid
    row = ax_service.get_ax_row(user_id=user_id)
    return row["openid"] if row else ""


def _decode_token(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        return jwt_lib.decode(auth[7:], JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(401, "token 无效")


def _require_admin(request: Request) -> str:
    payload = _decode_token(request)
    email = (payload.get("email") or "").lower()
    if payload.get("role") != "ADMIN" and email not in _ADMIN_EMAILS:
        raise HTTPException(403, "无展位后台权限")
    return email


async def _push_reward_message(user_id: str, level: int, code: str,
                               extra: str = "") -> None:
    """完成消息推送（模板消息，失败不影响主流程）。"""
    try:
        if level == 1:
            title = "🎉 通关成功！你的奖励到了"
            content = (
                f"体验礼已解锁：铜钱钥匙链 + 3 个月 pro 会员\n"
                f"核销码：{code}，凭码到展位领取\n"
                f"{extra}\n"
                f"💎 导入真实持仓可再升级为高级伴手礼"
            )
        else:
            title = "💎 持仓监控已开启！"
            content = (
                f"股民礼已解锁：高级伴手礼 + 3 个月会员\n"
                f"核销码：{code}，凭码到展位领取\n"
                f"{extra}\n"
                f"有异动第一时间告诉你，不只说\"跌了\"，更说\"该不该慌\"。"
            )
        await wx_push.broadcast(title, content, log_id="", user_id=user_id,
                                push_type="", detail_url=_AX_PAGE_URL)
    except Exception as e:
        logger.warning("[ax] 奖励消息推送失败(非致命) user={}: {}", user_id, e)


# ── 注册 ─────────────────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    state: str
    email: str


@router.post("/ax/register")
async def ax_register(body: RegisterIn):
    """邮箱注册：state 换 openid → 建号（或升级 @test 临时号）→ 发邮件 → 免密登录。"""
    email = body.email.lower().strip()
    if not _EMAIL_RE.match(email) or email.endswith("@test"):
        return JSONResponse({"error": "请输入有效的邮箱地址"}, status_code=400)

    raw = _redis.get(f"wx_state:{body.state}")
    if not raw:
        return JSONResponse({"error": "微信授权已过期，请回到服务号重新点「活动注册」"},
                            status_code=401)
    parts = raw.split("|", 3)
    openid = parts[0]
    unionid = parts[1] if len(parts) > 1 else ""
    nickname = parts[2] if len(parts) > 2 else ""
    headimg = parts[3] if len(parts) > 3 else ""

    password = ax_service.gen_password()
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    send_mail = True

    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur = conn.cursor()
        bound_uid = _redis.get(f"wx_uid_by_openid:{openid}")

        if bound_uid:
            cur.execute('SELECT id, email, role FROM "apbase_User" WHERE id = %s',
                        (bound_uid,))
            urow = cur.fetchone()
        else:
            urow = None

        if urow and not urow[1].endswith("@test"):
            # 已绑定真实账号（防御分支：回调正常不会走到这）——直接放行进活动
            uid, uemail, role = urow
            email = uemail
            send_mail = False
        elif urow:
            # 微信之前自动建过 @test 临时号：原地升级为真实邮箱账号（保留自选股等数据）
            uid, _old_email, role = urow
            cur.execute('SELECT id FROM "apbase_User" WHERE email = %s', (email,))
            if cur.fetchone():
                conn.close()
                return JSONResponse(
                    {"error": "该邮箱已注册过账号，请换一个邮箱，或找工作人员协助"},
                    status_code=409)
            cur.execute(
                '''UPDATE "apbase_User"
                   SET email = %s, password = %s, name = %s, "updatedAt" = NOW()
                   WHERE id = %s''',
                (email, pw_hash, nickname or email.split("@")[0], uid),
            )
            conn.commit()
        else:
            # 全新用户：创建真实账号
            cur.execute('SELECT id FROM "apbase_User" WHERE email = %s', (email,))
            if cur.fetchone():
                conn.close()
                return JSONResponse(
                    {"error": "该邮箱已注册过账号，请点服务号菜单「登陆注册」用原密码绑定，"
                              "或换一个邮箱"},
                    status_code=409)
            uid = "c" + uuid.uuid4().hex[:24]
            role = "USER"
            cur.execute(
                '''INSERT INTO "apbase_User"
                   (id, email, password, name, role, "createdAt", "updatedAt",
                    "wechatOpenid", "wechatUnionid", "wechatNickname", "wechatHeadimgurl")
                   VALUES (%s,%s,%s,%s,'USER',NOW(),NOW(),%s,%s,%s,%s)''',
                (uid, email, pw_hash, nickname or email.split("@")[0],
                 openid, unionid or None, nickname or None, headimg or None),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[ax] 注册数据库操作失败: {}", e)
        return JSONResponse({"error": "服务器繁忙，请稍后重试"}, status_code=500)

    # 绑定关系：Redis + finance-data（非致命）
    _redis.set(f"wx_uid_by_openid:{openid}", uid, ex=365 * 24 * 3600)
    _redis.set(f"wx_openid:{uid}", openid, ex=365 * 24 * 3600)
    _redis.sadd("wx_bound_users", uid)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hc:
            await hc.post(
                f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
                headers=_FD_HEADERS,
                json={"user_id": uid, "email": email, "openid": openid,
                      "unionid": unionid, "nickname": nickname, "headimgurl": headimg},
            )
    except Exception as fd_err:
        logger.warning("[ax] finance-data 绑定失败(非致命): {}", fd_err)

    # 活动表落行
    await asyncio.to_thread(ax_service.upsert_registration, openid, uid, email, nickname)

    # 发账号密码邮件（后台执行，不阻塞注册返回）
    if send_mail:
        asyncio.create_task(asyncio.to_thread(ax_service.send_account_email, email, password))

    _redis.delete(f"wx_state:{body.state}")
    token = _make_jwt(uid, email, role or "USER")
    logger.info("[ax] 活动注册成功 email={} uid={}", email, uid)
    return {"ok": True, "token": token, "email": email}


# ── 已登录用户直接进活动（老板场景：H5 有登录态但 openid 反查键缺失）──────────

class AttachIn(BaseModel):
    state: str


@router.post("/ax/attach")
async def ax_attach(body: AttachIn, request: Request):
    """把微信 openid 绑定到当前已登录账号，跳过邮箱注册直接进活动。

    场景：用户早期通过手机账号密码登录/旧版绑定表单进的 H5，localStorage 有
    有效 JWT，但 openid↔user 反查关系缺失 → 点「活动注册」被误判为新用户。
    前端检测到「有 state 也有登录态」时调本接口续绑。
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    payload = _decode_token(request)
    email = payload.get("email", "")

    logger.info(
        "[ax attach] ENTER user_id={} email={} state_prefix={}...",
        user_id, email, (body.state or '')[:8],
    )
    raw = _redis.get(f"wx_state:{body.state}")
    if not raw:
        logger.warning("[ax attach] 401 state 已不在 Redis state_prefix={}...", (body.state or '')[:8])
        return JSONResponse({"error": "微信授权已过期"}, status_code=401)
    parts = raw.split("|", 3)
    openid = parts[0]
    unionid = parts[1] if len(parts) > 1 else ""
    nickname = parts[2] if len(parts) > 2 else ""
    headimg = parts[3] if len(parts) > 3 else ""

    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur = conn.cursor()
        # openid 若被其他账号占用（如历史 @test 临时号），先释放（unique 约束）
        cur.execute(
            'SELECT id, email FROM "apbase_User" WHERE "wechatOpenid" = %s AND id <> %s',
            (openid, user_id))
        other = cur.fetchone()
        if other:
            cur.execute(
                'UPDATE "apbase_User" SET "wechatOpenid"=NULL,"wechatUnionid"=NULL,'
                '"updatedAt"=NOW() WHERE id=%s', (other[0],))
            logger.info("[ax attach] 释放旧占用 openid: {} ({})", other[0], other[1])
        cur.execute(
            '''UPDATE "apbase_User"
               SET "wechatOpenid"=%s,
                   "wechatUnionid"=COALESCE(NULLIF(%s,''), "wechatUnionid"),
                   "updatedAt"=NOW()
               WHERE id=%s''',
            (openid, unionid, user_id))
        affected = cur.rowcount
        conn.commit()
        conn.close()
        if affected == 0:
            # 幽灵 user_id：JWT 签名 OK 但 apbase_User 里已无此行（如运维清理过）
            logger.error(
                "[ax attach] 幽灵 user_id：apbase_User UPDATE 影响 0 行 uid={} openid={}*** —— "
                "前端应清 localStorage 重新走注册",
                user_id, openid[:8],
            )
            return JSONResponse(
                {"error": "GHOST_USER", "message": "登录状态无效，请点一键重新授权"},
                status_code=401,
            )
    except Exception as e:
        logger.exception("[ax attach] 数据库操作失败: {}", e)
        return JSONResponse({"error": "服务器繁忙，请稍后重试"}, status_code=500)

    _redis.set(f"wx_uid_by_openid:{openid}", user_id, ex=365 * 24 * 3600)
    _redis.set(f"wx_openid:{user_id}", openid, ex=365 * 24 * 3600)
    _redis.sadd("wx_bound_users", user_id)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hc:
            await hc.post(
                f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
                headers=_FD_HEADERS,
                json={"user_id": user_id, "email": email, "openid": openid,
                      "unionid": unionid, "nickname": nickname, "headimgurl": headimg},
            )
    except Exception as fd_err:
        logger.warning("[ax attach] finance-data 绑定失败(非致命): {}", fd_err)

    await asyncio.to_thread(ax_service.upsert_registration, openid, user_id, email, nickname)
    # V4 Fix：不 delete wx_state，让后续 register-and-analyze 或 1h TTL 消费
    # 原代码 _redis.delete(f"wx_state:{body.state}") 会导致 attach 后前端 fall through 到 intake
    # 时 state 已死 → 用户填表提交后无谓 401「过期」
    logger.info(
        "[ax attach] SUCCESS user_id={} openid={}*** rowcount={} (state 保留，TTL 自然过期)",
        user_id, openid[:8], affected,
    )
    return {"ok": True}


# ── 第一级（异步版）：提交即返回，后台看护分析任务，完成后微信推送 ────────────
# 现场排队场景：用户不在页面等 1-3 分钟，提交后手机还给下一位，
# 分析跑完自动推模板消息，点消息回到 /wx/ax?report=ID 查看报告和核销码。

async def _watch_analysis_and_reward(user_id: str, openid: str, task_id: str,
                                     stock_code: str, stock_name: str) -> None:
    from app.routers import online_analysis as _oa
    final = None
    for _ in range(144):  # 最多等 12 分钟
        await asyncio.sleep(5)
        final = _oa._RESULTS.get(task_id)
        if final is not None:
            break
    report_id = (final or {}).get("report_id")
    try:
        row = await asyncio.to_thread(
            ax_service.complete_level1, openid, stock_code, stock_name, report_id)
    except Exception as e:
        logger.error("[ax] watcher 发奖失败 user={}: {}", user_id, e)
        return
    code = row["level1_code"]
    url = f"{_AX_PAGE_URL}?report={report_id}" if report_id else _AX_PAGE_URL
    if final:
        title = f"分析完成 · {stock_name or stock_code}"
        content = (f"你体检的「{stock_name or stock_code}」多智能体分析已完成\n"
                   f"🎫 体验礼核销码：{code}（凭码到展位领取）\n"
                   f"点击查看完整真相报告，并可导入持仓升级股民礼")
    else:
        # 分析超时/异常：奖励照发（体验礼人人可得），引导回页面
        title = "你的体验礼到了"
        content = (f"🎫 体验礼核销码：{code}（凭码到展位领取）\n"
                   f"报告生成稍有延迟，点击回到活动页查看")
    try:
        await wx_push.broadcast(title, content, log_id="", user_id=user_id,
                                push_type="", detail_url=url)
        logger.info("[ax] level1 完成推送 user={} report={}", user_id, report_id)
    except Exception as e:
        logger.warning("[ax] level1 完成推送失败 user={}: {}", user_id, e)


class Level1SubmitIn(BaseModel):
    task_id: str
    stock_code: str
    stock_name: str = ""


@router.post("/ax/level1/submit")
async def ax_level1_submit(body: Level1SubmitIn, request: Request):
    """登记后台看护：分析完成后自动发奖+推微信消息，前端无需等待。"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    openid = _get_openid(user_id)
    if not openid:
        raise HTTPException(400, "未找到微信绑定，请从服务号菜单重新进入")
    if not ax_service.get_ax_row(openid=openid):
        await asyncio.to_thread(
            ax_service.upsert_registration, openid, user_id,
            _decode_token(request).get("email", ""), "")
    asyncio.create_task(_watch_analysis_and_reward(
        user_id, openid, body.task_id, body.stock_code.strip(), body.stock_name.strip()))
    return {"ok": True, "msg": "分析完成后将通过微信消息提醒"}


# ── V4：合并 Step 1（邮箱 + 股票一次提交，分析完成邮件送达）──────────────────

async def _watch_analysis_and_email_report(
    user_id: str, openid: str, task_id: str,
    stock_code: str, stock_name: str, to_email: str,
    account_password: str = "",
) -> None:
    """V4 watcher：等分析结果 → 写核销码 → 发综合邮件（分析报告+账号+核销码）+ wx push。"""
    from app.routers import online_analysis as _oa
    final = None
    for _ in range(144):  # 最多等 12 分钟
        await asyncio.sleep(5)
        final = _oa._RESULTS.get(task_id)
        if final is not None:
            break

    report_id = (final or {}).get("report_id")
    try:
        row = await asyncio.to_thread(
            ax_service.complete_level1, openid, stock_code, stock_name, report_id)
    except Exception as e:
        logger.error("[ax] V4 watcher 发奖失败 user={}: {}", user_id, e)
        return
    code = row["level1_code"]
    report_url = (
        f"{_AX_PAGE_URL}?report={report_id}" if report_id else _AX_PAGE_URL
    )

    # 发邮件（核心，V4 首要通知通道）
    decision = (final or {}).get("decision", "HOLD")
    confidence = (final or {}).get("confidence")
    key_reason = (final or {}).get("key_reason", "") or (final or {}).get("summary", "")
    try:
        sent = await asyncio.to_thread(
            ax_service.send_analysis_email,
            to_email, stock_name, stock_code,
            decision, confidence, key_reason,
            report_url, code, account_password, _AX_PAGE_URL,
            final or None,  # V4.1：把完整 final dict 直接嵌入邮件正文
        )
        if sent:
            await asyncio.to_thread(ax_service.mark_level1_email_sent, openid)
    except Exception as e:
        logger.warning("[ax] V4 分析邮件发送失败 user={}: {}", user_id, e)

    # 兼容：同时发微信模板消息（用户可能已关注服务号）
    try:
        title = f"分析完成 · {stock_name or stock_code}"
        if final:
            content = (f"「{stock_name or stock_code}」AI 分析完成，报告已发到你的邮箱\n"
                       f"🎫 体验礼核销码：{code}\n"
                       f"点击查看完整报告和核销码")
        else:
            content = (f"报告生成稍有延迟，核销码照发\n🎫 体验礼核销码：{code}")
        await wx_push.broadcast(title, content, log_id="", user_id=user_id,
                                push_type="", detail_url=report_url)
    except Exception as e:
        logger.warning("[ax] V4 wx push 失败 user={}: {}", user_id, e)


def _migrate_hermes_from_test(old_uid: str, new_uid: str) -> None:
    """老 @test 号绑真实账号后,把 hermes DB 中该 uid 的数据迁到真实账号。
    与 wx_oauth.bind_account 里的 SQL 逻辑一致(stocks 复合主键 + push_tasks)。
    失败仅打 warn,不阻塞主流程(用户宁可数据延迟出现也不阻塞绑定)。"""
    try:
        import psycopg2 as _pg
        HERMES_DB = "postgresql://hermes:Hermes2026DB!@localhost:5432/hermes"
        conn_h = _pg.connect(HERMES_DB)
        cur_h = conn_h.cursor()
        cur_h.execute(
            "INSERT INTO stocks (code, name, market, exchange, enabled, asset_type, user_id) "
            "SELECT code, name, market, exchange, enabled, asset_type, %s "
            "FROM stocks WHERE user_id = %s "
            "ON CONFLICT (code, user_id) DO NOTHING",
            (new_uid, old_uid))
        cur_h.execute("DELETE FROM stocks WHERE user_id = %s", (old_uid,))
        cur_h.execute("UPDATE push_tasks SET user_id = %s WHERE user_id = %s",
                      (new_uid, old_uid))
        conn_h.commit()
        conn_h.close()
        logger.info("[ax bind] hermes DB 迁移完成: {}→{}", old_uid, new_uid)
    except Exception as e:
        logger.warning("[ax bind] hermes DB 迁移失败(非致命): {}", e)


def _try_bind_to_existing(cur, conn, dup_row, password, openid, unionid,
                          nickname, headimg, old_uid):
    """遇到"邮箱已注册"时的绑定分支。
    - dup_row: (real_uid, pw_hash, role) — 已通过 email 查到的真实账号行
    - password: 用户输入的原密码;空则返回 409+need_password 让前端提示
    - old_uid: 当前微信绑着的 @test 号 uid(如有);无则 None
    返回:
      成功: (real_uid, real_email, real_role) — 后续流程用真实账号
      失败: JSONResponse(应直接 return 给客户端)
    """
    real_uid, pw_hash, real_role = dup_row
    if not password:
        conn.close()
        return JSONResponse(
            {"error": "该邮箱已注册过账号,输入原密码可将本次微信绑定到该账号",
             "need_password": True},
            status_code=409)
    try:
        ok = bcrypt.checkpw(password.encode(), pw_hash.encode())
    except Exception:
        ok = False
    if not ok:
        conn.close()
        return JSONResponse({"error": "密码错误,请检查后重试"}, status_code=403)
    # 清老 @test 号 wechat 字段(如有), 释放 unique constraint
    if old_uid and old_uid != real_uid:
        cur.execute(
            'UPDATE "apbase_User" SET "wechatOpenid"=NULL,"wechatUnionid"=NULL,'
            '"updatedAt"=NOW() WHERE id=%s',
            (old_uid,))
    # 覆盖式绑到真实账号(即使该账号已绑过其他微信也会覆盖,与 wx_oauth.bind_account 一致)
    cur.execute(
        '''UPDATE "apbase_User"
           SET "wechatOpenid"=%s,"wechatUnionid"=%s,"wechatNickname"=%s,
               "wechatHeadimgurl"=%s,"updatedAt"=NOW()
           WHERE id=%s''',
        (openid, unionid or None, nickname or None, headimg or None, real_uid))
    conn.commit()
    # 迁移 hermes DB 数据(自选股 + push_tasks)
    if old_uid and old_uid != real_uid:
        _migrate_hermes_from_test(old_uid, real_uid)
        # Redis 清理老 uid 残留(反查键由主流程重设指向 real_uid)
        try:
            _redis.delete(f"wx_openid:{old_uid}")
            _redis.srem("wx_bound_users", old_uid)
        except Exception:
            pass
    # 返回给主流程,替换 uid/email/role
    cur.execute('SELECT email FROM "apbase_User" WHERE id = %s', (real_uid,))
    real_email_row = cur.fetchone()
    real_email = real_email_row[0] if real_email_row else ""
    logger.info("[ax bind] 邮箱绑定成功 old_uid={} → real_uid={} email={}",
                old_uid or "(none)", real_uid, real_email)
    return real_uid, real_email, real_role or "USER"


class RegisterAndAnalyzeIn(BaseModel):
    state: str
    email: str
    stock_code: str
    stock_name: str = ""
    market: str = "A"       # 仅 A 股（AX 活动限定），保留字段以便未来扩展
    exchange: str = ""      # SH / SZ；空时由后端根据 code 推断
    asset_type: str = "stock"
    password: str = ""      # 邮箱已注册时用于验证并绑定;空即走注册分支


@router.post("/ax/register-and-analyze")
async def ax_register_and_analyze(body: RegisterAndAnalyzeIn):
    """V4：邮箱 + 股票一次提交，注册 + 启动分析，分析完成后邮件送达。"""
    logger.info(
        "[ax V4] register-and-analyze ENTER email={} state={}... stock={} name={}",
        body.email, (body.state or '')[:8], body.stock_code, body.stock_name,
    )
    email = body.email.lower().strip()
    if not _EMAIL_RE.match(email) or email.endswith("@test"):
        logger.info("[ax V4] 400 邮箱格式非法 email={}", body.email)
        return JSONResponse({"error": "请输入有效的邮箱地址"}, status_code=400)
    stock_code = body.stock_code.strip()
    stock_name = body.stock_name.strip()
    if not stock_code:
        logger.info("[ax V4] 400 未选股票 email={}", email)
        return JSONResponse({"error": "请先选择要分析的股票"}, status_code=400)

    raw = _redis.get(f"wx_state:{body.state}")
    if not raw:
        logger.warning("[ax V4] 401 state 过期或不存在 state_prefix={}...", (body.state or '')[:8])
        return JSONResponse({"error": "微信授权已过期，请回到服务号重新点「活动注册」"},
                            status_code=401)
    parts = raw.split("|", 3)
    openid = parts[0]
    unionid = parts[1] if len(parts) > 1 else ""
    nickname = parts[2] if len(parts) > 2 else ""
    headimg = parts[3] if len(parts) > 3 else ""

    # ── 复用 register 逻辑：建号 / 升级 @test / 已绑真实账号 ────────────────
    password = ax_service.gen_password()
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    account_password = password  # 邮件里带出来（老用户会置空）

    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur = conn.cursor()
        bound_uid = _redis.get(f"wx_uid_by_openid:{openid}")
        if bound_uid:
            cur.execute('SELECT id, email, role FROM "apbase_User" WHERE id = %s',
                        (bound_uid,))
            urow = cur.fetchone()
        else:
            urow = None

        if urow and not urow[1].endswith("@test"):
            uid, uemail, role = urow
            email = uemail
            account_password = ""  # 老账号不重发密码
        elif urow:
            # 微信当前绑着 @test 临时号,准备升级到真实邮箱
            uid, _old_email, role = urow
            cur.execute(
                'SELECT id, password, role FROM "apbase_User" WHERE email = %s',
                (email,))
            dup = cur.fetchone()
            if dup:
                # 邮箱已被别人注册 → 支持"输入密码绑到该账号"分支
                bind_res = _try_bind_to_existing(
                    cur, conn, dup, body.password,
                    openid, unionid, nickname, headimg, old_uid=uid)
                if isinstance(bind_res, JSONResponse):
                    return bind_res
                uid, email, role = bind_res
                account_password = ""
            else:
                cur.execute(
                    '''UPDATE "apbase_User"
                       SET email = %s, password = %s, name = %s, "updatedAt" = NOW()
                       WHERE id = %s''',
                    (email, pw_hash, nickname or email.split("@")[0], uid))
                conn.commit()
        else:
            cur.execute(
                'SELECT id, password, role FROM "apbase_User" WHERE email = %s',
                (email,))
            dup = cur.fetchone()
            if dup:
                # 邮箱已注册 + 本次微信首次访问 → 支持密码绑定分支
                bind_res = _try_bind_to_existing(
                    cur, conn, dup, body.password,
                    openid, unionid, nickname, headimg, old_uid=None)
                if isinstance(bind_res, JSONResponse):
                    return bind_res
                uid, email, role = bind_res
                account_password = ""
            else:
                uid = "c" + uuid.uuid4().hex[:24]
                role = "USER"
                cur.execute(
                    '''INSERT INTO "apbase_User"
                       (id, email, password, name, role, "createdAt", "updatedAt",
                        "wechatOpenid", "wechatUnionid", "wechatNickname", "wechatHeadimgurl")
                       VALUES (%s,%s,%s,%s,'USER',NOW(),NOW(),%s,%s,%s,%s)''',
                    (uid, email, pw_hash, nickname or email.split("@")[0],
                     openid, unionid or None, nickname or None, headimg or None))
                conn.commit()
        conn.close()
    except Exception as e:
        logger.error("[ax] V4 register-and-analyze DB 失败: {}", e)
        return JSONResponse({"error": "服务器繁忙，请稍后重试"}, status_code=500)

    # 绑定 openid ↔ uid
    _redis.set(f"wx_uid_by_openid:{openid}", uid, ex=365 * 24 * 3600)
    _redis.set(f"wx_openid:{uid}", openid, ex=365 * 24 * 3600)
    _redis.sadd("wx_bound_users", uid)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hc:
            await hc.post(
                f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
                headers=_FD_HEADERS,
                json={"user_id": uid, "email": email, "openid": openid,
                      "unionid": unionid, "nickname": nickname, "headimgurl": headimg})
    except Exception as fd_err:
        logger.warning("[ax] V4 finance-data 绑定失败(非致命): {}", fd_err)

    await asyncio.to_thread(ax_service.upsert_registration, openid, uid, email, nickname)
    _redis.delete(f"wx_state:{body.state}")
    token = _make_jwt(uid, email, role or "USER")

    # ── 默认把这只股票加到用户自选（非致命，失败不阻塞分析）─────────────────
    try:
        market = body.market if body.market in ("A", "HK", "US", "FUND") else "A"
        exchange = body.exchange if body.exchange in ("SH", "SZ", "HK", "US", "OF") else (
            "SH" if stock_code.startswith(("6", "9")) else "SZ"
        )
        asset_type = body.asset_type if body.asset_type in ("stock", "etf", "fund") else "stock"
        added = await asyncio.to_thread(
            add_stock_by_user, stock_code, stock_name or stock_code,
            market, exchange, asset_type, uid,
        )
        await asyncio.to_thread(
            fd_subscribe, stock_code, stock_name or stock_code,
            market, exchange, asset_type,
        )
        logger.info("[ax V4] 默认加入自选 uid={} code={} added={}", uid, stock_code, added)
    except Exception as wl_err:
        logger.warning("[ax V4] 默认加入自选失败(非致命) uid={} code={}: {}",
                       uid, stock_code, wl_err)

    # ── 启动 online-analysis（内部调用，避免 HTTP 回环）──────────────────────
    from app.routers.online_analysis import (
        start_analysis as _start_analysis, StartAnalysisIn as _StartAnalysisIn
    )

    class _FakeState:
        def __init__(self, uid_: str): self.user_id = uid_
    class _FakeReq:
        def __init__(self, uid_: str): self.state = _FakeState(uid_)

    try:
        payload = _StartAnalysisIn(
            stock_code=stock_code, stock_name=stock_name or stock_code,
            change_pct=0, trigger_desc="AdventureX 展位现场体验",
            thesis="", kill_conditions=[],
        )
        start_resp = await _start_analysis(_FakeReq(uid), payload)
        task_id = start_resp["task_id"]
    except Exception as e:
        logger.exception("[ax V4] 启动 online-analysis 失败 email={} stock={}: {}",
                         email, stock_code, e)
        return JSONResponse({"error": "分析任务启动失败，请稍后重试"}, status_code=500)

    # ── 启动 email watcher（后台等分析完成 → 发邮件+wx push）───────────────
    asyncio.create_task(_watch_analysis_and_email_report(
        uid, openid, task_id, stock_code, stock_name, email, account_password))

    branch = "老账号已绑" if not account_password else ("升级@test号" if urow else "新建")
    logger.info("[ax V4] SUCCESS email={} uid={} branch={} stock={} task={}",
                email, uid, branch, stock_code, task_id)
    return {"ok": True, "token": token, "email": email, "task_id": task_id,
            "stock_code": stock_code, "stock_name": stock_name}


# ── 第二级辅助：持仓截图识别（仅辅助填写，处理完即丢弃，不留存图片）──────────

class OcrIn(BaseModel):
    image_b64: str  # dataURL（data:image/...;base64,xxx）或纯 base64


@router.post("/ax/ocr-positions")
async def ax_ocr_positions(body: OcrIn, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    b64 = body.image_b64.strip()
    if len(b64) > 12_000_000:
        return JSONResponse({"error": "图片太大，请重新截图后上传"}, status_code=400)
    data_url = b64 if b64.startswith("data:") else f"data:image/jpeg;base64,{b64}"

    def _call_vision() -> str:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("ONE_API_KEY", ""),
            base_url=os.getenv("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1"),
            timeout=60,
        )
        model = os.getenv("ONE_API_MODEL", "gemini-3-flash-preview")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content":
                    "你是券商持仓截图识别助手。从证券App持仓截图中提取每只股票："
                    "名称(name)、6位代码(code,截图里没有则留空字符串)、"
                    "成本价/买入均价(cost_price,数字,没有则null)、持股数(shares,整数,没有则null)。"
                    "只输出 JSON 数组，不要输出任何其他文字。"
                    '示例: [{"name":"中际旭创","code":"300308","cost_price":145.0,"shares":100}]'},
                {"role": "user", "content": [
                    {"type": "text", "text": "识别这张持仓截图"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
            max_tokens=1000,
            temperature=0,
        )
        return resp.choices[0].message.content or ""

    try:
        text = await asyncio.to_thread(_call_vision)
    except Exception as e:
        logger.error("[ax] 截图识别 LLM 调用失败: {}", e)
        return JSONResponse({"error": "识别服务繁忙，请手动填写或稍后重试"}, status_code=502)

    import json as _json
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return JSONResponse({"error": "没识别出持仓，请换张更清晰的截图或手动填写"}, status_code=422)
    try:
        items = _json.loads(m.group(0))
    except Exception:
        return JSONResponse({"error": "没识别出持仓，请换张更清晰的截图或手动填写"}, status_code=422)

    from app.routers import online_analysis as _oa
    positions = []
    for it in items[:10]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        code = str(it.get("code") or "").strip()
        if not name and not code:
            continue
        if not re.match(r"^\d{6}$", code):
            code = ""
        if not code and name:
            # 按名称补齐代码（本地 A 股目录）
            try:
                cands = await _oa.stock_search(name, limit=1)
                if cands:
                    code = cands[0]["code"]
                    name = name or cands[0]["name"]
            except Exception:
                pass
        exchange = "SH" if code.startswith(("60", "68", "11", "51", "52")) else "SZ"
        try:
            cost_price = float(it.get("cost_price")) if it.get("cost_price") is not None else None
        except (TypeError, ValueError):
            cost_price = None
        try:
            shares = int(it.get("shares")) if it.get("shares") is not None else None
        except (TypeError, ValueError):
            shares = None
        positions.append({
            "name": name, "code": code, "exchange": exchange, "market": "A",
            "cost_price": cost_price, "shares": shares,
        })
    if not positions:
        return JSONResponse({"error": "没识别出持仓，请换张更清晰的截图或手动填写"}, status_code=422)
    logger.info("[ax] 截图识别成功 user={} 识别出 {} 只", user_id, len(positions))
    return {"ok": True, "positions": positions}


# ── 第一级：AI 分析完成 ───────────────────────────────────────────────────────

class Level1In(BaseModel):
    stock_code: str
    stock_name: str = ""
    report_id: int | None = None


@router.post("/ax/level1")
async def ax_level1(body: Level1In, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    openid = _get_openid(user_id)
    if not openid:
        raise HTTPException(400, "未找到微信绑定，请从服务号菜单重新进入")
    if not ax_service.get_ax_row(openid=openid):
        await asyncio.to_thread(
            ax_service.upsert_registration, openid, user_id,
            _decode_token(request).get("email", ""), "")

    row = await asyncio.to_thread(
        ax_service.complete_level1, openid,
        body.stock_code.strip(), body.stock_name.strip(), body.report_id)

    extra = f"你刚体检了「{row['level1_stock_name'] or row['level1_stock_code']}」"
    asyncio.create_task(_push_reward_message(user_id, 1, row["level1_code"], extra))
    return {"ok": True, "level1_code": row["level1_code"],
            "member_months": row["member_months"]}


# ── 第二级：导入持仓 ─────────────────────────────────────────────────────────

class PositionIn(BaseModel):
    code: str
    name: str
    cost_price: float
    shares: int | None = None
    market: str = "A"
    exchange: str = "SH"
    asset_type: str = "stock"


class Level2In(BaseModel):
    positions: list[PositionIn]


def _validate_position(p: PositionIn) -> str:
    """返回错误信息，空串表示通过。A 股校验 K 线存在 + 买入价合理区间。"""
    code = p.code.strip()
    if not re.match(r"^[0-9A-Za-z.]{4,10}$", code):
        return f"股票代码格式不对：{code}"
    if p.cost_price <= 0:
        return f"{p.name} 的买入价必须大于 0"
    if p.market != "A":
        return ""  # 港美股：代码来自搜索候选，不做价格校验
    bars = get_kline_with_fallback(code, "daily", 500)
    if not bars:
        return f"未找到股票 {code}（{p.name}）的行情数据，请从搜索候选中选择"
    lows = [b.get("low") or b.get("close") for b in bars if b.get("low") or b.get("close")]
    highs = [b.get("high") or b.get("close") for b in bars if b.get("high") or b.get("close")]
    if lows and highs:
        lo, hi = min(lows) * 0.8, max(highs) * 1.2
        if not (lo <= p.cost_price <= hi):
            return (f"「{p.name}」买入价 {p.cost_price} 似乎不对"
                    f"（近两年价格区间 {min(lows):.2f}–{max(highs):.2f}），请确认")
    return ""


@router.post("/ax/level2")
async def ax_level2(body: Level2In, request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    if not body.positions:
        raise HTTPException(400, "至少导入一只持仓")
    if len(body.positions) > 10:
        raise HTTPException(400, "一次最多导入 10 只")
    openid = _get_openid(user_id)
    if not openid:
        raise HTTPException(400, "未找到微信绑定，请从服务号菜单重新进入")

    # ① 校验（真实代码 + 买入价合理性）
    for p in body.positions:
        err = await asyncio.to_thread(_validate_position, p)
        if err:
            return JSONResponse({"error": err}, status_code=400)

    # ② 加自选股 + 写成本价（持仓监控立即开始）
    from datetime import date
    today = date.today().isoformat()
    for p in body.positions:
        code = p.code.strip()
        exchange = p.exchange if p.exchange in ("SH", "SZ", "HK", "US", "OF") else "SH"
        market = p.market if p.market in ("A", "HK", "US", "FUND") else "A"
        await asyncio.to_thread(add_stock_by_user, code, p.name, market,
                                exchange, p.asset_type or "stock", user_id)
        await asyncio.to_thread(fd_subscribe, code, p.name, market,
                                exchange, p.asset_type or "stock")
        await asyncio.to_thread(
            upsert_thesis_by_user, code, user_id, "AdventureX 现场导入",
            p.shares, p.cost_price, today)

    # ③ 状态机推进 + 核销码 + 会员
    positions_json = [
        {"code": p.code.strip(), "name": p.name,
         "cost_price": p.cost_price, "shares": p.shares}
        for p in body.positions
    ]
    row = await asyncio.to_thread(ax_service.complete_level2, openid, positions_json)

    names = "、".join(p.name for p in body.positions[:3])
    extra = f"你导入的 {len(body.positions)} 只持仓（{names}），猎鹿人已开始 7×24 盯盘"
    asyncio.create_task(_push_reward_message(user_id, 2, row["level2_code"], extra))
    return {"ok": True, "level2_code": row["level2_code"],
            "member_months": row["member_months"]}


# ── 我的活动状态（奖励领取页） ────────────────────────────────────────────────

@router.get("/ax/me")
async def ax_me(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    payload = _decode_token(request)
    openid = _get_openid(user_id)
    row = ax_service.get_ax_row(openid=openid) if openid else None
    if not row and openid:
        await asyncio.to_thread(
            ax_service.upsert_registration, openid, user_id,
            payload.get("email", ""), "")
        row = ax_service.get_ax_row(openid=openid)
    # V4：附带功能体验进度（只统计"分配集合"内已完成的）
    features_used_count = (
        len(set(row.get("features_used") or []) & get_assigned_ids(openid or ""))
        if row else 0
    )
    ax_active = bool(row)  # 有 ax_event 行即视为参与者
    return {
        "ok": True, "email": payload.get("email", ""), "activity": row,
        "ax_active": ax_active,
        "features_used_count": features_used_count,
        "features_total": AX_UNLOCK_THRESHOLD,
    }


# ── V4：功能体验追踪（features）────────────────────────────────────────────────

@router.get("/ax/features")
async def ax_features(request: Request):
    """返回功能清单 + 已体验标记 + 进度。未参与活动的用户返回空进度。"""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    openid = _get_openid(user_id)
    if not openid:
        return {"ok": True, "ax_active": False,
                "features": [{**f, "used": False} for f in get_assigned_features("")],
                "used_count": 0, "total_count": AX_UNLOCK_THRESHOLD,
                "unlocked": False, "level2_code": ""}
    summary = await asyncio.to_thread(ax_service.get_features_summary, openid)
    return {"ok": True, "ax_active": True, **summary}


@router.get("/ax/features/all")
async def ax_features_all(request: Request):
    """功能地图数据源:返回 Hunter 全部 10 项功能 + 每项的 used/assigned 状态。

    与 /ax/features 的区别:
      - /ax/features 只返回该用户被分配的 4 项通关任务
      - /ax/features/all 返回全量 AX_ALL_FEATURES(产品功能全景介绍),
        每项额外带 assigned 字段标识"是否属于本次通关任务",used 字段标识已体验
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    openid = _get_openid(user_id)
    used_set: set[str] = set()
    assigned_set: set[str] = set()
    if openid:
        row = await asyncio.to_thread(ax_service.get_ax_row, openid=openid)
        if row:
            used_set = set(row.get("features_used") or [])
        assigned_set = get_assigned_ids(openid)
    items = [
        {**f, "used": f["id"] in used_set, "assigned": f["id"] in assigned_set}
        for f in AX_ALL_FEATURES
    ]
    return {
        "ok": True,
        "ax_active": bool(openid and assigned_set),
        "features": items,
        "assigned_used_count": len(used_set & assigned_set),
        "assigned_total": AX_UNLOCK_THRESHOLD,
        "unlocked": len(used_set & assigned_set) >= AX_UNLOCK_THRESHOLD,
    }


class TrackIn(BaseModel):
    feature_id: str


@router.post("/ax/features/track")
async def ax_features_track(body: TrackIn, request: Request):
    """埋点：标记某功能已体验。达阈时自动发核销码 + 通知。
    非活动参与者（无 ax_event 行）返回 200 但不做事，避免污染普通用户请求。
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(401, "未登录")
    fid = (body.feature_id or "").strip()
    if fid not in AX_FEATURE_IDS:
        return {"ok": True, "skipped": True, "reason": "unknown_feature"}
    openid = _get_openid(user_id)
    if not openid:
        return {"ok": True, "skipped": True, "reason": "not_ax_user"}

    used_count, unlocked_now = await asyncio.to_thread(
        ax_service.add_feature_used, openid, fid)

    # 达阈：写核销码 + 会员，异步发通知
    level2_code = ""
    if unlocked_now:
        try:
            row = await asyncio.to_thread(ax_service.complete_level2_by_features, openid)
            level2_code = row.get("level2_code", "")
            asyncio.create_task(_notify_level2_unlocked(user_id, openid, row))
            logger.info("[ax] V4 features 达阈解锁 user={} code={}", user_id, level2_code)
        except Exception as e:
            logger.error("[ax] V4 complete_level2_by_features 失败 user={}: {}", user_id, e)

    return {
        "ok": True, "used_count": used_count, "total_count": AX_UNLOCK_THRESHOLD,
        "unlocked": bool(level2_code) or used_count >= AX_UNLOCK_THRESHOLD,
        "level2_code": level2_code,
    }


async def _notify_level2_unlocked(user_id: str, openid: str, row: dict) -> None:
    """股民礼解锁：发邮件 + 微信推送。"""
    email = row.get("email", "")
    code = row.get("level2_code", "")
    months = int(row.get("member_months") or 3)
    # 邮件
    if email:
        try:
            await asyncio.to_thread(
                ax_service.send_reward_email, email, code, months, _AX_PAGE_URL)
        except Exception as e:
            logger.warning("[ax] V4 股民礼邮件失败 user={}: {}", user_id, e)
    # 微信推送
    try:
        title = "🎉 股民礼已解锁！"
        content = (f"恭喜完成全部 {AX_UNLOCK_THRESHOLD} 项功能体验\n"
                   f"高级伴手礼 + {months} 个月会员\n"
                   f"核销码：{code}（凭码到展位领取）")
        await wx_push.broadcast(title, content, log_id="", user_id=user_id,
                                push_type="", detail_url=_AX_PAGE_URL)
    except Exception as e:
        logger.warning("[ax] V4 股民礼 wx push 失败 user={}: {}", user_id, e)


# ── 展位后台（工作人员） ──────────────────────────────────────────────────────

@router.get("/ax/admin/list")
async def ax_admin_list(request: Request, q: str = ""):
    _require_admin(request)
    return {"ok": True, "items": await asyncio.to_thread(ax_service.list_participants, q)}


class RedeemIn(BaseModel):
    code: str


@router.post("/ax/admin/redeem")
async def ax_admin_redeem(body: RedeemIn, request: Request):
    operator = _require_admin(request)
    result = await asyncio.to_thread(ax_service.redeem_code, body.code, operator)
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@router.get("/ax/admin/stats")
async def ax_admin_stats(request: Request):
    _require_admin(request)
    return {"ok": True, "stats": await asyncio.to_thread(ax_service.get_stats)}
