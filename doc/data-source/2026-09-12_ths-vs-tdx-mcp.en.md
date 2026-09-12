# HiThink (THS) vs Tongdaxin (TDX) Official MCP · Field Test: End-to-End API Key Verification

> 2026-09-12
> Upstream: Three principles agreed between the CEO and @云清 —— ① Must be official, ② Users apply for their own key, ③ Our code carries zero secrets
> Companion doc: `20260912-通达信MCP_实测调研_三条路线与压力测试.md` (the TDX-reverse-engineering routes have been recommended for shutdown)
> This doc does exactly one thing: **use real keys to walk both chains end-to-end**, no second-hand reporting.
> Originally titled "HiThink Financial Data · Field Study"; renamed to a two-vendor comparison after TDX's official MCP was successfully connected on the night of 9-12.
>
> **This is the English translation of `2026-09-12_ths-vs-tdx-mcp.md`. All numeric data, endpoints, code blocks, and JSON-RPC captures are preserved verbatim from the original Chinese source of truth.**

---

## 0. TL;DR

**It works. Official MCP exists, API keys are self-issued, data accuracy verified, rate limits are generous.**

**Only gap: across 387KB of documentation there is zero mention of terms of service, commercial use, or pricing** —— and that is exactly what the CEO cares about most.

---

## 1. Line-by-line check against the three principles

| CEO requirement | HiThink (THS) | Tongdaxin (TDX) (corrected on the night of 9-12) |
|---|---|---|
| Must be **official** | ✅ Hard evidence in §2 | ✅ Endpoint hard-coded in the TdxClaw client: `txmcp.tdx.com.cn:3001/clawmcp` |
| **Users can self-issue keys** | ✅ Login → issue → use, three steps, no fee observed | ✅ Self-serve creation on `vip.tdx.com.cn` store, **paid credit-based** (CEO topped up ¥188) |
| **Our code carries zero keys** | ✅ Native support: `X-api-key` header / env var | ✅ Native support: `Authorization: Bearer TDX-…` / env var `TDX_MCP_KEY` |
| Backend & UI must support selection | ✅ Doable (6 independent MCP endpoints, naturally selectable) | ✅ Doable (single endpoint, 20 tools) |

> The earlier "❌ no application entry / all reverse-engineered by individuals" claim in the TDX column **was wrong**. See
> `20260912-通达信官方MCP_最终定论_真连成功与鉴权验证.md` for details. Both vendors are now **fully equivalent on these three principles**.

---

## 2. Identity verification —— this is HiThink official, with hard evidence

First the suspicious signal: the GitHub account `HiThink-Tech` is a **personal account, not an organization** — registered 2026-06-09, 1 repo, 89 followers, company/bio/email all blank. Taken alone, this looks like a hobby project.

**But two pieces of evidence nail down the identity:**

1. The doc site loads `s.thsi.cn/js/chameleon/...`
2. **The presigned URL for whole-market data export lands on `o.thsi.cn`**

`thsi.cn` is HiThink's official resource domain. **Third parties cannot forge presigned URL hosts.**

---

## 3. MCP field test —— acting as an actual client

Not "the docs mention MCP" — **we spoke JSON-RPC directly**:

```
① initialize
   ✅ server fuyao-a-share-mcp 1.0.0 · protocol version 2025-06-18
      Session-Id: a3ca2769-dc91-4bd5-bf3d-1c6e300d0e3d

② tools/list
   ✅ 21 tools

③ tools/call  get_a_share_prices_snapshot {"thscodes":"600519.SH"}
   ✅ {"code":0,"data":{"item":[{"thscode":"600519.SH","last_price":1275.16,
      "open_price":1285.15,"high_price":1286.15,"low_price":1263.01,...}]}}
```

### 6 hosted MCP servers (no install, connect the URL directly)

