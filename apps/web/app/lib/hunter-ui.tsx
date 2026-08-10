'use client'
import type { CSSProperties, ReactNode } from 'react'
import { HUNTER } from './hunter-theme'

/** 深色森林绿 Header + logo + 副标 + 右侧插槽（用于历史/地图按钮） */
export function HunterHeader({ sub, right }: { sub: string; right?: ReactNode }) {
  return (
    <div style={{
      background: HUNTER.HEADER_BG,
      padding: '18px 18px 20px',
      borderRadius: '0 0 18px 18px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <span style={{ fontSize: 26, lineHeight: 1 }}>🦌</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 19, fontWeight: 700, color: HUNTER.COPPER2, whiteSpace: 'nowrap' }}>
            猎鹿人 · Hunter
          </div>
          <div style={{ fontSize: 12, color: HUNTER.PAPER2, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {sub}
          </div>
        </div>
      </div>
      {right && <div style={{ flexShrink: 0 }}>{right}</div>}
    </div>
  )
}

/** 温润宣纸白卡片，米线边框，圆角 14 */
export function HunterCard({
  children, style, accent,
}: {
  children: ReactNode
  style?: CSSProperties
  accent?: string
}) {
  return (
    <div style={{
      background: HUNTER.PAPER,
      border: `1px solid ${HUNTER.LINE}`,
      borderRadius: HUNTER.R_LG,
      padding: 18,
      ...(accent ? { borderTop: `3px solid ${accent}` } : {}),
      ...style,
    }}>{children}</div>
  )
}

/** 铜色实心圆角主 CTA 按钮 */
export function HunterBtn({
  onClick, disabled, ghost, children, style, type,
}: {
  onClick?: () => void
  disabled?: boolean
  ghost?: boolean
  children: ReactNode
  style?: CSSProperties
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type || 'button'}
      onClick={onClick}
      disabled={disabled}
      style={{
        width: '100%',
        padding: '12px 0',
        borderRadius: HUNTER.R_MD,
        fontSize: 15,
        fontWeight: 600,
        border: ghost ? `1.5px solid ${HUNTER.THEME}` : 'none',
        background: ghost ? 'transparent' : disabled ? '#C9B9A5' : HUNTER.THEME,
        color: ghost ? HUNTER.THEME : '#fff',
        cursor: disabled ? 'not-allowed' : 'pointer',
        ...style,
      }}>
      {children}
    </button>
  )
}

/** 章节标题：宋体 + emoji */
export function HunterSectionTitle({
  icon, children, style,
}: {
  icon: string
  children: ReactNode
  style?: CSSProperties
}) {
  return (
    <div style={{
      fontFamily: HUNTER.SERIF,
      fontSize: 16,
      fontWeight: 700,
      color: HUNTER.INK,
      marginBottom: 12,
      letterSpacing: 0.5,
      ...style,
    }}>
      <span style={{ marginRight: 8 }}>{icon}</span>{children}
    </div>
  )
}
