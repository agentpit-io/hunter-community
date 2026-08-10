'use client'
// SKILL 5 · 情景模拟 · 深色主题三卡 · 仿设计稿
import { HUNTER } from '../../../lib/hunter-theme'
import { fmtMoney } from './shared'

interface AffectedStock {
  code: string
  name: string
  sector: string
  beta_to_shock: number
  loss: number
}

interface StressData {
  type: 'portfolio_stress'
  scenario?: string
  shock_code?: string
  shock_pct?: number
  shock_sector?: string
  portfolio_value?: number
  direct_loss?: {
    value: number
    held_shares?: number
    held_value?: number
    current_price?: number
    note?: string
  }
  sector_pass_through?: {
    value: number
    enabled: boolean
    affected_stocks: AffectedStock[]
  }
  total_loss?: { value: number; pct_of_portfolio: number }
  mitigation?: string
  empty?: boolean
  hint?: string
  error?: string
}

export default function PortfolioStressCard({ data }: { data: StressData }) {
  if (data.error) {
    return <div style={errStyle}><b>⚠</b> {data.error}</div>
  }
  if (data.empty) {
    return (
      <div style={darkCard}>
        <div style={{ padding: '32px 24px', textAlign: 'center', color: '#f4eee7' }}>
          <div style={{ fontSize: 32, marginBottom: 8, opacity: 0.7 }}>🌊</div>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, marginBottom: 8 }}>
            还未录入持仓数据
          </div>
          <div style={{ color: '#a89887', fontSize: 12.5, marginBottom: 18, lineHeight: 1.65 }}>
            {data.hint || '情景模拟需要 shares + cost_price · 去持仓页录入'}
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

  const shockPct = data.shock_pct || 0
  const scenario = data.scenario || `${data.shock_code || ''} 冲击 ${shockPct}%`

  return (
    <div style={darkCard}>
      {/* 头部 */}
      <div style={{
        padding: '20px 24px 14px',
        display: 'flex', alignItems: 'baseline', gap: 10,
        borderBottom: '1px solid #3a2f28',
      }}>
        <div style={{
          fontFamily: HUNTER.SERIF, fontSize: 22, fontWeight: 700,
          color: '#f4eee7', letterSpacing: '.01em',
        }}>
          深层 · 情景模拟
        </div>
        <div style={{
          marginLeft: 'auto', color: '#f0c19c', fontSize: 10, fontWeight: 700,
          letterSpacing: '.15em', textTransform: 'uppercase',
        }}>
          LAYER 3 · SCENARIO
        </div>
      </div>

      <div style={{ padding: '10px 24px 16px', color: '#a89887', fontSize: 12.5 }}>
        把心里的「万一」焦虑，用数字回答
      </div>

      {/* 问题条 */}
      <div style={{
        margin: '0 24px 20px', padding: '14px 18px',
        background: '#2a2320', borderRadius: 10,
        color: '#f0c19c', fontSize: 14,
      }}>
        <span style={{ color: '#d4925a', marginRight: 6 }}>⓵</span>
        如果{scenario}，我组合会亏多少？
      </div>

      {/* 3 张损失卡 */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
        gap: 12, padding: '0 24px 20px',
      }}>
        <LossCard
          label="直接损失"
          value={data.direct_loss?.value ?? 0}
          sub={data.direct_loss?.held_shares
            ? `${data.shock_code || ''} 持仓部分 · ${data.direct_loss.held_shares} 股`
            : (data.direct_loss?.note || '无')}
        />
        <LossCard
          label="行业联动"
          value={data.sector_pass_through?.value ?? 0}
          sub={data.sector_pass_through?.affected_stocks?.length
            ? `${data.sector_pass_through.affected_stocks.map(a => a.name).slice(0, 3).join(' · ')} · ${data.shock_sector || ''}板块传导`
            : '无联动股'}
        />
        <LossCard
          label="组合总损失"
          value={data.total_loss?.value ?? 0}
          sub={data.total_loss
            ? `占总资产 ${data.total_loss.pct_of_portfolio.toFixed(2)}%`
            : ''}
          highlight
        />
      </div>

      {/* 修复建议 */}
      {data.mitigation && (
        <div style={{
          margin: '0 24px 20px', color: '#f0c19c', fontSize: 13.5, lineHeight: 1.75,
        }}>
          <span style={{ color: '#d4925a', marginRight: 6 }}>→</span>
          {data.mitigation}
        </div>
      )}

      {/* 联动细节（若有） */}
      {data.sector_pass_through?.affected_stocks && data.sector_pass_through.affected_stocks.length > 0 && (
        <details style={{
          margin: '0 24px 16px',
          padding: '10px 14px',
          background: '#2a2320',
          borderRadius: 10,
          color: '#a89887', fontSize: 12,
        }}>
          <summary style={{ cursor: 'pointer', color: '#f0c19c' }}>展开联动细节</summary>
          <table style={{ width: '100%', marginTop: 8, borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={smTh}>股票</th>
                <th style={smTh}>板块</th>
                <th style={smTh}>β</th>
                <th style={{ ...smTh, textAlign: 'right' }}>估计损失</th>
              </tr>
            </thead>
            <tbody>
              {data.sector_pass_through.affected_stocks.map(a => (
                <tr key={a.code}>
                  <td style={smTd}>{a.name}</td>
                  <td style={smTd}>{a.sector}</td>
                  <td style={smTd}>{a.beta_to_shock.toFixed(2)}</td>
                  <td style={{ ...smTd, textAlign: 'right', color: '#e08a75' }}>{fmtMoney(a.loss)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {/* 底部 slogan */}
      <div style={{
        padding: '14px 24px 18px', borderTop: '1px solid #3a2f28',
        color: '#a89887', fontSize: 11.5, lineHeight: 1.6,
      }}>
        这才是决策助手 —— <span style={{ color: '#f0c19c', fontFamily: HUNTER.SERIF }}>
        不只告诉你现在怎么办，还告诉你极端情况下会怎样</span>
      </div>
    </div>
  )
}

function LossCard({ label, value, sub, highlight }: { label: string; value: number; sub: string; highlight?: boolean }) {
  const color = value < 0 ? '#f0c19c' : '#7fb08a'  // 亏损时铜色 · 收益时绿
  return (
    <div style={{
      background: highlight ? 'linear-gradient(180deg, #322723 0%, #2a2320 100%)' : '#2a2320',
      borderRadius: 14, padding: '24px 18px', textAlign: 'center',
      border: `1px solid ${highlight ? '#4a3d33' : '#3a2f28'}`,
    }}>
      <div style={{ color: '#a89887', fontSize: 13, marginBottom: 12 }}>{label}</div>
      <div style={{
        fontFamily: HUNTER.SERIF, fontSize: 30, fontWeight: 700,
        color, marginBottom: 12, letterSpacing: '.01em',
      }}>
        {fmtMoney(value)}
      </div>
      <div style={{ color: '#a89887', fontSize: 11, lineHeight: 1.55 }}>{sub}</div>
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
const errStyle: React.CSSProperties = {
  margin: '12px 0', padding: '14px 18px',
  background: '#fbeaea', border: '1px solid #f3c9c9', borderRadius: 10,
  fontSize: 13, color: HUNTER.UP,
}
const smTh: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'left',
  fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '.05em',
  color: '#a89887', fontWeight: 600,
  borderBottom: '1px solid #3a2f28',
}
const smTd: React.CSSProperties = {
  padding: '7px 10px', fontSize: 12, color: '#f4eee7',
  borderBottom: '1px solid #3a2f28',
}
