'use client'
/**
 * 新用户向导 · 六步（一期 M1 · M-02）
 *
 * 视觉基线：`plan/ref/原型/06-新用户向导.html`（逐页重构，不抄代码）。
 * 步骤口径：`03-新用户向导与操作参数方案.md` §一 —— 风险告知 → 选档位 → 板块偏好
 *           → 策略选择 → 短线参数 → 确认开启。
 * 开户口径：`POST /api/v1/fin/projects` 只收 `tier`；**本金与整套参数由服务端按档位写死**。
 *           所以第 3~5 步展示的是「该档位将要生效的参数」（只读预览），不是假开关 ——
 *           界面里能改的东西如果后端不采纳，就是在骗人（总控规则 §六-10）。
 *
 * 第 6 步**不出人机协作提示行**（那是二期 H-09）：那行代码挂在 `FEATURE_COPILOT` 后面，
 * 默认 false，不产生任何 DOM。
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Lock, ShieldCheck, Layers, ListChecks, SlidersHorizontal, Rocket, Check, TriangleAlert } from 'lucide-react'
import { FEATURE_COPILOT } from '../../lib/features'
import { Button, Card, CardHead, Chip, KV, Note, finFetch, money, pct } from '../_ui'

type Strategy = { key: string; name: string; params: Record<string, number>; version: string }
type Tier = {
  tier: string
  label: string
  initial_capital: number
  max_positions: number
  max_positions_hard: number
  max_position_pct: number
  min_order_amount: number
  max_price: number
  stop_loss_pct: number
  take_profit_pct: number
  hold_days_max: number
  daily_max_new: number
  daily_max_orders: number
  account_drawdown_halt_pct: number
  daily_loss_halt_pct: number
  liquidity_min_amount: number
  liquidity_max_participation: number | null
  board_flags: Record<string, boolean>
  sector_prefs: string[]
  strategies: Strategy[]
}
type CurrentProject = { project_id: string; tier: string; initial_capital: number; opened_at: string }

const STEPS = ['风险告知', '选档位', '板块偏好', '策略选择', '短线参数', '确认开启']

const TIER_BLURB: Record<string, string> = {
  play: '熟悉流程、看它怎么干活。手续费占比高、可买标的少，不适合期待收益。',
  manage: '认真跑一段、看策略是否靠谱。最平衡：标的覆盖广，费用正好到名义档。',
  operate: '当成一笔真实资产来运营。可做组合与科创板，但要防大单的冲击成本。',
}

const BOARD_LABELS: Array<{ key: string; label: string; note: string }> = [
  { key: 'main', label: '沪市 / 深市主板', note: '60xxxx / 00xxxx · 流通性好、波动温和' },
  { key: 'chinext', label: '创业板', note: '300xxx · ±20% 波动；真实账户需 10 万元资产 + 24 个月经验' },
  { key: 'star', label: '科创板', note: '688xxx · ±20%、单笔 200 股起；真实账户需 50 万元资产 + 24 个月经验' },
  { key: 'bse', label: '北交所', note: '流动性差，不适合 1~3 天短线' },
  { key: 'st', label: 'ST / *ST', note: '±5% 涨跌幅、退市风险，短线一律禁碰' },
  { key: 'sub_new', label: '次新股', note: '上市不满 60 个交易日，无历史波动参照' },
]

const STRATEGY_PARAM_TEXT: Record<string, string> = {
  ma_momentum: '快线 5 日 · 慢线 10 日 · 放量 1.5 倍',
  oversold_rebound: '参考 20 日线 · 偏离 −8% · 确认 1 日',
  volume_breakout: '窗口 20 日 · 放量 2.0 倍',
  ma_pullback: '5 / 10 / 20 日 · 回踩容差 1%',
  fund_flow: '连续净流入 ≥ 3 日 · 按市值设门槛',
}

export default function SetupWizardPage() {
  const router = useRouter()
  const [step, setStep] = useState(0)
  const [tiers, setTiers] = useState<Tier[]>([])
  const [tierKey, setTierKey] = useState<string | null>(null)
  const [riskOk, setRiskOk] = useState(false)
  const [current, setCurrent] = useState<CurrentProject | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitErr, setSubmitErr] = useState('')
  const [result, setResult] = useState<{ project: CurrentProject; created: boolean } | null>(null)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [t, c] = await Promise.all([
          finFetch<{ tiers: Tier[] }>('/tiers'),
          finFetch<{ project: CurrentProject | null }>('/projects/current').catch(e => {
            if ((e as any)?.status === 401) throw e
            return { project: null }
          }),
        ])
        if (!alive) return
        setTiers(t.tiers)
        setCurrent(c.project)
      } catch (e: any) {
        if (!alive) return
        if (e?.status === 401) { router.push('/login'); return }
        setLoadErr(e?.message || '加载失败')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [router])

  const tier = tiers.find(t => t.tier === tierKey) || null

  async function submit() {
    if (!tier) return
    setSubmitting(true); setSubmitErr('')
    try {
      const r = await finFetch<{ project: CurrentProject; created: boolean }>('/projects', {
        method: 'POST',
        body: JSON.stringify({ tier: tier.tier }),
      })
      setResult({ project: r.project, created: r.created })
    } catch (e: any) {
      if (e?.status === 401) { router.push('/login'); return }
      setSubmitErr(e?.message || '开户失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 加载 / 失败态 ───────────────────────────────────────────────────
  if (loading) {
    return <div className="max-w-3xl text-sm" style={{ color: 'var(--text-muted)' }}>正在加载档位模板…</div>
  }
  if (loadErr) {
    return (
      <div className="max-w-3xl flex flex-col gap-3">
        <Note tone="warn">档位模板加载失败：{loadErr}</Note>
        <div><Button onClick={() => location.reload()}>重试</Button></div>
      </div>
    )
  }

  // ── 开户成功态 ──────────────────────────────────────────────────────
  if (result) {
    return (
      <div className="max-w-3xl">
        <Card>
          <CardHead title="项目已开启" icon={<Check className="w-4 h-4" />} />
          <div className="p-5 flex flex-col gap-3">
            <Note tone="ok">
              项目 <b>{result.project.project_id}</b> 已创建并处于 <b>进行中</b>
              {result.created ? '（本次新建）' : '（你已有进行中的项目，接口没有新建第二个）'}。
              这是模拟盘，不接实盘、不配交易凭证。
            </Note>
            <KV k="档位" v={tier ? tier.label : result.project.tier} />
            <KV k="初始资金" v={money(result.project.initial_capital)} />
            <KV k="状态" v="进行中（active）" />
            <div className="flex gap-2 pt-1">
              <Button kind="pri" onClick={() => router.push('/finance/overview')}>去总览</Button>
              <Button onClick={() => router.push('/finance/account')}>看我的账户</Button>
            </div>
          </div>
        </Card>
      </div>
    )
  }

  const canNext = step === 0 ? riskOk : step === 1 ? !!tier : true

  return (
    <div className="flex flex-col xl:flex-row gap-5 items-start">
      {/* 主列 */}
      <div className="flex-1 min-w-0 w-full">
        {/* 步骤条 */}
        <div className="flex items-center gap-1 flex-wrap mb-4">
          {STEPS.map((s, i) => (
            <span key={s} className="flex items-center gap-1">
              <button onClick={() => i <= step && setStep(i)} disabled={i > step}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold"
                style={{
                  borderColor: i === step ? 'var(--blue)' : 'transparent',
                  background: i === step ? 'rgba(176,106,50,.07)' : 'transparent',
                  color: i === step ? '#8A5A18' : i < step ? 'var(--green)' : 'var(--text-muted)',
                  cursor: i <= step ? 'pointer' : 'default',
                }}>
                <b className="w-5 h-5 rounded-full flex items-center justify-center text-[11px]"
                  style={{
                    background: i === step ? 'var(--blue)' : i < step ? 'rgba(63,107,64,.18)' : 'rgba(122,111,99,.16)',
                    color: i === step ? '#fff' : i < step ? 'var(--green)' : 'var(--text-muted)',
                  }}>{i + 1}</b>
                {s}
              </button>
              {i < STEPS.length - 1 && <span className="w-3 h-px" style={{ background: 'var(--border)' }} />}
            </span>
          ))}
        </div>

        {/* 已有项目警告 */}
        {current && step > 0 && (
          <div className="mb-4">
            <Note tone="warn">
              当前已有项目在跑：<b>{tiers.find(t => t.tier === current.tier)?.label || current.tier} · {money(current.initial_capital)}</b>。
              再次开户不会新建第二个进行中的项目（「一个账户同时只有一个进行中项目」由库层唯一索引保证）——
              重复提交会返回现有这一份。换档位要走「关停旧项目、开新项目」（M-16）。
            </Note>
          </div>
        )}

        {/* 第 1 步 · 风险告知 */}
        {step === 0 && (
          <Card>
            <CardHead title="第 1 步 · 先读这三条" sub="读完再往下走，一共不到一分钟" icon={<ShieldCheck className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-3">
              {[
                ['这是模拟盘，不接实盘', '全部是虚拟资金，不绑券商、不配交易凭证，系统里没有任何开关能切到实盘。'],
                ['模拟收益不等于实盘收益', '成交按「委托当时那一刻的行情快照价」撮合，无法逐笔复刻真实排队与成交明细。'],
                ['随时能停，刹车在你手上', '一个开关暂停全部自动交易；已有持仓按规则处理，不受影响。'],
              ].map(([t, d]) => (
                <div key={t} className="flex gap-3">
                  <Check className="w-4 h-4 mt-0.5 shrink-0" style={{ color: 'var(--green)' }} />
                  <div>
                    <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{t}</div>
                    <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{d}</div>
                  </div>
                </div>
              ))}
              <label className="flex items-center gap-2.5 mt-1 pt-3 text-sm font-semibold cursor-pointer"
                style={{ borderTop: '1px solid var(--border)', color: 'var(--text)' }}>
                <input type="checkbox" checked={riskOk} onChange={e => setRiskOk(e.target.checked)} className="w-4 h-4" />
                我已阅读，下一步
              </label>
            </div>
          </Card>
        )}

        {/* 第 2 步 · 选档位 */}
        {step === 1 && (
          <Card>
            <CardHead title="第 2 步 · 选一个档位" sub="三选一 · 金额固定，不可修改" icon={<Layers className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-4">
              <div className="grid gap-3 md:grid-cols-3">
                {tiers.map(t => {
                  const on = tierKey === t.tier
                  return (
                    <button key={t.tier} onClick={() => setTierKey(t.tier)}
                      className="text-left rounded-2xl border p-4 transition-colors relative"
                      style={{
                        borderColor: on ? 'var(--blue)' : 'var(--border)',
                        background: on ? 'rgba(176,106,50,.06)' : '#fff',
                        boxShadow: on ? '0 3px 12px rgba(176,106,50,.13)' : 'none',
                      }}>
                      {on && <Check className="w-4 h-4 absolute top-3 right-3" style={{ color: 'var(--blue)' }} />}
                      <div className="text-xs font-bold" style={{ color: '#8A5A18' }}>{t.label}</div>
                      <div className="text-2xl font-extrabold mt-1 mb-1" style={{ color: 'var(--text)' }}>{money(t.initial_capital)}</div>
                      <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{TIER_BLURB[t.tier]}</div>
                      <div className="flex flex-wrap gap-1.5 mt-2.5">
                        <Chip tone="copper">{t.max_positions} 只持仓</Chip>
                        <Chip tone="slate">{t.strategies.length} 个策略</Chip>
                      </div>
                      <div className="text-[11px] mt-2.5 pt-2 leading-relaxed" style={{ color: 'var(--text-muted)', borderTop: '1px dashed rgba(216,205,186,.9)' }}>
                        单笔 ≥ {money(t.min_order_amount)} · 可买股价 ≤ {money(t.max_price)}
                        <br />止盈 {pct(t.take_profit_pct)} / 止损 {pct(t.stop_loss_pct)}
                      </div>
                    </button>
                  )
                })}
              </div>

              {tier && (
                <div className="flex items-center gap-2 text-sm rounded-[10px] border border-dashed px-3.5 py-2.5"
                  style={{ borderColor: 'var(--border)', background: 'rgba(122,111,99,.06)', color: 'var(--text-muted)' }}>
                  <Lock className="w-4 h-4" />
                  档位金额 <b className="mx-1" style={{ color: 'var(--text)' }}>{money(tier.initial_capital)}</b>
                  （只读）—— 档位金额不提供自定义，它决定的不只是本金，还有持仓只数、单笔最小金额与策略范围。
                </div>
              )}

              <Note tone="copper">
                初始资金是所有收益率的分母，事后改动会让历史成绩失真，所以档位金额<b>不可修改</b>。
                想换档位，唯一的路是<b>开新项目</b>（需先关停当前项目）。
              </Note>

              {tier && (
                <div className="flex flex-wrap gap-2">
                  <Button kind="pri" onClick={() => setStep(5)}>一键照抄推荐参数，直接去确认</Button>
                  <Button onClick={() => setStep(2)}>逐项配置</Button>
                </div>
              )}
            </div>
          </Card>
        )}

        {/* 第 3 步 · 板块偏好 */}
        {step === 2 && tier && (
          <Card>
            <CardHead title="第 3 步 · 股票板块偏好" sub="分三层：先划边界，再选偏好，最后定流动性门槛" icon={<Layers className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-4">
              <div>
                <div className="text-xs font-bold mb-2" style={{ color: 'var(--text-muted)' }}>① 能力边界 · 能不能碰（按档位，只读）</div>
                <div className="flex flex-col">
                  {BOARD_LABELS.map(b => {
                    const allowed = !!tier.board_flags[b.key]
                    return (
                      <div key={b.key} className="flex items-start gap-3 py-2.5" style={{ borderBottom: '1px solid rgba(216,205,186,.55)' }}>
                        <Chip tone={allowed ? 'ok' : 'slate'}>{allowed ? '允许' : '禁止'}</Chip>
                        <div className="min-w-0">
                          <div className="text-sm font-semibold" style={{ color: allowed ? 'var(--text)' : 'var(--text-muted)' }}>{b.label}</div>
                          <div className="text-[11px] mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{b.note}</div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              <div>
                <div className="text-xs font-bold mb-1.5" style={{ color: 'var(--text-muted)' }}>② 选股倾向 · 优先看谁</div>
                <Note tone="copper">
                  本档位默认<b>不限行业</b>（行业基本面偏好对 1~3 天短线作用很小，真正的胜负手是流动性）。
                </Note>
              </div>

              <div>
                <div className="text-xs font-bold mb-1.5" style={{ color: 'var(--text-muted)' }}>③ 流动性门槛 · 真正的胜负手</div>
                <KV k="最小日均成交额" v={`≥ ${money(tier.liquidity_min_amount)}`} />
                <KV k="单只买入上限" v={tier.liquidity_max_participation === null ? '不设（本档单笔金额小）' : `≤ 该股昨日成交额的 ${pct(tier.liquidity_max_participation)}`} />
                <Note tone="copper">
                  「板块偏好 / 策略 / 参数」在一期由<b>档位决定</b>（服务端口径）；
                  开启后可在「自动交易」页调整——风控参数<b>只能收紧、不能放宽</b>。
                </Note>
              </div>
            </div>
          </Card>
        )}

        {/* 第 4 步 · 策略选择 */}
        {step === 3 && tier && (
          <Card>
            <CardHead title="第 4 步 · 量化策略选择" sub={`本档开放 ${tier.strategies.length} 个（持有 1~3 个交易日的短线量化）`} icon={<ListChecks className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-3">
              {tier.strategies.map(s => (
                <div key={s.key} className="rounded-xl border p-3.5" style={{ borderColor: 'var(--border)', background: '#fff' }}>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="text-sm font-bold flex items-center gap-2" style={{ color: 'var(--text)' }}>
                      <Check className="w-4 h-4" style={{ color: 'var(--green)' }} />{s.name}
                    </div>
                    <Chip tone="copper">版本 {s.version}</Chip>
                  </div>
                  <div className="text-xs mt-1.5" style={{ color: 'var(--text-muted)' }}>
                    默认参数：{STRATEGY_PARAM_TEXT[s.key] || Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(' · ')}
                  </div>
                </div>
              ))}
              <Note tone="copper">
                策略越多，越容易在亏损时换策略、最后变成随机交易——所以 ① 档只给 2 个、② 档 4 个、③ 档 5 个。
                本版的启用清单由档位决定（服务端写死）；参数改动留版本号与生效时间。
              </Note>
            </div>
          </Card>
        )}

        {/* 第 5 步 · 短线参数 */}
        {step === 4 && tier && (
          <Card>
            <CardHead title="第 5 步 · 短线操作参数" sub="本次按短线（1~3 天）配置 · 数值由档位决定" icon={<SlidersHorizontal className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  ['买入时点', '每日 14:30 – 14:55（收盘前，全天量价已成型）'],
                  ['卖出时点', '次日 09:35 – 09:45（开盘情绪释放后）；止损不受时点限制，立即市价挂出'],
                  ['委托类型', '限价单为主；止损走市价'],
                  ['加仓', '禁止（短线不做摊平）'],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-xl border p-3" style={{ borderColor: 'var(--border)', background: '#fff' }}>
                    <div className="text-xs font-bold mb-1" style={{ color: 'var(--blue)' }}>{k}</div>
                    <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{v}</div>
                  </div>
                ))}
              </div>
              <div>
                <KV k="单笔止盈" v={pct(tier.take_profit_pct)} />
                <KV k="单笔止损" v={pct(tier.stop_loss_pct)} />
                <KV k="最大持有" v={`${tier.hold_days_max} 个交易日`} />
                <KV k="持仓上限" v={`${tier.max_positions} 只 · 单只 ≤ ${pct(tier.max_position_pct)}`} />
                <KV k="每日最多开新仓" v={`${tier.daily_max_new} 笔（每日最多 ${tier.daily_max_orders} 笔）`} />
                <KV k="单日亏损熔断" v={pct(tier.daily_loss_halt_pct)} />
                <KV k="账户停手线" v={pct(tier.account_drawdown_halt_pct)} />
              </div>
              <Note tone="copper">
                硬禁：加仓/摊平、杠杆融资、持有超过 {tier.hold_days_max} 个交易日、买入 ST 与退市整理期股票。
                止损止盈由代码判定，智能体无权改动；风控参数开启后<b>只能收紧，不能放宽</b>。
              </Note>
            </div>
          </Card>
        )}

        {/* 第 6 步 · 确认开启 */}
        {step === 5 && tier && (
          <Card>
            <CardHead title="第 6 步 · 确认并开启" sub="核一遍，然后开跑" icon={<Rocket className="w-4 h-4" />} />
            <div className="p-4 flex flex-col gap-3">
              {!tierKey && <Note tone="warn">还没有选档位，请先回到第 2 步。</Note>}
              <div>
                <KV k="档位" v={tier.label} />
                <KV k="初始资金" v={`${money(tier.initial_capital)}（固定）`} />
                <KV k="币种 / 市场" v="开户默认开设 A 股（人民币 CNY）子账户" />
                <KV k="持仓上限" v={`${tier.max_positions} 只 · 单只 ≤ ${pct(tier.max_position_pct)}`} />
                <KV k="单笔最小金额" v={money(tier.min_order_amount)} />
                <KV k="可买股价上限" v={`≤ ${money(tier.max_price)}`} />
                <KV k="可选策略" v={`${tier.strategies.length} 个`} />
                <KV k="止盈 / 止损" v={`${pct(tier.take_profit_pct)} / ${pct(tier.stop_loss_pct)}`} />
                <KV k="每日开新仓" v={`${tier.daily_max_new} 笔`} />
                <KV k="账户停手线" v={pct(tier.account_drawdown_halt_pct)} />
                <KV k="结算周期" v={`短线 · 持有 1~${tier.hold_days_max} 个交易日`} />
              </div>

              {/* 二期 H-09 的提示行挂在这个开关后面；一期（默认 false）不产生任何 DOM。 */}
              {FEATURE_COPILOT && (
                <Note tone="ok">
                  运行中途随时可以切到人机协作。
                </Note>
              )}

              {submitErr && <Note tone="warn">开户失败：{submitErr}</Note>}

              <div className="flex flex-wrap gap-2 pt-1">
                <Button onClick={() => setStep(4)}>上一步</Button>
                <Button kind="pri" size="lg" disabled={!tierKey || submitting} onClick={submit}>
                  {submitting ? '正在创建项目…' : '立即开启'}
                </Button>
              </div>
              <div className="text-[11px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                数字最终由模拟账本与规则代码执行；智能体只提买卖意图。本项目只做模拟，不接实盘、不配交易凭证。
              </div>
            </div>
          </Card>
        )}

        {/* 上/下一步 */}
        {step < 5 && (
          <div className="flex items-center gap-2 mt-4 pt-3.5 flex-wrap" style={{ borderTop: '1px solid rgba(216,205,186,.7)' }}>
            <Button onClick={() => setStep(s => Math.max(0, s - 1))} disabled={step === 0}>上一步</Button>
            <div className="flex-1" />
            {tier && step === 1 && <span className="text-xs" style={{ color: 'var(--text-muted)' }}>当前选中：{tier.label}</span>}
            <Button kind="pri" onClick={() => setStep(s => Math.min(5, s + 1))} disabled={!canNext}>下一步</Button>
          </div>
        )}
      </div>

      {/* 右侧常驻参数摘要 */}
      <aside className="w-full xl:w-[300px] xl:shrink-0 xl:sticky xl:top-4">
        <Card>
          <CardHead title="参数摘要" sub="选档位时实时更新" />
          <div className="p-4">
            {tier ? (
              <>
                <KV k="档位" v={tier.label} />
                <KV k="初始资金" v={money(tier.initial_capital)} />
                <KV k="持仓上限" v={`${tier.max_positions} 只`} />
                <KV k="单笔最小" v={money(tier.min_order_amount)} />
                <KV k="可买股价上限" v={`≤ ${money(tier.max_price)}`} />
                <KV k="可选策略" v={`${tier.strategies.length} 个`} />
                <KV k="止盈 / 止损" v={`${pct(tier.take_profit_pct)} / ${pct(tier.stop_loss_pct)}`} />
                <KV k="每日开新仓" v={`${tier.daily_max_new} 笔`} />
                <KV k="账户停手线" v={pct(tier.account_drawdown_halt_pct)} />
                <KV k="币种 / 市场" v="CNY · 默认子账户为 A 股" />
                <div className="text-[11px] mt-3 leading-relaxed flex gap-1.5" style={{ color: 'var(--text-muted)' }}>
                  <TriangleAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span>档位金额<b>不可改</b>；想换档位就开新项目（需先关停当前项目）。</span>
                </div>
              </>
            ) : (
              <div className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>还没选档位。第 2 步选一个档位后，这里会显示将要生效的全部参数。</div>
            )}
          </div>
        </Card>
      </aside>
    </div>
  )
}
