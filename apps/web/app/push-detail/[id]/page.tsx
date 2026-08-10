'use client'
import { useEffect, useState, useRef, useCallback } from 'react'
import { useParams } from 'next/navigation'

// ── 推送类型元数据 ─────────────────────────────────────────────────────────
const PUSH_TYPE_LABELS: Record<string, string> = {
  watchlist_morning:    '自选股早报',
  close_review:         '盘后复盘',
  news_today:           '今日新闻速递',
  fundflow_topn:        '资金流向 TopN',
  weekly_summary:       '周度持仓汇总',
  custom:               '自定义推送',
  price_alert:          '价格提醒',
  price_alert_analysis: '多智能体归因分析',
}

const PUSH_TYPE_KICKER: Record<string, string> = {
  watchlist_morning:    '会员早报',
  close_review:         '盘后复盘',
  news_today:           '今日新闻速递',
  fundflow_topn:        '资金流向 TOPN',
  weekly_summary:       '周度持仓汇总',
  custom:               '自定义推送',
  price_alert:          '价格提醒',
  price_alert_analysis: '归因分析',
}

const PUSH_TYPE_PILL_DESC: Record<string, string> = {
  watchlist_morning:    '猎鹿会员 · 开盘快报',
  close_review:         '盘后收盘总结',
  news_today:           '今日新闻 · 智能精选',
  fundflow_topn:        '盘中实时数据快照',
  weekly_summary:       '周度汇总 · 持仓表现',
  price_alert:          '价格触发提醒',
  price_alert_analysis: '多智能体深度归因',
}

// close_review 与 watchlist_morning 同为橄榄绿系；fundflow_topn 深森林绿
const PUSH_TYPE_HEADER_BG: Record<string, string> = {
  watchlist_morning:    'linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%)',
  close_review:         'linear-gradient(160deg,#28300C 0%,#384218 55%,#2C340E 100%)',
  news_today:           'linear-gradient(160deg,#0D2A2E 0%,#123438 55%,#0F2E32 100%)',
  fundflow_topn:        'linear-gradient(160deg,#0E2A14 0%,#183520 55%,#122A18 100%)',
  weekly_summary:       'linear-gradient(160deg,#1E1A30 0%,#2A2440 55%,#1C1A2E 100%)',
  price_alert:          'linear-gradient(160deg,#2A1A10 0%,#3C2415 55%,#2E1C10 100%)',
  price_alert_analysis: 'linear-gradient(160deg,#1A1020 0%,#28183A 55%,#1C1225 100%)',
}
const DEFAULT_HEADER_BG = 'linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%)'

const PUSH_TYPE_ACCENT: Record<string, string> = {
  news_today:           '#127A7E',
  fundflow_topn:        '#B8862A',
  price_alert:          '#C05A20',
  price_alert_analysis: '#7A52B0',
}
const DEFAULT_ACCENT = '#B06A32'

const UP    = '#A4332B'
const DOWN  = '#3F6B40'
const THEME = '#B06A32'

// ── Header SVG 图标 ────────────────────────────────────────────────────────

function SvgBarChartColored() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none">
      <rect x="3" y="9" width="5" height="12" rx="1" fill="#A4332B" />
      <rect x="9.5" y="5" width="5" height="16" rx="1" fill="#3F6B40" />
      <rect x="16" y="12" width="5" height="9" rx="1" fill="#D4925A" />
      <line x1="2" y1="22" x2="22" y2="22" stroke="rgba(255,253,249,0.8)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}
function SvgTrendBox() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.92)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <polyline points="7,16 11,11 15,13 17,10" />
      <polyline points="14,9 17,9 17,12" />
    </svg>
  )
}
function SvgNewspaper() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.92)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="7" y1="8" x2="17" y2="8" />
      <rect x="7" y="11" width="4" height="4" rx="0.5" />
      <rect x="13" y="11" width="4" height="4" rx="0.5" />
      <line x1="7" y1="17" x2="17" y2="17" />
    </svg>
  )
}
// fundflow_topn 专属：柱图 + 上升趋势线复合图标
function SvgFundFlowIcon() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none">
      <rect x="2" y="13" width="4" height="8" rx="0.8" fill="rgba(255,253,249,0.75)" />
      <rect x="8" y="9" width="4" height="12" rx="0.8" fill="rgba(255,253,249,0.75)" />
      <rect x="14" y="11" width="4" height="10" rx="0.8" fill="rgba(255,253,249,0.75)" />
      <polyline points="2,18 8,12 14,15 20,8"
        stroke="rgba(212,146,90,1)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="17,7 20,8 19,11"
        stroke="rgba(212,146,90,1)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
