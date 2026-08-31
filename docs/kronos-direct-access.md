# Kronos 走势预测 · 直连接入

**大多数私有部署不需要看这篇。** 默认路径(通过 `hunt_tools_` 一把 key 走 `hunter.agentpit.io/api/saas/kronos` 网关)已经内置在 hunter-community 里 · 配了 `HUNTER_API_KEY` 就自动通。

**这篇专门讲的是"直连"用法** —— 绕开 hunter 网关 · 直接从你的私有部署打 `https://kronos.agentpit.io` · 用一把专属的 `oapk_` 类型 hunterkey。适合的场景:

- 你只想用 Kronos 一样能力 · 不想为了它去申请 hunter 平台账号
- 你想把 Kronos 集成到 hunter-community 以外的自研系统(Python/Node/curl 都行)
- 你要极低延迟 · 少一跳网关

## 申请 KRONOS 类型 hunterkey

1. 打开 [https://hunter.agentpit.io/dev/api-keys](https://hunter.agentpit.io/dev/api-keys)
2. 登录后点「申请 API Key」· **API 类型选 KRONOS**
3. 简要写一下用途 · 提交
4. 审批通过后拿到形如 `oapk_XXXXXXXXXXXX...` 的 key(**只显示一次 · 立即复制存好**)

审批时间通常在工作日内几小时。

## 配置 hunter-community 走直连

修改 `.env`:

```env
# 默认(推荐)· 通过 hunter 网关 · 一把 HUNTER_API_KEY 通吃
# FORECAST_PROVIDER=kronos_saas
# HUNTER_API_KEY=hunt_tools_xxx

# 直连(本篇讲的)· 绕开 hunter 网关 · 用专属 KRONOS key
FORECAST_PROVIDER=kronos_local
KRONOS_LOCAL_URL=https://kronos.agentpit.io
KRONOS_LOCAL_KEY=oapk_xxx        # 你申请到的 KRONOS 类型 hunterkey
```

> **注意**:目前 `kronos_local` provider 的 API key 传递需要 v0.2+ 版本 · 如果你在用更早版本 · 见文末「手动集成」段落。

重启 hunter-community(`docker compose restart api`)后 · 走势预测 SKILL 会直接打 `https://kronos.agentpit.io` · 不经过 hunter 网关。

## 手动集成(不用 hunter-community · 自己写代码)

`https://kronos.agentpit.io` 就是一个标准 HTTPS REST 服务 · 只有两个端点:

```bash
# 健康检查
curl -H "Authorization: Bearer oapk_xxx" https://kronos.agentpit.io/health
# → {"status":"ok","model":"Kronos-base"}

# 走势预测(POST · A 股/美股/港股皆可)
curl -X POST https://kronos.agentpit.io/predict \
  -H "Authorization: Bearer oapk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"600519","pred_len":10}'
```

Python 示例:

```python
import requests

KRONOS_URL = "https://kronos.agentpit.io"
KRONOS_KEY = "oapk_xxx"

resp = requests.post(
    f"{KRONOS_URL}/predict",
    headers={"Authorization": f"Bearer {KRONOS_KEY}"},
    json={"symbol": "AAPL", "pred_len": 10},
    timeout=180,   # GPU 推理 30-70s · 别设太紧
)
data = resp.json()
# data.predictions = [{date, open, high, low, close, volume}, ...]
```

## 常见错误

| HTTP | 含义 | 排查 |
|---|---|---|
| 401 | 无 key / key 无效 / key 已撤销 | 检查 `Authorization: Bearer` 是否带对 · key 是否复制完整(oapk_ 后 32 位) |
| 403 | key 是有效的 · 但 apiType 不是 KRONOS | 你可能申请的是 KPRED/FIN_R1 等其他类型 · 需要重新申请 KRONOS 类型 |
| 404 | 股票代码找不到 | 用 A 股原始代码(如 `600519` 或 `600519.SH`)· 美股用 ticker(`AAPL`)|
| 429 | 触发速率限制 | 每 IP 每分钟对无效 key 有硬顶(防洪水)· valid key 走缓存不受此限 |
| 502/504 | 上游 Kronos backend 异常 | 通常是 GPU 服务重启 · 稍后重试;若持续 5 分钟以上 · 到 GitHub 提 issue |

## 撤销 / 换 key

在 `https://hunter.agentpit.io/dev/api-keys` 页面找到对应 key · 点「撤销」。撤销后:
- 服务端有 Redis 缓存(**最多 5 分钟**)后彻底失效
- 你的私有部署会开始拿 401 · 需要申请新 key 替换

## 计费

Kronos 直连当前免费 · 每把 key 有 quota 上限(默认 1000 次)· 打完需要重新申请。未来可能引入付费档 · 会提前公告。

## 相关文档

- 平台管道模式(默认 · 推荐 99% 场景):`docs/01-getting-started.md`
- Provider 选型:`docs/02-providers.md`
- API Key 类型对比:`hunt_tools_` vs `oapk_` 见 [agentpit 开发文档](https://develop.agentpit.io/dashboard/open-api)
