"""用户自定义数据源 · CRUD + 测试 —— `_21` §7 步 2。

API 前缀 `/api/user_sources/*` · 走 JWT 中间件。

**这是「用户脱离我们也能玩转」这条要求的落点。**在这之前用户想换数据源
只能改 `DATA_SOURCE_PROVIDER` 这个 env,而那是全局单选 ——
换了 A股会一并换掉港美股(注册表里 `a.akshare` 的注释早就承认了这点)。

结构照 `user_mcp.py`,因为两者解决的是同一类问题:用户凭证要安全存、
要能回显、失败时要降级而不是每次卡满超时。能抄的都抄了,
不同的地方都在注释里说明了为什么不同。
"""
from __future__ import annotations

import json
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from app.services import source_templates
from app.services.database import get_conn
from app.services.mcp_crypto import encrypt, decrypt, key_hint
from app.services.source_catalog import DataKind, Market

router = APIRouter(prefix="/user_sources", tags=["user-sources"])

# 上限比 MCP 的 10 高一些:数据源是按 (市场 × 类型) 切的,
# 一个认真配置的用户光 A股就可能配行情/K线/新闻/财务四条
MAX_SOURCES_PER_USER = 30

_VALID_MARKETS = {m.value for m in Market}
_VALID_KINDS = {k.value for k in DataKind}
_VALID_KEY_IN = {"header", "query", "body"}


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


def _validate_endpoint(endpoint: str) -> str:
    """校验并**返回清理后的**地址。

    `strip()` 不是可有可无的:Claude Code 专门为粘贴带来的前后空白做了警告,
    因为它的表现是"地址明明对却一直连不上",极难排查。我们这里直接清掉,
    但**空白出现在中间**(比如 `https://a.com/ path`)不清 —— 那是真的填错了,
    悄悄改掉反而会让用户以为自己填对了。
    """
    ep = (endpoint or "").strip()
    if not ep:
        raise HTTPException(400, "接口地址不能为空")
    if not (ep.startswith("https://") or ep.startswith("http://")):
        raise HTTPException(400, "接口地址必须以 https:// 或 http:// 开头")
    if " " in ep:
        raise HTTPException(400, "接口地址中间不能有空格 —— 请检查是否粘贴串行了")
    # SSRF 防护 · 与 user_mcp 同一套规则。
    # 注意这条对数据源比对 MCP 更要紧:MCP 至少要模型主动调用,
    # 数据源是**取数链路自动走的**,一旦指向内网就是无人值守的探测
    lower = ep.lower()
    for bad in ("localhost", "127.", "10.", "172.", "192.168.", "169.254.", "0.0.0.0"):
        if bad in lower:
            raise HTTPException(400, f"接口地址不允许内网地址({bad})")
    return ep


def _validate_slot(upstream: str, market: str, kind: str) -> None:
    if not source_templates.is_known(upstream):
        raise HTTPException(400, f"未知来源 {upstream!r} · 请从下拉里选,或选「自定义接口」")
    if market not in _VALID_MARKETS:
        raise HTTPException(400, f"未知市场 {market!r} · 可选 {'/'.join(sorted(_VALID_MARKETS))}")
    if kind not in _VALID_KINDS:
        raise HTTPException(400, f"未知数据类型 {kind!r}")
    tpl = source_templates.get(upstream)
    if tpl and tpl.kinds and kind not in tpl.kinds:
        raise HTTPException(
            400,
            f"{upstream} 不提供「{kind}」这类数据 —— 这个组合是填错了,"
            f"它支持的是 {', '.join(tpl.kinds)}"
        )


_COLS = ("id, user_id, name, upstream, market, kind, endpoint, requires_key, "
         "key_in, key_name, key_prefix, api_key_enc, api_key_hint, headers, "
         "field_map, enabled, timeout_ms, fail_streak, cooldown_until, "
         "last_ok_at, last_err, call_count, error_count, created_at, updated_at")