function SvgCalendarCheck() {
  return (
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.92)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <polyline points="8,15 11,18 16,13" />
    </svg>
  )
}

const PUSH_TYPE_SVG: Record<string, React.ReactNode> = {
  watchlist_morning:    <SvgBarChartColored />,
  close_review:         <SvgTrendBox />,
  news_today:           <SvgNewspaper />,
  fundflow_topn:        <SvgFundFlowIcon />,
  weekly_summary:       <SvgCalendarCheck />,
  price_alert:          <SvgSecChart />,
  price_alert_analysis: <SvgSecAI />,
}

// close_review 标题前内嵌小图标（彩色柱图 app-icon 风格）
function SvgTitleBarChip() {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 26, height: 26, borderRadius: 6, background: '#FFFDF9',
      verticalAlign: 'middle', marginRight: 8, flexShrink: 0,
    }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
        <rect x="2" y="9" width="6" height="13" rx="1" fill="#A4332B" />
        <rect x="9" y="5" width="6" height="17" rx="1" fill="#3F6B40" />
        <rect x="16" y="11" width="6" height="11" rx="1" fill="#D4925A" />
      </svg>
    </span>
  )
}

// ── 内容区小图标 ───────────────────────────────────────────────────────────

function SvgCalendarSm({ color = 'rgba(255,253,249,0.9)' }: { color?: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}>
      <rect x="3" y="4" width="18" height="17" rx="2" />
      <line x1="3" y1="10" x2="21" y2="10" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="16" y1="2" x2="16" y2="6" />
    </svg>
  )
}

// 小节标题图标
function SvgSecAI() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  )
}
function SvgSecNews() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <line x1="7" y1="8" x2="17" y2="8" />
      <line x1="7" y1="13" x2="17" y2="13" />
      <line x1="7" y1="18" x2="12" y2="18" />
    </svg>
  )
}
function SvgSecChart() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3,17 8,10 13,13 19,6" />
      <line x1="3" y1="20" x2="21" y2="20" />
    </svg>
  )
}
function SvgSecList() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="9" y1="6" x2="20" y2="6" />
      <line x1="9" y1="12" x2="20" y2="12" />
      <line x1="9" y1="18" x2="20" y2="18" />
      <circle cx="4" cy="6" r="1.5" fill="rgba(255,253,249,0.95)" stroke="none" />
      <circle cx="4" cy="12" r="1.5" fill="rgba(255,253,249,0.95)" stroke="none" />
      <circle cx="4" cy="18" r="1.5" fill="rgba(255,253,249,0.95)" stroke="none" />
    </svg>
  )
}
function SvgSecRocket() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2s4 3 4 10v4l-4 4-4-4v-4c0-7 4-10 4-10z" />
      <circle cx="12" cy="10" r="1.5" fill="rgba(255,253,249,0.95)" stroke="none" />
      <path d="M8 14c-2 .5-3 2-3 3l2-1" /><path d="M16 14c2 .5 3 2 3 3l-2-1" />
    </svg>
  )
}

function getSectionIcon(text: string): React.ReactNode {
  if (/真源|产业链/.test(text))                   return <SvgTrueSource />
  if (/AI|撰写|重要资讯/.test(text))              return <SvgSecAI />
  if (/新闻/.test(text))                          return <SvgSecNews />
  if (/净流入|净流出|TOP|主力|资金/.test(text))   return <SvgSecRocket />
  if (/行情|快闻|快讯|复盘|涨跌|收盘/.test(text)) return <SvgSecChart />
  if (/总结|关注|开盘|要点|摘要|持仓|周度/.test(text)) return <SvgSecList />
  return null
}

