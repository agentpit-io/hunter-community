"""证券公司数据接口适配层（Broker Data Provider）。

包含：
- `envelope.py`：统一数据信封（`BrokerDataEnvelope`、`BrokerDataMeta`、`BrokerEnvelopeError`）
- `base.py`：8 大业务域只读 Provider 抽象协议（对应 8 个 `broker_*` MCP 服务）
- `galaxy_adapter.py`：合作证券公司标准只读适配器（`GalaxyBrokerAdapter`）
- `public_adapter.py`：真实公开数据源与本地数据库备用适配器（`PublicFallbackAdapter`）
- `router.py`：多源主备自动切换路由器（`BrokerDataRouter`，严禁跨源取均值）
"""
from .account_readonly import (
    BrokerAccountUnauthorizedError,
    GalaxyAccountReadonlyAdapter,
    VALID_READONLY_SCOPES,
    get_broker_authorization_status,
    get_default_account_readonly_adapter,
    get_latest_manual_import,
    grant_broker_readonly_authorization,
    list_broker_audit_logs,
    mask_account_id,
    parse_and_save_manual_settlement_csv,
    record_broker_audit_log,
    revoke_broker_readonly_authorization,
    verify_user_oauth_scope,
)
from .base import (
    AccountReadonlyProvider,
    BaseBrokerProvider,
    DisclosureProvider,
    EducationProvider,
    FundamentalProvider,
    FundsProvider,
    MarketDataProvider,
    ResearchProvider,
    SecurityMasterProvider,
)
from .envelope import (
    AdjustmentType,
    BrokerDataEnvelope,
    BrokerDataMeta,
    BrokerEnvelopeError,
    MarketCode,
    QualityStatus,
    format_iso_dt,
)
from .galaxy_adapter import (
    BrokerProviderUnavailableError,
    GalaxyBrokerAdapter,
    normalize_symbol,
)
from .public_adapter import (
    PublicFallbackAdapter,
    build_deterministic_kline_series,
)
from .router import (
    BrokerDataRouter,
    CrossSourceAveragingForbiddenError,
    assert_no_cross_source_averaging,
    get_default_broker_router,
)

__all__ = [
    "AdjustmentType",
    "MarketCode",
    "QualityStatus",
    "BrokerDataEnvelope",
    "BrokerDataMeta",
    "BrokerEnvelopeError",
    "format_iso_dt",
    "BaseBrokerProvider",
    "MarketDataProvider",
    "SecurityMasterProvider",
    "FundamentalProvider",
    "DisclosureProvider",
    "FundsProvider",
    "ResearchProvider",
    "EducationProvider",
    "AccountReadonlyProvider",
    "BrokerProviderUnavailableError",
    "GalaxyBrokerAdapter",
    "normalize_symbol",
    "PublicFallbackAdapter",
    "build_deterministic_kline_series",
    "BrokerDataRouter",
    "CrossSourceAveragingForbiddenError",
    "assert_no_cross_source_averaging",
    "get_default_broker_router",
    "BrokerAccountUnauthorizedError",
    "GalaxyAccountReadonlyAdapter",
    "VALID_READONLY_SCOPES",
    "get_broker_authorization_status",
    "get_default_account_readonly_adapter",
    "get_latest_manual_import",
    "grant_broker_readonly_authorization",
    "list_broker_audit_logs",
    "mask_account_id",
    "parse_and_save_manual_settlement_csv",
    "record_broker_audit_log",
    "revoke_broker_readonly_authorization",
    "verify_user_oauth_scope",
]