```
fuyao-a-share-mcp        https://fuyao.aicubes.cn/mcp/a-share
fuyao-a-share-index-mcp  https://fuyao.aicubes.cn/mcp/a-share-index
fuyao-fund-mcp           https://fuyao.aicubes.cn/mcp/fund
fuyao-futures-mcp        https://fuyao.aicubes.cn/mcp/futures
fuyao-options-mcp        https://fuyao.aicubes.cn/mcp/options
fuyao-meta-mcp           https://fuyao.aicubes.cn/mcp/meta
```

Auth: gateway checks the `X-api-key` header. **Same pattern as our own `kronos-mcp` / `truesource-mcp`** —— user brings the key, code holds zero secrets.

First 10 of the 21 tools on a-share:

```
get_a_share_prices_snapshot            get_a_share_prices_historical
get_a_share_corporate_actions_adjustment_factors
get_a_share_financials_income_statements
get_a_share_financials_balance_sheets  get_a_share_financials_cash_flow_statements
get_a_share_financials_indicators      get_a_share_valuations_snapshot
get_a_share_calendar_trading_days      get_a_share_special_data_limit_up_pool
```

---

## 4. API Key flow (self-service, verbatim from official)

```
Calling HiThink Financial Data takes 3 steps: Login → Issue API Key → Call with X-api-key

1. Log in to the doc site with a HiThink account, get redirected to the "API Key Management" page
2. Click "Create API Key", give it a nickname (e.g. my-dev-key)
3. Include the header X-api-key: <your-api-key>

MCP integration: same key reused for auth, injected via env var API_KEY
```

**API keys are bound to the HiThink account and manageable from the management page.**

---

## 5. Data field test

### 5.1 Accuracy —— three-source reconciliation matches

| Symbol | HiThink | eltdx (TDX protocol) | Tencent `qt.gtimg.cn` |
|---|---|---|---|
| 600519 Moutai | 1275.16 | 1275.16 | 1275.16 |
| 000001 Ping An Bank | 11.74 | 11.74 | 11.74 |

**Three independent sources match exactly.**

### 5.2 ⭐ Dragon-Tiger List 63 rows —— exactly our weakest table today

Our `a.lhb` **only has 17 tickers with records** (via akshare). HiThink returns 63 rows in one call, with **institutional desks and hot-money desks split**:

```
data.stock_items      (individual stock list)
data.hot_money_items  (hot-money desk list)

First row: Zhongbai Group 000759 · concept_list[Duty-free/Farming/Prepared foods] · change · net_value
```

### 5.3 ⭐ Whole-market 10-year daily K: 172.2 MB Parquet

```
GET /api/dump/market-dumps/daily-k/download-url
→ presigned_url (300-sec TTL) · host o.thsi.cn
→ Range fetch first 4 bytes = "PAR1"  ✅ genuine Parquet
→ Content-Range shows full file 172.2 MB
```

Also available: `/adjustment-factors/download-url` (corporate action adjustment factors).

**This is a dimensional-reduction attack on our Factor Plaza**: current state fetches thousands of tickers one by one, with `akshare_client.py` timing out at 15s and retrying repeatedly; the replacement reads a single file.

### 5.4 Other verified

- HiThink hot list 30 rows (Fenghua Advanced Tech rank 1, heat 5867772)
- Limit-up pool / broken-limit pool / anomaly reasons: endpoint works (`code=0`), 0 rows on the day

### 5.5 Rate limit gradient —— generous, zero 429s

| Concurrency | Rate | Median latency | Return code |
|---|---|---|---|
| 1 | 0.5/sec | 1717ms | All `code=0` |
| 4 | 2.1/sec | 1700ms | All `code=0` |
| 8 | 3.3/sec | 1684ms | All `code=0` |

**120 requests, zero throttling, zero exceptions.** The ~1700ms latency is due to overseas access + local proxy; will be far faster on domestic servers.

Docs explicitly say: **"This service currently does not limit cumulative call count"**; throttling returns HTTP 429 or `code=4001`.

---

## 6. ⚠️ Three gaps

### 6.1 🚨 Most critical: no ToS to be found

Searching 387KB of full-text documentation:

```
Terms of service / usage agreement / commercial / commercial use / resale / redistribution / free / billing / price / fees
→ Zero hits, all of them
```

All "price" hits are about stock prices.

