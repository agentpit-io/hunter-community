-- ════════════════════════════════════════════════════════════════════════
-- 0036_project_markets.sql · 智能炒股 · 用户选市场（P1：数据模型 + API）
--
-- 依据：`plan/需求与实现方案.md` §2（数据模型改动）
--       + `plan/迭代完善追加规则.md` §四（数据模型口径）
--
-- ⚠️ **已有部署不会执行这个文件**（`docker-entrypoint-initdb.d` 只在数据卷第一次
--    初始化时跑）。真正生效的 DDL 随代码走 —— api 启动时 `app.migrate` 按
--    `schema_migrations` 增量执行 `db/migrations/*.sql`（`0031` 起就是这么落的）；
--    本文件是**给全新安装用 + 留档**，两处保持一致。
--
-- 改了什么（**只做加法 / 放松约束，可重复执行，不改历史行**）：
--   ① 建表 `fin_project_market` —— 一个项目选了哪几个市场的**唯一真值**。
--      用表而不是数组列，是因为它与账本侧的子账户 `(project_id, market)`
--      同构（`apps/paper/app/valuation.py`），加市场 = 加一行。
--   ② `fin_project.currency` 放开为可空 —— ≥2 个市场时没有单一币种，
--      真值在 `fin_project_market.currency`；**只放松，不动其它列，
--      也不动 `market_scope` 的 CHECK**（单市场时它还是那个单值，读法逐字不变）。
--   ③ 事实回填：现有项目的 `market_scope='CN_A'` 是 0029 已核实的现状
--      （「现有 424 行全部 CN_A」），故按 `(project_id, 'CN_A', initial_capital,
--      'CNY', opened_at)` 回填 —— 这是**事实陈述，不是猜**（红线 1）。
--
-- ⚠️ `market_scope='MULTI'` 的历史行：方案称「实际不存在」。**若部署前实测发现有**，
--    按已有 `fin_project_market` 行回填；仍无行则**停下来报阻塞，不许编**
--    （`迭代完善追加规则.md` §四）。本文件末尾有一句 `RAISE NOTICE` 把这种项目
--    点名报出来（不报错、不阻断迁移，也不替它编市场）。
-- ════════════════════════════════════════════════════════════════════════


-- ── ① 市场集合的唯一真值表 ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fin_project_market (
  project_id      TEXT        NOT NULL REFERENCES fin_project(project_id),
  market          TEXT        NOT NULL CHECK (market IN ('CN_A','HK','US')),
  initial_capital NUMERIC     NOT NULL,       -- = 档位金额（每市场各一份，数字同档位）
  currency        TEXT        NOT NULL,       -- = 该市场本币（CNY/HKD/USD）
  opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, market)
);


-- ── ② 币种放开为可空（≥2 市场时无单一币种）────────────────────────────────
ALTER TABLE fin_project ALTER COLUMN currency DROP NOT NULL;


-- ── ③ 事实回填：现有 CN_A 项目 ─────────────────────────────────────────
INSERT INTO fin_project_market (project_id, market, initial_capital, currency, opened_at)
SELECT project_id, 'CN_A', initial_capital, 'CNY', opened_at
  FROM fin_project
 WHERE market_scope = 'CN_A'
ON CONFLICT (project_id, market) DO NOTHING;


-- ── ④ 权限：新表必须显式授权（09 §一；不给默认权限）─────────────────────
-- `fin_project_market` 是**只增不改**的范围声明（行一旦写下，`opened_at` 不动）
-- → 与 `fin_trade` 等追加表同口径给 SELECT / INSERT。给 paper 侧（P2 起读市场集合）
-- 用；写入者仍是 api（表 owner）。任何角色都不给 DELETE。
GRANT SELECT, INSERT ON fin_project_market TO fin_paper_rw;


-- ── ⑤ MULTI 项目的阻塞报告（不编、不阻断）───────────────────────────────
-- 迁移本身不替 `MULTI` 项目猜市场；这里只把「有 MULTI 行、但一行 fin_project_market
-- 都没有」的项目点名报出来，交给人工核实后补（`迭代完善追加规则.md` §四）。
DO $$
DECLARE
  n_multi       BIGINT;
  n_unfilled    BIGINT;
BEGIN
  SELECT count(*) INTO n_multi FROM fin_project WHERE market_scope = 'MULTI';
  SELECT count(*) INTO n_unfilled
    FROM fin_project p
   WHERE p.market_scope = 'MULTI'
     AND NOT EXISTS (SELECT 1 FROM fin_project_market m WHERE m.project_id = p.project_id);
  IF n_multi > 0 THEN
    RAISE NOTICE '0036: fin_project 里有 % 个 market_scope=''MULTI'' 的项目',
                 n_multi;
  END IF;
  IF n_unfilled > 0 THEN
    RAISE NOTICE '0036: 其中 % 个没有任何 fin_project_market 行 —— 不许编，人工核实后再补（追加规则 §四）', n_unfilled;
  END IF;
END $$;
