# 同花顺 与 通达信 官方 MCP · 实测对照:API Key 全链路验证

> 2026-09-12
> 上游:老板在与 @云清 的对话中定的三条原则 —— ①必须走官方、②用户自己申请 key、③我们代码不集成 key
> 配套:`20260912-通达信MCP_实测调研_三条路线与压力测试.md`(通达信那条已建议关闭)
> 本文只做一件事:**用真 key 把两家链路各跑一遍**,不做二手转述。
> 原名《同花顺金融数据_实测调研》,9-12 深夜通达信官方 MCP 真连后改为两家对照,故改名。

---

## 0. 一句话结论

**通了。官方有 MCP、有 API Key 自助签发、数据实测准确、限流宽松。**

**唯一缺口:387KB 全文文档里搜不到任何服务条款、商用说明、定价** —— 而这恰恰是老板最在意的那一条。

---

## 1. 老板三条原则的逐条核对

| 老板要求 | 同花顺 | 通达信(对照,9-12 深夜已订正) |
|---|---|---|
| 必须是**官方**的 | ✅ 硬证据见 §2 | ✅ 端点写死在 TdxClaw 客户端里:`txmcp.tdx.com.cn:3001/clawmcp` |
| **用户能自己申请 key** | ✅ 登录→签发→用,三步,未见收费 | ✅ `vip.tdx.com.cn` 商城自助创建,**付费积分制**(老板 188 元) |
| **我们代码不集成 key** | ✅ 天然支持:`X-api-key` header / 环境变量 | ✅ 天然支持:`Authorization: Bearer TDX-…` / 环境变量 `TDX_MCP_KEY` |
| 后台与界面支持选择 | ✅ 可做(6 个独立 MCP 端点,天然可选) | ✅ 可做(单端点 20 工具) |

> 通达信列此前写的「❌ 没有申请入口 / 全是个人逆向」是错的,详见
> `20260912-通达信官方MCP_最终定论_真连成功与鉴权验证.md`。两家现在**在这三条原则上完全对等**。

---

## 2. 身份验证 —— 这是同花顺官方,有硬证据

先说疑点:GitHub 账号 `HiThink-Tech` 是**个人账号不是组织**,2026-06-09 注册、1 个仓库、89 粉丝、公司/简介/邮箱全空。单看这个像个人项目。

**但两条证据把身份钉死了:**

1. 文档站加载 `s.thsi.cn/js/chameleon/...`
2. **全市场数据导出的预签名链接落在 `o.thsi.cn`**

`thsi.cn` 是同花顺的官方资源域名。**第三方伪造不了预签名 URL 的主机。**

---

## 3. MCP 实测 —— 真当客户端连了一次

不是"文档说有 MCP",是**直接说 JSON-RPC 协议连上去**:

```
① initialize
   ✅ 服务端 fuyao-a-share-mcp 1.0.0 · 协议版本 2025-06-18
      Session-Id: a3ca2769-dc91-4bd5-bf3d-1c6e300d0e3d

② tools/list
   ✅ 21 个工具

③ tools/call  get_a_share_prices_snapshot {"thscodes":"600519.SH"}
   ✅ {"code":0,"data":{"item":[{"thscode":"600519.SH","last_price":1275.16,
      "open_price":1285.15,"high_price":1286.15,"low_price":1263.01,...}]}}
```

### 6 个托管 MCP Server(不用安装,直接连 URL)

```
fuyao-a-share-mcp        https://fuyao.aicubes.cn/mcp/a-share
fuyao-a-share-index-mcp  https://fuyao.aicubes.cn/mcp/a-share-index
fuyao-fund-mcp           https://fuyao.aicubes.cn/mcp/fund
fuyao-futures-mcp        https://fuyao.aicubes.cn/mcp/futures
fuyao-options-mcp        https://fuyao.aicubes.cn/mcp/options
fuyao-meta-mcp           https://fuyao.aicubes.cn/mcp/meta
```

鉴权:网关校验 `X-api-key` header。**和我们自己发的 `kronos-mcp` / `truesource-mcp` 是同一个模式** —— 用户自带 key、代码里零密钥。

