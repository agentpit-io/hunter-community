# CLAUDE.md — hunter-community

本文件是 Claude Code 在本仓库工作时会自动读取的项目指令。

---

## 铁律:严禁 mock 兜底 · 空的比假的好

量化 / 指标 / 因子 / 回测 / 评分 类代码,任何**用户会看到或送给 LLM 分析**的数字都必须是真算出来的。算不出就显示 `—` 并注明原因。

### 严禁的模式

- `Object.assign(mockDefault, realResult)` — 真数据缺字段时假数据会露出
- `realValue || 0` / `realValue ?? 0.5` — `0.00` 看起来像个结论
- 渲染层直接写常量或伪造公式(基准 `= 1 + i*0.005`、超额 `= ann_ret - 0.062`、相似度 `= [87,62,54][i]` 之类)
- API 转换层挂 `_mock` / `metrics_mock` 字段 — 修了展示层不算完,上游会继续注入
- 把不确定真假的指标送进 `askHunter` / LLM — 模型会认真点评根本不存在的业绩

### Why

2026-08-18 事故:「全能蓝筹」3 年回测同屏出现「年化 −1.2%」与「信息比率 0.98 / 月度胜率 62%」—— 后两个是 `backtest.html` 里写死的常量,因为后端 `_calc_metrics()` 从来没算过 `ir` / `win_rate`,`Object.assign` 用 mock 补上了。全量排查后共发现 4 个页面 **8 处**假数据(20 个因子的 60 个指标、6 个官方策略 metrics、净值曲线、基准直线、因子归因公式…),其中最糟的是 `askHunter` 把假指标发给 LLM 分析。

相关提交(按顺序读):
```
05c8d3d fix(quant): 回测不再用假数字冒充真结果 (_17 步1-3)
fefcb6c fix(quant): 清掉全部写死的假数据 (_17 全量排查)
13a6f8f feat(quant): 接真指数日线 + 实现回测基准
863d2d1 feat(quant): 指数成分股走代理拉取 + seed 真数据
ba62a96 fix(quant): 补完 _17 剩下四条
9335fe3 fix(quant): 修白屏 —— 漏了两处读取方与三类语法坑
```

演示模式不是借口 —— 要 demo 数据就跑一次真回测把结果存下来当样例,不要在渲染层编。

### 落地方法

**改动前** —— 先全量 grep,把所有假数据来源列一遍再动手:

```bash
grep -rn "mock\|_mock\|Object.assign\|writeDefault\|\|\| 0" apps/web/public apps/api/app
```

**修一处后要再全量排查一次** —— 上游 API 转换层常常在真数据上重新挂假字段。前科:先修了 `backtest.html` 的 mock,但 `app.js:197` 在 API 转换时又挂了 `metrics_mock`,真数据流下来被再次污染。

**删/改字段名要 grep 所有读取方,不能只搜字段名本身:**
- ❌ 只搜 `metrics_mock`
- ✅ 还要搜 `.metrics.` 、`?.metrics`、`s.metrics` 等直接属性访问
- 前科:白屏事故就是漏了 `s.metrics.ann_ret` 这种直接读取

**前端验证不能只靠 HTTP 200 / `node --check` / curl** —— 这些都测不到内联 `<script>` 里的错。用 `apps/web/public/strategies/render_check.js`,在 node 的 `vm` 里真跑一遍页面脚本,假 `document` / `localStorage` / `fetch` 兜住。

### 触发词

出现下列任一词的改动,自动触发本铁律:
> 回测 · 指标 · 因子 · 成分股 · 换手率 · 换仓频率 · 基准 · 超额 · IR · 信息比率 · 胜率 · 净值 · 归因 · 持仓 · 相似度 · 评分 · metrics · sharpe · ann_ret

---

## 铁律:db/migrations 里的 .sql **对已有部署不生效**

`docker-compose.yml` 把 `./db/migrations` 挂到 postgres 的
`/docker-entrypoint-initdb.d` —— 那个目录**只在数据卷第一次初始化时执行**。
线上库已经跑了几周,你新加一个 `00XX_xxx.sql` 它**永远不会被执行**。

