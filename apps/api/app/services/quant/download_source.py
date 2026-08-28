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
        import os
        # 总开关。**默认开** —— 通道本身是通的(走我们的 SaaS 数据网关),
        # 要临时关掉(比如上游出问题、或者商务上还没准备好对外)
        # 在 .env 里设 AGENTPIT_CHANNEL=off,不用改代码不用重新部署。
        if (os.getenv("AGENTPIT_CHANNEL") or "on").lower() in ("off", "0", "false"):
            return {"error": "channel_off",
                    "message": "「AgentPit 高速通道」当前已关闭。"
                               "请用「本地免费源」或填你自己的数据源。"}

        # 单次上限。挡住"一次拉全 A 股"这种极端请求 —— 不是因为配额不够
        # (配额可以加),而是**一次 5534 只跑下来要很久**,
        # 中途任何波动都要从头再来。大批量请用数据包。
        cap = int(os.getenv("AGENTPIT_MAX_STOCKS") or 2000)
        n = int((custom or {}).get("_stocks") or 0)
        if n > cap:
            return {"error": "too_many",
                    "message": f"高速通道单次最多 {cap} 只,你选了 {n} 只。"
                               f"这么大的量建议用「从数据包导入」——"
                               f"那是我们打好的包,秒级导入,不用一只一只拉。"}
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
    """走 AgentPit 的 SaaS 数据网关。

    实测(2026-08-28)它给的比免费源还多:

        600519.SH   901 根   2023-05-15 ~ 2026-08-28   (3.5 年)
        000001.SZ   903 根
        00700.HK    511 根

    比腾讯免费源的 801 根多 100 根。**所以界面上"最长 2 年"那句
    要改** —— 那个数字是我从 XTick 套餐说明推的,而实际走网关拿到的
    是 3.5 年。推测出来的数字不如实测的准。

    **拿不到就返回空,不偷偷回落免费源** —— 用户选了这条通道却拿到
    免费源的数据,他会以为这条通道就这个水平,而且他可能是付了费的。
    """
    from app.services import finance_data_client as fdc
    sym = fdc.to_symbol(code)
    if not sym:
        log.warning("[download_source] agentpit 认不出代码 %s", code)
        return []
    try:
        data = fdc._get(f"/api/v1/kline/{sym}",
                        {"tf": "1d", "range": "all", "fq": "front"})
    except Exception as e:                                    # noqa: BLE001
        log.warning("[download_source] agentpit 取数失败 %s · %s", code, e)
        return []
    if not isinstance(data, list) or not data:
        return []
    s_iso, e_iso = start.isoformat(), end.isoformat()
    out = []
    for b in data:
        ts = str(b.get("ts") or "")[:10]
        # 网关一次给全历史,按调用方要的区间裁 —— 不裁的话
        # 会把用户只要 1 年的请求写进 3.5 年的数据,磁盘估算全不准
        if not ts or ts < s_iso or ts > e_iso:
            continue
        try:
            c = float(b.get("close") or 0)
            if c <= 0:
                continue
            out.append({"ts": ts, "open": float(b.get("open") or 0),
                        "high": float(b.get("high") or 0),
                        "low": float(b.get("low") or 0), "close": c,
                        "volume": int(float(b.get("volume") or 0))})
        except (ValueError, TypeError):
            continue
    return out


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
