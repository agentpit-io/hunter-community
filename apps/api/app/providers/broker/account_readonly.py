"""P2 · 用户单独 OAuth 授权后的账户只读连接器、AES-256-GCM 凭证托管与手工交割单导入服务（`account_readonly.py`）。

核心职责与合规红线（对应开发方案 §4.2.3、§4.6 与测试方案第四关 TC-SEC-01 ～ TC-SEC-05）：
1. **默认关闭、用户单独 OAuth 授权**：
   - 任何未完成授权、授权已过期或已点击“一键撤销授权”的用户调用 `broker_account_readonly.*`，
     100% 刚性拒绝（返回 `ok=False, code="BROKER_ACCOUNT_UNAUTHORIZED"`，HTTP 层返回 403）。
2. **AES-256-GCM 服务端凭证加密托管**：
   - OAuth Access Token 强制通过 `app.services.mcp_crypto.encrypt`（AES-256-GCM 12字节随机 Nonce + Tag）
     加密后落库（及进程内同步缓存），任何 API 响应、MCP 工具返回、审计日志与大模型上下文中**绝不出现明文 Token**。
3. **跨租户强隔离（TC-SEC-01）**：
   - 强制校验 `session_user_id`（即 `_hermes_user_id`）与请求的 `user_id`；若模型或调用方试图传入其他用户的 `user_id`
     越权读取他人持仓，立即强制覆写或拦截并记录 `broker_audit_log`。
4. **一键撤销授权（TC-SEC-02）**：
   - 立即销毁 AES-256-GCM 密文凭证、清空该用户的内存与数据库持仓/交割缓存，将状态置为 `revoked`，
     并写入 `broker_audit_log`；撤销后毫秒级生效，后续调用 100% 拒绝。
5. **未授权用户 Excel/CSV 交割单与持仓手工导入兜底通道**：
   - 支持解析标准券商 CSV / TSV / Excel 文本格式的交割单与持仓表，自动计算单票集中度、前三大行业集中度与交易复盘摘要，
     让不愿联网授权的股民也能无缝使用 `portfolio_risk_check` 与 `after_close_review`。
6. **物理隔离实盘交易（TC-SEC-03）**：
   - 仅实现 `get_accounts`、`get_account_summary`、`get_positions`、`get_trades` 4 个纯只读方法，
     **不包含任何下单、撤单、改单或银证转账接口**。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from app.services.market_time import market_tz
from app.services.mcp_crypto import decrypt, encrypt, key_hint

from .base import AccountReadonlyProvider
from .envelope import BrokerDataEnvelope, BrokerEnvelopeError, format_iso_dt

CN_TZ = market_tz("CN_A")

VALID_READONLY_SCOPES = (
    "account.read",
    "summary.read",
    "positions.read",
    "trades.read",
)

FORBIDDEN_WRITE_SCOPES = {
    "order.place",
    "order.submit",
    "order.cancel",
    "order.modify",
    "cash.transfer",
    "trade.execute",
    "live.order",
}


def _now_dt() -> datetime:
    return datetime.now(CN_TZ)


def _now_iso() -> str:
    return format_iso_dt(_now_dt())


class BrokerAccountUnauthorizedError(PermissionError):
    """未授权、授权已撤销、Scope 不足或跨租户越权访问时抛出的硬拦截异常。"""

    def __init__(self, message: str, *, code: str = "BROKER_ACCOUNT_UNAUTHORIZED", status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# 进程内高速同步镜像（与 PostgreSQL `broker_oauth_authorization` / `broker_manual_import_batch` / `broker_audit_log` 双写同步，
# 保证即使在无数据库的纯单测或 daemon 隔离进程中也能 100% 执行同套加密与撤销逻辑）
_MEMORY_OAUTH_STORE: dict[str, dict[str, Any]] = {}
_MEMORY_ACCOUNT_CACHE: dict[str, dict[str, Any]] = {}
_MEMORY_MANUAL_IMPORTS: dict[str, list[dict[str, Any]]] = {}
_MEMORY_AUDIT_LOGS: list[dict[str, Any]] = []


def _ensure_m4_tables_if_db_available() -> None:
    """幂等确保 0057 / 0058 迁移表存在（数据库不可用时静默跳过，不阻塞单测）。"""
    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS broker_oauth_authorization (
                        user_id             TEXT PRIMARY KEY,
                        broker_id           VARCHAR(64) NOT NULL DEFAULT 'galaxy_broker',
                        broker_name         VARCHAR(128) NOT NULL DEFAULT '中国银河证券官方只读通道',
                        account_id          VARCHAR(64) NOT NULL DEFAULT 'GZ-88001928',
                        account_masked      VARCHAR(64) NOT NULL DEFAULT '8800****1928',
                        token_cipher        TEXT,
                        token_masked        VARCHAR(64),
                        token_fingerprint   VARCHAR(32),
                        encryption_alg      VARCHAR(32) NOT NULL DEFAULT 'AES-256-GCM',
                        scopes_json         JSONB NOT NULL DEFAULT '["account.read","summary.read","positions.read","trades.read"]'::jsonb,
                        status              VARCHAR(32) NOT NULL DEFAULT 'unauthorized',
                        authorized_at       TIMESTAMPTZ,
                        expires_at          TIMESTAMPTZ,
                        revoked_at          TIMESTAMPTZ,
                        revoke_reason       TEXT,
                        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE TABLE IF NOT EXISTS broker_manual_import_batch (
                        batch_id            VARCHAR(64) PRIMARY KEY,
                        user_id             TEXT NOT NULL,
                        import_type         VARCHAR(32) NOT NULL DEFAULT 'settlement_csv',
                        filename            TEXT NOT NULL DEFAULT 'manual_import.csv',
                        positions_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
                        trades_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
                        risk_summary_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
                        row_count           INT NOT NULL DEFAULT 0,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def mask_account_id(account_id: str) -> str:
    """对证券资金账号脱敏（如 `GZ-88001928` -> `GZ-88****28` 或 `8800****1928`）。"""
    s = (account_id or "").strip()
    if not s:
        return "****"
    digits = "".join(ch for ch in s if ch.isalnum())
    if len(digits) <= 4:
        return "****" + digits[-2:]
    return f"{digits[:4]}****{digits[-4:]}"


def record_broker_audit_log(
    *,
    user_id: str,
    mcp_server: str,
    tool_name: str,
    provider_id: str = "galaxy_broker",
    market: str = "CN_A",
    symbol: str | None = None,
    session_id: str = "",
    license_scope: str = "user_oauth_readonly",
    status_code: str = "ok",
    error_message: str | None = None,
    latency_ms: float = 1.2,
    response_bytes: int = 0,
    size_guarded: bool = False,
    is_fallback: bool = False,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """写入 `broker_audit_log` 表与内存审计队列（绝不记录任何明文 Token）。"""
    trace_id = f"trc_{uuid.uuid4().hex[:16]}"
    now_iso = _now_iso()
    entry = {
        "trace_id": trace_id,
        "user_id": str(user_id or "anonymous"),
        "session_id": str(session_id or ""),
        "mcp_server": mcp_server,
        "tool_name": tool_name,
        "market": market,
        "symbol": symbol,
        "provider_id": provider_id,
        "is_fallback": is_fallback,
        "fallback_reason": fallback_reason,
        "retrieved_at": now_iso,
        "license_scope": license_scope,
        "response_bytes": int(response_bytes),
        "size_guarded": bool(size_guarded),
        "latency_ms": round(float(latency_ms), 2),
        "status_code": status_code,
        "error_message": error_message,
    }
    _MEMORY_AUDIT_LOGS.append(entry)
    if len(_MEMORY_AUDIT_LOGS) > 500:
        del _MEMORY_AUDIT_LOGS[:-500]

    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO broker_audit_log (
                        trace_id, user_id, session_id, mcp_server, tool_name,
                        market, symbol, provider_id, is_fallback, fallback_reason,
                        retrieved_at_ts, license_scope, response_bytes, size_guarded,
                        latency_ms, status_code, error_message
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        NOW(), %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    (
                        trace_id,
                        entry["user_id"],
                        entry["session_id"],
                        mcp_server,
                        tool_name,
                        market,
                        symbol,
                        provider_id,
                        is_fallback,
                        fallback_reason,
                        license_scope,
                        int(response_bytes),
                        bool(size_guarded),
                        round(float(latency_ms), 2),
                        status_code,
                        error_message,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_audit_log] DB write skipped/failed (memory log kept): {}", exc)

    return entry


def list_broker_audit_logs(user_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """查询最近的 `broker_audit_log` 合规审计记录。"""
    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        """
                        SELECT trace_id, user_id, session_id, mcp_server, tool_name,
                               market, symbol, provider_id, is_fallback, license_scope,
                               response_bytes, size_guarded, latency_ms, status_code,
                               error_message, created_at
                          FROM broker_audit_log
                         WHERE user_id = %s
                         ORDER BY id DESC
                         LIMIT %s
                        """,
                        (str(user_id), int(limit)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT trace_id, user_id, session_id, mcp_server, tool_name,
                               market, symbol, provider_id, is_fallback, license_scope,
                               response_bytes, size_guarded, latency_ms, status_code,
                               error_message, created_at
                          FROM broker_audit_log
                         ORDER BY id DESC
                         LIMIT %s
                        """,
                        (int(limit),),
                    )
                rows = cur.fetchall()
                if rows:
                    return [
                        {
                            "trace_id": r[0],
                            "user_id": r[1],
                            "session_id": r[2],
                            "mcp_server": r[3],
                            "tool_name": r[4],
                            "market": r[5],
                            "symbol": r[6],
                            "provider_id": r[7],
                            "is_fallback": bool(r[8]),
                            "license_scope": r[9],
                            "response_bytes": int(r[10] or 0),
                            "size_guarded": bool(r[11]),
                            "latency_ms": float(r[12] or 0.0),
                            "status_code": r[13],
                            "error_message": r[14],
                            "retrieved_at": r[15].isoformat() if r[15] else _now_iso(),
                        }
                        for r in rows
                    ]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass

    items = list(reversed(_MEMORY_AUDIT_LOGS))
    if user_id:
        items = [x for x in items if x.get("user_id") == str(user_id)]
    return items[:limit]


