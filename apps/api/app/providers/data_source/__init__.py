"""Data source provider factory · env-driven singleton.

Set DATA_SOURCE_PROVIDER to one of: hunter · saas · akshare · yfinance
Leave it unset and you get "hunter", whether or not a platform key is
configured (see app.services.hunter_key). Without a key its calls raise
HunterKeyRequired ("go apply for a key") instead of falling back to akshare;
the comment in get_data_source() explains why. For no-key data, set
DATA_SOURCE_PROVIDER=akshare (A-shares) or yfinance (US/HK).

Only caller today: app.services.finance_data_client.get_quote() (via
_provider_get_quote_sync), and only when no data key is configured
(HK/US try the free market_source channel first).
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
        # URL/KEY 的解析顺序见 finance_data_auth；与 finance_data_client /
        # sentinel 共用入口，包含默认网关和数据库里网页填写的 key。
        from app.services import finance_data_auth as _auth
        from app.services import hunter_key
        from .hunter_tools import HunterKeyRequired
        url = _auth.data_url()
        key = _auth.data_token()
        if not key:
            # HunterKeyRequired, not a bare RuntimeError: _provider_get_quote_sync
            # re-raises only this type so the FastAPI handler can turn it into the
            # structured hunter_key_required response instead of a silent None.
            raise HunterKeyRequired(
                "DATA_SOURCE_PROVIDER=saas requires a key. "
                "Set HUNTER_API_KEY (统一 key) or HUNTER_SAAS_DATA_KEY (独立数据 key). "
                "Free-tier: https://hunter.agentpit.io/dev/api-keys",
                hunter_key.APPLY_URL,
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
