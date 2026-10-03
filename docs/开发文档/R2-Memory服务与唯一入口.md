# R2 · Memory Service 与唯一入口（`memory.append_evidence` / `memory.query`）

> 链路：智能炒股 · 第四段「统一经验与迭代（记忆系统）」· 阶段代号 **R2**
> 依据：`plan/记忆系统追加规则.md` §五（红线 1/2/4/5）· `plan/记忆系统_需求分析与实现方案.md` §三 / §六 · `plan/R2.md`
> 上一阶段：`docs/开发文档/R1-经验三表落库.md`（迁移 `0041_memory_core.sql`，三张表已就绪）
> 上海时间：2026-10-03 22:56
> 本轮范围：**只做后端服务与两个工具**。不写工作流（R3）、不碰界面（R4）、不碰 `apps/fin-worker/**`（除守护测试扩展）。

---

## 一、做了什么

把 R1 的三张表装进**唯一入口**：一个服务模块 + 一条双通道路由。**过滤一律在服务端，不在调用方。**

| # | 落点 | 作用 |
|---|---|---|
| 1 | `apps/api/app/services/fin/memory.py`（**新增**，约 560 行） | **全仓唯一**碰 `fin_experience*` 三张表的模块。对外只有两个函数：`append_evidence(...)`（唯一写入口）· `query(...)`（唯一读入口，`freeze=True` 时顺带冻结） |
| 2 | `apps/api/app/routers/fin_memory.py`（**新增**，约 220 行） | 双通道路由：内网口令写/读（给 fin-worker）+ JWT 读/写（给前端）。**这一层不做任何过滤**，只判通道、翻错误码 |
| 3 | `apps/api/main.py`（+6 行） | 照 `:423-436` 的写法挂 `app.include_router(fin_memory_router.router, prefix="/api")` |
| 4 | `apps/api/tests/test_fin_memory.py`（**新增**，27 条） | 纯校验（不连库）：规则 1/2/5/6 + 枚举 + 取值范围 |
| 5 | `apps/api/tests/test_fin_memory_router.py`（**新增**，30 条） | 真库路由用例：八条硬校验 + 四条硬过滤 + 零开关 + 时间边界 + 冻结重放 |
| 6 | `apps/api/tests/test_fin_memory_guard.py`（**新增**，3 条） | **唯一入口守护**：全仓 grep 断言 |
| 7 | `apps/fin-worker/tests/test_no_ledger_access.py`（+1 条，共 6 条） | 守护口径**扩展到经验三表** |

**五条对外路径**（router 内的 path 已含 `/internal/...` 与 `/v1/fin/...`，`prefix="/api"` 之后对外）：

| 通道 | 路径 | 谁调 |
|---|---|---|
| 内网口令写 | `POST /api/internal/fin/memory/evidence` | fin-worker（R3） |
| 内网口令读 | `POST /api/internal/fin/memory/query` | 工作流（R3） |
| JWT 读 | `GET  /api/v1/fin/memory/experiences` | 前端（R4） |
| JWT 读 | `GET  /api/v1/fin/memory/snapshots/{memory_snapshot_id}` | 回放 / 审计 |
| JWT 写 | `POST /api/v1/fin/memory/evidence` | 真人（R4 表单） |

**冻结不是第三个工具**（方案 §3.4）：`query(freeze=true)` 一次同时「查到」与「冻住」，返回 `msnap_...`。
「唯一入口」在**工具清单层面**也因此是真的（还是那两个）。

---

## 二、偏离方案的决策与原因

### 2.1 规则 6 的判据：**含任一阿拉伯数字即 400**，**不复用** `report.py:extract_numbers`

方案 §3.2 规则 6 要求「`statement` 里出现阿拉伯数字 ⇒ 400」，并提示「`report.py:408 extract_numbers`
是现成的解析器，先读它再决定直接复用还是写一个更窄的」。

**读完后决定不用它**：`extract_numbers` 会先把**日期**（`\d{4}-\d{2}-\d{2}`）与**时刻**
（`\d{1,2}:\d{2}`）掩掉再抽数字 —— 那对**报告正文**是对的（日期不是数字主张），
但方案 §3.2 点名要拦的正是「15:30」这种写法。复用它会把 `15:30` 静默放行，**与规则 6 的字面要求相反**。

