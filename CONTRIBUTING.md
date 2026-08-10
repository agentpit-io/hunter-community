# Contributing to Hunter Community Edition

> Preview stage · Sprint 06 in progress · full contribution guide lands with v0.1.0.

## Current status

Hunter CE is in **P1 skeleton** phase. The bulk of the code has been migrated from
the SaaS repo `hangeaiagent/hunter` but:

- SaaS-specific routers (WeChat / Lark / booth) are still present and will be stripped in P2.
- Auth still expects the private `agentpit` user DB; local auth ships in P3.
- The pluggable provider layer (data source · LLM · forecast) ships in P4.

If you want to contribute right now, please open a discussion first — the surface is
changing fast and PRs against the current skeleton may need rework.

## Once we hit v0.1.0

- Fork → branch off `main`
- Write your change · include a test if it touches API or provider layer
- `docker compose up` should still boot healthy after your change
- Open PR against `main` · one reviewer approval + green CI required

## Code style

- Python: black + ruff (config lands with P5)
- TypeScript: prettier + eslint (already inherited from Next.js defaults)
- Commit messages: [Conventional Commits](https://www.conventionalcommits.org/)

## Reporting issues

- Bug: open GitHub Issue with reproducer + `docker compose logs` output
- Security: see [SECURITY.md](./SECURITY.md) — do not open public issues for vulns
- Feature request: start with a Discussion so we can align before you build
