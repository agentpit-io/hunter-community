# 结构化选股支持字段字典（`stock_screener_builder` 参考规范）

| 标准字段名 | 中文名称 | 单位 | 权威数据源工具 |
|---|---|---|---|
| `pe_ttm` | 市盈率 PE(TTM) | 倍 | `broker_fundamental.get_valuation` |
| `pb_mrq` | 市净率 PB(MRQ) | 倍 | `broker_fundamental.get_valuation` |
| `dividend_yield_pct` | 股息率(TTM) | % | `broker_fundamental.get_valuation` |
| `roe_ttm_pct` | 净资产收益率 ROE(TTM) | % | `broker_fundamental.get_financial_ratios` |
| `revenue_yoy_pct` | 营业收入同比增速 | % | `broker_fundamental.get_financial_ratios` |
| `net_profit_yoy_pct` | 归母净利润同比增速 | % | `broker_fundamental.get_financial_ratios` |
| `ocf_to_np_ratio` | 净现比 (经营现金流/净利润) | 倍 | `broker_fundamental.get_financial_ratios` |
| `is_st` | 是否 ST/*ST 风险警示 | 布尔 | `broker_security.get_security_status` |
| `is_halted` | 是否停牌不可交易 | 布尔 | `broker_security.get_security_status` |
