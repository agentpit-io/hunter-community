// 一 key 解锁能力矩阵面板 · UnlockModal 的核心 UX 改造
// 2026-08-29 关联方案:doc/开源hunter-community/05hunterSkill/*.md
//
// 三视角展示 Hunter API key 解锁什么:
//   1. 按市场(A 股 / 港股 / 美股 / 全球)
//   2. 按数据类型(12 类 × 4 市场 矩阵)
//   3. 按用途 SKILL(综合分析 / 投研报告 / 事件筛选 / 尽调风控 / 组合级)
//
// 默认折叠 · 用户点击展开(避免 modal 太长 · scroll 疲劳)
// 数据来源:GET /api/hunter/capabilities/matrix(hunter_unlock.py)
'use client'
import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

// ═══════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════

type Summary = {
  sources: { total: number; need_key: number; free: number; unavailable: number }
  tools:   { total: number; need_key: number; free: number }
  skills:  { total: number; need_key: number; free: number }
}

type MarketBlock = {
  key: string; label: string; flag: string
  need_key: number; free: number
  highlights: string[]
  sources: { key: string; name: string; kind: string; tier: string; requires_key: boolean }[]
}

type KindCell = { source_key: string; source_name: string; status: 'official' | 'free'; count_in_cell: number } | null
type KindRow = { key: string; label: string; markets: { a: KindCell; hk: KindCell; us: KindCell; global: KindCell } }

type SkillItem = { key: string; name: string; icon: string; prompt_tpl: string; brand: string; hint: string; need_key: boolean }
type SkillCategory = { key: string; label: string; skills: SkillItem[] }

export type CapabilityMatrix = {
  summary: Summary
  by_market: MarketBlock[]
  by_kind: KindRow[]
  by_skill_category: SkillCategory[]
  unlocked: boolean
}

// ═══════════════════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════════════════

interface Props {
  /** unlocked=true 时 heading 与文案切换成"你现在能用什么" */
  unlocked?: boolean
  /** 默认是否展开 · 未解锁时 false(避免劝退)· 已解锁时 false(默认摘要,展开看细) */
  defaultOpen?: boolean
}