a-share 那个的 21 个工具(前 10):

```
get_a_share_prices_snapshot            get_a_share_prices_historical
get_a_share_corporate_actions_adjustment_factors
get_a_share_financials_income_statements
get_a_share_financials_balance_sheets  get_a_share_financials_cash_flow_statements
get_a_share_financials_indicators      get_a_share_valuations_snapshot
get_a_share_calendar_trading_days      get_a_share_special_data_limit_up_pool
```

---

## 4. API Key 流程(用户自助,官方原文)

```
调用同花顺金融数据接口只需要 3 步:登录 → 签发 API Key → 携带 X-api-key 调用

1. 使用同花顺账号登录文档站,回跳「API Key 管理」页
2. 点「创建 API Key」,填个别名(如 my-dev-key)
3. 请求头带 X-api-key: <your-api-key>

MCP 接入:鉴权复用同一把 key,通过环境变量 API_KEY 注入
```

**API Key 与同花顺账号绑定,可在管理页查看和管理。**

---

## 5. 数据实测

### 5.1 准确性 —— 三方对账一致

| 标的 | 同花顺 | eltdx(通达信协议) | 腾讯 `qt.gtimg.cn` |
|---|---|---|---|
| 600519 茅台 | 1275.16 | 1275.16 | 1275.16 |
| 000001 平安银行 | 11.74 | 11.74 | 11.74 |

**三个独立来源完全一致。**

### 5.2 ⭐ 龙虎榜 63 条 —— 正好是我们最薄的那张表

我们库里 `a.lhb` **只有 17 只有记录**(走 akshare)。同花顺一次返回 63 条,且**机构榜与游资榜分开**:

```
data.stock_items      (个股榜)
data.hot_money_items  (游资榜)

首条:中百集团 000759 · concept_list[免税店/农业种植/预制菜] · change · net_value
```

### 5.3 ⭐ 全市场 10 年日 K:172.2 MB Parquet

```
GET /api/dump/market-dumps/daily-k/download-url
→ presigned_url(300 秒有效) · 主机 o.thsi.cn
→ Range 取前 4 字节 = "PAR1"  ✅ 真 Parquet
→ Content-Range 显示完整文件 172.2 MB
```

另有 `/adjustment-factors/download-url`(复权因子)。

**这对因子广场是降维打击**:现状是几千只票逐个拉、`akshare_client.py` 15 秒超时反复重试;换成下一个文件读一遍。

### 5.4 其它已验证

- 同花顺热榜 30 条(风华高科 rank 1,heat 5867772)
- 涨停池 / 炸板池 / 异动原因:接口通(`code=0`),当日 0 条

### 5.5 限流梯度 —— 宽松,零 429

| 并发 | 速率 | 延迟中位 | 返回码 |
|---|---|---|---|
| 1 | 0.5/秒 | 1717ms | 全部 `code=0` |
| 4 | 2.1/秒 | 1700ms | 全部 `code=0` |
| 8 | 3.3/秒 | 1684ms | 全部 `code=0` |

**120 次请求零限流零异常。** 延迟 ~1700ms 是境外访问 + 本地代理造成的,国内服务器上会快得多。

文档明写:**「本服务当前不限制累计调用次数」**,触发限流才返 HTTP 429 或 `code=4001`。

---

## 6. ⚠️ 三个口子

### 6.1 🚨 最关键:找不到任何服务条款

387KB 全文文档里搜索:

```
服务条款 / 使用协议 / 商用 / 商业用途 / 转售 / 再分发 / 免费 / 计费 / 价格 / 收费
→ 全部 0 命中
```

所有"价格"命中都是股票价格。

**能注册、能拿 key、能用,但「能不能商用」没写在任何地方。**

这正是老板要的那一条:

> 不是我们要用这个功能,是让用户用,**所以不能给用户提供这种违法的借口**

**待办:回 `fuyao.aicubes.cn`「API Key 管理」页找用户协议链接;若注册时没让勾任何协议,直接问客服。**
重点看有没有这两句之一:**禁止转售/再分发数据**、**仅限个人/非商业使用**。