所以真正生效的 DDL 必须**随代码走**,写成幂等的 `CREATE TABLE IF NOT EXISTS` /
`ADD COLUMN IF NOT EXISTS`,放在用到它的模块里,首次使用时 `_ensure_table()`。
参考写法:`app/routers/settings.py` 的 `_DDL`、
`app/services/cap_group_names.py`、`app/services/cap_item_groups.py`。

`db/migrations/*.sql` 仍然要写,但它的作用是**给全新安装用 + 留档**。
两处必须保持一致,且在 .sql 里注明"已有部署不会执行这个文件"。

**这个坑不报错**:新表没建 → 首个用到它的请求 500,而你以为迁移已经跑过了。

---

## 铁律:`/api/catalog/*` 是**免登录前缀** · 写接口不能挂进去

`app/middleware/auth.py` 的 `_PUBLIC_PREFIXES` 里有 `/api/catalog/`。
它是故意公开的 —— 这个前缀只回答"这套部署能拿到什么数据",不含任何凭证。
中间件对它做的是**可选身份识别**(有 token 就认,没有当匿名,都不拒绝)。

**在这个前缀下加任何写操作 = 谁都能改别人的数据。**
写接口另起一个前缀走默认硬鉴权,例:
`/api/capability-groups`(能力库分组的改名与迁移)。

加公开前缀之前先问自己:这条路径将来会不会长出写操作。

---

## 能力库的「用户自定义分类」· 两张表与三条约定

能力库侧栏那些分组是**算出来的**,不是存下来的:
`catalog.list_capabilities` 把每个 SKILL 的 `hunter.category` 和每个工具的
`category` 聚合起来,组名就是那个字符串。三个来源都不由用户掌控
(工具是 `tool_catalog.py` 里的 Python 字面量;内置 SKILL 写在
`skills/*/SKILL.md`,改了下次 `git pull` 就被覆盖;自装 SKILL 的
category 被无视,一律进「自定义安装」)。

所以自定义分类是**加在上面的两层 per-user 覆盖**,不动上述任何一处:

| 表 | 管什么 | 服务 |
|---|---|---|
| `user_cap_group_name` | 组**叫什么名字** | `services/cap_group_names.py` |
| `user_cap_item_group` | 某个能力**归哪个组** | `services/cap_item_groups.py` |

**三条约定,改这块之前必须知道:**

1. **`category` 永远是稳定 key**。排序(`CATEGORY_ORDER`)、URL 的 `?group=`
   参数、前端筛选全都用它,**不随改名变化**。改名只动 `display_name`。
   这样用户改完名之后,他之前收藏的链接照样打得开。
   前端渲染一律用 `display_name || category`,筛选与 key 一律用 `category`。

2. **`item_key` 故意不做外键**。能力不是库里的行,是从 SKILL.md 与
   `tool_catalog.py` 现算出来的。用户卸掉 SKILL 后留一条孤儿记录,
   分组时查不到自然不生效 —— 无害,而且重新装回来时分类还在。

3. **接口收到的组名可能是"改名后的显示名"**。用户手打一个组名时,
   要先用 `cap_group_names.get_all()` 反查回原始 category,
   否则会新建一个同名组,侧栏出现两行一模一样的字、计数却不同。

改动这块时,`cap_group_names.current_categories(user_id)` 的口径必须和
`catalog.list_capabilities` 的分组口径**逐字对齐** ——
一旦漂了,表现是"改一个明明看得见的组,接口说它不存在"。

---

## 铁律:新功能的入口不许藏在 hover 或二级面板里

2026-09-07 同一个错误犯了两次:

| 做的功能 | 入口放哪 | 结果 |
|---|---|---|
| 分组改名 | 侧栏组名旁的 ✎,`opacity:0`,**悬停才出现** | 用户找不到,报"没做" |
| 能力迁移 | 单击卡片 → 右侧详情面板 → 「类目」行 → 「移动…」 | 用户找不到,报"不能迁移" |

两次后端都是好的、构建也上线了 —— **功能做完但没人能发现,等于没做**,
而且比没做更糟:用户以为你没干活,你以为你干完了,双方都在错误的前提上继续。

**规则**:

- **刚提出的新需求,入口必须常驻可见**。藏进 hover 的前提是"用户已经知道它在那儿",
  新功能不满足这个前提。等它变成老功能、用户形成肌肉记忆之后,再谈收敛视觉噪音。
