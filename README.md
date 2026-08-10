# Hunter Community Edition

> 你的私人金融 AI 团队 · 开源自部署 · 15 分钟起步
>
> Your Private Financial AI Team · Open-source · Self-hosted · Ready in 15 minutes

[![License](https://img.shields.io/badge/license-Apache_2.0-blue)](./LICENSE)
[![Status](https://img.shields.io/badge/status-preview-orange)]()

---

## 🚧 Preview · 敬请期待

Hunter Community Edition (CE) 是 [Hunter](https://hunter.agentpit.io) 商业 SaaS 的**开源自部署版本**。当前处于筹备期，正式版本将在 Sprint 完成后一次性发布。

**已确认路线**：
- 📦 完整 chat + 全部 SKILL + MCP 组件 + 多 agent 分析
- 🐳 单条 `docker compose up` 一键起
- 🔓 Apache 2.0 · 无付费墙 · 无微信/飞书商业化模块
- 🔌 可选接入 SaaS API（数据 / LLM / Kronos）加速

**当前状态**：骨架仓库 · 追踪进度请点 Watch。

---

## 与 SaaS 版差异（预告）

| 功能 | Community | Cloud ([hunter.agentpit.io](https://hunter.agentpit.io)) |
|---|---|---|
| Chat + 全部 SKILL | ✅ | ✅ |
| 自选 / 持仓 / 组合 | ✅ | ✅ |
| UZI 深度分析 | ✅ | ✅ |
| Kronos 走势预测 | ✅（需 GPU 或 SaaS Key） | ✅ |
| 微信推送 | ❌ | ✅ |
| 飞书 / Lark | ❌ | ✅ |
| 会员额度 / 计费 | ❌ | ✅ |
| 官方数据源 | 需申请 Key | ✅ |

---

## 底层依赖 · opencode fork

Hunter CE 的 chat 引擎基于我们对 [sst/opencode](https://github.com/sst/opencode) 的定制 fork。
Fork 源码为我们的商业秘密，但**产物（Docker image + npm package）保持公开可用**：

- Docker: `ghcr.io/agentpit-io/hunter-opencode:latest`
- npm: `@agentpit-io/opencode`（规划中）

Fork 遵循 Apache 2.0 授权，NOTICE 中会明确列出上游归属。

---

## 时间线

- **T-14** · 内部 dogfooding · README/文档定稿
- **T-7** · Discord 建好 · 技术博客准备
- **T-0** · Show HN · Product Hunt · v0.1.0 发布

具体日程见 `hangeaiagent/hunter` 的 `doc/codex/开源整合方案/06-Sprint计划.md`（内部）。

---

## 保持关注

- ⭐ Star 这个仓库获取发布通知
- 💬 Discord（发布日一起公开）
- 📮 邮件订阅：待定

---

## License

Apache License 2.0 · 见 [LICENSE](./LICENSE) 与 [NOTICE](./NOTICE)。

商标 **Hunter · AgentPit · 猎鹿人** 归 AgentPit 团队所有 · fork 请自行改名。
