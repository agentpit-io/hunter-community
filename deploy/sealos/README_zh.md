# HunterCode

[English](./README.md)

自部署的 AI 投研助手，覆盖 A 股 / 港股 / 美股。用你自己的大模型 key ——
行情、对话和 key 本身全部留在你自己的实例上。

## 它能做什么

| | |
|---|---|
| **和分析师对话** | 用大白话问一只票，模型去调真实数据工具，而不是凭记忆报数字 |
| **深度分析** | 一只票一份多段报告（多空观点、技术面、财务、公告、新闻），并显示**覆盖率** —— 哪几路数据没取到是写明的，不会拿空的冒充有的 |
| **魔法筛选器** | 用大白话或 ThinkScript 风格语法写条件，全市场跑一遍 |
| **自选股与组合** | 持仓、研究线、提醒 |
| **SKILL** | 从 GitHub 装方法论包，模型回答前会先读 |

## 部署完成之后

1. **先把部署表单里的「初始化口令」复制下来再提交** —— 首启向导第 0 步要填它。
   （它防的是：部署完成到你第一次打开页面之间，别人抢先进来把大模型配成他自己的。）
2. 打开应用地址，会自动进入**首启向导**。
3. 输入口令 → 环境自检 → 选大模型厂商 → 粘贴你自己的 API key。向导会**由 api
   容器当场发起三项检测**（连通 / 真实对话 / 真实工具调用），各显示真实耗时；
   **测不通不让保存**。
4. 选数据供给，完成。配置写进数据库并**热生效，不用重启容器**（实测几秒内就绪）。
5. 注册第一个账号，它自动成为管理员。

全程**不需要编辑任何文件**。

## 你需要自备什么

- 一把 OpenAI 兼容的大模型 API key。DeepSeek / 通义 / Moonshot / OpenAI /
  Anthropic / 自建网关都行。模板**刻意不在部署前要求你填** —— 在向导里填，
  填完当场测，测通了才存。
- 可选：去 <https://hunter.agentpit.io/dev/api-keys> 免费申请一把 HunterCode
  平台 key，解锁实时行情和全部工具。不填也能用 —— 没有它时助手会**如实说
  「暂未配置 Hunter Key，无法获取实时行情」**，而不是编一个价格出来。

## 资源建议

以下是六个核心服务整栈的实测值（见项目的 R0 预研结论；智能交易的四个服务
paper / temporal / temporal-ui / fin-worker 另加约 +0.6 GB，temporal 占大头）：

| | |
|---|---|
| 空闲 | 六个容器合计 1171 MB |
| 峰值（深度分析 + 全市场扫描并发） | **1271 MB** |
| 最低 | 2 核 3 GB |
| 推荐 | 2 核 6 GB |
| 磁盘 | ≥ 10 GB（镜像约 4 GB + 数据） |

内存大头是 `opencode`（约 869 MB），而且**几乎与负载无关** —— 它是常驻占用，
不是峰值风险。

## 数据放在哪

| 内容 | 位置 |
|---|---|
| 自选股、组合、研究台、配置、**大模型 key 密文** | `-pg` 的 PostgreSQL |
| 对话正文与会话 | `-opencode` 的 PVC，挂在 `/home/hunter/.local` |
| 你装的 SKILL、导入的数据包 | `-api` 的两个 PVC |

> ⚠️ PostgreSQL 里的 `chat_session_owner` 表**不是对话备份** —— 它只存
> session_id ↔ user_id 的归属映射，对话正文一个字都不在里面。正文只在
> opencode 那个卷上。

## 升级

把四个 `ghcr.io/agentpit-io/hunter-community-*` 工作负载的镜像标签**一起**换成
新版本再部署即可。api 启动时会自动补跑数据库迁移（两阶段、带 advisory lock 与
记账表），**不需要手工执行任何 SQL**。

## 这份模板里几个不能随手改的地方

- opencode 的 StatefulSet 上那行 `securityContext.fsGroup: 1001` **是必须的**，
  不是装饰。K8s 的新 PVC 是空目录、属主由平台决定，而容器以 uid 1001 运行 ——
  不设它入口脚本会打印「会话数据目录不可写」并退出，Pod 进 CrashLoopBackOff。
  （Docker 的具名卷不会有这个问题，所以本地 compose 里看不到这条。）
- api 服务上的 `LLM_SHIM_URL` **不能省**。向导保存后 api 会把 provider 的
  baseURL 推给 opencode，那个值取自 api 容器的这个变量；不设就会回落到硬编码的
  `llm-shim` 主机名，在这里解析不了 —— 症状是**向导五步全绿、发消息却永远
  没有回复，日志里一条报错都没有**。
- PostgreSQL 与 Redis 用 KubeBlocks 的 `Cluster`，和本仓库另外 100 多个模板一致；
  连接凭据取自 KubeBlocks 生成的 Secret。
- 只有 web 挂 Ingress，其余服务在命名空间外不可达。

## 链接

- 源码与文档：<https://github.com/agentpit-io/hunter-community>
- Sealos 部署说明：<https://github.com/agentpit-io/hunter-community/blob/main/docs/deploy/sealos.md>
- 许可证：Apache-2.0