**You can register, get a key, and use it, but "whether commercial use is allowed" is documented nowhere.**

This is exactly the line the CEO drew:

> This isn't for us to use — it's for users to use, **so we cannot hand users a pretext for illegality**

**Action: return to `fuyao.aicubes.cn` "API Key Management" page and hunt for a user-agreement link; if no agreement checkbox appeared at signup, ask support directly.**
Look specifically for one of these: **prohibits resale/redistribution of data**, **for personal / non-commercial use only**.

### 6.2 Some capabilities return `2004` —— means "not yet released", not "insufficient permission"

```
/api/a-share/capital-flow/snapshot  → code=2004
```

`2004` doesn't appear anywhere in the 387KB docs (only `2001 unauthenticated` / `2003 insufficient permission` are defined).

**But the API index gives the answer:**

> Capital flow | `/api/a-share/capital-flow` | A-shares main-force capital real-time snapshot and history, **planned for future integration with HiThink AI Client, stay tuned**

Also:

> Some data and analysis capabilities are planned for future integration with the HiThink AI Client, **and are not usable in the current version of this project**

**So `2004` = capability not yet open, not a paywall.** This is a more optimistic reading than "needs plan upgrade" but still inference — the error code is undocumented.

### 6.3 ⚠️ Defect in my own test: batch not tested properly

```
10 tickers → returns 8
50 tickers → returns 8
100 tickers → returns 8
```

Looks like the batch limit is 8. **Actually my input construction is broken** — I only used 8 distinct codes and repeated them N times; the server dedupes.

**Actual batch capacity is unknown; need to re-test with 100 distinct codes.** This finding cannot stand as a conclusion.

---

## 7. Comparison with TDX (both verified live, updated night of 9-12)

> The previous version of this section claimed TDX had "no official MCP, no signup entry" —— **that was wrong**. After unpacking the TdxClaw installer we found the
> hard-coded official endpoint and connected with a real key. See `20260912-通达信官方MCP_最终定论_真连成功与鉴权验证.md`.

| | HiThink (THS) | Tongdaxin (TDX) |
|---|---|---|
| Official MCP endpoint | `https://fuyao.aicubes.cn/mcp/a-share` etc. **6 endpoints** | `https://txmcp.tdx.com.cn:3001/clawmcp` **1 endpoint** |
| Transport | streamable-http | streamable-http |
| Auth header | `X-api-key: <key>` | `Authorization: Bearer TDX-<key>` |
| No key / fake key | Rejected | **401** (measured: no key "Missing Bearer authorization", fake key "Unauthorized access") |
| Live-connect result | `initialize` 200 · 21 tools · `tools/call` Moutai 1275.16 | `initialize` 200 · `tdx-finance-mcp-server 1.0.0` · **20 tools** · `tdx_quotes` Moutai 1275.16 |
| Key acquisition | 3-step self-serve on doc site, no fee observed | Store "Points & Key Management" self-serve, **paid credit-based** |
| News/disclosure/research/macro | ❌ Docs say not currently provided | ✅ `wenda_news/notice/report/macro_query` × 4, news call 634ms returns real data |
| Whole-market bulk export | ✅ 172MB Parquet, 10-year daily K | Not observed |
| Official Skills | None | ✅ 46 in SkillHub (`tdx.com.cn/skillhub`), ZIP contains only SKILL.md, depends on the 10 tools above |
| Data accuracy | ✅ Three-source match | ✅ Three-source match |
| **Commercial / resale terms** | ⚠️ **Not found** | ⚠️ **Not found** |

### 7.1 Unified pattern for MCP / Skill usage with a key (identical across both)

Both vendors' official MCPs are **hosted remote services + user-brought key**, same pattern as our own `kronos-mcp` / `truesource-mcp`. Only two details differ:

```
                     HiThink                          TDX
MCP endpoint         fuyao.aicubes.cn/mcp/*          txmcp.tdx.com.cn:3001/clawmcp
Auth header          X-api-key: <key>                Authorization: Bearer TDX-<key>
Env var (client)     API_KEY                         TDX_MCP_KEY (called tdxMcpKey in TdxClaw)
Skill layer          —                               SkillHub ZIPs contain only prompts; tools come from the MCP above
```