现行判据：`memory.statement_has_numbers()` = 正则 `[0-9０-９]`（半角 + 全角阿拉伯数字）。
纯中文结论（含中文数字「一二三」）不受影响 —— 那是人话的一部分，不是裸数字主张。

**适用范围只有 `statement`**：方案自己的例子 `applicability="港股主板 · 14:30 后"` /
`invalidation_condition="成交额口径变更或样本 < 30"` 都含数字，规则 6 只点名 `statement`。

### 2.2 `evidence_kind='external'` 本轮**一律 400**（方案没写，本模块补的一刀）

`0041` 的 CHECK 允许 `evidence ∈ {trade, report, fact, snapshot, external}`，但规则 3 只给了
四类引用各自「验在不在」的对应表；`external`（外部文档 id）**在库内没有任何可校验的来源**。
收下它就等于绕过规则 3「防编造引用」—— 所以宁可不收，报错里点名可用的四类（「空的比假的好」）。
`0041` 的 CHECK 保持不动（多一个不收的枚举值无害，改动已发布迁移反而违规）。

### 2.3 `supersedes` 参数（规则 8 的落点）与「不许二次推翻」

规则 8 要求「推翻 = 追加一条 `status='已推翻'` 并把原条目 `superseded_by` 指向它；不 DELETE」，
但方案的入参示例里**没有**表达「推翻谁」的字段 —— 这一处必须自己定。本模块加了可选入参
`supersedes`（被推翻的 `experience_id`）：给了就把新条目 `status` 强制成 `已推翻`，并把原条目
`superseded_by` 指过去。同一条经验**已被推翻过**时再推翻 → 400（避免两个「推翻者」互相覆盖，
把 `superseded_by` 静默改成一个中间态）。

### 2.4 `market` 过滤**含跨市场结论**（`market = %s OR market IS NULL`）

`0041` 的列注释写明 `market` 为 `NULL` = **跨市场结论**。所以按某市场查时返回
「该市场结论 + 跨市场结论」；不传 `market` 则返回全部。这是对 schema 注释的忠实读法，
有专门用例（`test_market_filter_includes_cross_market`）盯着。

### 2.5 `holdout_tainted` 按**真值**归一（防「把 true 写成 1」绕过传染）

`bool(ev.get("holdout_tainted"))` —— 任何「看起来为真」的写法（`True` / `1` / `"true"`）都算污染。
防泄露是**不可申诉**的红线，宁可在畸形入参上朝「更不可见」的方向多传染一步，也不能因为
调用方把 `true` 写成 `1` 就静默放行。这个方向只会让经验**更不可见**，不会让风险变大。

### 2.6 其余照方案执行，无偏离

- 不新容器、不新角色、不新密钥：内网口令复用 `X-Hunter-Internal-Key`（`fin_report.py:52` 同一条）。
- 三张表仍在应用库、由 api 启动时 `app.migrate` 自动落（R1 已验）。
- `for_decision=true` 的 `valid_until` 判据用 **`now()`**（照方案 §3.3 规则 3 的字面）；
  `needs_recheck` 同口径（`valid_until IS NOT NULL AND valid_until <= now`），是**派生标志不是状态值**。

---

## 三、测试用例表（命令 / 结果 / 实测数字）

**测试环境**：本机开发栈 `hunter-community-postgres-1`（PG 16，端口 5598）里**新建库 `r2_test`**，
用仓库代码的迁移器在干净库上跑全链（`DATABASE_URL=…/r2_test HUNTER_MIGRATIONS_DIR=$PWD/db/migrations
PYTHONPATH=$PWD/apps/api python -m app.migrate`）→ **42 个迁移全成功**，含 `0041_memory_core.sql`。