def _row(r: tuple, with_key: bool = False) -> dict:
    d = {
        "id": r[0], "name": r[2], "upstream": r[3], "market": r[4], "kind": r[5],
        "endpoint": r[6], "requires_key": r[7],
        "key_in": r[8], "key_name": r[9], "key_prefix": r[10],
        # **永远不返回密文本身**,只返回"有没有"和末 4 位。
        # 加密存了却在 API 里原样吐出来,加密就白做了
        "has_api_key": bool(r[11]), "api_key_hint": r[12] or "",
        "headers": r[13] or {}, "field_map": r[14] or {},
        "enabled": r[15], "timeout_ms": r[16],
        "fail_streak": r[17],
        "cooldown_until": r[18].isoformat() if r[18] else None,
        "last_ok_at": r[19].isoformat() if r[19] else None,
        "last_err": r[20] or "",
        "call_count": r[21], "error_count": r[22],
        "created_at": r[23].isoformat() if r[23] else None,
        "updated_at": r[24].isoformat() if r[24] else None,
        "owner": "user",
    }
    if with_key:
        d["_enc"] = r[11]
    return d


# ═════════════════════════════════════════════════════════════════
# GET /api/user_sources/templates · 来源下拉的选项
# ═════════════════════════════════════════════════════════════════

@router.get("/templates")
async def list_templates():
    """来源模板 —— 表单的下拉数据。

    单独一个端点而不是塞进 `/user_sources`,因为**没登录也该能看** ——
    用户在决定要不要用这个开源版时,"它支持接哪些数据源"是个先决问题。
    """
    return {"templates": source_templates.all_templates()}


# ═════════════════════════════════════════════════════════════════
# GET /api/user_sources · 当前用户的全部自定义源
# ═════════════════════════════════════════════════════════════════

@router.get("")
async def list_sources(request: Request):
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT {_COLS} FROM user_data_sources "
                    "WHERE user_id=%s ORDER BY created_at DESC", (uid,))
        rows = cur.fetchall()
    finally:
        conn.close()
    items = [_row(r) for r in rows]
    return {
        "sources": items,
        "max": MAX_SOURCES_PER_USER,
        "enabled_count": sum(1 for i in items if i["enabled"]),
        # 「一键用官方」按钮要据此决定是"去填 key"还是"停用你的源"。
        # 前端自己判断不了 —— 平台 key 可能来自 env 也可能是网页里填的
        "platform_key": _has_platform_key(),
    }


def _has_platform_key() -> bool:
    try:
        from app.services import source_catalog
        return source_catalog._has_platform_key()
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════
# POST /api/user_sources/bulk-enable · 一键用官方 / 一键切回自己的
# ═════════════════════════════════════════════════════════════════

class BulkIn(BaseModel):
    enabled: bool


@router.post("/bulk-enable")
async def bulk_enable(body: BulkIn, request: Request):
    """批量停用/启用当前用户的全部自定义源(`_21` §5 步 3)。

    **停用,不是删除。**用户点「一键用官方」多半是在排查问题
    (我自己配的是不是坏了?),排查完要能一键切回去。删掉就回不来了 ——
    他得照着记忆把地址和 key 重新填一遍,而 key 我们只回显末 4 位,
    等于让他去翻原始凭证。

    顺带清熔断:重新启用时如果还带着冷却状态,用户会看到
    "我明明启用了怎么还在走官方源"。
    """
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE user_data_sources SET enabled=%s, updated_at=NOW()"
            + (", fail_streak=0, cooldown_until=NULL" if body.enabled else "")
            + " WHERE user_id=%s AND enabled<>%s",
            (body.enabled, uid, body.enabled),
        )
        n = cur.rowcount
        conn.commit()
        cur.execute("SELECT count(*) FROM user_data_sources WHERE user_id=%s", (uid,))
        total = cur.fetchone()[0]
    finally:
        conn.close()
    return {"changed": n, "total": total, "enabled": body.enabled}


