# Hunter Community Edition

> 你的私人金融 AI 团队 · 开源自部署 · 15 分钟起步
>
> Your private financial AI team · self-hosted · ready in 15 minutes.

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](./LICENSE)
[![CI](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml/badge.svg)](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-agentpit--io-blue)](https://github.com/agentpit-io/hunter-community/pkgs/container/hunter-community-api)

Live preview: **[https://hunter-community.agentpit.io](https://hunter-community.agentpit.io)**
(first-time visitors are prompted to create the initial admin account).

---

## Highlights

- 🎯 **Chat-first UI** · one sentence to call tools + rich cards
- 🧠 **Pluggable providers** · pick data source (akshare · yfinance · SaaS),
  LLM (OpenAI-compatible · Anthropic · SaaS Gemini), forecast (noop · Kronos)
- 🔐 **Local auth** · email + argon2id password + JWT · first user auto-admin
- 🐳 **`docker compose up` and you're done** · Postgres + Redis + API + Web
- 🔓 **Apache 2.0 · no paywall · no telemetry**
- 🚀 **Optional SaaS accelerators** · plug in your own key for data · LLM · Kronos

---

## 5-minute quickstart

```bash
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
cp .env.example .env
# Edit .env · at minimum set a strong JWT_SECRET
# (openssl rand -base64 48 | tr -d '=/+' | head -c 60)
docker compose up -d --build
open http://localhost:3100
```

On first visit you'll be routed to `/register` to create the admin account.

Default host ports (change in `.env`):
- Web · `3100`
- API · `8100`
- Postgres · `5442`
- Redis · `6479`

---

## Provider matrix

| Layer | Env var | Options | Default |
|---|---|---|---|
| Data source | `DATA_SOURCE_PROVIDER` | `akshare` · `yfinance` · `saas` | `akshare` |
| LLM | `LLM_PROVIDER` | `openai_compat` · `anthropic` · `saas_gemini` | `openai_compat` |
| Forecast | `FORECAST_PROVIDER` | `noop` · `kronos_local` · `kronos_saas` | `noop` |

Every provider has an SaaS alternative that talks to
[hunter.agentpit.io](https://hunter.agentpit.io) — free tier keys at
[/dev/api-keys](https://hunter.agentpit.io/dev/api-keys). You pick per
deployment via `.env` **or** per user via the `/settings` page.

See [docs/02-providers.md](./docs/02-providers.md) for details and shape
contracts.

---

## Feature comparison · Community vs Cloud

| Feature | Community (self-hosted) | Cloud ([hunter.agentpit.io](https://hunter.agentpit.io)) |
|---|---|---|
| Chat + full SKILL set | ✅ | ✅ |
| Watchlist · portfolio · signals | ✅ | ✅ |
| UZI depth analysis | ✅ | ✅ |
| Kronos forecast | ✅ (needs GPU or SaaS key) | ✅ |
| WeChat push | ❌ | ✅ |
| Lark / 飞书 | ❌ | ✅ |
| Multi-tenant billing | ❌ | ✅ |
| First-party market data | needs key | ✅ |

---

## Roadmap

- [x] **P1** · Monorepo skeleton + docker-compose + fin-r1 deploy
- [x] **P2** · SaaS strip (WeChat / Lark / booth / SSO removed)
- [x] **P3** · Local email + password auth
- [x] **P4** · Pluggable provider layer (data · LLM · forecast) + per-user settings
- [ ] **P5** · Push channel refactor to SMTP / Slack
- [ ] **v1.0** · hunter-opencode GHCR image + SKILL catalog UI

---

## Architecture

```
             ┌─────────────────────┐
Browser  →   │  web (Next.js 15)   │ :3100
             └────────┬────────────┘
                      │ /api/*
             ┌────────▼────────────┐
             │  api (FastAPI)      │ :8100
             │  · auth (JWT)       │
             │  · providers layer  │───► SaaS / akshare / yfinance
             │  · agents           │───► OpenAI / Anthropic / SaaS Gemini
             │                     │───► Kronos local / SaaS / noop
             └─┬─────────────┬─────┘
        Postgres           Redis
         :5442             :6479
```

---

## Contributing

Read [CONTRIBUTING.md](./CONTRIBUTING.md). Pull requests welcome, but the
codebase is fresh out of an ongoing extraction from a private repo —
expect churn until v1.0.

## Security

Vulnerabilities: `security@agentpit.io`. See [SECURITY.md](./SECURITY.md).

## License

Apache 2.0 · see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

**Hunter · AgentPit · 猎鹿人** are trademarks of the AgentPit team.
Forks must rename.