### 6.2 部分能力返回 `2004` —— 是"还没上线",不是"没权限"

```
/api/a-share/capital-flow/snapshot  → code=2004
```

`2004` 在 387KB 文档里查不到(只定义了 `2001 未认证` / `2003 权限不足`)。

**但文档接口总览里写了答案:**

> 主力资金 | `/api/a-share/capital-flow` | A 股主力资金实时快照与历史数据,**计划在后续版本接入同花顺AI客户端,敬请期待**

以及:

> 部分数据与分析能力计划在后续版本接入同花顺AI客户端,**当前版本暂不可使用本项目数据**

**所以 `2004` = 能力尚未开放,不是付费墙。** 这个判断比"需要升级套餐"乐观,但仍是推测 —— 错误码未文档化。

### 6.3 ⚠️ 我自己的测试缺陷:批量没测准

```
10 只 → 返回 8 条
50 只 → 返回 8 条
100 只 → 返回 8 条
```

看着像批量上限是 8。**其实是我的构造有问题** —— 我只用了 8 个不同代码重复了 N 遍,服务端做了去重。

**批量能力实际上限未知,要用 100 个不同代码重测。** 这一条不能当结论用。

---

## 7. 与通达信的对照(两家都已真连,9-12 深夜更新)

> 本节原版把通达信写成「无官方 MCP、无申请入口」,**是错的**。拆开 TdxClaw 安装包后找到了
> 写死的官方端点并用真 key 连通,详见 `20260912-通达信官方MCP_最终定论_真连成功与鉴权验证.md`。

| | 同花顺 | 通达信 |
|---|---|---|
| 官方 MCP 端点 | `https://fuyao.aicubes.cn/mcp/a-share` 等 **6 个** | `https://txmcp.tdx.com.cn:3001/clawmcp` **1 个** |
| 传输 | streamable-http | streamable-http |
| 鉴权头 | `X-api-key: <key>` | `Authorization: Bearer TDX-<key>` |
| 无 key / 假 key | 拒绝 | **401**(实测:无 key「Missing Bearer authorization」,假 key「Unauthorized access」) |
| 真连结果 | `initialize` 200 · 21 工具 · `tools/call` 茅台 1275.16 | `initialize` 200 · `tdx-finance-mcp-server 1.0.0` · **20 工具** · `tdx_quotes` 茅台 1275.16 |
| key 获取 | 文档站登录三步签发,未见收费 | 商城「积分和Key管理」自助创建,**付费积分制** |
| 新闻/公告/研报/宏观 | ❌ 文档明写暂不提供 | ✅ `wenda_news/notice/report/macro_query` 四个,实调资讯 634ms 返真数据 |
| 全市场批量导出 | ✅ 172MB Parquet 10 年日 K | 未见 |
| 官方 Skill | 无 | ✅ SkillHub 46 个(`tdx.com.cn/skillhub`),ZIP 内只有 SKILL.md,依赖上面 10 个工具 |
| 数据准确性 | ✅ 三方一致 | ✅ 三方一致 |
| **商用/转售条款** | ⚠️ **未见** | ⚠️ **未见** |

### 7.1 用 key 使用 MCP / Skill 的统一模式(两家一样)

两家的官方 MCP 都是**托管远程服务 + 用户自带 key**,和我们自己发的 `kronos-mcp` / `truesource-mcp` 是同一个模式。只有两处细节不同:

```
                     同花顺                          通达信
MCP 端点             fuyao.aicubes.cn/mcp/*          txmcp.tdx.com.cn:3001/clawmcp
鉴权 header          X-api-key: <key>                Authorization: Bearer TDX-<key>
环境变量(客户端)   API_KEY                         TDX_MCP_KEY(TdxClaw 里叫 tdxMcpKey)
Skill 层             —                               SkillHub ZIP 只有提示词;工具由上面的 MCP 提供
```