export default function CapabilityMatrixPanel({ unlocked = false, defaultOpen = false }: Props) {
  const [matrix, setMatrix] = useState<CapabilityMatrix | null>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(defaultOpen)
  const [tab, setTab] = useState<'market' | 'kind' | 'skill'>('market')

  useEffect(() => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
    fetch('/api/hunter/capabilities/matrix', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setMatrix)
      .catch(() => setMatrix(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{
        padding: 14, borderRadius: HUNTER.R_MD, background: HUNTER.PAPER2,
        display: 'flex', alignItems: 'center', gap: 6, color: HUNTER.INK_F, fontSize: 12,
      }}>
        <Loader2 size={13} className="animate-spin" />
        <span>能力清单加载中…</span>
      </div>
    )
  }
  if (!matrix) return null

  const s = matrix.summary

  return (
    <div style={{
      marginBottom: 14,
      border: `1px solid ${HUNTER.LINE}`, borderRadius: HUNTER.R_MD,
      background: HUNTER.PAPER2,
    }}>
      {/* 头部 · 摘要 + 折叠开关 */}
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          width: '100%', padding: '10px 12px', background: 'none',
          border: 'none', cursor: 'pointer', textAlign: 'left',
          display: 'flex', alignItems: 'center', gap: 8,
        }}
      >
        {open ? <ChevronDown size={14} style={{ color: HUNTER.INK_F }} />
              : <ChevronRight size={14} style={{ color: HUNTER.INK_F }} />}
        <div style={{ flex: 1, fontSize: 12, color: HUNTER.INK_S, lineHeight: 1.65 }}>
          {unlocked ? (
            <>
              <b style={{ color: HUNTER.SUCCESS }}>已解锁</b> · 数据源 {s.sources.total} 条 ·
              工具 {s.tools.total} 个 · SKILL {s.skills.total} 个
            </>
          ) : (
            <>
              一把 key 解锁 · <b style={{ color: HUNTER.COPPER3 }}>{s.sources.need_key}</b>{' '}
              数据源 · <b style={{ color: HUNTER.COPPER3 }}>{s.tools.need_key}</b> 工具 ·
              <b style={{ color: HUNTER.COPPER3 }}> {s.skills.need_key}</b> SKILL{' '}
              <span style={{ color: HUNTER.INK_F }}>(免 key 已有 {s.sources.free} 源 · {s.skills.free} SKILL)</span>
            </>
          )}
        </div>
        <span style={{ fontSize: 11, color: HUNTER.SOFT }}>{open ? '收起' : '展开'}</span>
      </button>

      {/* 展开区 */}
      {open && (
        <div style={{ padding: '0 12px 12px' }}>
          {/* tab 切换 */}
          <div style={{
            display: 'flex', gap: 4, marginBottom: 10,
            borderBottom: `1px solid ${HUNTER.LINE}`,
          }}>
            {([['market', '按市场'], ['kind', '按数据类型'], ['skill', '按用途 SKILL']] as const).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                style={{
                  padding: '6px 10px', fontSize: 12,
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: tab === k ? HUNTER.COPPER3 : HUNTER.INK_F,
                  borderBottom: tab === k ? `2px solid ${HUNTER.THEME}` : '2px solid transparent',
                  fontWeight: tab === k ? 600 : 400,
                  marginBottom: -1,
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === 'market'  && <MarketView blocks={matrix.by_market} />}
          {tab === 'kind'    && <KindMatrixView rows={matrix.by_kind} />}
          {tab === 'skill'   && <SkillCategoryView cats={matrix.by_skill_category} />}
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
// 视角 1 · 按市场
// ═══════════════════════════════════════════════════════════════

function MarketView({ blocks }: { blocks: MarketBlock[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {blocks.map(b => (
        <div key={b.key} style={{
          padding: '8px 10px', border: `1px solid ${HUNTER.LINE}`,
          borderRadius: HUNTER.R_SM, background: '#fff',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <span style={{ fontSize: 14 }}>{b.flag}</span>
            <b style={{ fontSize: 13, color: HUNTER.INK }}>{b.label}</b>
            {b.need_key > 0 && (
              <span style={{
                marginLeft: 6, padding: '1px 6px', borderRadius: 3,
                background: HUNTER.BRAND_PALE, color: HUNTER.COPPER3,
                fontSize: 10, fontWeight: 600,
              }}>🔑 {b.need_key} 条</span>
            )}
            {b.free > 0 && (
              <span style={{
                padding: '1px 6px', borderRadius: 3,
                background: HUNTER.TAG_OK_BG, color: HUNTER.TAG_OK_FG,
                fontSize: 10, fontWeight: 600,
              }}>🆓 {b.free} 条</span>
            )}
          </div>
          {b.highlights.length > 0 && (
            <div style={{ fontSize: 11.5, color: HUNTER.INK_S, lineHeight: 1.6 }}>
              含 {b.highlights.slice(0, 4).join(' · ')}
              {b.sources.length > 4 && <> 等 {b.sources.length} 条</>}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
// 视角 2 · 按数据类型 12x4 矩阵
// ═══════════════════════════════════════════════════════════════

function KindMatrixView({ rows }: { rows: KindRow[] }) {
  const cellStyle: React.CSSProperties = {
    padding: '4px 2px', textAlign: 'center', fontSize: 11,
    border: `1px solid ${HUNTER.LINE}`, verticalAlign: 'middle',
  }
  const headStyle: React.CSSProperties = {
    ...cellStyle, background: HUNTER.PAPER, fontWeight: 600, color: HUNTER.INK_S,
  }
  const renderCell = (c: KindCell) => {
    if (!c) return <span style={{ color: HUNTER.SOFT }}>—</span>
    if (c.status === 'official') {
      return <span title={c.source_name} style={{ color: HUNTER.COPPER3, fontWeight: 600 }}>🔑</span>
    }
    return <span title={c.source_name} style={{ color: HUNTER.SUCCESS, fontWeight: 600 }}>🆓</span>
  }
  return (
    <div>
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
        <thead>
          <tr>
            <th style={{ ...headStyle, textAlign: 'left', paddingLeft: 6, width: '40%' }}>类型</th>
            <th style={headStyle}>A股</th>
            <th style={headStyle}>港</th>
            <th style={headStyle}>美</th>
            <th style={headStyle}>全球</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key}>
              <td style={{ ...cellStyle, textAlign: 'left', paddingLeft: 6, color: HUNTER.INK }}>
                {r.label}
              </td>
              <td style={cellStyle}>{renderCell(r.markets.a)}</td>
              <td style={cellStyle}>{renderCell(r.markets.hk)}</td>
              <td style={cellStyle}>{renderCell(r.markets.us)}</td>
              <td style={cellStyle}>{renderCell(r.markets.global)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 10.5, color: HUNTER.SOFT, marginTop: 6, lineHeight: 1.5 }}>
        🔑 需 Hunter key · 🆓 免 key 可用 · — 该市场无此类数据 · hover 看具体源
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
// 视角 3 · 按 SKILL 用途
// ═══════════════════════════════════════════════════════════════

function SkillCategoryView({ cats }: { cats: SkillCategory[] }) {
  if (cats.length === 0) {
    return <div style={{ fontSize: 12, color: HUNTER.SOFT, textAlign: 'center', padding: 12 }}>暂无 SKILL</div>
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {cats.map(cat => (
        <div key={cat.key}>
          <div style={{ fontSize: 11, fontWeight: 600, color: HUNTER.COPPER3, marginBottom: 4 }}>
            {cat.label}
          </div>
          {cat.skills.map(sk => (
            <div key={sk.key} style={{
              display: 'flex', alignItems: 'flex-start', gap: 6, padding: '4px 8px',
              fontSize: 12, color: HUNTER.INK, lineHeight: 1.55,
            }}>
              <span style={{ fontSize: 13, lineHeight: '1.4em' }}>
                {sk.need_key ? '🔑' : '🆓'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div><b>{sk.name}</b></div>
                {sk.prompt_tpl && (
                  <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 1 }}>
                    示例:{sk.prompt_tpl}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
