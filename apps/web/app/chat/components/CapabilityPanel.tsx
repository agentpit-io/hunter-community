'use client'

// 侧栏「能力」区 · 三段瘦身版(方案 D · 2026-08-17)
//
// 改之前:
//   3 段折叠面板 · 全展开高度 1400px+ · 单 SKILL pill 每类 3-4 行 · 用户滚 3 屏
//   侧栏塞不下 · 用户找不到管理入口
//
// 改之后(总高 ~480px · 减 66%):
//   ┌ 数据源       32/33  →     一行 · 点跳 /library?tab=sources
//   │ ▓▓▓▓▓▓▓░  97%
//   │ 1 个需要 key
//   ├ 工具箱       13/13  →     一行 · 点跳 /library?tab=tools
//   │ ▓▓▓▓▓▓▓  100%
//   ├ SKILL 库     23/23  + →   一行 · 点跳 /library?tab=skills
//   │ ▓▓▓▓▓▓▓  100%
//   ├─ 最近用 ─
//   │ ⚡ UZI · 速判
//   │ 📈 Kronos · 走势预测
//   │ 🎯 UZI · 深度分析
//   │ 🌅 自选股日报
//   │ 💰 UZI · DCF 估值
//   └─ 所有类目 ─
//     [快速判断 3] [综合 2] [投研 4] [估值 4] [事件 3] [组合 3] [尽调 2]
//
// 深度浏览/管理去 /library · 侧栏只留高频快速用
// 见方案 §7: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { Lock, Settings2, Plus } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  listSources, listToolbox, listCatalogSkills,
  type SourceGroup, type ToolGroup, type SkillGroup, type Summary,
  type CatalogSkillItem,
} from '../lib/catalogClient'
import { getUnlockStatus, onUnlockChange, peekUnlockStatus } from '../lib/unlockClient'
import { getRecentSkills, trackSkillUsage } from '../lib/skillUsage'
import UnlockModal from './UnlockModal'
import SkillAddPanel from './SkillAddPanel'

interface Props {
  onPick: (tpl: string, key: string) => void
  onManage: () => void
  refreshKey?: number
}

const RECENT_N = 5

