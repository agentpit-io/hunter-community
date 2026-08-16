# hunter-community · 大模型适配矩阵

> **最后更新**: 2026-08-16
> **测试方法**: [`README.md`](./README.md) § 3-4(4 维度 + 7 golden case)
> **实测数据**: [`results/`](./results/) 各 provider 的 `*_v3_full.json` + `*_v3_summary.csv`
> **env 模板**: [`env-samples/`](./env-samples/)
>
> **推荐用法**(给自部署用户):按你的**语言/预算/合规**需求选一家 · 复制对应 `.env.<provider>.example` 到 `hunter-community/.env` · `docker compose up -d --force-recreate opencode llm-shim`。

---

## 主矩阵

| 模型 | 接入方式 | tool_call hit | 稳定性 | 单次延迟 · 平均 | 推荐等级 | 已知踩坑 |
|---|---|---|---|---|---|---|
| **Claude Sonnet 5** | aihubmix 网关 | **7/7**(满分) | ✅ 无 timeout · 无 think 泄漏 · B2 编排 4 tool | 25.7s | ⭐⭐⭐⭐⭐ **海外首推** | 走 aihubmix Alpine 容器 TLS 指纹拦截 · 需 host proxy |
| **Qwen 3.8 Max** | aihubmix 网关 | **7/7**(满分) | ✅ 无 timeout · 无 think 泄漏 | 48.1s | ⭐⭐⭐⭐⭐ **国内首推** | 深度分析 100-120s 较慢 |
| **DeepSeek v4 pro** | 直连 `api.deepseek.com` | **6/7**(A2 过度调用) | ✅ 无 think 泄漏 · B2 70s | 30s | ⭐⭐⭐⭐⭐ **P0 直连默认** | 必开 `LLM_SCHEMA_SANITIZE=1` |
| **Gemini 3.5 Flash** | aihubmix 网关 | 6/7(C1 边界超时) | ⚠ C1 timeout · 无 think 泄漏 | 62.3s | ⭐⭐⭐⭐ 便宜快 · 边界需修 | C1 过度调用 tool 触发 240s timeout |
| **Doubao Seed 2.1 Pro** | aihubmix 网关(推荐直连火山) | 6/7(C1 边界超时) | ⚠ 深度分析慢 3-5× | 103.9s | ⭐⭐⭐ 火山生态 · 建议直连 | 走 aihubmix 网关慢 · endpoint_id 直连快 |
| **GPT-5.6 sol** | aihubmix 网关 | 5/7(A3+B1 错选 tool)| ✅ 最快 127s / 7 case | 18.1s | ⭐⭐⭐ 快 · tool 命中偏差 | 对 `hunter_user_list_my_sources` 过度偏好 · 需修 tool 描述 |
| **MiniMax M3** | aihubmix 网关 | 6/7(C1 边界超时) | ❌ **7/7 全 `<think>` 泄漏** | 60.9s | ⭐⭐ **不推荐**(除非能剥 think tag) | assistant.content 里包含原始 `<think>...</think>` |

**基线对比**: DeepSeek v4 pro 直连 6/7 hit(见 [`2026-08-15_deepseek_v4pro_对比分析.md`](./results/2026-08-15_deepseek_v4pro_对比分析.md))。
**aihubmix 6 家详细对比**: [`2026-08-16_aihubmix_六家对比.md`](./results/2026-08-16_aihubmix_六家对比.md)

---

## 详解:DeepSeek v4 pro(P0 默认)

**基线数据**(2026-08-15 · 7 case · runner v3 从 `GET /session/{sid}/message` 抓完整 tool 序列 + `state.time.start/end` 计时):

