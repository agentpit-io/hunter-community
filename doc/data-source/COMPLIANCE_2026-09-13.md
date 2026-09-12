# 合规修订记录 · 通达信 MCP 部分撤回

**日期**:2026-09-13
**范围**:hunter-community 仓库(agentpit-io/hunter-community)
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

### 保留

**同花顺(HiThink)相关内容全部保留** —— 该厂商的 MCP 端点在其官方文档站公开列出、Key 通过官方文档站三步签发、我们所有测试均使用官方渠道,**合规无争议**。

### 未涉及

- `apps/api/`、`apps/web/`、`mcp/`、`tools/`、`user-skills/`、`deploy/` 全部代码目录 **零通达信引用**(经全量 grep 验证)
- hunter-community 从未有过任何通达信接口的代码集成
- 无需 Docker 容器重建、无需数据库迁移、无需服务重启

## 通达信部分的路线

hunter-community 期望通达信官方能够:

- 公开授权 MCP 接口的第三方接入文档
- 允许已购买付费 key 的用户,自主将 Key 配置到 hunter-community 等开源客户端使用

**在通达信官方书面确认第三方 MCP 集成方式之前,hunter-community 不集成任何通达信相关接口。**

如通达信官方希望联系我们,请通过 GitHub Issues 或 hangeaiagent@gmail.com。

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
