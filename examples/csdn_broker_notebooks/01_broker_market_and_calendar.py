#!/usr/bin/env python3
"""实战 01：券商行情快照、K 线前/后/不复权与 A/H/美股交易日历查询实战。

对应 CSDN × 证券公司开发者实战示例包（M5-2）：
- 演示通过 `BrokerDataRouter`（主源：合作券商 GalaxyBrokerAdapter + 备源：公开源 PublicFallbackAdapter）
  获取实时/收盘行情快照、带复权口径校验的历史 K 线、停牌状态处理（缺值报 `null` 禁填 `0`）与交易日历；
- 演示 `BrokerDataEnvelope` 中 `as_of`（行情业务时刻）与 `retrieved_at`（接口抓取时刻）的严格分离。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.providers.broker import get_default_broker_router  # noqa: E402


def run_demo() -> dict:
    router = get_default_broker_router()

    # 1. 查询 A 股交易日历与当前市场状态
    calendar_env = router.get_market_calendar(
        market="CN_A",
        start_date="2026-11-09",
        end_date="2026-11-15",
    )
    market_status_env = router.get_market_status(market="CN_A")

    # 2. 查询贵州茅台 (600519.SH) 实时/收盘报价快照
    quote_env = router.get_quote("600519.SH")

    # 3. 查询前复权与后复权日 K 线
    kline_qfq_env = router.get_kline(
        symbol="600519.SH",
        period="1d",
        adjustment="前复权",
    )
    kline_hfq_env = router.get_kline(
        symbol="600519.SH",
        period="1d",
        adjustment="后复权",
    )

    # 4. 查询停牌标的示例 600898.SH（验证缺数据返回 null 与 warnings，绝不填 0.00）
    suspended_env = router.get_quote("600898.SH")

    q_data = quote_env.data or {}
    s_data = suspended_env.data or {}
    cal_data = calendar_env.data or {}
    ms_data = market_status_env.data or {}
    qfq_bars = (kline_qfq_env.data or {}).get("bars", []) if isinstance(kline_qfq_env.data, dict) else (kline_qfq_env.data or [])
    hfq_bars = (kline_hfq_env.data or {}).get("bars", []) if isinstance(kline_hfq_env.data, dict) else (kline_hfq_env.data or [])

    return {
        "example": "01_broker_market_and_calendar",
        "calendar_days": len(cal_data.get("days", cal_data.get("calendar", []))) if isinstance(cal_data, dict) else len(cal_data),
        "market_status": ms_data.get("session_status") or ms_data.get("status"),
        "quote_600519": {
            "symbol": q_data.get("symbol"),
            "name": q_data.get("name"),
            "last_price": q_data.get("last_price"),
            "pct_change": q_data.get("pct_change"),
            "as_of": quote_env.meta.as_of,
            "retrieved_at": quote_env.meta.retrieved_at,
            "provider": quote_env.meta.provider,
        },
        "kline_qfq_bars": len(qfq_bars),
        "kline_hfq_bars": len(hfq_bars),
        "suspended_check": {
            "symbol": s_data.get("symbol"),
            "halted": s_data.get("halted"),
            "last_price": s_data.get("last_price"),
            "missing_fields": suspended_env.meta.missing_fields,
            "warnings": suspended_env.meta.warnings,
        },
        "disclaimer": "本示例数据仅供量化与投研开发演示参考，不构成任何投资建议。",
    }


if __name__ == "__main__":
    output = run_demo()
    print(json.dumps(output, ensure_ascii=False, indent=2))