| Case | prompt | tool_call(v3 抓) | 耗时 | tokens(i/o/r) | hit | 内容质量 |
|---|---|---|---|---|---|---|
| A1 | 601899.SH 最新股价 | `watchlist_stock_quickview` | **17.8s** | 443/167/53 | ✅ | 完整卡片 |
| A2 | 600519 30 天 K 线 | `uzi_stock_deep_analysis`(过度调用) | **240s TIMEOUT** | 96/51/134 | ❌ | ⚠️ DeepSeek 应调 kline · 却走了重量级深度分析 tool · 内部长任务未完成 |
| A3 | 601899 加自选 | `watchlist_watchlist_add` | 37.9s | 320/71/56 | ✅ | 精准 · "已在自选" |
| A4 | 601899 24h 新闻 | `watchlist_stock_news` | **18.7s** | 503/333/28 | ✅ | markdown 表格 · 带来源 |
| B1 | 预测茅台走势 | `watchlist_stock_quickview`(只调 quickview) | 20.5s | 446/280/275 | ❌ | ⚠️ 未触发 `hunter_cap_kpred` MCP tool · 只用 quickview 数据自己分析 |
| B2 | 分析茅台走势(明说深度) | `uzi_stock_deep_analysis` | **69.9s**(v2 是 278s · 4× 加速) | 867/517/218 | ✅ | 深度报告 · 完整段落 |
| C1 | "分析一下这只股票"(边界)| **无 tool_call** | **4.9s** | 88/27/57 | ✅ | 完美 · 短提示 · 不瞎调 |

**关键改进对比 v2 → v3**:
- runner 单次 case 平均 **80s → 30s**(60% 加速)
- 深度分析 B2 从 **278s → 70s**(4× 加速)· 因 shim `Connection: close` + 尾部 EOF 静默 · 避免了 SSL 断连的重试等待
- C1 边界 case 从 timeout → 4.9s 完美通过

**推荐场景**:
- ✅ 自部署用户默认(便宜 + 中文 + tool 稳)
- ✅ 深度分析类长任务(reasoning 支持好)
- ✅ 新闻聚合 + 表格生成
- ⚠️ 极端复杂 5-6 轮 tool 循环 · 建议监控 reasoning tokens

**接入 3 步**:
```bash
# 1. 拿 key
open https://platform.deepseek.com/api_keys

# 2. 改 .env 关键 4 行(见 env-samples/.env.deepseek.example)
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_DEFAULT_MODEL=deepseek-v4-pro
LLM_API_KEY=sk-xxxx
LLM_SCHEMA_SANITIZE=1

# 3. 重建 opencode + shim
cd $HUNTER_COMMUNITY
docker compose up -d --force-recreate opencode llm-shim
```

---

## 详解:其他 provider(待评测)

按优先级路线图:

### P0 · 通义 qwen-max
用户群:阿里生态 / 内网合规 / 中文重
预估 tool_call OK · 但 pricing 是 DeepSeek 的 3-5 倍
**等你提供 dashscope key 我 30 分钟出评测**

### P1 · 豆包 pro-32k(火山引擎)
用户群:头条系
配置最绕(endpoint_id 而非 model 名)
**等火山引擎 API Key 我 45 分钟出评测(含 endpoint 创建 10 分钟)**

### P2 · Claude Sonnet 4.6
用户群:海外 · 需最好 tool 稳定性
`LLM_PROVIDER=anthropic` 走 anthropic 原生
**预计需要 hunter-guard / hunter-mcp-context plugin 适配 anthropic tool schema · 未验证**

### P2 · GPT-4o
用户群:海外 · 官方 OpenAI 基线
tool 稳定性行业标杆 · 但贵 5-10 倍
CN 直连限流严重 · 建议走 OneAPI 中转

---

## 更新记录

- **2026-08-15**: 首版 · DeepSeek v4 pro 完整评测 · 5 家 env-samples 模板齐备 · runner v3 入库
- **2026-08-16**: aihubmix 网关接入 · 6 家最新旗舰 × 7 case 完整评测(GPT-5.6 sol / Claude Sonnet 5 / Gemini 3.5 Flash / Qwen 3.8 Max / Doubao Seed 2.1 Pro / MiniMax M3)· Claude 和 Qwen 满分 · MiniMax think 泄漏问题记录 · Alpine 容器 TLS 指纹拦截 workaround(host proxy)入库
