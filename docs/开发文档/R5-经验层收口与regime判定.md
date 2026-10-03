# R5 · 经验层收口：迁移 `0042` + regime 判定器 + 标的市场规范化

> 阶段代号 `R5`（第四段「统一经验与迭代」· `plan/R5.md`）。
> 依据：`plan/记忆系统追加规则.md` §三 3.3 / §四 / §五 · `00记忆新方案/03…代码修改方案.md` §4-A ·
> `00记忆新方案/01…调整建议.md` §三 / §八 8.1（`0042` DDL 草案）· `R3` / `R4` 成果文档。
> 本轮把 `03` 的 **P1 收口**做完：经验有了**结构化标签**（能确定性聚合）、有了**市场状态**
> （不再跨 regime 污染）、标的有了**规范身份**（`HK:00700` 与 `US:0700` 不再撞车）。

---

## 一、做了什么

| # | 交付 | 落点 |
|---|---|---|
| 1 | 迁移 `0042_memory_layer.sql`（九列 + 3 GIN + 1 组合索引，**只加不回填**） | `db/migrations/0042_memory_layer.sql` |
| 2 | 标的市场规范化 `normalize(market, code) -> "<MARKET>:<CODE>"` | `apps/api/app/services/fin/symbols.py`（新） |
| 3 | regime 判定器（固定四元组 · 版本化阈值 · 缺行情 `unknown`） | `apps/api/app/services/fin/regime.py`（新） |
| 4 | Memory Service 接纳九列 + 规则 9（策略记忆必须来自真值） | `apps/api/app/services/fin/memory.py` |
| 5 | 复核工作流接线：`review_propose` 打 `symbols` / `regime_tags` / `regime_source` | `apps/api/app/services/fin/review.py` |
| 6 | 唯一入口透传新字段 | `routers/fin_memory.py` · `apps/fin-worker/app/bridge/hunter_api.py` · `app/activities.py` |
| 7 | **R3 拦单判定改读 `symbols` 列 + 加 `polarity` 负向层** | `apps/fin-worker/app/strategy/memory_gate.py` |

### 1.1 迁移 `0042`（只加列，不回填）

九个新列（全文见 §四）：`memory_layer` · `polarity` · `symbols` · `strategy_keys` ·
`regime_tags` · `regime_source` · `importance` · `last_validated_at` · `duplicate_of`。

三条硬约束逐条落地：

- **旧行一律 `NULL`。** 迁移文件里**没有任何 `UPDATE` / `INSERT`**，不从 `applicability`
  抠标的、不从 `statement` 推 regime、不给 `importance` 编默认值。证据见 §五.3：
  迁移后 `symbols IS NOT NULL` = **0**，存量行 3 条。
- **`symbols` 做市场规范化**：`symbols.py` 是唯一口径；服务层**再校验一次形态**
  （裸代码 `0700` → 400），杜绝「绕过规范化」把两个市场重新撞回去。
- **`importance` 只影响展示排序、不参与统计加权。** 写进了 `0042` 文件头、
  `memory.py` 模块文档、以及本文件 §八 的 `R6` 交接。

**迁移纪律**（照 `0041`）：只加列 / 只加索引、`IF NOT EXISTS`、`DROP CONSTRAINT IF EXISTS`
再 `ADD`、**不带 `BEGIN;`/`COMMIT;`**、可重复执行。见 §五.1 / §五.2。

### 1.2 regime 判定器

`regime.py` 输出**固定四元组**（一字不多、一字不少）：

```python
{"label": "<bull|bear|range|unknown>", "rule_version": "regime-v1",
 "source_snapshot_id": "<SNAP-… 或 null>", "as_of": "<ISO 时刻>"}
```

四条不许退让的规则（`R5.md` §一.2）逐条落地见 §六。

### 1.3 标的市场规范化

`normalize('HK','00700') = 'HK:00700'` · `normalize('US','0700') = 'US:0700'` ·
**两者不相等**（§五.7）。`review_propose` 用本次复盘的成交代码生成 `symbols`，
经唯一写入口落库；`memory.query` 的返回体带上它。

### 1.4 R3 拦单判定改读 `symbols` 列

`memory_gate.is_blocking` 现在是四条同时成立：`kind='verified'` · `status='已确认'` ·
`symbols` 列**全等**包含本次标的 · **负向**（`polarity`）。R3 回归见 §五.8。

---

## 二、偏离方案的决策与原因

