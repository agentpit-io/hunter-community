'use client'
/**
 * 总览页（一期 M6 · M-03）。
 *
 * 视觉基线：`plan/ref/原型/index.html`。页面只回答三个问题：它在帮我赚钱吗 /
 * 它现在在做什么 / 我能控制什么 —— 所以区块顺序是
 * **总资产 → 今日盈亏 → 账目自洽 → 持仓概览 → 最近动作 → 去其它页**。
 *
 * **页面上一律不写死数字**（`05 §3.2` M-03 验收项）：所有值来自 `GET /api/v1/fin/overview`，
 * 接口没给的显示 `—`。**不含「待我确认」条**（L4 是第三期，`05 §1.2`）。
 */
import Link from 'next/link'
import {
  ArrowRight, Activity, Coins, TrendingUp, Wallet, Clock, ShieldCheck, CheckCircle2,
} from 'lucide-react'
import { Card, CardHead, Chip, Note, money, pct, useCurrentProject } from '../_ui'
import { MarketSwitcher, MarketStatusLine, useCurrentMarket, useMarketStatus, currencyOf, marketsOf, onlySelected, MARKET_LABEL, type MarketStatus } from '../_market'
import { Kpi, Grid, MiniChart, RowKV, SectionTitle, Sparkline, TimelineItem, EmptyState, ErrorState, LoadingCard } from '../_parts'
import { useFinPage } from '../_data'

type Action = {
  order_id: string; code: string; name: string | null; side: 'buy' | 'sell'; qty: number
  filled_qty: number; price_type: string; limit_price: number | null; status: string
  status_text: string; outcome: 'traded' | 'declined' | 'open'; decline_reason: string | null
  trade_id: string | null; price: number | null; total_fee: number | null
  snapshot_id: string | null; created_at: string
  market: string | null; currency: string | null
}
type Combined = {
  by_market: { market: string; label: string; currency: string | null; total_assets: number | null; as_of: string | null }[]
  terms: any[]
  total_cny: number | null; currency: string
  fx_source: string | null; fx_at: string | null; reason: string | null
}
type Overview = {
  project: { project_id: string; tier: string; initial_capital: number; opened_at: string; status: string } | null
  market: string | null
  currency: string | null
  as_of: string | null
  account: {
    available: number | null; frozen: number | null; market_value: number | null
    total_assets: number | null; nav: number | null; as_of: string | null; quality: string | null
    market: string | null; currency: string | null
    equation: { available: number | null; frozen: number | null; market_value: number | null; sum: number | null; total_assets: number | null; ok: boolean } | null
    today: { date: string; prev_date: string; pnl: number; pnl_pct: number | null; nav: number | null } | null
    series: { date: string; nav: number | null; total_assets: number | null }[]
  }
  positions: { count: number; market_value_by_snapshot: number | null; missing_price_codes: string[]; items: any[] }
  recent_actions: Action[]
  switch: { auto_enabled: boolean }
  schedule: { at: string; plain: string }[]
  markets: MarketStatus[]
  combined: Combined | null
  ops: { recon: { passed: boolean; as_of: string } | null; jobs: any[] }
  notes: string[]
}

const ENTRIES = [
  { href: '/finance/auto-trade', t: '自动交易', d: '一个开关控制全部。选策略、调风险、看它今天做了什么。' },
  { href: '/finance/account', t: '我的账户', d: '看持仓与成交。每笔成交价都能追到当时那一刻的行情快照。' },
  { href: '/finance/report', t: '每日报告', d: '每个交易日一份复盘：它做了什么、为什么、明天关注什么。' },
  { href: '/finance/help', t: '安全与帮助', d: '只做模拟的承诺、常见问题、怎么一键暂停。' },
]

