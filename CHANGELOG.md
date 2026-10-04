# Changelog

All notable changes to HunterCode · Community Edition follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-10-05

> **主版本 · 智能炒股「五期 · 闭环补齐」（`L01`–`L10`）。**
> 一/二/三/四期把「能跑、能记账、能学、能提案」做完了，五期补的是**最后几块不闭环的地方**：
> 自动闭环只闭了一半（**会复盘、会验证，但不会自己把经验变成提案**）· 策略没有身份与版本锁 ·
> 决策缺「当时看到了什么、用什么模型算的」的时间字段 · 账本缺部分成交 / 成交量约束 / 停牌 / 公司行为 / 逐市场 tick ·
> 行情口令与下单口令是同一把、放行靠拒绝名单 · 报告只有站内一个出口且「发出去没发出去」从不写 ·
> 外面**没法启动 / 查询 / 暂停 / 取消**工作流 · 新闻 / 基本面没接定时采集、情绪完全无表。
> 本期把这几块一一补齐，并把全过程整合成一份**自包含 HTML**（`docs/开发文档/五期-闭环补齐-整合报告.html`）。
>
> **新增七个数据库迁移** `0047`–`0053`（只做加法、可重复执行、**不改任何历史行**、文件内不带 `BEGIN;`/`COMMIT;`），
> 由 `api` 启动时按 `schema_migrations` 账本**增量自动执行**。台账从 **47 个文件 / 47 行**
> 走到 **54 个文件 / 54 行**（`max = 0053_collection_complete.sql`）。
>
> **动到 `api` / `fin-worker` / `paper` 三个镜像**（`web` / `llm-shim` / `opencode` **代码未动**，版本号随发版一起走）。
>
> **新增两个环境变量**：`HUNTER_EXEC_KEY`（下单执行凭证，与行情读取的 `HUNTER_INTERNAL_KEY` **分开**；
> **都没有默认值**，缺任一 `paper` / `fin-worker` **拒绝启动**）。**默认值没变**：
> `FIN_MEMORY_ENABLED=0` / `FIN_EVOLUTION_MODE=off` / `FIN_AUTO_APPLY=0` / `FIN_LIVE_ORDER_ENABLED=0`。
>
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 两个旋钮一起改 `2.0.0`**，再
> `docker compose --profile fin pull && docker compose --profile fin up -d`（**`--profile fin` 必须带**，
> 否则 `paper` / `fin-worker` 不参与重建）。逐步清单见 `docs/开发文档/L10-五期上线与整合报告.md`（含**回滚一步**）。
>
> ⚠️ **升级前先备份**：`pg_dump -Fc` 记下路径 / 大小 / `sha256` / PG 版本 / `schema_migrations` 行数。
> 本版**只加表加列、不改任何历史行**。
>
> ⚠️ **`HUNTER_EXEC_KEY` 是本期新加的必填项**：老 `.env` 里没有它，升级后 `paper` / `fin-worker`
> **会因为缺凭证拒绝启动** —— 先在 `.env` 里生成一个（`openssl rand -hex 32`）再 `up -d`。

### ✨ 新增 · Added

- **`L01` · 「经验 → 提案」接上（`fin.propose` 工作流 + 「实验」实体）。** 原来那个能提提案的
  `POST /internal/fin/evolution/proposal` 写好了却**全仓没有调用方**（自动闭环只闭了一半）。
  现在 `fin.propose` 是 **Temporal Schedule 里的一条**（**不另起 cron / 线程定时器**），
  按市场、对进行中的项目**自动、有闸门地**把可用经验变成提案；并补出**「实验」实体**
  （方案 §12 时间线里缺的那一格「样本外 / 滚动验证」）。**只自动「提」，不自动「生效」** ——
  `FIN_AUTO_APPLY` 恒 0 未动，生效仍要人工 `confirm=true`。迁移 `0047_evolution_experiment.sql`。
- **`L03` · 决策「出身证」。** 补 §6.2 的缺失时间字段（`available_at` / `revision_id` / `decision_as_of`）·
  §10.2 的 `DataSnapshot` 对象 · §10.3 的决策上下文（`execution_model_version` / 显式 `mode` / `portfolio_version`）。
  **拿不到真值的一律 `NULL`**（红线 5），不拿 `now()` / 自增号 / 默认值冒充。迁移 `0048_decision_provenance.sql`。
- **`L04` · 自有策略服务。** 「策略定义」这一层原来根本没有：唯一的策略是写死的示例策略，身份是
  `fin_param.strategies` 里一个**可被任意改写的自由字符串**。现在有**只追加的登记表 + 内容哈希 + 触发器锁版本**，
  外加 `strategy.submit` / `strategy.get` / `strategy.cancel` 三个正式入口；示例策略**降级为一个登记在册、
  可被替换的内置策略**（不删 —— 个人本地开源部署靠它开箱可用）。迁移 `0049_strategy_registry.sql`。
- **`L05` · 账本补齐五项。** ① **成交量约束**真的读 `liquidity_max_participation`（成交股数 ≤ `floor(盘口量 × 参与率)`）；
  ② **部分成交** `part_fill` 开关真的生效（开着成交一部分 + 剩余挂着；关着**行为逐字节不变**）；
  ③ **停牌** `fin_instrument.halted` + 风控**先读它** + 拒单；④ **公司行为**只追加事件表
  `fin_corporate_action` + 账务（分红入现金 / 拆送股调股数与成本）；⑤ **逐市场 tick**
  （标的 → 市场分档 → 全局回落三级解析，三市场各不同，替掉原来写死的全局 `0.01`）。
  **停牌 / 公司行为数据源没有 → 只做「状态位 / 事件表 + 账务 + 人工登记入口」，如实标注「数据源未接」，一个字节都没编。**
  迁移 `0050_account_ledger_complete.sql`。
- **`L06` · 凭证分离 + 执行允许名单（默认拒绝）。** **两把钥匙分开**：行情读取用 `HUNTER_INTERNAL_KEY`，
  下单执行用新的 `HUNTER_EXEC_KEY`（两个不同环境变量、**都没有默认值**）。**门改成「允许名单」**：
  `paper` 会改账本的四个端点入口先查新表 `fin_exec_allowance`（**只追加**）—— **空名单 = 谁都不许**，
  要**显式登记**才放行（`POST /api/v1/exec-allowances`）。实盘字段的**拒绝名单**（`LIVE_FIELD_DENYLIST`）
  **继续保留**，两道防线都在。`GET /healthz` 多报一个执行允许名单现行条目数。迁移 `0051_exec_allowance.sql`。
- **`L07` · 发布适配器 + `UNKNOWN` 待核实。** 发布从生成流程里**拆出来独立一步**、渠道**走适配器**：
  `in_app`（原样搬，**行为不变**）· 通用 **`webhook`** · **`file`** 落盘（记路径 + `sha256`）。
  **只做这三个**，不接任何要注册 / 要付费 / 要审资质的第三方内容平台。新增 `publish.submit` / `publish.get`
  两个入口，回执统一落 `fin_publish_receipt`。**`UNKNOWN` 待核实 + 不盲目重发**：超时 / 回执不明 → 写 `UNKNOWN`
  并进待核实队列；`UNKNOWN` 下再发同一份报告**拒绝**（除非显式「重发」标记且**留痕**）。
  **补上故障注入第 7 项**（「内容已发布，但返回超时或网络断开」）。迁移 `0052_publish_adapter.sql`。
- **`L08` · 控制通道 + MCP 控制类 + 长任务取消。** 四个控制入口 `runtime.workflow_start / get / pause / cancel`
  （**只认白名单模板名 + 类型化参数**，**不接受任意代码或 shell**；状态**从 Temporal 现查**，不另存一份；
  四个都**幂等**）。新增**一个控制类 MCP**（`scripts/opencode-mcp/runtime_mcp.py`），
  在 `gen-config.py` 里**与数据类 MCP 分开登记** —— 这就是方案 §6.3 的「控制通道与数据通道分离」。
  **长任务取消接通**：`workflow_cancel` → 查工作流当前 `fin_job` → Temporal 取消 → 调 paper 的 `cancel`
  → `fin_job` 落 `CANCEL_REQUESTED`，补上 §10.4 的「可请求取消」。
- **`L09` · 采集补齐。** 方案要的「24 小时自动采集」里，**新闻与基本面真的接上了定时采集**
  （每个交易日按市场触发、写库、记来源 / 发布时刻 / 入库时刻 / **缺口**）；**情绪没有数据源 → 只建表 + 登记口 + 口径**，
  **一个情绪值都没编**。三条链路全走 **Temporal Schedule**，**没有另起任何 cron / 线程定时器**。
  迁移 `0053_collection_complete.sql`。
- **`L10` · 整合报告（自包含 HTML）。** `scripts/gen_wuqi_report.py` 生成
  `docs/开发文档/五期-闭环补齐-整合报告.html` —— **一份文件、六部分**（开发方案 / 开发计划 /
  开发完成后总结 / 开发部署 / 开发详细测试方案 / 测试后截图），**截图 base64 内嵌，离线可读**。
  脚本随仓库提交、**可重跑**（同一 `--date` + 同一输入 → 逐字节相同的输出）。

### 🔧 变更 · Changed

