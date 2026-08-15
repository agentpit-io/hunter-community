'use client'

// 侧栏「能力」区 · 三层模型重排(_14 §6 Step C)
//
// 改之前:平铺 29 张 SKILL 卡 + 一个「查看全部工具 (29)」抽屉。
// 问题是用户只看得见最上面那一层,底下靠什么数据、什么工具在跑,一个字都没有。
// 查美股查不出来时,他分不清是没数据、没配 key、还是根本没通道 —— 三种情况
// 该做的事完全不同,却长得一模一样。
//
// 改之后三块:
//   数据源   按 A股/港股/美股/全球 分 · 每条带状态点与量级
//   工具箱   MCP 与自有工具**算一类**(用户原话),来源只体现在一个小标签上
//   SKILL    按后端 category 分 · 点一下把提问模板填进输入框
//
// 顺序是有意的:数据源在最上面,因为它是"我能拿到什么"的根;SKILL 在最下面
// 但**默认展开**,因为它是用户每天真正要点的东西。
//
// 【未解锁时】三块**照常全部显示**,该标锁的标锁。藏起来会让用户以为开源版
// 没这些能力 —— 恰恰相反,要让他看见再去拿 key。

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Lock, Settings2, Database, Wrench, Sparkles, Clock, Plus } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  listSources, listToolbox, listCatalogSkills, statusDot,
  type SourceGroup, type ToolGroup, type SkillGroup, type Summary,
} from '../lib/catalogClient'
import { getUnlockStatus, onUnlockChange, peekUnlockStatus } from '../lib/unlockClient'
import UnlockModal from './UnlockModal'
import SkillInstallCard from './SkillInstallCard'

interface Props {
  onPick: (tpl: string, key: string) => void
  onManage: () => void
  refreshKey?: number
}

type BlockId = 'sources' | 'toolbox' | 'skills'

