# 开源金融 SKILL 复用边界与许可证安全清查表（SBOM）

> **文档版本：** V1.0（M0 基线发布版 `v3.0.2`）  
> **制定日期：** 2026-11-13（上海时间）  
> **适用仓库：** `hunter-community`、`huntercode-atomcode`（GitHub / GitCode）、`huntercode`（独立业务仓）  
> **核心原则：** **只借鉴成熟的研究方法论、章节结构与确定性校验逻辑；绝不盲目整包照搬、绝不越界使用无授权的第三方 Vendored 内容、绝不混入境外持牌投顾话术或未授权专有数据源。**

---

## 一、大白话先说清：为什么要立这张安全清单？

在建设 HunterCode × 证券公司接口与金融技能体系（22 个核心金融 SKILL）的过程中，开源社区已经有许多优秀的金融分析 Skill 和量化研究项目。合理借鉴它们可以让我们少走弯路，但如果“不管三七二十一直接复制粘贴”，会带来三个致命风险：

1. **法律与开源许可证传染风险**：有些仓库虽然根目录挂着 `MIT` 许可证，但里面塞了几百个从别处拷来的第三方文件（Vendored 目录），那些文件的真实许可证可能是禁止商用或强制开源传染的。
2. **数据源与账号强绑定风险**：有些开源 Skill 里面硬编码了海外券商（如 Alpaca、FMP）或特定平台（如 Longbridge、agentii.ai）的专有 CLI 命令和付费 API，直接拷过来在国内 A 股/港股环境根本跑不通。
3. **金融合规越界风险**：部分海外开源 Skill 带有“自动下单执行”或境外持牌投顾（如新加坡 FA、美国 SEC 监管口径）的投资建议话术，直接照搬会违反中国大陆证券合规红线。

因此，我们在 **M0 阶段** 先把所有参考开源项目的**许可证底细、哪些能用、哪些严禁碰、怎么改造**全部立成铁规矩，形成本《开源金融 SKILL 复用与许可证清单（SBOM）》。

---

## 二、核心参考开源 SKILL 仓库合规审查与复用边界总表（SBOM）

| 序号 | 开源项目 / 仓库地址 | 根目录许可证 | 许可证核验结论 | 允许借鉴与复用的内容（白名单） | 严禁直接复制的内容（黑名单 / 隔离区） | HunterCode 对应落地模块 |
|---:|---|---|---|---|---|---|
| 1 | **Longbridge Skills**<br>`https://github.com/longbridge/skills` | MIT | 原创 Skill 文本与结构遵循 MIT，可合规改造复用（需保留版权声明） | 13 个领域的技能分层思路、行情/技术面/基本面/组合/财报的章节编排框架、触发词设计 | 严禁复制 Longbridge 专属 CLI/SDK 调用命令与专有字段；全部底层取数必须替换为 Hunter `broker_*` MCP 与 `BrokerDataEnvelope` | `technical_dashboard`、`fundamental_scorecard`、`earnings_review`、`etf_dca_planner` |
| 2 | **kangcodex/finance-skills**<br>`https://github.com/kangcodex/finance-skills` | MIT | 根目录及 5 个核心 Skill 均为 MIT，附带确定性脚本设计思路清晰 | `news-rss-watch` 的水位线（Watermark）去重、跨源事件聚类与重要性打分机制；`daily-market-watch` 的晨报结构；交叉核验流程 | 严禁引入 `sg-financial-advisor`（新加坡持牌财务顾问制度条款）；剥离 `smart-money-tracker` 中仅针对美股国会议员交易（STOCK Act）的硬编码逻辑 | `news_signal_digest`、`daily_market_brief` |
| 3 | **dr-pabs/Agent-claude-trading-skills**<br>`https://github.com/dr-pabs/Agent-claude-trading-skills` | MIT | 根仓库 MIT，定位于“非自动交易”的计划、风控与复盘闭环，理念高度契合 | 每日市场检查、候选股筛选前风险门禁（Risk Gate）、交易前计划-交易后复盘日志闭环、每周/月度组合回顾模板 | 严禁复制其针对美股 Alpaca / FMP 的 API 调用脚本；必须适配 A/H 股 T+1、涨跌停与申万行业分类口径 | `trading_journal`、`after_close_review`、`portfolio_risk_check` |
| 4 | **Serennity007/awesome-stock-quant-skills**<br>`https://github.com/Serennity007/awesome-stock-quant-skills` | **混合许可**<br>（原创部分 MIT；含 16 个第三方目录共 225 个 vendored 文件） | **高风险隔离审查**：根目录 MIT **仅覆盖作者原创的 31 个 Skill 与 33 个脚本**，不能自动覆盖 225 个第三方 vendored `SKILL.md` | **仅限原创 MIT 部分**：A 股因子有效性检验（IC/IR/分层回测）、A 股印花税/过户费/滑点成本建模、涨跌停与停牌过滤检查表 | **严禁整包复制或批量导入 16 个第三方 vendored 目录（225 个文件）！** 任何第三方子目录未经逐文核验原始作者、原始 LICENSE 与商用许可前，100% 列入隔离黑名单 | `factor_research`、`backtest_quality_auditor`、`stock_screener_builder` |
| 5 | **agentii-ai/agentii-investment-intelligence**<br>`https://github.com/agentii-ai/agentii-investment-intelligence` | Apache-2.0 | 与 HunterCode 主许可证（Apache-2.0）天然兼容（需保留 NOTICE 与变更说明） | 渐进式文档检索（先目录后页码）、三表勾稽核对清单、研究报告原文页码引用规范、多情景估值假设表格结构 | 严禁依赖其 `agentii.ai` 商业托管数据平面与专有 MCP 接口；所有财报与公告必须走本地 PDF 解析器与 `broker_disclosure` / `broker_fundamental` | `announcement_impact`、`earnings_review`、`investment_thesis_tracker` |
| 6 | **TerminalSkills/skills (`trading-agents`)**<br>`https://github.com/TerminalSkills/skills` | Apache-2.0 | Apache-2.0 兼容，可借鉴多角色协作流程 | 基本面、技术面、新闻面、牛熊辩论、风控审查的角色拆分与汇总结论格式 | 严禁将其改造成“自动生成实盘买卖指令”的交易机器人 | `investor_panel`（升级）、`uzi`（总调度升级） |

