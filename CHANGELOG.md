# Changelog

All notable changes to Hunter Community Edition follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