export default function CapabilityPanel({ onPick, onManage, refreshKey }: Props) {
  // SKILL 默认展开 —— 它是每天要点的;另外两块是"想知道时才看"的
  const [open, setOpen] = useState<Record<BlockId, boolean>>({
    sources: false, toolbox: false, skills: true,
  })
  const [sources, setSources] = useState<{ groups: SourceGroup[]; summary: Summary } | null>(null)
  const [toolbox, setToolbox] = useState<{ groups: ToolGroup[]; summary: Summary } | null>(null)
  const [skills, setSkills] = useState<{ groups: SkillGroup[]; summary: Summary } | null>(null)
  // null = 还没问出来。用三态而不是 boolean —— 初始化成 true 会让面板闪一下锁,
  // 初始化成 false 又会在真锁着时先放行一次点击。
  const [unlocked, setUnlocked] = useState<boolean | null>(peekUnlockStatus()?.unlocked ?? null)
  const [gate, setGate] = useState<string | null>(null)
  // 装 skill 的面板 —— 直接开在侧栏里,不用先进「管理」再往下滚
  const [installOpen, setInstallOpen] = useState(false)
  const [installed, setInstalled] = useState('')
  const locked = unlocked === false

  useEffect(() => {
    // 三个接口各自失败各自静默 —— 能力面板挂掉绝不能影响聊天
    listSources().then(setSources).catch(() => {})
    listToolbox().then(setToolbox).catch(() => {})
    listCatalogSkills().then(setSkills).catch(() => {})
  }, [refreshKey])

  useEffect(() => {
    void getUnlockStatus().then((st) => setUnlocked(st.unlocked)).catch(() => {})
    return onUnlockChange((st) => setUnlocked(st.unlocked))
  }, [])

  const toggle = useCallback((id: BlockId) => {
    setOpen((o) => ({ ...o, [id]: !o[id] }))
  }, [])

  const handlePick = useCallback((tpl: string, key: string) => {
    // 自建能力不门控:那是用户自己接的数据源,与平台 key 无关
    if (locked && !key.startsWith('custom:')) { setGate(key); return }
    onPick(tpl, key)
  }, [locked, onPick])

  return (
    <div style={{ borderBottom: `1px solid ${HUNTER.LINE}`, paddingBottom: 6, marginBottom: 2 }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '10px 12px 4px' }}>
        <div style={sectionLabel}>能力</div>
        <button onClick={onManage} style={miniLink} title="管理你的能力">
          <Settings2 size={11} strokeWidth={1.5} style={{ marginRight: 3 }} />
          管理
        </button>
      </div>

      {/* ① 数据源 */}
      <Block
        id="sources" icon={Database} title="数据源"
        headline={sources?.summary.headline}
        // 这个副标题是整块的重点:告诉用户"还有 N 个申请个 key 就能用"
        sub={sources ? subOfSources(sources.summary) : ''}
        open={open.sources} onToggle={toggle}
      >
        {sources?.groups.map((g) => (
          <div key={g.market} style={{ marginBottom: 6 }}>
            <div style={groupHead}>
              <span>{g.label}</span>
              <span style={{ color: HUNTER.INK_F, fontWeight: 400 }}>{g.ready}/{g.total}</span>
            </div>
            {g.sources.map((s) => {
              const dot = statusDot(s.status)
              return (
                <div key={s.key} style={row}
                     title={s.unavailable_reason || s.note || `${dot.label}${s.endpoint ? ' · ' + s.endpoint : ''}`}>
                  <span style={{ ...dotStyle, background: dot.color }} />
                  <span style={rowName}>{s.name}</span>
                  <span style={rowMeta}>{s.volume_hint || dot.label}</span>
                </div>
              )
            })}
          </div>
        ))}
        {/* 美股/港股那些"有数据但通道未开"的,必须有一句人话解释,
            否则用户看到一排灰点只会以为坏了 */}
        {sources && sources.summary.unavailable_count ? (
          <div style={footNote}>
            灰点 = 平台已建成但开源版暂无通道(需直连数据库),共 {sources.summary.unavailable_count} 个
          </div>
        ) : null}
      </Block>

      {/* ② 工具箱 · MCP 与自有工具算一类 */}
      <Block
        id="toolbox" icon={Wrench} title="工具箱"
        headline={toolbox?.summary.headline}
        sub="模型可以直接调用的能力"
        open={open.toolbox} onToggle={toggle}
      >
        {toolbox?.groups.map((g) => (
          <div key={g.server} style={{ marginBottom: 6 }}>
            <div style={groupHead}>
              <span>{g.label}</span>
              <span style={{ color: HUNTER.INK_F, fontWeight: 400 }}>{g.ready}/{g.total}</span>
            </div>
            {g.tools.map((t) => {
              const dot = statusDot(t.status)
              return (
                <div key={t.key} style={row} title={`${t.summary}${t.note ? ' · ' + t.note : ''}`}>
                  <span style={{ ...dotStyle, background: dot.color }} />
                  <span style={rowName}>{t.name}</span>
                  {t.slow && <Clock size={9} strokeWidth={1.8} style={{ color: HUNTER.SOFT, flexShrink: 0 }} />}
                  {t.degraded_by?.length > 0 && (
                    <span style={{ ...tagStyle, borderColor: '#C08A2E', color: '#9B571F' }}
                          title={`可用,但这些数据源缺: ${t.degraded_by.join(', ')}`}>部分</span>
                  )}
                  <span style={{ ...tagStyle, opacity: t.origin === 'platform' ? 1 : 0.55 }}>
                    {t.origin_label}
                  </span>
                </div>
              )
            })}
          </div>
        ))}
      </Block>

      {/* ③ SKILL · 唯一可点的一块 */}
      <Block
        id="skills" icon={Sparkles} title="SKILL"
        headline={skills?.summary.headline}
        sub={skills?.summary.user_added ? `含你加的 ${skills.summary.user_added} 个` : '点一下开始提问'}
        open={open.skills} onToggle={toggle}
        action={
          <button
            onClick={(e) => { e.stopPropagation(); setOpen((o) => ({ ...o, skills: true })); setInstallOpen((v) => !v) }}
            title="从 GitHub 装,或自己写一个"
            style={addBtn}
            onMouseEnter={(e) => { e.currentTarget.style.color = HUNTER.THEME }}
            onMouseLeave={(e) => { e.currentTarget.style.color = HUNTER.INK_F }}
          >
            <Plus size={13} strokeWidth={2.2} />
          </button>
        }
      >
        {installOpen && (
          <div style={{ margin: '2px 10px 8px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 8, overflow: 'hidden' }}>
            <SkillInstallCard
              onClose={() => setInstallOpen(false)}
              onInstalled={(names, msg) => {
                setInstallOpen(false)
                setInstalled(msg || `已装好:${names.join('、')}`)
                listCatalogSkills().then(setSkills).catch(() => {})
              }}
            />
          </div>
        )}
        {installed && (
          <div style={okBox} onClick={() => setInstalled('')}>{installed}(点击关闭)</div>
        )}
        {skills?.groups.map((g) => (
          <div key={g.category} style={{ marginBottom: 7 }}>
            <div style={groupHead}>
              <span>{g.category}</span>
              <span style={{ color: HUNTER.INK_F, fontWeight: 400 }}>{g.total}</span>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '0 10px' }}>
              {g.skills.map((s) => (
                <button
                  key={s.key}
                  onClick={() => handlePick(s.prompt_tpl, s.key)}
                  title={`${s.hint}${s.brand ? ' · ' + s.brand : ''}${
                    s.status !== 'ready' ? '\n⚠ 依赖未就绪: ' + [...s.missing_tools, ...s.blocked_tools].join(', ') : ''}`}
                  style={{ ...chip, opacity: s.status === 'ready' ? 1 : 0.55 }}
                  onMouseEnter={(e) => { e.currentTarget.style.borderColor = HUNTER.THEME }}
                  onMouseLeave={(e) => { e.currentTarget.style.borderColor = HUNTER.LINE }}
                >
                  <span style={{ fontSize: 11 }}>{s.icon}</span>
                  <span style={chipName}>{s.name}</span>
                  {locked && <Lock size={9} strokeWidth={1.6} style={{ color: HUNTER.SOFT, flexShrink: 0 }} />}
                </button>
              ))}
            </div>
          </div>
        ))}
      </Block>

      {gate !== null && <UnlockModal triggeredBy={gate || undefined} onClose={() => setGate(null)} />}
    </div>
  )
}


