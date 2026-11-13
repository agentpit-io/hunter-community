"""证券公司数据源统一数据信封（BrokerDataEnvelope）。

设计铁律：
1. 任何券商或备用数据源返回给智能体之前，必须包装进标准数据信封。
2. 三个时间戳严格分离：
   - `as_of`：行情/业务时点（如收盘时刻 `2026-11-13T15:00:00+08:00`）
   - `published_at`：公告/财报实际公开披露时间（如 `2026-11-13T16:10:00+08:00`）
   - `retrieved_at`：本次接口实际抓取时间（如 `2026-11-13T16:10:03+08:00`）
   严禁将接口抓取时间 `retrieved_at` 冒充行情时间 `as_of`！
3. 严禁假数据与算不出填 0：
   - 缺失字段一律保持 `None`（序列化为 JSON `null`），并在 `meta.missing_fields` 与
     `meta.warnings` 中如实登记原因，绝对禁止用 `0` 或 `0.0` 顶替缺失值。
4. 支持 Point-in-Time（PIT，历史当时点）防偷看过滤：
   - 回测或历史复盘传入 `pit_as_of` 时，任何 `published_at > pit_as_of` 的未来披露记录
     必须被剔除并记入告警，防止偷看未来财报或公告。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

MarketCode = Literal["CN_A", "HK", "US", "FUND", "BOND", "FUTURES", "OPTION", "OTHER"]
AdjustmentType = Literal["前复权", "后复权", "不复权", "qfq", "hfq", "none", "N/A"]
QualityStatus = Literal["ok", "partial", "degraded", "stale", "error"]


def _parse_iso_dt(value: str | datetime | None, field_name: str) -> datetime | None:
    """解析 ISO-8601 时间字符串或 datetime，统一转换为带时区的 datetime。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"字段 `{field_name}` 不是合法的 ISO-8601 时间格式: {value!r}") from exc
    else:
        raise ValueError(f"字段 `{field_name}` 类型无效: {type(value).__name__}")

    if dt.tzinfo is None:
        # 默认按东八区（上海/香港时间）解释无时区时间戳
        from datetime import timedelta

        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt


def format_iso_dt(dt: datetime) -> str:
    """格式化为带时区的 ISO-8601 字符串。"""
    if dt.tzinfo is None:
        from datetime import timedelta

        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.isoformat(timespec="seconds")


class BrokerEnvelopeError(BaseModel):
    """信封级结构化错误项。"""

    code: str = Field(..., description="错误代码，如 PROVIDER_TIMEOUT / MISSING_DATA / UNAUTHORIZED")
    message: str = Field(..., description="面向人类与智能体的中文大白话错误说明")
    provider: str | None = Field(default=None, description="报错的数据源标识")
    retryable: bool = Field(default=False, description="是否可重试或触发主备切换")


class BrokerDataMeta(BaseModel):
    """统一数据信封元数据封面（9 项强制信息 + 质量审计字段）。"""

    provider: str = Field(..., min_length=1, description="数据源标识，如 galaxy_broker / akshare_fallback")
    market: MarketCode = Field(default="CN_A", description="市场标识：CN_A / HK / US 等")
    symbol: str | None = Field(default=None, description="证券代码，如 600000.SH")
    currency: str = Field(default="CNY", description="币种，如 CNY / HKD / USD")
    unit: str = Field(default="元", description="数值单位，如 元 / 股 / % / 万元")
    as_of: str | None = Field(
        default=None,
        description="行情或业务数据所属时点（ISO-8601），严禁拿抓取时间冒充",
    )
    published_at: str | None = Field(
        default=None,
        description="公告或财报公开披露时间（ISO-8601），用于 Point-in-Time 防偷看校验",
    )
    retrieved_at: str = Field(
        ...,
        description="本次接口实际抓取时间（ISO-8601）",
    )
    delay_seconds: int = Field(
        default=0,
        ge=0,
        description="行情延迟秒数，0 表示实时，900 表示延时 15 分钟",
    )
    adjustment: AdjustmentType = Field(
        default="不复权",
        description="复权方式：前复权 / 后复权 / 不复权 / N/A",
    )
    source_id: str | None = Field(default=None, description="原始记录或快照唯一标识")
    source_url: str | None = Field(default=None, description="原始公告/新闻/文档出处链接")
    license_scope: str = Field(
        default="authorized_user_readonly",
        description="数据授权使用范围说明",
    )
    is_fallback: bool = Field(
        default=False,
        description="是否由备用公开数据源降级返回（主源超时或未覆盖时为 True）",
    )
    fallback_reason: str | None = Field(
        default=None,
        description="切换到备用数据源的原因说明",
    )
    report_period: str | None = Field(
        default=None,
        description="财务报表报告期，如 2026Q3 / 2025A",
    )
    restated: bool | None = Field(
        default=None,
        description="财务数据是否经过重述/追溯调整",
    )
    fields: list[str] = Field(default_factory=list, description="本次返回包含的核心字段名列表")
    missing_fields: list[str] = Field(
        default_factory=list,
        description="缺失字段列表（对应值保持 None/null，严禁填 0）",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="数据质量、停牌、延时、缺失或降级警告信息",
    )
    quality_status: QualityStatus = Field(
        default="ok",
        description="数据质量状态：ok / partial / degraded / stale / error",
    )

    @model_validator(mode="after")
    def _validate_timestamps_and_fallback(self) -> "BrokerDataMeta":
        ret_dt = _parse_iso_dt(self.retrieved_at, "retrieved_at")
        if ret_dt is None:
            raise ValueError("`retrieved_at`（接口抓取时间）不能为空")

        as_of_dt = _parse_iso_dt(self.as_of, "as_of")
        pub_dt = _parse_iso_dt(self.published_at, "published_at")

        # 防止把未来的行情时间或披露时间塞入当前抓取信封（允许最多 5 秒时钟漂移）
        from datetime import timedelta

        drift_tolerance = timedelta(seconds=5)
        if as_of_dt is not None and as_of_dt > ret_dt + drift_tolerance:
            raise ValueError(
                f"行情时间 `as_of` ({self.as_of}) 晚于抓取时间 `retrieved_at` ({self.retrieved_at})，疑似时间戳造假或时区错误"
            )
        if pub_dt is not None and pub_dt > ret_dt + drift_tolerance:
            raise ValueError(
                f"披露时间 `published_at` ({self.published_at}) 晚于抓取时间 `retrieved_at` ({self.retrieved_at})，违反时间先后约束"
            )

        if self.is_fallback and not self.fallback_reason:
            raise ValueError("当 `is_fallback=True`（降级使用备用源）时，必须填写 `fallback_reason` 说明原因")

        if self.is_fallback:
            fallback_notice = f"数据源已降级至备用源 `{self.provider}`：{self.fallback_reason}"
            if fallback_notice not in self.warnings:
                self.warnings.append(fallback_notice)
            if self.quality_status == "ok":
                self.quality_status = "degraded"

        return self


