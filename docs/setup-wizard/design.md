# hunter-community · 一键部署模板 + 首启向导 · 设计与技术方案

> **本文件是本方案在仓库内的权威版本**（2026-09-18 起以这份为准）。正文为原方案全文；
> 第十节已按 R0 预研的实测结果回填结论，与原方案冲突处**以实测为准**并在该节标注。
> 预研原始数据与实测方法见同目录 [`R0-预研结论.md`](./R0-预研结论.md)。

> 编写：2026-09-17（上海时间）
> 核对基准：hunter-community main `d4eaf97`（含镜像瘦身 v1.0.1、llm-shim 代理修复）；huntercode dev `313457f`；本机与演示站实测
> 关系：**取代** 同目录 `2026-09-16_hunter-community首启向导_开发计划.md`。旧计划写于镜像瘦身之前，假设「多容器共享运行时卷 + 重启 opencode 约 52 秒」，这两点在云平台上不成立，且已有更好的解法（见第三节）
> 相关：`2026-09-16_hunter-community-GitHub优化_执行计划.md` 第 3.4（Zeabur 模板）、3.6（首启向导）

---

## 零、结论

1. **一键部署和首启向导是一件事的两半**：模板负责「把 6 个容器跑起来」，向导负责「跑起来之后不用碰配置文件就能用」。只做模板，用户在云平台上还得去改环境变量、重启；只做向导，本地之外的人依然卡在 `docker compose`。所以合并设计、一起交付。
2. **现在的形态上不了任何云平台**。compose 里有 13 处挂载仓库文件（bind mount）、api 与 opencode 共用 `user-skills/` 目录、数据库迁移只在建卷时执行一次、`JWT_SECRET` 缺了直接拒绝启动。这些在 Zeabur / Railway / Sealos 上都不成立。
3. **技术路线：镜像自包含 + 配置入库 + 向导热生效**。
   - 所有运行时需要的脚本、SKILL、迁移文件打进镜像，不再依赖仓库目录
   - 大模型配置存数据库（加密），`.env` 有值时优先且锁定
   - 向导保存后通过 opencode 自带的 `PATCH /config` 热生效，**不重启容器**（已在 fork 源码中确认该接口会写配置并在响应后销毁实例、下次请求重新加载，待预研实测）
   - 密钥：云平台由模板生成；本地由 api 首次启动生成
4. **平台优先级**：第一批 Zeabur（国内外都可用、模板能力最全）+ Sealos（国内）；第二批 Railway（海外）+ 1Panel 应用商店（国内自有服务器）；第三批 Coolify / Dokploy / 云厂商镜像。
5. **排期 20 个工作日**，2026-09-21 ~ 10-23（避开国庆），分 4 个里程碑，第一天做 3 项预研。

---

## 一、现状核实（不改这些，一键部署无从谈起）

| # | 现状 | 证据 | 对一键部署 / 向导的影响 |
|---|---|---|---|
| 1 | `JWT_SECRET: ${JWT_SECRET:?...}`，不填拒绝启动；`.env.example` 示例值是公开字符串 | `docker-compose.yml`、`.env.example` | 用户必须先改文件；照抄示例等于公开密钥（它还派生加密已存 key 的 AES 密钥，`app/utils/crypto.py`） |
| 2 | 大模型三项只能写 `.env`；opencode 缺配置时入口脚本 `gen-config.py` 退出，llm-shim 缺 `LLM_BASE_URL` 也退出（`shim.py` 第 382 行） | `scripts/opencode/*`、`scripts/llm-shim/shim.py` | 没有「先起来、再配置」的可能；向导无处容身 |
| 3 | 13 处挂载仓库文件：opencode 挂入口脚本、6 个 MCP/插件文件、SKILL 目录；api 挂 `skills/`、`data/`、`user-skills/`、`data-packages/`；llm-shim 整个源码目录；postgres 挂迁移目录；web 挂 `public/` | `docker compose config` 实测 | 云平台只能拉镜像，拿不到仓库文件 |
| 4 | `user-skills/` 被 api（读写）和 opencode（只读）**同时挂载** | 同上 | 云平台上两个服务通常不能共用一个卷 → 用户在界面装的 SKILL，模型看不到 |
| 5 | 数据库迁移靠 postgres 的 `docker-entrypoint-initdb.d`，**只在第一次建卷时执行** | compose | 老用户升级后缺表缺列。**今天本机就踩到**：数据卷建于 8 月，页面 `compliance-status` 接口 500（缺 `compliance_ack_at` 列），手工补跑全部迁移后恢复（实际是 22 个文件，见 3.4 的 M1 实测更正）|
| 6 | api / web 镜像已由 `docker-publish.yml` 推 GHCR，但只有 amd64；compose 仍用本地 `build:` | `.github/workflows/docker-publish.yml` | 云平台要用预构建镜像；arm64 机器（Apple Silicon、部分国产云）需多架构 |
| 7 | web 镜像构建时 `NEXT_PUBLIC_API_URL` 为空 → 前端走同源 `/api/*`，由 web 自带转发到 api | `apps/web/app/api/[...path]/route.ts`、publish 工作流未传构建参数 | **有利**：web 是唯一对外入口，云平台只需给 web 绑域名 |
| 8 | 单用户模式默认开（`HUNTER_SINGLE_USER=1`）：任何人访问即得管理员身份 | `routers/auth.py` | 云平台实例一创建就在公网上，**谁先打开谁就是管理员** → 必须有「初始化口令」 |
| 9 | opencode 支持 `PATCH /config`：写实例目录 `config.json`，并在响应后标记实例销毁，下次请求重建实例、重读配置 | huntercode `server/routes/instance/httpapi/handlers/config.ts`、`lifecycle.ts`、`config/config.ts` 第 624 行 | **向导可以热生效**，不必重启容器（旧计划的最大风险点） |
| 10 | 镜像瘦身后体积：opencode 618 MB、api 909 MB、web 1.66 GB；本机空闲内存占用合计约 1.07 GB（opencode 650 MB、api 196 MB、web 173 MB、postgres 24 MB、shim 21 MB、redis 11 MB） | 本机 `docker stats` 实测 | 云平台最低 2 GB 内存可跑，推荐 4 GB（深度分析、全市场扫描时峰值未测，预研补测） |
| 11 | llm-shim 已支持透传宿主机代理（`2ca1e6b`） | compose | 云平台上不需要代理，变量留空即可 |

---

## 二、目标与用户旅程

### 2.1 目标

| 指标 | 现在 | 目标 |
|---|---|---|
| 从零到第一次对话需要编辑的文件 | `.env` 至少 4 处 | **0** |
| 云平台部署 | 不支持 | Zeabur / Sealos / Railway 点按钮部署；1Panel 应用商店一键安装 |
| 本地首次启动 | `git clone` → 改 `.env` → `up` | `git clone` → `docker compose up -d` → 浏览器完成向导 |
| 配置错误暴露时机 | 发第一条消息后，表现为空回复 | 向导第 3 步当场检测、分类报错，测不通不让保存 |
| 升级后数据库 | 缺表缺列需手工补 | api 启动自动迁移 |

### 2.2 非目标

- 不做多租户计费、团队协作（Cloud 版能力）
- 不做模型切换以外的高级配置 UI（端口、代理、预算、子任务模型仍走环境变量）
- 不加遥测回传
- 不在本期做云厂商市场镜像（腾讯云轻量、阿里云计算巢），放第三批

