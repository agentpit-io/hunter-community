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
| PUT/GET | `/api/v1/instruments/{code}` · `/market-calendar/{date}` · `/fee-models/{version}` | 风控的输入（参考数据，可更正） |

**没有** `PUT/PATCH/DELETE` 指向成交、流水、持仓、估值、对账的任何一条。
`PUT` 只用在参考数据的 upsert 上（标的 / 日历 / 费率——它们不是账本）。
`/docs` 与 `/openapi.json` 关闭：它们不吃 app 级鉴权，实测无密钥也能 200。

## 账本语义（`app/ledger.py` 头部有完整表）

- 买入 = `freeze → buy → fee` 三条流水（冻结在同一事务里归零）；
- 卖出 = `sell → fee → tax` 三条；
- `amount` = 该条对**现金总额**的变动，因此 `Σ(amount) == 可用 + 冻结`；
- 一笔成交的流水之和 == 那笔的现金影响（买入 `−(金额+费用)`，卖出 `+(金额−费用)`）。

对账（`app/recon.py`）每天核这七项：现金合计 / 资产负债表恒等式 / 买入整手 /
成交与流水逐笔对上 / 持仓 = 成交净额 / 版本号 = 成交笔数 / 无挂单时冻结归零。

## 测试

```sh
cd apps/paper
python -m pytest tests/ -q                      # 纯风控用例 + 鉴权（不连库）
PAPER_TEST_DSN=postgresql://fin_paper_rw:pw@127.0.0.1:5432/hunter \
python -m pytest tests/ -q                      # 另加账本端到端
```