/** 下一轮运行。**按所选市场的当地时间**算（市场时区来自 `/markets`，不写死 +8）。 */
function nextRun(schedule: { at: string }[], m: MarketStatus | undefined): string | null {
  if (!schedule.length) return null
  let hhmm = ''
  let wd = -1
  if (m?.local_time) {
    const d = new Date(m.local_time)
    if (!Number.isNaN(d.getTime())) { hhmm = d.toISOString().slice(11, 16); wd = d.getUTCDay() }
  }
  if (!hhmm) {
    const now = new Date()
    const sh = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000)
    hhmm = `${String(sh.getHours()).padStart(2, '0')}:${String(sh.getMinutes()).padStart(2, '0')}`
    wd = sh.getDay()
  }
  for (const p of [...schedule].sort((a, b) => a.at.localeCompare(b.at))) {
    if (p.at > hhmm) return `今天 ${p.at}`
  }
  return wd === 5 ? '下周一 09:15' : '明天 09:15'
}

export default function FinanceOverviewPage() {
  const proj = useCurrentProject()
  // 已选市场（P3）：切换器、分列卡片、跨市场合计都以它为准 —— 不再列三个市场。
  const allowed = marketsOf(proj.project)
  const [market, setMarket] = useCurrentMarket('CN_A', allowed)
  const { data, loading, error, noProject, reload } = useFinPage<Overview>(`/overview?market=${market}`, { skip: proj.loading })
  const { markets } = useMarketStatus()

  if (loading) return <Card><CardHead title="总览" sub="正在从模拟账本读取" /><div className="p-4"><LoadingCard title="加载中…" /></div></Card>

  if (noProject) {
    return (
      <div className="max-w-3xl">
        <Card>
          <CardHead title="总览" sub="还没有进行中的项目" />
          <div className="p-5">
            <EmptyState
              title="你还没有开启模拟项目"
              desc="走一遍设置向导（七步，约一分钟）：选资金档位、再选做哪几个市场（可多选），系统会按档位与市场写死各子账户本金与整套参数，然后它就开始按交易日自动运行。"
              cta={<Link href="/finance/setup" className="mt-1 inline-flex items-center gap-1.5 rounded-[10px] px-4 py-2 text-sm font-semibold"
                style={{ background: 'var(--blue)', color: '#fff' }}>去设置向导<ArrowRight className="w-4 h-4" /></Link>} />
          </div>
        </Card>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="max-w-3xl">
        <Card>
          <CardHead title="总览" sub="读取失败" />
          <div className="p-5">
            <ErrorState title="读不到账本数据" onRetry={reload}
              desc={`${error || '接口没有返回内容'}。页面上的数字全部来自模拟账本接口，取不到就如实显示失败 —— 这里不会拿旧值或估算值顶上。`} />
          </div>
        </Card>
      </div>
    )
  }

  const a = data.account
  const eq = a.equation
  const navs = a.series.map(s => s.nav).filter((v): v is number => typeof v === 'number')
  const todayPnl = a.today?.pnl ?? null
  const todayPct = a.today?.pnl_pct ?? null
  const cumPct = a.nav !== null ? a.nav - 1 : null
  // 只列这个项目**已选**的市场（`allowed` 为空 = 历史遗留项目，退回不限制的老行为）
  const mkList = onlySelected(data.markets || markets, allowed)
  const mk = mkList.find(m => m.market === market)
  const next = nextRun(data.schedule, mk)
  const cur = currencyOf(market, a.currency || data.currency)
  const comb = data.combined
  // 跨市场汇总只在**选了 2 个及以上**市场时才出现（P3 · 方案 §5.4）：
  // 单市场项目把本币折成人民币不叫「跨市场合计」，那是凭空多出来的一个数字。
  const multi = allowed.length >= 2
  // 分列只列**已选**市场（`comb.by_market` 是「有估值的市场」，两者取交集最诚实：
  // 已选但还没估值的市场不在里面，卡片本身会有空态说明）
  const byMarket = onlySelected(comb?.by_market, allowed)

  return (
    <div className="flex flex-col gap-6 max-w-6xl">
      {/* 市场切换器 + 市场状态（N5）—— 直接对齐策略中心那一排圆角按钮 */}
      <Card>
        <div className="p-4 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>看哪个市场</div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                这里只列**这个项目已选**的市场（各自独立账本、各自本币记账）。切换只换视角，不改任何设置。
              </div>
            </div>
            <MarketSwitcher value={market} onChange={setMarket} markets={allowed.length ? allowed : undefined} />
          </div>
          <div className="flex gap-4 flex-wrap">
            {mkList.map(m => <MarketStatusLine key={m.market} m={m} />)}
            {mkList.length === 0 && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>市场状态读取中…</span>}
          </div>
        </div>
      </Card>

      {/* Hero */}
      <Card style={{ background: 'linear-gradient(135deg,rgba(176,106,50,.15),rgba(176,106,50,.03) 55%,var(--bg-card))', borderColor: 'rgba(176,106,50,.28)' }}>
        <div className="p-6 grid gap-6 lg:grid-cols-[1.4fr_1fr] items-center">
          <div>
            <div className="flex gap-2 flex-wrap mb-3">
              <Chip tone="copper">模拟盘 · 一期</Chip>
              <Chip tone="ok">仅模拟 · 不接实盘</Chip>
              {data.switch.auto_enabled ? <Chip tone="ok">自动交易运行中</Chip> : <Chip tone="amber">自动交易已暂停</Chip>}
            </div>
            <h1 className="text-2xl font-extrabold m-0 mb-2 leading-snug" style={{ color: 'var(--text)' }}>
              让 AI 智能体替你盯盘，<br />你只需要点一下开关
            </h1>
            <p className="text-sm m-0 mb-4 max-w-xl leading-relaxed" style={{ color: '#5C5348' }}>
              它按你选的策略做判断、在<b>虚拟账户</b>里自动买卖，每个交易日给你一份说人话的复盘。
              全程不接实盘，钱是假的，流程是真的。
            </p>
            <div className="flex gap-2 flex-wrap">
              <Link href="/finance/setup" className="inline-flex items-center gap-1.5 rounded-[10px] px-4 py-2 text-sm font-semibold" style={{ background: 'var(--blue)', color: '#fff' }}>
                首次使用？走设置向导
              </Link>
              <Link href="/finance/auto-trade" className="inline-flex items-center gap-1.5 rounded-[10px] border px-4 py-2 text-sm font-semibold" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }}>
                进入自动交易
              </Link>
            </div>
          </div>

          <Card style={{ borderColor: 'rgba(63,107,64,.28)', background: 'rgba(63,107,64,.05)' }}>
            <div className="p-4">
              <div className="flex items-center justify-between gap-2 mb-3">
                <span className="text-sm font-bold" style={{ color: 'var(--text)' }}>当前状态</span>
                <Chip tone="ok">账本自洽 <CheckCircle2 className="w-3 h-3" /></Chip>
              </div>
              <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>模拟总资产{a.as_of ? ` · 截至 ${a.as_of.slice(0, 10)} 收盘` : ''}</div>
              <div className="text-3xl font-extrabold" style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{money(a.total_assets, cur)}</div>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className="text-sm font-bold" style={{ color: (todayPnl ?? 0) >= 0 ? 'var(--red)' : 'var(--green)' }}>
                  {todayPnl === null ? '—' : `${todayPnl >= 0 ? '+' : '−'}${money(Math.abs(todayPnl), cur)}`}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>最后交易日 · {pct(todayPct)}</span>
                <Sparkline values={navs} width={96} height={28} />
              </div>
              <div className="mt-3 pt-3 text-xs flex items-center gap-2 flex-wrap" style={{ borderTop: '1px dashed rgba(216,205,186,.9)', color: 'var(--text-muted)' }}>
                <Coins className="w-3.5 h-3.5" />初始资金 {money(data.project?.initial_capital, cur)} · 市场范围 {MARKET_LABEL[market] || market}
              </div>
              <div className="mt-2 text-xs flex items-center gap-2" style={{ color: 'var(--text-muted)' }}>
                <Clock className="w-3.5 h-3.5" />下一轮自动运行：{next || '—'}
              </div>
            </div>
          </Card>
        </div>
      </Card>

      {/* KPI */}
      <Grid cols={4}>
        <Kpi label="模拟总资产" value={money(a.total_assets, cur)} icon={<Coins className="w-4 h-4" />}
          hint={`初始 ${money(data.project?.initial_capital, cur)} · 本市场子账户（${MARKET_LABEL[market] || market}）`} />
        <Kpi label="最后交易日盈亏" value={a.today ? `${todayPnl! >= 0 ? '+' : '−'}${money(Math.abs(todayPnl!), cur)}` : '—'}
          delta={pct(todayPct)} deltaTone={(todayPct ?? 0) >= 0 ? 'up' : 'down'}
          icon={<TrendingUp className="w-4 h-4" />}
          hint={a.today ? `${a.today.prev_date} → ${a.today.date}` : '需要一个以上的收盘估值'} />
        <Kpi label="累计收益率" value={pct(cumPct)} delta={money((a.total_assets ?? 0) - (data.project?.initial_capital ?? 0), cur)}
          deltaTone={(cumPct ?? 0) >= 0 ? 'up' : 'down'} icon={<Activity className="w-4 h-4" />}
          hint={`净值 ${a.nav === null ? '—' : a.nav.toFixed(4)}`} />
        <Kpi label="已记录收盘估值" value={`${a.series.length} 个交易日`} icon={<Clock className="w-4 h-4" />}
          hint={data.ops.recon ? `对账${data.ops.recon.passed ? '通过' : '未通过'} · ${data.ops.recon.as_of?.slice(0, 10)}` : '还没有对账记录'} />
      </Grid>

      {/* 按市场分列 + 跨市场合计（N5）—— 合计处标注汇率来源与时刻，取不到显示 — */}
      {/* P3：只列已选市场；「跨市场合计」只在选了 ≥2 个市场时才出现（单市场折人民币不叫跨市场合计）。 */}
      <Card>
        <CardHead title={multi ? '已选市场分别有多少' : '这个市场有多少'} icon={<Coins className="w-4 h-4" />}
          sub={multi
            ? '各自本币记账、不做折算；只有「跨市场合计」一处出现折算值'
            : '本币记账、不做折算；只选了一个市场，所以没有「跨市场合计」'}
          right={multi && comb ? <Chip tone={comb.total_cny === null ? 'amber' : 'ok'}>
            {comb.total_cny === null ? '合计算不出' : '合计可算'}</Chip> : undefined} />
        <div className="p-4 grid gap-5 md:grid-cols-2">
          <div>
            {byMarket.length === 0
              ? <EmptyState title="还读不到任何市场的估值" desc="每个市场收盘后各写一行估值，这里会出现分列。" />
              : (byMarket.map(b => (
                <RowKV key={b.market}
                  k={<span>{b.label}{b.market === market ? <Chip tone="copper">当前</Chip> : null}</span>}
                  v={money(b.total_assets, currencyOf(b.market, b.currency))} />
              )))}
            {multi && (
              <RowKV k={<b>跨市场合计（折人民币）</b>}
                v={<b>{money(comb?.total_cny ?? null, 'CNY')}</b>} />
            )}
          </div>
          <div className="flex flex-col gap-3">
            {multi && comb?.total_cny !== null && comb?.fx_source && (
              <Note tone="ok">
                折算依据：汇率来源 <b>{comb.fx_source}</b>，取值时刻 <b>{comb.fx_at || '—'}</b>。
                汇率**请求时现取**，不落账本（`11-…实施方案.md` §3.1 A 方案）。
              </Note>
            )}
            {multi && comb?.total_cny === null && (
              <Note tone="warn">
                跨市场合计显示 <b>—</b>（不是 0）。原因：{comb?.reason || '汇率不可得'}。
                <b>取不到汇率就不折算</b>，这一列如实留空。
              </Note>
            )}
            {multi && comb?.terms?.some((t: any) => t.fx) && (
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {comb.terms.filter((t: any) => t.fx).map((t: any) => (
                  <div key={t.market}>
                    {t.label}：{money(t.total_assets, currencyOf(t.market, t.currency))} ×{' '}
                    {t.fx.rate}（{t.fx.source}） = {money(t.converted, 'CNY')}
                  </div>
                ))}
              </div>
            )}
            <Note tone="copper">
              每个市场是**独立子账户**：港币盈亏不会因为汇率波动而改变账本数字。
              这里的分列都是只读展示，账本内没有任何跨币种换算。
            </Note>
          </div>
        </div>
      </Card>

      {/* 账目自洽 */}
      <Card>
        <CardHead title="账目自洽" icon={<ShieldCheck className="w-4 h-4" />}
          sub="持仓市值 + 可用资金 + 冻结资金 = 总资产 · 这一条由模拟账本每次收盘对账核一遍"
          right={eq ? <Chip tone={eq.ok ? 'ok' : 'amber'}>{eq.ok ? '等式成立' : '等式不平'}</Chip> : undefined} />
        <div className="p-4 grid gap-5 md:grid-cols-2">
          <div>
            <RowKV k="持仓市值" v={money(eq?.market_value, cur)} />
            <RowKV k="可用资金" v={money(eq?.available, cur)} />
            <RowKV k="冻结资金（挂单占用）" v={money(eq?.frozen, cur)} />
            <RowKV k={<b>合计</b>} v={<b>{money(eq?.sum, cur)}</b>} />
            <RowKV k="账本记录的总资产" v={<b>{money(eq?.total_assets, cur)}</b>} />
          </div>
          <div className="flex flex-col gap-3">
            {data.positions.missing_price_codes.length > 0 && (
              <Note tone="warn">这些代码暂时没有行情快照，现价与浮动盈亏显示 <b>—</b>：{data.positions.missing_price_codes.join('、')}</Note>
            )}
            {!eq?.ok && <Note tone="warn">账本记录的总资产与三项之和对不上。这种情况下页面照实显示两个数，不做四舍五入去凑。</Note>}
            <Note tone="copper">
              总资产与净值取自<b>最后一行收盘估值</b>（由账本服务写下）；「合计」是页面按三项重加一遍的结果 ——
              能自己验一遍，这个等式才算证据。
            </Note>
            {data.notes.map((n, i) => <Note key={i} tone="copper">{n}</Note>)}
          </div>
        </div>
      </Card>

      {/* 持仓概览 + 净值 */}
      <Grid cols={2}>
        <Card>
          <CardHead title="持仓概览" icon={<Wallet className="w-4 h-4" />}
            sub={`${data.positions.count} 只 · 按最新快照价`} right={<Link href="/finance/account" className="text-xs font-semibold" style={{ color: '#8A5A18' }}>看全部</Link>} />
          <div className="p-4">
            {data.positions.items.length === 0 ? (
              <EmptyState title="账户里还没有持仓" desc="打开自动交易并跑完第一轮后，这里会出现持仓明细。" />
            ) : (
              <div className="flex flex-col">
                {data.positions.items.slice(0, 6).map((p: any) => (
                  <div key={p.code} className="flex items-center justify-between gap-3 py-2.5" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
                    <div className="min-w-0">
                      <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{p.name || p.code} <span className="font-mono text-[11px]" style={{ color: 'var(--text-muted)' }}>{p.code}</span></div>
                      <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{p.qty} 股 · 成本 {money(p.avg_cost, currencyOf(p.market, p.currency))} · 现价 {money(p.last_price, currencyOf(p.market, p.currency))}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{money(p.market_value, currencyOf(p.market, p.currency))}</div>
                      <div className="text-[11px] font-semibold" style={{ color: (p.pnl ?? 0) >= 0 ? 'var(--red)' : 'var(--green)' }}>
                        {p.pnl === null ? '—' : `${p.pnl >= 0 ? '+' : '−'}${money(Math.abs(p.pnl), currencyOf(p.market, p.currency))} · ${pct(p.pnl_pct)}`}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>

        <Card>
          <CardHead title="净值走势" icon={<Activity className="w-4 h-4" />} sub={`${a.series.length} 个交易日的收盘净值`} />
          <div className="p-4">
            {navs.length < 2
              ? <EmptyState title="净值曲线还画不出来" desc="至少要两个交易日的收盘估值。第一个交易日结束后这里会出现一条线。" />
              : (
                <>
                  <MiniChart series={navs} />
                  <div className="flex gap-5 flex-wrap mt-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    <span>{a.series[0]?.date} → {a.series[a.series.length - 1]?.date}</span>
                    <span>期初净值 <b style={{ color: 'var(--text)' }}>{navs[0]?.toFixed(4)}</b></span>
                    <span>期末净值 <b style={{ color: 'var(--text)' }}>{navs[navs.length - 1]?.toFixed(4)}</b></span>
                  </div>
                </>
              )}
          </div>
        </Card>
      </Grid>

      {/* 最近动作 */}
      <div>
        <SectionTitle title="最近动作" sub="每一笔都能看懂：买了什么、按什么价成交、是怎么决定的"
          right={<Link href="/finance/auto-trade" className="text-xs font-semibold" style={{ color: '#8A5A18' }}>看今天的全部动作</Link>} />
        <Card>
          <div className="px-4 py-1">
            {data.recent_actions.length === 0 ? (
              <EmptyState title="还没有任何委托" desc="它还没有下过单。等下一个交易时点（09:30 出意图）跑完，这里会出现记录。" />
            ) : data.recent_actions.map(o => (
              <TimelineItem key={o.order_id + (o.trade_id || '')}
                time={o.created_at?.slice(11, 16) || '—'}
                title={<>
                  {o.outcome === 'declined' ? '放弃' : o.outcome === 'open' ? '挂着' : ''}{o.side === 'buy' ? '买入' : '卖出'} {o.name || o.code}{' '}
                  <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                    {o.filled_qty || o.qty} 股{o.price !== null ? ` · ${money(o.price, currencyOf(o.market, o.currency))}` : ''}
                  </span>
                </>}
                chip={<Chip tone={o.outcome === 'traded' ? 'ok' : o.outcome === 'declined' ? 'amber' : 'slate'}>{o.status_text}</Chip>}
                reason={o.outcome === 'declined'
                  ? `被风控拦下：${o.decline_reason || '未记录原因'}`
                  : (o.snapshot_id ? `成交价依据：快照 ${o.snapshot_id}` : undefined)} />
            ))}
          </div>
        </Card>
      </div>

      {/* 入口 */}
      <div>
        <SectionTitle title="其它页面" sub="整个板块只有这几件事" />
        <Grid cols={4}>
          {ENTRIES.map(e => (
            <Link key={e.href} href={e.href} className="block rounded-2xl border p-4 transition-colors"
              style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
              <div className="text-sm font-bold mb-1.5" style={{ color: 'var(--text)' }}>{e.t}</div>
              <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{e.d}</div>
              <div className="text-xs font-semibold mt-2.5 flex items-center gap-1" style={{ color: '#8A5A18' }}>
                进入 <ArrowRight className="w-3 h-3" />
              </div>
            </Link>
          ))}
        </Grid>
      </div>

      <Note tone="copper">
        本页所有数字来自 <code className="text-[11px]">GET /api/v1/fin/overview</code>（模拟账本），
        页面上没有任何写死的数值；接口取不到时显示失败态或 `—`。
      </Note>
    </div>
  )
}
