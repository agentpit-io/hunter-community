<div align="right">

**🌏 [中文](./README.md) · English (current)**

</div>

<div align="center">

<img src="./docs/assets/logo.png" alt="HunterCode" width="160" height="160" />

# HunterCode

### Community Edition

**A self-hosted AI research assistant for A-shares, Hong Kong and US stocks · your data and chats stay on your machine**

HunterCode is an open-source, local alternative to Tencent WorkBuddy Finance Edition · built for hedge-fund researchers and serious individual investors<br>
<sub>Tencent and WorkBuddy are trademarks of Tencent. HunterCode is not affiliated with, partnered with, or endorsed by Tencent.</sub>

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](./LICENSE)
[![CI](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml/badge.svg)](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/agentpit-io/hunter-community)](https://github.com/agentpit-io/hunter-community/releases)
[![Stars](https://img.shields.io/github/stars/agentpit-io/hunter-community?style=social)](https://github.com/agentpit-io/hunter-community/stargazers)
[![Discussions](https://img.shields.io/github/discussions/agentpit-io/hunter-community)](https://github.com/agentpit-io/hunter-community/discussions)

<img src="./docs/screenshots/hunter-demo.gif" alt="HunterCode demo: asking about a stock returns a rich card" width="760" />

[**🚀 Live demo**](https://hunter-community.agentpit.io) &nbsp;·&nbsp; [**⚡ Deploy in 5 min**](#-deploy-in-5-minutes) &nbsp;·&nbsp; [**📖 Docs**](./docs/01-getting-started.md) &nbsp;·&nbsp; [**💬 Discussions**](https://github.com/agentpit-io/hunter-community/discussions)

🏆 [Finalist, Global Open-source AI Competition (GOAI), Track 2 Top 15](https://mp.weixin.qq.com/s/n8olfrqdP0-rkj6mU_N6Hg) · [Feature-by-feature comparison with WorkBuddy Finance (with official sources)](https://www.agentpit.io/compare/workbuddy) · [Full demo video, 3:34 (recorded on the Cloud edition)](https://github.com/user-attachments/assets/37f4a065-663b-4eee-8d3f-c9c0573270e3)

</div>

> **⚠️ Disclaimer**: this is a research tool. All output is AI-generated, for research reference only, and is not investment advice. Investing involves risk.

---

## ⏱ In 30 seconds

**What it is** — a financial AI assistant that runs on your own computer or server. Give it an LLM key and it can pull quotes and news, run deep single-stock analysis, forecast price paths, manage your watchlist and positions, and follow the SKILLs (analysis methodologies) you write. Chats, positions and investment theses all live in a local database.

**What it is not** — it does not place trades, does not make decisions for you, and does not guarantee forecast accuracy. It is a research assistant that organizes public data, analysis methodology and LLMs.

**Who it is for** — individual investors who can run Docker, hedge-fund researchers and small quant teams; anyone who wants to own their data or plug in their own data sources and methods.

---

## 🚀 Deploy in 5 minutes

**You need**: Docker Desktop (Windows / macOS) or Docker Engine + Compose v2 (Linux) · 20 GB disk (the chat-engine image is ~7.5 GB) · 4 GB RAM · access to `ghcr.io`

> [!IMPORTANT]
> **Only two things to understand before you start**
> 1. **An LLM key (required)**: powers the chat itself. We recommend [DeepSeek](https://platform.deepseek.com/api_keys); any OpenAI-compatible gateway works (Qwen, Claude, GPT, OpenRouter, OneAPI, AIHubMix, ...).
> 2. **Where data comes from (pick one, can wait)**: ① free open-source sources, work out of the box; ② your own MCP / data sources; ③ the platform data pipeline, [free key](https://hunter.agentpit.io/dev/api-keys). See [Data supply: pick one of three](#-data-supply-pick-one-of-three).

**Time**: ~5 minutes with images already pulled; ~10–15 minutes on first pull, depending on your network.

```bash
# 1. Get the code
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
cp .env.example .env

# 2. Generate a secret (Linux / macOS; for Windows PowerShell see docs/01-getting-started.md)
echo "JWT_SECRET=$(openssl rand -base64 48)" >> .env
#    then delete the example line JWT_SECRET=change-me-in-production-please from .env

# 3. Edit .env and fill in the three LLM settings (DeepSeek example)
# LLM_BASE_URL=https://api.deepseek.com/v1
# LLM_DEFAULT_MODEL=deepseek-v4-pro
# LLM_API_KEY=sk-xxxxx
# LLM_SCHEMA_SANITIZE=1                 # required for DeepSeek
# HUNTER_API_KEY=hunt_tools_xxxxx       # optional · platform data pipeline

# 4. Start and open the browser
docker compose up -d
open http://localhost:3100
```

**Then try**:
- Ask "601899 现在多少钱" (what's 601899 trading at) — returns a rich card (live price · 52-week percentile · AI comment)
- Open "策略中心" (Strategy Center) in the top bar — market-wide screener and research desk
- Click "UZI 深度分析" in the sidebar and enter a ticker — a multi-dimension report in 60–300 s

Stuck? Check the [FAQ](#-faq) and [`docs/01-getting-started.md`](./docs/01-getting-started.md), or ask in [Discussions Q&A](https://github.com/agentpit-io/hunter-community/discussions/categories/q-a).

---

## 🔑 Data supply: pick one of three

Apart from the LLM key, you decide where data comes from — **our platform key is not required**:

| Option | Whose key | Data | Good for |
|---|---|---|---|
| **① Free open-source** | none | AKShare (A-shares) · yfinance (US / HK) | Trying it out; coverage gaps are clearly flagged |
| **② Your own tools / MCP** | yours | your broker, data vendor, self-built MCP, or any MCP from the Cline / Cursor ecosystem | You already pay for data; add via "Toolbox ＋" in the sidebar |
| **③ Platform data pipeline** | a `hunt_tools_` key, [free](https://hunter.agentpit.io/dev/api-keys) | Aggregated quotes, financials and news, data for UZI deep analysis, Kronos forecasts | Free sources aren't enough and you want broader data |

The platform key can go in `.env`, or be pasted under "解锁全部工具" (unlock all tools) at the bottom-left of the UI — it takes effect immediately. The platform only counts requests per key; it cannot see your chats or positions.

<details>
<summary><b>Official data-vendor MCP status (updated 2026-09-13)</b></summary>

**Current position: this project ships no built-in MCP integration for any specific financial data vendor until we have formal authorization from each vendor.**

- **HiThink (同花顺) · awaiting partnership**: HiThink lists `fuyao.aicubes.cn/mcp/*` MCP endpoints on its official docs site with self-service API keys, but we have no written authorization to act as an integrator, so it is not pre-configured. We are in contact and hope to partner formally.
- **Tongdaxin (通达信) · awaiting public MCP docs**: Tongdaxin has no publicly authorized API endpoints or developer docs. We do not integrate any unpublished interface, and we are talking with them so paying users can connect properly.

**Available today**: free open-source sources (AKShare / yfinance) + third-party MCPs you bring yourself (generic, no vendor pre-configured).

**If you are a data vendor**: reach out via GitHub Issues or email.
</details>

---

## ✨ What it does

<table>
<tr>
<td width="25%" valign="top">

**💹 Data**
- Live quotes · A / HK / US
- K-lines · financials · news
- Dragon-tiger list · top-10 holders
- Northbound / southbound flow · AH premium
- CNINFO filings · industry classification

</td>
<td width="25%" valign="top">

**🧠 Analysis**
- UZI deep analysis · investor panel
- Kronos forecasting (Tsinghua time-series model)
- Market-wide screener
- Research desk · quant factors and backtests

</td>
<td width="25%" valign="top">

**💬 Interaction**
- Streaming chat · rich cards (quote / news / forecast)
- Three-tier sidebar: sources / toolbox / SKILLs
- Watchlist cards · positions · investment theses
- Strategy Center

</td>
<td width="25%" valign="top">

**🔌 Extend**
- Write SKILLs in Markdown
- Install SKILLs from GitHub in one click
- Connect your own MCP
- Any LLM

</td>
</tr>
</table>

---

## 📚 SKILLs: methodology as a capability

A SKILL is Markdown that explains how to analyze a kind of question, in the **Anthropic Agent Skills standard format** — standard SKILLs from the web work unchanged.

**6 ship with the code** (`skills/`):

| SKILL | Category | Description |
|---|---|---|
| `uzi` | Overall | Research dispatcher: routes to deep research, investor panel, dragon-tiger list, risk scan |
| `investor_panel` | Overall | Investor panel: simulated votes from 9 schools (value, growth, hot money, quant, ...) |
| `deep_analysis` | Research report | Multi-dimension single-stock analysis: fundamentals, technicals, flows and committee verdict |
| `lhb_analyzer` | Events & screening | Dragon-tiger list analysis: identifies hot-money seats, institutions vs. hot money |
| `trap_detector` | Due diligence | Pump-and-dump detector: tip sources, insider rumors, fundamentals mismatch |
| `risk_profile` | Portfolio | Reads/writes risk preference, cash and per-position caps for portfolio suggestions |

**More from the community**: click "＋" in the SKILL row of the sidebar and paste a GitHub URL; preview before installing.

<details>
<summary><b>18 community SKILLs installed on the live demo (and their source repos)</b></summary>

| Source repo | SKILLs |
|---|---|
| [prof-little-bear/cc-equity-research](https://github.com/prof-little-bear/cc-equity-research) | `catalyst_calendar` · `earnings_analysis` · `earnings_preview` · `idea_generation` · `initiating_coverage` · `model_update` · `morning_note` · `sector_overview` · `thesis_tracker` |
| [bmtrnavsky/nanobot-stock-trader](https://github.com/bmtrnavsky/nanobot-stock-trader) | `catalyst_growth_investor` · `longterm_quality_investor` · `stock_data` · `swing_trade_scanner` |
| [algoderiv/agent-skills](https://github.com/algoderiv/agent-skills) | `rice_quant` · `tqsdk` |
| [yennanliu/InvestSkill](https://github.com/yennanliu/InvestSkill) | `invest_stock_eval` |
| [tigersking520/stock-analysis-skill](https://github.com/tigersking520/stock-analysis-skill) | `stock_analysis` |
| Created in the demo UI | 1 |

Read from the demo's `/api/catalog/skills` on 2026-09-16; licenses are those of the source repos.
</details>

<img src="./docs/screenshots/05-sidebar-skill-full.png" alt="Sidebar SKILL category view" width="720" />

Want to write your own? See [Extending HunterCode](#-extending-huntercode).

---

## ☁️ Community or Cloud?

**Comfortable with Docker, want to own your data, want to plug in your own sources or methods → Community (this repo). Just want to open a web page or phone and go, need WeChat / Lark push → [Cloud](https://hunter.agentpit.io).**

<details>
<summary><b>Side-by-side</b></summary>

| Capability | Community (self-hosted) | Cloud ([hunter.agentpit.io](https://hunter.agentpit.io)) |
|---|---|---|
| Chat (bring your own LLM key) | ✅ | ✅ |
| Free data sources (AKShare / yfinance) | ✅ no key from us | ✅ |
| Your own MCP / data sources | ✅ no key from us | ✅ |
| Built-in SKILLs and GitHub SKILL install | ✅ | ✅ |
| UZI deep analysis | ✅ any of the three data options | ✅ |
| Kronos forecasting | ✅ platform pipeline (free) · or self-hosted GPU · [direct access](./docs/kronos-direct-access.md) | ✅ |
| Investment theses (local storage) | ✅ local Postgres | ✅ |
| WeChat push / Lark notifications | ❌ | ✅ |
| Multi-tenant billing | ❌ | ✅ |
</details>

---

## 📖 Going deeper

<details>
<summary><b>🤖 LLM compatibility (tool calling tested on 7 standard cases)</b></summary>

We test each model on 7 standard cases for correct tool calls — for HunterCode, being able to chat is not enough; calling tools correctly is what makes a model usable.

| Model | Access | Tool-call hits | Avg. latency | Rating | Notes |
|---|---|---|---|---|---|
| **DeepSeek v4 pro** | direct `api.deepseek.com` | 6/7 | 30 s | ⭐⭐⭐⭐⭐ direct default | requires `LLM_SCHEMA_SANITIZE=1` |
| **Claude Sonnet 5** | AIHubMix gateway | 7/7 | 25.7 s | ⭐⭐⭐⭐⭐ top pick outside China | containers may hit TLS-fingerprint blocking; use a host proxy |
| **Qwen 3.8 Max** | AIHubMix gateway | 7/7 | 48.1 s | ⭐⭐⭐⭐⭐ top pick in China | slower on deep analysis |
| **Gemini 3.5 Flash** | AIHubMix gateway | 6/7 | 62.3 s | ⭐⭐⭐⭐ cheap and fast | occasional over-calling on edge cases |
| **Doubao Seed 2.1 Pro** | AIHubMix gateway | 6/7 | 103.9 s | ⭐⭐⭐ | connect to Volcano Engine directly |
| **MiniMax M3** | AIHubMix gateway | 6/7 | 77.3 s | ⭐⭐⭐⭐ | thinking leakage stripped by llm-shim |
| **GPT-5.6 sol** | AIHubMix gateway | 5/7 | 24.4 s | ⭐⭐⭐ | picks the wrong tool on some cases |

Tested 2026-08-15 / 08-16. Per-provider `.env` templates (URL, model name, sanitize switch, known pitfalls): [`docs/env-samples/`](./docs/env-samples/). Method and raw data: [`docs/model-testing/`](./docs/model-testing/) ([compat matrix](./docs/model-testing/model-compat-matrix.md) · [runner](./docs/model-testing/scripts/run-golden-cases.py)).

**`<think>` leakage from thinking models**: MiniMax / Qwen thinking / Kimi thinking put reasoning inside the reply. `scripts/llm-shim/shim.py` disables thinking on the request and strips the tags from the response; set `LLM_STRIP_THINK=0` to turn it off.
</details>

<details>
<summary><b>🧠 Investment theses: remember why you bought</b></summary>

Watchlist and positions can store an investment thesis per stock (reason to buy, key assumptions, cost). It lives in local Postgres and never leaves your machine.

To have the AI keep checking whether a thesis still holds, install a community SKILL such as `thesis_tracker` from [prof-little-bear/cc-equity-research](https://github.com/prof-little-bear/cc-equity-research) and review it in chat against fresh data.

> ⚠️ Thesis reviews are AI-generated research references, not investment advice.
</details>

<details>
<summary><b>🛠 Tech stack</b></summary>

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 (App Router) · React 19 · TypeScript 5 · Tailwind CSS · shadcn/ui |
| Backend | FastAPI · Python 3.12 · httpx · loguru |
| Chat engine | Customized [OpenCode](https://opencode.ai) · MCP · Bun |
| Database | Postgres 16 · Redis 7 |
| LLM | Any OpenAI-compatible gateway · Anthropic · Kronos |
| Auth | JWT (HS256) · argon2id · single-user / multi-user switch |
| Deploy | Docker Compose · GHCR images |
</details>

<details>
<summary><b>🏗 Architecture</b></summary>

```
             ┌─────────────────────┐
Browser  →   │  web (Next.js 15)   │ :3100
             └────────┬────────────┘
                      │ /api/* (built-in forwarding · no reverse proxy needed)
             ┌────────▼────────────┐        ┌──────────────────┐
             │  api (FastAPI)      │        │ opencode engine   │
             │  · auth (JWT)       │◄───────┤ MCP tool callbacks│
             │  · data/LLM/forecast│        │ plugins: auth /   │
             │  · SKILL loader     │        │ guard / budget /  │
             │  · analysis agents  │───► platform pipeline (optional · one key)
             └─┬─────────────┬─────┘        │ audit / context   │
                                            └────────┬─────────┘
                                                     │ tool schema sanitizing
                                          ┌──────────▼─────────┐
                                          │ llm-shim  :3999    │──► your LLM gateway
                                          └────────────────────┘
        Postgres           Redis
         :5442             :6479
```

- **web forwarding layer**: Next.js forwards `/api/*` and the chat stream, carrying the JWT
- **hunter-mcp-context**: injects the current user identity into tool calls
- **hunter-guard**: sanitizes MCP schemas (covers DeepSeek rejecting `parameters: null`)
- **hunter-auth**: JWT gate, links sessions to users
- **hunter-budget**: usage accounting with optional caps (off for self-hosting)
- **hunter-audit**: full audit log AUDIT.jsonl
</details>

<details>
<summary><b>📊 Switching data / LLM / forecast providers</b></summary>

| Layer | Env var | Values | When empty |
|---|---|---|---|
| Data | `DATA_SOURCE_PROVIDER` | `hunter` · `akshare` · `yfinance` · `saas` | `hunter` if a platform key is set, otherwise `akshare` |
| LLM | `LLM_PROVIDER` | `openai_compat` · `anthropic` · `saas_gemini` | `openai_compat` |
| Forecast | `FORECAST_PROVIDER` | `kronos_saas` · `kronos_local` · `noop` | `kronos_saas` |

AKShare can be unreliable from inside containers when reaching mainland data sources. Response shapes and details: [`docs/02-providers.md`](./docs/02-providers.md).
</details>

<details>
<summary><b>🔌 Extending HunterCode</b></summary>

<a id="-extending-huntercode"></a>

**1. Write your own SKILL (easiest)**

```
user-skills/
  your-skill/
    SKILL.md
```

```markdown
---
name: your-skill
description: One sentence on when to use it — the model decides from this
---

# Methodology goes here
Steps, what to look at first, what to watch out for.
```

Then `docker compose restart opencode api`. Yours overrides a built-in with the same name. To use our data and tools, add `hunter.needs_tools` to the frontmatter — see [`user-skills/README.md`](./user-skills/README.md).

**2. Install a SKILL from GitHub**: click "＋" in the SKILL row → paste a GitHub URL → preview → install.

<img src="./docs/screenshots/04-sidebar-skill-install.png" alt="Install a SKILL from GitHub" width="720" />

**3. Connect your own MCP**: click "＋" in the Toolbox row and fill in name, type (HTTP / SSE / stdio), URL and key; tool descriptions go straight to the model.

<img src="./docs/screenshots/03-sidebar-mcp-add.png" alt="Connect your own MCP" width="720" />
</details>

---

## ❓ FAQ

<details>
<summary><b>opencode keeps restarting?</b></summary>

Most likely the three LLM settings are incomplete. `docker compose logs opencode --tail 20` names the missing one: `LLM_BASE_URL`, `LLM_DEFAULT_MODEL`, `LLM_API_KEY`. DeepSeek also needs `LLM_SCHEMA_SANITIZE=1`.
</details>

<details>
<summary><b>With DeepSeek, replies only show "深度思考完成" (thinking done), or a 400 "Invalid schema type: null"?</b></summary>

Add `LLM_SCHEMA_SANITIZE=1` to `.env`, then `docker compose up -d` (it must be `up`; `restart` does not re-read `.env`).
</details>

<details>
<summary><b>Deep analysis report is empty?</b></summary>

Without a valid platform key (`HUNTER_API_KEY`) the full data isn't available. Get one free at [hunter.agentpit.io/dev/api-keys](https://hunter.agentpit.io/dev/api-keys), or paste it under "解锁全部工具" at the bottom-left of the UI.
</details>

<details>
<summary><b>Changes to skills/ don't show up?</b></summary>

opencode scans SKILL directories only once at startup:

```bash
docker compose restart opencode          # ~50 s
python scripts/check_skill_sync.py       # compares disk with what opencode actually loaded
```
</details>

<details>
<summary><b>Port already in use?</b></summary>

Change `WEB_HOST_PORT` / `API_HOST_PORT` / `POSTGRES_HOST_PORT` in `.env`, and set `NEXT_PUBLIC_API_URL` to the new API port.
</details>

<details>
<summary><b>More</b></summary>

See common errors in [`docs/01-getting-started.md`](./docs/01-getting-started.md), or ask in [Discussions Q&A](https://github.com/agentpit-io/hunter-community/discussions/categories/q-a).
</details>

---

## 🤝 Contributing

**The easiest contribution is a SKILL** — if you know an analysis method and can write Markdown, that's enough. Next come docs and translation, then code.

- Starter tasks: [`good first issue`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) · SKILLs we'd love: [`skill-wanted`](https://github.com/agentpit-io/hunter-community/issues?q=is%3Aissue+is%3Aopen+label%3Askill-wanted)
- Full workflow: [CONTRIBUTING.md](./CONTRIBUTING.md)
- Made a great analysis with HunterCode? Share it in [Show and tell](https://github.com/agentpit-io/hunter-community/discussions/categories/show-and-tell)

### 🙏 Contributors

The list is maintained by the [All Contributors](https://allcontributors.org) bot. Maintainers comment `@all-contributors please add @username for code` on any issue or PR; see the [contribution types](https://allcontributors.org/docs/en/emoji-key) (code, docs, content, translation, bug reports, ideas and more).

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/forever-ivy"><img src="https://avatars.githubusercontent.com/u/187021713?v=4?s=72" width="72px;" alt="Ziggy xuan"/><br /><sub><b>Ziggy xuan</b></sub></a><br /><a href="https://github.com/agentpit-io/hunter-community/commits?author=forever-ivy" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

### 🧩 Built with HunterCode

| Project | Description |
|---|---|
| [HunterCode Cloud](https://hunter.agentpit.io) | Official hosted edition with WeChat / Lark push |

Built something on HunterCode or maintaining a fork? Tell us in [Discussions](https://github.com/agentpit-io/hunter-community/discussions) and we'll list it here.

---

## 💬 Community & support

| Channel | Link |
|---|---|
| 💡 GitHub Discussions | [Ask · share · suggest](https://github.com/agentpit-io/hunter-community/discussions) |
| 💬 WeChat (join the group) | `agentpit` |
| 📱 WeChat Official Account | `agentpit.io` |
| 🐦 X | [@agentpit_io](https://x.com/agentpit_io) |

**Bugs**: [open an issue](https://github.com/agentpit-io/hunter-community/issues/new/choose) · **Security vulnerabilities**: do not disclose publicly, see [SECURITY.md](./SECURITY.md)

---

## 🗺 Roadmap

- [x] **v0.1** · Self-hosted skeleton, local account auth, pluggable data / LLM / forecast layers
- [x] **v0.2** · opencode chat engine, plugins and MCP, one key for everything, GitHub SKILL install
- [x] **v1.0.0** (2026-09-13) · Market-wide screener, research desk agent, quant factors and backtests isolated per market, SKILL import with attached docs and Chinese descriptions, kronos / truesource MCPs published, chat sessions on named volumes — [full changelog](./CHANGELOG.md)
- [ ] **v1.0.1** · Docs and community groundwork — [milestone](https://github.com/agentpit-io/hunter-community/milestone/1)
- [ ] **v1.1.0** · First-run setup wizard (no `.env` editing), multi-arch images, daily deploy smoke test — [milestone](https://github.com/agentpit-io/hunter-community/milestone/2)

Want a feature? Vote in [Discussions Ideas](https://github.com/agentpit-io/hunter-community/discussions/categories/ideas).

## ⭐ Star history

[![Star History Chart](https://api.star-history.com/svg?repos=agentpit-io/hunter-community&type=Date)](https://star-history.com/#agentpit-io/hunter-community&Date)

---

## ™️ Trademarks & forks

HunterCode · Hunter · AgentPit · 猎鹿人 are trademarks of the AgentPit team.

- **Personal forks, internal deployments, learning and modification**: no renaming needed — go ahead.
- **Public redistribution, hosted services, commercial releases**: please remove the names and logos above; you may state "based on HunterCode Community Edition".

The code is licensed under [Apache 2.0](./LICENSE); the trademark terms do not limit any of your rights to the code. See also [NOTICE](./NOTICE).

<div align="center">

**⭐ If you find it useful, a Star keeps us going**

Made with ❤️ by [AgentPit](https://agentpit.io) team

</div>
