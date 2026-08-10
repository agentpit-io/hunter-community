'use client'
// 模型回测 · Admin 后台
// 三块: 总览看板 / 股票池管理(可增删) / 参数配置
import { useCallback, useEffect, useState } from 'react'
import { Activity, Settings, ListPlus, Trash2, RefreshCw, Play, Loader2, AlertCircle } from 'lucide-react'

type Cfg = {
  pool_mode: string; pred_len: number; run_hour: number; run_minute: number
  concurrency: number; enabled: boolean
  flat_band: number; strict_dir: boolean
  rel_err_pct: number; abs_err_pp: number
  reversal_min: number; strength_delta: number; driver_min_share: number
  model_ver: string
  skip_suspended?: boolean; skip_limit?: boolean; skip_st?: boolean
  min_list_days?: number; min_amount_wan?: number; adjust_mode?: string
  benchmark_code?: string; max_pred_pct?: number; outlier_mode?: string
  retain_days?: number; kronos_retry?: number; kronos_timeout?: number; alert_hit_rate?: number
}
type PoolItem = { symbol: string; name: string; chain?: string; source?: string; enabled?: boolean }
type Accuracy = {
  sample: number; hit_rate: number | null; amt_hit_rate: number | null; mae: number | null
  by_horizon: { horizon: number; n: number; hit_rate: number | null; mae: number | null }[]
  by_signal: { signal: string; n: number; hit_rate: number | null }[]
}
type Consistency = {
  distribution: Record<string, number>; sample: number; stability: number
  top_reversal_drivers: { factor: string; label?: string; n: number }[]
}
type Reversal = {
  symbol: string; pred_date: string; prev_change: number | null; curr_change: number | null
  delta: number | null; top_driver: string | null; driver_share: number | null; explain?: string
}

const authH = (): Record<string, string> => {
  const t = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }
}

const POOL_LABEL: Record<string, string> = {
  core: '产业链代表股(推荐)', chain_all: '产业链全部股', watchlist: '用户自选股', custom: '纯自定义',
}

