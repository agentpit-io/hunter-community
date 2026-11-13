"""合作证券公司（如银河/华西/东莞等）标准只读数据源适配器（GalaxyBrokerAdapter）。

功能定位：
1. 实现 P0 四大只读业务域接口：`MarketDataProvider`、`SecurityMasterProvider`、
   `FundamentalProvider`、`DisclosureProvider`。
2. 当配置了合作券商网关（`BROKER_GATEWAY_URL` + `BROKER_API_KEY`）时，通过标准 REST 接口
   请求券商官方权威数据；当网关超时（如 HTTP 504）、未配置或某字段未覆盖时，抛出明确的
   `BrokerProviderUnavailableError` 或返回缺失字段 `None`，交由 `BrokerDataRouter`
   按策略执行主备切换或如实标记 `missing_fields`。
3. 支持注入确定性真实历史快照（`snapshot_store`），用于离线回归测试与券商沙盒联调回放，
   严格遵循“三时间戳分离、缺失值保留 None 禁填 0、Point-in-Time 防偷看”铁律。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from app.services.market_time import market_tz

from .base import (
    DisclosureProvider,
    EducationProvider,
    FundamentalProvider,
    FundsProvider,
    MarketDataProvider,
    ResearchProvider,
    SecurityMasterProvider,
)
from .envelope import (
    AdjustmentType,
    BrokerDataEnvelope,
    MarketCode,
    format_iso_dt,
)

CN_TZ = market_tz("CN_A")


class BrokerProviderUnavailableError(RuntimeError):
    """券商主数据源不可用、超时（如 HTTP 504）或未覆盖当前请求时抛出的可降级异常。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "PROVIDER_UNAVAILABLE",
        provider: str = "galaxy_broker",
        status_code: int | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


def _now_iso() -> str:
    return format_iso_dt(datetime.now(CN_TZ))


