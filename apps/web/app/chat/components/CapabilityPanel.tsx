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
  listSources, listToolbox, listCatalogSkills, listCapabilities,
  type SourceGroup, type ToolGroup, type SkillGroup, type Summary,
  type CatalogSkillItem, type CapabilityGroup,
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

/** 统一的「能力」—— SKILL 与工具在入口层归一(`_22` §3)。
 *
 *  `kind` 只用来在卡片上标一个小记号(📋 带方法论 / 🔧 直接执行),
 *  **不是分类**。用户不需要理解它,但当他好奇"为什么这个 90 秒那个 2 秒"时,
 *  这个记号给了答案。 */
interface Capability {
  key: string
  name: string
  icon: string
  prompt_tpl: string
  kind: 'skill' | 'tool'
  status: string
  hint?: string
  brand?: string
  /** 依赖没就绪的项 · 用于置灰与 title 提示 */
  missing: string[]
}

const RECENT_N = 5

export default function CapabilityPanel({ onPick, onManage, refreshKey }: Props) {
  const [sources, setSources] = useState<{ groups: SourceGroup[]; summary: Summary } | null>(null)
  const [toolbox, setToolbox] = useState<{ groups: ToolGroup[]; summary: Summary } | null>(null)
  const [skills, setSkills]   = useState<{ groups: SkillGroup[]; summary: Summary } | null>(null)
  const [caps, setCaps]       = useState<{ groups: CapabilityGroup[]; summary: Summary } | null>(null)
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
    listCapabilities().then(setCaps).catch(() => {})
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

  // ── 统一「能力」反查表(`_22` §3)────────────────────────────
  //
  // **直接用 /catalog/capabilities**,不在前端把 skills + toolbox 再拼一遍。
  // 拼一遍的代价是判据要抄两份:哪些工具已被 SKILL 代表、哪些 pickable ——
  // 抄两份的结果是侧栏和 /library 显示的项不一样,而用户看不出为什么。
  const capByKey = useMemo(() => {
    const m = new Map<string, Capability>()
    caps?.groups.forEach((g) => g.items.forEach((i) => m.set(i.key, {
      key: i.key, name: i.name, icon: i.icon,
      prompt_tpl: i.prompt_tpl, kind: i.kind,
      status: i.status, hint: i.hint, brand: i.brand,
      missing: i.blocked_by || [],
    })))
    return m
  }, [caps])

  // 最近用 top N · 已 track 的过滤存在的 · 不够就补默认(第一批高频)
  const recent = useMemo(() => {
    if (!caps) return [] as Capability[]
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const _ = recentTick   // 让 useMemo 依赖 tick
    const trackedKeys = getRecentSkills(RECENT_N * 2)   // 拿多点 · 过滤后可能不够
    const items: Capability[] = []
    for (const k of trackedKeys) {
      const s = capByKey.get(k)
      if (s) items.push(s)
      if (items.length >= RECENT_N) break
    }
    // 补齐默认(用户还没用过时至少让他看到几个入口)。
    // 里面**特意放了两个工具** —— 加自选股是老板点名要的"一句话加自选",
    // 行情速查是最高频的问法。原来这两条只能通过薄壳 SKILL 触达,
    // 而薄壳马上要删(步 4),默认位得先换成工具本身
    if (items.length < RECENT_N) {
      // 用的是**列表里真实存在的 key**:quickview 已被 quote SKILL 代表,
      // 所以这里写 quote 而不是那个工具 —— 写工具的话它查不到,
      // 默认位会静默少一个
      const DEFAULTS = ['uzi_quick_scan', 'watchlist_watchlist_add',
                        'quote', 'forecast', 'uzi_dcf']
      for (const k of DEFAULTS) {
        if (items.length >= RECENT_N) break
        if (items.some((x) => x.key === k)) continue
        const s = capByKey.get(k)
        if (s) items.push(s)
      }
    }
    return items
  }, [caps, capByKey, recentTick])

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

      {/* ② 能力 · 工具与 SKILL 合成一条(`_22` 步 3)
             原来是「工具箱」「SKILL 库」两行 —— 那是把我们的实现细节
             推给了用户。他要判断的是"这个平台能替我做多少事",
             一个数字就够了 */}
      <ProgressLine
        icon="✨"
        title="能力"
        summary={caps?.summary}
        href="/library?tab=capabilities"
        subtitle={caps?.summary
          ? `📋 ${(caps.summary as any).skills ?? 0} 带方法论 · 🔧 ${(caps.summary as any).tools ?? 0} 直接执行`
            + (caps.summary.user_added ? ` · 含你加的 ${caps.summary.user_added}` : '')
          : undefined}
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
              // 装完 SKILL 两份都要重拉:skills 给这个面板的类目下拉,
              // caps 给合并后的能力计数与「最近用」。只刷一份的表现是
              // "装好了但计数没动",用户会以为没装上
              listCatalogSkills().then(setSkills).catch(() => {})
              listCapabilities().then(setCaps).catch(() => {})
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
                title={`${s.hint || ''}${s.brand ? ' · ' + s.brand : ''}`
                  + `\n${s.kind === 'tool' ? '🔧 直接执行' : '📋 带方法论'}`
                  + (s.status !== 'ready' && s.missing.length
                      ? '\n⚠ 依赖未就绪: ' + s.missing.join(', ') : '')}
                style={{ ...recentRow, opacity: s.status === 'ready' ? 1 : 0.6 }}
                onMouseEnter={(e) => { e.currentTarget.style.background = HUNTER.PANEL_2 }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{ fontSize: 13, flexShrink: 0 }}>{s.icon}</span>
                <span style={recentName}>{s.name}</span>
                {/* 工具标个 🔧 —— 侧栏一行放不下"直接执行"四个字,
                    完整说明在 title 里。不标的话用户分不出为什么
                    有的秒回有的要等一分钟 */}
                {s.kind === 'tool' && (
                  <span style={{ fontSize: 9, color: HUNTER.SOFT, flexShrink: 0 }}>🔧</span>
                )}
                {locked && <Lock size={9} strokeWidth={1.6} style={{ color: HUNTER.SOFT, flexShrink: 0 }} />}
              </button>
            ))}
          </div>
        </>
      )}

      {/* 所有类目 chip */}
      {caps && caps.groups.length > 0 && (
        <>
          <div style={subheaderStyle}>所有类目</div>
          <div style={chipRow}>
            {caps.groups.map((g) => (
              <Link
                key={g.category}
                href={`/library?tab=capabilities&group=${encodeURIComponent(g.category)}`}
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
