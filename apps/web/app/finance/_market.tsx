'use client'
/**
 * 多市场共享件（N5）：币种符号 / 市场切换器 / 市场状态条。
 *
 * 三条约定（都对齐已有实现，不另发明）：
 *
 * 1. **市场切换器直接对齐策略中心已有的 A股/美股 切换**
 *    （`apps/web/public/strategies/data.html:232` 的 `seg('dc-market', …)`）：
 *    同一套形状 —— 一排圆角按钮（7px 圆角、激活态 `#FDF3E7` 底 + `#A85F17` 字）。
 *    这里只是把它从静态页搬成 React 组件，**不是新交互**。
 * 2. **市场状态一律来自 `GET /api/v1/fin/markets`**，它按 `fin_market_calendar`
 *    的 `(market, trade_date)` 行判定（**不含节假日的 gm 判定不可用**）。日历缺行 =
 *    状态 `unknown`（「未知 ≠ 交易日」）。前端**不自己算今天是几号星期几**。
 * 3. **币种符号只有一处**（`_ui.currencySymbol()`，由 `Intl` 的货币数据给出，
 *    与后端 `report.CURRENCY_SYMBOL` 显示同一批符号）。页面上的金额一律过
 *    `money(v, currency)`，**源码里不出现硬编码的货币符号**。
 */
import { useEffect, useState } from 'react'
import { finFetch, currencySymbol } from './_ui'

export const MARKET_LABEL: Record<string, string> = { CN_A: 'A 股', HK: '港股', US: '美股', MULTI: '跨市场汇总' }
export const MARKET_CURRENCY: Record<string, string> = { CN_A: 'CNY', HK: 'HKD', US: 'USD' }

export type MarketStatus = {
  market: string; label: string; currency: string; symbol: string; timezone: string
  trade_date: string; local_time: string
  is_trading_day: boolean | null
  state: string; state_label: string
  sessions: { open: string; close: string }[]
  calendar_source: string | null; note: string
}

/** 市场切换器（对齐策略中心那一排圆角按钮）。 */
export function MarketSwitcher({ value, onChange, markets = ['CN_A', 'HK', 'US'], disabled }: {
  value: string
  onChange: (m: string) => void
  markets?: string[]
  disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap gap-[7px]" role="tablist" aria-label="切换市场">
      {markets.map(m => {
        const on = m === value
        return (
          <button key={m} role="tab" aria-selected={on} disabled={disabled}
            onClick={() => { if (!on && !disabled) onChange(m) }}
            className="rounded-[7px] border px-3.5 py-[7px] text-[13px] font-inherit transition-colors"
            style={{
              borderColor: on ? 'var(--blue)' : 'var(--border)',
              background: on ? 'rgba(176,106,50,.10)' : 'var(--bg-card)',
              color: on ? '#8A5A18' : 'var(--text)',
              fontWeight: on ? 600 : 400,
              cursor: disabled ? 'not-allowed' : (on ? 'default' : 'pointer'),
              opacity: disabled ? 0.6 : 1,
            }}>
            {MARKET_LABEL[m] || m}
          </button>
        )
      })}
    </div>
  )
}

/** 一条市场状态（当前市场）。**判定与文案都来自后端**，前端不自己算。 */
export function MarketStatusLine({ m }: { m: MarketStatus | null | undefined }) {
  if (!m) return null
  const tone = m.state === 'open' ? 'var(--green)' : m.state === 'unknown' ? '#8A5A18' : 'var(--text-muted)'
  return (
    <span className="inline-flex items-center gap-2 text-xs" style={{ color: tone }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: tone }} />
      {m.label} · {m.state_label}
      <span style={{ color: 'var(--text-muted)' }}>
        （{m.trade_date}{m.calendar_source ? ` · ${m.calendar_source}` : ''}）
      </span>
    </span>
  )
}

/** 一次拉三个市场的状态 + 本地时间（市场状态条用）。 */
export function useMarketStatus() {
  const [markets, setMarkets] = useState<MarketStatus[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    finFetch<{ markets: MarketStatus[] }>('/markets')
      .then(d => { if (alive) setMarkets(d.markets) })
      .catch(e => { if (alive) setError(e?.message || '市场状态读取失败') })
    return () => { alive = false }
  }, [])
  return { markets, error }
}

/** 当前选中的市场（跨页面记住：localStorage；刷新保持，不改语义）。 */
export function useCurrentMarket(fallback = 'CN_A') {
  const [market, setMarket] = useState(fallback)
  useEffect(() => {
    const saved = typeof window !== 'undefined' ? localStorage.getItem('hunter_fin_market') : ''
    if (saved && saved !== fallback) setMarket(saved)
  }, [fallback])
  const pick = (m: string) => {
    setMarket(m)
    try { localStorage.setItem('hunter_fin_market', m) } catch { /* 隐私模式忽略 */ }
  }
  return [market, pick] as const
}

/** 市场 → 金额格式化用的币种。取不到就回落该市场常量（仍不猜成 CNY）。 */
export function currencyOf(market: string | null | undefined, explicit?: string | null): string {
  return explicit || (market ? MARKET_CURRENCY[market] : '') || 'CNY'
}
