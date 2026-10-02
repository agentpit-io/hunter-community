'use client'
/**
 * 自动交易页 · AI 自动版（一期 M6 · M-04）。
 *
 * 视觉基线：`plan/ref/原型/01-自动交易.html`，但**只做一期的子集**：
 * 运行模式 / 我的选股池 / 我来下单三块由 `FEATURE_COPILOT`（默认 false）控制，
 * **不渲染**（`05 §1.3`：不是灰掉、不是禁用 —— 灰色按钮会骗人）。
 *
 * 三个写操作都真的落库（`fin_param` + `fin_param_change_log`）：
 *   · 总开关   → 关掉后 fin-worker 的 decide 时点不再产生新委托
 *   · 策略切换 → 下一次决策生效
 *   · 风险档位 → 单向棘轮，放宽要二次确认
 */
import { useState } from 'react'
import {
  Zap, Play, Pause, Shield, SlidersHorizontal, ListChecks, Scale, Lock, TriangleAlert,
} from 'lucide-react'
import { Button, Card, CardHead, Chip, Note, pct } from '../_ui'
import { Grid, RowKV, SectionTitle, TimelineItem, EmptyState, ErrorState, LoadingCard } from '../_parts'
import { useFinPage, finPost } from '../_data'

type Strategy = { key: string; name: string; version: string; params: Record<string, number>; active?: boolean }
type TierPreset = { label: string; values: Record<string, number>; fields: Record<string, string> }
type AutoTrade = {
  project: { project_id: string; tier: string; status: string } | null
  switch: { auto_enabled: boolean; updated_at: string | null }
  strategies: Strategy[]
  active_strategy: Strategy | null
  risk: {
    tier: string | null; label: string | null
    matches_preset: string | null
    current: Record<string, number | null>
    presets: Record<string, TierPreset>
  }
  today: { date: string; actions: any[] }
  recent_actions: any[]
  schedule: { key: string; at: string; title: string; plain: string }[]
  constraints: { key: string; title: string; text: string; source: string | null }[]
  param_change_log: { id: number; actor: string; field: string; old_value: any; new_value: any; changed_at: string }[]
}

const TIER_ORDER = ['conservative', 'steady', 'aggressive']

const STRATEGY_PARAM_TEXT: Record<string, string> = {
  ma_momentum: '快线 5 日 · 慢线 10 日 · 放量 1.5 倍',
  oversold_rebound: '参考 20 日线 · 偏离 −8% · 确认 1 日',
  volume_breakout: '窗口 20 日 · 放量 2.0 倍',
  ma_pullback: '5 / 10 / 20 日 · 回踩容差 1%',
  fund_flow: '连续净流入 ≥ 3 日 · 按市值设门槛',
}

function Switch({ on, busy, onToggle }: { on: boolean; busy: boolean; onToggle: () => void }) {
  return (
    <button onClick={onToggle} disabled={busy} aria-pressed={on} aria-label="自动交易总开关"
      className="relative inline-flex items-center rounded-full transition-colors"
      style={{ width: 52, height: 28, background: on ? 'var(--green)' : 'rgba(122,111,99,.35)', cursor: busy ? 'wait' : 'pointer', opacity: busy ? 0.6 : 1 }}>
      <span className="absolute rounded-full bg-white transition-all" style={{ width: 22, height: 22, left: on ? 27 : 3, top: 3, boxShadow: '0 1px 3px rgba(0,0,0,.25)' }} />
    </button>
  )
}

