'use client'
// 从自选股清单快速选择股票 · 底部弹起 sheet
// 用于 4 个研究 tab (深度研究/一手情报/量化择时/持仓研判) 免去打字查询步骤
// 后端零改动: 复用主组件已加载的 stocks + quotes

import { useMemo, useState } from 'react'

// 设计 token (与 WxHome.tsx 保持一致)
const THEME  = '#B06A32'
const UP     = '#A4332B'
const DN     = '#3F6B40'
const PAPER  = '#FFFDF9'
const PAPER2 = '#EFE8DC'
const INK    = '#211C18'
const INK_S  = '#4B423A'
const INK_F  = '#7A6F63'
const LINE   = '#D8CDBA'
const SERIF  = '"Songti SC","Source Han Serif SC",Georgia,serif'

export interface PickerStock { code: string; name: string; market: string; exchange?: string }
export interface PickerQuote { price?: number | null; change_pct?: number | null }

interface WatchlistPickerProps {
  stocks: PickerStock[]
  quotes: Record<string, PickerQuote>
  onPick: (code: string, name: string) => void
  onClose: () => void
  onAddMore?: () => void
  marketFilter?: 'A' | 'HK' | 'US'   // 默认只显示 A 股(4 个研究 tab 均只支持 A 股)
  currentCode?: string               // 高亮当前已选中
}

