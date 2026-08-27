"""个股日线 —— **不经过任何我们自己的服务**。

## 为什么要有这个模块

量化的 K 线原来全部走 `finance_data_client`,而它默认指向
`https://hunter.agentpit.io/api/saas/data`,要我们的 `HUNTER_API_KEY`。
开源用户拿掉 key,量化整块归零 —— 这和「脱离我们也能用」是反的。

取数顺序:

    用户在「数据源」页配的 kline 源  →  腾讯直连  →  明确拿不到

**不回落到我们的网关。** 用户真想用我们的服务,数据源页本身就支持
填任意 URL,由他自己加,而不是代码替他默认连上。

## 腾讯这条实测(2026-08-21)

- 一次 800 条 = 2023-05-08 ~ 2026-08-21,三年多
- 12 只压测 4.8 秒全部成功,按此速度 300 只约 2 分钟
- 免 key、零 header
"""
from __future__ import annotations

import logging
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

_TENCENT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_UA = {"User-Agent": "Mozilla/5.0"}


# 北交所 —— **免费源都给不了历史日线,只能排除**。
#
# 实测(2026-08-27,以 920000 安徽凤凰为例):
#   腾讯 bj920000   只返回当天 1 条,而且是 day 不是 qfqday(连复权都没有);
#                   换 n=60/300/1500、带不带 qfq 都一样
#   腾讯 sh/sz920000  0 条(前缀本来就不对)
#   东财 secid=0.920000  第一次拿到 643 条,之后三次重试全 RemoteDisconnected
#                   —— 和指数日线、行业板块是同一个毛病,不可靠
#   新浪 klc_kl.js  是自定义二进制编码,不是 JSON,要逆向
#
# 而 stocks_catalog_baseline.json 里有 331 只 920xxx,且 exchange 字段
# **错标成 SZ**。不排除的话:每次全 A 下载必然失败 331 次,而且失败的票
# 不写 coverage → 下次续跑又重试一遍,永远失败永远重试。
#
# 测试人员实测 511 次失败里,331 次就是这个。
_BJ_PREFIXES = ("4", "8", "92")


def is_unsupported(code: str) -> bool:
    """这只票我们拿不到历史日线 —— 调用方应当跳过并**说明原因**,
    而不是当成普通失败混在失败计数里。"""
    c = str(code).zfill(6)
    return c.startswith(_BJ_PREFIXES)


def _prefixed(code: str) -> str:
    """A 股代码加交易所前缀。6/9 开头是沪市,其余(0/2/3)是深市。

    ⚠ 这个规则**不适用于北交所**(4/8/92 开头)—— 它们要 bj 前缀,
    而且即使用对前缀腾讯也只给当天一条。用 is_unsupported() 先挡掉。
    """
    c = str(code).zfill(6)
    return ("sh" if c[0] in "69" else "sz") + c


# 被限流时把重试关掉 —— 见 fetch_daily 的说明
_RETRY_ON = True


def set_retry(on: bool) -> None:
    """worker 检测到限流时调 set_retry(False)。

    3 次重试对付**偶发失败**是对的,但遇到限流是灾难:
    每只失败都重试 3 次 = 请求量放大 3 倍 = 持续刺激上游。
    实测服务器那次在"全败"状态下硬跑了两千多只,每只打 3 次。
    """
    global _RETRY_ON
    _RETRY_ON = on