| # | 命令 | 结果 |
|---|---|---|
| 1 | `PYTHONPATH=. pytest tests/test_fin_memory.py` | ✅ **27 passed** in 0.23s |
| 2 | `PYTHONPATH=. pytest tests/test_fin_memory_guard.py` | ✅ **3 passed** in 10.95s |
| 3 | `TEST_DATABASE_URL=…/r2_test PYTHONPATH=. pytest tests/test_fin_memory_router.py` | ✅ **30 passed**, 1 warning in 4.25s |
| 4 | fin-worker 容器内 `pytest tests/test_no_ledger_access.py` | ✅ **6 passed** in 0.15s（5 条旧 + 1 条新） |
| 5 | fin-worker 容器内 `pytest tests/`（整目录） | ✅ **135 passed** in 2.12s |
| 6 | `pytest apps/api/tests/`（**改动前**，忽略新增 3 文件） | 519 passed, 76 skipped in 41.37s |
| 7 | `pytest apps/api/tests/`（改动后，无 `TEST_DATABASE_URL`） | **549 passed, 106 skipped** in 53.19s |
| 8 | `TEST_DATABASE_URL=…/r2_test pytest apps/api/tests/`（改动后，带真库全量） | **651 passed, 4 skipped** in 73.46s |

**改动前后对照（出口标准第 9 条）**：519 → 549 passed（+30：新增的 27 + 3 条纯函数 / 守护用例），
另有 30 条真库用例在无 `TEST_DATABASE_URL` 时按设计 skip（第 7 行 106 = 76 + 30），
带上真库后全部转绿（第 8 行 651 = 549 + 30 真库用例 + 72 条既有真库用例转绿）。**总数只增不减。**

---

## 四、关键证据原文

### 4.1 五条路由真实可调（真实 HTTP，非 TestClient）

起法（一次性容器，仓库 `apps/api` 覆盖镜像 `/app`；**不含** `main.py` 的重型 lifespan，避免后台任务去拉上游）：

```bash
docker run --rm -d --name r2-live --network hunter-community_default \
  -v "$PWD/apps/api:/app:ro" -v /tmp/r2live:/r2live:ro -e PYTHONPATH=/r2live \
  -e DATABASE_URL=postgresql://hunter:hunter@postgres:5432/r2_test \
  -e JWT_SECRET=r2-live-secret -e HUNTER_INTERNAL_KEY=r2-live-key -p 127.0.0.1:8899:8000 \
  --entrypoint uvicorn hunter-community-api:dev r2live:app --host 0.0.0.0 --port 8000
```

其中 `r2live.py` = 真实 `AuthMiddleware` + 真实 `fin_memory` 路由（验证挂载用的就是真实中间件）：

```python
from fastapi import FastAPI
from app.middleware.auth import AuthMiddleware
from app.routers import fin_memory
app = FastAPI(); app.add_middleware(AuthMiddleware)
app.include_router(fin_memory.router, prefix="/api")
```

真实 app（`main:app`）里的挂载点实测（`import main` 后打印路由）：

```
mounted fin/memory routes:
   /api/internal/fin/memory/evidence
   /api/internal/fin/memory/query
   /api/v1/fin/memory/evidence
   /api/v1/fin/memory/experiences
   /api/v1/fin/memory/snapshots/{memory_snapshot_id}
```

**① 内网口令写** `POST /api/internal/fin/memory/evidence`（`X-Hunter-Internal-Key: r2-live-key`）

```jsonc
// 请求
{ "project_id":"prj_live_d38a237fae","kind":"hypothesis",
  "statement":"港股缩量到低位后追高胜率显著下降","source":"human_mixed",   // ← 强传，应被忽略
  "evidence":[{"evidence_kind":"report","ref_id":"rpt_live_4557e0e4eb"}],
  "as_of":"2026-09-01T09:00:00+08:00" }
// 响应 200
{"ok":true,"experience":{"experience_id":"exp_29e14303f00e4618afc9bfc1","kind":"hypothesis",
  "status":"待验证","source":"ai","created_by":"ai","exposure_scope":"searchable",
  "holdout_tainted":false,"as_of":"2026-09-01T01:00:00+00:00", … }}
```

**② 内网口令读** `POST /api/internal/fin/memory/query`（`freeze=true`）

```jsonc
// 请求
{ "project_id":"prj_live_d38a237fae","for_decision":true,"freeze":true,
  "purpose":"decision","trade_date":"2026-10-03","point":"1430",
  "as_of":"2027-01-01T00:00:00+08:00" }
// 响应 200
{ "memory_snapshot_id":"msnap_ddf1de0b3abe4868a41e338e",
  "as_of_basis":"2027-01-01T00:00:00+08:00",
  "items":[{"experience_id":"exp_202d94bdce3b4b9f9a46fa3f","kind":"verified","status":"已确认",
    "statement":"缩量整理后突破的持续性更强","method":"全样本回测","sample_size":42,
    "confidence":0.71,"evidence_count":1,
    "evidence":[{"evidence_kind":"report","ref_id":"rpt_live_4557e0e4eb"}],"needs_recheck":false}] }
```