### 2.3 三条入口，汇合到同一个向导

```
 本地 docker compose          云平台一键部署(Zeabur/Sealos/Railway)     面板应用商店(1Panel/宝塔)
 git clone && compose up      点 README 按钮 → 平台生成密钥与口令         应用商店搜索 → 表单填端口/口令
          │                              │                                        │
          └───────────────┬──────────────┴────────────────────────────────────────┘
                          ▼
              浏览器打开首页(web 唯一入口)
                          │  api 报告「大模型未配置」
                          ▼
     ┌─ 第 0 步 · 验证初始化口令 ─(本机访问自动跳过;公网访问必须)
     ├─ 第 1 步 · 环境自检 ─ 6 个服务 / 数据库迁移版本 / 密钥来源 / 公网暴露提醒
     ├─ 第 2 步 · 选大模型 ─ 预设卡片(实测命中率与耗时) + 自定义
     ├─ 第 3 步 · 填 key 当场测 ─ 连通 → 对话 → 工具调用,分类报错,通过才可保存
     ├─ 第 4 步 · 数据供给三选一 ─ 免费源 / 平台 key(复用现有校验) / 自接 MCP
     └─ 第 5 步 · 完成 ─ 热生效(无重启) → 示例问题 → 进入对话
```

---

## 三、总体技术方案

### 3.1 镜像自包含（去掉对仓库文件的依赖）

| 服务 | 现在 | 改为 | 说明 |
|---|---|---|---|
| opencode | 基础镜像 `hunter-opencode:1.18.12-slim.1` + 13 处挂载中的 10 处 | 新增 **`hunter-community-opencode`** 镜像：`FROM ghcr.io/agentpit-io/hunter-opencode:<版本>`，`COPY scripts/opencode → /opt/hunter-boot`、`scripts/opencode-mcp → /opt/hunter-mcp` 与覆盖文件、`skills/ → /opt/opencode-workspace/.opencode/skills` | 放在 hunter-community 仓库构建（这些文件的源头在这里）；只加几十 KB，拉取几乎无感 |
| llm-shim | `python:3.12-alpine` + 挂源码目录 | 新增 **`hunter-community-llm-shim`** 镜像：同基础镜像 `COPY scripts/llm-shim /app` | 约 50 MB |
| api | 挂 `skills/`、`data/`、`db/migrations` | api 镜像构建上下文改为仓库根（Dockerfile 仍在 `apps/api/`），`COPY skills data db/migrations`；`user-skills` 与 `data-packages` 改为 api 自己的卷 | 构建上下文变大，用 `.dockerignore` 控制 |
| web | 挂 `apps/web/public` | 取消挂载（镜像里本来就有 `public/`） | 该挂载只为本地改静态文件免重建，移到开发覆盖文件 |
| postgres | 挂迁移目录作 initdb | 取消；改由 api 启动时迁移（3.4） | — |

