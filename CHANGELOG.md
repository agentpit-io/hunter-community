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