export default function CapabilityPanel({ onPick, onManage, refreshKey }: Props) {
  const [sources, setSources] = useState<{ groups: SourceGroup[]; summary: Summary } | null>(null)
  const [toolbox, setToolbox] = useState<{ groups: ToolGroup[]; summary: Summary } | null>(null)
  const [skills, setSkills]   = useState<{ groups: SkillGroup[]; summary: Summary } | null>(null)
  const [unlocked, setUnlocked] = useState<boolean | null>(peekUnlockStatus()?.unlocked ?? null)
  const [gate, setGate] = useState<string | null>(null)
  const [installOpen, setInstallOpen] = useState(false)
  const [installed, setInstalled] = useState('')
  const [recentTick, setRecentTick] = useState(0)   // 触发重新读 localStorage
  const locked = unlocked === false

  useEffect(() => {
    listSources().then(setSources).catch(() => {})
    listToolbox().then(setToolbox).catch(() => {})
    listCatalogSkills().then(setSkills).catch(() => {})
  }, [refreshKey])

  useEffect(() => {
    void getUnlockStatus().then((st) => setUnlocked(st.unlocked)).catch(() => {})
    return onUnlockChange((st) => setUnlocked(st.unlocked))
  }, [])

  // usage 变了(自己 track 或另一个 tab 改了)重取一次
  useEffect(() => {
    const handler = () => setRecentTick((n) => n + 1)
    window.addEventListener('hunter-skill-usage', handler)
    window.addEventListener('storage', handler)
    return () => {
      window.removeEventListener('hunter-skill-usage', handler)
      window.removeEventListener('storage', handler)
    }
  }, [])

  // 建 key → SKILL item 反查表(所有 group 展平)
  const skillByKey = useMemo(() => {
    const m = new Map<string, CatalogSkillItem>()
    skills?.groups.forEach((g) => g.skills.forEach((s) => m.set(s.key, s)))
    return m
  }, [skills])

  // 最近用 top N · 已 track 的过滤存在的 · 不够就补默认(第一批高频)
  const recent = useMemo(() => {
    if (!skills) return [] as CatalogSkillItem[]
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const _ = recentTick   // 让 useMemo 依赖 tick
    const trackedKeys = getRecentSkills(RECENT_N * 2)   // 拿多点 · 过滤后可能不够
    const items: CatalogSkillItem[] = []
    for (const k of trackedKeys) {
      const s = skillByKey.get(k)
      if (s) items.push(s)
      if (items.length >= RECENT_N) break
    }
    // 补齐默认(用户还没用过时至少让他看到几个入口)
    if (items.length < RECENT_N) {
      const DEFAULTS = ['uzi_quick_scan', 'forecast', 'stock_deep_analysis', 'watchlist_daily', 'uzi_dcf']
      for (const k of DEFAULTS) {
        if (items.length >= RECENT_N) break
        if (items.some((x) => x.key === k)) continue
        const s = skillByKey.get(k)
        if (s) items.push(s)
      }
    }
    return items
  }, [skills, skillByKey, recentTick])

  const handlePick = useCallback((tpl: string, key: string) => {
    if (locked && !key.startsWith('custom:')) { setGate(key); return }
    trackSkillUsage(key)
    onPick(tpl, key)
  }, [locked, onPick])

  return (
    <div style={rootStyle}>
      {/* Header */}
      <div style={headerStyle}>
        <span style={sectionLabel}>能力</span>
        <Link href="/library" style={miniLink} title="打开完整能力库">
          ⇱ 完整库
        </Link>
        <button onClick={onManage} style={miniBtn} title="管理你的能力">
          <Settings2 size={11} strokeWidth={1.5} style={{ marginRight: 3 }} />
          管理
        </button>
      </div>

      {/* ① 数据源 · 一行进度条 */}
      <ProgressLine
        icon="📊"
        title="数据源"
        summary={sources?.summary}
        href="/library?tab=sources"
        subtitle={sources ? subOfSources(sources.summary) : ''}
      />

      {/* ② 工具箱 · 一行进度条 */}
      <ProgressLine
        icon="🛠"
        title="工具箱"
        summary={toolbox?.summary}
        href="/library?tab=tools"
        subtitle="模型可直接调用的能力"
      />

      {/* ③ SKILL 库 · 一行进度条 + 加号(直接打开 SkillAddPanel) */}
      <ProgressLine
        icon="✨"
        title="SKILL 库"
        summary={skills?.summary}
        href="/library?tab=skills"
        subtitle={skills?.summary.user_added ? `含你加的 ${skills.summary.user_added} 个` : undefined}
        actionSlot={
          <button
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setInstallOpen((v) => !v) }}
            title="加一个 SKILL(从 GitHub / 手写)"
            style={addBtn}
            onMouseEnter={(e) => { e.currentTarget.style.color = HUNTER.THEME }}
            onMouseLeave={(e) => { e.currentTarget.style.color = HUNTER.INK_F }}
          >
            <Plus size={13} strokeWidth={2.2} />
          </button>
        }
      />

      {installOpen && (
        <div style={{ padding: '0 8px 6px' }}>
          <SkillAddPanel
            categories={skills?.groups.map((g) => g.category) || []}
            onClose={() => setInstallOpen(false)}
            onDone={(msg) => {
              setInstallOpen(false)
              setInstalled(msg)
              listCatalogSkills().then(setSkills).catch(() => {})
            }}
          />
        </div>
      )}
      {installed && (
        <div style={okBox} onClick={() => setInstalled('')}>{installed}(点击关闭)</div>
      )}

      {/* 最近用 top 5 */}
      {recent.length > 0 && (
        <>
          <div style={subheaderStyle}>最近用</div>
          <div style={{ padding: '0 8px' }}>
            {recent.map((s) => (
              <button
                key={s.key}
                onClick={() => handlePick(s.prompt_tpl, s.key)}
                title={`${s.hint || ''}${s.brand ? ' · ' + s.brand : ''}${
                  s.status !== 'ready'
                    ? '\n⚠ 依赖未就绪: ' + [...s.missing_tools, ...s.blocked_tools].join(', ')
                    : ''}`}
                style={{ ...recentRow, opacity: s.status === 'ready' ? 1 : 0.6 }}
                onMouseEnter={(e) => { e.currentTarget.style.background = HUNTER.PANEL_2 }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{ fontSize: 13, flexShrink: 0 }}>{s.icon}</span>
                <span style={recentName}>{s.name}</span>
                {locked && <Lock size={9} strokeWidth={1.6} style={{ color: HUNTER.SOFT, flexShrink: 0 }} />}
              </button>
            ))}
          </div>
        </>
      )}

      {/* 所有类目 chip */}
      {skills && skills.groups.length > 0 && (
        <>
          <div style={subheaderStyle}>所有类目</div>
          <div style={chipRow}>
            {skills.groups.map((g) => (
              <Link
                key={g.category}
                href={`/library?tab=skills&group=${encodeURIComponent(g.category)}`}
                style={catChip}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = HUNTER.THEME }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = HUNTER.LINE }}
              >
                <span>{g.category}</span>
                <span style={{ color: HUNTER.INK_F, marginLeft: 4 }}>{g.total}</span>
              </Link>
            ))}
          </div>
        </>
      )}

      {gate !== null && <UnlockModal triggeredBy={gate || undefined} onClose={() => setGate(null)} />}
    </div>
  )
}