| # | 事 | 决定 | 原因 |
|---|---|---|---|
| 1 | **`regime_source` 存什么** | 判定器写它的 **`rule_version`**（如 `regime-v1`）；真人手写时写 `human` | `R5.md` §一.1 明说「与 §2 的 `rule_version` 对齐」；`01 §8.1` 草案写的是 `detector / human`。两者不冲突：**列语义 = 这些标签的来源键**，判定器给的来源就是它的规则版本。已写进 `0042` 文件头 |
| 2 | **`01 §8.1` ⑧（拓宽 `evidence_kind` 到含 `experience`/`evolution`）** | **本轮不做** | 那属于 `M4 周度巩固` / `E5 回滚经验`（`R6` 及以后）；`R5.md` §一.1 只要求 `fin_experience` 上的九列，§二 明确不做提案层。已写进 `0042` 文件头「需要时另起一个迁移文件」 |
| 3 | **旧行 `symbols IS NULL` 不再拦单** | **接受**（这是 `0042` 与 `03 §4-A` 的必然取舍） | 要么保留文本匹配（没真的改成读列），要么旧行不拦。任务书 §一.1 三条硬约束 + §一.3「不再对 applicability 自由文本做匹配」选了后者。见 §五.8 与 `memory_gate` 模块文档 |
| 4 | **`unknown` 时仍给 `source_snapshot_id`** | 给了（窗口存在但不够长 / 不合法时） | 四元组允许 `null`。给了是**审计信息**：说明「我们确实看过这份行情、只是没判出来」，与「完全没数据源」区分开。完全无 observations / 无市场 → `null`（§五.5 展示两种） |
| 5 | **判定器不接网络行情源** | `observe()` 默认读 `fin_snapshot`；本部署没有基准快照 → `None` → `unknown` | `03 §4-A` 规则 4：「没有可靠数据源时继续复盘、停止策略提案」+ `R5.md` §一.2 规则 4：「本轮只把判定器的返回值和「不可用」状态做出来」。**不拿一只持仓股票冒充大盘**，也不给复盘路径引入一个会超时 / 触 WAF 的外网依赖（`CLAUDE.md` 腾讯 WAF 前科）。接基准行情时只动 `observe()` 一处 |
| 6 | **`regime.py` 的规则表放代码里（`RULES` 字典），不是数据库表** | 采纳 | `R5.md` §一.2 说「带版本号的**表或配置**」。规则是随代码发布的、要 review 的常量，放代码里与 `CONTENT_HASH_VERSION` 等既有版本常量同路数；换阈值 = 换版本键（`RULES` 加一条），旧结论永不被新阈值重解释 |
| 7 | **`memory_layer` 闭集为什么是这四个** | 见 §三 | 从 `02` 的「多层记忆」一节取（Episodic / Semantic / Procedural），外加 `strategy` |

---

## 三、`memory_layer` 闭集的选定理由（`R5.md` §五.1 要求）

**闭集 = `('episodic', 'semantic', 'procedural', 'strategy')`**（`0042` 的 `CHECK` +
`memory.py:MEMORY_LAYERS`，两处逐字一致）。

| 取值 | 来自 | 是什么 | 谁写它 |
|---|---|---|---|
| `episodic` | `02 §2.2` Episodic Memory | 某一次交易 / 某一天发生了什么的**事件记忆** | `fin.review`（复核回路产出的都是当日事件） |
| `semantic` | `02 §2.3` Semantic Memory | 从多次事件里提炼的**规律**（「缩量后追高胜率下降」） | 周度巩固（`M4`，`R6`+） |
| `procedural` | `02 §2.4` Procedural Memory | 「以后该怎么做」的**行为经验**（`IF regime=… THEN reduce …`） | 提案 / 生效链路（`R6`–`R8`） |
| `strategy` | `01 §8.3` 规则 9 | **策略记忆**：耦合到某个稳定策略版本的结论 | 回测 / 实盘真值 |

**为什么不许自由文本：** 这一列的用途就是**确定性聚合**（`GROUP BY memory_layer`，
`R5.md` §一.4 的自证）。自由文本（「重要教训」/「经验之谈」）会让同一类结论裂成几十个
键，聚合出来的东西没人能读、也没法喂给 `R6` 的提案算法。给不出这四个之一时，
**留 `NULL`**（旧行就是这个形态）—— 空的比假的好。

**为什么 `strategy` 单独一档、且必须来自真值：** 它是唯一会**驱动改策略参数**的那一层。
`01 §8.3` 规则 9 要求 `memory_layer='strategy'` ⇒ 必须有 `strategy_keys`（稳定版本键）
且 `kind='verified'`。猜一条进来，进化就会基于想象改参数 —— 那是 `01 §3.6 承重三`
点名的「自进化头号死法」的入口。

