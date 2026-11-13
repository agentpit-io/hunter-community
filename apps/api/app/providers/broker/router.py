"""券商数据源多源主备路由器（BrokerDataRouter）。

核心职责与铁律（对应开发方案 §4.1 与测试方案 TC-MCP-04）：
1. **合作券商官方源优先**：优先调用 `primary_provider`（如 `GalaxyBrokerAdapter`）。
2. **受控主备自动切换**：当主源未配置、未覆盖某字段或发生超时（如 HTTP 504）时：
   - 若 `allow_fallback=True`（默认），自动切换至 `fallback_provider`（`PublicFallbackAdapter`）；
   - 在返回的 `BrokerDataEnvelope.meta` 中强制将 `provider` 标记为备用源真实名称，
     置 `is_fallback=True`、写入清晰的 `fallback_reason`，并将 `quality_status` 标记为 `"degraded"`；
   - 若 `allow_fallback=False`，绝不静默切换，直接返回 `ok=False, data=None` 的结构化错误信封。
3. **严禁跨源取平均值（一票否决红线）**：
   - 主源与备用源的价格、成交量或财务指标存在差异时，只允许整包采用单一主源或单一备用源并标注来源；
   - 绝对禁止将两个数据源的价格或指标相加除以 2 取平均值（提供 `assert_no_cross_source_averaging` 硬拦截）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

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
    BrokerEnvelopeError,
    MarketCode,
    format_iso_dt,
)
from .galaxy_adapter import BrokerProviderUnavailableError, GalaxyBrokerAdapter
from .public_adapter import PublicFallbackAdapter

CN_TZ = market_tz("CN_A")


def _now_iso() -> str:
    return format_iso_dt(datetime.now(CN_TZ))


class CrossSourceAveragingForbiddenError(ValueError):
    """尝试对不同数据源的价格或指标取平均值时抛出的合规红线异常。"""


def assert_no_cross_source_averaging(strategy: str) -> None:
    """校验多源融合策略，严禁使用 average / mean / blend 等跨源取均值策略。"""
    norm = (strategy or "primary_then_fallback").strip().lower()
    if norm in ("average", "mean", "blend", "avg", "cross_average"):
        raise CrossSourceAveragingForbiddenError(
            f"合规红线拦截：严禁对主备数据源的价格或财务指标取平均值（非法策略 `{strategy}`）！"
            "必须采用单一权威源并在信封 meta.provider 中明确标注。"
        )


class BrokerDataRouter(
    MarketDataProvider,
    SecurityMasterProvider,
    FundamentalProvider,
    DisclosureProvider,
    FundsProvider,
    ResearchProvider,
    EducationProvider,
):
    """P0 + P1 七大业务域多源主备路由器。"""

    provider_id: str = "broker_router"
    provider_name: str = "券商主备数据源路由器"
    is_official_broker: bool = True

    def __init__(
        self,
        *,
        primary: GalaxyBrokerAdapter | None = None,
        fallback: PublicFallbackAdapter | None = None,
        allow_fallback: bool = True,
        merge_strategy: str = "primary_then_fallback",
    ) -> None:
        assert_no_cross_source_averaging(merge_strategy)
        self.primary = primary or GalaxyBrokerAdapter()
        self.fallback = fallback or PublicFallbackAdapter()
        self.allow_fallback = allow_fallback
        self.merge_strategy = merge_strategy

    @staticmethod
    def combine_sources(*args: Any, **kwargs: Any) -> None:
        """显式禁止跨源合并取均值。"""
        raise CrossSourceAveragingForbiddenError(
            "严禁将合作券商数据源与公开数据源的价格或指标取平均值！"
        )

    def _route_call(
        self,
        method_name: str,
        *args: Any,
        market: MarketCode = "CN_A",
        symbol: str | None = None,
        **kwargs: Any,
    ) -> BrokerDataEnvelope:
        """统一执行主源调用 -> 异常捕获 -> 备用源降级与信封打标。"""
        primary_fn: Callable[..., BrokerDataEnvelope] = getattr(self.primary, method_name)
        try:
            env = primary_fn(*args, **kwargs)
            return env
        except BrokerProviderUnavailableError as exc:
            if not self.allow_fallback or self.fallback is None:
                return BrokerDataEnvelope.build(
                    provider=self.primary.provider_id,
                    market=market,
                    symbol=symbol,
                    as_of="2026-11-13T15:00:00+08:00",
                    retrieved_at=_now_iso(),
                    ok=False,
                    data=None,
                    warnings=[f"主数据源 `{self.primary.provider_id}` 不可用且已禁用备用源降级：{exc}"],
                    errors=[
                        BrokerEnvelopeError(
                            code=exc.code,
                            message=str(exc),
                            provider=self.primary.provider_id,
                            retryable=exc.retryable,
                        )
                    ],
                )

            fallback_fn: Callable[..., BrokerDataEnvelope] = getattr(self.fallback, method_name)
            fb_env = fallback_fn(*args, **kwargs)
            reason = (
                f"主源 `{self.primary.provider_id}` 不可用或超时（{exc.code}: {exc}），"
                f"已自动切换至备用源 `{self.fallback.provider_id}`（严禁跨源取均值）"
            )
            new_warnings = list(fb_env.meta.warnings)
            fallback_notice = f"数据源已降级至备用源 `{self.fallback.provider_id}`：{reason}"
            if fallback_notice not in new_warnings:
                new_warnings.append(fallback_notice)

            updated_meta = fb_env.meta.model_copy(
                update={
                    "provider": self.fallback.provider_id,
                    "is_fallback": True,
                    "fallback_reason": reason,
                    "warnings": new_warnings,
                    "quality_status": "degraded",
                }
            )
            return BrokerDataEnvelope(
                ok=fb_env.ok,
                data=fb_env.data,
                meta=updated_meta,
                errors=list(fb_env.errors),
            )

    # ── 1. SecurityMasterProvider ────────────────────────────────────────────

    def search_security(
        self,
        query: str,
        market: MarketCode | None = None,
        asset_type: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "search_security",
            query,
            market=market or "CN_A",
            asset_type=asset_type,
        )

    def get_security_profile(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_security_profile",
            symbol,
            market=market,
            symbol=symbol,
        )

    def get_security_status(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_security_status",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
        )

    def get_index_constituents(
        self,
        index_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_index_constituents",
            index_code,
            as_of=as_of,
            symbol=index_code,
        )

    # ── 2. MarketDataProvider ────────────────────────────────────────────────

    def get_quote(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_quote",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
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
        return self._route_call(
            "get_kline",
            symbol,
            market=market,
            period=period,
            start=start,
            end=end,
            adjustment=adjustment,
            symbol=symbol,
        )

    def get_orderbook(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        levels: int = 5,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_orderbook",
            symbol,
            market=market,
            levels=levels,
            symbol=symbol,
        )

    def get_market_status(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_market_status",
            market=market,
            as_of=as_of,
        )

    def get_market_overview(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_market_overview",
            market=market,
            as_of=as_of,
        )

    def get_market_calendar(
        self,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_market_calendar",
            market=market,
            start_date=start_date,
            end_date=end_date,
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
        return self._route_call(
            "get_financial_statements",
            symbol,
            market=market,
            report_period=report_period,
            statement_type=statement_type,
            as_of=as_of,
            symbol=symbol,
        )

    def get_financial_ratios(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        report_period: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_financial_ratios",
            symbol,
            market=market,
            report_period=report_period,
            as_of=as_of,
            symbol=symbol,
        )

    def get_valuation(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_valuation",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
        )

    def get_shareholders(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_shareholders",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
        )

    def get_corporate_actions(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_corporate_actions",
            symbol,
            market=market,
            start_date=start_date,
            end_date=end_date,
            symbol=symbol,
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
        return self._route_call(
            "search_announcements",
            query=query,
            symbol=symbol,
            category=category,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

    def get_announcement(
        self,
        announcement_id: str,
        page_range: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_announcement",
            announcement_id,
            page_range=page_range,
        )

    def search_news(
        self,
        query: str | None = None,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "search_news",
            query=query,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

    def get_news_detail(
        self,
        news_id: str,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_news_detail",
            news_id,
        )

    # ── 5. FundsProvider (P1) ────────────────────────────────────────────────

    def get_etf_quote(
        self,
        symbol: str,
        market: MarketCode = "FUND",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_etf_quote",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
        )

    def get_fund_nav(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_fund_nav",
            symbol,
            start_date=start_date,
            end_date=end_date,
            market="FUND",
            symbol=symbol,
        )

    def get_etf_holdings(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_etf_holdings",
            symbol,
            as_of=as_of,
            market="FUND",
            symbol=symbol,
        )

    def get_fund_fee(
        self,
        symbol: str,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_fund_fee",
            symbol,
            market="FUND",
            symbol=symbol,
        )

    def get_bond_data(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_bond_data",
            symbol,
            as_of=as_of,
            market="BOND",
            symbol=symbol,
        )

    # ── 6. ResearchProvider (P1) ─────────────────────────────────────────────

    def get_research_reports(
        self,
        symbol: str | None = None,
        sector: str | None = None,
        query: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_research_reports",
            symbol=symbol,
            sector=sector,
            query=query,
            as_of=as_of,
        )

    def get_consensus_estimates(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_consensus_estimates",
            symbol,
            market=market,
            as_of=as_of,
            symbol=symbol,
        )

    def get_sector_data(
        self,
        sector_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_sector_data",
            sector_code,
            as_of=as_of,
            symbol=sector_code,
        )

    # ── 7. EducationProvider (P1) ────────────────────────────────────────────

    def search_investor_education(
        self,
        query: str,
        category: str | None = None,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "search_investor_education",
            query,
            category=category,
        )

    def get_product_guide(
        self,
        product_type: str,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_product_guide",
            product_type,
        )

    def get_risk_disclosure(
        self,
        risk_type: str,
    ) -> BrokerDataEnvelope:
        return self._route_call(
            "get_risk_disclosure",
            risk_type,
        )


_DEFAULT_ROUTER: BrokerDataRouter | None = None


def get_default_broker_router() -> BrokerDataRouter:
    """获取全局默认的多源主备路由器单例。"""
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None:
        _DEFAULT_ROUTER = BrokerDataRouter()
    return _DEFAULT_ROUTER
