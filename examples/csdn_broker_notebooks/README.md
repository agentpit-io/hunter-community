# CSDN × 证券公司开发者金融数据实战示例包（`v3.5.0`）

本目录面向 CSDN 开发者与合作证券公司投研工程师，提供 **5 个开箱即用、确定性可复现** 的金融数据与投研工作流实战示例（同时提供可直接执行的 `.py` 脚本与交互式 `.ipynb` Jupyter Notebook）。

## 一、5 大实战示例清单

| 序号 | 脚本文件 | Notebook 文件 | 核心实战内容 | 对应底层模块 / SKILL |
|---:|---|---|---|---|
| 01 | `01_broker_market_and_calendar.py` | `01_broker_market_and_calendar.ipynb` | 券商行情快照、前/后/不复权 K 线、停牌缺值（`null` 禁填 `0`）与交易日历查询实战 | `broker_market`、`broker_security`、`BrokerDataEnvelope` |
| 02 | `02_56_technical_indicators.py` | `02_56_technical_indicators.ipynb` | 7 大类 56 项技术指标确定性计算、分位数与多空信号冲突诊断（如均线多头但 RSI 超买） | `services/indicators`、`technical_dashboard` |
| 03 | `03_90_fundamental_scorecard_and_disclosure.py` | `03_90_fundamental_scorecard_and_disclosure.ipynb` | 6 大维度 90 项基本面体检评分卡、Point-in-Time（`published_at <= as_of`）防偷看与 PDF 公告页码溯源 | `broker_fundamental`、`broker_disclosure`、`fundamental_scorecard`、`announcement_impact` |
| 04 | `04_factor_ic_and_backtest_audit.py` | `04_factor_ic_and_backtest_audit.ipynb` | 因子 IC / Rank IC、5 层单调性检验与回测 6 维质量审计（前视偏差 / 幸存者偏差 / 交易成本 / 涨跌停过滤） | `factor_research`、`backtest_quality_auditor` |
| 05 | `05_etf_dca_and_daily_workflow.py` | `05_etf_dca_and_daily_workflow.ipynb` | ETF 定投正向历史回测、目标金额反推、一次性买入对比及 08:30 盘前 / 15:30 盘后工作流编排 | `services/etf_dca`、`etf_dca_planner`、`daily_market_brief`、`after_close_review` |

## 二、快速运行方式

在仓库根目录下直接运行任意脚本即可输出结构化 JSON 结果：

```bash
python3 examples/csdn_broker_notebooks/01_broker_market_and_calendar.py
python3 examples/csdn_broker_notebooks/02_56_technical_indicators.py
python3 examples/csdn_broker_notebooks/03_90_fundamental_scorecard_and_disclosure.py
python3 examples/csdn_broker_notebooks/04_factor_ic_and_backtest_audit.py
python3 examples/csdn_broker_notebooks/05_etf_dca_and_daily_workflow.py
```

## 三、工程与合规铁律说明

1. **严禁算不出填 `0`**：停牌或未披露字段统一返回 `null`（前端显示 `—`），并在 `meta.warnings` 中注明原因。
2. **严禁大模型心算数字**：56 项技术指标、90 项基本面指标、因子 IC/IR 与 ETF 定投 IRR 100% 由后端 Python 引擎确定性算出，AI 报告经 `number_verifier.py` 正则回读校验。
3. **只读合规边界**：全部示例仅调用只读行情、财务、公告与定投测算接口，不包含任何实盘交易下单指令。本目录全部示例仅供技术开发与量化研究参考，不构成任何投资建议。