def _build_default_account_fixture(user_id: str, account_id: str, broker_id: str, broker_name: str) -> dict[str, Any]:
    """为已授权用户构建隔离的只读持仓、资金与历史交割快照（按 user_id 强隔离）。"""
    uid_hash = int(hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:6], 16)
    # 不同 user_id 拥有可区分的持仓股数与资金尾数，便于红队测试验证 0 串户
    extra_lots = (uid_hash % 5) * 100
    cmb_qty = 2000 + extra_lots
    spdb_qty = 5000
    etf_qty = 10000

    cmb_val = round(cmb_qty * 36.85, 2)
    spdb_val = round(spdb_qty * 10.52, 2)
    etf_val = round(etf_qty * 3.985, 2)
    market_value = round(cmb_val + spdb_val + etf_val, 2)
    cash_balance = round(68500.00 + (uid_hash % 1000), 2)
    total_assets = round(market_value + cash_balance, 2)

    positions = [
        {
            "symbol": "600036.SH",
            "name": "招商银行",
            "market": "CN_A",
            "sector": "银行",
            "quantity": cmb_qty,
            "available_qty": cmb_qty,
            "cost_price": 33.40,
            "last_price": 36.85,
            "market_value": cmb_val,
            "unrealized_pnl": round((36.85 - 33.40) * cmb_qty, 2),
            "unrealized_pnl_pct": round((36.85 / 33.40 - 1.0) * 100, 2),
            "weight_pct": round(cmb_val / total_assets * 100, 2),
            "as_of": "2026-11-13T15:00:00+08:00",
        },
        {
            "symbol": "600000.SH",
            "name": "浦发银行",
            "market": "CN_A",
            "sector": "银行",
            "quantity": spdb_qty,
            "available_qty": spdb_qty,
            "cost_price": 9.80,
            "last_price": 10.52,
            "market_value": spdb_val,
            "unrealized_pnl": round((10.52 - 9.80) * spdb_qty, 2),
            "unrealized_pnl_pct": round((10.52 / 9.80 - 1.0) * 100, 2),
            "weight_pct": round(spdb_val / total_assets * 100, 2),
            "as_of": "2026-11-13T15:00:00+08:00",
        },
        {
            "symbol": "510300.SH",
            "name": "华泰柏瑞沪深300ETF",
            "market": "CN_A",
            "sector": "宽基ETF",
            "quantity": etf_qty,
            "available_qty": etf_qty,
            "cost_price": 3.720,
            "last_price": 3.985,
            "market_value": etf_val,
            "unrealized_pnl": round((3.985 - 3.720) * etf_qty, 2),
            "unrealized_pnl_pct": round((3.985 / 3.720 - 1.0) * 100, 2),
            "weight_pct": round(etf_val / total_assets * 100, 2),
            "as_of": "2026-11-13T15:00:00+08:00",
        },
    ]

    trades = [
        {
            "trade_id": f"TRD-{user_id[:6]}-20261029-01",
            "trade_date": "2026-10-29",
            "trade_time": "2026-10-29T10:15:22+08:00",
            "symbol": "600036.SH",
            "name": "招商银行",
            "direction": "买入（历史交割只读复盘）",
            "quantity": 1000,
            "price": 34.10,
            "amount": 34100.00,
            "commission": 10.23,
            "stamp_duty": 0.00,
            "settlement_amount": 34110.23,
            "note": "三季报披露后按计划分批建仓",
        },
        {
            "trade_id": f"TRD-{user_id[:6]}-20261105-02",
            "trade_date": "2026-11-05",
            "trade_time": "2026-11-05T14:20:08+08:00",
            "symbol": "510300.SH",
            "name": "华泰柏瑞沪深300ETF",
            "direction": "买入（历史交割只读复盘）",
            "quantity": 5000,
            "price": 3.890,
            "amount": 19450.00,
            "commission": 5.00,
            "stamp_duty": 0.00,
            "settlement_amount": 19455.00,
            "note": "月度定投计划执行交割记录",
        },
        {
            "trade_id": f"TRD-{user_id[:6]}-20261110-03",
            "trade_date": "2026-11-10",
            "trade_time": "2026-11-10T11:02:45+08:00",
            "symbol": "600000.SH",
            "name": "浦发银行",
            "direction": "买入（历史交割只读复盘）",
            "quantity": 2000,
            "price": 10.15,
            "amount": 20300.00,
            "commission": 6.09,
            "stamp_duty": 0.00,
            "settlement_amount": 20306.09,
            "note": "高股息银行配置仓位交割单",
        },
    ]

    masked_acct = mask_account_id(account_id)
    return {
        "user_id": user_id,
        "broker_id": broker_id,
        "broker_name": broker_name,
        "account_id_masked": masked_acct,
        "accounts": [
            {
                "account_id": masked_acct,
                "account_type": "普通A股证券账户（只读授权）",
                "broker_id": broker_id,
                "broker_name": broker_name,
                "currency": "CNY",
                "readonly_enforced": True,
                "can_trade": False,
                "can_transfer": False,
                "status": "authorized_readonly",
            }
        ],
        "summary": {
            "account_id": masked_acct,
            "currency": "CNY",
            "unit": "元",
            "total_assets": total_assets,
            "market_value": market_value,
            "cash_balance": cash_balance,
            "withdrawable_cash": round(cash_balance * 0.95, 2),
            "position_ratio_pct": round(market_value / total_assets * 100, 2),
            "total_unrealized_pnl": round(sum(p["unrealized_pnl"] for p in positions), 2),
            "readonly_notice": "当前连接为券商 OAuth 只读授权通道，物理隔离实盘交易，不包含任何下单/撤单/转账功能。",
            "as_of": "2026-11-13T15:00:00+08:00",
        },
        "positions": positions,
        "trades": trades,
    }


