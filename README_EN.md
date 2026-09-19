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

## ☁️ One-click deploy to a cloud platform

If you would rather not run a server yourself, deploy to one of these and finish the
first-run wizard in the browser.

| Platform | Who it fits | Notes |
|---|---|---|
| [Zeabur](docs/deploy/zeabur.md) | Works inside and outside China; most capable | Template generates the secrets, binds the domain, attaches volumes |
| [Sealos](docs/deploy/sealos.md) | Users in China | Kubernetes template; postgres / redis via KubeBlocks |
| [Railway](docs/deploy/railway.md) | Users outside China | Step-by-step list for building the 6 services in the console, plus how to generate and publish the template |
| [1Panel](docs/deploy/1panel.md) | Your own server + a Chinese control panel | App package; fill in a port and a token |
| [Coolify / Dokploy](docs/deploy/coolify-dokploy.md) | Your own server + a self-hosted PaaS | **Two compose files you can paste as-is** |

> ℹ️ **There are no deploy buttons in this release.** We do not have accounts on any of
> these platforms, have never run a real deployment on them, and have not listed the
> templates in any marketplace — so: docs only, no buttons.
> Every template was **equivalence-verified**: mechanically translated into a compose
> file (same images, same environment variables, random secrets generated the way that
> platform generates them, same volumes, same dependencies) and run locally from empty
> volumes through "six services healthy → finish the wizard → a real conversation →
> a deep-dive analysis → restart without losing data".
> Each doc states plainly what was verified, what was not, and what you must check
> yourself on the platform. If you have an account and try one, please tell us in
> [Issues](https://github.com/agentpit-io/hunter-community/issues) — once a template is
> verified on the real platform, the button goes in.

**Read this before exposing an instance to the internet**: these platforms put your
instance on a public address the moment it is created. Every template therefore turns
off single-user passwordless mode (`HUNTER_SINGLE_USER=0`) and generates a setup token
`HUNTER_SETUP_TOKEN`, which step 0 of the wizard asks for — otherwise whoever opens the
page first gets to point your instance at their own model. The token is visible in the
platform's environment-variable panel.

---

## 🚀 Deploy in 5 minutes

**You need**: Docker Desktop (Windows / macOS) or Docker Engine + Compose v2 (Linux) · 10 GB disk · 4 GB RAM (measured peak ~1.3 GB) · access to `ghcr.io`

All six images ship for **amd64 and arm64**, so Apple Silicon and arm cloud instances run natively — no emulation.

> [!IMPORTANT]
> **Only two things to understand before you start**
> 1. **Where the model comes from.** Recommended: **HunterCode built-in quota** — [get a free `hunt_tools_` platform key](https://hunter.agentpit.io/dev/api-keys) (~30 seconds), pick the first card in wizard step 2, and **you never hunt for an LLM key of your own**. The endpoint and model name are filled in for you, and you get a free daily token allowance. See [the built-in quota guide](./docs/builtin-llm/使用说明.md) (Chinese).
>    **Advanced: bring your own LLM key** — [DeepSeek](https://platform.deepseek.com/api_keys) or any OpenAI-compatible gateway (Qwen, Claude, GPT, OpenRouter, OneAPI, AIHubMix, ...). You can switch between the two paths at any time.
> 2. **Where data comes from (pick one, can wait)**: ① free open-source sources (A-share quotes need `DATA_SOURCE_PROVIDER=akshare` in `.env`); ② your own MCP / data sources; ③ the platform data pipeline, [free key](https://hunter.agentpit.io/dev/api-keys). See [Data supply: pick one of three](#-data-supply-pick-one-of-three).
>    On the built-in-quota path this is **already taken care of** — the same `hunt_tools_` key is the data-supply key, and the wizard tells you it is already unlocked.

**Time**: since v1.1.0 all six services run from **pre-built images — nothing is built locally**. First run is ~3–5 minutes (all of it image downloads); later `up -d` takes seconds.

```bash
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
docker compose up -d
open http://localhost:3100          # finish the setup wizard in the browser — no file edits
```

**There is no step 2.** Since v1.1.0 you never touch `.env`: secrets are generated,
the database migrates itself, all six services run from pre-built images, and the LLM
is configured in the browser.

### The first-run wizard

The first time you open the app (with no LLM configured) it takes you through five steps:

| Step | What it does |
|---|---|
| 1 · Environment check | Six services, migration ledger, secret origin & strength, volume writability, how you're reaching this instance — every item actually probed |
| 2 · Pick a model | **The first card is "use the HunterCode built-in quota" (recommended)** — pick it and the endpoint and model name fill themselves in; all you need is one `hunt_tools_` key. The cards below it are the bring-your-own-key path, carrying **measured** tool-call hit rates and latencies (from [`docs/model-testing/`](./docs/model-testing/model-compat-matrix.md)) |
| 3 · Paste the key, test it now | Reachability → chat → tool call. Failures are classified, and **you cannot save a config that did not pass**. On the built-in-quota path it also points deep analysis at `hunter-deep` and unlocks data supply with the same key |
| 4 · Data supply | Free open-source sources / platform data pipeline / your own MCP — pick one, or skip. On the built-in-quota path it just says "already unlocked with the same key" |
| 5 · Done | Applied live, **without restarting any container**, plus three example questions to get you into the chat |

<p align="center">
  <img src="./docs/screenshots/builtin-llm/02-第2步-内置额度是第一张卡.png" alt="Step 2 · the built-in quota is the first card" width="760" />
</p>

All screenshots: [`docs/screenshots/builtin-llm/`](./docs/screenshots/builtin-llm/) (built-in quota,
end to end) and [`docs/screenshots/setup-wizard/`](./docs/screenshots/setup-wizard/) (bring-your-own-key path).

> [!IMPORTANT]
> **If this instance is reachable from the public internet, set `HUNTER_SETUP_TOKEN` in `.env` first**
> (any random string — `openssl rand -base64 24`), then `docker compose up -d`.
> Without it, whoever opens the page first gets to configure the LLM — the wizard decides
> "is this local?" from HTTP forwarding headers, and on a bare `docker compose` (no nginx or
> other reverse proxy in front) a visitor can forge those. With the token set, the wizard asks
> for it up front and locks for 15 minutes after 5 wrong tries.
> Not needed for a machine only you can reach.

**The old way (hard-coding it in `.env`) still works and takes priority**: an instance with
`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL` set is **locked** — the wizard shows the
values read-only and cannot change them (that is how the demo site runs). To switch models,
edit `.env` and run `docker compose up -d` (**not `restart`** — restart does not re-read `.env`).
The built-in quota can be hard-coded the same way (one-click deploy templates do exactly that):

```bash
LLM_BASE_URL=https://hunter.agentpit.io/api/saas/llm/v1
LLM_API_KEY=hunt_tools_xxxxxxxxxxxxxxxx      # the platform key — no second key needed
LLM_DEFAULT_MODEL=hunter-chat
LLM_SCHEMA_SANITIZE=0                        # sanitizing happens at the gateway
```

> Without an LLM configured all six services still come up healthy; sending a message just
> returns a plain "the LLM is not configured yet" notice. Want to do it later? Click
> "先进对话页(稍后再说)" on the last step. To run the wizard again: Settings → 大模型 →
> "重新运行初始化向导".

**Working on the code?** Stack the development override file on top. It brings back local builds and every source bind mount (edits under `apps/web/public` and `scripts/` take effect immediately):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
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
| **① Free open-source** | none | AKShare (A-shares) · yfinance (US / HK) | Trying it out; A-share quotes need `DATA_SOURCE_PROVIDER=akshare` in `.env` (see below the table) |
| **② Your own tools / MCP** | yours | your broker, data vendor, self-built MCP, or any MCP from the Cline / Cursor ecosystem | You already pay for data; add via "Toolbox ＋" in the sidebar |
| **③ Platform data pipeline** | a `hunt_tools_` key, [free](https://hunter.agentpit.io/dev/api-keys) | Aggregated quotes, financials and news, data for UZI deep analysis, Kronos forecasts | Free sources aren't enough and you want broader data |

**Turning on free sources**: with `DATA_SOURCE_PROVIDER` left empty, the default is `hunter` (the platform pipeline). Without a key, an A-share quote request prompts you to apply for one rather than silently switching to a free source; to get A-share quotes without a key, set `DATA_SOURCE_PROVIDER=akshare` in `.env` and run `docker compose up -d`. HK / US quotes and daily bars already use the built-in free channels (Tencent / Sina) by default, so there is nothing to set for them.

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
| Data | `DATA_SOURCE_PROVIDER` | `hunter` · `akshare` · `yfinance` · `saas` | `hunter` (even without a platform key) |
| LLM | `LLM_PROVIDER` | `openai_compat` · `anthropic` · `saas_gemini` | `openai_compat` |
| Forecast | `FORECAST_PROVIDER` | `kronos_saas` · `kronos_local` · `noop` | `kronos_saas` |

`DATA_SOURCE_PROVIDER` only decides where live quotes come from when no platform key is configured: A-shares use it directly, while HK / US try the built-in free channel first and fall back to it only if that fails. With a key, it is ignored. AKShare can be unreliable from inside containers when reaching mainland data sources. Response shapes and details: [`docs/02-providers.md`](./docs/02-providers.md).
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

### 💝 Thanks for sharing

- Community: [LINUX DO](https://linux.do/) — Chinese developer community

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
- [x] **v1.0.1** (2026-09-17) · Chat-engine image **7.56 GB → 618 MB** (download 1.70 GB → 153 MB), api image 1.32 GB → 909 MB, entrypoint hardening, daily deploy smoke CI, docs and community groundwork — [how and measurements](./docs/image-slim/)
- [x] **v1.1.0** (2026-09-18) · Works out of the box with no config file editing — [milestone](https://github.com/agentpit-io/hunter-community/milestone/2)
  - [x] All six services from pre-built images, amd64 + arm64 (`v1.1.0-rc1`)
  - [x] Database migrations run automatically on api start; `JWT_SECRET` and friends generated on first boot
  - [x] LLM settings can live in the database (no longer `.env`-only) and apply live without restarting containers
  - [x] Graphical first-run wizard (pick a model → paste the key and test it on the spot → start chatting)
  - [x] Deployment for five platforms (Zeabur / Sealos / Railway / 1Panel / Coolify·Dokploy)
        — templates and docs are ready and equivalence-verified, but **none has been run
        on the real platform and none is listed in a marketplace**, so this release ships
        no deploy buttons. See [One-click deploy](#-one-click-deploy-to-a-cloud-platform)
  - Progress and measurements: [`docs/setup-wizard/`](./docs/setup-wizard/)
- [ ] **Next** · Verify and list each platform once we have accounts (buttons go in then), China mirrors, arm64 on real hardware

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
