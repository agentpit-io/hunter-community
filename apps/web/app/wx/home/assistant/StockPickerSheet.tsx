/**
 * 股票选择 Sheet · 底部弹起
 * - 顶部输入框：模糊搜索（防抖 300ms → /api/online-analysis/search-stock 公开端点）
 * - 上部：搜索结果（若有 query）
 * - 下部：我的自选股（点击直选）
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { T } from './tokens'

export interface PickerStock { code: string; name: string; market?: string }
export interface PickerQuote { price?: number | null; change_pct?: number | null }

export interface StockPickerSheetProps {
  stocks: PickerStock[]                       // 用户自选股
  quotes?: Record<string, PickerQuote>
  currentCode?: string | null
  onPick: (code: string, name: string) => void
  onClose: () => void
  inline?: boolean                            // true = 内嵌模式（无遮罩+无关闭+撑满父容器）
}

interface SearchItem { code: string; name: string; market?: string }

export function StockPickerSheet({
  stocks, quotes = {}, currentCode, onPick, onClose, inline = false,
}: StockPickerSheetProps) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // 只显示 A 股自选
  const myStocks = useMemo(
    () => stocks.filter(s => !s.market || s.market === 'A'),
    [stocks],
  )

  // 防抖 300ms 触发搜索
  useEffect(() => {
    const kw = q.trim()
    if (!kw) { setResults([]); setSearching(false); abortRef.current?.abort(); return }
    setSearching(true)
    const timer = setTimeout(async () => {
      abortRef.current?.abort()
      const ctrl = new AbortController()
      abortRef.current = ctrl
      try {
        const resp = await fetch(`/api/online-analysis/search-stock?q=${encodeURIComponent(kw)}&limit=15`,
                                  { signal: ctrl.signal })
        if (!resp.ok) throw new Error(String(resp.status))
        const data = await resp.json()
        setResults(Array.isArray(data.items) ? data.items : [])
      } catch (e: any) {
        if (e?.name !== 'AbortError') setResults([])
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [q])

  const pick = (code: string, name: string) => { onPick(code, name); if (!inline) onClose() }

  // 内嵌模式：无遮罩、无关闭按钮、无动画、撑满父容器
  if (inline) {
    return (
      <div style={{
        width: '100%', height: '100%', background: T.PAPER,
        display: 'flex', flexDirection: 'column',
      }}>
        <PickerBody
          q={q} setQ={setQ} results={results} searching={searching}
          myStocks={myStocks} quotes={quotes} currentCode={currentCode}
          onPick={pick}
        />
      </div>
    )
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 500,
        background: 'rgba(30, 22, 12, 0.55)',
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        animation: 'sps_fade 200ms ease',
      }}
    >
      <style>{`
        @keyframes sps_fade { from { opacity: 0 } to { opacity: 1 } }
        @keyframes sps_slide { from { transform: translateY(100%) } to { transform: translateY(0) } }
      `}</style>
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 480, maxHeight: '80vh',
          background: T.PAPER, borderRadius: '18px 18px 0 0',
          display: 'flex', flexDirection: 'column',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          boxShadow: '0 -6px 24px rgba(20, 12, 4, 0.25)',
          animation: 'sps_slide 260ms cubic-bezier(0.2, 0.9, 0.3, 1)',
        }}
      >
        {/* 顶栏 */}
        <div style={{
          padding: '14px 18px 10px', borderBottom: `1px solid ${T.PAPER2}`,
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: T.INK, fontFamily: T.SERIF }}>
              📌 选择股票
            </div>
            <div style={{ fontSize: 11, color: T.INK_F, marginTop: 3 }}>
              搜索或从自选股快速选择
            </div>
          </div>
          <button onClick={onClose} aria-label="关闭" style={{
            background: 'transparent', border: 'none', color: T.INK_F,
            fontSize: 20, padding: '4px 8px', cursor: 'pointer', lineHeight: 1,
          }}>✕</button>
        </div>

        <PickerBody
          q={q} setQ={setQ} results={results} searching={searching}
          myStocks={myStocks} quotes={quotes} currentCode={currentCode}
          onPick={pick}
        />
      </div>
    </div>
  )
}