# ═════════════════════════════════════════════════════════════════
# POST /api/user_sources · 新建
# ═════════════════════════════════════════════════════════════════

class SourceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    upstream: str
    market: str
    kind: str
    endpoint: str
    requires_key: bool = True
    key_in: str = "header"
    key_name: str = "Authorization"
    key_prefix: str = ""
    api_key: Optional[str] = None
    headers: dict = Field(default_factory=dict)
    field_map: dict = Field(default_factory=dict)
    timeout_ms: int = 15000


@router.post("")
async def create_source(body: SourceIn, request: Request):
    uid = _uid(request)
    ep = _validate_endpoint(body.endpoint)
    _validate_slot(body.upstream, body.market, body.kind)
    if body.key_in not in _VALID_KEY_IN:
        raise HTTPException(400, f"key 位置必须是 {'/'.join(sorted(_VALID_KEY_IN))}")

    # 勾了"需要 key"却没填 —— 直接拦。用户原话是"调用的时候 AI 会判断没 key 告诉他",
    # 但 AI 判断不了:它看到的只是一个失败的工具返回。真正知道缺 key 的是上游的 401。
    # 既然这里就能知道,没有理由拖到调用时才暴露
    if body.requires_key and not (body.api_key or "").strip():
        raise HTTPException(400, "勾了「需要 key」就必须填 key —— 不需要的话把勾去掉")

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT count(*) FROM user_data_sources WHERE user_id=%s", (uid,))
        if cur.fetchone()[0] >= MAX_SOURCES_PER_USER:
            raise HTTPException(400, f"最多 {MAX_SOURCES_PER_USER} 个自定义数据源")
        raw = (body.api_key or "").strip()
        try:
            cur.execute(f"""
                INSERT INTO user_data_sources
                  (user_id, name, upstream, market, kind, endpoint, requires_key,
                   key_in, key_name, key_prefix, api_key_enc, api_key_hint,
                   headers, field_map, timeout_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                RETURNING {_COLS}
            """, (uid, body.name.strip(), body.upstream, body.market, body.kind, ep,
                  body.requires_key, body.key_in, body.key_name.strip(),
                  body.key_prefix, encrypt(raw) if raw else None,
                  key_hint(raw) if raw else "",
                  _json(body.headers), _json(body.field_map), body.timeout_ms))
            row = cur.fetchone()
        except Exception as e:
            # 唯一索引撞了 —— 说明这个 (市场,类型,来源) 槽位已经有一条了。
            # 直接说"已存在"没用,用户想知道的是"那我该怎么办"
            if "idx_uds_user_slot" in str(e):
                raise HTTPException(
                    400,
                    f"你已经配过一个「{body.market}·{body.kind}·{body.upstream}」的源了。"
                    f"改那一条即可 —— 同一个槽位只允许一条,"
                    f"否则「优先用户的」会变成「优先用户的哪一条」"
                )
            raise
        conn.commit()
    finally:
        conn.close()
    return _row(row)


def _json(v: dict) -> str:
    return json.dumps(v or {}, ensure_ascii=False)


# ═════════════════════════════════════════════════════════════════
# PATCH / DELETE
# ═════════════════════════════════════════════════════════════════

class SourcePatch(BaseModel):
    name: Optional[str] = None
    endpoint: Optional[str] = None
    requires_key: Optional[bool] = None
    key_in: Optional[str] = None
    key_name: Optional[str] = None
    key_prefix: Optional[str] = None
    api_key: Optional[str] = None
    headers: Optional[dict] = None
    field_map: Optional[dict] = None
    enabled: Optional[bool] = None
    timeout_ms: Optional[int] = None


