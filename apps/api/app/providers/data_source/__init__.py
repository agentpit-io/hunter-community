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
        # URL/KEY 三级 fallback · 与 finance_data_client / unified_fetcher 一致。
        # 只填 HUNTER_API_KEY 就自动通往官方 finance-data.agentpit.io。
        _DEFAULT_SAAS_URL = "https://finance-data.agentpit.io"
        url = (
            os.getenv("HUNTER_SAAS_DATA_URL")
            or os.getenv("FINANCE_DATA_URL")
            or _DEFAULT_SAAS_URL
        )
        key = (
            os.getenv("HUNTER_SAAS_DATA_KEY")
            or os.getenv("FINANCE_DATA_TOKEN")
            or os.getenv("HUNTER_API_KEY", "")
        )
        if not key:
            raise RuntimeError(
                "DATA_SOURCE_PROVIDER=saas requires a key. "
                "Set HUNTER_API_KEY (统一 key) or HUNTER_SAAS_DATA_KEY (独立数据 key). "
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
