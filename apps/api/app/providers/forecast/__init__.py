"""Forecast provider factory · env-driven singleton.

Set FORECAST_PROVIDER to one of:
  - kronos_saas  (default · via hunter gateway · 只需 HUNTER_API_KEY)
  - kronos_local (needs GPU · KRONOS_LOCAL_URL required)
  - noop         (Kronos disabled · UI hides forecast SKILL)
"""
import os
from loguru import logger
from .base import IForecast

_INSTANCE: IForecast | None = None


def get_forecast() -> IForecast:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    # 留空 = kronos_saas(走 hunter 网关)。以前默认 noop 是因为 Kronos 接不上 ——
    # 网关打通后没理由再默认藏起来。没 key 时网关回 401 + 申请引导,
    # 与工具/数据源的行为一致:能力照常显示,点了才提示解锁。
    # 明确不想要的人设 FORECAST_PROVIDER=noop 即可。
    provider = (os.getenv("FORECAST_PROVIDER") or "kronos_saas").lower()
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
        # 默认走 hunter 网关 · 凭统一的 HUNTER_API_KEY,不再需要 URL/KEY 两个专有变量。
        #
        # 原来这里要求 HUNTER_SAAS_KRONOS_URL + HUNTER_SAAS_KRONOS_KEY(hunt_kron_ 前缀),
        # 但**平台从没签发过 hunt_kron_ 这种 key** —— 用户点「Kronos 走势预测」会被
        # 提示去配 key,然后发现根本申请不到,是条死路。
        from app.services import saas_gateway as _gw
        url = os.getenv("HUNTER_SAAS_KRONOS_URL") or _gw.kronos_url()
        key = os.getenv("HUNTER_SAAS_KRONOS_KEY") or _gw._token()
        from .kronos_http import KronosHTTPForecast
        _INSTANCE = KronosHTTPForecast(url, api_key=key, model="kronos-saas")
    else:
        raise RuntimeError(
            f"unknown FORECAST_PROVIDER={provider!r} · "
            "expected one of: noop | kronos_local | kronos_saas"
        )
    return _INSTANCE


__all__ = ["IForecast", "get_forecast"]
