# L08 · 让外部能开动它（控制通道 + MCP 控制类 + 长任务取消）

> 链路：智能炒股 · 第五段「闭环补齐」· 阶段代号 **L08**
> 依据：`plan/L08.md` · `plan/五期追加规则.md`（§三 / §五 红线）· 五期方案 §四 `L08` / §二 第 8 条
> · 技术方案 **§5.1 / §5.2 / §5.3 / §6.3 / §10.1 / §10.4** · 参考 `docs/开发文档/R13-上线与自动观察.md`（`fin.observe` 如何接进调度）
> 上一阶段：`docs/开发文档/L07-发布适配器与待核实.md`
> 上海时间：2026-10-05 04:0x
> 本轮范围：**四个控制入口**（`runtime.workflow_start/get/pause/cancel`）+ **一个控制类 MCP**（与数据类分开）+ **长任务取消接通** + OpenCode 接上控制。

---

## 一、做了什么（人话）

技术方案 §5.1 把运行时分工写死了：OpenCode 负责「**启动计划 / 暂停计划 / 查询状态**」，Runtime Bridge 负责「**工作流模板、参数校验、权限**」，Temporal 是**唯一调度权威**。改造前这三件事**接口一个都没有**（方案 §2.6）：Temporal 工作流只有「调度触发」和「给排障用的手工补跑」两个触发点，**没有任何取消调用**；MCP 挂了一堆**全是数据工具**，没有一个控制类；paper 有 `cancel` 端点、**fin-worker 从不调用**。于是「可取消」（§10.4）缺了一半。

本段补上三件事：

1. **四个控制入口**（`apps/fin-worker/app/runtime_control.py` + `api.py` 四个路由）：
   - `runtime.workflow_start` —— 只认**白名单模板名 + 类型化参数**；
   - `runtime.workflow_get` —— 状态**从 Temporal 现查**（不另存一份）；
   - `runtime.workflow_pause` —— 暂停 / 恢复（**调度**用 Temporal 原生暂停；**正在跑的工作流**用 `pause`/`resume` 信号，协作式）；
   - `runtime.workflow_cancel` —— **Temporal 原生取消**，并把该工作流当前的长任务（`fin_job`）**一并取消**。
   四个都**幂等**（`cancel` 两次不报错；对已结束的工作流返回 `already_ended`）。
2. **一个控制类 MCP**（`scripts/opencode-mcp/runtime_mcp.py`）：暴露上面四个工具，
   在 `gen-config.py` 里**与数据类 MCP 分开登记**（`_CONTROL_MCP` vs `_DATA_MCP`）—— 这就是 §6.3 的「控制通道与数据通道分离」。
3. **长任务取消接通**：`runtime.workflow_cancel` → 查工作流当前 `fin_job` → Temporal 取消 → 调 paper `/api/v1/jobs/{id}/cancel`
   → `fin_job` 落到 `CANCEL_REQUESTED`。**补上 §10.4 的「可请求取消」。**

> ⚠️ **没改错地方**：仓里 `apps/fin-worker/app/bridge/` 也叫「Runtime Bridge」，但它是「策略意图 → Paper 命令」的翻译层。本段**一个字没动它** —— 控制入口在**新文件** `runtime_control.py`。

---

## 二、改造前基线（grep 证据，`HEAD=33c78fc`）

```bash
$ git grep -n "workflow_cancel\|workflow_pause\|runtime\.workflow\|workflow_start\|workflow_get" HEAD -- apps tools scripts
（空 —— 四个入口全仓无命中）
```

```bash
$ git grep -n "cancel_job\|/cancel" HEAD -- apps/fin-worker        # 除 .venv
HEAD:apps/fin-worker/app/bridge/paper.py:167:  "POST", f"/api/v1/orders/{order_id}/cancel", ...
（只有 `cancel_order` = 撤单，**不是长任务取消**；`fin_job` 的 cancel 端点 fin-worker 从不调用）
```

