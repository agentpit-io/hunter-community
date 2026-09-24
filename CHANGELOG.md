# Changelog

All notable changes to HunterCode · Community Edition follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.2] - 2026-09-25

> 补丁版本,无数据库变更、无新环境变量。
> 升级:`.env` 里的 `HUNTER_VERSION` 改成 `1.2.2`(没写的不用改),然后
> `docker compose pull && docker compose up -d`。桌面启动器用户点「升级」即可。

**上线前实测**:opencode 新镜像在断网状态下启动,`/path` 0.18 秒返回、日志里没有任何依赖安装
(1.2.1 镜像同样条件下 60 秒仍超时);登录中间件新增用例 9 项全过;CI 与 docker compose 端到端通过。


### 🐛 修复 · Fixed

- **新装 / 升级后模型选择器是空的,点了没反应**(2026-09-25 本机重装实测)。opencode 启动时
  给三个配置目录现场 `npm install @opencode-ai/plugin`,插件初始化要等它装完;这三个目录不在
  数据卷里,每次新装或升级都要重装。国内直连 npm 官方源碰上卡死的连接没有总超时,opencode
  的所有接口(连 `/path` 都算)一直挂着,浏览器里看不到任何报错。现在依赖在构建镜像时装好,
  启动时检查到已安装就跳过,不再联网。
- **重装后界面卡在旧登录状态**。数据卷清空而 `JWT_SECRET` 被沿用时,浏览器里的旧 token
  仍能验签,但用户已不在库里,各接口回 404「用户不存在」;前端只认 401 才会重新登录,
  于是合规弹层、偏好引导都不出现,多处功能静默失败。现在登录中间件验签后再确认用户存在,
  不存在回 401,单用户模式下前端会自动换一把新 token,用户无感。
- **选股器**:追加模式下点「VCP 波段收缩」报不认识 `sma50`;点官方示例改为按「追加 / 替换」
  开关合并,不再一律替换。