// 新闻条目左侧小文档图标
function SvgDocMini() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke={THEME} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="2" width="16" height="20" rx="2" />
      <line x1="8" y1="8" x2="16" y2="8" />
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="18" x2="12" y2="18" />
    </svg>
  )
}
// 行情行左侧趋势小图标（仅 watchlist_morning 使用）
function SvgTrendMini() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke={THEME} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3,17 8,10 13,13 19,6" />
      <line x1="3" y1="20" x2="21" y2="20" />
    </svg>
  )
}
// 建议框灯泡图标
function SvgBulb() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke={THEME} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21h6M12 3a6 6 0 0 1 6 6c0 2.4-1.2 4.5-3 5.7V18H9v-3.3A6 6 0 0 1 6 9a6 6 0 0 1 6-6z" />
    </svg>
  )
}

// 真源信号图标
function SvgTrueSource() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="rgba(255,253,249,0.95)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
      <line x1="11" y1="8" x2="11" y2="14" /><line x1="8" y1="11" x2="14" y2="11" />
    </svg>
  )
}

// 将 **bold** 转换为 <strong> 节点
function renderInlineMd(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  if (parts.length === 1) return text
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) {
          return <strong key={i} style={{ fontWeight: 700 }}>{p.slice(2, -2)}</strong>
        }
        return p
      })}
    </>
  )
}

// ── 行情解析 ───────────────────────────────────────────────────────────────

function parseQuote(line: string) {
  const m = line.match(
    /^(🔴|🟢)\s+(.+?)\s+\((\w+)\)\s+([+-][\d.]+%)\s+((?:HK\$|¥)[\d.]+)\s+\(([+-][\d.]+)\)/
  )
  if (!m) return null
  const [, dot, name, code, pct, price, change] = m
  const isUp = dot === '🔴' || pct.startsWith('+')
  return { name, code, pct, price, change, isUp }
}

function parseNews(line: string) {
  const m = line.match(/^(.+?)\s+\((\w+)\)\s+[—–]\s*(.+?)(\s{2,}(.+?))?$/)
  if (!m) return null
  const [, nameWithEmoji, code, headline, , source] = m
  return { nameWithEmoji: nameWithEmoji.trim(), code, headline: headline.trim(), source: source?.trim() || '' }
}

// ── 行情卡片 ──────────────────────────────────────────────────────────────

function QuoteCard({ q, flow, flowColor, showTrendIcon }: {
  q: NonNullable<ReturnType<typeof parseQuote>>
  flow?: string
  flowColor?: string
  showTrendIcon?: boolean
}) {
  const color = q.isUp ? UP : DOWN
  const arrow = q.isUp ? '▲' : '▼'
  return (
    <div style={s.quoteRow}>
      <div style={{ position: 'absolute', left: 0, top: 6, bottom: 6, width: 4, background: color }} />
      <div style={{ display: 'flex', alignItems: 'center', flex: 1, padding: '12px 16px 12px 14px', gap: 10 }}>
        {showTrendIcon && <div style={{ flexShrink: 0, opacity: 0.7 }}><SvgTrendMini /></div>}
        <div style={s.quoteLeft}>
          <span style={s.qName}>{q.name}</span>
          <span style={s.qCode}>{q.code}</span>
          {flow && (
            <span style={{ fontSize: 11, color: flowColor, marginTop: 2, fontFamily: 'Georgia,monospace', fontWeight: 600 }}>
              {flow}
            </span>
          )}
        </div>
        <div style={s.quoteRight}>
          <span style={{ ...s.qPct, color }}>{arrow} {q.pct}</span>
          <span style={s.qPrice}>
            {q.price}<span style={s.qChange}> ({q.change})</span>
          </span>
        </div>
      </div>
    </div>
  )
}

// ── renderContent ─────────────────────────────────────────────────────────