- **`L02` · 同一事实只留一份（工程债）。** 三处最要命的「同一事实多副本」各合成一份，并加了
  **会真失败的守护用例**：① **港股交易时段** 3 份 → 1 份字面量（`market_sessions.py` 双镜像逐字节相同）
  + **DB 行为权威**（api 种子脚本改成读 DB 行）；② `apps/api` 业务代码里的
  `timezone(timedelta(hours=±N))` **27 处 → 0**、IANA 时区名字面量 **11 处 → 0**（只在 `market_time.py`）；
  ③ `ensure_schema` 里重复定义的 DDL **2 处 → 0**（paper 内 3 份收成 `aux_ddl.py` 一份）。
  **新代码不许再制造「同一事实多副本」**（五期新增红线 14）。
- **`L05` · 风控规则清单从六条变七条**（停牌作为**第一条**，方案要求「风控先读它」）。

### 🆕 新增环境变量 · Added env

| 变量 | 默认 | 说明 |
|---|---|---|
| `HUNTER_EXEC_KEY` | **无（必填）** | **下单执行**凭证。与行情读取的 `HUNTER_INTERNAL_KEY` **分开**。缺任一 → `paper` / `fin-worker` **拒绝启动**。 |

> 迁移 `0047`–`0053` 均为**只做加法**；`0051` 新建的 `fin_exec_allowance` **默认空名单 = 拒绝**，
> 空库 / 老部署升级后需要**显式登记**才放行（这是设计，不是故障）。

### ⚠️ 与上游文档 / 旧方案不同、需要知道的

- **发布渠道只做 `in_app` / `webhook` / `file`**（方案 §6.1 的缩小范围）——**不接**任何要注册、要付费、
  要审资质的第三方内容平台。
- **停牌 / 公司行为 / 情绪没有数据源**：只做**表 + 账务 / 规则 + 人工登记入口**，如实写「数据源未接」，
  **绝不编造**（红线 5）。清单见各段成果文档的「未接数据源清单」一节。
- **`L04` 的策略服务不写 `fin_param`** —— 策略参数改动仍走 `control.py` 的**唯一写入口**（红线 7）。
- **api 全量用例里有 17 条**（`tests/test_fin_evolution_router.py`）在「整目录一次跑」时失败 ——
  这是**既有的跨文件测试污染**（`test_fin_evolution_propose.py` 泄漏模块级 `_INTERNAL_KEY`），
  与本期无关：**同一文件单独跑 18 passed**，**排除污染源后全量 986 · 0 fail · 4 skip**。
  本期**不改别人的用例**（红线 6）。

### 🧪 测试 · Tests

- `apps/api`：**998 条 · 17 fail（上述既有污染）· 0 err · 4 skip**；排除污染源 **986 · 0 fail · 4 skip · 982 passed**。
- `apps/fin-worker`：**319 passed · 0 failed · 0 skipped**。
- `apps/paper`：**320 · 319 passed · 1 skipped · 0 failed**。
- 迁移幂等：**54 个文件 = 54 行台账**，**连跑两遍 0 报错**（第二遍 `本次待执行 0 个`）。
- `03 §6` 十二条必测场景（`scripts/r10_demo_verify.py`）：**24/24 通过**。
- 真浏览器（Playwright）：`r13` / `r9`（`empty` / `degraded`）**0 pageErrors**。

## [1.8.0] - 2026-10-05

> **次要版本 · 把「经验库开关」从 `.env` 搬上界面（按项目）。**
> `1.7.0` 那条链本来就能学，但「要不要学」这个总闸只藏在环境变量 `FIN_MEMORY_ENABLED` 里：
> 想开要改 `.env`、还得 `up -d`（不是 `restart`）、六步操作，而且四个开关一个都不留痕。
> 本版让你在**成长页**和**设置向导第 7 步**直接点：**按项目**设，点完**立刻生效**（不用重建容器），
> **每次改动都留一行流水账**。**环境变量仍是天花板**（部署者的意志），界面是**天窗**（使用者的日常选择）：
> 部署侧把 `FIN_MEMORY_ENABLED` 设成 `0` 时，界面上的开关是灰的、写接口直接 `400` —— **界面永远开不出部署者不允许的东西**。
> 顺手修了一个隐性浪费：**经验库关着时，复核工作流不再先调一次模型再被拒**（原来那一次模型调用白烧）。
>
> **新增一个数据库迁移** `0046_memory_switch.sql`（两张表：`fin_memory_switch` 现值 + `fin_memory_switch_log` 纯追加流水），
> 由 `api` 启动时按 `schema_migrations` 账本**增量自动执行**，只做加法（`CREATE TABLE IF NOT EXISTS`）、
> 可重复执行、**不改任何历史行**、文件内不带 `BEGIN;`/`COMMIT;`。
> 台账从 **46 个文件 / 46 行** 走到 **47 个文件 / 47 行**（`max = 0046_memory_switch.sql`）。
>
> 动到 **`api` / `web` 两个镜像**：
> `api`（`switches.py` 加覆盖层 + `switch_meta()` 展示表 · `fin_runtime.py` 新写入口与只读接口扩展 ·
> `fin_review.py` 的「关着不烧钱」守卫 · `memory.py` / `evolution.py` 按项目判闸）·
> `web`（共用组件 `RuntimeSwitchPanel` + 成长页 ④ + 向导第 7 步）。
> `fin-worker` / `paper` / `llm-shim` / `opencode` **代码未动，版本号随发版一起走**。
>
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 两个旋钮一起改 `1.8.0`**，再
> `docker compose --profile fin pull && docker compose --profile fin up -d`（**`--profile fin` 必须带**，
> 否则 `paper` / `fin-worker` 不参与重建）。逐步清单见 `docs/开发文档/R21-开关上界面·发版与演示站验收.md`（含**回滚一步**）。
>
> ⚠️ **升级前先备份**：`pg_dump -Fc` 记下路径 / 大小 / `sha256` / PG 版本 / `schema_migrations` 行数。
> 本版**加两张新表、不改任何历史行**；记忆层是可审计数据，备份与恢复演练的做法随 `1.6.0` 交付（`docs/开发文档/R4`）。
>
> **默认值没变**：出厂默认仍是关（`FIN_MEMORY_ENABLED=0` / `FIN_EVOLUTION_MODE=off`）。
> 本版给的是「**想开的时候有个地方点**」，不是「默认打开」。

### ✨ 新增 · Added

- **迁移 `0046_memory_switch.sql`：按项目的经验库开关（现值 + 流水账）。** 造型照抄既有的 `fin_param` / `fin_param_change_log`：
  `fin_memory_switch`（现值 · PK `project_id` · `memory_enabled` / `evolution_mode` **两列可空**，
  `NULL` = 这个项目还没单独设过、跟随部署侧默认）+ `fin_memory_switch_log`
  （**纯追加** · 无 `UPDATE` / `DELETE` · `switch_key` / `old_value` / `new_value` / `actor` / `reason`（必填）/ `created_at`）。
  `evolution_mode` 有 `CHECK` 约束；`project_id` 不建外键（流水账要活过项目本身）。**两个硬开关不进这张表** —— 它们连界面入口都没有。
- **写入口 `POST /v1/fin/runtime/switch`（`R21` 的唯一写入口）。** body `{project_id, switch_key, value, reason}`，
  JWT + **项目归属校验**（跨用户 / 不存在一律 `404`，不区分两者、不泄露存在性）。
  四条硬规矩写在服务端、不靠界面自觉：**硬开关 key → 400**（且库里零写入）· **超天花板 → 400**（报错写清天花板是多少）·
  **`reason` 必填** · **没变就不写**（`changed=false`，流水账记的是「改动」不是「点了一下」）。
  改完**立刻生效**（写入口主动清 5 秒 TTL 缓存），**不需要重建 / 重启任何服务**。
- **前端共用组件 `RuntimeSwitchPanel`（表驱动 · 前端零枚举）。** 成长页 ④ 与向导第 7 步共用；
  可选项、短标签、长说明、「为什么灰」的提示**全部来自后端 `GET /v1/fin/runtime` 的 `meta`** ——
  前端**不写死任何枚举 / 中文名**（`R9` 既有纪律）。天花板之上的选项**画灰、点不动**（服务端另外还会 400 —— 两道一起拦）。

### 🔧 变更 · Changed

- **`switches.py` 加覆盖层：环境变量是天花板，`fin_memory_switch` 是天窗。**
  `memory_enabled(project_id)` / `evolution_mode_requested(project_id)` 的生效值 = **天花板 ∩ 天窗**，
  **取更保守的一个**（`0 < 1`、`off < observe < paper`）。**不带 `project_id` 时就是天花板、行为逐字节不变**（老调用点一个没动）。
  覆盖层读库带 **5 秒模块级 TTL 缓存**（热路径上挡住库往返）；**库不通 / 表还没迁移 / 行读坏了 → 回落环境变量**（fail-safe，绝不「猜一个」）。
  **覆盖层只活在这一个文件里** —— 读点仍是一处（守护用例盯着）。
- **只读接口 `GET /v1/fin/runtime` 扩展（老字段一个没改）。** 新增 `project_id` / `ceiling`（部署侧允许到哪）/
  `selected`（界面上选了啥，`null` = 没设过）/ `can_change`（这一项能不能改）/ `meta`（给前端的展示表）。
  带 `project_id` 时 `memory_enabled` / `evolution_mode*` 是**这个项目的生效值**（天花板 ∩ 天窗）；
  不带时就是天花板值，老调用点行为逐字不变。
