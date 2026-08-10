'use client'
// SKILL 6 · 风险画像 · Sprint 1 · 深色主题 · 5 约束卡 + diff
import { HUNTER } from '../../../lib/hunter-theme'
import { goChat } from './shared'

interface ProfileData {
  cash_balance:         number
  cash_balance_label:   string
  risk_tolerance:       'low' | 'medium' | 'high'
  risk_tolerance_label: string
  max_position:         number
  max_position_label:   string
  max_hk_ratio:         number
  max_hk_ratio_label:   string
  max_sector:           number
  max_sector_label:     string
  is_default:           boolean
  updated_at:           string | null
}

interface ChangeItem {
  field:  string
  before: string
  after:  string
}

interface Data {
  type: 'update_risk_profile'
  profile:  ProfileData
  changes:  ChangeItem[]
  hint?:    string
  error?:   string
}

const FIELD_ZH: Record<string, string> = {
  cash_balance:   '可用现金',
  risk_tolerance: '风险容忍',
  max_position:   '单票上限',
  max_hk_ratio:   '港股上限',
  max_sector:     '单行业上限',
}

const TOL_COLOR: Record<string, string> = {
  low:    '#7fb08a',   // 保守 = 绿
  medium: '#f0c19c',   // 稳健 = 铜
  high:   '#e08a75',   // 进取 = 红
}

export default function RiskProfileCard({ data }: { data: Data }) {
  if (data.error) {
    return <div style={errStyle}><b>⚠</b> {data.error}</div>
  }
  const p = data.profile
  const hasChanges = data.changes && data.changes.length > 0

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
          🎚 风险画像
        </div>
        <div style={{
          marginLeft: 'auto', color: '#f0c19c', fontSize: 10, fontWeight: 700,
          letterSpacing: '.15em', textTransform: 'uppercase',
        }}>
          {p.is_default ? 'DEFAULT · 未设置' : (hasChanges ? 'UPDATED' : 'CURRENT')}
        </div>
      </div>

      <div style={{ padding: '10px 24px 12px', color: '#a89887', fontSize: 12.5 }}>
        {data.hint || '风险画像影响组合建议 / 情景模拟的约束'}
      </div>

      {/* 5 张约束卡 · 2+3 网格 */}
      <div style={{
        padding: '0 24px 16px',
        display: 'grid', gap: 12,
        gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr',
      }}>
        <MetricCard
          label="风险容忍"
          value={p.risk_tolerance_label}
          color={TOL_COLOR[p.risk_tolerance]}
          large
        />
        <MetricCard
          label="可用现金"
          value={p.cash_balance_label}
          color="#f4eee7"
          large
        />
        <MetricCard label="单票上限"    value={p.max_position_label}   color="#f0c19c" />
        <MetricCard label="港股上限"    value={p.max_hk_ratio_label}   color="#f0c19c" />
        <MetricCard label="单行业上限"  value={p.max_sector_label}     color="#f0c19c" />
      </div>

      {/* diff · 若有变化 */}
      {hasChanges && (
        <div style={{
          margin: '0 24px 16px', padding: '12px 16px',
          background: '#2a2320', borderRadius: 10,
          color: '#a89887', fontSize: 12.5, lineHeight: 1.7,
        }}>
          <b style={{ color: '#f0c19c' }}>本次变化</b>：
          {data.changes.map((c, i) => (
            <span key={c.field}>
              {i > 0 && ' · '}
              {FIELD_ZH[c.field] || c.field}
              <span style={{ color: '#7fb08a', margin: '0 4px' }}>{c.before}</span>
              →
              <span style={{ color: '#f0c19c', marginLeft: 4 }}>{c.after}</span>
            </span>
          ))}
        </div>
      )}

      {/* 底部 CTA · 追问按钮 */}
      <div style={{
        padding: '14px 24px 18px', borderTop: '1px solid #3a2f28',
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      }}>
        <div style={{ flex: 1, fontSize: 11.5, color: '#a89887', lineHeight: 1.6 }}>
          画像会被
          <span style={{ color: '#f0c19c', fontFamily: HUNTER.SERIF }}> 组合建议 · 情景模拟 </span>
          自动读取
        </div>
        <button
          type="button"
          onClick={() => goChat('帮我看看我的持仓怎么调仓')}
          style={ctaBtn}
        >
          🎯 组合建议 →
        </button>
        <button
          type="button"
          onClick={() => goChat('如果紫金跌 20% · 我组合会亏多少?')}
          style={ctaBtn}
        >
          🌊 情景模拟 →
        </button>
      </div>
    </div>
  )
}

function MetricCard({ label, value, color, large }: {
  label: string; value: string; color: string; large?: boolean
}) {
  return (
    <div style={{
      background: '#2a2320', borderRadius: 12,
      padding: '16px 14px', textAlign: 'center',
      border: '1px solid #3a2f28',
    }}>
      <div style={{ color: '#a89887', fontSize: 11.5, marginBottom: 8 }}>{label}</div>
      <div style={{
        fontFamily: HUNTER.SERIF, fontWeight: 700, color,
        fontSize: large ? 22 : 18, letterSpacing: '.01em',
        fontVariantNumeric: 'tabular-nums',
      }}>
        {value}
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
const ctaBtn: React.CSSProperties = {
  padding: '8px 14px', background: '#3a2f28', color: '#f0c19c',
  border: '1px solid #4a3d33', borderRadius: 8,
  fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
}
const errStyle: React.CSSProperties = {
  margin: '12px 0', padding: '14px 18px',
  background: '#fbeaea', border: '1px solid #f3c9c9', borderRadius: 10,
  fontSize: 13, color: HUNTER.UP,
}
