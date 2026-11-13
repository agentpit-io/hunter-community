# HunterCode

[简体中文](./README_zh.md)

Self-hosted AI investment research assistant for the A-share, Hong Kong and US
markets. Bring your own LLM key — market data, conversations and the key itself
all stay on your own instance.

## What you get

| | |
|---|---|
| **Chat with an analyst** | Ask about a ticker in plain language; the agent calls real data tools instead of recalling numbers from memory |
| **Deep analysis** | A multi-section report per ticker (bull/bear, technicals, financials, filings, news) with a coverage percentage so you can see what was missing |
| **Magic screener** | Write a screen in plain language or in ThinkScript-style syntax and run it across the whole market |
| **Watchlists & portfolios** | Track positions, research theses and alerts |
| **SKILLs** | Install methodology packs from GitHub; the model reads them before answering |

## After you click Deploy

1. Copy the **setup token** from the deploy form before submitting — step 0 of
   the first-run wizard asks for it. (It exists so that nobody can reach your
   instance between deployment and your first visit and point it at *their* LLM.)
2. Open the app URL. You will land on the **first-run wizard**.
3. Enter the token → environment self-check → pick an LLM vendor → paste your
   own API key. The wizard runs **three live checks from inside the api
   container** (reachability / a real completion / a real tool call) and shows
   the measured latency of each. It will not let you save if they fail.
4. Pick a data source, finish. The configuration is written to the database and
   **hot-applied — no container restart** (measured: the engine is ready again
   in a few seconds).
5. Register the first account. It automatically becomes the administrator.

You do **not** need to edit any file, at any point.

## What you need to bring

- One OpenAI-compatible LLM API key. DeepSeek, Qwen, Moonshot, OpenAI,
  Anthropic or your own gateway all work. The template deliberately does **not**
  ask for it up front — you paste it in the wizard, where it gets tested before
  it is saved.
- Optionally, a free HunterCode platform key from
  <https://hunter.agentpit.io/dev/api-keys> to unlock live quotes and the full
  tool set. Without it the assistant still works but will tell you honestly that
  it cannot fetch live quotes rather than inventing a price.

## Resources

Measured on the full six-service stack (see the project's `R0` pre-study):

| | |
|---|---|
| Idle | 1171 MB across the six core containers (+~0.6 GB for the four trading services: paper / temporal / temporal-ui / fin-worker) |
| Peak (deep analysis + a full-market scan, concurrently) | **1271 MB** |
| Minimum | 2 vCPU / 3 GB |
| Recommended | 2 vCPU / 6 GB |
| Disk | ≥ 10 GB (images ~4 GB + data) |

The single largest consumer is the `opencode` component at ~869 MB, and it is
almost load-independent — that is resident usage, not a spike risk.

## Where your data lives

| Content | Location |
|---|---|
| Watchlists, portfolios, research, settings, **encrypted LLM key** | the `-pg` PostgreSQL cluster |
| Conversation text and sessions | the `-opencode` PVC at `/home/hunter/.local` |
| SKILLs you install, data packages you import | the `-api` PVCs |

> ⚠️ The PostgreSQL `chat_session_owner` table is **not a backup of your
> conversations** — it only maps session id to user id. The conversation text
> lives only on the opencode volume.

## Upgrading

Change the image tag on all four `ghcr.io/agentpit-io/hunter-community-*`
workloads together and redeploy. The api container runs the database migration
on start (two-phase, with an advisory lock and a bookkeeping table), so there is
**no SQL to run by hand**.

## Notes on this template

- `securityContext.fsGroup: 1001` on the opencode StatefulSet is **required**,
  not cosmetic. A fresh Kubernetes PVC is an empty directory owned by whatever
  the platform picks, and the container runs as uid 1001 — without it the
  entrypoint prints "session directory not writable" and the pod crash-loops.
- PostgreSQL and Redis are provisioned as KubeBlocks `Cluster` resources, the
  same way the other 100+ templates in this repository do it; credentials come
  from the secrets KubeBlocks generates.
- Only the web component has an Ingress. Nothing else is reachable from outside
  the namespace.

## Links

- Source & docs: <https://github.com/agentpit-io/hunter-community>
- Sealos deployment guide (Chinese): <https://github.com/agentpit-io/hunter-community/blob/main/docs/deploy/sealos.md>
- License: Apache-2.0
