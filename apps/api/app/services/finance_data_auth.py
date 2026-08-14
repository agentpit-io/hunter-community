"""finance-data 访问凭证 · 唯一解析入口。

原来这套"URL 三级 + TOKEN 三级"的 fallback 在四个文件里各抄了一遍
(finance_data_client / sentinel.unified_fetcher / providers.data_source /
online_analysis.unified_fetcher)。抄多份的直接后果是**接缝处对不上**:

  · 用户在网页左下角填的 key 存在数据库(hunter_config 表),
    但这几处只 os.getenv,看不见它 —— 于是行情工具有数据、深度分析没有,
    而且不报错,只是静默降级到免费源,用户完全看不出跟 key 有关。
  · 四份都是**模块导入时**求值,网页填完 key 必须重启容器才生效,
    与 README §5 承诺的"立即生效,不用重启"矛盾。

所以收敛成这一个模块,并且**调用时求值**。

解析优先级(与之前完全一致,只在最后多接了一层):

    URL   : FINANCE_DATA_URL → HUNTER_SAAS_DATA_URL → hunter 网关(默认)
    TOKEN : FINANCE_DATA_TOKEN → HUNTER_SAAS_DATA_KEY → hunter_key.resolve()
                                                         └ 它自己是 env→DB 两级

即:显式的老变量名永远优先,老用户的配置不受任何影响;新增的只是"env 里都没有
时,去数据库看一眼用户有没有在网页里填过"。
"""
from __future__ import annotations

import os

from app.services import hunter_key

# 默认走 hunter 网关中转 —— finance-data 只认内部共享 token,不对外发放,
# 网关负责校验用户 key 再补上内部 token。见 hunter 仓库 routers/saas_data.py。
DEFAULT_GATEWAY_URL = "https://hunter.agentpit.io/api/saas/data"


def data_url() -> str:
    return (
        os.getenv("FINANCE_DATA_URL")
        or os.getenv("HUNTER_SAAS_DATA_URL")
        or DEFAULT_GATEWAY_URL
    ).rstrip("/")


def data_token() -> str:
    """当前生效的凭证。env 里没有时回落到数据库(网页填的那把)。"""
    return (
        os.getenv("FINANCE_DATA_TOKEN")
        or os.getenv("HUNTER_SAAS_DATA_KEY")
        or hunter_key.resolve()          # env HUNTER_API_KEY → hunter_config 表
        or ""
    )


def is_gateway(url: str | None = None) -> bool:
    """走网关还是直连 finance-data —— 决定用哪种 auth 头。"""
    return "/api/saas/data" in (url if url is not None else data_url())


def data_headers() -> dict:
    """网关认 Bearer,直连 finance-data 认 X-Finance-Token。没凭证就返空。"""
    token = data_token()
    if not token:
        return {}
    if is_gateway():
        return {"Authorization": f"Bearer {token}"}
    return {"X-Finance-Token": token}


def use_saas() -> bool:
    """有凭证就试 SaaS 路径;没有则调用方走本地免费源(akshare/yfinance)。"""
    return bool(data_token())