export function WatchlistPicker({
  stocks, quotes, onPick, onClose, onAddMore,
  marketFilter = 'A', currentCode = '',
}: WatchlistPickerProps) {
  const [q, setQ] = useState('')

  const filtered = useMemo(
    () => stocks.filter(s => s.market === marketFilter),
    [stocks, marketFilter],
  )
  const visible = useMemo(() => {
    if (!q.trim()) return filtered
    const kw = q.trim().toLowerCase()
    return filtered.filter(s =>
      s.name.toLowerCase().includes(kw) || s.code.toLowerCase().includes(kw)
    )
  }, [filtered, q])

  const marketLabel = marketFilter === 'A' ? 'A 股' : marketFilter === 'HK' ? '港股' : '美股'

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 500,
        background: 'rgba(30, 22, 12, 0.55)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        animation: 'wpFadeIn 200ms ease',
      }}
    >
      <style>{`
        @keyframes wpFadeIn { from { opacity: 0 } to { opacity: 1 } }
        @keyframes wpSlideUp { from { transform: translateY(100%) } to { transform: translateY(0) } }
      `}</style>
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 480, maxHeight: '75vh',
          background: PAPER, borderRadius: '18px 18px 0 0',
          display: 'flex', flexDirection: 'column',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          boxShadow: '0 -6px 24px rgba(20, 12, 4, 0.25)',
          animation: 'wpSlideUp 260ms cubic-bezier(0.2, 0.9, 0.3, 1)',
        }}
      >
        {/* 顶部标题栏 */}
        <div style={{
          padding: '14px 18px 12px',
          borderBottom: `1px solid ${PAPER2}`,
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: INK, fontFamily: SERIF }}>
              📋 从自选股选择
            </div>
            <div style={{ fontSize: 11, color: INK_F, marginTop: 3 }}>
              {filtered.length} 只{marketLabel}自选 · 点击即分析
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭"
            style={{
              background: 'transparent', border: 'none', color: INK_F,
              fontSize: 20, padding: '4px 8px', cursor: 'pointer', lineHeight: 1,
            }}
          >✕</button>
        </div>

        {/* 过滤搜索(自选 > 12 只时启用) */}
        {filtered.length > 12 && (
          <div style={{ padding: '10px 14px', borderBottom: `1px solid ${PAPER2}` }}>
            <input
              autoFocus={false}
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="过滤名称 / 代码"
              style={{
                width: '100%', padding: '9px 12px', boxSizing: 'border-box',
                background: PAPER2, border: 'none', borderRadius: 8,
                color: INK, fontSize: 13, outline: 'none',
              }}
            />
          </div>
        )}

        {/* 列表主体 */}
        <div style={{ flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {filtered.length === 0 && (
            <div style={{ padding: '40px 20px', textAlign: 'center', color: INK_F, lineHeight: 1.7 }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>📋</div>
              <div style={{ fontSize: 14, marginBottom: 16 }}>
                还没有 {marketLabel} 自选股
              </div>
              {onAddMore && (
                <button
                  onClick={onAddMore}
                  style={{
                    padding: '10px 22px', background: THEME, color: '#fff',
                    border: 'none', borderRadius: 10, fontSize: 13, cursor: 'pointer',
                  }}
                >+ 去添加自选股</button>
              )}
            </div>
          )}
          {filtered.length > 0 && visible.length === 0 && q && (
            <div style={{ padding: '30px 20px', textAlign: 'center', color: INK_F, fontSize: 13 }}>
              没找到匹配"{q}"的自选股
            </div>
          )}
          {visible.map(s => {
            const quote = quotes[s.code]
            const price = quote?.price ?? null
            const chg = quote?.change_pct ?? null
            const chgColor = chg == null ? INK_F : chg >= 0 ? UP : DN
            const isCurrent = s.code === currentCode
            return (
              <div
                key={s.code}
                onClick={() => { onPick(s.code, s.name); onClose() }}
                style={{
                  padding: '13px 18px', borderBottom: `1px solid ${PAPER2}`,
                  display: 'flex', alignItems: 'center', gap: 12,
                  cursor: 'pointer',
                  background: isCurrent ? 'rgba(176, 106, 50, 0.06)' : 'transparent',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 15, color: INK, fontWeight: 600, fontFamily: SERIF }}>
                      {s.name}
                    </span>
                    {isCurrent && (
                      <span style={{
                        fontSize: 10, background: THEME, color: '#fff',
                        padding: '1px 6px', borderRadius: 999, fontWeight: 600,
                      }}>当前</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{s.code}</div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div style={{ fontSize: 14, color: chgColor, fontWeight: 600, lineHeight: 1.2 }}>
                    {price != null ? price.toFixed(2) : '—'}
                  </div>
                  <div style={{ fontSize: 11, color: chgColor, marginTop: 2, lineHeight: 1.2 }}>
                    {chg != null ? `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%` : ''}
                  </div>
                </div>
                <span style={{ color: THEME, fontSize: 14 }}>›</span>
              </div>
            )
          })}
        </div>

        {/* 底部添加更多入口 */}
        {onAddMore && filtered.length > 0 && (
          <div style={{ padding: '12px 18px', borderTop: `1px solid ${PAPER2}` }}>
            <button
              onClick={onAddMore}
              style={{
                width: '100%', padding: '11px 0',
                background: 'transparent', color: INK_S,
                border: `1px dashed ${LINE}`, borderRadius: 10,
                fontSize: 13, cursor: 'pointer',
              }}
            >+ 添加更多自选股</button>
          </div>
        )}
      </div>
    </div>
  )
}


// ── 触发按钮 (每个 tab 里放在搜索栏下方) ─────────────────────────────────────
interface PickerButtonProps {
  count: number
  onOpen: () => void
  emptyHint?: string     // count === 0 时的引导文案
  onEmptyClick?: () => void
}

export function WatchlistPickerButton({ count, onOpen, emptyHint, onEmptyClick }: PickerButtonProps) {
  const isEmpty = count === 0
  const handleClick = () => {
    if (isEmpty && onEmptyClick) onEmptyClick()
    else onOpen()
  }
  return (
    <button
      onClick={handleClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        width: '100%', padding: '10px 14px', marginTop: 8,
        background: PAPER, border: `1px solid ${isEmpty ? LINE : THEME}`,
        borderRadius: 10, color: isEmpty ? INK_F : THEME,
        fontSize: 13, fontWeight: 600, cursor: 'pointer',
      }}
    >
      <span style={{ fontSize: 15 }}>📋</span>
      <span style={{ flex: 1, textAlign: 'left' }}>
        {isEmpty
          ? (emptyHint || '你还没有 A 股自选 · 去添加')
          : `从自选选择 (${count} 只 A 股)`}
      </span>
      <span style={{ fontSize: 14 }}>›</span>
    </button>
  )
}
