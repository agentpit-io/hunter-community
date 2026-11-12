-- 智能炒股 · 自动交易页的三个控制所需的列（一期 M6）
--
-- ⚠️ **已有部署不会执行这个文件**（仓内铁律：`db/migrations/*.sql` 只在数据卷
--    第一次初始化时被 postgres 的 docker-entrypoint-initdb.d 跑）。真正生效的 DDL
--    随代码走，写在 `apps/api/app/services/fin/control.py` 的 `_DDL` 里（幂等，
--    首次使用时补列）。本文件的作用只有一个：**给全新安装一份完整的 DDL 留档**，
--    两处必须保持一致 —— 改一处要连另一处一起改。
--
-- 两列都挂在 `fin_param`（开户时写下的那套参数）上：
--   · auto_enabled —— 自动交易总开关。false 时 fin-worker 的 decide 时点不再产生
--     新委托。默认 true：老项目的行为一字不变。
--   · risk_tier    —— 风险档位（conservative / steady / aggressive）。为空表示
--     「还没有单独设过档位，按开户档位模板跑」——**不编一个默认档位名**。

ALTER TABLE fin_param ADD COLUMN IF NOT EXISTS auto_enabled BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE fin_param ADD COLUMN IF NOT EXISTS risk_tier TEXT;
