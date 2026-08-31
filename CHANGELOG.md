# Changelog

All notable changes to Hunter Community Edition follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-rc1] - 2026-09-01

### 复赛 4 类评委优化(A/B/C/D 全部就绪)

#### ✨ 新增 · Added
- **阶段 1** klines Daily ETL cron 挂载(6adcbd8)
  - 每交易日 15:30 CST A股 · 17:00 港股 · 03:30 CST 次日 美股
  - POST /api/admin/etl/run-market 手动触发端点
  - GET /api/admin/etl/health 数据新鲜度暴露
- **阶段 2** Kronos provider 生产切 kronos_saas(f633b2a)
  - 修 upstream API 字段 code → symbol 422 bug
  - 生产 .env FORECAST_PROVIDER=noop → kronos_saas
  - kronos.agentpit.io gateway 端到端通
- **阶段 3** scheduler 真跑 · daily_pipeline 端到端(小王 + 我 · 昨晚打通最后堵点)
  - snapshot_job / backtest_job / consistency_job 三步已存在
  - daily_close view v2 加 adv_20d(98da971 + 21cb47f)
  - CSI300 300 只 seed + backtest_config 27 字段(0efd7ed)
- **阶段 4** 逐笔成本 + sqrt_impact 冲击模型(21cb47f + 9312a16)
  - backtest_trade 表 · 每笔含 commission/stamp_tax/slippage/other/adv_20d/impact_bps_actual
  - broker preset(cn/hk/us/zero)· bp_static / sqrt_impact 双档滑点
  - GET /api/quant/backtest/{id}/trades · 前 200 笔 JSON
  - GET /api/quant/backtest/{id}/trades.csv · 全量 UTF-8 BOM CSV
  - 前端 backtest.html 加逐笔明细面板 + 导出按钮
- **阶段 5** 分享页 /p/{token}(小王 ee4f8d3)
  - SSR + 演示数据黄条 + 事后 outcome 展示 + 免责声明
- **阶段 6** 合规硬约束 · 灰度开关(小王 + 我 9815033)
  - orchestrator SYSTEM_PROMPT 5 条合规硬约束
  - compliance_guard 三档(strict/permissive/off)
  - compliance_violation_log 表 · fire-and-forget 落库
  - 报告水印扩到 5 页

### 🔧 变更 · Changed
- `providers/forecast/kronos_http.py` request body 加 symbol 字段(向后兼容 code)
- `backtest_result` 加 trading_cost / gross_metrics / slippage_model 3 列(持久化 · 修缓存命中丢失)
- `daily_close` view v2 · 加 adv_20d(20 日均成交额)

### 🐛 修复 · Fixed
- Kronos SaaS gateway 422 错误(f633b2a)
- 缓存命中时 trading_cost 丢失

### 📝 文档 · Docs
- doc/开源hunter-community/04开源比赛/ 一批新方案 + 接手 + 交接文档
- doc/01远程服务器编程/README-hunter-community.md fin-r1 手册

### 🏗️ 数据库变更 · Migrations
- 0009 compliance_violation_log
- 0010 daily_close view v1
- 0011 backtest_config
- 0012 company_master
- 0013 backtest_trade + backtest_result 加 3 列
- 0014 daily_close v2(加 adv_20d)

### 🎯 剩余工作(v1.0.0 正式发)
- 观察连续 3 个交易日 scheduler 全绿
- pred_backtest 积到 30+ 样本 · 校准 tab 有真数据
- Grafana dashboard 关键指标
- 稳定观察 3 天 → tag v1.0.0

---

## [0.1.0-alpha] - 2026-08-10

First public preview cutting five compressed sprints into `main`.

### Added
- **P1** · Monorepo (`apps/{api,web}` · `db/migrations` · `docs`),
  Dockerfiles, `docker-compose.yml` (postgres 16 · redis 7 · api · web),
  `HUNTER_MINIMAL_BOOT` boot flag
- **P2** · SaaS strip (WeChat / Lark / booth / SSO removed · -17k LOC)
- **P3** · Local auth (`argon2id` password · JWT HS256 · rotating refresh
  token · first user auto-admin · `REGISTRATION_MODE=open|invite|closed`)
- **P4** · Pluggable provider layer:
  - `providers/data_source/{saas,akshare,yfinance}`
  - `providers/llm/{openai_compat,anthropic}`
  - `providers/forecast/{noop,kronos_http}`
  - `/settings` page with per-user SaaS accelerator configuration
  - `apps/api/app/utils/crypto.py` AES-256-GCM at-rest encryption
- **P5** · GitHub Actions: `ci.yml` (gitleaks + api compile + web build)
  · `docker-publish.yml` (GHCR api+web images) · `release.yml`
  (CHANGELOG-driven release notes)