---

## 四、`0042_memory_layer.sql` 全文

```sql
-- ════════════════════════════════════════════════════════════════════════
-- 0042_memory_layer.sql · 智能炒股 · 经验层收口（结构化标签 / 市场状态 / 标的规范身份）
-- 依据：plan/R5.md §一.1 · 01…调整建议.md §八 8.1 · 03…代码修改方案.md §4-A
-- 只做加法；旧行一律保持 NULL，不凭文本猜测；不带 BEGIN;/COMMIT;（migrate.py 包事务）
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS memory_layer TEXT;
ALTER TABLE fin_experience DROP CONSTRAINT IF EXISTS fin_experience_memory_layer_chk;
ALTER TABLE fin_experience ADD CONSTRAINT fin_experience_memory_layer_chk
  CHECK (memory_layer IS NULL OR memory_layer IN ('episodic','semantic','procedural','strategy'));

ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS polarity TEXT;
ALTER TABLE fin_experience DROP CONSTRAINT IF EXISTS fin_experience_polarity_chk;
ALTER TABLE fin_experience ADD CONSTRAINT fin_experience_polarity_chk
  CHECK (polarity IS NULL OR polarity IN ('support','refute','neutral'));

ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS symbols TEXT[];
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS strategy_keys TEXT[];

ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS regime_tags TEXT[];
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS regime_source TEXT;

ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS importance NUMERIC(9,6);
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMPTZ;
ALTER TABLE fin_experience ADD COLUMN IF NOT EXISTS duplicate_of TEXT
  REFERENCES fin_experience(experience_id);

CREATE INDEX IF NOT EXISTS fin_experience_symbols_gin
  ON fin_experience USING GIN (symbols);
CREATE INDEX IF NOT EXISTS fin_experience_strategy_keys_gin
  ON fin_experience USING GIN (strategy_keys);
CREATE INDEX IF NOT EXISTS fin_experience_regime_gin
  ON fin_experience USING GIN (regime_tags);
CREATE INDEX IF NOT EXISTS fin_experience_layers
  ON fin_experience (project_id, memory_layer, polarity);
```

> 磁盘上的文件带完整中文注释（每列为什么这么定、`importance` 不参与加权、
> `duplicate_of` 不删、⑧ 为何不做），上面是**结构部分**的摘录；全文见仓库文件。

---

## 五、验收证据（逐条真实输出）

### 5.1 / 5.2 迁移连跑两遍幂等 + 九列三索引

空库（`r5_test`）全量迁移 **43 个成功**，**第二遍执行 0 个**（幂等）：

```
[43/43] 0042_memory_layer.sql 完成，耗时 0.014 秒
② 增量迁移完成：本次执行 43 个；两阶段总耗时 1.34 秒
--- 第二遍 ---
② 增量迁移（db/migrations）：共 43 个迁移文件，已应用 43 个，本次待执行 0 个
② 增量迁移完成：本次执行 0 个；两阶段总耗时 0.14 秒
```

开发库（`hunter`）增量 1 个（`0042`）：

```
[1/1] 0042_memory_layer.sql 完成，耗时 0.020 秒
```

九个新列（`information_schema.columns`）+ 两个 `CHECK`：

```
duplicate_of      | text                     | null=YES
importance        | numeric                  | null=YES
last_validated_at | timestamp with time zone | null=YES
memory_layer      | text                     | null=YES
polarity          | text                     | null=YES
regime_source     | text                     | null=YES
regime_tags       | ARRAY                    | null=YES
strategy_keys     | ARRAY                    | null=YES
symbols           | ARRAY                    | null=YES

fin_experience_memory_layer_chk : CHECK (memory_layer IS NULL OR memory_layer IN ('episodic','semantic','procedural','strategy'))
fin_experience_polarity_chk     : CHECK (polarity IS NULL OR polarity IN ('support','refute','neutral'))
```

四个索引（三个 GIN + 一个组合）：

```
fin_experience_symbols_gin        | ... USING gin (symbols)
fin_experience_strategy_keys_gin  | ... USING gin (strategy_keys)
fin_experience_regime_gin         | ... USING gin (regime_tags)
fin_experience_layers             | ... USING btree (project_id, memory_layer, polarity)
```

### 5.3 未回填的证明

