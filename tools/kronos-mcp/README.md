# kronos-mcp

<!-- mcp-name: io.github.hangeaiagent/kronos-mcp -->

**Stock price forecasting for your AI agent — China A-shares.**

An [MCP](https://modelcontextprotocol.io) server wrapping **Kronos**, a K-line
time-series model. Ask it for a symbol, get the next N daily candles predicted:
open / high / low / close / volume.

> **Coverage is China A-shares only.** Verified 2026-09-10: `AAPL`, `AAPL.US`,
> `NASDAQ:AAPL`, `00700` and `0700.HK` all return 404 — the upstream has no
> K-line data for them. This is a data limitation, not a symbol-format issue.

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](https://github.com/agentpit-io/hunter-community/blob/main/LICENSE)

---

## Tools

| Tool | What it does |
|---|---|
| `kronos_health()` | Check the service is reachable and your key works. **Call this first when something fails.** |
| `kronos_predict(symbol, pred_len)` | Predict the next `pred_len` daily candles (1–30, default 10) |

`kronos_predict` also returns `expected_return` — last predicted close over last
real close, minus one. That ratio is the number most people actually want.

---

## Quick start

You need an API key first — see [below](#api-key).

### Claude Desktop / Cursor

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kronos": {
      "command": "uvx",
      "args": ["kronos-mcp"],
      "env": { "KRONOS_API_KEY": "oapk_xxx" }
    }
  }
}
```

### Command line

```bash
# no install needed
KRONOS_API_KEY=oapk_xxx uvx kronos-mcp

# or install it
pip install kronos-mcp
KRONOS_API_KEY=oapk_xxx kronos-mcp
```

---

## API key

**This server has no keyless mode, and cannot have one** — the upstream returns
401 without a key.

The reason is cost, not gatekeeping: Kronos runs on our own GPUs and a single
inference takes **30–70 seconds**. Open anonymous access would let a handful of
loops saturate the service, with no way to tell who is doing it.

Get one:

1. Open <https://hunter.agentpit.io/dev/api-keys> and sign in
2. Click **申请 API Key** and choose API type **KRONOS**
3. Copy the `oapk_…` key — **it is shown once**
4. Set it as `KRONOS_API_KEY`

Approval usually lands within hours on a business day. Free, with a default
quota of 1000 calls per key.

If the key is missing, this server does **not** return sample data or a fake
success. It returns an error that tells you exactly where to get a key.

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `KRONOS_API_KEY` | — | **Required.** `oapk_` key of type KRONOS |
| `KRONOS_URL` | `https://kronos.agentpit.io` | Point this at your own deployment if you self-host |
| `KRONOS_TIMEOUT` | `180` | Seconds. GPU inference is 30–70s — don't set this tight |
| `KRONOS_MCP_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `KRONOS_MCP_HOST` / `KRONOS_MCP_PORT` | `0.0.0.0` / `8932` | Remote transports only |

`stdio` is what Claude Desktop, Cursor and `uvx` use. Run a remote transport
only if you want one long-running server serving several clients — and note the
MCP spec marks SSE as deprecated, so prefer `streamable-http` for new setups.

---

## What to expect

**Every call takes 30–70 seconds.** That is the GPU, not a hang. Don't retry
because it feels slow, and don't loop it over dozens of symbols — upstream takes
one symbol per request.

**This is a statistical extrapolation, not investment advice.** The model reads
price history and nothing else: no news, no halts, no earnings dates. It will
drift badly in unusual conditions. Treat the output as one input among many.

---

## Errors you might hit

| Error | Meaning |
|---|---|
| `missing_api_key` | `KRONOS_API_KEY` not set — see [API key](#api-key) |
| `invalid_api_key` (401) | Key wrong, incomplete or revoked. Revocation takes up to 5 min to propagate |
| `wrong_key_type` (403) | Key is valid but isn't a **KRONOS** key — you may have applied for KPRED or FIN_R1 |
| `symbol_not_found` (404) | Use `600519` or `600519.SH`. **A-shares only** — US and HK tickers land here |
| `rate_limited` (429) | Per-IP cap on invalid keys, or your quota is exhausted |
| `upstream_down` (502/504) | GPU service restarting. If it lasts more than a few minutes, please open an issue |

---

## Related

- **[truesource-mcp](https://github.com/agentpit-io/hunter-community/tree/main/tools/truesource-mcp)** — verifiable first-hand market signals: filings, procurement wins, macro data
- **[akshare-mcp](https://github.com/agentpit-io/hunter-community/tree/main/tools/akshare-mcp)** — lets a model explore AKShare's 1000+ China market data functions on its own
- **[hunter-community](https://github.com/agentpit-io/hunter-community)** — the open-source stack these come from

Apache-2.0.
