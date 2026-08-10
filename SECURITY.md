# Security Policy

## Supported versions

Hunter CE is in preview. Only `main` receives security fixes until v0.1.0 GA.

## Reporting a vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Email: `security@agentpit.io` (PGP key available on request)

Include:
- Affected component (api / web / opencode / infra)
- Reproduction steps
- Impact assessment
- Suggested fix if you have one

We aim to acknowledge within 3 business days and ship a patch within 14 days
for critical issues.

## Security-relevant defaults

- `.env.example` ships with placeholder `JWT_SECRET=change-me-in-production-please`
  — **rotate before exposing your instance to the internet**.
- Postgres default password is `hunter` — **rotate for any non-toy deployment**.
- The container images run as unprivileged users where possible (`web` runs as `nextjs`).
- Non-standard host ports (3100/8100/5442/6479) reduce accidental exposure but do NOT
  substitute for a firewall — put a reverse proxy in front for production.
