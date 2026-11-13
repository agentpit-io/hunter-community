"""券商只读授权管理 API、主备数据源切换与 Excel/CSV 交割单手工导入路由（`apps/api/app/routers/broker_connector.py`）。

提供以下 REST 接口供前端「设置 -> 合作券商数据源与只读授权管理」面板、「组合复盘工作台」及 `hookd` 安全钩子调用：
- `GET /api/broker/status`：查询当前用户券商只读授权状态（AES-256-GCM 脱敏展示）、主备数据源路由配置与手工交割单导入概况
- `POST /api/broker/oauth/authorize`：开启用户单独 OAuth 券商账户只读授权（Token 使用 AES-256-GCM 加密托管于服务端，绝不进入大模型提示词或日志）
- `POST /api/broker/oauth/revoke`：“一键撤销授权”接口（立即销毁 AES-256-GCM 密文凭证、清空账户持仓缓存，并写入 `broker_audit_log`）
- `POST /api/broker/routing/config`：切换合作券商主数据源与公开备用源降级策略（严禁跨源取均值）
- `POST /api/broker/manual-import`：未授权用户的“Excel/CSV 交割单与持仓导入”解析接口（使不联网授权的用户也能无缝使用组合风险巡检与交易复盘）
- `GET /api/broker/manual-import/latest`：获取当前用户最新导入的手工交割单与持仓风险概览
- `GET /api/broker/account/accounts`：只读获取已授权账户列表（账号脱敏，未授权/已撤销/跨租户 100% 返 403）
- `GET /api/broker/account/summary`：只读获取已授权账户资金概览（未授权/已撤销/跨租户 100% 返 403）
- `GET /api/broker/account/positions`：只读获取已授权账户持仓快照（未授权/已撤销/跨租户 100% 返 403）
- `GET /api/broker/account/trades`：只读获取已授权账户历史成交记录（未授权/已撤销/跨租户 100% 返 403）
- `GET /api/broker/audit-logs`：查询 `broker_audit_log` 合规审计留痕记录
- `GET /api/internal/broker/oauth-verify`：供 `hookd` 在 `PreToolUse` 阶段极速校验用户 OAuth 授权状态与 Scope
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.providers.broker import (
    BrokerAccountUnauthorizedError,
    CrossSourceAveragingForbiddenError,
    VALID_READONLY_SCOPES,
    assert_no_cross_source_averaging,
    get_broker_authorization_status,
    get_default_account_readonly_adapter,
    get_default_broker_router,
    get_latest_manual_import,
    grant_broker_readonly_authorization,
    list_broker_audit_logs,
    parse_and_save_manual_settlement_csv,
    record_broker_audit_log,
    revoke_broker_readonly_authorization,
    verify_user_oauth_scope,
)

router = APIRouter(prefix="/broker", tags=["broker_connector"])
internal_router = APIRouter(prefix="/internal/broker", tags=["internal_broker_connector"])

# 运行时主备数据源路由配置状态
_ROUTING_CONFIG_STATE: dict[str, Any] = {
    "primary_provider_id": "galaxy_broker",
    "primary_provider_name": "中国银河证券官方数据源（主源优先）",
    "fallback_provider_id": "akshare_public_fallback",
    "fallback_provider_name": "公开免费权威数据源（AKShare/交易所公开源）",
    "allow_fallback": True,
    "merge_strategy": "primary_then_fallback",
    "simulate_primary_timeout": False,
}


def _resolve_session_user_id(request: Request, fallback_user_id: str | None = None) -> str:
    """从请求上下文解析真实登录用户 ID。"""
    state_uid = getattr(request.state, "user_id", None)
    if state_uid:
        return str(state_uid).strip()
    header_uid = request.headers.get("X-Hunter-User-Id", "").strip()
    if header_uid:
        return header_uid
    if fallback_user_id and fallback_user_id.strip():
        return fallback_user_id.strip()
    return "default_local_user"


class OAuthAuthorizeRequest(BaseModel):
    broker_id: str = Field(default="galaxy_broker", description="合作券商标识，如 galaxy_broker / huaxi_broker / dongguan_broker")
    broker_name: str = Field(default="中国银河证券官方只读通道", description="合作券商通道名称")
    account_id: str = Field(default="GZ-88001928", description="资金账号（服务端自动脱敏展示）")
    access_token: str = Field(
        default="gz_oauth_ro_20261113_9f2a8b7c6d5e",
        description="券商 OAuth 2.0 短期只读访问凭证（服务端 AES-256-GCM 加密存储，绝不回显明文）",
    )
    scopes: list[str] = Field(
        default_factory=lambda: list(VALID_READONLY_SCOPES),
        description="授权只读 Scope 列表",
    )
    ttl_hours: int = Field(default=24, ge=1, le=720, description="短期凭证有效小时数")
    user_id: str | None = Field(default=None, description="可选用户标识（未登录本地单用户模式兜底）")


class OAuthRevokeRequest(BaseModel):
    reason: str = Field(default="用户在设置页主动点击一键撤销只读授权", description="撤销原因")
    user_id: str | None = Field(default=None, description="可选用户标识")


class RoutingConfigUpdateRequest(BaseModel):
    primary_provider_id: str = Field(default="galaxy_broker", description="主数据源标识")
    allow_fallback: bool = Field(default=True, description="主源超时或未覆盖时是否允许自动切换至备用公开源")
    merge_strategy: str = Field(default="primary_then_fallback", description="多源路由策略（严禁 average / mean 跨源取均值）")
    simulate_primary_timeout: bool = Field(default=False, description="是否模拟主源 HTTP 504 超时（供主备降级演练）")


class ManualImportRequest(BaseModel):
    filename: str = Field(default="galaxy_settlement_20261113.csv", description="上传文件名")
    import_type: str = Field(default="settlement_csv", description="导入类型：settlement_csv / positions_csv / excel_xlsx")
    csv_content: str = Field(..., description="CSV/TSV 交割单或持仓文本内容")
    user_id: str | None = Field(default=None, description="可选用户标识")


class ComplianceAuditEventRequest(BaseModel):
    user_id: str = Field(default="anonymous")
    session_id: str = Field(default="")
    mcp_server: str = Field(default="hookd")
    tool_name: str = Field(...)
    provider_id: str = Field(default="galaxy_broker")
    market: str = Field(default="CN_A")
    symbol: str | None = Field(default=None)
    license_scope: str = Field(default="authorized_user_readonly")
    status_code: str = Field(default="ok")
    error_message: str | None = Field(default=None)
    latency_ms: float = Field(default=1.0)
    response_bytes: int = Field(default=0)
    size_guarded: bool = Field(default=False)


@router.get("/status")
def api_get_broker_connector_status(
    request: Request,
    user_id: str | None = Query(default=None, description="可选查询用户ID"),
) -> dict[str, Any]:
    """获取当前用户的券商数据源路由配置、OAuth 只读授权状态（AES-256-GCM 脱敏）与手工交割单导入状态。"""
    uid = _resolve_session_user_id(request, user_id)
    oauth_status = get_broker_authorization_status(uid)
    latest_import = get_latest_manual_import(uid)
    audit_logs = list_broker_audit_logs(user_id=None, limit=15)

    return {
        "ok": True,
        "version": "3.4.0",
        "user_id": uid,
        "routing": dict(_ROUTING_CONFIG_STATE),
        "oauth": oauth_status,
        "manual_import": latest_import,
        "recent_audit_logs": audit_logs,
        "mcp_readonly_server": {
            "server_name": "broker_account_readonly",
            "tier": "P2",
            "default_enabled": False,
            "tools": ["get_accounts", "get_account_summary", "get_positions", "get_trades"],
            "forbidden_capabilities": ["place_order", "cancel_order", "modify_order", "transfer_cash"],
        },
    }


@router.post("/oauth/authorize")
def api_authorize_broker_readonly(req: OAuthAuthorizeRequest, request: Request) -> JSONResponse:
    """开启用户单独 OAuth 券商账户只读授权（Token 服务端 AES-256-GCM 加密托管）。"""
    uid = _resolve_session_user_id(request, req.user_id)
    try:
        status = grant_broker_readonly_authorization(
            user_id=uid,
            access_token=req.access_token,
            broker_id=req.broker_id,
            broker_name=req.broker_name,
            account_id=req.account_id,
            scopes=req.scopes,
            ttl_hours=req.ttl_hours,
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "message": "券商账户只读授权已开启（Token 已通过 AES-256-GCM 加密托管，绝不进入大模型上下文）",
                "oauth": status,
            },
        )
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": str(exc)},
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "INVALID_OAUTH_INPUT", "message": str(exc)},
        )


@router.post("/oauth/revoke")
def api_revoke_broker_readonly(req: OAuthRevokeRequest, request: Request) -> dict[str, Any]:
    """一键撤销券商只读授权：立即销毁加密凭证、清空对应账户缓存并记录 `broker_audit_log`。"""
    uid = _resolve_session_user_id(request, req.user_id)
    status = revoke_broker_readonly_authorization(user_id=uid, reason=req.reason)
    return {
        "ok": True,
        "message": "已一键撤销券商只读授权，AES-256-GCM 密文凭证已销毁，账户持仓缓存已清空并记入合规审计日志。",
        "oauth": status,
    }


@router.post("/routing/config")
def api_update_broker_routing_config(req: RoutingConfigUpdateRequest, request: Request) -> JSONResponse:
    """切换主备数据源配置（严格拦截跨源取均值策略）。"""
    uid = _resolve_session_user_id(request)
    try:
        assert_no_cross_source_averaging(req.merge_strategy)
    except CrossSourceAveragingForbiddenError as exc:
        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_connector",
            tool_name="update_routing_config",
            status_code="blocked",
            error_message=str(exc),
        )
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "CROSS_SOURCE_AVERAGING_FORBIDDEN", "message": str(exc)},
        )

    _ROUTING_CONFIG_STATE["primary_provider_id"] = req.primary_provider_id
    _ROUTING_CONFIG_STATE["allow_fallback"] = req.allow_fallback
    _ROUTING_CONFIG_STATE["merge_strategy"] = req.merge_strategy
    _ROUTING_CONFIG_STATE["simulate_primary_timeout"] = req.simulate_primary_timeout

    rt = get_default_broker_router()
    rt.allow_fallback = req.allow_fallback
    rt.primary.simulate_timeout = req.simulate_primary_timeout

    record_broker_audit_log(
        user_id=uid,
        mcp_server="broker_connector",
        tool_name="update_routing_config",
        provider_id=req.primary_provider_id,
        is_fallback=req.simulate_primary_timeout and req.allow_fallback,
        fallback_reason="用户开启主源超时模拟演练" if req.simulate_primary_timeout else None,
        status_code="ok",
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "message": "合作券商主备数据源路由策略已更新（严禁跨源取均值）",
            "routing": dict(_ROUTING_CONFIG_STATE),
        },
    )


@router.post("/manual-import")
def api_import_manual_settlement(req: ManualImportRequest, request: Request) -> JSONResponse:
    """未授权用户的 Excel/CSV 交割单与持仓手工导入解析接口。"""
    uid = _resolve_session_user_id(request, req.user_id)
    try:
        batch = parse_and_save_manual_settlement_csv(
            user_id=uid,
            csv_content=req.csv_content,
            filename=req.filename,
            import_type=req.import_type,
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "message": f"成功解析并导入 {batch['row_count']} 条交割/持仓记录，可直接用于组合风险巡检与交易复盘。",
                "batch": batch,
            },
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "INVALID_IMPORT_CSV", "message": str(exc)},
        )


@router.get("/manual-import/latest")
def api_get_latest_manual_import(
    request: Request,
    user_id: str | None = Query(default=None),
) -> dict[str, Any]:
    uid = _resolve_session_user_id(request, user_id)
    batch = get_latest_manual_import(uid)
    return {
        "ok": True,
        "user_id": uid,
        "has_manual_import": batch is not None,
        "batch": batch,
    }


@router.get("/account/accounts")
def api_get_readonly_accounts(
    request: Request,
    user_id: str | None = Query(default=None, description="请求的 user_id"),
    session_user_id: str | None = Query(default=None, description="真实会话登录用户 ID（用于跨租户校验）"),
) -> JSONResponse:
    real_uid = getattr(request.state, "user_id", None) or request.headers.get("X-Hunter-User-Id", "").strip() or session_user_id
    target_uid = user_id or real_uid or ""
    adapter = get_default_account_readonly_adapter()
    try:
        env = adapter.get_accounts(user_id=target_uid, session_user_id=real_uid)
        return JSONResponse(status_code=200, content=env.model_dump(mode="json"))
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": str(exc), "data": None},
        )


@router.get("/account/summary")
def api_get_readonly_account_summary(
    request: Request,
    user_id: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    session_user_id: str | None = Query(default=None),
) -> JSONResponse:
    real_uid = getattr(request.state, "user_id", None) or request.headers.get("X-Hunter-User-Id", "").strip() or session_user_id
    target_uid = user_id or real_uid or ""
    adapter = get_default_account_readonly_adapter()
    try:
        env = adapter.get_account_summary(user_id=target_uid, account_id=account_id, session_user_id=real_uid)
        return JSONResponse(status_code=200, content=env.model_dump(mode="json"))
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": str(exc), "data": None},
        )


@router.get("/account/positions")
def api_get_readonly_positions(
    request: Request,
    user_id: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    session_user_id: str | None = Query(default=None),
) -> JSONResponse:
    real_uid = getattr(request.state, "user_id", None) or request.headers.get("X-Hunter-User-Id", "").strip() or session_user_id
    target_uid = user_id or real_uid or ""
    adapter = get_default_account_readonly_adapter()
    try:
        env = adapter.get_positions(user_id=target_uid, account_id=account_id, session_user_id=real_uid)
        return JSONResponse(status_code=200, content=env.model_dump(mode="json"))
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": str(exc), "data": None},
        )


@router.get("/account/trades")
def api_get_readonly_trades(
    request: Request,
    user_id: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    session_user_id: str | None = Query(default=None),
) -> JSONResponse:
    real_uid = getattr(request.state, "user_id", None) or request.headers.get("X-Hunter-User-Id", "").strip() or session_user_id
    target_uid = user_id or real_uid or ""
    adapter = get_default_account_readonly_adapter()
    try:
        env = adapter.get_trades(
            user_id=target_uid,
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            session_user_id=real_uid,
        )
        return JSONResponse(status_code=200, content=env.model_dump(mode="json"))
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "code": exc.code, "message": str(exc), "data": None},
        )


@router.get("/audit-logs")
def api_list_broker_audit_logs(
    user_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
) -> dict[str, Any]:
    logs = list_broker_audit_logs(user_id=user_id, limit=limit)
    return {
        "ok": True,
        "count": len(logs),
        "logs": logs,
    }


@internal_router.get("/oauth-verify")
def internal_verify_broker_oauth(
    user_id: str = Query(..., description="待校验的用户 ID"),
    scope: str = Query(default="positions.read", description="所需只读 Scope"),
    tool_name: str = Query(default="get_positions"),
    session_user_id: str | None = Query(default=None),
) -> JSONResponse:
    """供 `hookd` 在 `PreToolUse` 阶段核验用户的 OAuth 只读授权与 Scope。"""
    try:
        rec = verify_user_oauth_scope(
            user_id=user_id,
            required_scope=scope,
            session_user_id=session_user_id,
            tool_name=tool_name,
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "authorized": True,
                "user_id": rec["user_id"],
                "broker_id": rec["broker_id"],
                "account_masked": rec["account_masked"],
                "scopes": rec["scopes"],
            },
        )
    except BrokerAccountUnauthorizedError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "authorized": False,
                "code": exc.code,
                "message": str(exc),
            },
        )


@internal_router.post("/audit-log")
def internal_write_broker_audit_log(req: ComplianceAuditEventRequest) -> dict[str, Any]:
    """供 `hookd` 的 `audit` 钩子将券商 MCP 调用与安全拦截事件同步写入 `broker_audit_log` 表。"""
    entry = record_broker_audit_log(
        user_id=req.user_id,
        session_id=req.session_id,
        mcp_server=req.mcp_server,
        tool_name=req.tool_name,
        provider_id=req.provider_id,
        market=req.market,
        symbol=req.symbol,
        license_scope=req.license_scope,
        status_code=req.status_code,
        error_message=req.error_message,
        latency_ms=req.latency_ms,
        response_bytes=req.response_bytes,
        size_guarded=req.size_guarded,
    )
    return {"ok": True, "trace_id": entry["trace_id"]}
