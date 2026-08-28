"""下载通道 —— 用户选"从哪里下"。

方案见 doc/开源hunter-community/01详细工作目录/12独立数据源测试和优化/
      06_20260828_下载通道三选一_方案.md

## 三条通道

    free      腾讯 / 新浪            免费 · 最长 3.25 年(801 根)
    agentpit  AgentPit 平台付费源     需平台 Key · 最长约 2 年 · 消耗配额
    custom    用户自己填 API + Key    额度算用户的

## 为什么免费的历史反而更长

实测(2026-08-28):

    腾讯免费源   一次给 801 根 ≈ 3.25 年
    XTick 付费   库里 5648 只平均每只 518 行 ≈ 2.12 年

XTick 套餐说明也印证:「历史数据只能调用近 1 个月;订阅 12 个月以上,
Tick/分钟历史调用近两年」。

**所以界面上直接标出来,不藏。** 用户付了费拿到更少的数据、
回头来问,那时候再解释就成了狡辩。

## Key 绝不落日志

这个项目踩过:`check_xtick.py` 里硬编码 token,任何人 cat 一下就看到。
这里 Key 只在内存里传,日志一律打 `***`。
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

FREE = "free"
AGENTPIT = "agentpit"
CUSTOM = "custom"
VALID = (FREE, AGENTPIT, CUSTOM)

# 各通道的历史深度上限(月)· 用于前端提示和预估
SPAN_CAP = {
    FREE: 39,        # 801 根 ≈ 3.25 年
    AGENTPIT: 24,    # 实测平均 2.12 年
    CUSTOM: None,    # 看用户的源,我们不知道
}


def mask(s: str | None) -> str:
    """Key 打码 —— 只用于日志和错误信息。"""
    if not s:
        return "(空)"
    return s[:4] + "***" + s[-2:] if len(s) > 8 else "***"


def normalize(source: str | None) -> str:
    """不认识的一律回落 free —— **默认行为不变**,
    老调用方不传 source 时和以前完全一样。"""
    s = (source or FREE).strip().lower()
    return s if s in VALID else FREE


def validate(source: str, custom: dict | None) -> dict | None:
    """开始前把能查的都查掉。返回 None 表示通过,否则返回错误。

    **在这里挡住,而不是让任务跑起来再失败** —— 跑到一半失败的话,
    用户已经等了几分钟,而且库里留下半截数据。
    """
    if source == CUSTOM:
        url = (custom or {}).get("url", "").strip()
        key = (custom or {}).get("key", "").strip()
        if not url or not key:
            return {"error": "missing_credential",
                    "message": "选了「我自己的数据源」就要填 API 地址和 Key"}
        if not url.startswith(("http://", "https://")):
            return {"error": "bad_url",
                    "message": f"API 地址要以 http:// 或 https:// 开头,你填的是「{url[:40]}」"}
    elif source == AGENTPIT:
        # 平台 Key 从环境变量读,不让用户填 —— 用户填的话等于把 Key 散出去
        import os
        if not os.getenv("HUNTER_API_KEY") and not os.getenv("AGENTPIT_API_KEY"):
            return {"error": "no_platform_key",
                    "message": "还没配置平台 Key —— 在 .env 里设 HUNTER_API_KEY 后重启"}
    return None


def test_connection(url: str, key: str) -> dict:
    """测试用户填的源通不通。

    **这个按钮是必须的。** 不测的话,Key 填错要跑十分钟才发现 ——
    而那十分钟里用户不知道是在下载还是卡住了。
    """
    import requests
    u = url.rstrip("/")
    # 试几个常见的探活路径。**不猜业务接口** —— 猜错会打出一堆 404,
    # 而且可能消耗用户的配额
    for path in ("/doc/quota", "/health", "/api/health", "/"):
        try:
            r = requests.get(u + path, params={"token": key},
                             timeout=10, headers={"User-Agent": "hunter/1.0"})
            if r.status_code == 200:
                body = (r.text or "")[:200]
                # 有些源用 HTTP 200 装错误(XTick 就是),照实报出来
                if '"code":-1' in body or '"code": -1' in body:
                    return {"ok": False,
                            "message": f"连上了,但对方返回错误:{body[:120]}"}
                return {"ok": True, "message": f"连接正常({path} 返回 200)"}
        except Exception:                                     # noqa: BLE001
            continue
    log.info("[download_source] 自定义源测试失败 url=%s key=%s", u, mask(key))
    return {"ok": False,
            "message": "连不上 —— 检查 API 地址是否正确、Key 是否有效、"
                       "以及这台机器能不能访问那个地址"}


def fetch_daily(code: str, start: date, end: date, *,
                source: str = FREE, custom: dict | None = None,
                market: str = "A") -> list[dict]:
    """按通道取日线。返回和 local_kline.fetch_daily 一致的结构。"""
    if source == CUSTOM:
        return _fetch_custom(code, start, end, custom or {})
    if source == AGENTPIT:
        return _fetch_agentpit(code, start, end)
    from app.services.quant import local_kline
    return local_kline.fetch_daily(code, start, end)


def _fetch_agentpit(code: str, start: date, end: date) -> list[dict]:
    """走 AgentPit 平台的付费源。

    ⚠️ 这条通道**尚未接通** —— 需要先解决两件非技术问题:
        1. XTick 的商业授权(官方 FAQ:商用需联系获取授权),
           把它的数据转给我们的用户属于转售
        2. 配额规划:全 A 股一次下载 = 5534 次调用,而白银版每天
           只有 2 万次,我们自己的采集已经用掉 1.6 万

    在这两件事定下来之前,**明确返回空并说明原因**,
    而不是悄悄回落免费源 —— 用户选了付费通道却拿到免费源的数据,
    那是骗人。
    """
    log.warning("[download_source] agentpit 通道尚未接通 · code=%s", code)
    return []


def _fetch_custom(code: str, start: date, end: date, custom: dict) -> list[dict]:
    """走用户自己的源。

    目前只支持 XTick 兼容格式(我们自己在用,格式熟)。
    其它源要靠 source_templates 里的模板,那是另一个工程。
    """
    import requests
    url = (custom.get("url") or "").rstrip("/")
    key = custom.get("key") or ""
    if not url or not key:
        return []
    try:
        r = requests.get(f"{url}/doc/kline/market",
                         params={"token": key, "type": 1, "code": code,
                                 "period": "1d", "fq": "none",
                                 "startDate": start.isoformat(),
                                 "endDate": end.isoformat()},
                         timeout=25, headers={"User-Agent": "hunter/1.0"})
        data = r.json()
    except Exception as e:                                    # noqa: BLE001
        log.warning("[download_source] 自定义源取数失败 %s · key=%s · %s",
                    code, mask(key), e)
        return []
    # 对方可能用 HTTP 200 装错误
    if isinstance(data, dict):
        msg = str(data.get("message") or "")
        if msg:
            log.warning("[download_source] 自定义源返回错误 · %s · key=%s", msg, mask(key))
        return []
    if not isinstance(data, list):
        return []
    out = []
    for b in data:
        try:
            t = str(b.get("time") or b.get("date") or "")[:10]
            c = float(b.get("close") or 0)
            if not t or c <= 0:
                continue
            out.append({"ts": t, "open": float(b.get("open") or 0),
                        "high": float(b.get("high") or 0),
                        "low": float(b.get("low") or 0), "close": c,
                        "volume": int(float(b.get("volume") or 0))})
        except (ValueError, TypeError):
            continue
    return out