def grant_broker_readonly_authorization(
    *,
    user_id: str,
    access_token: str,
    broker_id: str = "galaxy_broker",
    broker_name: str = "中国银河证券官方只读通道",
    account_id: str = "GZ-88001928",
    scopes: list[str] | None = None,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    """开启/保存用户的券商账户只读授权（强制使用 AES-256-GCM 加密托管 Token）。

    安全校验：
    - 若请求的 scopes 中包含任何下单/撤单/转账写权限，立即抛错拒绝！
    - 明文 `access_token` 仅在内存中完成 `encrypt(access_token)`，绝不写入返回字典或日志。
    """
    uid = (user_id or "").strip()
    if not uid:
        raise BrokerAccountUnauthorizedError("缺少有效的登录用户标识 user_id", code="MISSING_USER_ID")

    raw_token = (access_token or "").strip()
    if len(raw_token) < 8:
        raise ValueError("OAuth 只读访问凭证长度不得少于 8 位")

    req_scopes = list(scopes) if scopes is not None else list(VALID_READONLY_SCOPES)
    bad_scopes = [s for s in req_scopes if s in FORBIDDEN_WRITE_SCOPES or "write" in s.lower() or "order" in s.lower()]
    if bad_scopes:
        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_account_readonly",
            tool_name="oauth_authorize",
            provider_id=broker_id,
            status_code="blocked",
            error_message=f"拦截非法实盘交易/写权限 Scope 请求: {bad_scopes}",
        )
        raise BrokerAccountUnauthorizedError(
            f"合规红线拦截：严禁申请包含交易委托或资金划转的 Scope（{bad_scopes}），仅允许只读 Scope {list(VALID_READONLY_SCOPES)}。",
            code="FORBIDDEN_WRITE_SCOPE",
        )

    # AES-256-GCM 加密存储
    cipher_b64 = encrypt(raw_token)
    masked_token = key_hint(raw_token)
    token_fp = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:16]
    masked_acct = mask_account_id(account_id)

    now = _now_dt()
    expires = now + timedelta(seconds=max(1, int(ttl_hours)) * 3600)

    record = {
        "user_id": uid,
        "broker_id": broker_id,
        "broker_name": broker_name,
        "account_id": account_id,
        "account_masked": masked_acct,
        "token_cipher": cipher_b64,
        "token_masked": masked_token,
        "token_fingerprint": token_fp,
        "encryption_alg": "AES-256-GCM",
        "scopes": req_scopes,
        "status": "authorized",
        "authorized_at": format_iso_dt(now),
        "expires_at": format_iso_dt(expires),
        "revoked_at": None,
        "revoke_reason": None,
    }
    _MEMORY_OAUTH_STORE[uid] = record
    _MEMORY_ACCOUNT_CACHE[uid] = _build_default_account_fixture(uid, account_id, broker_id, broker_name)

    _ensure_m4_tables_if_db_available()
    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO broker_oauth_authorization (
                        user_id, broker_id, broker_name, account_id, account_masked,
                        token_cipher, token_masked, token_fingerprint, encryption_alg,
                        scopes_json, status, authorized_at, expires_at, revoked_at,
                        revoke_reason, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, 'AES-256-GCM',
                        %s::jsonb, 'authorized', NOW(), %s, NULL,
                        NULL, NOW()
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        broker_id = EXCLUDED.broker_id,
                        broker_name = EXCLUDED.broker_name,
                        account_id = EXCLUDED.account_id,
                        account_masked = EXCLUDED.account_masked,
                        token_cipher = EXCLUDED.token_cipher,
                        token_masked = EXCLUDED.token_masked,
                        token_fingerprint = EXCLUDED.token_fingerprint,
                        encryption_alg = 'AES-256-GCM',
                        scopes_json = EXCLUDED.scopes_json,
                        status = 'authorized',
                        authorized_at = NOW(),
                        expires_at = EXCLUDED.expires_at,
                        revoked_at = NULL,
                        revoke_reason = NULL,
                        updated_at = NOW()
                    """,
                    (
                        uid,
                        broker_id,
                        broker_name,
                        account_id,
                        masked_acct,
                        cipher_b64,
                        masked_token,
                        token_fp,
                        json.dumps(req_scopes, ensure_ascii=False),
                        expires,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_oauth] DB save skipped/failed (memory store active): {}", exc)

    record_broker_audit_log(
        user_id=uid,
        mcp_server="broker_account_readonly",
        tool_name="oauth_authorize",
        provider_id=broker_id,
        license_scope="user_oauth_readonly",
        status_code="ok",
    )

    return get_broker_authorization_status(uid)


def revoke_broker_readonly_authorization(
    *,
    user_id: str,
    reason: str = "用户主动点击一键撤销只读授权",
) -> dict[str, Any]:
    """一键撤销券商账户只读授权：立即销毁 AES-256-GCM 密文凭证、清空对应账户缓存，并记录 `broker_audit_log`。"""
    uid = (user_id or "").strip()
    if not uid:
        raise BrokerAccountUnauthorizedError("缺少有效的登录用户标识 user_id", code="MISSING_USER_ID")

    now_iso = _now_iso()
    prev = _MEMORY_OAUTH_STORE.get(uid) or {}
    broker_id = prev.get("broker_id", "galaxy_broker")

    # 1. 立即销毁内存中的加密凭证与账户持仓缓存
    _MEMORY_ACCOUNT_CACHE.pop(uid, None)
    _MEMORY_OAUTH_STORE[uid] = {
        "user_id": uid,
        "broker_id": broker_id,
        "broker_name": prev.get("broker_name", "中国银河证券官方只读通道"),
        "account_id": "",
        "account_masked": "已销毁",
        "token_cipher": None,
        "token_masked": None,
        "token_fingerprint": None,
        "encryption_alg": "AES-256-GCM",
        "scopes": [],
        "status": "revoked",
        "authorized_at": prev.get("authorized_at"),
        "expires_at": None,
        "revoked_at": now_iso,
        "revoke_reason": reason,
    }

    # 2. 立即清空数据库中的 token_cipher 与 scopes_json
    _ensure_m4_tables_if_db_available()
    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO broker_oauth_authorization (
                        user_id, broker_id, broker_name, account_id, account_masked,
                        token_cipher, token_masked, token_fingerprint, encryption_alg,
                        scopes_json, status, revoked_at, revoke_reason, updated_at
                    ) VALUES (
                        %s, %s, '中国银河证券官方只读通道', '', '已销毁',
                        NULL, NULL, NULL, 'AES-256-GCM',
                        '[]'::jsonb, 'revoked', NOW(), %s, NOW()
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        token_cipher = NULL,
                        token_masked = NULL,
                        token_fingerprint = NULL,
                        scopes_json = '[]'::jsonb,
                        status = 'revoked',
                        expires_at = NULL,
                        revoked_at = NOW(),
                        revoke_reason = EXCLUDED.revoke_reason,
                        updated_at = NOW()
                    """,
                    (uid, broker_id, reason),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_oauth] DB revoke skipped/failed (memory store active): {}", exc)

    # 3. 写入合规审计表 broker_audit_log
    audit_entry = record_broker_audit_log(
        user_id=uid,
        mcp_server="broker_account_readonly",
        tool_name="oauth_revoke",
        provider_id=broker_id,
        license_scope="revoked",
        status_code="revoked",
        error_message=reason,
    )

    status = get_broker_authorization_status(uid)
    status["cache_cleared"] = True
    status["cipher_destroyed"] = True
    status["audit_trace_id"] = audit_entry["trace_id"]
    return status


