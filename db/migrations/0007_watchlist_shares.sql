-- 自选股持仓手数 · 供「交易成本」小框计算真实成本
-- 2026-08-30 · 见 doc/开源hunter-community/04开源比赛/
--                2026-08-30_导航重构方案-对话与自选股双栏.md §5.2
--
-- 为什么放在 stocks 而不是复用 /portfolio:
--   portfolio 是另一套模型(建仓价 / 成本 / 盈亏 / 调仓建议),
--   而这里只需要一个数量 —— 用来把「A股单向 10.6bps」这种抽象费率
--   换算成「你这 2 手真要花 27.5 元」。
--   为一个整数去耦合一整套持仓模型,不划算。
--
-- 幂等 · 只加列 · 不动任何已有数据(符合生产库规则)

ALTER TABLE stocks
  ADD COLUMN IF NOT EXISTS shares INT NOT NULL DEFAULT 0;

COMMENT ON COLUMN stocks.shares IS '持仓手数 · 0 表示只关注未持仓 · 供交易成本测算';
