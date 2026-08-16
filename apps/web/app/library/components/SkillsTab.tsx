'use client'
import { useMemo } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { SkillGroup, CatalogSkillItem } from '../../chat/lib/catalogClient'
import EntityCard from './EntityCard'

interface Props {
  groups: SkillGroup[]
  activeGroup?: string
  search?: string
  selected: CatalogSkillItem | null
  onSelect: (item: CatalogSkillItem) => void
  /** 双击行 = 把 prompt_tpl 送到 chat 输入框(走 /chat?q= autoText 通道) */
  onPickToChat?: (item: CatalogSkillItem) => void
}

export default function SkillsTab({ groups, activeGroup, search, selected, onSelect, onPickToChat }: Props) {
  const filteredGroups = useMemo(() => {
    let gs = activeGroup ? groups.filter((g) => g.category === activeGroup) : groups
    if (search) {
      const q = search.toLowerCase()
      gs = gs.map((g) => ({
        ...g,
        skills: g.skills.filter((s) =>
          s.name.toLowerCase().includes(q) ||
          s.hint?.toLowerCase().includes(q) ||
          s.category?.toLowerCase().includes(q) ||
          s.brand?.toLowerCase().includes(q) ||
          s.prompt_tpl?.toLowerCase().includes(q)
        ),
      })).filter((g) => g.skills.length > 0)
    }
    return gs
  }, [groups, activeGroup, search])

  const totalCount = filteredGroups.reduce((a, g) => a + g.skills.length, 0)

  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>
      <div style={headStyle}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: HUNTER.INK, margin: 0, fontFamily: HUNTER.SERIF }}>
          ✨ SKILL 库
        </h1>
        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>
          显示 {totalCount} 项
        </span>
      </div>

      {filteredGroups.length === 0 && (
        <div style={emptyStyle}>
          没有匹配的 SKILL
          {search && (
            <div style={{ marginTop: 8, fontSize: 12 }}>
              没找到 "{search}" · Phase 2 可从 GitHub 装扩展 SKILL
            </div>
          )}
        </div>
      )}

      {filteredGroups.map((g) => (
        <div key={g.category} style={{ marginBottom: 20 }}>
          <div style={groupHeadStyle}>
            <span>{g.category}</span>
            <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{g.ready}/{g.total}</span>
          </div>
          {g.skills.map((s) => (
            <EntityCard
              key={s.key}
              icon={s.icon}
              title={s.name}
              subtitle={s.hint}
              status={s.status}
              tags={[
                { text: s.builtin ? '内置' : (s.source_url ? 'GitHub' : '自建'), tone: 'default' },
              ]}
              meta={s.brand || undefined}
              selected={selected?.key === s.key}
              onClick={() => onSelect(s)}
              onDoubleClick={onPickToChat ? () => onPickToChat(s) : undefined}
              doubleClickHint={onPickToChat ? '单击查看详情 · 双击直接填入对话框' : undefined}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

const headStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  marginBottom: 16,
}

const groupHeadStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  fontSize: 13,
  fontWeight: 600,
  color: HUNTER.INK_S,
  padding: '0 4px 6px',
}

const emptyStyle: React.CSSProperties = {
  padding: '40px 20px',
  textAlign: 'center',
  color: HUNTER.INK_F,
  fontSize: 13,
}
