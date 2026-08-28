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

export default function EvaluationPage() {
  const [days, setDays] = useState(90)
  const [acc, setAcc] = useState<Accuracy | null>(null)
  const [cons, setCons] = useState<Consistency | null>(null)
  const [revs, setRevs] = useState<Reversal[]>([])
  const [evoCode, setEvoCode] = useState('600519.SH')
  const [evo, setEvo] = useState<EvoRow[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (d: number) => {
    setLoading(true); setErr('')
    try {
      const [a, c, v] = await Promise.all([
        fetch(`/api/backtest/accuracy?days=${d}`, { headers: authH() }).then(r => r.json()),
        fetch(`/api/backtest/consistency?days=${d}`, { headers: authH() }).then(r => r.json()),
        fetch(`/api/backtest/reversals?limit=20`, { headers: authH() }).then(r => r.json()),
      ])
      if (a?.detail) setErr(a.detail)
      setAcc(a?.detail ? null : a)
      setCons(c?.detail ? null : c)
      setRevs(v?.items || [])
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

  useEffect(() => { load(days) }, [load, days])
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
            <button onClick={() => load(days)} disabled={loading}
              className="px-3 py-1 rounded text-sm border border-gray-300 hover:bg-gray-50
                         dark:hover:bg-gray-800 disabled:opacity-50 flex items-center gap-1">
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />刷新
            </button>
          </div>
        </div>

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
               subtitle="同一只股票 · 多次预测同一目标日的轨迹 · 看模型如何"改主意"">
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
