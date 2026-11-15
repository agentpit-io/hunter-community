# 投研工作台（`/workbench`）去硬编码与真数据逻辑实现技术方案

- **文档版本**：v3.5.2
- **适用范围**：HunterCode 主工作仓（`/mnt/mixplode/hca`）、社区仓（`hunter-community`）、Hermes 生产环境（`https://hunter.agentpit.io/workbench`）
- **核心目标**：全面排查并消除 `/mnt/mixplode/hca/apps/web/app/workbench/page.tsx` 中的硬编码参数（如写死的交易日期、默认自选股串、固定快捷股票数组、回退 `|| 0`、硬编码演练标的与公告正文兜底），实现 100% 真数据驱动、动态交易日历联动、用户真实自选股同步以及缺失字段严格展示 `—`（严禁假 0 与模拟数据兜底）。

---

## 一、现状审计：硬编码（Hardcode）与潜在假数据兜底问题清单

通过对 `/mnt/mixplode/hca/apps/web/app/workbench/page.tsx`（共 2014 行）的逐行审计，发现以下 6 类硬编码与兜底问题：

### 1. 硬编码的交易日期与回测截止日
- **问题定位**：
  - `loadMarketWorkbench`（第 133 行）：硬编码 `trade_date=2026-11-13`；
  - `triggerPreMarketWorkflow`（第 153 行）与 `triggerAfterCloseWorkflow`（第 176 行）：硬编码 `trade_date: '2026-11-13'`；
  - `dcaStartDate` / `dcaEndDate`（第 75–76 行）：硬编码 `'2023-11-01'` 与 `'2026-11-13'`。
- **风险影响**：当日期推进或用户在真实交易日访问时，工作台请求始终停留在固定历史日期，无法随当前自然日/最新交易日自动演进。

### 2. Tab 1：市场工作台（`01 / MARKET`）涨跌幅 `?? 0` 兜底与空状态缺失
- **问题定位**：
  - 第 624 行：`const pct = idx.pct_chg ?? idx.pct_change ?? 0`，当后端指数行情缺失涨跌幅时被静默兜底为 `0`（显示 `+0.00%`），违反“算不出显 `—`、严禁假 0”铁律；
  - 市场广度（上涨/下跌/平盘家数）、领涨/领跌行业板块在后端未返回或正在生成时缺少明确的空状态引导与手动刷新入口。

### 3. Tab 2：自选股工作台（`02 / WATCHLIST`）未优先对接真实 `/api/watchlist` 与硬编码异动标的
- **问题定位**：
  - 第 53 行与第 111 行：状态初始化直接写死 `'600036.SH,600519.SH,600000.SH,600898.SH'`，未优先拉取用户真实自选股接口 `GET /api/watchlist`；
  - `handleAddWatchlistSymbol`（第 206–217 行）：仅修改前端本地 state，未同步调用 `POST /api/watchlist` 落库，也未提供从自选股移除或按任意自选股触发盘中异动归因的能力；
  - 第 795 行与第 802 行：盘中异动雷达按钮硬编码死 `'600036.SH'`，无法跟随当前选中的自选股动态触发；
  - 右侧 PDF 公告原文抽屉（`openAnnouncementDrawer`）：仅使用列表概要中内嵌的 `pages`，当公告详情需要按页码实时拉取时未调用 `GET /api/workflows/announcement/{announcement_id}`。

### 4. Tab 3：个股研究工作台（`03 / STOCK RESEARCH`）固定 4 只股票按钮与硬编码评价兜底
- **问题定位**：
  - 第 1010 行：研究标的仅提供 `['600036.SH', '600519.SH', '600000.SH', '000001.SZ']` 四个写死的按钮，缺少自由股票代码输入框（`input` + 查询按钮），且快捷切换未动态关联用户真实自选股列表；
  - 第 866 行：自选股表格列头写死 `加权ROE(2026Q3)`，应改为动态报告期或通用 `加权ROE`；
  - 第 1259 行：`{m.evaluation || '正常'}`，当某项基本面指标因停牌或财报未披露而未计算出评价时，被强行兜底显示为 `'正常'`，违反“缺失显 `—`、严禁假评级”铁律。

### 5. Tab 4：量化研究工作台（`04 / QUANT LAB`）ETF 代码不可自由输入与日期区间固定
- **问题定位**：
  - 第 1313–1322 行：ETF 标的仅限下拉框中 3 只固定 ETF，不支持用户输入任意场内 ETF 代码（如 `510500.SH`、`588000.SH` 等），且起止日期（`dcaStartDate` / `dcaEndDate`）、目标年限（`targetYears`）、假设年化收益率（`assumedReturnPct`）虽有 state 却未暴露或未随动态日期计算。

