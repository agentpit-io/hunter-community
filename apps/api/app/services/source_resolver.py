"""数据源解析链 —— 「优先用户的 → 失败降级官方 → 标明用了谁」(`_21` §6)。

**这是整个 `_21` 里真正的架构改动。**在这之前取数走的是
`providers/data_source/__init__.py` 的 `get_data_source()`,那是个
**全局 env 单例**:

    _INSTANCE = None
    provider = os.getenv("DATA_SOURCE_PROVIDER") or "hunter"

三个问题,每一个都直接挡住"用户脱离我们也能玩转":
  1. 没有 user_id → 无法"优先用户自己的"
  2. 一次只选一个 → 没有"失败了降级"的概念,只有"启动时选定"
  3. 进程级单例 → A 用户配的源会影响 B 用户

**这里采用的是"加一层在前面"而不是"重写取数层"。** 理由:

    用户没配任何源时(现在 100% 的情况),这一层直接放行,
    走的还是原来那条久经使用的路径,行为**逐字节不变**。

重写 `finance_data_client` 里那十几个函数才能做到"统一降级",
但那会把一条从没出过问题的热路径整个换掉,换来的是当下没人用到的能力。
等真有用户配了源、跑出问题了,再谈重写。

## 熔断为什么必须有

降级链是"用户的失败了才走我们的"。没有熔断的话,用户配了个连不上的源,
**每一次请求**都要先卡满超时再降级 —— 表现是"整个平台变慢了",
而根因藏在一个他自己填错的地址里,极难联想。

熔断状态存在库里(`fail_streak` / `cooldown_until`)而不是进程内存:
多 worker 部署时进程内存各算各的,一个源要被 N 个 worker 分别熔断 N 次。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from app.services import request_ctx, source_mapping
from app.services.mcp_crypto import decrypt
from app.services.request_ctx import SourceUse

# 连续失败几次进冷却 · 冷却多久
FAIL_THRESHOLD = 3
COOLDOWN_MIN = 10


@dataclass
class UserSource:
    id: int
    name: str
    upstream: str
    endpoint: str
    requires_key: bool
    key_in: str
    key_name: str
    key_prefix: str
    key_enc: str | None
    headers: dict
    field_map: dict
    timeout_ms: int
    # `_24`:映射要按市场区分单位(腾讯 A股成交额是万元、港股是元)。
    # 放最后并给默认值 —— `UserSource(*r)` 是按位置构造的,
    # 插在中间会把后面所有字段错位一格,而那种错不报错、只是值全乱
    market: str = ""
    # HTTP 动词。多数源是 GET;巨潮公告只吃 POST(实测 GET→500)。
    # 同 market —— 放最后并给默认值,`UserSource(*r)` 是按位置构造的
    http_method: str = "GET"
    # 单地址 RPC 的 body 模板 —— Tushare 四个接口共用一个地址,
    # 靠 body 里的 api_name 区分。值里的占位符走 expand() 同一套
    body_tpl: dict = field(default_factory=dict)
    # 备用地址 —— 主地址连不上时依次试。东财分片会轮换,见
    # source_templates.Endpoint.alt_urls
    alt_urls: list = field(default_factory=list)


def _candidates(uid: str, market: str, kind: str) -> list[UserSource]:
    """当前用户在这个 (市场,类型) 槽位上**可用**的源。

    SQL 里就把冷却中的排除掉 —— 在 Python 里过滤的话,冷却判断会散在
    调用方,早晚有一处忘了。
    """
    try:
        from app.services.database import get_conn
    except Exception:
        return []
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, upstream, endpoint, requires_key, key_in, key_name, "
            "       key_prefix, api_key_enc, headers, field_map, timeout_ms, market, http_method, body_tpl, alt_urls "
            "FROM user_data_sources "
            "WHERE user_id=%s AND market=%s AND kind=%s AND enabled "
            "  AND (cooldown_until IS NULL OR cooldown_until < NOW()) "
            "ORDER BY updated_at DESC",
            (uid, market, kind),
        )
        rows = cur.fetchall()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[resolver] 查用户源失败(按无处理): {}", e)
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return [UserSource(*r) for r in rows]


def _mark(sid: int, ok: bool, err: str = "") -> None:
    """记一次调用结果 —— 成功清零,失败累加并可能进冷却。"""
    try:
        from app.services.database import get_conn
    except Exception:
        return
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        if ok:
            cur.execute(
                "UPDATE user_data_sources SET fail_streak=0, cooldown_until=NULL, "
                "last_ok_at=NOW(), last_err='', call_count=call_count+1 WHERE id=%s",
                (sid,),
            )
        else:
            # 冷却时间在 SQL 里算,避免 Python 与数据库时区不一致 ——
            # asyncpg/psycopg 那边为时区问题已经栽过一次
            cur.execute(
                "UPDATE user_data_sources SET fail_streak=fail_streak+1, "
                "  last_err=%s, call_count=call_count+1, error_count=error_count+1, "
                "  cooldown_until = CASE WHEN fail_streak+1 >= %s "
                "                        THEN NOW() + (%s || ' minutes')::interval "
                "                        ELSE cooldown_until END "
                "WHERE id=%s",
                (err[:400], FAIL_THRESHOLD, str(COOLDOWN_MIN), sid),
            )
        conn.commit()
    except Exception as e:                                    # noqa: BLE001
        logger.warning("[resolver] 记录调用结果失败: {}", e)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def expand(endpoint: str, code: str) -> str:
    """把地址里的占位符换成这个上游要的代码形式。

    **只支持 `{symbol}` 是不够的。**同一只茅台,各家要的写法完全不同:

        东财     secid=1.600519      ← 1=沪 0=深,前缀跟交易所走
        Tushare  ts_code=600519.SH
        Yahoo    600519.SS / 0700.HK
        裸代码   600519

    只给 `{symbol}` 的话,用户接东财时得自己想办法拼出那个 `1.` 前缀 ——
    而它是**按股票变的**(60/68 开头是沪、00/30 是深),没法写死在地址里。
    等于"选了已知来源仍然填不对",那"模板"就白给了。

    `_24` §8.2② 又加了三个,因为新预置的来源要:

        腾讯/新浪 {sina}   sh600519 / sz000001   ← 前缀在前,和东财的 `1.` 相反
        腾讯港股  {code5}  00700                 ← 补足 5 位
        SEC      {cik10}  0000320193            ← 补足 10 位

    **`{cik10}` 现在只是把输入补零,不做 ticker→CIK 的查询。**用户填 AAPL
    是查不到的,得填 320193。真做映射要拉 SEC 那张 company_tickers.json
    再缓存,那是另一件事 —— 这里不假装能换,免得用户以为填 ticker 就行,
    拿到一个 404 却不知道为什么。模板 note 里写明了要填 CIK。
    """
    raw = (code or "").strip()
    bare = raw.split(".")[0].strip()

    # ⚠️ **先判港股再判 A 股**。港股 00700(腾讯)是 5 位、以 "00" 开头,
    # 而 A 股深市也是 "00" 开头 —— 按 A 股的规则先匹配的话,
    # 腾讯会被展开成 `0.00700` / `00700.SZ`,打到深交所去。
    # 实测就是这么错的:00700 → 00700.SZ。
    # 区分靠**长度**:A 股一律 6 位,港股 4-5 位。
    is_hk = (raw.upper().endswith(".HK")
             or (bare.isdigit() and len(bare) <= 5))
    if is_hk:
        return (endpoint
                .replace("{symbol}", bare).replace("{code}", bare)
                .replace("{secid}", f"116.{bare.zfill(5)}")     # 东财港股用 116.
                .replace("{ts_code}", f"{bare.zfill(5)}.HK")
                .replace("{code5}", bare.zfill(5))              # 腾讯港股 hk00700
                .replace("{sina}", f"hk{bare.zfill(5)}")
                .replace("{cik10}", bare.zfill(10))
                # ⚠️ Yahoo 港股要**四位**:0700.HK。
                # `zfill(4)` 对 "00700"(5 位)是空操作,拼出 00700.HK ——
                # 而那个地址**返回 200 且结构完整,只是 price 是 null**,
                # 于是被映射层判成"上游结构变了",让人去查 Yahoo 改没改接口。
                # 先 lstrip 掉前导零再补到 4 位才对(00700→700→0700)。
                .replace("{yahoo}", f"{(bare.lstrip('0') or '0').zfill(4)}.HK"))

    if bare.startswith(("60", "68", "11", "51", "52")):
        exch, em = "SH", "1"
    elif bare.startswith(("00", "30", "12", "15", "16")):
        exch, em = "SZ", "0"
    elif bare.startswith(("43", "83", "87", "88")):
        exch, em = "BJ", "0"
    else:
        # 美股等字母代码 —— 原样传,各家写法一致
        exch, em = "", ""

    yahoo = (f"{bare}.SS" if exch == "SH" else
             f"{bare}.SZ" if exch == "SZ" else bare)

    # 腾讯/新浪要 sh600519 —— 前缀在**前**,和东财的 `1.600519` 正好相反。
    # 字母代码(美股)没有前缀概念,原样给。
    sina = (f"{exch.lower()}{bare}" if exch in ("SH", "SZ") else
            f"bj{bare}" if exch == "BJ" else bare)

    return (endpoint
            .replace("{symbol}", bare)
            .replace("{code}", bare)
            .replace("{code5}", bare)
            .replace("{secid}", f"{em}.{bare}" if em else bare)   # 东财
            .replace("{ts_code}", f"{bare}.{exch}" if exch else bare)  # Tushare
            .replace("{sina}", sina)                              # 腾讯/新浪
            .replace("{cik10}", bare.zfill(10) if bare.isdigit() else bare)  # SEC
            .replace("{yahoo}", yahoo))


# ── SEC 的 ticker → CIK 对照 ──────────────────────────────────
#
# SEC 的接口按 **CIK**(10 位数字)索引,不认 ticker:
#     data.sec.gov/submissions/CIK0000320193.json   200
#     data.sec.gov/submissions/CIKAAPL.json         404
#
# 之前 `{cik10}` 只做补零,用户填 AAPL 就拼出 CIKAAPL → 404,而 404
# 看起来像"这家公司没有备案",实际是我们没做转换。文档里写了"得填 CIK",
# 但**没人会记住一串数字** —— 该我们查。
#
# SEC 自己发布了对照表(company_tickers.json,约 10000 家,600KB),
# 免费、无需 key,只要 UA 带联系邮箱。进程内缓存一次。
_CIK_MAP: dict[str, str] | None = None
_SEC_UA = {"User-Agent": os.getenv("SEC_USER_AGENT",
                                   "Hunter Community admin@example.com")}


def _cik_of(ticker: str) -> str | None:
    """AAPL → 0000320193。查不到返回 None(调用方保持原样,让上游报 404)。"""
    global _CIK_MAP
    if _CIK_MAP is None:
        _CIK_MAP = {}
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as c:
                d = c.get("https://www.sec.gov/files/company_tickers.json",
                          headers=_SEC_UA).json()
            # 它的结构是 {"0": {cik_str, ticker, title}, "1": {...}} ——
            # 一个**用序号当键的对象**,不是数组
            for v in (d.values() if isinstance(d, dict) else []):
                t = str(v.get("ticker") or "").upper()
                if t:
                    _CIK_MAP[t] = str(v.get("cik_str") or "").zfill(10)
            logger.info("[sec] 载入 ticker→CIK 对照 {} 条", len(_CIK_MAP))
        except Exception as e:                                # noqa: BLE001
            # 拉不到就退化成"不转换" —— 不抛,否则一次网络抖动会让所有
            # SEC 源一起挂掉,而它们本来只是少了一层便利
            logger.warning("[sec] 拉 ticker→CIK 失败(本次不转换): {}", e)
    return _CIK_MAP.get(ticker.upper()) or None


# ── 巨潮的 code → orgId 对照 ────────────────────────────────
#
# 巨潮的公告查询要 `stock=600519,gssh0600519`(代码 + orgId 复合)。
# 只给代码返回 totalRecordNum:0(不报错),**一个参数都不给则返回
# 随机 30 家公司的公告** —— 而那 30 条会被当成用户问的那只票的公告
# 报给他。实测:问茅台的公告,答的是太兴集团的中期股息。
#
# 这是最糟的一类错:答案完整、格式正确、看不出任何异常,只是公司不对。
#
# 巨潮自己发布了对照表(szse_stock.json,6239 条,免费无 key)。
_CNINFO_ORG: dict[str, str] | None = None


def _cninfo_stock(code: str) -> str:
    """600519 → "600519,gssh0600519"。查不到就返回裸代码 ——
    那会让上游返回 0 条,是个**空结果而不是错结果**。"""
    global _CNINFO_ORG
    bare = str(code).strip().split(".")[0]
    if _CNINFO_ORG is None:
        _CNINFO_ORG = {}
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as c:
                d = c.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                          headers={"User-Agent": "Mozilla/5.0"}).json()
            arr = d.get("stockList") or d.get("data") or []
            for x in arr:
                cd, org = str(x.get("code") or ""), str(x.get("orgId") or "")
                if cd and org:
                    _CNINFO_ORG[cd] = org
            logger.info("[cninfo] 载入 code→orgId 对照 {} 条", len(_CNINFO_ORG))
        except Exception as e:                                 # noqa: BLE001
            logger.warning("[cninfo] 拉 orgId 对照失败: {}", e)
    org = _CNINFO_ORG.get(bare)
    return f"{bare},{org}" if org else bare


def _expand_deep(obj, code: str):
    """递归展开 body 模板里的占位符(只碰字符串,结构原样保留)。"""
    if isinstance(obj, str):
        return expand(obj, code)
    if isinstance(obj, dict):
        return {k: _expand_deep(v, code) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_deep(v, code) for v in obj]
    return obj


def _fetch_one(src: UserSource, symbol: str) -> dict:
    """打一次用户的源。**主地址不行就依次试备用地址。**

    为什么需要备用地址:东财的 push2his 分片**会轮换** —— 实测同一分钟
    82. 是 5/5、十分钟后 0/4,而同时 7. 变 4/4;更糟的时候所有分片一起
    不通,只剩 push2delay(只给当日一行,但 100% 可达)。

    换地址的条件有两个,**缺一不可**:
      · 抛异常(连不上)
      · **或者返回了但一行数据都没有** —— 这是关键。东财挂掉时经常是
        `rc:0 + 空数组`,HTTP 200、结构完整,只有数据是空的。
        只按异常判断的话,这种"成功地返回了空"会被当成正常结果。

    全部试完仍不行就抛最后一个错误 —— 不返回空 dict 冒充成功。
    """
    urls = [src.endpoint] + [u for u in (src.alt_urls or []) if u]
    last_err: Exception | None = None
    for i, u in enumerate(urls):
        try:
            got = _fetch_at(src, symbol, u)
        except Exception as e:                                 # noqa: BLE001
            last_err = e
            if i + 1 < len(urls):
                logger.info("[resolver] {} 第 {} 个地址失败({}),换备用地址",
                            src.name, i + 1, type(e).__name__)
            continue
        if not _looks_empty(got):
            if i:
                logger.info("[resolver] {} 用了第 {} 个备用地址", src.name, i)
            return got
        last_err = RuntimeError("上游返回了但没有数据(HTTP 200 + 空)")
        if i + 1 < len(urls):
            logger.info("[resolver] {} 第 {} 个地址返回空,换备用地址",
                        src.name, i + 1)
    raise last_err or RuntimeError("取数失败")


def _looks_empty(payload: dict) -> bool:
    """返回了但没有实际数据吗。

    **只认几个已知形状,认不出就当成有数据** —— 宁可漏判(不换地址),
    也不要误判成空而把好数据丢掉换个更差的源。
    """
    if not isinstance(payload, dict):
        return not payload
    d = payload.get("data")
    if isinstance(d, dict):
        k = d.get("klines")
        if isinstance(k, list):
            return len(k) == 0
    if isinstance(d, list):
        return len(d) == 0
    return False


def _fetch_at(src: UserSource, symbol: str, url: str = "") -> dict:
    """打一次指定地址并返回原始负载。任何一步失败都抛异常。"""
    # 地址里要 CIK 而用户给的是 ticker(字母)→ 先查对照表换成数字。
    # **只在真的需要时才查**(地址里有 {cik10} 且代码不是纯数字),
    # 这样非 SEC 的源不会因此多一次网络请求
    sym = symbol
    # ⚠️ **占位符要作用在真正要打的那个地址上。**
    #
    # 加备用地址之后这个函数收的是 `url`(可能是备用地址),而不再总是
    # `src.endpoint`。第一版把 {cninfo} 替换写在 `src` 上、下面却用 `url` ——
    # 结果主地址里的 `{cninfo}` 原封不动发了出去,巨潮返回空、映射报
    # 「必需字段 items 没取到」。**改一处忘另一处,又是同一个形状。**
    raw_url = url or src.endpoint
    if "{cninfo}" in raw_url:
        raw_url = raw_url.replace("{cninfo}", _cninfo_stock(symbol))
    if "{cik10}" in raw_url and not str(symbol).strip().isdigit():
        hit = _cik_of(str(symbol).strip())
        if hit:
            sym = hit
    ep = expand(raw_url, sym)
    headers = {k: str(v) for k, v in (src.headers or {}).items()}
    params: dict = {}
    body: dict | None = None

    # body 模板先展开占位符 —— Tushare 的 params.ts_code 要 600519.SH
    if src.body_tpl:
        body = _expand_deep(src.body_tpl, sym)

    if src.requires_key and src.key_enc:
        raw = decrypt(src.key_enc)
        val = f"{src.key_prefix}{raw}" if src.key_prefix else raw
        if src.key_in == "header":
            headers[src.key_name] = val
        elif src.key_in == "query":
            params[src.key_name] = val
        else:
            # **合并进模板而不是覆盖它**。第一版写的是 `body = {key: val}`,
            # 那会把 api_name 整个冲掉 —— 上游返回"请指定正确的接口名",
            # 而错误看起来像是我们没配对接口
            body = {**(body or {}), src.key_name: val}

    timeout = min(max(src.timeout_ms, 1000), 30000) / 1000
    # ⚠️ **params 为空时必须传 None,不能传 {}**。
    # httpx 收到 params 就用它**整体替换** URL 上的 query ——
    # 空 dict 会把 `?secid=1.600519&fields=…` 整段冲掉。
    # 表现极隐蔽:上游照样 200,只是返回 {"rc":102,"data":null},
    # 然后被映射层判成"结构不对",用户去查地址、查 key、查网络,
    # 而地址其实是对的,是我们自己把它改短了。
    kw = {"headers": headers}
    if params:
        kw["params"] = params

    # ── 连接层断开要重试 ──────────────────────────────────────
    #
    # 东财的 K线分片(`82.push2his`)实测**时好时坏**:同一个地址同一分钟内
    # 6 次里断 3 次,`RemoteProtocolError: Server disconnected`。
    # 换 host、换参数、换响应大小都试过,规律只有"看运气"。
    #
    # 用户看到的是「这个源时灵时不灵」,而他会归咎于我们 ——
    # 毕竟地址是我们预填的。
    #
    # **只重试连接层错误(断开/超时),不重试 HTTP 状态码。**
    # 4xx/5xx 是上游明确的回答,重试只是把同一个答案再要一遍;
    # 而 GET 是幂等的,连接断了重来一次没有副作用。
    # POST 不重试 —— 它可能已经在上游产生了效果,我们无从判断。
    # 动词:模板声明的优先,其次看有没有 body(key 放在 body 里就必须 POST)
    verb = (src.http_method or "GET").upper()
    if body is not None:
        verb = "POST"

    # POST 不重试 —— 它可能已经在上游产生了效果,我们无从判断
    attempts = 1 if verb != "GET" else 3
    last: Exception | None = None
    for i in range(attempts):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as c:
                if verb == "GET":
                    r = c.get(ep, **kw)
                else:
                    # 没有 key body 时也要能 POST —— 巨潮就是这种:
                    # 参数全在 URL query 上,但它只接受 POST 动词
                    r = c.post(ep, json=body, **kw) if body is not None else c.post(ep, **kw)
            break
        except (httpx.RemoteProtocolError, httpx.ConnectError,
                httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            last = e
            if i == attempts - 1:
                raise
            time.sleep(0.4 * (i + 1))                     # 0.4s → 0.8s
    if not r.is_success:
        raise RuntimeError(f"HTTP {r.status_code}")
    # **不是所有上游都返回 JSON。**腾讯/新浪给的是
    # `v_sh600519="1~贵州茅台~..."` 这种位置分隔的文本,东财新闻给的是
    # JSONP(`x({...})`)—— 直接 `.json()` 会抛 JSONDecodeError,
    # 而那个错会在测试面板上显示成 "connect 阶段失败",
    # 让用户以为是网络不通,去查地址和防火墙 —— 其实已经取回来了。
    #
    # 取不动就把原文塞进 `_raw` 交给映射层。认识这个形状的映射
    # (delimited / jsonp)自己会去读;不认识的照旧会因为拿不到必需字段
    # 而明确报"结构不对",不会静默通过。
    try:
        return r.json()
    except Exception:                                     # noqa: BLE001
        return {"_raw": r.text}


def try_user(market: str, kind: str, symbol: str) -> dict | None:
    """先试用户自己的源。**没有可用的就返回 None,由调用方走原路径。**

    返回 None 与抛异常是两件事:
      · None  —— 用户压根没配 / 全在冷却 → 这是**正常状态**,不是降级
      · 走到最后仍失败 → 记 provenance,让 UI 能说"你的源没用上,原因是…"

    这个区分直接决定徽章弹不弹。全程走官方(用户没配)不该弹,
    那是常态;试过用户的没成功才该弹。
    """
    uid = request_ctx.user_id()
    if not uid:
        return None
    srcs = _candidates(uid, market, kind)
    if not srcs:
        return None

    tried: list[dict] = []
    t0 = time.time()
    for s in srcs:
        try:
            raw = _fetch_one(s, symbol)
            data = source_mapping.apply(s.upstream, kind, raw, s.field_map or None,
                                        market=s.market)
            _mark(s.id, True)
            request_ctx.record(SourceUse(
                market=market, kind=kind, used=f"user:{s.id}", used_label=s.name,
                ok=True, tried=tried, ms=int((time.time() - t0) * 1000),
            ))
            return data
        except source_mapping.MappingError as e:
            # 映射失败**不计入熔断** —— 这不是"源连不上",是我们的映射和
            # 它的格式对不上。熔断它没用(下次一样对不上),而且会让用户
            # 以为是网络问题。原因照实记下来,让他能去详情里改映射
            reason = str(e)
            _mark(s.id, False, reason)
            tried.append({"label": s.name, "reason": reason})
            logger.info("[resolver] {} 映射失败: {}", s.name, reason)
        except Exception as e:                                # noqa: BLE001
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            _mark(s.id, False, reason)
            tried.append({"label": s.name, "reason": reason})
            logger.info("[resolver] {} 取数失败: {}", s.name, reason)

    # 全试完了都没成 —— 记成"降级到官方",这条会让徽章亮起来
    request_ctx.record(SourceUse(
        market=market, kind=kind, used="official", used_label="官方源",
        ok=True, tried=tried, ms=int((time.time() - t0) * 1000),
    ))
    return None
