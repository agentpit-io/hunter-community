'use client'
/**
 * 成长与复盘页（第四段 `R9` · `plan/R9.md`）。
 *
 * 视觉基线：`plan/ref/_gen/page-04-growth.mjs` 的六块结构 + `page-06-iteration.mjs` 的经验表列。
 *
 * 本页**不放任何占位数字**（`growth/page.tsx` 那 12 行自述里的话，改完之后仍然成立）：
 *   · ④ 经验库 · ⑤ 四条底线 · ⑥ 提案与验证 —— 本轮实体，数据全部来自后端；
 *   · ① 本期自我总结 · ② 它有没有在变强 · ③ 迭代轨迹 —— 本轮**未做界面**，
 *     在页面内如实标「本版未做」+ 为什么（**不返回整页 ComingSoon**）。
 *
 * 三条硬口径（`plan/R9.md` §二 / §三）：
 *   1. **状态只有三种**（待验证 / 已确认 / 已推翻）—— 取自后端枚举，前端不自己拼；
 *      「需重验」是**角标不是状态值**；
 *   2. **筛选值全部来自后端**（`GET /v1/fin/memory/filters`），前端不写死任何枚举；
 *   3. ⑥ 块**只读 + 两个人工动作**：生效与回滚都走同一个服务端闸门，
 *      **界面上没有任何「自动生效」开关**（`FIN_AUTO_APPLY` 恒 0）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  BookOpen, ShieldCheck, FlaskConical, History, ClipboardList, Plus, Activity, GitBranch,
} from 'lucide-react'
import { Card, CardHead, Chip, Note, KV, Button, finFetch } from '../_ui'
import { Grid, SectionTitle, Tbl, EmptyState, ErrorState, LoadingCard, Meter } from '../_parts'

// ── 类型（形状照后端返回体；**不在这里定义任何枚举值**）────────────────────

type Project = {
  project_id: string; tier: string; status: string; market_scope: string
  currency: string; opened_at: string; markets?: any[]
}
type Evidence = { evidence_kind: string; ref_id: string }
type Exp = {
  experience_id: string; kind: string; status: string; market: string | null
  statement: string; applicability: string | null; invalidation_condition: string | null
  method: string | null; sample_size: number | null; confidence: number | null
  as_of: string | null; valid_until: string | null; source: string
  superseded_by: string | null; polarity: string | null; memory_layer: string | null
  symbols: string[] | null; regime_tags: string[] | null
  evidence_count: number; evidence: Evidence[]; needs_recheck: boolean
}
type Opt = { value: string; label: string; count: number }
type Filters = {
  kinds: Opt[]; statuses: Opt[]; markets: Opt[]; regimes: Opt[]
  polarities: Opt[]; memory_layers: Opt[]; symbols: Opt[]
}
type Runtime = {
  memory_enabled: boolean; evolution_mode: string; evolution_mode_requested: string
  degraded_reason: string | null; auto_apply: boolean; live_order_enabled: boolean
}
type Plan = {
  metric: string; window_days: number; min_comparable_sample: number
  pass_line: number | null; fail_line: number | null
  early_stop_condition: any; rollback_line: any; cost_model: string; slippage_model: string
  data_source_version: string; plan_hash: string; frozen_at: string
}
type DiffItem = { field: string; old: any; new: any }
type Proposal = {
  proposal_id: string; project_id: string; status: string; target: string; direction: string
  param_diff: DiffItem[]; evidence_refs: string[]; evidence_snapshot_id: string | null
  regime_tags: string[]; rule_version: string | null; rationale: string
  created_by: string; created_at: string
  base_config_hash: string; candidate_config_hash: string
  plan: Plan | null; reinject_pending: boolean
}
type ArmMetrics = {
  points: number; trades: number; final_total_assets: number | null
  portfolio_net_return: number | null; max_drawdown: number | null
  turnover: number | null; transaction_cost: number | null
}
type Metrics = {
  arms: { incumbent: ArmMetrics; candidate: ArmMetrics }
  delta_candidate_minus_incumbent: number | null
  comparable_samples: number
}
type Ev = {
  event_id: string; proposal_id: string; kind: string; payload: any
  actor: string; created_at: string
}

// ── 小工具（**不写业务数字**：算不出就 `—`）────────────────────────────────

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return String(iso).replace('T', ' ').slice(0, 16)
}
function pctOrDash(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const s = (v * 100).toFixed((v * 100) % 1 === 0 ? 0 : 2)
  return (v > 0 ? '+' : '') + s + '%'
}
function numOrDash(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toFixed(digits)
}
/** 阿拉伯数字判据 —— 与服务端 `memory.statement_has_numbers` **同口径**（半角 + 全角）。 */
const ARABIC = /[0-9０-９]/
const DIGIT_MSG = '结论里不要写数字，数字请通过「证据引用」挂上去'

/** 生效模式的中文名（值来自后端，前端只做展示映射）。 */
const MODE_TEXT: Record<string, string> = {
  off: '关闭（不复盘、不提案）',
  observe: '观察（拉行情 · 复盘 · 建经验 · 展示提案，没有委托发送能力）',
  paper: '模拟盘（独立模拟账本跑现行臂与候选臂）',
}
const KIND_TONE: Record<string, 'copper' | 'ok' | 'amber' | 'slate' | 'up' | 'down'> = {
  fact: 'copper', hypothesis: 'amber', verified: 'down',
}
const STATUS_TONE: Record<string, 'copper' | 'ok' | 'amber' | 'slate'> = {
  待验证: 'amber', 已确认: 'ok', 已推翻: 'slate',
}
const EVENT_TEXT: Record<string, string> = {
  created: '提案创建（含冻结计划）', validating: '进入验证窗口', passed: '验证通过',
  rejected: '提案被驳回', rejected_by_gate: '被闸门拒绝', failed: '验证不达标',
  inconclusive: '不结论（样本 / 差异不足）', applied: '已应用到模拟盘',
  rolled_back: '已回滚到上一个已验证版本', alert: '观察期告警',
}