- **入口放在用户已经在看的那个东西上**。能力库里用户看的是**卡片**和**侧栏**,
  不是右侧详情面板 —— 他不会为了改分类先点开详情。
- 二级面板里的那份可以保留(改完能就地看到结果),但它是**补充**,不是主入口。
- 交付时**说清楚入口在哪、长什么样**,别只说"功能已上线"。

参考实现:`components/GroupPicker.tsx` 一份逻辑两个壳
(`compact` 给卡片、完整版给详情面板)。

**卡片上的控件必须拦事件**:卡片本身 `onClick` 打开详情、`onDoubleClick` 跳对话框、
`onKeyDown` Enter 也跳对话框。按钮 / select / input 三处都要 `stopPropagation`,
否则"点按钮顺带打开详情"、"在输入框按回车直接跳去对话框"。

---

## 铁律:对上游的并发拉数必须有总预算 · 工具卡片 completed 后不许假装还在生成

2026-09-07 茅台事故(复盘:`agentpit/doc/服务器管理/2026-09-07_hunter-community_深度分析超时卡片假计时.md`):
深度分析主拉数 8 路 `asyncio.gather`,finance-data 那几路各自 10s,但 akshare / 用户源那几路
**一个超时都没有**,某一路卡了 137s → uzi_mcp 120s ReadTimeout → 工具返回 `{error}` →
前端卡片显示 `NaNs · 覆盖率 0%` 和一个从 0 起跳的假计时器「已跑 2062 秒仍未出内容」,
而 chat 模型转头凭记忆写了一篇带「营收增速 1.30%」的"分析"。

### 后端:三条

1. **一批并发拉数必须有总预算**,不能指望每一路各自的超时——总有一路没配。
   用 `internal_uzi._gather_budget(named, budget_s, tag)`:到点收工、没拿到的记 `None`
   进 `dims_missing`(空的比假的好,也比永远等下去好)。预算走 env(`UZI_FETCH_BUDGET_S`
   等),三段加起来必须压在 MCP 超时之下(当前 40+30+60=130s < uzi_mcp 170s < opencode 180s)。
2. **分路计时进日志**。事故当时哪一路慢**无法确认**,就是因为没有分路耗时;`_gather_budget`
   打 `各路耗时 {quote: 1.3s, ...}`,下次一眼看出。
3. **同步 SDK 调用(OpenAI / httpx / psycopg2)在 async 端点里必须 `asyncio.to_thread`**,
   并给单次调用设 timeout。原来的 `client.chat.completions.create` 直接在 async 里调,
   LLM 跑多久事件循环就卡多久。

### 前端:两条

4. **富卡片必须认识工具的失败对象**。`ToolCallCard` 按 tool 名分发,工具失败返回的
   `{error, detail}` 也会进同一张卡。每张卡都要有 `failed` 分支:头部写「调用失败」、正文写
   原因和下一步、**不画覆盖率 / 复制按钮这些"有数据"才有意义的东西**;数字字段
   `Number.isFinite` 兜底显示 `—`,不许出现 `NaN`。
5. **卡片只在 tool `completed` 后渲染,所以卡片里不许有计时器、转圈或「仍在生成」的暗示**。
   工具收工后不会再有内容来;计时器显示的只是卡片挂在屏幕上的秒数,与任务无关,
   会让用户干等。失败就明说失败、空就明说空,都是终态。

### MCP:一条

6. **工具失败对象必须带 `type` / `code`,并写一句 `instruction` 明文告诉模型别编**
   (「严禁凭记忆自行撰写分析或给出任何数字」)。`str(httpx.ReadTimeout)` 是空串,
   报错文案要自己写清楚是多少秒未响应。这条挡不住所有情况——真正的硬约束仍是
   §A7「LLM 严禁自由生成数字」:不给模型数据,它就没数字可抄。

### 部署备注

`scripts/opencode-mcp/uzi_mcp.py` 是 huntercode `mcp/uzi_mcp.py` 的**部署副本**,靠 compose
bind mount 覆盖镜像文件(GHCR 拉取在服务器上 denied,机器上没有 gh)。改动先改 huntercode,
再整个拷过来并更新文件头的提交号;不要在副本里单独改。