```
total=3, symbols_not_null=0, polarity_not_null=0, memory_layer_not_null=0, importance_not_null=0
```

存量行 3 条，**`symbols IS NOT NULL` 计数 = 0**（迁移前也是 0）——没有回填。

### 5.4 regime 四元组（真实跑一次）

判定器拿**真实基准日线**跑（来源 `web.ifzq.gtimg.cn` 腾讯 K 线，沪深300 `sh000300`，250 根）：

```
bars=250 last=4357.62 last_date=2026-09-30
四元组: {'label': 'bear', 'rule_version': 'regime-v1',
         'source_snapshot_id': 'SNAP-BENCH-CN_A-sh000300-2026-09-30',
         'as_of': '2026-10-03T17:30:00+08:00'}
is_available: True
```

同一轮三市场（恒生 `hkHSI` 250 根 → `bear`；标普 `usINX` 只取到 1 根 → `unknown`）。

### 5.5 行情缺失 → `unknown`

```
observations=None     -> {'label': 'unknown', 'rule_version': 'regime-v1',
                          'source_snapshot_id': None, 'as_of': '2026-10-03T17:30:00+08:00'}
窗口不足(3 根)        -> {'label': 'unknown', 'rule_version': 'regime-v1',
                          'source_snapshot_id': None, 'as_of': '2026-10-03T17:30:00+08:00'}
```

`test_fin_regime.py` 另有 8 类坏输入（空 / 差一根 / 含 `None` / 价格为 0 / 负价 /
非序列 …）逐条断言 `unknown`，且 `is_available=False`。

### 5.6 label 稳定性（同一 `source_snapshot_id` 两次判定）

```
一次: {'label': 'bear', 'rule_version': 'regime-v1',
       'source_snapshot_id': 'SNAP-BENCH-CN_A-sh000300-2026-09-30', 'as_of': '2026-10-03T17:30:00+08:00'}
二次: {'label': 'bear', 'rule_version': 'regime-v1',
       'source_snapshot_id': 'SNAP-BENCH-CN_A-sh000300-2026-09-30', 'as_of': '2026-10-03T17:30:00+08:00'}
```

`label` 与 `rule_version` 两次完全相同（`test_fin_regime.py::test_same_input_is_deterministic`
把它固化成断言）。

### 5.7 规范化真值表 + `symbols` 精查

```
normalize('HK','00700') = HK:00700
normalize('US','0700')  = US:0700
两者相等？              = False
```

经**唯一写入口**写三条带 `symbols` 的经验后，按数组包含精查 —— **各只命中自己的行**：

```
symbols @> ARRAY['HK:00700'] -> [('exp_fa2a4139fb7b4f5ebc34fb04', ['HK:00700'])]
symbols @> ARRAY['US:0700']  -> [('exp_034155a65f734a139dbe8ce3', ['US:0700'])]
```

（这两条演示经验已按 `applicability='R5 演示'` 从开发库清理；查询与清理都经唯一入口/直连只读。）

### 5.8 R3 拦单回归（改读 `symbols` 之后仍 `halted`）

```
冻结经验集 snapshot=msnap_44f2c96066cd41bc890a9e7e 共 1 条
blocking_experience(CN_A/601398) -> {'experience_id': 'exp_f130b92aabc8423bb538e585',
    'statement': '甲股标的缩量整理后追高的回撤概率显著上升',
    'symbols': ['CN_A:601398'], 'polarity': 'refute', 'kind': 'verified', 'status': '已确认'}
=> halted? True
跨市场不误拦 blocking_experience(HK/00700) -> None
```

**旧行不再拦（有意取舍，见 §二.3）**：

```
旧行 exp_0efddcf1 symbols=None applicability='CN_A:601398 缩量整理后追高'
  -> is_blocking(CN_A/601398) = False   （symbols 列为 NULL，按设计不拦；且该行是 holdout_only，
                                          本来就被 R1 的硬过滤挡在决策集之外）
```

`apps/fin-worker/tests/test_review_flow.py` 里 `test_memory_hit_halts_the_symbol` /
`test_support_experience_does_not_halt` / `test_memory_of_another_market_does_not_halt`
把这三件事固化成断言。

### 5.9 确定性聚合自证（不引入向量库）

```
按 memory_layer:            [('(NULL)', 3), ('semantic', 3)]
按 polarity:                [('(NULL)', 3), ('refute', 3)]
按 regime_tags（数组展开）:  [('(NULL)', 3), ('unknown', 3)]
按标的（数组展开）:          [('(NULL)', 3), ('CN_A:601398', 1), ('HK:00700', 1), ('US:0700', 1)]
按 strategy_keys（数组展开）:[('(NULL)', 6)]
按 (layer, polarity):       [('-', '-', 3), ('semantic', 'refute', 3)]
```