```bash
$ git show HEAD:scripts/opencode/gen-config.py | grep -n "_EXTRA_MCP\|hunter_cap\|screener"
346:    _EXTRA_MCP = [
347:        ("hunter_cap", "hunter_capability_mcp.py", 180000),
348:        ("screener",   "screener_mcp.py",           60000),
（gen-config 只注册数据类 MCP，没有控制类）
$ ls scripts/opencode-mcp/*.py
watchlist_mcp.py uzi_mcp.py screener_mcp.py hunter_user_mcp.py hunter_capability_mcp.py
（五个全是数据工具）
```

对照方案 §2.6：**完全一致**。

---

## 三、四个入口的契约（模板白名单 + 参数类型）

四个路由都在 fin-worker（`apps/fin-worker/app/api.py`），路径 `/internal/runtime/*`，
鉴权 `X-Hunter-Internal-Key == HUNTER_INTERNAL_KEY`（读取凭证，与 api 数据面同一把），
**端口只绑 `127.0.0.1`**（`docker-compose.yml` fin-worker 的 `ports`），**不放公网**。

| 入口 | 方法/路径 | 入参 | 出参 |
|---|---|---|---|
| `workflow_start` | `POST /internal/runtime/workflow_start` | `{template, params, workflow_id?}` | `{started, template, workflow_id, run_id, params}` |
| `workflow_get` | `GET /internal/runtime/workflow_get` | `?workflow_id=&run_id=` 或 `?schedule_id=` | 工作流：`{kind,workflow_id,run_id,status,task_queue,start_time,close_time,history_length,paused,current_job}`；调度：`{kind,schedule_id,paused,note,num_actions,next_action_times,workflow}` |
| `workflow_pause` | `POST /internal/runtime/workflow_pause` | `{schedule_id}` 或 `{workflow_id, run_id?, resume?}` | `{kind,…,paused,resumed,changed?}` |
| `workflow_cancel` | `POST /internal/runtime/workflow_cancel` | `{workflow_id, run_id?, job_id?}` | `{cancelled, already_ended, workflow_id, run_id, status, job_id, job_status}` |

### 模板白名单（`runtime_control.TEMPLATES`，共 **12** 个 = 全部已注册工作流类型）

| 模板 | 必填 | 允许的参数（名称→类型） |
|---|---|---|
| `fin.point_preopen` / `_decide` / `_match_a` / `_match_b` / `_match_c` / `_close` | `market`(`CN_A`\|`HK`\|`US`) | `trade_date`(YYYY-MM-DD) · `at`(HH:MM) · `point` · `now` · `project_id` · `code` · `hold_seconds`(int) · `lookback_days` · `lookahead_days` · `report`(bool) |
| `fin.market_etl` | `market`(`cn`\|`hk`\|`us`) | `trade_date` · `limit`(int) · `bars`(int) · `now` |
| `fin.instrument_sync` | — | `market`(`cn`\|`hk`\|`us`) · `codes`(str[]) |
| `fin.review` | `market` | `trade_date` · `at` · `point` · `now` · `project_id` |
| `fin.shadow` | `market` | + `code` |
| `fin.observe` | `market` | + `as_of` |
| `fin.propose` | `market` | `trade_date` · `at` · `point` · `now` · `project_id` |

**校验规则**（`validate_start`，纯函数、可单测）：模板不在白名单 → **404**；参数名没登记 → **400**（**不静默丢弃**）；
类型不符（`bool` 不当 int、`market` 枚举外、日期/时刻格式、int 非负上限、str 非空≤200、str[] 元素类型）→ **400**。

---

## 四、为什么它不是后门（第一红线）

第一红线：**控制通道严禁变成「执行任意代码 / 任意 shell / 任意工作流名」的后门**。四条护住：

1. **只认模板名 + 参数**。模板名不在 `TEMPLATES` 里 → 404；参数名没登记 / 类型不符 → 400。
   **没有任何参数能表达「跑一段代码 / 跑一条命令 / 打任意 URL」**。