**Skill ≠ MCP.** TDX SkillHub's 46 skills are pure SKILL.md files; the ZIPs contain no server code;
they work by calling `tdx_api_data` / `tdx_quotes` / `wenda_*` and 7 other tools, which are provided by the official MCP endpoint.
**Without a key you cannot connect the MCP, and the Skills spin their wheels.** This is precisely what the CEO meant by "Skills are only shells; the data tools are the real layer to integrate."

Integration on our platform (one flow for both):

1. Add two options in the "Data Source" UI, one key input each (prominent location, per CEO)
2. Backend attaches the right header to the right endpoint based on user choice; **our code holds zero keys**
3. If user hasn't filled a key → per "empty is better than fake", return a clear error + signup link; do not fall back to our own key

⚠️ Note TDX has **two** possible data paths, only one is compliant:

```
Official MCP   txmcp.tdx.com.cn:3001/clawmcp   strict auth    ← use this
Internal endpoint tdxhub.icfqs.com:7615/TQLEX  no auth         ← what community plugins hit directly, bypassing the gateway; do not use
```

Both paths carry identical data (Moutai 1275.16 matches exactly), differing only in the auth gateway. **Integrate the official MCP, not TQLEX.**

**Both vendors now reach the same milestone: technically all-clear, ToS blank.** The only blocker requires a human ask (see §8.1).

### 7.2 TDX official MCP: evidence chain for "officially provided" + accessible financial data (measured night of 9-12)

**Why we can conclude this MCP is officially provided (four points, all empirical):**

1. **Endpoint is hard-coded in the official client** —— TdxClaw 1.0.31 main process `const ol = "https://txmcp.tdx.com.cn:3001/clawmcp"`,
   domain `tdx.com.cn`; the client also has a domain whitelist accepting only `*.tdx.com.cn` / `*.icfqs.com`
2. **Server self-identifies** —— `initialize` returns `server = tdx-finance-mcp-server 1.0.0`, protocol `2025-06-18`
3. **Auth is real** —— purchased-store key works; no key → 401 "Missing Bearer authorization"; fake key → 401 "Unauthorized access";
   OAuth metadata `authorization_servers: ["https://auth.tdx.com.cn"]`
4. **Data is real** —— Moutai 1275.16 matches Tencent / HiThink / eltdx three independent sources

The "Wenda MCP" permission ticked when creating the key corresponds to this exact endpoint. The official docs **do not publish this address**
(they only tell users to fill the key into TdxClaw), but the key-creation page explicitly says "You may also develop your own integration based on the MCP protocol" —— third-party direct connection is a supported usage, just undocumented.

**Financial data accessible with this key (`tools/list` × 20, in two categories):**

```
Data tier (17) —— usable server-side
  tdx_quotes                 real-time quotes / L5 depth              ✅ verified Moutai 1275.16
  tdx_kline                  K-lines (minute–month, adjusted)         ✅ verified 100 bars
  tdx_api_data               F10: financials / shareholders / capital flow / dragon-tiger / dividends / equity, 30+ submodules
  tdx_lookup_stock           code/name search (A/HK/US/fund/index)
  tdx_screener               natural-language screener               ✅ verified 42 results
  tdx_indicator_select       indicator lookup / industry valuation comparison
  tdx_security_deep_info     natural-language deep profile
  tdx_futures_quotes / tdx_futures_deep_info   futures
  tdx_option_t_quote         option T-quote
  tdx_technical_indicator_lookup / _query      technical indicators
  tdx_ai_listening
  wenda_news_query           news                                     ✅ verified 634ms
  wenda_notice_query         disclosures
  wenda_report_query         research reports
  wenda_macro_query          macro (requires "pipe-format" query)
Client UI tier (3) —— only meaningful inside TdxClaw, useless server-side
  tdx_present_research_ui  tdx_open_data_function_panel  tdx_add_favorite
```

