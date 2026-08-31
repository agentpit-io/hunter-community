'use client'

/**
 * 概率校准 · /calibration?symbol=600519.SH
 *
 * 复赛评委四项建议之三。三个小框里最后一个占位符,补上。
 *
 * ## 校准是什么,为什么值得单独一页
 *
 * 「命中率」只回答"猜对了几次";**校准回答"说 70% 把握的时候,是不是真有 70%"**。
 * 一个模型可以命中率不低但严重过度自信 —— 每次都喊 90%,实际只对 60%。
 * 拿它做仓位管理会亏得莫名其妙,因为你信的是那个 90%。
 *
 * 三个指标:
 *   Brier   均方概率误差 · 0=完美 · 0.25=抛硬币 · 越低越好
 *   ECE     各概率桶「说的」与「实际的」平均差距 · 越低越校准
 *   可靠性曲线  贴对角线 = 校准好 · 落在线下方 = 过度自信
 *
 * ## 为什么曲线是手写 SVG 而不是图表库
 *
 * 就一条折线加一条对角线,上 recharts/echarts 要多打 500KB。
 * 而且这页要能在评委的网络下秒开。
 *
 * ## 不粉饰
 *
 * 演示数据的 Brier ≈ 0.30(比抛硬币还差一点)· ECE ≈ 0.22。
 * 这个数字很难看,但**照实显示**,并在页面上直说难看在哪 ——
 * 因为这一页的价值恰恰是"能测出模型不行",藏起来就没有意义了。
 * seed 数据本来就是"真实历史 + 高斯噪声",测出接近随机才是对的。
 */

