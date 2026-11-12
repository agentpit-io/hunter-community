# fin-worker · Runtime Bridge + Temporal Workers

智能炒股板块的**执行编排层**（`plan/ref/01方案-技术基线.md` §5.1 / §11.3）。
它做两件事：

1. **六个交易时点的 Temporal 工作流**（`05 §3.2` M-14）；
2. **策略意图 → Paper Service 命令**的翻译（M-15 Runtime Bridge）。

## 三条不能破的边界

1. **不碰账本。** 依赖里没有 psycopg2、配置里没有连接串、源码里没有写账本的 SQL。
   要改账本只能打 `paper` 的 HTTP 接口（`01方案 §7.4` / §11.3）。
   `tests/test_no_ledger_access.py` 有源码守卫钉住 —— 加一条直连账本的捷径会当场红。
2. **不做策略分析。** 它只把已经成形的 `StrategyDecision` 翻译成命令，
   不读行情、不排名、不产生新的投资观点（`01方案 §11.3` 执行区禁止项）。
3. **不带兜底调度。** 六个时点只归 Temporal；这里没有 crontab、没有进程内定时器
   （`01方案 §5.3`，`tests/test_workflows.py::test_no_fallback_cron_or_in_process_timer`）。

## 六个时点

| 时点（上海） | 工作流 | 干什么 |
|---|---|---|
| 09:15 | `fin.point_0915` | 同步交易日历 + T+1 日切（`confirm_t1`） |
| 09:30 | `fin.point_0930` | 固定示例策略出意图 → 提交委托 |
| 11:30 | `fin.point_1130` | 挂单再撮合 |
| 13:00 | `fin.point_1300` | 挂单再撮合 |
| 14:55 | `fin.point_1455` | 挂单再撮合（尾盘） |
| 15:30 | `fin.point_1530` | 收盘撤单 → 估值 → 对账 |

另有三条 K 线 ETL 触发（`fin.market_etl`，A 股 / 港股 / 美股各一条）。
**取数实现原样复用** api 的 `klines_etl.run_market`，M4 只改「谁决定什么时候拉」。

**非交易日不触发**：每个工作流第一步读 `fin_market_calendar`，三种结果分开处理 ——
交易日继续、非交易日跳过、**日历缺数据则跳过并告警**（未知 ≠ 交易日）。

## 崩溃恢复与「不重复下单」

- **可持久化**：工作流状态在 Temporal 服务端（不在 Worker 进程里）。
- **可恢复**：杀掉 Worker 重启，未完成的工作流从执行历史恢放，未完成的 Activity 重试。
- **可幂等重试**：Activity 是 at-least-once，所以每处写操作都带**业务幂等键**
  （`app/bridge/idem.py`，从「项目 + 交易日 + 时点 + decision_id」推导，不含随机数）。
  重试拿到同一个键 → `paper` 的 `fin_idempotency` 返回原回执 → `fin_trade` 不会多出第二笔。
- 每个时点在 `fin_job` 里登记一条**业务检查点**（`checkpoint` 写「跑到哪一步」）。

## 运行

```sh
cd apps/fin-worker && pip install -r requirements-dev.txt
python -m app.selfcheck            # 打印配置与连通性（外部依赖只告警）
python -m app.main                 # 内部 HTTP(:8300) + Temporal Worker
```

容器：`docker compose --profile fin up -d fin-worker`（需要 `temporal` 先起）。

## 内部接口（`X-Hunter-Internal-Key`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/healthz` | 存活 + 六个时点（**免密钥**，健康检查用） |
| GET  | `/internal/points` | 六个时点的定义 |
| POST | `/internal/trigger/{point}` | 手工触发某个时点（补跑 / 验收）；已跑过 → 409 |

## 测试

```sh
cd apps/fin-worker && python -m pytest tests/ -q
```

不连网、不连库、不连 Temporal：全部靠假 HTTP 传输与纯函数。
