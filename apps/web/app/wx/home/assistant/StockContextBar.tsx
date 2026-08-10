import React from 'react'
import { T } from './tokens'

export interface StockContextBarProps {
  code: string | null
  name: string | null
  price?: number | null
  onSwitchClick: () => void
}

export function StockContextBar({ code, name, price, onSwitchClick }: StockContextBarProps) {
  if (!code) {
    return (
      <div style={{
        background: T.PAPER, borderBottom: `1px solid ${T.LINE}`,
        padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8,
        fontSize: 12, color: T.INK_F,
      }}>
        <span style={{ color: T.THEME }}>📌</span>
        还没选股票 → 直接问，或
        <button onClick={onSwitchClick} style={btn}>点这里选一只</button>
      </div>
    )
  }
  return (
    <div style={{
      background: T.PAPER, borderBottom: `1px solid ${T.LINE}`,
      padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
    }}>
      <span style={{ color: T.THEME }}>📌</span>
      <span style={{ color: T.INK_F }}>当前讨论：</span>
      <span style={{ color: T.INK, fontWeight: 600 }}>{name || code}</span>
      <span style={{ color: T.INK_F, marginLeft: 4 }}>{code}</span>
      {typeof price === 'number' && price > 0 && (
        <span style={{ color: T.UP, fontWeight: 600, marginLeft: 6 }}>¥{price}</span>
      )}
      <button onClick={onSwitchClick} style={{ ...btn, marginLeft: 'auto' }}>切换 ›</button>
    </div>
  )
}

const btn: React.CSSProperties = {
  border: 'none', background: 'transparent', color: T.THEME,
  fontSize: 12, cursor: 'pointer', padding: 0,
}