2. **状态现查**（`workflow_get` 从 Temporal 查），**不另起调度、不另存工作流状态**（红线：Temporal 唯一权威）。
3. **内部口令 + 最小权限**：四个入口都要 `X-Hunter-Internal-Key`；端口只绑 `127.0.0.1`。
4. **控制类 MCP 只是转发**：它把四个工具转给 fin-worker 的 `/internal/runtime/*`，**判定全在 fin-worker**（脚本里那份模板清单只是给模型看的提示，写歪也不放宽）。

**白名单外被拒的实证**（真机 `curl`）：

```bash
$ curl -s -X POST "$B/internal/runtime/workflow_start" -H "$K" -H 'Content-Type: application/json' \
    -d '{"template":"fin.evil","params":{}}' -w ' [http=%{http_code}]\n'
{"detail":"未知工作流模板 'fin.evil'；允许的模板只有：['fin.instrument_sync', 'fin.market_etl',
 'fin.observe', 'fin.point_close', 'fin.point_decide', 'fin.point_match_a', 'fin.point_match_b',
 'fin.point_match_c', 'fin.point_preopen', 'fin.propose', 'fin.review', 'fin.shadow']} [http=404]

$ curl ... -d '{"template":"fin.point_decide","params":{"market":"CN_A","evil":1}}'
{"detail":"模板 fin.point_decide 不接受参数 ['evil']；允许的参数：['at','code','hold_seconds',
 'lookahead_days','lookback_days','market','now','point','project_id','report','trade_date']"} [http=400]

$ curl ... -d '{}'                                          # 连 template 都没有
{"detail":"缺 template"} [http=400]
```

---

## 五、长任务取消链路（§10.4 的「可请求取消」）

```
POST /internal/runtime/workflow_cancel {workflow_id}
      │
      │ ① describe()  —— 已终态？ → 直接返回 already_ended（幂等，不报错）
      │ ② query("current_job") —— 从工作流取当前 fin_job（L08 新加的 query 处理器）
      │ ③ handle.cancel()      —— Temporal 原生取消（工作流在下一个 await 停住）
      │ ④ paper POST /api/v1/jobs/{job_id}/cancel —— fin-worker 调 paper 取消那个长任务
      ▼
   fin_job: RUNNING ──► CANCEL_REQUESTED        （若还在 ACCEPTED 则直接 CANCELLED）
   workflow 执行:  RUNNING ──► CANCELED
```

**为什么先取 job 再取消**：取消后工作流随即停住，不会再走到 `fin_point_job`（成功）—— 所以那个 `fin_job` 不会被反过来标成 `SUCCEEDED`。
**`current_job` 从哪来**：`workflows._PausableWorkflow` 混入给每个工作流实例加了 `pause`/`resume` 信号、`paused`/`current_job` 两类查询，并在时点路径的 `begin_point_job` 之后 `_track_job(job_id)`、收尾 `_track_job(None)`。
> ⚠️ **技术取舍（如实记）**：Temporal Python SDK `1.15.0` **没有 `workflow.shield()`**，所以**取消之后跑不了清理 Activity**（本机实测：取消后 `execute_activity` 立即抛 `CancelledError`，清理活动不执行）。因此取消链里那一步 **paper cancel 放在 HTTP 层（fin-worker 自己）执行**，而不是「工作流收到取消后再调」—— 两者都是 fin-worker 调 paper，链路闭环；顺序是「先取 job → 取消 Temporal → 调 paper」，见上。这是本 SDK 版本下的正确做法，不是遗漏。

**真机读数**（工作流被 `pause` 冻在一个 **RUNNING** 的 job 上，再取消）：

