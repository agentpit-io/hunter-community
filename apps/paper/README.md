# Paper Service · 唯一模拟账本 + 确定性风控

智能炒股板块（`apps/web/app/finance/`）的执行区。**全仓唯一能写账本的服务**。

依据：`plan/ref/01方案-技术基线.md` §七（模拟交易：以独立 Paper Service 为核心）、
`plan/ref/08-开源产品集成部署方案.md` §二/§八、`plan/ref/09-数据库结构方案.md` §4.5/§4.6。

## 三条不能破的边界

1. **只做模拟。** `PAPER_MODE` 固定 `PAPER`，非该值**拒绝启动**；请求里出现
   `live` / `real` / `broker` / `account_no` 之类字段**直接报错**（不是忽略）。
2. **只追加。** 账本表（`fin_trade` / `fin_cash_ledger` / `fin_snapshot` /
   `fin_valuation` / `fin_recon_log`）在**库层**就只授了 `INSERT`/`SELECT`；
   服务里也没有任何改历史 / 删流水的端点。启动自检会验证这一点 —— 把连接串指到
   管理员角色（对追加表有 UPDATE）会**起不来**。
3. **余额是算出来的。** 可用 / 冻结 = `fin_cash_ledger` 最后一行；不存「余额字段」。

## 运行

```sh
# 依赖
cd apps/paper && pip install -r requirements-dev.txt

# 启动自检（模式 / 口令 / 库连通 / 表齐 / 角色权限）
PAPER_MODE=PAPER HUNTER_INTERNAL_KEY=k \
PAPER_DATABASE_URL=postgresql://fin_paper_rw:pw@127.0.0.1:5432/hunter \
python -m app.selfcheck

# 起服务
PAPER_MODE=PAPER HUNTER_INTERNAL_KEY=k \
PAPER_DATABASE_URL=postgresql://fin_paper_rw:pw@127.0.0.1:5432/hunter \
uvicorn app.main:app --port 8000
```

容器：`docker compose --profile fin up -d paper`（先跑
`scripts/fin_provision_paper_role.sh`）。

## 鉴权

除 `/healthz` 外，**所有请求必须带 `X-Hunter-Internal-Key`**，值等于
`HUNTER_INTERNAL_KEY`；不带或不对 → 401。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/healthz` | 存活 + 模式（唯一免密钥端点） |
| POST | `/api/v1/orders` | 提交委托：风控 → 成交 → 记账。**唯一改账本的入口** |
| GET  | `/api/v1/projects/{id}` | 项目 + 参数 + 现金 + 持仓 |
| POST | `/api/v1/projects/{id}/funding` | 把本金记成一条 `deposit` 流水（幂等） |
| GET  | `/api/v1/projects/{id}/orders` `/trades` `/cash` `/positions` | 七类实体的读 |
| POST | `/api/v1/projects/{id}/valuation` | 估值（缺价 → 409，不拿成本价顶替） |
| GET  | `/api/v1/projects/{id}/valuation/latest` | 最近一次估值 |
| POST/GET | `/api/v1/projects/{id}/recon` | 对账：跑七项检查并写 `fin_recon_log` |
| PUT/GET | `/api/v1/instruments/{code}` · `/market-calendar/{date}` · `/fee-models/{version}` · `/execution-models/{version}` | 风控与撮合的输入（参考数据，可更正） |
| POST | `/api/v1/snapshots?code=…` | 取一张快照（M3 起快照由服务端采集） |
| GET  | `/api/v1/snapshots/{snapshot_id}` · `/api/v1/snapshots?code=…` | 读快照 |
| POST | `/api/v1/orders/{order_id}/cancel` | 撤一张挂单并解冻 |
| POST | `/api/v1/projects/{id}/orders/expire` | 收盘（`close`）或过期（`validity`）批量撤单解冻 |
| POST | `/api/v1/projects/{id}/orders/match-open` | 拿新快照再撮一遍挂单（M4 的时点工作流调它） |
| POST | `/api/v1/jobs` · `/jobs/{id}` · `/jobs/{id}/{running,succeed,fail,cancel}` | 长任务协议（`01方案 §10.4`） |

**没有** `PUT/PATCH/DELETE` 指向成交、流水、持仓、估值、对账的任何一条。
`PUT` 只用在参考数据的 upsert 上（标的 / 日历 / 费率 / 执行模型——它们不是账本）。
`/docs` 与 `/openapi.json` 关闭：它们不吃 app 级鉴权，实测无密钥也能 200。

## 四步链路（M3 · `app/matching/`）

`POST /api/v1/orders` 走完：**取快照 → 过规则 → 按快照撮合 → 记账并绑快照编号**。

| 口径 | 取值 |
|---|---|
| 成交价 | **委托到达时刻的快照价**（不用当日均价、不用收盘价、不回填） |
| 限价单 | 快照价**不劣于**限价才成交（买 `≤`、卖 `≥`），成交在快照价上 |
| 市价单 | **对手价 + 滑点**（买卖一 `+`、卖买一 `−`；滑点 = `slippage_ticks × tick_size`） |
| 不可成交 | 挂单（`pending`，买入冻结资金）；收盘仍未成交 → 撤单（`expired`）并**解冻** |
| 行情断流 | `missing_flag=true` → **一律不成交**（宁可挂单，绝不用过期价成交） |
| 部分成交 | 一期 `part_fill=false`：整笔成交或挂单 |

快照编号 `SNAP-{日期}-{时刻}-{代码}`，**时刻来自数据源**（`PAPER_QUOTE_URL` 指向
api 的 `GET /api/quote/{code}`，背后是现仓的 `providers.data_source`）。
数据源没给时间戳 → **不落这一行**，委托被拒（拿不到报价就不该有成交）。

幂等（`app/idempotency.py`）：同键同内容 → 返回原回执；同键不同内容 → 409 不覆盖。
**先返回原回执、再校验账户版本**（`01方案 §11.1` 末两条）——
写反了会让「已经成功、只是回执丢了」的重试被版本校验拒掉。

## 账本语义（`app/ledger.py` 头部有完整表）

- 买入（受理即成交）= `freeze → buy → fee [→ unfreeze]`；
- 买入（挂单）= `freeze`，成交时再 `buy → fee [→ unfreeze]`，撤单时 `unfreeze`；
- 卖出 = `sell → fee → tax`（券的占用不建账本列，由未成交卖单算出来）；
- `amount` = 该条对**现金总额**的变动，因此 `Σ(amount) == 可用 + 冻结`；
- 一笔成交的流水之和 == 那笔的现金影响（买入 `−(金额+费用)`，卖出 `+(金额−费用)`）。

对账（`app/recon.py`）每天核这八项：现金合计 / 资产负债表恒等式 / 买入整手 /
成交与流水逐笔对上 / 持仓 = 成交净额 / 版本号 = 成交笔数 / 无挂单时冻结归零 /
**冻结额 = 未成交买单的占用之和**。

## 测试

```sh
cd apps/paper
python -m pytest tests/ -q                      # 快照 / 撮合 / 风控 / 鉴权（纯函数，不连库）
PAPER_TEST_DSN=postgresql://fin_paper_rw:pw@127.0.0.1:5432/hunter \
python -m pytest tests/ -q                      # 另加账本端到端、幂等、长任务
```
