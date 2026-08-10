'use client'
// SKILL 3 · 自选股日报 · Top3 涨/跌 + AI 归因
import { TrendingUp, TrendingDown, Microscope } from 'lucide-react'
import { HUNTER } from '../../../lib/hunter-theme'
import { fmtPct, upDnColor, goChat } from './shared'

interface StockCard {
  code: string
  name: string
  market_suffix?: string
  price: number
  change_pct: number
  attribution: string
  signals: string[]
}

interface DigestData {
  type: 'watchlist_digest'
  total_count?: number
  up_count?: number
  down_count?: number
  flat_count?: number
  avg_pct?: number
  top_gainers?: StockCard[]
  top_losers?: StockCard[]
  ai_summary?: string
  empty?: boolean
  hint?: string
  error?: string
}

export default function WatchlistDigestCard({ data }: { data: DigestData }) {
  if (data.error) {
    return (
      <div style={errStyle}>
        <b>⚠ 自选股日报生成失败</b><br />
        <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{data.error}</span>
      </div>
    )
  }
  if (data.empty) {
    return (
      <div style={cardStyle}>
        <div style={{
          padding: '32px 24px', textAlign: 'center',
        }}>
          <div style={{ fontSize: 30, marginBottom: 8, opacity: 0.6 }}>📊</div>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
            还没自选股
          </div>
          <div style={{ color: HUNTER.INK_F, fontSize: 12.5, marginBottom: 16, lineHeight: 1.65 }}>
            {data.hint || '去自选股页面添加 3 只股票，再来看日报'}
          </div>
          <a
            href="/watchlist"
            style={{
              display: 'inline-block', padding: '8px 18px',
              background: HUNTER.THEME, color: '#fff',
              borderRadius: 10, fontSize: 13, fontWeight: 600, textDecoration: 'none',
            }}
          >
            → 去自选股页
          </a>
        </div>
      </div>
    )
  }

  const avg = data.avg_pct || 0
  const avgColor = upDnColor(avg)

  return (
    <div style={cardStyle}>
      {/* 头部 · 汇总 */}
      <div style={{
        padding: '16px 18px',
        background: 'linear-gradient(180deg, #fff9f0 0%, #fff 100%)',
        borderBottom: `1px solid ${HUNTER.LINE}`,
      }}>
        <div style={{ fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, marginBottom: 8 }}>
          📊 自选股日报
        </div>
        <div style={{
          display: 'flex', gap: 14, fontSize: 12,
          color: HUNTER.INK_F, flexWrap: 'wrap',
        }}>
          <span><b style={{ color: HUNTER.INK }}>{data.total_count}</b> 只</span>
          <span><b style={{ color: HUNTER.UP }}>↑ {data.up_count}</b></span>
          <span><b style={{ color: HUNTER.DN }}>↓ {data.down_count}</b></span>
          {data.flat_count! > 0 && <span><b>→ {data.flat_count}</b></span>}
          <span style={{ marginLeft: 'auto' }}>
            组合均值 <b style={{ color: avgColor, fontFamily: HUNTER.SERIF }}>{fmtPct(avg)}</b>
          </span>
        </div>
      </div>

      {/* 今日最强 */}
      {data.top_gainers && data.top_gainers.length > 0 && (
        <div style={{ padding: '14px 18px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontFamily: HUNTER.SERIF, fontSize: 13, fontWeight: 700, marginBottom: 12,
          }}>
            <TrendingUp size={14} style={{ color: HUNTER.UP }} />
            <span>今日最强 Top {data.top_gainers.length}</span>
          </div>
          {data.top_gainers.map(s => <StockRow key={s.code} s={s} up />)}
        </div>
      )}

      {/* 今日最弱 */}
      {data.top_losers && data.top_losers.length > 0 && (
        <div style={{ padding: '14px 18px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontFamily: HUNTER.SERIF, fontSize: 13, fontWeight: 700, marginBottom: 12,
          }}>
            <TrendingDown size={14} style={{ color: HUNTER.DN }} />
            <span>今日最弱 Top {data.top_losers.length}</span>
          </div>
          {data.top_losers.map(s => <StockRow key={s.code} s={s} up={false} />)}
        </div>
      )}

      {/* AI 总结 */}
      {data.ai_summary && (
        <div style={{
          padding: '14px 18px',
          background: HUNTER.BRAND_PALE,
          fontSize: 13, lineHeight: 1.7, color: HUNTER.COPPER3,
        }}>
          <b>💬 </b>{data.ai_summary}
        </div>
      )}
    </div>
  )
}

function StockRow({ s, up }: { s: StockCard; up: boolean }) {
  const color = up ? HUNTER.UP : HUNTER.DN
  return (
    <div style={{
      padding: 12, background: HUNTER.PAPER, borderRadius: 10, marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 6 }}>
        <span style={{ fontFamily: HUNTER.SERIF, fontSize: 14, fontWeight: 700 }}>{s.name}</span>
        <span style={{ fontFamily: 'ui-monospace, menlo, monospace', fontSize: 10.5, color: HUNTER.INK_F }}>
          {s.code}{s.market_suffix ? `.${s.market_suffix}` : ''}
        </span>
        <span style={{
          marginLeft: 'auto', fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, color,
        }}>
          {fmtPct(s.change_pct)}
        </span>
      </div>
      <div style={{ fontSize: 12, color: HUNTER.INK, lineHeight: 1.55, marginBottom: 8 }}>
        {s.attribution}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        {(s.signals || []).map((sig, i) => (
          <span key={i} style={{
            display: 'inline-flex', height: 20, padding: '0 8px',
            borderRadius: 999, background: '#fff', border: `1px solid ${HUNTER.LINE}`,
            color: HUNTER.INK_F, fontSize: 11, fontWeight: 600, alignItems: 'center',
          }}>
            {sig}
          </span>
        ))}
        <button
          type="button"
          onClick={() => goChat(`对 ${s.name} 做多空辩论 · 给出买卖决策`)}
          style={{
            marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 3,
            color: HUNTER.THEME, fontSize: 11, fontWeight: 600, cursor: 'pointer',
            background: 'transparent', border: 0,
          }}
        >
          <Microscope size={11} /> 深度分析 →
        </button>
      </div>
    </div>
  )
}

const cardStyle: React.CSSProperties = {
  margin: '12px 0', background: '#fff',
  border: `1px solid ${HUNTER.LINE}`, borderRadius: 14, overflow: 'hidden',
  boxShadow: '0 4px 18px rgba(40,35,27,.04)',
}
const errStyle: React.CSSProperties = {
  margin: '12px 0', padding: '14px 18px',
  background: '#fbeaea', border: '1px solid #f3c9c9', borderRadius: 10,
  fontSize: 13, color: HUNTER.UP,
}
