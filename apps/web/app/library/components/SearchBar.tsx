'use client'
// 顶栏搜索 · debounce 200ms · 前端 filter
import { useEffect, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'

interface Props {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}

export default function SearchBar({ value, onChange, placeholder }: Props) {
  const [local, setLocal] = useState(value)

  // 外部 value 变化时同步(如清空)
  useEffect(() => { setLocal(value) }, [value])

  // debounce 200ms 上报
  useEffect(() => {
    if (local === value) return
    const t = setTimeout(() => onChange(local), 200)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local])

  return (
    <div style={wrapStyle}>
      <span style={iconStyle}>🔍</span>
      <input
        type="text"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        placeholder={placeholder || '搜索能力(名称 / 描述 / 分类)...'}
        style={inputStyle}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { setLocal(''); onChange('') }
        }}
      />
      {local && (
        <button
          onClick={() => { setLocal(''); onChange('') }}
          style={clearBtnStyle}
          title="清空 (ESC)"
        >×</button>
      )}
    </div>
  )
}

const wrapStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 10px',
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: HUNTER.R_MD,
  minWidth: 320,
  maxWidth: 480,
  transition: 'border-color 0.15s',
}

const iconStyle: React.CSSProperties = {
  fontSize: 13,
  opacity: 0.5,
}

const inputStyle: React.CSSProperties = {
  flex: 1,
  border: 'none',
  outline: 'none',
  fontSize: 13,
  fontFamily: 'inherit',
  color: HUNTER.INK,
  background: 'transparent',
  padding: '2px 0',
}

const clearBtnStyle: React.CSSProperties = {
  width: 20,
  height: 20,
  borderRadius: '50%',
  border: 'none',
  background: HUNTER.PANEL_2,
  color: HUNTER.INK_F,
  fontSize: 14,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 0,
  lineHeight: 1,
}
