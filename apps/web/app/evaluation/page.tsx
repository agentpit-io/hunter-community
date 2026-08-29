'use client'
// /evaluation · 预测评估看板 · 面向复赛评委的只读页
// 与 /backtest (admin 后台) 分开:这里没有 pool/config 编辑 · 只展示 4 API 结果
// 关联方案:doc/开源hunter-community/04开源比赛/2026-08-28_复赛验证方案-*.md §3.A
import { useCallback, useEffect, useState } from 'react'
import { Activity, AlertTriangle, RefreshCw, TrendingUp, TrendingDown } from 'lucide-react'

type Accuracy = {
  sample: number
  hit_rate: number | null
  amt_hit_rate: number | null
  mae: number | null
  by_horizon: { horizon: number; n: number; hit_rate: number | null; mae: number | null }[]
  by_signal: { signal: string; n: number; hit_rate: number | null }[]
  window_days: number
}
type Consistency = {
  distribution: Record<string, number>
  sample: number
  stability: number
  top_reversal_drivers: { factor: string; label?: string; n: number }[]
  window_days: number
}
type Reversal = {
  symbol: string
  pred_date: string
  prev_run?: string
  curr_run?: string
  prev_change: number | null
  curr_change: number | null
  delta: number | null
  top_driver: string | null
  driver_share: number | null
  explain?: string
}
type EvoRow = {
  base_date: string
  pred_date: string
  change_pct: number | null
  signal?: string
  real_change?: number | null
  dir_hit?: boolean | null
}
type ReliabilityBin = {
  bin: [number, number]
  avg_pred: number | null
  freq: number | null
  n: number
}
type Calibration = {
  sample_size: number
  brier: number | null
  ece: number | null
  reliability: ReliabilityBin[] | null
  window_days: number
  symbol: string | null
  note: string | null
  threshold_pct?: number
  prob_model?: string
}

const authH = (): Record<string, string> => {
  const t = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }
}