**Skill ≠ MCP。** 通达信 SkillHub 的 46 个技能是纯 SKILL.md,ZIP 里没有任何服务器代码;
它们靠 `tdx_api_data` / `tdx_quotes` / `wenda_*` 等 10 个工具工作,而这 10 个工具由官方 MCP 端点提供。
**没有 key 连不上 MCP,Skill 就全部空转。** 这就是老板说「Skill 只是壳,数据工具才是真正要接的层」的精确含义。

对我们平台的接法(两家共用一套):

1. 界面上「数据源」里加两个可选项,各一个 key 输入框(明显位置,老板要求)
2. 后端按用户选的源,把对应 header 挂到对应端点,**我们的代码里零 key**
3. 用户没填 key → 按「空的比假的好」返明确错误 + 申请入口链接,不用我们的 key 兜底

⚠️ 注意通达信有**两条**能取数的路,只有一条合规:

```
官方 MCP   txmcp.tdx.com.cn:3001/clawmcp   严格鉴权    ← 走这条
内部端点   tdxhub.icfqs.com:7615/TQLEX      不鉴权      ← 社区插件绕过网关直打的,不能用
```

两条数据同源(茅台 1275.16 完全一致),差别只在鉴权网关。**接官方 MCP,不接 TQLEX。**

**两家现在都到了同一步:技术全通、条款空白。** 唯一阻塞项只能靠人问(见 §8.1)。

### 7.2 通达信官方 MCP:「官方提供」的证据链,与能拿到的金融数据(9-12 深夜实测)

**为什么能断定是官方提供的 MCP(四条,全是实测不是推断):**

1. **端点写死在官方客户端里** —— TdxClaw 1.0.31 主进程 `const ol = "https://txmcp.tdx.com.cn:3001/clawmcp"`,
   域名 `tdx.com.cn`;客户端里另有域名白名单只认 `*.tdx.com.cn` / `*.icfqs.com`
2. **服务端自报身份** —— `initialize` 返回 `server = tdx-finance-mcp-server 1.0.0`,协议 `2025-06-18`
3. **鉴权是真的** —— 商城买的 key 通;无 key 401「Missing Bearer authorization」;假 key 401「Unauthorized access」;
   OAuth 元数据 `authorization_servers: ["https://auth.tdx.com.cn"]`
4. **数据是真的** —— 茅台 1275.16 与腾讯 / 同花顺 / eltdx 三方一致

创建 key 时勾的「问小达MCP」权限,对应的就是这个端点。官方文档**没公开这个地址**(只教在 TdxClaw 里填 key),
但 key 创建页明写「也可基于 MCP 协议自行开发集成」—— 第三方直连是允许的用法,只是没写文档。

**用这把 key 能拿到的金融数据(`tools/list` 20 个,分两类):**

```
数据类(17)—— 服务器端可用
  tdx_quotes                 实时行情 / 五档              ✅ 已验 茅台 1275.16
  tdx_kline                  K 线(分钟~月线,含复权)     ✅ 已验 100 根
  tdx_api_data               F10:财报/股东/资金流向/龙虎榜/分红/股本 30+ 子模块
  tdx_lookup_stock           代码/名称检索(A/港/美/基金/指数)
  tdx_screener               自然语言选股                ✅ 已验 42 条
  tdx_indicator_select       指标查询 / 行业估值对比
  tdx_security_deep_info     个股深度资料(自然语言)
  tdx_futures_quotes / tdx_futures_deep_info   期货
  tdx_option_t_quote         期权 T 型报价
  tdx_technical_indicator_lookup / _query      技术指标
  tdx_ai_listening
  wenda_news_query           资讯                        ✅ 已验 634ms
  wenda_notice_query         公告
  wenda_report_query         研报
  wenda_macro_query          宏观(要「管道格式」query)
客户端 UI 类(3)—— 只在 TdxClaw 里有意义,服务器端用不上
  tdx_present_research_ui  tdx_open_data_function_panel  tdx_add_favorite
```

**`tdx_kline` 实调记录:**