（`(NULL)` 那 3 条 = 存量行；`semantic/refute` 那 3 条 = 本轮演示写入、跑完即清理。
**样本很少也照贴、不编行数** —— 这是「`03 §1` / `01 §六`：结构化标签 + 确定性
`GROUP BY` 够用、不引入向量库」这个决定的证据。）

### 5.10 唯一入口 grep 守护

```
apps/api:        tests/test_fin_memory_guard.py   -> 3 passed in 10.99s
apps/fin-worker: tests/test_no_ledger_access.py   -> 6 passed in 0.18s
```

守护口径：全仓 `fin_experience*` / `fin_memory_snapshot` 只许命中
`services/fin/memory.py` · `routers/fin_memory.py` · 迁移 · 测试。本轮新增的
`symbols.py` / `regime.py` / `review.py` / `fin-worker` 各文件**一个都没碰这三张表**
（`regime.py` 读的是 `fin_snapshot`，不是经验表）。

### 5.11 pytest 无回归

| 套件 | 改动前（`R4` 末） | 改动后 | 差 |
|---|---|---|---|
| `apps/api`（无 `TEST_DATABASE_URL`） | 616 passed, 115 skipped | **664 passed, 123 skipped** | +48 |
| `apps/api`（`TEST_DATABASE_URL=…/r5_test`） | 727 passed, 4 skipped | **783 passed, 4 skipped** | +56 |
| `apps/fin-worker` | 180 passed | **197 passed** | +17 |

只增不减；新增的真库用例（`test_fin_memory_router.py` 的 8 条 R5 用例）在无库时按设计 skip。

### 5.12 上下文守卫读数与档位

```
开发中：上下文 30.1% · ok · 约 301,480 / 1,000,000
收尾时：上下文 31.8% · ok · 约 318,383 / 1,000,000
阈值 warn/compact/stop   70% / 85% / 92%
档位 0（ok）· 未触发交接
```

（两次读数都在档位 0；`$BASE/handoff-R5.md` 未生成。）

---

## 六、regime 规则版本与阈值表（`R5.md` §五.1 要求）

`regime.py:RULES`（**改任何一条阈值 = 新增一个版本键**，不许原地改）：

| 键 | `regime-v1` | 含义 |
|---|---|---|
| `benchmarks` | `{CN_A: 000300, HK: HSI, US: .INX}` | 各市场基准（取数 / 身份） |
| `ma_window` | `60` | 中期均线周期（交易日） |
| `trend_lookback` | `120` | 趋势回看（交易日，约半年） |
| `bull_band` | `+0.05` | 半年涨幅 ≥ +5% 且站上均线 → `bull` |
| `bear_band` | `-0.05` | 半年涨跌 ≤ -5% 且跌破均线 → `bear` |
| `min_bars` | `121` | `= trend_lookback + 1`；不够 → `unknown` |

**判据**：站上 `ma60` **且** 近 `120` 交易日涨跌超阈值 → `bull` / `bear`；其余 → `range`；
窗口不足 / 数据不合法 / 无市场 → `unknown`。取值理由写在 `regime.py` 的 `RULES` 上方
（太短的均线会把一次回踩判成熊市，太长会因本地日线不够而永远 `unknown`）。

**四条规则怎么落地的**：

1. **阈值随版本固定** —— 规则在 `RULES[rule_version]`，未登记的版本键 **抛
   `ValueError`**（不静默降级成 `unknown`：「版本写错了」与「没行情」是两件事）。
2. **行情缺失 ⇒ `unknown`** —— `_clean_closes` 任一格不合法就整条作废（不悄悄跳格子
   拼一个不是真的窗口）；`classify` 窗口不足即 `unknown`。
3. **`unknown` 是独立取值** —— `LABELS`（明确 regime）与 `ALL_LABELS`（含 `unknown`）
   分开；`UNKNOWN not in LABELS` 有用例盯着。
4. **不可用状态** —— `is_available(result)` 是唯一判据（`label in LABELS`），
   `R6` 在提案入口调它。

---

## 七、测试用例表