- **平台数据源缺 key 时静默返回空行情**,现在正确提示去申请 Hunter key(外部贡献 #44)。
- **安全**:去掉 api 里误带的 SaaS 数据库连接串默认值,改为与 compose 一致的本地默认值。
  各部署方式都显式设置 `DATABASE_URL`,不受影响。

### 🔧 其他 · Changed

- Windows 上 `pip install -r requirements.txt` 不再因 uvloop 失败;新增 `requirements-dev.txt`。
- 后端用例、SKILL 检查与策略页自检接入 CI,前端类型检查改为阻塞。
- README:新增桌面启动器下载一节;数据源默认值与港美股来源说明更正。


## [1.2.1] - 2026-09-22

> 补丁版本,只修内置额度的两个接入问题,无数据库变更、无新环境变量。
> 升级:`.env` 里的 `HUNTER_VERSION` 改成 `1.2.1`(没写的不用改),然后
> `docker compose pull && docker compose up -d`。

**上线前实测**(测试服务器上另起一套不建 `.env` 的实例,浏览器端到端):
自带 key(`qwen3.8-max`)+ 已配平台 key → 打开「Hunter key 已配置 · 管理」→ 出现
「一键开启内置额度」→ 点击后约 9 秒自动刷新 → 选择器只剩 **Gemini 3.8 Flash** →
本地存着的旧模型名被自动纠正为 `hunter-llm/hunter-chat` → 发消息正常回答,
网关当日用量同步增加;再次打开弹窗显示「对话也在用内置额度」。全程无 4xx/5xx。


### 🐛 修复 · Fixed

- **侧栏填了 Hunter key,对话却还是自己的模型**(2026-09-22 用户实报)。平台 key 与大模型
  配置本来就是两件事,侧栏「Hunter key 管理」弹窗只存前者 —— 用户以为填了 key 就会用上
  内置 Gemini,实际要再走一遍向导选第一张卡。现在弹窗的已解锁状态下多一张卡片:
  - 自带 key 的实例:「**一键开启内置额度**」。用库里那把平台 key 跑与向导同口径的
    三项检测(连通 / 对话 / 工具调用),通过才写库、写深度分析模型名、热推 opencode,
    然后刷新页面;没通过就**原样保留**当前配置并说清哪一项没过。卡片上如实写明
    「会替换当前的大模型配置」以及怎么切回自带 key。
  - `.env` 锁定的实例:说清网页为什么改不了、`.env` 里改哪三行。
  - 已在用内置额度:一行绿字。
  - 新接口 `POST /api/setup/llm/adopt-builtin`,门禁与向导其它写接口相同,
    key 不经过浏览器。单测 +6 条(`tests/test_setup_builtin.py`)。
- **换模型后浏览器还拿着旧模型名发消息**。热推配置是 mergeDeep,旧模型名会残留在
  `hunter-llm` 的清单里,于是本地存的 `hunter-llm/deepseek-flash` 仍被判为有效 ——
  切到内置额度后每条消息都是 400 `model_not_allowed`,选择器里也同时挂着新旧两个。
  现在 `hunter-llm` 下只认 opencode 当前选定的那一个模型(与占位名同一个根因、同一种修法)。
  走向导切换模型的用户同样受益。

## [1.2.0] - 2026-09-19

> 1.2.0-rc1 已在测试服务器全流程验证过（见 `docs/builtin-llm/P2-成果与测试报告.md`）。
> 正式版相对 rc1 的增量只有下面「对外开放」这一段 —— 内置额度从灰度转为**对外开放**，
> 配套的服务条款、条款链接、以及两道全局闸门的错误卡。发正式版而不是继续挂 rc 的理由：
> 公告已经让所有人按 `docker compose up -d` 起，而 compose 的默认标签就是这里写的那个，
> 让新用户装一个 rc 不合适。

### 🛡️ 对外开放 · Public availability (P3)

- **服务条款与可接受使用政策**（[中文](docs/builtin-llm/服务条款.md) ·
  [English](docs/builtin-llm/terms-of-service.md)）· 中英各一份、十节一一对应。
  写明：仅限自部署用户的研究用途；禁止转售、禁止当通用 API 用、禁止脚本化刷量；
  额度与服务可能调整或下线；我们只记 token 数与模型名不记内容；滥用会停用单个 key；
  **没有 SLA**。第 5.3 节如实写了「你的问题文本确实会经过我们的服务器」——
  只说「不记录」是不够的，不接受「经过」的人应该去走自带 key 或本地模型。
  Terms of Service & AUP, in Chinese and English, section-by-section aligned.
- 条款链接接进用户真会看到的地方：向导第 2 步卡片、第 3 步说明、README 中英的
  「5 分钟跑起来」、`docs/builtin-llm/使用说明.md`。README 里同时写明了额度数字
  （每把 key 每天 30 万 token）与隐私一行。
- **认得出网关的两道全局闸门**：`global_quota_exceeded`（402 · 全平台当日总量熔断）与
  `service_disabled`（403 · 内置额度整体暂停）。两个都**不用 5xx** —— AI SDK 把
  `408/409/429/>=500` 判为可重试，用 503 的话用户看到的是永不停止的转圈、
  那段中文一个字都到不了界面（这正是 rc1 里把 429 改成 402 的同一个坑）。
  不加这两条 code 的话：402 会被说成「模型账户余额不足，请管理员充值」、
  403 会被说成「模型密钥无效，请检查 LLM_API_KEY」——两句都会把用户带到错误的方向。
- `apps/web/scripts/model-error-check.mjs` 的断言从 7 条加到 **10 条**，
  新增的 3 条用的是**从生产网关真抓下来的**报错对象。
- 使用说明补了用户真会遇到的另外两种拒绝（熔断 / 停用），并写明两者都跟个人额度无关，
  工具、数据供给、SKILL、已有会话与数据都不受影响。

### ⚠️ 已知限制 · Known limitation

- **四个一键部署模板（1Panel / Railway / Sealos / Zeabur）仍钉在 `1.1.0`**，
  从它们装出来的实例**没有内置额度那张卡**。它们是各自版本化的独立包
  （如 `deploy/1panel/hunter-community/1.1.0/`），重切一版并在四个平台上各验一遍
  是单独的一件活，本轮没做 —— 没验过就推上去比慢一版更糟。
  想用内置额度请走 `git clone` + `docker compose up -d`（README 的「5 分钟跑起来」），
  或者把模板里的镜像标签手工改成 `1.2.0`。
  The four one-click deploy templates still pin `1.1.0` and therefore ship without
  the built-in-quota card; use the `git clone` path, or bump the tag by hand.

<a id="120-rc1"></a>
### 以下为 1.2.0-rc1（2026-09-19）的内容

### ✨ 新增 · Added
- **内置模型额度**(`hunter-chat` / `hunter-deep`)· **不用自己去各家申请大模型 key 了**。
  向导第 2 步第一张卡「使用 HunterCode 内置额度(推荐)」选中即自动填好网关地址与模型名,
  只要一把免费的 `hunt_tools_` 平台 key —— 那把 key 本来就要申请(它管工具、SKILL 与数据源),
  现在**同一把 key 也管大模型额度**,不需要第二把。每天有免费 token 额度,
  用完会在对话里用中文说清什么时候重置、怎么改用自带 key。
  隐私:网关**只记 token 数与模型名,不记任何 prompt 与回复内容**。
  用法与隐私声明见 [`docs/builtin-llm/使用说明.md`](docs/builtin-llm/使用说明.md)。
  A built-in model quota: pick the first card in the wizard and paste one free platform key —
  no LLM account of your own. The gateway records token counts and model names only, never content.
- **设置页 → 大模型 → 今日额度**:剩余 / 上限 / 已用 / 进度条 / 重置时间 / 计量口径。
  数字直接来自网关(新接口 `GET /api/setup/llm/quota`),**前端不换算、不补默认值**;
  取不到就显示「—」并写明原因。
- **内置额度路径下向导替你做完两件事**:把深度分析相关的模型变量指向 `hunter-deep`;
  用同一把 key 解锁数据供给(第 4 步直接显示「已解锁」,不用再填一遍)。
- `apps/web/scripts/model-error-check.mjs` · 「模型调用失败时用户看到什么」的回归检查,
  用**真实抓到的**报错对象跑 7 条断言,已接进 CI。

### 🐛 修复 · Fixed
- **额度用完时页面上是一个永不停止的转圈**。网关原来返 429,而 OpenAI 兼容客户端
  (opencode 用的 AI SDK)把 429 当「等会儿再来」,按 `Retry-After` **无限重试** ——
  实测 17 分钟仍在等,那段写清「今天用完了、几点重置、现在能怎么办」的中文引导一个字都没露出来。
  网关改成返 **402**(`type` 仍是 `insufficient_quota`),前端 1 秒出卡片并**直接显示上游那段中文**,
  不再套「模型请求太频繁,稍等一会儿再重发」那种方向相反的模板。
- **深度分析的模型变量在没有 `.env` 时是空串,不是默认值**。`ASSISTANT_MODEL_* /
  AGENT_SUB_* / AGENT_MODEL_* / SIGNAL_ANALYSIS_MODEL` 在 compose 里写成 `${X:-}`,
  注进容器的是空字符串,于是 `os.getenv(名, 默认值)` 拿到的是 `""` —— 请求打到上游时
  `model` 是空的,表现是「工具调用成功、之后的 LLM 汇总整个失败,只能用模板兜底」。
  **所有没写 `.env` 的部署都中招**,不只是内置额度。现在统一走
  `runtime_config.agent_model()`:**环境变量非空 → 数据库(向导写入)→ 代码默认值**;
  调用点全部改成惰性读取(模块级常量在向导热生效之后不会更新)。
- **`LLM_SCHEMA_SANITIZE` 原来只给了 opencode,api 看不到**。`.env` 里写 `=0` 的锁定实例上,
  opencode 按 0 直连而 api 退回数据库里的旧值,设置页显示的开关与实际走法不一致。
- 设置页:额度重置时间原来按**浏览器所在时区**渲染(显示「今天 16:00」,旁边却标着
  Asia/Shanghai);`Row` 的标签列 90px 装不下 `LLM_DEFAULT_MODEL`,取值叠在标签上面。

### 🔧 变更 · Changed
- `.env.example` 的 `LLM_BASE_URL` / `LLM_DEFAULT_MODEL` 由预填 OpenAI 地址改为**留空**。
  `env_locked()` 的语义是「任何一项非空就锁」,预填会让 `cp .env.example .env` 的人
  反而进不了向导、只看到「这台实例的大模型配置已锁定」。
- 预设卡片:自带 key 的四张加「高级」标签,DeepSeek 那张的「推荐」改成「自带 key 首推」——
  一屏里只留一张卡说「推荐」。
- README ×2 的「5 分钟跑起来」、`docs/01-getting-started.md` 把内置额度写成推荐路径、
  自带 key 为高级路径。

## [1.1.0] - 2026-09-18

### ✨ 新增 · Added
- **浏览器里的首启向导**(`/setup`)· 五步配完就能对话,**全程不用改任何文件**:
  环境自检 → 选大模型 → 填 key 当场测 → 数据供给 → 完成。
  第 3 步由 api 容器(和 opencode 实际调用走同一条网络路径)依次做**连通 / 对话 / 工具调用**
  三项检测并显示各自真实耗时,**测不通不让保存**;schema 清洗开关由检测结果自动决定,不用自己猜。
  最后一步**不重启任何容器**热生效。设置页 →「大模型」→「重新运行初始化向导」可随时换模型。
  A first-run wizard in the browser: env self-check → pick a model → paste the key and test it
  on the spot → data supply → done, applied live without restarting anything.
- **`HUNTER_SETUP_TOKEN`** · 公网实例的初始化口令。**设了就一律要**(不管来源看起来是不是本机);
  没设且来源判为公网时**拒绝进入向导**并说明怎么做。连错 5 次锁 15 分钟,
  通过后签发 30 分钟的初始化会话。
  来源判断:优先看反代覆盖写的 `X-Real-IP`,退而取 `X-Forwarded-For` **最右边**那一项
  (`$proxy_add_x_forwarded_for` 是追加写,最左边是客户端自己带的)。有反代时这个判断可信;
  裸 compose 没有反代时仍可被伪造 —— 所以暴露在公网就必须设口令,向导第 1 步会对此告警。
- **`data/llm-presets.json`** · 四个预设(DeepSeek v4 pro / Qwen 3.8 Max / Claude Sonnet 5 /
  Gemini 3.5 Flash)+ 自定义。卡片上的工具调用命中率与耗时**全部抄自 `docs/model-testing/`**
  的实测结果并标注实测日期,对不上的字段留空。
- **`git clone` 之后不用改任何文件就能起来**。`docker compose up -d` 直接拉预构建镜像跑,
  `JWT_SECRET` 留空会在首次启动自动生成并写进 `hunter_secrets` 卷(opencode 与 web 只读挂同一个卷读回同一把)。
  从零到六个服务健康需要编辑的文件从 4 处变成 **0 处**;大模型也由上面的向导在浏览器里配完。
  A fresh clone now boots with `docker compose up -d` — no file edits, secrets are generated on first start.
- **四个自家服务全部改成自包含的预构建镜像**(`api` / `web` / `opencode` / `llm-shim`),
  `linux/amd64` + `linux/arm64` 双架构。原来 compose 里 16 处挂载仓库文件的地方一处都不剩 ——
  那些文件(SKILL、静态数据、迁移 SQL、MCP 脚本、插件)现在都打进镜像,云平台上没有仓库目录也能跑。
- **数据库迁移改由 api 启动时执行**,带 `schema_migrations` 账本与 advisory lock(多副本安全)。
  原来挂给 postgres 的 `docker-entrypoint-initdb.d` **只在数据卷第一次创建时执行**,
  所以老部署一直缺表缺列。升级后第一次启动会把没跑过的迁移补齐,日志里逐个列出来。
- **大模型配置可以存数据库并热生效**,不重启容器(实测端到端 9.2 秒,MCP 全部重连)。
  key 用 AES-256-GCM 加密后入库,接口只回显末 4 位,日志一个字不打。
- **`scripts/migrate-volumes.sh`** · 升级用:把 `user-skills/` 与 `data-packages/` 搬进新的具名卷。
- **`docker-compose.dev.yml`** · 开发者用:带回本地构建与全部源码挂载。
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`

- **五个平台的部署方案**(`deploy/` 与 `docs/deploy/`):Zeabur 模板、Sealos 模板(K8s)、
  1Panel 应用包、Railway 手工搭建清单、Coolify / Dokploy 两份可直接粘贴的 compose。
  ⚠️ **都还没在真实平台上跑过、也都没上架**(我们没有这些平台的账号),所以本版
  **不放任何部署按钮**,只给文档;每篇文档都写明了哪些验过、哪些没验过。
  Deployment recipes for five platforms — verified by equivalence, **not yet on the real
  platforms and not listed anywhere**, so no deploy buttons in this release.
- **`deploy/tools/template-to-compose.py`** · 把各平台模板**机械翻译**成等价 compose
  (同镜像、同环境变量、**用平台自己的方式生成的随机密钥**、同卷、同依赖、同 init 规则),
  在本机从空卷跑完整流程。没有平台账号也能验模板本身 —— 人不能在中间改。
- **`deploy/tools/validate-templates.py`** · 58 项静态校验:Zeabur 官方 JSON Schema、
  Sealos 的 K8s 资源过 `kubeconform -strict`、1Panel 过官方 `validate_app_package.py`,
  外加这一轮真踩到过的规则(PGDATA 必须是挂载点的子目录、非 root 镜像挂卷必须
  `RAILWAY_RUN_UID=0`、api 的卷不能遮住镜像自带的静态数据目录……)。
- **`HUNTER_BIND_HOST`** · 监听地址可配,默认 `0.0.0.0` 不变。Railway 2025-10-16 之前
  创建的环境私有网络是 IPv6-only,那里设 `::`。

### 🐛 修复 · Fixed
- **`db/migrations/` 里有 7 个迁移文件一直在静默失败**。它们 ALTER 的目标表(`stocks` / `klines` /
  `backtest_result`)是 api 启动时 `init_db()` 建的,而 postgres 的 initdb 在 api 第一次启动**之前**就跑,
  那时表还不存在;initdb 的 psql 默认不带 `ON_ERROR_STOP`,失败被静默吞掉。
  现在迁移分两阶段:先 `init_db()` 建基础表,再跑增量迁移。
- **手工补跑过迁移的部署升级后 api 起不来**。`0010_daily_close_view.sql` 与 `0014` 定义同一个视图、
  0014 多一列,而 `CREATE OR REPLACE VIEW` 不许减列 —— 账本为空的库会从 0001 重跑,正好撞上
  `cannot drop columns from view`。0010 开头补了 `DROP VIEW IF EXISTS`。
- **开源部署的数据可能被发到项目方的网关**。18 处把 `LLM_BASE_URL` / `ONE_API_BASE_URL` 的默认值
  写成了项目演示站的网关地址,用户没配地址时请求会发到那里。现在未配置就是未配置,如实报错。
  Removed 18 hardcoded fallbacks that pointed at the project's own LLM gateway.
- **保存 SKILL 要等 30 秒然后提示「请重启 opencode」**,而文件其实早就写好了。
  api 用同步 HTTP 调 opencode,而 opencode 会回头来拉 api 的清单,单 worker 的事件循环被自己堵死。
  现在走线程池,**30.07 秒 → 0.164 秒**。
- **模型名写错却被报成「key 无效」**。不少网关(演示站那台 OneAPI 实测)对写错的模型名回
  HTTP **403** 并附一句「该令牌无权使用模型:xxx」,先按状态码判就会让用户拿着一把好 key 去重新申请。
  现在先看报错里提没提模型名,提了就按模型名报,并列出这个地址上可用的模型。
- **全新安装无法校验平台 key**。v1.1.0 把 `HUNTER_UPSTREAM_URL` 的兜底改成空之后,
  `manifest()` 拼出来的地址没有协议头,httpx 直接抛错,界面显示「连不上 Hunter 服务器,检查网络后重试」——
  原因说反了。现在:**没有 key 就一个请求都不发**(独立运行模式下不该因为打开一个页面就去连官方);
  用户主动粘一把 `hunt_tools_` key 时,没配上游就问官方平台(那把 key 本来就只能从那里申请)。
  数据请求那条路不受影响,独立模式下仍然不指回官方。
- **llm-shim 缺 `LLM_BASE_URL` 时不再拒绝启动**;上游地址加了白名单校验(拒绝内部服务名、回环、
  内网网段与 `169.254.169.254` 云元数据地址),防止它被当成访问内网的跳板。
- **未配置大模型时对话会一直转圈**(实测 100 秒以上没有返回)。现在 llm-shim 立刻返回
  OpenAI 兼容的中文错误体,流式请求返回合法的 SSE 错误帧。
- **配了宿主机代理,对话仍一直超时**:大模型请求由 llm-shim 容器发出,但 `HTTP_PROXY_UPSTREAM` / `HTTPS_PROXY_UPSTREAM`
  原来只传给了 api 容器。宿主机开 TUN 代理,或网关按 TLS 指纹拦截容器直连(aihubmix 实测报 `SSL: UNEXPECTED_EOF`)时,
  shim 连不上上游,前端表现为对话一直转圈。现在 llm-shim 与 api 共用这组变量,留空时行为不变。
  The LLM proxy variables are now passed to the llm-shim container as well, which is where model requests are sent from.

- **云部署时向导五步全绿、发消息却永远没有回复,日志里一条报错都没有**。api 也需要知道
  llm-shim 在哪(向导保存后由 api 把 provider 的 `baseURL` 推给 opencode,推的是
  **api 容器里**的 `LLM_SHIM_URL`),不设就回落到硬编码的 `http://llm-shim:3999/v1` ——
  服务名不叫 `llm-shim` 的部署全中。
- **重新部署之后,向导里配好的模型又变回「尚未配置」**。opencode 启动时向 api 要配置
  只重试 3 次、总共等 3 秒,而云平台大多不编排启动顺序(六个服务同时起),api 要跑完
  数据库迁移才监听。改成按预算退避重试(`HUNTER_CONFIG_WAIT`,默认 90 秒),
  且**只对连不上重试** —— api 一回话就立刻按它说的办,全新安装一秒都不多等。
- **合规声明弹窗盖住首启向导**。弹窗本来就排除了 `/setup`,但路径是在**挂载时**判的;
  全新用户落在 `/`、由首页在客户端跳到 `/setup`,2.5 秒后回调才跑完 —— 那时人已经在
  向导里了,而它是全屏遮罩,把「下一步」整个挡住。改成落地时再判一次。
- **向导第 4 步「免费开源数据源」承诺了它做不到的事**:那一项不写任何配置,而后端
  未配置时默认走 hunter 网关(有意为之:宁可如实报「未配置 Hunter Key」,也不悄悄
  回落到容器里经常连不通的 AKShare),于是用户选完第一条对话就顶出红色的
  「无法拉取 行情」。改成如实说明现在能用什么、不能用什么。
- **首次启动生成的密钥被说成「还没落进 hunter_secrets 卷」**,其实紧接着就写回去了。
- **冷启后头几十秒,向导第 1 步必现一条黄色 `ReadTimeout`**(opencode 还在加载插件,
  扫一遍 SKILL 要十几秒;热起来只要 16~80 毫秒)。超时放宽到 10 秒,文案也改成人话。
- **`boot.sh` 在云部署上误报「密钥重启后会变」**:密钥来自环境变量时这句一个字都不成立。
- **向导第 1 步把环境变量来的密钥报成「首启自动生成(hunter_secrets 卷)」**:判据恒为真。
- `scripts/migrate-volumes.sh`:支持 `--project`(用 `docker compose -p` 起的栈本来
  认不出项目名,会报「卷还不存在」把人带偏);卷找不到时列出疑似卷名;收尾提示里那条
  直接 `curl` opencode `/skill/refresh` 的命令在开了 `OPENCODE_PASS` 的部署上是 401、
  还被 `curl -s` 吞掉,改成重启 api 与 opencode。

### 🔧 变更 · Changed
- `docker-compose.yml` **默认只用预构建镜像**,不再有 `build:` 段。版本由 `HUNTER_VERSION` 控制
  (默认 `1.1.0-rc1`),镜像源由 `HUNTER_REGISTRY` 控制。要本地构建请叠加 `docker-compose.dev.yml`。
- `JWT_SECRET` 不再是必填项(原来缺了直接拒绝启动)。**已经填了的不要动** ——
  它派生了加密已存 key 的 AES 密钥,换掉会让所有已保存的 key 解不开、登录全部失效。
- 用户 SKILL 不再靠 api 与 opencode 共享目录,改由 opencode 按 URL 向 api 拉取
  (云平台上两个服务通常不能共用一个卷)。
- `user-skills/` 与 `data-packages/` 从 bind mount 改为 api 自己的具名卷。
  **老用户升级必须跑一次 `scripts/migrate-volumes.sh`**,否则装过的 SKILL 会从界面上消失
  (文件没丢,只是容器看不到了)。

### ⚠️ 升级注意 · Upgrade notes

```bash
git pull
docker compose pull && docker compose up -d
bash scripts/migrate-volumes.sh     # 装过 SKILL / 导入过数据包的老用户必须跑
                                    # 用 `docker compose -p <名>` 起的栈:加 --project <名>
```

`JWT_SECRET` 已经填在 `.env` 里的**不要动** —— 它派生了加密已存 key 的 AES 密钥。

## [1.0.1] - 2026-09-17

对话引擎镜像从 **7.56 GB 瘦到 618 MB**,首次要下载的量从 1.70 GB 降到 153 MB
(实测冷拉:美国节点 123 秒 → 6 秒,新加坡节点 9 秒;国内没有测试机,未测)。
磁盘要求从 20 GB 降到 10 GB。首次启动的耗时大头也随之从「下镜像」变成了「本地构建 api 与 web」。
The chat-engine image went from **7.56 GB to 618 MB** — a 1.70 GB download became 153 MB
(cold pull measured at 123 s → 6 s from US-Central, 9 s from Singapore; mainland China not measured).

### ✨ 新增 · Added
- **对话引擎镜像改为单文件二进制**。旧镜像是「整个 opencode monorepo `bun install` 之后原样拷进运行层,再 `bun run` 源码」,
  2.5 GB node_modules + 130 MB 源码,末尾一句 `chown -R` 又把这 2.78 GB 复制成第二层。
  现在编译阶段 `bun --compile` 出单文件,运行层只有二进制 + 6 个插件 + 5 个 MCP 脚本 + 配置。
  没有换成 opencode 官方预编译二进制 —— `POST /skill/refresh` 是我们 fork 自己加的路由,
  官方版没有,换过去会让「UI 里存了 SKILL、对话里却没有这个能力」且不报任何错。
- **对话引擎镜像支持 arm64**(Apple Silicon / AWS Graviton 自部署)。
  `linux/amd64` 与 `linux/arm64` 同一标签下发布。api / web 镜像仍只有 amd64。
- **每日部署冒烟工作流** `.github/workflows/e2e-compose.yml`:干净环境起全栈 → 等 6 个服务健康 →
  查 `/api/health` 与 MCP 连接状态 → 配了仓库密钥 `SMOKE_LLM_API_KEY` 时再真发一条消息。
  定时 + PR + 手动都能触发。以前 CI 只做「import 能过 / 前端能 build」,
  而这个项目最常见的坏法是**服务起不来**,单仓语法检查一个都看不出来。
- **`OPENCODE_REGISTRY`**:换镜像源(自建 registry / 私有镜像站)不用改 `docker-compose.yml`。
  Docker Hub / 阿里云 ACR 的官方分发仍在规划中。
- **`HUNTER_MCP_TIMEOUT_MS` / `UZI_HTTP_TIMEOUT`**:MCP 工具超时可调。
  换了更慢的后端时工具会被掐断,而症状是模型回「服务不可用」、日志里看不到任何超时字样。

### 🔧 变更 · Changed
- **`OPENCODE_TAG` 默认值从 `latest` 变成具体版本 `1.18.12-slim.1`**。
  浮动标签意味着某天 `docker compose pull` 会无声换掉运行方式,出问题连「什么时候变的」都查不出来。
  升级须知见下。
- **api 镜像改多阶段** · 1.32 GB → 909 MB(−31%)。编译工具链(build-essential / libpq-dev)
  只留在 builder 阶段,运行层保留 tesseract 中英文 OCR 与 curl。新增 `apps/api/.dockerignore`。
- **opencode 容器入口脚本从 40 多行有效命令降到 7 行**。原来启动时要现补装 `mcp<2`、
  用两处 `sed` 改超时 —— 三件事都在镜像源头修好了。那三段 sed 依赖「文件可写」且「字符串恰好匹配」,
  任何一边变了就静默失效,而失效的症状是「深度分析说服务不可用」,根本指不到入口脚本。
- **入口脚本同时兼容新旧镜像**:检测到 `opencode` 二进制就用它,否则回落旧的源码启动方式。
  反过来,新镜像里放了一个 `bun` 垫片,让老用户没更新的旧脚本也能把容器拉起来。
  两种组合都在演示站实测过。
- 镜像内 4 个 MCP 的 `timeout` 统一 30000 → 180000 ms(深度分析 60–300s、组合建议 45s+ 本来就会被 30s 掐断)。

### 🐛 修复 · Fixed
- **升级后「暂无对话」**(本次瘦身过程中发现并修掉,未流出到任何发布版本)。
  opencode 的会话库文件名跟 `InstallationChannel` 走:跑源码时叫 `opencode-local.db`,
  编译版会默认去开 `opencode.db`。卷、权限、路径全对,但打开的是一个空库。
  镜像里钉死 `OPENCODE_DB=opencode-local.db`,新旧镜像读同一个库,升级和回滚都不丢会话。
- **流式回复首帧被扣住**:模型中转服务(llm-shim)转发 SSE 时用 `read(4096)`,要等凑满 4 KB 或上游结束才转发,
  导致回复开头几个字迟迟不出、最后一次性吐出。改用 `read1(4096)`,有数据就立即转发;
  think 标签过滤、跨块拼行、`[DONE]` 最后发送的逻辑不变。新增标准库回归测试并接入 CI。
  Streamed replies were held back until a 4 KB buffer filled; the shim now forwards data as soon as it arrives.
  ([#1](https://github.com/agentpit-io/hunter-community/pull/1))

### ⬆️ 升级须知 · Upgrading

```bash
git pull
docker compose pull opencode
docker compose up -d opencode api
```

- `.env` 里没写 `OPENCODE_TAG` 的,`git pull` 之后自动拿到 `1.18.12-slim.1`,不用动。
- **`.env` 里写着 `OPENCODE_TAG=latest` 或 `dev` 的照样能跑**(新镜像带 `bun` 兼容垫片),
  但建议改成 `OPENCODE_TAG=1.18.12-slim.1`,免得以后被浮动标签换掉运行方式。
- 会话数据不受影响。升级前后 `docker exec <api 容器> curl -s http://opencode:3901/session` 的条数应当一致;
  对不上**先别删卷**,`/home/hunter/.local/share/opencode/` 下看看是不是多了一个空的 `opencode.db`。
- 旧镜像先别删,确认新版本正常之后再 `docker image rm ghcr.io/agentpit-io/hunter-opencode:dev`。

### 📖 文档 · Docs
- 新增 `docs/image-slim/`:基线测量、决策记录、演示站端到端测试报告、两地拉取耗时。
- README / README_EN / `docs/01-getting-started.md` 的磁盘要求与首拉耗时改成实测值
  (153 MB · 美国节点 6 秒 / 新加坡节点 9 秒;国内没有测试机,未测)。
- 排错表新增两条:升级后「暂无对话」怎么自查;`docker compose pull` 报 `denied` 时
  先 `docker logout ghcr.io` —— 镜像是公开的,报 denied 恰恰是因为多带了一份过期凭据,
  而 docker 被拒之后不会退回匿名。

### 🙏 贡献者 · Contributors
- [@forever-ivy](https://github.com/forever-ivy) — 流式回复首帧修复 · streaming first-frame fix ([#1](https://github.com/agentpit-io/hunter-community/pull/1))

## [1.0.0] - 2026-09-13

首个正式版。汇总 1.0.0-rc1 之后到 2026-09-13 的改动(约 220 个提交)。
注:rc1 里列的「剩余工作」(scheduler 连续 3 个交易日全绿、pred_backtest 30+ 样本、Grafana 看板)本版不作为完成项声明。

### ✨ 新增 · Added
- **全市场扫描筛选器**(魔法筛选器,已升为一级页面)
  - thinkScript 子集脚本 + 可视条件行(中文),支持自然语言生成脚本,本地关键词识别优先、AI 按需调用
  - 可用字段中文名、同族折叠;保存扫描策略;系统示例可隐藏
  - RS 相对强度评级(IBD 口径,移植自 IBD-RS-Rating · MIT)与 RS 线上涨天数,新建全市场日线管线
  - VCP 字段(收缩次数 / 每次深度 / 量能递减)与 VCP 五条规则可直接筛选
  - 时间回溯:按过去某一天收盘的日线跑同一套条件
  - 结果悬停弹出近一年日K(缩放 + 十字星),并标出脚本过去一年的命中日
  - 脚本编译失败时提供「AI 修脚本」(只给替换指令)
- **小鹿智能体 / 研究台**(策略中心)
  - 自迭代量化原型接入真实数据:VCP 波段纸上交易 + 观察列表
  - 多方向并行 + 防过拟合规则优化器;评分定仓位(S/A/B/C/D)与组合风险上限
  - 研究台:研究线分层、唐奇安突破线、「突破买入」研究线、回测关与 30 笔判定
  - 历史交易记录卡片(代号、手续费、扣费后净损益、悬停日K)与 CSV 导出
- **量化**
  - 因子参数可自定义(约 20 项),新增 7 个纯日线因子(量比 / 52 周高点 / 换手率 / Amihud / 小市值 / 贝塔 / 偏度)
  - 回测、因子、每日流水线按市场隔离;数据页可下载全美股日线;策略工作台可回测美股
  - 回测结果在策略工作台就地展示;交易成本加买入均价、净盈亏与保本价
- **能力库与 SKILL**
  - 分组自由改名、能力可迁移分组;装不全的 SKILL 明确提示
  - 导入 SKILL 时一并安装引用到的附属文档,子目录型 SKILL 整树搬运
  - SKILL 说明自动译为中文;「说明」与「点它会问」可就地编辑
  - investor_panel 补齐 references
- **MCP**:新增 kronos / truesource 两个 MCP,akshare MCP 迁到 mcp 2.x,三个已发布到 PyPI 与官方 Registry
- **对话**:生成中发送键变为停止键

### 🔧 变更 · Changed
- 深度分析报告结构改由 SKILL 决定;按市场分派数据源
- 筛选器与页面不再点名上游数据源
- kronos MCP 覆盖范围改为「仅 A 股」(0.1.2)

### 🐛 修复 · Fixed
- 对话:回答整段英文(语言守卫)、流式回答末尾被吞字、换会话残留报错与输入内容、一条消息多出空会话、按 SKILL 回答越来越简单
- opencode:会话数据与审计日志落具名卷,修 recreate 后对话消失与重启循环
- 深度分析:拉数总预算 + LLM 移出事件循环,失败态不再假计时;美股/港股前几节无数据
- 量化:冲击成本接入净值并修正冲击系数量纲;成交量单位按板块归一;工作台回测指标恒显示「—」;连亏停机永久不开仓
- 存证自动发放被短路;策略中心登录态续期
- 产品经理多轮反馈:换策略结果不变、美股误用 A 股数据源、思考过程外泄、净值区间与持仓假数据等

### 🛡️ 合规 · Compliance
- 撤回同花顺、通达信 MCP 的预置与未文档化技术细节,厂商数据源待正式合作后再上

### 📝 文档 · Docs
- 官方 MCP 实测对照(中英)、多市场取数与语言守卫等开发铁律写入 CLAUDE.md

---

## [1.0.0-rc1] - 2026-09-01

### 复赛 4 类评委优化(A/B/C/D 全部就绪)

#### ✨ 新增 · Added
- **阶段 1** klines Daily ETL cron 挂载(6adcbd8)
  - 每交易日 15:30 CST A股 · 17:00 港股 · 03:30 CST 次日 美股
  - POST /api/admin/etl/run-market 手动触发端点
  - GET /api/admin/etl/health 数据新鲜度暴露
- **阶段 2** Kronos provider 生产切 kronos_saas(f633b2a)
  - 修 upstream API 字段 code → symbol 422 bug
  - 生产 .env FORECAST_PROVIDER=noop → kronos_saas
  - kronos.agentpit.io gateway 端到端通
- **阶段 3** scheduler 真跑 · daily_pipeline 端到端(小王 + 我 · 昨晚打通最后堵点)
  - snapshot_job / backtest_job / consistency_job 三步已存在
  - daily_close view v2 加 adv_20d(98da971 + 21cb47f)
  - CSI300 300 只 seed + backtest_config 27 字段(0efd7ed)
- **阶段 4** 逐笔成本 + sqrt_impact 冲击模型(21cb47f + 9312a16)
  - backtest_trade 表 · 每笔含 commission/stamp_tax/slippage/other/adv_20d/impact_bps_actual
  - broker preset(cn/hk/us/zero)· bp_static / sqrt_impact 双档滑点
  - GET /api/quant/backtest/{id}/trades · 前 200 笔 JSON
  - GET /api/quant/backtest/{id}/trades.csv · 全量 UTF-8 BOM CSV
  - 前端 backtest.html 加逐笔明细面板 + 导出按钮
- **阶段 5** 分享页 /p/{token}(小王 ee4f8d3)
  - SSR + 演示数据黄条 + 事后 outcome 展示 + 免责声明
- **阶段 6** 合规硬约束 · 灰度开关(小王 + 我 9815033)
  - orchestrator SYSTEM_PROMPT 5 条合规硬约束
  - compliance_guard 三档(strict/permissive/off)
  - compliance_violation_log 表 · fire-and-forget 落库
  - 报告水印扩到 5 页

### 🔧 变更 · Changed
- `providers/forecast/kronos_http.py` request body 加 symbol 字段(向后兼容 code)
- `backtest_result` 加 trading_cost / gross_metrics / slippage_model 3 列(持久化 · 修缓存命中丢失)
- `daily_close` view v2 · 加 adv_20d(20 日均成交额)

### 🐛 修复 · Fixed
- Kronos SaaS gateway 422 错误(f633b2a)
- 缓存命中时 trading_cost 丢失

### 📝 文档 · Docs
- doc/开源hunter-community/04开源比赛/ 一批新方案 + 接手 + 交接文档
- doc/01远程服务器编程/README-hunter-community.md fin-r1 手册

### 🏗️ 数据库变更 · Migrations
- 0009 compliance_violation_log
- 0010 daily_close view v1
- 0011 backtest_config
- 0012 company_master
- 0013 backtest_trade + backtest_result 加 3 列
- 0014 daily_close v2(加 adv_20d)

### 🎯 剩余工作(v1.0.0 正式发)
- 观察连续 3 个交易日 scheduler 全绿
- pred_backtest 积到 30+ 样本 · 校准 tab 有真数据
- Grafana dashboard 关键指标
- 稳定观察 3 天 → tag v1.0.0

---

## [0.1.0-alpha] - 2026-08-10

First public preview cutting five compressed sprints into `main`.

### Added
- **P1** · Monorepo (`apps/{api,web}` · `db/migrations` · `docs`),
  Dockerfiles, `docker-compose.yml` (postgres 16 · redis 7 · api · web),
  `HUNTER_MINIMAL_BOOT` boot flag
- **P2** · SaaS strip (WeChat / Lark / booth / SSO removed · -17k LOC)
- **P3** · Local auth (`argon2id` password · JWT HS256 · rotating refresh
  token · first user auto-admin · `REGISTRATION_MODE=open|invite|closed`)
- **P4** · Pluggable provider layer:
  - `providers/data_source/{saas,akshare,yfinance}`
  - `providers/llm/{openai_compat,anthropic}`
  - `providers/forecast/{noop,kronos_http}`
  - `/settings` page with per-user SaaS accelerator configuration
  - `apps/api/app/utils/crypto.py` AES-256-GCM at-rest encryption
- **P5** · GitHub Actions: `ci.yml` (gitleaks + api compile + web build)
  · `docker-publish.yml` (GHCR api+web images) · `release.yml`
  (CHANGELOG-driven release notes)

### Not yet
- Push channel refactor (SMTP · Slack) · `HUNTER_MINIMAL_BOOT` removal
- hunter-opencode GHCR image · shared `JWT_SECRET` plugin
- Password reset flow · rate limit · settings account tab
- Full `docs/01-13` coverage (only 01-02 shipped)

## [0.2.0] - 2026-08-11

### Added
- **`opencode` chat engine now runs** via `ghcr.io/agentpit-io/hunter-opencode:latest`
  · docker-compose service uncommented · 5 hunter plugins loaded
  (hunter-auth · hunter-audit · hunter-guard · hunter-budget · hunter-mcp-context)
  · 4 MCP servers registered (watchlist · portfolio · uzi · hunter_user).
- **Companion image build** in huntercode private repo:
  `packages/hunter-server/Dockerfile` (bun 1.3.14-alpine + python3 + `mcp` +
  `httpx`) · `.github/workflows/hunter-community-publish.yml` publishes on
  push to dev · multi-tag GHCR (branch, sha, tag).
- **nginx `/api/opencode/` location** proxies to `:3921` (opencode host port)
  with 600s SSE-friendly timeout · Bearer JWT passed through.

### Changed
- **opencode basic auth OFF by default** · `OPENCODE_USER/PASS` defaults are
  empty in `docker-compose.yml`. hunter-auth plugin (JWT via shared
  `JWT_SECRET`) is the sole gate. Setting the vars re-enables basic auth
  but requires additional nginx work.
- `.env.example` documents `HUNTER_INTERNAL_KEY` (shared secret for MCP →
  api container callbacks), `OPENCODE_TAG`, `HUNTER_BUDGET_ENABLED`.

### Fixed (during Session B / opencode enablement)
- `packages/hunter-server/Dockerfile` broadened `COPY . .` (needs
  patches/ turbo.json for `bun install --frozen-lockfile`) + added
  python3+make+g++ to deps stage (postinstall node-gyp compile).

### Verified on fin-r1
- `GET /api/opencode/agent` → 200 · 19.5KB (9 agents including build/plan/
  explore/summary/triage/duplicate-pr)
- `GET /api/opencode/session` → 200 · `[]`
- `GET /api/opencode/config/providers` → 200 · 6KB provider list

### GHCR
- `ghcr.io/agentpit-io/hunter-opencode:dev` published (visibility: private
  by default · needs manual UI flip to public per doc 08 for anon pull)
- Alternative: `docker login ghcr.io` with a PAT to pull private image

## [0.1.3] - 2026-08-11

### Added
- **`<AuthGuard>` global 401 interceptor** (`apps/web/app/components/AuthGuard.tsx`)
  · monkey-patches `window.fetch` at layout mount · on `/api/*` 401 with
  `needLogin:true` or `INVALID_TOKEN`/`UNAUTHORIZED` error, wipes tokens
  from `localStorage` and redirects to `/login?return_to=<original>`.
  Fixes the infinite "初始化 session 失败" console spam when JWT expired
  or DB volume was wiped. 30+ existing fetch callsites need no change.
- `login/page.tsx` honors `?return_to=` so re-auth lands where you were.

### Changed
- fin-r1 demo instance postgres password rotated from default `hunter/hunter`
  to a random 28-char string (in fin-r1 `.env`, not in git). Applied via
  `ALTER USER hunter WITH PASSWORD '...'` inside the running container so
  no data was lost.

### Deferred to v0.2.0 (documented in `doc 13 · opencode-enablement.md`)
- hunter-opencode GHCR image + docker-compose enable · chat features
- SaaS data key wiring · needs `hunter.agentpit.io/dev/api-keys` first
- LLM provider wiring for subagents / online_analysis / agents/graph
- GM data source refactor to yfinance
- SMTP/Slack push channels

## [0.1.2] - 2026-08-11

### Security
- **Scrub `FinAPI@2026!` token leak** · previously hardcoded as a `os.getenv`
  fallback default in `finance_data_client.py`, `online_analysis/unified_fetcher.py`,
  `agents/sentinel/unified_fetcher.py`, `factor_engine.py`. All 4 defaults
  now empty · users must provide `FINANCE_DATA_TOKEN` explicitly.
  Trufflehog didn't catch this because it's a plain word (no entropy).
- New CI guardrail: `os.getenv` fallback values matching a shared-secret
  pattern (6+ alphanumerics not on the whitelist) fail the build.

### Added
- **Provider fallback in `finance_data_client.get_quote()`** · when
  `FINANCE_DATA_URL` is empty (the OSS default) it now delegates to
  `providers.data_source.get_data_source().get_quote()` via an async→sync
  bridge · users can pick `akshare` (A-shares, China network) or
  `yfinance` (US/HK/A, non-China network) with a single env var.
- `yfinance==0.2.51` added to `requirements.txt` (was missing despite
  the provider impl existing).
- Quote `/api/quote/{code}` cache-miss branch actively fetches via
  `fd_get_quote` before returning "数据未就绪" placeholder · fills cache.

### Fixed
- `providers/data_source/yfinance_impl.py::get_quote()` rewritten to use
  `Ticker.history(period="5d")` instead of `fast_info` · the latter throws
  `KeyError: 'currentTradingPeriod'` on newer yfinance when market is closed.
- Shape adapter in `finance_data_client.get_quote` returns `None` when the
  provider yields null price · UI now correctly shows "数据未就绪" instead of
  misleading `price: 0.0`.

### Known limitation
- The demo instance at `https://hunter-community.agentpit.io` shows null
  prices for A-shares (akshare backend blocked from GCP Singapore) and US
  stocks (Yahoo Finance rate-limits GCP IPs with HTTP 429). Users on other
  networks or with a `HUNTER_SAAS_DATA_URL/KEY` are unaffected.

## [0.1.1] - 2026-08-11

Post-release patch closing the P0 items from
`doc/codex/开源整合方案/10-v0.1.0-alpha-测试报告.md`.

### Fixed
- **Business tables now always built** · `init_db()` runs unconditionally in
  lifespan; `HUNTER_MINIMAL_BOOT=1` only gates the background schedulers
  (collector · signal_monitor · gm_alerts · backtest · stocks_catalog seed).
  Fixes 500 on `/api/watchlist` `/api/alerts/list` `/api/user_mcp`
  `/api/portfolio/summary` after fresh volume.
- **Redis env respected** · `apps/api/app/routers/{quote,portfolio}.py` +
  `services/collector.py` switched from hardcoded `redis://localhost:6379`
  to `os.getenv("REDIS_URL", ...)`. Fixes `redis.ConnectionError` in docker.
- **Multi-tenant migration folded into `init_db`** · `stocks` /
  `position_thesis` / `push_tasks` now always have `user_id` column and the
  composite primary keys. Fixes `UndefinedColumn: column "user_id" does not
  exist` on `/api/watchlist` and `/api/portfolio/summary`.
- **Swagger closed by default** · FastAPI ctor now hides `/docs` `/redoc`
  `/openapi.json` unless `HUNTER_ENABLE_DOCS=1`. Fixes API-surface leak via
  direct `:8100/docs` bypass (nginx wasn't intercepting).
- **`/api/signals/` public** · middleware whitelist widened so the signal
  dashboard renders without auth for anonymous visitors.

### Removed
- `apps/api/routers/` and `apps/api/services/` dead paths (rsync artifact
  from hermes' old layout; only `apps/api/app/*` is imported).
- `POST /api/watchlist/feishu/config` + `GET /api/watchlist/feishu/config`
  routes and their `get_feishu_config` / `upsert_feishu_config` +
  `feishu_bindings` helpers · P2 completion.

### Added
- `scripts/export-openapi.py` · dumps `app.openapi()` to
  `docs/api-reference.json` (spec is generated even while HTTP endpoint is
  closed).
- `.github/workflows/ci.yml` · new `guardrails` job that fails CI on
  regressions: hardcoded `redis://localhost:6379`, stray
  `apps/api/{routers,services}` dirs, `wx_openid`/`feishu_bindings`/
  `booth_admin`/`ADVENTUREX_` leftovers.
- `.env.example` · clearer `HUNTER_MINIMAL_BOOT` docstring.

## [Unreleased]

### Added
- Initial monorepo scaffold (`apps/api`, `apps/web`, `db/`, `scripts/`, `docs/`)
- Dockerfile for `api` (Python 3.11 slim) and `web` (Node 22 alpine)
- `docker-compose.yml` with postgres 16 + redis 7 + api + web
- `HUNTER_MINIMAL_BOOT` env flag to skip legacy schedulers during P1 boot
- Non-standard host port defaults (web 3100 / api 8100 / postgres 5442 / redis 6479)
  to avoid conflicts on shared machines

### Known limitations (P1 skeleton)
- SaaS-side routers (wechat / feishu / ax / booth) still present · Sprint 06 P2 removes them
- No local auth yet · JWT still expects upstream `agentpit` DB · Sprint 06 P3 replaces
- Provider abstraction not yet built · LLM/data source calls will fail until you point
  at real backends · Sprint 06 P4 introduces provider layer
- `opencode` service commented out · needs `ghcr.io/agentpit-io/hunter-opencode` image
  which Sprint 06 P3 publishes
- No CI · Sprint 06 P5 adds GitHub Actions

### Sprint 06 roadmap
See `hangeaiagent/hermes-1 · doc/codex/开源整合方案/06-Sprint计划.md` (internal).