```bash
$ curl -s -X POST "$B/internal/runtime/workflow_pause" -H "$K" -d '{"workflow_id":"l08-ctl-hk-cancel2"}'
{"kind":"workflow","workflow_id":"l08-ctl-hk-cancel2","status":"RUNNING","paused":true,"resumed":false,"changed":true}
frozen current_job = job_70c58887a4c64ba09acef238
$ psql ... "select job_id,status from fin_job where job_id='job_70c…'"
job_70c58887a4c64ba09acef238|RUNNING                 ← 取消前
$ curl -s -X POST "$B/internal/runtime/workflow_cancel" -H "$K" -d '{"workflow_id":"l08-ctl-hk-cancel2"}'
{"cancelled":true,"already_ended":false,"workflow_id":"l08-ctl-hk-cancel2","run_id":"8e45bbcc-…",
 "status":"CANCEL_REQUESTED","job_id":"job_70c58887a4c64ba09acef238","job_status":"CANCEL_REQUESTED"}
$ curl -s -X POST "$B/internal/runtime/workflow_cancel" -H "$K" -d '{"workflow_id":"l08-ctl-hk-cancel2"}'   # 第二次
{"cancelled":false,"already_ended":true,"workflow_id":"l08-ctl-hk-cancel2","run_id":"8e45bbcc-…","status":"CANCELED"}
$ psql ... "select job_id,status from fin_job where job_id='job_70c…'"
job_70c58887a4c64ba09acef238|CANCEL_REQUESTED        ← 取消后：链路闭环
$ curl ... workflow_get?workflow_id=l08-ctl-hk-cancel2   → status= CANCELED
```

---

## 六、MCP 注册方式（控制 / 数据分离，§6.3）

新 server：`scripts/opencode-mcp/runtime_mcp.py`（照 `watchlist_mcp.py` 的 `@server.list_tools` / `@server.call_tool` 写法，stdio）。
**单独一个 server**，与数据类（`watchlist_mcp` / `uzi_mcp` / `screener_mcp` / `hunter_user_mcp` / `hunter_capability_mcp`）**分开**。

`gen-config.py` 里把两个清单**分开登记** —— 生成的 `mcp` 段（真跑 `_project_config`，把 `HUNTER_EXTRA_MCP_DIR` 指向仓库目录）：

```json
{
  "hunter_cap":      { "type":"local", "command":["python3","…/hunter_capability_mcp.py"], "timeout":180000 },   ← 数据类
  "screener":        { "type":"local", "command":["python3","…/screener_mcp.py"],          "timeout":60000  },   ← 数据类
  "runtime_control": { "type":"local", "command":["python3","…/runtime_mcp.py"],           "timeout":60000  }    ← 控制类（L08）
}
```

**怎么区分哪个是控制**：注册名以 `runtime_` 起头（数据类用 `hunter_*` / `screener` / 镜像自带的 `watchlist` / `uzi` 等）；
代码里 `_CONTROL_MCP` 与 `_DATA_MCP` 是两个清单（各自带注释）。它转发到 fin-worker 的 `/internal/runtime/*`，**不返回任何行情数据**。
`docker-compose.yml` 的 opencode 服务加了 `FIN_WORKER_URL`（默认 `http://fin-worker:8300`）—— fin-worker 在 `fin` profile 下，没启用时控制工具**如实报连不上**（不是崩）。

**MCP 客户端真调**（用 `mcp` 客户端在 fin-worker 同网容器里驱动，输出原文）：

```
[runtime-mcp] boot · fin_worker=http://fin-worker:8300 key_set=True
TOOLS: ['runtime_workflow_start', 'runtime_workflow_get', 'runtime_workflow_pause', 'runtime_workflow_cancel']
GET   : {"kind":"schedule","schedule_id":"fin-point-CN_A-0930","paused":false,"note":"runtime_control","num_actions":0,
         "next_action_times":["2026-10-05T01:30:00+00:00", …],"workflow":"fin.point_decide"}
START : {"started":true,"template":"fin.instrument_sync","workflow_id":"l08-mcp-sync","run_id":"e27c1112-…","params":{"market":"us"}}
REJECT: {"detail":"未知工作流模板 'fin.evil'；允许的模板只有：[…]"}
PAUSE : {"kind":"schedule","schedule_id":"fin-point-CN_A-0930","paused":true,"resumed":false}
RESUME: {"kind":"schedule","schedule_id":"fin-point-CN_A-0930","paused":false,"resumed":true}
```

