"""hunter 网关地址与凭证 · Kronos / TrueSource 的唯一解析入口。

与 finance_data_auth 同款套路,原因也一样:同一套"env 覆盖 → 网关默认 → 带 key"
的逻辑如果在 kpred / backtest.jobs / forecast / truesource / discover 五个文件里
各抄一遍,迟早在某个接缝上对不上(今天已经在 finance-data 上见过一次)。

为什么要走网关:Kronos(136.110.39.14:8000)和 TrueSource(34.92.72.140:8000)
上游是**裸 IP · 无鉴权 · 无 HTTPS**。直连的后果是谁都能打、没有任何计量、
IP 一变全线 502。收编到 hunter.agentpit.io/api/saas/{kronos,truesource}/* 之后:
用户只需一把 HUNTER_API_KEY,裸 IP 对用户完全隐藏。

**老 env 优先级最高**:已经手填 KRONOS_URL / TRUESOURCE_API_URL 的用户完全不受
影响,也可以用它一键回退到直连(网关出问题时的应急通道)。

设计见 hunter 仓库 doc/codex/community/2026-08-14_09-*.md
"""
from __future__ import annotations

import os

from app.services import hunter_key

DEFAULT_KRONOS_GATEWAY = "https://hunter.agentpit.io/api/saas/kronos"
DEFAULT_TRUESOURCE_GATEWAY = "https://hunter.agentpit.io/api/saas/truesource"


def _token() -> str:
    """网关凭证 = 平台 key。env 里没有就查数据库(网页左下角填的那把)。"""
    return hunter_key.resolve() or ""


def kronos_url() -> str:
    """KRONOS_URL(老 env · 可用于回退直连)→ 网关默认。"""
    return (os.getenv("KRONOS_URL") or DEFAULT_KRONOS_GATEWAY).rstrip("/")


def truesource_url() -> str:
    return (os.getenv("TRUESOURCE_API_URL") or DEFAULT_TRUESOURCE_GATEWAY).rstrip("/")


def is_gateway(url: str) -> bool:
    return "/api/saas/" in url


def headers_for(url: str) -> dict:
    """走网关才带 Bearer;直连裸 IP 的上游没有鉴权,带了也没用。"""
    if not is_gateway(url):
        return {}
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def kronos_headers() -> dict:
    return headers_for(kronos_url())


def truesource_headers() -> dict:
    return headers_for(truesource_url())