- **复核不再「关着也烧钱」。** `routers/fin_review.py` 的 `review/propose` 在经验库关时**直接返回空候选、不调模型**
  （原来会先调一次模型写候选，最后一步才被唯一写入口拒掉 —— 那一次模型调用白烧，且 `503` 会让 Temporal 重试、可能被重放）。
  守卫**排在模型调用之前**，`fin-worker` 一行不用改（`R20` 实测：模型 **0 次调用**、worker 不报错 / 不重试）。
- **`evolution.py` 的提案闸门按项目判**（`propose` 用 `evolution_enabled(project_id)`）；**`apply_proposal` / `rollback_proposal` 仍只看天花板** ——
  把学习关掉不该让已经走到验证中途的提案变孤儿（故意的不对称，写进 `R20` 成果文档）。
- **`memory.py` 的 `MemoryDisabledError` 文案点名是哪个闸**（既有断言盯着）：同时提 `FIN_MEMORY_ENABLED=0` 与本项目开关。
- **`.env.example` / `.env.personal.example` / `docker-compose*.yml` 的版本旋钮升到 `1.8.0`。**

### 🆕 新增环境变量 · Added env

**无。** 这四个 `FIN_*` 开关的**口径没变** —— 只是从「只能改 env」变成「env 是天花板、界面是天窗」。
`FIN_MEMORY_ENABLED` / `FIN_EVOLUTION_MODE` 的默认值仍是 `0` / `off`（见 §「与上游文档 / 旧方案不同」）。

### ⚠️ 与上游文档 / 旧方案不同、需要知道的

1. **本版是「按项目」，不是「按机器」。** 方案稿 `10` 原设计是机器级（`fin_runtime_switch`，迁移 `0047`）；
   用户口径落成了**按项目**（`fin_memory_switch` + `fin_memory_switch_log`，迁移 `0046`）——
   一个部署里不同项目可以有不同的学习设置。原稿其余设计一条不改。
2. **默认仍是关。** 出厂默认 `FIN_MEMORY_ENABLED=0` / `FIN_EVOLUTION_MODE=off` 未动。
   界面能改，是因为**部署侧允许**；部署侧没开时，界面上的开关就是灰的。
3. **两个硬开关仍无任何界面入口。** `FIN_AUTO_APPLY` / `FIN_LIVE_ORDER_ENABLED` 不进可改项列表，
   请求带这两个 key → `400` 且**库里零写入**（`R20` 实测）。本版**不交付实盘能力**。
4. **「关着不烧钱」的守卫落在 `api` 侧，不在 `fin-worker`。** 花模型钱的那一步本来就在 api 的
   `review/propose` 里；在它前面拦下就够，比在 `fin-worker` 三个工作流各加一道更小、更少重复。
5. **多进程最多差 5 秒。** 覆盖层缓存的 TTL 缘故；个人本地部署只有一个 api 进程，实际是「立刻」。

## [1.7.0] - 2026-10-04

> **次要版本 · 让「统一经验与自进化」这条链**真的通电**。**
> `1.6.0` 把「经验 → 提案 → 独立影子验证 → 人工确认生效 → 紧急线回滚」这一圈的**架子**搭好了，
> 但三处还差最后一段线：判定器手上**没有行情**（永远回 `unknown`）、两条臂跑的是**同一套不读参数的动作**（`delta` 恒 0）、
> 生效之后**没人自动盯着**（观察期靠人工去叫）。本版把这三段线都接上 ——
> `regime` 能说出 `bull / bear / range`、示例策略**真读白名单参数**让两臂真的分叉、
> `fin.observe` **每市场一条 Schedule 到点自动观察**（越普通线只告警、踩紧急线才自动回滚）。
> **全程人工确认、没有实盘订单出口。**
>
> **没有新增数据库迁移**（`R11`–`R13` 一个迁移文件都没加；台账停在 `0045_evolution_apply.sql`，46 个文件 / 46 行）。
> 唯一的结构变化是新增 `.env` 旋钮 `FIN_OBSERVE_DELAY_MINUTES`（默认 30）—— 不设也能跑。
>
> 动到 **`api` / `fin-worker` 两个镜像**：
> `api`（`R11`：基准指数日线接入 `klines_etl.run_benchmark` + `regime.observe()` 换数据源）·
> `fin-worker`（`R12`：示例策略真读 `max_position_pct` + 影子臂同源搬参；`R13`：`fin.observe` 工作流 + 每市场 Schedule + 两个活动）。
> `web` / `paper` / `llm-shim` / `opencode` 代码未动，版本号随发版一起走。
>
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 两个旋钮一起改 `1.7.0`**，再
> `docker compose --profile fin pull && docker compose --profile fin up -d`。
> 逐步清单见 `docs/开发文档/R13-上线与自动观察.md`（含**回滚一步**）。
>
> ⚠️ **升级前先备份**：`pg_dump -Fc` 记下路径 / 大小 / `sha256` / PG 版本 / `schema_migrations` 行数。
> 本版不加迁移、不回填任何历史行，但 `R11` 会让三个 `fin-etl-*` Schedule 的那一轮**开始顺带取基准指数**（写进 `klines`）。

### ✨ 新增 · Added

- **`R11` · 基准指数日线接入，`regime` 不再恒 `unknown`。** `apps/api/app/services/data/klines_etl.py` 加显式基准映射
  `BENCHMARKS = {cn: 000300, hk: HSI, us: .INX}` 与 `run_benchmark(market)`；`fetch_tencent` 的解析口径抽成
  `_fetch_tencent_bars`（股票与指数**共用一处**，`[日期,开,收,高,低,量]` 的顺序只解一次）；
  `run_market()` 在**股票池检查之前**顺带取基准（**空池的新部署也取得到**，失败不挡股票池那一轮）。
  `regime.observe()` 数据源由 `fin_snapshot` 换成 `klines`（**只改这一个函数**）——
  `RULES` / `classify()` / `detect()` / 四元组形状**逐字节未动**。取不到 / 窗口不足 / 查库异常 → 仍 `unknown` → **仍停止策略提案**（fail-closed，规矩一条没放宽）。
  ⚠️ **不许靠 `_tencent_symbol` 前缀猜基准代码**：`.split(".")[0]` 会把 `.INX` 截成空串、把 `HSI` 拼成 `hk00HSI`；基准走**显式映射**。
- **`R12` · 示例策略真读白名单参数，影子两臂 `delta` 第一次非零。** `apps/fin-worker/app/strategy/sample.py` 的
  定量口径改为按 `max_position_pct` 算：`qty = min(档位手数, ⌊可用资金 × 占比 × 0.995 ÷ (参考价 × 每手股数)⌋ × 每手股数)`；
  四个输入（可用资金 / 参考价 / 每手股数 / 占比）**全是真值**，量价三输入任一缺失 → 回落**档位固定手数**（写死的数字，不是猜的）。
  **A 股市价单路径一行未改**（市价单出意图时不取价格与余额，硬按占比算的分母是编的 → 如实不作为，仍按档位固定手数）。
  策略读的字段是**唯一一份清单** `sample.STRATEGY_READ_FIELDS`，影子臂 `shadow.param_of()` 从它推导（少搬一个字段 = 接线做完但 `delta` 还是 0 且不报错）。
- **`R13` · 自动盯盘 `fin.observe` 接调度（本轮唯一缺自动化的一环）。** 新增 Temporal 工作流 `fin.observe`
  （`apps/fin-worker/app/workflows.py`）+ **每市场一条 Schedule**（`fin-observe-CN_A` / `fin-observe-HK` / `fin-observe-US`，`schedules.observe_specs`）：
  时点 = 该市场**时段末点** + `FIN_REVIEW_DELAY_MINUTES`（30）+ `FIN_SHADOW_DELAY_MINUTES`（15）+ **`FIN_OBSERVE_DELAY_MINUTES`（默认 30）**。
  骨架照 `fin.review` / `fin.shadow`：**查日历 → 非交易日空跑并记明原因 → 其他市场照常**；
  内层对「该市场所有进行中的项目」下每个**已生效**的提案各观察一次（两个活动 `observe_proposals` / `observe_applied`，经 `HunterApiClient` 走内网口令，**全程不碰数据库**）。
  **行为逐字复用服务层**：越普通线（提前停止线 / 观察期结束净收益 ≤ 失败线）→ **只写 `alert`**（`fin_alert_log` + 追加事件，**配置一动不动**、等人确认）；
  **只有**踩到冻结计划的 `rollback_line` 才自动回滚 —— 回滚目标取**版本链**（`applied` 事件的 `from_key`，**不许只信 `base_strategy_key` 字符串**），
  并一并产出 `polarity='refute'` 的失败经验（写入失败**不许吞**：落回灌重试队列，只读接口据此报「回灌待完成」）。
  **单条提案观察失败不挂整条工作流**（记进汇总继续）；**回滚失败必须可见**（api 把「回滚核验失败」翻成 409、把「进化未启用」翻成 503，工作流标 `rollback_error` 并打 `ERROR` 日志）。

### 🔧 变更 · Changed

