# 安全策略 · Security Policy

## 支持的版本 · Supported versions

| 版本 · Version | 是否接收安全修复 · Security fixes |
|---|---|
| 最新 minor 版本(当前 1.0.x)· latest minor (currently 1.0.x) | ✅ |
| 上一个 minor 版本 · previous minor | ✅ 仅严重漏洞 · critical only |
| 更早版本 · older | ❌ 请升级 · please upgrade |

`main` 分支始终包含最新修复。· `main` always carries the latest fixes.

## 报告漏洞 · Reporting a vulnerability

**请不要为安全漏洞开公开 issue。· Do NOT open a public GitHub issue for security vulnerabilities.**

任选一种方式 · Use either:

1. **GitHub 私密报告(推荐)· GitHub private reporting (preferred)**:[Report a vulnerability](https://github.com/agentpit-io/hunter-community/security/advisories/new)
2. **邮件 · Email**:`security@agentpit.io`

请尽量包含 · Please include:

- 受影响组件(api / web / opencode / llm-shim / 部署配置)· affected component
- 受影响版本或提交 · affected version or commit
- 复现步骤 · reproduction steps
- 影响评估 · impact assessment
- 修复建议(如有)· suggested fix, if any

## 响应时限 · Response times

| 阶段 · Stage | 时限 · Target |
|---|---|
| 确认收到 · acknowledgement | 3 个工作日内 · within 3 business days |
| 初步评估 · initial assessment | 7 天内 · within 7 days |
| 严重漏洞修复发布 · patch for critical issues | 14 天内 · within 14 days |

修复发布后,我们会在 Release Notes 和 GitHub 安全公告中致谢报告者(除非你希望匿名)。· After a fix ships, we credit reporters in the Release Notes and the GitHub security advisory unless you prefer to stay anonymous.

## 部署安全提示 · Deployment security notes

- **`JWT_SECRET` 必须自己生成**,不要使用任何示例值或公开值:它同时用于签发登录凭证和加密已保存的 key。生成方法见 README。· **Generate your own `JWT_SECRET`** — never use an example or public value; it signs sessions and encrypts stored keys.
- **单用户模式(`HUNTER_SINGLE_USER=1`,默认开启)只适合本机使用**:开启时任何能访问该实例的人都能拿到管理员身份。暴露到局域网或公网前必须设为 `0`。· **Single-user mode (default on) is for local use only**: anyone who can reach the instance gets an admin session. Set it to `0` before exposing the instance.
- Postgres 默认密码是 `hunter`,非本机试用请修改。· The default Postgres password is `hunter`; change it for anything beyond local trials.
- `HUNTER_INTERNAL_KEY`、`OPENCODE_PASS` 在暴露实例前请重新生成(`openssl rand -hex 20`)。· Regenerate `HUNTER_INTERNAL_KEY` and `OPENCODE_PASS` before exposing the instance.
- 非标准主机端口(3100 / 8100 / 5442 / 6479)只能减少误暴露,不能代替防火墙;公网部署请在前面加反向代理并启用 TLS。· Non-standard host ports reduce accidental exposure but are not a firewall; put a TLS reverse proxy in front for public deployments.
- 容器尽量以非 root 用户运行(`web` 以 `nextjs` 运行)。· Containers run as unprivileged users where possible (`web` runs as `nextjs`).
