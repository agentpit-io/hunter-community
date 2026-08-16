# env-samples · 各家 LLM provider 的 .env 模板

> 用法:选一份对应 provider 的 `.env.<provider>.example` · 复制到 `$HUNTER_COMMUNITY/.env`(或 merge 需要的 LLM_* 行到你现有 .env)· 填 API_KEY · `docker compose up -d`(不是 restart · 要 recreate 读新 env)· 然后跑 `scripts/run-golden-cases.py`。
>
> 已实测(2026-08-15): **DeepSeek v4 pro** ✅ P0 推荐(见 `../results/2026-08-15_deepseek_v4pro_对比分析.md`)
> 已实测(2026-08-16): **AIHubMix 网关**(gpt-4o / claude-sonnet-4-6 / gemini-2.5-pro / qwen3-max)✅ 一 key 打通四家
> 待测(直连): doubao-pro-32k

---

## 切模型步骤(3 步)

```bash
cd $HUNTER_COMMUNITY

# 1. 备份现有 .env
cp .env .env.bak.$(date +%Y%m%d-%H%M)

# 2. 挑一份样本 · 拷进 .env(只覆盖 LLM_* 段 · 保留 HUNTER_/OPENCODE_/DB_/JWT 等)
#    最省事:vim .env 手改 LLM_PROVIDER / LLM_BASE_URL / LLM_DEFAULT_MODEL / LLM_API_KEY 四行
#    或 sed 批量替换

# 3. 重建 opencode + llm-shim 让新 env 生效(注意:必须 up · 不能 restart)
docker compose up -d --force-recreate opencode llm-shim
sleep 15
docker compose logs opencode --tail 10 | grep gen-config    # 应看到 provider hunter-llm → 新上游
```

**验证 provider 切换成功**:
```bash
curl -sS -u "opencode:$(grep OPENCODE_PASS .env | cut -d= -f2)" http://127.0.0.1:3921/config/providers | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d['providers']:
    print(p['id'], list(p['models'].keys()))
"
# 应看到 hunter-llm 列出的 model 是你新配的
```

**跑评测**:
```bash
python3 docs/model-testing/scripts/run-golden-cases.py \
  --provider hunter-llm --model <你新配的 model 名> --label <provider-tag>
# 输出 results/YYYY-MM-DD_HHMM_<label>_v3_full.json + _v3_summary.csv
```

---

## 4 家 provider · 关键差异速查

| provider | BASE_URL | MODEL 值 | SCHEMA_SANITIZE | 已知踩坑 |
|---|---|---|---|---|
| **AIHubMix 网关**(推荐 · 一 key 通 30+ 家) | `https://aihubmix.com/v1` | 见 `.env.aihubmix.example` 速查 | `1`(兼顾 DeepSeek/GLM) | 按上游实际模型计费 · +10-30% 通道费 · Gemini 有 reasoning_tokens |
| **DeepSeek**(直连基线 · P0 推荐) | `https://api.deepseek.com/v1` | `deepseek-v4-pro` | **必开 =1** | reasoning tokens 会吃 max_tokens · shim SSL 尾部偶发不阻塞 |
| **通义 qwen** | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` / `qwen3-235b-a22b-instruct-2507` | `auto` | compatible-mode 后缀不能漏 · 官方 mode 与 OpenAI 差异小 |
| **豆包(火山引擎)** | `https://ark.cn-beijing.volces.com/api/v3` | endpoint_id(如 `ep-xxxx-xxxx`)· **不是 doubao-pro-32k** | `auto` | 用 endpoint_id 而非模型名 · 配置最绕 · 走 volcengine 创建 endpoint 才能拿 id |
| **Claude**(海外首选) | `https://api.anthropic.com` | `claude-sonnet-4-6` | `0`(不需要) | 需 `LLM_PROVIDER=anthropic` · schema 与 OpenAI 不同 · 但 tool 稳定性最好 |
| **GPT** | `https://api.openai.com/v1` | `gpt-4o` / `gpt-4o-mini` | `0` | 便宜 · CN 直连有限流 · 走 OneAPI 中转更稳 |
| **OneAPI 路由** | `https://<oneapi-host>/v1` | 视上游(如 `gemini-2.5-pro` 走 OneAPI 的 Gemini 通道) | `auto`(Gemini 通道必开) | 视上游 model 特性 · Gemini 通道必开 SCHEMA_SANITIZE |

---

## 文件清单

| 文件 | provider | 状态 |
|---|---|---|
| [`.env.aihubmix.example`](./.env.aihubmix.example) | AIHubMix 网关(GPT/Claude/Gemini/Qwen/DeepSeek/Grok/GLM) | ✅ 已 smoke test 4 家 · 推荐 |
| [`.env.deepseek.example`](./.env.deepseek.example) | DeepSeek v4 pro(直连) | ✅ 已实测 · P0 直连基线 |
| [`.env.qwen.example`](./.env.qwen.example) | 通义 qwen-max(直连 dashscope) | ⏳ 待评测(可先走 aihubmix) |
| [`.env.doubao.example`](./.env.doubao.example) | 豆包(火山引擎直连) | ⏳ 待评测 |
| [`.env.claude.example`](./.env.claude.example) | Claude Sonnet 4.6(直连 anthropic) | ⏳ 待评测(可先走 aihubmix) |
| [`.env.openai.example`](./.env.openai.example) | GPT-4o(直连 openai) | ⏳ 待评测(可先走 aihubmix) |