- **`docker-compose.yml` 补齐 `FIN_SHADOW_DELAY_MINUTES` 透传。** `R7` 起 `config.shadow_delay_minutes()` 就读这个 env，
  但 compose **没接线** —— 部署侧改了 `.env` 也不生效（隐性缺口）。本版与新增的 `FIN_OBSERVE_DELAY_MINUTES` 一并补上（默认值与代码默认一致：15 / 30）。
- **`.env.example` / `.env.personal.example` / `docker-compose*.yml` 的版本旋钮升到 `1.7.0`。**

### 🆕 新增环境变量 · Added env

| 变量 | 默认 | 作用 |
|---|---|---|
| `FIN_OBSERVE_DELAY_MINUTES` | `30` | `fin-observe-<market>` 相对「时段末点 + 复核 + 影子」再往后推多少分钟。**可配**；读不到 / 非数字 / 负数一律回落 30 并在日志写明「用了默认」。 |
| `FIN_SHADOW_DELAY_MINUTES` | `15` | （本版**补上透传**）`fin-shadow-<market>` 相对「时段末点 + 复核」再往后推多少分钟。 |

### ⚠️ 与上游文档 / 旧方案不同、需要知道的

1. **`regime` 的 `unknown` 语义没变**，只是不再恒真：取不到基准 → 仍 `unknown` → 仍停止策略提案。
   演示站/线上要等这一版 `api` 镜像重建后，三个 `fin-etl-*` Schedule 的那一轮才会**自动开始取基准**。
2. **`R12` 只让「只接限价单的市场」（港美股）按占比定量**；A 股市价单路径不读占比 —— 这不是漏读，是如实的不作为。
3. **`R13` 的观察对象是「已生效」提案**，待验证提案归 `fin.shadow` 管，两者不重叠。
4. **`R13` 的 `fin.observe` 是「唯一自主改配置」的场合（紧急回滚）**，仍然**没有实盘出口**：`FIN_AUTO_APPLY` 恒 `0`、`FIN_LIVE_ORDER_ENABLED` 恒 `0`。

## [1.6.0] - 2026-10-04

> **次要版本 · 统一经验与迭代（记忆系统 + 受控自进化闭环）。**
> 让系统「学到的」这一层从一格都没有，建成**可存、可查、可冻结、防泄露**的一层，
> 并让它**真的改变行为**：经验 → 提案 → 独立影子验证 → **人工确认**生效 → 紧急线自动回滚
>（回滚本身产出一条有证据的失败经验）。**全程人工确认、没有实盘订单出口。**
>
> 五个数据库迁移，由 `api` 启动时按 `schema_migrations` 账本**增量自动执行**，
> 一律只做加法（`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`）、
> 可重复执行、**不改任何历史行**、文件内不带 `BEGIN;`/`COMMIT;`：
> `0041_memory_core.sql`（经验三表）· `0042_memory_layer.sql`（记忆层九列）·
> `0043_evolution_loop.sql`（演化四表 + 不可变触发器）· `0044_shadow_refs_jsonb.sql`（影子两列改 `jsonb`）·
> `0045_evolution_apply.sql`（生效事件 + 回灌重试队列）。
>
> 动到 **`api` / `fin-worker` / `web` / `paper` 四个镜像**：
> `api`（Memory 服务 + 提案 / 生效 / 回滚 + 迁移 + regime 判定器）·
> `fin-worker`（`fin.review` 复核工作流 + `fin.shadow` 影子工作流 + 每市场 Schedule）·
> `web`（成长页经验库与提案验证界面）· `paper`（影子臂路径 `app/shadow.py`）。
> `llm-shim` / `opencode` 代码未动，但版本号随发版一起走。
>
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 两个旋钮一起改 `1.6.0`**，再
> `docker compose --profile fin pull && docker compose --profile fin up -d`。
> 逐步清单见 `docs/开发文档/R10-上线与个人本地交付.md`（含**回滚一步**）。
>
> ⚠️ **升级前先备份**：`pg_dump -Fc` 记下路径 / 大小 / `sha256` / PG 版本 / `schema_migrations` 行数。
> 本次迁移只加表加列，但记忆层是可审计数据；备份与恢复演练的做法随本版交付（`docs/开发文档/R4`）。

### ✨ 新增 · Added

- **迁移 `0041_memory_core.sql`：经验三表。** `fin_experience`（经验条目 · `kind ∈ {fact,hypothesis,verified}`
  × `status ∈ {待验证,已确认,已推翻}` **分两列**）+ `fin_experience_evidence`（证据行 · 每条经验至少一行 ·
  `UNIQUE(experience_id, evidence_kind, ref_id)`）+ `fin_memory_snapshot`（`msnap_` 前缀的冻结快照 ·
  回放**按 id 取、不重查**）。四道 `CHECK` 硬约束（`hypothesis` ⇒ `待验证`、`verified` ⇒ 必填 `method`/`sample_size`、
  `exposure_scope` 枚举、`kind`/`status`/`source`/`market` 枚举）。**与既有的行情快照表 `fin_snapshot` 不撞名**
  （主键分别是 `memory_snapshot_id` / `snapshot_id`）。**这三张表刻意不给 `fin_paper_rw` 任何授权** ——
  「唯一入口」最硬的收口就是账本角色连 `SELECT` 都没有。
- **唯一的 Memory Service 与两个工具名。** `apps/api/app/services/fin/memory.py` 是**全仓唯一**碰
  经验三表的模块；对外只有 `memory.append_evidence`（唯一写入口）与 `memory.query`
  （唯一读入口，`freeze=True` 时顺带冻结 —— **冻结不是第三个工具**）。双通道路由：
  内网口令（`X-Hunter-Internal-Key`，给 `fin-worker`）+ JWT（给前端）。**过滤全在服务端**：
  `statement` 含阿拉伯数字 → 400（数字一律走 `evidence_kind='fact'` 引用）、证据至少一行、
  `ref_id` 必须真实存在、`holdout_tainted` 传染、`source` 由服务端按通道判定（**入参里强传被忽略**）。
- **复核工作流 `fin.review`（Temporal）+ 决策注入 `memory_snapshot_id`。** 各市场**收盘后 + `FIN_REVIEW_DELAY_MINUTES`**
  触发（可配，改值不动代码）：读当日账本 → 模型**只写解释文字**、所有数值来自账本真值 → 经唯一写入口落成经验。
  决策链路在出单前 `freeze_memory` 冻结经验集，命中标的的**负向 `verified`** 结论 ⇒ 该标的 `halted`，
  `memory_snapshot_id` 与命中条目写进 `fin_job.checkpoint`。**没读到经验集时 fail-closed**（抛错重试，不放过闸门）。
- **regime 判定器（`services/fin/regime.py`）。** 输出**固定四元组** `{label, rule_version, source_snapshot_id, as_of}`；
  阈值随**规则版本**固定（`regime-v1`，改阈值 = 新增版本键）；**行情缺失 / 窗口不足一律 `unknown`**；
  `unknown` 是独立取值，**不与明确 regime 混成同组**得出可交易结论。本部署没有基准行情源 ⇒ 恒 `unknown` ⇒
  **继续复盘、停止策略提案**（不拿一只持仓股冒充大盘，也不给复盘路径引入会超时 / 触 WAF 的外网依赖）。
- **标的市场规范化（`services/fin/symbols.py`）。** `normalize('HK','00700') = 'HK:00700'` ·
  `normalize('US','0700') = 'US:0700'`，**两者不相等** —— 拦单与提案都按结构化 `symbols` 列**精确匹配**，
  不再从 `applicability` 自由文本做包含匹配。
- **迁移 `0042_memory_layer.sql`：记忆层九列（只加列、不回填）。** `memory_layer`（闭集
  `episodic/semantic/procedural/strategy`）· `polarity`（`support/refute/neutral`）· `symbols` · `strategy_keys` ·
  `regime_tags` · `regime_source` · `importance` · `last_validated_at` · `duplicate_of` + 三个 GIN 索引 + 一个组合索引。
  **存量行一律保持 `NULL`**，不凭文本猜标的或市场状态；`importance` 只影响展示排序、**不参与统计加权**。
- **迁移 `0043_evolution_loop.sql`：受控自进化四表。** `fin_evolution_proposal`（提案 · `status` 只是**可重建投影**）+
  **`fin_evolution_plan`（冻结计划 · 写后不可改）** + `fin_evolution_shadow_event`（影子事件 · 追加）+
  `fin_evolution_event`（状态事件 · 追加 · **审计权威**）。不可变性由**数据库触发器**（`BEFORE UPDATE OR DELETE`）
  保证，另有**事件哈希链**兜底发现被绕过的篡改。红线的数据库层兜底：
  `CHECK (target <> 'risk' OR direction <> 'loosen')` —— **AI 提不了放宽风控的案**。
- **白名单提案 + 机器算 diff。** `target` / `direction` / 两个配置哈希 / `param_diff` **全部服务端算**，
  请求体里根本没有这些字段（「不采信写入方」）。提案只许改**白名单参数**（类型 / 取值范围 / 单次最大变化 / 基线版本），
  证据必须来自**同一份冻结快照**（同项目、同时间边界、非 holdout，`refute` 失败经验同样可引用）。
  **一次失败不得事后改 plan 再判通过 —— 改口径 = 新建提案。**