---

## 七、测试用例表（命令 / 结果 / 实测数字）

计数用 `--junitxml`（本仓 `-q` 有时看不到末尾摘要行，见 `CLAUDE.md`）。

| # | 用例集 | 命令 | 结果 |
|---|---|---|---|
| 1 | L08 控制通道（白名单 / 类型化参数 / 幂等 / 四入口） | `apps/fin-worker: .venv/bin/python -m pytest tests/test_runtime_control.py -q` | **29 passed** |
| 2 | L08 控制 MCP（无依赖桩，工具名 / 模板清单与 fin-worker 一致 / 路由） | `apps/fin-worker: .venv/bin/python -m pytest tests/test_runtime_mcp.py -q` | **4 passed** |
| 3 | **fin-worker 全量** | `apps/fin-worker: .venv/bin/python -m pytest tests -q` | **297 passed / 0 failed / 0 skipped**（264 + 33 新） |
| 4 | **paper 全量** | `apps/paper: .venv/bin/python -m pytest tests -q` | **183 passed / 33 skipped / 0 failed**（共 216） |
| 5 | **api 全量** | `apps/api: TEST_DATABASE_URL=…/l08_test PYTHONPATH=. .venv/bin/python -m pytest tests -q` | **959 passed / 0 failed / 4 skipped**（排除已知污染源一次，见 §九-1） |
| 6 | api 全量（**不排除**，原始） | 同上，不加 `--ignore` | 971 tests / **17 failed** / 0 error / 4 skipped —— **17 条全在 `test_fin_evolution_router.py`，是既有污染，与本段无关** |
| 7 | 迁移幂等（连跑两遍） | `DATABASE_URL=…/l08_test .venv/bin/python -m app.migrate` ×2 | 第一遍「本次执行 53 个」、第二遍「**本次待执行 0 个**」；`schema_migrations` 53 行 |

**L08 新用例逐条（33 条）**：

| 组 | 用例 | 断言要点 |
|---|---|---|
| A | `test_whitelist_lists_only_registered_workflows` | 白名单恰 12 个、与已注册工作流类型一一对应 |
| A | `test_unknown_template_is_rejected` / `test_shell_and_arbitrary_names_are_not_templates` | `fin.evil` / `bash` / `os.system` / `eval` / `…; rm -rf /` → 404 |
| A | `test_missing_required_market_rejected` / `test_bad_market_value_rejected` | 必填缺失 / 枚举外 → 400 |
| A | `test_etl_market_uses_lowercase_channel_names` | ETL 用 `cn/hk/us`，时点用 `CN_A/HK/US`（**口径不同，不混**） |
| A | `test_unknown_param_rejected_not_silently_dropped` | 未知参数名 → 400（**不静默丢弃**） |
| A | `test_date_and_hhmm_typing` / `test_bool_is_not_accepted_as_int` / `test_int_range_and_type` / `test_str_list_typing` | 类型化参数逐类 |
| A | `test_derive_workflow_id_is_stable_and_prefixed` | id 前缀 `fin-ctl-`、稳定可复现 |
| B | `test_start_impl_returns_run_id` / `test_start_impl_conflict_is_409_not_500` | 启动成功 / 重复启动 409 |
| B | `test_get_impl_reports_status_and_paused` / `test_get_impl_unknown_workflow_404` | 状态 + `paused`；不存在 → 404 |
| B | `test_get_impl_schedule_shows_paused` | 调度 `paused` 可查 |
| B | `test_pause_schedule_is_idempotent` | **暂停两次不报错**；恢复 |
| B | `test_pause_workflow_sends_signal` / `test_pause_closed_workflow_is_not_an_error` | 发 `pause`/`resume` 信号；已结束的返回 `changed:false` |
| B | `test_cancel_impl_cancels_workflow_and_job` | 取消工作流**并把 job 一并取消**（job_status 透传） |
| B | `test_cancel_impl_is_idempotent_on_closed_workflow` / `test_cancel_impl_second_call_after_first` | **取消两次第二次不报错** |
| C | `test_all_four_require_key` | 四个入口未带口令 → 401 |
| C | `test_http_start_rejects_unknown_template` / `test_http_start_rejects_unknown_param` / `test_http_start_ok` | 路由层 404 / 400 / 200 |
| C | `test_http_get_and_pause_and_cancel` | get / pause / cancel 三入口 HTTP |
| C | `test_http_cancel_requires_workflow_id` | 缺 workflow_id → 400 |
| M | `test_four_control_tools_are_registered` | MCP 恰四个工具、`start` 的 `template` 必填 |
| M | `test_template_list_matches_fin_worker_whitelist` | **MCP 脚本里的模板清单 == `runtime_control.TEMPLATES`**（防漂） |
| M | `test_call_tool_routes_to_right_endpoints` | 四工具路由到正确方法/路径/体 |
| M | `test_unknown_tool_returns_structured_failure` | 失败对象带 `type`/`code`/`instruction`（同 uzi_mcp 约定） |

