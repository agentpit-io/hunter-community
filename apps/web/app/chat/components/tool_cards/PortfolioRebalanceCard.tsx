'use client'
// SKILL 4 · 组合级建议 · 深色主题表格 · 仿设计稿
import { HUNTER } from '../../../lib/hunter-theme'
import { fmtMoney, goChat } from './shared'

interface Position {
  code: string
  name: string
  current_shares: number
  current_price: number
  current_value: number
  current_pct: number
  target_pct: number
  gap_pct: number
  action: 'buy' | 'sell' | 'hold'
  action_label: string
  action_shares: number
  action_value: number
  sector: string
}

interface RebalanceData {
  type: 'portfolio_rebalance'
  portfolio_value?: number
  cash?: number
  total_with_cash?: number
  positions?: Position[]
  sector_exposure?: Record<string, number>
  risk_warning?: string
  has_explicit_target?: boolean
  empty?: boolean
  hint?: string
  error?: string
}

export default function PortfolioRebalanceCard({ data }: { data: RebalanceData }) {
  if (data.error) {
    return <div style={errStyle}><b>⚠</b> {data.error}</div>
  }
  if (data.empty) {
    return (
      <div style={darkCard}>
        <div style={{ padding: '32px 24px', textAlign: 'center', color: '#f4eee7' }}>
          <div style={{ fontSize: 32, marginBottom: 8, opacity: 0.7 }}>🎯</div>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, marginBottom: 8 }}>
            还未录入持仓数据
          </div>
          <div style={{ color: '#a89887', fontSize: 12.5, marginBottom: 18, lineHeight: 1.65 }}>
            {data.hint || '去『持仓』页录入 shares + cost，再来问组合建议'}
          </div>
          <a
            href="/portfolio"
            style={{
              display: 'inline-block', padding: '9px 20px',
              background: HUNTER.THEME, color: '#fff',
              borderRadius: 10, fontSize: 13, fontWeight: 600, textDecoration: 'none',
            }}
          >
            → 去持仓页录入
          </a>
        </div>
      </div>
    )
  }

  const positions = data.positions || []
  const totalValue = data.portfolio_value || 0
  const cash = data.cash || 0

  return (
    <div style={darkCard}>
      {/* 头部 · 标题 + LAYER label */}
      <div style={{
        padding: '20px 24px 14px',
        display: 'flex', alignItems: 'baseline', gap: 10,
        borderBottom: '1px solid #3a2f28',
      }}>
        <div style={{
          fontFamily: HUNTER.SERIF, fontSize: 22, fontWeight: 700,
          color: '#f4eee7', letterSpacing: '.01em',
        }}>
          深层 · 组合级建议
        </div>
        <div style={{
          marginLeft: 'auto', color: '#f0c19c', fontSize: 10, fontWeight: 700,
          letterSpacing: '.15em', textTransform: 'uppercase',
        }}>
          LAYER 2 · REBALANCE
        </div>
      </div>

      <div style={{ padding: '10px 24px 16px', color: '#a89887', fontSize: 12.5 }}>
        <span style={{ color: '#f0c19c', fontWeight: 600 }}>
          {fmtMoney(totalValue)}
        </span>
        {' 持仓 + '}
        <span style={{ color: '#f0c19c', fontWeight: 600 }}>{fmtMoney(cash)}</span>
        {' 现金 · '}
        {positions.length} 只自选 · 组合建议如下
      </div>

      {/* 表格 */}
      <table style={{
        width: '100%', borderCollapse: 'separate', borderSpacing: 0,
        fontSize: 13, color: '#f4eee7',
      }}>
        <thead>
          <tr>
            {['股票', '现在', '目标', '差距', '建议动作'].map((h, i) => (
              <th key={h} style={{
                padding: '10px 16px',
                paddingLeft: i === 0 ? 24 : 16,
                background: '#2a2320', color: '#a89887',
                fontWeight: 600, fontSize: 11.5, textAlign: 'left',
                textTransform: 'uppercase', letterSpacing: '.05em',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const gapColor = p.gap_pct > 1 ? '#e08a75' : p.gap_pct < -1 ? '#7fb08a' : '#a89887'
            const actColor = p.action === 'buy' ? '#f0c19c' : p.action === 'sell' ? '#7fb08a' : '#a89887'
            return (
              <tr key={p.code}>
                <td style={{
                  ...td, paddingLeft: 24,
                  fontFamily: HUNTER.SERIF, fontWeight: 700,
                }}>
                  {p.name}
                  <span style={{
                    marginLeft: 6, fontFamily: 'ui-monospace, menlo, monospace',
                    fontSize: 10.5, color: '#a89887', fontWeight: 400,
                  }}>
                    {p.code}
                  </span>
                </td>
                <td style={td}>{p.current_pct.toFixed(1)}%</td>
                <td style={td}>
                  {p.target_pct.toFixed(1)}%
                  {data.has_explicit_target && (
                    <span style={{ color: '#7fb08a', marginLeft: 4 }}>✓</span>
                  )}
                </td>
                <td style={{ ...td, color: gapColor, fontWeight: 700 }}>
                  {p.gap_pct >= 0 ? '+' : ''}{p.gap_pct.toFixed(1)}%
                </td>
                <td style={{ ...td, color: actColor, fontWeight: 600, paddingRight: 24 }}>
                  {p.action_label}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {/* 底部 · 板块暴露 + 风险提示 */}
      <div style={{
        padding: '14px 24px', borderTop: '1px solid #3a2f28',
        background: '#2a2320', fontSize: 12, color: '#a89887', lineHeight: 1.7,
      }}>
        {data.sector_exposure && Object.keys(data.sector_exposure).length > 0 && (
          <div style={{ marginBottom: data.risk_warning ? 8 : 0 }}>
            <b style={{ color: '#f0c19c' }}>板块暴露</b>：
            {Object.entries(data.sector_exposure)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 5)
              .map(([k, v]) => `${k} ${v.toFixed(0)}%`)
              .join(' · ')}
          </div>
        )}
        {data.risk_warning && (
          <div style={{ color: '#e08a75' }}>⚠ {data.risk_warning}</div>
        )}
      </div>

      {/* 底部 slogan + 追问按钮 */}
      <div style={{
        padding: '14px 24px 18px', borderTop: '1px solid #3a2f28',
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, fontSize: 11.5, color: '#a89887', lineHeight: 1.6 }}>
          这不是券商版，是<span style={{ color: '#f0c19c', fontFamily: HUNTER.SERIF }}>
          你手里的原因和比例都是什么</span>的组合大脑
        </div>
        <button
          type="button"
          onClick={() => goChat('如果紫金跌 20% · 我组合会亏多少?')}
          style={{
            padding: '8px 14px', background: '#3a2f28', color: '#f0c19c',
            border: '1px solid #4a3d33', borderRadius: 8,
            fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
          }}
        >
          🌊 情景模拟 →
        </button>
      </div>
    </div>
  )
}

const darkCard: React.CSSProperties = {
  margin: '12px 0',
  background: '#211b17',
  color: '#f4eee7',
  borderRadius: 16,
  overflow: 'hidden',
  boxShadow: '0 12px 40px rgba(20,15,8,.35)',
}
const td: React.CSSProperties = {
  padding: '14px 16px',
  borderBottom: '1px solid #3a2f28',
  fontVariantNumeric: 'tabular-nums',
}
const errStyle: React.CSSProperties = {
  margin: '12px 0', padding: '14px 18px',
  background: '#fbeaea', border: '1px solid #f3c9c9', borderRadius: 10,
  fontSize: 13, color: HUNTER.UP,
}