@router.patch("/{sid}")
async def patch_source(sid: int, body: SourcePatch, request: Request):
    uid = _uid(request)
    sets, args = [], []

    if body.name is not None:
        sets.append("name=%s"); args.append(body.name.strip())
    if body.endpoint is not None:
        sets.append("endpoint=%s"); args.append(_validate_endpoint(body.endpoint))
    if body.requires_key is not None:
        sets.append("requires_key=%s"); args.append(body.requires_key)
    if body.key_in is not None:
        if body.key_in not in _VALID_KEY_IN:
            raise HTTPException(400, f"key 位置必须是 {'/'.join(sorted(_VALID_KEY_IN))}")
        sets.append("key_in=%s"); args.append(body.key_in)
    if body.key_name is not None:
        sets.append("key_name=%s"); args.append(body.key_name.strip())
    if body.key_prefix is not None:
        sets.append("key_prefix=%s"); args.append(body.key_prefix)
    if body.api_key is not None:
        raw = body.api_key.strip()
        sets.append("api_key_enc=%s"); args.append(encrypt(raw) if raw else None)
        sets.append("api_key_hint=%s"); args.append(key_hint(raw) if raw else "")
    if body.headers is not None:
        sets.append("headers=%s::jsonb"); args.append(_json(body.headers))
    if body.field_map is not None:
        sets.append("field_map=%s::jsonb"); args.append(_json(body.field_map))
    if body.enabled is not None:
        sets.append("enabled=%s"); args.append(body.enabled)
    if body.timeout_ms is not None:
        sets.append("timeout_ms=%s"); args.append(body.timeout_ms)

    if not sets:
        raise HTTPException(400, "没有要改的字段")

    # 改了配置就清熔断状态 —— 用户改地址/key 多半就是因为它一直失败,
    # 不清的话改完还得等冷却结束才生效,表现是"我改了怎么没用"
    sets += ["fail_streak=0", "cooldown_until=NULL", "updated_at=NOW()"]
    args += [sid, uid]

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"UPDATE user_data_sources SET {', '.join(sets)} "
                    f"WHERE id=%s AND user_id=%s RETURNING {_COLS}", tuple(args))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "数据源不存在")
        conn.commit()
    finally:
        conn.close()
    return _row(row)


@router.delete("/{sid}")
async def delete_source(sid: int, request: Request):
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM user_data_sources WHERE id=%s AND user_id=%s", (sid, uid))
        n = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not n:
        raise HTTPException(404, "数据源不存在")
    return {"deleted": True}


# ═════════════════════════════════════════════════════════════════
# GET /api/user_sources/health · 「你的源用上了吗」
# ═════════════════════════════════════════════════════════════════

@router.get("/health")
async def sources_health(request: Request):
    """用户源的真实使用情况 —— 降级标注(`_21` §6.3)的数据面。

    **这个端点回答的是老板那句话真正的验收问题:「我脱离了吗?」**

    只给"配了几个源"是不够的 —— 配了不等于用上了。所以这里给的是
    调用计数、最近一次成功时间、当前是否在熔断冷却里,
    以及冷却的原因。这三样合起来才能回答那个问题。
    """
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, upstream, market, kind, enabled, call_count, error_count, "
            "       last_ok_at, last_err, fail_streak, cooldown_until, "
            "       (cooldown_until IS NOT NULL AND cooldown_until > NOW()) AS cooling "
            "FROM user_data_sources WHERE user_id=%s ORDER BY updated_at DESC", (uid,))
        rows = cur.fetchall()
    finally:
        conn.close()

    items = [{
        "id": r[0], "name": r[1], "upstream": r[2], "market": r[3], "kind": r[4],
        "enabled": r[5], "call_count": r[6], "error_count": r[7],
        "last_ok_at": r[8].isoformat() if r[8] else None,
        "last_err": r[9] or "", "fail_streak": r[10],
        "cooldown_until": r[11].isoformat() if r[11] else None,
        "cooling": bool(r[12]),
        # 三种"没在用"分开说 —— 用户的下一步动作完全不同:
        #   停用了   → 去启用
        #   熔断中   → 去看 last_err 修配置
        #   没调用过 → 可能是这个 (市场,类型) 根本没被用到,不是坏了
        "state": ("disabled" if not r[5]
                  else "cooling" if r[12]
                  else "never_called" if not r[6]
                  else "active"),
    } for r in rows]
    return {
        "sources": items,
        "active": sum(1 for i in items if i["state"] == "active"),
        "cooling": sum(1 for i in items if i["state"] == "cooling"),
        # 本次请求的出处记录 —— 只在同一个请求里有意义,这里给的是
        # 空的(GET /health 自己没取数)。真正的徽章数据走
        # 各业务接口返回体里的 _provenance,见 request_ctx
        "note": "各业务接口的返回体里带 _provenance,那才是本次回答用了谁",
    }