export default function FinanceGrowthPage() {
  const router = useRouter()

  const [projects, setProjects] = useState<Project[] | null>(null)
  const [tierLabels, setTierLabels] = useState<Record<string, string>>({})
  const [pid, setPid] = useState<string>('')
  const [exps, setExps] = useState<Exp[] | null>(null)
  const [filters, setFilters] = useState<Filters | null>(null)
  const [rt, setRt] = useState<Runtime | null>(null)
  const [props, setProps] = useState<Proposal[] | null>(null)
  const [events, setEvents] = useState<Ev[] | null>(null)
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [reinject, setReinject] = useState<any[]>([])
  const [pickedId, setPickedId] = useState<string>('')
  const [formOpen, setFormOpen] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // 筛选（项目 → `pid`；其余维度见下）
  const [fMarket, setFMarket] = useState('')
  const [fKind, setFKind] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [fRegime, setFRegime] = useState('')
  const [fPolarity, setFPolarity] = useState('')
  const [fSymbol, setFSymbol] = useState('')
  const [hi, setHi] = useState('')                 // 从 ⑥ 的证据引用跳过来时高亮那条

  const guard = useCallback((e: any) => {
    if (e?.status === 401) { router.push('/login'); return true }
    return false
  }, [router])

  // ── 首次：加载项目列表 + 档位中文名（供「项目」下拉的标签用）
  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [p, t] = await Promise.all([
          finFetch<{ items: Project[] }>('/projects'),
          finFetch<{ tiers: any[] }>('/tiers'),
        ])
        if (!alive) return
        setProjects(p.items || [])
        setTierLabels(Object.fromEntries((t.tiers || []).map((x: any) => [x.tier, x.label])))
        const active = (p.items || []).find(x => x.status === 'active') || (p.items || [])[0]
        setPid(active ? active.project_id : '')
      } catch (e: any) {
        if (!alive || guard(e)) return
        setErr(e?.message || '加载项目失败')
      }
    })()
    return () => { alive = false }
  }, [guard])

  // ── 运行模式横幅（`R4` 的只读接口）—— 与项目无关，加载一次
  useEffect(() => {
    let alive = true
    finFetch<Runtime>('/runtime')
      .then(d => { if (alive) setRt(d) })
      .catch(e => { if (alive && !guard(e)) setErr(e?.message || '读不到运行模式') })
    return () => { alive = false }
  }, [guard])

  // ── 换项目 / 换筛选 → 重新取数
  const load = useCallback(async () => {
    if (!pid) return
    setErr('')
    try {
      const q = new URLSearchParams({ project_id: pid })
      if (fMarket) q.set('market', fMarket)
      if (fKind) q.set('kind', fKind)
      if (fStatus) q.set('status', fStatus)
      const [e, f, pr, ev] = await Promise.all([
        finFetch<{ items: Exp[] }>(`/memory/experiences?${q.toString()}`),
        finFetch<Filters>(`/memory/filters?project_id=${encodeURIComponent(pid)}`),
        finFetch<{ items: Proposal[] }>(`/evolution/proposals?project_id=${encodeURIComponent(pid)}`),
        finFetch<{ items: Ev[] }>(`/evolution/events?project_id=${encodeURIComponent(pid)}`),
      ])
      setExps(e.items || [])
      setFilters(f)
      setProps(pr.items || [])
      setEvents(ev.items || [])
      setPickedId(prev => (pr.items || []).some(x => x.proposal_id === prev)
        ? prev : ((pr.items || [])[0]?.proposal_id || ''))
    } catch (e: any) {
      if (guard(e)) return
      setErr(e?.message || '读取失败')
      setExps(null)
    }
  }, [pid, fMarket, fKind, fStatus, guard])

  useEffect(() => { void load() }, [load])

  const picked = useMemo(
    () => (props || []).find(p => p.proposal_id === pickedId) || null, [props, pickedId])

  // ── 选中的提案 → 两臂对照 + 回灌队列
  useEffect(() => {
    let alive = true
    if (!picked) { setMetrics(null); setReinject([]); return }
    ;(async () => {
      try {
        const m = await finFetch<Metrics>(`/evolution/metrics?proposal_id=${encodeURIComponent(picked.proposal_id)}`)
        if (alive) setMetrics(m)
      } catch (e: any) { if (alive && !guard(e)) setMetrics(null) }
      try {
        const r = await finFetch<{ items: any[] }>(`/evolution/reinject?proposal_id=${encodeURIComponent(picked.proposal_id)}`)
        if (alive) setReinject(r.items || [])
      } catch { if (alive) setReinject([]) }
    })()
    return () => { alive = false }
  }, [picked, guard])

  // ── 客户端二次筛选（后端已按 market / kind / status 过滤；regime / polarity / symbols 是**展示层**收窄）
  const shown = useMemo(() => {
    return (exps || []).filter(e => {
      if (fRegime && !(e.regime_tags || []).includes(fRegime)) return false
      if (fPolarity && e.polarity !== fPolarity) return false
      if (fSymbol && !(e.symbols || []).includes(fSymbol)) return false
      return true
    })
  }, [exps, fRegime, fPolarity, fSymbol])

  const projLabel = (p: Project) =>
    `${tierLabels[p.tier] || p.tier} · ${(filters?.markets || []).find(m => m.value === p.market_scope)?.label || p.market_scope} · ${p.status === 'active' ? '进行中' : '已关闭'}`

  // ════════════════════════════════════════════════════════════════════
  // 渲染
  // ════════════════════════════════════════════════════════════════════

  if (err && !exps) {
    return (
      <div className="max-w-3xl">
        <Card><CardHead title="成长与复盘" sub="读取失败" />
          <div className="p-5"><ErrorState title="读不到成长数据" onRetry={() => { void load() }}
            desc={`${err}。本页每个数字都来自后端，取不到就不显示上一份顶替。`} /></div>
        </Card>
      </div>
    )
  }

  return (
    <div data-r9="growth-page" className="flex flex-col gap-6 max-w-6xl">
      <SectionTitle title="成长与复盘" sub="它学到的东西、它提出的改动、以及这些改动凭什么生效" />

      {/* ① 本期自我总结 */}
      <NotDone
        n="①" title="本期自我总结"
        why="「某一天」的自我总结已经落在每日报告里（报告页那三栏）。「本期」（跨若干交易日）的汇总要先把多份报告聚合成一段叙述 —— 这属于报告聚合，本轮不做这块界面。"
      />
      {/* ② 它有没有在变强 */}
      <NotDone
        n="②" title="它有没有在变强"
        why="要回答它得拿「第一周 vs 本周」两段同期指标并排，而当前账本还没有足够的跨期样本。样本不够就画对比，等于编趋势 —— 所以本轮不做，等账本攒够。"
      />
      {/* ③ 迭代轨迹 */}
      <NotDone
        n="③" title="迭代轨迹"
        why="数据已经在：⑥ 块的「事件链」就是**状态事件表**的原始形态（谁在什么时候提了什么、被谁拒了、有没有生效）。这里缺的是「每一版为什么改、改了什么、验证结果如何」的摘要视图 —— 本轮不做这块界面（数据已在，界面未做）。"
      />

      {/* ④ 经验库 */}
      <div id="experience-block" data-r9="experience-card">
        <SectionTitle title="④ 经验库"
          sub="它学到的东西 · 有证据的才留，被推翻的也不删（推翻 = 追加一条，不覆盖）" />
        <Card>
          <CardHead icon={<BookOpen className="w-4 h-4" />}
            title="经验条目"
            sub="唯一入口 memory.query / memory.append_evidence · 证据与时间权限过滤全在服务端"
            right={
              <Button size="sm" onClick={() => setFormOpen(v => !v)}>
                <Plus className="w-3.5 h-3.5" />{formOpen ? '收起追加表单' : '追加一条经验（人机混合）'}
              </Button>
            } />

          {formOpen && (
            <AddExperience pid={pid} filters={filters}
              onDone={() => void load()} onClose={() => setFormOpen(false)} />
          )}

          {/* 筛选：值全部来自后端 */}
          <div className="px-3 py-2.5 flex items-center gap-2 flex-wrap" style={{ borderBottom: '1px solid var(--border)' }}>
            <FilterSelect label="项目" value={pid} onChange={setPid}
              options={(projects || []).map(p => ({ value: p.project_id, label: projLabel(p) }))} />
            <FilterSelect label="市场" value={fMarket} onChange={setFMarket} options={filters?.markets || []} />
            <FilterSelect label="类型" value={fKind} onChange={setFKind} options={filters?.kinds || []} />
            <FilterSelect label="状态" value={fStatus} onChange={setFStatus} options={filters?.statuses || []} />
            <FilterSelect label="市场状态" value={fRegime} onChange={setFRegime} options={filters?.regimes || []} />
            <FilterSelect label="极性" value={fPolarity} onChange={setFPolarity} options={filters?.polarities || []} />
            <FilterSelect label="标的" value={fSymbol} onChange={setFSymbol} options={filters?.symbols || []} />
          </div>

          <div data-r9="exp-table">
            {exps === null ? (
              <div className="p-4"><LoadingCard title="正在读取经验…" /></div>
            ) : shown.length === 0 ? (
              <div className="p-4">
                <EmptyState title="还没有经验——复核工作流跑过之后才会有"
                  desc={exps.length > 0
                    ? '当前筛选条件下没有匹配的经验。清掉筛选看看全部。'
                    : '经验由收盘后的复核工作流产出（或由你手工追加）；没有就是没有，这里不放示例数据撑场面。'} />
              </div>
            ) : (
              <Tbl
                head={['经验 id', '类型', '来源 / 证据', '适用边界', '失效 / 重验条件', '时间边界', '状态']}
                rows={shown.map(e => [
                  <span className="flex items-start gap-1.5">
                    {e.polarity === 'refute' && (
                      <span title="失败经验（系统的燃料）" style={{ width: 3, alignSelf: 'stretch', borderRadius: 2, background: 'var(--blue)', display: 'inline-block' }} />
                    )}
                    <span className="flex flex-col gap-1 max-w-[22rem]">
                      <span className="font-mono text-[11px]" style={{ color: hi === e.experience_id ? '#8A5A18' : 'var(--text-muted)', fontWeight: hi === e.experience_id ? 700 : 400 }}>
                        {e.experience_id}
                      </span>
                      <span className="text-sm leading-snug" style={{ color: 'var(--text)' }}>{e.statement}</span>
                    </span>
                  </span>,
                  <span className="flex items-center gap-1.5 flex-wrap">
                    <Chip tone={KIND_TONE[e.kind] || 'copper'}>{labelOf(filters?.kinds, e.kind)}</Chip>
                    {e.polarity === 'refute' && <Chip tone="copper">失败经验 · 燃料</Chip>}
                  </span>,
                  <span className="flex flex-col gap-1">
                    <Chip tone={e.source === 'human_mixed' ? 'copper' : 'slate'}>
                      {e.source === 'human_mixed' ? '人机混合' : 'AI 自主'}
                    </Chip>
                    <EvidenceCell e={e} />
                  </span>,
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{e.applicability || '—'}</span>,
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{e.invalidation_condition || '—'}</span>,
                  <span className="text-[11px] flex flex-col" style={{ color: 'var(--text-muted)' }}>
                    <span>起 {fmtTime(e.as_of)}</span>
                    <span>止 {e.valid_until ? fmtTime(e.valid_until) : '不限'}</span>
                  </span>,
                  <span className="flex flex-col gap-1.5 items-start">
                    <Chip tone={STATUS_TONE[e.status] || 'slate'}>{e.status}</Chip>
                    {e.needs_recheck && (
                      <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded"
                        style={{ border: '1px dashed rgba(164,51,43,.5)', color: 'var(--red)' }}>需重验</span>
                    )}
                    {e.confidence !== null && e.confidence !== undefined && (
                      <span className="w-16" title={`置信度 ${e.confidence}`}><Meter pct={e.confidence * 100} /></span>
                    )}
                  </span>,
                ])}
                foot={<span>状态只有三种：待验证（有依据但样本不足）· 已确认（多次验证成立）· 已推翻（被数据否定，停用但保留）。「需重验」是角标不是状态值；「失败经验」标的是 polarity（系统的燃料）。</span>} />
            )}
          </div>
        </Card>
      </div>

      {/* ⑤ 四条底线 */}
      <div>
        <SectionTitle title="⑤ 迭代的四条底线" sub="自我成长不能变成「自我美化」" />
        <Grid cols={2}>
          <Card>
            <CardHead icon={<ShieldCheck className="w-4 h-4" />} title="四条底线" />
            <div className="p-4 flex flex-col gap-3 text-sm" style={{ color: '#5C5348' }}>
              {[
                ['只改规则与参数，不改历史', '任何改版都记录生效时间，历史成绩一律不追溯修改。'],
                ['被推翻的判断照实保留', '错的判断不删、不藏，标成「已推翻」并写上反例。'],
                ['无证据只能标「待验证」', '没有具体日期与成交凭证支撑的猜测，不许写成「已确认」。'],
                ['改版不影响在跑的任务', '旧任务按旧版本结算，不会中途被新参数搞乱。'],
              ].map(([t, d]) => (
                <div key={t} className="flex gap-2.5">
                  <span className="shrink-0 font-bold" style={{ color: 'var(--blue)' }}>·</span>
                  <div><b style={{ color: 'var(--text)' }}>{t}</b>：{d}</div>
                </div>
              ))}
            </div>
          </Card>
          <Card>
            <CardHead icon={<FlaskConical className="w-4 h-4" />} title="这套底线怎么落到代码上" sub="不是口号，每条都有对应的机制" />
            <div className="p-4 flex flex-col">
              <KV k="只改规则不改历史" v="配置按版本追加，历史成交不追溯改写" />
              <KV k="推翻不删" v="没有删除授权，推翻 = 追加一条并指向原文" />
              <KV k="无证据只能待验证" v="写经验必须挂证据引用；结论里不许出现数字" />
              <KV k="改版不影响在跑任务" v="生效只切模拟盘版本，先出完整 diff 与冻结计划" />
            </div>
          </Card>
        </Grid>
      </div>

      {/* ⑥ 提案与验证 */}
      <div>
        <SectionTitle title="⑥ 提案与验证" sub="它提出的改动、冻结的验证口径、两臂对照、以及谁能让它生效" />

        {/* 运行模式横幅 */}
        <div data-r9="runtime-banner">
          <Card style={{ marginBottom: 16 }}>
            <CardHead icon={<Activity className="w-4 h-4" />} title="运行模式" sub="读服务端的只读接口 · 前端不自己判断模式" />
            <div className="p-4 flex flex-col gap-3">
              {!rt ? <LoadingCard title="正在读取运行模式…" /> : (
                <>
                  <Grid cols={4}>
                    <KV k="经验库" v={rt.memory_enabled ? '已启用' : '未启用'} />
                    <KV k="生效模式" v={MODE_TEXT[rt.evolution_mode] || rt.evolution_mode} />
                    <KV k="请求模式" v={MODE_TEXT[rt.evolution_mode_requested] || rt.evolution_mode_requested} />
                    <KV k="自动生效" v={rt.auto_apply ? '开（异常）' : '关（本方案恒为关闭）'} />
                  </Grid>
                  <KV k="实盘订单出口" v={rt.live_order_enabled ? '开（异常）' : '无（本项目不接券商）'} />
                  {rt.degraded_reason && (
                    <Note tone="warn"><b>已降级运行</b>：{rt.degraded_reason}</Note>
                  )}
                </>
              )}
            </div>
          </Card>
        </div>

        {(props || []).length === 0 ? (
          <Card>
            <div className="p-4">
              <EmptyState title="还没有提案"
                desc="提案由复核工作流在攒够经验后提出；这里只展示真实入库的提案，没有就是没有。" />
            </div>
          </Card>
        ) : (
          <div className="flex flex-col gap-4">
            <div data-r9="proposal-list">
              <ProposalPicker items={props || []} pickedId={pickedId} onPick={setPickedId} />
            </div>

            {picked && (
              <>
                <ProposalCard p={picked} onJumpExperience={(id) => {
                  setFMarket(''); setFKind(''); setFStatus(''); setFRegime(''); setFPolarity(''); setFSymbol('')
                  setHi(id)
                  document.getElementById('experience-block')?.scrollIntoView({ behavior: 'smooth' })
                }} />

                <Grid cols={2}>
                  <div data-r9="frozen-plan"><FrozenPlanCard plan={picked.plan} /></div>
                  <div data-r9="arm-compare"><ArmCompare metrics={metrics} plan={picked.plan} /></div>
                </Grid>

                <div data-r9="actions">
                  <ButtonsRow p={picked} events={events || []} reinject={reinject}
                    busy={busy} setBusy={setBusy}
                    onDone={() => void load()} onErr={setErr} />
                </div>
              </>
            )}

            <Card>
              <div data-r9="events">
              <CardHead icon={<History className="w-4 h-4" />} title="事件链"
                sub="按时间倒序 · 权威是事件表，提案上的状态只是它的投影 · 含被闸门拒绝的事件" />
              <div className="p-4">
                {(events || []).length === 0 ? (
                  <EmptyState title="还没有事件" desc="提案创建、验证、生效、回滚、被拒都会在这里留一行。" />
                ) : (
                  <div className="flex flex-col">
                    {(events || []).map(ev => (
                      <div key={ev.event_id} className="py-2.5 flex gap-3" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
                        <span className="shrink-0 text-[11px] font-mono pt-0.5" style={{ color: 'var(--text-muted)', width: 120, fontVariantNumeric: 'tabular-nums' }}>{fmtTime(ev.created_at)}</span>
                        <span className="shrink-0 pt-0.5">
                          <Chip tone={ev.kind === 'rejected_by_gate' || ev.kind === 'failed' ? 'amber' : ev.kind === 'rolled_back' || ev.kind === 'alert' ? 'up' : ev.kind === 'applied' || ev.kind === 'passed' ? 'ok' : 'slate'}>
                            {EVENT_TEXT[ev.kind] || ev.kind}
                          </Chip>
                        </span>
                        <span className="min-w-0 text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                          <span className="font-mono text-[11px]">{ev.proposal_id}</span> · 由 {ev.actor || '—'} 记下
                          {ev.payload?.reason && <div style={{ color: '#7C2A24' }}>原因：{ev.payload.reason}</div>}
                          {!ev.payload?.reason && ev.payload?.stage && <div>阶段：{ev.payload.stage}</div>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              </div>
            </Card>
          </div>
        )}
      </div>

      <Note tone="copper">
        本页所有内容都来自服务端的只读接口：运行模式 · 经验列表 · 筛选取值 · 提案与冻结计划 ·
        状态事件链 · 两臂对照。页面不生成、不改写任何数字；算不出的一律显示 <b>—</b>。
      </Note>
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════
// 子组件
// ════════════════════════════════════════════════════════════════════════

function labelOf(opts: Opt[] | undefined, value: string): string {
  return (opts || []).find(o => o.value === value)?.label || value
}

/** 「本版未做」块 —— **不返回整页 ComingSoon**，在页面内如实说明 + 为什么。 */
function NotDone({ n, title, why }: { n: string; title: string; why: string }) {
  return (
    <div data-r9="notdone">
      <Card>
        <CardHead title={`${n} ${title}`} right={<Chip tone="slate">本版未做</Chip>} />
        <div className="p-4 text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{why}</div>
      </Card>
    </div>
  )
}

/** 筛选下拉 —— **选项全部来自后端**（`value` / `label` 都是）。
 *
 * 用自绘下拉而不是原生 `<select>`：原生下拉的展开态是**操作系统画的**，
 * 浏览器截图里拍不到（`r9_browser_check.mjs` 要交「筛选下拉展开态」这张图）。
 * 自绘的列表就在 DOM 里，截得到、也读得到（`role="listbox"` / `role="option"`）。
 */
function FilterSelect({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  const [open, setOpen] = useState(false)
  const cur = options.find(o => o.value === value)
  const pick = (v: string) => { onChange(v); setOpen(false) }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs relative" data-r9={`filter-${label}`}
      style={{ color: 'var(--text-muted)' }}>
      {label}
      <button onClick={() => setOpen(v => !v)} aria-haspopup="listbox" aria-expanded={open}
        aria-label={label} className="rounded-lg px-2 py-1 text-xs inline-flex items-center gap-1"
        style={{ border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', cursor: 'pointer', minWidth: 84 }}>
        <span className="truncate">{cur ? cur.label : '全部'}</span>
        <span style={{ opacity: .5 }}>▾</span>
      </button>
      {open && (
        <span role="listbox" aria-label={`${label}选项`}
          className="absolute left-0 top-full mt-1 z-30 rounded-lg py-1 flex flex-col min-w-[9rem] max-h-72 overflow-auto"
          style={{ border: '1px solid var(--border)', background: 'var(--bg-card)', boxShadow: '0 8px 24px rgba(0,0,0,.10)' }}>
          <button role="option" aria-selected={value === ''} onClick={() => pick('')}
            className="text-left px-3 py-1.5 text-xs" style={{ background: value === '' ? 'rgba(176,106,50,.12)' : 'transparent', cursor: 'pointer', color: 'var(--text)' }}>全部</button>
          {options.map(o => (
            <button role="option" aria-selected={value === o.value} key={o.value} onClick={() => pick(o.value)}
              className="text-left px-3 py-1.5 text-xs"
              style={{ background: value === o.value ? 'rgba(176,106,50,.12)' : 'transparent', cursor: 'pointer', color: 'var(--text)' }}>
              {o.label}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}

/** 证据单元格：默认一行「证据 N 笔」，点开看引用 id。 */
function EvidenceCell({ e }: { e: Exp }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="flex flex-col gap-1">
      <button onClick={() => setOpen(v => !v)} className="text-[11px] font-semibold text-left"
        style={{ color: '#8A5A18', cursor: 'pointer' }}>
        证据 {e.evidence_count} 笔{e.evidence_count > 0 ? (open ? '（收起）' : '（展开）') : ''}
      </button>
      {open && (
        <span className="flex flex-col gap-0.5">
          {e.evidence.length === 0
            ? <span className="font-mono text-[10px]" style={{ color: 'var(--text-muted)' }}>—</span>
            : e.evidence.map((v, i) => (
              <span key={i} className="font-mono text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {v.evidence_kind} · {v.ref_id}
              </span>
            ))}
        </span>
      )}
    </span>
  )
}

// ── 人机混合写入表单（默认折叠）────────────────────────────────────────────
//
// ⚠️ 默认折叠不是省事：一是**空态页面里不该有一堆输入框**，二是本页要求
// 「空项目全文不出现阿拉伯数字」，常驻的表单说明里很容易带出数字。

function AddExperience({ pid, filters, onDone, onClose }: {
  pid: string; filters: Filters | null; onDone: () => void; onClose: () => void
}) {
  const [kind, setKind] = useState('fact')
  const [statement, setStatement] = useState('')
  const [applicability, setApplicability] = useState('')
  const [invalidation, setInvalidation] = useState('')
  const [method, setMethod] = useState('')
  const [sampleSize, setSampleSize] = useState('')
  const [confidence, setConfidence] = useState('')
  const [validUntil, setValidUntil] = useState('')
  const [symbols, setSymbols] = useState<string[]>([])
  const [picked, setPicked] = useState<Evidence[]>([])
  const [opts, setOpts] = useState<{ reports: any[]; trades: any[] } | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  // 证据候选：本项目的报告 / 成交（**不让用户手打 id**）。表单打开时才取。
  useEffect(() => {
    if (!pid) return
    let alive = true
    ;(async () => {
      try {
        const [r, a] = await Promise.all([
          finFetch<{ items: any[] }>(`/projects/${encodeURIComponent(pid)}/reports?limit=50`),
          finFetch<{ trades: any[] }>(`/account?project_id=${encodeURIComponent(pid)}`),
        ])
        if (alive) setOpts({ reports: r.items || [], trades: a.trades || [] })
      } catch { if (alive) setOpts({ reports: [], trades: [] }) }
    })()
    return () => { alive = false }
  }, [pid])

  function toggle(kindKey: string, refId: string) {
    setPicked(prev => prev.some(x => x.evidence_kind === kindKey && x.ref_id === refId)
      ? prev.filter(x => !(x.evidence_kind === kindKey && x.ref_id === refId))
      : [...prev, { evidence_kind: kindKey, ref_id: refId }])
  }

  async function submit() {
    setMsg('')
    // **前端拦住阿拉伯数字** —— 与服务端 `memory.statement_has_numbers` 同口径，
    // 提示语用同一句。拦在本地是为了不让用户白等一次往返。
    if (ARABIC.test(statement)) { setMsg(DIGIT_MSG); return }
    if (!statement.trim()) { setMsg('结论不能为空'); return }
    if (picked.length === 0) { setMsg('必须挂至少一条证据引用（从本项目的报告 / 成交里选）'); return }
    if (kind === 'verified' && (!method.trim() || !sampleSize.trim())) {
      setMsg('类型选「验证结论」时必须填「怎么验的」与样本笔数'); return
    }
    setBusy(true)
    try {
      await finFetch('/memory/evidence', {
        method: 'POST',
        body: JSON.stringify({
          project_id: pid, kind, statement,
          evidence: picked,
          applicability: applicability || null,
          invalidation_condition: invalidation || null,
          method: method || null,
          sample_size: sampleSize ? Number(sampleSize) : null,
          confidence: confidence ? Number(confidence) : null,
          valid_until: validUntil ? new Date(validUntil + 'T00:00:00').toISOString() : null,
          symbols: symbols.length ? symbols : null,
        }),
      })
      setStatement(''); setApplicability(''); setInvalidation(''); setMethod('')
      setSampleSize(''); setConfidence(''); setValidUntil(''); setSymbols([]); setPicked([])
      setMsg('已追加。它不会编辑或删除已有结论 —— 要推翻只能再追加一条。')
      onDone()
    } catch (e: any) {
      if (e?.status === 401) { setMsg('登录已失效，请重新登录'); return }
      setMsg(e?.message || '提交失败')
    } finally { setBusy(false) }
  }

  const fieldStyle = { border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }
  const cls = 'w-full rounded-lg px-2.5 py-1.5 text-xs mt-1'

  return (
    <div data-r9="add-form" className="p-4 flex flex-col gap-3" style={{ borderBottom: '1px solid var(--border)', background: 'rgba(176,106,50,.03)' }}>
      <div className="flex items-center justify-between">
        <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>追加一条经验（人机混合）</div>
        <button onClick={() => { onClose(); setMsg('') }} className="text-xs"
          style={{ color: 'var(--text-muted)', cursor: 'pointer' }}>收起</button>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>结论（必填 · 不许写数字）
          <input className={cls} style={fieldStyle} value={statement} onChange={e => setStatement(e.target.value)}
            placeholder="一句话结论，例如：缩量整理后追高的回撤概率显著上升" />
        </label>
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>类型
          <select className={cls} style={fieldStyle} value={kind} onChange={e => setKind(e.target.value)}>
            {(filters?.kinds || []).map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </label>
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>适用边界（可留空）
          <input className={cls} style={fieldStyle} value={applicability} onChange={e => setApplicability(e.target.value)}
            placeholder="在什么范围内成立，例如：A 股主板 · 震荡市" />
        </label>
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>失效 / 重验条件（可留空）
          <input className={cls} style={fieldStyle} value={invalidation} onChange={e => setInvalidation(e.target.value)}
            placeholder="什么情况下作废，例如：单边趋势市失效" />
        </label>
        {kind === 'verified' && (
          <>
            <label className="text-xs" style={{ color: 'var(--text-muted)' }}>怎么验的（验证结论必填）
              <input className={cls} style={fieldStyle} value={method} onChange={e => setMethod(e.target.value)} />
            </label>
            <label className="text-xs" style={{ color: 'var(--text-muted)' }}>样本笔数（验证结论必填）
              <input className={cls} style={fieldStyle} inputMode="numeric" value={sampleSize} onChange={e => setSampleSize(e.target.value)} />
            </label>
          </>
        )}
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>置信度（可留空 · 介于零与一之间的系数）
          <input className={cls} style={fieldStyle} inputMode="decimal" value={confidence} onChange={e => setConfidence(e.target.value)} />
        </label>
        <label className="text-xs" style={{ color: 'var(--text-muted)' }}>有效期至（可留空 = 不限）
          <input className={cls} style={fieldStyle} type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} />
        </label>
      </div>

      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>标的（从本项目已有的标的里选，可多选）
        {(filters?.symbols || []).length === 0
          ? <div className="mt-1">本项目还没有出现过标的。</div>
          : (
            <div className="flex gap-1.5 flex-wrap mt-1">
              {(filters?.symbols || []).map(s => {
                const on = symbols.includes(s.value)
                return (
                  <button key={s.value} onClick={() => setSymbols(p => on ? p.filter(x => x !== s.value) : [...p, s.value])}
                    className="text-[11px] px-2 py-0.5 rounded-full font-semibold"
                    style={{
                      border: '1px solid var(--border)',
                      background: on ? 'rgba(176,106,50,.14)' : 'var(--bg-card)',
                      color: on ? '#8A5A18' : 'var(--text-muted)', cursor: 'pointer',
                    }}>{s.label}</button>
                )
              })}
            </div>
          )}
      </div>

      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>证据引用（必填 · 从本项目的报告 / 成交里选）
        {!opts ? <div className="mt-1">正在读取本项目的报告与成交…</div>
          : (opts.reports.length + opts.trades.length === 0)
            ? <div className="mt-1">本项目还没有报告或成交可引用 —— 先有账本，才有证据。</div>
            : (
              <div className="mt-1 flex flex-col gap-1 max-h-40 overflow-auto">
                {opts.reports.map(r => (
                  <label key={r.report_id} className="flex items-center gap-2 text-[11px]">
                    <input type="checkbox" checked={picked.some(x => x.evidence_kind === 'report' && x.ref_id === r.report_id)}
                      onChange={() => toggle('report', r.report_id)} />
                    报告 · {r.trade_date} · <span className="font-mono">{r.report_id}</span>
                  </label>
                ))}
                {opts.trades.map(t => (
                  <label key={t.trade_id} className="flex items-center gap-2 text-[11px]">
                    <input type="checkbox" checked={picked.some(x => x.evidence_kind === 'trade' && x.ref_id === t.trade_id)}
                      onChange={() => toggle('trade', t.trade_id)} />
                    成交 · {t.code || '—'} · {t.side || '—'} · <span className="font-mono">{t.trade_id}</span>
                  </label>
                ))}
              </div>
            )}
      </div>

      {msg && <Note tone={msg === DIGIT_MSG ? 'warn' : 'copper'}>{msg}</Note>}

      <div className="flex items-center gap-2 flex-wrap">
        <Button kind="pri" size="sm" disabled={busy} onClick={() => void submit()}>{busy ? '提交中…' : '追加'}</Button>
        <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          提交后 source 由服务端强制记成「人机混合」；本页不提供删除与「编辑已确认结论」的入口。
        </span>
      </div>
    </div>
  )
}

// ── ⑥ 的子件 ──────────────────────────────────────────────────────────────

function ProposalPicker({ items, pickedId, onPick }: {
  items: Proposal[]; pickedId: string; onPick: (id: string) => void
}) {
  return (
    <Card>
      <CardHead icon={<ClipboardList className="w-4 h-4" />} title="提案列表"
        sub="状态读 proposal.status（只是投影，权威是下面的事件链）" />
      <Tbl
        head={['提案 id', '状态', '目标', '方向', '创建者', '创建时间']}
        rows={items.map(p => [
          <button onClick={() => onPick(p.proposal_id)} className="font-mono text-[11px]"
            style={{ color: p.proposal_id === pickedId ? '#8A5A18' : 'var(--text)', fontWeight: p.proposal_id === pickedId ? 700 : 400, textDecoration: 'underline', cursor: 'pointer' }}>
            {p.proposal_id}
          </button>,
          <span className="flex items-center gap-1.5 flex-wrap">
            <Chip tone={p.status === 'applied' ? 'ok' : p.status === 'passed' ? 'down' : p.status === 'rolled_back' ? 'up' : 'slate'}>{p.status}</Chip>
            {p.reinject_pending && <Chip tone="amber">回灌待完成</Chip>}
          </span>,
          <span className="text-xs">{p.target === 'risk' ? '风控参数' : '策略参数'}</span>,
          <Chip tone={p.direction === 'tighten' ? 'ok' : p.direction === 'loosen' ? 'up' : 'slate'}>{p.direction}</Chip>,
          <span className="text-xs">{p.created_by}</span>,
          <span className="text-[11px]">{fmtTime(p.created_at)}</span>,
        ])} />
    </Card>
  )
}

function ProposalCard({ p, onJumpExperience }: { p: Proposal; onJumpExperience: (id: string) => void }) {
  return (
    <Card>
      <CardHead icon={<GitBranch className="w-4 h-4" />} title={`提案 ${p.proposal_id}`}
        sub={p.rationale || '—'}
        right={<Chip tone={p.target === 'risk' ? 'up' : 'copper'}>{p.target === 'risk' ? '风控' : '策略'}</Chip>} />
      <div className="p-4 flex flex-col gap-3">
        <div>
          <div className="text-xs font-bold mb-1.5" style={{ color: 'var(--text)' }}>完整 param_diff（逐字段 旧 → 新）</div>
          {(p.param_diff || []).length === 0
            ? <div className="text-xs" style={{ color: 'var(--text-muted)' }}>—</div>
            : (
              <Tbl head={['字段', '旧值', '新值']}
                rows={(p.param_diff || []).map(d => [
                  <span className="font-mono text-xs">{d.field}</span>,
                  <span className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>{String(d.old)}</span>,
                  <b className="font-mono text-xs">{String(d.new)}</b>,
                ])} />
            )}
        </div>
        <Grid cols={2}>
          <div>
            <KV k="证据引用" v={
              (p.evidence_refs || []).length === 0 ? '—' : (
                <span className="flex flex-col items-end gap-0.5">
                  {(p.evidence_refs || []).map(id => (
                    <button key={id} onClick={() => onJumpExperience(id)}
                      className="font-mono text-[11px] underline" style={{ color: '#8A5A18', cursor: 'pointer' }}>{id}</button>
                  ))}
                </span>
              )
            } />
            <KV k="证据快照" v={<span className="font-mono text-[11px]">{p.evidence_snapshot_id || '—'}</span>} />
            <KV k="市场状态标签" v={(p.regime_tags || []).join(' · ') || '—'} />
            <KV k="规则版本" v={p.rule_version || '—'} />
          </div>
          <div>
            <KV k="基线配置哈希" v={<span className="font-mono text-[11px]">{(p.base_config_hash || '').slice(0, 16) || '—'}</span>} />
            <KV k="候选配置哈希" v={<span className="font-mono text-[11px]">{(p.candidate_config_hash || '').slice(0, 16) || '—'}</span>} />
            <KV k="创建者 / 时间" v={`${p.created_by} · ${fmtTime(p.created_at)}`} />
            <KV k="当前状态" v={<Chip tone="slate">{p.status}</Chip>} />
          </div>
        </Grid>
      </div>
    </Card>
  )
}

function FrozenPlanCard({ plan }: { plan: Plan | null }) {
  return (
    <Card>
      <CardHead icon={<ShieldCheck className="w-4 h-4" />} title="冻结计划"
        sub="口径冻结：这些数字在提案创建时就冻死了，事后改不了" />
      <div className="p-4">
        {!plan ? <div className="text-xs" style={{ color: 'var(--text-muted)' }}>这条提案没有冻结计划行（异常）。</div> : (
          <>
            <KV k="指标" v={plan.metric} />
            <KV k="窗口（交易日）" v={plan.window_days} />
            <KV k="最小可比样本" v={plan.min_comparable_sample} />
            <KV k="通过线 / 失败线" v={`${pctOrDash(plan.pass_line)} / ${pctOrDash(plan.fail_line)}`} />
            <KV k="提前停止线" v={plan.early_stop_condition?.max_drawdown_pct !== undefined ? pctOrDash(plan.early_stop_condition.max_drawdown_pct) : '—'} />
            <KV k="观察期回滚线（唯一自动）" v={plan.rollback_line?.max_drawdown_pct !== undefined ? pctOrDash(plan.rollback_line.max_drawdown_pct) : '—'} />
            <KV k="成本 / 滑点模型" v={`${plan.cost_model} · ${plan.slippage_model}`} />
            <KV k="plan_hash" v={<span className="font-mono text-[11px]">{plan.plan_hash}</span>} />
            <div className="mt-2"><Note tone="copper">
              改口径 = <b>新建提案</b>；这条计划写库后不可改（数据库触发器拒改 + 事件表哈希链兜底）。
            </Note></div>
          </>
        )}
      </div>
    </Card>
  )
}

function ArmCompare({ metrics, plan }: { metrics: Metrics | null; plan: Plan | null }) {
  if (!metrics) return (
    <Card><CardHead title="候选 vs 基线" sub="影子验证（R7）" />
      <div className="p-4"><LoadingCard title="正在读取两臂对照…" /></div></Card>
  )
  const minSample = plan?.min_comparable_sample ?? null
  const insufficient = minSample !== null && metrics.comparable_samples < minSample
  const A = metrics.arms?.incumbent, B = metrics.arms?.candidate
  return (
    <Card>
      <CardHead title="候选 vs 基线（两臂同条件）"
        sub="同一行情快照 · 同初始现金 / 仓位 · 同费率滑点（R7）"
        right={<Chip tone={insufficient ? 'amber' : 'copper'}>
          可比样本 {metrics.comparable_samples}{minSample !== null ? ` / 需 ${minSample}` : ''}
        </Chip>} />
      <div className="p-4 flex flex-col gap-3">
        <Tbl
          head={['指标', { t: '现行臂', r: true }, { t: '候选臂', r: true }]}
          rows={[
            ['组合净收益', pctOrDash(A?.portfolio_net_return), pctOrDash(B?.portfolio_net_return)],
            ['最大回撤', pctOrDash(A?.max_drawdown), pctOrDash(B?.max_drawdown)],
            ['换手（占初始资金）', numOrDash(A?.turnover), numOrDash(B?.turnover)],
            ['交易成本', numOrDash(A?.transaction_cost), numOrDash(B?.transaction_cost)],
            ['成交笔数', String(A?.trades ?? '—'), String(B?.trades ?? '—')],
            ['估值点数', String(A?.points ?? '—'), String(B?.points ?? '—')],
          ].map(r => [
            <span className="text-xs">{r[0]}</span>,
            <span className="font-mono text-xs">{r[1]}</span>,
            <span className="font-mono text-xs">{r[2]}</span>,
          ])} />
        <KV k="候选 − 现行（组合净收益）" v={pctOrDash(metrics.delta_candidate_minus_incumbent)} />
        {insufficient && (
          <Note tone="warn">
            可比样本不足（{metrics.comparable_samples} &lt; 冻结计划要求的 {minSample}）→ 结论为
            <b> inconclusive</b>（不结论）。不把样本不足说成「候选更好 / 更差」，也不用 0 顶替算不出的值。
          </Note>
        )}
      </div>
    </Card>
  )
}

function ButtonsRow({ p, events, reinject, busy, setBusy, onDone, onErr }: {
  p: Proposal; events: Ev[]; reinject: any[]
  busy: boolean; setBusy: (b: boolean) => void; onDone: () => void; onErr: (s: string) => void
}) {
  const [confirming, setConfirming] = useState('')
  const [msg, setMsg] = useState('')
  const [reason, setReason] = useState('人工回滚：观察期风险由人判断')

  // 生效后的行哈希（回滚 CAS 的期望值）—— 从 applied 事件里取，**不猜**。
  const appliedEv = [...events].reverse().find(e => e.proposal_id === p.proposal_id && e.kind === 'applied')
  const currentHash = appliedEv?.payload?.active_config_hash || ''
  const canApply = p.status === 'passed'
  const canRollback = p.status === 'applied'

  async function doApply() {
    setBusy(true); setMsg('')
    try {
      const out = await finFetch<any>('/evolution/apply', {
        method: 'POST',
        body: JSON.stringify({
          proposal_id: p.proposal_id,
          expected_base_config_hash: p.base_config_hash,
          confirm: true,
        }),
      })
      setMsg(`已生效：${out.from_key} → ${out.to_key}`)
      setConfirming('')
      onDone()
    } catch (e: any) {
      if (e?.status === 401) { onErr('登录已失效'); return }
      setMsg(`未生效：${e?.message || '请求失败'}`)
      onDone()      // 被闸门拒绝也会追加一条 rejected_by_gate，刷新事件链看得见
    } finally { setBusy(false) }
  }

  async function doRollback() {
    setBusy(true); setMsg('')
    try {
      const out = await finFetch<any>('/evolution/rollback', {
        method: 'POST',
        body: JSON.stringify({
          proposal_id: p.proposal_id, reason,
          expected_current_config_hash: currentHash || null,
        }),
      })
      setMsg(`已回滚：${out.from_key} → ${out.to_key}`)
      setConfirming('')
      onDone()
    } catch (e: any) {
      if (e?.status === 401) { onErr('登录已失效'); return }
      setMsg(`回滚被拒：${e?.message || '请求失败'}`)
      onDone()
    } finally { setBusy(false) }
  }

  async function doRetry() {
    setBusy(true); setMsg('')
    try {
      const out = await finFetch<any>('/evolution/reinject/retry', {
        method: 'POST', body: JSON.stringify({ proposal_id: p.proposal_id }),
      })
      setMsg(`回灌重试：已重试 ${out.tried} · 完成 ${out.done} · 待完成 ${out.pending}`)
      onDone()
    } catch (e: any) {
      setMsg(`回灌重试失败：${e?.message || '请求失败'}`)
    } finally { setBusy(false) }
  }

  return (
    <Card>
      <CardHead title="人工动作" sub="只有人能改配置 · 界面上没有任何「自动生效」开关（FIN_AUTO_APPLY 恒 0）" />
      <div className="p-4 flex flex-col gap-3">
        {p.reinject_pending && (
          <Note tone="warn">
            <b>回滚已完成，失败经验回灌待完成</b>：回滚要落三样（事件 / 配置日志 / 一条有证据的失败经验），
            第三样这次没写进去。任务已落库、不会丢，重试即可。
            {reinject.length > 0 && (
              <div className="mt-1.5 flex flex-col gap-0.5">
                {reinject.map((t, i) => (
                  <span key={i} className="font-mono text-[11px]">
                    {t.task_id} · {t.status} · 已试 {t.attempts} 次 · {t.last_error || t.reason || ''}
                  </span>
                ))}
              </div>
            )}
            <div className="mt-2"><Button size="sm" disabled={busy} onClick={() => void doRetry()}>重试回灌</Button></div>
          </Note>
        )}

        <div className="flex items-center gap-2 flex-wrap">
          <Button kind="pri" size="sm" disabled={busy || !canApply}
            onClick={() => { setMsg(''); setConfirming(confirming === 'apply' ? '' : 'apply') }}>
            应用到模拟盘
          </Button>
          {!canApply && (
            <span className="text-[11px] flex items-center gap-1.5" style={{ color: 'var(--text-muted)' }}>
              只有 <Chip tone="down">passed</Chip>（验证通过）的提案才能生效；不结论与不达标都不是通过。
            </span>
          )}
          {canRollback && (
            <Button kind="warn" size="sm" disabled={busy}
              onClick={() => { setMsg(''); setConfirming(confirming === 'rollback' ? '' : 'rollback') }}>
              回滚到上一个已验证版本
            </Button>
          )}
        </div>

        {confirming === 'apply' && (
          <div className="rounded-xl p-3 flex flex-col gap-2" style={{ border: '1px solid rgba(176,106,50,.35)', background: 'rgba(176,106,50,.05)' }}>
            <div className="text-xs" style={{ color: '#6B5334' }}>
              确认把候选配置应用到<b>模拟盘</b>？（先核对上面的 <b>param_diff</b> 与 <b>冻结计划</b>，两者都已显示在本页）
              <br />生效会：注册一个新配置版本 → 写参数变更日志 → 切换 active 版本，三步同一事务。
              基线哈希：<span className="font-mono">{(p.base_config_hash || '').slice(0, 16)}</span>
            </div>
            <div className="flex gap-2">
              <Button kind="pri" size="sm" disabled={busy} onClick={() => void doApply()}>确认应用</Button>
              <Button size="sm" disabled={busy} onClick={() => setConfirming('')}>取消</Button>
            </div>
          </div>
        )}

        {confirming === 'rollback' && (
          <div className="rounded-xl p-3 flex flex-col gap-2" style={{ border: '1px solid rgba(164,51,43,.35)', background: 'rgba(164,51,43,.05)' }}>
            <div className="text-xs" style={{ color: '#7C2A24' }}>
              确认回滚到<b>上一个已验证版本</b>？回滚方向永远是「风险只降不升」，且会一并停掉本项目的模拟下单（重新开启是人的动作）。
            </div>
            <input value={reason} onChange={e => setReason(e.target.value)}
              className="w-full rounded-lg px-2.5 py-1.5 text-xs"
              style={{ border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }} />
            <div className="flex gap-2">
              <Button kind="warn" size="sm" disabled={busy} onClick={() => void doRollback()}>确认回滚</Button>
              <Button size="sm" disabled={busy} onClick={() => setConfirming('')}>取消</Button>
            </div>
          </div>
        )}

        {msg && <Note tone={msg.startsWith('已') ? 'ok' : 'warn'}>{msg}</Note>}
      </div>
    </Card>
  )
}