### Not yet
- Push channel refactor (SMTP · Slack) · `HUNTER_MINIMAL_BOOT` removal
- hunter-opencode GHCR image · shared `JWT_SECRET` plugin
- Password reset flow · rate limit · settings account tab
- Full `docs/01-13` coverage (only 01-02 shipped)

## [0.2.0] - 2026-08-11

### Added
- **`opencode` chat engine now runs** via `ghcr.io/agentpit-io/hunter-opencode:latest`
  · docker-compose service uncommented · 5 hunter plugins loaded
  (hunter-auth · hunter-audit · hunter-guard · hunter-budget · hunter-mcp-context)
  · 4 MCP servers registered (watchlist · portfolio · uzi · hunter_user).
- **Companion image build** in huntercode private repo:
  `packages/hunter-server/Dockerfile` (bun 1.3.14-alpine + python3 + `mcp` +
  `httpx`) · `.github/workflows/hunter-community-publish.yml` publishes on
  push to dev · multi-tag GHCR (branch, sha, tag).
- **nginx `/api/opencode/` location** proxies to `:3921` (opencode host port)
  with 600s SSE-friendly timeout · Bearer JWT passed through.

### Changed
- **opencode basic auth OFF by default** · `OPENCODE_USER/PASS` defaults are
  empty in `docker-compose.yml`. hunter-auth plugin (JWT via shared
  `JWT_SECRET`) is the sole gate. Setting the vars re-enables basic auth
  but requires additional nginx work.
- `.env.example` documents `HUNTER_INTERNAL_KEY` (shared secret for MCP →
  api container callbacks), `OPENCODE_TAG`, `HUNTER_BUDGET_ENABLED`.

### Fixed (during Session B / opencode enablement)
- `packages/hunter-server/Dockerfile` broadened `COPY . .` (needs
  patches/ turbo.json for `bun install --frozen-lockfile`) + added
  python3+make+g++ to deps stage (postinstall node-gyp compile).

### Verified on fin-r1
- `GET /api/opencode/agent` → 200 · 19.5KB (9 agents including build/plan/
  explore/summary/triage/duplicate-pr)
- `GET /api/opencode/session` → 200 · `[]`
- `GET /api/opencode/config/providers` → 200 · 6KB provider list

### GHCR
- `ghcr.io/agentpit-io/hunter-opencode:dev` published (visibility: private
  by default · needs manual UI flip to public per doc 08 for anon pull)
- Alternative: `docker login ghcr.io` with a PAT to pull private image

## [0.1.3] - 2026-08-11

### Added
- **`<AuthGuard>` global 401 interceptor** (`apps/web/app/components/AuthGuard.tsx`)
  · monkey-patches `window.fetch` at layout mount · on `/api/*` 401 with
  `needLogin:true` or `INVALID_TOKEN`/`UNAUTHORIZED` error, wipes tokens
  from `localStorage` and redirects to `/login?return_to=<original>`.
  Fixes the infinite "初始化 session 失败" console spam when JWT expired
  or DB volume was wiped. 30+ existing fetch callsites need no change.
- `login/page.tsx` honors `?return_to=` so re-auth lands where you were.

### Changed
- fin-r1 demo instance postgres password rotated from default `hunter/hunter`
  to a random 28-char string (in fin-r1 `.env`, not in git). Applied via
  `ALTER USER hunter WITH PASSWORD '...'` inside the running container so
  no data was lost.

### Deferred to v0.2.0 (documented in `doc 13 · opencode-enablement.md`)
- hunter-opencode GHCR image + docker-compose enable · chat features
- SaaS data key wiring · needs `hunter.agentpit.io/dev/api-keys` first
- LLM provider wiring for subagents / online_analysis / agents/graph
- GM data source refactor to yfinance
- SMTP/Slack push channels

## [0.1.2] - 2026-08-11

### Security
- **Scrub `FinAPI@2026!` token leak** · previously hardcoded as a `os.getenv`
  fallback default in `finance_data_client.py`, `online_analysis/unified_fetcher.py`,
  `agents/sentinel/unified_fetcher.py`, `factor_engine.py`. All 4 defaults
  now empty · users must provide `FINANCE_DATA_TOKEN` explicitly.
  Trufflehog didn't catch this because it's a plain word (no entropy).
- New CI guardrail: `os.getenv` fallback values matching a shared-secret
  pattern (6+ alphanumerics not on the whitelist) fail the build.

### Added
- **Provider fallback in `finance_data_client.get_quote()`** · when
  `FINANCE_DATA_URL` is empty (the OSS default) it now delegates to
  `providers.data_source.get_data_source().get_quote()` via an async→sync
  bridge · users can pick `akshare` (A-shares, China network) or
  `yfinance` (US/HK/A, non-China network) with a single env var.
