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
        # Default to the Hunter gateway even with no key configured. Without a
        # key it raises HunterKeyRequired, which reaches the user as "go apply
        # for a key" — the honest answer. Silently falling back to akshare here
        # produced a much worse experience: akshare is frequently unreachable
        # from inside a container, so the user got "行情暂时无法获取" and had no
        # idea a free key would fix it.
        #
        # akshare / yfinance are still one env var away for anyone who wants
        # no-key data: DATA_SOURCE_PROVIDER=akshare
        provider = "hunter"
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