### 四个入口逐条 `curl`（真机，`$B=http://127.0.0.1:8399`，`$K='X-Hunter-Internal-Key: m4-verify-key'`）

```bash
# ① start（白名单模板）
$ curl -s -X POST "$B/internal/runtime/workflow_start" -H "$K" -H 'Content-Type: application/json' \
    -d '{"template":"fin.instrument_sync","params":{"market":"us"},"workflow_id":"l08-ctl-sync-us"}'
{"started":true,"template":"fin.instrument_sync","workflow_id":"l08-ctl-sync-us","run_id":"2159067a-…","params":{"market":"us"}}

# ② get（状态从 Temporal 现查）
$ curl -s "$B/internal/runtime/workflow_get?workflow_id=l08-ctl-sync-us" -H "$K"
{"kind":"workflow","workflow_id":"l08-ctl-sync-us","run_id":"2159067a-…","status":"COMPLETED",
 "task_queue":"fin-trading","start_time":"2026-10-04T19:56:54+00:00","close_time":"…","history_length":11,
 "paused":false,"current_job":null}

# ③ pause（调度 —— Temporal 原生暂停；幂等）
$ curl -s -X POST "$B/internal/runtime/workflow_pause" -H "$K" -d '{"schedule_id":"fin-point-CN_A-0930"}'
{"kind":"schedule","schedule_id":"fin-point-CN_A-0930","paused":true,"resumed":false}
$ curl -s "$B/internal/runtime/workflow_get?schedule_id=fin-point-CN_A-0930" -H "$K"   → "paused":true
$ curl -s -X POST "$B/internal/runtime/workflow_pause" -H "$K" -d '{"schedule_id":"fin-point-CN_A-0930","resume":true}'
{"kind":"schedule","schedule_id":"fin-point-CN_A-0930","paused":false,"resumed":true}

# ④ cancel（见 §五 全链）
```

**`pause` 后工作流真的停了**（真机）—— 起一个 HK 多项目 decide（正常约 2 秒跑完），0.6 秒时暂停：

```bash
$ curl … workflow_start -d '{"template":"fin.point_decide","params":{"market":"HK","trade_date":"2026-09-29",…},"workflow_id":"l08-ctl-hk-pause"}'
$ curl … workflow_pause -d '{"workflow_id":"l08-ctl-hk-pause"}'
{"kind":"workflow",…,"status":"RUNNING","paused":true,"changed":true}
$ sleep 6; curl … workflow_get?workflow_id=l08-ctl-hk-pause
status= RUNNING paused= True          ← 6 秒后仍 RUNNING（对照：同样参数不加暂停约 2 秒就 COMPLETED）
$ curl … workflow_pause -d '{"workflow_id":"l08-ctl-hk-pause","resume":true}'; sleep 4
after resume status= COMPLETED paused= False
```

---

## 八、偏离 / 决策与原因