---

## 铁律:流式转发层不许无条件扣留数据 · 补发必须是合法帧且排在 [DONE] 之前

2026-09-07 事故(复盘:`agentpit/doc/服务器管理/2026-09-07_hunter-community_shim吞掉回答末尾7字符.md`):
chat 每条回答的末尾都少 7 个字符,断在句子中间(实测「…同板块其他高分红/高壁垒资」),
**没有任何报错**,追问建议照常生成 —— 看起来像模型自己没写完,所以它活了很久。

根因在 `scripts/llm-shim/shim.py` 的两处叠加:

1. `ThinkStripper.process()` 为防 `<think>` 被 SSE chunk 切断,**无条件**保留最后
   7 个字符(`out.append(self.buf[:-self.TAIL])`),整条流稳定滞后,指望 `flush()` 补回。
2. 而收尾的补发分支写的是 `pass` —— 注释写着"避免最后一段被吞掉",实现是空的,
   还附了一句方向说反的"tail 通常 0 字节"(因为 1 的无条件扣留,它几乎总是满 7 字符)。

`LLM_SCHEMA_SANITIZE=1` + 模型名含 gemini ⇒ 每次 LLM 调用都过 shim,全量命中。

### 三条规则

- **扣留必须有条件**:只在尾部真的是某个标记的前半截(`<`、`<th`、`</thi`…)时才留那几个字符,
  正文里没有 `<` 就一个字都不滞留。别把正确性 100% 押在收尾补发这一个点上 ——
  那个点写错一次,就是**每条**回答都缺内容。
- **补发要包成合法 `data: {...}` 帧**(外层字段复用上游 chunk 模板),裸文本会被下游
  SSE 解析器当噪音丢掉;**且必须排在 `data: [DONE]` 之前**,OpenAI 兼容 SDK 读到
  [DONE] 就收工。做法:转发时把 [DONE] 那行扣下,写完补发帧再放行。
- **`pass` + 一句"这里应该 X"的注释 = 未完成的代码**。要么 `raise NotImplementedError`,
  要么把"为什么可以不做"论证清楚,别写凭感觉的断言 —— 本次那句错注释让所有
  后来人都跳过了这段。

### 验证方式(重要)

这类 bug 下 **HTTP 200、SSE 帧格式合法、日志干净、页面能用**,全都为真。
`curl` / `node --check` / 看日志一个都发现不了。**唯一手段是端到端逐字节比对
上游发出的和下游收到的**:起一个假上游 SSE 服务 → 过 shim → 比对文本。
凡是"会改写用户可见内容"的中间层(think 剥离 / schema 清洗 / 合规过滤)都要配。

### 排查判据

chat 回答不完整时,先分清是管道还是模型:

| 现象 | 方向 |
|---|---|
| 缺失量**恒定且很小**(每次都差不多几个字) | **管道** —— 转发层的缓冲/扣留 |
| 缺失量**随内容长短变化**、长回答才断 | 模型 `max_tokens` / 额度 |

链路 `LLM 网关 → llm-shim → opencode → web BFF → 前端` **四层都在改写或转发 SSE**,
四层都能悄悄吃掉内容。别一看到"回答不完整"就去调 max_tokens。

### 部署备注

`scripts/llm-shim/` 是整目录 bind mount 到容器 `/app`(镜像是裸 `python:3.12-alpine`),
**改完 `docker compose restart llm-shim` 就生效,不需要也不该 build** ——
本仓"改 python 也要 build"那条规则对它不适用(那条针对 COPY 进镜像的 api/web)。

---

## 铁律:有状态容器必须挂卷 · opencode 的会话正文只活在卷里

2026-09-07 事故(复盘:`agentpit/doc/服务器管理/2026-09-07_hunter-community_opencode会话数据全丢.md`):
为了让新挂的 `uzi_mcp.py` 生效跑了一句 `docker compose up -d opencode`,
挂载配置变了 → compose 判定 **Recreate** → 旧容器删除 → **30+ 条用户会话正文全部消失,不可恢复**。
当时 opencode 服务的 volumes 全是 `:ro` 的配置文件,没有一个卷存数据。

### 三条