import { useEffect, useState, useCallback, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import TopNav from '../components/TopNav'
import { HUNTER } from '../lib/hunter-theme'

type Bin = { bin: [number, number]; avg_pred: number; freq: number; n: number }
type Report = {
  sample_size: number
  brier: number | null
  ece: number | null
  reliability: Bin[] | null
  window_days: number
  symbol: string | null
  note: string | null
  prob_model?: string
}
type Interval = { p80: [number, number]; p95: [number, number]; sample: number; residual_std: number }

function CalInner() {
  const sp = useSearchParams()
  const symbol = sp.get('symbol') || ''

  const [days, setDays] = useState(90)
  const [rep, setRep] = useState<Report | null>(null)
  const [itv, setItv] = useState<Interval | null>(null)
  const [itvMsg, setItvMsg] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const auth = useCallback((): Record<string, string> => {
    try {
      const t = localStorage.getItem('hunter_token')
      return t ? { Authorization: `Bearer ${t}` } : {}
    } catch { return {} }
  }, [])

  useEffect(() => {
    setLoading(true); setErr('')
    const q = new URLSearchParams({ days: String(days) })
    if (symbol) q.set('symbol', symbol)
    fetch(`/api/backtest/calibration?${q}`, { headers: auth() })
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status} · ${(await r.text()).slice(0, 120)}`)
        return r.json()
      })
      .then(setRep)
      .catch(e => setErr(String(e?.message || e)))
      .finally(() => setLoading(false))

    if (!symbol) { setItv(null); return }
    fetch(`/api/backtest/interval/${encodeURIComponent(symbol)}?days=${days}`, { headers: auth() })
      .then(async r => {
        if (r.status === 404) { setItvMsg('该股历史预测样本不足 30 条,给不出区间'); return null }
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => { if (d) { setItv(d); setItvMsg('') } })
      .catch(() => setItvMsg('区间数据暂不可用'))
  }, [days, symbol, auth])

  const enough = rep && rep.sample_size >= 30 && rep.reliability

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.PAPER }}>
      <TopNav />
      <main style={{ maxWidth: 820, margin: '0 auto', padding: '24px 20px 60px' }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, color: HUNTER.INK, marginBottom: 4 }}>
          概率校准
        </h1>
        <p style={{ fontSize: 12.5, color: HUNTER.INK_F, lineHeight: 1.85, marginBottom: 16 }}>
          命中率只说「猜对了几次」;校准说的是
          <b style={{ color: HUNTER.INK_S }}>「模型说有七成把握时,是不是真有七成」</b>。
          {symbol ? <> 当前标的 <b>{symbol}</b>。</> : <> 当前为全样本(未指定标的)。</>}
        </p>

        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {[30, 90, 180, 365].map(d => (
            <button key={d} onClick={() => setDays(d)} style={{
              padding: '5px 12px', fontSize: 12.5, borderRadius: 8, cursor: 'pointer',
              fontFamily: 'inherit',
              border: `1px solid ${days === d ? HUNTER.THEME : HUNTER.LINE}`,
              background: days === d ? HUNTER.BRAND_PALE : '#fff',
              color: days === d ? HUNTER.COPPER3 : HUNTER.INK_S,
            }}>近 {d} 天</button>
          ))}
        </div>

        {loading && <div style={muted}>加载中…</div>}
        {err && (
          <div style={{ ...cardS, borderColor: '#E0B4A4', color: '#B4472A', fontSize: 13 }}>
            拿不到校准数据 · {err}
          </div>
        )}

        {rep && !enough && !err && (
          <div style={{ ...cardS, fontSize: 13, color: '#8A5A1B', lineHeight: 1.9 }}>
            <b>样本不足,不出结论。</b><br />
            {rep.note || `近 ${days} 天只有 ${rep.sample_size} 条已验证预测,少于 30 条。`}
            <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 8 }}>
              校准指标在小样本上会剧烈波动 —— 20 条数据算出的 ECE 基本是噪声。
              这里宁可显示「不知道」,也不给一个看着像结论的数字。
            </div>
          </div>
        )}

        {enough && rep && (
          <>
            <section style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
              <Metric title="Brier 分数" v={rep.brier} note="越低越好 · 0.25=抛硬币"
                      bad={(rep.brier ?? 0) >= 0.25} />
              <Metric title="ECE 校准误差" v={rep.ece} note="越低越校准 · <0.05 算好"
                      bad={(rep.ece ?? 0) >= 0.10} />
              <Metric title="样本量" v={rep.sample_size} raw
                      note={`近 ${rep.window_days} 天已验证预测`} />
            </section>

            <section style={cardS}>
              <div style={label}>可靠性曲线</div>
              <p style={{ fontSize: 12, color: HUNTER.INK_F, lineHeight: 1.85, margin: '6px 0 10px' }}>
                横轴 = 模型说的概率,纵轴 = 实际发生的频率。
                <b style={{ color: HUNTER.INK_S }}>贴住虚线对角线 = 校准良好</b>;
                落在对角线下方 = 过度自信(说得比做到的多)。
              </p>
              <Reliability bins={rep.reliability!} />
            </section>

            {(rep.brier ?? 0) >= 0.25 && (
              <div style={{ ...cardS, borderColor: '#E3C89A', background: '#FDF8EE' }}>
                <div style={{ fontSize: 13, color: '#8A5A1B', lineHeight: 1.9 }}>
                  <b>这组数字很难看 —— 照实显示。</b><br />
                  Brier {rep.brier} ≥ 0.25,意味着在当前样本上,概率输出
                  <b>不比抛硬币更有信息量</b>。
                  演示数据本来就是「真实行情 + 高斯噪声」合成的,测出接近随机符合预期。
                  <div style={{ marginTop: 8, fontSize: 12, color: HUNTER.INK_F }}>
                    这一页的价值不是证明模型好,而是
                    <b style={{ color: HUNTER.INK_S }}>能把不好测出来</b> ——
                    换成真实模型跑同一套指标,这个数字才有意义。
                  </div>
                </div>
              </div>
            )}

            {rep.prob_model && (
              <div style={{ fontSize: 11.5, color: HUNTER.INK_F, lineHeight: 1.8, marginTop: 4 }}>
                概率化方式:{rep.prob_model}
              </div>
            )}
          </>
        )}

        {symbol && (
          <section style={cardS}>
            <div style={label}>预测区间 · {symbol}</div>
            {itv ? (
              <div style={{ fontSize: 13, color: HUNTER.INK_S, lineHeight: 2, marginTop: 6 }}>
                <div>80% 区间 <b style={{ color: HUNTER.INK }}>
                  {itv.p80[0].toFixed(2)}% ~ {itv.p80[1].toFixed(2)}%</b></div>
                <div>95% 区间 <b style={{ color: HUNTER.INK }}>
                  {itv.p95[0].toFixed(2)}% ~ {itv.p95[1].toFixed(2)}%</b></div>
                <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 6 }}>
                  基于 {itv.sample} 条历史预测的残差分位 · 残差标准差 {itv.residual_std}%。
                  区间用<b>经验分位</b>算,不假设正态分布。
                </div>
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 6 }}>
                {itvMsg || '加载中…'}
              </div>
            )}
          </section>
        )}

        <div style={{ fontSize: 11.5, color: HUNTER.INK_F, lineHeight: 1.9, marginTop: 18 }}>
          原始数据可直接验:<br />
          <code style={code}>GET /api/backtest/calibration?days={days}{symbol ? `&symbol=${symbol}` : ''}</code>
          {symbol && <><br /><code style={code}>GET /api/backtest/interval/{symbol}</code></>}
          <div style={{ marginTop: 8 }}>
            以上为历史统计,不预示未来表现,不构成投资建议。
          </div>
        </div>
      </main>
    </div>
  )
}

/** 可靠性图 · 手写 SVG(见文件头:不为一条折线引图表库) */
function Reliability({ bins }: { bins: Bin[] }) {
  const W = 380, H = 300, P = 38
  const x = (v: number) => P + v * (W - P - 12)
  const y = (v: number) => H - P - v * (H - P - 12)
  const pts = bins.filter(b => b.n > 0)
  const maxN = Math.max(...pts.map(b => b.n), 1)

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={W} height={H} style={{ display: 'block' }}>
        {[0, 0.25, 0.5, 0.75, 1].map(g => (
          <g key={g}>
            <line x1={x(0)} y1={y(g)} x2={x(1)} y2={y(g)} stroke={HUNTER.LINE} strokeWidth={1} />
            <text x={x(0) - 6} y={y(g) + 4} textAnchor="end"
                  fontSize={10} fill={HUNTER.INK_F}>{g}</text>
            <text x={x(g)} y={H - P + 14} textAnchor="middle"
                  fontSize={10} fill={HUNTER.INK_F}>{g}</text>
          </g>
        ))}
        {/* 完美校准的参照线 */}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)}
              stroke={HUNTER.INK_F} strokeWidth={1.2} strokeDasharray="4 4" />
        {/* 实测曲线 */}
        <polyline fill="none" stroke={HUNTER.THEME} strokeWidth={2}
                  points={pts.map(b => `${x(b.avg_pred)},${y(b.freq)}`).join(' ')} />
        {/* 点 · 半径反映桶内样本量 */}
        {pts.map((b, i) => (
          <circle key={i} cx={x(b.avg_pred)} cy={y(b.freq)}
                  r={3 + 4 * Math.sqrt(b.n / maxN)}
                  fill={HUNTER.THEME} fillOpacity={0.75}>
            <title>{`模型说 ${(b.avg_pred * 100).toFixed(0)}% · 实际 ${(b.freq * 100).toFixed(0)}% · ${b.n} 条`}</title>
          </circle>
        ))}
        <text x={W / 2} y={H - 6} textAnchor="middle" fontSize={10.5} fill={HUNTER.INK_F}>
          模型给出的上涨概率
        </text>
        <text x={12} y={H / 2} textAnchor="middle" fontSize={10.5} fill={HUNTER.INK_F}
              transform={`rotate(-90 12 ${H / 2})`}>实际上涨频率</text>
      </svg>
      <div style={{ fontSize: 11.5, color: HUNTER.INK_F, marginTop: 4 }}>
        圆点大小 = 该桶样本量 · 悬停看明细
      </div>
    </div>
  )
}

function Metric({ title, v, note, bad, raw }: {
  title: string; v: number | null; note: string; bad?: boolean; raw?: boolean
}) {
  return (
    <div style={{
      flex: '1 1 180px', padding: '12px 14px', background: '#fff',
      border: `1px solid ${bad ? '#E3C89A' : HUNTER.LINE}`, borderRadius: 10,
    }}>
      <div style={{ fontSize: 11.5, color: HUNTER.INK_F }}>{title}</div>
      <div style={{
        fontSize: 22, fontWeight: 600, marginTop: 2,
        color: bad ? '#B4772A' : HUNTER.INK,
      }}>{v == null ? '—' : raw ? v : v.toFixed(4)}</div>
      <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2 }}>{note}</div>
    </div>
  )
}

const cardS: React.CSSProperties = {
  background: '#fff', border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 10, padding: '14px 16px', marginBottom: 14,
}
const label: React.CSSProperties = {
  fontSize: 12.5, fontWeight: 600, color: HUNTER.INK_S,
}
const muted: React.CSSProperties = { fontSize: 13, color: HUNTER.INK_F, padding: '20px 0' }
const code: React.CSSProperties = {
  background: '#F4F1EC', padding: '2px 6px', borderRadius: 4,
  fontSize: 11, color: HUNTER.INK_S,
}

export default function Page() {
  return (
    <Suspense fallback={<div style={{ padding: 40, fontSize: 13 }}>加载中…</div>}>
      <CalInner />
    </Suspense>
  )
}