- **影子验证：候选臂独立模拟记账 + 两臂同条件（`apps/paper/app/shadow.py` + `fin.shadow` 工作流）。**
  候选臂与现行臂**同一行情快照、同一初始现金 / 仓位、同一费率 / 滑点 / 停牌 / 撮合假设**；
  样本按「**两臂均有可比机会的交易日 / 事件**」计（不把「候选有交易、现行没交易」的笔数直接相减）。
  **影子臂零订单出口**：用 `DecisionRecorder` / `OrderExecutor` 接口隔离，集成测试断言候选流程**从未调用执行端**，
  影子成交**一笔都不进 `fin_trade` / `fin_order`**。迁移 `0044` 把影子两列的持仓 / 估值快照改成 `jsonb`
  （估值口径 `total_assets = 现金 + 冻结 + Σ(股数×价)`、`nav = total_assets / 初始本金`，**缺价不出估值**）；
  唯一键 `(validation_id, arm, trade_date, point, symbol)` 保证**重试 / 重启不重复记账**。
  **窗口未结束绝不通过**；样本不足 / 行情缺口 / 未完成持仓一律 `inconclusive`（**不结论是合法终局**）。
- **人工确认生效 + 紧急回滚（迁移 `0045`）。** 成长页点「应用到模拟盘」→ API **CAS 校验**当前 `base_config_hash`
  未变（基线一动，旧提案作废）→ 走 `control.py` 的**唯一参数写入口**在**一个事务里**
  注册候选版本 + 写 `fin_param_change_log` + 切 active key（**绝不出现「配置已改而日志缺失」**）。
  观察期**继续按同一份冻结计划**算；**全系统唯一自动改配置的触发点**是已冻结的 `rollback_line`（默认回撤 −10%）——
  触发即回滚到**上一个已验证版本**（从版本链核验，不只信字符串）并**停掉该项目模拟下单**；其余不达标只告警、等人工。
  回滚**一并落三样**：`rolled_back` 事件 + 配置日志 + **一条有证据的失败经验**（`polarity='refute'`）；
  第三样写不进去时**不吞** —— 落一行重试队列任务，界面常驻「回灌待完成」+ 重试入口。
- **四个运行开关落在 API 服务端（`services/fin/switches.py` · 读点唯一）+ 只读接口 `GET /v1/fin/runtime`。**
  不是前端隐藏按钮：`FIN_MEMORY_ENABLED=0` ⇒ `memory.query` 返回空集合、`memory.append_evidence` **HTTP 503**；
  `FIN_EVOLUTION_MODE=paper` 的五个前置依赖（模拟账本 / 市场日历 / 行情快照 / 策略决策器 / 验证调度）
  **缺一即降级 `observe` 并在页面显示原因**（**绝不用历史成交顶替影子结果**）；两个硬开关非 0 ⇒ 服务端 503。
  非法值一律回落安全默认值并 `logger.warning` 留痕。
- **成长页（`apps/web/app/finance/growth/page.tsx`）：经验库 + 提案与验证。** 六块：①②③ 三块**如实标「本版未做」
  并写明为什么**（不返回整页占位），④ 经验库（列表 + 六维筛选 + **人机混合写入表单**：前端拦阿拉伯数字 +
  必选证据引用）、⑤ 四条底线（纯文案）、⑥ **提案与验证**（运行模式横幅 · 完整 `param_diff` · 冻结计划卡含 `plan_hash` ·
  两臂对照 · 事件链含被闸门拒绝的事件 · 两个人工按钮 · 「回灌待完成」状态位）。
  **界面一个字都不自己算**：净值 / 回撤 / 换手 / 成本 / 样本数全部原样展示，算不出一律 `—`；
  空态**全文不出现任何阿拉伯数字**（机器断言）。帮助页补了相关 FAQ（含「保底测试集为什么搜不到」）。

### ⚙️ 新增环境变量 · Added env vars

| 变量 | 代码默认 | 建议值 | 含义 |
|---|---|---|---|
| `FIN_MEMORY_ENABLED` | `0`（fail-safe） | **`1`** | `0` ⇒ `memory.query` 回空集、`append_evidence` **503**。P1 验收后设 1 |
| `FIN_EVOLUTION_MODE` | `off` | **`observe`** | `off` / `observe` / `paper`；本地首次启用选 `observe`；`paper` 依赖缺一即降级 |
| `FIN_AUTO_APPLY` | `0` | **`0`** | **恒为 0**：本方案不做自动生效，生效一律人工确认 |
| `FIN_LIVE_ORDER_ENABLED` | `0` | **`0`** | **恒为 0**：本项目没有实盘订单出口 |
| `FIN_REVIEW_DELAY_MINUTES` | `30` | `30` | 复核时点 = 该市场**时段末点 + 这个值**；非法值回落 30 并留痕 |

（前四个也透传给 `fin-worker`，但 worker **不读** —— 读点只有 api 的 `switches.py`；
执行侧要知道**生效**模式去问 `GET /api/v1/fin/runtime`。）

### ⚠️ 与上游文档 / 新方案稿不同 · 需要知道的四点

1. **经验库另建 `fin_experience` 三表，`user_memory` 一行不动。** 上游 `08 §78-79` / `09 :541` 假设
   「二期起复用 `user_memory` 当经验库」，本版**相反**：用户偏好（`user_memory`）与投资论点 / 证据 / 结论
   （`fin_experience*`）**互不读写**，后者只被 `services/fin/memory.py` 与 `routers/fin_memory.py` 引用
   （有 `grep` 守护测试盯着）。
2. **防评估泄露是「零开关」，不是「默认关闭的开关」。** 服务端**永远**
   `WHERE exposure_scope='searchable' AND holdout_tainted=false`，**不实现**任何 `include_holdout` / `debug` /
   `admin` 之类能放开的参数 —— **不实现就不可能被误开**（穷举入参也查不到 holdout 那条）。
3. **影子验证用的是独立模拟记账，`fin_trade` 不含候选收益。** `fin_trade` 只记真实 / 现行策略成交，
   推不出**未执行候选策略**的收益；候选臂的成绩只进 `fin_evolution_shadow_event`，
   且**影子臂没有任何订单出口**。
4. **`FIN_AUTO_APPLY` 恒为 0，生效一律人工确认。** 个人本地首版只人工确认模拟盘；
   放行依据含风控、成本与样本质量，不只是单一收益阈值。**回滚也不自动放宽风控** ——
   策略切换 ≠ 可放宽风控（`target='risk'` 的提案不走生效路径）。

### 🐞 修复 · Fixed

- **`stop_loss_pct` 正负号口径不一致 —— 任何向导开出来的真实项目都提不出提案。**
  `R6` 的白名单把 `stop_loss_pct` 写成了**正号**范围 `[0.005, 0.5]`（`tighter_when='smaller'`），
  而档位模板（`apps/api/app/services/fin/tiers.py`）与向导写进 `fin_param.stop_loss_pct` 的是**负数**
  （`-0.04` / `-0.03`，与另两条熔断线 `daily_loss_halt_pct` / `account_drawdown_halt_pct` 同口径）。
  `normalize_candidate` 会校验候选配置的**每一个键**，于是候选里带着 `stop_loss_pct=-0.04`
  一进门就被范围检查拒掉 —— 实测本机库 **1294 / 1312** 个项目的这个值都是 `-0.04 ~ -0.03`，
  **只有 `R9` 当时为跑通演示手工归一成 `+0.03` 的那 1 个例外**。
  现改为**负号口径** `[-0.5, -0.005]` + `tighter_when='larger'`（越接近 0 = 越早离场 = 越紧），
  与两条熔断线的写法**逐字同源**。按 `R6` 自己的规矩「改白名单任何一项 = 换版本键」，
  `ALGO_VERSION` 由 `evolution-algo-v1` 升 **`evolution-algo-v2`**
  （旧提案仍指 `v1`，永不被新口径重新解释）。**不需要数据迁移** —— 存量行立刻可提案。
  回归用例 `test_stop_loss_pct_uses_fin_param_sign_convention` 直接拿档位默认值
  `-0.04` / `-0.03` 断言通过，正号与越界值仍被拒。R6/R7/R8 的既有白名单 / 影子 / 生效用例
  跟着改成负号口径（共 12 处），**只改数据不改断言逻辑**。
  > 这一处是 `R9` 成果文档 §六-1 记下的「跨阶段缺陷」，`R10` 出口验收时实测复现并修掉。

### 🧪 测试 · Tests

- `apps/api`：R1–R9 逐阶段只增不减（无库 519 → **688** passed，真库 651 → **855** passed）·
  `apps/fin-worker`：135 → **204** passed · `apps/paper`：264 passed（新增影子隔离 7 条）。
  新增守护测试：`test_fin_memory_guard.py`（经验三表唯一入口）· `test_fin_evolution_guard.py`（演化四表）·
  `test_fin_switches_guard.py`（`FIN_*` 读点唯一）· `test_no_ledger_access.py`（`fin-worker` 不碰账本 / 经验表）。