function renderContent(content: string, accent: string, pushType: string) {
  const lines = content.split('\n')
  const nodes: React.ReactNode[] = []
  let key = 0
  let firstLineSeen = false
  const showTrendIcon = pushType === 'watchlist_morning'

  type PendingQ = { q: NonNullable<ReturnType<typeof parseQuote>> }
  let pendingQ: PendingQ | null = null

  const flushPending = (flow?: string, flowColor?: string) => {
    if (!pendingQ) return
    nodes.push(<QuoteCard key={key++} q={pendingQ.q} flow={flow} flowColor={flowColor} showTrendIcon={showTrendIcon} />)
    pendingQ = null
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue

    if (line === '---') {
      flushPending()
      nodes.push(<div key={key++} style={s.blockGap} />)
      continue
    }

    if (!firstLineSeen) { firstLineSeen = true; continue }

    // ── 净流入/出行（始终显示在卡片外独立行）────────────────
    if (/^净(流入|流出)/.test(line)) {
      flushPending()
      const isIn = line.startsWith('净流入')
      nodes.push(
        <div key={key++} style={{ ...s.flowLine, color: isIn ? UP : DOWN }}>{line}</div>
      )
      continue
    }

    // ── 行情行 ───────────────────────────────────────────────
    const q = parseQuote(line)
    if (q) {
      flushPending()
      pendingQ = { q }
      continue
    }

    flushPending()

    // ── 建议框 ───────────────────────────────────────────────
    if (/^(建议：|建议:|💡)/.test(line)) {
      const text = line.replace(/^(建议：|建议:|💡)\s*/, '')
      nodes.push(
        <div key={key++} style={s.suggestionBox}>
          <div style={s.suggestionIcon}><SvgBulb /></div>
          <div style={s.suggestionText}>
            <span style={s.suggestionLabel}>建议</span>{text}
          </div>
        </div>
      )
      continue
    }

    // ── 新闻行（含股票代码）──────────────────────────────────
    const hasCode = /\(\d{4,6}\)/.test(line)
    if (hasCode) {
      const news = parseNews(line)
      if (news) {
        nodes.push(
          <div key={key++} style={s.newsRow}>
            <div style={s.newsDocIcon}><SvgDocMini /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={s.newsTop}>
                <span style={{ ...s.newsStock, color: accent }}>{news.nameWithEmoji}</span>
                <span style={s.newsCode}>{news.code}</span>
              </div>
              {news.headline === '暂无相关新闻' ? (
                <span style={s.noNews}>{news.headline}</span>
              ) : (
                <div style={s.newsHeadline}>{news.headline}</div>
              )}
              {news.source && <div style={s.newsSource}>{news.source}</div>}
            </div>
          </div>
        )
      } else {
        nodes.push(<p key={key++} style={s.contentLine}>{line}</p>)
      }
      continue
    }

    // ── 新闻快讯行（▸ 开头）─────────────────────────────────
    if (line.startsWith('▸')) {
      const linkMatch = line.match(/▸\s*\[(.+?)\]\((.+?)\)\s*(.*)/)
      if (linkMatch) {
        const [, title, url, rest] = linkMatch
        nodes.push(
          <a key={key++} href={url} target="_blank" rel="noopener noreferrer"
            style={{ ...s.newsletterRow, textDecoration: 'none', display: 'flex', cursor: 'pointer' }}>
            <span style={s.newsletterDot} />
            <div style={{ ...s.newsletterContent, flex: 1 }}>
              <span style={{ ...s.newsletterTitle, color: THEME }}>{title}</span>
              {rest && <span style={s.newsletterSource}>{rest.trim()}</span>}
            </div>
            <span style={s.linkArrow}>↗</span>
          </a>
        )
      } else {
        nodes.push(
          <div key={key++} style={s.newsletterRow}>
            <span style={s.newsletterDot} />
            <div style={s.newsletterContent}>
              <span style={s.newsletterTitle}>{renderInlineMd(line.slice(1).trim())}</span>
            </div>
            <span style={s.aiTag}>AI摘要</span>
          </div>
        )
      }
      continue
    }

    // ── 小节标题（带图标）────────────────────────────────────
    const looksLikeHeader = line.length < 60 && !/[+-]\d+\.\d+%/.test(line)
    if (looksLikeHeader) {
      const secIcon = getSectionIcon(line)
      nodes.push(
        <div key={key++} style={{ ...s.sectionHeader, background: accent }}>
          {secIcon && <span style={s.sectionIconWrap}>{secIcon}</span>}
          {renderInlineMd(line)}
        </div>
      )
      continue
    }

    nodes.push(<p key={key++} style={s.contentLine}>{renderInlineMd(line)}</p>)
  }

  flushPending()
  return nodes
}

