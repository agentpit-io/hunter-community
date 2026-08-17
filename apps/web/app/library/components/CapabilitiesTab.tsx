'use client'

// 统一「能力」列表 · `_22` 步 3
//
// 原来是「工具箱」和「SKILL 库」两个并列的顶层分类。老板的观察是对的:
// 从用户视角两者都是「点一下开始干活的东西」,分成两栏只是把我们的
// 实现细节推给了用户。
//
// **合的是入口,不是实体。**每条仍带 kind(📋 带方法论 / 🔧 直接执行),
// 但那是卡片上一个小记号,不是分类维度 —— 分组按**用途**走
// (快速判断 / 估值建模 / 组合级 …),因为"我想估值"才是用户会问的问题,
// "这是工具还是 SKILL"不是。

import { useMemo, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { CapabilityGroup, CapabilityItem } from '../../chat/lib/catalogClient'

interface Props {
  groups: CapabilityGroup[]
  activeGroup?: string
  search?: string
  selected: CapabilityItem | null
  onSelect: (item: CapabilityItem) => void
  /** 双击 = 跳对话框并填好模板(老板要的那个动作) */
  onUse: (item: CapabilityItem) => void
}

type KindFilter = '' | 'skill' | 'tool'

export default function CapabilitiesTab({
  groups, activeGroup, search, selected, onSelect, onUse,
}: Props) {
  // 类型筛选条 —— 平时不该用到,但当用户确实想问"哪些是直接执行的"时,
  // 得有地方回答。默认全部
  const [kind, setKind] = useState<KindFilter>('')

  const filtered = useMemo(() => {
    let gs = activeGroup ? groups.filter((g) => g.category === activeGroup) : groups
    const q = (search || '').toLowerCase()
    if (q || kind) {
      gs = gs.map((g) => ({
        ...g,
        items: g.items.filter((i) =>
          (!kind || i.kind === kind) &&
          (!q || i.name.toLowerCase().includes(q)
              || i.hint?.toLowerCase().includes(q)
              || i.prompt_tpl?.toLowerCase().includes(q)),
        ),
      })).filter((g) => g.items.length > 0)
    }
    return gs
  }, [groups, activeGroup, search, kind])

  const total = filtered.reduce((a, g) => a + g.items.length, 0)
  const skillN = filtered.reduce((a, g) => a + g.items.filter((i) => i.kind === 'skill').length, 0)

  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>
      <div style={headStyle}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: HUNTER.INK, margin: 0, fontFamily: HUNTER.SERIF }}>
          ✨ 能力
        </h1>
        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>
          {total} 项 · 📋 {skillN} 带方法论 · 🔧 {total - skillN} 直接执行
        </span>
      </div>

      <div style={chipRow}>
        <Chip label="全部" active={!kind} onClick={() => setKind('')} />
        <Chip label="📋 带方法论" active={kind === 'skill'} onClick={() => setKind('skill')} />
        <Chip label="🔧 直接执行" active={kind === 'tool'} onClick={() => setKind('tool')} />
      </div>

      <div style={hintBar}>
        <b>双击</b>任意一项 → 跳到对话框并填好提问模板。单击看详情。
      </div>

      {filtered.length === 0 && <div style={emptyStyle}>没有匹配的能力</div>}

      {filtered.map((g) => (
        <div key={g.category} style={{ marginBottom: 20 }}>
          <div style={groupHeadStyle}>
            <span>{g.category}</span>
            <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{g.ready}/{g.total}</span>
          </div>
          {g.items.map((i) => (
            <Card key={i.key} item={i}
                  selected={selected?.key === i.key}
                  onSelect={() => onSelect(i)}
                  onUse={() => onUse(i)} />
          ))}
        </div>
      ))}
    </div>
  )
}

function Card({ item, selected, onSelect, onUse }: {
  item: CapabilityItem; selected: boolean
  onSelect: () => void; onUse: () => void
}) {
  const blocked = item.status !== 'ready'
  return (
    <div
      onClick={onSelect}
      onDoubleClick={onUse}
      // 双击是主操作但**不可发现** —— 键盘可达 + 一个显式的"用它"按钮
      // 兜底。只有双击的话,不知道这个约定的用户就永远用不上
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onUse() }}
      title={`${item.hint || ''}\n${item.kind === 'tool' ? '🔧 直接执行' : '📋 带方法论'}`
        + (item.slow ? ' · 耗时较长' : '')
        + (blocked && item.blocked_by.length ? `\n⚠ 依赖未就绪: ${item.blocked_by.join(', ')}` : '')}
      style={{ ...cardStyle, ...(selected ? cardSelected : null), opacity: blocked ? 0.55 : 1 }}
    >
      <span style={{ fontSize: 15, flexShrink: 0 }}>{item.icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: HUNTER.INK }}>{item.name}</span>
          <span style={kindTag(item.kind)}>{item.kind_label}</span>
          {item.slow && <span style={{ fontSize: 10.5, color: HUNTER.INK_F }}>⏱ 慢</span>}
          {!item.builtin && <span style={{ fontSize: 10.5, color: HUNTER.THEME }}>你加的</span>}
        </div>
        {/* 显示的是**提问模板**而不是功能描述 —— 用户要判断的是
            "点了它会发生什么",模板就是答案,而且他还能照着改 */}
        <div style={tplStyle}>{item.prompt_tpl || item.hint}</div>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onUse() }}
        style={useBtn} title="跳到对话框并填好这句话"
      >用它 →</button>
    </div>
  )
}

function Chip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return <button onClick={onClick} style={chipStyle(active)}>{label}</button>
}

const headStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 12,
}
const chipRow: React.CSSProperties = { display: 'flex', gap: 6, marginBottom: 10 }
const chipStyle = (active: boolean): React.CSSProperties => ({
  padding: '3px 12px', fontSize: 12, borderRadius: 999,
  border: `1px solid ${active ? HUNTER.THEME : HUNTER.LINE}`,
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  color: active ? HUNTER.THEME : HUNTER.INK_S,
  cursor: 'pointer', fontFamily: 'inherit',
})
const hintBar: React.CSSProperties = {
  fontSize: 11.5, color: HUNTER.INK_F, marginBottom: 14, lineHeight: 1.7,
}
const groupHeadStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
  fontSize: 13, fontWeight: 600, color: HUNTER.INK_S, padding: '0 4px 6px',
}
const cardStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10,
  padding: '9px 12px', marginBottom: 6, borderRadius: 9,
  border: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER,
  cursor: 'pointer', userSelect: 'none',
}
const cardSelected: React.CSSProperties = {
  borderColor: HUNTER.THEME, background: HUNTER.BRAND_PALE,
}
const kindTag = (kind: string): React.CSSProperties => ({
  fontSize: 10, padding: '1px 6px', borderRadius: 4,
  background: kind === 'tool' ? '#EDF2F0' : '#F3EEE6',
  color: kind === 'tool' ? '#4A6B5E' : '#7A6244',
})
const tplStyle: React.CSSProperties = {
  fontSize: 11.5, color: HUNTER.INK_F, marginTop: 2,
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
}
const useBtn: React.CSSProperties = {
  flexShrink: 0, padding: '4px 10px', fontSize: 11, borderRadius: 6,
  border: `1px solid ${HUNTER.LINE}`, background: 'transparent',
  color: HUNTER.INK_S, cursor: 'pointer', fontFamily: 'inherit',
}
const emptyStyle: React.CSSProperties = {
  padding: '40px 20px', textAlign: 'center', color: HUNTER.INK_F, fontSize: 13,
}