1. **动 `docker compose up -d <svc>` 之前,先问这个容器有没有状态**。
   compose 只要发现镜像、环境变量或挂载有变化就会 Recreate,可写层跟着没。
   `restart` 不换容器所以安全,`up -d` 不是。**有状态而没挂卷 = 一次 up -d 就清零。**
2. **`chat_session_owner` 表不是备份**。它只存 `session_id ↔ user_id ↔ title` 归属映射,
   对话正文一个字都不在里面。前端会话列表 = opencode 全量会话 ∩ owner 表,
   opencode 那一侧没了,表再完整前端也是"暂无对话"。
   看到 postgres 数据完好**不等于**数据没丢,要去数据真正所在的那一侧确认。
3. **opencode 的卷必须挂 `/home/hunter/.local` 整个目录**,不要图精确挂
   `.local/share/opencode`。镜像里根本没有 `.local`(实测 `docker run --rm ... ls -ld` 报
   No such file),docker 会**以 root** 补建 `.local` 与 `.local/share` 两级父目录,
   容器以 `hunter`(1001) 跑,启动时建 `.local/state` 直接 EACCES 重启循环。
   父目录不在卷内,进临时容器 chown 没用 —— 每次 recreate 由 docker 重造,必现。
   挂 `.local` 之后 `state` 与 `share` 都在卷内,而**具名卷根目录属主持久**,
   `chown -R 1001:1001` 一次长期有效。

### 挂了卷不等于状态都进卷了 · 用 df 逐个查

首轮修复只把会话数据挪进卷,`HUNTER_AUDIT_PATH` 还指着 `/tmp/hunter-audit/`,
**`/tmp` 同样是容器可写层**,所以审计日志在这次事故里跟会话正文一起没了 ——
而审计日志的全部意义就是留痕,放在会被 recreate 清掉的地方等于没有审计,
事后想查"到底谁跑了什么"时它正好不在。已改指卷内(`2cb19a4`)。

**别只看 compose 的 volumes 段**(它只说明"挂了什么",不说明"进程往哪写"),
直接去容器里问:

```bash
docker inspect <容器> --format '{{range .Mounts}}{{.Type}} {{.Destination}} {{if .RW}}rw{{else}}ro{{end}}
{{end}}'
docker exec <容器> sh -lc 'df <可疑路径>'     # overlay = 可写层,recreate 就没
```

本仓 6 个服务全量扫过,没有第三处:api 的两个 rw 挂载是 bind 到宿主机目录(重建不丢),
web / llm-shim 全 `:ro` 无状态,postgres / redis 各有具名卷。

### 读 opencode 的 sqlite 必须连 WAL 一起读

`opencode-local.db` 是 WAL 模式,`-wal` 文件常有几 MB 未 checkpoint。
**用 `readonly` 打开可能只读到主库、得出"表是空的"这种错误结论** ——
排查这次事故时据此误判过一次"数据全空"。两种正确读法:

```bash
# A. 连 -wal / -shm 一起复制出来再读
# B. 取一致性快照(自动合并 WAL),也是推荐的备份手法
docker exec <opencode> bun -e "new (require('bun:sqlite').Database)(process.env.HOME+'/.local/share/opencode/opencode-local.db',{readonly:true}).exec(\"VACUUM INTO '/tmp/bk.db'\")"
```

同理:**看 postgres 的 `chat_session_owner` 完好,不等于会话还在**(见上面第 2 条),
对话正文只在 opencode 那个 sqlite 里。

### 顺带两条操作纪律

- **改生产前确认 `git pull` 真的成功**。有一次 pull 因网络抖动失败,后续步骤基于旧
  compose 执行,容器 `stop` 后停在那里,服务中断。停服务的操作必须有"无论如何都起回来"的收尾。
- **`docker compose run --rm` 起的临时容器只共享卷**,可写层与目标容器无关。
  在里面 chown 非卷路径是无效操作,看着成功其实没生效。

---

## 详细文档

完整问题清单与实施记录(在 agentpit repo 内,不在本仓):

- `agentpit/doc/开源hunter-community/01详细工作目录/11量化策略/17_20260818_回测可信度问题与修复方案.md`
- `agentpit/doc/开源hunter-community/01详细工作目录/11量化策略/18_20260818_回测可信度修复实施记录.md`
