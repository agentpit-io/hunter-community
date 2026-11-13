"""证券公司数据接口适配层（统一数据信封 + 8 大业务域抽象 Provider）。"""
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

__all__ = [
    "AccountReadonlyProvider",
    "AdjustmentType",
    "BaseBrokerProvider",
    "BrokerDataEnvelope",
    "BrokerDataMeta",
    "BrokerEnvelopeError",
    "DisclosureProvider",
    "EducationProvider",
    "FundamentalProvider",
    "FundsProvider",
    "MarketCode",
    "MarketDataProvider",
    "QualityStatus",
    "ResearchProvider",
    "SecurityMasterProvider",
    "format_iso_dt",
]
