# Provider layer

Hunter Community abstracts three integration points behind small
interfaces (`apps/api/app/providers/`):

- **Data source** · quote · kline · news
- **LLM** · chat · chat_stream · list_models
- **Forecast** · K-line prediction (Kronos-shaped output)

You pick an impl per layer via `.env`. Users can override with their own
SaaS keys per-account via the `/settings` page.

## Data source

`DATA_SOURCE_PROVIDER` = `akshare` (default) · `yfinance` · `saas`

| impl | Coverage | Free? | Notes |
|---|---|---|---|
| `akshare` | A-shares | ✅ | Wraps `akshare` pip package · sync API bridged to async |
| `yfinance` | US · HK · rough A-share | ✅ | Needs internet reach to Yahoo Finance |
| `saas` | Everything hunter aggregates | free tier | Requires `HUNTER_SAAS_DATA_URL` + `HUNTER_SAAS_DATA_KEY` · [get a key](https://hunter.agentpit.io/dev/api-keys) |

All impls return the same shape:

```json
// get_quote(code) →
{
  "code": "600519",
  "name": "贵州茅台",
  "price": 1728.5,
  "change_pct": 1.24,
  "volume": 1234567,
  "open": 1710.0,
  "high": 1740.2,
  "low": 1705.0,
  "prev_close": 1707.4
}
```

## LLM

`LLM_PROVIDER` = `openai_compat` (default) · `anthropic` · `saas_gemini`

| impl | Base URL example | Notes |
|---|---|---|
| `openai_compat` | `https://api.openai.com/v1` · `https://openrouter.ai/api/v1` · your own OneAPI | Most flexible · use your existing key |
| `anthropic` | (SDK default) | Requires adding `anthropic` to `apps/api/requirements.txt` |
| `saas_gemini` | `https://oneapi.hermes.agentpit.io/v1` | Free tier via hunter's Gemini gateway |

Required env for `openai_compat` / `saas_gemini`:
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_DEFAULT_MODEL` (default `gpt-4o-mini`)

For `anthropic`:
- `LLM_API_KEY`
- `LLM_DEFAULT_MODEL` (default `claude-sonnet-4-6`)

Interface (`apps/api/app/providers/llm/base.py`):

```python
class ILLM:
    async def chat(messages, model=None, **kw) -> dict
    async def chat_stream(messages, model=None, **kw) -> AsyncIterator[str]
    def list_models() -> list[str]
```

## Forecast (Kronos)

`FORECAST_PROVIDER` = `noop` (default · UI hides forecast SKILL) ·
`kronos_local` · `kronos_saas`

| impl | Notes |
|---|---|
| `noop` | Placeholder · Kronos disabled |
| `kronos_local` | POST `/predict` on `KRONOS_LOCAL_URL` · needs GPU |
| `kronos_saas` | POST `/predict` on `HUNTER_SAAS_KRONOS_URL` · needs `HUNTER_SAAS_KRONOS_KEY` |

Return shape:

```json
{
  "code": "600519",
  "pred_len": 10,
  "ohlc": [[o, h, l, c], ...],
  "confidence": 0.72,
  "model": "kronos-v1",
  "generated_at": "2026-08-10T14:32:00Z"
}
```

## Per-user overrides via `/settings`

Any logged-in user can add their own SaaS URL + Key on the **SaaS 加速**
tab. The key is encrypted at rest (AES-256-GCM with a KDF derived from
`JWT_SECRET`) and takes precedence over the env-configured provider for
that user's requests.

Test buttons hit each service's `/models` or `/health` endpoint and
return latency + HTTP status without persisting anything.

## Adding a new impl

1. `class MyDataSource(IDataSource): ...` in `providers/data_source/my.py`
2. Wire in `providers/data_source/__init__.py::get_data_source()` under a
   new `DATA_SOURCE_PROVIDER=my` branch
3. Add any pip deps to `apps/api/requirements.txt`
4. Set `DATA_SOURCE_PROVIDER=my` in your `.env`

That's it — no other code needs to change. Same shape applies to LLM and
Forecast layers.