**本地开发体验不降级**：现在的挂载全部移到 `docker-compose.dev.yml`，开发者用 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`；默认的 `docker-compose.yml` 只用预构建镜像，`build:` 保留但默认走 `image:`（`pull_policy: missing`）。

**镜像发布**：`docker-publish.yml` 矩阵从 api/web 扩到 api/web/opencode/llm-shim，平台 `linux/amd64,linux/arm64`，标签与 Release 版本一致（`v1.1.0` → `1.1.0`）。compose 与各平台模板**锁具体版本**。

### 3.2 配置分层与密钥

**优先级（每一项独立判断）**：环境变量非空 → 数据库（向导写入）→ 默认值。环境变量有值的项在向导里显示「来自部署配置，已锁定」，不可编辑——这与现有 `hunter_key.env_locked()` 的语义一致，老用户零影响。

> 注意 compose 的 `${X:-}` 会把「未设置」变成「空字符串」注入容器，判断一律用「非空」。

**密钥（`JWT_SECRET`、`HUNTER_INTERNAL_KEY`）怎么来**：

| 部署方式 | 来源 | 为什么 |
|---|---|---|
| 云平台模板 | 模板生成随机值注入各服务环境变量（Zeabur `${PASSWORD}` 类变量、Railway `secret()`、Sealos `random()`、1Panel 表单 `random`） | 云平台服务之间不共享磁盘，只能靠环境变量同源 |
| 本地 compose | 用户不填时，api 启动脚本生成并写入 **`hunter_secrets` 卷**的 `secrets.env`；opencode 以只读方式挂同一卷读取 | 本地 compose 可以共享卷；这是唯一保留的共享卷，只存两行密钥 |
| 任何方式下用户显式填写 | 环境变量优先 | — |

- compose 中 `JWT_SECRET` 从 `:?` 改为 `:-`
- **弱密钥检测**：等于 `.env.example` 示例值或长度 < 32 → api 启动日志 ERROR + 向导第 1 步黄色警告；**不自动轮换**（轮换会让已加密的 key 全部解不开、登录全部失效）
- **数据库加密依赖 `JWT_SECRET`**：文档与向导都写明「更换或丢失密钥 = 需要重新填写 key」；解密失败返回「密钥已变化，请重新填写」，不报 500

### 3.3 大模型配置：入库 + 热生效

**存储**：沿用 `hunter_config` 表（现存平台 key 的地方），新增键 `llm.base_url`、`llm.api_key`（`crypto.encrypt` 加密）、`llm.model`、`llm.sanitize`、`llm.tested_at`、`setup.completed_at`。

**读取统一入口**：新增 `apps/api/app/services/runtime_config.py`：

```python
def llm() -> LLMConfig:            # 环境变量非空优先,否则读库(30 秒缓存),都无返回 configured=False
def source(key) -> "env" | "db" | "none"
def save_llm(cfg, tested_token)    # 校验测试令牌后写库并失效缓存
```

api 内直接 `os.getenv("LLM_*")` 的 10 个文件（`providers/llm/__init__.py`、`agents/*`、`services/online_analysis/llm_client.py`、`services/quant/screen_nl.py`、`routers/internal_uzi.py` 等）改为调用它。

**opencode 启动时**（`gen-config.py`）：

1. 环境变量有大模型三项 → 按现在的逻辑生成
2. 否则请求 `GET http://api:8000/api/internal/runtime/llm`（带 `HUNTER_INTERNAL_KEY`，api 健康后才启动，compose 已有 `depends_on: service_healthy`）
3. 都没有 → **仍然生成配置并启动**，provider 指向 llm-shim，模型名为占位 `hunter-unconfigured`。**不能不写 provider**：不写时 opencode 会回落到内置的 OpenCode Zen，把用户的问题发给第三方免费模型

**llm-shim 改造**：

- 启动不再要求 `LLM_BASE_URL`
- 上游地址优先取请求头 `X-Hunter-Upstream`（由 opencode 的 provider `options.headers` 带上），否则取环境变量；只允许 `http(s)://`，且拒绝指向内部服务名与本机回环（防止被当成内网跳板）
- 模型名是 `hunter-unconfigured` 或没有上游时，返回 OpenAI 兼容的错误体：「大模型尚未配置，请打开首页完成初始化向导」，前端能直接显示

**热生效（向导第 5 步）**：api 保存成功后调用 opencode：

```
PATCH /config
{
  "provider": { "hunter-llm": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://llm-shim:3999/v1", "apiKey": "<key>",
                   "headers": { "X-Hunter-Upstream": "<用户填的地址>" } },
      "models": { "<模型名>": { "name": "<模型名>" } } } },
  "model": "hunter-llm/<模型名>"
}
```

opencode 写入实例目录 `config.json` 并在响应后销毁实例；下一次请求重建实例、重读配置、重启 MCP 子进程。前端第 5 步轮询 `GET /api/setup/engine-ready`（api 查 `GET /config/providers`，当前模型等于新模型即就绪）。

**`config.json` 与启动时生成的 `opencode.json` 谁覆盖谁**：源码中实例目录候选顺序为 `opencode.jsonc`、`opencode.json`、`config.json`，合并优先级需预研实测。**设计上不依赖顺序**：两份内容始终一致（都从数据库生成）；`config.json` 在容器可写层，重建容器即消失，启动时 `gen-config` 以数据库为准重新生成。

**降级方案**（预研若证明热生效不可靠）：api 调 `POST /global/dispose`（全局销毁）；仍不行则提示「保存成功，正在重启对话引擎」，由 opencode 入口脚本以子进程方式运行、监听配置版本号变化后自行重启进程（本地 compose 与云平台都适用，因为不依赖 Docker API）。

### 3.4 启动时自动迁移

- api 镜像内带 `db/migrations/*.sql`；启动时（`boot.sh` → `python -m app.migrate`）执行：
  - 建表 `schema_migrations(filename text primary key, checksum text, applied_at timestamptz)`
  - `pg_advisory_lock` 防多副本并发
  - 按文件名顺序执行未记录的迁移；已记录但校验和变化 → 日志 WARNING，不重跑
  - **首次在老库上运行**：现有迁移全部跑一遍后记录。
    > **M1 实测更正（2026-09-18）**：① 迁移文件实际是 **22 个**不是 21 个（有两个 `0020_` 前缀），加 M1 新增的 `0022_schema_migrations.sql` 共 23 个。
    > ② 「全部可重复执行」**不成立**：`0010_daily_close_view.sql` 与 `0014` 定义同一个视图、0014 多一列，而 `CREATE OR REPLACE VIEW` 不许减列，在跑过 0014 的库上重跑 0010 会报 `cannot drop columns from view`。已在 0010 开头补 `DROP VIEW IF EXISTS daily_close;` 根治（当时还没有任何库记录过它的 checksum，改的代价为零）。
    > ③ 迁移**必须排在 `init_db()` 之后**：7 个迁移文件 ALTER 的目标表是 `init_db()` 建的，而原来 postgres 的 initdb 在 api 启动之前就跑它们，那几个迁移一直在静默失败。
    > 详见 [`M1-成果与测试报告.md`](./M1-成果与测试报告.md) 第三节。
- CI 增加迁移校验：新迁移文件必须可重复执行（grep 规则 + 空库连跑两遍）
- 删除 postgres 的 initdb 挂载

### 3.5 用户 SKILL 不再依赖共享目录

| 现在 | 改为 |
|---|---|
| api 把 SKILL 写进 `user-skills/`，opencode 读同一目录 | **数据库为准**：新表 `user_skill_files(skill_name, path, content bytea, sha256, updated_at)`；api 写库（本地若挂了目录，同时写目录，保持向后兼容） |
| api 调 `POST /skill/refresh` | api 先调 opencode 内的同步插件拉取，再调 `/skill/refresh` |

opencode 侧新增插件 `hunter-skill-sync.ts`（放 huntercode `plugins/`）：实例初始化时与收到同步请求时，从 `GET /api/internal/skills/export`（内部口令）拉取清单，按 sha256 增量写入 `~/.config/opencode/skills/`（opencode 自己的卷）。

> 预研项：插件能否暴露可被 api 调用的同步入口；不能的话退化为「实例初始化时同步」，api 保存 SKILL 后调 `POST /instance/dispose` 触发重建（旧记录显示 dispose 不刷新 SKILL 列表，但若同步发生在实例初始化阶段、在 SKILL 扫描之前，则可生效，需实测）。

---

## 四、首启向导详细设计

### 4.1 页面与路由

```
apps/web/app/setup/
  page.tsx                 容器:步骤条、状态拉取、上一步/下一步
  steps/Unlock.tsx         第 0 步 · 初始化口令(公网访问才出现)
  steps/EnvCheck.tsx       第 1 步
  steps/ModelPick.tsx      第 2 步
  steps/ModelTest.tsx      第 3 步
  steps/DataSupply.tsx     第 4 步(复用 unlockClient 的平台 key 校验)
  steps/Done.tsx           第 5 步
  lib/setupClient.ts       /api/setup/* 调用(同源,经 web 转发)
```

- **进入条件**：对话页拿到会话后调 `GET /api/setup/status`；`llm.source == "none"` 且 `setup.completed_at` 为空 → `router.replace('/setup')`
- **设置页入口**：「重新运行初始化向导」（换模型用）；已锁定的项只读展示
- **演示站不受影响**：演示站大模型来自环境变量，`source=env`，永不进入向导
- 样式沿用 `app/lib/hunter-ui.tsx`、`hunter-theme.ts`；文案全中文

### 4.2 第 0 步 · 初始化口令（公网安全）

问题：云平台实例一创建就在公网上，单用户模式下谁先打开谁是管理员，还能改大模型配置、看到别人的 key。

| 场景 | 行为 |
|---|---|
| 请求来自本机 / 局域网（`127.0.0.1`、`::1`、`10/8`、`172.16/12`、`192.168/16`，以 web 转发时附带的真实来源地址判断） | 跳过第 0 步 |
| 公网访问且配置了 `HUNTER_SETUP_TOKEN` | 必须输入口令；通过后签发 30 分钟的初始化会话；连续错 5 次锁 15 分钟 |
| 公网访问且**没有**配置口令 | 拒绝进入向导，页面说明「请在部署平台的环境变量里查看或设置 HUNTER_SETUP_TOKEN」 |
| 向导完成后 | 云平台模板默认 `HUNTER_SINGLE_USER=0`：向导最后一步引导创建管理员账号（复用现有注册流程，首个账号为管理员） |

云平台模板为 `HUNTER_SETUP_TOKEN` 生成随机值，部署完成页 / 平台变量面板可见。

### 4.3 第 1 步 · 环境自检

| 检查项 | 数据来源 | 异常时的提示 |
|---|---|---|
| api / 数据库 / redis / opencode / llm-shim 连通 | api 逐个探测 | 指出哪个服务、给出查看日志的命令（本地）或平台日志入口（云） |
| 数据库迁移版本 | `schema_migrations` 最新文件名 | 迁移失败时显示失败的文件与错误摘要 |
| 密钥来源与强度 | `runtime_config.source` + 弱密钥检测 | 弱密钥：黄色警告 + 轮换步骤与后果说明 |
| 访问方式 | 来源地址 + `HUNTER_SINGLE_USER` | 公网 + 单用户模式：红色警告 |

### 4.4 第 2 步 · 选大模型

预设来自 `data/llm-presets.json`（随镜像分发），数值抄自 `docs/model-testing/model-compat-matrix.md` 并标注实测日期；**模型名逐一与 `docs/env-samples/` 核对，对不上的留空让用户填，不凭印象写**。

| 预设 | 地址 | 清洗开关 | 实测命中 / 耗时 | 标签 |
|---|---|---|---|---|
| DeepSeek v4 pro | `https://api.deepseek.com/v1` | `1` | 6/7 · 30 秒 | 推荐 · 国内直连 |
| Qwen 3.8 Max | AIHubMix 网关 | `1` | 7/7 · 48 秒 | 国内首推（本机实测经网关需宿主机代理） |
| Claude Sonnet 5 | AIHubMix 网关 | `auto` | 7/7 · 26 秒 | 海外首推 |
| Gemini 3.5 Flash | 任意 OpenAI 兼容网关 | `auto` | 6/7 · 62 秒 | 便宜快 |
| 自定义 | 用户填 | 由第 3 步检测结果决定 | — | — |

### 4.5 第 3 步 · 填 key 当场测试

`POST /api/setup/llm/test`，入参地址、key、模型名；依次执行：

| 检测 | 请求 | 通过条件 | 失败分类 → 提示 |
|---|---|---|---|
| ① 连通 | `GET {base}/models`，10 秒 | 2xx；404 视为网关未实现该接口，继续 | 超时 / DNS 失败 → 「服务器连不上该地址」（本地额外提示代理变量）；401/403 → 「key 无效」；SSL EOF → 「网关拦截了容器连接，需配置代理」 |
| ② 对话 | `POST /chat/completions`，**只发 1 条 user 消息**（避开部分 Gemini 版本对「system + user 两条消息」返回 400 的问题），`max_tokens=16`，30 秒 | 有非空文本；正文为空但 `finish_reason=length`（被探针预算截断，按通过 + 黄色提示） | 404 / model_not_found → 「模型名不对」并列出 ① 拿到的可用模型；回复含 `<think>` → 黄色提示；正文为空且非 `length` 截断 → 「上游没产出内容」 |
| ③ 工具调用 | 带 1 个工具，schema 故意含 `$schema`、`additionalProperties`（模拟 opencode 实际发送的格式） | 返回 `tool_calls` 且函数名正确 | 400 且报错涉及 schema → 用 shim 同款清洗函数重试，通过则建议清洗开关设 `1`；仍失败或不调用工具 → 「该模型不支持工具调用，行情与分析功能不可用」，**不允许保存** |

- 检测请求由 api 发出（与 opencode 实际调用走同一网络路径），经 llm-shim 的清洗函数（抽成 `scripts/llm-shim/schema_clean.py`，shim 与 api 共用一份）
- 全部通过返回 `test_token`：对「地址 + 模型 + key 摘要」做 HMAC，10 分钟有效；`PUT /api/setup/llm` 必须携带，配置被改动或过期即拒绝
- 每次检测显示真实耗时；不做任何估算展示

### 4.6 第 4 步 · 数据供给三选一

| 选项 | 行为 |
|---|---|
| 免费开源源 | 直接下一步；说明覆盖范围与容器内访问 AKShare 可能不稳 |
| 平台数据管道 | 粘贴 `hunt_tools_` key，调用现有 `PUT /api/hunter/unlock`（先校验再保存） |
| 自接工具 / MCP | 跳转 `/mcp-config`，可「稍后」 |

### 4.7 第 5 步 · 完成

1. 调 `POST /api/setup/apply`：api 调 opencode `PATCH /config` → 前端轮询 `engine-ready`，进度条上限 60 秒
2. 超时仍未就绪 → 显示降级提示与排错入口（不假装成功）
3. 3 个示例问题按钮 → `/chat?q=...`，对话页填入输入框，不自动发送
4. 写 `setup.completed_at`；云平台模式下引导创建管理员账号

### 4.8 接口清单（`apps/api/app/routers/setup.py`）

| 方法 | 路径 | 鉴权 | 作用 |
|---|---|---|---|
| POST | `/api/setup/unlock` | 公开 + 限流 | 校验初始化口令，签发初始化会话 |
| GET | `/api/setup/status` | 管理员或初始化会话 | 汇总状态（服务、迁移、密钥、大模型来源、数据供给、是否完成、访问方式） |
| GET | `/api/setup/llm-presets` | 同上 | 读 `data/llm-presets.json` |
| POST | `/api/setup/llm/test` | 同上 + 限流 | 三项检测，返回分项结果与 `test_token` |
| PUT | `/api/setup/llm` | 同上 | 校验 `test_token` 后写库；环境变量锁定时 409 |
| POST | `/api/setup/apply` | 同上 | 热生效到 opencode |
| GET | `/api/setup/engine-ready` | 同上 | 就绪探测 |
| POST | `/api/setup/complete` | 同上 | 标记完成（含「稍后配置」） |
| GET | `/api/internal/runtime/llm` | 内部口令 | 供 opencode 启动时拉取配置 |
| GET | `/api/internal/skills/export` | 内部口令 | 供 SKILL 同步插件拉取 |

---

## 五、一键部署模板设计

### 5.1 平台能力对照（2026-09-17 查官方文档）

| 平台 | 模板形式 | 多服务 | 卷 | 注入配置文件 | 随机密钥 | 自动域名 HTTPS | 上架 / 按钮 |
|---|---|---|---|---|---|---|---|
| **Zeabur** | `template.yaml`（`kind: Template`），服务类型 `PREBUILT` 镜像或 `GIT` | ✅ 支持 `dependencies` | ✅ 每服务 `volumes`（`id` + `dir`） | ✅ `configs`（`path` + `template`，支持变量替换） | ✅ `${PASSWORD}` 类变量；服务可 `expose` 变量供其他服务引用 | ✅ `domainKey` 绑定域名变量 | CLI 发布到模板市场；按钮格式上线前核实 |
| **Sealos** | `template/<名>/index.yaml`（Template CR + K8s 资源） | ✅ | ✅（StatefulSet 卷） | ✅ ConfigMap | ✅ `${{ random(8) }}`，输入 `${{ inputs.x }}` | ✅ Ingress | 向 `labring-actions/templates` 提 PR，需 `README.md` / `README_zh.md` / logo / 截图；按钮 `[![](https://sealos.io/Deploy-on-Sealos.svg)](https://sealos.io/products/app-store/<name>)` |
| **Railway** | **没有模板文件**：官方流程是先把项目跑通，再 Settings → Generate Template from Project 反向生成（M4 核实） | ✅ | ⚠️ **一个服务只能挂一个卷**；卷以 **root 属主**挂载，非 root 镜像要 `RAILWAY_RUN_UID=0`（M4 核实） | 无（镜像自带即可） | ✅ `${{secret(length?, alphabet?)}}` 默认 32 字符、`${{randomInt()}}`；引用变量 `${{服务名.变量}}` | ✅ | 控制台 Publish → 模板市场；按钮 `https://railway.com/button.svg` → `https://railway.com/new/template/<CODE>`（维护者可获佣金） |
| **1Panel** | `apps/<key>/` 下 `data.yml` + 版本目录 `docker-compose.yml` | ✅ 标准 compose | ✅ | ✅ 版本目录可带 `data/` 与 `scripts/` | ✅ **`type: password` + `random: true`**（M3 更正：**没有** `type: random`） | 由面板反代配置 | 向 `1Panel-dev/appstore` 提交 |
| **Coolify / Dokploy** | 直接用 docker-compose（**可整段粘贴**） | ✅ | ✅ 具名卷原样，平台只读展示 | 依赖 compose 能力 | Coolify：magic 变量 `SERVICE_PASSWORD_<ID>`（32 位）/ `SERVICE_FQDN_<服务>_<端口>`；**Dokploy 没有生成器**，UI 变量写进 `.env`、**不自动注入容器**，靠 compose 的 `${VAR}` 插值（M4 核实） | ✅ Coolify 按 FQDN 变量；Dokploy 在 Domains 页签选服务 + 端口，Traefik 标签由平台自动加 | 无需上架，文档给步骤 |

> Zeabur 按钮 URL、Railway 模板文件格式、Coolify/Dokploy 细节本次未在官方文档中查到原文，**模板开发第一天核实**，不凭印象写进 README。

### 5.2 平台优先级

| 批次 | 平台 | 理由 |
|---|---|---|
| 第一批 | **Zeabur** | 能力最全（多服务 + 卷 + 配置文件注入 + 随机密钥 + 自动域名），国内外用户都能用 |
| 第一批 | **Sealos** | 国内用户主力选择之一；有官方模板仓库与部署按钮 |
| 第二批 | **Railway** | 海外开发者主流；支持 `secret()` |
| 第二批 | **1Panel 应用商店** | 国内自有服务器用户；本质是 compose，改造成本低 |
| 第三批 | Coolify / Dokploy、宝塔、腾讯云轻量 / 阿里云计算巢 | 文档化或另行立项 |

> **M4 实际执行（2026-09-18）**：1Panel 在 M3 提前做完，所以第二批做的是 **Railway + Coolify / Dokploy**
> （后两家从第三批提前 —— 它们直接吃 compose，改造成本极低）。宝塔与云厂商市场仍未做。
> **五个平台一次真实部署都没做、一家都没上架**，见 [`M4-成果与测试报告.md`](./M4-成果与测试报告.md) 第八节。

### 5.3 模板共用规格（所有平台一致）

| 服务 | 镜像 | 对外端口 | 卷 | 关键环境变量 |
|---|---|---|---|---|
| web | `hunter-community-web:<版本>` | **3000，唯一绑定域名** | — | `HERMES_API_URL=http://api:8000`、`OPENCODE_URL`、`HUNTER_INTERNAL_KEY` |
| api | `hunter-community-api:<版本>` | 不对外 | `/opt/hunter-user-skills`、`/opt/hunter-packages` | `DATABASE_URL`、`REDIS_URL`、`JWT_SECRET`、`HUNTER_INTERNAL_KEY`、`HUNTER_SETUP_TOKEN`、`HUNTER_SINGLE_USER=0`、`HUNTER_MINIMAL_BOOT=1`、`OPENCODE_URL`、**`LLM_SHIM_URL`**（M3 补，见下） |
| opencode | `hunter-community-opencode:<版本>` | 不对外 | `/home/hunter/.local`（会话数据，**必须持久化**） | `JWT_SECRET`、`HUNTER_INTERNAL_KEY`、`HERMES_API_URL`、`LLM_SHIM_URL` |
| llm-shim | `hunter-community-llm-shim:<版本>` | 不对外 | — | 无必填 |
| postgres | `postgres:16-alpine` | 不对外 | 数据目录 | `POSTGRES_PASSWORD`（模板生成） |
| redis | `redis:7-alpine` | 不对外 | `/data` | — |

- **模板生成的随机值**：`JWT_SECRET`（≥ 48 字符）、`HUNTER_INTERNAL_KEY`、`POSTGRES_PASSWORD`、`HUNTER_SETUP_TOKEN`
- **模板不要求用户填写任何大模型配置**（全部交给向导）；高级用户仍可在平台变量里填 `LLM_*`，此时向导显示「已锁定」
- **镜像源**：GHCR 为主；国内平台若拉取 GHCR 受限，需先完成执行计划中的 Docker Hub / 阿里云 ACR 同步（本方案列为 Sealos 模板的前置预研项）

#### 资源建议（M3 按 R0 实测回填）

| 场景 | 实测内存（六容器合计） |
|---|---|
| 空闲 60 秒 | 1171 MB |
| 全市场扫描 | 1250 MB |
| 深度分析 | 1245 MB |
| **两者并发（峰值）** | **1271 MB** |

| | 值 | 依据 |
|---|---|---|
| 最低 | **2 核 2 GB** | 峰值 1271 MB，2 GB 留给系统约 700 MB，紧但可跑 |
| 推荐 | **2 核 4 GB** | 舒适 |
| 磁盘 | **≥ 10 GB** | 镜像实测合计 **3.8 GB**（web 1.67 / api 909 MB / opencode 618 MB / postgres 420 MB / redis 61 MB / shim 83 MB）+ 数据 |

内存大头是 `opencode`（869 MB，占 68%），而且**几乎与负载无关** —— 那是常驻占用，
不是峰值风险；真正随负载涨的是 api（105 → 203 MB）。

> 原文写「镜像约 3.4 GB」，实测 3.8 GB，已更正。
> 三个模板里每个工作负载的 requests / limits 都按上表这几个数字写。

#### M3 实测补进来的三条硬性要求

1. **api 必须显式设 `LLM_SHIM_URL`**（原方案只给了 opencode）。向导保存后 api 走
   `PATCH /global/config` 把 provider 的 `baseURL` 推给 opencode，那个值取自
   **api 容器的** `LLM_SHIM_URL`；不设就回落到硬编码的 `http://llm-shim:3999/v1`。
   服务名不叫 `llm-shim` 的部署（Sealos `<应用名>-llm-shim`、1Panel
   `<容器前缀>-llm-shim`、Zeabur `llm-shim.zeabur.internal`）会被推进去一个解析不了
   的主机名 —— Sealos 等价栈上实测：**向导五步全绿、`engine-ready` 也返回 true，
   但发消息永远收不到回复，日志里一条报错都没有**。
2. **opencode 的卷在「空目录」语义的平台上必须先改属主**。K8s 用
   `securityContext.fsGroup: 1001`；Zeabur 的卷同样是空目录（官方指南原文
   *Zeabur Volume defaults to empty directory*），用 `init` 规则 chown；
   1Panel 是 bind mount，用 `scripts/init.sh`。实测这三条都是**不做就 CrashLoop**。
3. **`${PASSWORD}` 在 Zeabur 上是「每个服务一个」**，不是「每次引用一个」。要四把
   互不相同的密钥，只能把它们借位定义在四个不同的服务上再 `expose`。Sealos 的
   `${{ random(N) }}` 每处独立求值，没有这个问题。

### 5.4 Zeabur 模板草图（M3 已按官方文档核实并实现 → [`deploy/zeabur/template.yaml`](../../deploy/zeabur/template.yaml)）

> 下面这份草图**保留原样留档**，与最终实现的差异全部记在
> [`M3-成果与测试报告.md`](./M3-成果与测试报告.md) 第二节。最要紧的四处：
> `template` 用 `PREBUILT_V2`；服务间主机名靠 `${CONTAINER_HOSTNAME}` + `expose`
> 而不是写死服务名；四把密钥要借位到四个服务（`${PASSWORD}` 每服务一个）；
> api 必须补 `LLM_SHIM_URL`。


```yaml
apiVersion: zeabur.com/v1
kind: Template
metadata:
  name: HunterCode
spec:
  description: 自部署 AI 投研助手 · A股/港股/美股 · 数据和对话在你自己的实例上
  variables:
    - key: PUBLIC_DOMAIN
      type: DOMAIN
      name: 访问域名
      description: 打开后按初始化向导配置大模型
  services:
    - name: postgres
      template: PREBUILT
      spec:
        source: { image: postgres:16-alpine }
        volumes: [{ id: pgdata, dir: /var/lib/postgresql/data }]
        env:
          POSTGRES_PASSWORD: { default: "${PASSWORD}", expose: true }
          POSTGRES_USER: { default: hunter }
          POSTGRES_DB: { default: hunter }
    - name: redis
      template: PREBUILT
      spec:
        source: { image: redis:7-alpine }
        volumes: [{ id: redisdata, dir: /data }]
    - name: api
      template: PREBUILT
      dependencies: [postgres, redis]
      spec:
        source: { image: ghcr.io/agentpit-io/hunter-community-api:1.1.0 }
        volumes:
          - { id: userskills, dir: /opt/hunter-user-skills }
          - { id: packages, dir: /opt/hunter-packages }
        env:
          DATABASE_URL: { default: "postgresql://hunter:${POSTGRES_PASSWORD}@postgres:5432/hunter" }
          REDIS_URL: { default: "redis://redis:6379/0" }
          JWT_SECRET: { default: "<模板生成的随机值>", expose: true }
          HUNTER_INTERNAL_KEY: { default: "<模板生成的随机值>", expose: true }
          HUNTER_SETUP_TOKEN: { default: "<模板生成的随机值>" }
          HUNTER_SINGLE_USER: { default: "0" }
    - name: llm-shim
      template: PREBUILT
      spec:
        source: { image: ghcr.io/agentpit-io/hunter-community-llm-shim:1.1.0 }
    - name: opencode
      template: PREBUILT
      dependencies: [api, llm-shim]
      spec:
        source: { image: ghcr.io/agentpit-io/hunter-community-opencode:1.1.0 }
        volumes: [{ id: sessions, dir: /home/hunter/.local }]
        env:
          JWT_SECRET: { default: "${JWT_SECRET}" }
          HUNTER_INTERNAL_KEY: { default: "${HUNTER_INTERNAL_KEY}" }
          HERMES_API_URL: { default: "http://api:8000" }
          LLM_SHIM_URL: { default: "http://llm-shim:3999/v1" }
    - name: web
      template: PREBUILT
      dependencies: [api, opencode]
      spec:
        source: { image: ghcr.io/agentpit-io/hunter-community-web:1.1.0 }
        ports: [{ id: web, port: 3000, type: HTTP }]
        env:
          HERMES_API_URL: { default: "http://api:8000" }
          OPENCODE_URL: { default: "http://opencode:3901" }
          HUNTER_INTERNAL_KEY: { default: "${HUNTER_INTERNAL_KEY}" }
      domainKey: PUBLIC_DOMAIN
```

核实项：服务间主机名是否就是服务名；多个随机变量如何分别生成；`expose` 变量的引用写法；卷的首次属主（opencode 以 uid 1001 运行，卷根目录需可写——必要时 opencode 镜像入口增加属主自检与明确报错）。

### 5.5 Sealos / Railway / 1Panel 要点

| 平台 | 要点 |
|---|---|
| Sealos | 每个服务一个 StatefulSet（有卷）或 Deployment；密钥用 `${{ random(48) }}` 在 `defaults` 里生成（每处独立求值），**直接以环境变量注入多个工作负载，不需要另建 Secret**；postgres / redis 走 KubeBlocks `Cluster`（与仓库里 100+ 个现有模板一致），凭据取自 KubeBlocks 生成的 `<名>-pg-conn-credential` / `<名>-redis-redis-account-default`；web 配 Ingress（**必须放宽 `proxy-read-timeout` 到 600 并关 `proxy-buffering`**，深度分析是 SSE 流式，实测单次 20～54 秒）；opencode 必须 `fsGroup: 1001`；提交是 `template/<名>/index.yaml` + `README.md` / `README_zh.md` / logo / `website-screenshot.webp` |
| Railway | 在一个示例项目里配好 6 个服务后「生成模板」；密钥 `${{secret(48)}}` 定义在 api，其余服务用引用变量取同一个值；内网主机名用 `${{api.RAILWAY_PRIVATE_DOMAIN}}` 这类引用而非写死。**M4 实测补三条硬性要求**：① postgres 必须设 `PGDATA=<挂载点>/pgdata`（卷是 ext4、根目录带 `lost+found`，`initdb` 拒绝非空目录 → 无限重启）；② postgres / redis / opencode 必须设 `RAILWAY_RUN_UID=0`（卷以 root 属主挂载）；③ **一个服务只能挂一个卷**，api 的两份数据合并进同一个卷的两个子目录，且挂载点**不能选 `/opt/hunter-data`**（那是镜像自带的静态数据目录；Docker 具名卷会拷贝镜像内容所以本机看不出来，Railway 的卷不拷贝） |
| Coolify / Dokploy | 单机 Docker，**三个容器可以共用 `hunter_secrets` 卷** —— 所以这两份 compose 里根本没有 `JWT_SECRET` / `HUNTER_INTERNAL_KEY`，由 api 首启生成、web 与 opencode 只读挂同一个卷读回。少两个能填错的地方就少两类「服务全绿但一对话 401」的故障。云平台（Zeabur / Sealos / Railway）不能这么做 |
| 1Panel | `data.yml` 表单：访问端口、`HUNTER_SETUP_TOKEN`、`POSTGRES_PASSWORD`（两者都是 **`type: password` + `random: true`**，M3 更正）；compose 与 3.1 的默认 compose 基本一致，但**六个具名卷改成 `./data/*` 相对挂载**（面板的应用备份只打包安装目录，用具名卷会漏掉对话正文）、服务间一律用 `${CONTAINER_NAME}-<服务>`（`1panel-network` 是全面板共用的外部网络，裸服务名会撞别名）；面板反代由用户在面板内配置 |

### 5.6 README 入口

在「5 分钟跑起来」之前增加「一键部署」小节：各平台按钮 + 一句话（适合谁、大概费用由平台决定、部署后打开域名按向导操作、初始化口令在平台变量 `HUNTER_SETUP_TOKEN` 中查看）。按钮只在对应模板**上架并实测通过后**才加。

> M3 状态：三个模板都写完并做了等价验证，但**都没有平台账号、都没上架**，所以
> **README 本轮不加任何部署按钮**，只在 `docs/deploy/` 下给了三篇用户视角的说明。
>
> **M4 最终状态（v1.1.0 发布时）**：平台增加到**五个**（Zeabur / Sealos / Railway / 1Panel / Coolify·Dokploy），
> 全部做过等价验证，**仍然一个都没上架、一次真实部署都没做**。README 与 README_EN 里加了
> 「一键部署到云平台」小节，**只有文档链接、没有任何按钮**，并写清了为什么没有按钮、
> 以及「有账号的朋友帮忙实测一次，验过就加按钮」。

---

## 六、安全设计汇总

| 风险 | 措施 |
|---|---|
| 公网实例被抢先初始化 | 第 0 步初始化口令；无口令的公网访问拒绝进入向导；云模板默认多用户模式 |
| key 泄露 | 数据库加密存储；接口只回显打码值；日志不打印；`/api/setup/status` 不返回 key |
| llm-shim 被当内网跳板 | 上游地址只允许 `http(s)`，拒绝内部服务名、回环、链路本地地址；shim 不对外暴露端口 |
| 测试接口被滥用刷他人 key | 需初始化会话 + 限流（每分钟 5 次） |
| 未测试的配置被保存 | `test_token` 绑定配置摘要与有效期 |
| 弱密钥 | 启动日志 ERROR + 向导警告，不自动轮换 |
| 模板镜像被替换 | 模板锁具体版本号；后续可加镜像签名（执行计划阶段 5） |

---

## 七、测试方案

| 层级 | 内容 |
|---|---|
| 单元 | `runtime_config` 优先级与空串判断；`test_token` 篡改/过期；迁移执行器（空库、老库、重复执行、并发加锁）；shim 上游地址校验；三项检测的全部失败分类（模拟上游） |
| 集成（CI） | 扩展现有 `e2e-compose.yml`：**不建 `.env`** 冷启动 → 6 服务健康 → 迁移记录齐全 → `/api/setup/status` 返回未配置；有 `SMOKE_LLM_API_KEY` 时走完 `test → save → apply → engine-ready` 并发一条对话 |
| 老库升级 | 用 8 月版本的数据卷快照启动新版本，确认自动补齐迁移、原有平台 key 仍可解密、原会话仍在 |
| 真浏览器 | playwright：本机访问走完 5 步；模拟公网来源必须输入口令；环境变量锁定时不出现向导；换模型后新对话使用新模型；截图存 `docs/screenshots/setup-wizard/` |
| 平台验收 | 每个平台用**新账号**从按钮部署 → 打开域名 → 输入口令 → 完成向导 → 对话成功；记录部署耗时、资源规格、月费（平台账单实际值）；写入 `docs/deploy/<平台>.md` |

---

## 八、排期（20 个工作日，含预研 1 天与缓冲 2 天）

| 里程碑 | 工作日 | 日期 | 内容 | 交付 / 验收 |
|---|---|---|---|---|
| **预研** | 1 | 09-21（一） | ① `PATCH /config` 热生效实测（生效耗时、MCP 重启耗时、`config.json` 与 `opencode.json` 优先级）② SKILL 同步插件可行性 ③ 深度分析与全市场扫描时的内存峰值 | 预研结论写入本文第十节；如热生效不可行，启用 3.3 降级方案并调整排期 |
| **M1 自包含与配置入库** | 4 | 09-22 ~ 09-25 | 3 个新镜像（opencode 包装、llm-shim、api 带 SKILL/迁移）+ 多架构发布；自动迁移；`runtime_config` 与 10 个文件改造；密钥生成；shim 免配置启动与上游头；compose 拆分默认 / 开发两份 | 不建 `.env` 冷启动 6 服务健康；老库升级通过；原有 `.env` 用户行为不变 |
| **M2 首启向导** | 5 | 09-28 ~ 09-30、10-08 ~ 10-09 | setup 接口 10 个；向导 6 个页面；热生效与就绪探测；初始化口令；SKILL 同步插件（huntercode） | playwright 全流程通过；公网口令逻辑通过；换模型无需重启 |
| **M3 第一批模板** | 4 | 10-12 ~ 10-15 | Zeabur、Sealos 模板与平台实测；README 一键部署小节 | 两个平台新账号从零部署成功，记录耗时与费用 |
| **M4 第二批模板与发布** | 4 | 10-16 ~ 10-21 | Railway、1Panel；文档 `docs/deploy/*`；发版 v1.1.0；公告 | 两个平台实测通过；v1.1.0 Release 与公告帖 |
| 缓冲 | 2 | 10-22 ~ 10-23 | 平台审核往返、问题修复 | — |

> 10-01 ~ 10-07 国庆不排上线动作；如遇调休按实际工作日顺延。Sealos 若因 GHCR 拉取受限而阻塞，先完成 Docker Hub / 阿里云 ACR 同步（执行计划待办），或将 Sealos 移到第二批。

---

## 九、改动文件清单

| 仓库 | 文件 | 改动 |
|---|---|---|
| hunter-community | `docker-compose.yml`、新增 `docker-compose.dev.yml` | 默认只用镜像；挂载移入开发覆盖文件；新增 `hunter_secrets` 卷；去掉 initdb 挂载；`JWT_SECRET` 不再强制 |
| hunter-community | 新增 `deploy/opencode.Dockerfile`、`deploy/llm-shim.Dockerfile`；改 `apps/api/Dockerfile`（上下文改仓库根）与 `.dockerignore` | 自包含镜像 |
| hunter-community | `.github/workflows/docker-publish.yml`、`e2e-compose.yml` | 4 个镜像多架构发布；无 `.env` 冷启动与向导冒烟 |
| hunter-community | 新增 `apps/api/boot.sh`、`app/migrate.py`、`app/services/runtime_config.py`、`app/routers/setup.py`；改 `app/utils/crypto.py`、`middleware/auth.py`、`routers/auth.py`、`routers/chat_skill.py`、`services/opencode_admin.py`、`services/skill_files.py` 及 10 个读 `LLM_*` 的文件 | 配置入库、迁移、向导接口、SKILL 入库 |
| hunter-community | 新增 `db/migrations/0022_user_skill_files.sql`、`0023_schema_migrations.sql` | 新表 |
| hunter-community | `scripts/opencode/gen-config.py`、`scripts/llm-shim/shim.py`、新增 `scripts/llm-shim/schema_clean.py` | 从 api 拉配置、未配置占位；shim 免配置启动与上游头校验 |
| hunter-community | 新增 `data/llm-presets.json`；新增 `apps/web/app/setup/**`；改 `app/chat/page.tsx`、`app/settings/page.tsx` | 向导前端 |
| hunter-community | 新增 `deploy/zeabur/template.yaml`、`deploy/sealos/index.yaml`、`deploy/1panel/**`、`docs/deploy/*.md`；改 README ×2、`.env.example`、`docs/01-getting-started.md`、`CHANGELOG.md` | 模板与文档 |
| huntercode | 新增 `plugins/hunter-skill-sync.ts` | SKILL 同步插件（发布新的基础镜像标签） |
| 各平台模板仓库 | `labring-actions/templates`、`1Panel-dev/appstore` 的 PR；Zeabur / Railway 平台内发布 | 上架 |

---

## 十、预研项与风险（已按 R0 实测回填）

> R0 实测于 2026-09-18（上海时间），预发栈 `hunter-staging`，代码基准 hunter-community main `404dda86`、huntercode dev `346f1119`。
> 完整数据、命令与源码依据见 [`R0-预研结论.md`](./R0-预研结论.md)。**与本文前面章节冲突的地方，以本节的实测结论为准。**

| # | 预研 / 风险 | 判定标准 | 实测结论 | 处置 |
|---|---|---|---|---|
| 1 | `PATCH /config` 热生效 | 保存后 30 秒内新对话使用新模型，MCP 全部重新连接 | **不通过**。`PATCH /config` 返回 200（9 ms）并写出 `<实例目录>/config.json`，但该文件**不在配置加载路径上**，内容永不生效；PATCH 后 `GET /config` 与新会话仍用旧模型。**改用 `PATCH /global/config` 则通过**：PATCH 34～48 ms、`GET /config` 反映新模型 0.52 s、6/6 MCP 重连 3.4～3.6 s、新会话首个请求 3.4～3.7 s，**端到端 7.42 / 7.87 秒**；旧会话可读、可续聊，会话不丢 | 3.3 的热生效接口改为 `PATCH /global/config`，`opencode_admin.apply_llm()` 按此实现。**不需要**「入口脚本子进程重启」降级方案 |
| 2 | `config.json` 与启动时生成的 `opencode.json` 优先级 | 两者内容不一致时明确谁生效 | 工作区根的 `config.json` **根本不参与合并**，冲突不存在。真正的优先级是：全局 `~/.config/opencode/{config,opencode}.json(c)` < `OPENCODE_CONFIG` < 从实例目录向上找到的 `opencode.json(c)` < `.opencode/opencode.json(c)`。所以 gen-config 写的项目 `opencode.json` **会覆盖**全局配置——首次 `PATCH /global/config` 时出现 provider 合并成功但 `model` 没变 | `gen-config.py` 分两处写：`mcp`/`instructions` → 项目 `opencode.json`；`provider`/`model` 在「配置来自数据库」时写全局 `~/.config/opencode/opencode.jsonc`，在「配置来自环境变量」时仍写项目文件（项目文件优先级最高，天然实现「环境变量锁定」） |
| 3 | SKILL 同步插件 | api 装 SKILL 后，模型 60 秒内可见 | **通过，且不需要写插件**。插件钩子里没有 HTTP 路由扩展能力，但 opencode **原生支持 `skills.urls`**：`Discovery.pull(base)` 拉 `<base>index.json`（`{skills:[{name,files,version}]}`）、文件取自 `<base><name>/<file>`、按 `version` 原子换版；`POST /skill/refresh` 会重跑整个发现流程（含 URL 拉取）。实测注入 urls → 可见 **0.48 s**；改内容 + bump version + refresh → 可见 **0.07 s**；模型实际调用 `skill` 工具答出埋点口令。写本地目录 `~/.config/opencode/skills/` + refresh 同样 **0.02 s** 可见 | 3.5 改为：api 暴露 `GET /api/internal/skills/{key}/index.json` 与 `/{key}/{name}/{file}`（**鉴权放 URL 路径段**——`Discovery.pull` 用裸 GET，带不了自定义头），gen-config 写 `skills.urls`，api 写完 SKILL 后调 `POST /skill/refresh`。**huntercode 无改动，不需要发 `hc-v1.18.12-slim.2`**，opencode 包装镜像继续 `FROM hunter-opencode:1.18.12-slim.1` |
| 4 | 深度分析 / 全市场扫描时的内存峰值 | 实测记录 | 空闲 **1171 MB**；全市场扫描 **1251 MB**；深度分析 **1245 MB**；两者并发 **1271 MB**；全程各服务峰值之和 1284 MB。大头是 opencode（约 860～880 MB，**与负载几乎无关**，常驻），随负载增长的是 api（105 → 204 MB） | 5.3 的「最低 2 核 2 GB、推荐 2 核 4 GB」**维持不变**，实测支持 |
| 5 | 国内平台拉取 GHCR | Sealos 实测拉取成功且耗时可接受 | **未测**（无国内网络测试机，不猜数据）。已测：agentpit 与 fin-r1 **匿名拉取均成功**（api:latest 全新拉取 8.5 s / 18.5 s）。架构现状：`hunter-community-api`、`hunter-community-web` **仅 `linux/amd64`**；`hunter-opencode` 已是 `amd64+arm64` | 保留「国内平台受限则先做 Docker Hub / 阿里云 ACR 同步」的前置项，留到 M3 Sealos 阶段用真实平台验证。M1 把 api / web / opencode 包装 / llm-shim 四个镜像都补成 `linux/amd64,linux/arm64` |
| 6 | 卷首次挂载属主（opencode uid 1001） | 各平台新卷可写 | **分两种**。Docker 具名卷：镜像里 `/home/hunter/.local` 已存在且属主 1001，空卷首次挂载会继承内容与属主 → 卷根 `1001:1001`，`mkdir`/写文件 **均 OK**。模拟 K8s PVC（空目录 bind mount，属主 `1000:1003` 或 `0:0`，不拷贝镜像内容）→ **`Permission denied`** | compose 语义平台（Zeabur / Railway / 1Panel / Coolify / Dokploy）无需额外处理；**K8s 平台（Sealos）模板必须设 `securityContext.fsGroup: 1001` 或加 initContainer chown**。opencode 入口脚本增加 `/home/hunter/.local` 可写自检 + 明确中文报错（列入 M1） |
| 7 | 平台模板审核周期 | — | 未到时点 | 排期已留 2 天缓冲；README 按钮在上架后再加 |
| 8 | 老用户升级兼容（`.env` 配置、本地挂载、老数据卷） | 老 `.env` 用户升级后不出现向导、功能不变 | 未到时点（M1 测试用例 7、10 覆盖） | 保留环境变量优先规则；开发覆盖文件保持原挂载 |

### 10.1 R0 顺带核实到的、会改变做法的事实

1. **GHCR `latest` 就是 main**：api/web 两个 `latest` 镜像的 `org.opencontainers.image.revision` 等于 main 的 `404dda86…`。预发栈可直接用预构建镜像，不必在 agentpit 上构建 web。
2. **上游不可达时对话「挂住」而不是报错**：把 provider 的 `baseURL` 指向不可达地址后，一条 `你好` 超过 100 秒没有返回。所以 3.3 里 llm-shim 的「大模型尚未配置」错误**必须由 shim 自己立刻返回**，不能靠上游超时兜底。
3. **`models` 字段会累积**：`updateGlobal` 是 `mergeDeep`，删不掉键，多次换模型后旧模型名仍留在全局配置里。属外观问题（`model` 指向新模型、对话正确，容器重启时 gen-config 整份重写清空）。向导第 5 步的就绪判定用「当前 `model` 等于新模型」，不要用「models 列表只有一个」。
4. **`GET /config` 明文回显 `apiKey`**，全局配置文件里也是明文（容器可写层，recreate 即消失）。与改造前的 `opencode.json` 行为一致，不是新增风险，但**向导前端不要直接代理 opencode 的 `/config`**。
5. **实例重建的可靠日志信号**：每次实例重建会重新加载全部 6 个插件，日志里 `[hunter-mcp-context] loaded` 等 6 行会整组重复出现。
6. **`docker-compose.yml` 里一段老注释已过时**：「镜像里根本没有 /home/hunter/.local」在 `1.18.12-slim.1` 上不成立，该目录存在且属主 1001，原来那个 `EACCES: mkdir '/home/hunter/.local/state'` 复现不了。挂 `.local` 整目录的做法仍然保留（同时覆盖 `state` 与 `share`）。

### 10.2 排期影响

M2 不需要做热生效降级方案、也不需要写 huntercode 插件与发布新的基础镜像标签，**预计比原排期省 1～2 天**。

## 十一、参考

- Zeabur 模板格式：https://zeabur.com/docs/en-US/template/template-in-code
- Railway 模板创建：https://docs.railway.com/guides/create
- Sealos 模板仓库：https://github.com/labring-actions/templates
- 1Panel 应用提交说明：https://github.com/1Panel-dev/appstore/wiki
- 仓库内：`docs/model-testing/model-compat-matrix.md`、`docs/image-slim/`、`scripts/opencode/gen-config.py`、`scripts/llm-shim/shim.py`
- huntercode：`packages/opencode/src/server/routes/instance/httpapi/handlers/config.ts`、`lifecycle.ts`、`packages/opencode/src/config/config.ts`
