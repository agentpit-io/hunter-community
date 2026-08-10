"""Data source provider factory · env-driven singleton.

Set DATA_SOURCE_PROVIDER to one of: saas · akshare · yfinance
Default: akshare (works out-of-box for A-shares · no key needed).
"""
import os
from loguru import logger
from .base import IDataSource

_INSTANCE: IDataSource | None = None


def get_data_source() -> IDataSource:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    provider = (os.getenv("DATA_SOURCE_PROVIDER") or "akshare").lower()
    logger.info("[providers.data_source] loading provider={}", provider)

    if provider == "saas":
        url = os.getenv("HUNTER_SAAS_DATA_URL", "")
        key = os.getenv("HUNTER_SAAS_DATA_KEY", "")
        if not url:
            raise RuntimeError(
                "DATA_SOURCE_PROVIDER=saas requires HUNTER_SAAS_DATA_URL. "
                "Free-tier: https://hunter.agentpit.io/dev/api-keys"
            )
        from .saas import SaasDataSource
        _INSTANCE = SaasDataSource(url, key)
    elif provider == "akshare":
        from .akshare_impl import AkshareDataSource
        _INSTANCE = AkshareDataSource()
    elif provider == "yfinance":
        from .yfinance_impl import YFinanceDataSource
        _INSTANCE = YFinanceDataSource()
    else:
        raise RuntimeError(
            f"unknown DATA_SOURCE_PROVIDER={provider!r} · "
            "expected one of: saas | akshare | yfinance"
        )
    return _INSTANCE


__all__ = ["IDataSource", "get_data_source"]