**`tdx_kline` field-test record:**

```
▶ tdx_kline {code: 600519, setcode: 1}
【Kweichow Moutai】600519 | Last 1275.16 (-0.78%) | K-line count: 100 bars
Range high 1294.99 · low 1263.01
ItemHead: Data / Second / Open / High / Low / Close / Amount / VolInStock / Volume / Settle / up / down
AttachInfo: Name / HqDate 20260911 / Close 1285.13 / Open / MaxP / MinP / Volume / Amount / fHSL(turnover) / lBelongHY(industry)
```

**`tdx_kline` parameters (verbatim from tool definition):**

| Param | Values |
|---|---|
| `code` | Numeric ticker code |
| `setcode` | `1` SSE (leading 6/68) · `0` SZSE (00/30) · `2` BSE (43/83) |
| `period` | `0` 5-min (default) · `1` 15-min · `2` 30-min · `3` 1-hour · **`4` daily** · `5` weekly · `6` monthly |
| `wantNum` | 1–1000, default 100; ~250 for a year of daily |
| `startxh` | Start offset (reverse), 0 = latest; used for paginated history |
| `tqFlag` | `0` no adjustment · **`1` forward-adjusted (default)** · `2` back-adjusted |
| `hasIpoPrice` | Include IPO price, default 0 |

⚠️ In my test I passed `period: "day"`, not a legal value, so the server fell back to the default 5-min line (see `Second: 53100` in the row). **For daily K pass `"4"`.**

**Credit consumption:** as of this section, the official MCP has been called ~10 times cumulatively (initialize×4, tools/list×3, tools/call×5).
I cannot see how many credits per call from my side; only the CEO can reconcile via the store's "Key Usage Ledger" —— see §8.1.

---

## 8. Next steps

### 8.1 Only you / CEO can do this (one item per vendor + one shared)

| Who | Task | Why |
|---|---|---|
| HiThink | Return to `fuyao.aicubes.cn` "API Key Management", find user agreement, screenshot | 387KB of docs, zero ToS |
| TDX | Look for ToS on the store purchase / key-creation page; `auth.tdx.com.cn` is the OAuth authorization server and may carry a policy page | Same zero-ToS situation |
| TDX | Check the store's **"Key Usage Ledger"**: I ran ~6 real calls the night of 9-12 (initialize×3, tools/list×2, tools/call×4), reconcile credits consumed → per-call cost → how many calls ¥188 covers | Credit-based, cost must be sized |

Three places, three same keywords: **"resale" "redistribution" "commercial use"**. This is the shared and sole compliance gap for both paths.

### 8.2 After ToS clears

In the order the CEO gave:

1. ✅ **We apply for a key, verify the API** —— done in this doc
2. **Code integration** —— add `provider="ths"` branch, user fills their own key
3. **UI must show data source & settings entry** (CEO said "prominent location" twice)
4. **Publish README & social**

⚠️ **Iron rule: our key is only for dev testing. Never into repo, never into image, never fetched on behalf of users.**
The moment we use our own key to fetch data for a user, it becomes data resale —— which is exactly what we must avoid.

### 8.3 Highest-value technical work first

**Not replacing quotes, but wiring up `market-dumps`.**

172MB Parquet, one shot, whole-market 10-year daily K, directly solves:

- `quant/akshare_client.py` 15-sec timeouts and repeated retries
- Stability of the 17:00 daily run on Factor Plaza
- The "batch daily-K fetch triggers Tencent WAF and IP ban" we hit on 9-11 —— **file downloads don't get banned**

---

## Appendix: Test environment and methodology

- Local Docker (`python:3.11-slim`) + local Python, **no production server IP used**
- MCP testing: no SDK dependency, **raw JSON-RPC** (`initialize` → `tools/list` → `tools/call`),
  which validates the endpoint itself rather than a specific SDK version
- Rate-limit test: staged load (1/4/8 concurrency), stop on first exception
- Cross-validation sources: Tencent `qt.gtimg.cn` + eltdx (TDX protocol)
- Key used was a dev key self-issued by the user on the HiThink official site
