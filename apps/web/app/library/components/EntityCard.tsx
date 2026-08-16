'use client'
// 通用卡片 · 3 类都用这一个 · 通过 props 定制显示
import { HUNTER } from '../../lib/hunter-theme'
import { statusDot } from '../../chat/lib/catalogClient'

export interface EntityCardProps {
  icon?: string
  title: string
  subtitle?: string
  status: string       // 会经 statusDot() 转成颜色
  statusLabel?: string // 覆盖默认 label(如"未调用过")
  tags?: { text: string; tone?: 'default' | 'ok' | 'warn' }[]
  meta?: string        // 右侧灰字(如"全市场 5000+ 只" / "内置" / "23 只已入库")
  selected?: boolean
  onClick?: () => void
  onDoubleClick?: () => void
  doubleClickHint?: string  // 双击时的 tooltip 提示(SKILL 用"双击填入对话框")
}

export default function EntityCard(props: EntityCardProps) {
  const dot = statusDot(props.status)
  return (
    <div
      style={cardStyle(props.selected)}
      onClick={props.onClick}
      onDoubleClick={props.onDoubleClick}
      title={props.doubleClickHint}
      onMouseEnter={(e) => { if (!props.selected) (e.currentTarget as HTMLDivElement).style.background = HUNTER.PAPER3 }}
      onMouseLeave={(e) => { if (!props.selected) (e.currentTarget as HTMLDivElement).style.background = '#fff' }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span style={{ ...dotStyle, background: dot.color, marginTop: 6 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {props.icon && <span style={{ fontSize: 15 }}>{props.icon}</span>}
            <span style={titleStyle}>{props.title}</span>
            {props.tags?.map((t, i) => <Tag key={i} {...t} />)}
          </div>
          {props.subtitle && <div style={subtitleStyle}>{props.subtitle}</div>}
        </div>
        {props.meta && <div style={metaStyle}>{props.meta}</div>}
      </div>
    </div>
  )
}

function Tag({ text, tone = 'default' }: { text: string; tone?: 'default' | 'ok' | 'warn' }) {
  const bg = tone === 'ok' ? HUNTER.TAG_OK_BG : tone === 'warn' ? HUNTER.TAG_WARN_BG : HUNTER.PANEL_2
  const fg = tone === 'ok' ? HUNTER.TAG_OK_FG : tone === 'warn' ? HUNTER.TAG_WARN_FG : HUNTER.INK_F
  return (
    <span style={{
      padding: '1px 6px',
      fontSize: 10,
      background: bg,
      color: fg,
      borderRadius: 4,
      whiteSpace: 'nowrap',
    }}>{text}</span>
  )
}

const cardStyle = (selected?: boolean): React.CSSProperties => ({
  padding: '10px 12px',
  background: selected ? HUNTER.BRAND_PALE : '#fff',
  border: `1px solid ${selected ? HUNTER.THEME : HUNTER.LINE}`,
  borderRadius: HUNTER.R_MD,
  cursor: 'pointer',
  transition: 'background 0.1s, border-color 0.1s',
  marginBottom: 6,
})

const dotStyle: React.CSSProperties = {
  width: 8,
  height: 8,
  borderRadius: '50%',
  display: 'inline-block',
  flexShrink: 0,
}

const titleStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: HUNTER.INK,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const subtitleStyle: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.INK_F,
  marginTop: 3,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
}

const metaStyle: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.INK_F,
  whiteSpace: 'nowrap',
  marginLeft: 8,
}