export default function FinanceAutoTradePage() {
  const { data, loading, error, noProject, reload } = useFinPage<AutoTrade>('/auto-trade')
  const [busy, setBusy] = useState('')
  const [flash, setFlash] = useState('')
  const [confirm, setConfirm] = useState<{ tier: string; loosened: any[] } | null>(null)

  async function act(path: string, body: any, okText: string) {
    setBusy(path); setFlash('')
    try {
      const r = await finPost<any>(path, body)
      setFlash(typeof r === 'object' && r.changed === false ? '没有变化（当前已是这个设置）' : okText)
      setConfirm(null)
      reload()
    } catch (e: any) {
      if (e?.status === 409 && e?.detail?.loosened) {
        setConfirm({ tier: body.tier, loosened: e.detail.loosened })
      } else if (e?.status === 401) {
        location.href = '/login'
      } else {
        setFlash(`失败：${e?.message || '请求没成功'}`)
      }
    } finally {
      setBusy('')
    }
  }

  if (loading) return <Card><CardHead title="自动交易" sub="正在从模拟账本读取" /><div className="p-4"><LoadingCard title="加载中…" /></div></Card>
  if (noProject) return (
    <div className="max-w-3xl"><Card><CardHead title="自动交易" sub="还没有进行中的项目" />
      <div className="p-5"><EmptyState title="先在设置向导里开一个项目" desc="档位决定本金与整套参数；开完之后这里才会出现开关、策略与风险档位。" /></div>
    </Card></div>
  )
  if (error || !data) return (
    <div className="max-w-3xl"><Card><CardHead title="自动交易" sub="读取失败" />
      <div className="p-5"><ErrorState title="读不到自动交易状态" onRetry={reload}
        desc={`${error || '接口没有返回内容'}。这一页的开关状态、策略与风险档位都来自后端，取不到就不显示假状态。`} /></div>
    </Card></div>
  )

  const sw = data.switch.auto_enabled
  const activeKey = data.active_strategy?.key
  const risk = data.risk

  return (
    <div className="flex flex-col gap-6 max-w-6xl">
      {flash && <Note tone="copper">{flash}</Note>}

      {/* 大开关 */}
      <Card>
        <CardHead icon={<Zap className="w-4 h-4" />} title="自动交易总开关"
          sub="整个产品只有这一个开关：开 = 按交易日自动运行，关 = 全部停下"
          right={<Chip tone={sw ? 'ok' : 'amber'}>{sw ? '运行中' : '已暂停'}</Chip>} />
        <div className="p-4 flex flex-col gap-3">
          <div className="flex items-center gap-4 flex-wrap">
            <Switch on={sw} busy={busy !== ''} onToggle={() => act('/auto-trade/switch', { enabled: !sw }, sw ? '已暂停：不再产生新委托' : '已恢复：按时刻表继续运行')} />
            <div className="min-w-0 text-sm" style={{ color: 'var(--text)' }}>
              {sw
                ? <>正在<b>按时刻表自动运行</b> · 每个交易日按 09:15 / 09:30 / 11:30 / 13:00 / 14:55 / 15:30 六个时点跑</>
                : <>已暂停 · <b>不会再下新单</b>，已有持仓保持不变</>}
            </div>
          </div>
          <Note tone="copper">
            点开关只会<b>暂停 / 恢复后续操作</b>，不会卖掉已有持仓。关掉之后，自动交易的任务在出买卖意图之前就会停下 ——
            连委托都不会产生（不是「下了单再拒掉」）。
          </Note>
        </div>
      </Card>

      {/* 选择策略 */}
      <div>
        <SectionTitle title="选择策略" sub="它用什么思路替你买卖 · 点一下切换，随时可换" />
        <Card>
          <div className="p-4 flex flex-col gap-3">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {data.strategies.map(s => {
                const on = s.key === activeKey
                return (
                  <button key={s.key} onClick={() => !on && act('/auto-trade/strategy', { key: s.key }, `已切到「${s.name}」，下一次决策生效`)}
                    disabled={busy !== ''}
                    className="text-left rounded-xl border p-3.5 transition-colors"
                    style={{ borderColor: on ? 'var(--blue)' : 'var(--border)', background: on ? 'rgba(176,106,50,.06)' : 'var(--bg-card)', cursor: on ? 'default' : 'pointer' }}>
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span className="text-sm font-bold" style={{ color: 'var(--text)' }}>{s.name}</span>
                      {on ? <Chip tone="copper">正在使用</Chip> : <Chip tone="slate">点击选用</Chip>}
                    </div>
                    <div className="text-xs mt-1.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                      {STRATEGY_PARAM_TEXT[s.key] || Object.entries(s.params || {}).map(([k, v]) => `${k}=${v}`).join(' · ')}
                    </div>
                    <div className="text-[11px] mt-1.5" style={{ color: 'var(--text-muted)' }}>版本 {s.version}</div>
                  </button>
                )
              })}
            </div>
            <Note tone="copper">
              策略切换<b>下一次决策生效</b>：正在跑的那一轮仍用它开始时的那份设置，不会中途被换掉。
              每一次切换都记进参数变更日志（页尾可见）。
            </Note>
          </div>
        </Card>
      </div>

      {/* 风险档位 */}
      <div id="risk-anchor">
        <SectionTitle title="设定风险上限" sub="先设好底线，它再怎么判断也越不过去 · 参数只能收紧，放宽要二次确认" />
        <Grid cols={2}>
          <Card>
            <CardHead icon={<Shield className="w-4 h-4" />} title="风险档位"
              sub="决定单票最多买多少、亏多少自动停下来"
              right={risk.label ? <Chip tone="copper">当前：{risk.label}</Chip> : <Chip tone="slate">尚未单独设定</Chip>} />
            <div className="p-4 flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-3">
                {TIER_ORDER.map(t => {
                  const p = risk.presets[t]
                  const on = risk.tier === t
                  return (
                    <button key={t} onClick={() => act('/auto-trade/risk', { tier: t }, `风险档位已改为「${p.label}」，并记入变更日志`)}
                      disabled={busy !== ''}
                      className="text-left rounded-xl border p-3 transition-colors"
                      style={{ borderColor: on ? 'var(--blue)' : 'var(--border)', background: on ? 'rgba(176,106,50,.06)' : 'var(--bg-card)', cursor: 'pointer' }}>
                      <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{p.label}</div>
                      <div className="text-[11px] mt-1 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                        单票 ≤ {pct(p.values.max_position_pct)}
                        <br />日亏损 {pct(p.values.daily_loss_halt_pct)} 停
                        <br />回撤 {pct(p.values.account_drawdown_halt_pct)} 停手
                      </div>
                    </button>
                  )
                })}
              </div>

              {confirm && (
                <Note tone="warn">
                  <div className="font-bold mb-1 flex items-center gap-1.5"><TriangleAlert className="w-3.5 h-3.5" />这次调整会<b>放宽</b>风控上限，需要你明确确认</div>
                  <div className="mb-2">
                    {confirm.loosened.map((x: any) => (
                      <div key={x.field}>
                        · {x.label}：{x.old === null ? '—' : pct(x.old)} → <b>{pct(x.new)}</b>
                      </div>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" kind="warn" onClick={() => act('/auto-trade/risk', { tier: confirm.tier, confirm: true }, '已按你的确认放宽，并记入变更日志')}>确认放宽</Button>
                    <Button size="sm" onClick={() => setConfirm(null)}>取消</Button>
                  </div>
                </Note>
              )}
              <Note tone="copper">
                收紧随时可以，放宽必须你点头并留痕（`03` §七）。档位金额与本金不在这里 —— 它们由开户档位写死，不可修改。
              </Note>
            </div>
          </Card>

          <Card>
            <CardHead icon={<SlidersHorizontal className="w-4 h-4" />} title="当前生效参数" sub="取自账本，不是界面上的副本" />
            <div className="p-4">
              <RowKV k="单票最高占比" v={pct(risk.current.max_position_pct)} />
              <RowKV k="单日亏损熔断线" v={pct(risk.current.daily_loss_halt_pct)} />
              <RowKV k="账户回撤熔断线" v={pct(risk.current.account_drawdown_halt_pct)} />
              <div className="mt-3">
                <Note tone="copper">这三条是<b>确定性规则</b>，由账本服务在下单前逐条校验；策略与 AI 都改不了它们。</Note>
              </div>
            </div>
          </Card>
        </Grid>
      </div>

      {/* 今天做了什么 */}
      <div>
        <SectionTitle title="它今天做了什么" sub={`${data.today.date} · 每一笔都能看懂：买了什么、按什么价成交、是怎么决定的`}
          right={<Chip tone="copper">{data.today.actions.length} 条动作</Chip>} />
        <Card>
          <div className="px-4 py-1">
            {data.today.actions.length === 0 ? (
              <EmptyState title="今天还没有动作"
                desc={`${data.today.date} 还没有产生任何委托。可能是非交易日（周末 / 节假日），或者总开关关着，也可能它今天判断「不交易」。`
                  + (data.recent_actions?.[0]?.created_at ? `最近一次动作在 ${String(data.recent_actions[0].created_at).slice(0, 10)}，可以到「我的账户」页看成交明细。` : '')} />
            ) : data.today.actions.map((o: any) => (
              <TimelineItem key={o.order_id + (o.trade_id || '')}
                time={o.created_at?.slice(11, 16) || '—'}
                title={<>
                  {o.outcome === 'declined' ? '放弃' : o.outcome === 'open' ? '挂着' : ''}
                  {o.side === 'buy' ? '买入' : '卖出'} {o.name || o.code}{' '}
                  <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                    {o.filled_qty || o.qty} 股{o.price !== null ? ` · ${'¥' + Number(o.price).toLocaleString('zh-CN')}` : ''}
                  </span>
                </>}
                chip={<Chip tone={o.outcome === 'traded' ? 'ok' : o.outcome === 'declined' ? 'amber' : 'slate'}>{o.status_text}</Chip>}
                reason={o.outcome === 'declined'
                  ? `被风控拦下：${o.decline_reason || '未记录原因'}。这是保护，不是失误。`
                  : (o.snapshot_id ? `成交价依据：行情快照 ${o.snapshot_id}（可在「我的账户」页点开核对）` : undefined)} />
            ))}
          </div>
        </Card>
      </div>

      {/* 时刻表 */}
      <div>
        <SectionTitle title="它每天什么时候自动运行" sub="按沪深交易日历走 · 周末与节假日自动跳过" />
        <Card>
          <div className="p-4">
            {data.schedule.map(p => (
              <div key={p.key} className="flex gap-3 py-2.5" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
                <div className="text-xs font-bold pt-0.5 shrink-0" style={{ color: '#8A5A18', width: 46, fontVariantNumeric: 'tabular-nums' }}>{p.at}</div>
                <div>
                  <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{p.title}</div>
                  <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>{p.plain}</div>
                </div>
              </div>
            ))}
            <div className="mt-3">
              <Note tone="copper">同一时刻只跑一轮，<b>不会重复下单</b>；中断后从断点继续，过期信号直接作废、不补单。</Note>
            </div>
          </div>
        </Card>
      </div>

      {/* A 股硬约束 */}
      <div>
        <SectionTitle title="A 股硬约束" sub="它再想买，也越不过这几条 · 本版本只做 A 股" />
        <Card>
          <div className="p-4 flex flex-col gap-3">
            {data.constraints.map(c => (
              <div key={c.key} className="flex gap-3">
                <span className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center" style={{ background: 'rgba(176,106,50,.13)', color: '#8A5A18' }}>
                  <ListChecks className="w-3.5 h-3.5" />
                </span>
                <div>
                  <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{c.title}</div>
                  <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                    {c.text}{c.source && <span className="ml-1 opacity-70">（费率来自 {c.source}）</span>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* 我还能怎么控制 */}
      <div>
        <SectionTitle title="我还能怎么控制" sub="不想它乱来？这里每一招都真的能生效 —— 不能生效的按钮不画" />
        <Grid cols={3}>
          <Card>
            <div className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center gap-2 text-sm font-bold" style={{ color: 'var(--text)' }}>
                {sw ? <Pause className="w-4 h-4" style={{ color: 'var(--blue)' }} /> : <Play className="w-4 h-4" style={{ color: 'var(--green)' }} />}
                {sw ? '停用自动交易' : '恢复自动交易'}
              </div>
              <div className="text-xs leading-relaxed flex-1" style={{ color: 'var(--text-muted)' }}>
                关掉总开关，它就不再自动运行，直到你重新打开。历史记录与报告都会保留。
              </div>
              <div><Button kind={sw ? 'warn' : 'pri'} size="sm"
                onClick={() => act('/auto-trade/switch', { enabled: !sw }, sw ? '已停用' : '已恢复')}>
                {sw ? '停用' : '恢复'}
              </Button></div>
            </div>
          </Card>
          <Card>
            <div className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center gap-2 text-sm font-bold" style={{ color: 'var(--text)' }}>
                <Scale className="w-4 h-4" style={{ color: 'var(--blue)' }} />收紧风险上限
              </div>
              <div className="text-xs leading-relaxed flex-1" style={{ color: 'var(--text-muted)' }}>
                把单票占比、亏损熔断线调紧，随时可以，不用确认。想放宽才需要二次确认。
              </div>
              <div><Button size="sm" onClick={() => document.getElementById('risk-anchor')?.scrollIntoView({ behavior: 'smooth' })}>去看风险档位</Button></div>
            </div>
          </Card>
          <Card>
            <div className="p-4 flex flex-col gap-3 h-full">
              <div className="flex items-center gap-2 text-sm font-bold" style={{ color: 'var(--text)' }}>
                <Lock className="w-4 h-4" style={{ color: 'var(--blue)' }} />换本金 / 换档位
              </div>
              <div className="text-xs leading-relaxed flex-1" style={{ color: 'var(--text-muted)' }}>
                档位金额在开户时就锁死了。要换只能「开新项目」—— 旧账本封存、只读、可回看。
              </div>
              <div><Button size="sm" onClick={() => { location.href = '/finance/account' }}>去我的账户</Button></div>
            </div>
          </Card>
        </Grid>
        <div className="text-[11px] mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          一期只有「AI 自主」这一种运行方式，所以控制手段就是上面这些 —— 全部已经生效，没有一个是画上去的。
          二期的人机协作相关入口排在后面，这里一个字都不画：一个不能兑现的入口比没有入口更伤信任。
        </div>
      </div>

      {/* 参数变更日志 */}
      <div>
        <SectionTitle title="参数变更日志" sub="谁、什么时候、把哪一条从多少改成了多少 · 只追加，不可修改" />
        <Card>
          <div className="p-4">
            {data.param_change_log.length === 0 ? (
              <EmptyState title="还没有任何参数改动" desc="开户时写下的那套参数至今没被改过。改总开关、切策略、调风险档位都会在这里留一行。" />
            ) : (
              <div className="flex flex-col">
                {data.param_change_log.map(l => (
                  <div key={l.id} className="py-2 text-xs flex items-center gap-2 flex-wrap" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
                    <span className="font-mono" style={{ color: 'var(--text-muted)' }}>{String(l.changed_at || '').slice(0, 19).replace('T', ' ')}</span>
                    <Chip tone="slate">{l.field}</Chip>
                    <span style={{ color: 'var(--text-muted)' }}>{JSON.stringify(l.old_value)} → <b style={{ color: 'var(--text)' }}>{JSON.stringify(l.new_value)}</b></span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}