const VERDICT_LABEL: Record<string, { label: string; color: string }> = {
  consistent: { label: '一致', color: '#22c55e' },
  strengthen: { label: '强化', color: '#3b82f6' },
  weaken: { label: '弱化', color: '#f59e0b' },
  reversal: { label: '反转', color: '#ef4444' },
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v.toFixed(digits)}%`
}
function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

// URL 拿 symbol · 客户端组件在 useEffect 里读 window · 不用 useSearchParams 免 Suspense
function getInitialSymbol(): string {
  if (typeof window === 'undefined') return ''
  return new URLSearchParams(window.location.search).get('symbol') || ''
}

export default function EvaluationPage() {
  const [days, setDays] = useState(90)
  const [symbol, setSymbol] = useState<string>('')  // '' = 全部
  const [acc, setAcc] = useState<Accuracy | null>(null)
  const [cons, setCons] = useState<Consistency | null>(null)
  const [revs, setRevs] = useState<Reversal[]>([])
  const [evoCode, setEvoCode] = useState('600519.SH')
  const [evo, setEvo] = useState<EvoRow[]>([])
  const [cali, setCali] = useState<Calibration | null>(null)   // §3.C 校准
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  // URL ?symbol=xxx 首次挂载读一次
  useEffect(() => {
    const s = getInitialSymbol()
    if (s) { setSymbol(s); setEvoCode(s) }
  }, [])

  const load = useCallback(async (d: number, sym: string) => {
    setLoading(true); setErr('')
    try {
      const accUrl = sym
        ? `/api/backtest/accuracy?days=${d}&symbol=${encodeURIComponent(sym)}`
        : `/api/backtest/accuracy?days=${d}`
      // §3.C · calibration 也带 symbol 过滤
      const caliUrl = sym
        ? `/api/backtest/calibration?days=${d}&symbol=${encodeURIComponent(sym)}`
        : `/api/backtest/calibration?days=${d}`
      const [a, c, v, cl] = await Promise.all([
        fetch(accUrl, { headers: authH() }).then(r => r.json()),
        fetch(`/api/backtest/consistency?days=${d}`, { headers: authH() }).then(r => r.json()),
        fetch(`/api/backtest/reversals?limit=50`, { headers: authH() }).then(r => r.json()),
        fetch(caliUrl, { headers: authH() }).then(r => r.json()),
      ])
      if (a?.detail) setErr(a.detail)
      setAcc(a?.detail ? null : a)
      setCons(c?.detail ? null : c)
      setCali(cl?.detail ? null : cl)
      // reversals 客户端按 symbol 过滤(API 不带 symbol 参数)· 全部时留 20 条
      const items = v?.items || []
      setRevs(sym ? items.filter((r: Reversal) => r.symbol === sym) : items.slice(0, 20))
    } catch (e) {
      setErr(String(e))
    } finally { setLoading(false) }
  }, [])

  const loadEvo = useCallback(async (code: string) => {
    if (!code.trim()) return
    try {
      const r = await fetch(`/api/backtest/evolution/${encodeURIComponent(code.trim())}?limit=40`,
                            { headers: authH() })
      const d = await r.json()
      setEvo(Array.isArray(d?.items) ? d.items : Array.isArray(d) ? d : [])
    } catch { setEvo([]) }
  }, [])

  useEffect(() => { load(days, symbol) }, [load, days, symbol])
  useEffect(() => { loadEvo(evoCode) }, [loadEvo, evoCode])

  const reversalRate = cons && cons.sample > 0
    ? (cons.distribution?.reversal || 0) / cons.sample * 100
    : null

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <div className="max-w-6xl mx-auto p-4 md:p-6 space-y-4">

        {/* Header + demo banner */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Activity className="w-6 h-6" /> 预测评估看板
            </h1>
            <p className="text-sm opacity-70 mt-1">
              每日预测的方向命中率 · 一致性分布 · 反转清单 · 单股演变
            </p>
          </div>
          <div className="flex items-center gap-2">
            {[30, 60, 90, 180].map(d => (
              <button key={d} onClick={() => setDays(d)}
                className={`px-3 py-1 rounded text-sm border transition ${
                  days === d ? 'bg-blue-600 text-white border-blue-600'
                             : 'border-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}>{d}天</button>
            ))}
            <button onClick={() => load(days, symbol)} disabled={loading}
              className="px-3 py-1 rounded text-sm border border-gray-300 hover:bg-gray-50
                         dark:hover:bg-gray-800 disabled:opacity-50 flex items-center gap-1">
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />刷新
            </button>
          </div>
        </div>

        {/* 单股筛选态提示条 · 从 kpred 底部跳过来会带 ?symbol= */}
        {symbol && (
          <div className="p-3 rounded border border-blue-300 bg-blue-50 dark:bg-blue-950
                          dark:border-blue-700 text-sm flex items-center justify-between">
            <span>
              当前只看 <b className="font-mono">{symbol}</b> 的历史评估 ·
              命中率/反转清单/单股演变均按此过滤
            </span>
            <button onClick={() => { setSymbol('') }}
              className="text-xs px-2 py-0.5 rounded border border-blue-400 hover:bg-blue-100
                        dark:hover:bg-blue-900">
              清除筛选 ×
            </button>
          </div>
        )}

        {/* 演示数据横幅 · 复赛期间明示 */}
        <div className="p-3 rounded border border-amber-300 bg-amber-50 dark:bg-amber-950
                        dark:border-amber-700 text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 text-amber-600 flex-shrink-0" />
          <div>
            <b>演示数据</b> · 当前展示的是复赛评审用示例(model_ver = <code>demo-v1</code>)·
            真实收盘取自 akshare · 预测为合成 · 目的是让评委直观理解"持续验证"链路。
            生产环境请用 <code>services/backtest/scheduler.py</code> 跑每日流水线。
          </div>
        </div>

        {err && (
          <div className="p-3 rounded border border-red-300 bg-red-50 dark:bg-red-950
                          dark:border-red-700 text-sm">
            {err}
          </div>
        )}

        {/* 头部指标带 · 4 个大数字 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard title="总预测数" value={acc?.sample?.toLocaleString() ?? '—'}
                      subtitle={`最近 ${acc?.window_days ?? days} 天`} />
          <MetricCard title="方向命中率" value={fmtPct(acc?.hit_rate)}
                      subtitle="预测方向 = 真实方向" highlight />
          <MetricCard title="幅度命中率" value={fmtPct(acc?.amt_hit_rate)}
                      subtitle="误差 < 1%" />
          <MetricCard title="平均绝对误差" value={fmtNum(acc?.mae)}
                      subtitle="MAE · %" />
        </div>

        {/* 命中率横向条 · 按 horizon */}
        <Panel title="按预测跨度拆分" subtitle="第 N 个交易日的命中率与误差">
          {acc?.by_horizon?.length ? (
            <div className="space-y-2">
              {acc.by_horizon.map(h => (
                <div key={h.horizon} className="flex items-center gap-3">
                  <span className="w-14 text-sm opacity-70">第 {h.horizon} 日</span>
                  <div className="flex-1 h-6 bg-gray-100 dark:bg-gray-800 rounded overflow-hidden">
                    <div className="h-full bg-blue-500 flex items-center justify-end pr-2 text-xs text-white"
                         style={{ width: `${Math.min(100, Math.max(0, h.hit_rate || 0))}%` }}>
                      {fmtPct(h.hit_rate)}
                    </div>
                  </div>
                  <span className="w-24 text-xs opacity-70 text-right">
                    n={h.n} · MAE {fmtNum(h.mae)}
                  </span>
                </div>
              ))}
            </div>
          ) : <Empty />}
        </Panel>

        {/* 一致性分布 */}
        <Panel title="一致性分布"
               subtitle={`相邻两次预测的稳定性 · 稳定率 ${fmtPct(cons?.stability)} · 反转 ${fmtPct(reversalRate)}`}>
          {cons?.sample ? (
            <>
              <div className="flex h-8 rounded overflow-hidden mb-3">
                {(['consistent', 'strengthen', 'weaken', 'reversal'] as const).map(k => {
                  const n = cons.distribution?.[k] || 0
                  const pct = n / cons.sample * 100
                  if (pct === 0) return null
                  return (
                    <div key={k} className="flex items-center justify-center text-xs text-white font-medium"
                         style={{ width: `${pct}%`, background: VERDICT_LABEL[k].color }}
                         title={`${VERDICT_LABEL[k].label}: ${n} (${pct.toFixed(1)}%)`}>
                      {pct > 8 ? `${VERDICT_LABEL[k].label} ${pct.toFixed(0)}%` : ''}
                    </div>
                  )
                })}
              </div>
              {cons.top_reversal_drivers?.length > 0 && (
                <div className="text-sm">
                  <div className="opacity-70 mb-1">反转主因 top 5(变卦时贡献最大的因子):</div>
                  <div className="flex flex-wrap gap-2">
                    {cons.top_reversal_drivers.slice(0, 5).map(d => (
                      <span key={d.factor} className="px-2 py-1 rounded bg-red-50 dark:bg-red-950
                                                     border border-red-200 dark:border-red-800 text-xs">
                        {d.label || d.factor} <span className="opacity-60">×{d.n}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : <Empty />}
        </Panel>

        {/* 反转清单 */}
        <Panel title="最近反转清单" subtitle="昨看涨今看跌(或反之)+ 因子级归因">
          {revs.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs opacity-70 border-b border-gray-200 dark:border-gray-700">
                  <tr>
                    <th className="text-left py-2 pr-2">股票</th>
                    <th className="text-left py-2 pr-2">目标日</th>
                    <th className="text-right py-2 pr-2">先前预测</th>
                    <th className="text-right py-2 pr-2">当前预测</th>
                    <th className="text-left py-2 pr-2">变卦主因</th>
                    <th className="text-left py-2">解读</th>
                  </tr>
                </thead>
                <tbody>
                  {revs.map((r, idx) => (
                    <tr key={idx} className="border-b border-gray-100 dark:border-gray-800
                                             hover:bg-gray-50 dark:hover:bg-gray-900 cursor-pointer"
                        onClick={() => setEvoCode(r.symbol)}>
                      <td className="py-2 pr-2 font-mono text-xs">{r.symbol}</td>
                      <td className="py-2 pr-2 text-xs opacity-70">{r.pred_date?.slice(5)}</td>
                      <td className={`py-2 pr-2 text-right font-mono text-xs ${
                            (r.prev_change ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                        {r.prev_change !== null ? `${r.prev_change >= 0 ? '+' : ''}${r.prev_change.toFixed(2)}%` : '—'}
                      </td>
                      <td className={`py-2 pr-2 text-right font-mono text-xs ${
                            (r.curr_change ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                        {r.curr_change !== null ? `${r.curr_change >= 0 ? '+' : ''}${r.curr_change.toFixed(2)}%` : '—'}
                      </td>
                      <td className="py-2 pr-2 text-xs">
                        {r.top_driver ? (
                          <span className="px-1.5 py-0.5 rounded bg-red-50 dark:bg-red-950
                                          border border-red-200 dark:border-red-800">
                            {r.top_driver} {r.driver_share ? `(${r.driver_share.toFixed(0)}%)` : ''}
                          </span>
                        ) : '—'}
                      </td>
                      <td className="py-2 text-xs opacity-70">{r.explain || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-xs opacity-50 mt-2">💡 点击行 → 下方查看该股预测演变</div>
            </div>
          ) : <Empty />}
        </Panel>

        {/* 单股演变 */}
        <Panel title="单股预测演变"
               subtitle="同一只股票 · 多次预测同一目标日的轨迹 · 看模型如何改主意">
          <div className="flex items-center gap-2 mb-3">
            <input value={evoCode} onChange={e => setEvoCode(e.target.value)}
                   placeholder="股票代码 · 如 600519.SH"
                   className="px-3 py-1.5 border rounded text-sm bg-white dark:bg-gray-900
                             border-gray-300 dark:border-gray-700 w-56 font-mono" />
            <button onClick={() => loadEvo(evoCode)}
                    className="px-3 py-1.5 rounded text-sm border border-gray-300
                              hover:bg-gray-50 dark:hover:bg-gray-800">查询</button>
          </div>
          {evo.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-xs opacity-70 border-b border-gray-200 dark:border-gray-700">
                  <tr>
                    <th className="text-left py-2 pr-2">发起日</th>
                    <th className="text-left py-2 pr-2">目标日</th>
                    <th className="text-right py-2 pr-2">预测涨跌</th>
                    <th className="text-right py-2 pr-2">真实涨跌</th>
                    <th className="text-left py-2 pr-2">信号</th>
                    <th className="text-center py-2">方向</th>
                  </tr>
                </thead>
                <tbody>
                  {evo.slice(0, 40).map((r, i) => (
                    <tr key={i} className="border-b border-gray-100 dark:border-gray-800">
                      <td className="py-1.5 pr-2 text-xs opacity-70">{r.base_date?.slice(5)}</td>
                      <td className="py-1.5 pr-2 text-xs opacity-70">{r.pred_date?.slice(5)}</td>
                      <td className={`py-1.5 pr-2 text-right font-mono text-xs ${
                            (r.change_pct ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                        {r.change_pct !== null && r.change_pct !== undefined
                          ? `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}%` : '—'}
                      </td>
                      <td className={`py-1.5 pr-2 text-right font-mono text-xs ${
                            (r.real_change ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'
                          }`}>
                        {r.real_change !== null && r.real_change !== undefined
                          ? `${r.real_change >= 0 ? '+' : ''}${r.real_change.toFixed(2)}%` : '—'}
                      </td>
                      <td className="py-1.5 pr-2 text-xs">{r.signal || '—'}</td>
                      <td className="py-1.5 text-center">
                        {r.dir_hit === true ? <TrendingUp className="w-4 h-4 text-green-600 inline" />
                          : r.dir_hit === false ? <TrendingDown className="w-4 h-4 text-red-600 inline" />
                          : <span className="opacity-40">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty />}
        </Panel>

        {/* 概率校准(§3.C · 复赛新增) */}
        <Panel title="概率校准"
               subtitle="Reliability diagram + Brier + ECE · 越贴对角线越校准 · 越低越好">
          <CalibrationView cali={cali} />
        </Panel>

        {/* 合规底栏 · 演示数据说明 */}
        <div className="pt-4 pb-8 text-xs opacity-60 text-center leading-relaxed">
          Hunter Community · 预测评估看板 · 数据仅供研究参考 · 不构成投资建议 ·
          历史准确性不代表未来 · 演示数据请勿用于实盘决策
        </div>
      </div>
    </div>
  )
}

function MetricCard({ title, value, subtitle, highlight }:
                    { title: string; value: string; subtitle?: string; highlight?: boolean }) {
  return (
    <div className={`p-4 rounded-lg border ${
      highlight ? 'border-blue-300 bg-blue-50 dark:bg-blue-950 dark:border-blue-700'
                : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900'
    }`}>
      <div className="text-xs opacity-70">{title}</div>
      <div className="text-2xl font-bold mt-1">{value}</div>
      {subtitle && <div className="text-xs opacity-50 mt-1">{subtitle}</div>}
    </div>
  )
}

function Panel({ title, subtitle, children }:
               { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="p-4 rounded-lg border border-gray-200 dark:border-gray-700
                    bg-white dark:bg-gray-900">
      <div className="mb-3">
        <div className="font-semibold">{title}</div>
        {subtitle && <div className="text-xs opacity-60 mt-0.5">{subtitle}</div>}
      </div>
      {children}
    </div>
  )
}

function Empty() {
  return <div className="text-sm opacity-50 py-8 text-center">暂无数据</div>
}


// ─── 概率校准视图(§3.C 复赛验证) ────────────────────────────
// reliability diagram · SVG 手绘 · 45° 对角线 = 完美校准
// 严禁 mock 兜底 · note 存在或 sample_size < 30 直接显数据不足

function CalibrationView({ cali }: { cali: Calibration | null }) {
  if (!cali) return <Empty />
  if (cali.note || cali.sample_size < 30) {
    return (
      <div className="text-sm py-6 text-center opacity-70">
        {cali.note || `样本量 ${cali.sample_size} < 30 · 数据不足以给出校准评估`}
      </div>
    )
  }
  const rc = cali.reliability || []
  const bins = rc.filter(b => b.avg_pred !== null && b.freq !== null && b.n > 0)

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* 左:reliability diagram */}
      <div className="md:col-span-2">
        <ReliabilityDiagram bins={bins} />
        <div className="mt-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
          横轴:该桶预测概率平均 avg_pred · 纵轴:该桶实际频率 freq ·
          点越贴 45° 对角线越校准 · 点大小 = 样本量 n
        </div>
      </div>
      {/* 右:Brier · ECE · 样本 */}
      <div className="space-y-3">
        <BigMetric label="Brier Score"
                   value={cali.brier === null ? '—' : cali.brier.toFixed(3)}
                   hint="越低越好 · 0.25 = 抛硬币 · 0 = 完美"
                   color={cali.brier !== null && cali.brier < 0.25 ? '#22c55e' : '#f59e0b'} />
        <BigMetric label="ECE"
                   value={cali.ece === null ? '—' : cali.ece.toFixed(3)}
                   hint="Expected Calibration Error · 越低越校准" />
        <BigMetric label="样本量"
                   value={cali.sample_size.toLocaleString()}
                   hint={`最近 ${cali.window_days} 天`} />
        {cali.prob_model && (
          <div className="text-[10px] opacity-60 pt-2">
            {cali.prob_model}
          </div>
        )}
      </div>
    </div>
  )
}

function ReliabilityDiagram({ bins }: { bins: ReliabilityBin[] }) {
  const size = 280, pad = 32
  const inner = size - pad * 2
  // n → 半径 · 3-10 px 范围
  const maxN = Math.max(...bins.map(b => b.n), 1)
  const rOf = (n: number) => 3 + 7 * Math.sqrt(n / maxN)

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}
         style={{ display: 'block' }}>
      {/* 背景网格 */}
      {[0.25, 0.5, 0.75].map(t => {
        const p = pad + t * inner
        return (
          <g key={t}>
            <line x1={pad} y1={p} x2={size - pad} y2={p}
                  stroke="rgba(148,163,184,0.15)" strokeWidth={1} />
            <line x1={p} y1={pad} x2={p} y2={size - pad}
                  stroke="rgba(148,163,184,0.15)" strokeWidth={1} />
          </g>
        )
      })}
      {/* 对角线 · 完美校准 */}
      <line x1={pad} y1={size - pad} x2={size - pad} y2={pad}
            stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 4" />
      {/* 坐标 · 底部 X · 左侧 Y */}
      <line x1={pad} y1={size - pad} x2={size - pad} y2={size - pad}
            stroke="var(--text-muted)" strokeWidth={1} />
      <line x1={pad} y1={pad} x2={pad} y2={size - pad}
            stroke="var(--text-muted)" strokeWidth={1} />
      {[0, 0.5, 1.0].map(t => {
        const px = pad + t * inner
        const py = size - pad - t * inner
        return (
          <g key={t}>
            <text x={px} y={size - pad + 12} textAnchor="middle"
                  style={{ fontSize: 9, fill: 'var(--text-muted)' }}>{t.toFixed(1)}</text>
            <text x={pad - 4} y={py + 3} textAnchor="end"
                  style={{ fontSize: 9, fill: 'var(--text-muted)' }}>{t.toFixed(1)}</text>
          </g>
        )
      })}
      {/* 数据点 · 半径按 n · 颜色按偏离对角线程度 */}
      {bins.map((b, i) => {
        const x = pad + (b.avg_pred! * inner)
        const y = size - pad - (b.freq! * inner)
        const dev = Math.abs((b.avg_pred || 0) - (b.freq || 0))
        const color = dev < 0.05 ? '#22c55e' : dev < 0.15 ? '#f59e0b' : '#ef4444'
        return (
          <g key={i}>
            <circle cx={x} cy={y} r={rOf(b.n)} fill={color} fillOpacity={0.5}
                    stroke={color} strokeWidth={1.5} />
            <title>bin [{b.bin[0]}-{b.bin[1]}] · avg_pred {b.avg_pred?.toFixed(3)} ·
                   freq {b.freq?.toFixed(3)} · n {b.n}</title>
          </g>
        )
      })}
      {/* 轴标签 */}
      <text x={size / 2} y={size - 6} textAnchor="middle"
            style={{ fontSize: 10, fill: 'var(--text-muted)' }}>预测概率 avg_pred</text>
      <text x={10} y={size / 2} textAnchor="middle"
            transform={`rotate(-90 10 ${size / 2})`}
            style={{ fontSize: 10, fill: 'var(--text-muted)' }}>实际频率 freq</text>
    </svg>
  )
}

function BigMetric({ label, value, hint, color }:
  { label: string; value: string; hint?: string; color?: string }) {
  return (
    <div className="p-3 rounded border border-gray-200 dark:border-gray-700
                    bg-white dark:bg-gray-900">
      <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="font-mono font-bold text-2xl mt-1" style={{ color: color || 'var(--text)' }}>
        {value}
      </div>
      {hint && <div className="text-[10px] opacity-60 mt-1">{hint}</div>}
    </div>
  )
}