class BrokerDataEnvelope(BaseModel):
    """券商与金融数据工具统一返回信封。"""

    ok: bool = Field(default=True, description="本次调用是否成功")
    data: Any = Field(default=None, description="结构化业务数据（字典或列表），缺失值保持 None/null")
    meta: BrokerDataMeta = Field(..., description="信封元数据封面")
    errors: list[BrokerEnvelopeError] = Field(default_factory=list, description="错误明细列表")

    @model_validator(mode="after")
    def _validate_envelope_consistency(self) -> "BrokerDataEnvelope":
        if not self.ok and self.meta.quality_status == "ok":
            self.meta.quality_status = "error"
        if self.meta.missing_fields and self.meta.quality_status == "ok":
            self.meta.quality_status = "partial"
        return self

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        data: dict[str, Any] | list[dict[str, Any]] | None,
        retrieved_at: str | datetime,
        as_of: str | datetime | None = None,
        published_at: str | datetime | None = None,
        market: MarketCode = "CN_A",
        symbol: str | None = None,
        currency: str = "CNY",
        unit: str = "元",
        delay_seconds: int = 0,
        adjustment: AdjustmentType = "不复权",
        source_id: str | None = None,
        source_url: str | None = None,
        license_scope: str = "authorized_user_readonly",
        is_fallback: bool = False,
        fallback_reason: str | None = None,
        report_period: str | None = None,
        restated: bool | None = None,
        expected_fields: list[str] | None = None,
        missing_reason_map: dict[str, str] | None = None,
        forbid_same_as_retrieved: bool = False,
        warnings: list[str] | None = None,
        errors: list[BrokerEnvelopeError] | None = None,
        ok: bool = True,
    ) -> "BrokerDataEnvelope":
        """构造统一数据信封，自动执行时间戳分离检查与缺失字段（None）扫描。

        参数说明：
        - `forbid_same_as_retrieved=True`：用于历史快照/收盘行情/财报查询，强制禁止将
          `retrieved_at` 直接复制冒充 `as_of`。
        - `expected_fields`：期望存在的字段列表；若在 `data` 中为 `None` 或未出现，自动记入
          `meta.missing_fields` 与 `meta.warnings`，绝不填 `0`。
        """
        ret_dt = _parse_iso_dt(retrieved_at, "retrieved_at")
        if ret_dt is None:
            raise ValueError("`retrieved_at` 不能为空")
        ret_str = format_iso_dt(ret_dt)

        as_of_dt = _parse_iso_dt(as_of, "as_of")
        as_of_str = format_iso_dt(as_of_dt) if as_of_dt else None

        pub_dt = _parse_iso_dt(published_at, "published_at")
        pub_str = format_iso_dt(pub_dt) if pub_dt else None

        if forbid_same_as_retrieved:
            if as_of_dt is None:
                raise ValueError("行情/快照数据必须提供真实的 `as_of` 业务时间戳，不能为空")
            if as_of_str == ret_str:
                raise ValueError(
                    f"禁止将接口抓取时间 `retrieved_at` ({ret_str}) 直接冒充行情业务时间 `as_of`！"
                )

        warn_list = list(warnings or [])
        reason_map = missing_reason_map or {}
        present_fields: list[str] = []
        missing_fields: list[str] = []

        records: list[dict[str, Any]] = []
        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = [item for item in data if isinstance(item, dict)]

        if records:
            all_keys: list[str] = []
            for rec in records:
                for k in rec.keys():
                    if k not in all_keys:
                        all_keys.append(k)
            target_keys = expected_fields if expected_fields is not None else all_keys
            for key in target_keys:
                # 只要任意一条记录缺失或值为 None，即登记为缺失字段，保留 None 绝不填 0
                is_missing = any((key not in rec) or (rec.get(key) is None) for rec in records)
                if is_missing:
                    for rec in records:
                        if key not in rec:
                            rec[key] = None
                    if key not in missing_fields:
                        missing_fields.append(key)
                    reason = reason_map.get(key, "数据源未返回或标的停牌/未披露，保留 null（严禁填 0）")
                    msg = f"字段 `{key}` 缺失：{reason}"
                    if msg not in warn_list:
                        warn_list.append(msg)
                else:
                    if key not in present_fields:
                        present_fields.append(key)
        elif expected_fields:
            for key in expected_fields:
                missing_fields.append(key)
                reason = reason_map.get(key, "数据为空")
                warn_list.append(f"字段 `{key}` 缺失：{reason}")

        meta = BrokerDataMeta(
            provider=provider,
            market=market,
            symbol=symbol,
            currency=currency,
            unit=unit,
            as_of=as_of_str,
            published_at=pub_str,
            retrieved_at=ret_str,
            delay_seconds=delay_seconds,
            adjustment=adjustment,
            source_id=source_id,
            source_url=source_url,
            license_scope=license_scope,
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
            report_period=report_period,
            restated=restated,
            fields=present_fields,
            missing_fields=missing_fields,
            warnings=warn_list,
        )

        return cls(
            ok=ok,
            data=data,
            meta=meta,
            errors=list(errors or []),
        )

    def filter_point_in_time(
        self,
        pit_as_of: str | datetime,
        *,
        published_field: str = "published_at",
    ) -> "BrokerDataEnvelope":
        """按历史当时点（Point-in-Time）过滤数据，剔除 `published_at > pit_as_of` 的未来信息。

        用于回测与历史复盘防偷看（TC-MCP-03）：
        - 若 `data` 是列表，逐条检查记录中的 `published_field`（如 `published_at`），仅保留
          在 `pit_as_of` 及之前已公开披露的记录。
        - 若 `data` 是单条字典，或者信封级 `meta.published_at > pit_as_of`，则将其置为 `None`
          并写入 `warnings`。
        """
        cutoff_dt = _parse_iso_dt(pit_as_of, "pit_as_of")
        if cutoff_dt is None:
            raise ValueError("`pit_as_of` 不能为空")
        cutoff_str = format_iso_dt(cutoff_dt)

        new_warnings = list(self.meta.warnings)
        dropped_count = 0

        if isinstance(self.data, list):
            kept: list[Any] = []
            for item in self.data:
                if not isinstance(item, dict):
                    kept.append(item)
                    continue
                item_pub_raw = item.get(published_field) or self.meta.published_at
                item_pub_dt = _parse_iso_dt(item_pub_raw, published_field)
                if item_pub_dt is None:
                    dropped_count += 1
                    continue
                if item_pub_dt <= cutoff_dt:
                    kept.append(item)
                else:
                    dropped_count += 1
            filtered_data: Any = kept
        elif isinstance(self.data, dict):
            item_pub_raw = self.data.get(published_field) or self.meta.published_at
            item_pub_dt = _parse_iso_dt(item_pub_raw, published_field)
            if item_pub_dt is not None and item_pub_dt <= cutoff_dt:
                filtered_data = dict(self.data)
            else:
                dropped_count = 1
                filtered_data = None
        else:
            filtered_data = self.data

        if dropped_count > 0:
            pit_msg = (
                f"Point-in-Time 防偷看过滤：已剔除 {dropped_count} 条披露时间晚于或缺失于 "
                f"`{cutoff_str}` 的记录"
            )
            if pit_msg not in new_warnings:
                new_warnings.append(pit_msg)

        new_meta = self.meta.model_copy(
            update={
                "as_of": cutoff_str,
                "warnings": new_warnings,
                "quality_status": "partial" if dropped_count > 0 and self.meta.quality_status == "ok" else self.meta.quality_status,
            }
        )
        return BrokerDataEnvelope(
            ok=self.ok,
            data=filtered_data,
            meta=new_meta,
            errors=list(self.errors),
        )