- 真浏览器实测脚本 `scripts/r9_browser_check.mjs`：五阶段 **43 / 43** 断言，`pageErrors 0` / `consoleErrors 0`。
- 十二个必测场景（后来写入经验不进入旧快照 / 被推翻与 holdout 不进入提案 / 同标的不同市场不误匹配 /
  regime 缺失停止提案 / 两臂同行情时间戳 / 缺报价停牌未平仓返回不确定 / 验证窗口结束前绝不通过 /
  改计划必须产生新提案 / 并发人工改策略时 CAS 阻止旧提案生效 / 风险放宽在 API·数据库约束·端到端三处均被阻止 /
  重启后不重复影子成交 / 回滚后配置·日志·失败经验可互相追溯）在演示站逐条验收，
  证据见 `docs/开发文档/R10-上线与个人本地交付.md`。

## [1.5.2] - 2026-10-03

> **补丁版本 · 交易日历进迁移（`0039` A 股全天 + `0040` 港美股全天）+ 补上港股时段的第三份副本。**
> 动到 **`api` / `fin-worker` 两个镜像**（`db/migrations/*.sql` 是打进 `api` 镜像的，
> 容器启动时自动执行；`MARKET_SESSIONS` 也在 `api`；`0039` 发布时 `api` 镜像还没跟上）。
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 都改 `1.5.2`**，
> 再 `docker compose pull && docker compose up -d`。

### ✨ 新增 · Added

- **迁移 `0039_cn_a_market_calendar.sql`：幂等写入 A 股 2026 全年交易日历（365 行 / 242 个交易日）。**
  `fin_market_calendar` 此前**没有任何迁移写过数据**（`0023` 只建表、`0029` 只加市场维度、
  `0034` 只 UPDATE 港美股时段），所以**从迁移建起来的库，三个市场的日历都是空的**：
  自动交易页对 A 股一直显示「日历缺失」，`fin-worker` 的 CN_A 时点永远 `calendar_unknown` 空跑。
  本次写入与运行时 `fin-worker/app/activities.py:sync_calendar` **同口径**（`note` / `calendar_source`
  用同一批常量，`sessions` 现取 `fin_market_rule` 的 CN_A 行、不写死），
  `ON CONFLICT (market, trade_date) DO UPDATE` 幂等；**不带** `BEGIN;` / `COMMIT;`
  —— `migrate.py:apply_one` 已把每个迁移文件包在独立事务里，文件内再写事务边界会破坏那个承诺。
  已在演示站用「事务内清空 CN_A → 跑本文件 → 查数 → `ROLLBACK`」验过：**365 / 242 / 123**，
  国庆口径正确（09-30 交易日、10-01~10-07 休市、10-08 恢复），`sessions` 取自规则表。
  ⚠️ 本条目初稿写「港美股不在本次范围内」—— **同一天已由下面的 `0040` 补掉**。
- **迁移 `0040_hk_us_market_calendar.sql`：幂等写入港股 / 美股 2026 全年交易日历
  （各 365 行 · **港股 247 个交易日 / 美股 251 个**）。** 补掉 `0039` 留下的港美股缺口 ——
  在此之前从迁移建起来的库 `fin_market_calendar` 的 HK / US **一段数据都没有**，
  「只选港股」这类单市场项目在新部署上一直读不到日历。
  · 假期清单来源：**交易所官方页**的结构化数据（港股 HKEX `HKEX-Calendar` 页内
  `var DataSource` 的 `holidayIcon == "HongKongPublicHolidays"`；美股 NYSE `hours-calendars`
  页的 `Holiday | 2026 | 2027 | 2028` 三列表），取数用仓内自己的解析函数
  `market_calendar.parse_hkex_calendar` / `parse_nyse_calendar` —— **不是凭记忆写的**；
  并与同模块 `MANUAL_SEED` 人工兜底种子**差集为空**、与演示站现网数据**逐条一致**。
  · 港股 17 天休市（其中 3 天落在周六）· 美股 10 天（全在工作日；07-04 是周六，官方给 07-03 补休）。
  港股 `2026-02-16` / `12-24` / `12-31` 是**半日市**（上午照常交易），按拍板 §3.1 第 4 条
  算作交易日、写完整时段。
  · `note` / `calendar_source` / `sessions` 与 `scripts/seed_hk_us_calendar.py` 的产出
  **逐字节一致**（`sessions` 现取 `fin_market_rule`，故港股含 `16:00–16:10` 收市竞价）。
  · 已在演示站用「事务内清空 HK/US 2026 → 跑本文件 → 与现网逐字段比对 → `ROLLBACK`」验过：
  行数 **365/365**、交易日 **247/251**，与现网相比 **`is_trading` 差异 0 行**；
  另有 236 行 `sessions` 与 18 行 `note` 的差异，正是本文件**顺带修掉的**两处旧数据问题
  （见下面「修复」两条）。`ROLLBACK` 后线上数据核对仍为 HK 365 / US 365，未改动生产。

### 🐞 修复 · Fixed

- **港股时段的第三份副本没跟上（`apps/api/app/services/fin/market_calendar.py:MARKET_SESSIONS`）。**
  `v1.5.1` 把港股收市竞价 `16:00–16:10` 补进了 `fin_market_rule.sessions`（迁移 `0034`）
  与 `fin-worker` 的 `DEFAULT_SESSIONS["HK"]`，**漏了这一份** —— 而它正是
  `scripts/seed_hk_us_calendar.py` 种日历时写进 `fin_market_calendar.sessions` 的取值；
  `paper` / `api` 又是**日历行优先**（`risk/session.py:resolve_sessions`、`markets.py`），
  于是种出来的港股交易日只带两段时段，`16:00–16:10` 的成交仍被判「不在交易时段」。
  演示站实测（2026-10-03）：**HK 2026 的 247 个交易日里，236 行的 `sessions` 只有两段**
  （三段的 11 行是 `v1.5.1` 当时就地重跑同步的那个窗口）。三份副本现已逐字一致。
- **更正 `0038_fee_model_cn_a.sql` 文件头那条 ⚠️ 说明（该文件本身不改）。**
  它写「已有部署不会自动执行这个文件 —— `db/migrations` 只在数据卷第一次初始化时跑」，**与事实相反**：
  那是 `docker-entrypoint-initdb.d` 时代的旧行为；自 `apps/api/app/migrate.py` 上线后，
  **api 每次启动都按 `schema_migrations` 账本增量执行 `db/migrations/*.sql`**。
  演示站实测：api 容器 `2026-10-03T04:23:24Z` 启动，`0038` 于 `04:23:27Z` 落账（+3 秒），即自动执行。
  迁移文件一经发布不可变（改动会触发 checksum drift 告警），故更正写在本节与 `[1.5.1]` 条目里。
- **`.env.example` 里钉的镜像版本停在 `1.4.0`**（`HUNTER_VERSION` / `FIN_TAG`）—— `1.5.0` 起
  的发版就再没跟过。照这份模板抄 `.env` 的新部署会一直拉 `1.4.0` 的镜像、拿不到后面三个版本的
  迁移与修复。已改 `1.5.2`。

## [1.5.1] - 2026-10-03

> **补丁版本 · 演示站上线验收（P4）就地修的三处**。只有一个数据库迁移
> （`0038`，幂等补一行 A 股费率；api 启动时自动执行），动到 **`api` / `fin-worker` 两个镜像**。
> 升级：`.env` 的 **`HUNTER_VERSION` 与 `FIN_TAG` 都改 `1.5.1`**，再
> `docker compose pull api fin-worker && docker compose up -d api fin-worker`。
> ~~⚠️ **已有部署不会自动执行 `db/migrations/0038`**（那个目录只在数据卷首次初始化时跑）——
> 演示站等历史库请用 `0038` 里那段 `INSERT ... ON CONFLICT DO NOTHING` 手工补一次。~~
> → **此条作废（2026-10-03 更正）**：`db/migrations/*.sql` 由 api 启动时的迁移器
> （`apps/api/app/migrate.py`）**按 `schema_migrations` 账本增量执行**，并非「只在数据卷首次初始化时跑」。
> 演示站实测：api 容器 `04:23:24Z` 启动，`0038` 于 `04:23:27Z` 落账（+3 秒），**自动执行、无需手工补**。
> 详见 `[1.5.2]` 一节。

### 🐞 修复 · Fixed

- **A 股「费用」卡片显示的是美股费率**（2026-10-03 演示站实测）。`views._fee_model`
  在按市场取费率行失败时会**无条件回落**「`ORDER BY version DESC LIMIT 1`」，取到字典序
  最大的 `fee-us-v1`，把美股费率（佣金万1 / 印花税 — / 过户费万0.2）当成 A 股费率渲染了出来。
  现在**只有老库（没有 `market` 列）才回落**；列在、行缺 → 返回 `None`，卡片降级成
  「该市场的费率行尚未配置」而**不显示任何数字**（「空的比假的好」）。
  同时补迁移 `0038_fee_model_cn_a.sql` 幂等地写入一期的 `fee-cn-a-v1`（此前全仓没有任何
  迁移写它，`0029` 假定它已存在、`0034` 只补了 HK/US —— 从迁移建起来的库因此只有两行）。