export default function BacktestPage() {
  const [tab, setTab] = useState<'board' | 'pool' | 'config'>('board')
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [poolSize, setPoolSize] = useState(0)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [prog, setProg] = useState<{ done: number; total: number; ok: number }>({ done: 0, total: 0, ok: 0 })

  const [acc, setAcc] = useState<Accuracy | null>(null)
  const [cons, setCons] = useState<Consistency | null>(null)
  const [revs, setRevs] = useState<Reversal[]>([])

  const [pool, setPool] = useState<{ effective: PoolItem[]; custom: PoolItem[]; count: number } | null>(null)
  const [newCode, setNewCode] = useState('')
  const [newName, setNewName] = useState('')
  const [detailCode, setDetailCode] = useState('')

  const loadCfg = useCallback(async () => {
    try {
      const r = await fetch('/api/backtest/config', { headers: authH() })
      if (r.status === 403) { setErr('仅管理员可访问本页'); return }
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setCfg(d.config); setPoolSize(d.pool_size); setErr('')
    } catch (e) { setErr(String(e)) }
  }, [])

  const loadBoard = useCallback(async () => {
    try {
      const [a, c, v] = await Promise.all([
        fetch('/api/backtest/accuracy?days=30').then(r => r.json()),
        fetch('/api/backtest/consistency?days=30').then(r => r.json()),
        fetch('/api/backtest/reversals?limit=15').then(r => r.json()),
      ])
      setAcc(a); setCons(c); setRevs(v.items || [])
    } catch { /* 空数据不报错 */ }
  }, [])

  const loadPool = useCallback(async () => {
    try {
      const r = await fetch('/api/backtest/pool', { headers: authH() })
      if (r.ok) setPool(await r.json())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadCfg(); loadBoard()
    // 页面刷新后若后台仍在跑, 恢复进度显示
    fetch('/api/backtest/run/status', { headers: authH() })
      .then(r => r.ok ? r.json() : null)
      .then(s => {
        if (s?.running) { setRunning(true); setProg({ done: s.done || 0, total: s.total || 0, ok: s.ok || 0 }) }
      }).catch(() => {})
  }, [loadCfg, loadBoard])
  useEffect(() => { if (tab === 'pool') loadPool() }, [tab, loadPool])

  const patchCfg = async (patch: Partial<Cfg>) => {
    setSaving(true)
    try {
      const r = await fetch('/api/backtest/config', {
        method: 'PUT', headers: authH(), body: JSON.stringify(patch),
      })
      if (r.ok) { const d = await r.json(); setCfg(d.config); setPoolSize(d.pool_size) }
    } catch { /* ignore */ }
    setSaving(false)
  }

  // 任务在后台跑(约12分钟, 远超Nginx 120s超时), 故异步启动 + 轮询进度
  const runNow = async () => {
    if (!confirm(`立即跑一次回测?股票池 ${poolSize} 只,预计 ${Math.ceil(poolSize / 6)} 分钟。\n\n任务在后台运行,可以关闭页面。`)) return
    try {
      const r = await fetch('/api/backtest/run', { method: 'POST', headers: authH() })
      const d = await r.json()
      if (!r.ok) { alert(`启动失败:${d.detail || '未知'}`); return }
      setRunning(true)
      setProg({ done: 0, total: d.total || poolSize, ok: 0 })
    } catch (e) { alert(`启动失败:${e}`) }
  }

  // 运行中每 5 秒查一次进度
  useEffect(() => {
    if (!running) return
    const t = setInterval(async () => {
      try {
        const r = await fetch('/api/backtest/run/status', { headers: authH() })
        if (!r.ok) return
        const s = await r.json()
        setProg({ done: s.done || 0, total: s.total || 0, ok: s.ok || 0 })
        if (!s.running) {
          setRunning(false)
          if (s.error) alert(`运行出错:${s.error}`)
          else if (s.result) {
            const sn = s.result.snapshot || {}
            alert(`完成!成功 ${sn.ok ?? 0} 只,入库 ${sn.rows ?? 0} 条预测。`)
          }
          loadBoard(); loadPool()
        }
      } catch { /* ignore */ }
    }, 5000)
    return () => clearInterval(t)
  }, [running, loadBoard, loadPool])

  const addStock = async () => {
    const code = newCode.trim()
    if (!/^\d{6}$/.test(code)) { alert('请输入6位股票代码'); return }
    const r = await fetch('/api/backtest/pool', {
      method: 'POST', headers: authH(), body: JSON.stringify({ symbol: code, name: newName.trim() }),
    })
    if (r.ok) { setNewCode(''); setNewName(''); loadPool(); loadCfg() }
    else alert('添加失败')
  }

  const delStock = async (symbol: string) => {
    if (!confirm(`从回测池移除 ${symbol}?`)) return
    await fetch(`/api/backtest/pool/${symbol}`, { method: 'DELETE', headers: authH() })
    loadPool(); loadCfg()
  }

  const importChains = async () => {
    if (!confirm('把29条产业链的代表股导入自定义池?')) return
    const r = await fetch('/api/backtest/pool/import-chains?rep_only=true', { method: 'POST', headers: authH() })
    const d = await r.json()
    alert(r.ok ? `已导入 ${d.imported} 只` : '导入失败')
    loadPool(); loadCfg()
  }

  if (err) return (
    <div className="p-8">
      <div className="flex items-center gap-2 text-red-600"><AlertCircle className="w-5 h-5" />{err}</div>
    </div>
  )

  const Num = ({ label, value, unit = '', hint = '', good }: {
    label: string; value: number | null | undefined; unit?: string; hint?: string; good?: boolean
  }) => (
    <div className="rounded-xl border p-4" style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="text-2xl font-bold mt-1"
        style={{ color: value == null ? 'var(--text-muted)' : good === undefined ? 'var(--text)' : good ? '#16a34a' : '#dc2626' }}>
        {value == null ? '--' : value}{unit}
      </div>
      {hint && <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>{hint}</div>}
    </div>
  )

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2"><Activity className="w-5 h-5" />模型回测</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            Kronos 多因子预测的准确性与稳定性评估 · 当前股票池 <b>{poolSize}</b> 只
            {cfg && ` · ${POOL_LABEL[cfg.pool_mode] || cfg.pool_mode}`}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => { loadBoard(); loadCfg() }}
            className="px-3 py-2 rounded-lg border text-sm flex items-center gap-1"
            style={{ borderColor: 'var(--border)' }}><RefreshCw className="w-4 h-4" />刷新</button>
          <button onClick={runNow} disabled={running}
            className="px-3 py-2 rounded-lg text-sm text-white flex items-center gap-1"
            style={{ background: running ? '#94a3b8' : '#2563eb' }}>
            {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {running ? `运行中 ${prog.done}/${prog.total}` : '立即运行'}
          </button>
        </div>
      </div>

      <div className="flex gap-1 mb-5 border-b" style={{ borderColor: 'var(--border)' }}>
        {([['board', '回测看板', Activity], ['pool', '股票池', ListPlus], ['config', '参数配置', Settings]] as const)
          .map(([k, label, Icon]) => (
            <button key={k} onClick={() => setTab(k)}
              className="px-4 py-2 text-sm flex items-center gap-1.5"
              style={{
                borderBottom: tab === k ? '2px solid #2563eb' : '2px solid transparent',
                color: tab === k ? '#2563eb' : 'var(--text-muted)', fontWeight: tab === k ? 600 : 400,
              }}><Icon className="w-4 h-4" />{label}</button>
          ))}
      </div>

      {/* ── 回测看板 ── */}
      {tab === 'board' && (
        <div className="space-y-6">
          {(!acc || acc.sample === 0) && (
            <div className="rounded-xl border p-6 text-sm" style={{ borderColor: 'var(--border)', background: 'var(--card)', color: 'var(--text-muted)' }}>
              还没有回测数据。系统每交易日 {cfg ? `${cfg.run_hour}:${String(cfg.run_minute).padStart(2, '0')}` : '16:40'} 自动运行,
              需要**至少两天**的预测才能产生重叠一致性数据。也可以点右上角「立即运行」手动跑一次。
            </div>
          )}
          <div className="grid grid-cols-4 gap-3">
            <Num label="方向命中率" value={acc?.hit_rate} unit="%" hint="涨跌看对的比例 · >55% 才算有效"
              good={acc?.hit_rate != null ? acc.hit_rate > 55 : undefined} />
            <Num label="幅度命中率" value={acc?.amt_hit_rate} unit="%" hint={cfg ? `相对误差≤${cfg.rel_err_pct}% 或 绝对≤${cfg.abs_err_pp}pp` : ''} />
            <Num label="平均绝对误差" value={acc?.mae} unit="pp" hint="预测与实际差几个百分点" />
            <Num label="稳定性指数" value={cons?.stability} unit="%" hint="1−反转率 · >85% 说明不朝令夕改"
              good={cons?.stability != null ? cons.stability > 85 : undefined} />
          </div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            样本量:回测 {acc?.sample ?? 0} 条 · 一致性对比 {cons?.sample ?? 0} 条
            {(acc?.sample ?? 0) < 200 && <span className="text-amber-600 ml-2">⚠ 样本不足200条,结论仅供参考</span>}
          </div>
          <div className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
            💡 <b>怎么看这几个数</b>:方向命中率要和 50%(瞎猜)比;如果大盘连涨,全猜"涨"也能有高命中率,
            所以运行日志里还会算<b>超额命中率</b>(扣掉跟随大盘的部分)。
            统计已自动剔除停牌、涨跌停、ST、次新股和低流动性样本——这些股票即使预测准也无法交易。
          </div>

          {acc && acc.by_horizon?.length > 0 && (
            <div>
              <h3 className="font-semibold mb-2 text-sm">按预测天数(看预测能力衰减)</h3>
              <table className="w-full text-sm border rounded-lg overflow-hidden" style={{ borderColor: 'var(--border)' }}>
                <thead style={{ background: 'var(--card)' }}>
                  <tr><th className="p-2 text-left">第几天</th><th className="p-2">样本</th><th className="p-2">方向命中率</th><th className="p-2">平均误差</th></tr>
                </thead>
                <tbody>
                  {acc.by_horizon.map(h => (
                    <tr key={h.horizon} className="border-t" style={{ borderColor: 'var(--border)' }}>
                      <td className="p-2">T+{h.horizon}</td>
                      <td className="p-2 text-center">{h.n}</td>
                      <td className="p-2 text-center font-semibold"
                        style={{ color: (h.hit_rate ?? 0) > 55 ? '#16a34a' : '#dc2626' }}>{h.hit_rate ?? '--'}%</td>
                      <td className="p-2 text-center">{h.mae ?? '--'}pp</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {acc && acc.by_signal?.length > 0 && (
            <div>
              <h3 className="font-semibold mb-2 text-sm">按信号强度(验证打分是否可信)</h3>
              <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                关键检查:如果「强多」的命中率还不如「偏多」,说明打分逻辑有问题
              </p>
              <table className="w-full text-sm border rounded-lg overflow-hidden" style={{ borderColor: 'var(--border)' }}>
                <thead style={{ background: 'var(--card)' }}>
                  <tr><th className="p-2 text-left">信号</th><th className="p-2">样本</th><th className="p-2">方向命中率</th></tr>
                </thead>
                <tbody>
                  {acc.by_signal.map(s => (
                    <tr key={s.signal} className="border-t" style={{ borderColor: 'var(--border)' }}>
                      <td className="p-2">{s.signal}</td>
                      <td className="p-2 text-center">{s.n}</td>
                      <td className="p-2 text-center font-semibold">{s.hit_rate ?? '--'}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {cons && Object.keys(cons.distribution || {}).length > 0 && (
            <div>
              <h3 className="font-semibold mb-2 text-sm">重叠一致性分布(昨天预测 vs 今天预测)</h3>
              <div className="flex gap-3 flex-wrap">
                {Object.entries(cons.distribution).map(([k, v]) => (
                  <div key={k} className="rounded-lg border px-4 py-2 text-sm" style={{ borderColor: 'var(--border)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>
                      {{ consistent: '一致', strengthen: '强化', weaken: '弱化', reversal: '反转' }[k] || k}
                    </span>
                    <b className="ml-2">{v}</b>
                  </div>
                ))}
              </div>
              {cons.top_reversal_drivers?.length > 0 && (
                <div className="mt-3 text-sm">
                  <span style={{ color: 'var(--text-muted)' }}>反转主因排行:</span>
                  {cons.top_reversal_drivers.map(d => (
                    <span key={d.factor} className="ml-2 px-2 py-0.5 rounded" style={{ background: 'var(--card)' }}>
                      {d.label || d.factor} <b>{d.n}</b>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {revs.length > 0 && (
            <div>
              <h3 className="font-semibold mb-2 text-sm">预测反转清单(为什么变卦)</h3>
              <div className="space-y-2">
                {revs.map((r, i) => (
                  <div key={i} onClick={() => setDetailCode(r.symbol)}
                    className="rounded-lg border p-3 text-sm cursor-pointer hover:shadow-sm"
                    style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
                    <div className="flex items-center gap-2">
                      <b>{r.symbol}</b>
                      <span style={{ color: 'var(--text-muted)' }}>目标日 {r.pred_date}</span>
                      <span className="ml-auto font-semibold" style={{ color: (r.delta ?? 0) < 0 ? '#dc2626' : '#16a34a' }}>
                        {r.prev_change?.toFixed(1)}% → {r.curr_change?.toFixed(1)}%
                      </span>
                    </div>
                    {r.top_driver && (
                      <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                        主因:<b>{r.top_driver}</b>(贡献 {r.driver_share?.toFixed(0)}%)
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 股票池 ── */}
      {tab === 'pool' && (
        <div className="space-y-5">
          <div className="rounded-xl border p-4" style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
            <h3 className="font-semibold text-sm mb-3">添加股票到回测池</h3>
            <div className="flex gap-2">
              <input value={newCode} onChange={e => setNewCode(e.target.value)} placeholder="6位代码 如 300308"
                className="px-3 py-2 rounded-lg border text-sm w-40" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }} />
              <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="名称(可选)"
                className="px-3 py-2 rounded-lg border text-sm w-40" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }} />
              <button onClick={addStock} className="px-4 py-2 rounded-lg text-sm text-white" style={{ background: '#2563eb' }}>添加</button>
              <button onClick={importChains} className="px-4 py-2 rounded-lg text-sm border ml-auto" style={{ borderColor: 'var(--border)' }}>
                一键导入29条产业链代表股
              </button>
            </div>
          </div>

          <div>
            <h3 className="font-semibold text-sm mb-1">回测股票池({pool?.count ?? 0} 只)</h3>
            <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
              点任意股票查看它的预测详情(近5次预测 + 实际结果对照 + 因子分值)
            </p>
            <div className="grid grid-cols-4 gap-2 text-sm">
              {(pool?.effective || []).map(s => {
                const custom = pool?.custom?.find(c => c.symbol === s.symbol)
                const manual = custom?.source === 'manual'
                return (
                  <div key={s.symbol}
                    onClick={() => setDetailCode(s.symbol)}
                    className="rounded border px-2 py-1.5 cursor-pointer hover:shadow-sm transition-shadow relative group"
                    style={{ borderColor: 'var(--border)' }}>
                    <div className="font-medium truncate pr-5">{s.name || s.symbol}</div>
                    <div className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                      {s.symbol} {s.chain}{manual && ' · 手工'}
                    </div>
                    <button onClick={e => { e.stopPropagation(); delStock(s.symbol) }}
                      className="absolute right-1 top-1 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-50">
                      <Trash2 className="w-3.5 h-3.5 text-red-500" />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          {pool?.custom?.some(c => !c.enabled) && (
            <div>
              <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-muted)' }}>
                已移除(不参与回测)
              </h3>
              <div className="flex flex-wrap gap-2 text-xs">
                {pool.custom.filter(c => !c.enabled).map(s => (
                  <button key={s.symbol}
                    onClick={async () => {
                      await fetch('/api/backtest/pool', {
                        method: 'POST', headers: authH(),
                        body: JSON.stringify({ symbol: s.symbol, name: s.name || '' }),
                      })
                      loadPool(); loadCfg()
                    }}
                    className="px-2 py-1 rounded border" style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
                    {s.name || s.symbol} <span className="ml-1">↩ 恢复</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {detailCode && <StockDetailModal code={detailCode} onClose={() => setDetailCode('')} />}

      {/* ── 参数配置 ── */}
      {tab === 'config' && cfg && (
        <div className="space-y-6 max-w-3xl">
          <Group title="A · 股票池与运行">
            <Row label="股票池">
              <select value={cfg.pool_mode} onChange={e => patchCfg({ pool_mode: e.target.value })}
                className="px-3 py-1.5 rounded border text-sm" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }}>
                {Object.entries(POOL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>当前 {poolSize} 只</span>
            </Row>
            <NumRow label="预测天数" v={cfg.pred_len} onSave={n => patchCfg({ pred_len: n })} hint="每次预测未来几个交易日" />
            <Row label="每日运行时间">
              <NumInput v={cfg.run_hour} onSave={n => patchCfg({ run_hour: n })} w={14} /> :
              <NumInput v={cfg.run_minute} onSave={n => patchCfg({ run_minute: n })} w={14} />
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>须晚于全市场日线(16:20)</span>
            </Row>
            <NumRow label="并发数" v={cfg.concurrency} onSave={n => patchCfg({ concurrency: n })} hint="Kronos 并发,过高会挤占行情采集" />
            <Row label="启用">
              <input type="checkbox" checked={cfg.enabled} onChange={e => patchCfg({ enabled: e.target.checked })} />
            </Row>
          </Group>

          <Group title="B · 涨跌方向判定">
            <NumRow label="平盘带宽度(%)" v={cfg.flat_band} step={0.1} onSave={n => patchCfg({ flat_band: n })}
              hint="涨跌幅在此区间内视为看平,不计入方向统计。太窄会把噪音算成错判" />
          </Group>

          <Group title="C · 幅度准确判定(双阈值,满足任一即命中)">
            <NumRow label="相对误差阈值(%)" v={cfg.rel_err_pct} onSave={n => patchCfg({ rel_err_pct: n })}
              hint="|预测−实际|÷|实际|,大涨跌时有效" />
            <NumRow label="绝对误差阈值(pp)" v={cfg.abs_err_pp} step={0.1} onSave={n => patchCfg({ abs_err_pp: n })}
              hint="小涨跌时的兜底:预测0.5%实际0.1%,相对误差400%但只差0.4pp,不该判错" />
          </Group>

          <Group title="D · 一致性与反转判定">
            <NumRow label="反转最小幅度(%)" v={cfg.reversal_min} step={0.1} onSave={n => patchCfg({ reversal_min: n })}
              hint="方向相反且至少一侧幅度≥此值才算反转,防 +0.1%→−0.1% 噪音" />
            <NumRow label="强化/弱化阈值(pp)" v={cfg.strength_delta} step={0.1} onSave={n => patchCfg({ strength_delta: n })} />
            <NumRow label="归因主因最小占比(%)" v={cfg.driver_min_share} onSave={n => patchCfg({ driver_min_share: n })}
              hint="某因子贡献超此比例才标为主因" />
          </Group>

          <Group title="E · 数据质量过滤(回测准确性的前提)">
            <Row label="排除停牌股">
              <input type="checkbox" checked={cfg.skip_suspended ?? true}
                onChange={e => patchCfg({ skip_suspended: e.target.checked })} />
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>停牌日无真实成交,对比会算出假误差</span>
            </Row>
            <Row label="排除涨跌停">
              <input type="checkbox" checked={cfg.skip_limit ?? true}
                onChange={e => patchCfg({ skip_limit: e.target.checked })} />
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>涨停买不进/跌停卖不出,预测再准也无法交易</span>
            </Row>
            <Row label="排除ST股">
              <input type="checkbox" checked={cfg.skip_st ?? true}
                onChange={e => patchCfg({ skip_st: e.target.checked })} />
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>ST涨跌停±5%,规则与普通股不同</span>
            </Row>
            <NumRow label="上市满N个交易日" v={cfg.min_list_days ?? 60} onSave={n => patchCfg({ min_list_days: n })}
              hint="次新股K线太短,MA60/MACD等因子算不出来" />
            <NumRow label="日均成交额下限(万元)" v={cfg.min_amount_wan ?? 5000} onSave={n => patchCfg({ min_amount_wan: n })}
              hint="低流动性股价格易被操纵,预测本就不可靠" />
            <Row label="复权方式">
              <select value={cfg.adjust_mode ?? 'qfq'} onChange={e => patchCfg({ adjust_mode: e.target.value })}
                className="px-3 py-1.5 rounded border text-sm" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }}>
                <option value="qfq">前复权(推荐)</option>
                <option value="hfq">后复权</option>
                <option value="none">不复权</option>
              </select>
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>不复权时10送10会被算成暴跌50%</span>
            </Row>
          </Group>

          <Group title="F · 基准对比与极端值">
            <Row label="基准指数">
              <input value={cfg.benchmark_code ?? '000300.SH'}
                onBlur={e => e.target.value !== cfg.benchmark_code && patchCfg({ benchmark_code: e.target.value })}
                onChange={() => {}}
                defaultValue={cfg.benchmark_code ?? '000300.SH'}
                className="px-2 py-1 rounded border text-sm w-32" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }} />
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>
                算「超额命中率」用:扣掉跟随大盘也能猜对的部分
              </span>
            </Row>
            <NumRow label="单日预测上限(%)" v={cfg.max_pred_pct ?? 11} step={0.5} onSave={n => patchCfg({ max_pred_pct: n })}
              hint="A股主板日涨跌停10%,预测出-16%属模型异常" />
            <Row label="超限处理">
              <select value={cfg.outlier_mode ?? 'clip'} onChange={e => patchCfg({ outlier_mode: e.target.value })}
                className="px-3 py-1.5 rounded border text-sm" style={{ borderColor: 'var(--border)', background: 'var(--bg)' }}>
                <option value="clip">截断到上限</option>
                <option value="exclude">排除出统计</option>
                <option value="keep">原样保留</option>
              </select>
            </Row>
          </Group>

          <Group title="G · 运维">
            <NumRow label="数据保留天数" v={cfg.retain_days ?? 365} onSave={n => patchCfg({ retain_days: n })}
              hint="超期的预测快照自动清理,防止表无限膨胀" />
            <NumRow label="Kronos失败重试次数" v={cfg.kronos_retry ?? 2} onSave={n => patchCfg({ kronos_retry: n })} />
            <NumRow label="Kronos超时(秒)" v={cfg.kronos_timeout ?? 300} onSave={n => patchCfg({ kronos_timeout: n })}
              hint="实测单次预测需95~150秒" />
            <NumRow label="命中率告警下限(%)" v={cfg.alert_hit_rate ?? 50} step={0.5} onSave={n => patchCfg({ alert_hit_rate: n })}
              hint="低于此值写警告日志,提示模型可能异常" />
            <Row label="模型版本">
              <span className="text-sm px-2 py-1 rounded" style={{ background: 'var(--card)' }}>{cfg.model_ver}</span>
              <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>
                改因子权重后应换版本号,否则新旧数据混在一起比无意义
              </span>
            </Row>
          </Group>

          {saving && <div className="text-sm" style={{ color: 'var(--text-muted)' }}>保存中…</div>}
        </div>
      )}
    </div>
  )
}

type StockDetail = {
  symbol: string; name: string
  runs: {
    run_date: string; signal: string; score: number | null; confidence: number | null
    last_close: number | null; factors: Record<string, number>
    preds: {
      pred_date: string; horizon: number; change_pct: number | null
      real_change?: number | null; dir_hit?: boolean; amt_hit?: boolean; abs_error?: number | null
    }[]
  }[]
  matrix: Record<string, Record<string, number | null>>
  reversals: {
    pred_date: string; prev_run: string; curr_run: string
    prev_change: number | null; curr_change: number | null; delta: number | null
    verdict: string; top_driver: string | null; driver_share: number | null
  }[]
  accuracy: { sample: number; hit_rate: number | null; amt_hit_rate: number | null; mae: number | null }
  factor_labels: Record<string, string>
}

/** 单股回测详情:近5次预测 + 实际对照 + 因子 + 反转 */
function StockDetailModal({ code, onClose }: { code: string; onClose: () => void }) {
  const [d, setD] = useState<StockDetail | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetch(`/api/backtest/stock/${code}?runs=5`, { headers: authH() })
      .then(r => r.json()).then(setD).catch(() => setD(null))
      .finally(() => setLoading(false))
  }, [code])

  const pct = (v: number | null | undefined) =>
    v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
  const clr = (v: number | null | undefined) =>
    v == null ? 'var(--text-muted)' : v > 0 ? '#dc2626' : v < 0 ? '#16a34a' : 'var(--text-muted)'

  // matrix: 目标日 × 各次预测,用于看同一天被预测了几次、是否反复
  const predDates = d ? Object.keys(d.matrix).sort() : []
  const runDates = d ? d.runs.map(r => r.run_date) : []

  return (
    <div onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: 'rgba(0,0,0,.45)' }}>
      <div onClick={e => e.stopPropagation()}
        className="rounded-2xl shadow-xl max-w-4xl w-full max-h-[85vh] overflow-y-auto"
        style={{ background: 'var(--bg)' }}>
        <div className="sticky top-0 px-5 py-4 border-b flex items-center gap-3"
          style={{ borderColor: 'var(--border)', background: 'var(--bg)' }}>
          <div>
            <h2 className="text-lg font-bold">{d?.name || code}</h2>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{code} · 回测详情</div>
          </div>
          {d?.accuracy && d.accuracy.sample > 0 && (
            <div className="flex gap-4 ml-6 text-sm">
              <div><span style={{ color: 'var(--text-muted)' }}>方向命中 </span>
                <b style={{ color: (d.accuracy.hit_rate ?? 0) > 55 ? '#16a34a' : '#dc2626' }}>
                  {d.accuracy.hit_rate ?? '--'}%</b></div>
              <div><span style={{ color: 'var(--text-muted)' }}>幅度命中 </span><b>{d.accuracy.amt_hit_rate ?? '--'}%</b></div>
              <div><span style={{ color: 'var(--text-muted)' }}>平均误差 </span><b>{d.accuracy.mae ?? '--'}pp</b></div>
              <div style={{ color: 'var(--text-muted)' }}>样本 {d.accuracy.sample}</div>
            </div>
          )}
          <button onClick={onClose} className="ml-auto p-1.5 rounded hover:bg-gray-100">✕</button>
        </div>

        <div className="p-5 space-y-6">
          {loading && <div className="py-10 text-center" style={{ color: 'var(--text-muted)' }}>加载中…</div>}
          {!loading && (!d || d.runs.length === 0) && (
            <div className="py-10 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
              这只股票还没有预测记录。点右上角「立即运行」跑一次后就有了。
            </div>
          )}

          {d && d.runs.length > 0 && (
            <>
              {/* 同一目标日的历次预测对比 —— 一眼看出模型是否反复 */}
              {predDates.length > 0 && runDates.length > 1 && (
                <div>
                  <h3 className="font-semibold text-sm mb-1">预测演变(同一天被预测了几次)</h3>
                  <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                    横向看同一个目标日在历次预测中的变化,数字跳动大说明模型反复
                  </p>
                  <div className="overflow-x-auto">
                    <table className="text-sm border rounded-lg" style={{ borderColor: 'var(--border)' }}>
                      <thead style={{ background: 'var(--card)' }}>
                        <tr>
                          <th className="p-2 text-left whitespace-nowrap">目标日</th>
                          {runDates.map(rd => (
                            <th key={rd} className="p-2 whitespace-nowrap text-xs">{rd.slice(5)} 预测</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {predDates.map(pd => (
                          <tr key={pd} className="border-t" style={{ borderColor: 'var(--border)' }}>
                            <td className="p-2 whitespace-nowrap">{pd.slice(5)}</td>
                            {runDates.map(rd => {
                              const v = d.matrix[pd]?.[rd]
                              return (
                                <td key={rd} className="p-2 text-center whitespace-nowrap"
                                  style={{ color: clr(v) }}>
                                  {v == null ? '·' : pct(v)}
                                </td>
                              )
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* 每次预测的明细 */}
              <div>
                <h3 className="font-semibold text-sm mb-2">近 {d.runs.length} 次预测明细</h3>
                <div className="space-y-3">
                  {d.runs.map(r => (
                    <div key={r.run_date} className="rounded-xl border p-3"
                      style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
                      <div className="flex items-center gap-3 mb-2 text-sm">
                        <b>{r.run_date}</b> 发起
                        <span className="px-2 py-0.5 rounded text-xs" style={{ background: 'var(--bg)' }}>{r.signal}</span>
                        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                          评分 {r.score?.toFixed(3) ?? '--'} · 置信度 {r.confidence ?? '--'}%
                          {r.last_close != null && ` · 基准价 ${r.last_close}`}
                        </span>
                      </div>
                      <table className="w-full text-sm">
                        <thead>
                          <tr style={{ color: 'var(--text-muted)' }} className="text-xs">
                            <th className="text-left py-1">目标日</th><th>第几天</th>
                            <th>预测涨跌</th><th>实际涨跌</th><th>误差</th><th>方向</th><th>幅度</th>
                          </tr>
                        </thead>
                        <tbody>
                          {r.preds.map(p => (
                            <tr key={p.pred_date} className="border-t" style={{ borderColor: 'var(--border)' }}>
                              <td className="py-1.5">{p.pred_date.slice(5)}</td>
                              <td className="text-center">T+{p.horizon}</td>
                              <td className="text-center font-medium" style={{ color: clr(p.change_pct) }}>{pct(p.change_pct)}</td>
                              <td className="text-center" style={{ color: clr(p.real_change) }}>
                                {p.real_change == null ? <span style={{ color: 'var(--text-muted)' }}>未到期</span> : pct(p.real_change)}
                              </td>
                              <td className="text-center text-xs" style={{ color: 'var(--text-muted)' }}>
                                {p.abs_error == null ? '--' : `${p.abs_error.toFixed(2)}pp`}
                              </td>
                              <td className="text-center">{p.dir_hit == null ? '--' : p.dir_hit ? '✅' : '❌'}</td>
                              <td className="text-center">{p.amt_hit == null ? '--' : p.amt_hit ? '✅' : '❌'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {/* 因子分值 */}
                      {r.factors && Object.keys(r.factors).length > 0 && (
                        <div className="mt-2 pt-2 border-t flex flex-wrap gap-2 text-xs"
                          style={{ borderColor: 'var(--border)' }}>
                          {Object.entries(r.factors).map(([k, v]) => (
                            <span key={k} className="px-2 py-0.5 rounded" style={{ background: 'var(--bg)' }}>
                              {d.factor_labels?.[k] || k}
                              <b className="ml-1" style={{ color: v > 0 ? '#dc2626' : v < 0 ? '#16a34a' : 'inherit' }}>
                                {v > 0 ? '+' : ''}{Number(v).toFixed(2)}
                              </b>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* 反转记录 */}
              {d.reversals.length > 0 && (
                <div>
                  <h3 className="font-semibold text-sm mb-2">预测变化记录</h3>
                  <div className="space-y-1.5">
                    {d.reversals.filter(r => r.verdict !== 'consistent').slice(0, 10).map((r, i) => (
                      <div key={i} className="rounded-lg border px-3 py-2 text-sm flex items-center gap-2"
                        style={{ borderColor: 'var(--border)' }}>
                        <span className="px-1.5 py-0.5 rounded text-xs" style={{
                          background: r.verdict === 'reversal' ? '#fee2e2' : 'var(--card)',
                          color: r.verdict === 'reversal' ? '#dc2626' : 'var(--text-muted)',
                        }}>
                          {{ reversal: '反转', strengthen: '强化', weaken: '弱化' }[r.verdict] || r.verdict}
                        </span>
                        <span style={{ color: 'var(--text-muted)' }}>目标 {r.pred_date.slice(5)}</span>
                        <span>{pct(r.prev_change)} → {pct(r.curr_change)}</span>
                        {r.top_driver && (
                          <span className="ml-auto text-xs" style={{ color: 'var(--text-muted)' }}>
                            主因 <b>{r.top_driver}</b> {r.driver_share?.toFixed(0)}%
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: 'var(--border)', background: 'var(--card)' }}>
      <h3 className="font-semibold text-sm mb-3">{title}</h3>
      <div className="space-y-3">{children}</div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <div className="text-sm w-44 flex-shrink-0">{label}</div>
      <div className="flex items-center">{children}</div>
    </div>
  )
}

function NumInput({ v, onSave, w = 20 }: { v: number; onSave: (n: number) => void; w?: number }) {
  const [val, setVal] = useState(String(v))
  useEffect(() => setVal(String(v)), [v])
  return (
    <input value={val} onChange={e => setVal(e.target.value)}
      onBlur={() => { const n = Number(val); if (!Number.isNaN(n) && n !== v) onSave(n) }}
      className="px-2 py-1 rounded border text-sm text-center"
      style={{ borderColor: 'var(--border)', background: 'var(--bg)', width: `${w * 4}px` }} />
  )
}

function NumRow({ label, v, onSave, hint, step }: {
  label: string; v: number; onSave: (n: number) => void; hint?: string; step?: number
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="text-sm w-44 flex-shrink-0 pt-1">{label}</div>
      <div>
        <NumInput v={v} onSave={onSave} />
        {hint && <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>{hint}</div>}
      </div>
    </div>
  )
}