- `yfinance==0.2.51` added to `requirements.txt` (was missing despite
  the provider impl existing).
- Quote `/api/quote/{code}` cache-miss branch actively fetches via
  `fd_get_quote` before returning "数据未就绪" placeholder · fills cache.

### Fixed
- `providers/data_source/yfinance_impl.py::get_quote()` rewritten to use
  `Ticker.history(period="5d")` instead of `fast_info` · the latter throws
  `KeyError: 'currentTradingPeriod'` on newer yfinance when market is closed.
- Shape adapter in `finance_data_client.get_quote` returns `None` when the
  provider yields null price · UI now correctly shows "数据未就绪" instead of
  misleading `price: 0.0`.

### Known limitation
- The demo instance at `https://hunter-community.agentpit.io` shows null
  prices for A-shares (akshare backend blocked from GCP Singapore) and US
  stocks (Yahoo Finance rate-limits GCP IPs with HTTP 429). Users on other
  networks or with a `HUNTER_SAAS_DATA_URL/KEY` are unaffected.

## [0.1.1] - 2026-08-11

Post-release patch closing the P0 items from
`doc/codex/开源整合方案/10-v0.1.0-alpha-测试报告.md`.

### Fixed
- **Business tables now always built** · `init_db()` runs unconditionally in
  lifespan; `HUNTER_MINIMAL_BOOT=1` only gates the background schedulers
  (collector · signal_monitor · gm_alerts · backtest · stocks_catalog seed).
  Fixes 500 on `/api/watchlist` `/api/alerts/list` `/api/user_mcp`
  `/api/portfolio/summary` after fresh volume.
- **Redis env respected** · `apps/api/app/routers/{quote,portfolio}.py` +
  `services/collector.py` switched from hardcoded `redis://localhost:6379`
  to `os.getenv("REDIS_URL", ...)`. Fixes `redis.ConnectionError` in docker.
- **Multi-tenant migration folded into `init_db`** · `stocks` /
  `position_thesis` / `push_tasks` now always have `user_id` column and the
  composite primary keys. Fixes `UndefinedColumn: column "user_id" does not
  exist` on `/api/watchlist` and `/api/portfolio/summary`.
- **Swagger closed by default** · FastAPI ctor now hides `/docs` `/redoc`
  `/openapi.json` unless `HUNTER_ENABLE_DOCS=1`. Fixes API-surface leak via
  direct `:8100/docs` bypass (nginx wasn't intercepting).
- **`/api/signals/` public** · middleware whitelist widened so the signal
  dashboard renders without auth for anonymous visitors.

### Removed
- `apps/api/routers/` and `apps/api/services/` dead paths (rsync artifact
  from hermes' old layout; only `apps/api/app/*` is imported).
- `POST /api/watchlist/feishu/config` + `GET /api/watchlist/feishu/config`
  routes and their `get_feishu_config` / `upsert_feishu_config` +
  `feishu_bindings` helpers · P2 completion.

### Added
- `scripts/export-openapi.py` · dumps `app.openapi()` to
  `docs/api-reference.json` (spec is generated even while HTTP endpoint is
  closed).
- `.github/workflows/ci.yml` · new `guardrails` job that fails CI on
  regressions: hardcoded `redis://localhost:6379`, stray
  `apps/api/{routers,services}` dirs, `wx_openid`/`feishu_bindings`/
  `booth_admin`/`ADVENTUREX_` leftovers.
- `.env.example` · clearer `HUNTER_MINIMAL_BOOT` docstring.

## [Unreleased]

### Added
- Initial monorepo scaffold (`apps/api`, `apps/web`, `db/`, `scripts/`, `docs/`)
- Dockerfile for `api` (Python 3.11 slim) and `web` (Node 22 alpine)
- `docker-compose.yml` with postgres 16 + redis 7 + api + web
- `HUNTER_MINIMAL_BOOT` env flag to skip legacy schedulers during P1 boot
- Non-standard host port defaults (web 3100 / api 8100 / postgres 5442 / redis 6479)
  to avoid conflicts on shared machines

### Known limitations (P1 skeleton)
- SaaS-side routers (wechat / feishu / ax / booth) still present · Sprint 06 P2 removes them
- No local auth yet · JWT still expects upstream `agentpit` DB · Sprint 06 P3 replaces
- Provider abstraction not yet built · LLM/data source calls will fail until you point
  at real backends · Sprint 06 P4 introduces provider layer
- `opencode` service commented out · needs `ghcr.io/agentpit-io/hunter-opencode` image
  which Sprint 06 P3 publishes
- No CI · Sprint 06 P5 adds GitHub Actions

### Sprint 06 roadmap
See `hangeaiagent/hermes-1 · doc/codex/开源整合方案/06-Sprint计划.md` (internal).