**③ JWT 读** `GET /api/v1/fin/memory/experiences?project_id=…&as_of=2027-01-01T00:00:00%2B08:00`

```jsonc
// 响应 200（真实 AuthMiddleware 校验 Bearer JWT；未带 token → 401，见 4.6）
{ "memory_snapshot_id":null,"as_of_basis":"2027-01-01T00:00:00+08:00",
  "items":[{"experience_id":"exp_f59d7f90d3a249d78720f57d","kind":"verified","status":"已确认", …},
           {"experience_id":"exp_29e14303f00e4618afc9bfc1","kind":"hypothesis","status":"待验证", …}] }
```

**④ JWT 写** `POST /api/v1/fin/memory/evidence`（body 强传 `"source":"ai"`）

```jsonc
// 响应 200 —— source 被强制成 human_mixed，created_by 用了 JWT 的 sub
{"ok":true,"experience":{"experience_id":"exp_0726513ac09146e2b9d7cd57","kind":"fact",
  "status":"已确认","source":"human_mixed","created_by":"user:6cc6a2e6-d16f-4a4b-970d-50378827222b", … }}
```

**⑤ JWT 回放** `GET /api/v1/fin/memory/snapshots/msnap_621ca8af97654b5aa039ab30`

```jsonc
// 响应 200 —— 按 id 取冻结集合，不重跑查询
{ "memory_snapshot_id":"msnap_621ca8af97654b5aa039ab30","project_id":"prj_live_d38a237fae",
  "purpose":"decision","query_filter":{"for_decision":false,"as_of_basis":"2027-01-01T00:00:00+08:00",
    "exposure_scope":"searchable"},
  "experience_ids":["exp_0726513ac09146e2b9d7cd57","exp_f59d7f90d3a249d78720f57d", … ],
  "items":[ … ] }
```

### 4.2 八条硬校验（逐条：通过的真实响应 + 违规的真实 400）

| 规则 | 请求 | 响应 |
|---|---|---|
| **2** hypothesis 强制待验证（强传 `status=已确认`） | `{"kind":"hypothesis","status":"已确认",…}` | `200` `{"status":"待验证","source":"ai", …}` |
| **5** verified 缺 `method` | `{"kind":"verified","sample_size":10,…}` | **400** `{"detail":"kind='verified' 必须提供 method（怎么验的）"}` |
| **5** verified 缺 `sample_size` | `{"kind":"verified","method":"全样本回测",…}` | **400** `{"detail":"kind='verified' 必须提供 sample_size（样本笔数）"}` |
| **5** verified 齐备 | `{"kind":"verified","method":"全样本回测","sample_size":42,"confidence":0.71,…}` | `200` `{"status":"已确认","sample_size":42,"confidence":0.71}` |
| **6** statement 含「3 笔」 | `{"kind":"fact","statement":"重复 3 笔后胜率下降",…}` | **400** `{"detail":"statement 里不许出现阿拉伯数字（收到 '3'）—— 数字一律通过 evidence_kind='fact' 的证据引用，不要写进结论"}` |
| **6** 纯中文结论（不误杀） | `{"kind":"fact","statement":"缩量之后追高胜率下降",…}` | `200` |
| **1** `evidence=[]` | `{"kind":"fact","evidence":[]}` | **400** `{"detail":"evidence 至少一行 —— 经验必须带证据"}` |
| **3** `ref_id` 不存在 | `{"evidence":[{"evidence_kind":"report","ref_id":"rpt_does_not_exist"}]}` | **400** `{"detail":"证据引用不存在：report rpt_does_not_exist"}` |
| **3** 四类真引用全部接受 | `report` / `trade` / `snapshot` / `fact` 各一条 | `200`（用例 `test_rule3_real_refs_all_accepted` 参数化四类，实建真行） |
| **4** 证据 `holdout_tainted=true` | `{"evidence":[{"evidence_kind":"report","ref_id":"rpt_live_…","holdout_tainted":true}]}` | `200` `{"exposure_scope":"holdout_only","holdout_tainted":true}`（入库核对 `SELECT` 亦为 `holdout_only / t`） |
| **7** 内网通道（强传 `source=human_mixed`） | 见 ① | `source="ai"`, `created_by="ai"` |
| **7** JWT 通道（强传 `source=ai`） | 见 ④ | `source="human_mixed"`, `created_by="user:6cc6a2e6-…"` |
| **8** 推翻 | 先写旧条目 `exp_cd6126460a0c45528032371f`，再 `{"supersedes":"exp_cd61…"}` | 新条目 `200` `{"status":"已推翻"}`；`SELECT … WHERE experience_id='exp_cd61…' OR superseded_by='exp_cd61…'` → `exp_cd6126460a0c45528032371f\|已确认\|exp_6948ac588a424e549ec48e8e`（**旧条目还在、指了过去、没删**） |
| **8** 推翻不存在的目标 | `{"supersedes":"exp_does_not_exist"}` | **400** `{"detail":"要推翻的经验不存在：exp_does_not_exist"}` |

