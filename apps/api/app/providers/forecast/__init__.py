"""Forecast provider factory · env-driven singleton.

Set FORECAST_PROVIDER to one of:
  - noop         (default · Kronos disabled · UI hides forecast SKILL)
  - kronos_local (needs GPU · KRONOS_LOCAL_URL required)
  - kronos_saas  (via hunter's managed endpoint · needs HUNTER_SAAS_KRONOS_URL/_KEY)
"""
import os
from loguru import logger
from .base import IForecast

_INSTANCE: IForecast | None = None


def get_forecast() -> IForecast:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    provider = (os.getenv("FORECAST_PROVIDER") or "noop").lower()
    logger.info("[providers.forecast] loading provider={}", provider)

    if provider == "noop":
        from .noop import NoopForecast
        _INSTANCE = NoopForecast()
    elif provider == "kronos_local":
        url = os.getenv("KRONOS_LOCAL_URL")
        if not url:
            raise RuntimeError(
                "FORECAST_PROVIDER=kronos_local requires KRONOS_LOCAL_URL"
            )
        from .kronos_http import KronosHTTPForecast
        _INSTANCE = KronosHTTPForecast(url, api_key="", model="kronos-local")
    elif provider == "kronos_saas":
        url = os.getenv("HUNTER_SAAS_KRONOS_URL")
        key = os.getenv("HUNTER_SAAS_KRONOS_KEY", "")
        if not url:
            raise RuntimeError(
                "FORECAST_PROVIDER=kronos_saas requires HUNTER_SAAS_KRONOS_URL · "
                "free-tier: https://hunter.agentpit.io/dev/api-keys"
            )
        from .kronos_http import KronosHTTPForecast
        _INSTANCE = KronosHTTPForecast(url, api_key=key, model="kronos-saas")
    else:
        raise RuntimeError(
            f"unknown FORECAST_PROVIDER={provider!r} · "
            "expected one of: noop | kronos_local | kronos_saas"
        )
    return _INSTANCE


__all__ = ["IForecast", "get_forecast"]
