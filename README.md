# Hunter Community Edition

> 你的私人金融 AI 团队 · 开源自部署 · 跟着下面走一遍就能用
>
> Your private financial AI team · self-hosted · follow the steps below and it runs.

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](./LICENSE)
[![CI](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml/badge.svg)](https://github.com/agentpit-io/hunter-community/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/ghcr.io-agentpit--io-blue)](https://github.com/agentpit-io/hunter-community/pkgs/container/hunter-community-api)

Live preview: **[https://hunter-community.agentpit.io](https://hunter-community.agentpit.io)**
（演示站开了多用户，首次访问会引导你注册；自己 clone 下来跑是不需要账号的）

---

## 先搞清楚两把 key

这是使用本项目**唯一需要理解的概念**，其余都是照抄命令。

| | 谁的 key | 干什么 | 不填会怎样 |
|---|---|---|---|
| **① 大模型 key** | 你自己的（OpenAI / OpenRouter / 任意 OpenAI 兼容网关） | 驱动对话本身 | 聊天不可用 |
| **② Hunter 平台 key** | 我们签发的，[免费申请](https://hunter.agentpit.io/dev/api-keys) | 解锁左侧的**工具与 SKILL**：行情速查 · K 线 · 财报 · 关键新闻 · UZI 深度分析 · Kronos 走势预测 | 聊天照常，但点这些能力会提示你去申请 |

为什么工具要我们的 key：这些能力背后是我们持续维护的数据管道和模型服务，
在 Hunter 服务器上执行，不在你的机器上。产物免费给你用，用量按 key 记账。

**开源版不打折**：左侧工具全部照常显示，点下去会告诉你怎么解锁，而不是把功能藏起来。

---

## 跑起来（约 10 分钟，大头是拉镜像）

### 0. 前置

- Docker Desktop（Windows / macOS）或 Docker Engine + Compose v2（Linux）
- 磁盘留 5 GB，内存留 4 GB
- 能访问 `ghcr.io`（对话引擎镜像从这里拉）

### 1. 拉代码

```bash
git clone https://github.com/agentpit-io/hunter-community
cd hunter-community
cp .env.example .env
```

### 2. 改 `.env` 三处

先生成签名密钥（**直接复制整行执行**，会自动写进 `.env`）：

```powershell
# Windows PowerShell
$b=New-Object byte[] 48;[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
$s=([Convert]::ToBase64String($b) -replace '[=/+]','').Substring(0,60)
(Get-Content .env -Raw) -replace '(?m)^JWT_SECRET=.*$',"JWT_SECRET=$s" | Set-Content .env -NoNewline
```

```bash
# Linux / macOS
sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -base64 48 | tr -d '=/+' | head -c 60)|" .env && rm .env.bak
```

<details>
<summary>这是什么？为什么不能用默认值？</summary>

它是签名密钥：登录凭证（JWT）用它签名，服务端再用它验签。本机自用时被人伪造凭证的
风险很低，**真正的理由是它还兼了第二份工** —— 你的 Hunter 平台 key 和第三方 MCP
凭证是用它派生出的密钥加密存在数据库里的（`apps/api/app/utils/crypto.py`）。
以后再改它，那些已存的东西就解不开了，得重填一遍。所以现在设一次最省事。

</details>

然后编辑 `.env` 填大模型：

```bash
# ② 你自己的大模型 · 聊天靠它
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-<你的 key>
LLM_DEFAULT_MODEL=gpt-4o-mini

# ③ Hunter 平台 key · 解锁工具与 SKILL（可以先留空，之后在网页里填）
# HUNTER_API_KEY=hunt_tools_xxxxxxxx
```

其余保持默认即可。

### 3. 启动

```bash
docker compose up -d --build
```

首次会构建 api / web 镜像并拉取对话引擎，10 分钟左右属正常。看状态：

```bash
docker compose ps        # 六个服务都应是 running / healthy
docker compose logs -f api
```

### 4. 打开浏览器

<http://localhost:3100>

**不需要注册，也不需要登录** —— 跑在你自己机器上，直接就是对话页。
先发一句"你好"验证大模型通不通。

> 想把这台实例开放给别人访问？在 `.env` 里设 `HUNTER_SINGLE_USER=0` 再
> `docker compose up -d`，登录/注册就回来了（第一个注册的账号自动是管理员）。
> **开着单用户模式暴露到公网 = 谁都能拿到管理员权限**，别这么干。

### 5. 解锁工具

对话页左下角点 **「申请 Key · 解锁全部工具」**：

1. 弹窗里点「去申请 Key（免费）」，在 Hunter 平台登录后一键签发
2. 复制那串 `hunt_tools_...`（**只显示一次**）
3. 粘回弹窗点「保存」——立即生效，不用重启

也可以写进 `.env` 的 `HUNTER_API_KEY` 再 `docker compose up -d`，
适合你自己长期跑的实例。

至此左侧工具的小锁消失，「行情速查」「Kronos 走势预测」等能力全部可用。

### 默认端口（在 `.env` 里改）

| 服务 | 端口 |
|---|---|
| Web | 3100 |
| API | 8100 |
| Postgres | 5442 |
| Redis | 6479 |

---

## 常见问题

**要不要注册账号**
不要。默认单用户模式，打开就能用。只有当你把 `HUNTER_SINGLE_USER` 设成 `0`
（准备给别人访问时）才会出现登录和注册。

**打不开 3100 / 页面一直转**
`docker compose ps` 看 web 是不是 healthy；不是就 `docker compose logs web`。
端口被占了就改 `.env` 的 `WEB_HOST_PORT` 再 `docker compose up -d`。

**聊天报错说连不上模型**
`LLM_BASE_URL` 要带 `/v1`，`LLM_API_KEY` 要是能用的。
换了这两个之后必须 `docker compose up -d`（重建容器才会读新 env）。

**点左侧工具弹"需要 key"**
这就是设计如此，见上面「两把 key」。申请是免费的。

**问股价时回答"尚未配置 Hunter key"**
正常，见上面「两把 key」。想先不申请 key 试试基础行情，
在 `.env` 里设 `DATA_SOURCE_PROVIDER=akshare` 再 `docker compose up -d`。

**填了 key 还是提示未解锁**
弹窗会告诉你具体原因：key 被吊销了、或连不上 `hunter.agentpit.io`。
后者多半是本机代理／防火墙，`docker compose exec api curl -I https://hunter.agentpit.io` 验一下。

**启动报 `JWT_SECRET` 相关的错，直接起不来**
`.env` 里少了 `JWT_SECRET` 这一行。这是故意拦的：api 和 opencode 必须共用同一个值，
少了会变成"服务起得来但对话莫名 401"的哑故障，不如启动时就说清楚。
回到第 2 步跑一遍生成命令即可。

**改了 `.env` 没生效**
`docker compose restart` 不会重读 env，用 `docker compose up -d`。
`NEXT_PUBLIC_*` 是构建期烘进前端的，改它要 `docker compose up -d --build`。

**想从头再来**
```bash
docker compose down -v      # -v 会删掉数据库，账号和自选都没了
docker compose up -d --build
```

---

## Provider 矩阵

数据源默认走 Hunter 网关（`hunter`）。没配 key 时它会明确回一句"去申请 key"，
而不是假装数据源出问题——这是有意的。

想完全不用 key 也能取 A 股行情，把 `DATA_SOURCE_PROVIDER` 设成 `akshare`
（美股港股用 `yfinance`）。两者都免费，但覆盖面和数据质量不如平台管道，
而且 akshare 在容器里经常连不上境内数据源，失败率不低。

| 层 | 环境变量 | 可选值 | 默认 |
|---|---|---|---|
| 数据源 | `DATA_SOURCE_PROVIDER` | `hunter` · `akshare` · `yfinance` · `saas` | 留空 = `hunter` |
| 大模型 | `LLM_PROVIDER` | `openai_compat` · `anthropic` · `saas_gemini` | `openai_compat` |
| 预测 | `FORECAST_PROVIDER` | `noop` · `kronos_local` · `kronos_saas` | `noop` |

细节与返回结构见 [docs/02-providers.md](./docs/02-providers.md)。

---

## Community vs Cloud

| 能力 | Community（自部署） | Cloud（[hunter.agentpit.io](https://hunter.agentpit.io)） |
|---|---|---|
| 对话（自带大模型 key） | ✅ | ✅ |
| 左侧工具与 SKILL | ✅ 需平台 key（免费申请） | ✅ |
| UZI 深度分析 | ✅ 需平台 key | ✅ |
| Kronos 走势预测 | ✅ 需平台 key（或自建 GPU） | ✅ |
| 自选 · 持仓 · 信号 | ✅ | ✅ |
| 接入你自己的 MCP 数据源 | ✅ 不需要平台 key | ✅ |
| 微信推送 | ❌ | ✅ |
| 飞书 | ❌ | ✅ |
| 多租户计费 | ❌ | ✅ |

---

## Roadmap

- [x] **P1** · Monorepo skeleton + docker-compose + fin-r1 deploy
- [x] **P2** · SaaS strip (WeChat / Lark / booth / SSO removed)
- [x] **P3** · Local email + password auth
- [x] **P4** · Pluggable provider layer (data · LLM · forecast) + per-user settings
- [x] **P5** · 平台 key 门控 · 工具与 SKILL 免费解锁
- [ ] **P6** · Push channel refactor to SMTP / Slack
- [ ] **v1.0** · SKILL catalog UI

---

## 架构

```
             ┌─────────────────────┐
浏览器    →   │  web (Next.js 15)   │ :3100
             └────────┬────────────┘
                      │ /api/*  （web 自带 BFF 转发，无需反向代理）
             ┌────────▼────────────┐        ┌──────────────────┐
             │  api (FastAPI)      │        │ opencode 引擎     │
             │  · auth (JWT)       │◄───────┤ MCP tools 回调    │
             │  · providers layer  │───► hunter 网关 / akshare / yfinance
             │  · agents           │───► 你的 OpenAI 兼容网关
             └─┬─────────────┬─────┘        └────────┬─────────┘
                                                     │ tool schema 清洗
                                          ┌──────────▼─────────┐
                                          │ llm-shim  :3999    │──► 你的网关
                                          └────────────────────┘
        Postgres           Redis
         :5442             :6479

     工具与 SKILL ──► https://hunter.agentpit.io/api/saas/tools/*
                      （凭平台 key 放行并计量）
```

---

## Contributing

Read [CONTRIBUTING.md](./CONTRIBUTING.md). Pull requests welcome, but the
codebase is fresh out of an ongoing extraction from a private repo —
expect churn until v1.0.

## Security

Vulnerabilities: `security@agentpit.io`. See [SECURITY.md](./SECURITY.md).

## License

Apache 2.0 · see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

**Hunter · AgentPit · 猎鹿人** are trademarks of the AgentPit team.
Forks must rename.