> **规则 3 的引用映射**（`memory.REF_TABLE_SQL`）：
> `trade`→`fin_trade.trade_id` · `report`→`fin_report.report_id` ·
> `fact`→`fin_report_fact.report_id||':'||metric_key` · `snapshot`→`fin_snapshot.snapshot_id`。

### 4.3 零开关实测（红线，最关键的一条）

先写一条 `holdout_only` 经验（4.2 规则 4），再穷举「想把它放回来」的入参 —— 基线与每一组合逐条列出：

```
基线可见: ['新结论取代旧结论', '旧结论将被推翻', '真人写下的结论', '内网写下的结论',
           '缩量之后追高胜率下降', '缩量突破持续性更强', '港股缩量后追高胜率下降']   ← 无「保底测试集上的结论」
  传 "include_holdout":true → 不含保底结论 ✓
  传 "debug":true → 不含保底结论 ✓
  传 "admin":true → 不含保底结论 ✓
  传 "include_holdout":true,"debug":1,"admin":"yes" → 不含保底结论 ✓
  传 "exposure_scope":"holdout_only" → 不含保底结论 ✓
  传 "holdout_only":true → 不含保底结论 ✓
  传 "whatever":123 → 不含保底结论 ✓
JWT 通道同问：
  ?include_holdout=true → 不含保底结论 ✓
  ?debug=1 → 不含保底结论 ✓
  ?admin=true → 不含保底结论 ✓
```

**结论：没有任何参数能把 `holdout_only` 放回来** —— 不是「默认关着的开关」，是**根本没实现那个开关**
（服务层 `query()` 的签名里没有它们的落点，路由的 pydantic 模型里也没有；未知键被忽略）。
用例 `test_zero_switch_holdout_invisible_even_with_every_param` 把这件事钉死。

### 4.4 时间边界实测

```
写入：exp A（as_of 2026-01-01，statement「一季度形成的认知」）
      exp B（as_of 2026-07-01，statement「三季度形成的认知」）
用早的基准查：POST /query {"as_of":"2026-03-01T00:00:00+08:00"}
→ as_of_basis = 2026-03-01T00:00:00+08:00
→ 可见: ['一季度形成的认知']           ← 只出前一条，晚的那条读不到
用晚的基准查：{"as_of":"2026-09-01T00:00:00+08:00"} → 两条都在（用例 test_time_boundary_only_earlier_experience）
```

### 4.5 冻结重放实测（两次输出逐字节对照）

```
冻结：POST /query {"freeze":true,"purpose":"decision","trade_date":"2026-10-03","point":"1430"}
   → 冻结 id = msnap_06972f5cc4cb493d9d31201d · 冻结 9 条
冻结之后新增一条经验（「冻结之后才形成的结论」）
首次回放：GET /api/v1/fin/memory/snapshots/msnap_06972f5cc4cb493d9d31201d
   9 条 id: [exp_6948ac588a424e549ec48e8e, exp_cd6126460a0c45528032371f, exp_63d8b00f3144444ebcb95624,
             exp_94eb196172f94d88b0454a96, exp_b543d365366f48d896db8371, exp_e4f7ec5f87b14b0da0b4376b,
             exp_6ac73256433b478bb153461a, exp_ea4d9a5290284dc1a12b5ecd, exp_d39d86dcb6364fe0aea77f40]
二次回放：GET …snapshots/msnap_06972f5cc4cb493d9d31201d      ← 逐字节同一批
   9 条 id: [ …同上九条，一字不差… ]
不冻结再查（{"as_of":"2027-01-01"}）：10 条          ← 现在能查到后来那条
```