| # | 命令 | 结果 |
|---|---|---|
| 1 | `cd apps/api && PYTHONPATH=. pytest tests/test_fin_symbols.py` | ✅ 17 passed（新文件） |
| 2 | `cd apps/api && PYTHONPATH=. pytest tests/test_fin_regime.py` | ✅ 20 passed（新文件） |
| 3 | `cd apps/api && PYTHONPATH=. pytest tests/test_fin_memory.py` | ✅ 34 passed（+9） |
| 4 | `cd apps/api && PYTHONPATH=. pytest tests/test_fin_review.py` | ✅ 38 passed（+4） |
| 5 | `TEST_DATABASE_URL=…/r5_test pytest tests/test_fin_memory_router.py` | ✅ 38 passed（+8） |
| 6 | `cd apps/api && PYTHONPATH=. pytest tests/`（无库） | ✅ **664 passed, 123 skipped** |
| 7 | `TEST_DATABASE_URL=…/r5_test pytest tests/`（真库） | ✅ **783 passed, 4 skipped** |
| 8 | `cd apps/fin-worker && pytest tests/` | ✅ **197 passed** |
| 9 | `pytest tests/test_fin_memory_guard.py`（唯一入口守护） | ✅ 3 passed |
| 10 | `pytest tests/test_no_ledger_access.py`（fin-worker 不碰账本/经验表） | ✅ 6 passed |
| 11 | `python -m app.migrate`（空库 `r5_test`，两遍） | ✅ 43 / 0 |
| 12 | `python -m app.migrate`（开发库 `hunter`） | ✅ 1（`0042`） |

---

## 八、遗留问题与下一阶段（`R6`）交接

1. **⚠️ `unknown` 的聚合纪律（`R5.md` §一.2 规则 3，明确交给 `R6`）**：
   **`regime_tags` 含 `unknown` 的样本，不得与明确 regime 的样本聚合。**
   本轮只把它做成「判定器的产出约定」（`unknown` 是独立取值、`is_available` 是唯一判据），
   聚合侧的 SQL / 分组必须自己按这条办。`R6` 的提案算法在 `GROUP BY regime_tags` 时
   要么把 `unknown` 单独成组、要么排除，**绝不能并进 `bull`/`bear`/`range` 任意一组**。
2. **⚠️ regime 判定是提案的前置条件（规则 4）**：`is_available(out) == False` 时
   **继续复盘、停止策略提案**。`R6` 的提案入口要调 `regime.is_available`，
   不要另写 `label != 'unknown'`。
3. **⚠️ `importance` 不参与统计加权**：`R6` 聚合不许按 `importance` 加权
   （`03 §4-A`）。它只影响展示排序。
4. **`fin_experience.evidence_kind` 需要 `'experience'` / `'evolution'` 时另起迁移
   （`0043` 或更后）** —— 那是 `M4 周度巩固` / `E5 回滚经验` 的事，本轮没做（§二.2）。
5. **`observe()` 仍是「本部署没有基准行情源」的形态。** 要接基准行情时**只动
   `regime.observe()` 一处**（判定规则与四元组输出都不用动）；接之前先想清楚
   限速 / 超时 / WAF（`CLAUDE.md` 腾讯 WAF 前科）。真接了以后，`unknown` 会变少，
   `R6` 的提案才有意义。
6. **`polarity='refute'` 的「排序置顶」没做**（`01 §8.3` 规则 11 后半句）。
   本轮的 `memory.query` 顺序仍是 `as_of DESC, id ASC`（`R3` 的 `memory_gate`
   依赖这个顺序做「第一条」的确定性）。置顶属于展示 / 聚合层的排序，留给 `R9` / `R6`。
7. **fixture 的 `polarity` 为 `NULL` 的旧经验按负向拦（fail-closed）** ——
   这是 `memory_gate` 的有意选择（拿不准就按负向），写进了模块文档与用例。
   若 `R6` 之后决定「`NULL` 不拦」，要同时改模块文档与 `test_memory_gate.py`。
8. **本轮的界面（growth 页展示新列）不做** —— `R9`。
9. **演示 / 回归用的临时经验**已按 `applicability='R5 演示'` 从开发库清理；
   临时库 `r5_test` 保留（真库用例要用），**未动开发库 `hunter` 的历史行**。

---

## 九、收尾四件套

1. 本文档 `docs/开发文档/R5-经验层收口与regime判定.md`；
2. 合并 `main`、push（提交信息末尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`）；
3. `notify-qq "[智能炒股·记忆系统] R5 完成 · 记忆层四列落库、regime 判定器上线、标的不再撞车 · <上海时间>"`；
4. `progress.log` 追加 `[上海时间] R5 · 完成`。
