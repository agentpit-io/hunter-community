# 合规修订记录 · 所有厂商 MCP 集成待官方合作

**日期**:2026-09-13(下午更新 —— 立场从"只撤通达信"扩展到"所有厂商都撤回,等逐一沟通")
**范围**:hunter-community 仓库(agentpit-io/hunter-community)+ agentpit.io 博客与微信公众号
**决策人**:项目所有者

---

## 修订原因

2026-09-12 我们发布了《同花顺 vs 通达信官方 MCP 实测对照》系列文档,内容包含通过反编译通达信官方客户端(TdxClaw 1.0.31)获得的、未经官方文档化的技术细节:

- 未公开的 MCP 端点 URL(`txmcp.tdx.com.cn:3001/clawmcp`)
- 客户端主进程 JS 里的硬编码字符串
- Bearer key 鉴权格式与 20 个工具签名
- 401 鉴权行为的抓包证据

经复核,**上述做法在中国现行法律环境下存在合规风险**,主要论点:

1. **通达信官方未在任何公开文档中授权第三方直连该 MCP 端点** —— 端点只在其官方客户端 TdxClaw 内部使用
2. **公开传播未文档化的技术接入路径**,可能被解读为:
    - 帮助他人绕过官方设置的"客户端封装"这一实际访问控制层
    - 披露官方未主动公开的技术信息
    - 引发通达信可能的商务/法务反应(投诉平台删帖、Key 拉黑、乃至民事诉讼)
3. hunter-community 作为开源项目、面向开发者与用户,**不应向用户传递"绕过官方文档发现端点是可行做法"的信号**

**风险评估:反编译本身低风险,但公开传播是中-高风险。**

---

## 修订内容

### 已删除

- `doc/data-source/2026-09-12_ths-vs-tdx-mcp.md` —— 中文完整实测报告(含端点/拆包过程/工具清单/鉴权细节)
- `doc/data-source/2026-09-12_ths-vs-tdx-mcp.en.md` —— 英文完整报告

### 已修改

- `README.md` —— 中文 · 「官方 MCP 实测对照:同花顺 vs 通达信」小节替换为「官方 MCP 支持进展(2026-09-13)」精简说明
- `README_EN.md` —— 英文 · 对应小节同步替换

### 保留 → 撤回(2026-09-13 下午更新)

**同花顺(HiThink)相关内容也已撤回**:

- 上午的判断:同花顺 MCP 端点公开、Key 官方签发 · 合规无争议
- 下午的更新:**"官方公开的 API"≠"官方对我们的产品授权集成"**。同花顺没有对 hunter-community 作为集成方书面授权 · 提前公开宣传"实测通过"在商务上可能不当
- 立场调整为:与同花顺、通达信、以及任何其他数据厂商 · 全部等**逐一正式沟通并拿到授权后再开放集成**

### 同步撤回

- agentpit 博客文章 `/blog/ths-tdx-official-mcp-field-test`(中英)已从 lib/blog.ts 中删除 · 页面 404 · sitemap 自动移除
- 微信公众号草稿已通过 draft/delete API 删除

### 未涉及

- `apps/api/`、`apps/web/`、`mcp/`、`tools/`、`user-skills/`、`deploy/` 全部代码目录 **零通达信引用**(经全量 grep 验证)
- hunter-community 从未有过任何通达信接口的代码集成
- 无需 Docker 容器重建、无需数据库迁移、无需服务重启

## 所有厂商的路线

hunter-community 期望所有金融数据厂商能够:

- 与我们建立正式合作关系,书面授权 hunter-community 作为第三方集成方
- 支持"用户自持 key"的合规模式,允许用户将自己付费购买的 key 配置到 hunter-community 使用

**在与每家数据厂商完成正式沟通、拿到书面授权之前,hunter-community 不集成任何具体厂商的接口。**

用户目前可用的数据供给:

- **免费开源源**:akshare(A 股)、yfinance(美股/港股)—— 保留
- **用户自持第三方 MCP**:通用 MCP 接入能力(用户自己接入任何 MCP)—— 保留

厂商相关的官方集成(如 HiThink、Tongdaxin、其他数据商)· 一律**等我们正式沟通并拿到授权后陆续开放**。

如数据厂商希望联系我们,请通过 GitHub Issues 或 hangeaiagent@gmail.com。

---

## 校验

```bash
# 全仓验证零残留(除本文与 README 的说明性提及外)
grep -rlE '通达信|tongdaxin|txmcp|tdx.com.cn|tdx_mcp|TDX_MCP|tdxhub|icfqs|clawmcp' \
  apps/ mcp/ tools/ user-skills/ deploy/ 2>/dev/null
# 预期:无输出
```

## 生效方式

- 生产服务器(`35.198.212.241`)拉取本次 commit 后 · 所有 Docker 容器**无需重启**(纯文档修订)
- GitHub 上 README 与 doc/data-source/ 立即生效
- 参考文章:agentpit.io/blog 与微信公众号「agentpit」同步走「方案 A · 只发同花顺」版本