**回放不因新增而变多** —— 这就是「历史回放不得读后来才形成的经验」的技术兑现。
（详细断言见 `test_freeze_then_replay_ignores_later_writes`。）

### 4.6 鉴权

- 内网通道口令错 → **401** `{"detail":"internal auth failed"}`（用错 key 实测）。
- JWT 通道无 token → 真实 `AuthMiddleware` 拦下 **401**
  （日志 `[auth] 401 UNAUTHORIZED path=/api/v1/fin/memory/experiences`）。
- JWT 跨用户 → **404**（`test_cross_user_query_and_append_404`，不区分「不存在」与「无权限」）。

---

## 五、验收清单（逐条核对）

- [x] `memory.py` / `fin_memory.py` 关键段 + `main.py` 的两行挂载 → §一 · §四.1（路由挂载实测打印五条）
- [x] 五条路由真实请求 / 响应 → §四.1（内网 2 条 + JWT 3 条，真实 HTTP）
- [x] 八条硬校验逐条（通过 + 违规 400）→ §四.2
- [x] 零开关：`holdout_only` 查不到 + 穷举入参组合仍查不到 → §四.3
- [x] 时间边界：只出前一条 → §四.4
- [x] 冻结重放：两次输出逐字节对照 → §四.5
- [x] AI / 人分开 + 强传 `source` 被忽略 → §四.1 ①/④ · §四.2 规则 7
- [x] 两条守护测试 pytest 统计 → §三 行 2（3 passed）/ 行 4（6 passed）
- [x] `pytest apps/api/tests/` 整体统计（与改动前对照）→ §三 行 6/7/8
- [x] 上下文守卫读数与档位 → §六

---

## 六、上下文守卫读数与档位

```
上下文 25.9% · ok · 约 258,607 / 1,000,000
  实测（最后一条 usage）   258,391
  粗估增量（之后写入的）   216（756 字节 ÷ 3.5）
  合计                     258,607
  阈值 warn/compact/stop   70% / 85% / 92%
  → 不用管。
```

档位 **0（ok）**，退出码 0。余量充足，未触发交接。

---

## 七、遗留问题与 R3 交接

1. **R3 要做的第一件事**：`fin.review` 复核工作流经 `HunterApiClient` 调
   `POST /api/internal/fin/memory/evidence` 与 `POST /api/internal/fin/memory/query`（`freeze:true`）；
   **fin-worker 全程不碰数据库** —— 守护测试已扩到经验三表，加了调用后仍应绿。
2. **`FIN_REVIEW_DELAY_MINUTES`（默认 30）** 落在 `docker-compose.yml` fin-worker 段 + `.env.example`（Q4，R3 做）。
3. **`uncertainty` 口径未定**（Q3）：本轮允许 NULL，只在 `verified` 时要求 `sample_size`。谁定谁改。
4. **`supersedes` / `external` 两处是本模块自己定的口径**（§2.2 / §2.3），R3 / R4 若要别的形态先回来改这里。
5. **`for_decision` 的 `valid_until` 判据用 `now()`**（照方案 §3.3 规则 3 字面）。若将来要严格按回放基准
   判过期，要连 `needs_recheck` 一起改成 `as_of_basis` —— 两处同口径，别只改一处。
6. **本机开发库 `hunter` 仍停在 `0037`**：下次 api 重启会自动补 `0038`–`0041`（迁移器设计行为，非故障）。
   R2 的验收用的是**新建的空库 `r2_test`**（干净库上跑全链）。
7. **`r2-live` 一次性容器与 `r2_test` 库**是本次现场验收的脚手架，不属交付物。

---

*R2 收尾四件套：本文档 · 合并 `main` 并 push · notify-qq 邮件 · `progress.log` 追加 `[2026-10-03 22:56] R2 · 完成`。*
