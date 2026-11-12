-- ════════════════════════════════════════════════════════════════════════
-- 0051_exec_allowance.sql · 智能炒股 · 五期 L06 · 执行允许名单（默认拒绝）
--
-- 依据：`plan/00五期方案/…五期(闭环补齐)…md` §二 第 7 条 / §四 `L06`；
--       `plan/00五期方案/AI_炒股智能体系统_开源组件整合与技术实施方案.md` §7.4
--       （「模拟执行接口采用服务端鉴权、**最小权限与允许列表**」）；`plan/L06.md` §一。
--
-- 现状（方案 §二 第 7 条实测）：放行侧只有「口令对不对」这一条 —— 实盘字段靠
--   `apps/paper/app/config.py` 的 `LIVE_FIELD_DENYLIST`（**拒绝名单**）拦，
--   全仓**没有**「哪些项目 / 哪些标的 / 哪些工具被允许执行」的**允许名单**。
--
-- 本迁移建**允许名单表**。口径（`plan/L06.md` §1.2）：
--   · **默认空 = 谁都不许** —— 要**显式登记**才放行（这是加固，不是放松）；
--   · **只追加**（触发器挡 UPDATE / DELETE）—— 撤销 = 再追加一条 `status='revoked'`，
--     某一 (scope, subject) 的**现行状态** = `allowance_id` 最大的那一行；
--   · 三个维度 `scope`：`project`（项目）/ `instrument`（标的）/ `tool`（执行工具）；
--     项目维度**必须显式登记**（基础闸门），标的 / 工具维度**有人登记过才生效**
--     （附加约束）；subject = `'*'` 是**显式登记的通配**（不是默认放行）。
--   · 判据的**唯一实现**在 `apps/paper/app/allowlist.py`，本表只存事实。
--
-- `fin_paper_rw`（账本运行期角色）是**唯一写入口**（登记 = 追加一行）→ 授
--   SELECT / INSERT，**不授 UPDATE / DELETE**（`09 §一`：GRANT 就是边界）。
--
-- 类型口径沿用 `0023`：时刻 TIMESTAMPTZ；文本 TEXT。全表**不出现 float / double / REAL**。
-- 本迁移**只做加法**（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）；
--   **不带 `BEGIN;` / `COMMIT;`**（`app/migrate.py` 已把每个文件包在独立事务里）；可重复执行。
-- ════════════════════════════════════════════════════════════════════════


-- ── 执行允许名单（**只追加**）─────────────────────────────────────────────
--   一行 = 一次登记动作（放行 / 撤销），不删不改。现行状态按 (scope, subject)
--   取 `allowance_id` 最大的那行。空表 = 任何执行都被拒（默认拒绝）。
CREATE TABLE IF NOT EXISTS fin_exec_allowance (
  allowance_id BIGSERIAL PRIMARY KEY,
  scope        TEXT NOT NULL CHECK (scope IN ('project', 'instrument', 'tool')),
  subject      TEXT NOT NULL,                    -- project_id / 标的代码 / 工具名；'*' = 显式通配
  status       TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  granted_by   TEXT NOT NULL,                    -- 谁登记的（留痕）
  granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  note         TEXT
);

-- 取现状用 (scope, subject) 分组取最大 allowance_id —— 这个索引正好覆盖。
CREATE INDEX IF NOT EXISTS fin_exec_allowance_scope_subject
  ON fin_exec_allowance (scope, subject, allowance_id DESC);

COMMENT ON TABLE fin_exec_allowance IS
  '执行允许名单（L06 · 只追加）。一行 = 一次放行/撤销登记；现行状态 = (scope,subject) '
  '里 allowance_id 最大的那行。**空表 = 默认拒绝**（谁都不许下单）。判据唯一实现在 '
  'apps/paper/app/allowlist.py；denylist（实盘字段）是第二道防线，两者都在。';

COMMENT ON COLUMN fin_exec_allowance.scope IS
  'project（项目，必须显式登记）/ instrument（标的）/ tool（执行工具，如 place_order）。';
COMMENT ON COLUMN fin_exec_allowance.subject IS
  'scope=project → project_id；scope=instrument → 标的代码；scope=tool → 工具名。'
  '''*'' = 显式登记的通配（不是默认放行）。';

-- 只追加：改 / 删一律抛异常（照 `0043` / `0049` / `0050` 的同款触发器）。
CREATE OR REPLACE FUNCTION fin_exec_allowance_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fin_exec_allowance 只追加：% 被拒绝（允许名单不可改、不可删；撤销=再追加一条 status=revoked）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_exec_allowance_immutable ON fin_exec_allowance;
CREATE TRIGGER fin_exec_allowance_immutable
  BEFORE UPDATE OR DELETE ON fin_exec_allowance
  FOR EACH ROW EXECUTE FUNCTION fin_exec_allowance_immutable();


-- ── 权限（照 `0023` §一：新表必须显式授权，不给默认权限）─────────────────
--   追加式表 → 只 SELECT / INSERT；序列要 USAGE（BIGSERIAL 第一次 INSERT 才报缺权限）。
GRANT SELECT, INSERT ON fin_exec_allowance TO fin_paper_rw;
GRANT USAGE ON SEQUENCE fin_exec_allowance_allowance_id_seq TO fin_paper_rw;
