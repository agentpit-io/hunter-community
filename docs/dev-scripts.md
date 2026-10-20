# 开发自检脚本 · `scripts/check_*.py`

6 个脚本都只做检查、不改数据(`check_skill_sync.py --fix` 例外,会重启 opencode)。
每个都支持 `--help`,输出用途、前置条件与参数。退出码统一:0 = 通过,非 0 = 有问题,可以直接进 CI。

| 脚本 | 查什么 | 什么时候用 | 前置条件 | 参数 |
|---|---|---|---|---|
| `check_skill_sync.py` | 磁盘上的 SKILL 数量与 opencode 实际加载的是否一致 | 增删改 `skills/` 或 `user-skills/` 并重启 opencode 之后;侧栏和模型手上的 SKILL 对不上时 | opencode 容器在跑;凭证读 `.env` 的 `OPENCODE_USER` / `OPENCODE_PASS`;不需要平台 key | `--fix`:不一致时自动重启 opencode 并复查(要 docker) |
| `check_skill_tools.py` | SKILL 引用的工具真实存在、`prompt_tpl` 是自然语言 | 写或改 SKILL 后、提 SKILL 类 PR 前(PR 模板要求) | 默认从 opencode 容器读工具集,容器没起自动用离线清单;不需要平台 key | `--offline`:不读容器,CI 与本地提 PR 前用这个 |
| `check_tools.py` | 工具箱注册表(`tool_catalog.py`)与数据源、SKILL 引用、容器里的 MCP 工具是否对得上 | 改 `tool_catalog.py`、MCP 工具或 SKILL 的 `needs_tools` 之后 | 需要 `apps/api` 的 Python 依赖(或管道进 api 容器跑);不需要平台 key | `--live`:追加与 opencode 容器对账(要 docker) |
| `check_tool_prompts.py` | 工具入口模板 `prompt_tpl`:占位符是 `{中文}`、不太短、`internal_only` 不写模板 | 新加工具或改工具模板之后 | 同上;不联网 | 无 |
| `check_sources.py` | 数据源注册表(`source_catalog.py`)key 唯一、代码里记录的 key 都已注册、字段自洽 | 改 `source_catalog.py` 或新增 `source_health.record(...)` 之后 | 同上;默认离线 | `--probe`:真打一次可用数据源的端点(要联网与 `.env` 里的 finance-data 凭证,会消耗上游配额) |
| `check_source_upstream.py` | 每条数据源的 `upstream` 已填写、已登记,并带行内证据注释 | 新增或改数据源的 `upstream` 之后 | 同上;不联网 | 无 |

## 怎么跑

在仓库根目录:

```bash
python scripts/check_skill_tools.py --offline
python scripts/check_tools.py
```

后四个脚本要 import `apps/api` 的代码。本机没装 api 依赖时,可以管道进 api 容器跑:

```bash
docker compose exec -T api python - < scripts/check_tools.py
```
