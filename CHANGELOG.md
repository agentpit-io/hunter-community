# Changelog

All notable changes to Hunter Community Edition follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
