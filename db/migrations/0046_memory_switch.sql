-- ════════════════════════════════════════════════════════════════════════
-- 0046_memory_switch.sql · 智能炒股 · 经验库的「按项目开关」（第四段 R20/R21）
--   1. `fin_memory_switch`     —— 现值：**这个项目**要不要攒经验、学习强度是多少；
--   2. `fin_memory_switch_log` —— 流水账：谁、什么时候、把哪一项从什么改成了什么、为什么。
--
-- 为什么要有它（用户原话）：
--   「让在前端界面可以设置当前项目是否可以把经验库打开和关闭，而不需要重新部署和启动系统；
--     增加按项目动态设置，让用户可以参与决策和设置。」
--
--   在此之前，经验库总闸只有**环境变量** `FIN_MEMORY_ENABLED` 一条路：改 `.env` 还得
--   `docker compose up -d api`（注意不是 `restart` —— 那个不重读环境变量），
--   改一次要动部署、而且四个开关一个都不留痕。
--
-- 与 `fin_param` / `fin_param_change_log` 的关系（**同一套造型，不同的东西，不许混**）：
--   · `fin_param` 是**项目参数**（多少钱、什么策略、风险档位），唯一写入口是
--     `services/fin/control.py`（红线 7）；
--   · 本表是**这台机器允不允许这个项目学习**的开关，唯一写入口是
--     `services/fin/switches.py` 的 `set_project_switch()`。
--   两者唯一的共同点就是「现值表 + 只追加的流水账表」这个造型 —— 照抄它，不发明新的。
--
-- ⚠️ **天花板规矩**：环境变量仍是**天花板**（部署者的意志），本表是**天窗**
--   （使用者的日常选择）——**最终生效值 = 两者的交集，取更保守的一个**。
--   环境变量把 `FIN_MEMORY_ENABLED` 设成 `0` 时，界面上的开关是灰的、接口直接 400：
--   **界面永远开不出部署者不允许的东西**。
--
-- ⚠️ 两个**硬开关**（`FIN_AUTO_APPLY` / `FIN_LIVE_ORDER_ENABLED`）**不进这张表** ——
--   它们是「本方案规定不许开」，不是「还没配」，所以连界面入口都没有（服务端 400）。
--
-- ⚠️ 纪律（`记忆系统追加规则.md` §四）：**只做加法**；**不带 `BEGIN;` / `COMMIT;`**
--   —— `app/migrate.py:apply_one` 已把每个文件包在独立事务里；可重复执行。
--
-- ⚠️ **没有 DELETE 授权**（沿用经验三表的纪律）：清掉某一项 = 把那一列写回 `NULL`
--   （= 跟随部署侧的默认），**不删行**。流水账只增不改，是「谁把刹车松了/紧了」的唯一权威。
-- ════════════════════════════════════════════════════════════════════════

-- ── 1 · 现值：一个项目一行 ───────────────────────────────────────────────
--   两列都**可空**：`NULL` = 这个项目还没单独设过，跟随部署侧的默认值。
--   这和 `memory.py` 那句「旧行一律保持 NULL，不凭文本猜测」是同一个口径 ——
--   「没设过」和「显式设成关」是两件事，界面要分开显示。
CREATE TABLE IF NOT EXISTS fin_memory_switch (
  project_id      TEXT PRIMARY KEY REFERENCES fin_project(project_id),
  memory_enabled  BOOLEAN,                          -- NULL = 未设置（跟随环境变量）
  evolution_mode  TEXT,                             -- NULL = 未设置；否则 off/observe/paper
  updated_by      TEXT,                             -- 谁改的（users.id / 'system'）
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_memory_switch_mode_chk
    CHECK (evolution_mode IS NULL OR evolution_mode IN ('off','observe','paper'))
);

COMMENT ON TABLE fin_memory_switch IS
  '经验库的按项目开关（R21）。memory_enabled/evolution_mode 为 NULL = 未设置，跟随环境变量。'
  '环境变量是天花板、本表是天窗：最终生效值取两者中更保守的一个（switches.py 唯一读点）。'
  '硬开关（FIN_AUTO_APPLY / FIN_LIVE_ORDER_ENABLED）不进本表。';

-- ── 2 · 流水账：只追加，不给改 ───────────────────────────────────────────
--   `project_id` **不建外键**：流水账要活过项目本身（审计口径同 `fin_evolution_event`）。
--   `old_value` / `new_value` 用 JSONB 存 —— 布尔与字符串两种值共用一张表，不必分列。
CREATE TABLE IF NOT EXISTS fin_memory_switch_log (
  log_id      TEXT PRIMARY KEY,                     -- fmsl_<24hex>
  project_id  TEXT NOT NULL,
  switch_key  TEXT NOT NULL,                        -- memory_enabled | evolution_mode
  old_value   JSONB,                                -- NULL = 之前未设置
  new_value   JSONB,
  actor       TEXT,                                 -- 谁改的（users.id）
  reason      TEXT NOT NULL,                        -- 为什么改（必填，服务端强制）
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fin_memory_switch_log_key_chk
    CHECK (switch_key IN ('memory_enabled','evolution_mode'))
);

CREATE INDEX IF NOT EXISTS fin_memory_switch_log_by_project
  ON fin_memory_switch_log (project_id, created_at DESC);

COMMENT ON TABLE fin_memory_switch_log IS
  '经验库按项目开关的流水账（R21）· 纯追加：无 UPDATE、无 DELETE。'
  '与 fin_param_change_log 同一造型；reason 必填。';
