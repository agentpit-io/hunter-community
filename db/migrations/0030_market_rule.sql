-- ════════════════════════════════════════════════════════════════════════
-- 0030_market_rule.sql · 智能炒股二期 · 市场规则表（本期核心）
--
-- 依据：`plan/ref/11-美股港股模拟交易需求分析与技术实施方案.md` §4.1 / §3.3
--       （把「风控六条」从写死 A 股改成按市场取参数 —— 这张表就是那套参数）
--       + `plan/拍板-2026-10-03-港美股必须可交易.md` §四（三市场成交口径）
--
-- 口径纪律（红线 §六.1「不许编数字」）：
--   · 每个市场的每一个参数，来源写进 `source`；**拿不到的留空并写 `unavailable`**，
--     绝不填一个「看起来合理」的数；
--   · 本文件里不出现「大概」「约」这类词；
--   · `CN_A` 一行与现状**逐项等价**（`sellable_rule='t_plus_n'` + `sellable_days=1`、
--     `lot_rule='fixed'` + `lot_fixed=100`、`price_limit_mode='pct'`）——
--     这是「A 股一行都不能坏」在本迁移里的落点。
--   · 港美股「费率模型」这一项 **unavailable**（本期不落数）—— 费率随年份变，
--     必须按 `effective_from` 分年取自公布口径（`fin_fee_model` 加 market 列见 0029）。
--
-- 幂等：`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` /
--       `ADD CONSTRAINT` + `INSERT ... ON CONFLICT (market) DO NOTHING`。
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS fin_market_rule (
  market            TEXT PRIMARY KEY CHECK (market IN ('CN_A','HK','US')),
  currency          TEXT NOT NULL CHECK (currency IN ('CNY','HKD','USD')),
  timezone          TEXT NOT NULL,          -- IANA 时区名（夏令时靠时区名自动跟随，不写死偏移）
  sessions          JSONB NOT NULL,         -- 本地时刻的时段数组 [{"open","close"}, ...]
  points            JSONB NOT NULL,         -- 该市场的调度时点列表（本地时刻 "HH:MM"）
  sellable_rule     TEXT NOT NULL CHECK (sellable_rule IN ('t_plus_n','same_day')),
  sellable_days     INTEGER,                -- n（same_day 时为 0）
  lot_rule          TEXT NOT NULL CHECK (lot_rule IN ('fixed','per_instrument','one')),
  lot_fixed         INTEGER,                -- lot_rule='fixed' 时的手数；per_instrument 时为 NULL
  price_limit_mode  TEXT NOT NULL CHECK (price_limit_mode IN ('pct','none','band')),
  fee_model_version TEXT,                   -- 指向 fin_fee_model.version；拿不到费率时为 NULL
  source            TEXT NOT NULL,          -- 每个参数的来源（交易所 / 监管公布口径）
  effective_from    DATE NOT NULL,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE fin_market_rule IS
  '每个市场一行：时区 / 时段 / 调度时点 / 可卖规则 / 手数 / 价格限制模式 / 费用模型版本。'
  '风控六条从写死 A 股改为按本表取参（N2）。数值全部标 source，拿不到的留空。';

-- 允许约束重复执行（先建表再补 CHECK，便于将来在旧库上补约束）
ALTER TABLE fin_market_rule DROP CONSTRAINT IF EXISTS fin_market_rule_market_check;
ALTER TABLE fin_market_rule ADD  CONSTRAINT fin_market_rule_market_check
  CHECK (market IN ('CN_A','HK','US'));


-- ── 三行种子 ─────────────────────────────────────────────────────────────
-- ON CONFLICT DO NOTHING：重复执行不报错、不改已落地的行（迁移文件一经发布不可变）。

INSERT INTO fin_market_rule
  (market, currency, timezone, sessions, points,
   sellable_rule, sellable_days, lot_rule, lot_fixed,
   price_limit_mode, fee_model_version, source, effective_from)
VALUES
-- ── CN_A · 与一期现状逐项等价（不得回归）────────────────────────────────
(
  'CN_A', 'CNY', 'Asia/Shanghai',
  '[{"open":"09:30","close":"11:30"},{"open":"13:00","close":"15:00"}]'::jsonb,
  '["09:15","09:30","11:30","13:00","14:55","15:30"]'::jsonb,
  't_plus_n', 1, 'fixed', 100,
  'pct', 'fee-cn-a-v1',
  'CN_A 各项来源：'
  '①交易时段=上交所/深交所公布的连续竞价时间 09:30-11:30 / 13:00-15:00（上交所 www.sse.com.cn「交易时间」、深交所 www.szse.cn），'
  '与 fin_market_calendar.sessions 现状、paper/risk/session.py 口径逐条一致；'
  '②调度时点=本项目自定（fin-worker/app/points.py 六时点 09:15/09:30/11:30/13:00/14:55/15:30），非交易所公布；'
  '③可卖规则=沪深交易所交易规则「当日买入的证券，当日不得卖出」→ t_plus_n / 1 天；'
  '④每手=沪深交易所交易规则「买入数量应当为 100 股或其整数倍」→ fixed / 100；'
  '⑤价格限制=按板块百分比（主板 10%、创业板/科创板 20%，幅度取 fin_instrument.limit_up_pct）→ price_limit_mode=pct；'
  '⑥费用模型=fee-cn-a-v1（fin_fee_model，一期落表，见 0023/0025）。'
  'effective_from=一期基线日（0023_fin_core 落库日 2026-10-02），表示该行自基线起与现状等价。',
  DATE '2026-10-02'
),
-- ── HK · 港股 ────────────────────────────────────────────────────────────
(
  'HK', 'HKD', 'Asia/Hong_Kong',
  '[{"open":"09:30","close":"12:00"},{"open":"13:00","close":"16:00"}]'::jsonb,
  '["09:15","09:30","12:00","13:00","15:55","16:15"]'::jsonb,
  'same_day', 0, 'per_instrument', NULL,
  'none', NULL,
  'HK 各项来源：'
  '①交易时段=HKEX 官网交易时间 09:30-12:00 / 13:00-16:00（www.hkex.com.hk「Trading Hours」）；'
  '②调度时点=本项目自定（结构对齐 A 股六时点，见 points.py 口径），非交易所公布；'
  '③可卖规则=港股实行 T+2 交收但可当日买入当日卖出（T+0 交易，HKEX）→ same_day / 0；'
  '④每手=按标的，各不相同（lot_rule=per_instrument，数量取港交所官方 ListOfSecurities 导出 data/hk_master.csv，见 N0 报告 S7）；'
  '⑤价格限制=港股无每日涨跌幅限制（HKEX，另设市调机制 VCM）→ price_limit_mode=none，本期不做价格带校验，回执与报告须留痕「未做」；'
  '⑥费用模型=unavailable（本期未落 fin_fee_model 行；港股印花税双边 + 交易费/交易征费/结算费等须按公布口径落表并标 effective_from）。'
  'effective_from=本批市场规则落表日 2026-10-03。',
  DATE '2026-10-03'
),
-- ── US · 美股 ────────────────────────────────────────────────────────────
(
  'US', 'USD', 'America/New_York',
  '[{"open":"09:30","close":"16:00"}]'::jsonb,
  '["09:15","09:30","12:00","14:55","15:55","16:15"]'::jsonb,
  'same_day', 0, 'one', 1,
  'none', NULL,
  'US 各项来源：'
  '①交易时段=NYSE 交易时间 09:30-16:00 ET（www.nyse.com/markets/hours-calendars）；'
  '②时区=America/New_York（IANA 时区名，夏令时由时区名自动跟随，禁止写死 ±N 偏移）；'
  '③调度时点=本项目自定（结构对齐 A 股六时点，美股无午休，见 points.py 口径），非交易所公布；'
  '④可卖规则=美股可当日买入当日卖出（NYSE/NASDAQ）→ same_day / 0；'
  '⑤每手=1 股（美股以 1 股为最小交易单位，事实；lot_rule=one）；'
  '⑥价格限制=美股无每日涨跌幅限制（NYSE，另设 LULD 熔断）→ price_limit_mode=none，本期不做价格带校验，回执与报告须留痕「未做」；'
  '⑦费用模型=unavailable（本期未落 fin_fee_model 行；SEC 规费与交易活动费按 effective_from 分年落表）。'
  'effective_from=本批市场规则落表日 2026-10-03。',
  DATE '2026-10-03'
)
ON CONFLICT (market) DO NOTHING;


-- ── 权限（09 §七：新表必须显式授权，不给默认权限）────────────────────────
-- 与 fin_instrument / fin_market_calendar / fin_fee_model 同口径：参考数据可被更正。
GRANT SELECT, INSERT, UPDATE ON fin_market_rule TO fin_paper_rw;
