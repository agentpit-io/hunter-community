-- 0038 · 补上 A 股的费率行 `fee-cn-a-v1`（幂等）
--
-- ⚠️ **已有部署不会自动执行这个文件** —— `db/migrations` 只在数据卷第一次初始化时跑。
--    本仓的规矩是「真正生效的 DDL/数据随代码走」；这一条是**数据**种子，历史部署
--    （含演示站）请用下面同一段 SQL 补一次，或由 `views._fee_model` 的降级路径兜住
--    （缺行时**不显示费率数字**，不拿别的市场顶）。
--
-- 为什么需要它（2026-10-03 演示站实测）：
--   `0029_market_scope.sql` 假定「现有唯一一行 'fee-cn-a-v1'」已由一期落表，`0034`
--   只补了 `fee-hk-v1` / `fee-us-v1` —— **全仓没有任何迁移幂等地写入 A 股费率行**。
--   于是从迁移建起来的库（演示站就是）只有 HK / US 两行，`views._fee_model` 的
--   「回落最新一行」把 `fee-us-v1`（字典序最大）当成 A 股费率渲染了出来
--   （佣金万1 / 印花税 — / 过户费万0.2，全是错的）。
--   修了两处：`views._fee_model` 不再跨市场回落；这里把 A 股费率行幂等补上。
--
-- 值取一期的 `fee-cn-a-v1`（0023 文件头与 `035`/`036` 无关，纯事实回填，不做任何近似）：
--   佣金 万2.5（最低 5 元）· 印花税 千0.5（仅卖出）· 过户费 万0.1（双边）

INSERT INTO fin_fee_model
  (version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct,
   market, currency, stamp_side, transfer_fee_side, effective_from, source, note)
VALUES
(
  'fee-cn-a-v1', 0.000250, 5.0000, 0.000500, 0.000010,
  'CN_A', 'CNY', 'sell', 'both', TIMESTAMPTZ '2026-01-01T00:00:00Z',
  '一期 A 股费率口径 fee-cn-a-v1（0023/0025 落表）：佣金万2.5+最低5元 · 印花税千1(卖出) · 过户费万0.1',
  '本行是**事实回填**：值抄自一期落表的同一行，不做近似。'
)
ON CONFLICT (version) DO NOTHING;
