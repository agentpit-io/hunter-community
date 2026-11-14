# HunterCode × 证券公司接口与金融 SKILL 体系建设工程（M0–M5）全链路开发、部署、测试与三仓同步收官总结报告（`v3.5.0`）

> **工程代号：** `HCA-BROKER`（阶段 M0 ～ M5 全链路收官）  
> **最终发布版本：** `v3.5.0`  
> **完成日期：** 2026-11-14（上海时间）  
> **部署与验收环境：** 香港服务器（`34.92.73.207` · `/mnt/mixplode/hca` · `http://127.0.0.1:3200`）  
> **适用仓库（严格遵循两级三仓同步铁律）：**  
> 1. **第一级 · 国际与国内双开源主仓（100% 完全相同代码、同 Commit SHA）：**  
>    - `https://github.com/agentpit-io/hunter-community`  
>    - `https://gitcode.com/agentpit-io/huntercode-atomcode`（及 `https://github.com/agentpit-io/huntercode-atomcode`）  
> 2. **第二级 · 独立业务仓库（同步功能模块与版本号 Tag，保留自身独立业务）：**  
>    - `https://github.com/agentpit-io/huntercode`（默认分支 `dev`）  
> 3. **桌面启动器仓库：**  
>    - `https://github.com/agentpit-io/HunterLauncher`

---

## 一、工程目标与六个阶段（M0–M5）全景交付总表

本工程严格依据《HunterCode_证券公司接口与金融SKILL体系建设方案.md》《03HunterCode_证券公司接口与金融SKILL_详细开发方案.md》《04HunterCode_证券公司接口与金融SKILL_开发计划.md》《05HunterCode_证券公司接口与金融SKILL_测试方案.md》四份核心方案，历时 6 个里程碑（M0 → M5），完成了从数据底座、8 大券商专属 MCP、三大确定性计算引擎、22 个金融 SKILL、三条日常工作流、五大 Web 工作台、安全门卫红队测试到 CSDN 开发者实战包与桌面端适配的 100% 闭环交付：

| 里程碑 | 阶段名称 | 发布版本 | 核心交付成果（大白话总结） | 测试与验收结果 |
|---|---|---|---|---|
| **M0** | 基线对齐与三仓同步底座 | `v3.0.2` | 核平 12 个既有 MCP 服务计数口径；实现 `BrokerDataEnvelope` 统一数据信封（严格分离 `as_of` / `published_at` / `retrieved_at` 三个时间戳，强制缺值报 `null` 禁填 `0`）；制定《开源SKILL复用与许可证清单.md》（SBOM）；跑通三仓同步与核验流水线 | 单元测试 100% 通过 + 三仓 `v3.0.2` 同步 |
| **M1** | 首期 P0 只读券商接口与指标计算引擎 | `v3.1.0` | 实现 4 个 P0 券商只读 MCP（`broker_security`、`broker_market`、`broker_disclosure`、`broker_fundamental`）；实现 `BrokerDataRouter` 主备数据源路由器（严禁跨源取均值）；建成 **7 大类 56 项技术指标确定性计算引擎** 与 **6 大维度 90 项基本面体检计算引擎**；新增幂等迁移 `0059_broker_provider_and_audit.sql` | 56 项技术指标与 90 项基本面真值误差 `< 1e-6` + 三仓 `v3.1.0` 同步 |
| **M2** | P0 八大日常股民 SKILL 与三大工作台 | `v3.2.0` | 升级 7 个既有 SKILL 并新增 8 个 P0 日常高频 SKILL；实现数字正则回读校验器（`number_verifier.py`）；打通 08:30 盘前观察卡、盘中异动解释（含频率节流与断网诚实停止）、15:30 盘后复盘（沉淀 `fin_memory`）三条工作流；上线「市场」「自选股（含 PDF 页码抽屉）」「个股研究」三大工作台 | Playwright 真浏览器 E2E 验收通过 + 三仓 `v3.2.0` 同步 |
| **M3** | P1 七大进阶 SKILL、ETF 定投器与量化工作台 | `v3.3.0` | 实现 3 个 P1 券商只读 MCP（`broker_funds`、`broker_research`、`broker_education`）；实现 **ETF 定投计算器引擎**（正向回测 XIRR、目标金额反推、定投 vs 一次性买入对比、强制历史假设免责声明）；新增 7 个 P1 进阶与量化 SKILL；上线「量化研究工作台」与「组合复盘工作台」 | Playwright 真浏览器 E2E 验收通过 + 三仓 `v3.3.0` 同步 |
| **M4** | 用户授权只读账户连接器与安全红队验收 | `v3.4.0` | 实现 P2 级 `broker_account_readonly` 只读 MCP（默认关闭，仅含 4 个只读工具，物理级 0 交易下单接口）；服务端 AES-256-GCM 加密托管 Token 与一键撤销立即清缓存；Excel/CSV 交割单手工导入兜底；升级 `hookd` 五道合规门卫（跨租户强隔离、Scope 核验、禁实盘委托、防 PDF 注入、违规荐股词过滤） | 6 大红队攻击场景 100% 拦截 + Playwright 验收通过 + 三仓 `v3.4.0` 同步 |
| **M5** | CSDN 开发者实战包、安全扫描器与全链路收官 | `v3.5.0` | 实现第三方金融 SKILL 许可证与安全静态扫描器（`tools/skill_security_scanner.py`，22 个 SKILL 100% 通过）；建成 `examples/csdn_broker_notebooks/` 5 套 CSDN × 券商实战脚本与 Notebook；升级 `HunterLauncher` 支持 `v3.5.0`（829 条 `cargo test` 通过）；完成香港 `:3200` 全量部署与 Playwright 六关总验收 | **434 项 `pytest` + 829 项 `cargo test` + Playwright 六关 E2E 100% 通过 + 三仓 `v3.5.0` 同步** |