### 6. Tab 5：组合复盘工作台（`05 / PORTFOLIO`）股票筛选与真实 Broker 授权状态联动
- **问题定位**：
  - `thesisSymbolFilter`（第 93 行）定义了状态但界面上缺少筛选输入框与清空按钮，用户无法按指定持仓代码过滤投资论点；
  - 组合复盘顶部缺少对当前券商只读授权（`GET /api/broker/status`）真实连接状态的实时提示与快速跳转引导。

---

## 二、真数据前后端联调架构与接口映射方案

### 1. 全局动态日期与数值格式化铁律（`fmtVal` / `fmtPct`）
- **动态交易日计算**：
  - 新增 `getLatestTradeDateISO()` 辅助函数：基于当前东八区（`Asia/Shanghai`）真实日期计算最新交易日（若遇周六自动回退至周五，周日回退至周五），并支持用户在市场工作台顶部通过日期选择器（`input[type="date"]`）自由切换任意历史交易日。
  - 新增 `getDefaultDcaStartDateISO(endDateStr, years = 3)`：基于截止日动态向前推算 3 年作为默认定投起始日，彻底移除写死的 `'2023-11-01'` 与 `'2026-11-13'`。
- **严格空值展示（零假 0、零假评级）**：
  - `fmtVal(v, suffix, digits)`：当 `v === null || v === undefined || v === '' || Number.isNaN(Number(v))` 时一律返回 `'—'`；
  - 指数与板块涨跌幅：先判断 `rawPct = idx.pct_chg ?? idx.pct_change`，若 `rawPct === null || rawPct === undefined` 则直接展示 `'—'`，严禁 `?? 0`；
  - 90 项基本面指标体检评价：`m.evaluation || '—'`（当 `m.value === null || m.value === undefined` 时评价严格显示 `'—'`，绝不兜底为 `'正常'`）。

### 2. Tab 1：市场工作台（`01 / MARKET`）真数据联调
- **API 端点**：
  - `GET /api/workflows/workbench/market?market=CN_A&trade_date=${tradeDate}`
  - `POST /api/workflows/pre-market`（传入当前 `tradeDate` 与当前真实 `watchlistSymbols` 数组）
  - `POST /api/workflows/after-close`（传入当前 `tradeDate` 与当前真实 `watchlistSymbols` 数组）
- **空状态与容错**：
  - 当 `preCard?.market_overview?.indices` 为空数组或未就绪时，渲染清晰的空状态面板：“当前交易日盘前行情数据尚未生成或数据源未刷新，请点击上方按钮重新拉取”，绝不使用静态模拟数组填充。

### 3. Tab 2：自选股工作台（`02 / WATCHLIST`）真数据联调
- **API 端点**：
  1. **真实自选股列表同步**：页面初始化时首先调用 `GET /api/watchlist`（携带 `localStorage.getItem('hunter_token')` 鉴权头）。
     - 若返回非空用户自选股列表，则自动将用户的 `code` + `exchange` 规范化为标准证券代码（如 `600036.SH`），若 URL query `?symbols=` 指定了代码则优先使用 query 参数；
     - 用户在输入框添加新股票时，规范化代码格式（自动识别 6 开头为 `.SH`、0/3 开头为 `.SZ`、4/8 开头为 `.BJ`），同时调用 `POST /api/watchlist` 同步保存至后端数据库，并刷新 `/api/workflows/workbench/watchlist`。
  2. **盘中异动归因动态标的**：
     - 新增异动监控标的选择器（默认绑定当前自选股第一只或用户在表格中点击选中的股票 `selectedAnomalySymbol`），调用 `POST /api/workflows/intraday-anomaly` 进行真实实时归因。
  3. **PDF 公告原文按需拉取**：
     - 点击“打开 PDF 原文并高亮页码”时，若该公告对象未包含分页正文或需拉取完整页码，异步调用 `GET /api/workflows/announcement/${encodeURIComponent(ann.announcement_id)}?page_range=1-5` 补齐真实分页原文；若无原文则诚实显示“该页暂无文本内容（`—`）”。

### 4. Tab 3：个股研究工作台（`03 / STOCK RESEARCH`）真数据联调
- **API 端点**：
  - `GET /api/workflows/workbench/stock?symbol=${encodeURIComponent(s)}&adjustment=${encodeURIComponent(a)}&restated=${rs}`
