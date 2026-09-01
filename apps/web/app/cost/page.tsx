'use client'

/**
 * 单股交易成本测算 · /cost?symbol=600519
 *
 * 复赛评委四项建议之二「交易成本」的单股入口。
 *
 * ## 为什么要单独建这个页,而不是链到回测页
 *
 * 自选股卡片上的「💰 交易成本」原本链到 /strategies/backtest.html ——
 * **那是组合回测,不是这只股票的交易成本**:
 *
 *     评委要的     这只票 · 我持仓 N 手 · 买卖一趟付多少钱
 *     回测页给的   整个策略组合 · 选因子选股票池 · 毛净收益曲线
 *
 * 完全是两回事。链过去用户会一脸茫然。
 *
 * ## 设计:先要手数,再谈成本
 *
 * 不填手数的话这一页只能显示「A股单向 10.6bps」这种抽象费率 ——
 * 用户看不懂,也不关心。
 *
 * 填了手数就能算出「你这 2 手真要花 27.5 元」—— 一个抽象参数
 * 变成跟他自己的钱有关的数字。**这是这一页存在的全部理由。**
 *
 * 手数存在 stocks.shares(0007 迁移加的列),下次进来自动带出。
 */

import { useEffect, useState, useCallback, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import TopNav from '../components/TopNav'
import ComplianceWatermark from '../components/ComplianceWatermark'
import { HUNTER } from '../lib/hunter-theme'

type Preset = {
  key: string
  label: string
  market: string
  lot_size: number
  notes: string
  total_bps_per_side: number
  total_bps_round_trip: number
  breakdown: Record<string, number>
  buy: Record<string, number>
  sell: Record<string, number>
}

function CostInner() {
  const sp = useSearchParams()
  const symbol = sp.get('symbol') || ''

  const [presets, setPresets] = useState<Record<string, Preset>>({})
  const [presetKey, setPresetKey] = useState('cn_default')
  const [shares, setShares] = useState<number>(0)
  const [avgCost, setAvgCost] = useState<number>(0)   // 买入均价 · 0 = 未填
  const [price, setPrice] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [err, setErr] = useState('')

  const auth = useCallback((): Record<string, string> => {
    try {
      const t = localStorage.getItem('hunter_token')
      return t ? { Authorization: `Bearer ${t}` } : {}
    } catch { return {} }
  }, [])

  useEffect(() => {
    fetch('/api/quant/broker/presets', { headers: auth() })
      .then(r => r.json())
      .then(d => {
        // ⚠ API 返回的是**数组**不是字典(实测 {"presets":[{key:...},...])。
        // 按字典取会拿到 undefined,而页面不会报错 —— 只是费率区一片空白。
        const raw = d?.presets ?? d
        const map: Record<string, Preset> = Array.isArray(raw)
          ? Object.fromEntries(raw.map((x: Preset) => [x.key, x]))
          : (raw || {})
        setPresets(map)
        // A 股代码默认选 cn · 5 位数字是港股 · 含字母是美股
        const bare = symbol.split('.')[0]
        if (/^\d{5}$/.test(bare)) setPresetKey('hk_default')
        else if (/[A-Za-z]/.test(bare)) setPresetKey('us_default')
      })
      .catch(e => setErr(String(e?.message || e)))

    if (!symbol) return
    fetch(`/api/quote/${encodeURIComponent(symbol)}`, { headers: auth() })
      .then(r => r.json())
      .then(q => { if (q?.price != null) { setPrice(q.price); setName(q.name || '') } })
      .catch(() => {})

    fetch('/api/stocks', { headers: auth() })
      .then(r => r.json())
      .then(d => {
        const s = (d?.stocks || []).find((x: any) => x.code === symbol)
        if (s?.shares) setShares(s.shares)
        if (s?.avg_cost) setAvgCost(s.avg_cost)
        if (s?.name && !name) setName(s.name)
      })
      .catch(() => {})
  }, [symbol, auth])   // eslint-disable-line react-hooks/exhaustive-deps

  const p = presets[presetKey]
  const lot = p?.lot_size || 100
  // 市值 = 手数 × 每手股数 × 价格
  const notional = shares && price ? shares * lot * price : 0
  const bps = (v: number) => (notional * v) / 10000

  // ── 有买入价时才算得出「这笔到底赚没赚」──────────────────
  //
  // 只有手数,页面能回答的最多是"买卖一趟花 27.5 元" —— 用户看完
  // 仍不知道这 27.5 元疼不疼。有了买入价,三个真正有用的数才出得来:
  //
  //   净盈亏  = 毛盈亏 − 买入当时已付的成本 − 现在卖出还要付的成本
  //   保本价  = 涨到多少才真回本(把往返成本折进价格)
  //   成本吃掉了盈利的百分之多少
  //
  // 这就是评委那句「交易成本」的毛/净框架,只不过落在用户自己的持仓上。
  const qty = shares * lot
  const hasPos = qty > 0 && avgCost > 0 && price != null && p != null
  // 买入时按买入价计的市值(成本要按当时的金额算,不是按现价)
  const buyNotional = hasPos ? qty * avgCost : 0
  const buyCost = hasPos ? (buyNotional * p!.total_bps_per_side) / 10000 : 0
  const sellCost = hasPos ? (qty * price! * (p!.total_bps_round_trip - p!.total_bps_per_side)) / 10000 : 0
  const grossPnl = hasPos ? qty * (price! - avgCost) : 0
  const netPnl = grossPnl - buyCost - sellCost
  // 保本价:卖出价 × (1 - 卖出费率) = 买入价 × (1 + 买入费率)
  const buyFee = p ? p.total_bps_per_side / 10000 : 0
  const sellFee = p ? (p.total_bps_round_trip - p.total_bps_per_side) / 10000 : 0
  const breakEven = hasPos ? (avgCost * (1 + buyFee)) / (1 - sellFee) : 0
  const money = (v: number) =>
    `${v < 0 ? '-' : ''}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`

  const saveShares = async () => {
    if (!symbol) return
    setSaving(true); setSavedMsg('')
    try {
      const r = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/shares`, {
        method: 'PATCH',
        headers: { ...auth(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ shares, avg_cost: avgCost || 0 }),
      })
      setSavedMsg(r.ok ? '已保存' : `保存失败 HTTP ${r.status}`)
    } catch (e: any) {
      setSavedMsg(`保存失败 · ${e?.message || e}`)
    }
    setSaving(false)
  }

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.PAPER }}>
      <TopNav />
      <main style={{ maxWidth: 760, margin: '0 auto', padding: '24px 20px 60px' }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, color: HUNTER.INK, marginBottom: 4 }}>
          交易成本测算
        </h1>
        <p style={{ fontSize: 12.5, color: HUNTER.INK_F, lineHeight: 1.8, marginBottom: 18 }}>
          {symbol
            ? <>标的 <b>{name || symbol}</b>({symbol}){price != null && <> · 最新 <b>{price.toFixed(2)}</b></>}</>
            : <>没指定标的 —— 从自选股卡片的「💰 交易成本」进来会自动带上。</>}
        </p>

        {err && (
          <div style={box('#B4472A')}>拿不到券商费率参数 · {err}</div>
        )}

        {/* 手数 —— 这一页的核心输入 */}
        <section style={card}>
          <div style={label}>① 你打算买卖多少</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
            <input
              type="number" min={0} value={shares || ''}
              onChange={e => { setShares(Math.max(0, Number(e.target.value) || 0)); setSavedMsg('') }}
              placeholder="0"
              style={{
                width: 110, padding: '7px 10px', fontSize: 15, fontWeight: 600,
                border: `1px solid ${HUNTER.LINE}`, borderRadius: 8, color: HUNTER.INK,
              }} />
            <span style={{ fontSize: 13, color: HUNTER.INK_S }}>手 · 每手 {lot} 股</span>
            <span style={{ fontSize: 13, color: HUNTER.INK_S, marginLeft: 4 }}>买入价</span>
            <input
              type="number" min={0} step="0.01" value={avgCost || ''}
              onChange={e => { setAvgCost(Math.max(0, Number(e.target.value) || 0)); setSavedMsg('') }}
              placeholder="选填"
              style={{
                width: 96, padding: '7px 10px', fontSize: 15, fontWeight: 600,
                border: `1px solid ${HUNTER.LINE}`, borderRadius: 8, color: HUNTER.INK,
              }} />
            {symbol && (
              <>
                <button onClick={saveShares} disabled={saving}
                  style={btn}>{saving ? '保存中…' : '保存到自选'}</button>
                <span style={{ fontSize: 12, color: HUNTER.INK_F }}>{savedMsg}</span>
              </>
            )}
          </div>
          {notional > 0 && (
            <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 8 }}>
              名义市值 <b style={{ color: HUNTER.INK }}>
                {notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </b> 元 = {shares} 手 × {lot} 股 × {price?.toFixed(2)}
            </div>
          )}
          {!price && symbol && (
            <div style={{ fontSize: 12, color: '#8A5A1B', marginTop: 8 }}>
              拿不到 {symbol} 的最新价,下面只能显示费率,算不出金额。
            </div>
          )}
        </section>

        {/* 费率档位 */}
        <section style={card}>
          <div style={label}>② 按哪档费率算</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            {Object.values(presets).map(x => (
              <button key={x.key} onClick={() => setPresetKey(x.key)}
                style={{
                  padding: '6px 12px', fontSize: 12.5, borderRadius: 8, cursor: 'pointer',
                  fontFamily: 'inherit',
                  border: `1px solid ${presetKey === x.key ? HUNTER.THEME : HUNTER.LINE}`,
                  background: presetKey === x.key ? HUNTER.BRAND_PALE : '#fff',
                  color: presetKey === x.key ? HUNTER.COPPER3 : HUNTER.INK_S,
                }}>{x.label}</button>
            ))}
          </div>
          {p && (
            <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 8, lineHeight: 1.8 }}>
              {p.notes}
            </div>
          )}
        </section>

        {/* 结果 */}
        {p && (
          <section style={card}>
            <div style={label}>③ 这笔要付多少</div>
            {notional > 0 ? (
              <>
                <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
                  <Big title="买入成本" v={bps(p.total_bps_per_side)} pct={p.total_bps_per_side} />
                  <Big title="往返成本(买+卖)" v={bps(p.total_bps_round_trip)}
                       pct={p.total_bps_round_trip} strong />
                </div>
                <table style={{ width: '100%', marginTop: 16, fontSize: 12.5, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: HUNTER.INK_F }}>
                      <th style={th}>项目</th><th style={th}>买入</th><th style={th}>卖出</th><th style={th}>合计</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ['佣金', 'commission'], ['印花税', 'stamp_tax'],
                      ['滑点', 'slippage'], ['其它(过户等)', 'other'],
                    ].map(([cn, k]) => {
                      const b = p.buy?.[k] || 0, s2 = p.sell?.[k] || 0
                      if (!b && !s2) return null
                      return (
                        <tr key={k} style={{ borderTop: `1px solid ${HUNTER.LINE}` }}>
                          <td style={td}>{cn}</td>
                          <td style={td}>{b ? `${bps(b).toFixed(2)} 元 (${b}bps)` : '—'}</td>
                          <td style={td}>{s2 ? `${bps(s2).toFixed(2)} 元 (${s2}bps)` : '—'}</td>
                          <td style={{ ...td, fontWeight: 600 }}>{(bps(b) + bps(s2)).toFixed(2)} 元</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 12, lineHeight: 1.9 }}>
                  往返成本占市值 <b>{(p.total_bps_round_trip / 100).toFixed(2)}%</b> ——
                  也就是说这只票<b>至少要涨 {(p.total_bps_round_trip / 100).toFixed(2)}%</b> 你才回本。
                  <br />
                  本页滑点按静态 {p.breakdown?.slippage || 0}bps 估。
                  大单的实际冲击更高 —— 策略回测那边支持
                  <b>平方根冲击模型(sqrt_impact)</b>,按下单额占日均成交额的比例
                  逐笔计算,已计入净收益。
                  <br />
                  以上按 <b>{p.label}</b> 档费率扣除;
                  <b>实盘可能因流动性与成交时机与此不同</b>。
                </div>
                {/* 合规第 2 层 · 报告卡水印 */}
                <ComplianceWatermark compact />
              </>
            ) : (
              <div style={{ fontSize: 13, color: HUNTER.INK_F, padding: '12px 0' }}>
                填上手数就能算出具体金额。现在这档费率是
                <b> 单向 {p.total_bps_per_side}bps · 往返 {p.total_bps_round_trip}bps</b>。
              </div>
            )}
          </section>
        )}

        <p style={{ fontSize: 11.5, color: HUNTER.INK_F, marginTop: 18, lineHeight: 1.9 }}>
          费率参数对标 2026 年现行监管规则,但**各家券商佣金有差异** ——
          实际以你的开户券商为准。此页仅供测算,不构成投资建议。
        </p>
        {/* ④ 有买入价才出现 · 这一页真正的落点 */}
        {hasPos && (
          <section style={card}>
            <div style={label}>④ 这笔现在赚没赚(扣完成本)</div>
            <div style={{ display: 'flex', gap: 14, marginTop: 10, flexWrap: 'wrap' }}>
              <PnlBox title="毛盈亏" v={grossPnl} sub="只看股价涨跌" money={money} />
              <PnlBox title="净盈亏" v={netPnl} strong
                      sub="扣掉买入已付 + 卖出将付" money={money} />
            </div>
            <div style={{ fontSize: 12.5, color: HUNTER.INK_F, marginTop: 14, lineHeight: 2 }}>
              买入 {shares} 手 × {lot} 股 @ <b>{avgCost.toFixed(2)}</b>,
              现价 <b>{price!.toFixed(2)}</b>
              <br />
              买入时已付 <b>{money(buyCost)}</b> 元 · 现在卖出还要付 <b>{money(sellCost)}</b> 元
              <br />
              <b style={{ color: HUNTER.INK }}>
                保本价 {breakEven.toFixed(2)}
              </b>
              {price! >= breakEven
                ? <>(现价已过保本线)</>
                : <>(还差 {((breakEven - price!) / price! * 100).toFixed(2)}%)</>}
              {grossPnl > 0 && (
                <><br />成本吃掉了这笔毛利的 <b>
                  {((buyCost + sellCost) / grossPnl * 100).toFixed(1)}%
                </b></>
              )}
            </div>
            <ComplianceWatermark compact />
          </section>
        )}

      </main>
    </div>
  )
}

/** 盈亏数字 · A 股口径红涨绿跌 */
function PnlBox({ title, v, sub, strong, money }: {
  title: string; v: number; sub: string; strong?: boolean
  money: (n: number) => string
}) {
  const c = v > 0 ? '#C0392B' : v < 0 ? '#1E8449' : HUNTER.INK
  return (
    <div style={{
      flex: '1 1 200px', padding: '12px 14px', borderRadius: 10,
      border: `1px solid ${strong ? HUNTER.THEME : HUNTER.LINE}`,
      background: strong ? HUNTER.BRAND_PALE : '#fff',
    }}>
      <div style={{ fontSize: 11.5, color: HUNTER.INK_F }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: c, marginTop: 2 }}>
        {v > 0 ? '+' : ''}{money(v)} 元
      </div>
      <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2 }}>{sub}</div>
    </div>
  )
}

function Big({ title, v, pct, strong }: { title: string; v: number; pct: number; strong?: boolean }) {
  return (
    <div style={{
      flex: '1 1 200px', padding: '12px 14px', borderRadius: 10,
      background: strong ? HUNTER.BRAND_PALE : HUNTER.PAPER2,
      border: `1px solid ${strong ? HUNTER.THEME : HUNTER.LINE}`,
    }}>
      <div style={{ fontSize: 11.5, color: HUNTER.INK_F }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: HUNTER.INK, marginTop: 2 }}>
        {v.toFixed(2)} <span style={{ fontSize: 13, fontWeight: 400 }}>元</span>
      </div>
      <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2 }}>{pct} bps</div>
    </div>
  )
}

const card: React.CSSProperties = {
  background: '#fff', border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 12, padding: '14px 16px 22px', marginBottom: 12,
  // ComplianceWatermark 是 absolute 定位 · 没有 relative 父元素会飘到 body 角上
  position: 'relative',
}
const label: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: HUNTER.INK }
const th: React.CSSProperties = { textAlign: 'left', padding: '4px 6px', fontWeight: 500 }
const td: React.CSSProperties = { padding: '6px', color: HUNTER.INK_S }
const btn: React.CSSProperties = {
  padding: '6px 12px', fontSize: 12.5, borderRadius: 8, cursor: 'pointer',
  border: `1px solid ${HUNTER.THEME}`, background: HUNTER.THEME, color: '#fff',
  fontFamily: 'inherit',
}
function box(color: string): React.CSSProperties {
  return {
    padding: '10px 12px', borderRadius: 8, marginBottom: 12,
    border: `1px solid ${color}`, color, fontSize: 12.5, background: '#fff',
  }
}

export default function CostPage() {
  return (
    <Suspense fallback={null}>
      <CostInner />
    </Suspense>
  )
}
