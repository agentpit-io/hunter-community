'use client'
import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import dynamic from 'next/dynamic'
// echarts 走 next/dynamic (2026-08-04 修复):
// 老写法直接 import 会把 ~600KB (gzip 180KB) 打进 /wx/home 主 bundle,
// 微信 WebView 首次登录时 parse+execute 2-5s。现在改为仅打开 kpred 子 tab 时才加载。
const ReactECharts = dynamic(() => import('echarts-for-react'), {
  ssr: false,
  loading: () => <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 12 }}>K 线加载中…</div>,
})
import { routeByIntent, MODE_LABEL, type TargetMode } from './intentRouter'
import { getRecommendation } from './researchRecommend'
import { WatchlistPicker, WatchlistPickerButton } from './WatchlistPicker'
import { readSwr, writeSwr } from '../../lib/swrCache'
// Agent Chat V2 走 next/dynamic 懒加载，flag off 的用户完全不下载新组件
// （对应 04-开发计划.md T-P3-13 · 首屏 JS 增量 ≤ 30KB gzip 目标）
import { useFeatureFlag } from './assistant/featureFlag'
const ResearchAssistantChatV2 = dynamic(
  () => import('./assistant/ResearchAssistantChatV2').then(m => m.ResearchAssistantChatV2),
  { ssr: false, loading: () => <div style={{ padding: 40, textAlign: 'center', color: '#7A6F63' }}>加载对话助手…</div> },
)

// ── Types ──────────────────────────────────────────────────────────────────
interface Stock { code: string; name: string; market: string; exchange: string }
interface Quote {
  code: string; name: string; price: number | null; change_pct?: number; change_amt?: number
  open?: number; prev_close?: number; high?: number; low?: number; volume?: number; amount?: number; market?: string
  ts?: string
}
interface Task { id: number; name: string; template_id: string; schedule_time: string; enabled: boolean; content_type: string; last_status?: string }
interface Template { id: string; title: string; emoji: string; description: string; default_time: string; default_content_type: string }
interface SearchResult { code: string; name: string; market: string; exchange: string; asset_type?: string }
interface KlineBar { ts: string; open: number; high: number; low: number; close: number; volume: number }
interface TsBar { time: string; price: number; avg?: number; volume?: number }
interface PriceAlert { id: number; condition_type: string; threshold: number; label: string; enabled: boolean }
interface TrueBriefSignal { source: string; title: string; content?: string; date: string | null }
interface TrueBriefStock {
  symbol: string; name: string | null; chain: string | null
  price: number | null; change_pct: number | null
  signals: TrueBriefSignal[]; signal_count: number
  alert_level: 'red' | 'yellow' | 'green' | 'grey'; in_coverage: boolean
}
interface TrueBrief { date: string; stocks: TrueBriefStock[] }
interface ProcurementSignal { title: string; content: string; url: string | null; date: string | null }
interface TrueProcurement { date: string; count: number; signals: ProcurementSignal[] }
interface OpportunityCard {
  chain: string; desc: string; match_score: number
  alert_level: 'red' | 'yellow' | 'green' | 'grey'
  signal_count: number; truth_verified: boolean; truth_summary: string
  rep_stocks: { symbol: string; name: string }[]; sectors: string[]
  category?: string
}
interface SectorMeta { id: string; name: string; count: number }

// 四步闭环: ①选股 discover → ②盯盘 watchlist → ③持仓 holding → ④挖掘 kpred
// (key 保持旧值不改, 避免旧深链/埋点失效; 仅展示层改名)
type Tab = 'watchlist' | 'kpred' | 'mine' | 'copilot' | 'discover' | 'profile' | 'holding' | 'backtest'

interface ResearchStockCtx {
  input: string;                                       setInput: (v: string) => void
  selectedCode: string;                                setSelectedCode: (v: string) => void
  selectedName: string;                                setSelectedName: (v: string) => void
}
type StockTab = '行情' | 'K线' | '分时'

const THEME  = '#B06A32'
const UP     = '#A4332B'
const DN     = '#3F6B40'
const BG     = '#F7F3EC'
const PAPER  = '#FFFDF9'
const PAPER2 = '#EFE8DC'
const INK    = '#211C18'
const INK_S  = '#4B423A'
const INK_F  = '#7A6F63'
const LINE   = '#D8CDBA'
const COPPER2= '#D4925A'
const HEADER_BG = 'linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%)'
const SERIF  = '"Songti SC","Source Han Serif SC",Georgia,serif'

// ── SVG Tab Icons ──────────────────────────────────────────────────────────
function IconWatchlist({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3,17 8,10 13,13 19,6" />
      <line x1="3" y1="20" x2="21" y2="20" />
    </svg>
  )
}
function IconBell({ hasAlert }: { hasAlert: boolean }) {
  const c = hasAlert ? THEME : INK_F
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={hasAlert ? 2 : 1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      {hasAlert && <circle cx="18" cy="5" r="3.5" fill={THEME} stroke="#fff" strokeWidth="1" />}
    </svg>
  )
}
function IconKPred({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="12" width="4" height="9" rx="1" />
      <rect x="10" y="6" width="4" height="15" rx="1" />
      <rect x="17" y="9" width="4" height="12" rx="1" />
    </svg>
  )
}
function IconMine({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
    </svg>
  )
}
function IconBacktest({ active }: { active: boolean }) {
  // 打钩的走势线 = 事后验证
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17l5-6 4 3 4-6" />
      <polyline points="14.5 19 17 21.5 21.5 16" />
    </svg>
  )
}
function IconCopilot({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M9 9h6M9 12h5M9 15h3" />
      <circle cx="17.5" cy="17.5" r="2.5" />
      <line x1="16.3" y1="18.7" x2="14.5" y2="20.5" />
    </svg>
  )
}
function IconPortfolio({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 7H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1Z" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <line x1="3" y1="12" x2="21" y2="12" />
    </svg>
  )
}

function IconDiscover({ active }: { active: boolean }) {
  const c = active ? THEME : INK_F, w = active ? 2 : 1.5
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26" />
    </svg>
  )
}
function IconAI() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3,18 8,11 13,14 19,7" />
      <circle cx="19" cy="7" r="2.5" />
      <line x1="20.8" y1="9" x2="23" y2="11.2" />
    </svg>
  )
}
function IconShieldUser() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2L3 6v6c0 5 4 9.3 9 10.5C17 21.3 21 17 21 12V6L12 2z" />
      <circle cx="12" cy="10" r="3" />
      <path d="M6.5 19.5c.9-2.4 3-4 5.5-4s4.6 1.6 5.5 4" />
    </svg>
  )
}
function IconDeer() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={INK_S} strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round">
      {/* 头部轮廓：下宽上尖的六边形脸 */}
      <path d="M9 21 Q7 18 7.5 14.5 Q9 11 12 11 Q15 11 16.5 14.5 Q17 18 15 21 Q13.5 22.5 12 22.5 Q10.5 22.5 9 21Z" />
      {/* 眼睛：logo 风格斜线眼 */}
      <path d="M10.2 15.8 L10.9 14.8 L10.4 16.8" />
      <path d="M13.8 15.8 L13.1 14.8 L13.6 16.8" />
      {/* 下颌/颈部线条 */}
      <path d="M10 21.5 Q12 23 14 21.5" />
      {/* ── 左鹿角 ── */}
      {/* 主干：从头顶左侧大幅弯向左上 */}
      <path d="M9.5 12 C8.5 9.5 6 7 4 4" />
      {/* 内侧枝：向正上方 */}
      <path d="M7.5 8.5 C8 7 8 5.5 7.5 3.5" />
      {/* 外侧枝：从主干中部向左展开 */}
      <path d="M5.5 6 C4.5 5.5 3 6.5 2 5.5" />
      {/* 顶端小枝 */}
      <path d="M4 4 C3 3.5 2.5 4.5 2 3.5" />
      {/* ── 右鹿角（左侧镜像） ── */}
      <path d="M14.5 12 C15.5 9.5 18 7 20 4" />
      <path d="M16.5 8.5 C16 7 16 5.5 16.5 3.5" />
      <path d="M18.5 6 C19.5 5.5 21 6.5 22 5.5" />
      <path d="M20 4 C21 3.5 21.5 4.5 22 3.5" />
    </svg>
  )
}
function IconLaptop() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={INK_S} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="4" width="20" height="13" rx="2" />
      <path d="M1 21h22" />
    </svg>
  )
}
function IconLink() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={INK_S} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  )
}
function IconGlobe() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={INK_S} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}
// ── Push Tab & Signal SVG Icons ───────────────────────────────────────────
function IconNewspaper() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="7" y1="8" x2="17" y2="8" />
      <rect x="7" y="11" width="4" height="4" rx="0.5" />
      <rect x="13" y="11" width="4" height="4" rx="0.5" />
      <line x1="7" y1="17" x2="17" y2="17" />
    </svg>
  )
}
function IconBarChartColored() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="9" width="5" height="12" rx="1" fill={UP} />
      <rect x="9.5" y="5" width="5" height="16" rx="1" fill={DN} />
      <rect x="16" y="12" width="5" height="9" rx="1" fill={THEME} />
      <line x1="2" y1="22" x2="22" y2="22" stroke={THEME} strokeWidth="1.6" />
    </svg>
  )
}
function IconTrendBox() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <polyline points="7,16 11,11 15,13 17,10" />
      <polyline points="14,9 17,9 17,12" />
    </svg>
  )
}
function IconMoneyFlow() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="7" x2="12" y2="17" />
      <line x1="9" y1="10.5" x2="15" y2="10.5" />
      <line x1="9" y1="13.5" x2="15" y2="13.5" />
      <polyline points="9,5 12,3 15,5" />
      <path d="M4 12a8 8 0 0 0 13.66 5.66" />
      <polyline points="19,15 19.5,18.5 16,18" />
    </svg>
  )
}
function IconCalendarCheck() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <polyline points="8,15 11,18 16,13" />
    </svg>
  )
}
function IconClockSm() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ display: 'inline-block', verticalAlign: 'middle', marginTop: -1 }}>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12,6 12,12 16,14" />
    </svg>
  )
}
// Signal icons
function IconCPI() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="11" width="5" height="10" rx="1" />
      <rect x="9" y="6" width="5" height="15" rx="1" />
      <rect x="16" y="9" width="5" height="12" rx="1" />
      <line x1="1" y1="22" x2="23" y2="22" />
    </svg>
  )
}
function IconOilDrop() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-13z" />
    </svg>
  )
}
function IconBuilding() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="2,9 12,3 22,9" />
      <rect x="4" y="9" width="2.5" height="10" rx="0.5" />
      <rect x="8.75" y="9" width="2.5" height="10" rx="0.5" />
      <rect x="13.5" y="9" width="2.5" height="10" rx="0.5" />
      <rect x="18" y="9" width="2" height="10" rx="0.5" />
      <line x1="2" y1="19" x2="22" y2="19" />
      <line x1="1" y1="21" x2="23" y2="21" />
    </svg>
  )
}
function IconRocket() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2s4 3 4 10v4l-4 4-4-4v-4c0-7 4-10 4-10z" />
      <circle cx="12" cy="10" r="1.5" />
      <path d="M8 14c-2 .5-3 2-3 3l2-1" />
      <path d="M16 14c2 .5 3 2 3 3l-2-1" />
    </svg>
  )
}
function IconExchangeArrows() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={THEME} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="17,4 21,8 17,12" />
      <path d="M3 8h18" />
      <polyline points="7,12 3,16 7,20" />
      <path d="M21 16H3" />
    </svg>
  )
}

const PUSH_ICON_MAP: Record<string, React.ReactNode> = {
  daily_news_evening: <IconNewspaper />,
  watchlist_morning:  <IconBarChartColored />,
  close_review:       <IconTrendBox />,
  fundflow_alert:     <IconMoneyFlow />,
  weekly_summary:     <IconCalendarCheck />,
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt(v: number | null | undefined, d = 2) {
  if (v == null || isNaN(Number(v))) return '--'
  return Number(v).toFixed(d)
}
function fmtAmt(v?: number) {
  if (!v) return '--'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万'
  return String(v)
}
function priceColor(pct?: number) { return pct == null ? '#333' : pct > 0 ? UP : pct < 0 ? DN : '#333' }

// 产品介绍卡片（Tab 顶部，可关闭后 localStorage 记住）
function IntroCard({ storageKey, icon, title, description, badges, gradient, border, accent, textColor }: {
  storageKey: string; icon: string; title: string; description: string; badges: string[]
  gradient: string; border: string; accent: string; textColor: string
}) {
  const [visible, setVisible] = useState(true)
  useEffect(() => {
    try { if (localStorage.getItem(storageKey) === 'closed') setVisible(false) } catch { /* ignore */ }
  }, [storageKey])
  const close = () => {
    setVisible(false)
    try { localStorage.setItem(storageKey, 'closed') } catch { /* ignore */ }
  }
  if (!visible) return null
  return (
    <div style={{ marginBottom: 10, padding: '9px 12px 8px', background: gradient, borderRadius: 12, border: `1px solid ${border}`, position: 'relative' }}>
      <button onClick={close} aria-label="关闭"
        style={{ position: 'absolute', top: 4, right: 6, background: 'none', border: 'none', color: textColor, fontSize: 15, cursor: 'pointer', opacity: 0.45, padding: '2px 6px', lineHeight: 1 }}>×</button>
      <div style={{ fontSize: 12, fontWeight: 700, color: accent, marginBottom: 4, paddingRight: 18 }}>
        {icon} {title}
      </div>
      <div style={{ fontSize: 11, color: textColor, lineHeight: 1.5, marginBottom: 6 }}>
        {description}
      </div>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        {badges.map(b => (
          <span key={b} style={{ fontSize: 10, padding: '2px 7px', borderRadius: 6, background: 'rgba(255,255,255,0.65)', color: accent, fontWeight: 600 }}>{b}</span>
        ))}
      </div>
    </div>
  )
}

// 带超时的 JSON fetch：同时用 AbortController（取消底层网络）+ Promise.race（覆盖 body 解析卡死）
async function fetchJsonWithTimeout<T = unknown>(url: string, init: RequestInit = {}, timeoutMs = 10000): Promise<T> {
  const ctrl = new AbortController()
  const timeoutErr = new Error('timeout')
  const timerP = new Promise<never>((_, reject) => setTimeout(() => { ctrl.abort(); reject(timeoutErr) }, timeoutMs))
  const req = fetch(url, { ...init, signal: ctrl.signal, cache: 'no-store' })
  try {
    const r = await Promise.race([req, timerP])
    const j = await Promise.race([r.json(), timerP])
    return j as T
  } catch (e) {
    ctrl.abort()
    throw e
  }
}

// 股票搜索专用: 25s 超时 + sessionStorage 5min 缓存 + 首次失败自动重试 1 次。
// 对 4G 弱网 + 微信 X5 keepalive 抖动更宽容,几乎消除"查询超时(10 秒)"误报。
type SearchItem = { code: string; name: string; market?: string; exchange?: string }
interface SearchStockOpts {
  headers?: HeadersInit
  onSlowNetwork?: () => void   // 8s 时触发,提示"网络较慢,正在重试"
}
async function searchStockWithCache(q: string, opts: SearchStockOpts = {}): Promise<SearchItem[]> {
  const key = `hunter_search:${q.trim().toLowerCase()}`
  // 5 分钟本地缓存, 反复输入同一 code 命中率高
  try {
    const raw = sessionStorage.getItem(key)
    if (raw) {
      const cached = JSON.parse(raw) as { at: number; items: SearchItem[] }
      if (Date.now() - cached.at < 5 * 60 * 1000) return cached.items || []
    }
  } catch {}

  const url = `/api/online-analysis/search-stock?q=${encodeURIComponent(q)}&limit=8`
  const init = { headers: opts.headers || {} }
  const doFetch = () => fetchJsonWithTimeout<{ items?: SearchItem[] }>(url, init, 25000)

  // 8s 后若还没回来, 触发慢网络提示
  const slowT = opts.onSlowNetwork
    ? setTimeout(() => { try { opts.onSlowNetwork!() } catch {} }, 8000)
    : null

  let d: { items?: SearchItem[] } | null = null
  try {
    d = await doFetch()
  } catch (e) {
    // 首次失败(timeout / 网络异常) 自动重试 1 次
    const isTimeout = e instanceof Error && (e.message === 'timeout' || e.name === 'AbortError')
    if (isTimeout) {
      try { d = await doFetch() } catch (e2) { throw e2 }
    } else {
      throw e
    }
  } finally {
    if (slowT) clearTimeout(slowT)
  }

  const items = (d?.items) || []
  try { sessionStorage.setItem(key, JSON.stringify({ at: Date.now(), items })) } catch {}
  return items
}

// ── Simple SVG line chart ──────────────────────────────────────────────────
function LineChart({ values, color = THEME, height = 200 }: { values: number[]; color?: string; height?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || values.length < 2) return
    const dpr = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    if (!rect.width) return
    canvas.width = rect.width * dpr
    canvas.height = rect.height * dpr
    const ctx = canvas.getContext('2d')!
    ctx.scale(dpr, dpr)
    const W = rect.width, H = rect.height
    const PAD_L = 54, PAD_R = 6, PAD_T = 8, PAD_B = 6
    const cW = W - PAD_L - PAD_R, cH = H - PAD_T - PAD_B
    const min = Math.min(...values), max = Math.max(...values)
    const range = max - min || 1
    const mid = (min + max) / 2
    const px = (i: number) => PAD_L + (i / (values.length - 1)) * cW
    const py = (v: number) => PAD_T + (1 - (v - min) / range) * cH
    const hexRgba = (hex: string, a: number) => {
      const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16)
      return `rgba(${r},${g},${b},${a})`
    }
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, W, H)
    ctx.strokeStyle = '#f0f0f0'
    ctx.lineWidth = 0.5
    for (const v of [max, mid, min]) {
      ctx.beginPath(); ctx.moveTo(PAD_L, py(v)); ctx.lineTo(W - PAD_R, py(v)); ctx.stroke()
    }
    ctx.fillStyle = '#bbb'
    ctx.font = `${10 * Math.min(dpr, 2)}px -apple-system,sans-serif`
    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.scale(1, 1)
    ctx.scale(dpr, dpr)
    ctx.font = '10px -apple-system,sans-serif'
    ctx.textAlign = 'right'
    for (const v of [max, mid, min]) {
      ctx.fillText(v.toFixed(2), PAD_L - 4, py(v) + 4)
    }
    const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + cH)
    grad.addColorStop(0, hexRgba(color.length === 7 ? color : THEME, 0.28))
    grad.addColorStop(1, hexRgba(color.length === 7 ? color : THEME, 0))
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.moveTo(px(0), py(values[0]))
    for (let i = 1; i < values.length; i++) ctx.lineTo(px(i), py(values[i]))
    ctx.lineTo(px(values.length - 1), PAD_T + cH)
    ctx.lineTo(PAD_L, PAD_T + cH)
    ctx.closePath(); ctx.fill()
    ctx.strokeStyle = color.length === 7 ? color : THEME
    ctx.lineWidth = 1.8
    ctx.lineJoin = 'round'
    ctx.beginPath()
    ctx.moveTo(px(0), py(values[0]))
    for (let i = 1; i < values.length; i++) ctx.lineTo(px(i), py(values[i]))
    ctx.stroke()
  }, [values, color, height])
  if (values.length < 2) return <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', fontSize: 13 }}>暂无数据</div>
  return <canvas ref={canvasRef} style={{ width: '100%', height, display: 'block' }} />
}


// ── K线图 (Canvas) ────────────────────────────────────────────────────────
function KlineChart({ bars }: { bars: KlineBar[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !bars.length) return
    const dpr = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    if (!rect.width) return
    canvas.width = rect.width * dpr
    canvas.height = rect.height * dpr
    const ctx = canvas.getContext('2d')!
    ctx.scale(dpr, dpr)
    const W = rect.width, H = rect.height
    const PAD_L = 54, PAD_R = 6, PAD_T = 10, PAD_B = 22
    const cW = W - PAD_L - PAD_R, cH = H - PAD_T - PAD_B
    const recent = bars.slice(-30)
    const allPrices = recent.flatMap(b => [b.high, b.low])
    const min = Math.min(...allPrices), max = Math.max(...allPrices)
    const range = max - min || 1
    const mid = (min + max) / 2
    const y = (v: number) => PAD_T + (1 - (v - min) / range) * cH
    const bx = cW / recent.length
    const bw = Math.max(3, bx * 0.62)
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, W, H)
    // 网格
    ctx.strokeStyle = '#f0f0f0'; ctx.lineWidth = 0.5
    for (const v of [max, mid, min]) {
      ctx.beginPath(); ctx.moveTo(PAD_L, y(v)); ctx.lineTo(W - PAD_R, y(v)); ctx.stroke()
    }
    // Y轴价格
    ctx.fillStyle = '#bbb'; ctx.font = '10px -apple-system,sans-serif'; ctx.textAlign = 'right'
    for (const v of [max, mid, min]) ctx.fillText(v.toFixed(2), PAD_L - 4, y(v) + 4)
    // 蜡烛
    recent.forEach((b, i) => {
      const up = b.close >= b.open
      const clr = up ? '#e84040' : '#1fa351'
      const cx = PAD_L + i * bx + bx / 2
      ctx.strokeStyle = clr; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(cx, y(b.high)); ctx.lineTo(cx, y(b.low)); ctx.stroke()
      ctx.fillStyle = clr
      const top = Math.min(y(b.open), y(b.close))
      const bh = Math.max(1.5, Math.abs(y(b.open) - y(b.close)))
      ctx.fillRect(cx - bw / 2, top, bw, bh)
    })
    // X轴日期
    ctx.fillStyle = '#bbb'; ctx.font = '9px -apple-system,sans-serif'; ctx.textAlign = 'center'
    for (const i of [0, Math.floor(recent.length / 2), recent.length - 1]) {
      if (recent[i]) ctx.fillText(recent[i].ts.slice(5), PAD_L + i * bx + bx / 2, H - 5)
    }
  }, [bars])
  if (!bars.length) return <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', fontSize: 13 }}>暂无K线数据</div>
  return <canvas ref={canvasRef} style={{ width: '100%', height: 240, display: 'block' }} />
}


// ── 登录页 ─────────────────────────────────────────────────────────────────
function LoginScreen({ onLogin }: { onLogin: (token: string, email: string) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  async function doLogin() {
    if (!email.includes('@')) { setMsg('请输入正确的邮箱地址'); return }
    if (password.length < 6) { setMsg('密码至少 6 位'); return }
    setLoading(true); setMsg('')
    try {
      const res = await fetch('/api/wx/mobile/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (res.ok && data.token) {
        localStorage.setItem('hunter_token', data.token)
        onLogin(data.token, data.user?.email || email)
      } else {
        setMsg(data.error || '登录失败，请检查账号密码')
      }
    } catch { setMsg('网络错误，请稍后重试') }
    setLoading(false)
  }

  const inp: React.CSSProperties = { width: '100%', height: 48, padding: '0 14px', boxSizing: 'border-box', border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 15, background: BG, outline: 'none', color: INK, fontFamily: SERIF }

  return (
    <div style={{ minHeight: '100vh', background: `linear-gradient(160deg,${BG} 0%,${PAPER2} 100%)`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', fontFamily: SERIF, padding: '0 24px' }}>
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <img src="/logo-hunter.png" alt="猎鹿人" style={{ width: 80, height: 80, borderRadius: '50%', marginBottom: 12, objectFit: 'cover' }} />
        <div style={{ fontSize: 23, fontWeight: 800, color: INK }}>猎鹿人 · Hunter</div>
        <div style={{ fontSize: 13, color: INK_F, marginTop: 6 }}>agentpit.io · 实时行情 · 持仓预警</div>
      </div>
      <div style={{ width: '100%', maxWidth: 380, background: PAPER, borderRadius: 20, padding: '28px 24px 24px', boxShadow: '0 8px 32px rgba(50,35,10,.12)', border: `1px solid ${LINE}` }}>
        <div style={{ fontSize: 17, fontWeight: 700, color: INK, marginBottom: 20 }}>登录账号</div>
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, color: INK_S, marginBottom: 6 }}>邮箱</div>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} onKeyDown={e => e.key === 'Enter' && doLogin()} placeholder="AgentPit 账号邮箱" style={inp} />
        </div>
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 13, color: INK_S, marginBottom: 6 }}>密码</div>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} onKeyDown={e => e.key === 'Enter' && doLogin()} placeholder="请输入密码" style={inp} />
        </div>
        {msg && <div style={{ fontSize: 13, color: '#e74c3c', marginBottom: 14, textAlign: 'center' }}>{msg}</div>}
        <button onClick={doLogin} disabled={loading} style={{ width: '100%', height: 50, background: loading ? COPPER2 : THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 16, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer' }}>
          {loading ? '登录中...' : '登 录'}
        </button>
        <div style={{ marginTop: 16, fontSize: 12, color: INK_F, textAlign: 'center', lineHeight: 1.7 }}>
          使用 AgentPit 账号登录 ·{' '}
          <a href="https://agentpit.io" target="_blank" style={{ color: THEME, textDecoration: 'none' }}>注册账号</a>
        </div>
      </div>
    </div>
  )
}