1. **`pause` 分两种目标**：给 `schedule_id` → 暂停 **Temporal Schedule**（§5.1 的「暂停计划」，原生、状态可见 `paused=true`）；给 `workflow_id` → 发 `pause`/`resume` **信号**（§1.1 的「信号机制」，协作式：工作流在下一个检查点停住并 `wait_condition`）。
   **为什么是协作式**：Temporal 没有原生的「暂停一个正在跑的 workflow」；唯一能改状态的终态操作是 `cancel`。信号 + 检查点是与官方语义一致的做法，且实测「暂停后 6 秒仍 RUNNING、不暂停 2 秒就完成」—— 是真停住了。
2. **`_PausableWorkflow` 不做成**给每个工作流手写信号处理器**：抽成混入（`__init__` + `@workflow.signal` + `@workflow.query` + `_gate`），12 个工作流类统一继承。本机实测**继承的信号 / 查询处理器会被 SDK 正确识别**。
   **不加命令、不改历史**：`_gate` 未暂停时 `if self._paused` 为假直接返回（连 `wait_condition` 都不调）→ 零命令 → 既有历史 replay 逐字节不变。新增的只是信号 / 查询处理器（不产生命令）。
3. **取消链那一步 paper cancel 放在 HTTP 层**：见 §五 的取舍框（SDK 1.15 无 `workflow.shield`，取消后跑不了 Activity）。**没有造假**，是这一版 SDK 下的正解。
4. **`start` 的 workflow_id 可显式给、也可推导**（`fin-ctl-{template}-{market}-{trade_date}`）；复用 `ALLOW_DUPLICATE_FAILED_ONLY`（与手工补跑同一口径）—— 同一天同模板重复启动会撞 409，是「同一件事不重复开跑」在编排层的一层保护。
5. **`get` 的 `paused` / `current_job` 只在 RUNNING 时查**（终端工作流没有这些 query 意义；不支持的 query 吞掉当没有）。
6. **控制 MCP 的模板清单只作提示**：真正判定在 fin-worker。脚本里那份清单用测试 `test_template_list_matches_fin_worker_whitelist` 盯着，**防漂**。
7. **附带修了一处既有启动竞态**（`app/main.py`）：HTTP 线程与 Worker 主线程**各 import 一次 `temporalio`** 会撞上 `partially initialized module 'temporalio'`（包的初始化不是线程安全的），容器进入 Restarting 循环。现在主线程先导一次 `app.api`，两个线程读的都是完成态。**这是既有隐患**，本段改用更强重启观察到；修它是为了让控制通道所在的服务稳定起来。

---

## 九、未接数据源清单（`五期追加规则 §五` 第 15 条）

**本段不引入任何业务数据源** —— 控制通道只操作 Temporal（启动 / 查询 / 暂停 / 取消），
不读行情 / 财务 / 情绪。**数据源侧「未接」= 无**。

按 §6.3 补一句口径：控制 / 数据分离落地的形态是「**一个控制类 MCP server + 若干数据类 MCP server**」；
将来新增控制能力（如「改运行开关」）应加进**同一个控制 server**，不要混进数据类 —— 否则 §6.3 的分离就没了。

---

## 十、遗留问题

1. **api 全量用例里有 17 条**（`tests/test_fin_evolution_router.py`）**在「整目录一次跑」时失败 —— 既有测试污染，与 L08 无关**。
   根因：`tests/test_fin_evolution_propose.py:284` 把 `router_mod._INTERNAL_KEY` 改成 `"l01-internal"` 且**没还原**，
   按字母序 `…_propose.py` 排在 `…_router.py` 之前 → 后者用自己的 `test-internal-key` 请求时 `internal auth failed`（401）。
   **证据**：`git diff HEAD -- apps/api/tests/test_fin_evolution_{propose,router}.py` 为空（本段没碰这两文件）；
   本段全程**不改别人的用例**（红线 6）。已在 §七 表里给出「排除污染源一次」的干净读数（**959/0/4**）。
   建议后续单开一小修：`test_fin_evolution_propose.py` 收尾还原 `_INTERNAL_KEY`。