// ── 折叠块外壳 ────────────────────────────────────────────────

function Block({ id, icon: Icon, title, headline, sub, open, onToggle, action, children }: {
  id: BlockId
  icon: typeof Database
  title: string
  headline?: string
  sub?: string
  open: boolean
  onToggle: (id: BlockId) => void
  /** 块标题右侧的操作按钮 —— 「加一个」这种动作要贴着它作用的东西放,
   *  藏进通用设置里用户找不到(实测反馈) */
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div style={{ position: 'relative' }}>
      <button onClick={() => onToggle(id)} style={blockHead}
              onMouseEnter={(e) => { e.currentTarget.style.background = HUNTER.PANEL_2 }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}>
        {open ? <ChevronDown size={12} strokeWidth={2} /> : <ChevronRight size={12} strokeWidth={2} />}
        <Icon size={12} strokeWidth={1.6} style={{ color: HUNTER.SOFT }} />
        <span style={{ flex: 1, textAlign: 'left', fontWeight: 600 }}>{title}</span>
        {/* headline 没拿到时显示 — 而不是 0/0,免得像"什么都没有" */}
        <span style={{ fontSize: 11, color: HUNTER.INK_F, fontVariantNumeric: 'tabular-nums' }}>
          {headline ?? '—'}
        </span>
      </button>
      {/* action 放在折叠按钮**外面** —— 嵌在 button 里会变成"点加号也切换折叠" */}
      {action}
      {open && (
        <div style={{ paddingBottom: 4 }}>
          {sub ? <div style={subLine}>{sub}</div> : null}
          {children}
        </div>
      )}
    </div>
  )
}

function subOfSources(s: Summary): string {
  if (s.need_key_count) return `${s.need_key_count} 个填上 key 即可用`
  return '按市场分 · 每类数据一个接口'
}


// ── 样式 ──────────────────────────────────────────────────────

const sectionLabel: React.CSSProperties = {
  flex: 1, fontSize: 11, fontWeight: 600, letterSpacing: 0.4, color: HUNTER.INK_F,
}
const miniLink: React.CSSProperties = {
  display: 'flex', alignItems: 'center', background: 'none', border: 'none',
  color: HUNTER.INK_F, fontSize: 11, cursor: 'pointer', padding: '2px 4px', fontFamily: 'inherit',
}
const blockHead: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, width: '100%',
  padding: '6px 12px', background: 'transparent', border: 'none',
  fontSize: 12.5, color: HUNTER.INK_S, cursor: 'pointer', fontFamily: 'inherit',
  transition: 'background 0.1s',
}
const subLine: React.CSSProperties = {
  padding: '0 12px 4px 30px', fontSize: 10.5, color: HUNTER.INK_F,
}
const groupHead: React.CSSProperties = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  padding: '3px 12px 2px 30px', fontSize: 10.5, fontWeight: 600, color: HUNTER.INK_F,
}
const row: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6,
  padding: '2px 12px 2px 30px', fontSize: 11.5, color: HUNTER.INK_S,
}
const rowName: React.CSSProperties = {
  flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
}
const rowMeta: React.CSSProperties = {
  fontSize: 10, color: HUNTER.INK_F, flexShrink: 0,
  maxWidth: 96, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
}
const dotStyle: React.CSSProperties = {
  width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
}
const tagStyle: React.CSSProperties = {
  fontSize: 9, color: HUNTER.INK_F, border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 3, padding: '0 3px', flexShrink: 0,
}
const chip: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 3,
  padding: '3px 7px', borderRadius: 5, cursor: 'pointer',
  background: '#fbfaf5', border: `1px solid ${HUNTER.LINE}`,
  fontSize: 11, color: HUNTER.INK_S, fontFamily: 'inherit',
  transition: 'border-color 0.1s',
}
const chipName: React.CSSProperties = {
  maxWidth: 118, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
}
const addBtn: React.CSSProperties = {
  position: 'absolute', right: 34, top: 5,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  width: 20, height: 20, borderRadius: 5, cursor: 'pointer',
  background: 'transparent', border: 'none', color: HUNTER.INK_F,
  transition: 'color 0.1s',
}
const okBox: React.CSSProperties = {
  margin: '0 10px 8px', padding: '7px 9px', borderRadius: 7, cursor: 'pointer',
  background: HUNTER.TAG_OK_BG, color: HUNTER.TAG_OK_FG, fontSize: 11, lineHeight: 1.6,
}
const footNote: React.CSSProperties = {
  padding: '4px 12px 2px 30px', fontSize: 10, color: HUNTER.INK_F, lineHeight: 1.5,
}
