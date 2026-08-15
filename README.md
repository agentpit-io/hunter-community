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
| **② Hunter 平台 key** (`hunt_tools_...` 或 `hunt_data_...`) | 我们签发的，[免费申请](https://hunter.agentpit.io/dev/api-keys) | 解锁左侧**工具/SKILL**(行情/K线/财报/UZI/Kronos)**+** 深度分析的**数据基座**(巨潮/龙虎榜/北向/finance-data 聚合新闻 15+ 路) | 聊天照常，工具会提示"去申请"；深度分析报告 Sentinel 只能靠 akshare 免费源，常态**抓 14 条 / 保留 0 条**，基本是 LLM 空谈 |

**Hunter 平台 key · 一 key 通用**:
一把 `hunt_tools_...` 同时解锁**工具**(sidebar SKILL)+ **数据源**(深度分析
Sentinel 用的 finance-data)· 只需填 `HUNTER_API_KEY` 一处。数据访问走
`hunter.agentpit.io/api/saas/data/*` 网关中转(2026-08-14 起) · 服务端替你完成
finance-data 内部鉴权 · 用户侧只管填一把 key。

为什么工具要我们的 key：这些能力背后是我们持续维护的数据管道和模型服务，
在 Hunter 服务器上执行，不在你的机器上。产物免费给你用，用量按 key 记账。

**开源版不打折**：左侧工具全部照常显示，点下去会告诉你怎么解锁，而不是把功能藏起来。

---

## 跑起来（约 10 分钟，大头是拉镜像）

### 0. 前置

- Docker Desktop（Windows / macOS）或 Docker Engine + Compose v2（Linux）
- 磁盘留 20 GB（opencode 引擎镜像解压后 ~7.5 GB 是大头 · web/api 各 ~1.5 GB · 加 Postgres 数据卷），内存留 4 GB
- 能访问 `ghcr.io`（对话引擎镜像从这里拉，压缩包 ~1.7 GB，国内首拉 10 分钟起步）

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
$u=New-Object Text.UTF8Encoding $false
$c=[IO.File]::ReadAllText("$PWD\.env",$u) -replace '(?m)^JWT_SECRET=.*$',"JWT_SECRET=$s"
[IO.File]::WriteAllText("$PWD\.env",$c,$u)
```

> 上面第 3、4 行必须**显式指定 UTF-8**,不能图省事写成
> `(Get-Content .env -Raw) ... | Set-Content .env`。
> 中文版 Windows 的默认代码页是 GBK,那样读会把 `.env` 里的中文注释拆坏、
> 连带吃掉换行符 —— 实测 25 个变量里有 4 个(`LLM_PROVIDER` /
> `NEXT_PUBLIC_API_URL` / `DATA_SOURCE_PROVIDER` / `FORECAST_PROVIDER`)
> 会被并进上一行注释里,等于被注释掉,而且**看不出任何报错**。

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
# ② 你自己的大模型 · 聊天靠它 · 三项都必填
# 少任何一项 opencode 容器会拒启动(状态 Restarting),日志里会明说少的是哪个 ——
# 这是故意的:留空的话上游会 401,opencode 又会把错误吞成一条空消息,
# 前端只显示"深度思考完成"却什么都没答,谁也看不出是 key 没填。
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-<你的 key>
LLM_DEFAULT_MODEL=gpt-4o-mini

# 用 DeepSeek(deepseek-v4-flash / deepseek-v4-pro)?加一行:
#   LLM_SCHEMA_SANITIZE=1
# 不然对话会 400「Invalid schema ... type: 'null'」。详见 docs/02-providers.md。

# ③ Hunter 平台 key · 一 key 通用(工具 + 数据源全解锁)
# 申请:https://hunter.agentpit.io/dev/api-keys → 免费 · 30 秒
# HUNTER_API_KEY=hunt_tools_xxxxxxxxxxxxxxxx
```

其余保持默认即可。

**深度分析报告的档位**:

| 场景 | 未填 ③ | 填了 ③ |
|---|---|---|
| Sentinel 抓取新闻 | 10-15 条(akshare 免费源 · 容器网络下常挂) | 50-80 条(finance-data 聚合) |
| 保留通过核查条数 | 0-3 条 | 5-15 条 |
| 判官置信度 | 15-30% | 50-80% |
| 决策依据 | LLM 空谈 + 今日涨跌 | 巨潮/龙虎榜/北向/财联社均可引用 |

> 客户端默认走 `hunter.agentpit.io/api/saas/data/*` 网关中转 · 由服务端注入
> 内部 X-Finance-Token 访问 finance-data · 用户侧只需一把 key。
> 详见 [统一 key gateway 方案](https://github.com/hangeaiagent/hunter/blob/main/doc/codex/community/2026-08-14_community-真正统一key-gateway方案.md)。

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

### 5. 解锁工具 key(② `hunt_tools_...`)

对话页左下角点 **「申请 Key · 解锁全部工具」**：

1. 弹窗里点「去申请 Key（免费）」，在 Hunter 平台登录后一键签发
2. 复制那串 `hunt_tools_...`（**只显示一次**）
3. 粘回弹窗点「保存」——立即生效，不用重启

也可以写进 `.env` 的 `HUNTER_API_KEY` 再 `docker compose up -d`，
适合你自己长期跑的实例。

至此左侧工具的小锁消失，「行情速查」「Kronos 走势预测」等能力全部可用。

### 6. 一把 key 覆盖了什么(§5 填完就全开)

§5 那把 `HUNTER_API_KEY` 是**唯一**要填的平台 key。所有需要我们服务端的能力都走
`hunter.agentpit.io/api/saas/*` 网关中转 —— 网关校验你的 key、替你完成上游内部
鉴权、并记一笔用量。上游地址对你完全隐藏,我们换机器你不用改任何配置。

| 能力 | 网关路径 | 上游 | 没填 key 会怎样 |
|---|---|---|---|
| 工具 / SKILL(行情·K线·财报) | `/api/saas/tools/*` | hunter 自有 | 点了提示去申请 |
| 深度分析数据基座(新闻·公告·龙虎榜) | `/api/saas/data/*` | finance-data | 静默降级到 akshare 免费源,报告很空 |
| Kronos 走势预测 | `/api/saas/kronos/*` | kronos.agentpit.io | 401 + 申请引导 |
| 发现页 · Scout 情报 | `/api/saas/truesource/*` | truesource.agentpit.io | 401 + 申请引导 |

不需要 `hunt_data_` / `hunt_kron_` 之类的第二把 key —— **平台从来没签发过这些前缀**,
早期文档里出现过是笔误,照着填只会白费功夫。

**验证生效**:
```bash
# 四个网关探活(都无需 key)
for gw in tools data kronos truesource; do
  docker compose exec api curl -s https://hunter.agentpit.io/api/saas/$gw/_ping; echo
done
# 期望每行都是 {"ok":true,"gateway":"saas-xxx","upstream":"..."}
# (tools 网关没有 _ping,用 /manifest 代替)

# 带自己的 key 拉一条新闻
docker compose exec api python -c "
import os, httpx
r = httpx.get('https://hunter.agentpit.io/api/saas/data/api/v1/news/articles',
              params={'symbol':'601899.SH','hours':24,'threshold':0.0},
              headers={'Authorization': f\"Bearer {os.getenv('HUNTER_API_KEY','')}\"},
              timeout=15)
print(r.status_code, len(r.json().get('items', [])), '条')
"
# 期望:200 20+ 条  ·  401 = §5 的 key 没填/无效
```

**私有部署 finance-data**(自建镜像)· 显式配 `.env`:
```bash
FINANCE_DATA_URL=https://your.finance-data.example.com
FINANCE_DATA_TOKEN=<your shared token>
# 客户端检测到 URL 不包含 /api/saas/data 会切到 X-Finance-Token 直连模式
```

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

**深度分析报告"技术面 AI 暂不可用" / "抓 14 条 · 保留 0 条" / 判官置信度只有 15-30%**
`HUNTER_API_KEY` 空的必然结果 —— 深度分析数据源依赖它(通过 `hunter.agentpit.io/api/saas/data/*`
网关中转 · 见 §6)。检查表:
1. `docker compose exec api env | grep HUNTER_API_KEY` · 空 = 没填
2. `docker compose exec api curl -s https://hunter.agentpit.io/api/saas/data/_ping` · 应返 `{"ok":true,...}`
3. 完整验证请求 · 见 §6 的 curl 脚本
4. 一行修:`.env` 加 `HUNTER_API_KEY=hunt_tools_...` · `docker compose up -d`

**启动报 `JWT_SECRET` 相关的错，直接起不来**
`.env` 里少了 `JWT_SECRET` 这一行。这是故意拦的：api 和 opencode 必须共用同一个值，
少了会变成"服务起得来但对话莫名 401"的哑故障，不如启动时就说清楚。
回到第 2 步跑一遍生成命令即可。

**`docker compose ps` 里 opencode 一直 `Restarting`**
`.env` 里 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_DEFAULT_MODEL` 至少缺一个 ——
`docker compose logs opencode` 会明说缺哪个。同 `JWT_SECRET` 一个道理：留空的话
上游 401,opencode 会把错误吞成一条空消息,前端只显示"深度思考完成"没有内容,
根本看不出是 key 没填,所以启动阶段直接拒掉。补上再 `docker compose up -d`。

**发第一条对话报 `Invalid schema for function ... type: "null"`**
用了 DeepSeek(或其它对 tool schema 严格的 OpenAI 兼容网关)。opencode 打包
MCP 工具时会送 `parameters: null` 的 schema,DeepSeek 直接 400 —— 前端表现是
气泡里只有"深度思考完成"没有正文。修法:`.env` 加 `LLM_SCHEMA_SANITIZE=1`
让请求过一遍 `llm-shim` 自动清洗,再 `docker compose up -d`。
细节和适用列表见 [docs/02-providers.md](./docs/02-providers.md)。

**首次发消息像卡了 30-40 秒**
opencode 冷启动要下 `@ai-sdk/openai-compatible` npm 包 + 初始化 provider,
第一次 chat/session 创建就是这么慢。第二次开始秒回。docker restart 后同样。

**改了 `.env` 没生效**
`docker compose restart` 不会重读 env，用 `docker compose up -d`。
`NEXT_PUBLIC_*` 是构建期烘进前端的，改它要 `docker compose up -d --build`。

**首次 `docker compose up` 十几分钟没输出，是不是卡了**
大概率没卡，是 compose 在非 TTY 下几乎不打印进度。opencode 引擎镜像压缩包
1.7 GB / 解压后 7.5 GB，国内网络下就是这个体感。判断标准：`docker system df`
里 `SIZE` 有没有持续涨，或者单独 `docker pull ghcr.io/agentpit-io/hunter-opencode:latest`
拿实时进度看。

**api build 报 `HASHES FROM THE REQUIREMENTS FILE` / `unknown package`**
`requirements.txt` 里没手写 hash，是 pip 校验 PyPI 元数据 sha256 时下载损坏，
PyPI CDN 偶发抖动就会中一次。修法：
```bash
docker compose build --no-cache api && docker compose up -d
```
重来一遍基本就过了。

**Hunter 平台 key（`hunt_tools_*`）能顺便当大模型 key 用吗**
不能。`saas_gemini` provider 指向的 `oneapi.hermes.agentpit.io` 是 Hunter 内网机器，
公网 DNS 查不到。**两把 key 各管各的**（见开头「先搞清楚两把 key」）：Hunter key
只解锁工具，聊天还是要你自己出一把 OpenAI 兼容 key（DeepSeek 最便宜、OpenRouter
最灵活、也可以对着你自己的网关）。

**想从头再来**
```bash
docker compose down -v      # -v 会删掉数据库，账号和自选都没了
docker compose up -d --build
```

---

## 加你自己的 SKILL

SKILL 是**分析逻辑**——一段讲清"这类问题该怎么分析"的方法论。我们内置了 29 个
(行情速查 · 深度分析 · DCF 估值 · 多空辩论 …),你也可以加自己的。

用的是 **Anthropic Agent Skills 标准格式**,所以**网上下载的 skill 不用改一个字**:

```
user-skills/
  你的skill名/
    SKILL.md
```

```markdown
---
name: 你的skill名
description: 一句话说明什么时候该用它 —— 模型据此判断要不要调用
---

# 正文写方法论
分几步、先看什么后看什么、注意什么。
```

放好后 `docker compose restart api opencode` 即可。**同名时你的覆盖我们的** ——
想改我们某个 SKILL 的措辞,放一个同名目录就行,不用动我们的文件。

想让你的 SKILL 用我们的数据与工具,在 frontmatter 里加一段 `hunter:`
(标准加载器会忽略它,不影响兼容)——详见 `user-skills/README.md`。

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
| 发现页 · Scout 情报 | ✅ 需平台 key | ✅ |
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

### 改了 skills/ 之后必须重启 opencode

opencode **只在启动时扫一次** skill 目录,之后缓存住。改完 `skills/` 或
`user-skills/` 不重启,会出现「侧栏显示 N 个、模型手上是 M 个」的不一致 ——
而且不报错,只表现为模型答非所问或调用已删除的能力。

```bash
docker compose restart opencode          # 约 50 秒
python scripts/check_skill_sync.py       # 比对磁盘 ↔ opencode,不一致会列出差异
python scripts/check_skill_sync.py --fix # 不一致就自动重启并复查
```

> 实测过:`POST /instance/dispose` 无效,文件挂载可见也无效 —— 只能重启。
