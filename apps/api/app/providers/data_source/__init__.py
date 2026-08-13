"""Data source provider factory · env-driven singleton.

Set DATA_SOURCE_PROVIDER to one of: hunter · saas · akshare · yfinance
Leave it unset and we pick for you: "hunter" when a platform key is configured
(see app.services.hunter_key), else "akshare" (A-shares out-of-box, no key).
"""
import os
from loguru import logger
from .base import IDataSource

_INSTANCE: IDataSource | None = None


def get_data_source() -> IDataSource:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    provider = (os.getenv("DATA_SOURCE_PROVIDER") or "").lower()
    if not provider:
        # No explicit choice: if a Hunter key is around, the user clearly wants
        # the unlocked path. Otherwise fall back to akshare (A-shares, no key).
        from app.services import hunter_key
        provider = "hunter" if hunter_key.resolve() else "akshare"
    logger.info("[providers.data_source] loading provider={}", provider)

    if provider == "hunter":
        from .hunter_tools import HunterToolsDataSource
        _INSTANCE = HunterToolsDataSource()
    elif provider == "saas":
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
            "expected one of: hunter | saas | akshare | yfinance"
        )
    return _INSTANCE


__all__ = ["IDataSource", "get_data_source"]
