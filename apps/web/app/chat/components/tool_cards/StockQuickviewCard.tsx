'use client'
// SKILL 1 · 单股速答 · 股价卡 + AI 短评 + 3 CTA 按钮
import { useState } from 'react'
import { TrendingUp, Plus, Newspaper, Microscope } from 'lucide-react'
import { HUNTER } from '../../../lib/hunter-theme'
import { fmtPct, fmtVolume, upDnColor, goChat, toast } from './shared'

interface QuickviewData {
  type: 'stock_quickview'
  code: string
  market_suffix?: string
  market?: string
  name: string
  price: {
    current: number
    change: number
    change_pct: number
    prev_close: number
    open: number
    high: number
    low: number
    volume: number
    amount: number
  }
  range_52w: { high: number; low: number; position: number | null }
  ai_comment: string
  in_watchlist: boolean
  actions: Array<{ key: string; label: string; workflow?: string; hint?: string; prefill?: string; disabled?: boolean }>
  error?: string
}

export default function StockQuickviewCard({ data }: { data: QuickviewData }) {
  const [inWl, setInWl] = useState(data.in_watchlist)
  const [addBusy, setAddBusy] = useState(false)

  if (data.error) {
    return (
      <div style={errStyle}>
        <b>无法拉取 {data.code} 行情</b><br />
        <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{data.error}</span>
      </div>
    )
  }

  const isUp = data.price.change_pct > 0
  const chgColor = upDnColor(data.price.change_pct)

  const handleAddWatchlist = async () => {
    if (inWl || addBusy) return
    setAddBusy(true)
    try {
      const token = localStorage.getItem('hunter_token') || ''
      const market = data.market || 'A'
      const exchange = data.market_suffix || (data.code.startsWith('6') ? 'SH' : 'SZ')
      const res = await fetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: token ? `Bearer ${token}` : '' },
        body: JSON.stringify({ code: data.code, name: data.name, market, exchange, asset_type: 'stock' }),
      })
      if (res.ok) {
        setInWl(true)
        toast(`✓ 已把 ${data.name} 加入自选`, 'ok')
      } else {
        const t = await res.text().catch(() => '')
        toast(`加自选失败: ${t.slice(0, 80)}`, 'err')
      }
    } catch (e: any) {
      toast(`加自选失败: ${e?.message || e}`, 'err')
    } finally {
      setAddBusy(false)
    }
  }

  const handleAction = (key: string) => {
    const act = data.actions.find(a => a.key === key)
    if (!act) return
    if (key === 'add_watchlist') { handleAddWatchlist(); return }
    if (key === 'deep_analysis' && act.hint) { goChat(act.hint); return }
    if (key === 'view_news' && act.prefill) { goChat(act.prefill); return }
  }

  // 52 周条位置（百分比）
  const rangePos = data.range_52w.position != null
    ? Math.round(data.range_52w.position * 100)
    : null

  return (
    <div style={cardStyle}>
      {/* 头部 · 名称 + 市场 + 自选态 */}
      <div style={{ padding: '16px 18px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
          <span style={{ fontFamily: HUNTER.SERIF, fontSize: 18, fontWeight: 700 }}>{data.name}</span>
          <span style={{ fontFamily: 'ui-monospace, menlo, monospace', fontSize: 11.5, color: HUNTER.INK_F }}>
            {data.code}{data.market_suffix ? `.${data.market_suffix}` : ''}
          </span>
          {inWl && (
            <span style={{
              marginLeft: 'auto', fontSize: 10.5, padding: '2px 8px',
              background: '#eef5f1', color: '#2c6b55', borderRadius: 999, fontWeight: 600,
            }}>
              ✓ 已在自选
            </span>
          )}
        </div>

        {/* 价格行 */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12 }}>
          <span style={{ fontFamily: HUNTER.SERIF, fontSize: 28, fontWeight: 700 }}>
            ¥{data.price.current.toFixed(2)}
          </span>
          <span style={{ fontSize: 15, fontWeight: 700, color: chgColor }}>
            {data.price.change >= 0 ? '+' : ''}{data.price.change.toFixed(2)} ({fmtPct(data.price.change_pct)})
          </span>
          <span style={{ color: chgColor, fontSize: 14 }}>{isUp ? '↑' : '↓'}</span>
        </div>

        {/* 4 列指标 */}
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 6, fontSize: 11.5, color: HUNTER.INK_F,
        }}>
          <div>开 <b style={{ color: HUNTER.INK, fontVariantNumeric: 'tabular-nums' }}>{data.price.open.toFixed(2)}</b></div>
          <div>高 <b style={{ color: HUNTER.INK, fontVariantNumeric: 'tabular-nums' }}>{data.price.high.toFixed(2)}</b></div>
          <div>低 <b style={{ color: HUNTER.INK, fontVariantNumeric: 'tabular-nums' }}>{data.price.low.toFixed(2)}</b></div>
          <div>量 <b style={{ color: HUNTER.INK, fontVariantNumeric: 'tabular-nums' }}>{fmtVolume(data.price.volume)}</b></div>
        </div>

        {/* 52 周区间 */}
        {rangePos != null && (
          <div style={{
            marginTop: 12, padding: '8px 12px', background: HUNTER.PAPER2,
            borderRadius: 8, fontSize: 11,
          }}>
            <div style={{ color: HUNTER.INK_F, marginBottom: 6 }}>
              52 周区间 · 当前位于 <b style={{ color: HUNTER.INK }}>{rangePos}%</b> 分位
            </div>
            <div style={{
              height: 4, background: 'linear-gradient(90deg,#3f6b40 0%,#e5deca 50%,#a4332b 100%)',
              borderRadius: 2, position: 'relative',
            }}>
              <span style={{
                position: 'absolute', top: -3, left: `${rangePos}%`,
                width: 10, height: 10, marginLeft: -5,
                borderRadius: '50%', background: HUNTER.THEME,
                border: '2px solid #fff', boxShadow: '0 1px 4px rgba(0,0,0,.15)',
              }} />
            </div>
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              color: HUNTER.INK_F, fontFamily: 'ui-monospace, menlo, monospace', marginTop: 3,
            }}>
              <span>{data.range_52w.low.toFixed(2)}</span>
              <span>{data.range_52w.high.toFixed(2)}</span>
            </div>
          </div>
        )}
      </div>

      {/* AI 短评 */}
      {data.ai_comment && (
        <div style={{
          padding: '12px 18px',
          background: 'linear-gradient(180deg, #f6eee7 0%, #fff 100%)',
          borderTop: `1px solid ${HUNTER.PAPER2}`,
          fontSize: 13, lineHeight: 1.7, color: HUNTER.INK,
        }}>
          <b style={{ color: HUNTER.COPPER3 }}>💬 AI 短评</b> · {data.ai_comment}
        </div>
      )}

      {/* 3 按钮 CTA */}
      <div style={{
        padding: '12px 18px', borderTop: `1px solid ${HUNTER.LINE}`,
        display: 'flex', gap: 8, background: HUNTER.PAPER,
      }}>
        <button style={btnPrimary} onClick={() => handleAction('deep_analysis')}>
          <Microscope size={14} /> 深度分析
        </button>
        <button
          style={inWl || addBusy ? { ...btn, opacity: 0.5, cursor: 'not-allowed' } : btn}
          onClick={() => handleAction('add_watchlist')}
          disabled={inWl || addBusy}
        >
          {inWl ? '✓ 已加自选' : addBusy ? '加中...' : <><Plus size={14} /> 加自选</>}
        </button>
        <button style={btn} onClick={() => handleAction('view_news')}>
          <Newspaper size={14} /> 查看新闻
        </button>
      </div>
    </div>
  )
}

const cardStyle: React.CSSProperties = {
  margin: '12px 0',
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 14,
  overflow: 'hidden',
  boxShadow: '0 4px 18px rgba(40,35,27,.04)',
}
const btn: React.CSSProperties = {
  flex: 1, height: 36, border: `1px solid ${HUNTER.LINE}`, borderRadius: 10,
  background: '#fff', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  gap: 6, fontSize: 13, fontWeight: 600, color: HUNTER.INK, cursor: 'pointer',
  fontFamily: 'inherit',
}
const btnPrimary: React.CSSProperties = {
  ...btn, background: HUNTER.THEME, color: '#fff', borderColor: HUNTER.THEME,
  boxShadow: '0 4px 12px rgba(181,107,45,.2)',
}
const errStyle: React.CSSProperties = {
  margin: '12px 0', padding: '14px 18px',
  background: '#fbeaea', border: '1px solid #f3c9c9', borderRadius: 10,
  fontSize: 13, color: HUNTER.UP,
}
