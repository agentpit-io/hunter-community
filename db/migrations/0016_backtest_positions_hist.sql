-- backtest_result 存逐期持仓 · 2026-09-01
--
-- 「持仓变化」面板需要逐期持仓才能算换入换出。引擎会返回 positions_hist,
-- 但表里只存了 metrics / nav_series / positions(最后一期)。
-- 结果:新跑的回测有调仓记录,而**缓存命中的那次没有** ——
-- 同一个策略,点第二次反而显示"还没有调仓记录",用户完全无法理解。
--
-- 加一列存下来。纯 ADD COLUMN IF NOT EXISTS,不动既有数据;
-- 老结果这一列是 NULL,前端按"没有记录"处理(它们确实没存过)。
ALTER TABLE backtest_result
  ADD COLUMN IF NOT EXISTS positions_hist JSONB;

COMMENT ON COLUMN backtest_result.positions_hist IS
  '逐期持仓 [{date, codes[]}] · 供「持仓变化」面板算换入换出';
