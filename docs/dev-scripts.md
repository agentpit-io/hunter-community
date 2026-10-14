# 开发自检脚本 · Dev check scripts

`scripts/check_*.py` 是六个只读的自检脚本,核对「某份清单」和代码 / 容器 / 上游是否还对得上。
在仓库根目录直接 `python scripts/<脚本>.py` 就能跑(除 `check_sources.py --probe` 外只用标准库);
每个都支持 `--help`(用途、前置条件、参数、退出码)。退出码 0 = 通过,1 = 有问题,可以直接进 CI。

> English: six read-only self-check scripts. Each supports `--help`; exit code 0 = pass, 1 = problems found. The table says when to run which.

| 脚本 | 查什么 | 什么时候跑 | 参数 |
|---|---|---|---|
| `check_skill_sync.py` | 磁盘上的 SKILL 与 opencode 实际加载的是否一致 | 改了 `skills/` / `user-skills/`、重启 opencode 之后 | `--fix`:不一致就重启 opencode 再查一次 |
| `check_skill_tools.py` | SKILL 的 `needs_tools` 是否真实存在、`prompt_tpl` 是否是自然语言 | 提交 SKILL 之前(CI 跑 `--offline`) | `--offline`:不连容器,用 `tool_catalog.py` 的清单 |
| `check_tools.py` | 工具注册表 `tool_catalog.py` 与数据源注册表、SKILL 引用、容器里的 MCP 工具是否对得上 | 改了 `tool_catalog.py` 或 MCP 工具之后 | `--live`:追加与 opencode 容器对账 |
| `check_tool_prompts.py` | 工具入口模板 `prompt_tpl` 的写法:中文占位符、不过短、`internal_only` 不写模板 | 给工具加 / 改入口模板之后 | 无 |
| `check_sources.py` | 数据源注册表 `source_catalog.py` 与代码:key 唯一、代码里记录的 key 都已注册、字段自洽 | 加 / 改数据源,或改了 `source_health.record(...)` 之后 | `--probe`:对可用的上游真打一次(要联网,耗配额) |
| `check_source_upstream.py` | 每条数据源的 `upstream` 都已核实并带证据注释 | 加数据源或改 `upstream` 之后 | 无 |

## 前置条件

- **不联网、不要 docker**:`check_skill_tools.py --offline`、`check_tools.py`(不加 `--live`)、`check_tool_prompts.py`、
  `check_sources.py`(不加 `--probe`)、`check_source_upstream.py`(连不上数据库时只打一条 WARNING,不影响结论)。
- **要运行中的 opencode**:`check_skill_sync.py`(凭证读环境变量或 `.env` 里的 `OPENCODE_USER` / `OPENCODE_PASS`,
  地址默认 `http://127.0.0.1:3921`;`--fix` 还要本机 docker)、`check_tools.py --live`。
  `check_skill_tools.py` 不加 `--offline` 时也会先试着读容器,读不到自动退回离线清单。
- **`check_sources.py --probe`** 要联网、`apps/api` 的依赖与平台凭证(同 api 服务),建议在 api 容器里跑。

`check_tools.py` / `check_tool_prompts.py` / `check_sources.py` / `check_source_upstream.py` 也可以在 api 容器里跑,
脚本从 stdin 传进去:

```bash
docker compose exec -T api python - < scripts/check_sources.py
```