// ── 页面主体 ───────────────────────────────────────────────────────────────

export default function PushDetailPage() {
  const params = useParams()
  const id = params?.id as string
  const [log, setLog] = useState<Record<string, string> | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!id) return
    fetch(`/api/push/logs/public/${id}`, { cache: 'no-store' })
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d || d.detail) setNotFound(true)
        else setLog(d)
      })
      .catch(() => setNotFound(true))
  }, [id])

  const handleExport = useCallback(async () => {
    if (!containerRef.current || exporting) return
    setExporting(true)
    try {
      const { toPng } = await import('html-to-image')
      const dataUrl = await toPng(containerRef.current, {
        cacheBust: true,
        pixelRatio: 2,
        backgroundColor: '#F7F3EC',
      })
      const isMobile = /iPhone|iPad|iPod|Android|MicroMessenger/i.test(navigator.userAgent)
      if (isMobile) {
        setImageUrl(dataUrl)
      } else {
        const link = document.createElement('a')
        link.download = `hunter-push-${id}.png`
        link.href = dataUrl
        link.click()
        setImageUrl(dataUrl)
      }
    } catch (e) {
      console.error('Export failed:', e)
      alert('图片生成失败，请重试')
    } finally {
      setExporting(false)
    }
  }, [exporting, id])

  if (notFound) {
    return (
      <div style={s.page}>
        <div style={s.container}><div style={s.centerMsg}>推送记录不存在或已过期</div></div>
      </div>
    )
  }
  if (!log) {
    return (
      <div style={s.page}>
        <div style={s.container}><div style={s.centerMsg}>加载中…</div></div>
      </div>
    )
  }

  // ── 图片预览弹窗 ──
  const ImageModal = imageUrl ? (
    <div style={s.modalOverlay} onClick={() => setImageUrl(null)}>
      <div style={s.modalInner} onClick={e => e.stopPropagation()}>
        <button style={s.modalCloseBtn} onClick={() => setImageUrl(null)}>✕</button>
        <div style={s.modalScrollArea}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={imageUrl} alt="push-detail" style={s.modalImg} />
        </div>
        <div style={s.modalHint}>长按图片保存到相册</div>
      </div>
    </div>
  ) : null

  const pushType  = log.push_type || ''
  const label     = PUSH_TYPE_LABELS[pushType] || pushType
  const kicker    = PUSH_TYPE_KICKER[pushType] || label
  const pillDesc  = PUSH_TYPE_PILL_DESC[pushType] || label
  const accent    = PUSH_TYPE_ACCENT[pushType] || DEFAULT_ACCENT
  const headerBg  = PUSH_TYPE_HEADER_BG[pushType] || DEFAULT_HEADER_BG
  const svgIcon   = PUSH_TYPE_SVG[pushType]
  const titleLine = log.content?.split('\n')[0] || label
  const dateStr   = log.push_date || ''

  // 日期 Pill 格式化
  const d = log.push_date ? new Date(log.push_date + 'T00:00:00+08:00') : null
  const pillMM   = d ? String(d.getMonth() + 1).padStart(2, '0') : ''
  const pillDD   = d ? String(d.getDate()).padStart(2, '0') : ''
  const pillHHMM = log.executed_at
    ? new Date(log.executed_at).toLocaleTimeString('zh-CN', {
        timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false,
      })
    : ''
  const pillDate = pillMM && pillDD ? `${pillMM}月${pillDD}日 ${pillHHMM}`.trim() : pillHHMM

  const timeStr = log.executed_at
    ? new Date(log.executed_at).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
      })
    : ''

  // fundflow_topn：金色圆框；其余：半透明白边圆角正方形
  const isFundFlow = pushType === 'fundflow_topn'
  const iconWrapStyle: React.CSSProperties = isFundFlow
    ? { ...s.headerIconWrap, borderRadius: '50%', border: '2px solid rgba(212,146,90,0.7)', background: 'rgba(255,253,249,0.1)' }
    : { ...s.headerIconWrap }

  return (
    <div style={s.page}>
      {ImageModal}
      <div style={s.container} ref={containerRef}>

        {/* ── Hero Header ── */}
        <div style={{ ...s.header, background: headerBg, borderBottomColor: accent }}>
          <div style={iconWrapStyle}>
            {svgIcon ?? <span style={{ fontSize: 26 }}>{label}</span>}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={s.headerKicker}>{kicker}</div>
            <div style={{ ...s.headerTitle, display: 'flex', alignItems: 'center' }}>
              {/* close_review：标题前内嵌彩色柱图 chip */}
              {pushType === 'close_review' && <SvgTitleBarChip />}
              <span>{titleLine}</span>
            </div>
            <div style={s.headerMeta}>{dateStr}{timeStr ? ` · ${timeStr}` : ''}</div>
          </div>
        </div>

        {/* ── 日期标签 Pill（铜色填充，白色文字）── */}
        {pillDate && (
          <div style={{ ...s.datePill, background: accent }}>
            <SvgCalendarSm color="rgba(255,253,249,0.9)" />
            <span style={s.datePillDate}>{pillDate}</span>
            <span style={s.datePillSep}>|</span>
            <span style={s.datePillDesc}>{pillDesc}</span>
          </div>
        )}

        {/* ── 正文 ── */}
        <div style={s.body}>
          {renderContent(log.content || '', accent, pushType)}
        </div>

        {/* ── Footer ── */}
        <div style={s.footer}>
          <span style={s.footerBrand}>Hunter 猎鹿人 · AgentPit</span>
          <span style={s.footerNote}>agentpit.io</span>
        </div>
      </div>

      {/* ── 导出图片悬浮按钮（不在 container 内，不会被截图）── */}
      <button
        onClick={handleExport}
        disabled={exporting}
        style={{ ...s.exportFab, opacity: exporting ? 0.6 : 1 }}
        title="导出为图片"
        aria-label="导出为图片"
      >
        {exporting ? (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ animation: 'spin 1s linear infinite' }}>
            <path d="M21 12a9 9 0 1 1-9-9" />
          </svg>
        ) : (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
            <circle cx="12" cy="13" r="4"/>
          </svg>
        )}
        <span style={{ fontSize: 11, marginTop: 2 }}>{exporting ? '生成中' : '保存图片'}</span>
      </button>
      <style>{`@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}

// ── 样式表 ─────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  page: {
    minHeight: '100vh',
    background: '#F7F3EC',
    fontFamily: '"Songti SC","Source Han Serif SC","Noto Serif CJK SC",Georgia,serif',
  },
  container: {
    maxWidth: '520px',
    margin: '0 auto',
    minHeight: '100vh',
    background: '#F7F3EC',
    display: 'flex',
    flexDirection: 'column',
  },

  /* ── Hero Header ── */
  header: {
    borderBottom: '4px solid #B06A32',
    padding: '28px 20px 24px 24px',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '16px',
    color: '#FFFDF9',
    position: 'relative',
  },
  headerIconWrap: {
    width: 52,
    height: 52,
    borderRadius: '12px',
    background: 'rgba(255,253,249,0.15)',
    border: '1px solid rgba(255,253,249,0.28)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: 2,
  },
  headerKicker: {
    fontSize: '10px',
    letterSpacing: '0.28em',
    color: '#D4925A',
    textTransform: 'uppercase' as const,
    marginBottom: '8px',
  },
  headerTitle: {
    fontSize: '24px',
    fontFamily: 'Georgia,"Songti SC",serif',
    fontWeight: 700,
    lineHeight: 1.25,
    marginBottom: '8px',
    color: '#FFFDF9',
  },
  headerMeta: {
    fontSize: '11px',
    color: '#C8BEB2',
  },

  /* ── 日期标签 Pill（铜色填充）── */
  datePill: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    margin: '14px 16px 0',
    padding: '8px 16px',
    background: DEFAULT_ACCENT,
    borderRadius: 20,
    width: 'fit-content',
  },
  datePillDate: {
    fontSize: '13px',
    fontWeight: 600,
    color: '#FFFDF9',
    fontVariantNumeric: 'tabular-nums',
    fontFamily: 'Georgia,monospace',
  },
  datePillSep: {
    color: 'rgba(255,253,249,0.5)',
    fontSize: 13,
    margin: '0 1px',
  },
  datePillDesc: {
    fontSize: '11px',
    color: 'rgba(255,253,249,0.82)',
    letterSpacing: '0.02em',
  },

  /* ── 正文容器 ── */
  body: {
    flex: 1,
    paddingTop: '10px',
    paddingBottom: '16px',
  },
  blockGap: { height: '20px' },

  /* ── 小节标签（带图标）── */
  sectionHeader: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    width: 'fit-content',
    fontSize: '11px',
    fontWeight: 700,
    color: '#FFFDF9',
    background: '#B06A32',
    padding: '5px 14px 5px 10px',
    borderRadius: '20px',
    letterSpacing: '0.12em',
    margin: '20px 16px 10px 16px',
  },
  sectionIconWrap: {
    display: 'inline-flex',
    alignItems: 'center',
    flexShrink: 0,
  },

  /* ── 行情卡片 ── */
  quoteRow: {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
    margin: '6px 12px',
    borderRadius: '8px',
    border: '1px solid #E4DAC8',
    boxShadow: '0 1px 4px rgba(50,35,10,0.07)',
    background: '#FFFDF9',
  },
  quoteLeft: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: '3px',
    minWidth: 0,
  },
  qName: {
    fontSize: '16px',
    fontWeight: 700,
    color: '#211C18',
    fontFamily: 'Georgia,"Songti SC",serif',
    lineHeight: 1.2,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  qCode: {
    fontSize: '11px',
    color: '#7A6F63',
    fontFamily: 'Georgia,monospace',
    letterSpacing: '0.05em',
  },
  quoteRight: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    gap: '3px',
    flexShrink: 0,
  },
  qPct: {
    fontSize: '17px',
    fontWeight: 700,
    fontVariantNumeric: 'tabular-nums',
    fontFamily: 'Georgia,serif',
    letterSpacing: '-0.2px',
  },
  qPrice: {
    fontSize: '13px',
    color: '#4B423A',
    fontVariantNumeric: 'tabular-nums',
    fontFamily: 'Georgia,monospace',
  },
  qChange: {
    fontSize: '12px',
    color: '#7A6F63',
  },

  /* ── 新闻行（左侧文档图标）── */
  newsRow: {
    margin: '6px 12px',
    padding: '13px 14px',
    borderRadius: '8px',
    border: '1px solid #E4DAC8',
    boxShadow: '0 1px 4px rgba(50,35,10,0.07)',
    background: '#FFFDF9',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '10px',
  },
  newsDocIcon: {
    width: 32,
    height: 32,
    borderRadius: '8px',
    background: '#F0EAE0',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: 1,
  },
  newsTop: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    marginBottom: '4px',
  },
  newsStock: {
    fontSize: '13px',
    fontWeight: 700,
    color: '#B06A32',
  },
  newsCode: {
    fontSize: '10px',
    color: '#7A6F63',
    background: '#EFE8DC',
    padding: '1px 5px',
    borderRadius: '3px',
    fontFamily: 'Georgia,monospace',
  },
  newsHeadline: {
    fontSize: '13px',
    color: '#211C18',
    lineHeight: 1.7,
  },
  newsSource: {
    fontSize: '11px',
    color: '#7A6F63',
    marginTop: '4px',
    letterSpacing: '0.03em',
  },
  noNews: {
    fontSize: '13px',
    color: '#A89F94',
  },

  /* ── 建议框 ── */
  suggestionBox: {
    margin: '8px 12px',
    padding: '12px 14px',
    borderRadius: '10px',
    background: '#FFF8EE',
    border: '1px solid #E8D5B0',
    display: 'flex',
    alignItems: 'flex-start',
    gap: '10px',
  },
  suggestionIcon: { flexShrink: 0, marginTop: 1 },
  suggestionText: {
    flex: 1,
    fontSize: '13px',
    color: '#4B423A',
    lineHeight: 1.75,
  },
  suggestionLabel: {
    fontWeight: 700,
    color: THEME,
    marginRight: 4,
  },

  /* ── 新闻快讯行 ── */
  newsletterRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '10px',
    margin: '6px 12px',
    padding: '12px 16px',
    borderRadius: '8px',
    border: '1px solid #E4DAC8',
    background: '#FFFDF9',
  },
  newsletterDot: {
    width: '5px',
    height: '5px',
    borderRadius: '50%',
    background: '#B06A32',
    flexShrink: 0,
    marginTop: '7px',
  },
  newsletterContent: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
  },
  newsletterTitle: {
    fontSize: '14px',
    color: '#211C18',
    lineHeight: 1.7,
    textDecoration: 'none',
  },
  newsletterSource: {
    fontSize: '11px',
    color: '#7A6F63',
    letterSpacing: '0.03em',
  },

  /* ── 信号链接箭头 ── */
  linkArrow: {
    fontSize: '14px',
    color: THEME,
    flexShrink: 0,
    alignSelf: 'center',
    marginLeft: 6,
    opacity: 0.8,
  },

  /* ── AI摘要标签（无链接信号）── */
  aiTag: {
    fontSize: '10px',
    color: '#A89F94',
    border: '1px solid #D8CDBA',
    borderRadius: '4px',
    padding: '1px 5px',
    flexShrink: 0,
    alignSelf: 'center',
    marginLeft: 6,
    letterSpacing: '0.03em',
  },

  /* ── 净流向独立行（卡片外）── */
  flowLine: {
    fontSize: '13px',
    fontWeight: 600,
    padding: '3px 16px 10px 28px',
    margin: '-4px 12px 4px 12px',
    fontFamily: 'Georgia,monospace',
  },

  /* ── 普通正文 ── */
  contentLine: {
    fontSize: '14px',
    color: '#4B423A',
    margin: '0 12px',
    padding: '8px 16px',
    lineHeight: 1.8,
  },

  /* ── Footer ── */
  footer: {
    padding: '16px 20px',
    borderTop: '1px solid #D8CDBA',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    background: '#EFE8DC',
  },
  footerBrand: {
    fontSize: '13px',
    fontWeight: 700,
    color: '#B06A32',
    fontFamily: 'Georgia,serif',
    letterSpacing: '0.03em',
  },
  footerNote: {
    fontSize: '11px',
    color: '#7A6F63',
  },

  /* ── 状态页 ── */
  centerMsg: {
    padding: '60px 20px',
    textAlign: 'center',
    color: '#7A6F63',
    fontSize: '14px',
    fontFamily: 'Georgia,"Songti SC",serif',
  },

  /* ── 导出悬浮按钮 ── */
  exportFab: {
    position: 'fixed',
    right: 20,
    bottom: 32,
    zIndex: 100,
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 3,
    width: 60,
    height: 60,
    borderRadius: '50%',
    background: '#B06A32',
    color: '#FFFDF9',
    border: 'none',
    boxShadow: '0 4px 16px rgba(176,106,50,0.45)',
    cursor: 'pointer',
    padding: 0,
    WebkitTapHighlightColor: 'transparent',
  },

  /* ── 图片预览弹窗 ── */
  modalOverlay: {
    position: 'fixed',
    inset: 0,
    zIndex: 200,
    background: 'rgba(0,0,0,0.82)',
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'center',
    padding: '0',
  },
  modalInner: {
    position: 'relative',
    width: '100%',
    maxWidth: 520,
    maxHeight: '100vh',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
  },
  modalCloseBtn: {
    position: 'absolute',
    top: 12,
    right: 12,
    zIndex: 10,
    background: 'rgba(255,253,249,0.15)',
    border: '1px solid rgba(255,253,249,0.3)',
    color: '#FFFDF9',
    width: 36,
    height: 36,
    borderRadius: '50%',
    fontSize: 18,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
  },
  modalScrollArea: {
    width: '100%',
    overflowY: 'auto' as const,
    maxHeight: 'calc(100vh - 56px)',
  },
  modalImg: {
    display: 'block',
    width: '100%',
    height: 'auto',
  },
  modalHint: {
    height: 56,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    color: 'rgba(255,253,249,0.75)',
    letterSpacing: '0.06em',
    flexShrink: 0,
    width: '100%',
    background: 'rgba(0,0,0,0.5)',
  },
}