---

## 二、核心架构与交付资产详单

### 2.1 20 个标准 MCP 服务（12 既有 + 8 券商专属 · 共 66 个工具）

1. **12 个既有核心 MCP 服务**：
   - `hcapack`（3 工具）、`akshare`（3 工具）、`kronos`（2 工具）、`truesource`（6 工具）、`uzi`（1 工具）、`watchlist`（8 工具，含 PDF 文档检索与按页读取）、`portfolio`（3 工具）、`screener`（1 工具）、`hunter_cap`（4 工具）、`hunter_user`（2 工具）、`runtime_control`（4 工具）、`fin_memory`（2 工具）。
2. **8 个新增证券公司专属只读 MCP 服务（`distro/mcp-servers/broker_*/`）**：
   - **P0 级（4 个）**：
     - `broker_security`：`search_security`、`get_security_profile`、`get_security_status`、`get_index_constituents`（支持 `as_of` 历史成分防幸存者偏差）
     - `broker_market`：`get_quote`、`get_kline`（强制复权参数校验）、`get_orderbook`、`get_market_status`、`get_market_overview`、`get_market_calendar`
     - `broker_disclosure`：`search_announcements`、`get_announcement`（支持 `page_range` 原文页码提取）、`search_news`、`get_news_detail`
     - `broker_fundamental`：`get_financial_statements`（强制 `published_at <= as_of` 防未来函数）、`get_financial_ratios`、`get_valuation`、`get_shareholders`、`get_corporate_actions`
   - **P1 级（3 个）**：
     - `broker_funds`：`get_etf_quote`、`get_fund_nav`、`get_etf_holdings`、`get_fund_fee`、`get_bond_data`
     - `broker_research`：`get_research_reports`、`get_consensus_estimates`、`get_sector_data`
     - `broker_education`：`search_investor_education`、`get_product_guide`、`get_risk_disclosure`
   - **P2 级（1 个 · 默认关闭、需用户单独授权）**：
     - `broker_account_readonly`：`get_accounts`、`get_account_summary`、`get_positions`、`get_trades`（100% 只读，无任何下单/撤单/转账接口）。

### 2.2 三大确定性计算引擎与数字回读防幻觉机制

1. **7 大类 56 项技术指标计算引擎（`apps/api/app/services/indicators/`）**：
   - 涵盖趋势型（18项）、超买超卖型（11项）、能量型（7项）、成交量型（6项）、通道路径型（6项）、波动率型（4项）、市场广度型（4项），自动输出历史分位数与多空信号冲突诊断（如均线多头排列但 RSI 超买）。
2. **6 大维度 90 项基本面体检计算引擎（`apps/api/app/services/fundamentals/`）**：
   - 涵盖盈利能力（18项）、成长能力（15项）、偿债与安全性（16项）、现金流质量（15项）、估值水平（14项）、公司治理与股东（12项），支持会计准则调整前/调整后（重述）对比与六维评分卡。
