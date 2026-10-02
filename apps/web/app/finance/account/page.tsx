'use client'
/**
 * 我的账户页（一期 M6 · M-05）。
 *
 * 视觉基线：`plan/ref/原型/02-我的账户.html`。一期的子集：
 * **不含「归属」列、不含「来源」列与「当时的信号」列**（`05 §1.2` H-04 / H-05，
 * 那两列是二期归因的地基；一期成交恒为 AI 自主）。
 *
 * 三条硬要求：
 *   · 金额输入框 **恒为只读**（档位锁定，想换金额只能开新项目）
 *   · 快照凭证的编号能与真实快照记录对上（点开能看到那一份）
 *   · 「开新项目」走 M-16 的关停流程，确认框写清代价
 */
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Coins, Wallet, Lock, RefreshCw, ShieldCheck, Clock, TrendingUp, Activity, FileSearch, X,
} from 'lucide-react'
import { Button, Card, CardHead, Chip, Note, money, pct, finFetch, currencySymbol } from '../_ui'
import { MarketSwitcher, MarketStatusLine, useCurrentMarket, useMarketStatus, currencyOf, MARKET_LABEL, type MarketStatus } from '../_market'
import { Grid, Kpi, Meter, MiniChart, RowKV, SectionTitle, Tbl, EmptyState, ErrorState, LoadingCard } from '../_parts'
import { useFinPage, finPost } from '../_data'

type Snap = {
  snapshot_id: string; code: string; snapshot_time: string; source: string
  last_price: number | null; bid1_price: number | null; ask1_price: number | null
  prev_close: number | null; quality: string; missing_flag: boolean
}
type Account = {
  project: { project_id: string; tier: string; status: string; initial_capital: number; currency: string; market_scope: string; opened_at: string; version: number } | null
  market: string | null
  currency: string | null
  markets: MarketStatus[]
  account: {
    available: number | null; frozen: number | null; market_value: number | null
    total_assets: number | null; nav: number | null; as_of: string | null
    market: string | null; currency: string | null
    equation: { sum: number | null; total_assets: number | null; ok: boolean } | null
    today: { date: string; prev_date: string; pnl: number; pnl_pct: number | null } | null
    series: { date: string; nav: number | null; total_assets: number | null }[]
  }
  positions: any[]
  positions_truncated: boolean
  trades: any[]
  cash_entries: any[]
  param_change_log: any[]
  constraints: { key: string; title: string; text: string }[]
  tier_options: { tier: string; label: string }[]
}

const TIER_LABEL: Record<string, string> = { play: '个人玩玩', manage: '个人资产管理', operate: '资产运营' }