- **港股「收市竞价」时段在兜底里缺失**（同日实测）。`fin-worker` 的
  `DEFAULT_SESSIONS["HK"]` 少了 `16:00–16:10`，而 `fin_market_rule.sessions` 有它 ——
  两份时段是同一个事实、却漂了。凡是「读市场规则失败 → 走兜底」那一次日历同步写出来的行，
  都会把收市竞价那根 tick（实测 `00700` 的 `event_time` 就是 `16:08:10`）判成
  「不在交易时段」→ 该市场当天**一笔都成不了交**。两处已对齐。

### 🧪 测试 · Tests

- `apps/api/tests/test_fin_markets_rule_view.py` +2：A 股费用卡片必须引 `fee-cn-a-v1`
  且文案是 A 股费率形状；**删掉该行后 `_fee_model` 返回 `None`**（直接复现上面那个缺陷）。

## [1.5.0] - 2026-10-03

> **次要版本 · 开户时自选市场，自动化按选中的市场跑（三期出口）**。有数据库迁移
> （新增 `0036` 一个迁移，由 api 启动时自动执行，**只加表 / 只放松约束 / 可重复执行**），
> 有 **`web` / `api` / `paper` / `fin-worker` 四个镜像的更新**。
>
> 升级主站：`.env` 里的 **`HUNTER_VERSION` 与 `FIN_TAG` 都改成 `1.5.0`**
> （`web` / `api` / `paper` / `fin-worker` 四个镜像都动了），然后
> `docker compose pull && docker compose up -d`。
> 部署与回滚的逐步清单见 `docs/开发文档/P3-上线清单.md`。

### ✨ 新增 · Added

- **开户时自选市场（可多选）**：设置向导从六步变**七步**，在「选档位」之后新增
  **「选做哪几个市场」**一步。三张可多选的卡片，每张的信息**全部从 `fin_market_rule` 取表**、
  不写死：本币 / 时区 / 交易时段 / 涨跌停模式 / T+N / 整手 / 费率模型 / 该市场本币本金。
  **至少选一个**才能下一步（未选时「下一步」禁用并给提示）；选中港 / 美股时**当场**提示
  「本版本不做价格带校验，回执与报告里如实标注『未做』」与「日历缺失该市场当日不可交易」。
- **每市场各一份档位本金、各用本币、互不折算**：10 万档选 A股 / 美股 →
  A 股子账户 100,000 CNY、美股子账户 100,000 USD，收益率各算各的。
- **「我的账户」页可追加市场（只增不减）**：常驻可见的「追加市场」入口，列出**还没选的**市场；
  界面写明「追加后该市场从此刻开始记账，不回填历史」。**全站没有任何「移除市场」控件**——
  要减少只能关停旧项目、开新项目；服务端对「隐含移除」的请求一律 400（双保险）。
- **各设置页按已选市场出**：总览 / 自动交易 / 我的账户的市场切换器**只列这个项目选中的市场**
  （不再是三个市场全列）；自动交易页在已选市场**日历缺失**时明文提示「该市场当前不可交易（日历缺失）」；
  向导的板块偏好、短线参数时点按市场出（港美股不出现「买入 ST」这条 A 股概念）；
  每日报告的跨市场汇总（`MULTI`）只在**已选 ≥2 个市场**时出现。

### 🔧 变更 · Changed

- `POST /api/v1/fin/projects` 与 `/projects/new` 收 `markets`（缺省老行为 `["CN_A"]` 逐字不变）；
  新增 `POST /api/v1/fin/projects/{id}/markets`（追加市场，目标集合必须是现有集合的超集，否则 400）。
- `GET /api/v1/fin/current` 与 `/projects` 带出 `markets` 数组（每市场本金 / 币种 / 开户时刻）。
- **`GET /api/v1/fin/markets` 每项新增 `rule`（`fin_market_rule` 的展示子集）与 `constraints`
  （该市场六条硬约束文案）** —— 只加字段，既有字段一个没动。
- `GET /api/v1/fin/account` 新增 `market_accounts`（该项目每个子账户一行：本币本金 + 最新收盘净值）。
- `paper` 的 `list_projects` 带出市场集合；`fin-worker` 的 `projects_for_market()` 由
  「`market_scope == market` 精确匹配」改为**「选中集合包含该市场」**——`MULTI` 项目不再空转，
  只在用户选中的那几个市场的时点各驱动一次。
- **港美股只接限价单**：示例策略按 `fin_market_rule.market_order_supported` 参数化，
  限价市场用**真实报价 + 真实可用资金 + 真实每手股数**定量，买不起一手就如实不出委托（不编数量）。

### 🗄️ 迁移 · Migrations

`0036` 建表 `fin_project_market(project_id, market, initial_capital, currency, opened_at)`
（市场集合的唯一真值）+ `fin_project.currency` 放开为可空 + 按**已核实的事实**回填
（现有项目全部 `market_scope='CN_A'`）。一律幂等，历史行一行未改。

### ⚠️ 已知边界

- **追加市场不回填历史**：新子账户的账本起点是明确的那一刻。
- 港美股费率仍是**回测级近似**（N2/N4 遗留，本版未动）。
- 港美股**不做价格带校验**（`price_limit_mode='none'`），回执 / 报告 / 界面三处都标「未做」。

---

## [1.4.0] - 2026-10-03

> **次要版本 · 智能交易板块支持港美股模拟交易（二期出口）**。有数据库迁移
> （新增 `0029`~`0035` 共 7 个迁移，由 api 启动时自动执行，**只加列 / 只放松约束 / 可重复执行**），
> 有 **`paper` / `fin-worker` 镜像的更新**（本期它们的调度、账本、风控都改了）。
>
> 升级主站：`.env` 里的 **`HUNTER_VERSION` 与 `FIN_TAG` 都改成 `1.4.0`**
> （`web` / `api` / `paper` / `fin-worker` 四个镜像都动了），然后
> `docker compose pull && docker compose up -d`。
> 部署与回滚的逐步清单见 `docs/开发文档/N5-上线清单.md`。

### ✨ 新增 · Added

- **港股 / 美股模拟交易**：三个市场（`CN_A` / `HK` / `US`）各一个子账户、**各自本币记账**
  （CNY / HKD / USD），**账本内永不折算**。港股 / 美股本期只接**限价单**，
  价格带校验模式为 `none` 并在回执与报告里**如实标注「未做」**。
  - 交易日历各自独立（表主键 `(market, trade_date)`，来源标在行上）：**A 股休市时港股 / 美股照常运行**。
  - 每市场一组调度时点、各自 IANA 时区（夏令时交给 Temporal，不写死偏移）。
  - 风控六条**逻辑不变、参数按市场取**：时段 / T+1 或 T+0 / 每手 / 价格带 / 费率 / 子账户资金。
- **每日报告按市场出**：每个市场一份 + **一份跨市场汇总**。金额按币种出符号
  （A 股 `¥`、港股 `HK$`、美股 `$`）；汇总里的跨币种合计**带汇率来源与取值时刻**
  （`fx_source` / `fx_at`，新浪外汇通道现取），**取不到就显示 `—` 并写明原因**。
- **前端市场切换器**（对齐策略中心已有的 A股/美股 切换）+ 市场状态条
  （读 `fin_market_calendar`，**日历缺失显示「日历缺失」而不是猜**）；
  总览页按市场分列资产并给出跨市场合计；自动交易页的时刻表与硬约束随市场切换。

### 🔧 变更 · Changed

- `apps/api` 的 `report.py`：`METRIC_SPEC` 的金额单位从写死的 `"CNY"` 改为 `money` +
  指标级 `currency`；事实表新增 `market` / `currency` 两列；报告口径文案写清
  「模拟盘 · 不接实盘」与「各市场规则不同」。
- `apps/api` 的 `overview` / `account` / `auto-trade` 三个读接口新增可选 `market` 参数
  （缺省回落项目 `market_scope`，**A 股旧调用点行为逐字不变**）；新增 `GET /api/v1/fin/markets`。
- `apps/web` 的 `money()` 加 `currency` 形参；`apps/web/app/finance/` 下不再有硬编码货币符号。

### 🗄️ 迁移 · Migrations

`0029` 市场维度 / `0030` 市场规则表 / `0031` 账本币种 / `0032` 印花税方向 /
`0033` 快照盘口质量 / `0034` 调度与账本市场列 / `0035` 报告市场维度。
一律 `ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`，历史行按 `'CNY'` / `'CN_A'` 回填
（**现有账本全是 A 股，回填是事实不是猜**）。

### ⚠️ 已知边界

- 港美股费率是**回测级近似**（三项征费合成一个 bps、美股 SEC 费率未按年分档）。
- 港股收市竞价（16:00–16:10）计入可交易时段 —— 数据源的港股最新价本来就是收市竞价打印。
- `market_scope='MULTI'` 的项目目前不驱动任何市场的自动调度（见研究报告遗留项）。

## [1.3.1] - 2026-10-02

> **修订版 · 顶栏补上「hunter 智能体自动炒股」主菜单**。只改前端一个组件
> (`apps/web/app/components/TopNav.tsx`) —— 无数据库迁移、无新增服务、无 env 变更。
>
> 升级:把 `.env` 里的 `HUNTER_VERSION` 改成 `1.3.1`,然后
> `docker compose pull web && docker compose up -d web`。其余服务镜像不用动
> (智能交易的四个 `fin` profile 容器与本版无关)。

### ✨ 新增 · Added