3. **ETF 定投计算器引擎（`apps/api/app/services/etf_dca/`）**：
   - 支持按月/双周/周正向历史净值回测（精确计算累计投入、申购费、分红再投份额、持仓均价、XIRR 内部收益率与最大回撤）、未来目标金额反推每期定投额、定投 vs 期初一次性买入同区间对比，并强制附带历史假设免责声明。
4. **大模型数字正则回读校验器（`apps/api/app/services/workflows/number_verifier.py`）**：
   - 自动提取 AI 报告正文中的所有价格、百分比、金额、倍数，与底层 JSON 真值逐一比对，一旦发现心算篡改立即拦截并回退至 100% 真值确定性报告。

### 2.3 22 个金融 SKILL 与第三方安全静态扫描器（`tools/skill_security_scanner.py`）

- **7 个升级既有 SKILL**：`uzi`、`deep_analysis`、`investor_panel`、`lhb_analyzer`、`risk_profile`、`trap_detector`、`market-event-analyst`。
- **8 个 P0 日常股民 SKILL**：`daily_market_brief`、`watchlist_daily_brief`、`announcement_impact`、`earnings_review`、`technical_dashboard`、`fundamental_scorecard`、`portfolio_risk_check`、`after_close_review`。
- **7 个 P1 进阶与量化 SKILL**：`factor_research`、`etf_dca_planner`、`investment_thesis_tracker`、`stock_screener_builder`、`news_signal_digest`、`trading_journal`、`backtest_quality_auditor`。
- **静态安全与许可证扫描器（`tools/skill_security_scanner.py`）**：对全部 22 个金融 SKILL 执行开源许可证（MIT / Apache-2.0）、未授权 vendored 文件、危险命令/外发窃密脚本、硬编码 API Key、违规荐股承诺话术五维扫描，**22/22 全部 100% 通过**。

### 2.4 CSDN × 证券公司开发者实战示例包（`examples/csdn_broker_notebooks/`）

编写并实测通过 5 套可直接运行的 `.py` 脚本与配套 `.ipynb` Jupyter Notebook：
1. `01_broker_market_and_calendar.py` / `.ipynb`：券商行情快照、K 线前/后复权、停牌缺值（`null`）与交易日历查询实战；
2. `02_56_technical_indicators.py` / `.ipynb`：7 大类 56 项技术指标计算与多空信号冲突诊断；
3. `03_90_fundamental_scorecard_and_disclosure.py` / `.ipynb`：90 项基本面体检、PIT 防偷看与 PDF 公告页码溯源解读；
4. `04_factor_ic_and_backtest_audit.py` / `.ipynb`：因子 IC 分层单调性检验与回测未来函数/幸存者偏差质量审计；
5. `05_etf_dca_and_daily_workflow.py` / `.ipynb`：ETF 定投正向/反推测算与 08:30 盘前 / 15:30 盘后工作流编排。

---

## 三、六道关卡全量测试与真机验收铁证（M5 总验收）

### 3.1 后端全量 `pytest` 测试（覆盖第一至第四关 + SKILL 安全扫描器 + CSDN 实战包）

```text
$ python3 -m pytest apps/api/tests/test_broker_envelope.py \
    apps/api/tests/test_broker_mcp_p0.py \
    apps/api/tests/test_indicators_56.py \
    apps/api/tests/test_fundamentals_90.py \
    apps/api/tests/test_m2_workflows_and_skills.py \
    apps/api/tests/test_m3_p1_mcp_etf_dca_and_skills.py \
    apps/api/tests/test_broker_security_redteam.py \
    apps/api/tests/test_l02_single_source.py \
    tools/tests/ -q
=========================== short test summary info ============================
434 passed, 1 skipped, 10 subtests passed in 34.85s
```

### 3.2 第三方金融 SKILL 许可证与安全静态扫描实录