---

## 三、关联量化与多智能体开源框架借鉴边界（非直接安装型 SKILL）

以下项目属于底层 Python 量化计算框架或多智能体工程参考，**不能当作 `SKILL.md` 直接装进工作区**，仅在后端确定性计算引擎（`apps/api/app/services/`）设计时参考其公开算法标准：

| 项目名称 | 仓库地址 | 许可证 | 借鉴范围 | 边界限制 |
|---|---|---|---|---|
| **TauricResearch/TradingAgents** | `https://github.com/TauricResearch/TradingAgents` | Apache-2.0 | 多角色研究员（Bull/Bear）辩论机制与研究经理综合裁决结构 | 仅借鉴工作流编排思想，所有数据必须绑定 `BrokerDataEnvelope` 真值，禁止无证据空谈 |
| **Microsoft Qlib** | `https://github.com/microsoft/qlib` | MIT | 因子表达式标准、Point-in-Time（PIT）历史截面数据对齐理念、IC/RankIC 评估指标定义 | 不整包强依赖 Qlib 重型运行时，由 HunterCode 轻量级确定性计算引擎独立实现 |
| **AI4Finance FinRL** | `https://github.com/AI4Finance-Foundation/FinRL` | MIT | 回测环境中的交易成本、滑点与最大回撤统计口径 | 强化学习自动交易模型不纳入股民日常投研默认技能，更不得连接实盘 |
| **Microsoft RD-Agent** | `https://github.com/microsoft/RD-Agent` | MIT | 因子假设生成 → 回测验证 → 反馈迭代的科研闭环思路 | 必须受 Token 预算闸门（`budget` Hook）与人工确认约束，防止后台无限循环跑回测 |

---

## 四、`Serennity007/awesome-stock-quant-skills` 专项隔离审查规程

由于该仓库体量较大且混合了原创内容与第三方收集内容，特制定以下 **三步隔离硬规程**（将在 M5 阶段固化进 `tools/skill_security_scanner.py` 自动化安检工具）：

1. **目录级白名单准入**：
   - 仅允许参考该仓库明确标注为作者原创（MIT License）的 31 个 A 股量化/因子/风控方法论设计；
   - 对仓库内的 16 个第三方汇总目录（合计 225 个 vendored `SKILL.md`）实行**默认拒绝（Default Deny）**策略。
2. **逐篇重写与本土化清洗**：
   - 即便是白名单内的原创 MIT 技能，也不采用原文件直接拷贝，而是按照 HunterCode 的统一规范重写为适配 `broker_*` MCP、强制输出【已核实事实 / 逻辑推断 / 待核实问题】三栏结构、并内置“算不出报 `null` 禁填 `0`”规则的标准 `SKILL.md`。
3. **强制署名与许可证留痕**：
   - 凡吸收了上述 MIT / Apache-2.0 开源项目思路的 SKILL，在其 `SKILL.md` 的 frontmatter `metadata.references` 或项目 `NOTICE` 文件中如实保留致谢与许可证说明。

---

## 五、外部 SKILL 引入的五条安全与合规红线（准入检查表）

任何新增或升级的金融 SKILL 在通过 `python3 tools/build_skills.py --check` 并合入主干前，必须 100% 满足以下五条红线：

1. **许可证清晰兼容**：仅允许 `MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause` 四类宽松开源许可证；严禁引入 `GPL`、`AGPL`、`CC-BY-NC`（禁止商用）或无许可证声明的野生文件。
2. **零硬编码密钥与零外网私连**：`SKILL.md` 及附属 `references/` 中严禁包含任何 API Key、Token、私有域名或引导大模型执行 `curl` / `wget` / `bash` 下载外部脚本的指令。
3. **数据源 100% 走受控 MCP**：所有行情、财务、公告、基金数据必须通过已注册的 12 个既有 MCP 或 8 个新增 `broker_*` MCP 获取，严禁让大模型凭训练记忆心算价格、涨跌幅、财务比率或技术指标。
4. **严禁实盘指令与违规荐股词**：严禁出现“自动下单”“满仓买入”“稳赚不赔”“保底收益”“必涨停”等违规诱导词；所有输出模板末尾必须自带标准免责声明（“仅供研究参考，不构成任何投资建议”）。
5. **诚实停止原则（Fail-Closed）**：当底层 MCP 返回 `ok=False`、数据源断连或核心字段为 `null` 时，SKILL 必须明确指导模型立即停止编造并如实报告缺失原因，绝对禁止用 `0` 或假数据填空。