// ── ProgressLine 一行进度条 ────────────────────────────

function ProgressLine({ icon, title, summary, href, subtitle, actionSlot }: {
  icon: string
  title: string
  summary?: Summary
  href: string
  subtitle?: string
  actionSlot?: React.ReactNode
}) {
  const total = summary?.total ?? 0
  const ready = summary?.ready ?? 0
  const pct = total > 0 ? Math.round((ready / total) * 100) : 0
  return (
    <div style={progressWrap}>
      <Link href={href} style={progressLink}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = HUNTER.PANEL_2 }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12 }}>{icon}</span>
          <span style={{ fontSize: 12, fontWeight: 600, color: HUNTER.INK_S, flex: 1 }}>{title}</span>
          <span style={{ fontSize: 10.5, color: HUNTER.INK_F, fontVariantNumeric: 'tabular-nums' }}>
            {summary ? `${ready}/${total}` : '—'}
          </span>
          {actionSlot}
        </div>
        <div style={progressBg}>
          <div style={{ ...progressBar, width: `${pct}%` }} />
        </div>
        {subtitle && <div style={progressSub}>{subtitle}</div>}
      </Link>
    </div>
  )
}

function subOfSources(s: Summary): string {
  if (s.need_key_count) return `${s.need_key_count} 个填 key 即可用`
  if (s.unavailable_count) return `${s.unavailable_count} 个通道未开`
  return '按市场分 · 每类一接口'
}

// ── 样式 ──────────────────────────────────────────

const rootStyle: React.CSSProperties = {
  borderBottom: `1px solid ${HUNTER.LINE}`,
  paddingBottom: 8,
}

const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 4,
  padding: '10px 12px 6px',
}

const sectionLabel: React.CSSProperties = {
  flex: 1,
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.4,
  color: HUNTER.INK_F,
}

const miniLink: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.THEME,
  textDecoration: 'none',
  padding: '2px 6px',
  borderRadius: 4,
  fontFamily: 'inherit',
}

const miniBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  background: 'none',
  border: 'none',
  color: HUNTER.INK_F,
  fontSize: 11,
  cursor: 'pointer',
  padding: '2px 4px',
  fontFamily: 'inherit',
}

const progressWrap: React.CSSProperties = {
  padding: '0 8px 4px',
}

const progressLink: React.CSSProperties = {
  display: 'block',
  padding: '6px 8px',
  borderRadius: 6,
  textDecoration: 'none',
  color: 'inherit',
  transition: 'background 0.1s',
}

const progressBg: React.CSSProperties = {
  marginTop: 4,
  height: 3,
  background: HUNTER.PANEL_2,
  borderRadius: 2,
  overflow: 'hidden',
}

const progressBar: React.CSSProperties = {
  height: '100%',
  background: HUNTER.SUCCESS,
  transition: 'width 0.3s',
}

const progressSub: React.CSSProperties = {
  marginTop: 3,
  fontSize: 10,
  color: HUNTER.INK_F,
}

const subheaderStyle: React.CSSProperties = {
  padding: '10px 14px 4px',
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: 0.4,
  color: HUNTER.INK_F,
  textTransform: 'uppercase',
}

const recentRow: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  width: '100%',
  padding: '5px 8px',
  background: 'transparent',
  border: 'none',
  borderRadius: 5,
  cursor: 'pointer',
  fontSize: 12,
  color: HUNTER.INK_S,
  fontFamily: 'inherit',
  textAlign: 'left',
  transition: 'background 0.1s',
}

const recentName: React.CSSProperties = {
  flex: 1,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const chipRow: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 4,
  padding: '0 12px 6px',
}

const catChip: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  padding: '3px 8px',
  fontSize: 10.5,
  color: HUNTER.INK_S,
  background: '#fbfaf5',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 10,
  textDecoration: 'none',
  transition: 'border-color 0.1s',
  fontFamily: 'inherit',
}

const addBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
  width: 19,
  height: 19,
  borderRadius: 5,
  cursor: 'pointer',
  background: 'transparent',
  border: 'none',
  color: HUNTER.INK_F,
  transition: 'color 0.1s',
}

const okBox: React.CSSProperties = {
  margin: '0 10px 8px',
  padding: '7px 9px',
  borderRadius: 7,
  cursor: 'pointer',
  background: HUNTER.TAG_OK_BG,
  color: HUNTER.TAG_OK_FG,
  fontSize: 11,
  lineHeight: 1.6,
}
