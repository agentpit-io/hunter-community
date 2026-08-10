/**
 * 猎鹿人 · 投研助手 V2（主对话 + 多智能体自动调度）
 * 详见 doc/codex/02-手机端UI设计.md 与 03-开发需求与技术方案.md §5
 */
import React, { useMemo, useState } from 'react'
import { T } from './tokens'
import { useAgentChatStream } from './useAgentChatStream'
import { ChatStream } from './ChatStream'
import { StockContextBar } from './StockContextBar'
import { EmptyHintV2 } from './EmptyHintV2'
import { StockPickerSheet } from './StockPickerSheet'

export interface Stock { code: string; name: string }
export interface Quote { price?: number }
export interface Shared { code?: string; name?: string }

export interface ResearchAssistantChatV2Props {
  token: string
  stocks: Stock[]
  quotes?: Record<string, Quote>
  shared?: Shared
  onOpenExpertGrid?: () => void
  onOpenStockPicker?: () => void
}

export function ResearchAssistantChatV2({
  token, stocks, quotes = {}, shared, onOpenExpertGrid, onOpenStockPicker,
}: ResearchAssistantChatV2Props) {
  const [stockCode, setStockCode] = useState<string | null>(shared?.code ?? null)
  const [stockName, setStockName] = useState<string | null>(shared?.name ?? null)
  const [inputVal, setInputVal] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)

  const { items, streaming, sessionId, lastError, send, abort, reset } = useAgentChatStream(token)

  const currentPrice = useMemo(() => {
    if (!stockCode) return null
    return quotes[stockCode]?.price ?? null
  }, [stockCode, quotes])

  const doSend = (q: string) => {
    const trimmed = q.trim()
    if (!trimmed || streaming) return
    // __general__ 是"跳过选股"哨兵值，实际请求不带 stock_code
    const realCode = stockCode === '__general__' ? undefined : (stockCode ?? undefined)
    const realName = stockCode === '__general__' ? undefined : (stockName ?? undefined)
    send({ query: trimmed, stockCode: realCode, stockName: realName })
    setInputVal('')
  }

  const openPicker = () => {
    // 优先用外部 picker（若父层传入）；否则打开自带 Sheet
    if (onOpenStockPicker) return onOpenStockPicker()
    setPickerOpen(true)
  }

  // 未选股 → 首屏直接展示 picker（内嵌模式），选完自动进对话
  if (!stockCode) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: T.BG }}>
        <div style={{
          padding: '18px 20px 12px', background: T.PAPER, borderBottom: `1px solid ${T.LINE}`,
        }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: T.INK, fontFamily: T.SERIF }}>
            📌 先选一只股票开始
          </div>
          <div style={{ fontSize: 12, color: T.INK_F, marginTop: 4, lineHeight: 1.55 }}>
            AI 会基于你选的股票做个性化研究；也可直接问通识问题（公司治理、财报解读、宏观等）
          </div>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', background: T.BG }}>
          <StockPickerSheet
            stocks={stocks as any}
            quotes={quotes as any}
            currentCode={null}
            onPick={(code, name) => { setStockCode(code); setStockName(name) }}
            onClose={() => { /* 内嵌模式不允许关闭 */ }}
            inline
          />
        </div>
        <div style={{
          padding: '12px 14px', background: T.PAPER, borderTop: `1px solid ${T.LINE}`,
        }}>
          <button
            onClick={() => { setStockCode('__general__'); setStockName('') }}
            style={{
              width: '100%', padding: '10px 0',
              background: 'transparent', color: T.INK_S,
              border: `1px dashed ${T.LINE}`, borderRadius: 10,
              fontSize: 12.5, cursor: 'pointer',
            }}
          >跳过 · 直接问通识问题（无股票上下文）</button>
        </div>
      </div>
    )
  }

  // 已选股或明确跳过 → 显示对话主界面
  const displayCode = stockCode === '__general__' ? null : stockCode
  const displayName = stockCode === '__general__' ? null : stockName

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: T.BG }}>
      <StockContextBar
        code={displayCode} name={displayName} price={currentPrice ?? undefined}
        onSwitchClick={openPicker}
      />

      {items.length === 0 && !streaming ? (
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <EmptyHintV2
            hasStock={!!displayCode}
            onExampleClick={q => doSend(q)}
            onOpenExpertGrid={() => onOpenExpertGrid?.()}
          />
        </div>
      ) : (
        <ChatStream items={items} streaming={streaming} lastError={lastError}
                    sessionId={sessionId} token={token} />
      )}

      {/* 输入区 */}
      <div style={{
        borderTop: `1px solid ${T.LINE}`, background: T.PAPER,
        padding: '10px 12px', display: 'flex', alignItems: 'flex-end', gap: 8,
      }}>
        <textarea
          value={inputVal}
          onChange={e => setInputVal(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault(); doSend(inputVal)
            }
          }}
          placeholder={stockCode ? `问关于 ${stockName || stockCode} 的问题…` : '输入你的问题…'}
          rows={1}
          style={{
            flex: 1, resize: 'none', maxHeight: 100,
            fontSize: 14, padding: '9px 12px', borderRadius: 18,
            border: `1px solid ${T.LINE}`, background: T.BG,
            color: T.INK, outline: 'none', lineHeight: 1.4,
          }}
        />
        {streaming ? (
          <button
            onClick={abort}
            style={{
              width: 40, height: 40, borderRadius: '50%',
              border: 'none', background: T.UP, color: '#fff',
              cursor: 'pointer', fontSize: 16, display: 'flex',
              alignItems: 'center', justifyContent: 'center',
            }}
            title="中断"
          >■</button>
        ) : (
          <button
            onClick={() => doSend(inputVal)}
            disabled={!inputVal.trim()}
            style={{
              width: 40, height: 40, borderRadius: '50%',
              border: 'none', background: inputVal.trim() ? T.THEME : T.LINE,
              color: '#fff', cursor: inputVal.trim() ? 'pointer' : 'not-allowed',
              fontSize: 18, display: 'flex',
              alignItems: 'center', justifyContent: 'center',
            }}
            title="发送"
          >↑</button>
        )}
      </div>

      {pickerOpen && (
        <StockPickerSheet
          stocks={stocks as any}
          quotes={quotes as any}
          currentCode={stockCode}
          onPick={(code, name) => {
            setStockCode(code)
            setStockName(name)
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}

      {/* 会话操作条 */}
      {(items.length > 0 || sessionId) && (
        <div style={{
          background: T.PAPER, borderTop: `1px solid ${T.PAPER2}`,
          padding: '6px 12px', display: 'flex', gap: 12, alignItems: 'center',
          fontSize: 11, color: T.INK_F,
        }}>
          {sessionId && <span>会话: {sessionId.slice(0, 12)}…</span>}
          <button onClick={reset} style={miniLink}>新对话</button>
          {onOpenExpertGrid && (
            <button onClick={onOpenExpertGrid} style={{ ...miniLink, marginLeft: 'auto' }}>
              5 位专家 ›
            </button>
          )}
        </div>
      )}
    </div>
  )
}

const miniLink: React.CSSProperties = {
  border: 'none', background: 'transparent', color: T.THEME,
  fontSize: 11, cursor: 'pointer', padding: 0,
}
