-- ════════════════════════════════════════════════════════════════════════
-- 0055_auto_apply_switch.sql · 智能炒股 · 「自动生效」上按项目开关（L12 · 自学习段）
--   1. 给现值表 `fin_memory_switch` 加一列 `auto_apply`（可空，NULL = 未设置）；
--   2. 放宽流水账 `fin_memory_switch_log.switch_key` 的 CHECK，把 `auto_apply` 加进允许取值。
--
-- 为什么要有它（用户口径）：
--   产品定位是「AI 自己学、自己改、自己跑，人工只在想干预时介入」。
--   在此之前 `FIN_AUTO_APPLY` 被做成**硬锁死**（只允许 0，填非 0 服务端直接拒绝）——
--   界面上只能看到一行「恒为关闭」的死文字，机器级也无法按项目关。
--   本迁移把它接进 R21 那套「按项目开关」的覆盖层：**环境变量是天花板、本表是天窗**，
--   最终生效值取两者中更保守的一个（`switches.auto_apply_effective()`）。
--
-- ⚠️ **本迁移只做开关层，不改生效链路**：`apply_proposal()` 仍然要求调用方显式
--   `confirm=true`。也就是说本迁移落地后，系统仍然不会自己生效任何东西；
--   「自动」那一步由下一段（L13）接。这里只是把开关做出来。
--
-- ⚠️ **表名 `fin_memory_switch` 已经过时，但刻意不改名**：它现在装的不止「记忆」开关
--   （`memory_enabled` / `evolution_mode` / `auto_apply`）。改名不是「加法」，会牵动
--   `switches.py` 的读写点、缓存键与既有数据（有风险却零收益）——**如实记在这里，不重命名**。
--
-- ⚠️ **本表里仍然只有「可改的真开关」**：唯一的硬开关 `FIN_LIVE_ORDER_ENABLED` 照旧
--   不进本表（连界面入口都没有，服务端 400）。加进 `auto_apply` 是因为它**已经不是**
--   硬开关了（`switches.HARD_SWITCH_KEYS` 只剩实盘那一条）。
--
-- ⚠️ 纪律（`记忆系统追加规则.md` §四）：**只做加法**；**不带 `BEGIN;` / `COMMIT;`**
--   —— `app/migrate.py:apply_one` 已把每个文件包在独立事务里；可重复执行（幂等）。
-- ════════════════════════════════════════════════════════════════════════

-- ── 1 · 现值表加一列（NULL = 这个项目还没单独设过，跟随环境变量）─────────────
--   与 `memory_enabled` 同一个口径：「没设过」和「显式设成关」是两件事，
--   界面要分开显示（`selected` 里 `NULL` vs `false`）。
ALTER TABLE fin_memory_switch
  ADD COLUMN IF NOT EXISTS auto_apply BOOLEAN;

COMMENT ON COLUMN fin_memory_switch.auto_apply IS
  '自动生效的按项目选择（L12）。NULL = 未设置，跟随环境变量 FIN_AUTO_APPLY；'
  'true/false = 这个项目显式选的开/关。生效值 = 天花板 ∩ 本列（取更保守，switches.py 唯一读点）。';

-- ── 2 · 流水账的 CHECK 放行 `auto_apply` ─────────────────────────────────
--   0046 建表时 CHECK 只允许 memory_enabled / evolution_mode。这里**先 DROP IF EXISTS
--   再 ADD**，两遍连跑都成功（幂等）。已有行全是那两个旧取值，是新集合的子集，校验必过。
ALTER TABLE fin_memory_switch_log
  DROP CONSTRAINT IF EXISTS fin_memory_switch_log_key_chk;

ALTER TABLE fin_memory_switch_log
  ADD CONSTRAINT fin_memory_switch_log_key_chk
  CHECK (switch_key IN ('memory_enabled', 'evolution_mode', 'auto_apply'));

-- ── 3 · 表注释跟着更新（原文只提了两个开关 + 「硬开关不进本表」）──────────────
COMMENT ON TABLE fin_memory_switch IS
  '经验库 / 学习强度的按项目开关（R21；L12 起含 auto_apply）。三列 NULL = 未设置，跟随环境变量。'
  '环境变量是天花板、本表是天窗：最终生效值取两者中更保守的一个（switches.py 唯一读点）。'
  '唯一的硬开关 FIN_LIVE_ORDER_ENABLED 不进本表（无界面入口）。表名 fin_memory_switch 已过时但刻意不改名。';