// 抽出的公共主体：搜索框 + 结果/自选股列表
function PickerBody({ q, setQ, results, searching, myStocks, quotes, currentCode, onPick }: {
  q: string
  setQ: (v: string) => void
  results: SearchItem[]
  searching: boolean
  myStocks: PickerStock[]
  quotes: Record<string, PickerQuote>
  currentCode?: string | null
  onPick: (code: string, name: string) => void
}) {
  return (
    <>
      {/* 搜索框 */}
      <div style={{ padding: '10px 14px', borderBottom: `1px solid ${T.PAPER2}` }}>
        <input
          autoFocus
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="输入股票名称或代码，如：茅台 / 600519"
          style={{
            width: '100%', padding: '10px 12px', boxSizing: 'border-box',
            background: T.PAPER2, border: 'none', borderRadius: 8,
            color: T.INK, fontSize: 14, outline: 'none',
          }}
        />
      </div>

      {/* 主体：搜索结果 优先 → 自选股列表 */}
      <div style={{ flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch' }}>
        {q.trim() && (
          <div>
            <SectionHeader label={searching ? '搜索中…' : `搜索结果 (${results.length})`} />
            {!searching && results.length === 0 && (
              <div style={{ padding: '24px', textAlign: 'center', color: T.INK_F, fontSize: 13 }}>
                没找到匹配"{q}"的股票
              </div>
            )}
            {results.map(s => (
              <StockRow key={s.code} s={s} q={quotes[s.code]} currentCode={currentCode} onPick={onPick} />
            ))}
          </div>
        )}

        {myStocks.length > 0 && (
          <div>
            <SectionHeader label={`我的自选股 (${myStocks.length})`} />
            {myStocks.map(s => (
              <StockRow key={s.code} s={s} q={quotes[s.code]} currentCode={currentCode} onPick={onPick} />
            ))}
          </div>
        )}

        {!q.trim() && myStocks.length === 0 && (
          <div style={{ padding: '48px 20px', textAlign: 'center', color: T.INK_F, lineHeight: 1.7 }}>
            <div style={{ fontSize: 36, marginBottom: 12 }}>🔍</div>
            <div style={{ fontSize: 14 }}>输入股票名称或代码开始搜索</div>
            <div style={{ fontSize: 12, marginTop: 6 }}>
              或去「① 选股」/「② 盯盘」添加自选股
            </div>
          </div>
        )}
      </div>
    </>
  )
}


function SectionHeader({ label }: { label: string }) {
  return (
    <div style={{
      padding: '10px 16px 6px', fontSize: 11, color: T.INK_F,
      letterSpacing: 0.3, textTransform: 'none',
      background: T.PAPER2, borderBottom: `1px solid ${T.LINE}`,
    }}>{label}</div>
  )
}


function StockRow({ s, q, currentCode, onPick }: {
  s: { code: string; name: string; market?: string }
  q?: { price?: number | null; change_pct?: number | null }
  currentCode?: string | null
  onPick: (code: string, name: string) => void
}) {
  const price = q?.price ?? null
  const chg = q?.change_pct ?? null
  const chgColor = chg == null ? T.INK_F : chg >= 0 ? T.UP : T.DN
  const isCurrent = s.code === currentCode
  return (
    <div
      onClick={() => onPick(s.code, s.name)}
      style={{
        padding: '12px 18px', borderBottom: `1px solid ${T.PAPER2}`,
        display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer',
        background: isCurrent ? 'rgba(176, 106, 50, 0.06)' : 'transparent',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14.5, color: T.INK, fontWeight: 600, fontFamily: T.SERIF }}>{s.name}</span>
          {isCurrent && (
            <span style={{
              fontSize: 10, background: T.THEME, color: '#fff',
              padding: '1px 6px', borderRadius: 999, fontWeight: 600,
            }}>当前</span>
          )}
        </div>
        <div style={{ fontSize: 11, color: T.INK_F, marginTop: 2 }}>
          {s.code}{s.market && s.market !== 'A' ? ` · ${s.market}` : ''}
        </div>
      </div>
      {price != null && (
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 13, color: chgColor, fontWeight: 600, lineHeight: 1.2 }}>
            {price.toFixed(2)}
          </div>
          {chg != null && (
            <div style={{ fontSize: 10, color: chgColor, marginTop: 2, lineHeight: 1.2 }}>
              {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
            </div>
          )}
        </div>
      )}
      <span style={{ color: T.THEME, fontSize: 14 }}>›</span>
    </div>
  )
}
