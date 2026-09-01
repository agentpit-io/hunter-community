'use client'
import { useState } from 'react'
import { ChevronDown, ChevronUp, Wrench, CheckCircle2, XCircle, Loader2, ExternalLink } from 'lucide-react'
import type { MessagePartTool } from '../lib/types'
import { HUNTER } from '../../lib/hunter-theme'
import StockQuickviewCard from './tool_cards/StockQuickviewCard'
import StockNewsCard from './tool_cards/StockNewsCard'
import WatchlistDigestCard from './tool_cards/WatchlistDigestCard'
import PortfolioRebalanceCard from './tool_cards/PortfolioRebalanceCard'
import PortfolioStressCard from './tool_cards/PortfolioStressCard'
import RiskProfileCard from './tool_cards/RiskProfileCard'
import UziDeepAnalysisCard from './tool_cards/UziDeepAnalysisCard'
import { parseToolOutput } from './tool_cards/shared'

const RICH_CARD_TOOLS = new Set([
  'stock_quickview',
  'stock_news',
  'watchlist_digest',
  'portfolio_rebalance',
  'portfolio_stress',
  'update_risk_profile',
  'stock_deep_analysis',   // Sprint 3 P2 · UZI 深度分析
])

/**
 * opencode 给 MCP tool 自动加了 `{server}_` 前缀，如
 *   watchlist_mcp.stock_quickview → 前端收到 `watchlist_stock_quickview`
 *   portfolio_mcp.portfolio_stress → 前端收到 `portfolio_portfolio_stress`
 * 剥离已知的 server 前缀，取本名再匹配富卡片 dispatch。
 */
function normalizeToolName(name: string): string {
  for (const prefix of ['watchlist_', 'portfolio_', 'uzi_']) {
    if (name.startsWith(prefix)) {
      const rest = name.slice(prefix.length)
      if (RICH_CARD_TOOLS.has(rest)) return rest
    }
  }
  return name
}

// tool 前缀 → SKILL badge 映射（fallback 卡片用 · 让用户看清 tool 归属哪个 SKILL）
type SkillBadge = { label: string; color: string; bg: string }
const SKILL_BADGES: Array<{ match: (n: string) => boolean; badge: SkillBadge }> = [
  { match: (n) => n.startsWith('kronos_'),           badge: { label: 'KRONOS', color: '#7c3aed', bg: '#f3f0ff' } },
  { match: (n) => n.startsWith('uzi_') || n === 'stock_deep_analysis',
                                                     badge: { label: 'UZI',    color: '#7C4A22', bg: '#F6EEE7' } },
  { match: (n) => n.startsWith('watchlist_'),        badge: { label: '自选股', color: '#1d4ed8', bg: '#eff6ff' } },
  { match: (n) => n.startsWith('portfolio_'),        badge: { label: '持仓',   color: '#3F6B40', bg: '#EAF3EB' } },
  { match: (n) => n.startsWith('truesource_'),       badge: { label: '数据源', color: '#525252', bg: '#f5f5f4' } },
]
function pickSkillBadge(toolName: string): SkillBadge | null {
  return SKILL_BADGES.find((s) => s.match(toolName))?.badge ?? null
}

function fmtArgs(input: any): string {
  if (input == null) return ''
  if (typeof input === 'string') return input
  try {
    const s = JSON.stringify(input)
    return s.length > 60 ? s.slice(0, 60) + '…' : s
  } catch {
    return String(input)
  }
}

interface Props {
  /** 这一轮 assistant 的正文 —— 富卡片用它判断"模型是不是把我复述了一遍",
   *  复述了就默认折叠,没复述就默认展开(见 UziDeepAnalysisCard) */
  turnText?: string
  part: MessagePartTool
  onOpenArtifact?: (part: MessagePartTool) => void
}

/** 自选股整合 6 tool · 完成时渲染富卡片 · 否则走 fallback 通用卡
 *  opencode 会给 MCP tool 加 server 前缀（portfolio_update_risk_profile 等），
 *  这里剥离前缀取本名匹配。 */
function tryRenderRichCard(part: MessagePartTool, turnText?: string): React.ReactNode | null {
  const name = normalizeToolName(part.tool)
  if (!RICH_CARD_TOOLS.has(name)) return null
  if (part.state?.status !== 'completed') return null
  const data = parseToolOutput<any>(part.state.output)
  if (!data) return null
  try {
    switch (name) {
      case 'stock_quickview':    return <StockQuickviewCard data={data} />
      case 'stock_news':         return <StockNewsCard data={data} />
      case 'watchlist_digest':   return <WatchlistDigestCard data={data} />
      case 'portfolio_rebalance':return <PortfolioRebalanceCard data={data} />
      case 'portfolio_stress':   return <PortfolioStressCard data={data} />
      case 'update_risk_profile':return <RiskProfileCard data={data} />
      case 'stock_deep_analysis':return <UziDeepAnalysisCard data={data} turnText={turnText} />
    }
  } catch (e) {
    console.error('[ToolCallCard] rich card render failed:', part.tool, e)
    return null
  }
  return null
}