```
▶ tdx_kline {code: 600519, setcode: 1}
【贵州茅台】600519 | 现价 1275.16 (-0.78%) | K线数量: 100 根
区间最高 1294.99 · 最低 1263.01
ItemHead: Data / Second / Open / High / Low / Close / Amount / VolInStock / Volume / Settle / up / down
AttachInfo: Name / HqDate 20260911 / Close 1285.13 / Open / MaxP / MinP / Volume / Amount / fHSL(换手) / lBelongHY(行业)
```

**`tdx_kline` 参数(取自工具定义原文):**

| 参数 | 取值 |
|---|---|
| `code` | 纯数字证券代码 |
| `setcode` | `1` 沪市(6/68 开头)· `0` 深市(00/30)· `2` 北交所(43/83) |
| `period` | `0` 5 分钟(默认)· `1` 15 分 · `2` 30 分 · `3` 1 小时 · **`4` 日线** · `5` 周线 · `6` 月线 |
| `wantNum` | 1~1000,默认 100;日线一年约 250 |
| `startxh` | 起始偏移(倒序),0 = 最新;用于分页拉历史 |
| `tqFlag` | `0` 不复权 · **`1` 前复权(默认)** · `2` 后复权 |
| `hasIpoPrice` | 是否附带发行价,默认 0 |

⚠️ 我实调时传了 `period: "day"`,不是合法值,服务端落回默认的 5 分钟线(行里 `Second: 53100` 可证)。**要日线传 `"4"`。**

**积分消耗:** 截至本节,官方 MCP 累计调用约 10 次(initialize×4、tools/list×3、tools/call×5)。
单次扣多少积分我这边看不到,只能由老板在商城「Key使用流水」核对 —— 见 §8.1。

---

## 8. 下一步

### 8.1 只能你/老板做的(两家各一件 + 一件共同的)

| 谁 | 事 | 为什么 |
|---|---|---|
| 同花顺 | 回 `fuyao.aicubes.cn`「API Key 管理」找用户协议,截图 | 387KB 文档零条款 |
| 通达信 | 商城购买页 / Key 创建页找服务协议;`auth.tdx.com.cn` 是 OAuth 授权服务器,可能有政策页 | 同样零条款 |
| 通达信 | 看商城「**Key使用流水**」:我 9-12 深夜真连约 6 次(initialize×3、tools/list×2、tools/call×4),核对扣了多少积分 → 单次成本 → 188 元能撑多少次 | 积分制,成本要先算清 |

三处都盯同三个词:**「转售」「再分发」「商业用途」**。这是两条路共同的、也是唯一的合规缺口。

### 8.2 条款过关之后可以做的

按老板给的顺序:

1. ✅ **我们申请 key、测通接口** —— 本文已完成
2. **代码调试通过** —— 加 `provider="ths"` 分支,用户填自己的 key
3. **界面明确展示与设置入口**(老板强调两遍「明显位置」)
4. **发布 readme 和自媒体**

⚠️ **铁律:我们那把 key 只用于开发调试。不进仓库、不进镜像、不替用户调。**
一旦用我们的 key 替用户取数,就是转售数据 —— 正是要避开的事。

### 8.3 技术上最值得先做的

**不是替换行情,是接 `market-dumps`。**

172MB Parquet 一次拿全市场 10 年日 K,直接解决:

- `quant/akshare_client.py` 15 秒超时反复重试
- 因子广场每日 17:00 那一轮的稳定性
- 我们 9-11 刚踩的「批量拉日线触发腾讯 WAF 封 IP」—— **下文件不会被封**

---

## 附:测试环境与方法

- 本地 Docker(`python:3.11-slim`)+ 本机 Python,**未使用任何生产服务器 IP**
- MCP 测试:不依赖 SDK,**直接发 JSON-RPC**(`initialize` → `tools/list` → `tools/call`),
  这样验证的是端点本身而非某个 SDK 版本
- 限流测试:分级加压(1/4/8 并发),见异常即停
- 交叉验证源:腾讯 `qt.gtimg.cn` + eltdx(通达信协议)
- 使用的 key 为用户自行在同花顺官网签发的开发用 key
