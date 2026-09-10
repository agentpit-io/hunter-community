# truesource-mcp

<!-- mcp-name: io.github.hangeaiagent/truesource-mcp -->

**First-hand market signals your AI agent can actually cite.**

An [MCP](https://modelcontextprotocol.io) server wrapping **TrueSource** — a
crawler and AI-retrieval stack that watches things which have *already happened*
and carry a verifiable source: exchange filings, government procurement awards,
R&D expansion moves, northbound holdings, and official macro releases.

It does not summarise analyst reports and it does not emit buy/sell calls. Every
signal comes with a date and where it came from.

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](https://github.com/agentpit-io/hunter-community/blob/main/LICENSE)

---

## Tools

| Tool | What it does | Speed |
|---|---|---|
| `truesource_procurement(days, limit)` | Recent government procurement awards — who is actually winning contracts | fast |
| `truesource_macro(days)` | Macro releases from the statistics bureau, customs and industry bodies | fast |
| `truesource_daily_brief(symbols)` | Signal digest + alert level for a batch of tickers (last 3 days) | fast |
| `truesource_alert_signals(symbols)` | Only signals that crossed an alert threshold (last 26 hours) | fast |
| `truesource_report(symbol)` | Full research report — **35 pre-built AI-compute names only** | fast |
| `truesource_scout(symbol, name)` | Live full collection for **any A-share** | **30–60s** |

Procurement is the most distinctive one: a contract award is a **fact that has
already occurred**, which is a harder input than a forecast.

---

## Quick start

You need an API key first — see [below](#api-key).

### Claude Desktop / Cursor

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "truesource": {
      "command": "uvx",
      "args": ["truesource-mcp"],
      "env": { "HUNTER_API_KEY": "hunt_tools_xxx" }
    }
  }
}
```

### Command line

```bash
# no install needed
HUNTER_API_KEY=hunt_tools_xxx uvx truesource-mcp

# or install it
pip install truesource-mcp
HUNTER_API_KEY=hunt_tools_xxx truesource-mcp
```

---

## API key

**This server has no keyless mode, and cannot have one** — the upstream gateway
returns 403 without a key.

The reason is cost: `truesource_scout` runs real crawlers plus a Gemini search on
every call, so **each invocation spends money on third-party APIs**. Anonymous
access would be trivially expensive to abuse and impossible to attribute.

Get one at <https://hunter.agentpit.io/dev/api-keys> — sign in, click
**申请 API Key**, copy the `hunt_tools_…` key (shown once), set it as
`HUNTER_API_KEY`.

If the key is missing, this server does **not** return sample data or a fake
success. It returns an error that tells you exactly where to get a key.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `HUNTER_API_KEY` | — | **Required.** `hunt_tools_` key |
| `TRUESOURCE_URL` | `https://hunter.agentpit.io/api/saas/truesource` | Point at your own deployment if you self-host |
| `TRUESOURCE_TIMEOUT` | `20` | Seconds. `scout` gets its own 120s floor regardless |
| `TRUESOURCE_MAX_ITEMS` | `40` | Cap on items per response |
| `TRUESOURCE_MCP_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `TRUESOURCE_MCP_HOST` / `_PORT` | `0.0.0.0` / `8933` | Remote transports only |

`stdio` is what Claude Desktop, Cursor and `uvx` use. Run a remote transport only
if one long-running server should serve several clients — and prefer
`streamable-http`, since the MCP spec marks SSE as deprecated.

---

## Three things that will bite you otherwise

**`grey` is not `green`.** In `daily_brief`, an alert level of `grey` means *no
signal was collected for this ticker in the last 3 days*. That may mean the stock
was quiet, or it may mean coverage missed it. It does not mean "safe".

**An empty result is a conclusion, not a failure.** `alert_signals` returning
nothing means these tickers crossed no threshold in the last 26 hours. That is
information. This server never dresses an empty result up as an error, and never
fills it with placeholder rows.

**Truncation is always disclosed.** Responses are capped at `TRUESOURCE_MAX_ITEMS`
(40 by default). When that bites, the payload carries `truncated: true`, the real
`total`, and a note not to draw conclusions from the visible slice. Raise the cap
or narrow the date range instead.

`truesource_report` also will **not** silently fall back to `scout` when a ticker
isn't one of the 35 pre-built names. Scout costs 30–60 seconds and real money —
that choice should be made deliberately, not happen behind your back.

---

## Errors you might hit

| Error | Meaning |
|---|---|
| `missing_api_key` | `HUNTER_API_KEY` not set — see [API key](#api-key) |
| `invalid_api_key` (401) | Key wrong, incomplete or revoked |
| `forbidden` (403) | Key valid but lacks access to this endpoint |
| `not_found` (404) | For `report`: ticker isn't a pre-built name — use `truesource_scout` |
| `timeout` | `scout` genuinely takes 30–60s; raise `TRUESOURCE_TIMEOUT` |
| `upstream_down` (502/503) | Crawler service is down; retry later |

---

## Related

- **[kronos-mcp](https://github.com/agentpit-io/hunter-community/tree/main/tools/kronos-mcp)** — K-line forecasting for A-shares, US and HK equities
- **[akshare-mcp](https://github.com/agentpit-io/hunter-community/tree/main/tools/akshare-mcp)** — lets a model explore AKShare's 1000+ China market data functions on its own
- **[hunter-community](https://github.com/agentpit-io/hunter-community)** — the open-source stack these come from

Apache-2.0.