- **顶栏一级菜单「hunter智能体自动炒股」**(位置在「帮助中心」左侧),点击展开二级菜单,
  与智能交易板块页头的 7 个页签逐一对齐:总览 / 自动交易 / 我的账户 / 每日报告 /
  成长与复盘 / 安全与帮助 / 设置向导。
  - 补的是**入口**,不是功能:`/finance/*` 自 1.3.0 起就能用,但只挂在侧栏。
    从 `/chat` 主页进来第一眼看到的是顶栏,而顶栏原先只有「策略中心」——
    访客找不到炒股板块。
  - 二级项一律新窗口打开(`target="_blank"`),与顶栏既有约定一致,不打断对话页上下文;
    点菜单外部自动收起。
  - 一级项带「模拟」徽标,下拉底部另有一行「模拟盘 · 不接实盘」,避免被当成实盘下单入口。
  - 窄屏(手机)标签退化成「智能炒股」,否则 11 个字的标签会把顶栏撑破。

## [1.3.0] - 2026-10-02

> **次要版本 · 新增「智能交易」板块(纸上交易账本一期)**。有数据库迁移(新增
> `0023`~`0028` 共 6 个 `fin_*` 迁移),由 api 启动时自动执行;有新增可选服务
> (`paper` / `fin-worker` / `temporal` / `temporal-ui`,都在 compose 的 `fin` profile 下,
> **默认 `up -d` 不会起**)。
>
> 升级主站:`.env` 里的 `HUNTER_VERSION` 改成 `1.3.0`,`docker compose pull && docker compose up -d`。
> 要用智能交易再:设 `FIN_PAPER_PASSWORD=$(openssl rand -hex 24)` →
> `bash scripts/fin_provision_paper_role.sh` → `docker compose --profile fin up -d`。

**上线前实测**:本次没有浏览器端到端实测的缺口 —— 回归清单在部署机逐条真跑,
命令与结果见 `docs/开发文档/M8-成果与回归报告.md`。

### ✨ 新增 · Added

- **智能交易 · 纸上交易账本(一期出口)**。策略中心里的第四个板块,**只做模拟盘**。
  五个正文页(总览 / 持仓 / 委托与成交 / 报告 / 帮助)全部接真账本,不是静态样张。
  - **唯一账本服务 `paper`**:全仓唯一持有账本库运行期角色 `fin_paper_rw` 的进程。
    那个角色在追加表上**没有 `UPDATE` / `DELETE` 权限** ——「不可篡改」是一条 GRANT。
    启动时自检模式、内部口令、库连通、表齐与角色权限,任一不过直接退出,不带病启动。
  - **撮合四步链路**:策略意图 → 确定性风控(A 股六条:涨跌停 / T+1 / 100 股整手 /
    资金与持仓 / 单笔上限 / 黑名单)→ 撮合 → 记账。每一步都绑上行情快照;
    **拿不到报价就不成交**(挂单),不拿过期价成交。
  - **六个交易时点交给 Temporal**(09:15 / 09:30 / 11:30 / 13:00 / 14:55 / 15:30),
    **不留兜底 cron**。Worker 崩溃后按执行历史恢复,不重复下单。
  - **报告三层分离**:事实由代码算、文字由模型写、写完回读校验 —— 校验不过就不发布,
    宁可显示 `—` 也不让模型编数字。
  - **实盘隔离**:`PAPER_MODE` 恒为 `PAPER`,不配置任何交易凭证,请求参数里出现
    实盘相关字段即报错。新服务端口除 web 外全部只绑 `127.0.0.1`。
  - 一期**只做 A 股**;行情走现仓的 `providers.data_source`(hunter 网关优先,
    `tencent-qt` 免费通道兜底)。
- **两个新镜像**:`ghcr.io/agentpit-io/hunter-community-paper` 与
  `...-fin-worker`,amd64 + arm64 双架构,与现有四个镜像走同一条发布流水线。
- **五个一键部署模板同步**:Dokploy / Coolify / Railway / Zeabur / Sealos(以及 1Panel 应用包)
  都补上了这四个服务,并各带一个一次性 `fin-init` 容器给账本角色设口令
  (云平台上没有人工跑 provision 脚本这一步)。

### 🔧 其他 · Changed

- `deploy/tools/template-to-compose.py` 与 `validate-templates.py` 跟随模板更新,
  六个平台的等价 compose 都能生成并通过结构校验。
- `NOTICE` 补上 Temporal(MIT,服务端 / UI 镜像 + Python SDK)与 PostgreSQL。

### 📌 已知边界

- 行情数据源第一顺位是 hunter 网关;仓库**不含**任何明文 key,需自行申请填入 `.env`。
- `company_master` 只是演示种子,不是全市场 ST 权威 —— ST 判定走行情通道返回的证券简称,
  判不出即拒绝该标的(零容忍),绝不猜涨跌幅。
- 账本数据(`fin_*` 表)只做加法迁移,回滚镜像不需要回滚数据库。

## [1.2.3] - 2026-09-29

> 补丁版本,无数据库变更、无新环境变量。
> 升级:`.env` 里的 `HUNTER_VERSION` 改成 `1.2.3`(没写的不用改),然后
> `docker compose pull && docker compose up -d`。桌面启动器用户点「升级」即可。

**上线前实测**:策略页自检 `render_check.js`(69 条)、`agent_rules_check.js`、`agent_overfit_check.js` 均 PASS;
图标 512×512、65,156 字节,与全彩版 RMSE 0.0096。功能提交已在 main 上跑过 CI(5 项全过,含前端
`tsc --noEmit` + `next build` 与后端 pytest,后者覆盖本次新增的两个用例文件)。
**未做浏览器端到端实测** —— 规则编辑保存、手动回测进度、过拟合卡片只有前端逻辑自检与后端用例覆盖,
没有真在浏览器里走完整链路。这是本次发布的已知缺口。

### ✨ 新增 · Added

- **小鹿 · 个人规则编辑与手动回测**(涨停后强势整理、涨停三阴两条线)。规则区拆成「编辑规则」与「手动回测」两个入口:
  编辑弹窗只放参数和保存按钮,日期、进度与结果放在独立回测窗口 —— 原来的保存入口是全站裸按钮样式,
  没有边框和背景,被当成了一行文字。可改金额、持有天数、板块、均线、整理幅度与量能条件,
  **核心信号与费用口径固定**。新增接口 `GET/POST /api/quant/agent/rules/{branch}` 与 `POST .../backtest`,
  全部要求登录并按身份读写。
  - 参数、版本与最近十次回测存在 `agent_meta` 的 `manual-rule:<用户>:<方向>`,公共纸上交易数据属于共享研究,
    不会被任意账号改写。
  - 保存带版本检查,防止两个窗口互相覆盖;每次回测冻结完整参数快照,**改动未保存时禁止启动** ——
    不拿旧交易统计冒充新规则的统计。
  - 一次最多一年;数据库锁限制全站同时只跑一个手动回测;任务状态持久化,进程中断后刷新能识别失败并重跑。
  - 全池逐日重算,**不复用原固定预筛池**:放宽参数后旧池可能漏掉符合新规则的股票。
  - 不补造日线 —— 回测区间必须落在已有日线范围内。
- **小鹿 · 过拟合监测**(研究台、运行看板、每次手动回测完成后显示「验证充分度 X / 100」)。九项固定权重的检查
  清单:样本初筛、跨时段表现、幸运交易依赖、假设记录、全部试验留档、未见数据验证、参数邻域、成本压力、
  数据与成交时序。**总分为已通过项的权重之和,未做项不重新归一化 —— 当前最高可得 35 分**,
  只调历史收益拿不到高分。它回答的是「证据有多充分」,不是「下次赚钱概率」,也不输出「过拟合概率」。
  失败任务不评分,没有完整平仓交易显示 `—`,真实零分显示 0。第一版不自动发布、淘汰或改变交易规则。
- **产品说明书与帮助中心**。新增 `doc/产品说明书.md`(7 节:从哪里开始 / 一天的工作流程 / 两类应用方案 /
  量化选股监控与回测 / 用 SKILL 扩展研究方法 / 扩展实施路线 / 验收清单与使用边界)。
  `scripts/generate-help.py` 由它生成静态帮助页,顶部导航与 README 都加了入口。

### 🔧 其他 · Changed

- **站点图标从 1.2 MB 降到 64 KB**(1,222,097 → 65,156 字节)。`/icon.png` 与 `/logo.png` 一直是同一张
  1118×1126 的大图,而两处都只当 favicon 用 —— 每次打开标签页都要拉 1.2 MB。缩到 512×512 并做 256 色量化
  (与全彩版 RMSE 0.0096,肉眼难辨)。路径不变,策略中心 7 个静态页与 `render_check.js` 的断言都不用动。
- 帮助中心补上 favicon 声明。静态页不走 Next 的 `app/icon.png`,不写这两行浏览器就是空白图标。
  改在 `generate-help.py` 的模板里,重新生成不会被覆盖。
- `.gitignore` 增补 `.env.backup*`。改 `.env` 前的手工备份含真实密钥(`POSTGRES_PASSWORD` / `JWT_SECRET` /
  `LLM_API_KEY`),而原来的规则只覆盖 `.env` 与 `.env.*.local`,那个文件一直以未跟踪状态躺在工作区,
  一条 `git add -A` 就会把它推上 GitHub。

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