export default function FinanceAccountPage() {
  const router = useRouter()
  const [market, setMarket] = useCurrentMarket()
  const { data, loading, error, noProject, reload } = useFinPage<Account>(`/account?market=${market}`)
  const { markets } = useMarketStatus()
  const [voucher, setVoucher] = useState<{ id: string; snap: Snap | null; err: string; loading: boolean } | null>(null)
  const [newOpen, setNewOpen] = useState(false)
  const [newTier, setNewTier] = useState('manage')
  const [newBusy, setNewBusy] = useState(false)
  const [newErr, setNewErr] = useState('')

  async function openVoucher(snapshotId: string) {
    setVoucher({ id: snapshotId, snap: null, err: '', loading: true })
    try {
      const r = await finFetch<{ snapshot: Snap }>(`/snapshots/${encodeURIComponent(snapshotId)}`)
      setVoucher({ id: snapshotId, snap: r.snapshot, err: '', loading: false })
    } catch (e: any) {
      if (e?.status === 401) { router.push('/login'); return }
      setVoucher({ id: snapshotId, snap: null, err: e?.message || '读不到这份快照', loading: false })
    }
  }

  async function doOpenNew() {
    setNewBusy(true); setNewErr('')
    try {
      await finPost('/projects/new', { tier: newTier, reason: 'user_opened_new' })
      setNewOpen(false)
      router.refresh()
      reload()
      location.reload()
    } catch (e: any) {
      if (e?.status === 401) { router.push('/login'); return }
      setNewErr(e?.message || '开新项目失败')
    } finally {
      setNewBusy(false)
    }
  }

  if (loading) return <Card><CardHead title="我的账户" sub="正在从模拟账本读取" /><div className="p-4"><LoadingCard title="加载中…" /></div></Card>
  if (noProject) return (
    <div className="max-w-3xl"><Card><CardHead title="我的账户" sub="还没有进行中的项目" />
      <div className="p-5"><EmptyState title="先在设置向导里开一个项目" desc="开户时确定初始资金与市场范围；开完之后这里会显示持仓、成交与快照凭证。" /></div>
    </Card></div>
  )
  if (error || !data) return (
    <div className="max-w-3xl"><Card><CardHead title="我的账户" sub="读取失败" />
      <div className="p-5"><ErrorState title="读不到账户数据" onRetry={reload}
        desc={`${error || '接口没有返回内容'}。金额、持仓、成交都来自模拟账本，取不到就如实报错 —— 不拿旧值顶上。`} /></div>
    </Card></div>
  )

  const a = data.account
  const navs = a.series.map(s => s.nav).filter((v): v is number => typeof v === 'number')
  const todayPnl = a.today?.pnl ?? null
  const cumPct = a.nav !== null ? a.nav - 1 : null
  const mv = a.market_value
  const total = a.total_assets
  const pctOf = (v: number | null) => (v === null || !total ? null : (v / total) * 100)
  const lastTradeWithSnap = data.trades.find(t => t.snapshot_id)
  const mkList = data.markets || markets || []
  const mk = mkList.find(m => m.market === market)
  const cur = currencyOf(market, a.currency || data.currency)

  return (
    <div className="flex flex-col gap-6 max-w-6xl">
      {/* 顶部大数字 */}
      <Grid cols={4}>
        <Kpi label="模拟总资产" value={money(total, cur)} icon={<Coins className="w-4 h-4" />}
          hint={`现金 + 持仓市值${a.as_of ? ` · 截至 ${a.as_of.slice(0, 10)} 收盘` : ''}`} />
        <Kpi label="最后交易日盈亏" value={todayPnl === null ? '—' : `${todayPnl >= 0 ? '+' : '−'}${money(Math.abs(todayPnl), cur)}`}
          delta={pct(a.today?.pnl_pct)} deltaTone={(a.today?.pnl_pct ?? 0) >= 0 ? 'up' : 'down'}
          icon={<TrendingUp className="w-4 h-4" />} hint={a.today ? `${a.today.prev_date} → ${a.today.date}` : '需要两个收盘估值'} />
        <Kpi label="累计收益" value={pct(cumPct)} delta={money((total ?? 0) - (data.project?.initial_capital ?? 0), cur)}
          deltaTone={(cumPct ?? 0) >= 0 ? 'up' : 'down'} icon={<Activity className="w-4 h-4" />}
          hint={`净值 ${a.nav === null ? '—' : a.nav.toFixed(4)}`} />
        <Kpi label="持仓市值" value={money(mv, cur)} icon={<Wallet className="w-4 h-4" />}
          hint={`${data.positions.length} 只 · 占比 ${pct(pctOf(mv) === null ? null : pctOf(mv)! / 100)}`} />
      </Grid>

      {/* ① 资金与市场设置 */}
      <div>
        <SectionTitle title="① 资金与市场设置" sub="账户的起点：给多少虚拟本金、做哪个市场" />
        <Grid cols={2}>
          <Card>
            <CardHead icon={<Coins className="w-4 h-4" />} title="初始资金（人民币）"
              sub="金额由所选档位决定，任何时候不可修改；要换金额只能开新项目" />
            <div className="p-4 flex flex-col gap-3">
              <div className="flex gap-2.5 items-center flex-wrap">
                <span className="inline-flex items-center gap-2 rounded-[10px] border px-3 py-2"
                  style={{ borderColor: 'var(--border)', background: 'rgba(122,111,99,.08)' }}>
                  <span className="text-sm" style={{ color: 'var(--text-muted)' }}>{currencySymbol(cur) || cur}</span>
                  {/* 只读：档位金额不可修改（03 §11.1 拍板 1）。input 带 readOnly，
                      并且没有 onChange —— 界面上根本没有能改它的路径。 */}
                  <input readOnly value={data.project ? data.project.initial_capital.toLocaleString('zh-CN') : ''}
                    aria-label="初始资金（只读）" className="bg-transparent outline-none text-sm font-bold"
                    style={{ color: 'var(--text)', width: 140, cursor: 'not-allowed' }} />
                </span>
                <Button disabled size="sm"><Lock className="w-3.5 h-3.5" />档位锁定</Button>
              </div>
              <div>
                <RowKV k="所属档位" v={`${TIER_LABEL[data.project?.tier || ''] || data.project?.tier} · 金额不可修改`} />
                <RowKV k="本币" v={`${cur}（该市场本币；账本内不做任何折算）`} />
                <RowKV k="账户编号" v={data.project?.project_id} mono />
                <RowKV k="项目开启" v={`${data.project?.opened_at?.slice(0, 10)} · 开启即锁定`} />
                <RowKV k="账本版本" v={`v${data.project?.version}`} />
              </div>
              <Note tone="copper">
                初始资金决定收益率的<b>分母</b>，所以它在项目开启那一刻就锁死 —— 这不是不方便，
                是刻意堵住「事后偷偷调一下分母」的口子。
              </Note>
            </div>
          </Card>

          <Card>
            <CardHead icon={<Wallet className="w-4 h-4" />} title="市场与子账户"
              sub="一个项目下每个市场各一个子账户，各自本币记账" />
            <div className="p-4 flex flex-col gap-3">
              <MarketSwitcher value={market} onChange={setMarket} />
              <div className="flex flex-col gap-2">
                {mkList.length === 0 && <div className="text-xs" style={{ color: 'var(--text-muted)' }}>市场状态读取中…</div>}
                {mkList.map(m => (
                  <div key={m.market} className="flex items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5"
                    style={{
                      borderColor: m.market === market ? 'var(--blue)' : 'var(--border)',
                      background: m.market === market ? 'rgba(176,106,50,.06)' : 'var(--bg-card)',
                    }}>
                    <div>
                      <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                        {m.label} · {m.currency}
                      </div>
                      <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{m.note}</div>
                    </div>
                    <Chip tone={m.state === 'open' ? 'ok' : m.state === 'unknown' ? 'amber' : 'slate'}>
                      {m.state_label}
                    </Chip>
                  </div>
                ))}
              </div>
              <Note tone="ok">
                三个市场的交易时段、T+1 / T+0、整手、价格带、费用规则各不相同，各自按市场规则执行；
                <b>金额一律以该市场本币记账，账本内不做任何折算</b>。要用哪个市场，用上面的切换器切过去即可。
              </Note>
              <div className="pt-3" style={{ borderTop: '1px dashed rgba(216,205,186,.9)' }}>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>更换金额或档位：开新项目</div>
                    <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>关闭当前项目并开启新项目，另选档位</div>
                  </div>
                  <Button size="sm" onClick={() => { setNewOpen(true); setNewErr('') }}><RefreshCw className="w-3.5 h-3.5" />开新项目</Button>
                </div>
                <div className="text-[11px] mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                  开新项目会<b>关闭当前项目</b>（旧账本封存、只读、可回看），新项目从零开始计。<b>不删除</b>任何历史记录。
                </div>
              </div>
            </div>
          </Card>
        </Grid>
      </div>

      {/* 净值 + 账户构成 */}
      <Grid cols={2}>
        <Card>
          <CardHead icon={<Activity className="w-4 h-4" />} title="模拟净值走势"
            sub={`${a.series.length} 个交易日的收盘净值`} />
          <div className="p-4">
            {navs.length < 2 ? <EmptyState title="净值曲线还画不出来" desc="至少要两个交易日的收盘估值。" />
              : (
                <>
                  <MiniChart series={navs} />
                  <div className="flex gap-5 flex-wrap mt-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    <span>{a.series[0]?.date} → {a.series[a.series.length - 1]?.date}</span>
                    <span>区间收益 <b style={{ color: 'var(--text)' }}>{pct(cumPct)}</b></span>
                  </div>
                </>
              )}
          </div>
        </Card>
        <Card>
          <CardHead icon={<Coins className="w-4 h-4" />} title="账户构成" sub="现金 / 持仓 比例" />
          <div className="p-4 flex flex-col gap-4">
            <div>
              <div className="flex justify-between text-xs mb-1.5"><span style={{ color: 'var(--text-muted)' }}>持仓市值</span><b>{money(mv, cur)} · {pct(pctOf(mv) === null ? null : pctOf(mv)! / 100)}</b></div>
              <Meter pct={pctOf(mv)} />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1.5"><span style={{ color: 'var(--text-muted)' }}>可用资金</span><b>{money(a.available, cur)} · {pct(pctOf(a.available) === null ? null : pctOf(a.available)! / 100)}</b></div>
              <Meter pct={pctOf(a.available)} tone="green" />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1.5"><span style={{ color: 'var(--text-muted)' }}>冻结资金（挂单占用）</span><b>{money(a.frozen, cur)} · {pct(pctOf(a.frozen) === null ? null : pctOf(a.frozen)! / 100)}</b></div>
              <Meter pct={pctOf(a.frozen)} tone="red" />
            </div>
            <div>
              <RowKV k="初始资金" v={money(data.project?.initial_capital, cur)} />
              <RowKV k="冻结原因" v={data.cash_entries.find(e => e.kind === 'freeze')?.memo || '—'} />
              <RowKV k="模式" v={<Chip tone="ok">仅模拟 · 不可切换</Chip>} />
            </div>
          </div>
        </Card>
      </Grid>

      {/* 持仓表 */}
      <Card>
        <CardHead icon={<Wallet className="w-4 h-4" />} title="当前持仓"
          sub={`${data.positions.length} 只 · 市值 ${money(mv, cur)}`} />
        {data.positions.length === 0 ? (
          <div className="p-4"><EmptyState title="账户里还没有持仓" desc="打开自动交易并跑完第一轮后，这里会出现持仓明细。" /></div>
        ) : (
          <Tbl
            head={['名称', { t: '数量', r: true }, { t: '成本价', r: true }, { t: '现价', r: true }, { t: '浮动盈亏', r: true }, { t: '收益率', r: true }, { t: '占比', r: true }]}
            rows={data.positions.map(p => [
              <><span className="font-semibold">{p.name || p.code}</span><br /><span className="font-mono text-[11px]" style={{ color: 'var(--text-muted)' }}>{p.code}{p.board ? ` · ${p.board}` : ''}</span></>,
              `${p.qty} 股`,
              money(p.avg_cost, currencyOf(p.market, p.currency)),
              money(p.last_price, currencyOf(p.market, p.currency)),
              <span style={{ color: (p.pnl ?? 0) >= 0 ? 'var(--red)' : 'var(--green)', fontWeight: 600 }}>{p.pnl === null ? '—' : `${p.pnl >= 0 ? '+' : '−'}${money(Math.abs(p.pnl), currencyOf(p.market, p.currency))}`}</span>,
              <span style={{ color: (p.pnl_pct ?? 0) >= 0 ? 'var(--red)' : 'var(--green)' }}>{pct(p.pnl_pct)}</span>,
              pct(pctOf(p.market_value) === null ? null : pctOf(p.market_value)! / 100),
            ])}
            foot={<><ShieldCheck className="w-3.5 h-3.5" /><span>持仓与市值来自<b>唯一的模拟账本</b>；这些持仓全部由它按规则买卖，人只看不动手。现价取该代码最新一份行情快照。</span></>}
          />
        )}
      </Card>

      {/* 成交明细 */}
      <Card>
        <CardHead icon={<Clock className="w-4 h-4" />} title="最近成交"
          sub="每一笔都有价格出处、有编号、可追溯" />
        {data.trades.length === 0 ? (
          <div className="p-4"><EmptyState title="还没有成交" desc="它还没有成交过任何一笔。成交之后这里会列出价格与对应的行情快照编号。" /></div>
        ) : (
          <Tbl
            head={['时间', '方向', '标的', { t: '数量', r: true }, { t: '成交价 / 依据', r: true }, { t: '费用', r: true }]}
            rows={data.trades.map(t => [
              <span className="font-mono text-xs">{String(t.traded_at || '').slice(0, 16).replace('T', ' ')}</span>,
              <Chip tone={t.side === 'buy' ? 'up' : 'down'}>{t.side === 'buy' ? '买入' : '卖出'}</Chip>,
              <><span className="font-semibold">{t.name || t.code}</span> <span className="font-mono text-[11px]" style={{ color: 'var(--text-muted)' }}>{t.code}</span></>,
              `${t.qty} 股`,
              <div>
                <span className="font-semibold">{money(t.price, currencyOf(t.market, t.currency))}</span>
                <div className="text-[11px] mt-0.5">
                  {t.snapshot_id
                    ? <button onClick={() => openVoucher(t.snapshot_id)} className="inline-flex items-center gap-1 font-mono"
                      style={{ color: '#8A5A18', textDecoration: 'underline', cursor: 'pointer' }}>
                      <FileSearch className="w-3 h-3" />{t.snapshot_id}
                    </button>
                    : <span style={{ color: 'var(--text-muted)' }}>—</span>}
                </div>
              </div>,
              money(t.total_fee, currencyOf(t.market, t.currency)),
            ])}
            foot={<><ShieldCheck className="w-3.5 h-3.5" /><span>每一笔委托都带唯一编号与请求摘要，<b>重复提交不会重复下单</b>。点快照编号可以展开那份凭证。</span></>}
          />
        )}
      </Card>

      {/* 快照凭证 */}
      {voucher && (
        <Card>
          <CardHead icon={<FileSearch className="w-4 h-4" />} title="快照凭证"
            sub={`编号 ${voucher.id} · 与账本里那一份是同一条记录`}
            right={<button onClick={() => setVoucher(null)} className="p-1" aria-label="关闭"><X className="w-4 h-4" /></button>} />
          <div className="p-4">
            {voucher.loading && <LoadingCard title="正在读取快照…" />}
            {voucher.err && <ErrorState title="读不到这份快照" desc={voucher.err} />}
            {voucher.snap && (
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <RowKV k="快照编号" v={voucher.snap.snapshot_id} mono />
                  <RowKV k="标的" v={voucher.snap.code} mono />
                  <RowKV k="数据源时刻" v={String(voucher.snap.snapshot_time).slice(0, 19).replace('T', ' ')} mono />
                  <RowKV k="数据源" v={voucher.snap.source} mono />
                </div>
                <div>
                  <RowKV k="最新价" v={money(voucher.snap.last_price, cur)} />
                  <RowKV k="买一 / 卖一" v={`${money(voucher.snap.bid1_price, cur)} / ${money(voucher.snap.ask1_price, cur)}`} />
                  <RowKV k="昨收" v={money(voucher.snap.prev_close, cur)} />
                  <RowKV k="质量" v={<Chip tone={voucher.snap.quality === 'ok' ? 'ok' : 'amber'}>{voucher.snap.quality}</Chip>} />
                </div>
              </div>
            )}
            <div className="mt-3">
              <Note tone="warn">
                口径要诚实：模拟撮合按<b>公开行情快照价</b>成交，不可能逐笔复刻真实排队的成交明细。
                所以对外一律说「按当时快照价成交」，<b>不承诺与真实成交逐笔一致</b>。
              </Note>
            </div>
          </div>
        </Card>
      )}

      {/* ② 成交价是怎么来的 */}
      <div>
        <SectionTitle title="② 成交价是怎么来的" sub="模拟归模拟，价格必须是当时市场真实发生过的那个价" />
        <Card>
          <CardHead icon={<ShieldCheck className="w-4 h-4" />} title="一笔买单从「想做」到「成交」的四步"
            sub="AI 只说想买什么；价格、能不能成交、记多少账，全部由规则与账本决定" />
          <div className="p-4 flex flex-col gap-3">
            {[
              ['1', '取快照', 'AI 提出买卖意图的那一刻，系统取一份行情快照（含数据源时间戳、最新价、盘口），冻结下来作为依据。'],
              ['2', '过规则', '确定性风控逐条校验：单票占比、涨跌停、T+1、整手、可用资金、每日下单上限。任一条不过就整笔放弃。'],
              ['3', '按快照撮合', '限价单只在快照价不劣于限价时成交；市价单按快照对手价成交并计入滑点。不能立即成交就挂着排队，收盘未成交自动撤单、资金解冻。'],
              ['4', '记账并留凭证', '成交写入唯一账本，同时保存这份快照作为凭证。事后任何时候都能按编号回放核对。'],
            ].map(([n, t, d]) => (
              <div key={n} className="flex gap-3">
                <span className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-extrabold"
                  style={{ background: 'rgba(176,106,50,.14)', color: '#8A5A18' }}>{n}</span>
                <div>
                  <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{t}</div>
                  <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{d}</div>
                </div>
              </div>
            ))}
            {lastTradeWithSnap ? (
              <div className="mt-1 rounded-xl border p-3.5" style={{ borderColor: 'var(--border)', background: 'rgba(122,111,99,.05)' }}>
                <div className="text-xs font-bold mb-2" style={{ color: 'var(--text)' }}>本账户的真实一例（不是示例数字）</div>
                <RowKV k="标的" v={`${lastTradeWithSnap.name || ''} ${lastTradeWithSnap.code}`} />
                <RowKV k="成交时刻" v={String(lastTradeWithSnap.traded_at).slice(0, 19).replace('T', ' ')} mono />
                <RowKV k="行情快照时间" v={String(lastTradeWithSnap.snapshot_time || '').slice(0, 19).replace('T', ' ')} mono />
                <RowKV k="快照最新价" v={money(lastTradeWithSnap.snapshot_price, cur)} />
                <RowKV k="成交价" v={<b>{money(lastTradeWithSnap.price, cur)}</b>} />
                <RowKV k="凭证编号" v={
                  <button onClick={() => openVoucher(lastTradeWithSnap.snapshot_id)} className="font-mono"
                    style={{ color: '#8A5A18', textDecoration: 'underline', cursor: 'pointer' }}>{lastTradeWithSnap.snapshot_id}</button>
                } mono />
              </div>
            ) : (
              <Note tone="copper">这个账户还没有成交过，所以没有可展示的真实成交价例子。等它成交第一笔再回来看。</Note>
            )}
            <Note tone="warn">
              口径要诚实：模拟撮合按<b>公开行情快照价</b>成交，不可能逐笔复刻真实排队的成交明细。
              所以对外一律说「按当时快照价成交」，<b>不承诺与真实成交逐笔一致</b>。
            </Note>
          </div>
        </Card>
      </div>

      {/* ③ A 股规则六条 */}
      <div>
        <SectionTitle title={`③ ${MARKET_LABEL[market] || market}规则（本版本全部生效）`}
          sub="这些不是参数，是硬约束，策略与 AI 都改不了 · 港美股未做价格带校验的条目已如实标注" />
        <Card>
          <div className="p-4 flex flex-col gap-3">
            {data.constraints.map(c => (
              <div key={c.key} className="flex gap-3">
                <ShieldCheck className="w-4 h-4 mt-0.5 shrink-0" style={{ color: 'var(--blue)' }} />
                <div>
                  <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{c.title}</div>
                  <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{c.text}</div>
                </div>
              </div>
            ))}
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              以上六条在真实系统里由确定性风控服务与模拟账本执行；界面上只展示结果，不提供关闭开关。
            </div>
          </div>
        </Card>
      </div>

      {/* ④ 三态示意 */}
      <div>
        <SectionTitle title="④ 界面状态示意" sub="数据为空 / 正在加载 / 加载失败时，页面长什么样" />
        <Grid cols={3}>
          <Card>
            <CardHead title="空 · 首次使用无持仓" sub="刚开户、还没跑过" />
            <div className="p-4">
              <EmptyState title="账户里还没有持仓" desc="初始资金已到账。打开自动交易并跑完第一轮后，这里会出现持仓明细。" />
            </div>
          </Card>
          <Card>
            <CardHead title="加载中 · 持仓明细" sub="正在从模拟账本读取" />
            <div className="p-4"><LoadingCard title="加载中…" /></div>
          </Card>
          <Card>
            <CardHead title="失败 · 行情源不可用" sub="只影响读取，不影响已有持仓" />
            <div className="p-4">
              <ErrorState title="暂时读不到最新行情" desc="报价服务没响应。账户与持仓不受影响，也不会因此下单。" onRetry={reload} />
            </div>
          </Card>
        </Grid>
        <div className="text-[11px] mt-2" style={{ color: 'var(--text-muted)' }}>
          三态都<b>只读不写</b>：加载中与失败态下，任何自动下单都会被挂起，直到数据确认可用。
        </div>
      </div>

      {/* 开新项目确认框 */}
      {newOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(30,26,22,.45)' }}
          role="dialog" aria-modal="true" aria-label="开新项目确认">
          <Card style={{ maxWidth: 520, width: '100%' }}>
            <CardHead icon={<RefreshCw className="w-4 h-4" />} title="开新项目 · 请确认代价"
              right={<button onClick={() => setNewOpen(false)} className="p-1" aria-label="关闭"><X className="w-4 h-4" /></button>} />
            <div className="p-4 flex flex-col gap-3">
              <Note tone="warn">
                这会<b>关闭当前项目</b>（{data.project?.project_id}），然后开一个全新的项目。代价有三条：
                <div className="mt-1.5">① 旧项目的账本、成交、报告<b>全部封存为只读</b>，不再产生新交易；</div>
                <div>② 新项目<b>从零开始计</b>，收益率曲线、胜率、回撤全部从头累积；</div>
                <div>③ 已锁定的档位金额不可改，新项目要<b>重新选一次档位</b>。</div>
                <div className="mt-1.5">历史记录不会被删除 —— 关掉再开不是「清空重来」，是「上一段模拟到此为止」。</div>
              </Note>
              <div>
                <div className="text-xs font-bold mb-2" style={{ color: 'var(--text-muted)' }}>新项目选一个档位（金额由服务端写死）</div>
                <div className="flex gap-2 flex-wrap">
                  {data.tier_options.map(t => (
                    <button key={t.tier} onClick={() => setNewTier(t.tier)}
                      className="px-3 py-2 rounded-[10px] border text-xs font-semibold"
                      style={{
                        borderColor: newTier === t.tier ? 'var(--blue)' : 'var(--border)',
                        background: newTier === t.tier ? 'rgba(176,106,50,.08)' : 'var(--bg-card)',
                        color: 'var(--text)', cursor: 'pointer',
                      }}>{t.label}</button>
                  ))}
                </div>
              </div>
              {newErr && <Note tone="warn">开新项目失败：{newErr}</Note>}
              <div className="flex gap-2 pt-1">
                <Button kind="warn" disabled={newBusy} onClick={doOpenNew}>{newBusy ? '正在关停并开新…' : '确认：关闭当前项目并开新'}</Button>
                <Button onClick={() => setNewOpen(false)}>取消</Button>
              </div>
            </div>
          </Card>
        </div>
      )}
    </div>
  )
}
