-- 数据中心 · 5 表 migration
-- (2026-08-24 · 见 doc/开源hunter-community/01详细工作目录/11量化策略/
--                 22_20260822_数据中心_技术方案.md §2 §10)
--
-- 背景:原来量化数据是开机自动跑、范围写死沪深300 ∪ 中证500。
-- 老板意见:「刚下载启动容器自动跑不太好,用户都不知道你就占用他的资源」。
-- 改成用户在「数据」页自己选范围和时长,后台跑、实时进度、增量更新。
--
-- 全部只 ADD,不 DROP / RENAME / ALTER TYPE。

-- ═══════════════════════════════════════════════════════════════
-- 表 1 · data_coverage · 每只股票每类数据下到哪儿了
-- ═══════════════════════════════════════════════════════════════
-- 增量更新的唯一依据。**只存一个连续区间,不存区间列表** ——
-- 中间有洞说明上次下到一半失败了,那种情况重下整段比拼接更可靠。
-- 拼接逻辑写错的表现是"数据看起来是连续的,中间少了三个月",
-- 而这种错在回测结果里看不出来。
CREATE TABLE IF NOT EXISTS data_coverage (
  code         VARCHAR(10)  NOT NULL,
  data_type    VARCHAR(16)  NOT NULL,          -- kline | financial
  covered_from DATE         NOT NULL,
  covered_to   DATE         NOT NULL,
  updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (code, data_type)
);
CREATE INDEX IF NOT EXISTS idx_dc_type ON data_coverage (data_type, covered_to DESC);


-- ═══════════════════════════════════════════════════════════════
-- 表 2 · data_job · 下载任务
-- ═══════════════════════════════════════════════════════════════
-- **状态必须落库,不能只放内存。** 现在的初始化进度是模块级 dict,
-- 后果实测过两次:一是 docker exec 另起进程跑,状态读不到;
-- 二是容器重建后进度全丢,而用户还在页面上等着。
--
-- 不做过期清理:一条记录几百字节,而"这批数据什么时候下的、下了多久、
-- 有没有失败"是数据出问题时的第一手线索,清掉就只能猜。
CREATE TABLE IF NOT EXISTS data_job (
  id             BIGSERIAL     PRIMARY KEY,
  user_id        VARCHAR(64),
  scope          JSONB         NOT NULL,       -- {kind, indexes[], industries[], codes[]}
  span_months    INT           NOT NULL,       -- 0 = 只补最新
  with_financial BOOLEAN       NOT NULL DEFAULT FALSE,
  keep_raw       BOOLEAN       NOT NULL DEFAULT FALSE,  -- 是否保留财报原始归档
  status         VARCHAR(16)   NOT NULL,       -- queued|running|paused|done|failed|canceled
  total          INT           NOT NULL DEFAULT 0,
  done_count     INT           NOT NULL DEFAULT 0,
  skipped_count  INT           NOT NULL DEFAULT 0,
  failed_count   INT           NOT NULL DEFAULT 0,
  current_code   VARCHAR(10),
  phase          VARCHAR(32),                  -- 当前阶段:成分股/日线/财报/算因子
  message        TEXT,
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ,
  updated_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dj_status ON data_job (status, id DESC);


-- ═══════════════════════════════════════════════════════════════
-- 表 3 · stock_industry · 行业二级分类
-- ═══════════════════════════════════════════════════════════════
-- 一级由我们自己归并成 7 个,不直接用东财的 80+ 板块名 ——
-- 平铺给用户选反而更难挑。
CREATE TABLE IF NOT EXISTS stock_industry (
  code       VARCHAR(10)  NOT NULL,
  l1         VARCHAR(32)  NOT NULL,   -- 科技/医药/消费/新能源/金融/制造/资源
  l2         VARCHAR(32)  NOT NULL,   -- 半导体/软件服务/消费电子 …
  updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (code)
);
CREATE INDEX IF NOT EXISTS idx_si_l1 ON stock_industry (l1, l2);


-- ═══════════════════════════════════════════════════════════════
-- 表 4 · financial_metric · 财报提炼指标(窄表)· 因子直接读这张
-- ═══════════════════════════════════════════════════════════════
-- 用长表不用宽表的理由:
--   · 加指标只是多插几行,不动表结构 —— 生产迁移规则是只 ADD 不 DROP,
--     宽表加字段会撞上这条
--   · 行业口径不同(银行没有"营业成本"),宽表里一堆 NULL
--
-- 实测:资产负债表 319 列,而 10 个基本面因子拆开只用到约 15 个字段。
-- 15 指标 × 20 期 × 5400 只 = 162 万行 ≈ 110 MB。
CREATE TABLE IF NOT EXISTS financial_metric (
  code        VARCHAR(10)      NOT NULL,
  report_date DATE             NOT NULL,   -- 报告期 2026-03-31 / 2026-06-30 …
  metric_key  VARCHAR(32)      NOT NULL,   -- revenue / net_profit_parent / total_asset …
  value       DOUBLE PRECISION,
  updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
  PRIMARY KEY (code, report_date, metric_key)
);
CREATE INDEX IF NOT EXISTS idx_fm_key_date ON financial_metric (metric_key, report_date DESC);


-- ═══════════════════════════════════════════════════════════════
-- 表 5 · financial_raw · 财报原始归档(可选)
-- ═══════════════════════════════════════════════════════════════
-- 默认不写。用户在数据页勾「保留原始报表」才落这张。
--
-- 为什么值得留:下载 8.6 秒/只,解析毫秒级。以后加一个新因子
-- (比如存货周转率),有归档就是重新解析几秒,没有就要重下 1.9 小时。
--
-- 为什么只留最近 20 期:资产负债表全历史 103 期 = 26 年,而回测最多几年、
-- 因子算同比只要 8 期。留 20 期(5 年)体积从 2.4 MB/只降到 0.5 MB/只。
CREATE TABLE IF NOT EXISTS financial_raw (
  code        VARCHAR(10)  NOT NULL,
  report_date DATE         NOT NULL,
  report_type VARCHAR(16)  NOT NULL,   -- balance | profit | cashflow | indicator
  payload     JSONB        NOT NULL,
  fetched_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (code, report_date, report_type)
);


-- ═══════════════════════════════════════════════════════════════
-- 一次性回填 · 老实例的 klines 数据补 coverage 记录
-- ═══════════════════════════════════════════════════════════════
-- `data_coverage` 是新表,而已有安装的 klines 里可能已经有几百只股票的数据
-- (我们自己这台实测 805 只)。不回填的话:
--   · overview 报 stocks=0 · empty=true,提示用户"到数据页下载"
--   · 定时任务读 covered_codes() 得到空,什么都不更新
-- 也就是升级之后数据看起来凭空消失了,而它明明在库里。
--
-- 幂等:ON CONFLICT DO NOTHING。已经有 coverage 记录的不动
-- (那是下载任务写的,比这里推断出来的准)。
INSERT INTO data_coverage (code, data_type, covered_from, covered_to)
SELECT code, 'kline', min(ts), max(ts)
  FROM klines WHERE period='daily'
 GROUP BY code
ON CONFLICT (code, data_type) DO NOTHING;