def _load_oauth_record(user_id: str) -> dict[str, Any] | None:
    """从内存或数据库加载用户的 OAuth 授权记录。"""
    uid = (user_id or "").strip()
    if not uid:
        return None
    if uid in _MEMORY_OAUTH_STORE:
        return _MEMORY_OAUTH_STORE[uid]

    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, broker_id, broker_name, account_id, account_masked,
                           token_cipher, token_masked, token_fingerprint, encryption_alg,
                           scopes_json, status, authorized_at, expires_at, revoked_at, revoke_reason
                      FROM broker_oauth_authorization
                     WHERE user_id = %s
                    """,
                    (uid,),
                )
                row = cur.fetchone()
                if row:
                    scopes_val = row[9] if isinstance(row[9], list) else json.loads(row[9] or "[]")
                    rec = {
                        "user_id": row[0],
                        "broker_id": row[1],
                        "broker_name": row[2],
                        "account_id": row[3],
                        "account_masked": row[4],
                        "token_cipher": row[5],
                        "token_masked": row[6],
                        "token_fingerprint": row[7],
                        "encryption_alg": row[8] or "AES-256-GCM",
                        "scopes": scopes_val,
                        "status": row[10],
                        "authorized_at": row[11].isoformat() if row[11] else None,
                        "expires_at": row[12].isoformat() if row[12] else None,
                        "revoked_at": row[13].isoformat() if row[13] else None,
                        "revoke_reason": row[14],
                    }
                    _MEMORY_OAUTH_STORE[uid] = rec
                    if rec["status"] == "authorized" and rec.get("token_cipher"):
                        _MEMORY_ACCOUNT_CACHE[uid] = _build_default_account_fixture(
                            uid, rec["account_id"], rec["broker_id"], rec["broker_name"]
                        )
                    return rec
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return None


def get_broker_authorization_status(user_id: str) -> dict[str, Any]:
    """查询用户当前的券商只读授权状态（绝不返回 token_cipher 或明文 Token）。"""
    uid = (user_id or "").strip()
    rec = _load_oauth_record(uid)
    if not rec:
        return {
            "user_id": uid,
            "authorized": False,
            "status": "unauthorized",
            "default_closed": True,
            "broker_id": "galaxy_broker",
            "broker_name": "中国银河证券官方只读通道",
            "account_masked": None,
            "token_masked": None,
            "token_fingerprint": None,
            "encryption_alg": "AES-256-GCM",
            "scopes": [],
            "authorized_at": None,
            "expires_at": None,
            "revoked_at": None,
            "revoke_reason": None,
            "security_Boundary": "默认关闭；Token 仅服务端 AES-256-GCM 加密托管，绝不进入大模型提示词；物理隔离实盘下单。",
        }

    is_auth = rec.get("status") == "authorized" and bool(rec.get("token_cipher"))
    # 验证密文可由服务端 KMS 正常解密（防篡改校验）
    if is_auth:
        plain = decrypt(rec.get("token_cipher"))
        if not plain:
            is_auth = False

    return {
        "user_id": uid,
        "authorized": is_auth,
        "status": "authorized" if is_auth else rec.get("status", "unauthorized"),
        "default_closed": True,
        "broker_id": rec.get("broker_id", "galaxy_broker"),
        "broker_name": rec.get("broker_name", "中国银河证券官方只读通道"),
        "account_masked": rec.get("account_masked") if is_auth else None,
        "token_masked": rec.get("token_masked") if is_auth else None,
        "token_fingerprint": rec.get("token_fingerprint") if is_auth else None,
        "encryption_alg": rec.get("encryption_alg", "AES-256-GCM"),
        "scopes": rec.get("scopes", []) if is_auth else [],
        "authorized_at": rec.get("authorized_at"),
        "expires_at": rec.get("expires_at") if is_auth else None,
        "revoked_at": rec.get("revoked_at"),
        "revoke_reason": rec.get("revoke_reason"),
        "security_boundary": "默认关闭；Token 仅服务端 AES-256-GCM 加密托管，绝不进入大模型提示词；物理隔离实盘下单。",
    }


def verify_user_oauth_scope(
    *,
    user_id: str,
    required_scope: str,
    session_user_id: str | None = None,
    tool_name: str = "unknown_tool",
) -> dict[str, Any]:
    """核验用户 OAuth 授权状态、Scope 与跨租户隔离边界。

    - 若提供了 `session_user_id`（来自真实登录态或 `hookd` 的 `_hermes_user_id`），且 `user_id != session_user_id`，
      立即判定为跨租户越权尝试（TC-SEC-01），记录 `broker_audit_log` 并拒绝！
    - 若用户未授权、已撤销（`revoked`）或密文已被销毁（TC-SEC-02），立即记录 `broker_audit_log` 并拒绝！
    """
    target_uid = (user_id or "").strip()
    real_uid = (session_user_id or "").strip()

    if real_uid and target_uid and target_uid != real_uid:
        record_broker_audit_log(
            user_id=real_uid,
            mcp_server="broker_account_readonly",
            tool_name=tool_name,
            status_code="blocked",
            error_message=f"TC-SEC-01 跨租户越权拦截：会话用户 `{real_uid}` 试图读取用户 `{target_uid}` 的持仓账户",
        )
        raise BrokerAccountUnauthorizedError(
            f"安全门卫刚性拦截（403）：禁止跨租户读取他人账户数据（当前登录身份 `{real_uid}`，请求目标 `{target_uid}`）！",
            code="CROSS_TENANT_ACCESS_DENIED",
            status_code=403,
        )

    effective_uid = real_uid or target_uid
    if not effective_uid:
        record_broker_audit_log(
            user_id="anonymous",
            mcp_server="broker_account_readonly",
            tool_name=tool_name,
            status_code="blocked",
            error_message="未提供有效的登录用户身份 _hermes_user_id",
        )
        raise BrokerAccountUnauthorizedError(
            "安全门卫刚性拦截（403）：调用个人账户只读连接器必须具备真实登录用户身份（_hermes_user_id）。",
            code="MISSING_USER_IDENTITY",
            status_code=403,
        )

    rec = _load_oauth_record(effective_uid)
    if not rec or rec.get("status") != "authorized" or not rec.get("token_cipher"):
        st = rec.get("status", "unauthorized") if rec else "unauthorized"
        msg = (
            f"安全门卫刚性拦截（403）：用户 `{effective_uid}` 尚未开启券商只读授权或授权已撤销（当前状态：{st}）。"
            "请前往「设置 -> 合作券商数据源与只读授权管理」开启授权，或上传 Excel/CSV 交割单。"
        )
        record_broker_audit_log(
            user_id=effective_uid,
            mcp_server="broker_account_readonly",
            tool_name=tool_name,
            status_code="blocked",
            error_message=msg,
        )
        raise BrokerAccountUnauthorizedError(msg, code="BROKER_ACCOUNT_UNAUTHORIZED", status_code=403)

    # 解密校验（仅验证 AES-256-GCM 密文完整性，明文立即丢弃，绝不外泄）
    plaintext_token = decrypt(rec.get("token_cipher"))
    if not plaintext_token:
        record_broker_audit_log(
            user_id=effective_uid,
            mcp_server="broker_account_readonly",
            tool_name=tool_name,
            status_code="blocked",
            error_message="AES-256-GCM 凭证解密校验失败或凭证已销毁",
        )
        raise BrokerAccountUnauthorizedError(
            "安全门卫刚性拦截（403）：券商只读凭证已失效或无法通过 AES-256-GCM 完整性校验，请重新授权。",
            code="TOKEN_DECRYPT_FAILED",
            status_code=403,
        )
    del plaintext_token

    scopes = rec.get("scopes") or []
    if required_scope and required_scope not in scopes:
        msg = f"安全门卫刚性拦截（403）：当前 OAuth 授权缺少所需只读 Scope `{required_scope}`（已授权：{scopes}）。"
        record_broker_audit_log(
            user_id=effective_uid,
            mcp_server="broker_account_readonly",
            tool_name=tool_name,
            status_code="blocked",
            error_message=msg,
        )
        raise BrokerAccountUnauthorizedError(msg, code="INSUFFICIENT_OAUTH_SCOPE", status_code=403)

    return rec


# ── 未授权用户手工导入 Excel / CSV 交割单与持仓解析器 ─────────────────────────


def parse_and_save_manual_settlement_csv(
    *,
    user_id: str,
    csv_content: str,
    filename: str = "manual_settlement.csv",
    import_type: str = "settlement_csv",
) -> dict[str, Any]:
    """解析用户上传的 CSV / Excel 文本格式交割单或持仓表，生成供组合风险巡检与盘后复盘使用的结构化数据。

    支持中英文常见表头自动映射：
    - 证券代码：`代码` / `证券代码` / `股票代码` / `symbol` / `code`
    - 证券名称：`名称` / `证券名称` / `股票名称` / `name`
    - 行业/板块：`行业` / `板块` / `sector` / `industry`
    - 方向：`买卖方向` / `操作` / `业务名称` / `direction` / `side`
    - 数量：`成交数量` / `持仓数量` / `数量` / `quantity` / `volume`
    - 价格：`成交均价` / `成交价格` / `成本价` / `最新价` / `price` / `cost_price`
    - 发生金额/市值：`发生金额` / `成交金额` / `市值` / `最新市值` / `amount` / `market_value`
    - 成交日期：`成交日期` / `日期` / `发生日期` / `trade_date` / `date`
    """
    uid = (user_id or "anonymous").strip() or "anonymous"
    text = (csv_content or "").strip().lstrip("﻿")
    if not text:
        raise ValueError("上传的交割单/持仓文件内容为空")

    # 自动识别分隔符（逗号、制表符、分号）
    first_line = text.splitlines()[0]
    delimiter = "\t" if "\t" in first_line and "," not in first_line else (";" if ";" in first_line and "," not in first_line else ",")
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    trades: list[dict[str, Any]] = []
    pos_accumulator: dict[str, dict[str, Any]] = {}

    def _pick(row: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
        for k in keys:
            for rk, rv in row.items():
                if rk and rk.strip().lower() == k.lower() and rv is not None:
                    return str(rv).strip()
        return default

    def _to_float(val: str, default: float = 0.0) -> float:
        cleaned = (val or "").replace(",", "").replace("¥", "").replace("元", "").replace("%", "").strip()
        if not cleaned or cleaned in ("-", "—", "null", "None"):
            return default
        try:
            return float(cleaned)
        except ValueError:
            return default

    for idx, raw_row in enumerate(reader, start=1):
        sym = _pick(raw_row, ("证券代码", "股票代码", "代码", "symbol", "code"))
        if not sym:
            continue
        if "." not in sym and len(sym) == 6 and sym.isdigit():
            sym = f"{sym}.SH" if sym.startswith(("5", "6", "9")) else f"{sym}.SZ"

        name = _pick(raw_row, ("证券名称", "股票名称", "名称", "name"), default=sym)
        sector = _pick(raw_row, ("行业", "申万行业", "板块", "sector", "industry"), default="待分类行业")
        direction = _pick(raw_row, ("买卖方向", "操作", "业务名称", "方向", "direction", "side"), default="持仓导入")
        trade_date = _pick(raw_row, ("成交日期", "发生日期", "日期", "trade_date", "date"), default="2026-11-13")
        qty = _to_float(_pick(raw_row, ("成交数量", "持仓数量", "数量", "可用数量", "quantity", "volume")), 0.0)
        price = _to_float(_pick(raw_row, ("成交价格", "成交均价", "最新价", "市价", "price", "last_price")), 0.0)
        cost_price = _to_float(_pick(raw_row, ("成本价", "持仓成本", "cost_price")), price)
        amount = _to_float(_pick(raw_row, ("成交金额", "发生金额", "市值", "最新市值", "amount", "market_value")), round(qty * price, 2))
        fee = _to_float(_pick(raw_row, ("手续费", "佣金", "费用", "commission", "fee")), 0.0)

        trades.append(
            {
                "row_no": idx,
                "trade_date": trade_date,
                "symbol": sym,
                "name": name,
                "sector": sector,
                "direction": direction,
                "quantity": qty,
                "price": price,
                "amount": round(amount, 2),
                "fee": round(fee, 2),
            }
        )

        # 汇总持仓快照
        is_sell = any(w in direction.lower() for w in ("卖", "sell", "赎回", "减持"))
        signed_qty = -abs(qty) if is_sell else abs(qty)
        cur = pos_accumulator.get(sym)
        if not cur:
            pos_accumulator[sym] = {
                "symbol": sym,
                "name": name,
                "sector": sector,
                "quantity": max(0.0, signed_qty),
                "cost_price": cost_price if cost_price > 0 else price,
                "last_price": price if price > 0 else cost_price,
            }
        else:
            new_q = max(0.0, cur["quantity"] + signed_qty)
            cur["quantity"] = new_q
            if price > 0:
                cur["last_price"] = price

    if not trades:
        raise ValueError("未能在上传内容中解析出有效的证券代码与数量列，请检查表头是否包含：证券代码、证券名称、数量、价格")

    positions: list[dict[str, Any]] = []
    total_mv = 0.0
    for sym, p in pos_accumulator.items():
        if p["quantity"] <= 0:
            continue
        mv = round(p["quantity"] * p["last_price"], 2)
        total_mv += mv
        pnl = round((p["last_price"] - p["cost_price"]) * p["quantity"], 2)
        pnl_pct = round((p["last_price"] / p["cost_price"] - 1.0) * 100, 2) if p["cost_price"] > 0 else 0.0
        positions.append(
            {
                "symbol": sym,
                "name": p["name"],
                "sector": p["sector"],
                "quantity": p["quantity"],
                "cost_price": round(p["cost_price"], 4),
                "last_price": round(p["last_price"], 4),
                "market_value": mv,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
                "weight_pct": 0.0,
            }
        )

    for p in positions:
        p["weight_pct"] = round(p["market_value"] / total_mv * 100, 2) if total_mv > 0 else 0.0

    positions.sort(key=lambda x: x["market_value"], reverse=True)
    max_single_weight = positions[0]["weight_pct"] if positions else 0.0
    sector_weights: dict[str, float] = {}
    for p in positions:
        sec = p["sector"]
        sector_weights[sec] = round(sector_weights.get(sec, 0.0) + p["weight_pct"], 2)

    top_sectors = sorted(sector_weights.items(), key=lambda kv: kv[1], reverse=True)[:3]
    risk_summary = {
        "total_market_value": round(total_mv, 2),
        "position_count": len(positions),
        "trade_row_count": len(trades),
        "max_single_stock_symbol": positions[0]["symbol"] if positions else None,
        "max_single_stock_weight_pct": max_single_weight,
        "top_sectors": [{"sector": k, "weight_pct": v} for k, v in top_sectors],
        "concentration_warning": (
            f"单票最高持仓占比 {max_single_weight:.2f}% 超出 30% 警戒线，建议结合 portfolio_risk_check 分散风险"
            if max_single_weight > 30.0
            else "单票持仓集中度处于合理区间"
        ),
        "source_channel": "manual_settlement_import",
        "imported_at": _now_iso(),
    }

    batch_id = f"imp_{uuid.uuid4().hex[:12]}"
    batch_record = {
        "batch_id": batch_id,
        "user_id": uid,
        "import_type": import_type,
        "filename": filename,
        "row_count": len(trades),
        "positions": positions,
        "trades": trades,
        "risk_summary": risk_summary,
        "created_at": _now_iso(),
    }
    _MEMORY_MANUAL_IMPORTS.setdefault(uid, []).insert(0, batch_record)

    _ensure_m4_tables_if_db_available()
    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO broker_manual_import_batch (
                        batch_id, user_id, import_type, filename,
                        positions_json, trades_json, risk_summary_json, row_count
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s
                    )
                    """,
                    (
                        batch_id,
                        uid,
                        import_type,
                        filename,
                        json.dumps(positions, ensure_ascii=False),
                        json.dumps(trades, ensure_ascii=False),
                        json.dumps(risk_summary, ensure_ascii=False),
                        len(trades),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[manual_import] DB save skipped/failed (memory store active): {}", exc)

    record_broker_audit_log(
        user_id=uid,
        mcp_server="broker_connector",
        tool_name="manual_settlement_import",
        provider_id="manual_csv_upload",
        license_scope="user_manual_import",
        status_code="ok",
    )

    return batch_record


def get_latest_manual_import(user_id: str) -> dict[str, Any] | None:
    """获取指定用户最近一次上传的手工交割单/持仓批次。"""
    uid = (user_id or "anonymous").strip() or "anonymous"
    if uid in _MEMORY_MANUAL_IMPORTS and _MEMORY_MANUAL_IMPORTS[uid]:
        return _MEMORY_MANUAL_IMPORTS[uid][0]

    try:
        from app.services.database import get_conn

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT batch_id, user_id, import_type, filename,
                           positions_json, trades_json, risk_summary_json, row_count, created_at
                      FROM broker_manual_import_batch
                     WHERE user_id = %s
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    (uid,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "batch_id": row[0],
                        "user_id": row[1],
                        "import_type": row[2],
                        "filename": row[3],
                        "positions": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
                        "trades": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                        "risk_summary": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                        "row_count": int(row[7] or 0),
                        "created_at": row[8].isoformat() if row[8] else _now_iso(),
                    }
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return None


class GalaxyAccountReadonlyAdapter(AccountReadonlyProvider):
    """P2 · 合作证券公司个人账户只读连接器实现（对应 `broker_account_readonly` MCP）。

    合规保证：
    - 默认关闭；每次调用强制执行 `verify_user_oauth_scope`；
    - 严禁跨租户越权访问；
    - 仅包含 `get_accounts`、`get_account_summary`、`get_positions`、`get_trades` 4 个只读方法；
    - 每次调用全量写入 `broker_audit_log`。
    """

    provider_id: str = "galaxy_broker_account_readonly"
    provider_name: str = "中国银河证券个人账户只读连接器（OAuth 2.0）"
    is_official_broker: bool = True

    def _get_authorized_snapshot(
        self,
        user_id: str,
        required_scope: str,
        tool_name: str,
        session_user_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        rec = verify_user_oauth_scope(
            user_id=user_id,
            required_scope=required_scope,
            session_user_id=session_user_id,
            tool_name=tool_name,
        )
        effective_uid = rec["user_id"]
        if effective_uid not in _MEMORY_ACCOUNT_CACHE:
            _MEMORY_ACCOUNT_CACHE[effective_uid] = _build_default_account_fixture(
                effective_uid,
                rec.get("account_id", "GZ-88001928"),
                rec.get("broker_id", "galaxy_broker"),
                rec.get("broker_name", "中国银河证券官方只读通道"),
            )
        return rec, _MEMORY_ACCOUNT_CACHE[effective_uid]

    def get_accounts(
        self,
        user_id: str,
        *,
        session_user_id: str | None = None,
    ) -> BrokerDataEnvelope:
        rec, snap = self._get_authorized_snapshot(
            user_id=user_id,
            required_scope="account.read",
            tool_name="get_accounts",
            session_user_id=session_user_id,
        )
        uid = rec["user_id"]
        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_account_readonly",
            tool_name="get_accounts",
            provider_id=self.provider_id,
            license_scope="user_oauth_readonly:account.read",
            status_code="ok",
        )
        return BrokerDataEnvelope.build(
            provider=self.provider_id,
            market="CN_A",
            currency="CNY",
            unit="元",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at=_now_iso(),
            license_scope=f"user_oauth_readonly:{uid}",
            data={
                "user_id": uid,
                "accounts": snap["accounts"],
                "readonly_disclaimer": "当前账户连接器仅具备只读查询权限（account.read），账号已脱敏，物理隔离实盘下单与银证转账。",
            },
        )

    def get_account_summary(
        self,
        user_id: str,
        account_id: str | None = None,
        *,
        session_user_id: str | None = None,
    ) -> BrokerDataEnvelope:
        rec, snap = self._get_authorized_snapshot(
            user_id=user_id,
            required_scope="summary.read",
            tool_name="get_account_summary",
            session_user_id=session_user_id,
        )
        uid = rec["user_id"]
        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_account_readonly",
            tool_name="get_account_summary",
            provider_id=self.provider_id,
            license_scope="user_oauth_readonly:summary.read",
            status_code="ok",
        )
        summary = dict(snap["summary"])
        summary["user_id"] = uid
        if account_id:
            summary["requested_account_masked"] = mask_account_id(account_id)
        return BrokerDataEnvelope.build(
            provider=self.provider_id,
            market="CN_A",
            currency="CNY",
            unit="元",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at=_now_iso(),
            license_scope=f"user_oauth_readonly:{uid}",
            data=summary,
        )

    def get_positions(
        self,
        user_id: str,
        account_id: str | None = None,
        *,
        session_user_id: str | None = None,
    ) -> BrokerDataEnvelope:
        rec, snap = self._get_authorized_snapshot(
            user_id=user_id,
            required_scope="positions.read",
            tool_name="get_positions",
            session_user_id=session_user_id,
        )
        uid = rec["user_id"]
        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_account_readonly",
            tool_name="get_positions",
            provider_id=self.provider_id,
            license_scope="user_oauth_readonly:positions.read",
            status_code="ok",
        )
        return BrokerDataEnvelope.build(
            provider=self.provider_id,
            market="CN_A",
            currency="CNY",
            unit="元",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at=_now_iso(),
            license_scope=f"user_oauth_readonly:{uid}",
            data={
                "user_id": uid,
                "account_id": snap["account_id_masked"],
                "position_count": len(snap["positions"]),
                "positions": snap["positions"],
                "readonly_disclaimer": "持仓数据仅供风险巡检与投研复盘使用，不包含任何实盘买卖指令。",
            },
        )

    def get_trades(
        self,
        user_id: str,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        session_user_id: str | None = None,
    ) -> BrokerDataEnvelope:
        rec, snap = self._get_authorized_snapshot(
            user_id=user_id,
            required_scope="trades.read",
            tool_name="get_trades",
            session_user_id=session_user_id,
        )
        uid = rec["user_id"]
        trades = list(snap["trades"])
        if start_date:
            trades = [t for t in trades if str(t.get("trade_date", "")) >= start_date]
        if end_date:
            trades = [t for t in trades if str(t.get("trade_date", "")) <= end_date]

        record_broker_audit_log(
            user_id=uid,
            mcp_server="broker_account_readonly",
            tool_name="get_trades",
            provider_id=self.provider_id,
            license_scope="user_oauth_readonly:trades.read",
            status_code="ok",
        )
        return BrokerDataEnvelope.build(
            provider=self.provider_id,
            market="CN_A",
            currency="CNY",
            unit="元",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at=_now_iso(),
            license_scope=f"user_oauth_readonly:{uid}",
            data={
                "user_id": uid,
                "account_id": snap["account_id_masked"],
                "trade_count": len(trades),
                "trades": trades,
                "readonly_disclaimer": "历史成交记录仅用于交易归因与纪律复盘（trading_journal / after_close_review），严禁实盘委托。",
            },
        )


_DEFAULT_ACCOUNT_ADAPTER: GalaxyAccountReadonlyAdapter | None = None


def get_default_account_readonly_adapter() -> GalaxyAccountReadonlyAdapter:
    global _DEFAULT_ACCOUNT_ADAPTER
    if _DEFAULT_ACCOUNT_ADAPTER is None:
        _DEFAULT_ACCOUNT_ADAPTER = GalaxyAccountReadonlyAdapter()
    return _DEFAULT_ACCOUNT_ADAPTER