2. **`pause` 是协作式的**（下一个检查点生效）：若工作流正卡在一个长时间 Activity 里，`paused=true` 会立刻可查，但工作流要等该 Activity 返回才真正停住。**这是协作式暂停的固有语义**，已在工具描述与本文写明。
3. **取消链的 paper cancel 是「best-effort」**：若 paper 调用失败，返回体里 `job_status` 会写成 `cancel_failed:<异常名>`（工作流的 Temporal 取消**已经生效**）。这样「取消工作流」不会被「取消 job 失败」拖住，同时失败可见。
4. **`workflow_cancel` 的 `already_ended` 只区分「已结束」，不细分是 COMPLETED / FAILED / CANCELED / TERMINATED / TIMED_OUT** —— 细分状态在 `status` 字段里给（`get` 同一份）。够用，未再拆错误码。

---

## 十一、给 `L09` 的交接

- **`L09`（采集补齐：新闻 / 基本面 / 情绪）与本段不冲突**：本段只加了「操作 Temporal 的手」，**没有改任何 Schedule、没有改取数实现**。
- `L09` 的新采集工作流**照现有模式注册**即可：在 `workflows.py` 加工作流类型 + `worker.activity_list()` 登记 Activity + `schedules.py` 加 Schedule。**若要能被外部控制通道启动**，在 `runtime_control.TEMPLATES` 加一行（模板名 + 参数类型），控制 MCP 侧**不用改**（它转发的是模板名，判定在 fin-worker）。
- **`L09` 的采集一律走 Temporal Schedule**（红线：不许另起定时器）。本段的 `runtime_control` 只是**操作** Temporal，**它自己一行调度都没有** —— 别把它当成「又一套调度入口」。
- 控制类 MCP 若将来要加工具，加在 `scripts/opencode-mcp/runtime_mcp.py`，**不要**混进数据类 server。

---

## 十二、上下文守卫档位与读数

```
$ python3 /mnt/mixplode/hunter-dev/stock-ai-loop/context-guard.py \
    --cwd /mnt/mixplode/hunter-dev/stock-ai/hunter-community --detail
上下文 30.3% · ok · 约 302,749 / 1,000,000
  实测（最后一条 usage）   302,430
  粗估增量（之后写入的）   319（1,117 字节 ÷ 3.5）
  → 不用管。
退出码 = 档位 0（0 / 10 / 20 / 30）
```

档位 **0**（远低于 warn 70%），本段全程未触发交接。

---

## 附 · 出口标准逐条对照（`plan/L08.md` §四）

| 出口标准 | 证据 |
|---|---|
| 改造前：`runtime.workflow_*` 无命中、无 cancel 调用 | §二（grep 原文，全空） |
| 四个入口逐条 `curl`（贴命令与返回） | §七「四个入口逐条 curl」 |
| `start` 白名单外 → 拒绝 | §四（`fin.evil` → 404 原文）；用例 A 组 |
| `pause` 后工作流真的停了（贴状态变化） | §七「pause 后工作流真的停了」（6 秒仍 RUNNING vs 2 秒完成） |
| `cancel` 两次，第二次不报错 | §五（`already_ended:true`）；用例 B 组 |
| 长任务取消链路闭环：`fin_job` 落到 `CANCEL_REQUESTED` | §五（RUNNING→CANCEL_REQUESTED，DB 原文） |
| 控制 MCP 真调通（贴 MCP 客户端调用与返回） | §六（`TOOLS` + GET/START/REJECT/PAUSE/RESUME 原文） |
| OpenCode 配置里能看到控制 MCP 注册项 | §六（`mcp.runtime_control` 片段） |
| 三套测试真实数字，一条不掉 | §七 表 3/4/5（fin-worker 297/0 · paper 183/33skip/0 · api 959/0/4） |
| 未接数据源清单（本段预期为「无」） | §九 |
| 上下文守卫档位与读数 | §十二 |