- **动态股票输入与自选联动**：
  - 新增自由股票代码搜索输入框与“深度体检”触发按钮，支持用户输入任意 A 股代码（如 `600588.SH`、`300750.SZ`、`000001.SZ` 等）立即调用后端 Python 确定性引擎计算 56 项技术指标与 90 项基本面指标；
  - 快捷切换按钮组由当前自选股列表动态派生（同时保留 `data-testid="btn-select-stock-${sym}"` 兼容自动化回归测试契约）；
  - 新增“会计重述口径（调整后 vs 调整前）”切换开关，实时联动 `restated` 参数重算。

### 5. Tab 4：量化研究工作台（`04 / QUANT LAB`）真数据联调
- **API 端点**：
  - 初次聚合加载：`GET /api/workflows/workbench/quant`
  - ETF 定投实时重算：`POST /api/workflows/etf-dca/calculate`
  - 因子 IC 与分层回测：`POST /api/workflows/factor/analyze`
  - 回测 6 维防坑审计：`POST /api/workflows/backtest/audit`
  - 自然语言选股器：`POST /api/workflows/screener/build`
- **交互增强**：
  - 支持下拉选择常见宽基 ETF 或直接输入自定义 ETF 代码，开放定投起止日期、定投频率、每期金额、申购费率、目标金额完整参数表单，所有结果 100% 由后端确定性现金流引擎实时计算返回。

### 6. Tab 5：组合复盘工作台（`05 / PORTFOLIO`）真数据联调
- **API 端点**：
  - `GET /api/workflows/workbench/portfolio-review?symbol=${encodeURIComponent(thesisSymbolFilter)}`
  - `POST /api/workflows/thesis/check`
  - `GET /api/broker/status`（在 `BrokerConnectorPanel` 及复盘页联动展示真实券商 OAuth 只读授权状态与 CSV 交割单导入批次）
- **交互增强**：
  - 增加按股票代码过滤投资论点的输入框与查询按钮；
  - 当未授权券商连接器时，清晰展示只读安全承诺与授权引导，绝不伪造实盘交割记录。

---

## 三、E2E 自动化测试契约（`data-testid`）兼容性保障

为确保现有六关 Playwright E2E 验收套件（`tools/e2e/verify_m5_full_playwright.py`）100% 零回归通过，改造过程中严格保留以下全部 `data-testid` 钩子：
- 顶部导航 Tab：`tab-market-workbench`、`tab-watchlist-workbench`、`tab-stock-workbench`、`tab-quant-workbench`、`tab-portfolio-review-workbench`
- 市场工作台：`panel-market-workbench`、`btn-generate-pre-market`、`btn-generate-after-close`、`market-envelope-meta`、`market-indices-grid`、`market-three-columns`
- 自选股工作台：`panel-watchlist-workbench`、`input-add-watchlist-symbol`、`btn-add-watchlist-symbol`、`btn-trigger-intraday-anomaly`、`btn-trigger-disconnect-drill`、`intraday-anomaly-card`、`watchlist-table`、`watchlist-announcements-list`、`btn-open-pdf-drawer-${idx}`、`portfolio-risk-card`、`announcement-pdf-drawer`、`btn-close-pdf-drawer`、`btn-pdf-page-${pNo}`、`drawer-page-citation`
- 个股研究工作台：`panel-stock-workbench`、`btn-select-stock-${sym}`、`technical-dashboard-56`、`fundamental-scorecard-90`、`restatement-comparison-box`
- 量化研究工作台：`panel-quant-workbench`、`etf-dca-calculator-card`、`select-etf-symbol`、`input-dca-periodic-amount`、`select-dca-frequency`、`input-dca-fee-pct`、`input-dca-target-amount`、`btn-run-etf-dca`、`dca-summary-metrics`、`dca-irr-value`、`dca-vs-lump-sum-box`、`dca-target-reverse-box`、`dca-mandatory-disclaimer`、`factor-research-card`、`select-factor-id`、`backtest-quality-audit-card`、`backtest-audit-score-badge`、`btn-audit-standard-pass`、`btn-audit-simulate-pit-fail`、`stock-screener-card`、`btn-screener-test-unsupported`、`input-screener-query`、`btn-run-screener`、`screener-unsupported-badge`
- 组合复盘工作台：`panel-portfolio-review-workbench`、`investment-thesis-board`、`input-custom-thesis`、`input-custom-invalidation`、`btn-check-thesis`、`trading-journal-card`、`news-signal-digest-card`、`workbench-broker-connector-section`