```text
$ python3 tools/skill_security_scanner.py
=== HunterCode 金融 SKILL 许可证与安全静态扫描报告 ===
扫描技能总数: 22 | 通过: 22 | 拦截: 0
  ✅ PASS [after_close_review] (license=MIT, files=2) -> skills/after_close_review/SKILL.md
  ✅ PASS [announcement_impact] (license=MIT, files=2) -> skills/announcement_impact/SKILL.md
  ✅ PASS [backtest_quality_auditor] (license=MIT, files=2) -> skills/backtest_quality_auditor/SKILL.md
  ✅ PASS [daily_market_brief] (license=MIT, files=2) -> skills/daily_market_brief/SKILL.md
  ✅ PASS [deep_analysis] (license=MIT, files=2) -> skills/deep_analysis/SKILL.md
  ✅ PASS [earnings_review] (license=MIT, files=2) -> skills/earnings_review/SKILL.md
  ✅ PASS [etf_dca_planner] (license=MIT, files=2) -> skills/etf_dca_planner/SKILL.md
  ✅ PASS [factor_research] (license=MIT, files=2) -> skills/factor_research/SKILL.md
  ✅ PASS [fundamental_scorecard] (license=MIT, files=2) -> skills/fundamental_scorecard/SKILL.md
  ✅ PASS [investment_thesis_tracker] (license=MIT, files=2) -> skills/investment_thesis_tracker/SKILL.md
  ✅ PASS [investor_panel] (license=MIT, files=11) -> skills/investor_panel/SKILL.md
  ✅ PASS [lhb_analyzer] (license=MIT, files=1) -> skills/lhb_analyzer/SKILL.md
  ✅ PASS [news_signal_digest] (license=MIT, files=2) -> skills/news_signal_digest/SKILL.md
  ✅ PASS [portfolio_risk_check] (license=MIT, files=2) -> skills/portfolio_risk_check/SKILL.md
  ✅ PASS [risk_profile] (license=MIT, files=1) -> skills/risk_profile/SKILL.md
  ✅ PASS [stock_screener_builder] (license=MIT, files=2) -> skills/stock_screener_builder/SKILL.md
  ✅ PASS [technical_dashboard] (license=MIT, files=2) -> skills/technical_dashboard/SKILL.md
  ✅ PASS [trading_journal] (license=MIT, files=2) -> skills/trading_journal/SKILL.md
  ✅ PASS [trap_detector] (license=MIT, files=1) -> skills/trap_detector/SKILL.md
  ✅ PASS [uzi] (license=MIT, files=1) -> skills/uzi/SKILL.md
  ✅ PASS [watchlist_daily_brief] (license=MIT, files=2) -> skills/watchlist_daily_brief/SKILL.md
  ✅ PASS [market-event-analyst] (license=Apache-2.0, files=1) -> apps/api/skills/market-event-analyst.skill
```

### 3.3 桌面启动器 `HunterLauncher` 适配 `v3.5.0` 与 `cargo test` 实录

- 新增 `templates/docker-compose.hunter-community-3.5.0.yml` 并将 `BUNDLED_TAG` 更新为 `3.5.0`；
- 在 `src-tauri/src/config.rs` 中加入 20 个 MCP 与 22 个金融 SKILL 自检常量与单测；
- 运行 `cargo test`：**829 passed; 0 failed**，已推送至 `https://github.com/agentpit-io/HunterLauncher`（Commit `61eecb5`）。

### 3.4 第五关：香港 `:3200` 环境 Playwright 真浏览器总验收实录

```json
{
  "ok": true,
  "version": "v3.5.0",
  "base_url": "http://127.0.0.1:3200",
  "console_error_count": 0,
  "page_error_count": 0,
  "screenshots": [
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/01_m5_market_workbench_pre_and_after_close.png",
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/02_m5_watchlist_workbench_pdf_drawer_and_anomaly.png",
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/03_m5_stock_research_workbench_56_and_90.png",
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/04_m5_quant_workbench_etf_dca_and_factor_audit.png",
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/05_m5_portfolio_review_workbench.png",
    "/mnt/mixplode/hunter-dev/broker-skill-chain/reports/screenshots_m5/06_m5_broker_connector_settings_and_audit.png"
  ]
}
```

### 3.5 第六关：三仓代码与版本同步专项验收（`v3.5.0`）

- **第一级（双开源仓 100% 完全相同、同 Commit SHA）**：
  - `https://github.com/agentpit-io/hunter-community`（`main` 分支与 `v3.5.0` 标签）
  - `https://gitcode.com/agentpit-io/huntercode-atomcode`（SSH 推送，`main` 分支与 `v3.5.0` 标签）
  - `https://github.com/agentpit-io/huntercode-atomcode`（`main` 分支与 `v3.5.0` 标签）
- **第二级（独立业务仓 `huntercode` 同步功能代码与 `v3.5.0` 标签，保持自身独立业务历史）**：
  - `https://github.com/agentpit-io/huntercode`（`dev` 分支已合入全部 8 个券商 MCP、22 个金融 SKILL、计算引擎、安全扫描器、CSDN 实战示例包与全链路验收总结报告，并打上 `v3.5.0` 标签）。