# ═════════════════════════════════════════════════════════════════
# POST /api/user_sources/test · 保存前先打一次真实请求
# ═════════════════════════════════════════════════════════════════

class TestIn(BaseModel):
    endpoint: str
    requires_key: bool = False
    key_in: str = "header"
    key_name: str = "Authorization"
    key_prefix: str = ""
    api_key: Optional[str] = None
    headers: dict = Field(default_factory=dict)
    symbol: str = "600519"
    timeout_ms: int = 15000


@router.post("/test")
async def test_source(body: TestIn, request: Request):
    """真打一次目标接口,把**原始返回**摆给用户看。

    为什么返回原始响应而不是只给一个 ok/fail:
    `_20` §6 那条原则 —— 装之前必须让用户看见。他看到返回体才知道
    自己填的地址对不对、key 生没生效、以及(步 6 之后)字段该怎么映射。

    为什么这个接口**不写库**:测试是探索性的,用户会连着试好几次。
    每次都落一条记录会让列表里堆满半成品。
    """
    _uid(request)
    # 占位符展开走 source_resolver.expand —— **必须和取数时用同一份**,
    # 否则"测试通过但取数失败"(或反过来),而这正是测试按钮要杜绝的事
    from app.services.source_resolver import expand
    ep = expand(_validate_endpoint(body.endpoint), body.symbol)

    headers = {k: str(v) for k, v in (body.headers or {}).items()}
    params: dict = {}
    json_body: dict | None = None
    raw = (body.api_key or "").strip()

    if body.requires_key and raw:
        val = f"{body.key_prefix}{raw}" if body.key_prefix else raw
        if body.key_in == "header":
            headers[body.key_name] = val
        elif body.key_in == "query":
            params[body.key_name] = val
        else:
            json_body = {body.key_name: val}

    t0 = time.time()
    try:
        timeout = min(max(body.timeout_ms, 1000), 30000) / 1000
        # params 为空必须传 None —— httpx 会用它整体替换 URL 上的 query,
        # 空 dict 把 `?secid=…&fields=…` 冲掉,上游照样 200 但返回空数据。
        # 同 source_resolver._fetch_one,两处必须一致,否则测试与取数行为不同
        kw: dict = {"headers": headers}
        if params:
            kw["params"] = params
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = (await c.post(ep, json=json_body, **kw)
                 if json_body is not None else await c.get(ep, **kw))
        ms = int((time.time() - t0) * 1000)
        text = r.text or ""
        # 截断但**说明截断了** —— 悄悄截断会让用户以为返回就这么点,
        # 然后照着不完整的结构去填字段映射
        truncated = len(text) > 4000
        return {
            "ok": r.is_success,
            "status": r.status_code,
            "duration_ms": ms,
            "content_type": r.headers.get("content-type", ""),
            "body": text[:4000],
            "truncated": truncated,
            "body_len": len(text),
            # 401/403 单独点出来:这是"缺 key / key 不对"最常见的信号,
            # 而用户此刻正好在决定要不要勾那个"需要 key"
            "hint": _hint(r.status_code, body.requires_key),
        }
    except httpx.TimeoutException:
        return {"ok": False, "status": 0,
                "duration_ms": int((time.time() - t0) * 1000),
                "body": "", "hint": f"超时({body.timeout_ms}ms)· 地址对吗?需要代理吗?"}
    except Exception as e:
        logger.warning("[user_sources] test failed: {}", e)
        return {"ok": False, "status": 0,
                "duration_ms": int((time.time() - t0) * 1000),
                "body": "", "hint": f"连不上:{str(e)[:200]}"}