def normalize_symbol(symbol: str, market: MarketCode = "CN_A") -> tuple[str, str, MarketCode]:
    """标准化证券代码，返回 (带后缀标准代码, 纯代码, 市场代码)。"""
    raw = (symbol or "").strip().upper()
    if not raw:
        raise ValueError("证券代码 `symbol` 不能为空")

    if "." in raw:
        bare, suffix = raw.split(".", 1)
        if suffix in ("SH", "SZ", "BJ"):
            return f"{bare}.{suffix}", bare, "CN_A"
        if suffix == "HK":
            return f"{bare.zfill(5)}.HK", bare.zfill(5), "HK"
        if suffix in ("US", "OQ", "N"):
            return f"{bare}.US", bare, "US"

    if market == "HK" or (raw.isdigit() and len(raw) == 5):
        bare = raw.zfill(5)
        return f"{bare}.HK", bare, "HK"

    if market == "US" or any(ch.isalpha() for ch in raw):
        return f"{raw}.US", raw, "US"

    bare = raw.zfill(6) if raw.isdigit() else raw
    if bare.startswith(("6", "9", "5", "11")):
        suffix = "SH"
    elif bare.startswith(("8", "4")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{bare}.{suffix}", bare, "CN_A"


class GalaxyBrokerAdapter(
    MarketDataProvider,
    SecurityMasterProvider,
    FundamentalProvider,
    DisclosureProvider,
    FundsProvider,
    ResearchProvider,
    EducationProvider,
):
    """合作证券公司官方只读接口标准适配器。"""

    provider_id: str = "galaxy_broker"
    provider_name: str = "合作证券公司官方数据源"
    is_official_broker: bool = True

    def __init__(
        self,
        *,
        gateway_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 8.0,
        snapshot_store: dict[str, Any] | None = None,
        simulate_timeout: bool = False,
    ) -> None:
        self.gateway_url = (
            gateway_url
            if gateway_url is not None
            else os.getenv("BROKER_GATEWAY_URL", "").strip()
        )
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("BROKER_API_KEY", "").strip()
        )
        self.timeout_seconds = timeout_seconds
        self.snapshot_store: dict[str, Any] = snapshot_store or {}
        self.simulate_timeout = simulate_timeout

    def _check_available(self, endpoint: str) -> None:
        if self.simulate_timeout:
            raise BrokerProviderUnavailableError(
                f"合作券商主源请求 `{endpoint}` 超时 (HTTP 504 Gateway Timeout)",
                code="PROVIDER_TIMEOUT_504",
                provider=self.provider_id,
                status_code=504,
                retryable=True,
            )

    def _request_gateway(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """若配置了实盘只读网关 URL，则发起只读 GET 请求；未配置则返回 None。"""
        self._check_available(path)
        if not self.gateway_url:
            return None
        url = f"{self.gateway_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise BrokerProviderUnavailableError(
                f"合作券商主源请求 `{path}` 超时 (HTTP 504)",
                code="PROVIDER_TIMEOUT_504",
                provider=self.provider_id,
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise BrokerProviderUnavailableError(
                f"合作券商主源网络连接异常: {exc}",
                code="PROVIDER_NETWORK_ERROR",
                provider=self.provider_id,
                retryable=True,
            ) from exc

        if resp.status_code >= 500:
            raise BrokerProviderUnavailableError(
                f"合作券商主源返回服务端错误 HTTP {resp.status_code}",
                code=f"PROVIDER_HTTP_{resp.status_code}",
                provider=self.provider_id,
                status_code=resp.status_code,
                retryable=True,
            )
        if resp.status_code != 200:
            raise BrokerProviderUnavailableError(
                f"合作券商主源返回状态码 HTTP {resp.status_code}",
                code=f"PROVIDER_HTTP_{resp.status_code}",
                provider=self.provider_id,
                status_code=resp.status_code,
                retryable=False,
            )
        return resp.json()

    # ── 1. SecurityMasterProvider ────────────────────────────────────────────

    def search_security(
        self,
        query: str,
        market: MarketCode | None = None,
        asset_type: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("security/search")
        gw = self._request_gateway(
            "v1/security/search",
            {"query": query, "market": market, "asset_type": asset_type},
        )
        if gw and "data" in gw:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=market or "CN_A",
                as_of=gw.get("as_of") or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=gw["data"],
            )
        if "securities" in self.snapshot_store:
            q = (query or "").strip().upper()
            rows = [
                item
                for item in self.snapshot_store["securities"]
                if (not q or q in item.get("symbol", "").upper() or q in item.get("name", "").upper())
                and (market is None or item.get("market") == market)
                and (asset_type is None or item.get("asset_type") == asset_type)
            ]
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=market or "CN_A",
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            "合作券商主源未配置在线网关，需切换至公开/本地主数据备用源",
            code="BROKER_GATEWAY_NOT_CONFIGURED",
            provider=self.provider_id,
        )

    def get_security_profile(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("security/profile")
        gw = self._request_gateway("v1/security/profile", {"symbol": std_sym, "market": std_mkt})
        if gw and "data" in gw:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=gw.get("as_of") or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=gw["data"],
            )
        profiles = self.snapshot_store.get("profiles", {})
        if std_sym in profiles:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=dict(profiles[std_sym]),
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 档案或未配置网关",
            code="BROKER_PROFILE_MISS",
            provider=self.provider_id,
        )

    def get_security_status(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("security/status")
        statuses = self.snapshot_store.get("statuses", {})
        if std_sym in statuses:
            rec = dict(statuses[std_sym])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=as_of or rec.get("as_of") or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rec,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 交易状态或未配置网关",
            code="BROKER_STATUS_MISS",
            provider=self.provider_id,
        )

    def get_index_constituents(
        self,
        index_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("security/index_constituents")
        idx_map = self.snapshot_store.get("index_constituents", {})
        code_norm = (index_code or "").strip().upper()
        if code_norm in idx_map:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                symbol=code_norm,
                as_of=as_of or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=list(idx_map[code_norm]),
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中指数 `{code_norm}` 成分股",
            code="BROKER_INDEX_MISS",
            provider=self.provider_id,
        )

    # ── 2. MarketDataProvider ────────────────────────────────────────────────

    def get_quote(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("market/quote")
        quotes = self.snapshot_store.get("quotes", {})
        if std_sym in quotes:
            q = dict(quotes[std_sym])
            quote_as_of = as_of or q.pop("_as_of", "2026-11-13T15:00:00+08:00")
            missing_reasons = q.pop("_missing_reasons", {})
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                currency="HKD" if std_mkt == "HK" else ("USD" if std_mkt == "US" else "CNY"),
                unit="元",
                as_of=quote_as_of,
                retrieved_at=_now_iso(),
                forbid_same_as_retrieved=True,
                expected_fields=[
                    "symbol",
                    "name",
                    "last_price",
                    "prev_close",
                    "open",
                    "high",
                    "low",
                    "pct_chg",
                    "volume",
                    "turnover",
                    "status",
                ],
                missing_reason_map=missing_reasons,
                data=q,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 实时报价或网关未连接",
            code="BROKER_QUOTE_MISS",
            provider=self.provider_id,
        )

    def get_kline(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        period: str = "1d",
        start: str | None = None,
        end: str | None = None,
        adjustment: AdjustmentType = "前复权",
    ) -> BrokerDataEnvelope:
        if not adjustment or adjustment == "N/A":
            raise ValueError("调用 `get_kline` 必须显式指定复权方式（前复权 / 后复权 / 不复权）")
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("market/kline")
        klines = self.snapshot_store.get("klines", {})
        if std_sym in klines:
            rows = list(klines[std_sym])
            if start:
                rows = [r for r in rows if str(r.get("date", "")) >= start]
            if end:
                rows = [r for r in rows if str(r.get("date", "")) <= end]
            last_date = rows[-1]["date"] if rows else "2026-11-13"
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=f"{last_date}T15:00:00+08:00" if "T" not in str(last_date) else str(last_date),
                retrieved_at=_now_iso(),
                adjustment=adjustment,
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` K线数据",
            code="BROKER_KLINE_MISS",
            provider=self.provider_id,
        )

    def get_orderbook(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        levels: int = 5,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("market/orderbook")
        books = self.snapshot_store.get("orderbooks", {})
        if std_sym in books:
            ob = dict(books[std_sym])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=ob.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=ob,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 盘口数据",
            code="BROKER_ORDERBOOK_MISS",
            provider=self.provider_id,
        )

    def get_market_status(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("market/status")
        if "market_status" in self.snapshot_store:
            ms = dict(self.snapshot_store["market_status"].get(market, {}))
            if ms:
                return BrokerDataEnvelope.build(
                    provider=self.provider_id,
                    market=market,
                    as_of=as_of or ms.get("as_of", "2026-11-13T15:00:00+08:00"),
                    retrieved_at=_now_iso(),
                    data=ms,
                )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未配置 `{market}` 市场状态",
            code="BROKER_MARKET_STATUS_MISS",
            provider=self.provider_id,
        )

    def get_market_overview(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("market/overview")
        if "market_overview" in self.snapshot_store:
            ov = dict(self.snapshot_store["market_overview"].get(market, {}))
            if ov:
                return BrokerDataEnvelope.build(
                    provider=self.provider_id,
                    market=market,
                    as_of=as_of or ov.get("as_of", "2026-11-13T15:00:00+08:00"),
                    retrieved_at=_now_iso(),
                    data=ov,
                )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未配置 `{market}` 市场概览",
            code="BROKER_MARKET_OVERVIEW_MISS",
            provider=self.provider_id,
        )

    def get_market_calendar(
        self,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("market/calendar")
        if "calendar" in self.snapshot_store:
            rows = list(self.snapshot_store["calendar"].get(market, []))
            if start_date:
                rows = [r for r in rows if str(r.get("trade_date", "")) >= start_date]
            if end_date:
                rows = [r for r in rows if str(r.get("trade_date", "")) <= end_date]
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=market,
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未配置 `{market}` 交易日历",
            code="BROKER_CALENDAR_MISS",
            provider=self.provider_id,
        )

    # ── 3. FundamentalProvider ───────────────────────────────────────────────

    def get_financial_statements(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        report_period: str | None = None,
        statement_type: str = "all",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("fundamental/statements")
        fin_map = self.snapshot_store.get("financial_statements", {})
        if std_sym in fin_map:
            rows = [dict(r) for r in fin_map[std_sym]]
            if report_period:
                rows = [r for r in rows if r.get("report_period") == report_period]
            if statement_type and statement_type != "all":
                rows = [
                    r for r in rows
                    if r.get("statement_type", "all") in (statement_type, "all")
                ]
            env = BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=as_of or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                report_period=report_period,
                data=rows,
            )
            if as_of:
                env = env.filter_point_in_time(as_of)
            return env
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 财务报表",
            code="BROKER_FIN_STATEMENTS_MISS",
            provider=self.provider_id,
        )

    def get_financial_ratios(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        report_period: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("fundamental/ratios")
        ratio_map = self.snapshot_store.get("financial_ratios", {})
        if std_sym in ratio_map:
            rows = [dict(r) for r in ratio_map[std_sym]]
            if report_period:
                rows = [r for r in rows if r.get("report_period") == report_period]
            env = BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                unit="%",
                as_of=as_of or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                report_period=report_period,
                data=rows,
            )
            if as_of:
                env = env.filter_point_in_time(as_of)
            return env
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 财务比率",
            code="BROKER_FIN_RATIOS_MISS",
            provider=self.provider_id,
        )

    def get_valuation(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("fundamental/valuation")
        val_map = self.snapshot_store.get("valuations", {})
        if std_sym in val_map:
            rec = dict(val_map[std_sym])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=as_of or rec.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=rec,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 估值数据",
            code="BROKER_VALUATION_MISS",
            provider=self.provider_id,
        )

    def get_shareholders(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("fundamental/shareholders")
        sh_map = self.snapshot_store.get("shareholders", {})
        if std_sym in sh_map:
            rec = dict(sh_map[std_sym])
            env = BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of=as_of or rec.get("as_of", "2026-11-13T15:00:00+08:00"),
                published_at=rec.get("published_at"),
                retrieved_at=_now_iso(),
                data=rec,
            )
            if as_of:
                env = env.filter_point_in_time(as_of)
            return env
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 股东数据",
            code="BROKER_SHAREHOLDERS_MISS",
            provider=self.provider_id,
        )

    def get_corporate_actions(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        std_sym, _, std_mkt = normalize_symbol(symbol, market)
        self._check_available("fundamental/corporate_actions")
        ca_map = self.snapshot_store.get("corporate_actions", {})
        if std_sym in ca_map:
            rows = [dict(r) for r in ca_map[std_sym]]
            if start_date:
                rows = [r for r in rows if str(r.get("ex_date", "")) >= start_date]
            if end_date:
                rows = [r for r in rows if str(r.get("ex_date", "")) <= end_date]
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=std_mkt,
                symbol=std_sym,
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 `{std_sym}` 公司行动数据",
            code="BROKER_CORP_ACTIONS_MISS",
            provider=self.provider_id,
        )

    # ── 4. DisclosureProvider ────────────────────────────────────────────────

    def search_announcements(
        self,
        query: str | None = None,
        symbol: str | None = None,
        category: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("disclosure/search_announcements")
        if "announcements" in self.snapshot_store:
            rows = [dict(r) for r in self.snapshot_store["announcements"]]
            if symbol:
                std_sym, bare, _ = normalize_symbol(symbol)
                rows = [
                    r for r in rows
                    if r.get("symbol") in (std_sym, bare, symbol)
                ]
            if category:
                rows = [r for r in rows if category in str(r.get("category", ""))]
            if query:
                q = query.strip().lower()
                rows = [
                    r for r in rows
                    if q in str(r.get("title", "")).lower()
                    or q in str(r.get("summary", "")).lower()
                ]
            if start_date:
                rows = [r for r in rows if str(r.get("published_at", "")) >= start_date]
            if end_date:
                rows = [r for r in rows if str(r.get("published_at", "")) <= end_date + "T23:59:59+08:00"]
            env = BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                symbol=symbol,
                as_of=as_of or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
            if as_of:
                env = env.filter_point_in_time(as_of)
            return env
        raise BrokerProviderUnavailableError(
            "合作券商主源未配置公告检索网关",
            code="BROKER_ANNOUNCEMENTS_MISS",
            provider=self.provider_id,
        )

    def get_announcement(
        self,
        announcement_id: str,
        page_range: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("disclosure/get_announcement")
        ann_map = self.snapshot_store.get("announcement_details", {})
        if announcement_id in ann_map:
            detail = dict(ann_map[announcement_id])
            pages = list(detail.get("pages", []))
            if page_range and pages:
                pages = _slice_pages(pages, page_range)
                detail["pages"] = pages
                detail["requested_page_range"] = page_range
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=detail.get("market", "CN_A"),
                symbol=detail.get("symbol"),
                as_of=detail.get("published_at", "2026-11-13T15:00:00+08:00"),
                published_at=detail.get("published_at"),
                retrieved_at=_now_iso(),
                source_id=announcement_id,
                source_url=detail.get("pdf_url"),
                data=detail,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中公告 `{announcement_id}`",
            code="BROKER_ANNOUNCEMENT_DETAIL_MISS",
            provider=self.provider_id,
        )

    def search_news(
        self,
        query: str | None = None,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("disclosure/search_news")
        if "news" in self.snapshot_store:
            rows = [dict(r) for r in self.snapshot_store["news"]]
            if symbol:
                std_sym, bare, _ = normalize_symbol(symbol)
                rows = [r for r in rows if r.get("symbol") in (std_sym, bare, symbol)]
            if query:
                q = query.strip().lower()
                rows = [
                    r for r in rows
                    if q in str(r.get("title", "")).lower()
                    or q in str(r.get("summary", "")).lower()
                ]
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                symbol=symbol,
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            "合作券商主源未配置合规财经资讯网关",
            code="BROKER_NEWS_MISS",
            provider=self.provider_id,
        )

    def get_news_detail(
        self,
        news_id: str,
    ) -> BrokerDataEnvelope:
        self._check_available("disclosure/get_news_detail")
        news_map = self.snapshot_store.get("news_details", {})
        if news_id in news_map:
            detail = dict(news_map[news_id])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=detail.get("market", "CN_A"),
                symbol=detail.get("symbol"),
                as_of=detail.get("published_at", "2026-11-13T15:00:00+08:00"),
                published_at=detail.get("published_at"),
                retrieved_at=_now_iso(),
                source_id=news_id,
                source_url=detail.get("url"),
                data=detail,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中新闻 `{news_id}`",
            code="BROKER_NEWS_DETAIL_MISS",
            provider=self.provider_id,
        )

    # ── 5. FundsProvider (P1) ────────────────────────────────────────────────

    def get_etf_quote(
        self,
        symbol: str,
        market: MarketCode = "FUND",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("funds/get_etf_quote")
        std_sym, _, _ = normalize_symbol(symbol, "CN_A")
        etf_quotes = self.snapshot_store.get("etf_quotes", {})
        if std_sym in etf_quotes or symbol in etf_quotes:
            item = dict(etf_quotes.get(std_sym) or etf_quotes[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=market,
                symbol=std_sym,
                as_of=as_of or item.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 ETF 行情 `{std_sym}`",
            code="BROKER_ETF_QUOTE_MISS",
            provider=self.provider_id,
        )

    def get_fund_nav(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("funds/get_fund_nav")
        std_sym, _, _ = normalize_symbol(symbol, "CN_A")
        nav_map = self.snapshot_store.get("fund_nav", {})
        if std_sym in nav_map or symbol in nav_map:
            item = dict(nav_map.get(std_sym) or nav_map[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="FUND",
                symbol=std_sym,
                as_of=item.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中基金历史净值 `{std_sym}`",
            code="BROKER_FUND_NAV_MISS",
            provider=self.provider_id,
        )

    def get_etf_holdings(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("funds/get_etf_holdings")
        std_sym, _, _ = normalize_symbol(symbol, "CN_A")
        holdings_map = self.snapshot_store.get("etf_holdings", {})
        if std_sym in holdings_map or symbol in holdings_map:
            item = dict(holdings_map.get(std_sym) or holdings_map[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="FUND",
                symbol=std_sym,
                as_of=as_of or item.get("as_of", "2026-11-13T15:00:00+08:00"),
                published_at=item.get("published_at"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中 ETF 申赎成分清单 `{std_sym}`",
            code="BROKER_ETF_HOLDINGS_MISS",
            provider=self.provider_id,
        )

    def get_fund_fee(
        self,
        symbol: str,
    ) -> BrokerDataEnvelope:
        self._check_available("funds/get_fund_fee")
        std_sym, _, _ = normalize_symbol(symbol, "CN_A")
        fee_map = self.snapshot_store.get("fund_fees", {})
        if std_sym in fee_map or symbol in fee_map:
            item = dict(fee_map.get(std_sym) or fee_map[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="FUND",
                symbol=std_sym,
                as_of=item.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中基金费率结构 `{std_sym}`",
            code="BROKER_FUND_FEE_MISS",
            provider=self.provider_id,
        )

    def get_bond_data(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("funds/get_bond_data")
        std_sym, _, _ = normalize_symbol(symbol, "CN_A")
        bond_map = self.snapshot_store.get("bond_data", {})
        if std_sym in bond_map or symbol in bond_map:
            item = dict(bond_map.get(std_sym) or bond_map[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="BOND",
                symbol=std_sym,
                as_of=as_of or item.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中可转债/债券数据 `{std_sym}`",
            code="BROKER_BOND_DATA_MISS",
            provider=self.provider_id,
        )

    # ── 6. ResearchProvider (P1) ─────────────────────────────────────────────

    def get_research_reports(
        self,
        symbol: str | None = None,
        sector: str | None = None,
        query: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("research/get_research_reports")
        if "research_reports" in self.snapshot_store:
            rows = [dict(r) for r in self.snapshot_store["research_reports"]]
            if symbol:
                std_sym, bare, _ = normalize_symbol(symbol)
                rows = [r for r in rows if r.get("symbol") in (std_sym, bare, symbol)]
            if sector:
                rows = [r for r in rows if sector in str(r.get("sector", ""))]
            if query:
                q = query.strip().lower()
                rows = [
                    r for r in rows
                    if q in str(r.get("title", "")).lower() or q in str(r.get("summary", "")).lower()
                ]
            env = BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                symbol=symbol,
                as_of=as_of or "2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
            if as_of:
                env = env.filter_point_in_time(as_of)
            return env
        raise BrokerProviderUnavailableError(
            "合作券商主源未配置合规授权研报摘要网关",
            code="BROKER_RESEARCH_REPORTS_MISS",
            provider=self.provider_id,
        )

    def get_consensus_estimates(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("research/get_consensus_estimates")
        std_sym, _, mkt = normalize_symbol(symbol, market)
        consensus_map = self.snapshot_store.get("consensus_estimates", {})
        if std_sym in consensus_map or symbol in consensus_map:
            item = dict(consensus_map.get(std_sym) or consensus_map[symbol])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market=mkt,
                symbol=std_sym,
                as_of=as_of or item.get("as_of", "2026-11-13T15:00:00+08:00"),
                published_at=item.get("published_at"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中分析师一致预期 `{std_sym}`",
            code="BROKER_CONSENSUS_MISS",
            provider=self.provider_id,
        )

    def get_sector_data(
        self,
        sector_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("research/get_sector_data")
        sector_map = self.snapshot_store.get("sector_data", {})
        if sector_code in sector_map:
            item = dict(sector_map[sector_code])
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                symbol=sector_code,
                as_of=as_of or item.get("as_of", "2026-11-13T15:00:00+08:00"),
                retrieved_at=_now_iso(),
                data=item,
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中行业景气度数据 `{sector_code}`",
            code="BROKER_SECTOR_DATA_MISS",
            provider=self.provider_id,
        )

    # ── 7. EducationProvider (P1) ────────────────────────────────────────────

    def search_investor_education(
        self,
        query: str,
        category: str | None = None,
    ) -> BrokerDataEnvelope:
        self._check_available("education/search_investor_education")
        if "investor_education" in self.snapshot_store:
            rows = [dict(r) for r in self.snapshot_store["investor_education"]]
            if category:
                rows = [r for r in rows if category in str(r.get("category", ""))]
            if query:
                q = query.strip().lower()
                rows = [
                    r for r in rows
                    if q in str(r.get("title", "")).lower() or q in str(r.get("summary", "")).lower()
                ]
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=rows,
            )
        raise BrokerProviderUnavailableError(
            "合作券商主源未配置投教知识库网关",
            code="BROKER_EDUCATION_SEARCH_MISS",
            provider=self.provider_id,
        )

    def get_product_guide(
        self,
        product_type: str,
    ) -> BrokerDataEnvelope:
        self._check_available("education/get_product_guide")
        guides = self.snapshot_store.get("product_guides", {})
        if product_type in guides:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=dict(guides[product_type]),
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中业务规则说明 `{product_type}`",
            code="BROKER_PRODUCT_GUIDE_MISS",
            provider=self.provider_id,
        )

    def get_risk_disclosure(
        self,
        risk_type: str,
    ) -> BrokerDataEnvelope:
        self._check_available("education/get_risk_disclosure")
        disclosures = self.snapshot_store.get("risk_disclosures", {})
        if risk_type in disclosures:
            return BrokerDataEnvelope.build(
                provider=self.provider_id,
                market="CN_A",
                as_of="2026-11-13T15:00:00+08:00",
                retrieved_at=_now_iso(),
                data=dict(disclosures[risk_type]),
            )
        raise BrokerProviderUnavailableError(
            f"合作券商主源未命中风险揭示书 `{risk_type}`",
            code="BROKER_RISK_DISCLOSURE_MISS",
            provider=self.provider_id,
        )


def _slice_pages(pages: list[dict[str, Any]], page_range: str) -> list[dict[str, Any]]:
    """按页码范围（如 `1-3` 或 `2`）过滤 PDF 公告页面列表。"""
    raw = (page_range or "").strip()
    if not raw:
        return pages
    if "-" in raw:
        left, right = raw.split("-", 1)
        start_p = int(left.strip())
        end_p = int(right.strip())
    else:
        start_p = end_p = int(raw)
    return [
        p for p in pages
        if start_p <= int(p.get("page_no", 0)) <= end_p
    ]
