"""证券公司数据接口抽象协议（8 大业务域 Provider）。

对应 8 个券商专属 MCP 服务：
1. `MarketDataProvider`       -> `broker_market`（行情快照、K线、盘口、交易状态、交易日历）
2. `SecurityMasterProvider`   -> `broker_security`（证券主数据、代码映射、指数与行业成分）
3. `FundamentalProvider`      -> `broker_fundamental`（三大报表、财务比率、估值、股东、公司行动）
4. `DisclosureProvider`       -> `broker_disclosure`（全市场公告检索、PDF页码解析、合规资讯）
5. `FundsProvider`            -> `broker_funds`（ETF行情/净值/持仓/费率/分红、可转债数据）
6. `ResearchProvider`         -> `broker_research`（合规授权研报摘要、行业景气度、一致预期）
7. `EducationProvider`        -> `broker_education`（券商官方投教文章、产品规则、风险揭示书）
8. `AccountReadonlyProvider`  -> `broker_account_readonly`（用户单独OAuth授权后的只读持仓/资金/交割单）

安全红线：
- 本模块所有接口均为**纯只读查询接口**，不包含任何实盘下单、撤单、改单或银证转账方法。
- 所有方法返回值统一为 `BrokerDataEnvelope`。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .envelope import AdjustmentType, BrokerDataEnvelope, MarketCode


class BaseBrokerProvider(ABC):
    """券商数据 Provider 公共基类。"""

    provider_id: str = "unknown_broker"
    provider_name: str = "未命名数据源"
    is_official_broker: bool = True

    def health_check(self) -> dict[str, Any]:
        """返回数据源健康检查状态。"""
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "is_official_broker": self.is_official_broker,
            "status": "ok",
        }


class MarketDataProvider(BaseBrokerProvider):
    """P0 · 行情与交易日历只读接口（对应 `broker_market` MCP）。"""

    @abstractmethod
    def get_quote(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取实时或延时报价快照（含最新价、昨收、开高低、成交量额、涨跌停价、停复牌状态）。"""

    @abstractmethod
    def get_kline(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        period: str = "1d",
        start: str | None = None,
        end: str | None = None,
        adjustment: AdjustmentType = "前复权",
    ) -> BrokerDataEnvelope:
        """获取历史 K 线数据（强制显式声明复权方式）。"""

    @abstractmethod
    def get_orderbook(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        levels: int = 5,
    ) -> BrokerDataEnvelope:
        """获取五档/十档买卖盘口深度。"""

    @abstractmethod
    def get_market_status(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取市场当前交易阶段（盘前/集合竞价/连续竞价/午间休市/已收盘/节假日休市）。"""

    @abstractmethod
    def get_market_overview(
        self,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取核心宽基指数、两市成交额、涨跌家数与领涨领跌行业概览。"""

    @abstractmethod
    def get_market_calendar(
        self,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取交易日历（区分交易日、节假日、半日市）。"""


class SecurityMasterProvider(BaseBrokerProvider):
    """P0 · 证券主数据与成分股只读接口（对应 `broker_security` MCP）。"""

    @abstractmethod
    def search_security(
        self,
        query: str,
        market: MarketCode | None = None,
        asset_type: str | None = None,
    ) -> BrokerDataEnvelope:
        """按代码、简称、拼音检索股票/ETF/债券。"""

    @abstractmethod
    def get_security_profile(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
    ) -> BrokerDataEnvelope:
        """获取证券基础档案（上市日期、所属板块、申万/中信行业、交易单位、币种）。"""

    @abstractmethod
    def get_security_status(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取证券交易状态（正常上市、停牌、ST/*ST风险警示、退市整理期、已退市）。"""

    @abstractmethod
    def get_index_constituents(
        self,
        index_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取指数或行业成分股及权重（支持历史时点成分，防幸存者偏差）。"""


class FundamentalProvider(BaseBrokerProvider):
    """P0/P1 · 财务报表、估值与公司行动只读接口（对应 `broker_fundamental` MCP）。"""

    @abstractmethod
    def get_financial_statements(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        report_period: str | None = None,
        statement_type: str = "all",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取利润表、资产负债表、现金流量表（带 `published_at`，严格支持 PIT 历史时点过滤）。"""

    @abstractmethod
    def get_financial_ratios(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        report_period: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取核心财务比率（盈利、成长、偿债、周转、现金流质量）。"""

    @abstractmethod
    def get_valuation(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取估值指标与历史分位数（PE-TTM、PB-MRQ、PS、EV/EBITDA、股息率）。"""

    @abstractmethod
    def get_shareholders(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取前十大股东/流通股东、股东户数变化、股权质押与限售解禁日历。"""

    @abstractmethod
    def get_corporate_actions(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取分红送转、配股、回购、增发等公司行动记录（区分公告日、除权除息日、生效日）。"""


class DisclosureProvider(BaseBrokerProvider):
    """P0 · 上市公司公告与合规财经资讯只读接口（对应 `broker_disclosure` MCP）。"""

    @abstractmethod
    def search_announcements(
        self,
        query: str | None = None,
        symbol: str | None = None,
        category: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """按证券代码、关键词、公告分类检索全市场公告列表。"""

    @abstractmethod
    def get_announcement(
        self,
        announcement_id: str,
        page_range: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取单篇公告详情与按页码提取的正文/表格内容（保留页码溯源）。"""

    @abstractmethod
    def search_news(
        self,
        query: str | None = None,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        """检索经合规授权的财经新闻摘要列表。"""

    @abstractmethod
    def get_news_detail(
        self,
        news_id: str,
    ) -> BrokerDataEnvelope:
        """获取单条合规财经资讯详情及关联证券标签。"""


class FundsProvider(BaseBrokerProvider):
    """P1 · ETF、公募基金与可转债只读接口（对应 `broker_funds` MCP）。"""

    @abstractmethod
    def get_etf_quote(
        self,
        symbol: str,
        market: MarketCode = "FUND",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取 ETF 二级市场行情、IOPV 实时参考净值与折溢价率。"""

    @abstractmethod
    def get_fund_nav(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取基金历史单位净值、累计净值与复权净值序列。"""

    @abstractmethod
    def get_etf_holdings(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取 ETF 申赎清单（PCF）或重仓成分股列表。"""

    @abstractmethod
    def get_fund_fee(
        self,
        symbol: str,
    ) -> BrokerDataEnvelope:
        """获取基金管理费率、托管费率、申购赎回费率结构。"""

    @abstractmethod
    def get_bond_data(
        self,
        symbol: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取可转债/债券行情、转股价值、转股溢价率、强赎/回售/下修条款。"""


class ResearchProvider(BaseBrokerProvider):
    """P1 · 合规授权研报摘要与行业一致预期只读接口（对应 `broker_research` MCP）。"""

    @abstractmethod
    def get_research_reports(
        self,
        symbol: str | None = None,
        sector: str | None = None,
        query: str | None = None,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """检索经合法授权的券商研究报告摘要列表。"""

    @abstractmethod
    def get_consensus_estimates(
        self,
        symbol: str,
        market: MarketCode = "CN_A",
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取分析师一致预期（EPS/营收预测均值、区间与调整方向）。"""

    @abstractmethod
    def get_sector_data(
        self,
        sector_code: str,
        as_of: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取行业景气度与同业可比指标统计。"""


class EducationProvider(BaseBrokerProvider):
    """P1 · 券商官方投资者教育与风险揭示书只读接口（对应 `broker_education` MCP）。"""

    @abstractmethod
    def search_investor_education(
        self,
        query: str,
        category: str | None = None,
    ) -> BrokerDataEnvelope:
        """检索券商官方投教文章（基础概念、交易规则、防非反诈科普）。"""

    @abstractmethod
    def get_product_guide(
        self,
        product_type: str,
    ) -> BrokerDataEnvelope:
        """获取业务规则说明（如科创板、北交所、ETF申赎、可转债、两融、期权规则）。"""

    @abstractmethod
    def get_risk_disclosure(
        self,
        risk_type: str,
    ) -> BrokerDataEnvelope:
        """获取标准风险揭示书与投资者适当性提示文本（如 ST退市、高波动、衍生品风险）。"""


class AccountReadonlyProvider(BaseBrokerProvider):
    """P2 · 用户单独 OAuth 授权后的账户只读接口（对应 `broker_account_readonly` MCP）。

    合规硬约束：
    - 默认关闭，必须验证当前登录用户 `user_id` 的有效 OAuth 只读授权凭证。
    - 严禁跨租户读取；严禁提供任何下单、撤单、转账方法。
    """

    @abstractmethod
    def get_accounts(
        self,
        user_id: str,
    ) -> BrokerDataEnvelope:
        """获取当前授权用户已绑定的只读账户列表（账号脱敏展示）。"""

    @abstractmethod
    def get_account_summary(
        self,
        user_id: str,
        account_id: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取授权账户的只读资金概览（总资产、证券市值、可用现金）。"""

    @abstractmethod
    def get_positions(
        self,
        user_id: str,
        account_id: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取授权账户的只读当前持仓快照。"""

    @abstractmethod
    def get_trades(
        self,
        user_id: str,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BrokerDataEnvelope:
        """获取授权账户的历史成交交割记录（仅用于复盘与风险归因）。"""