@router.post("/{sid}/test")
async def test_saved_source(sid: int, request: Request, symbol: str = "600519"):
    """测一条**已保存**的源(老板要的「接入后支持测试,点一下看看能不能连通」)。

    与 `POST /test` 的区别不只是"用存好的参数":这条会

      1. 用**存着的加密 key**(用户不用再粘一遍 —— 我们只回显末 4 位,
         让他重新粘等于让他去翻原始凭证)
      2. 跑**完整链路**,包括字段映射 —— 这才是"能不能真用上"的判据。
         连得通但映射不出价格,取数时照样降级,而用户会以为它是好的
      3. **写回熔断状态**:测通了就清 fail_streak 与冷却。用户改完配置
         点测试,期待的就是"好了,现在能用了" —— 不清的话他得等冷却结束
    """
    uid = _uid(request)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, upstream, endpoint, requires_key, key_in, key_name, "
            "       key_prefix, api_key_enc, headers, field_map, timeout_ms, kind "
            "FROM user_data_sources WHERE id=%s AND user_id=%s", (sid, uid))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "数据源不存在")

    from app.services import source_mapping, source_resolver
    src = source_resolver.UserSource(*row[:12])
    kind = row[12]

    t0 = time.time()
    try:
        raw = await _to_thread(source_resolver._fetch_one, src, symbol)
    except Exception as e:                                    # noqa: BLE001
        reason = f"{type(e).__name__}: {str(e)[:200]}"
        await _to_thread(source_resolver._mark, sid, False, reason)
        return {"ok": False, "stage": "connect", "duration_ms": _ms(t0),
                "reason": reason,
                "hint": "连不上 —— 检查地址是否可从服务器访问(不是从你的浏览器)"}

    # 连通了,再看映射 —— 这两步分开报,因为用户的下一步动作完全不同:
    # 连不上 → 改地址/网络;映射失败 → 改映射或换来源
    try:
        mapped = source_mapping.apply(src.upstream, kind, raw, src.field_map or None)
    except source_mapping.MappingError as e:
        await _to_thread(source_resolver._mark, sid, False, str(e))
        return {"ok": False, "stage": "mapping", "duration_ms": _ms(t0),
                "reason": str(e),
                "sample": json.dumps(raw, ensure_ascii=False)[:1500],
                "hint": "连得通,但我们读不懂它的返回 —— 取数时会降级到官方源"}

    await _to_thread(source_resolver._mark, sid, True)
    return {"ok": True, "stage": "done", "duration_ms": _ms(t0),
            "mapped": mapped if kind != "kline" else {
                "rows": len((mapped or {}).get("rows") or []),
                "first": ((mapped or {}).get("rows") or [{}])[0],
            },
            "hint": "通了 · 熔断状态已清除,取数会优先走它"}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


async def _to_thread(fn, *a):
    """`_fetch_one` / `_mark` 是同步的(httpx.Client + psycopg)——
    直接在 async handler 里调会阻塞事件循环,一条 15s 超时的源
    能把整个 api 卡住。"""
    import asyncio
    return await asyncio.to_thread(fn, *a)


def _hint(status: int, requires_key: bool) -> str:
    if status in (401, 403):
        return ("上游拒绝了(%d)—— %s" % (
            status,
            "key 不对或没权限" if requires_key
            else "这个接口其实**需要 key**,把「需要 key」勾上再填"))
    if status == 404:
        return "404 · 路径不对,或者这个代码在上游没有数据"
    if status == 429:
        return "429 · 被限流了,这个源的配额可能已经用完"
    if 200 <= status < 300:
        return ""
    return f"上游返回 {status}"