def _from_tencent(code: str, start: date, end: date, adjust: str = "qfq") -> list[dict]:
    """腾讯日线。

    **字段顺序是 [date, open, close, high, low, volume]** —— close 排在 high
    前面,和直觉相反。搞错会把开盘价当收盘价存进去,而这种错在回测结果里
    完全看不出来:数字都在合理范围,曲线照样能画,只是全错。

    所以下面校验 `high >= max(open, close)` 且 `low <= min(open, close)`。
    上游哪天改了顺序,这里直接判空,而不是安静地存错。
    """
    q = _prefixed(code)
    # ⚠ **上限 800,不是 1500。**
    #
    # 实测同一只票要不同天数(600519):
    #     请求  300 → 得 301   2025-06 起
    #     请求  800 → 得 801   2023-05 起   ← 最优
    #     请求 1200 → 得 641   2024-01 起   ← 反而变少
    #     请求 1500 → 得 641
    #     请求 3000 → 得   0   直接空
    #
    # 要多了反而给少。之前按 1500 请求,每只只拿到 641 条(2.5 年),
    # 服务器那次的 2016-2019 四个分卷因此全是空的。
    days = max(200, min(800, (end - start).days + 60))
    # 连着打会被限流 —— 实测批量跑 800 只时清一色 ReadTimeout,
    # 而单独拉一只完全正常。失败了退避重试,不是直接放弃:
    # 放弃的表现是"这只票没有数据",和真的没有数据分不开
    raw = []
    for attempt in range(3 if _RETRY_ON else 1):
        try:
            r = requests.get(_TENCENT, params={"param": f"{q},day,,,{days},{adjust}"},
                             headers=_UA, timeout=30)
            if r.status_code == 200:
                node = (r.json().get("data") or {}).get(q) or {}
                raw = node.get(f"{adjust}day") or node.get("day") or []
                break
        except Exception as e:                                # noqa: BLE001
            if attempt == 2:
                log.warning("[local_kline] 腾讯拉 %s 失败(重试 3 次): %s",
                            code, type(e).__name__)
        time.sleep(0.6 * (attempt + 1))
    if not raw:
        return []

    lo, hi = start.isoformat(), end.isoformat()
    out = []
    for x in raw:
        if len(x) < 6:
            continue
        ts = str(x[0])[:10]
        if not (lo <= ts <= hi):
            continue
        try:
            o, c, h, l = float(x[1]), float(x[2]), float(x[3]), float(x[4])
            v = float(x[5])
        except (TypeError, ValueError):
            continue
        if not (h >= max(o, c) and l <= min(o, c)):
            log.error("[local_kline] %s %s 的 OHLC 不自洽 —— 上游字段顺序可能变了,"
                      "不猜着解析", code, ts)
            return []
        out.append({"ts": ts, "open": o, "high": h, "low": l,
                    "close": c, "volume": v})
    return out


def _from_user_source(code: str, user_id: str | None) -> list[dict]:
    """用户自己配的 kline 源。没配 / 拿不到都返回 []。

    `try_user()` 靠 contextvar 取 user_id,而回填脚本和后台任务里
    **没有请求上下文** —— 这正是「数据源页配好了,量化却用不上」的原因。
    所以这里显式把 user_id 塞进 contextvar 再调。
    """
    if not user_id:
        return []
    try:
        from app.services import request_ctx, source_resolver
    except ImportError:
        return []
    prev = request_ctx.user_id()
    request_ctx.set_user(user_id)
    try:
        hit = source_resolver.try_user("A", "kline", code)
        rows = (hit or {}).get("rows") or []
        out = []
        for r in rows:
            ts = str(r.get("ts") or r.get("date") or "")[:10]
            c = r.get("close")
            if not ts or c is None:
                continue
            out.append({"ts": ts, "open": r.get("open"), "high": r.get("high"),
                        "low": r.get("low"), "close": c,
                        "volume": r.get("volume") or 0})
        return out
    except Exception as e:                                    # noqa: BLE001
        log.warning("[local_kline] 用户源取 %s 失败: %s", code, type(e).__name__)
        return []
    finally:
        # 恢复原值 —— 这个函数可能被跑在共享的事件循环里,
        # 把别人的 user_id 覆盖掉会让另一个请求悄悄用错源
        request_ctx.set_user(prev)


def fetch_daily(code: str, start: date, end: date,
                user_id: str | None = None) -> list[dict]:
    """日线 · 用户源优先 · 腾讯兜底 · 都没有就返回空列表。

    返回 [] 表示**真的没拿到**,调用方应当据此跳过这只票并计入失败,
    而不是当成"这只票这段时间没交易"。
    """
    rows = _from_user_source(code, user_id)
    if rows:
        return [r for r in rows if start.isoformat() <= r["ts"] <= end.isoformat()]
    return _from_tencent(code, start, end)