export default function ToolCallCard({ part, onOpenArtifact, turnText }: Props) {
  // 富卡片优先 · 5 tool 之一且完成时直接渲染
  const rich = tryRenderRichCard(part, turnText)
  if (rich) return <>{rich}</>
  const [open, setOpen] = useState(false)
  const state = part.state
  const args = fmtArgs(state?.input)
  const status = state?.status
  const output = state?.output
  const hasLargeOutput = output && typeof output === 'string' && output.length > 200

  const dur = state?.time?.start && state?.time?.end
    ? state.time.end - state.time.start
    : null

  return (
    <div style={{ margin: '10px 0' }}>
      <div
        onClick={() => setOpen(!open)}
        style={{
          padding: '10px 14px',
          background: '#f7f5ef',
          borderRadius: 12,
          fontSize: 13,
          color: HUNTER.INK_S,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          transition: 'background 0.1s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = '#f0ede2')}
        onMouseLeave={(e) => (e.currentTarget.style.background = '#f7f5ef')}
      >
        {status === 'error' ? (
          <XCircle size={14} style={{ color: HUNTER.UP, flexShrink: 0 }} />
        ) : status === 'completed' ? (
          <CheckCircle2 size={14} style={{ color: HUNTER.DN, flexShrink: 0 }} />
        ) : (
          <Loader2 size={14} style={{ color: HUNTER.THEME, flexShrink: 0, animation: 'spin 1.2s linear infinite' }} />
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, flex: 1 }}>
          <Wrench size={12} style={{ color: HUNTER.INK_F, flexShrink: 0 }} />
          {(() => {
            const sb = pickSkillBadge(part.tool)
            return sb ? (
              <span
                style={{
                  padding: '1px 6px',
                  borderRadius: 4,
                  background: sb.bg,
                  color: sb.color,
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: '.03em',
                  flexShrink: 0,
                  lineHeight: '14px',
                }}
              >
                {sb.label}
              </span>
            ) : null
          })()}
          <span
            style={{
              fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
              fontSize: 12,
              color: HUNTER.INK,
              fontWeight: 500,
            }}
          >
            {part.tool}
          </span>
          {args && (
            <span
              style={{
                fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                fontSize: 11.5,
                color: HUNTER.INK_F,
                minWidth: 0,
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {args}
            </span>
          )}
        </div>

        {dur != null && (
          <span style={{ fontSize: 11, color: HUNTER.INK_F, flexShrink: 0 }}>{dur}ms</span>
        )}

        {hasLargeOutput && onOpenArtifact && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onOpenArtifact(part)
            }}
            title="在右侧查看"
            style={{
              background: 'transparent',
              border: 'none',
              color: HUNTER.INK_F,
              cursor: 'pointer',
              padding: 2,
              display: 'flex',
              alignItems: 'center',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.color = HUNTER.THEME)}
            onMouseLeave={(e) => (e.currentTarget.style.color = HUNTER.INK_F)}
          >
            <ExternalLink size={12} />
          </button>
        )}

        {open ? (
          <ChevronUp size={13} style={{ color: HUNTER.INK_F, flexShrink: 0 }} />
        ) : (
          <ChevronDown size={13} style={{ color: HUNTER.INK_F, flexShrink: 0 }} />
        )}
      </div>

      {open && (
        <div
          style={{
            marginTop: 4,
            padding: '12px 14px',
            background: '#fbfaf5',
            borderRadius: 10,
            fontSize: 12,
            fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
            color: HUNTER.INK_S,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 340,
            overflowY: 'auto',
            lineHeight: 1.55,
          }}
        >
          <div style={{ color: HUNTER.INK_F, fontWeight: 600, marginBottom: 6, fontSize: 10 }}>INPUT</div>
          {state?.input != null ? (
            <div style={{ marginBottom: 12 }}>{JSON.stringify(state.input, null, 2)}</div>
          ) : (
            <div style={{ marginBottom: 12, opacity: 0.5 }}>(no input)</div>
          )}
          <div style={{ color: HUNTER.INK_F, fontWeight: 600, marginBottom: 6, fontSize: 10 }}>OUTPUT</div>
          {output ? (
            <div>{typeof output === 'string' ? output : JSON.stringify(output, null, 2)}</div>
          ) : (
            <div style={{ opacity: 0.5 }}>(pending / no output)</div>
          )}
        </div>
      )}
    </div>
  )
}