// ── 股票详情页 ─────────────────────────────────────────────────────────────
function StockDetail({ stock, quote, token, onBack }: { stock: Stock; quote: Quote | undefined; token: string; onBack: () => void }) {
  const [stab, setStab] = useState<StockTab>('行情')
  const [klines, setKlines] = useState<KlineBar[]>([])
  const [ts, setTs] = useState<TsBar[]>([])
  const [loading, setLoading] = useState(false)
  const h = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  useEffect(() => {
    if (stab === 'K线' && !klines.length) {
      setLoading(true)
      fetch(`/api/kline/${stock.code}?period=daily&limit=60`, { headers: h })
        .then(r => r.json()).then(d => setKlines(Array.isArray(d) ? d : [])).catch(() => {}).finally(() => setLoading(false))
    }
    if (stab === '分时' && !ts.length) {
      setLoading(true)
      fetch(`/api/timeshare/${stock.code}`, { headers: h })
        .then(r => r.json()).then(d => setTs(Array.isArray(d) ? d.map((r: {ts:string;close:number;volume?:number}) => ({ time: r.ts, price: r.close, volume: r.volume })) : [])).catch(() => {}).finally(() => setLoading(false))
    }
  }, [stab]) // eslint-disable-line

  const pct = quote?.change_pct
  const clr = priceColor(pct)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 顶栏 */}
      <div style={{ background: HEADER_BG, padding: '12px 16px', borderBottom: `2px solid ${THEME}`, display: 'flex', alignItems: 'center', gap: 10, position: 'sticky', top: 0, zIndex: 20 }}>
        <button onClick={onBack} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: PAPER, padding: '0 4px' }}>‹</button>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: PAPER, fontFamily: SERIF }}>{stock.name}</div>
          <div style={{ fontSize: 12, color: COPPER2 }}>{stock.code} · {stock.market === 'A' ? 'A股' : stock.market === 'HK' ? '港股' : '美股'}</div>
        </div>
        {quote?.price != null && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: clr }}>{quote.price}</div>
            <div style={{ fontSize: 13, color: clr }}>{pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) + '%' : '--'}</div>
          </div>
        )}
      </div>

      {/* Tab 切换 */}
      <div style={{ background: PAPER, display: 'flex', borderBottom: `1px solid ${LINE}` }}>
        {(['行情', 'K线', '分时'] as StockTab[]).map(t => (
          <button key={t} onClick={() => setStab(t)} style={{ flex: 1, padding: '10px 0', background: 'none', border: 'none', fontSize: 14, fontWeight: stab === t ? 600 : 400, color: stab === t ? THEME : INK_F, borderBottom: stab === t ? `2px solid ${THEME}` : '2px solid transparent', cursor: 'pointer', fontFamily: SERIF }}>{t}</button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 20 }}>
        {/* 行情 */}
        {stab === '行情' && (
          <div style={{ padding: '16px 12px' }}>
            {/* 更新时间徽标 · 非今日红字警示 */}
            {(() => {
              const ts = quote?.ts || ''
              if (!ts) return null
              const tsDate = ts.slice(0, 10)  // YYYY-MM-DD
              const todayCST = new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10)
              const isStale = tsDate !== todayCST
              // 尝试解析 ISO 时间；只有日期则用 tsDate
              let tsShow = tsDate
              if (ts.includes('T')) {
                try {
                  const d = new Date(ts)
                  const s = d.toLocaleString('zh-CN', {
                    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit',
                  })
                  tsShow = s.replace(/\//g, '-')
                } catch { /* 保留 tsDate */ }
              }
              return (
                <div style={{
                  marginBottom: 10, padding: '8px 12px',
                  background: isStale ? '#FEF2F2' : PAPER2,
                  border: `1px solid ${isStale ? '#FCA5A5' : LINE}`,
                  borderRadius: 8, fontSize: 12, color: isStale ? UP : INK_F,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span>{isStale ? '⚠️' : '🕒'}</span>
                  <span>
                    更新于 <b>{tsShow}</b>
                    {isStale && ' · 数据可能过期，请稍后再看'}
                  </span>
                </div>
              )
            })()}
            <div style={{ background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
              {[
                ['现价', quote?.price != null ? String(quote.price) : '--'],
                ['涨跌幅', pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) + '%' : '--'],
                ['涨跌额', quote?.change_amt != null ? (quote.change_amt > 0 ? '+' : '') + fmt(quote.change_amt) : '--'],
                ['今开', fmt(quote?.open)],
                ['昨收', fmt(quote?.prev_close)],
                ['最高', fmt(quote?.high)],
                ['最低', fmt(quote?.low)],
                ['成交量', fmtAmt(quote?.volume)],
                ['成交额', fmtAmt(quote?.amount)],
                ['市场', quote?.market === 'A' ? 'A股' : quote?.market === 'HK' ? '港股' : quote?.market || '--'],
              ].map(([label, val], i, arr) => (
                <div key={label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: i < arr.length - 1 ? `1px solid ${PAPER2}` : 'none' }}>
                  <span style={{ fontSize: 14, color: INK_F }}>{label}</span>
                  <span style={{ fontSize: 14, fontWeight: 500, color: label === '涨跌幅' || label === '涨跌额' ? clr : INK }}>{val}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {/* K线 */}
        {stab === 'K线' && (
          <div style={{ padding: '16px 12px' }}>
            <div style={{ background: PAPER, borderRadius: 14, padding: '16px 12px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
              <div style={{ fontSize: 13, color: INK_F, marginBottom: 8 }}>日K线（近60日）</div>
              {loading ? <div style={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', color: INK_F }}>加载中...</div> : <KlineChart bars={klines} />}
              {klines.length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 0', marginTop: 12 }}>
                  {[
                    ['最新收盘', fmt(klines[klines.length - 1]?.close)],
                    ['前日收盘', fmt(klines[klines.length - 2]?.close)],
                    ['区间最高', fmt(Math.max(...klines.map(k => k.high)))],
                    ['区间最低', fmt(Math.min(...klines.map(k => k.low)))],
                  ].map(([l, v]) => (
                    <div key={l}><span style={{ fontSize: 11, color: INK_F }}>{l}  </span><span style={{ fontSize: 13, color: INK_S }}>{v}</span></div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
        {/* 分时 */}
        {stab === '分时' && (
          <div style={{ padding: '16px 12px' }}>
            <div style={{ background: PAPER, borderRadius: 14, padding: '16px 12px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
              <div style={{ fontSize: 13, color: INK_F, marginBottom: 8 }}>今日分时</div>
              {loading ? <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', color: INK_F }}>加载中...</div>
                : ts.length > 0
                  ? <LineChart values={ts.map(t => t.price)} color={clr !== '#333' ? clr : THEME} height={200} />
                  : <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', fontSize: 13 }}>暂无分时数据（非交易时间）</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── 宏观信号订阅 Section ───────────────────────────────────────────────────
const SIGNAL_LIST: { type: string; Icon: React.FC; label: string; desc: string }[] = [
  { type: 'cpi',        Icon: IconCPI,            label: '美国 CPI',   desc: '通胀数据超预期/低预期时触发' },
  { type: 'oil',        Icon: IconOilDrop,        label: '布伦特油价', desc: '日内涨跌幅超阈值时触发' },
  { type: 'fomc',       Icon: IconBuilding,       label: 'FOMC 决议',  desc: '美联储利率决议 + 鹰鸽分析' },
  { type: 'spacex',     Icon: IconRocket,         label: 'SpaceX IPO', desc: '上市表现 + A股航天板块联动' },
  { type: 'northbound', Icon: IconExchangeArrows, label: '北向资金',   desc: '沪深港通大额净流入/流出' },
]

function SignalSubscriptionSection({ token }: { token: string }) {
  const [subs, setSubs] = useState<Record<string, boolean>>({})
  const [toggling, setToggling] = useState<string | null>(null)
  const h = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

  useEffect(() => {
    fetch('/api/signal-settings', { headers: h })
      .then(r => r.ok ? r.json() : {})
      .then(d => setSubs(typeof d === 'object' && d !== null ? (d as Record<string, boolean>) : {}))
      .catch(() => {})
  }, []) // eslint-disable-line

  async function toggle(type: string) {
    const next = subs[type] !== false ? false : true
    setToggling(type)
    setSubs(prev => ({ ...prev, [type]: next }))
    try {
      await fetch('/api/signal-settings', {
        method: 'POST', headers: h,
        body: JSON.stringify({ signal_type: type, enabled: next }),
      })
    } catch {}
    setToggling(null)
  }

  return (
    <div style={{ margin: '16px 0 0' }}>
      <div style={{ padding: '0 16px 8px', fontSize: 12, color: INK_F, fontWeight: 600, letterSpacing: '0.5px' }}>
        宏观信号监控
      </div>
      <div style={{ padding: '0 12px 4px', fontSize: 11, color: INK_F, lineHeight: 1.6 }}>
        信号触发时分析你的持仓影响，推送一条聚合消息到微信
      </div>
      <div style={{ background: PAPER, margin: '6px 12px 0', borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
        {SIGNAL_LIST.map((s, i) => {
          const enabled = subs[s.type] !== false
          return (
            <div key={s.type} style={{ padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: i < SIGNAL_LIST.length - 1 ? `1px solid ${PAPER2}` : 'none' }}>
              <div style={{ width: 44, height: 44, borderRadius: 10, background: BG, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <s.Icon />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>{s.label}</div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{s.desc}</div>
              </div>
              <div
                onClick={() => toggling !== s.type && toggle(s.type)}
                style={{ width: 46, height: 26, borderRadius: 13, background: enabled ? THEME : LINE, position: 'relative', cursor: toggling === s.type ? 'wait' : 'pointer', transition: 'background .25s', flexShrink: 0 }}
              >
                <div style={{ position: 'absolute', top: 3, left: enabled ? 21 : 3, width: 20, height: 20, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 4px rgba(0,0,0,.2)', transition: 'left .25s' }} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 推送设置 Tab ───────────────────────────────────────────────────────────
function PushSetupTab({ token }: { token: string }) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(false)
  const [togglingId, setTogglingId] = useState<number | null>(null)
  const [addingTpl, setAddingTpl] = useState<string | null>(null)   // template id being added
  const [addTime, setAddTime] = useState('08:30')
  const [saving, setSaving] = useState(false)

  const h = useCallback(() => ({ Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }), [token])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [r1, r2] = await Promise.all([
        fetch('/api/push/templates', { headers: h() }),
        fetch('/api/push/tasks', { headers: h() }),
      ])
      const d1 = await r1.json()
      const d2 = await r2.json()
      setTemplates((d1.templates || []).filter((t: Template) => t.id !== 'custom'))
      setTasks(d2.tasks || [])
    } catch {}
    setLoading(false)
  }, [h])

  useEffect(() => { load() }, [load])

  async function toggleTask(task: Task) {
    setTogglingId(task.id)
    try {
      await fetch(`/api/push/tasks/${task.id}/toggle`, { method: 'PATCH', headers: h() })
      await load()
    } catch {}
    setTogglingId(null)
  }

  async function createTask(tpl: Template) {
    setSaving(true)
    try {
      await fetch('/api/push/tasks', {
        method: 'POST', headers: h(),
        body: JSON.stringify({ name: tpl.title, template_id: tpl.id, schedule_time: addTime, content_type: tpl.default_content_type, enabled: true }),
      })
      setAddingTpl(null)
      await load()
    } catch {}
    setSaving(false)
  }

  async function deleteTask(id: number) {
    if (!window.confirm('确认删除此推送任务？')) return
    try {
      await fetch(`/api/push/tasks/${id}`, { method: 'DELETE', headers: h() })
      await load()
    } catch {}
  }

  if (loading && !templates.length) return <div style={{ textAlign: 'center', padding: '80px 0', color: INK_F, fontSize: 14 }}>加载中...</div>

  const taskMap = Object.fromEntries(tasks.map(t => [t.template_id, t]))

  return (
    <div style={{ paddingBottom: 20 }}>
      <div style={{ padding: '12px 16px 6px', fontSize: 12, color: INK_F, lineHeight: 1.7 }}>
        开启后在微信服务号推送，时间可按需调整
      </div>
      {templates.map(tpl => {
        const task = taskMap[tpl.id]
        const isAdding = addingTpl === tpl.id
        return (
          <div key={tpl.id} style={{ background: PAPER, margin: '8px 12px', borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
            <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 50, height: 50, borderRadius: 12, background: BG, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: `1px solid ${LINE}` }}>
                {PUSH_ICON_MAP[tpl.id] ?? <span style={{ fontSize: 22 }}>{tpl.emoji}</span>}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 600, color: INK, fontFamily: SERIF }}>{tpl.title}</div>
                <div style={{ fontSize: 12, color: INK_F, marginTop: 2 }}>{tpl.description}</div>
                {task && (
                  <div style={{ fontSize: 12, color: INK_S, marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><IconClockSm /> 每日 {task.schedule_time}</span>
                    {task.last_status === 'ok' && <span style={{ color: THEME }}>✓ 上次成功</span>}
                    {task.last_status === 'error' && <span style={{ color: '#e74c3c' }}>✗ 上次失败</span>}
                  </div>
                )}
              </div>
              {task ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8 }}>
                  {/* 开关 */}
                  <div onClick={() => !togglingId && toggleTask(task)} style={{ width: 50, height: 28, borderRadius: 14, background: task.enabled ? THEME : LINE, position: 'relative', cursor: togglingId ? 'wait' : 'pointer', transition: 'background .25s', flexShrink: 0 }}>
                    <div style={{ position: 'absolute', top: 3, left: task.enabled ? 23 : 3, width: 22, height: 22, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 4px rgba(0,0,0,.2)', transition: 'left .25s' }} />
                  </div>
                  <button onClick={() => deleteTask(task.id)} style={{ fontSize: 11, color: '#e74c3c', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>删除</button>
                </div>
              ) : (
                <button onClick={() => { setAddingTpl(isAdding ? null : tpl.id); setAddTime(tpl.default_time) }} style={{ fontSize: 13, color: '#fff', background: isAdding ? INK_F : THEME, border: 'none', borderRadius: 8, padding: '7px 14px', cursor: 'pointer', flexShrink: 0 }}>
                  {isAdding ? '取消' : '+ 添加'}
                </button>
              )}
            </div>
            {/* 展开：添加时间选择 */}
            {isAdding && !task && (
              <div style={{ padding: '12px 16px 16px', background: PAPER2, borderTop: `1px solid ${LINE}` }}>
                <div style={{ fontSize: 13, color: INK_S, marginBottom: 8 }}>设置推送时间</div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <input type="time" value={addTime} onChange={e => setAddTime(e.target.value)} style={{ flex: 1, height: 40, padding: '0 12px', border: `1px solid ${LINE}`, borderRadius: 10, fontSize: 15, outline: 'none', color: INK }} />
                  <button onClick={() => createTask(tpl)} disabled={saving} style={{ height: 40, padding: '0 18px', background: saving ? COPPER2 : THEME, color: '#fff', border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer' }}>
                    {saving ? '保存中' : '保存'}
                  </button>
                </div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 8 }}>保存后将在每天 {addTime} 推送到微信服务号</div>
              </div>
            )}
          </div>
        )
      })}
      <SignalSubscriptionSection token={token} />
    </div>
  )
}

// ── K线预测 Tab ───────────────────────────────────────────────────────────
type KPredBar = { date: string; open: number; high: number; low: number; close: number; volume: number }
type KPredResult = { symbol: string; name: string; last_date: string; last_close: number; history: KPredBar[]; predictions: KPredBar[]; data_note?: string; kronos_skipped?: boolean }
type KProInfo = { composite_score: number; factor_return_pct: number; adj_return_pct: number; kronos_raw_return_pct: number; rating: string; confidence: string; conflict_level: string; sigma_daily_pct?: number; factors: { key: string; label: string; score: number; weight: number; contribution: number }[] }
type KProResult = KPredResult & { pro: KProInfo }

function buildMobileCandleOption(data: KPredResult) {
  // 历史 K 线只显示最近 20 根(约 4 周),让 last_date(今天)靠近视图中央,
  // 避免用户误把左侧起点(即"今天前推 40 交易日 ≈ 6 月中旬")当作预测起点
  const hist = data.history.slice(-20)
  const pred = data.predictions
  const allDates = [...hist.map(b => b.date), ...pred.map(b => b.date)]
  const histData = hist.map(b => [b.open, b.close, b.low, b.high])
  const predData = pred.map(b => [b.open, b.close, b.low, b.high])
  const predFill = new Array(hist.length).fill('-').concat(predData)
  const upColor = '#ef4444', downColor = '#22c55e'
  // last_date 通常是今天。若因数据源陈旧,距今 > 3 天,则显示警告色 + "数据截止"文案
  const todayLabel = data.last_date
  const firstPredDate = pred[0]?.date
  const lastPredDate = pred[pred.length - 1]?.date
  let staleDays = 0
  try {
    const today = new Date()
    const ld = new Date(data.last_date + 'T00:00:00')
    staleDays = Math.floor((today.getTime() - ld.getTime()) / 86400000)
  } catch {}
  const isStale = staleDays > 3
  const markLabelText = isStale
    ? `⚠数据截止 ${todayLabel}(${staleDays}天前)`
    : `📍今日 ${todayLabel}`
  const markLineColor = isStale ? '#dc2626' : '#f59e0b'
  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(15,23,42,.92)',
      borderColor: '#334155',
      textStyle: { color: '#e2e8f0', fontSize: 11 },
      formatter(params: { value: number[]; name: string; seriesIndex: number }[]) {
        const p = params[0]; if (!p?.value || p.value as unknown === '-') return ''
        const [o, c, l, h] = p.value
        const isPred = p.seriesIndex === 1
        const chg = ((c - data.last_close) / data.last_close * 100).toFixed(2)
        return `<div style="font-size:11px;line-height:1.8"><b>${p.name}${isPred ? ' <span style="color:#f59e0b">预测</span>' : ''}</b><br/>开:${o?.toFixed(2)} 收:<b style="color:${c >= o ? upColor : downColor}">${c?.toFixed(2)}</b><br/>高:${h?.toFixed(2)} 低:${l?.toFixed(2)}${isPred ? `<br/>较当前:<b style="color:${Number(chg) >= 0 ? upColor : downColor}">${Number(chg) >= 0 ? '+' : ''}${chg}%</b>` : ''}</div>`
      },
    },
    grid: { top: 10, bottom: 32, left: 52, right: 8 },
    xAxis: {
      type: 'category', data: allDates,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10, formatter: (v: string) => v.slice(5) },
      splitLine: { show: false },
    },
    yAxis: {
      scale: true,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 10 },
      splitLine: { lineStyle: { color: '#e8edf2' } },
    },
    series: [
      { name: '历史', type: 'candlestick', data: histData, itemStyle: { color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor } },
      {
        name: '预测',
        type: 'candlestick',
        data: predFill,
        itemStyle: { color: 'rgba(251,191,36,.85)', color0: 'rgba(251,191,36,.5)', borderColor: '#fbbf24', borderColor0: '#f59e0b' },
        // 「今日」/「数据截止」竖线 — 明确分割 历史/预测。
        // 若 last_date 距今 > 3 天(数据源陈旧),红色警示;否则金色正常
        markLine: {
          symbol: ['none', 'none'],
          silent: true,
          lineStyle: { color: markLineColor, type: 'solid', width: 1.5, opacity: 0.75 },
          label: {
            show: true, position: 'insideEndTop', color: markLineColor,
            fontSize: 10, fontWeight: 700,
            formatter: markLabelText,
          },
          data: [{ xAxis: todayLabel }],
        },
        // 预测区淡黄色背景 — 视觉强化 "未来" 概念
        markArea: firstPredDate && lastPredDate ? {
          silent: true,
          itemStyle: { color: 'rgba(251,191,36,0.08)' },
          data: [[
            { xAxis: firstPredDate, name: '预测区间' },
            { xAxis: lastPredDate },
          ]],
        } : undefined,
      },
      { name: '预测线', type: 'line', data: new Array(hist.length - 1).fill(null).concat([data.last_close, ...pred.map(b => b.close)]), lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.5, opacity: .6 }, symbol: 'none', z: 0 },
    ],
  }
}

/* ─────────────────────── ⑤ 回测 ───────────────────────
   与 ④挖掘·量化择时 的分工:量化择时向前看(这只股票未来会怎样),
   回测向后看(模型过去准不准)。预测数据全体共用一份,每人只是用
   自己的参数去解读 —— 所以这里所有命中率都是按本人参数现场算的。 */

type BtStock = {
  symbol: string; name: string; added_on: string | null
  n: number; hit_rate: number | null; mae: number | null; enough: boolean
}
type BtSummary = {
  sample: number; hit_rate: number | null; amt_hit_rate: number | null; mae: number | null
  stability: number | null; consistency_sample: number
  by_horizon: { horizon: number; n: number; hit_rate: number | null }[]
  stocks: BtStock[]
  tracked: number; tracking_days: number; quota: number; is_pro: boolean
  enough_sample: boolean; min_sample: number; window_days: number
  config: Record<string, number | boolean>
}

const BT_PARAMS: { key: string; label: string; hint: string; step: number; unit: string }[] = [
  { key: 'pred_len', label: '预测天数', hint: '每次预测未来几个交易日', step: 1, unit: '天' },
  { key: 'flat_band', label: '平盘带宽度', hint: '涨跌幅在此范围内视为“看平”，不计入方向对错。太窄会把噪音当错判', step: 0.1, unit: '%' },
  { key: 'rel_err_pct', label: '幅度误差·相对', hint: '|预测−实际|÷|实际|，大涨跌时有效', step: 1, unit: '%' },
  { key: 'abs_err_pp', label: '幅度误差·绝对', hint: '小涨跌时的兜底：预测0.5%实际0.1%，相对误差400%但只差0.4pp，不该判错', step: 0.1, unit: 'pp' },
  { key: 'reversal_min', label: '反转最小幅度', hint: '两次预测方向相反、且至少一侧幅度≥此值才算“改口”', step: 0.1, unit: '%' },
  { key: 'strength_delta', label: '强化/弱化差值', hint: '幅度变化超过此值才算预测强化或弱化', step: 0.1, unit: '%' },
]

function BacktestTab({ token, mode, setMode, stocks }: {
  token: string
  mode: 'board' | 'pool' | 'config'
  setMode: (m: 'board' | 'pool' | 'config') => void
  stocks: Stock[]
}) {
  const [sum, setSum] = useState<BtSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [toast, setToast] = useState('')
  const [detail, setDetail] = useState<any>(null)
  const [cfg, setCfg] = useState<Record<string, any> | null>(null)
  const [saving, setSaving] = useState(false)
  const [addInput, setAddInput] = useState('')
  const [hits, setHits] = useState<SearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)

  const H = { Authorization: `Bearer ${token}` }
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(''), 2600) }

  const load = useCallback(() => {
    if (!token) return
    setLoading(true); setErr('')
    fetch('/api/backtest/my/summary', { headers: H })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(String(r.status))))
      .then(d => { setSum(d); setCfg(d.config) })
      .catch(() => setErr('回测数据暂不可用'))
      .finally(() => setLoading(false))
  }, [token])

  useEffect(() => { load() }, [load])

  const addStock = async (code: string, name = '') => {
    const c = code.trim()
    if (!/^\d{6}$/.test(c)) { flash('请输入6位股票代码'); return }
    try {
      const r = await fetch('/api/backtest/my/pool', {
        method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: c, name }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { flash(d?.detail || '添加失败'); return }
      flash(d?.msg || '已加入')
      setAddInput(''); setHits([]); setSearched(false); load()
    } catch { flash('网络错误') }
  }

  // 支持按名称搜(输代码也行) —— 用户不该被要求背 6 位数字
  const runSearch = async () => {
    const q = addInput.trim()
    if (!q) return
    if (/^\d{6}$/.test(q)) { addStock(q); return }   // 直接是代码就别绕搜索了
    setSearching(true); setSearched(false)
    try {
      const items = await searchStockWithCache(q, { onSlowNetwork: () => flash('网络较慢，正在重试…') })
      setHits((items || []).filter(x => /^\d{6}$/.test(x.code)).slice(0, 8))
    } catch { flash('搜索失败，可直接输入6位代码') } finally {
      setSearching(false); setSearched(true)
    }
  }

  const removeStock = async (code: string) => {
    try {
      await fetch(`/api/backtest/my/pool/${code}`, { method: 'DELETE', headers: H })
      load()
    } catch { flash('网络错误') }
  }

  const saveCfg = async (patch: Record<string, any>) => {
    setSaving(true)
    try {
      const r = await fetch('/api/backtest/my/config', {
        method: 'PUT', headers: { ...H, 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { flash(d?.detail || '保存失败'); return }
      setCfg(d.config); flash(d.msg || '已保存'); load()
    } catch { flash('网络错误') } finally { setSaving(false) }
  }

  const openDetail = async (code: string) => {
    setDetail({ loading: true, symbol: code })
    try {
      const r = await fetch(`/api/backtest/my/stock/${code}`, { headers: H })
      setDetail(r.ok ? await r.json() : { error: '暂无数据', symbol: code })
    } catch { setDetail({ error: '网络错误', symbol: code }) }
  }

  const card: React.CSSProperties = {
    background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`,
    boxShadow: '0 1px 8px rgba(50,35,10,.07)', padding: 16, marginBottom: 12,
  }
  const pct = (v: number | null | undefined) => v == null ? '—' : `${v}%`

  // ── 管理股票 ──
  if (mode === 'pool') {
    const pool = sum?.stocks || []
    const quota = sum?.quota ?? 5
    const inPool = new Set(pool.map(s => s.symbol))
    const candidates = stocks.filter(s => !inPool.has(s.code) && /^\d{6}$/.test(s.code))
    return (
      <div style={{ padding: '12px 12px 24px' }}>
        {toast && <BtToast text={toast} />}
        <BtBack onClick={() => setMode('board')} title="管理股票"
          right={`${pool.length} / ${quota}`} />

        <div style={card}>
          <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 10 }}>搜索添加</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={addInput} onChange={e => { setAddInput(e.target.value); setSearched(false) }}
              onKeyDown={e => { if (e.key === 'Enter') runSearch() }}
              placeholder="股票名称或6位代码"
              style={{ flex: 1, padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, outline: 'none' }} />
            <button onClick={runSearch} disabled={searching}
              style={{ padding: '10px 18px', background: THEME, color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', opacity: searching ? .6 : 1 }}>
              {searching ? '搜索中' : '搜索'}
            </button>
          </div>
          {hits.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {hits.map(h => (
                <div key={h.code} onClick={() => addStock(h.code, h.name)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 0', borderTop: `1px solid ${LINE}`, cursor: 'pointer' }}>
                  <span style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{h.name}</span>
                  <span style={{ fontSize: 12, color: INK_F, flex: 1 }}>{h.code}</span>
                  <span style={{ fontSize: 13, color: THEME }}>+ 加入</span>
                </div>
              ))}
            </div>
          )}
          {searched && hits.length === 0 && !searching && (
            <div style={{ marginTop: 10, fontSize: 12, color: INK_F }}>没找到匹配的 A 股，可直接输入 6 位代码</div>
          )}
          {pool.length >= quota && (
            <div style={{ marginTop: 10, fontSize: 12, color: '#b45309', background: '#fffbeb', borderRadius: 8, padding: '8px 10px' }}>
              {sum?.is_pro ? `已达上限 ${quota} 只，请先移除再添加`
                : `免费版最多跟踪 ${quota} 只，升级 Pro 可跟踪 10 只`}
            </div>
          )}
        </div>

        {candidates.length > 0 && pool.length < quota && (
          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 4 }}>从我的自选添加</div>
            <div style={{ fontSize: 11, color: INK_F, marginBottom: 10 }}>点一下即可加入跟踪</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {candidates.slice(0, 30).map(s => (
                <button key={s.code} onClick={() => addStock(s.code, s.name)}
                  style={{ padding: '7px 12px', background: PAPER2, border: `1px solid ${LINE}`, borderRadius: 16, fontSize: 12, color: INK_S, cursor: 'pointer' }}>
                  + {s.name || s.code}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={card}>
          <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 10 }}>正在跟踪</div>
          {pool.length === 0 ? (
            <div style={{ fontSize: 13, color: INK_F, padding: '10px 0' }}>还没有跟踪任何股票</div>
          ) : pool.map(s => (
            <div key={s.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 0', borderBottom: `1px solid ${LINE}` }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{s.name || s.symbol}</div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>
                  {s.symbol}{s.added_on ? ` · ${s.added_on} 加入` : ''} · 样本 {s.n} 条
                </div>
              </div>
              <button onClick={() => removeStock(s.symbol)}
                style={{ background: 'none', border: `1px solid ${LINE}`, color: '#e74c3c', fontSize: 12, borderRadius: 6, padding: '5px 12px', cursor: 'pointer' }}>移除</button>
            </div>
          ))}
          <div style={{ fontSize: 11, color: INK_F, marginTop: 12, lineHeight: 1.7 }}>
            ⓘ 新加入的股票今晚 18:20 首次预测，明天可看到第一条回测记录；<br />
            积累约 2 周（{sum?.min_sample ?? 20}+ 条）后统计才有参考价值。
          </div>
        </div>
      </div>
    )
  }

  // ── 回测设置 ──
  if (mode === 'config') {
    const c = cfg || {}
    return (
      <div style={{ padding: '12px 12px 24px' }}>
        {toast && <BtToast text={toast} />}
        <BtBack onClick={() => setMode('board')} title="回测设置" />

        <div style={{ ...card, background: '#fffbeb', border: '1px solid #fde68a' }}>
          <div style={{ fontSize: 12, color: '#92400e', lineHeight: 1.7 }}>
            这些参数只决定<b>“算不算命中”的口径</b>，不影响预测本身。<br />
            改完之后，过去所有记录会立刻按新口径重新计算。
          </div>
        </div>

        {BT_PARAMS.map(p => (
          <div key={p.key} style={card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ flex: 1, fontSize: 14, fontWeight: 600, color: INK }}>{p.label}</div>
              <input type="number" step={p.step} value={String(c[p.key] ?? '')}
                onChange={e => setCfg({ ...c, [p.key]: e.target.value === '' ? '' : Number(e.target.value) })}
                style={{ width: 84, padding: '8px 10px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, textAlign: 'right', outline: 'none' }} />
              <span style={{ fontSize: 12, color: INK_F, width: 22 }}>{p.unit}</span>
            </div>
            <div style={{ fontSize: 11, color: INK_F, marginTop: 7, lineHeight: 1.6 }}>{p.hint}</div>
          </div>
        ))}

        <div style={card}>
          <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 12 }}>数据过滤</div>
          {[
            { key: 'skip_limit', label: '排除涨跌停', hint: '涨停买不进、跌停卖不出，预测再准也无意义' },
            { key: 'skip_st', label: '排除 ST 股', hint: 'ST 股涨跌停规则不同（±5%），混在一起会失真' },
          ].map(x => (
            <label key={x.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 0', cursor: 'pointer' }}>
              <input type="checkbox" checked={!!c[x.key]}
                onChange={e => setCfg({ ...c, [x.key]: e.target.checked })}
                style={{ width: 17, height: 17, marginTop: 2, accentColor: THEME }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, color: INK }}>{x.label}</div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{x.hint}</div>
              </div>
            </label>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={async () => {
            await fetch('/api/backtest/my/config', { method: 'DELETE', headers: H })
            flash('已恢复默认'); load()
          }} style={{ flex: 1, padding: '13px 0', background: PAPER, color: INK_S, border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 15, cursor: 'pointer' }}>
            恢复默认
          </button>
          <button disabled={saving} onClick={() => saveCfg(cfg || {})}
            style={{ flex: 2, padding: '13px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer', opacity: saving ? .6 : 1 }}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    )
  }

  // ── 成绩单主页 ──
  return (
    <div style={{ padding: '12px 12px 24px' }}>
      {toast && <BtToast text={toast} />}

      {loading && !sum && <div style={{ textAlign: 'center', padding: 40, color: INK_F, fontSize: 13 }}>加载中…</div>}
      {err && <div style={{ ...card, color: '#e74c3c', fontSize: 13 }}>{err}</div>}

      {sum && sum.tracked === 0 && (
        <div style={{ textAlign: 'center', padding: '52px 20px 40px' }}>
          <div style={{ fontSize: 36, marginBottom: 16 }}>📊</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF, marginBottom: 10 }}>还没有跟踪任何股票</div>
          <div style={{ fontSize: 13, color: INK_F, marginBottom: 26, lineHeight: 1.8 }}>
            把你关心的股票加进来，系统每天收盘后<br />自动预测并核对准确率
          </div>
          <button onClick={() => setMode('pool')}
            style={{ padding: '13px 36px', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>
            + 添加股票
          </button>
        </div>
      )}

      {sum && sum.tracked > 0 && (
        <>
          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 14 }}>我的成绩单</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              {[
                { l: '方向命中率', v: pct(sum.hit_rate), big: true },
                { l: '幅度命中率', v: pct(sum.amt_hit_rate), big: true },
                { l: '平均误差', v: sum.mae == null ? '—' : `${sum.mae}pp` },
                { l: '稳定性', v: pct(sum.stability) },
              ].map(x => (
                <div key={x.l}>
                  <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>{x.l}</div>
                  <div style={{ fontSize: x.big ? 23 : 18, fontWeight: 700, color: x.big ? THEME : INK, fontFamily: SERIF }}>{x.v}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: INK_F, marginTop: 14, paddingTop: 12, borderTop: `1px solid ${LINE}` }}>
              样本 {sum.sample} 条 · 跟踪 {sum.tracked} 只 · 已积累 {sum.tracking_days ?? 0} 天
              {sum.sample === 0 && (sum.tracking_days ?? 0) < 2 && <span> · 明天开始出数据</span>}
            </div>
            {!sum.enough_sample && (
              <div style={{ marginTop: 10, fontSize: 12, color: '#92400e', background: '#fffbeb', borderRadius: 8, padding: '9px 10px', lineHeight: 1.6 }}>
                ⚠ 样本不足 {sum.min_sample} 条，现在的数字可能是运气，说明不了模型能力，建议积累 2 周以上再看
              </div>
            )}
          </div>

          {sum.by_horizon.length > 0 && (
            <div style={card}>
              <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 4 }}>预测能力衰减</div>
              <div style={{ fontSize: 11, color: INK_F, marginBottom: 14 }}>越往后越难猜，这是正常现象</div>
              <div style={{ display: 'flex', gap: 6 }}>
                {sum.by_horizon.map(h => (
                  <div key={h.horizon} style={{ flex: 1, textAlign: 'center' }}>
                    <div style={{ height: 54, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
                      <div style={{
                        width: '72%', borderRadius: '4px 4px 0 0',
                        height: `${Math.max(4, Math.min(100, h.hit_rate ?? 0))}%`,
                        background: (h.hit_rate ?? 0) >= 55 ? THEME : LINE,
                      }} />
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: INK, marginTop: 5 }}>{h.hit_rate == null ? '—' : `${h.hit_rate}%`}</div>
                    <div style={{ fontSize: 10, color: INK_F, marginTop: 1 }}>T+{h.horizon}</div>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: INK_F, marginTop: 12, lineHeight: 1.6 }}>
                股价短期方向接近随机，50% 是瞎猜基线，能稳定超过 55% 就有参考价值
              </div>
            </div>
          )}

          <div style={card}>
            <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 10 }}>我的股票表现</div>
            {sum.stocks.map(s => (
              <div key={s.symbol} onClick={() => openDetail(s.symbol)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 0', borderBottom: `1px solid ${LINE}`, cursor: 'pointer' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{s.name || s.symbol}</div>
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{s.symbol} · 样本 {s.n} 条</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  {s.n === 0 ? (
                    <span style={{ fontSize: 12, color: INK_F }}>等待数据</span>
                  ) : !s.enough ? (
                    <span style={{ fontSize: 12, color: INK_F }}>样本不足</span>
                  ) : (
                    <span style={{ fontSize: 16, fontWeight: 700, color: (s.hit_rate ?? 0) >= 55 ? THEME : INK_S, fontFamily: SERIF }}>{s.hit_rate}%</span>
                  )}
                </div>
                <span style={{ color: INK_F, fontSize: 15 }}>›</span>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
            <button onClick={() => setMode('config')}
              style={{ flex: 1, padding: '13px 0', background: PAPER, color: INK_S, border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 14, cursor: 'pointer' }}>⚙ 回测设置</button>
            <button onClick={() => setMode('pool')}
              style={{ flex: 1, padding: '13px 0', background: PAPER, color: INK_S, border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 14, cursor: 'pointer' }}>+ 管理股票</button>
          </div>

          <div style={{ fontSize: 11, color: INK_F, textAlign: 'center', lineHeight: 1.8, padding: '0 12px' }}>
            历史准确率不预示未来表现，不构成投资建议
          </div>
        </>
      )}

      {detail && <BtDetailModal d={detail} onClose={() => setDetail(null)} />}
    </div>
  )
}

function BtToast({ text }: { text: string }) {
  return (
    <div style={{
      position: 'fixed', top: 70, left: '50%', transform: 'translateX(-50%)', zIndex: 300,
      background: 'rgba(30,25,15,.92)', color: '#fff', fontSize: 13, padding: '10px 18px',
      borderRadius: 10, maxWidth: '86%', textAlign: 'center', lineHeight: 1.6,
    }}>{text}</div>
  )
}

function BtBack({ onClick, title, right }: { onClick: () => void; title: string; right?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
      <button onClick={onClick}
        style={{ background: PAPER2, border: 'none', borderRadius: 8, padding: '6px 12px', fontSize: 13, color: INK_S, cursor: 'pointer' }}>‹ 返回</button>
      <div style={{ flex: 1, fontSize: 15, fontWeight: 700, color: INK, fontFamily: SERIF }}>{title}</div>
      {right && <div style={{ fontSize: 13, color: INK_F }}>{right}</div>}
    </div>
  )
}

// 8因子分值条: 分值域为 [-1,1], 0 为中性, 从中线向两侧画
function BtFactors({ factors, labels, score }: {
  factors?: Record<string, number>; labels?: Record<string, string>; score?: number | null
}) {
  const [open, setOpen] = useState(false)
  const keys = Object.keys(factors || {})
  if (!keys.length) return null
  return (
    <div style={{ borderTop: `1px solid ${LINE}`, marginTop: 6, paddingTop: 8 }}>
      <div onClick={() => setOpen(!open)}
        style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12, color: INK_F }}>
        <span style={{ flex: 1 }}>
          8 因子分解{score != null && <span>　综合评分 {score.toFixed(2)}</span>}
        </span>
        <span>{open ? '收起 ▴' : '展开 ▾'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          {keys.map(k => {
            const v = Number(factors![k]) || 0
            const w = Math.min(50, Math.abs(v) * 50)   // 半幅 50%
            return (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                <span style={{ fontSize: 11, color: INK_S, width: 62, flexShrink: 0 }}>{labels?.[k] || k}</span>
                <div style={{ flex: 1, height: 14, position: 'relative', background: PAPER2, borderRadius: 3 }}>
                  <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: LINE }} />
                  <div style={{
                    position: 'absolute', top: 2, bottom: 2, borderRadius: 2,
                    background: v >= 0 ? '#c0392b' : '#27ae60',
                    left: v >= 0 ? '50%' : `${50 - w}%`, width: `${w}%`,
                  }} />
                </div>
                <span style={{ fontSize: 11, color: v >= 0 ? '#c0392b' : '#27ae60', width: 38, textAlign: 'right', flexShrink: 0 }}>
                  {v > 0 ? '+' : ''}{v.toFixed(2)}
                </span>
              </div>
            )
          })}
          <div style={{ fontSize: 10, color: INK_F, marginTop: 8, lineHeight: 1.6 }}>
            分值域 −1 ~ +1，右侧(红)看涨、左侧(绿)看跌。综合评分 = Σ(因子分值 × 权重)
          </div>
        </div>
      )}
    </div>
  )
}

function BtDetailModal({ d, onClose }: { d: any; onClose: () => void }) {
  const runs = d?.runs || []
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(20,15,8,.55)', zIndex: 400, display: 'flex', alignItems: 'flex-end' }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: BG, width: '100%', maxHeight: '86vh', overflowY: 'auto',
        borderRadius: '18px 18px 0 0', padding: '18px 14px calc(24px + env(safe-area-inset-bottom,0px))',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
          <div style={{ flex: 1, fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF }}>
            {d?.symbol} 预测记录
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 22, color: INK_F, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>

        {d?.loading && <div style={{ padding: 30, textAlign: 'center', color: INK_F, fontSize: 13 }}>加载中…</div>}
        {d?.error && <div style={{ padding: 20, color: '#e74c3c', fontSize: 13 }}>{d.error}</div>}
        {d?.hint && <div style={{ padding: 20, color: INK_F, fontSize: 13 }}>{d.hint}</div>}

        {!d?.loading && !d?.error && runs.length > 0 && (
          <>
            {d.sample > 0 && (
              <div style={{ background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, padding: 14, marginBottom: 12 }}>
                <span style={{ fontSize: 12, color: INK_F }}>已到期 {d.sample} 条 · 方向命中 </span>
                <span style={{ fontSize: 17, fontWeight: 700, color: THEME, fontFamily: SERIF }}>{d.hit_rate}%</span>
                {!d.enough_sample && <span style={{ fontSize: 11, color: '#b45309' }}>（样本不足 {d.min_sample} 条，仅供参考）</span>}
              </div>
            )}

            {/* 预测演变: 同一目标日被多次预测过, 横向对比看模型有没有改口 */}
            {d.matrix && Object.keys(d.matrix).length > 0 && (
              <div style={{ background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, padding: 14, marginBottom: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 4 }}>预测演变</div>
                <div style={{ fontSize: 11, color: INK_F, marginBottom: 12 }}>
                  同一个目标日被预测过多次，横着看模型有没有改口
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: 'left', padding: '6px 10px 6px 0', color: INK_F, fontWeight: 400, whiteSpace: 'nowrap' }}>目标日</th>
                        {(d.base_dates || []).slice().reverse().map((b: string) => (
                          <th key={b} style={{ textAlign: 'right', padding: '6px 0 6px 12px', color: INK_F, fontWeight: 400, whiteSpace: 'nowrap' }}>
                            {b.slice(5)} 预测
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Object.keys(d.matrix).sort().map((pd: string) => {
                        const row = d.matrix[pd]
                        const vals = (d.base_dates || []).slice().reverse().map((b: string) => row[b])
                        const seen = vals.filter((v: any) => v != null) as number[]
                        // 方向翻了就标红, 一眼看出哪天改了口
                        const flipped = seen.length > 1 && Math.min(...seen) < 0 && Math.max(...seen) > 0
                        return (
                          <tr key={pd}>
                            <td style={{ padding: '7px 10px 7px 0', color: INK, borderTop: `1px solid ${LINE}`, whiteSpace: 'nowrap' }}>
                              {pd.slice(5)}{flipped && <span style={{ color: '#e74c3c', marginLeft: 4 }}>改口</span>}
                            </td>
                            {vals.map((v: any, i: number) => (
                              <td key={i} style={{
                                padding: '7px 0 7px 12px', textAlign: 'right', borderTop: `1px solid ${LINE}`,
                                color: v == null ? LINE : v > 0 ? '#c0392b' : v < 0 ? '#27ae60' : INK_F,
                                fontWeight: flipped ? 600 : 400, whiteSpace: 'nowrap',
                              }}>
                                {v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`}
                              </td>
                            ))}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {runs.map((r: any) => (
              <div key={r.base_date} style={{ background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, padding: 14, marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: INK }}>{r.base_date} 收盘后预测</div>
                  {r.signal && <span style={{ fontSize: 11, color: THEME, background: PAPER2, borderRadius: 6, padding: '2px 8px' }}>{r.signal}</span>}
                  {r.confidence != null && <span style={{ fontSize: 11, color: INK_F }}>置信度 {Math.round(r.confidence)}%</span>}
                </div>
                {(r.preds || []).map((p: any) => (
                  <div key={p.pred_date} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderTop: `1px solid ${LINE}`, fontSize: 12 }}>
                    <span style={{ color: INK_F, width: 46 }}>T+{p.horizon}</span>
                    <span style={{ color: INK_F, width: 52 }}>{(p.pred_date || '').slice(5)}</span>
                    <span style={{ flex: 1, color: INK }}>
                      预测 {p.change_pct == null ? '—' : `${p.change_pct > 0 ? '+' : ''}${p.change_pct.toFixed(2)}%`}
                    </span>
                    {p.real_change == null ? (
                      <span style={{ color: INK_F }}>未到期</span>
                    ) : (
                      <>
                        <span style={{ color: INK, width: 68, textAlign: 'right' }}>
                          实际 {p.real_change > 0 ? '+' : ''}{p.real_change.toFixed(2)}%
                        </span>
                        <span style={{ width: 18, textAlign: 'center' }}>{p.dir_hit ? '✅' : '❌'}</span>
                      </>
                    )}
                  </div>
                ))}

                {/* 8因子分值: 这次预测是被哪些因子推起来/压下去的 */}
                <BtFactors factors={r.factors} labels={d.factor_labels} score={r.score} />
              </div>
            ))}

            {(d.reversals || []).length > 0 && (
              <div style={{ background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, padding: 14, marginBottom: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 4 }}>预测改口记录</div>
                <div style={{ fontSize: 11, color: INK_F, marginBottom: 10 }}>模型对同一天的看法发生了方向性变化，以及是哪个因子变了</div>
                {d.reversals.slice(0, 8).map((v: any, i: number) => (
                  <div key={i} style={{ fontSize: 12, color: INK_S, padding: '7px 0', borderTop: `1px solid ${LINE}`, lineHeight: 1.7 }}>
                    对 {(v.pred_date || '').slice(5)}：{v.prev_change > 0 ? '+' : ''}{v.prev_change}% → {v.curr_change > 0 ? '+' : ''}{v.curr_change}%
                    {v.top_driver && <span style={{ color: INK_F }}>　主因 {v.top_driver}{v.driver_share ? `（${Math.round(v.driver_share)}%）` : ''}</span>}
                  </div>
                ))}
              </div>
            )}

            <div style={{ fontSize: 11, color: INK_F, textAlign: 'center', padding: '6px 12px', lineHeight: 1.8 }}>
              历史准确率不预示未来表现，不构成投资建议
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function KPredMobileTab({ token, shared, stocks, quotes }: {
  token: string; shared: ResearchStockCtx;
  stocks: Stock[]; quotes: Record<string, Quote>
}) {
  const { input, setInput, selectedCode, setSelectedCode, selectedName, setSelectedName } = shared
  const [showPicker, setShowPicker] = useState(false)
  const aStockCount = stocks.filter(s => s.market === 'A').length
  const [hits, setHits] = useState<{ code: string; name: string }[]>([])
  const [searched, setSearched] = useState(false)
  const [searching, setSearching] = useState(false)
  const [marketWarn, setMarketWarn] = useState('')
  const [searchError, setSearchError] = useState('')
  const [days, setDays] = useState(10)
  const [mode, setMode] = useState<'basic' | 'pro'>('pro')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<KPredResult | KProResult | null>(null)
  const [error, setError] = useState('')
  const isComposingRef = useRef(false)

  const hdr = (): HeadersInit => token ? { Authorization: `Bearer ${token}` } : {}

  const detectMarket = (v: string): string => {
    const raw = v.trim()
    if (!raw) return ''
    if (/^\d{5}$/.test(raw)) return '当前仅支持A股，港股暂不支持'
    if (/^[A-Za-z]{1,5}$/.test(raw)) return '当前仅支持A股，美股暂不支持'
    return ''
  }

  const doSearch = async () => {
    const raw = input.trim()
    if (!raw) return
    setResult(null); setError(''); setSearchError('')
    setSelectedCode(''); setSelectedName('')
    const warn = detectMarket(raw)
    setMarketWarn(warn)
    if (warn) { setHits([]); setSearched(true); return }
    setSearching(true); setSearched(false)
    try {
      const items = await searchStockWithCache(raw, {
        headers: hdr(),
        onSlowNetwork: () => setSearchError('⏳ 网络较慢, 正在重试...'),
      })
      setHits(items)
      setSearchError('')  // 若之前显示了慢网络提示,现在成功了清掉
      if (items.length === 1) {
        setSelectedCode(items[0].code); setSelectedName(items[0].name)
      } else if (/^\d{6}$/.test(raw)) {
        const hit = items.find(x => x.code === raw)
        if (hit) { setSelectedCode(hit.code); setSelectedName(hit.name) }
      }
    } catch (e: unknown) {
      setHits([])
      const isTimeout = e instanceof Error && (e.message === 'timeout' || e.name === 'AbortError')
      setSearchError(isTimeout ? '查询超时(25秒 + 1 次重试), 请检查网络后再试' : '查询失败,请稍后重试')
    }
    setSearched(true); setSearching(false)
  }

  const confirmStock = (code: string, name: string) => {
    setSelectedCode(code); setSelectedName(name)
  }

  const resetSelection = () => {
    setSelectedCode(''); setSelectedName(''); setInput('')
    setHits([]); setSearched(false); setMarketWarn(''); setSearchError('')
    setResult(null); setError('')
  }

  const predict = async () => {
    const c = selectedCode
    if (!c) return
    setLoading(true); setError(''); setResult(null)
    try {
      const endpoint = mode === 'pro' ? `/api/kpred/${c}/pro?days=${days}` : `/api/kpred/${c}?days=${days}`
      const r = await fetch(endpoint, { headers: hdr() })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || '预测失败')
      setResult(data)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : '预测失败') }
    setLoading(false)
  }

  const isPro = (r: KPredResult | KProResult): r is KProResult => 'pro' in r

  const RATING_COLOR: Record<string, string> = { '强烈看多': '#ef4444', '偏多': '#f87171', '中性观望': '#6b7280', '偏空': '#22c55e', '强烈看空': '#16a34a' }
  const RATING_BG: Record<string, string> = { '强烈看多': '#fef2f2', '偏多': '#fef2f2', '中性观望': '#f9fafb', '偏空': '#f0fdf4', '强烈看空': '#f0fdf4' }
  const CONFLICT_COLOR: Record<string, string> = { '低': '#22c55e', '中': '#f59e0b', '高': '#ef4444' }

  const canPredict = !loading && !!selectedCode

  return (
    <div style={{ padding: '10px 12px 20px' }}>
      {/* 产品介绍 */}
      <IntroCard
        storageKey="hunter_intro_kpred"
        icon="🎯"
        title="Kronos · 清华大学金融时序大模型"
        description="深度学习A股全市场十余年数据，秒级输出未来5-20日K线与5档评级"
        badges={['清华出品', '5档评级', '6因子分解', '短线择时']}
        gradient="linear-gradient(135deg, #f4ecdc 0%, #ecdfc7 100%)"
        border="#d4c3a0"
        accent="#8b5a2b"
        textColor="#5a4a35"
      />

      {/* 搜索栏 + 查询按钮 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1, display: 'flex', gap: 8, background: PAPER, borderRadius: 12, padding: '10px 12px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', alignItems: 'center', border: `1px solid ${LINE}` }}>
            <span style={{ fontSize: 16 }}>🔍</span>
            <input
              value={input}
              onChange={e => { setInput(e.target.value); setSearched(false); setMarketWarn(''); setSearchError('') }}
              onCompositionStart={() => { isComposingRef.current = true }}
              onCompositionEnd={() => { isComposingRef.current = false }}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              placeholder="输入股票名称或代码，点击查询"
              style={{ flex: 1, border: 'none', outline: 'none', fontSize: 15, background: 'transparent', color: INK, fontFamily: SERIF, minWidth: 0 }}
            />
            {input && <button onClick={resetSelection} style={{ background: 'none', border: 'none', color: '#aaa', fontSize: 16, cursor: 'pointer', padding: 0 }}>✕</button>}
          </div>
          <button onClick={doSearch} disabled={!input.trim() || searching}
            style={{ flexShrink: 0, padding: '0 16px', background: input.trim() ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 12, fontSize: 14, fontWeight: 600, cursor: input.trim() ? 'pointer' : 'not-allowed' }}>
            {searching ? '查询中' : '查询'}
          </button>
        </div>

        {/* 从自选选择入口 (已选中后隐藏,避免视觉冗余) */}
        {!selectedCode && (
          <WatchlistPickerButton
            count={aStockCount}
            onOpen={() => setShowPicker(true)}
            onEmptyClick={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}
        {showPicker && (
          <WatchlistPicker
            stocks={stocks} quotes={quotes}
            marketFilter="A"
            currentCode={selectedCode}
            onPick={confirmStock}
            onClose={() => setShowPicker(false)}
            onAddMore={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}

        {marketWarn && (
          <div style={{ padding: '10px 12px', background: '#fff3e6', border: '1px solid #f5c691', borderRadius: 10, fontSize: 13, color: '#a05a1a', marginBottom: 8 }}>
            ⚠️ {marketWarn}，敬请期待
          </div>
        )}

        {searchError && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ flex: 1 }}>{searchError}</span>
            <button onClick={doSearch} style={{ background: '#a04040', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}>重试</button>
          </div>
        )}

        {!selectedCode && searched && !marketWarn && !searchError && hits.length > 0 && (
          <div style={{ background: PAPER, borderRadius: 10, border: `1px solid ${LINE}`, overflow: 'hidden' }}>
            <div style={{ padding: '8px 12px', fontSize: 12, color: INK_F, background: PAPER2 }}>请选择要预测的股票：</div>
            {hits.map(hit => (
              <div key={hit.code} onClick={() => confirmStock(hit.code, hit.name)}
                style={{ padding: '12px 14px', cursor: 'pointer', borderTop: `1px solid ${PAPER2}`, display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 14, color: INK, fontFamily: SERIF, fontWeight: 600 }}>{hit.name}</span>
                <span style={{ fontSize: 12, color: INK_F }}>{hit.code}</span>
                <span style={{ marginLeft: 'auto', fontSize: 12, color: THEME }}>选择 ›</span>
              </div>
            ))}
          </div>
        )}

        {!selectedCode && searched && !marketWarn && !searchError && hits.length === 0 && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040' }}>
            未找到相关A股，请检查股票名称或代码
          </div>
        )}

        {selectedCode && (
          <div style={{ padding: '12px 14px', background: '#fff', border: `2px solid ${THEME}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: THEME }}>已选择：</span>
            <span style={{ fontSize: 15, color: INK, fontWeight: 700, fontFamily: SERIF }}>{selectedName}</span>
            <span style={{ fontSize: 13, color: INK_F }}>{selectedCode}</span>
            <button onClick={resetSelection}
              style={{ marginLeft: 'auto', background: 'none', border: `1px solid ${LINE}`, borderRadius: 8, padding: '4px 10px', fontSize: 12, color: INK_S, cursor: 'pointer' }}>
              更换
            </button>
          </div>
        )}
      </div>

      {/* 参数行 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
        <div style={{ display: 'flex', background: PAPER, borderRadius: 10, overflow: 'hidden', boxShadow: '0 1px 6px rgba(50,35,10,.07)', flex: 1, border: `1px solid ${LINE}` }}>
          {[5, 10, 20].map(d => (
            <button key={d} onClick={() => setDays(d)} style={{ flex: 1, padding: '9px 0', border: 'none', background: days === d ? THEME : 'transparent', color: days === d ? '#fff' : INK_F, fontSize: 13, fontWeight: days === d ? 600 : 400, cursor: 'pointer' }}>{d}日</button>
          ))}
        </div>
        <div style={{ display: 'flex', background: PAPER, borderRadius: 10, overflow: 'hidden', boxShadow: '0 1px 6px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
          {(['basic', 'pro'] as const).map(m => (
            <button key={m} onClick={() => setMode(m)} style={{ padding: '9px 16px', border: 'none', background: mode === m ? THEME : 'transparent', color: mode === m ? '#fff' : INK_F, fontSize: 13, fontWeight: mode === m ? 600 : 400, cursor: 'pointer' }}>{m === 'pro' ? 'Pro' : '基础'}</button>
          ))}
        </div>
      </div>

      {/* 预测按钮 */}
      <button onClick={predict} disabled={!canPredict}
        style={{ width: '100%', padding: '13px 0', background: canPredict ? THEME : PAPER2, color: canPredict ? '#fff' : INK_F, border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: canPredict ? 'pointer' : 'not-allowed', marginBottom: 12 }}>
        {loading ? '预测中…' : selectedCode ? '开始预测' : '请先查询并选择股票'}
      </button>

      {error && (() => {
        // Kronos "未找到 XXX 的 K 线数据" 通常是次新股/新股(Kronos 至少需要 65 根历史 K 线才能预测)。
        // 用产品化提示替代红色报错,给用户明确原因 + 建议下一步动作,避免以为"服务器坏了"。
        const isNoKlineData = /未找到.*的\s*K\s*线数据|未找到.*K线数据/.test(error)
        if (isNoKlineData) {
          return (
            <div style={{
              background: '#fff8e6', border: '1px solid #f5d38b', borderRadius: 10,
              padding: '14px 16px', fontSize: 13, marginBottom: 12, color: '#8a5a1a',
              lineHeight: 1.7,
            }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: '#7a4515', marginBottom: 6 }}>
                ⏳ 该股暂时无法量化预测
              </div>
              <div style={{ marginBottom: 8 }}>
                <b>可能原因</b>: {selectedName || selectedCode} 上市不足 3 个月
                (Kronos 模型至少需要 65 根历史 K 线才能预测)。
              </div>
              <div style={{ marginBottom: 10 }}>
                <b>建议</b>:
                <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                  <li>去 <b>「深度研究」</b> 看基本面与产业链</li>
                  <li>去 <b>「一手情报」</b> 看最新动态和资金面</li>
                  <li>该股攒够 3 个月历史后自动可预测</li>
                </ul>
              </div>
              <div style={{ fontSize: 11, color: '#a86828', marginTop: 8, paddingTop: 8,
                            borderTop: '1px dashed #f5d38b' }}>
                提示: 主板/科创板/创业板次新股均可能命中此情况, 非系统故障。
              </div>
            </div>
          )
        }
        // 其他真实错误(网络/超时/服务异常)保留原红色告警
        return (
          <div style={{ background: '#fef2f2', color: '#dc2626', padding: '10px 14px',
                        borderRadius: 10, fontSize: 13, marginBottom: 12 }}>
            {error}
          </div>
        )
      })()}

      {/* 结果区 */}
      {result && (() => {
        const displayName = selectedName || result.name
        const pred = result.predictions
        const lastPred = pred[pred.length - 1]
        const retPct = (lastPred.close - result.last_close) / result.last_close * 100
        const isUp = retPct >= 0
        const upClr = '#ef4444', downClr = '#22c55e'
        const priceClr = isUp ? upClr : downClr
        const pro = isPro(result) ? result.pro : null

        return (
          <div style={{ background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>

            {/* ── 标题行 ── */}
            <div style={{ padding: '14px 16px 12px', borderBottom: `1px solid ${LINE}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 17, fontWeight: 700, color: INK, fontFamily: SERIF }}>{displayName}</div>
                <div style={{ fontSize: 12, color: INK_F, marginTop: 3 }}>{result.symbol} · 当前收盘 <b style={{ color: INK_S }}>{result.last_close.toFixed(2)}</b></div>
              </div>
              {pro && (
                <div style={{ background: RATING_BG[pro.rating] ?? '#f9fafb', borderRadius: 8, padding: '5px 11px', fontSize: 13, fontWeight: 700, color: RATING_COLOR[pro.rating] ?? '#6b7280' }}>{pro.rating}</div>
              )}
            </div>

            {/* ── 数据 note 横幅: Kronos 未收录 or 数据陈旧 fallback 提示 ── */}
            {result.data_note && (
              <div style={{
                padding: '12px 16px',
                background: result.kronos_skipped ? '#fff1e6' : '#fff8e6',
                borderBottom: `1px solid ${result.kronos_skipped ? '#f5b47a' : '#f5d38b'}`,
                fontSize: 12,
                color: result.kronos_skipped ? '#7a4515' : '#8a5a1a',
                lineHeight: 1.65,
              }}>
                {result.kronos_skipped && (
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#5a3410', marginBottom: 5 }}>
                    ⏳ Kronos 未收录该股 · 已按 7 个技术因子独立分析
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <span style={{ fontSize: 13, flexShrink: 0 }}>{result.kronos_skipped ? '📊' : '⚠'}</span>
                  <span>{result.data_note}</span>
                </div>
              </div>
            )}

            {/* ── K线图 ── */}
            <div style={{ borderBottom: `1px solid ${LINE}`, paddingTop: 4 }}>
              <ReactECharts
                option={buildMobileCandleOption(result)}
                style={{ height: 220 }}
                opts={{ renderer: 'canvas', devicePixelRatio: window.devicePixelRatio ?? 2 }}
              />
              <div style={{ display: 'flex', gap: 16, padding: '4px 12px 10px', justifyContent: 'center' }}>
                <span style={{ fontSize: 11, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: upClr, display: 'inline-block' }} />历史阳线
                </span>
                <span style={{ fontSize: 11, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: downClr, display: 'inline-block' }} />历史阴线
                </span>
                <span style={{ fontSize: 11, color: '#999', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: '#fbbf24', display: 'inline-block' }} />预测
                </span>
              </div>
            </div>

            {/* ── 核心数据行 ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0, borderBottom: `1px solid ${LINE}` }}>
              <div style={{ padding: '12px 16px', borderRight: `1px solid ${LINE}` }}>
                <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>{days}日预测收盘</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: priceClr }}>{lastPred.close.toFixed(2)}</div>
              </div>
              <div style={{ padding: '12px 16px' }}>
                <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>预测涨跌幅</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: priceClr }}>{isUp ? '▲' : '▼'} {Math.abs(retPct).toFixed(2)}%</div>
              </div>
            </div>

            {/* ── Pro 综合评分 ── */}
            {pro && (
              <>
                <div style={{ padding: '12px 16px', borderBottom: `1px solid ${LINE}` }}>
                  <div style={{ fontSize: 11, color: INK_F, marginBottom: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1 }}>综合评分</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    {/* 评分圆圈 */}
                    <div style={{ width: 56, height: 56, borderRadius: '50%', border: `2px solid ${RATING_COLOR[pro.rating] ?? '#6b7280'}`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                      <span style={{ fontSize: 16, fontWeight: 700, lineHeight: 1, color: RATING_COLOR[pro.rating] ?? '#6b7280' }}>
                        {pro.composite_score > 0 ? '+' : ''}{(pro.composite_score * 100).toFixed(0)}
                      </span>
                      <span style={{ fontSize: 9, color: '#aaa', marginTop: 1 }}>/ 100</span>
                    </div>
                    {/* 置信度 + 信号分歧 + 日波动 */}
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px' }}>
                        <span style={{ fontSize: 12, color: '#666' }}>置信度 <b style={{ color: '#333' }}>{pro.confidence}</b></span>
                        <span style={{ fontSize: 12, color: '#666' }}>信号分歧 <b style={{ color: CONFLICT_COLOR[pro.conflict_level] ?? '#6b7280' }}>{pro.conflict_level}</b></span>
                        {pro.sigma_daily_pct != null && <span style={{ fontSize: 12, color: '#666' }}>日波动 <b style={{ color: '#333' }}>{pro.sigma_daily_pct}%</b></span>}
                      </div>
                    </div>
                  </div>
                  {/* 三个收益预期 */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginTop: 12 }}>
                    {[
                      { label: '因子预期', val: pro.factor_return_pct },
                      { label: '调整后', val: pro.adj_return_pct },
                      { label: 'Kronos原始', val: pro.kronos_raw_return_pct, amber: true },
                    ].map(item => (
                      <div key={item.label} style={{ background: '#f9fafb', borderRadius: 8, padding: '8px 10px', textAlign: 'center' }}>
                        <div style={{ fontSize: 10, color: '#999', marginBottom: 4 }}>{item.label}</div>
                        <div style={{ fontSize: 14, fontWeight: 700, color: item.amber ? '#f59e0b' : (item.val >= 0 ? upClr : downClr) }}>
                          {item.val >= 0 ? '+' : ''}{item.val}%
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── 因子明细（全部） ── */}
                <div style={{ padding: '12px 16px 16px' }}>
                  <div style={{ fontSize: 11, color: '#999', marginBottom: 8, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1 }}>因子明细</div>
                  {/* 表头 */}
                  <div style={{ display: 'grid', gridTemplateColumns: '5.5rem 1fr 3rem 3rem 3.5rem', gap: '0 6px', marginBottom: 6 }}>
                    {['因子', '方向(←空|多→)', '评分', '权重', '贡献'].map(lbl => (
                      <span key={lbl} style={{ fontSize: 10, color: '#bbb' }}>{lbl}</span>
                    ))}
                  </div>
                  {pro.factors.map(f => {
                    const isBull = f.score >= 0
                    const halfW = Math.round(Math.min(Math.abs(f.score), 1) * 50)
                    const fClr = f.score > 0.05 ? upClr : f.score < -0.05 ? downClr : '#94a3b8'
                    const cClr = f.contribution > 0.01 ? upClr : f.contribution < -0.01 ? downClr : '#94a3b8'
                    return (
                      <div key={f.key} style={{ display: 'grid', gridTemplateColumns: '5.5rem 1fr 3rem 3rem 3.5rem', gap: '0 6px', alignItems: 'center', paddingTop: 7, paddingBottom: 7, borderTop: '1px solid #f5f5f5' }}>
                        <span style={{ fontSize: 12, color: '#444' }}>{f.label}</span>
                        {/* 双向柱 */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                          <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end', height: 8, background: 'rgba(0,0,0,.04)', borderRadius: '4px 0 0 4px', overflow: 'hidden' }}>
                            {!isBull && <div style={{ width: `${halfW * 2}%`, height: '100%', background: downClr, borderRadius: '4px 0 0 4px' }} />}
                          </div>
                          <div style={{ width: 1, height: 10, background: '#ddd', flexShrink: 0 }} />
                          <div style={{ flex: 1, height: 8, background: 'rgba(0,0,0,.04)', borderRadius: '0 4px 4px 0', overflow: 'hidden' }}>
                            {isBull && <div style={{ width: `${halfW * 2}%`, height: '100%', background: upClr, borderRadius: '0 4px 4px 0' }} />}
                          </div>
                        </div>
                        <span style={{ fontSize: 11, color: fClr, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{f.score > 0 ? '+' : ''}{f.score.toFixed(2)}</span>
                        <span style={{ fontSize: 11, color: '#aaa', textAlign: 'right' }}>{(f.weight * 100).toFixed(0)}%</span>
                        <span style={{ fontSize: 11, color: cClr, textAlign: 'right', fontWeight: 600 }}>{f.contribution > 0 ? '+' : ''}{f.contribution.toFixed(3)}</span>
                      </div>
                    )
                  })}
                  <p style={{ fontSize: 11, color: '#bbb', marginTop: 10, lineHeight: 1.6 }}>预测 K 线已根据多因子综合评分对 Kronos（清华大学金融时序大模型）原始幅度进行调整（40% Kronos + 60% 因子混合），形态保持不变。</p>
                </div>
              </>
            )}
          </div>
        )
      })()}
    </div>
  )
}

// ── 事件分析 Tab ──────────────────────────────────────────────────────────
const EVENT_PRESETS = [
  '美联储突然宣布加息50bp',
  '中美宣布新一轮关税战',
  '人民币单日贬值超1%',
  '国内宣布万亿刺激政策',
  '全球科技股估值泡沫破裂',
  '大宗商品超级周期来临',
]

interface EventHistoryItem { id: number; event_desc: string; created_at: string }

function fmtEventDate(iso: string) {
  const d = new Date(iso)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

function EventAnalysisTab({ token, shared }: { token: string; shared?: ResearchStockCtx }) {
  const [event, setEvent] = useState('')
  const [loading, setLoading] = useState(false)
  const [html, setHtml] = useState('')
  const [htmlTitle, setHtmlTitle] = useState('')
  const [error, setError] = useState('')
  const [history, setHistory] = useState<EventHistoryItem[]>([])
  const [histLoading, setHistLoading] = useState(true)

  // 从助手页跳转来时，展示用户当前关注的股票作为上下文提示
  const contextStock = shared?.selectedCode
    ? { code: shared.selectedCode, name: shared.selectedName || shared.selectedCode }
    : null
  const placeholderExample = contextStock
    ? `描述你想分析的事件，如"${contextStock.name}三季度预告下滑"`
    : '描述你想分析的事件，如"央行降息 25 个基点"'

  const h = useCallback((): HeadersInit => ({ Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }), [token])

  const loadHistory = useCallback(async () => {
    try {
      const r = await fetch('/api/event-analysis/history', { headers: h() })
      if (r.ok) setHistory(await r.json())
    } catch {}
    setHistLoading(false)
  }, [h])

  useEffect(() => { loadHistory() }, [loadHistory])

  async function analyze() {
    if (!event.trim() || loading) return
    setLoading(true); setError(''); setHtml('')
    try {
      // V2：若从助手/深度研究跳来带股（contextStock），传 focus_symbol 让后端聚焦分析
      const payload: Record<string, string> = { event: event.trim() }
      if (contextStock) {
        payload.focus_symbol = contextStock.code
        payload.focus_name   = contextStock.name
      }
      const r = await fetch('/api/event-analysis', {
        method: 'POST', headers: h(),
        body: JSON.stringify(payload),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error((d as { detail?: string }).detail || '分析失败，请稍后重试')
      }
      const d = await r.json()
      setHtml(d.html || '')
      // 标题带上股票名，便于历史区分
      setHtmlTitle(contextStock ? `${contextStock.name} · ${event.trim()}` : event.trim())
      loadHistory()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '未知错误')
    }
    setLoading(false)
  }

  async function viewHistory(item: EventHistoryItem) {
    setLoading(true); setError('')
    try {
      const r = await fetch(`/api/event-analysis/history/${item.id}`, { headers: h() })
      if (!r.ok) throw new Error('获取失败')
      const d = await r.json()
      setHtml(d.html || ''); setHtmlTitle(item.event_desc)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '未知错误')
    }
    setLoading(false)
  }

  return (
    <div style={{ paddingBottom: 20 }}>
      {/* 全屏 HTML 报告覆盖层 */}
      {html && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 300, display: 'flex', flexDirection: 'column', background: '#fff', height: '100dvh' }}>
          <div style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
            <button onClick={() => setHtml('')} style={{ background: '#f5f5f5', border: 'none', borderRadius: 8, padding: '6px 12px', fontSize: 13, color: '#555', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>‹ 返回</button>
            <span style={{ fontSize: 13, color: '#333', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {htmlTitle.length > 30 ? htmlTitle.slice(0, 30) + '…' : htmlTitle}
            </span>
          </div>
          <iframe srcDoc={html} style={{ flex: 1, border: 'none', width: '100%' }} sandbox="allow-same-origin allow-scripts" title="事件分析报告" />
        </div>
      )}

      {/* 助手页跳来时：展示当前关注股票（提示 focus 聚焦分析） */}
      {contextStock && (
        <div style={{
          margin: '10px 12px 0', padding: '10px 14px',
          background: '#F0F9F0', border: '1px solid #C7E0C9', borderRadius: 10,
          fontSize: 12, color: DN, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span>🎯 聚焦分析 <b>{contextStock.name}</b>（{contextStock.code}）· 事件影响将只针对这只股</span>
        </div>
      )}

      {/* 输入区 */}
      <div style={{ margin: '10px 12px 0', background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
        <textarea
          value={event} onChange={e => setEvent(e.target.value)}
          placeholder={placeholderExample + '\n\n例如：美联储突然宣布加息 50bp，超出市场预期...'}
          rows={4}
          style={{ width: '100%', padding: '14px 14px 10px', boxSizing: 'border-box', border: 'none', borderBottom: `1px solid ${PAPER2}`, fontSize: 14, resize: 'none', outline: 'none', color: INK, fontFamily: SERIF, background: 'transparent' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 14px 12px' }}>
          <span style={{ fontSize: 11, color: INK_F }}>{event.length > 0 ? `${event.length} 字` : '描述大事件，分析持仓影响'}</span>
          <button
            onClick={analyze} disabled={!event.trim() || loading}
            style={{ padding: '8px 18px', background: event.trim() && !loading ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 10, fontSize: 14, fontWeight: 600, cursor: event.trim() && !loading ? 'pointer' : 'not-allowed', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            {loading ? '分析中…' : '⚡ 分析影响'}
          </button>
        </div>
      </div>

      {/* 错误 */}
      {error && (
        <div style={{ margin: '8px 12px', padding: '10px 14px', background: '#fff5f5', border: '1px solid #fde', borderRadius: 10, fontSize: 13, color: '#e74c3c' }}>{error}</div>
      )}

      {/* 加载状态 */}
      {loading && (
        <div style={{ textAlign: 'center', padding: '40px 20px' }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>⏳</div>
          <div style={{ fontSize: 14, color: '#aaa' }}>正在分析事件对持仓的影响…</div>
          <div style={{ fontSize: 12, color: '#ccc', marginTop: 6 }}>通常需要 30–60 秒</div>
        </div>
      )}

      {!loading && (
        <>
          {/* 示例事件 */}
          <div style={{ padding: '14px 12px 6px' }}>
            <div style={{ fontSize: 11, color: '#bbb', marginBottom: 8 }}>示例事件（点击填入，可自行修改）</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {EVENT_PRESETS.map(p => (
                <button key={p} onClick={() => setEvent(p)}
                  style={{ fontSize: 11, padding: '5px 10px', borderRadius: 20, border: `1px solid ${event === p ? THEME : LINE}`, background: event === p ? PAPER2 : PAPER, color: event === p ? THEME : INK_F, cursor: 'pointer' }}>
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* 分析历史 */}
          <div style={{ padding: '14px 12px 0' }}>
            <div style={{ fontSize: 11, color: '#bbb', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
              🕐 我的分析历史
            </div>
            {histLoading && <div style={{ textAlign: 'center', padding: '20px 0', color: '#ccc', fontSize: 13 }}>加载中...</div>}
            {!histLoading && history.length === 0 && (
              <div style={{ textAlign: 'center', padding: '24px 0', color: INK_F, fontSize: 13, background: PAPER, borderRadius: 14, border: `1px solid ${LINE}` }}>暂无历史，分析完成后自动保存</div>
            )}
            {!histLoading && history.length > 0 && (
              <div style={{ background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
                {history.map((item, idx) => (
                  <button key={item.id} onClick={() => viewHistory(item)}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', padding: '13px 16px', background: 'none', border: 'none', borderBottom: idx < history.length - 1 ? `1px solid ${PAPER2}` : 'none', cursor: 'pointer', gap: 10, textAlign: 'left' }}>
                    <span style={{ fontSize: 16, flexShrink: 0 }}>⚡</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: INK, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.event_desc}</div>
                      <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{fmtEventDate(item.created_at)}</div>
                    </div>
                    <span style={{ color: '#ccc', fontSize: 16, flexShrink: 0 }}>›</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── 持仓报告 Tab ──────────────────────────────────────────────────────────
interface PortfolioSummary { total_market_value: number; total_cost: number; total_profit_loss: number; total_profit_pct: number; total_today_profit: number }
interface PositionItem { code: string; name: string; shares: number; cost_price: number; current_price: number | null; change_pct: number | null; market_value: number | null; profit_loss: number | null; profit_loss_pct: number | null; today_profit: number | null; buy_date?: string }

function PortfolioMobileTab({ token }: { token: string }) {
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [positions, setPositions] = useState<PositionItem[]>([])
  const [noCostStocks, setNoCostStocks] = useState<{ code: string; name: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [editCode, setEditCode] = useState<string | null>(null)
  const [editForm, setEditForm] = useState({ shares: '', cost_price: '', buy_date: '' })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  // ── OCR 截图导入 ─────────────────────────────────────────────────────────
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [ocrBusy, setOcrBusy] = useState(false)
  const [ocrErr, setOcrErr] = useState('')
  const [ocrRows, setOcrRows] = useState<Array<{
    code: string; name: string; exchange: string; market: string
    cost_price: string; shares: string; checked: boolean; hadCost: boolean
  }>>([])
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<{ ok: number; skip: number; fail: number } | null>(null)

  const h = (): HeadersInit => ({ Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/portfolio/summary', { headers: h() })
      if (r.ok) {
        const d = await r.json()
        setSummary(d.summary)
        setPositions(d.positions || [])
        setNoCostStocks(d.no_cost_stocks || [])
      }
    } catch {}
    setLoading(false)
  }, [token]) // eslint-disable-line

  useEffect(() => { load() }, [load])

  function openEdit(code: string, pos?: PositionItem) {
    setEditCode(code)
    setEditForm({
      shares: pos?.shares ? String(pos.shares) : '',
      cost_price: pos?.cost_price ? String(pos.cost_price) : '',
      buy_date: pos?.buy_date || '',
    })
    setMsg('')
  }

  async function savePosition() {
    if (!editCode) return
    const shares = parseInt(editForm.shares)
    const cost_price = parseFloat(editForm.cost_price)
    if (!shares || shares <= 0 || !cost_price || cost_price <= 0) { setMsg('请填写持股数量和买入均价'); return }
    setSaving(true)
    try {
      const r = await fetch(`/api/portfolio/${editCode}/position`, {
        method: 'PUT', headers: h(),
        body: JSON.stringify({ shares, cost_price, buy_date: editForm.buy_date || null }),
      })
      if (r.ok) { setEditCode(null); await load() } else { setMsg('保存失败，请稍后重试') }
    } catch { setMsg('网络错误') }
    setSaving(false)
  }

  async function clearPosition(code: string) {
    if (!window.confirm('确认清除该股票的持仓数据？')) return
    try {
      await fetch(`/api/portfolio/${code}/position`, { method: 'DELETE', headers: h() })
      await load()
    } catch {}
  }

  // ── 截图 OCR：上传 → 压缩 → 调用识别 API → 打开确认 sheet ────────────────
  const onScreenshot = async (file: File) => {
    setOcrBusy(true); setOcrErr(''); setImportResult(null)
    try {
      const dataUrl: string = await new Promise((resolve, reject) => {
        const img = new window.Image()
        const url = URL.createObjectURL(file)
        img.onload = () => {
          const scale = Math.min(1, 1280 / Math.max(img.width, img.height))
          const canvas = document.createElement('canvas')
          canvas.width = Math.round(img.width * scale)
          canvas.height = Math.round(img.height * scale)
          canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height)
          URL.revokeObjectURL(url)
          resolve(canvas.toDataURL('image/jpeg', 0.85))
        }
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图片读取失败')) }
        img.src = url
      })
      const r = await fetch('/api/ax/ocr-positions', { method: 'POST', headers: h(), body: JSON.stringify({ image_b64: dataUrl }) })
      const d = await r.json()
      if (!r.ok) { setOcrErr(d?.error || '识别失败，请重试'); return }
      const ps: Array<{ name: string; code: string; exchange: string; market: string; cost_price: number | null; shares: number | null }> = d.positions || []
      if (!ps.length) { setOcrErr('没识别出持仓，请换张更清晰的截图'); return }
      const costCodes = new Set(positions.map(p => p.code))
      setOcrRows(ps.map(p => ({
        code: p.code || '',
        name: p.name || '',
        exchange: p.exchange || 'SH',
        market: p.market || 'A',
        cost_price: p.cost_price != null ? String(p.cost_price) : '',
        shares: p.shares != null ? String(p.shares) : '',
        checked: !!p.code,
        hadCost: costCodes.has(p.code),
      })))
    } catch (e) {
      setOcrErr(e instanceof Error ? e.message : '识别失败，请重试')
    } finally {
      setOcrBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  // ── 批量导入：勾选行 → 不在自选股则先加，再 PUT 成本 ─────────────────────
  const submitImport = async () => {
    setImporting(true)
    let ok = 0, skip = 0, fail = 0
    for (const row of ocrRows) {
      if (!row.checked) { skip++; continue }
      const cost = parseFloat(row.cost_price)
      const shares = parseInt(row.shares)
      if (!row.code || !cost || cost <= 0 || !shares || shares <= 0) { fail++; continue }
      try {
        const inList = positions.some(p => p.code === row.code) || noCostStocks.some(s => s.code === row.code)
        if (!inList) {
          await fetch('/api/watchlist', { method: 'POST', headers: h(), body: JSON.stringify({
            code: row.code, name: row.name, market: row.market, exchange: row.exchange, asset_type: 'stock',
          }) })
        }
        const r = await fetch(`/api/portfolio/${row.code}/position`, { method: 'PUT', headers: h(), body: JSON.stringify({
          shares, cost_price: cost, buy_date: null,
        }) })
        if (r.ok) ok++
        else fail++
      } catch { fail++ }
    }
    setImporting(false)
    setImportResult({ ok, skip, fail })
    await load()
  }

  const pnlColor = (v: number | null) => v == null ? INK : v > 0 ? UP : v < 0 ? DN : INK
  const fmtPnl = (v: number | null) => v == null ? '--' : (v > 0 ? '+' : '') + '¥' + Math.abs(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  const fmtPct = (v: number | null) => v == null ? '--' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'

  if (loading && !summary) return <div style={{ textAlign: 'center', padding: '80px 0', color: INK_F, fontSize: 14 }}>加载中...</div>

  return (
    <div style={{ paddingBottom: 20 }}>
      {/* 截图导入持仓（顶部醒目按钮） */}
      <div style={{ padding: '12px 12px 0' }}>
        <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) onScreenshot(f) }} />
        <button onClick={() => !ocrBusy && fileRef.current?.click()} disabled={ocrBusy}
          style={{
            width: '100%', padding: '14px 0', background: ocrBusy ? COPPER2 : THEME, color: '#fff',
            border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600,
            cursor: ocrBusy ? 'not-allowed' : 'pointer', boxShadow: '0 2px 8px rgba(176,106,50,.25)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          }}>
          {ocrBusy ? '🔍 识别中，请稍候…' : '📷 截图导入持仓 · 自动识别'}
        </button>
        <div style={{ fontSize: 11, color: INK_F, marginTop: 6, textAlign: 'center' }}>
          从券商 App 截图，AI 自动识别股票和成本 · 图片不留存
        </div>
        {ocrErr && <div style={{ fontSize: 13, color: '#e74c3c', marginTop: 6, textAlign: 'center' }}>{ocrErr}</div>}
      </div>

      {/* 汇总卡片 */}
      {summary && (
        <div style={{ padding: '12px 12px 0', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <div style={{ background: PAPER, borderRadius: 12, padding: '14px 14px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, gridColumn: '1 / -1' }}>
            <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>总持仓市值</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: INK }}>¥{summary.total_market_value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
            <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>成本 ¥{summary.total_cost.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          </div>
          <div style={{ background: PAPER, borderRadius: 12, padding: '12px 14px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
            <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>累计盈亏</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: pnlColor(summary.total_profit_loss) }}>{fmtPnl(summary.total_profit_loss)}</div>
            <div style={{ fontSize: 11, color: pnlColor(summary.total_profit_loss), marginTop: 2 }}>{fmtPct(summary.total_profit_pct)}</div>
          </div>
          <div style={{ background: PAPER, borderRadius: 12, padding: '12px 14px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
            <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>今日盈亏</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: pnlColor(summary.total_today_profit) }}>{fmtPnl(summary.total_today_profit)}</div>
            <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{positions.length} 只持仓</div>
          </div>
        </div>
      )}

      {/* 持仓明细 */}
      {positions.length > 0 && (
        <div style={{ padding: '14px 12px 0' }}>
          <div style={{ fontSize: 12, color: INK_F, fontWeight: 600, marginBottom: 8, letterSpacing: '0.5px' }}>持仓明细</div>
          <div style={{ background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
            {positions.map((pos, i) => (
              <div key={pos.code} style={{ padding: '14px 16px', borderBottom: i < positions.length - 1 ? `1px solid ${PAPER2}` : 'none' }}>
                {/* 股票名+操作 */}
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, color: INK, fontFamily: SERIF }}>{pos.name}</div>
                    <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{pos.code}</div>
                  </div>
                  {pos.current_price != null && (
                    <div style={{ textAlign: 'right', marginRight: 12 }}>
                      <div style={{ fontSize: 18, fontWeight: 700, color: pnlColor(pos.change_pct) }}>{pos.current_price}</div>
                      <div style={{ fontSize: 12, color: pnlColor(pos.change_pct) }}>{fmtPct(pos.change_pct)}</div>
                    </div>
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <button onClick={() => openEdit(pos.code, pos)} style={{ fontSize: 12, color: THEME, background: 'rgba(176,106,50,.08)', border: `1px solid ${THEME}`, borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}>编辑</button>
                    <button onClick={() => clearPosition(pos.code)} style={{ fontSize: 12, color: '#e74c3c', background: 'none', border: '1px solid #e74c3c', borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}>清除</button>
                  </div>
                </div>
                {/* 数据格 */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '6px 0', background: BG, borderRadius: 8, padding: '10px 12px' }}>
                  {[
                    ['成本价', `¥${pos.cost_price}`],
                    ['持股数', `${pos.shares}股`],
                    ['持仓市值', pos.market_value != null ? `¥${pos.market_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}` : '--'],
                    ['盈亏金额', fmtPnl(pos.profit_loss)],
                    ['盈亏%', fmtPct(pos.profit_loss_pct)],
                    ['今日盈亏', fmtPnl(pos.today_profit)],
                  ].map(([label, val], idx) => (
                    <div key={label} style={{ textAlign: idx % 3 === 0 ? 'left' : idx % 3 === 1 ? 'center' : 'right' }}>
                      <div style={{ fontSize: 10, color: INK_F }}>{label}</div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: label.includes('盈亏') ? pnlColor(parseFloat(String(val).replace(/[^-0-9.]/g, '')) || null) : INK_S, marginTop: 2 }}>{val}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 未录入持仓 */}
      {noCostStocks.length > 0 && (
        <div style={{ padding: '14px 12px 0' }}>
          <div style={{ fontSize: 12, color: INK_F, fontWeight: 600, marginBottom: 4, letterSpacing: '0.5px' }}>未录入持仓</div>
          <div style={{ fontSize: 11, color: INK_F, marginBottom: 8 }}>点击录入成本价后可计算盈亏</div>
          <div style={{ background: PAPER, borderRadius: 14, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}` }}>
            {noCostStocks.map((s, i) => (
              <button key={s.code} onClick={() => openEdit(s.code)}
                style={{ display: 'flex', width: '100%', alignItems: 'center', padding: '13px 16px', background: 'none', border: 'none', borderBottom: i < noCostStocks.length - 1 ? `1px solid ${PAPER2}` : 'none', cursor: 'pointer' }}>
                <div style={{ flex: 1, textAlign: 'left' }}>
                  <div style={{ fontSize: 14, color: INK, fontFamily: SERIF }}>{s.name}</div>
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{s.code}</div>
                </div>
                <span style={{ fontSize: 12, color: THEME, border: `1px solid ${THEME}`, borderRadius: 6, padding: '4px 12px' }}>+ 录入</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {!loading && positions.length === 0 && noCostStocks.length === 0 && (
        <div style={{ textAlign: 'center', padding: '60px 20px' }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>📋</div>
          <div style={{ fontSize: 14, color: INK_F }}>先在自选股中添加股票，再来这里录入持仓成本</div>
        </div>
      )}

      {/* 刷新按钮 */}
      {(positions.length > 0 || noCostStocks.length > 0) && (
        <div style={{ padding: '16px 12px 0' }}>
          <button onClick={load} disabled={loading} style={{ width: '100%', padding: '12px 0', background: 'none', border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 14, color: INK_F, cursor: loading ? 'not-allowed' : 'pointer' }}>
            {loading ? '刷新中...' : '↻ 刷新行情'}
          </button>
        </div>
      )}

      {/* 编辑弹层 */}
      {editCode && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', zIndex: 300, display: 'flex', alignItems: 'flex-end' }}
          onClick={e => { if (e.target === e.currentTarget) setEditCode(null) }}>
          <div style={{ background: PAPER, width: '100%', maxWidth: 480, margin: '0 auto', borderRadius: '18px 18px 0 0', padding: '20px 20px 36px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 18 }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF, flex: 1 }}>
                录入持仓 · {positions.find(p => p.code === editCode)?.name || noCostStocks.find(s => s.code === editCode)?.name || editCode}
              </div>
              <button onClick={() => setEditCode(null)} style={{ background: PAPER2, border: 'none', color: INK_S, fontSize: 16, width: 28, height: 28, borderRadius: '50%', cursor: 'pointer' }}>✕</button>
            </div>
            {[
              { key: 'cost_price', label: '买入均价 *', placeholder: '如：1280.00', type: 'number' },
              { key: 'shares', label: '持股数量 *', placeholder: '如：100', type: 'number' },
              { key: 'buy_date', label: '买入日期（可选）', placeholder: '如：2026-01-15', type: 'date' },
            ].map(f => (
              <div key={f.key} style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, color: INK_S, marginBottom: 6 }}>{f.label}</div>
                <input
                  type={f.type}
                  value={editForm[f.key as keyof typeof editForm]}
                  onChange={e => setEditForm(prev => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  style={{ width: '100%', height: 46, padding: '0 14px', boxSizing: 'border-box', border: `1px solid ${LINE}`, borderRadius: 10, fontSize: 15, background: BG, color: INK, outline: 'none' }}
                />
              </div>
            ))}
            {msg && <div style={{ fontSize: 13, color: '#e74c3c', marginBottom: 10 }}>{msg}</div>}
            <button onClick={savePosition} disabled={saving}
              style={{ width: '100%', height: 50, background: saving ? COPPER2 : THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 16, fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer', marginTop: 4 }}>
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>
      )}

      {/* OCR 识别结果确认 sheet */}
      {ocrRows.length > 0 && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', zIndex: 300, display: 'flex', alignItems: 'flex-end' }}
          onClick={e => { if (e.target === e.currentTarget && !importing) { setOcrRows([]); setImportResult(null) } }}>
          <div style={{ background: PAPER, width: '100%', maxWidth: 480, margin: '0 auto', borderRadius: '18px 18px 0 0', maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '18px 20px 12px', borderBottom: `1px solid ${LINE}` }}>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF, flex: 1 }}>
                  {importResult ? '导入完成' : `识别到 ${ocrRows.length} 只 · 确认导入`}
                </div>
                <button onClick={() => { if (!importing) { setOcrRows([]); setImportResult(null) } }}
                  style={{ background: PAPER2, border: 'none', color: INK_S, fontSize: 16, width: 28, height: 28, borderRadius: '50%', cursor: 'pointer' }}>✕</button>
              </div>
              {!importResult && (
                <div style={{ fontSize: 12, color: INK_F, marginTop: 6 }}>
                  去勾即跳过 · 已有成本会被覆盖 · 缺失代码/成本/股数的行无法导入
                </div>
              )}
            </div>

            {importResult ? (
              <div style={{ padding: '32px 20px', textAlign: 'center' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>✅</div>
                <div style={{ fontSize: 15, color: INK_S, lineHeight: 1.8 }}>
                  成功 <b style={{ color: THEME }}>{importResult.ok}</b> 条
                  {importResult.skip > 0 && <> · 跳过 {importResult.skip} 条</>}
                  {importResult.fail > 0 && <> · 失败 <b style={{ color: '#e74c3c' }}>{importResult.fail}</b> 条</>}
                </div>
                <button onClick={() => { setOcrRows([]); setImportResult(null) }}
                  style={{ marginTop: 22, padding: '11px 42px', background: THEME, color: '#fff', border: 'none', borderRadius: 10, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>
                  关闭
                </button>
              </div>
            ) : (
              <>
                <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
                  {ocrRows.map((row, i) => (
                    <div key={i} style={{ border: `1px solid ${row.checked && row.code ? THEME : LINE}`, borderRadius: 10, padding: '10px 12px', marginBottom: 10, background: row.checked && row.code ? '#FBF1E4' : PAPER2, opacity: row.code ? 1 : 0.6 }}>
                      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
                        <input type="checkbox" checked={row.checked} disabled={!row.code}
                          onChange={e => setOcrRows(rs => rs.map((r, j) => j === i ? { ...r, checked: e.target.checked } : r))}
                          style={{ width: 18, height: 18, accentColor: THEME, marginRight: 10, cursor: row.code ? 'pointer' : 'not-allowed' }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>{row.name || '（未识别名称）'}</div>
                          <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>
                            {row.code || <span style={{ color: '#e74c3c' }}>⚠ 未识别代码，无法导入</span>}
                            {row.hadCost && row.code && <span style={{ color: COPPER2, marginLeft: 8 }}>· 已有成本将覆盖</span>}
                          </div>
                        </div>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                        <div>
                          <div style={{ fontSize: 11, color: INK_F, marginBottom: 3 }}>成本价</div>
                          <input type="number" value={row.cost_price} disabled={!row.code}
                            onChange={e => setOcrRows(rs => rs.map((r, j) => j === i ? { ...r, cost_price: e.target.value } : r))}
                            placeholder="必填" style={{ width: '100%', height: 36, padding: '0 10px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, boxSizing: 'border-box', background: '#fff' }} />
                        </div>
                        <div>
                          <div style={{ fontSize: 11, color: INK_F, marginBottom: 3 }}>持股数</div>
                          <input type="number" value={row.shares} disabled={!row.code}
                            onChange={e => setOcrRows(rs => rs.map((r, j) => j === i ? { ...r, shares: e.target.value } : r))}
                            placeholder="必填" style={{ width: '100%', height: 36, padding: '0 10px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, boxSizing: 'border-box', background: '#fff' }} />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ padding: '14px 16px calc(14px + env(safe-area-inset-bottom, 0px))', borderTop: `1px solid ${LINE}`, background: PAPER }}>
                  <button onClick={submitImport} disabled={importing || ocrRows.filter(r => r.checked).length === 0}
                    style={{ width: '100%', padding: '13px 0', background: importing || ocrRows.filter(r => r.checked).length === 0 ? COPPER2 : THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: importing ? 'not-allowed' : 'pointer', opacity: ocrRows.filter(r => r.checked).length === 0 ? 0.6 : 1 }}>
                    {importing ? '导入中…' : `✓ 确认导入 ${ocrRows.filter(r => r.checked).length} 条`}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 主页 ──────────────────────────────────────────────────────────────────
// ── 副驾 Tab ─────────────────────────────────────────────────────────────
const RATING_COLOR: Record<string, string> = {
  '强烈看多': UP, '偏多': '#C14B4B', '中性观望': INK_F, '偏空': '#3F8B47', '强烈看空': DN,
}
const RATING_BG: Record<string, string> = {
  '强烈看多': '#fdf0f0', '偏多': '#fdf0f0', '中性观望': PAPER2, '偏空': '#f0fdf4', '强烈看空': '#f0fdf4',
}
const ALERT_LEVEL_COLOR: Record<string, string> = { red: '#A4332B', yellow: '#C17B2A', green: '#3F6B40', grey: '#7A6F63' }
const ALERT_LEVEL_DOT:   Record<string, string> = { red: '🔴', yellow: '🟡', green: '🟢', grey: '⚪' }
const ALERT_LEVEL_LABEL: Record<string, string> = { red: '需关注', yellow: '关注', green: '积极信号', grey: '常规更新' }

// 单只股票卡片（供各分组复用）
function StockBriefCard({ s, q, pred, isLoading, briefStock }: {
  s: Stock; q?: Quote; pred?: KProResult | null; isLoading?: boolean; briefStock?: TrueBriefStock
}) {
  const pro = pred?.pro
  const lastPredClose = pred?.predictions?.[pred.predictions.length - 1]?.close
  const retPct = (lastPredClose != null && pred?.last_close)
    ? (lastPredClose - pred.last_close) / pred.last_close * 100 : null

  return (
    <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, overflow: 'hidden', boxShadow: '0 1px 8px rgba(50,35,10,.06)' }}>
      {/* 股票名 + 行情 */}
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: INK, fontFamily: SERIF }}>{s.name}</div>
            {briefStock && briefStock.alert_level !== 'grey' && (
              <span style={{ fontSize: 10, color: ALERT_LEVEL_COLOR[briefStock.alert_level], background: `${ALERT_LEVEL_COLOR[briefStock.alert_level]}15`, border: `1px solid ${ALERT_LEVEL_COLOR[briefStock.alert_level]}40`, borderRadius: 6, padding: '1px 6px' }}>
                {ALERT_LEVEL_DOT[briefStock.alert_level]} {ALERT_LEVEL_LABEL[briefStock.alert_level]}
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>
            {s.code} · {s.market === 'A' ? 'A股' : s.market === 'HK' ? '港股' : '美股'}
            {briefStock?.chain && <span style={{ color: COPPER2 }}> · {briefStock.chain}</span>}
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          {q?.price != null ? (
            <>
              <div style={{ fontSize: 17, fontWeight: 700, color: priceColor(q.change_pct) }}>{q.price}</div>
              <div style={{ fontSize: 12, color: priceColor(q.change_pct) }}>
                {q.change_pct != null ? (q.change_pct > 0 ? '+' : '') + q.change_pct.toFixed(2) + '%' : '--'}
              </div>
            </>
          ) : <div style={{ fontSize: 12, color: INK_F }}>非交易时段</div>}
        </div>
      </div>

      {/* 真源信号（有信号才显示） */}
      {briefStock && briefStock.signal_count > 0 && (
        <div style={{ borderTop: `1px solid ${PAPER2}`, padding: '10px 16px 12px' }}>
          <div style={{ fontSize: 11, color: COPPER2, fontWeight: 600, marginBottom: 6 }}>一手情报 · 近3天</div>
          {briefStock.signals.slice(0, 3).map((sig, i) => (
            <div key={i} style={{ fontSize: 12, color: INK_S, marginBottom: 4, lineHeight: 1.6, display: 'flex', gap: 6 }}>
              <span style={{ color: COPPER2, flexShrink: 0 }}>·</span>
              <span>{sig.title.length > 55 ? sig.title.slice(0, 55) + '…' : sig.title}</span>
            </div>
          ))}
          {briefStock.signal_count > 3 && (
            <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>另有 {briefStock.signal_count - 3} 条信号</div>
          )}
        </div>
      )}

      {/* Kronos 预测 */}
      <div style={{ borderTop: `1px solid ${PAPER2}`, padding: '12px 16px', background: BG }}>
        {s.market !== 'A' ? (
          <div style={{ fontSize: 12, color: INK_F }}>{s.market === 'HK' ? '港股' : '美股'} · 暂不支持 Kronos（清华大学金融时序大模型）预测</div>
        ) : isLoading ? (
          <div style={{ fontSize: 13, color: INK_F }}>⏳ Kronos 多因子分析中...</div>
        ) : pro ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ background: RATING_BG[pro.rating] ?? PAPER2, border: `1px solid ${(RATING_COLOR[pro.rating] ?? INK_F)}44`, borderRadius: 8, padding: '6px 12px', flexShrink: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: RATING_COLOR[pro.rating] ?? INK_F }}>{pro.rating}</div>
            </div>
            <div style={{ flex: 1 }}>
              {retPct != null && (
                <div style={{ fontSize: 14, fontWeight: 700, color: retPct >= 0 ? UP : DN }}>
                  明日预测 {retPct >= 0 ? '+' : ''}{retPct.toFixed(2)}%
                </div>
              )}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 2 }}>
                <span style={{ fontSize: 11, color: INK_F }}>置信度 <b style={{ color: INK_S }}>{pro.confidence}</b></span>
                <span style={{ fontSize: 11, color: INK_F }}>综合分 <b style={{ color: INK_S }}>{(pro.composite_score * 100).toFixed(0)}</b></span>
                <span style={{ fontSize: 11, color: INK_F }}>分歧 <b style={{ color: INK_S }}>{pro.conflict_level}</b></span>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: INK_F }}>暂无预测数据（历史数据不足）</div>
        )}
      </div>
    </div>
  )
}

function CopilotTab({ token, stocks, quotes, preds, loadingMap, brief }: {
  token: string; stocks: Stock[]; quotes: Record<string, Quote>
  preds: Record<string, KProResult | null>; loadingMap: Record<string, boolean>; brief: TrueBrief | null
}) {
  const [greyExpanded, setGreyExpanded] = useState(false)

  const now = new Date()
  const dateStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
  const briefBySymbol = brief ? Object.fromEntries(brief.stocks.map(b => [b.symbol, b])) : {}

  // 按 alert_level 分组
  const groups: Record<string, Stock[]> = { red: [], yellow: [], green: [], grey: [] }
  for (const s of stocks) {
    const level = briefBySymbol[s.code]?.alert_level ?? 'grey'
    if (level in groups) groups[level].push(s)
    else groups.grey.push(s)
  }
  const redCnt = groups.red.length, yellowCnt = groups.yellow.length
  const greenCnt = groups.green.length, greyCnt = groups.grey.length
  const urgentCnt = redCnt + yellowCnt

  // 组合整体摘要文案
  let overallIcon = '✅', overallMsg = '今日整体平稳，无重要信号'
  if (redCnt > 0)       { overallIcon = '⚠️'; overallMsg = `${redCnt} 只需关注，请留意风险` }
  else if (yellowCnt > 0) { overallIcon = '🟡'; overallMsg = `${yellowCnt} 只有值得关注的信号` }
  else if (greenCnt > 0)  { overallIcon = '✅'; overallMsg = `${greenCnt} 只有积极信号` }

  const SectionHeader = ({ dot, label, count, color }: { dot: string; label: string; count: number; color: string }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
      <span style={{ fontSize: 13 }}>{dot}</span>
      <span style={{ fontSize: 12, fontWeight: 700, color }}>{label} · {count}只</span>
      <div style={{ flex: 1, height: 1, background: `${color}30`, marginLeft: 4 }} />
    </div>
  )

  return (
    <div style={{ paddingBottom: 20 }}>
      {/* 顶部 Header */}
      <div style={{ background: HEADER_BG, padding: '16px 16px 14px', borderBottom: `2px solid ${THEME}` }}>
        <div style={{ fontSize: 11, color: COPPER2, letterSpacing: '0.5px', marginBottom: 4 }}>AI 投资管家 · 每日简报</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: PAPER, fontFamily: SERIF }}>你的组合 · 今日简报</div>
        <div style={{ fontSize: 12, color: '#aaa', marginTop: 4 }}>{dateStr} · Kronos 多因子 + 一手情报</div>
      </div>

      {stocks.length === 0 ? (
        <div style={{ margin: '40px 16px', textAlign: 'center', color: INK_F, fontSize: 14 }}>
          请先在"自选股"中添加持仓股票
        </div>
      ) : (
        <>
          {/* 组合整体摘要卡 */}
          <div style={{ margin: '10px 12px 0', padding: '14px 16px', background: PAPER, borderRadius: 14, border: `1px solid ${urgentCnt > 0 ? '#A4332B50' : LINE}` }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: urgentCnt > 0 ? '#A4332B' : DN, marginBottom: 10 }}>
              {overallIcon} {overallMsg}
            </div>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {redCnt > 0    && <span style={{ fontSize: 12, color: ALERT_LEVEL_COLOR.red    }}>🔴 需关注 {redCnt}只</span>}
              {yellowCnt > 0 && <span style={{ fontSize: 12, color: ALERT_LEVEL_COLOR.yellow }}>🟡 关注 {yellowCnt}只</span>}
              {greenCnt > 0  && <span style={{ fontSize: 12, color: ALERT_LEVEL_COLOR.green  }}>🟢 积极 {greenCnt}只</span>}
              {greyCnt > 0   && <span style={{ fontSize: 12, color: ALERT_LEVEL_COLOR.grey   }}>⚪ 常规 {greyCnt}只</span>}
            </div>
          </div>

          {/* 🔴 需关注 */}
          {groups.red.length > 0 && (
            <div style={{ margin: '14px 12px 0' }}>
              <SectionHeader dot="🔴" label="需关注" count={groups.red.length} color={ALERT_LEVEL_COLOR.red} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {groups.red.map(s => (
                  <StockBriefCard key={s.code} s={s} q={quotes[s.code]} pred={preds[s.code]} isLoading={loadingMap[s.code]} briefStock={briefBySymbol[s.code]} />
                ))}
              </div>
            </div>
          )}

          {/* 🟡 关注 */}
          {groups.yellow.length > 0 && (
            <div style={{ margin: '14px 12px 0' }}>
              <SectionHeader dot="🟡" label="关注" count={groups.yellow.length} color={ALERT_LEVEL_COLOR.yellow} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {groups.yellow.map(s => (
                  <StockBriefCard key={s.code} s={s} q={quotes[s.code]} pred={preds[s.code]} isLoading={loadingMap[s.code]} briefStock={briefBySymbol[s.code]} />
                ))}
              </div>
            </div>
          )}

          {/* 🟢 积极信号 */}
          {groups.green.length > 0 && (
            <div style={{ margin: '14px 12px 0' }}>
              <SectionHeader dot="🟢" label="积极信号" count={groups.green.length} color={ALERT_LEVEL_COLOR.green} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {groups.green.map(s => (
                  <StockBriefCard key={s.code} s={s} q={quotes[s.code]} pred={preds[s.code]} isLoading={loadingMap[s.code]} briefStock={briefBySymbol[s.code]} />
                ))}
              </div>
            </div>
          )}

          {/* ⚪ 常规更新（默认折叠） */}
          {groups.grey.length > 0 && (
            <div style={{ margin: '14px 12px 0' }}>
              <button onClick={() => setGreyExpanded(e => !e)}
                style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: PAPER, border: `1px solid ${LINE}`, borderRadius: 14, cursor: 'pointer', marginBottom: greyExpanded ? 10 : 0 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: INK_F }}>⚪ 常规更新 · {groups.grey.length}只（无重要信号）</span>
                <span style={{ fontSize: 13, color: INK_F }}>{greyExpanded ? '∧ 收起' : '∨ 展开'}</span>
              </button>
              {greyExpanded && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {groups.grey.map(s => (
                    <StockBriefCard key={s.code} s={s} q={quotes[s.code]} pred={preds[s.code]} isLoading={loadingMap[s.code]} briefStock={briefBySymbol[s.code]} />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      <div style={{ margin: '14px 12px 0', padding: '12px 14px', background: PAPER2, borderRadius: 12, fontSize: 11, color: INK_F, lineHeight: 1.7 }}>
        ℹ Kronos（清华大学金融时序大模型）预测基于多因子量化模型（技术面+动量+资金流），一手情报来自政采/北向/季报，均不构成投资建议。
      </div>
    </div>
  )
}

// ── 真源全量分析 Tab ──────────────────────────────────────────────────────
interface ScoutSignal {
  source: string
  title: string
  content: string
  url: string
  date: string | null
}
interface SentimentScore { score: number; label: string; reason: string }
interface SentimentLevel { level: 'low' | 'mid' | 'high'; reason?: string; count?: number }
interface ScoutSummary {
  sentiment: {
    short_term:    SentimentScore
    mid_term:      SentimentScore
    attention:     SentimentLevel
    controversy:   SentimentLevel
    event_density: SentimentLevel
  }
  overview: { category: string; events: string[]; count: number }[]
  conclusion: string
  generated_at?: string
  model?: string
}
interface ScoutResult {
  symbol: string
  name: string
  price: number | null
  change_pct: number | null
  sources: {
    price: Record<string, unknown> | null
    announcements: ScoutSignal[]
    ai_search: ScoutSignal[]
    historical: ScoutSignal[]
  }
  summary?: ScoutSummary | null
  total_signals: number
  fetched_at: string
}

function ScoutTab({ token, shared, stocks, quotes }: {
  token: string; shared: ResearchStockCtx;
  stocks: Stock[]; quotes: Record<string, Quote>
}) {
  const { input, setInput, selectedCode, setSelectedCode, selectedName, setSelectedName } = shared
  const [showPicker, setShowPicker] = useState(false)
  const aStockCount = stocks.filter(s => s.market === 'A').length
  const [hits, setHits] = useState<{ code: string; name: string }[]>([])
  const [searched, setSearched] = useState(false)
  const [searching, setSearching] = useState(false)
  const [marketWarn, setMarketWarn] = useState('')
  const [searchError, setSearchError] = useState('')
  const [loading, setLoading] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<ScoutResult | null>(null)
  const [error, setError] = useState('')
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isComposingRef = useRef(false)

  const hdr = (): HeadersInit => token ? { Authorization: `Bearer ${token}` } : {}

  const detectMarket = (v: string): string => {
    const raw = v.trim()
    if (!raw) return ''
    if (/^\d{5}$/.test(raw)) return '当前仅支持A股，港股暂不支持'
    if (/^[A-Za-z]{1,5}$/.test(raw)) return '当前仅支持A股，美股暂不支持'
    return ''
  }

  const doSearch = async () => {
    const raw = input.trim()
    if (!raw) return
    setResult(null); setError(''); setSearchError('')
    setSelectedCode(''); setSelectedName('')
    const warn = detectMarket(raw)
    setMarketWarn(warn)
    if (warn) { setHits([]); setSearched(true); return }
    setSearching(true); setSearched(false)
    try {
      const items = await searchStockWithCache(raw, {
        headers: hdr(),
        onSlowNetwork: () => setSearchError('⏳ 网络较慢, 正在重试...'),
      })
      setHits(items)
      setSearchError('')  // 若之前显示了慢网络提示,现在成功了清掉
      if (items.length === 1) {
        setSelectedCode(items[0].code); setSelectedName(items[0].name)
      } else if (/^\d{6}$/.test(raw)) {
        const hit = items.find(x => x.code === raw)
        if (hit) { setSelectedCode(hit.code); setSelectedName(hit.name) }
      }
    } catch (e: unknown) {
      setHits([])
      const isTimeout = e instanceof Error && (e.message === 'timeout' || e.name === 'AbortError')
      setSearchError(isTimeout ? '查询超时(25秒 + 1 次重试), 请检查网络后再试' : '查询失败,请稍后重试')
    }
    setSearched(true); setSearching(false)
  }

  const confirmStock = (code: string, name: string) => {
    setSelectedCode(code); setSelectedName(name)
  }

  const resetSelection = () => {
    setSelectedCode(''); setSelectedName(''); setInput('')
    setHits([]); setSearched(false); setMarketWarn(''); setSearchError('')
    setResult(null); setError('')
  }

  const analyse = async () => {
    const code = selectedCode
    if (!code) return
    setLoading(true); setError(''); setResult(null); setElapsed(0)
    elapsedRef.current = setInterval(() => setElapsed(s => s + 1), 1000)
    try {
      const nameParam = selectedName ? `?name=${encodeURIComponent(selectedName)}` : ''
      const r = await fetch(`/api/truesource/scout/${code}${nameParam}`, {
        method: 'POST',
        headers: hdr(),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || '采集失败')
      setResult(d)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : '采集失败') }
    if (elapsedRef.current) clearInterval(elapsedRef.current)
    setLoading(false)
  }

  const canAnalyse = !loading && !!selectedCode

  // 无原始 url 时(如 Gemini 搜索的 AI搜索条目),根据 source 生成智能搜索链接
  // 用户点"🔍 搜原文"跳到对应财经网站站内搜索,可追溯原始信息。
  const _buildFallbackSearchUrl = (title: string, source: string): string => {
    const cleanTitle = (title || '').replace(/^\[.+?\]\s*/, '').trim()
    const q = encodeURIComponent(cleanTitle)
    const src = (source || '').toLowerCase()
    // 常见财经媒体映射到站内搜索或百度 site 搜索
    if (src.includes('新浪')) return `https://search.sina.com.cn/?q=${q}&c=news`
    if (src.includes('财联社') || src.includes('cls')) return `https://www.cls.cn/searchPage?keyword=${q}`
    if (src.includes('证券时报') || src.includes('stcn')) return `https://www.baidu.com/s?wd=site%3Astcn.com+${q}`
    if (src.includes('上海证券报') || src.includes('cnstock')) return `https://www.baidu.com/s?wd=site%3Acnstock.com+${q}`
    if (src.includes('凤凰') || src.includes('ifeng')) return `https://www.baidu.com/s?wd=site%3Afinance.ifeng.com+${q}`
    if (src.includes('东方财富') || src.includes('eastmoney')) return `https://so.eastmoney.com/news/s?keyword=${q}`
    if (src.includes('第一财经') || src.includes('yicai')) return `https://www.baidu.com/s?wd=site%3Ayicai.com+${q}`
    if (src.includes('21世纪') || src.includes('21世纪经济')) return `https://www.baidu.com/s?wd=site%3A21jingji.com+${q}`
    if (src.includes('中国证券报') || src.includes('cs.com')) return `https://www.baidu.com/s?wd=site%3Acs.com.cn+${q}`
    if (src.includes('新华') || src.includes('xinhua')) return `https://so.news.cn/#search/0/${q}/1/0`
    if (src.includes('人民')) return `http://www.baidu.com/s?wd=site%3Apeople.com.cn+${q}`
    // 兜底: 百度综合搜索"标题 + 来源"
    const srcQ = source ? `+${encodeURIComponent(source)}` : ''
    return `https://www.baidu.com/s?wd=${q}${srcQ}`
  }

  const renderSignalList = (sigs: ScoutSignal[], label: string, badge: string) => {
    if (!sigs.length) return null
    return (
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: INK, fontFamily: SERIF }}>{label}</span>
          <span style={{ background: THEME, color: '#fff', borderRadius: 20, padding: '1px 8px', fontSize: 11, fontWeight: 600 }}>{badge}</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {sigs.map((s, i) => {
            const hasUrl = !!s.url
            const linkUrl = hasUrl ? s.url : _buildFallbackSearchUrl(s.title, s.source)
            const linkLabel = hasUrl ? '原文↗' : '🔍搜原文'
            return (
              <div key={i} style={{ background: PAPER, borderRadius: 10, padding: '10px 12px', border: `1px solid ${LINE}` }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                  <span style={{ fontSize: 13, color: INK, lineHeight: 1.5, flex: 1 }}>
                    {s.title.replace(/^\[.+?\]\s*/, '')}
                  </span>
                  <a href={linkUrl} target="_blank" rel="noopener noreferrer"
                    style={{
                      flexShrink: 0, fontSize: 11,
                      color: hasUrl ? THEME : '#8a5a1a',
                      border: `1px solid ${hasUrl ? THEME : '#d4a24a'}`,
                      background: hasUrl ? '#fff' : '#fff8e6',
                      borderRadius: 6, padding: '2px 7px',
                      textDecoration: 'none', whiteSpace: 'nowrap',
                    }}>
                    {linkLabel}
                  </a>
                </div>
                {s.content && s.content !== s.title && (
                  <div style={{ fontSize: 12, color: INK_F, marginTop: 5, lineHeight: 1.5 }}>{s.content.slice(0, 100)}</div>
                )}
                {s.date && (
                  <div style={{ fontSize: 11, color: '#bbb', marginTop: 4 }}>{s.date.slice(0, 10)}</div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: '10px 12px 24px' }}>
      {/* 产品介绍 */}
      <IntroCard
        storageKey="hunter_intro_scout"
        icon="🔍"
        title="一手情报 · 机构调研级第一手事件"
        description="巨潮公告 + Gemini AI 搜索 + 研发扩张 + 北向持仓，每条带原文链接可点击"
        badges={['一手情报', '带源链接', '全A股', '无观点加工']}
        gradient="linear-gradient(135deg, #e8f4f8 0%, #d5e6ee 100%)"
        border="#a8c8d4"
        accent="#2e5f7a"
        textColor="#4a6a80"
      />

      {/* 搜索栏 + 查询按钮 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1, display: 'flex', gap: 8, background: PAPER, borderRadius: 12, padding: '10px 12px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', alignItems: 'center', border: `1px solid ${LINE}` }}>
            <span style={{ fontSize: 16 }}>🔍</span>
            <input
              value={input}
              onChange={e => { setInput(e.target.value); setSearched(false); setMarketWarn(''); setSearchError('') }}
              onCompositionStart={() => { isComposingRef.current = true }}
              onCompositionEnd={() => { isComposingRef.current = false }}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              placeholder="输入股票名称或代码，点击查询"
              style={{ flex: 1, border: 'none', outline: 'none', fontSize: 15, background: 'transparent', color: INK, fontFamily: SERIF, minWidth: 0 }}
            />
            {input && <button onClick={resetSelection} style={{ background: 'none', border: 'none', color: '#aaa', fontSize: 16, cursor: 'pointer', padding: 0 }}>✕</button>}
          </div>
          <button onClick={doSearch} disabled={!input.trim() || searching}
            style={{ flexShrink: 0, padding: '0 16px', background: input.trim() ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 12, fontSize: 14, fontWeight: 600, cursor: input.trim() ? 'pointer' : 'not-allowed' }}>
            {searching ? '查询中' : '查询'}
          </button>
        </div>

        {marketWarn && (
          <div style={{ padding: '10px 12px', background: '#fff3e6', border: '1px solid #f5c691', borderRadius: 10, fontSize: 13, color: '#a05a1a', marginBottom: 8 }}>
            ⚠️ {marketWarn}，敬请期待
          </div>
        )}

        {searchError && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ flex: 1 }}>{searchError}</span>
            <button onClick={doSearch} style={{ background: '#a04040', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}>重试</button>
          </div>
        )}

        {/* 从自选选择入口 */}
        {!selectedCode && (
          <WatchlistPickerButton
            count={aStockCount}
            onOpen={() => setShowPicker(true)}
            onEmptyClick={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}
        {showPicker && (
          <WatchlistPicker
            stocks={stocks} quotes={quotes}
            marketFilter="A"
            currentCode={selectedCode}
            onPick={confirmStock}
            onClose={() => setShowPicker(false)}
            onAddMore={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}

        {!selectedCode && searched && !marketWarn && !searchError && hits.length > 0 && (
          <div style={{ background: PAPER, borderRadius: 10, border: `1px solid ${LINE}`, overflow: 'hidden' }}>
            <div style={{ padding: '8px 12px', fontSize: 12, color: INK_F, background: PAPER2 }}>请选择要采集的股票：</div>
            {hits.map(hit => (
              <div key={hit.code} onClick={() => confirmStock(hit.code, hit.name)}
                style={{ padding: '12px 14px', cursor: 'pointer', borderTop: `1px solid ${PAPER2}`, display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 14, color: INK, fontFamily: SERIF, fontWeight: 600 }}>{hit.name}</span>
                <span style={{ fontSize: 12, color: INK_F }}>{hit.code}</span>
                <span style={{ marginLeft: 'auto', fontSize: 12, color: THEME }}>选择 ›</span>
              </div>
            ))}
          </div>
        )}

        {!selectedCode && searched && !marketWarn && !searchError && hits.length === 0 && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040' }}>
            未找到相关A股，请检查股票名称或代码
          </div>
        )}

        {selectedCode && (
          <div style={{ padding: '12px 14px', background: '#fff', border: `2px solid ${THEME}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: THEME }}>已选择：</span>
            <span style={{ fontSize: 15, color: INK, fontWeight: 700, fontFamily: SERIF }}>{selectedName}</span>
            <span style={{ fontSize: 13, color: INK_F }}>{selectedCode}</span>
            <button onClick={resetSelection}
              style={{ marginLeft: 'auto', background: 'none', border: `1px solid ${LINE}`, borderRadius: 8, padding: '4px 10px', fontSize: 12, color: INK_S, cursor: 'pointer' }}>
              更换
            </button>
          </div>
        )}
      </div>

      {/* 采集按钮 */}
      <button onClick={analyse} disabled={!canAnalyse}
        style={{ width: '100%', padding: '13px 0', background: canAnalyse ? THEME : PAPER2, color: canAnalyse ? '#fff' : INK_F, border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: canAnalyse ? 'pointer' : 'not-allowed', marginBottom: 12, fontFamily: SERIF }}>
        {loading ? `采集中… ${elapsed}s` : selectedCode ? '一手情报采集' : '请先查询并选择股票'}
      </button>

      {/* 加载提示 */}
      {loading && (
        <div style={{ background: '#f0f7ff', borderRadius: 10, padding: '12px 14px', fontSize: 13, color: '#2563eb', marginBottom: 12, lineHeight: 1.6 }}>
          🔄 正在并行采集：价格行情 · 巨潮公告 · Gemini AI 产业链搜索<br />
          <span style={{ fontSize: 11, color: '#6b7280' }}>Gemini 搜索耗时约 30-50s，请稍候…</span>
        </div>
      )}

      {error && <div style={{ background: '#fef2f2', color: '#dc2626', padding: '10px 14px', borderRadius: 10, fontSize: 13, marginBottom: 12 }}>{error}</div>}

      {/* 结果区 */}
      {result && (
        <div>
          {/* 标的信息卡 */}
          <div style={{ background: `linear-gradient(135deg,${THEME},${COPPER2})`, borderRadius: 14, padding: '16px 18px', marginBottom: 14, color: '#fff' }}>
            <div style={{ fontSize: 18, fontWeight: 700, fontFamily: SERIF }}>{result.name}</div>
            <div style={{ fontSize: 13, opacity: 0.85, marginTop: 2 }}>{result.symbol}</div>
            {result.price != null && (
              <div style={{ marginTop: 10, display: 'flex', gap: 20 }}>
                <div>
                  <div style={{ fontSize: 11, opacity: 0.75 }}>最新价</div>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>¥{result.price.toFixed(2)}</div>
                </div>
                {result.change_pct != null && (
                  <div>
                    <div style={{ fontSize: 11, opacity: 0.75 }}>涨跌幅</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{result.change_pct > 0 ? '+' : ''}{result.change_pct.toFixed(2)}%</div>
                  </div>
                )}
              </div>
            )}
            <div style={{ fontSize: 11, opacity: 0.6, marginTop: 8 }}>共 {result.total_signals} 条信号 · {result.fetched_at.slice(0, 16).replace('T', ' ')}</div>
          </div>

          {/* ✨ AI 汇总总结（Gemini 3.5 生成） */}
          {result.summary && (
            <div style={{ marginBottom: 14 }}>
              {/* 综合总结 */}
              <div style={{ background: '#fff', border: `2px solid ${THEME}`, borderRadius: 12, padding: '14px', marginBottom: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: THEME, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>✨</span><span>AI 综合总结</span>
                  <span style={{ fontSize: 10, color: INK_F, fontWeight: 400 }}>· Gemini 3.5</span>
                </div>
                <div style={{ fontSize: 13, color: INK, lineHeight: 1.8, fontFamily: SERIF }}>{result.summary.conclusion}</div>
              </div>

              {/* 市场情绪 · 分组式(方向 + 可信度) + 综合判断 */}
              {(() => {
                const s = result.summary!.sentiment
                // 方向色:偏涨红,偏跌绿,中性灰
                const dirColor = (v: number) => v > 0.3 ? '#dc2626' : v < -0.3 ? '#16a34a' : '#6b7280'
                const dirArrow = (v: number) =>
                  v > 0.6 ? '↗↗' : v > 0.2 ? '↗' : v < -0.6 ? '↘↘' : v < -0.2 ? '↘' : '→'
                const dirWord = (v: number) =>
                  v > 0.6 ? '强涨' : v > 0.2 ? '偏涨' : v < -0.6 ? '强跌' : v < -0.2 ? '偏跌' : '中性'
                const dirHint = (v: number) =>
                  v > 0.6 ? '趋势明确,方向偏强' :
                  v > 0.2 ? '消息面利好占上风' :
                  v < -0.6 ? '趋势明确,方向偏弱' :
                  v < -0.2 ? '消息面利空占上风' :
                  '多空平衡, 方向不明'

                // 综合判断生成器: 一句人话把 5 个维度串起来
                const st = s.short_term.score
                const mt = s.mid_term.score
                const att = s.attention.level
                const ctrl = s.controversy.level
                const cnt = s.event_density.count || 0
                const direction =
                  st > 0.2 && mt > 0.2 ? '短期与中期同向偏涨,趋势明确' :
                  st < -0.2 && mt < -0.2 ? '短期与中期同向偏跌,趋势明确' :
                  st > 0.2 && mt < -0.2 ? '短期偏涨但中期承压,警惕反弹结束' :
                  st < -0.2 && mt > 0.2 ? '短期扰动但中期向好,或是低吸机会' :
                  Math.abs(st) < 0.2 && Math.abs(mt) < 0.2 ? '方向不明, 建议观望' :
                  '方向偏向' + (Math.abs(st) + Math.abs(mt) > 0 && (st + mt) > 0 ? '偏涨' : '偏跌')
                const reliability =
                  ctrl === 'low' && cnt >= 10 ? '各方共识明确 + 样本充足, 结论较可信' :
                  ctrl === 'high' ? '各方分歧较大, 结论仅供参考' :
                  cnt < 5 ? '样本较少, 建议结合更多信息' :
                  '有一定数据基础'
                const volatility =
                  att === 'high' && ctrl === 'high' ? '⚠ 高热度 + 高分歧, 波动可能剧烈' :
                  att === 'high' ? '⚠ 热度高, 对消息敏感, 涨跌容易被放大' :
                  ''

                // 单元格通用组件
                const dirCell = (icon: string, period: string, v: number) => (
                  <div key={period} style={{
                    background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10,
                    padding: '12px 8px', textAlign: 'center',
                  }}>
                    <div style={{ fontSize: 11, color: INK_F, marginBottom: 3 }}>
                      <span style={{ marginRight: 3 }}>{icon}</span>{period}
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: dirColor(v), lineHeight: 1.1 }}>
                      {dirArrow(v)} <span style={{ fontSize: 15 }}>{dirWord(v)}</span>
                    </div>
                    <div style={{ fontSize: 11, color: dirColor(v), marginTop: 3, fontWeight: 600 }}>
                      {v >= 0 ? `+${v.toFixed(1)}` : v.toFixed(1)}
                    </div>
                    <div style={{ fontSize: 10, color: INK_F, marginTop: 5, lineHeight: 1.4 }}>
                      {dirHint(v)}
                    </div>
                  </div>
                )
                const qualCell = (icon: string, name: string, big: string, bigColor: string, hint: string) => (
                  <div key={name} style={{
                    background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10,
                    padding: '10px 4px', textAlign: 'center',
                  }}>
                    <div style={{ fontSize: 10, color: INK_F, marginBottom: 3 }}>
                      <span style={{ marginRight: 2 }}>{icon}</span>{name}
                    </div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: bigColor, lineHeight: 1.1 }}>{big}</div>
                    <div style={{ fontSize: 10, color: INK_F, marginTop: 5, lineHeight: 1.4 }}>{hint}</div>
                  </div>
                )
                const attnColor = att === 'high' ? '#dc2626' : att === 'mid' ? '#f59e0b' : '#6b7280'
                const ctrlColor = ctrl === 'high' ? '#dc2626' : ctrl === 'mid' ? '#f59e0b' : '#16a34a'
                const cntColor = cnt >= 15 ? '#dc2626' : cnt >= 5 ? '#6b7280' : '#9ca3af'

                return (
                  <div style={{ marginBottom: 10 }}>
                    {/* 卡片标题 */}
                    <div style={{ fontSize: 12, fontWeight: 700, color: INK, marginBottom: 8,
                                  display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span>📊</span><span>市场怎么看这只股</span>
                      <span style={{ fontSize: 10, color: INK_F, fontWeight: 400, marginLeft: 4 }}>
                        (最近 {cnt} 条)
                      </span>
                    </div>

                    {/* 方向组 */}
                    <div style={{ fontSize: 11, color: INK_S, fontWeight: 600, marginBottom: 6 }}>
                      【会涨还是会跌?】
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6, marginBottom: 10 }}>
                      {dirCell('🕐', '短期(2周)', st)}
                      {dirCell('📅', '中期(3月)', mt)}
                    </div>

                    {/* 质量组 */}
                    <div style={{ fontSize: 11, color: INK_S, fontWeight: 600, marginBottom: 6 }}>
                      【判断有多靠谱?】
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginBottom: 10 }}>
                      {qualCell(
                        '🤝', '意见分歧',
                        ctrl === 'high' ? '很大' : ctrl === 'mid' ? '一般' : '很小',
                        ctrlColor,
                        ctrl === 'high' ? '争议大,波动可能大' : ctrl === 'mid' ? '有一定分歧' : '共识明确,结论可信',
                      )}
                      {qualCell(
                        '🔥', '讨论热度',
                        att === 'high' ? '很高' : att === 'mid' ? '一般' : '较低',
                        attnColor,
                        att === 'high' ? '对消息敏感, 波动加大' : att === 'mid' ? '关注度正常' : '关注较少',
                      )}
                      {qualCell(
                        '📊', '消息数量', `${cnt} 条`, cntColor,
                        cnt >= 15 ? '活跃期, 样本充足' : cnt >= 5 ? '样本正常' : '样本较少',
                      )}
                    </div>

                    {/* 综合判断 */}
                    <div style={{
                      background: '#fff8e6', border: '1px solid #f5d38b', borderRadius: 10,
                      padding: '10px 12px', fontSize: 12, color: '#5a4525', lineHeight: 1.7,
                    }}>
                      <div style={{ fontWeight: 700, marginBottom: 4, color: '#a86828' }}>💡 综合判断</div>
                      <div>{direction}, {reliability}。{volatility}</div>
                    </div>
                  </div>
                )
              })()}

              {/* 整体走向 5 大类归纳 */}
              {result.summary.overview.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: INK, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span>🗂️</span><span>整体走向 · {result.summary.overview.length} 类归纳</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {result.summary.overview.map((cat, i) => (
                      <div key={i} style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8, padding: '10px 12px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: THEME }}>{cat.category}</span>
                          <span style={{ background: THEME, color: '#fff', borderRadius: 10, padding: '1px 7px', fontSize: 10 }}>{cat.count}</span>
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12, color: INK_S, lineHeight: 1.7 }}>
                          {cat.events.map((ev, j) => (<li key={j}>{ev}</li>))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* AI 产业链信号 */}
          {renderSignalList(result.sources.ai_search, '🤖 AI产业链信号', `${result.sources.ai_search.length}条`)}

          {/* 公告 */}
          {renderSignalList(result.sources.announcements, '📋 最新公告', `${result.sources.announcements.length}条`)}

          {/* 历史信号 */}
          {renderSignalList(result.sources.historical, '📡 历史信号（近7日）', `${result.sources.historical.length}条`)}

          {!result.sources.ai_search.length && !result.sources.announcements.length && !result.sources.historical.length && (
            <div style={{ textAlign: 'center', color: INK_F, fontSize: 13, padding: '20px 0' }}>暂无信号数据</div>
          )}
        </div>
      )}
    </div>
  )
}

// ── 投研助手 Tab (V4) ─────────────────────────────────────────────────────
// 用户视角"我要不要买 / 该不该拿 / 什么时候买"→ 智能路由到对应智能体
// 详见 doc/20260724-研究tab智能推荐与投研助手方案.md 方案 D

// P1-P3：LLM 对话数据模型
type AssistantIntent = TargetMode | 'chat'
interface AssistantAction { mode: TargetMode; label: string; reason?: string }
interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
  intent?: AssistantIntent    // V2 加 'chat' 通用对话
  suggested_action?: AssistantAction | null
  additional_actions?: AssistantAction[]
  fallback?: boolean  // 若为 true 表示是降级到关键词的兜底回复
}

const QUICK_QUESTIONS: { icon: string; label: string; mode: TargetMode }[] = [
  { icon: '🔍', label: '了解这只票',  mode: 'research' },
  { icon: '🛡', label: '该不该继续拿', mode: 'hold' },
  { icon: '🎯', label: '找买卖时点',   mode: 'kpred' },
  { icon: '⚡', label: '事件影响',    mode: 'event' },
]

const AGENT_GRID: { icon: string; label: string; mode: TargetMode; desc: string }[] = [
  { icon: '📊', label: '深度研究', mode: 'research', desc: '这只值不值得买' },
  { icon: '🔍', label: '一手情报', mode: 'scout',    desc: '别人还不知道的动态' },
  { icon: '🎯', label: '量化择时', mode: 'kpred',    desc: '什么时候买合适' },
  { icon: '🛡', label: '持仓研判', mode: 'hold',     desc: '还该不该继续拿' },
  { icon: '⚡', label: '事件解读', mode: 'event',    desc: '突发消息影响我吗' },
]

// 智能体使用完成后的"接下来看什么"串联引导
// 只在 ④挖掘 tab 内部串联(hold/event 已移至 ③持仓, 由该 tab 自己的三个二级承接)
const NEXT_STEP_RULES: Record<TargetMode, TargetMode[]> = {
  research: ['kpred', 'scout'],       // 深度研究 → 补时点 + 补事实
  scout:    ['research'],             // 一手情报 → 综合视角消化
  kpred:    ['research'],             // 量化择时 → 补基本面
  hold:     [],
  event:    [],
}

/** 空态引导卡:新用户没有自选股时,告诉他这个产品怎么用(四步闭环) */
function StartGuideCard({ onGoDiscover, onSearch }: { onGoDiscover: () => void; onSearch: () => void }) {
  const STEPS = [
    { n: '①', t: '选股', d: '按你关注的板块,挑出值得研究的股票' },
    { n: '②', t: '盯盘', d: '加进自选,AI 帮你盯,该看时手机会响' },
    { n: '③', t: '持仓', d: '买了之后,多空辩论帮你判断还该不该拿' },
    { n: '④', t: '挖掘', d: '一手情报找下一只,量化择时找入场点' },
  ]
  return (
    <div style={{ padding: '20px 14px 24px' }}>
      <div style={{ textAlign: 'center', marginBottom: 18 }}>
        <div style={{ fontSize: 40, marginBottom: 8 }}>🦌</div>
        <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF }}>四步用起来</div>
        <div style={{ fontSize: 12, color: INK_F, marginTop: 4 }}>投资是循环,不是一次交易</div>
      </div>
      <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 14, padding: '6px 14px', marginBottom: 16 }}>
        {STEPS.map((s, i) => (
          <div key={s.n} style={{ display: 'flex', gap: 12, padding: '13px 0', borderBottom: i < 3 ? `1px solid ${PAPER2}` : 'none' }}>
            <span style={{ fontSize: 17, color: THEME, fontWeight: 700, flexShrink: 0 }}>{s.n}</span>
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: INK, fontFamily: SERIF }}>{s.t}</div>
              <div style={{ fontSize: 12, color: INK_S, marginTop: 3, lineHeight: 1.6 }}>{s.d}</div>
            </div>
          </div>
        ))}
      </div>
      <button onClick={onGoDiscover} style={{ width: '100%', padding: '14px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer', fontFamily: SERIF, marginBottom: 10 }}>
        从 ① 选股开始 →
      </button>
      <button onClick={onSearch} style={{ width: '100%', padding: '12px 0', background: 'transparent', color: THEME, border: `1px solid ${THEME}`, borderRadius: 12, fontSize: 13, cursor: 'pointer', fontFamily: SERIF }}>
        已有目标股票,直接搜索添加
      </button>
    </div>
  )
}

function NextStepCTA({
  current, onSwitchMode,
}: {
  current: TargetMode
  onSwitchMode: (m: TargetMode) => void
}) {
  const nexts = NEXT_STEP_RULES[current] || []
  if (!nexts.length) return null
  return (
    <div style={{
      margin: '14px 12px 20px', padding: '14px 16px',
      background: PAPER, border: `1px solid ${LINE}`, borderRadius: 12,
      borderTop: `3px solid ${THEME}`,
    }}>
      <div style={{ fontSize: 12, color: THEME, marginBottom: 10, fontWeight: 700, letterSpacing: 0.3 }}>
        🎯 接下来可以看
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {nexts.map(m => {
          const agent = AGENT_GRID.find(a => a.mode === m)
          if (!agent) return null
          return (
            <button key={m} onClick={() => onSwitchMode(m)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                background: PAPER2, border: `1px solid ${LINE}`, borderRadius: 10,
                cursor: 'pointer', textAlign: 'left', width: '100%',
              }}>
              <span style={{ fontSize: 20 }}>{agent.icon}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: INK, fontFamily: SERIF, fontWeight: 600 }}>
                  {agent.label}
                </div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{agent.desc}</div>
              </div>
              <span style={{ color: THEME, fontSize: 15 }}>›</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/**
 * AgentChatV2 —— 2026-08-04 起全量放量：直接 render 新版，删掉灰度分支避免闪屏
 * 老版 ResearchAssistantTab 函数保留在文件里（其他 tab 可能复用其内部工具函数），
 * 但不再被 render。若确认无其他引用可后续整体清理。
 */
function AssistantSwitcher(props: {
  token: string
  stocks: Stock[]
  quotes?: Record<string, any>
  shared: ResearchStockCtx
  onSwitchMode: (m: TargetMode) => void
}) {
  return (
    <ResearchAssistantChatV2
      token={props.token}
      stocks={props.stocks}
      quotes={props.quotes as any}
      shared={{ code: props.shared.selectedCode || undefined,
                 name: props.shared.selectedName || undefined }}
      onOpenExpertGrid={() => props.onSwitchMode('research')}
    />
  )
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function _KeepBelowResearchAssistantTabFn(): null {
  // 老 ResearchAssistantTab 函数定义在下方,现已被 AssistantSwitcher 短路不再渲染。
  // 保留原函数便于日后审查/复用其中的 helper（如 QUICK_QUESTIONS / AGENT_GRID / intent 逻辑）。
  return null
}

function ResearchAssistantTab({
  token, stocks, shared, onSwitchMode,
}: {
  token: string
  stocks: Stock[]
  shared: ResearchStockCtx
  onSwitchMode: (m: TargetMode) => void
}) {
  const { input, setInput, selectedCode, setSelectedCode, selectedName, setSelectedName } = shared
  const [hasThesis, setHasThesis] = useState(false)
  const [thesisLoading, setThesisLoading] = useState(false)
  const [hits, setHits] = useState<{ code: string; name: string }[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [chatInput, setChatInput] = useState('')
  const [marketWarn, setMarketWarn] = useState('')
  // P1-P3：LLM 对话状态
  const [chatBusy, setChatBusy] = useState(false)
  const [sessionId, setSessionId] = useState<string>('')
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  // V3：复制反馈 + 输入框 auto-resize
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null)
  const [savingImgIdx, setSavingImgIdx] = useState<number | null>(null)
  const bubbleRefs = useRef<(HTMLDivElement | null)[]>([])
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  // V4：微信 webview 图片预览 modal（微信里 <a download> 被禁 → 展示图长按保存）
  const [imgModalUrl, setImgModalUrl] = useState<string>('')
  const isWechat = typeof navigator !== 'undefined' && /MicroMessenger/i.test(navigator.userAgent)

  // 复制文本到剪贴板
  const copyMessage = async (text: string, idx: number) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedIdx(idx)
      setTimeout(() => setCopiedIdx(null), 2000)
    } catch {
      // 兜底：老浏览器
      const ta = document.createElement('textarea')
      ta.value = text; document.body.appendChild(ta); ta.select()
      try { document.execCommand('copy'); setCopiedIdx(idx); setTimeout(() => setCopiedIdx(null), 2000) } catch {}
      document.body.removeChild(ta)
    }
  }

  // V4：生成图片
  // - 微信 webview：展示 modal 让用户长按保存（微信禁 <a download>）
  // - 普通浏览器：直接 <a download> 下载
  const saveMessageAsImage = async (idx: number, stockName: string) => {
    const node = bubbleRefs.current[idx]
    if (!node) return
    setSavingImgIdx(idx)
    try {
      const html2canvas = (await import('html2canvas')).default
      const canvas = await html2canvas(node, {
        backgroundColor: '#FBF1E4',
        scale: 2,  // 高清（Retina）
        useCORS: true,
        logging: false,
      })

      if (isWechat) {
        // 微信：用 dataURL 展示到 modal，用户长按保存
        // （blob URL 在部分微信版本长按不出"保存到相册"菜单，用 dataURL 更稳）
        const dataUrl = canvas.toDataURL('image/png')
        setImgModalUrl(dataUrl)
      } else {
        // 普通浏览器：直接触发下载
        canvas.toBlob(blob => {
          if (!blob) return
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-')
          a.href = url
          a.download = `Hunter-${stockName || 'chat'}-${ts}.png`
          document.body.appendChild(a); a.click(); document.body.removeChild(a)
          URL.revokeObjectURL(url)
        }, 'image/png')
      }
    } catch (e) {
      console.error('save image failed', e)
    } finally {
      setSavingImgIdx(null)
    }
  }

  // textarea auto-resize
  const autoResize = () => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
  }
  useEffect(() => { autoResize() }, [chatInput])

  const hdr = (): HeadersInit => token ? { Authorization: `Bearer ${token}` } : {}
  const isInWatchlist = !!selectedCode && stocks.some(s => s.code === selectedCode)

  // 拉取 has_thesis
  useEffect(() => {
    if (!token || !selectedCode) { setHasThesis(false); return }
    setThesisLoading(true)
    fetchJsonWithTimeout<{ has_thesis?: boolean }>(
      `/api/watchlist/${encodeURIComponent(selectedCode)}/thesis`,
      { headers: hdr() },
      8000,
    ).then(d => setHasThesis(!!d.has_thesis))
      .catch(() => setHasThesis(false))
      .finally(() => setThesisLoading(false))
  }, [token, selectedCode]) // eslint-disable-line react-hooks/exhaustive-deps

  const detectMarket = (v: string): string => {
    const raw = v.trim()
    if (!raw) return ''
    if (/^\d{5}$/.test(raw)) return '当前仅支持 A 股，港股暂不支持'
    if (/^[A-Za-z]{1,5}$/.test(raw)) return '当前仅支持 A 股，美股暂不支持'
    return ''
  }

  const doSearch = async () => {
    const raw = input.trim()
    if (!raw) return
    setSearchError(''); setSelectedCode(''); setSelectedName('')
    const warn = detectMarket(raw); setMarketWarn(warn)
    if (warn) { setHits([]); setSearched(true); return }
    setSearching(true); setSearched(false)
    try {
      const items = await searchStockWithCache(raw, {
        headers: hdr(),
        onSlowNetwork: () => setSearchError('⏳ 网络较慢, 正在重试...'),
      })
      setHits(items)
      setSearchError('')
      if (items.length === 1) {
        setSelectedCode(items[0].code); setSelectedName(items[0].name)
      } else if (/^\d{6}$/.test(raw)) {
        const hit = items.find(x => x.code === raw)
        if (hit) { setSelectedCode(hit.code); setSelectedName(hit.name) }
      }
    } catch (e: unknown) {
      setHits([])
      const isTimeout = e instanceof Error && (e.message === 'timeout' || e.name === 'AbortError')
      setSearchError(isTimeout ? '查询超时(25秒 + 1 次重试), 请检查网络后再试' : '查询失败,请稍后重试')
    }
    setSearched(true); setSearching(false)
  }

  // 切股票时清空当前会话（不同股不共用 session，避免上下文错乱）
  useEffect(() => {
    setMessages([]); setSessionId('')
  }, [selectedCode])

  const routeQuery = async () => {
    const q = chatInput.trim()
    if (!q || chatBusy) return
    setChatBusy(true)
    // 乐观 UI：立即插入 user 消息
    setMessages(prev => [...prev, { role: 'user', content: q }])
    setChatInput('')
    try {
      const r = await fetch('/api/research-assistant/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...hdr() },
        body: JSON.stringify({
          query: q,
          stock_code: selectedCode || undefined,
          stock_name: selectedName || undefined,
          session_id: sessionId || undefined,
          context: { has_thesis: hasThesis, in_watchlist: isInWatchlist },
        }),
      })
      if (!r.ok) throw new Error(`http_${r.status}`)
      const d = await r.json()
      if (d.session_id) setSessionId(d.session_id)
      setMessages(prev => [...prev, {
        role: 'assistant', content: d.reply || '',
        intent: d.intent, suggested_action: d.suggested_action,
        additional_actions: d.additional_actions,
      }])
    } catch {
      // 降级：本地关键词路由 + 标注 fallback
      const { mode, matched } = routeByIntent(q)
      const label = MODE_LABEL[mode]
      setMessages(prev => [...prev, {
        role: 'assistant', fallback: true,
        content: matched
          ? `AI 助手暂时不可用，按关键词识别为「${label}」意图，你可以直接前往。`
          : `AI 助手暂时不可用，未识别到明确意图，先给你带到「${label}」，如果不对可返回助手。`,
        suggested_action: { mode, label: `去做${label}`, reason: '' },
      }])
    } finally {
      setChatBusy(false)
    }
  }

  // 重置对话（保留 session，清空消息 + 重置本地状态）
  const resetChat = async () => {
    if (sessionId) {
      try {
        await fetch(`/api/research-assistant/session/${encodeURIComponent(sessionId)}/reset`,
          { method: 'POST', headers: hdr() })
      } catch { /* 静默 */ }
    }
    setMessages([]); setSessionId('')
  }

  const rec = getRecommendation({
    stockCode:     selectedCode,
    stockName:     selectedName,
    isInWatchlist,
    hasThesis,
  })

  const stockLocked = !!selectedCode

  return (
    <div style={{ padding: '14px 12px 40px' }}>
      {/* V4：图片预览 modal（微信 webview 里长按保存到相册）*/}
      {imgModalUrl && (
        <div
          onClick={() => setImgModalUrl('')}
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.9)',
            zIndex: 1000, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', padding: '20px 16px',
          }}>
          <div style={{
            color: '#FBF1E4', fontSize: 14, marginBottom: 14, textAlign: 'center',
            lineHeight: 1.7,
          }}>
            👇 <b>长按图片</b>，选「保存到相册」<br />
            <span style={{ fontSize: 12, color: '#BFB3A0' }}>（点空白处关闭）</span>
          </div>
          <img
            src={imgModalUrl}
            alt="Hunter AI 分析"
            onClick={e => e.stopPropagation()}
            style={{
              maxWidth: '100%', maxHeight: '75vh', objectFit: 'contain',
              borderRadius: 8, boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
            }} />
          <button onClick={() => setImgModalUrl('')} style={{
            marginTop: 20, padding: '10px 28px', background: 'transparent',
            border: '1px solid rgba(255,255,255,0.4)', borderRadius: 20,
            color: '#fff', fontSize: 13, cursor: 'pointer',
          }}>
            关闭
          </button>
        </div>
      )}

      {/* ── 股票选择区 ── */}
      <div style={{
        background: PAPER, border: `1px solid ${LINE}`, borderRadius: 12,
        padding: '12px 14px', marginBottom: 14,
      }}>
        <div style={{ fontSize: 12, color: INK_F, marginBottom: 8, letterSpacing: 0.3 }}>
          🎯 选一只股票开始
        </div>
        {stockLocked ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '8px 12px', background: '#F0F9F0', border: '1px solid #C7E0C9',
            borderRadius: 8,
          }}>
            <span style={{ fontSize: 14, color: DN }}>
              ✅ 已选 · <b>{selectedName || selectedCode}</b>
              <span style={{ marginLeft: 6, color: INK_F, fontFamily: 'monospace', fontSize: 12 }}>
                {selectedCode}
              </span>
            </span>
            <button onClick={() => { setSelectedCode(''); setSelectedName(''); setInput(''); setHits([]); setSearched(false) }}
              style={{ background: 'none', border: 'none', color: INK_F, fontSize: 12, cursor: 'pointer' }}>
              × 换一只
            </button>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={input}
                onChange={e => { setInput(e.target.value); setMarketWarn(''); setSearchError('') }}
                onKeyDown={e => { if (e.key === 'Enter') doSearch() }}
                placeholder="输入股票名称或代码，如 中际旭创"
                style={{
                  flex: 1, padding: '10px 12px', fontSize: 14, borderRadius: 8,
                  border: `1.5px solid ${LINE}`, background: '#fff', color: INK, outline: 'none',
                }} />
              <button onClick={doSearch} disabled={!input.trim() || searching}
                style={{
                  padding: '0 14px', borderRadius: 8, background: (!input.trim() || searching) ? '#C9B9A5' : THEME,
                  color: '#fff', border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
                }}>
                {searching ? '查询中' : '🔍 查询'}
              </button>
            </div>
            {marketWarn && (
              <div style={{ marginTop: 8, padding: '8px 12px', background: '#FEF2F2', color: UP, borderRadius: 8, fontSize: 12 }}>
                ⚠️ {marketWarn}
              </div>
            )}
            {searchError && (
              <div style={{ marginTop: 8, padding: '8px 12px', background: PAPER2, color: INK_F, borderRadius: 8, fontSize: 12 }}>
                🔍 {searchError}
              </div>
            )}
            {hits.length > 1 && (
              <div style={{ marginTop: 8, border: `1px solid ${LINE}`, borderRadius: 8, overflow: 'hidden' }}>
                <div style={{ padding: '6px 12px', background: PAPER2, fontSize: 11, color: INK_F }}>
                  找到 {hits.length} 个匹配，点击选择：
                </div>
                {hits.map(h => (
                  <div key={h.code}
                    onClick={() => { setSelectedCode(h.code); setSelectedName(h.name); setHits([]) }}
                    style={{ padding: '10px 12px', fontSize: 14, background: '#fff', cursor: 'pointer', borderTop: `1px solid ${PAPER2}` }}>
                    <b>{h.name}</b>
                    <span style={{ color: INK_F, marginLeft: 8, fontFamily: 'monospace', fontSize: 12 }}>{h.code}</span>
                  </div>
                ))}
              </div>
            )}
            {searched && hits.length === 0 && !marketWarn && !searchError && (
              <div style={{ marginTop: 8, padding: '8px 12px', background: PAPER2, color: INK_F, borderRadius: 8, fontSize: 12 }}>
                🔍 未识别到该股票，请换个关键词试试
              </div>
            )}
          </>
        )}
      </div>

      {/* V3：未选股时的引导（仅显示专家 grid，避免用户没上下文就提问）*/}
      {!stockLocked && (
        <div style={{
          margin: '14px 0', padding: '12px 14px',
          background: '#FBF1E4', border: `1px dashed ${COPPER2}`, borderRadius: 10,
          fontSize: 12, color: THEME, lineHeight: 1.7,
        }}>
          👆 先选一只股票，AI 会基于该股给你个性化的分析和建议
        </div>
      )}

      {/* ── 智能推荐条（仅在已选股时显示）── */}
      {stockLocked && rec && !thesisLoading && (
        <div style={{
          background: '#FBF1E4', border: `1px solid ${COPPER2}`, borderRadius: 12,
          padding: '14px 16px', marginBottom: 14, borderLeft: `3px solid ${THEME}`,
        }}>
          <div style={{ fontSize: 12, color: THEME, marginBottom: 6, fontWeight: 700, letterSpacing: 0.3 }}>
            💡 智能推荐 · 建议先看 {rec.title}
          </div>
          <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.7, marginBottom: 10 }}>
            {rec.reason}
          </div>
          <button onClick={() => onSwitchMode(rec.mode)}
            style={{
              padding: '9px 20px', background: THEME, color: '#fff', border: 'none',
              borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}>
            {rec.cta} →
          </button>
        </div>
      )}

      {/* ── 2x2 快捷问题按钮（未选股时隐藏，避免无上下文提问）── */}
      {stockLocked && <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12, color: INK_F, marginBottom: 8, letterSpacing: 0.3 }}>
          💬 我想…
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {QUICK_QUESTIONS.map(q => (
            <button key={q.mode}
              onClick={() => onSwitchMode(q.mode)}
              disabled={!stockLocked}
              style={{
                padding: '12px 8px', background: stockLocked ? PAPER : PAPER2,
                border: `1px solid ${stockLocked ? LINE : PAPER2}`, borderRadius: 10,
                fontSize: 13, color: stockLocked ? INK : INK_F,
                cursor: stockLocked ? 'pointer' : 'not-allowed',
                textAlign: 'center', fontFamily: SERIF, fontWeight: 500,
              }}>
              <span style={{ fontSize: 18, marginRight: 6 }}>{q.icon}</span>{q.label}
            </button>
          ))}
        </div>
      </div>}

      {/* ── AI 对话区（未选股隐藏）── */}
      {stockLocked && <div style={{
        background: PAPER, border: `1px solid ${LINE}`, borderRadius: 12,
        padding: '12px 14px', marginBottom: 14,
      }}>
        {/* 顶部标题 + 重置按钮 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <div style={{ fontSize: 12, color: INK_F, letterSpacing: 0.3 }}>
            {messages.length > 0
              ? <>💬 AI 对话（{Math.floor(messages.length / 2) + (messages.length % 2 || 0)} 轮）</>
              : <>直接说，比如：<b>茅台还能拿吗？</b></>}
          </div>
          {messages.length > 0 && (
            <button onClick={resetChat}
              style={{
                background: 'none', border: 'none', color: INK_F,
                fontSize: 12, cursor: 'pointer', padding: '4px 6px',
              }}>
              ↻ 重置对话
            </button>
          )}
        </div>

        {/* 对话历史气泡区 */}
        {messages.length > 0 && (
          <>
            {/* V2：顶部一次性 AI 免责声明（不再每条 chat 消息后重复）*/}
            <div style={{
              padding: '6px 10px', marginBottom: 8, borderRadius: 8,
              background: PAPER2, fontSize: 11, color: INK_F, lineHeight: 1.6,
              textAlign: 'center',
            }}>
              💡 AI 生成内容仅供参考，不构成投资建议
            </div>
            <div style={{
              maxHeight: 500, overflowY: 'auto', marginBottom: 10, paddingRight: 4,
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              {messages.map((m, i) => m.role === 'user' ? (
                <div key={i} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <div style={{
                    maxWidth: '80%', padding: '8px 12px', borderRadius: 12,
                    background: THEME, color: '#fff', fontSize: 13, lineHeight: 1.6,
                    wordBreak: 'break-word',
                  }}>
                    {m.content}
                  </div>
                </div>
              ) : (
                // ── assistant 消息 · 按 intent 分岔样式 ──
                // chat 意图 → 浅铜底加宽长文气泡，无 CTA
                // 5 智能体意图 → 宣纸底短气泡 + 主推/次选按钮
                // fallback → 浅黄底警告气泡
                (() => {
                  const isChat = m.intent === 'chat'
                  const canSave = !m.fallback && m.content.length > 50  // 太短的回答不必截图
                  return (
                    <div key={i} style={{
                      display: 'flex', justifyContent: 'flex-start',
                    }}>
                      <div style={{ maxWidth: isChat ? '95%' : '90%' }}>
                        {/* 气泡节点（截图目标）*/}
                        <div ref={el => { bubbleRefs.current[i] = el }} style={{
                          padding: isChat ? '14px 16px' : '10px 12px',
                          borderRadius: 12,
                          background: m.fallback ? '#FEF7EC' : isChat ? '#FBF1E4' : PAPER2,
                          color: INK,
                          fontSize: 13,
                          lineHeight: isChat ? 1.85 : 1.75,
                          wordBreak: 'break-word',
                          whiteSpace: 'pre-wrap',
                          border: m.fallback ? '1px solid #F5D38B' :
                                  isChat ? `1px solid ${COPPER2}` : 'none',
                        }}>
                          {isChat && (
                            <div style={{ marginBottom: 8, paddingBottom: 8, borderBottom: `1px dashed ${COPPER2}` }}>
                              <span style={{
                                display: 'inline-block', marginRight: 6, padding: '2px 8px',
                                background: THEME, color: '#fff', fontSize: 11,
                                borderRadius: 4, fontWeight: 600,
                              }}>💬 猎鹿人 AI 深度回答</span>
                              {selectedName && (
                                <span style={{ fontSize: 11, color: INK_F }}>
                                  · {selectedName} {selectedCode}
                                </span>
                              )}
                            </div>
                          )}
                          {m.fallback && <span style={{ marginRight: 4 }}>⚠️</span>}
                          {m.content}
                        </div>

                        {/* V3：复制 + 保存图片按钮（chat 或长内容才显示）*/}
                        {canSave && (
                          <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                            <button onClick={() => copyMessage(m.content, i)}
                              style={{
                                padding: '4px 10px', background: 'transparent',
                                color: copiedIdx === i ? DN : INK_F, border: `1px solid ${LINE}`,
                                borderRadius: 6, fontSize: 11, cursor: 'pointer',
                              }}>
                              {copiedIdx === i ? '✓ 已复制' : '📋 复制'}
                            </button>
                            <button onClick={() => saveMessageAsImage(i, selectedName)}
                              disabled={savingImgIdx === i}
                              style={{
                                padding: '4px 10px', background: 'transparent',
                                color: INK_F, border: `1px solid ${LINE}`,
                                borderRadius: 6, fontSize: 11,
                                cursor: savingImgIdx === i ? 'wait' : 'pointer',
                              }}>
                              {savingImgIdx === i ? '⏳ 生成中…' : '🖼 保存图片'}
                            </button>
                          </div>
                        )}

                        {/* 主推 CTA（chat 无） */}
                        {m.suggested_action && !isChat && (
                          <button onClick={() => onSwitchMode(m.suggested_action!.mode)}
                            style={{
                              marginTop: 8, padding: '8px 16px', background: THEME, color: '#fff',
                              border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600,
                              cursor: 'pointer', display: 'inline-block',
                            }}>
                            {m.suggested_action.label} →
                          </button>
                        )}
                        {/* 次选按钮（chat 无） */}
                        {m.additional_actions && m.additional_actions.length > 0 && !isChat && (
                          <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {m.additional_actions.map((a, ai) => (
                              <button key={ai} onClick={() => onSwitchMode(a.mode)}
                                style={{
                                  padding: '6px 12px', background: 'transparent',
                                  color: THEME, border: `1px solid ${THEME}`, borderRadius: 8,
                                  fontSize: 12, cursor: 'pointer',
                                }}>
                                {a.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })()
              ))}
              {chatBusy && (
                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <div style={{
                    padding: '8px 12px', borderRadius: 12, background: PAPER2,
                    color: INK_F, fontSize: 13,
                  }}>
                    <span style={{ display: 'inline-block' }}>💭 助手思考中…</span>
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        {/* V3：多行输入框（textarea）· Enter 发送 · Shift+Enter 换行 · auto-resize */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <textarea
            ref={textareaRef}
            value={chatInput}
            onChange={e => setChatInput(e.target.value)}
            onKeyDown={e => {
              // Enter 发送，Shift+Enter 换行（对齐 ChatGPT/Claude 主流约定）
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                if (!chatBusy && chatInput.trim()) routeQuery()
              }
            }}
            placeholder={messages.length > 0
              ? '追问… （Enter 发送 · Shift+Enter 换行）'
              : '输入你的问题… （Enter 发送 · Shift+Enter 换行）'}
            disabled={chatBusy}
            rows={2}
            style={{
              flex: 1, padding: '10px 12px', fontSize: 14, borderRadius: 8,
              border: `1.5px solid ${LINE}`, background: '#fff', color: INK, outline: 'none',
              resize: 'none', fontFamily: 'inherit', lineHeight: 1.6,
              minHeight: 48, maxHeight: 160, overflowY: 'auto',
            }} />
          <button onClick={routeQuery} disabled={!chatInput.trim() || !stockLocked || chatBusy}
            style={{
              padding: '0 14px', borderRadius: 8, minHeight: 48,
              background: (!chatInput.trim() || !stockLocked || chatBusy) ? '#C9B9A5' : THEME,
              color: '#fff', border: 'none', fontSize: 13, fontWeight: 600,
              cursor: (!chatInput.trim() || !stockLocked || chatBusy) ? 'not-allowed' : 'pointer',
              whiteSpace: 'nowrap',
            }}>
            {chatBusy ? '…' : '→ 去问'}
          </button>
        </div>
      </div>}

      {/* ── 全部专家 grid ── */}
      <div>
        <div style={{ fontSize: 12, color: INK_F, marginBottom: 8, letterSpacing: 0.3, textAlign: 'center' }}>
          ── 全部专家 ──
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 6 }}>
          {AGENT_GRID.map(a => (
            <button key={a.mode}
              onClick={() => onSwitchMode(a.mode)}
              style={{
                padding: '10px 4px', background: PAPER, border: `1px solid ${LINE}`,
                borderRadius: 10, cursor: 'pointer', textAlign: 'center',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
              }}>
              <span style={{ fontSize: 20 }}>{a.icon}</span>
              <span style={{ fontSize: 11, color: INK, fontFamily: SERIF, fontWeight: 600 }}>{a.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}


// ── 深度研究 Tab ──────────────────────────────────────────────────────────
interface DeepSignal { source: string; title: string; content?: string; url?: string; date?: string | null }
interface DeepReport {
  symbol: string; name: string; sector?: string; chain?: string
  price?: number | null; change_pct?: number | null
  week_change_pct?: number | null
  conclusion: string
  recent_signals: DeepSignal[]
  // 分类信号（后端 _scout_to_report 返回，AI 算力预制报告也有）
  signals?: {
    cninfo?: DeepSignal[]        // 📌 公告
    rd_expansion?: DeepSignal[]  // 🧪 研发扩张
    northbound?: DeepSignal[]    // 💰 北向资金
    ai_search?: DeepSignal[]     // 🔎 AI 搜索
  }
  truth_alert?: string | null
  report_md?: string
  summary?: string
  generated_at?: string
  scout_mode?: boolean
}

// 长文本可折叠展示：超 maxLines 行时展示"展开全文"
function CollapsibleText({ text, maxLines = 6, colorMuted, colorLink }: {
  text: string; maxLines?: number; colorMuted: string; colorLink: string
}) {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n').filter(l => l.trim())
  const shouldFold = lines.length > maxLines
  const visible = open || !shouldFold ? lines : lines.slice(0, maxLines)
  return (
    <div>
      <p style={{ margin: 0, fontSize: 13, color: colorMuted, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
        {visible.join('\n')}
      </p>
      {shouldFold && (
        <button onClick={() => setOpen(!open)} style={{
          marginTop: 6, padding: 0, background: 'none', border: 'none',
          color: colorLink, fontSize: 12, fontWeight: 600, cursor: 'pointer',
        }}>
          {open ? '▲ 收起' : '▼ 展开全文'}
        </button>
      )}
    </div>
  )
}

// 信号分类卡：每类默认展示前 3 条，超出可展开全部
function SignalCategory({ icon, title, items, colorMuted, colorLink, colorInk, colorInkFaint, borderColor, bgPaper2 }: {
  icon: string; title: string; items: DeepSignal[]
  colorMuted: string; colorLink: string; colorInk: string; colorInkFaint: string
  borderColor: string; bgPaper2: string
}) {
  const [open, setOpen] = useState(false)
  if (!items || items.length === 0) return null
  const visible = open ? items : items.slice(0, 3)
  return (
    <div style={{
      background: bgPaper2, border: `1px solid ${borderColor}`, borderRadius: 10,
      padding: '10px 12px', marginBottom: 8,
    }}>
      <div style={{ fontSize: 12, color: colorLink, fontWeight: 700, marginBottom: 8, letterSpacing: 0.3 }}>
        {icon} {title}（{items.length}）
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {visible.map((sig, i) => (
          <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', lineHeight: 1.6 }}>
            <span style={{ color: colorLink, flexShrink: 0, marginTop: 2 }}>·</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              {sig.url ? (
                <a href={sig.url} target="_blank" rel="noopener noreferrer"
                  style={{ fontSize: 13, color: colorInk, textDecoration: 'none', wordBreak: 'break-word' }}>
                  {sig.title.replace(/^\[.+?\]\s*/, '')}
                </a>
              ) : (
                <span style={{ fontSize: 13, color: colorInk, wordBreak: 'break-word' }}>
                  {sig.title.replace(/^\[.+?\]\s*/, '')}
                </span>
              )}
              {sig.date && (
                <span style={{ fontSize: 11, color: colorInkFaint, marginLeft: 8 }}>{sig.date.slice(0, 10)}</span>
              )}
              {sig.content && (
                <div style={{ fontSize: 12, color: colorMuted, marginTop: 3, lineHeight: 1.65 }}>
                  {sig.content.length > 120 ? sig.content.slice(0, 120) + '…' : sig.content}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      {items.length > 3 && (
        <button onClick={() => setOpen(!open)} style={{
          marginTop: 8, padding: '4px 0', background: 'none', border: 'none',
          color: colorLink, fontSize: 12, fontWeight: 600, cursor: 'pointer',
        }}>
          {open ? '▲ 收起' : `▼ 展开全部 ${items.length} 条`}
        </button>
      )}
    </div>
  )
}

function ResearchDeepTab({ token, stocks, shared, quotes }: {
  token: string; stocks: Stock[]; shared: ResearchStockCtx;
  quotes: Record<string, Quote>
}) {
  const { input, setInput, selectedCode, setSelectedCode, selectedName, setSelectedName } = shared
  const [showPicker, setShowPicker] = useState(false)
  const aStockCount = stocks.filter(s => s.market === 'A').length
  const [hits, setHits] = useState<{ code: string; name: string }[]>([])
  const [searched, setSearched] = useState(false)
  const [searching, setSearching] = useState(false)
  const [marketWarn, setMarketWarn] = useState('')
  const [searchError, setSearchError] = useState('')
  const [loading, setLoading] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [report, setReport] = useState<DeepReport | null>(null)
  const [kronos, setKronos] = useState<KProResult | null>(null)
  const [error, setError] = useState('')
  const [mdExpanded, setMdExpanded] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addDone, setAddDone] = useState(false)
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isComposingRef = useRef(false)

  const hdr = (): HeadersInit => token ? { Authorization: `Bearer ${token}` } : {}

  const detectMarket = (v: string): string => {
    const raw = v.trim()
    if (!raw) return ''
    // 港股：5位纯数字（如 00700）
    if (/^\d{5}$/.test(raw)) return '当前仅支持A股，港股暂不支持'
    // 美股：纯英文字母 2-5 位（如 AAPL / TSLA）
    if (/^[A-Za-z]{1,5}$/.test(raw)) return '当前仅支持A股，美股暂不支持'
    return ''
  }

  const doSearch = async () => {
    const raw = input.trim()
    if (!raw) return
    // 清除旧结果
    setReport(null); setKronos(null); setError(''); setAddDone(false); setSearchError('')
    setSelectedCode(''); setSelectedName('')
    // 市场判定
    const warn = detectMarket(raw)
    setMarketWarn(warn)
    if (warn) { setHits([]); setSearched(true); return }
    setSearching(true); setSearched(false)
    try {
      const items = await searchStockWithCache(raw, {
        headers: hdr(),
        onSlowNetwork: () => setSearchError('⏳ 网络较慢, 正在重试...'),
      })
      setHits(items)
      setSearchError('')
      // 精确命中(唯一结果或纯6位数字直接匹配)自动选中
      if (items.length === 1) {
        setSelectedCode(items[0].code); setSelectedName(items[0].name)
      } else if (/^\d{6}$/.test(raw)) {
        const hit = items.find(x => x.code === raw)
        if (hit) { setSelectedCode(hit.code); setSelectedName(hit.name) }
      }
    } catch (e: unknown) {
      setHits([])
      const isTimeout = e instanceof Error && (e.message === 'timeout' || e.name === 'AbortError')
      setSearchError(isTimeout ? '查询超时(25秒 + 1 次重试), 请检查网络后再试' : '查询失败,请稍后重试')
    }
    setSearched(true); setSearching(false)
  }

  const confirmStock = (code: string, name: string) => {
    setSelectedCode(code); setSelectedName(name)
  }

  const resetSelection = () => {
    setSelectedCode(''); setSelectedName(''); setInput('')
    setHits([]); setSearched(false); setMarketWarn(''); setSearchError('')
    setReport(null); setKronos(null); setError(''); setAddDone(false)
  }

  const analyse = async () => {
    const code = selectedCode
    if (!code) return
    setLoading(true); setError(''); setReport(null); setKronos(null); setElapsed(0); setMdExpanded(false); setAddDone(false)
    elapsedRef.current = setInterval(() => setElapsed(s => s + 1), 1000)
    try {
      const nameParam = selectedName ? `?name=${encodeURIComponent(selectedName)}` : ''
      const [repRes, kroRes] = await Promise.allSettled([
        fetch(`/api/truesource/report/${code}${nameParam}`, { headers: hdr() }),
        fetch(`/api/kpred/${code}/pro?days=5`, { headers: hdr() }),
      ])
      if (repRes.status === 'fulfilled' && repRes.value.ok) {
        setReport(await repRes.value.json())
      } else {
        setError('研报获取失败，请稍后重试')
      }
      if (kroRes.status === 'fulfilled' && kroRes.value.ok) {
        setKronos(await kroRes.value.json())
      }
    } catch (e: unknown) { setError(e instanceof Error ? e.message : '请求失败') }
    if (elapsedRef.current) clearInterval(elapsedRef.current)
    setLoading(false)
  }

  const addToWatchlist = async () => {
    const code = selectedCode
    if (!code || !token) return
    setAdding(true)
    try {
      const r = await fetch('/api/watchlist', { method: 'POST', headers: { ...hdr(), 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) })
      if (r.ok) setAddDone(true)
    } catch { /* ignore */ }
    setAdding(false)
  }

  const canAnalyse = !loading && !!selectedCode
  const pro = kronos?.pro
  const lastPredClose = kronos?.predictions?.[kronos.predictions.length - 1]?.close
  const retPct = (lastPredClose != null && kronos?.last_close) ? (lastPredClose - kronos.last_close) / kronos.last_close * 100 : null
  const alreadyInList = stocks.some(s => s.code === selectedCode)

  // 简单解析 report_md 按 ## 拆分章节
  const mdSections = (report?.report_md || '').split(/\n## /).filter(Boolean).map(sec => {
    const lines = sec.split('\n')
    return { title: lines[0].replace(/^#+ /, ''), body: lines.slice(1).join('\n').trim() }
  })

  return (
    <div style={{ padding: '10px 12px 24px' }}>
      {/* 产品介绍 */}
      <IntroCard
        storageKey="hunter_intro_research"
        icon="📊"
        title="深度研究 · AI 综合研判"
        description="产业链归属 + 一句话结论 + 一手情报分类 + Kronos（清华金融大模型）预测，3分钟建立框架"
        badges={['一句话结论', '产业链归属', '综合视图', '全A股']}
        gradient="linear-gradient(135deg, #f5eee0 0%, #ecdec6 100%)"
        border="#d4b98a"
        accent="#a86828"
        textColor="#5a4525"
      />

      {/* 搜索栏 + 查询按钮 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1, display: 'flex', gap: 8, background: PAPER, borderRadius: 12, padding: '10px 12px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', alignItems: 'center', border: `1px solid ${LINE}` }}>
            <span style={{ fontSize: 16 }}>🔍</span>
            <input
              value={input}
              onChange={e => { setInput(e.target.value); setSearched(false); setMarketWarn(''); setSearchError('') }}
              onCompositionStart={() => { isComposingRef.current = true }}
              onCompositionEnd={() => { isComposingRef.current = false }}
              onKeyDown={e => e.key === 'Enter' && doSearch()}
              placeholder="输入股票名称或代码，点击查询"
              style={{ flex: 1, border: 'none', outline: 'none', fontSize: 15, background: 'transparent', color: INK, fontFamily: SERIF, minWidth: 0 }}
            />
            {input && <button onClick={resetSelection} style={{ background: 'none', border: 'none', color: '#aaa', fontSize: 16, cursor: 'pointer', padding: 0 }}>✕</button>}
          </div>
          <button onClick={doSearch} disabled={!input.trim() || searching}
            style={{ flexShrink: 0, padding: '0 16px', background: input.trim() ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 12, fontSize: 14, fontWeight: 600, cursor: input.trim() ? 'pointer' : 'not-allowed' }}>
            {searching ? '查询中' : '查询'}
          </button>
        </div>

        {/* 从自选选择入口 */}
        {!selectedCode && (
          <WatchlistPickerButton
            count={aStockCount}
            onOpen={() => setShowPicker(true)}
            onEmptyClick={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}
        {showPicker && (
          <WatchlistPicker
            stocks={stocks} quotes={quotes}
            marketFilter="A"
            currentCode={selectedCode}
            onPick={confirmStock}
            onClose={() => setShowPicker(false)}
            onAddMore={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
          />
        )}

        {/* 港股/美股提示 */}
        {marketWarn && (
          <div style={{ padding: '10px 12px', background: '#fff3e6', border: '1px solid #f5c691', borderRadius: 10, fontSize: 13, color: '#a05a1a', marginBottom: 8 }}>
            ⚠️ {marketWarn}，敬请期待
          </div>
        )}

        {/* 搜索错误（超时/失败） */}
        {searchError && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ flex: 1 }}>{searchError}</span>
            <button onClick={doSearch} style={{ background: '#a04040', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', flexShrink: 0 }}>重试</button>
          </div>
        )}

        {/* 查询结果列表（未选中时显示） */}
        {!selectedCode && searched && !marketWarn && !searchError && hits.length > 0 && (
          <div style={{ background: PAPER, borderRadius: 10, border: `1px solid ${LINE}`, overflow: 'hidden' }}>
            <div style={{ padding: '8px 12px', fontSize: 12, color: INK_F, background: PAPER2 }}>请选择要研究的股票：</div>
            {hits.map(hit => (
              <div key={hit.code} onClick={() => confirmStock(hit.code, hit.name)}
                style={{ padding: '12px 14px', cursor: 'pointer', borderTop: `1px solid ${PAPER2}`, display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 14, color: INK, fontFamily: SERIF, fontWeight: 600 }}>{hit.name}</span>
                <span style={{ fontSize: 12, color: INK_F }}>{hit.code}</span>
                <span style={{ marginLeft: 'auto', fontSize: 12, color: THEME }}>选择 ›</span>
              </div>
            ))}
          </div>
        )}

        {/* 未找到 */}
        {!selectedCode && searched && !marketWarn && !searchError && hits.length === 0 && (
          <div style={{ padding: '10px 12px', background: '#fdecea', border: '1px solid #f5b7b1', borderRadius: 10, fontSize: 13, color: '#a04040' }}>
            未找到相关A股，请检查股票名称或代码
          </div>
        )}

        {/* 已选中卡片 */}
        {selectedCode && (
          <div style={{ padding: '12px 14px', background: '#fff', border: `2px solid ${THEME}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 13, color: THEME }}>已选择：</span>
            <span style={{ fontSize: 15, color: INK, fontWeight: 700, fontFamily: SERIF }}>{selectedName}</span>
            <span style={{ fontSize: 13, color: INK_F }}>{selectedCode}</span>
            <button onClick={resetSelection}
              style={{ marginLeft: 'auto', background: 'none', border: `1px solid ${LINE}`, borderRadius: 8, padding: '4px 10px', fontSize: 12, color: INK_S, cursor: 'pointer' }}>
              更换
            </button>
          </div>
        )}
      </div>

      <button onClick={analyse} disabled={!canAnalyse}
        style={{ width: '100%', padding: '12px 0', background: canAnalyse ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: canAnalyse ? 'pointer' : 'not-allowed', marginBottom: 14 }}>
        {loading
          ? (elapsed > 8 ? `实时采集中… ${elapsed}s（首次约 30-60 秒）` : `生成研报中… ${elapsed}s`)
          : selectedCode ? '生成深度研报' : '请先查询并选择股票'}
      </button>

      {/* 实时采集加载提示（超过 8s） */}
      {loading && elapsed > 8 && (
        <div style={{ padding: '10px 12px', background: '#fff8e6', border: '1px solid #f5d38b', borderRadius: 10, fontSize: 12, color: '#8a5a1a', marginBottom: 10, lineHeight: 1.6 }}>
          正在从公告、研发、北向持仓和 AI 搜索等多源实时采集，请耐心等待…
        </div>
      )}

      {error && <div style={{ color: '#e74c3c', fontSize: 13, marginBottom: 10 }}>{error}</div>}

      {/* 投资决策三步走（空白态展示） */}
      {!loading && !report && !error && (
        <div style={{ marginBottom: 14, padding: '14px', background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, fontSize: 12, color: INK_S, lineHeight: 1.75 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: THEME, marginBottom: 12, fontFamily: SERIF }}>
            📱 投资决策三步走
          </div>

          <div style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, color: '#a86828', marginBottom: 3 }}>【深度研究】（AI 分析师）</div>
            <div><b style={{ color: INK }}>核心：</b>3 分钟建立认知。</div>
            <div><b style={{ color: INK }}>功能：</b>将海量信息浓缩成一句话结论和关键信号，附 Kronos（清华大学金融时序大模型）量化视角。</div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, color: '#2e5f7a', marginBottom: 3 }}>【一手情报】（机构调研）</div>
            <div><b style={{ color: INK }}>核心：</b>一手情报，直达真相。</div>
            <div><b style={{ color: INK }}>功能：</b>仅提供公告、研发、资金流向等原始事实，每条都可点开原文。</div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 700, color: '#8b5a2b', marginBottom: 3 }}>【量化择时】（量化专家）</div>
            <div><b style={{ color: INK }}>核心：</b>找准买卖时机。</div>
            <div><b style={{ color: INK }}>功能：</b>基于 Kronos（清华大学金融时序大模型）+ 5 个技术因子，预测未来 5/10/20 日走势，提供量化择时验证。</div>
          </div>

          <div style={{ padding: '10px 12px', background: BG, borderRadius: 8 }}>
            <div style={{ fontWeight: 700, color: INK, marginBottom: 6 }}>💡 专家组合建议</div>
            <div style={{ fontSize: 11, color: INK_F, lineHeight: 1.9 }}>
              · 初次关注股票时：先看 <b>【深度研究】</b> 快速建立框架。<br/>
              · 产生疑问或想看动态时：查看 <b>【一手情报】</b> 核实原始事实。<br/>
              · 准备下单决策前：参考 <b>【量化择时】</b> 看短期信号是否配合。
            </div>
          </div>
        </div>
      )}

      {report && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {/* 股票标题 + 行情 */}
          <div style={{ background: HEADER_BG, borderRadius: 14, padding: '16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 18, fontWeight: 700, color: PAPER, fontFamily: SERIF }}>{report.name}</div>
              <div style={{ fontSize: 12, color: COPPER2, marginTop: 3 }}>
                {report.symbol} {report.sector && `· ${report.sector}`} {report.chain && `· ${report.chain}`}
              </div>
            </div>
            {report.price != null && (
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: PAPER }}>¥{report.price.toFixed(2)}</div>
                {report.change_pct != null && (
                  <div style={{ fontSize: 13, color: report.change_pct >= 0 ? '#f87171' : '#4ade80' }}>
                    {report.change_pct >= 0 ? '+' : ''}{report.change_pct.toFixed(2)}%
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ═══════════════════════════════════════════════════════ */}
          {/* 🎯 TL;DR 简要汇总 · 3 秒扫完                              */}
          {/* ═══════════════════════════════════════════════════════ */}
          <div style={{
            background: '#FBF1E4', border: `1px solid ${COPPER2}`, borderRadius: 14,
            padding: '16px', borderLeft: `4px solid ${THEME}`,
          }}>
            <div style={{ fontSize: 11, color: THEME, fontWeight: 700, marginBottom: 12, letterSpacing: 0.5 }}>
              🎯 简要汇总 · 3 秒扫完
            </div>

            {/* Kronos 评级横幅（如有）*/}
            {pro && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px',
                background: PAPER, borderRadius: 10, border: `1px solid ${LINE}`, marginBottom: 12,
              }}>
                <div style={{
                  background: RATING_BG[pro.rating] ?? PAPER2,
                  border: `1px solid ${(RATING_COLOR[pro.rating] ?? INK_F)}44`,
                  borderRadius: 8, padding: '8px 14px', flexShrink: 0,
                }}>
                  <div style={{ fontSize: 14, fontWeight: 700, color: RATING_COLOR[pro.rating] ?? INK_F, fontFamily: SERIF }}>{pro.rating}</div>
                </div>
                <div style={{ flex: 1 }}>
                  {retPct != null && (
                    <div style={{ fontSize: 15, fontWeight: 700, color: retPct >= 0 ? UP : DN }}>
                      明日预测 {retPct >= 0 ? '+' : ''}{retPct.toFixed(2)}%
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 3 }}>
                    <span style={{ fontSize: 11, color: INK_F }}>置信度 <b style={{ color: INK_S }}>{pro.confidence}</b></span>
                    <span style={{ fontSize: 11, color: INK_F }}>综合分 <b style={{ color: INK_S }}>{(pro.composite_score * 100).toFixed(0)}</b></span>
                    <span style={{ fontSize: 11, color: INK_F }}>分歧 <b style={{ color: INK_S }}>{pro.conflict_level}</b></span>
                  </div>
                </div>
              </div>
            )}

            {/* ⭐ 一句话结论（宋体高亮）*/}
            {report.conclusion && (
              <div style={{ marginBottom: report.truth_alert ? 12 : 0 }}>
                <div style={{ fontSize: 11, color: INK_F, marginBottom: 5 }}>
                  {report.scout_mode ? '📌 采集摘要' : '⭐ 一句话结论'}
                </div>
                <div style={{ fontSize: 15, color: INK, lineHeight: 1.75, fontFamily: SERIF, fontWeight: 500 }}>
                  {report.conclusion}
                </div>
              </div>
            )}

            {/* ⚠️ 真相提示（合并进 TL;DR）*/}
            {report.truth_alert && (
              <div style={{
                background: '#fff8f0', borderRadius: 8, border: '1px solid #f59e0b50',
                padding: '10px 12px', marginTop: 8,
              }}>
                <div style={{ fontSize: 11, color: '#b45309', fontWeight: 700, marginBottom: 5 }}>⚠️ 真相提示</div>
                <div style={{ fontSize: 12, color: '#7c2d12', lineHeight: 1.7 }}>{report.truth_alert}</div>
              </div>
            )}

            {/* 关键指标 · 3 列网格 */}
            {(report.change_pct != null || report.week_change_pct != null || pro) && (
              <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6,
                marginTop: 12, paddingTop: 12, borderTop: `1px dashed ${LINE}`,
              }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 10, color: INK_F, marginBottom: 3 }}>今日</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: SERIF,
                    color: report.change_pct == null ? INK_F : report.change_pct >= 0 ? UP : DN }}>
                    {report.change_pct == null ? '--' : `${report.change_pct >= 0 ? '+' : ''}${report.change_pct.toFixed(2)}%`}
                  </div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 10, color: INK_F, marginBottom: 3 }}>7 日</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: SERIF,
                    color: report.week_change_pct == null ? INK_F : report.week_change_pct >= 0 ? UP : DN }}>
                    {report.week_change_pct == null ? '--' : `${report.week_change_pct >= 0 ? '+' : ''}${report.week_change_pct.toFixed(2)}%`}
                  </div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 10, color: INK_F, marginBottom: 3 }}>综合分</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: SERIF, color: pro ? THEME : INK_F }}>
                    {pro ? (pro.composite_score * 100).toFixed(0) : '--'}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ═══════════════════════════════════════════════════════ */}
          {/* 📊 关键信号 · 4 类分组                                    */}
          {/* ═══════════════════════════════════════════════════════ */}
          {report.signals && (
            (report.signals.cninfo?.length ||
             report.signals.rd_expansion?.length ||
             report.signals.northbound?.length ||
             report.signals.ai_search?.length) ? (
              <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, padding: '14px' }}>
                <div style={{ fontSize: 11, color: THEME, fontWeight: 700, marginBottom: 10, letterSpacing: 0.5 }}>
                  📊 关键信号 · 4 类分组
                </div>
                <SignalCategory icon="📌" title="公告" items={report.signals.cninfo || []}
                  colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                  borderColor={LINE} bgPaper2={PAPER2} />
                <SignalCategory icon="🧪" title="研发扩张" items={report.signals.rd_expansion || []}
                  colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                  borderColor={LINE} bgPaper2={PAPER2} />
                <SignalCategory icon="💰" title="北向资金" items={report.signals.northbound || []}
                  colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                  borderColor={LINE} bgPaper2={PAPER2} />
                <SignalCategory icon="🔎" title="AI 搜索" items={report.signals.ai_search || []}
                  colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                  borderColor={LINE} bgPaper2={PAPER2} />
              </div>
            ) : (
              // 后端无 signals 分类字段时，兜底展示 recent_signals（不再限 6 条）
              report.recent_signals && report.recent_signals.length > 0 && (
                <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, padding: '16px' }}>
                  <div style={{ fontSize: 11, color: THEME, fontWeight: 700, marginBottom: 10, letterSpacing: 0.5 }}>
                    📡 一手情报（{report.recent_signals.length} 条）
                  </div>
                  <SignalCategory icon="·" title="最新" items={report.recent_signals}
                    colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                    borderColor={LINE} bgPaper2={PAPER2} />
                </div>
              )
            )
          )}
          {/* 兜底：report 里没 signals 键但有 recent_signals */}
          {!report.signals && report.recent_signals && report.recent_signals.length > 0 && (
            <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, padding: '16px' }}>
              <div style={{ fontSize: 11, color: THEME, fontWeight: 700, marginBottom: 10, letterSpacing: 0.5 }}>
                📡 一手情报（{report.recent_signals.length} 条）
              </div>
              <SignalCategory icon="·" title="最新" items={report.recent_signals}
                colorMuted={INK_S} colorLink={THEME} colorInk={INK} colorInkFaint={INK_F}
                borderColor={LINE} bgPaper2={PAPER2} />
            </div>
          )}

          {/* ═══════════════════════════════════════════════════════ */}
          {/* 📄 深度研报 · 完整章节 · 每节 >6 行折叠                    */}
          {/* ═══════════════════════════════════════════════════════ */}
          {mdSections.length > 0 && (
            <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, overflow: 'hidden' }}>
              <div style={{ padding: '16px' }}>
                <div style={{ fontSize: 11, color: THEME, fontWeight: 700, marginBottom: 14, letterSpacing: 0.5 }}>
                  📄 深度研报 · 共 {mdSections.length} 节
                </div>
                {(mdExpanded ? mdSections : mdSections.slice(0, 3)).map((sec, i) => (
                  <div key={i} style={{ marginBottom: 18, paddingBottom: i < mdSections.length - 1 ? 14 : 0,
                                        borderBottom: i < mdSections.length - 1 ? `1px dashed ${LINE}` : 'none' }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: INK, fontFamily: SERIF, marginBottom: 8 }}>
                      {sec.title}
                    </div>
                    <CollapsibleText text={sec.body} maxLines={6} colorMuted={INK_S} colorLink={THEME} />
                  </div>
                ))}
              </div>
              {mdSections.length > 3 && (
                <button onClick={() => setMdExpanded(e => !e)}
                  style={{ width: '100%', padding: '12px', background: PAPER2, border: 'none',
                           borderTop: `1px solid ${LINE}`, fontSize: 13, color: THEME, cursor: 'pointer',
                           fontWeight: 600 }}>
                  {mdExpanded ? '▲ 收起研报' : `▼ 展开完整研报（共 ${mdSections.length} 节）`}
                </button>
              )}
            </div>
          )}

          {/* 加入自选股 */}
          <div style={{ marginTop: 4 }}>
            {alreadyInList || addDone ? (
              <div style={{ textAlign: 'center', fontSize: 14, color: DN, padding: '12px', background: '#f0fdf4', borderRadius: 12 }}>✅ 已加入自选股跟踪</div>
            ) : (
              <button onClick={addToWatchlist} disabled={adding}
                style={{ width: '100%', padding: '13px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: adding ? 'not-allowed' : 'pointer', opacity: adding ? 0.7 : 1 }}>
                {adding ? '添加中…' : '+ 加入自选股跟踪'}
              </button>
            )}
            {report.generated_at && (
              <div style={{ textAlign: 'center', fontSize: 11, color: INK_F, marginTop: 8 }}>
                研报生成于 {report.generated_at.slice(0, 16).replace('T', ' ')} UTC
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 持仓研判 Tab（原持仓管家） ──────────────────────────────────────────────
function HoldJudgeTab({ shared, stocks, quotes }: {
  shared: ResearchStockCtx;
  stocks: Stock[]; quotes: Record<string, Quote>
}) {
  const { selectedCode, selectedName, setSelectedCode, setSelectedName } = shared
  const [showPicker, setShowPicker] = useState(false)
  const aStockCount = stocks.filter(s => s.market === 'A').length
  const goJudge = () => {
    const q = selectedCode
      ? `?symbol=${encodeURIComponent(selectedCode)}${selectedName ? `&name=${encodeURIComponent(selectedName)}` : ''}`
      : ''
    window.location.href = '/online-analysis' + q
  }
  return (
    <div style={{ padding: '14px 12px 24px' }}>
      {/* 品牌介绍卡 */}
      <div style={{ marginBottom: 14, padding: '16px', background: 'linear-gradient(135deg, #3a3020 0%, #5a4835 100%)', borderRadius: 14, color: '#fff', boxShadow: '0 3px 12px rgba(50,35,10,0.15)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <span style={{ fontSize: 26 }}>🛡</span>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, fontFamily: SERIF, display: 'flex', alignItems: 'center', gap: 6 }}>
              持仓研判
              <span style={{ background: COPPER2, color: '#fff', fontSize: 9, padding: '1px 6px', borderRadius: 4, fontWeight: 700 }}>NEW</span>
            </div>
            <div style={{ fontSize: 11, opacity: 0.85, marginTop: 2 }}>原「AI 持仓管家」· 多空辩论 · 综合裁判</div>
          </div>
        </div>
        <div style={{ fontSize: 12, opacity: 0.9, lineHeight: 1.7 }}>
          从「已持有」视角对单只股票做深度研判：录入持仓逻辑和止损条件，AI 会派出多头（Bull）与空头（Bear）3 轮辩论，最后由综合裁判和风险裁判给出<b> BUY / HOLD / SELL </b>决策建议。
        </div>
      </div>

      {/* 当前选中股票 + 进入按钮 */}
      {selectedCode ? (
        <div style={{ background: PAPER, border: `2px solid ${THEME}`, borderRadius: 12, padding: '14px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 13, color: THEME }}>已选择：</span>
          <span style={{ fontSize: 15, color: INK, fontWeight: 700, fontFamily: SERIF }}>{selectedName || selectedCode}</span>
          <span style={{ fontSize: 12, color: INK_F }}>{selectedCode}</span>
          <button
            onClick={() => setShowPicker(true)}
            style={{ marginLeft: 'auto', background: 'none', border: `1px solid ${LINE}`, borderRadius: 8, padding: '4px 10px', fontSize: 12, color: INK_S, cursor: 'pointer' }}
          >更换</button>
        </div>
      ) : (
        <div style={{ background: '#fff8e6', border: '1px solid #f5d38b', borderRadius: 10, padding: '12px', marginBottom: 12, fontSize: 12, color: '#8a5a1a', lineHeight: 1.6 }}>
          ℹ 你尚未选择股票。可从下方「从自选选择」快速选中，或先去「深度研究」/「一手情报」查询后回来做持仓研判。
        </div>
      )}

      {/* 从自选选择入口(HoldJudge 无搜索,必须提供入口) */}
      <div style={{ marginBottom: 12 }}>
        <WatchlistPickerButton
          count={aStockCount}
          onOpen={() => setShowPicker(true)}
          onEmptyClick={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
        />
      </div>
      {showPicker && (
        <WatchlistPicker
          stocks={stocks} quotes={quotes}
          marketFilter="A"
          currentCode={selectedCode}
          onPick={(code, name) => { setSelectedCode(code); setSelectedName(name) }}
          onClose={() => setShowPicker(false)}
          onAddMore={() => { window.location.assign('/wx/home?nav=watchlist:list') }}
        />
      )}

      <button onClick={goJudge}
        style={{ width: '100%', padding: '14px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer', marginBottom: 10, boxShadow: '0 2px 8px rgba(176,106,50,0.25)' }}>
        {selectedCode ? `🛡 立即研判 · ${selectedName || selectedCode}` : '🛡 进入持仓研判'}
      </button>

      <button onClick={() => { window.location.href = '/online-analysis/history' }}
        style={{ width: '100%', padding: '11px 0', background: 'transparent', color: THEME, border: `1px solid ${THEME}`, borderRadius: 12, fontSize: 13, cursor: 'pointer', marginBottom: 14 }}>
        查看历史研判记录
      </button>

      {/* 使用说明 */}
      <div style={{ background: PAPER, borderRadius: 12, border: `1px solid ${LINE}`, padding: '14px', fontSize: 12, color: INK_S, lineHeight: 1.75 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: THEME, marginBottom: 10, fontFamily: SERIF }}>持仓研判 · 使用说明</div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontWeight: 600, color: INK, marginBottom: 3 }}>📌 什么时候用？</div>
          <div>已经买入某只股票，最近有波动或消息，拿不准该继续持有还是止损时。</div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontWeight: 600, color: INK, marginBottom: 3 }}>🎯 需要准备什么？</div>
          <div>· <b>持仓逻辑</b>：为什么买这只股（如"看好 AI 算力扩产"）<br/>· <b>止损条件</b>：什么情况会卖（如"股价跌破 100 元"、"季报低于预期"）</div>
        </div>

        <div style={{ marginBottom: 10 }}>
          <div style={{ fontWeight: 600, color: INK, marginBottom: 3 }}>🤖 会得到什么？</div>
          <div>· 多头（Bull）视角 · 空头（Bear）视角 · 3 轮辩论<br/>· 综合裁判：BUY / HOLD / SELL 决策<br/>· 风险裁判：决策合理性校验</div>
        </div>

        <div style={{ padding: '8px 12px', background: BG, borderRadius: 8, fontSize: 11, color: INK_F, lineHeight: 1.7 }}>
          ℹ 与深度研究的区别：深度研究是"我要不要买"（投前认知），持仓研判是"我买了该不该拿"（投中决策）。两个视角互补，同一股票可对照参考。
        </div>
      </div>
    </div>
  )
}

// ── 发现 Tab ─────────────────────────────────────────────────────────────
// 产业链关键词映射（用于归类政采信号）
const CHAIN_KEYWORDS: Array<{ chain: string; desc: string; keywords: string[]; stocks: string[] }> = [
  { chain: '光模块/光互联', desc: 'AI数据中心高速互联核心器件', keywords: ['光模块', '光互联', '光纤', '光通信', 'AOC', '硅光'], stocks: ['中际旭创', '新易盛', '天孚通信'] },
  { chain: 'AI服务器/整机', desc: '算力基础设施集成商', keywords: ['服务器', 'AI算力', '算力集群', 'GPU服务器', '智算', 'HPC'], stocks: ['工业富联', '浪潮信息', '中科曙光'] },
  { chain: '算力芯片', desc: '国产GPU/GPGPU替代加速', keywords: ['算力芯片', 'GPU', 'AI芯片', '人工智能芯片', 'NPU', '智能计算芯片'], stocks: ['海光信息', '寒武纪', '龙芯中科'] },
  { chain: 'IDC/算力运营', desc: '算力中心建设与运营', keywords: ['数据中心', 'IDC', '算力基础设施', '机柜', '超算', '智算中心'], stocks: ['润泽科技', '光环新网', '数据港'] },
]

function _matchChain(title: string): string | null {
  const t = title.toLowerCase()
  for (const c of CHAIN_KEYWORDS) {
    if (c.keywords.some(kw => t.includes(kw.toLowerCase()))) return c.chain
  }
  return null
}

function DiscoverTab({ procurement, loading, opportunities, oppsLoading, userProfile, watchlistCodes, onResearch, onAddWatchlist, onOpenPreference }: {
  procurement: TrueProcurement | null; loading: boolean
  opportunities: { opportunities: OpportunityCard[]; profile_complete: boolean; profile: { risk_tolerance: string; holding_period: string; focus_sectors: string[] } | null; sectors?: SectorMeta[] } | null
  oppsLoading: boolean
  userProfile: { risk_tolerance: string; holding_period: string; focus_sectors: string[] } | null
  watchlistCodes: Set<string>
  onResearch: (symbol: string, name: string) => void
  onAddWatchlist: (symbol: string, name: string) => Promise<boolean>
  onOpenPreference: () => void
}) {
  const [addState, setAddState] = useState<Record<string, 'pending' | 'done' | 'error'>>({})

  const handleAdd = async (sym: string, name: string) => {
    setAddState(p => ({ ...p, [sym]: 'pending' }))
    const ok = await onAddWatchlist(sym, name)
    setAddState(p => ({ ...p, [sym]: ok ? 'done' : 'error' }))
  }

  const RISK_LABEL: Record<string, string> = { conservative: '稳健', balanced: '均衡', aggressive: '积极' }
  const HORIZON_LABEL: Record<string, string> = { short: '短线', medium: '中线', long: '长线' }
  const LEVEL_COLOR: Record<string, string> = { green: DN, yellow: '#8B6914', red: UP, grey: INK_F }
  const LEVEL_BG: Record<string, string> = { green: 'rgba(63,107,64,0.1)', yellow: 'rgba(139,105,20,0.08)', red: 'rgba(164,51,43,0.08)', grey: 'rgba(122,111,99,0.06)' }
  const LEVEL_LABEL: Record<string, string> = { green: '▲ 真实数据向好', yellow: '→ 有正向信号', red: '▼ 存在风险信号', grey: '数据待确认' }

  const profile = opportunities?.profile || userProfile
  const hasProfile = !!(profile?.risk_tolerance)

  // 板块 filter：默认根据用户偏好初始化（首个非 balanced 板块），只自动设一次
  const [sectorFilter, setSectorFilter] = useState<string>('all')
  const sectorInitedRef = useRef(false)
  useEffect(() => {
    if (sectorInitedRef.current) return
    const p = opportunities?.profile || userProfile
    if (!p) return
    const preferred = (p.focus_sectors || [])[0]
    if (preferred && preferred !== 'balanced') setSectorFilter(preferred)
    sectorInitedRef.current = true
  }, [opportunities, userProfile])

  const filteredOpps = sectorFilter === 'all'
    ? (opportunities?.opportunities ?? [])
    : (opportunities?.opportunities ?? []).filter(o => o.category === sectorFilter)

  const chainSigs: Record<string, ProcurementSignal[]> = {}
  if (procurement) {
    for (const sig of procurement.signals) {
      const c = _matchChain(sig.title)
      if (c) {
        if (!chainSigs[c]) chainSigs[c] = []
        chainSigs[c].push(sig)
      }
    }
  }

  const now = new Date()
  const dateStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`

  return (
    <div style={{ paddingBottom: 20 }}>
      {/* Header */}
      <div style={{ background: HEADER_BG, padding: '16px 16px 14px', borderBottom: `2px solid ${THEME}` }}>
        <div style={{ fontSize: 11, color: COPPER2, letterSpacing: '0.5px', marginBottom: 4 }}>一手情报 · 非投资建议</div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: PAPER, fontFamily: SERIF }}>🎯 为你发现</div>
          <button onClick={onOpenPreference}
            style={{ flexShrink: 0, padding: '6px 12px', background: 'transparent', border: `1px solid ${COPPER2}`, borderRadius: 16, color: COPPER2, fontSize: 12, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}>
            ⚙ 设置板块偏好
          </button>
        </div>
        <div style={{ fontSize: 12, color: '#aaa', marginTop: 4 }}>
          {oppsLoading ? '匹配板块偏好中…' : `产业链机会 · 板块匹配 · ${dateStr}`}
        </div>
      </div>

      {/* 画像 Banner */}
      {hasProfile && (
        <div style={{ margin: '10px 12px 0', padding: '10px 14px', background: 'rgba(176,106,50,0.08)', borderRadius: 12, border: '1px solid rgba(176,106,50,0.2)', fontSize: 12, color: INK_S }}>
          <span style={{ color: THEME, fontWeight: 600 }}>基于你的板块偏好：</span>
          {RISK_LABEL[profile!.risk_tolerance] || profile!.risk_tolerance} · {HORIZON_LABEL[profile!.holding_period] || profile!.holding_period}
          {(profile!.focus_sectors?.length ?? 0) > 0 && ` · 偏${(profile!.focus_sectors.slice(0, 2).map(s => ({tech:'科技',consumer:'消费',energy:'能源',finance:'金融',medical:'医药',balanced:'均衡'}[s] || s))).join('/')}板块`}
        </div>
      )}

      {/* 板块横向 filter */}
      {(opportunities?.sectors?.length ?? 0) > 0 && (
        <div style={{ margin: '10px 12px 0', display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 4 }}>
          {(() => {
            const totalAll = opportunities!.opportunities.length
            const tabs: { id: string; name: string; count: number }[] = [
              { id: 'all', name: '全部', count: totalAll },
              ...(opportunities!.sectors || []).filter(s => s.count > 0),
            ]
            return tabs.map(t => (
              <button key={t.id} onClick={() => setSectorFilter(t.id)}
                style={{
                  flexShrink: 0, padding: '6px 14px', borderRadius: 16,
                  border: `1px solid ${sectorFilter === t.id ? THEME : LINE}`,
                  background: sectorFilter === t.id ? THEME : PAPER,
                  color: sectorFilter === t.id ? '#fff' : INK_S,
                  fontSize: 12, fontWeight: sectorFilter === t.id ? 600 : 400, cursor: 'pointer',
                }}>
                {t.name} <span style={{ opacity: 0.7 }}>{t.count}</span>
              </button>
            ))
          })()}
        </div>
      )}

      {/* 机会卡片 */}
      {oppsLoading ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: INK_F, fontSize: 13 }}>正在匹配产业链机会…</div>
      ) : filteredOpps.length > 0 ? (
        <>
          {filteredOpps.slice(0, sectorFilter === 'all' ? 6 : 20).map((opp, idx) => {
            const lc = LEVEL_COLOR[opp.alert_level] || INK_F
            const lb = LEVEL_BG[opp.alert_level] || LEVEL_BG.grey
            const ll = LEVEL_LABEL[opp.alert_level] || '—'
            const firstRep = opp.rep_stocks[0]
            return (
              <div key={idx} style={{ background: PAPER, margin: '10px 12px 0', borderRadius: 16, border: `1px solid ${LINE}`, overflow: 'hidden' }}>
                <div style={{ padding: '14px 16px 10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF }}>{opp.chain}</div>
                    <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                      {[1,2,3,4,5].map(i => (
                        <div key={i} style={{ width: 7, height: 7, borderRadius: '50%', background: i <= opp.match_score ? THEME : LINE }} />
                      ))}
                    </div>
                  </div>
                  <div style={{ fontSize: 12, color: INK_F, marginBottom: 8 }}>{opp.desc}</div>
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 20, background: lb }}>
                    <span style={{ fontSize: 11, color: lc }}>✓</span>
                    <span style={{ fontSize: 12, color: lc, fontWeight: 600 }}>{ll}</span>
                    {opp.signal_count > 0 && <span style={{ fontSize: 11, color: INK_F }}>· {opp.signal_count}条</span>}
                  </div>
                </div>
                <div style={{ padding: '4px 16px 10px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {opp.rep_stocks.slice(0, 3).map(s => (
                    <div key={s.symbol} style={{ fontSize: 12, color: THEME, background: 'rgba(176,106,50,0.08)', border: '1px solid rgba(176,106,50,0.2)', borderRadius: 20, padding: '3px 10px' }}>
                      {s.name}
                    </div>
                  ))}
                </div>
                <div style={{ padding: '0 16px 14px', display: 'flex', gap: 8 }}>
                  <button onClick={() => firstRep && onResearch(firstRep.symbol, firstRep.name)}
                    disabled={!firstRep}
                    style={{ flex: 1, padding: '9px 0', background: firstRep ? THEME : LINE, color: '#fff', border: 'none', borderRadius: 10, fontSize: 13, fontWeight: 600, cursor: firstRep ? 'pointer' : 'not-allowed' }}>
                    {firstRep ? `研究 ${firstRep.name}` : '深度研究'}
                  </button>
                  {firstRep && (() => {
                    const already = watchlistCodes.has(firstRep.symbol) || addState[firstRep.symbol] === 'done'
                    const state = addState[firstRep.symbol]
                    const label = already ? '✓ 已加入'
                      : state === 'pending' ? '添加中…'
                      : state === 'error'   ? '失败·重试'
                      : '+ 加入跟踪'
                    const clr = already ? DN : state === 'error' ? '#dc2626' : THEME
                    return (
                      <button onClick={() => !already && state !== 'pending' && handleAdd(firstRep.symbol, firstRep.name)}
                        disabled={already || state === 'pending'}
                        style={{ flex: 1, padding: '9px 0', background: 'transparent', color: clr, border: `1.5px solid ${clr}`, borderRadius: 10, fontSize: 13, fontWeight: 600, cursor: (already || state === 'pending') ? 'default' : 'pointer', opacity: already ? 0.7 : 1 }}>
                        {label}
                      </button>
                    )
                  })()}
                </div>
              </div>
            )
          })}
        </>
      ) : !oppsLoading && sectorFilter !== 'all' && (opportunities?.opportunities?.length ?? 0) > 0 ? (
        <div style={{ padding: '20px 16px', textAlign: 'center', color: INK_F, fontSize: 13 }}>
          该板块暂无匹配机会，请切换到其他板块
        </div>
      ) : !oppsLoading && (
        <div style={{ padding: '20px 16px', textAlign: 'center', color: INK_F, fontSize: 13 }}>暂无匹配机会，请先完善板块偏好</div>
      )}

      {/* 政采信号区块 */}
      <div style={{ margin: '16px 12px 4px', padding: '8px 2px', borderBottom: `1px solid ${LINE}`, display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: INK, fontFamily: SERIF }}>政采实时信号</div>
        <div style={{ fontSize: 11, color: INK_F }}>
          {loading ? '加载中…' : procurement ? `近7天 ${procurement.count} 条` : '暂无数据'}
        </div>
      </div>

      {CHAIN_KEYWORDS.map(chainDef => {
        const sigs = chainSigs[chainDef.chain] || []
        if (sigs.length === 0) return null
        return (
          <div key={chainDef.chain} style={{ background: PAPER, margin: '6px 12px 0', borderRadius: 12, border: `1px solid ${LINE}`, padding: '12px 14px' }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: INK, marginBottom: 6 }}>{chainDef.chain}</div>
            {sigs.slice(0, 2).map((sig, i) => (
              <div key={i} style={{ fontSize: 12, color: INK_S, display: 'flex', gap: 6, lineHeight: 1.5, marginBottom: 4 }}>
                <span style={{ color: UP, flexShrink: 0, fontSize: 10, marginTop: 2 }}>●</span>
                <span>{sig.title.replace(/^[\[【]政采[\]】]\s*/, '').slice(0, 55)}{sig.title.length > 55 ? '…' : ''}</span>
              </div>
            ))}
            {sigs.length > 2 && <div style={{ fontSize: 11, color: INK_F, marginTop: 4 }}>另有 {sigs.length - 2} 条</div>}
          </div>
        )
      })}

      {procurement && (() => {
        const unmatched = procurement.signals.filter(sig => !_matchChain(sig.title))
        if (unmatched.length === 0) return null
        return (
          <div style={{ background: PAPER, margin: '6px 12px 0', borderRadius: 12, border: `1px solid ${LINE}`, padding: '12px 14px' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: INK, marginBottom: 6 }}>其他算力相关</div>
            {unmatched.slice(0, 3).map((sig, i) => (
              <div key={i} style={{ fontSize: 12, color: INK_S, marginBottom: 5, lineHeight: 1.6, display: 'flex', gap: 6 }}>
                <span style={{ color: INK_F, flexShrink: 0 }}>·</span>
                <span>{sig.title.slice(0, 60)}{sig.title.length > 60 ? '…' : ''}</span>
              </div>
            ))}
          </div>
        )
      })()}

      <div style={{ margin: '10px 12px 0', padding: '10px 14px', background: PAPER2, borderRadius: 10, fontSize: 11, color: INK_F, lineHeight: 1.7 }}>
        ℹ 政采信号来自政府采购网（腾讯云232节点直连爬虫），每小时更新，仅供参考，不构成投资建议。
      </div>
    </div>
  )
}

export default function WxHome() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const [token, setToken] = useState<string | null>(null)
  const [userEmail, setUserEmail] = useState('')
  const [tab, setTab] = useState<Tab>('watchlist')
  const [researchMode, setResearchMode] = useState<'assistant' | 'kpred' | 'scout' | 'research' | 'hold' | 'event'>('assistant')
  const [portfolioMode, setPortfolioMode] = useState<'overview' | 'list' | 'position' | 'alert'>('overview')
  // ③持仓 的二级 tab: 持仓报告 / 持仓研判 / 事件解读
  const [holdingMode, setHoldingMode] = useState<'position' | 'hold' | 'event'>('position')
  // ⑤回测 的二级页: 成绩单 / 管理股票 / 回测设置
  const [backtestMode, setBacktestMode] = useState<'board' | 'pool' | 'config'>('board')
  const [showSiteMap, setShowSiteMap] = useState(false)
  const [axActive, setAxActive] = useState(false)  // 活动参与者才发埋点
  // 会员信息（"我的" tab 展示账号等级 + 有效期）
  const [membership, setMembership] = useState<{
    level: 'pro' | 'free'
    expiresAt: string | null   // ISO 字符串
    daysLeft: number | null
  }>({ level: 'free', expiresAt: null, daysLeft: null })

  // 研究 Tab 共享股票选择（跨深度研究 / 真源分析 / K线预测 保持）
  const [rsInput, setRsInput] = useState('')
  const [rsSelectedCode, setRsSelectedCode] = useState('')
  const [rsSelectedName, setRsSelectedName] = useState('')
  const researchStock: ResearchStockCtx = {
    input: rsInput, setInput: setRsInput,
    selectedCode: rsSelectedCode, setSelectedCode: setRsSelectedCode,
    selectedName: rsSelectedName, setSelectedName: setRsSelectedName,
  }

  // 绑定真实账号
  const [showBind, setShowBind] = useState(false)
  const [bindEmail, setBindEmail] = useState('')
  const [bindPassword, setBindPassword] = useState('')
  const [bindLoading, setBindLoading] = useState(false)
  const [bindMsg, setBindMsg] = useState('')

  // 自选股
  const [stocks, setStocks] = useState<Stock[]>([])
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [loadingW, setLoadingW] = useState(false)
  const [showSearch, setShowSearch] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [searchRes, setSearchRes] = useState<SearchResult[]>([])
  const [addingCode, setAddingCode] = useState('')

  // 股票详情
  const [detailStock, setDetailStock] = useState<Stock | null>(null)

  // 用户展示名（可编辑）
  const [displayName, setDisplayName] = useState('')
  const [editingName, setEditingName] = useState(false)
  const [nameInput, setNameInput] = useState('')
  const [nameSaved, setNameSaved] = useState(false)

  // 副驾/发现 缓存状态（Tab 切换后不重新请求）
  const [preds, setPreds] = useState<Record<string, KProResult | null>>({})
  const [predLoadingMap, setPredLoadingMap] = useState<Record<string, boolean>>({})
  const [brief, setBrief] = useState<TrueBrief | null>(null)
  const [procurement, setProcurement] = useState<TrueProcurement | null>(null)
  const [procurementLoading, setProcurementLoading] = useState(true)
  const [opportunities, setOpportunities] = useState<{ opportunities: OpportunityCard[]; profile_complete: boolean; profile: { risk_tolerance: string; holding_period: string; focus_sectors: string[] } | null } | null>(null)
  const [oppsLoading, setOppsLoading] = useState(false)
  const copilotFetchedRef = useRef(false)
  const discoverFetchedRef = useRef(false)
  const oppsFetchedRef = useRef(false)
  // 价格提醒面板状态
  const [alertExpanded, setAlertExpanded] = useState<string | null>(null)
  const [alertsMap, setAlertsMap] = useState<Record<string, PriceAlert[]>>({})
  const [alertForm, setAlertForm] = useState({ conditionType: 'change_pct_below', threshold: '3' })
  const [alertSaving, setAlertSaving] = useState(false)

  // 用户画像
  const [userProfile, setUserProfile] = useState<{risk_tolerance: string; holding_period: string; focus_sectors: string[]} | null>(null)
  const [profileLoaded, setProfileLoaded] = useState(false)
  const [showProfileSurvey, setShowProfileSurvey] = useState(false)
  const [profileSaving, setProfileSaving] = useState(false)
  const [surveyRisk, setSurveyRisk] = useState('')
  const [surveyHorizon, setSurveyHorizon] = useState('')
  const [surveySectors, setSurveySectors] = useState<string[]>([])

  // 锁定微信字体大小，阻止缩放悬浮条出现
  useEffect(() => {
    function lockWxFont() {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const bridge = (window as any).WeixinJSBridge
      if (bridge && bridge.invoke) bridge.invoke('setFontSizeCallback', { fontSize: 0 })
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if ((window as any).WeixinJSBridge) {
      lockWxFont()
    } else {
      document.addEventListener('WeixinJSBridgeReady', lockWxFont, false)
      return () => document.removeEventListener('WeixinJSBridgeReady', lockWxFont, false)
    }
  }, [])

  // Token 初始化（URL ?t= 或 localStorage）
  useEffect(() => {
    const urlT = new URLSearchParams(window.location.search).get('t')
    const raw = urlT || localStorage.getItem('hunter_token')
    if (urlT) window.history.replaceState({}, '', window.location.pathname)
    if (raw) {
      try {
        const p = JSON.parse(atob(raw.split('.')[1]))
        if (p.exp && p.exp < Date.now() / 1000) { localStorage.removeItem('hunter_token'); setToken(''); return }
        localStorage.setItem('hunter_token', raw)
        setToken(raw); setUserEmail(p.email || '')
      } catch { localStorage.removeItem('hunter_token'); setToken('') }
    } else { setToken('') }
  }, [])

  const authH = useCallback((): HeadersInit => ({ Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }), [token])

  // 加载并保存展示名
  useEffect(() => {
    if (!token) return
    fetch('/api/auth/profile', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json()).then(d => { if (d.display_name) setDisplayName(d.display_name) }).catch(() => {})
  }, [token])

  // V4：活动参与者检测（有 ax_event 行才发功能埋点）+ 会员状态提取
  useEffect(() => {
    if (!token) return
    fetch('/api/ax/me', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.ax_active) setAxActive(true)
        const exp = d?.activity?.member_expires_at as string | null | undefined
        if (exp) {
          const expDate = new Date(exp)
          const now = new Date()
          const isValid = expDate.getTime() > now.getTime()
          const days = isValid
            ? Math.ceil((expDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
            : 0
          setMembership({
            level: isValid ? 'pro' : 'free',
            expiresAt: exp,
            daysLeft: isValid ? days : null,
          })
        } else {
          setMembership({ level: 'free', expiresAt: null, daysLeft: null })
        }
      })
      .catch(() => {})
  }, [token])

  // V4：?nav= 参数入口（从活动页跳过来自动定位到指定功能）
  const navAppliedRef = useRef(false)
  useEffect(() => {
    if (navAppliedRef.current) return
    const nav = searchParams?.get('nav') || ''
    if (!nav) return
    navAppliedRef.current = true
    const [t, sub] = nav.split(':')
    // 旧深链兼容: 持仓报告/持仓研判/事件解读 已迁到 ③持仓, 自动重定向
    if (t === 'watchlist' && sub === 'position') {
      setTab('holding'); setHoldingMode('position')
    } else if (t === 'kpred' && (sub === 'hold' || sub === 'event')) {
      setTab('holding'); setHoldingMode(sub)
    } else if (t === 'holding' && (sub === 'position' || sub === 'hold' || sub === 'event')) {
      setTab('holding'); setHoldingMode(sub)
    } else if (t === 'watchlist' && (sub === 'overview' || sub === 'list' || sub === 'alert')) {
      setTab('watchlist'); setPortfolioMode(sub)
    } else if (t === 'kpred' && (sub === 'assistant' || sub === 'research' || sub === 'scout' || sub === 'kpred')) {
      setTab('kpred'); setResearchMode(sub)
    } else if (t === 'discover') {
      setTab('discover')
    } else if (t === 'profile') {
      setTab('profile')
    } else if (t === 'mine') {
      setTab('mine')          // 「我的」入口已从底部挪到顶部栏, 老深链仍然有效
    } else if (t === 'backtest') {
      setTab('backtest')
      if (sub === 'pool' || sub === 'config') setBacktestMode(sub)
    }
    // 清除 URL 参数，避免刷新残留
    try { window.history.replaceState({}, '', window.location.pathname) } catch {}
  }, [searchParams])

  // V4：功能体验埋点（仅活动参与者上报）
  useEffect(() => {
    if (!token || !axActive) return
    const key = tab === 'watchlist' ? `watchlist:${portfolioMode}`
              : tab === 'kpred' ? `kpred:${researchMode}`
              : tab === 'holding' ? `holding:${holdingMode}`
              : tab === 'discover' ? 'discover:main'
              : tab === 'profile' ? 'profile:main'
              : ''
    // feature_id 保持原值不变(活动埋点与后端约定), 仅入口位置变了
    const featureMap: Record<string, string> = {
      'watchlist:overview': 'portfolio_overview',
      'watchlist:list':     'watchlist_add',
      'watchlist:alert':    'portfolio_alert',
      'holding:position':   'portfolio_report',
      'holding:hold':       'research_hold',
      'holding:event':      'research_event',
      'kpred:research':     'research_deep',
      'kpred:scout':        'research_scout',
      'kpred:kpred':        'research_kpred',
      'discover:main':      'discover',
      'profile:main':       'sector_preference',
    }
    const fid = featureMap[key]
    if (!fid) return
    fetch('/api/ax/features/track', {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ feature_id: fid }),
    }).catch(() => {})
  }, [tab, researchMode, portfolioMode, holdingMode, token, axActive])

  const saveDisplayName = async () => {
    const name = nameInput.trim()
    if (!name) { setEditingName(false); return }
    try {
      await fetch('/api/auth/profile', { method: 'PATCH', headers: authH(), body: JSON.stringify({ display_name: name }) })
      setDisplayName(name)
      setNameSaved(true)
      setTimeout(() => setNameSaved(false), 2000)
    } catch {}
    setEditingName(false)
  }

  const loadWatchlist = useCallback(async () => {
    if (!token) return
    // P2 SWR: 有缓存立即渲染, 后台照常拉真数据 revalidate
    // (自选股列表结构性数据变化不频繁, 缓存 6h; quotes 不缓存避免误导)
    const cached = readSwr<Stock[]>('wx_watchlist')
    if (cached && cached.length) {
      setStocks(cached)
    } else {
      setLoadingW(true)
    }
    try {
      const [r1, r2] = await Promise.all([
        fetch('/api/watchlist', { headers: authH() }),
        fetch('/api/quotes', { headers: authH() }),
      ])
      const s: Stock[] = await r1.json()
      const { quotes: qs } = await r2.json()
      if (Array.isArray(s)) {
        setStocks(s)
        writeSwr('wx_watchlist', s)
      }
      const qm: Record<string, Quote> = {}
      for (const q of (qs || [])) qm[q.code] = q
      setQuotes(qm)
    } catch {}
    setLoadingW(false)
  }, [token, authH])

  useEffect(() => { if (token) loadWatchlist() }, [token]) // eslint-disable-line

  // 新用户(无自选股)默认落在 ①选股, 而不是空荡荡的盯盘页;
  // 已有自选股的老用户保持原落地(②盯盘), 且 ?nav= 深链优先级更高
  const landedRef = useRef(false)
  useEffect(() => {
    if (landedRef.current || loadingW || !token) return
    if (navAppliedRef.current) { landedRef.current = true; return }
    landedRef.current = true
    if (stocks.length === 0) setTab('discover')
  }, [stocks, loadingW, token])

  // 价格提醒 CRUD
  const loadAlerts = useCallback(async (code: string) => {
    if (!token) return
    try {
      const r = await fetch(`/api/alerts/${code}`, { headers: { Authorization: `Bearer ${token}` } })
      if (r.ok) { const d = await r.json(); setAlertsMap(prev => ({ ...prev, [code]: d.alerts || [] })) }
    } catch {}
  }, [token])

  const toggleAlertPanel = useCallback(async (code: string) => {
    if (alertExpanded === code) { setAlertExpanded(null); return }
    setAlertExpanded(code)
    await loadAlerts(code)
  }, [alertExpanded, loadAlerts])

  const deleteAlertItem = useCallback(async (alertId: number, code: string) => {
    if (!token) return
    try {
      await fetch(`/api/alerts/${alertId}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
      await loadAlerts(code)
    } catch {}
  }, [token, loadAlerts])

  const addAlertItem = useCallback(async (code: string) => {
    if (!token || !alertForm.threshold) return
    setAlertSaving(true)
    try {
      await fetch(`/api/alerts/${code}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ condition_type: alertForm.conditionType, threshold: parseFloat(alertForm.threshold), label: '' }),
      })
      await loadAlerts(code)
      setAlertForm(f => ({ ...f, threshold: '3' }))
    } catch {} finally { setAlertSaving(false) }
  }, [token, alertForm, loadAlerts])

  const condLabel = (type: string, thr: number) => {
    if (type === 'change_pct_below') return `跌幅超过 ${thr}%`
    if (type === 'price_above') return `涨至 ${thr} 元以上`
    if (type === 'price_below') return `跌至 ${thr} 元以下`
    if (type === 'volatility_above') return `日内振幅超 ${thr}%`
    return `${type} ≥ ${thr}`
  }

  // 副驾：Kronos 预测 + TrueSource 简报（只取一次，Tab 切换后复用）
  useEffect(() => {
    if (!token || !stocks.length || copilotFetchedRef.current) return
    copilotFetchedRef.current = true

    // 限流：最多 3 并发，避免打爆微信浏览器连接池（6 个/域名）
    // 每个 kpred 请求加 6s 超时，Kronos 挂时快速失败释放连接
    const aStocks = stocks.filter(s => s.market === 'A')
    aStocks.forEach(s => setPredLoadingMap(prev => ({ ...prev, [s.code]: true })))

    const queue = [...aStocks]
    const fetchOne = async (s: Stock) => {
      try {
        const d = await fetchJsonWithTimeout<KProResult | null>(
          `/api/kpred/${s.code}/pro?days=1`,
          { headers: { Authorization: `Bearer ${token}` } },
          6000,
        )
        setPreds(prev => ({ ...prev, [s.code]: d }))
      } catch {
        setPreds(prev => ({ ...prev, [s.code]: null }))
      } finally {
        setPredLoadingMap(prev => ({ ...prev, [s.code]: false }))
      }
    }
    const runWorker = async () => {
      while (queue.length) {
        const s = queue.shift()
        if (s) await fetchOne(s)
      }
    }
    // 启动 3 个 worker 并发消费队列
    Promise.all([runWorker(), runWorker(), runWorker()])

    const aShareCodes = aStocks.map(s => s.code).join(',')
    if (aShareCodes) {
      fetch(`/api/truesource/daily-brief?symbols=${aShareCodes}`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.ok ? r.json() : null)
        .then(d => setBrief(d))
        .catch(() => {})
    }
  }, [stocks, token]) // eslint-disable-line

  // 发现：政采信号（只取一次）
  useEffect(() => {
    if (!token || discoverFetchedRef.current) return
    discoverFetchedRef.current = true
    fetch('/api/truesource/procurement?days=7&limit=20', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => setProcurement(d))
      .catch(() => {})
      .finally(() => setProcurementLoading(false))
  }, [token]) // eslint-disable-line

  // 发现：板块偏好匹配机会
  const loadOpportunities = useCallback(async () => {
    if (!token) return
    setOppsLoading(true)
    try {
      const r = await fetch('/api/discover/opportunities', { headers: { Authorization: `Bearer ${token}` } })
      if (r.ok) setOpportunities(await r.json())
    } catch {}
    setOppsLoading(false)
  }, [token])

  useEffect(() => {
    if (!token || oppsFetchedRef.current) return
    oppsFetchedRef.current = true
    loadOpportunities()
  }, [token, loadOpportunities])

  // 用户画像：token 就绪后加载一次
  useEffect(() => {
    if (!token) return
    fetch('/api/user/preference', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(d => { setUserProfile(d); setProfileLoaded(true) })
      .catch(() => setProfileLoaded(true))
  }, [token]) // eslint-disable-line

  // 全新用户(未填画像 + 无自选股 + 没走过引导) → 跳三步引导页;
  // 只填了画像没自选股 / 老用户漏填画像 → 仍走原来的弹窗问卷, 不打断
  useEffect(() => {
    if (!profileLoaded || !token || loadingW) return
    const noProfile = !userProfile || !userProfile.risk_tolerance
    if (!noProfile) return
    let onboarded = '1'
    try { onboarded = localStorage.getItem('wx_onboarded') || '' } catch { /* ignore */ }
    if (!onboarded && stocks.length === 0 && !navAppliedRef.current) {
      router.replace('/wx/onboarding')
      return
    }
    if (tab === 'discover') setShowProfileSurvey(true)
  }, [tab, profileLoaded, userProfile, token, loadingW, stocks.length, router])

  // 搜索防抖
  useEffect(() => {
    if (!searchQ.trim()) { setSearchRes([]); return }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`/api/watchlist/search?q=${encodeURIComponent(searchQ)}&limit=10`, { headers: authH() })
        const d = await r.json()
        setSearchRes(d.items || d.results || (Array.isArray(d) ? d : []))
      } catch { setSearchRes([]) }
    }, 400)
    return () => clearTimeout(t)
  }, [searchQ]) // eslint-disable-line

  async function addStock(s: SearchResult) {
    setAddingCode(s.code)
    try {
      await fetch('/api/watchlist', { method: 'POST', headers: authH(), body: JSON.stringify({ code: s.code, name: s.name, market: s.market, exchange: s.exchange, asset_type: s.asset_type || 'stock' }) })
      setShowSearch(false); setSearchQ(''); setSearchRes([])
      await loadWatchlist()
    } catch {}
    setAddingCode('')
  }

  async function removeStock(code: string) {
    if (!window.confirm(`确认删除 ${code}？`)) return
    try {
      await fetch(`/api/watchlist/${code}`, { method: 'DELETE', headers: authH() })
      await loadWatchlist()
    } catch {}
  }

  function logout() { localStorage.removeItem('hunter_token'); setToken(''); setStocks([]); setQuotes({}) }

  // 加载中
  if (token === null) return <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: BG, fontFamily: SERIF }}><span style={{ color: INK_F }}>加载中...</span></div>
  // 未登录
  if (token === '') return <LoginScreen onLogin={(t, e) => { setToken(t); setUserEmail(e) }} />

  // 股票详情子页
  if (detailStock) return (
    <div style={{ minHeight: '100vh', background: BG, fontFamily: SERIF, maxWidth: 480, margin: '0 auto', display: 'flex', flexDirection: 'column' }}>
      <StockDetail stock={detailStock} quote={quotes[detailStock.code]} token={token} onBack={() => setDetailStock(null)} />
    </div>
  )

  // 四步闭环导航: 选股 → 盯盘 → 持仓 → 挖掘 (对应 BP 的 Discover/Monitor/Manage/Explore)
  const TAB_DEFS: { key: Tab; label: string; step?: string; Icon: React.FC<{ active: boolean }> }[] = [
    { key: 'discover',  label: '选股', step: '①', Icon: IconDiscover },
    { key: 'watchlist', label: '盯盘', step: '②', Icon: IconWatchlist },
    { key: 'holding',   label: '持仓', step: '③', Icon: IconPortfolio },
    { key: 'kpred',     label: '挖掘', step: '④', Icon: IconKPred },
    // ⑤回测 顶掉原来的「我的」:回测是每天收盘后要看的高频页, 而「我的」(改名字/看会员/
    // 退出登录)是低频页, 挪到顶部栏点标题进入 —— 与主流 App 的头像入口一致。
    { key: 'backtest',  label: '回测', step: '⑤', Icon: IconBacktest },
  ]

  return (
    <div style={{ minHeight: '100vh', background: BG, fontFamily: SERIF, display: 'flex', flexDirection: 'column', maxWidth: 480, margin: '0 auto' }}>

      {/* 顶部栏 */}
      <div style={{ background: HEADER_BG, padding: '12px 16px 10px', borderBottom: `2px solid ${THEME}`, display: 'flex', alignItems: 'center', gap: 10, position: 'sticky', top: 0, zIndex: 20 }}>
        {/* 标题区整块可点 → 「我的」(原底部第5个 Tab 已让位给⑤回测) */}
        <div onClick={() => setTab('mine')} role="button" aria-label="账号信息"
          style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0, cursor: 'pointer' }}>
          <img src="/logo-hunter.png" alt="猎鹿人" style={{ width: 34, height: 34, borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: PAPER, display: 'flex', alignItems: 'center', gap: 4 }}>
              猎鹿人 · Hunter
              <span style={{ fontSize: 13, color: COPPER2, opacity: tab === 'mine' ? 1 : 0.75 }}>›</span>
            </div>
            <div style={{ fontSize: 11, color: COPPER2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{
              tab === 'discover' ? '① 选股 · 找到值得买的那只'
              : tab === 'watchlist' ? (portfolioMode === 'overview' ? '② 盯盘 · 综合概览' : portfolioMode === 'list' ? '② 盯盘 · 自选股清单' : '② 盯盘 · 提醒设置')
              : tab === 'holding' ? (holdingMode === 'position' ? '③ 持仓 · 持仓报告' : holdingMode === 'hold' ? '③ 持仓 · 持仓研判' : '③ 持仓 · 事件解读')
              : tab === 'kpred' ? (researchMode === 'assistant' ? '④ 挖掘 · 投研助手' : researchMode === 'research' ? '④ 挖掘 · 深度研究' : researchMode === 'scout' ? '④ 挖掘 · 一手情报' : '④ 挖掘 · 量化择时')
              : tab === 'backtest' ? (backtestMode === 'pool' ? '⑤ 回测 · 管理股票' : backtestMode === 'config' ? '⑤ 回测 · 回测设置' : '⑤ 回测 · 我的成绩单')
              : '账号信息'}</div>
          </div>
        </div>
        {tab === 'watchlist' && (
          <button onClick={loadWatchlist} disabled={loadingW} style={{ background: 'none', border: `1px solid ${COPPER2}`, color: COPPER2, fontSize: 12, borderRadius: 6, padding: '4px 10px', cursor: 'pointer' }}>
            {loadingW ? '刷新中' : '↻ 刷新'}
          </button>
        )}
        <button onClick={() => setShowSiteMap(true)} title="功能地图"
          style={{ background: 'none', border: `1px solid ${COPPER2}`, color: COPPER2, fontSize: 13, borderRadius: 6, padding: '4px 8px', cursor: 'pointer', flexShrink: 0 }}>
          🗺 地图
        </button>
      </div>

      {/* 内容区 */}
      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 'calc(72px + env(safe-area-inset-bottom, 0px))' }}>

        {/* 持仓中心（原自选股 + 副驾合并） */}
        {tab === 'watchlist' && (
          <div>
            {/* 二级 tab：概览 / 清单 / 提醒设置(持仓报告已移至 ③持仓) */}
            <div style={{ background: PAPER, padding: '4px', display: 'flex', gap: 4, borderBottom: `1px solid ${LINE}` }}>
              {([['overview', '综合概览'], ['list', '自选股清单'], ['alert', '提醒设置']] as const).map(([mode, label]) => (
                <button key={mode} onClick={() => setPortfolioMode(mode)}
                  style={{ flex: 1, padding: '10px 0', background: portfolioMode === mode ? THEME : 'transparent', color: portfolioMode === mode ? '#fff' : INK_S, border: 'none', borderRadius: 8, fontSize: 13, fontWeight: portfolioMode === mode ? 600 : 400, cursor: 'pointer', fontFamily: SERIF }}>
                  {label}
                </button>
              ))}
            </div>

            {/* 综合概览（原副驾） */}
            {portfolioMode === 'overview' && (
              <CopilotTab token={token ?? ''} stocks={stocks} quotes={quotes} preds={preds} loadingMap={predLoadingMap} brief={brief} />
            )}

            {/* 持仓报告已移至 ③持仓;旧深链 nav=watchlist:position 会被重定向 */}

            {/* 提醒设置 */}
            {portfolioMode === 'alert' && token && (
              <PushSetupTab token={token} />
            )}

            {/* 自选股清单 */}
            {portfolioMode === 'list' && (
            <div>
            {loadingW && !stocks.length ? (
              <div style={{ textAlign: 'center', padding: '80px 0', color: '#bbb', fontSize: 14 }}>加载中...</div>
            ) : (
              <>
                {!stocks.length && (
                  <StartGuideCard onGoDiscover={() => setTab('discover')} onSearch={() => setShowSearch(true)} />
                )}
                {stocks.map(s => {
                  const q = quotes[s.code]
                  const pct = q?.change_pct
                  const clr = priceColor(pct)
                  return (
                    <div key={s.code} style={{ margin: '8px 12px' }}>
                      {/* 主卡片 */}
                      <div onClick={() => setDetailStock(s)} style={{ position: 'relative', background: PAPER, borderRadius: alertExpanded === s.code ? '14px 14px 0 0' : 14, padding: '14px 16px', boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, borderBottom: alertExpanded === s.code ? `1px solid ${PAPER2}` : `1px solid ${LINE}`, display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' } as React.CSSProperties}>
                        <div style={{ position: 'absolute', left: 0, top: 10, bottom: 10, width: 4, background: clr }} />
                        <div style={{ flex: 1, paddingLeft: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{ flex: 1 }}>
                            <div style={{ fontSize: 15, fontWeight: 600, color: INK, fontFamily: SERIF }}>{s.name}</div>
                            <div style={{ fontSize: 12, color: INK_F, marginTop: 3 }}>{s.code} · {s.market === 'A' ? 'A股' : s.market === 'HK' ? '港股' : '美股'}</div>
                          </div>
                          <div style={{ textAlign: 'right', minWidth: 72 }}>
                            {q?.price != null ? (
                              <>
                                <div style={{ fontSize: 20, fontWeight: 700, color: clr }}>{q.price}</div>
                                <div style={{ fontSize: 13, color: clr }}>{pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) + '%' : '--'}</div>
                              </>
                            ) : <div style={{ fontSize: 12, color: INK_F }}>非交易时段</div>}
                          </div>
                          <span style={{ color: LINE, fontSize: 18 }}>›</span>
                          <button onClick={e => { e.stopPropagation(); toggleAlertPanel(s.code) }} title="价格提醒" style={{ background: alertsMap[s.code]?.length ? 'rgba(176,106,50,0.12)' : 'rgba(0,0,0,0.04)', border: 'none', borderRadius: 20, padding: '5px 7px', cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center' }}>
                            <IconBell hasAlert={!!(alertsMap[s.code]?.length)} />
                          </button>
                          <button onClick={e => { e.stopPropagation(); removeStock(s.code) }} style={{ background: 'rgba(231,76,60,0.07)', border: 'none', borderRadius: 20, color: '#e74c3c', fontSize: 12, padding: '5px 12px', cursor: 'pointer', fontWeight: 500, flexShrink: 0 }}>删除</button>
                        </div>
                      </div>
                      {/* 价格提醒面板 */}
                      {alertExpanded === s.code && (
                        <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderTop: 'none', borderRadius: '0 0 14px 14px', padding: '12px 16px 14px', boxShadow: '0 2px 8px rgba(50,35,10,.07)' }}>
                          {(alertsMap[s.code] || []).length > 0 && (
                            <div style={{ marginBottom: 10 }}>
                              <div style={{ fontSize: 11, color: INK_F, marginBottom: 6, fontWeight: 600 }}>已设置的提醒</div>
                              {(alertsMap[s.code] || []).map(a => (
                                <div key={a.id} style={{ display: 'flex', alignItems: 'center', background: PAPER2, borderRadius: 8, padding: '6px 10px', marginBottom: 4 }}>
                                  <span style={{ flex: 1, fontSize: 13, color: INK }}>{condLabel(a.condition_type, a.threshold)}</span>
                                  <button onClick={() => deleteAlertItem(a.id, s.code)} style={{ background: 'none', border: 'none', color: '#e74c3c', fontSize: 12, cursor: 'pointer', padding: '0 4px' }}>× 删除</button>
                                </div>
                              ))}
                            </div>
                          )}
                          <div style={{ fontSize: 11, color: INK_F, marginBottom: 6, fontWeight: 600 }}>新增提醒</div>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                            <select value={alertForm.conditionType} onChange={e => setAlertForm(f => ({ ...f, conditionType: e.target.value }))} style={{ flex: 1, minWidth: 110, fontSize: 13, padding: '6px 8px', border: `1px solid ${LINE}`, borderRadius: 8, background: PAPER, color: INK, fontFamily: SERIF }}>
                              <option value="change_pct_below">跌幅超过</option>
                              <option value="price_above">涨至以上</option>
                              <option value="price_below">跌至以下</option>
                              <option value="volatility_above">日内振幅超</option>
                            </select>
                            <input
                              type="number" min="0.1" step="0.5"
                              value={alertForm.threshold}
                              onChange={e => setAlertForm(f => ({ ...f, threshold: e.target.value }))}
                              placeholder={['change_pct_below','volatility_above'].includes(alertForm.conditionType) ? '如：3' : '如：45'}
                              style={{ width: 72, fontSize: 13, padding: '6px 8px', border: `1px solid ${LINE}`, borderRadius: 8, background: PAPER, color: INK, fontFamily: SERIF }}
                            />
                            <span style={{ fontSize: 12, color: INK_F, flexShrink: 0 }}>
                              {['change_pct_below','volatility_above'].includes(alertForm.conditionType) ? '%' : '元'}
                            </span>
                            <button onClick={() => addAlertItem(s.code)} disabled={alertSaving || !alertForm.threshold} style={{ background: alertSaving ? INK_F : THEME, color: '#fff', border: 'none', borderRadius: 8, padding: '6px 16px', fontSize: 13, cursor: 'pointer', fontWeight: 600, flexShrink: 0 }}>
                              {alertSaving ? '…' : '确认'}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
                <div style={{ padding: '12px 12px 6px' }}>
                  <button onClick={() => setShowSearch(true)} style={{ width: '100%', padding: '13px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>+ 添加自选股</button>
                </div>
              </>
            )}
            </div>
            )}
          </div>
        )}

        {/* ③ 持仓 —— 买了之后怎么办: 持仓报告 + 持仓研判 + 事件解读 */}
        {tab === 'holding' && (
          <div>
            <div style={{ display: 'flex', background: PAPER, borderBottom: `1px solid ${LINE}`, padding: '0 8px', overflowX: 'auto' }}>
              {([['position', '持仓报告', '我现在赚还是亏'],
                 ['hold', '持仓研判', '还该不该继续拿'],
                 ['event', '事件解读', '突发消息影响我吗']] as const).map(([mode, label, hint]) => (
                <button key={mode} onClick={() => setHoldingMode(mode)}
                  style={{ flex: 1, minWidth: 0, padding: '9px 6px', background: 'none', border: 'none', borderBottom: holdingMode === mode ? `2px solid ${THEME}` : '2px solid transparent', cursor: 'pointer', fontFamily: SERIF }}>
                  <div style={{ fontSize: 13, fontWeight: holdingMode === mode ? 700 : 400, color: holdingMode === mode ? THEME : INK_F }}>{label}</div>
                  <div style={{ fontSize: 9, color: INK_F, marginTop: 2, opacity: holdingMode === mode ? 0.9 : 0.6 }}>{hint}</div>
                </button>
              ))}
            </div>
            {holdingMode === 'position' && token && <PortfolioMobileTab token={token} />}
            {holdingMode === 'position' && !token && <div style={{ padding: '60px 20px', textAlign: 'center', color: INK_F, fontSize: 13 }}>请先登录查看持仓报告</div>}
            {holdingMode === 'hold' && <HoldJudgeTab shared={researchStock} stocks={stocks} quotes={quotes} />}
            {holdingMode === 'event' && token && <EventAnalysisTab token={token} shared={researchStock} />}
            {holdingMode === 'event' && !token && <div style={{ padding: '60px 20px', textAlign: 'center', color: INK_F, fontSize: 13 }}>请先登录使用事件解读</div>}
          </div>
        )}

        {/* ④ 挖掘 —— 找下一只 + 找入场时点(持仓研判/事件解读已移至 ③持仓) */}
        {tab === 'kpred' && (
          <div>
            {/* 子 Tab 切换 */}
            <div style={{ display: 'flex', background: PAPER, borderBottom: `1px solid ${LINE}`, padding: '0 8px', overflowX: 'auto' }}>
              {([['assistant', '助手', '不知道用哪个就问'],
                 ['research', '深度研究', '这只值不值得买'],
                 ['scout', '一手情报', '别人还不知道的动态'],
                 ['kpred', '量化择时', '什么时候买合适']] as const).map(([mode, label, hint]) => (
                <button key={mode} onClick={() => setResearchMode(mode)}
                  style={{ flex: 1, minWidth: 0, padding: '9px 6px', background: 'none', border: 'none', borderBottom: researchMode === mode ? `2px solid ${THEME}` : '2px solid transparent', cursor: 'pointer', fontFamily: SERIF }}>
                  <div style={{ fontSize: 13, fontWeight: researchMode === mode ? 700 : 400, color: researchMode === mode ? THEME : INK_F }}>{label}</div>
                  <div style={{ fontSize: 9, color: INK_F, marginTop: 2, opacity: researchMode === mode ? 0.9 : 0.6 }}>{hint}</div>
                </button>
              ))}
            </div>
            {researchMode === 'assistant' && (
              <AssistantSwitcher
                token={token ?? ''}
                stocks={stocks}
                quotes={quotes}
                shared={researchStock}
                onSwitchMode={m => {
                  if (m === 'hold' || m === 'event') { setHoldingMode(m); setTab('holding') }
                  else setResearchMode(m)
                }}
              />
            )}
            {researchMode === 'research' && <>
              <ResearchDeepTab token={token ?? ''} stocks={stocks} quotes={quotes} shared={researchStock} />
              <NextStepCTA current="research" onSwitchMode={m => setResearchMode(m)} />
            </>}
            {researchMode === 'kpred' && <>
              <KPredMobileTab token={token ?? ''} shared={researchStock} stocks={stocks} quotes={quotes} />
              <NextStepCTA current="kpred" onSwitchMode={m => setResearchMode(m)} />
            </>}
            {researchMode === 'scout' && <>
              <ScoutTab token={token ?? ''} shared={researchStock} stocks={stocks} quotes={quotes} />
              <NextStepCTA current="scout" onSwitchMode={m => setResearchMode(m)} />
            </>}
            {/* hold / event 已移至 ③持仓 tab */}
          </div>
        )}

        {/* 发现 */}
        {tab === 'discover' && <DiscoverTab
          procurement={procurement}
          loading={procurementLoading}
          opportunities={opportunities}
          oppsLoading={oppsLoading}
          userProfile={userProfile}
          watchlistCodes={new Set(stocks.map(s => s.code))}
          onResearch={(symbol, name) => {
            setRsSelectedCode(symbol)
            setRsSelectedName(name)
            setRsInput(name)
            setTab('kpred')
            setResearchMode('research')
          }}
          onAddWatchlist={async (symbol, name) => {
            if (!token) return false
            try {
              const r = await fetch('/api/watchlist', { method: 'POST', headers: authH(), body: JSON.stringify({ code: symbol, name, market: 'A', exchange: symbol.startsWith('6') ? 'SH' : 'SZ', asset_type: 'stock' }) })
              if (!r.ok) return false
              await loadWatchlist()
              return true
            } catch { return false }
          }}
          onOpenPreference={() => setTab('profile')}
        />}

        {/* 我的板块偏好 */}
        {tab === 'profile' && (
          <>
            <div style={{ background: PAPER, borderBottom: `1px solid ${LINE}`, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
              <button onClick={() => setTab('mine')} style={{ background: PAPER2, border: 'none', borderRadius: 8, padding: '6px 12px', fontSize: 13, color: INK_S, cursor: 'pointer' }}>‹ 我的</button>
              <span style={{ fontSize: 14, color: INK, fontWeight: 600, fontFamily: SERIF }}>我的板块偏好</span>
            </div>
            <div style={{ padding: '16px 12px' }}>
              {userProfile && userProfile.risk_tolerance ? (
                <>
                  <div style={{ background: PAPER, borderRadius: 14, border: `1px solid ${LINE}`, padding: '20px', marginBottom: 12 }}>
                    <div style={{ fontSize: 12, color: THEME, fontWeight: 600, letterSpacing: 0.5, marginBottom: 14 }}>当前设置</div>
                    {(() => {
                      const rL: Record<string,string> = { conservative: '保守型', balanced: '平衡型', aggressive: '进取型' }
                      const hL: Record<string,string> = { short: '短线（<1年）', medium: '中线（1-3年）', long: '长线（>3年）' }
                      const sL: Record<string,string> = { tech: '科技', consumer: '消费', energy: '能源', finance: '金融', medical: '医药', balanced: '均衡' }
                      return [
                        { label: '风险承受度', value: rL[userProfile.risk_tolerance] || userProfile.risk_tolerance },
                        { label: '投资周期', value: hL[userProfile.holding_period] || userProfile.holding_period },
                        { label: '偏好板块', value: (userProfile.focus_sectors || []).map(s => sL[s.trim()] || s.trim()).filter(Boolean).join(' · ') },
                      ]
                    })().map(({ label, value }) => (
                      <div key={label} style={{ display: 'flex', alignItems: 'center', paddingBottom: 12, marginBottom: 12, borderBottom: `1px solid ${PAPER2}` }}>
                        <span style={{ fontSize: 13, color: INK_F, width: 80, flexShrink: 0 }}>{label}</span>
                        <span style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{value || '--'}</span>
                      </div>
                    ))}
                    <button onClick={() => {
                      if (userProfile) {
                        setSurveyRisk(userProfile.risk_tolerance || '')
                        setSurveyHorizon(userProfile.holding_period || '')
                        setSurveySectors(Array.isArray(userProfile.focus_sectors) ? userProfile.focus_sectors : [])
                      }
                      setShowProfileSurvey(true)
                    }} style={{ width: '100%', padding: '12px 0', background: PAPER2, border: `1px solid ${LINE}`, borderRadius: 10, fontSize: 14, color: INK_S, cursor: 'pointer', marginTop: 4 }}>
                      修改偏好
                    </button>
                  </div>
                  <div style={{ padding: '12px 14px', background: PAPER2, borderRadius: 12, fontSize: 12, color: INK_F, lineHeight: 1.7 }}>
                    ℹ 完善的板块偏好有助于「发现」为你匹配更精准的产业链机会。
                  </div>
                </>
              ) : (
                <div style={{ textAlign: 'center', padding: '60px 20px 40px' }}>
                  <div style={{ fontSize: 36, marginBottom: 16 }}>🎯</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF, marginBottom: 8 }}>尚未填写板块偏好</div>
                  <div style={{ fontSize: 13, color: INK_F, marginBottom: 24 }}>完善偏好后，发现将为你<br />推荐匹配的产业链机会</div>
                  <button onClick={() => setShowProfileSurvey(true)}
                    style={{ padding: '13px 36px', background: THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>
                    立即填写偏好
                  </button>
                </div>
              )}
            </div>
          </>
        )}

        {/* ⑤ 回测 —— 每人自己的股票池与判定参数 */}
        {tab === 'backtest' && (
          <BacktestTab token={token ?? ''} mode={backtestMode} setMode={setBacktestMode} stocks={stocks} />
        )}

        {/* 我的 */}
        {tab === 'mine' && (
          <div style={{ padding: '16px 12px' }}>
            {/* 账号头像卡片 */}
            <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12, padding: '20px 20px', display: 'flex', alignItems: 'center', gap: 18 }}>
              <img
                src="/avatar-deer.png"
                alt="猎鹿人"
                onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                style={{ width: 88, height: 88, borderRadius: '50%', objectFit: 'cover', flexShrink: 0, boxShadow: '0 4px 14px rgba(50,35,10,.22)', background: '#3a3a2a' }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: THEME, fontFamily: SERIF }}>agentpit.io · 猎鹿人 · Hunter</div>
                {/* 可编辑用户名 */}
                {editingName ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                    <input
                      autoFocus
                      value={nameInput}
                      onChange={e => setNameInput(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && saveDisplayName()}
                      maxLength={30}
                      style={{ flex: 1, fontSize: 14, padding: '4px 8px', border: `1px solid ${THEME}`, borderRadius: 8, background: BG, color: INK, outline: 'none', minWidth: 0 }}
                    />
                    <button
                      onClick={saveDisplayName}
                      style={{ flexShrink: 0, padding: '4px 12px', background: THEME, color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}
                    >保存</button>
                  </div>
                ) : (
                  <div style={{ marginTop: 6 }}>
                    <div
                      onClick={() => { setNameInput(displayName || userEmail.split('@')[0]); setEditingName(true) }}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}
                    >
                      <span style={{ fontSize: 14, color: INK, fontWeight: 500 }}>
                        {displayName || userEmail.split('@')[0]}
                      </span>
                      <span style={{ fontSize: 11, color: COPPER2 }}>✎</span>
                    </div>
                    {nameSaved && (
                      <div style={{ fontSize: 11, color: '#4caf50', marginTop: 2 }}>已保存 ✓</div>
                    )}
                  </div>
                )}
                {/* 注册邮箱 */}
                {userEmail && !userEmail.endsWith('@test') && (
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 3 }}>{userEmail}</div>
                )}
              </div>
            </div>

            {/* 账号等级 + 有效期 */}
            <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12, overflow: 'hidden' }}>
              <div style={{ padding: '14px 18px', borderBottom: `1px solid ${PAPER2}`, display: 'flex', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: INK_F, flex: 1 }}>账号等级</span>
                {membership.level === 'pro' ? (
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#fff', background: `linear-gradient(135deg, ${THEME} 0%, ${COPPER2} 100%)`, padding: '3px 10px', borderRadius: 12, letterSpacing: 0.5 }}>Pro 会员</span>
                ) : (
                  <span style={{ fontSize: 13, color: INK_F }}>免费用户</span>
                )}
              </div>
              <div style={{ padding: '14px 18px', display: 'flex', alignItems: 'center' }}>
                <span style={{ fontSize: 13, color: INK_F, flex: 1 }}>有效期</span>
                {membership.expiresAt && membership.level === 'pro' ? (
                  <span style={{ fontSize: 13, color: INK, textAlign: 'right' }}>
                    {membership.expiresAt.slice(0, 10)}
                    <span style={{ fontSize: 11, color: INK_F, marginLeft: 6 }}>剩余 {membership.daysLeft} 天</span>
                  </span>
                ) : membership.expiresAt ? (
                  <span style={{ fontSize: 13, color: '#e74c3c' }}>已过期</span>
                ) : (
                  <span style={{ fontSize: 13, color: INK_F }}>未开通</span>
                )}
              </div>
            </div>

            {/* 电脑版入口（两个链接） */}
            <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12, overflow: 'hidden' }}>
              <a href="https://hunter.agentpit.io" target="_blank" style={{ display: 'flex', alignItems: 'center', padding: '16px 18px', textDecoration: 'none', borderBottom: `1px solid ${PAPER2}` }}>
                <span style={{ marginRight: 14, display: 'flex', alignItems: 'center' }}><IconLaptop /></span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, color: INK }}>进入 agentpit 猎鹿人</div>
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>hunter.agentpit.io · 完整功能电脑版</div>
                </div>
                <span style={{ color: LINE, fontSize: 16 }}>›</span>
              </a>
              <a href="https://agentpit.io" target="_blank" style={{ display: 'flex', alignItems: 'center', padding: '16px 18px', textDecoration: 'none' }}>
                <span style={{ marginRight: 14, display: 'flex', alignItems: 'center' }}><IconGlobe /></span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 15, color: INK }}>进入 agentpit.io 财经智能体平台</div>
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>agentpit.io · 更多 AI 智能体</div>
                </div>
                <span style={{ color: LINE, fontSize: 16 }}>›</span>
              </a>
            </div>

            {/* 绑定已有账号 - 仅自动注册账号显示 */}
            {userEmail.endsWith('@test') && (
              <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12, overflow: 'hidden' }}>
                <div onClick={() => { setShowBind(!showBind); setBindMsg('') }}
                  style={{ display: 'flex', alignItems: 'center', padding: '16px 18px', cursor: 'pointer' }}>
                  <span style={{ marginRight: 14, display: 'flex', alignItems: 'center' }}><IconLink /></span>
                  <span style={{ flex: 1, fontSize: 15, color: INK }}>绑定已有账号</span>
                  <span style={{ color: LINE, fontSize: 16 }}>{showBind ? '∧' : '›'}</span>
                </div>
                {showBind && (
                  <div style={{ padding: '0 18px 18px', borderTop: `1px solid ${PAPER2}` }}>
                    <div style={{ fontSize: 12, color: INK_S, margin: '12px 0 10px' }}>
                      输入已有账号，合并后即可使用完整权限
                    </div>
                    <input value={bindEmail} onChange={e => setBindEmail(e.target.value)}
                      placeholder="邮箱" type="email"
                      style={{ width: '100%', padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, marginBottom: 8, boxSizing: 'border-box', background: BG, color: INK }} />
                    <input value={bindPassword} onChange={e => setBindPassword(e.target.value)}
                      placeholder="密码" type="password"
                      style={{ width: '100%', padding: '10px 12px', border: `1px solid ${LINE}`, borderRadius: 8, fontSize: 14, marginBottom: 10, boxSizing: 'border-box', background: BG, color: INK }} />
                    {bindMsg && <div style={{ fontSize: 13, color: bindMsg.startsWith('✓') ? '#1fa351' : '#e84040', marginBottom: 8 }}>{bindMsg}</div>}
                    <button disabled={bindLoading}
                      onClick={async () => {
                        if (!bindEmail || !bindPassword) { setBindMsg('请填写邮箱和密码'); return }
                        setBindLoading(true); setBindMsg('')
                        try {
                          const r = await fetch('/api/wx/bind-account', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                            body: JSON.stringify({ email: bindEmail, password: bindPassword }),
                          })
                          const d = await r.json()
                          if (r.ok && d.ok) {
                            localStorage.setItem('hunter_token', d.token)
                            setToken(d.token); setUserEmail(d.email)
                            setBindMsg('✓ 绑定成功，账号已合并'); setShowBind(false)
                          } else { setBindMsg(d.error || '绑定失败') }
                        } catch { setBindMsg('网络错误，请重试') }
                        setBindLoading(false)
                      }}
                      style={{ width: '100%', padding: '12px 0', background: THEME, color: '#fff', border: 'none', borderRadius: 8, fontSize: 15, cursor: bindLoading ? 'not-allowed' : 'pointer', opacity: bindLoading ? 0.7 : 1 }}>
                      {bindLoading ? '绑定中...' : '确认绑定'}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* 切换市场(保留登录态, 回分流页重选 A股/美港股) */}
            <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12 }}>
              <button onClick={() => {
                try { localStorage.removeItem('hunter_default_end'); } catch { /* ignore */ }
                window.location.href = '/entry?choose=1';
              }} style={{ width: '100%', padding: '16px 0', background: 'none', color: INK, border: 'none', borderRadius: 14, fontSize: 15, cursor: 'pointer', fontWeight: 500 }}>🌐 切换市场（A股 / 美港股）</button>
            </div>

            {/* 退出登录 */}
            <div style={{ background: PAPER, borderRadius: 14, boxShadow: '0 1px 8px rgba(50,35,10,.07)', border: `1px solid ${LINE}`, marginBottom: 12 }}>
              <button onClick={logout} style={{ width: '100%', padding: '16px 0', background: 'none', color: '#e74c3c', border: 'none', borderRadius: 14, fontSize: 15, cursor: 'pointer', fontWeight: 500 }}>退出登录</button>
            </div>

            <div style={{ textAlign: 'center', marginTop: 8, fontSize: 11, color: LINE }}>猎鹿人 · Hunter · agentpit.io</div>
          </div>
        )}
      </div>

      {/* 底部 Tab 栏 */}
      <div style={{ position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: '100%', maxWidth: 480, background: PAPER, borderTop: `1px solid ${LINE}`, display: 'flex', zIndex: 100, paddingBottom: 'env(safe-area-inset-bottom)' }}>
        {TAB_DEFS.map(({ key, label, step, Icon }) => (
          <button key={key} onClick={() => setTab(key)} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0 10px', background: 'none', border: 'none', cursor: 'pointer' }}>
            <Icon active={tab === key} />
            <span style={{ fontSize: 10, marginTop: 3, color: tab === key ? THEME : INK_F, fontWeight: tab === key ? 600 : 400 }}>
              {step && <span style={{ fontSize: 9, opacity: tab === key ? 0.9 : 0.5, marginRight: 1 }}>{step}</span>}{label}
            </span>
            {tab === key && <div style={{ width: 20, height: 2, background: THEME, borderRadius: 1, marginTop: 3 }} />}
          </button>
        ))}
      </div>

      {/* 搜索弹层 */}
      {showSearch && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', zIndex: 200, display: 'flex', alignItems: 'flex-end' }} onClick={e => { if (e.target === e.currentTarget) { setShowSearch(false); setSearchQ(''); setSearchRes([]) } }}>
          <div style={{ background: PAPER, width: '100%', maxWidth: 480, margin: '0 auto', borderRadius: '18px 18px 0 0', padding: '20px 16px 30px', maxHeight: '72vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
              <span style={{ fontSize: 16, fontWeight: 700, color: INK, fontFamily: SERIF }}>搜索并添加自选股</span>
              <button onClick={() => { setShowSearch(false); setSearchQ(''); setSearchRes([]) }} style={{ marginLeft: 'auto', background: PAPER2, border: 'none', color: INK_S, fontSize: 16, width: 28, height: 28, borderRadius: '50%', cursor: 'pointer' }}>✕</button>
            </div>
            <input autoFocus value={searchQ} onChange={e => setSearchQ(e.target.value)} placeholder="股票名称或代码，如：贵州茅台 / 600519" style={{ width: '100%', height: 46, padding: '0 14px', boxSizing: 'border-box', border: `1px solid ${LINE}`, borderRadius: 12, fontSize: 14, outline: 'none', background: BG, color: INK }} />
            <div style={{ fontSize: 11, color: INK_F, marginTop: 8, textAlign: 'center' }}>当前仅支持A股，港股/美股系统正在上线中，敬请期待</div>
            <div style={{ overflowY: 'auto', marginTop: 8, flex: 1 }}>
              {searchQ.trim() && !searchRes.length && <div style={{ textAlign: 'center', padding: '40px 0', color: INK_F, fontSize: 13 }}>未找到结果</div>}
              {!searchQ.trim() && <div style={{ textAlign: 'center', padding: '30px 0', color: INK_F, fontSize: 13 }}>输入关键词搜索</div>}
              {searchRes.map(s => {
                const inList = stocks.some(st => st.code === s.code)
                const unsupported = s.market === 'HK' || s.market === 'US'
                const marketLabel = s.market === 'HK' ? '港股' : s.market === 'US' ? '美股' : 'A股'
                return (
                  <div key={s.code} style={{ display: 'flex', alignItems: 'center', padding: '12px 4px', borderBottom: `1px solid ${PAPER2}`, opacity: unsupported ? 0.55 : 1 }}>
                    <div style={{ flex: 1 }}>
                      <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>{s.name}</span>
                      <span style={{ fontSize: 12, color: INK_F, marginLeft: 8 }}>{s.code} · {marketLabel}</span>
                      {unsupported && <div style={{ fontSize: 11, color: '#d97706', marginTop: 2 }}>{marketLabel}暂不支持，上线中</div>}
                    </div>
                    {unsupported
                      ? <span style={{ fontSize: 12, color: '#aaa', padding: '5px 12px', border: '1px solid #ddd', borderRadius: 8 }}>暂不支持</span>
                      : inList
                        ? <span style={{ fontSize: 12, color: THEME, padding: '5px 12px', border: `1px solid ${THEME}`, borderRadius: 8 }}>已添加</span>
                        : <button onClick={() => addStock(s)} disabled={addingCode === s.code} style={{ fontSize: 13, color: '#fff', background: addingCode === s.code ? INK_F : THEME, border: 'none', borderRadius: 8, padding: '6px 16px', cursor: 'pointer' }}>{addingCode === s.code ? '添加中' : '+ 添加'}</button>}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* 用户画像问卷弹窗 */}
      {showProfileSurvey && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 300, background: 'rgba(30,25,15,0.78)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px 16px' }}>
          <div style={{ background: PAPER, borderRadius: 20, padding: '28px 20px 24px', width: '100%', maxWidth: 420, boxShadow: '0 8px 40px rgba(0,0,0,0.3)' }}>
            <div style={{ fontSize: 11, color: THEME, letterSpacing: 1, fontWeight: 600, marginBottom: 6 }}>板块偏好设置</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: INK, fontFamily: SERIF, marginBottom: 4 }}>告诉我你的偏好</div>
            <div style={{ fontSize: 13, color: INK_F, marginBottom: 24 }}>让「发现」板块为你匹配更精准的机会</div>

            {/* Q1: 风险承受度 */}
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: INK_S, marginBottom: 10 }}>风险承受度</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {([['保守型','conservative'],['平衡型','balanced'],['进取型','aggressive']] as const).map(([label, val]) => (
                  <button key={val} onClick={() => setSurveyRisk(val)}
                    style={{ flex: 1, padding: '11px 4px', borderRadius: 10, border: `1.5px solid ${surveyRisk === val ? THEME : LINE}`, background: surveyRisk === val ? 'rgba(176,106,50,0.1)' : PAPER, color: surveyRisk === val ? THEME : INK_S, fontSize: 13, cursor: 'pointer', fontWeight: surveyRisk === val ? 600 : 400 }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Q2: 投资周期 */}
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: INK_S, marginBottom: 10 }}>投资周期</div>
              <div style={{ display: 'flex', gap: 8 }}>
                {([['短线','short','<1年'],['中线','medium','1-3年'],['长线','long','>3年']] as [string,string,string][]).map(([label, val, sub]) => (
                  <button key={val} onClick={() => setSurveyHorizon(val)}
                    style={{ flex: 1, padding: '8px 4px', borderRadius: 10, border: `1.5px solid ${surveyHorizon === val ? THEME : LINE}`, background: surveyHorizon === val ? 'rgba(176,106,50,0.1)' : PAPER, color: surveyHorizon === val ? THEME : INK_S, fontSize: 13, cursor: 'pointer', textAlign: 'center' }}>
                    <div style={{ fontWeight: surveyHorizon === val ? 600 : 400 }}>{label}</div>
                    <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{sub}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Q3: 偏好板块 */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: INK_S, marginBottom: 10 }}>偏好板块（可多选）</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {([['科技','tech'],['消费','consumer'],['能源','energy'],['金融','finance'],['医药','medical'],['均衡','balanced']] as const).map(([label, val]) => {
                  const sel = surveySectors.includes(val)
                  return (
                    <button key={val} onClick={() => setSurveySectors(prev => sel ? prev.filter(x => x !== val) : [...prev, val])}
                      style={{ padding: '8px 18px', borderRadius: 20, border: `1.5px solid ${sel ? THEME : LINE}`, background: sel ? 'rgba(176,106,50,0.1)' : PAPER, color: sel ? THEME : INK_S, fontSize: 13, cursor: 'pointer', fontWeight: sel ? 600 : 400 }}>
                      {label}
                    </button>
                  )
                })}
              </div>
            </div>

            <button
              disabled={!surveyRisk || !surveyHorizon || surveySectors.length === 0 || profileSaving}
              onClick={async () => {
                setProfileSaving(true)
                let saved = false
                try {
                  const r = await fetch('/api/user/preference', {
                    method: 'PUT',
                    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ risk_tolerance: surveyRisk, holding_period: surveyHorizon, focus_sectors: surveySectors }),
                  })
                  if (r.ok) {
                    const d = await r.json()
                    setUserProfile(d)
                    saved = true
                    // 保存成功 → 立刻拉最新匹配机会（loadOpportunities 内含节流，重复调用无副作用）
                    loadOpportunities()
                  }
                } catch {}
                setProfileSaving(false)
                setShowProfileSurvey(false)
                // 保存成功 → 自动切到「发现」tab；用户能直接看到基于新偏好的匹配结果
                if (saved) setTab('discover')
              }}
              style={{ width: '100%', padding: '14px 0', background: (!surveyRisk || !surveyHorizon || surveySectors.length === 0) ? LINE : THEME, color: '#fff', border: 'none', borderRadius: 12, fontSize: 15, fontWeight: 600, cursor: 'pointer', marginBottom: 10, opacity: profileSaving ? 0.7 : 1 }}>
              {profileSaving ? '保存中...' : '完成设置'}
            </button>
            <button onClick={() => setShowProfileSurvey(false)}
              style={{ width: '100%', padding: '10px 0', background: 'none', border: 'none', color: INK_F, fontSize: 13, cursor: 'pointer' }}>
              跳过，稍后再说
            </button>
          </div>
        </div>
      )}

      {/* 功能地图 sheet */}
      <SiteMapSheet
        open={showSiteMap}
        onClose={() => setShowSiteMap(false)}
        onNav={(action) => {
          setShowSiteMap(false)
          action({ setTab, setResearchMode, setPortfolioMode, setHoldingMode, setShowSearch, goExternal: (url: string) => { window.location.href = url } })
        }}
      />
    </div>
  )
}

// ── 功能地图 Sheet ──────────────────────────────────────────────────────────
interface NavCtx {
  setTab: (t: Tab) => void
  setResearchMode: (m: 'assistant' | 'kpred' | 'scout' | 'research' | 'hold' | 'event') => void
  setPortfolioMode: (m: 'overview' | 'list' | 'position' | 'alert') => void
  setHoldingMode: (m: 'position' | 'hold' | 'event') => void
  setShowSearch: (v: boolean) => void
  goExternal: (url: string) => void
}
interface SiteMapItem { id: string; icon: string; title: string; desc?: string; badge?: string; action: (ctx: NavCtx) => void }
interface SiteMapGroup { id: string; icon: string; title: string; items: SiteMapItem[] }

const SITE_MAP: SiteMapGroup[] = [
  {
    id: 'portfolio', icon: '📊', title: '持仓中心',
    items: [
      { id: 'wl-overview', icon: '📰', title: '综合概览', desc: 'AI 分级 · 每股一手情报 · Kronos 预测', action: c => { c.setTab('watchlist'); c.setPortfolioMode('overview') } },
      { id: 'wl-list',     icon: '📋', title: '自选股清单', desc: '实时行情 · 价格提醒', action: c => { c.setTab('watchlist'); c.setPortfolioMode('list') } },
      { id: 'wl-report',   icon: '📈', title: '持仓报告',   desc: '成本盈亏 · 持仓明细', action: c => { c.setTab('holding'); c.setHoldingMode('position') } },
      { id: 'wl-alert',    icon: '🔔', title: '提醒设置',   desc: '定时推送 · 消息触发', action: c => { c.setTab('watchlist'); c.setPortfolioMode('alert') } },
      { id: 'wl-add',      icon: '➕', title: '添加自选股', action: c => { c.setTab('watchlist'); c.setPortfolioMode('list'); c.setShowSearch(true) } },
    ],
  },
  {
    id: 'research', icon: '🔬', title: '研究',
    items: [
      { id: 'rs-deep',  icon: '📊', title: '深度研究', desc: 'AI 分析师 · 3 分钟建立认知', action: c => { c.setTab('kpred'); c.setResearchMode('research') } },
      { id: 'rs-scout', icon: '🔍', title: '一手情报', desc: '机构调研 · AI 汇总（Gemini 3.5）', action: c => { c.setTab('kpred'); c.setResearchMode('scout') } },
      { id: 'rs-kpred', icon: '🎯', title: '量化择时', desc: 'Kronos · 清华金融大模型 · 5 档评级', action: c => { c.setTab('kpred'); c.setResearchMode('kpred') } },
      { id: 'rs-hold',  icon: '🛡', title: '持仓研判', badge: 'NEW', desc: '原持仓管家 · Bull/Bear 辩论决策', action: c => { c.setTab('holding'); c.setHoldingMode('hold') } },
      { id: 'rs-event', icon: '⚡', title: '事件解读', desc: '突发事件对持仓的影响分析', action: c => { c.setTab('holding'); c.setHoldingMode('event') } },
    ],
  },
  {
    id: 'discover', icon: '🔎', title: '发现',
    items: [
      { id: 'dc-opps', icon: '💡', title: '板块匹配机会', desc: '29 条产业链 · 5 大板块', action: c => c.setTab('discover') },
      { id: 'dc-proc', icon: '📢', title: '政采实时信号', action: c => c.setTab('discover') },
    ],
  },
  {
    id: 'profile', icon: '👤', title: '我的',
    items: [
      { id: 'pf-pc',     icon: '💻', title: '进入电脑版', action: () => window.open('https://hunter.agentpit.io', '_blank') },
      { id: 'pf-pref',   icon: '🎯', title: '我的板块偏好', action: c => c.setTab('profile') },
    ],
  },
]

const QUICK_ACTIONS: SiteMapItem[] = [
  { id: 'qa-hold',   icon: '🛡', title: '持仓研判',     action: c => { c.setTab('holding'); c.setHoldingMode('hold') } },
  { id: 'qa-deep',   icon: '📊', title: '深度研究',     action: c => { c.setTab('kpred'); c.setResearchMode('research') } },
  { id: 'qa-add',    icon: '➕', title: '添加自选股',   action: c => { c.setTab('watchlist'); c.setPortfolioMode('list'); c.setShowSearch(true) } },
  { id: 'qa-alert',  icon: '🔔', title: '提醒设置',     action: c => { c.setTab('watchlist'); c.setPortfolioMode('alert') } },
]

function SiteMapSheet({ open, onClose, onNav }: { open: boolean; onClose: () => void; onNav: (action: SiteMapItem['action']) => void }) {
  if (!open) return null
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 200, display: 'flex', alignItems: 'flex-end' }} onClick={onClose}>
      <div style={{ width: '100%', maxWidth: 480, margin: '0 auto', background: PAPER, borderRadius: '20px 20px 0 0', maxHeight: '88vh', overflow: 'auto', padding: '18px 16px calc(20px + env(safe-area-inset-bottom, 0px))', boxShadow: '0 -4px 20px rgba(0,0,0,0.15)' }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, paddingBottom: 12, borderBottom: `1px solid ${PAPER2}` }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: INK, fontFamily: SERIF, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>🗺</span><span>功能地图</span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 22, color: INK_F, cursor: 'pointer', lineHeight: 1, padding: '0 4px' }}>×</button>
        </div>

        {SITE_MAP.map(group => (
          <div key={group.id} style={{ marginBottom: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: THEME, marginBottom: 8, fontFamily: SERIF }}>
              {group.icon} {group.title}
            </div>
            <div style={{ background: BG, borderRadius: 10, overflow: 'hidden' }}>
              {group.items.map((it, i) => (
                <div key={it.id} onClick={() => onNav(it.action)}
                  style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 12px', borderTop: i === 0 ? 'none' : `1px solid ${PAPER2}`, cursor: 'pointer' }}>
                  <span style={{ fontSize: 17, width: 26, textAlign: 'center', flexShrink: 0 }}>{it.icon}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, color: INK, display: 'flex', alignItems: 'center', gap: 6 }}>
                      {it.title}
                      {it.badge && <span style={{ background: COPPER2, color: '#fff', fontSize: 9, padding: '1px 5px', borderRadius: 3, fontWeight: 700 }}>{it.badge}</span>}
                    </div>
                    {it.desc && <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{it.desc}</div>}
                  </div>
                  <span style={{ color: LINE, fontSize: 16, flexShrink: 0 }}>›</span>
                </div>
              ))}
            </div>
          </div>
        ))}

        <div style={{ paddingTop: 8, borderTop: `1px solid ${PAPER2}` }}>
          <div style={{ fontSize: 12, color: INK_F, marginBottom: 8, fontWeight: 600 }}>⚡ 常用操作</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            {QUICK_ACTIONS.map(qa => (
              <button key={qa.id} onClick={() => onNav(qa.action)}
                style={{ padding: '11px', border: `1px solid ${LINE}`, borderRadius: 10, background: PAPER, fontSize: 13, color: INK, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                <span>{qa.icon}</span><span>{qa.title}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
