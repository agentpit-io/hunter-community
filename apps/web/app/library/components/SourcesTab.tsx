'use client'
import { useMemo } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { SourceGroup, DataSourceItem } from '../../chat/lib/catalogClient'
import EntityCard from './EntityCard'

interface Props {
  groups: SourceGroup[]
  activeGroup?: string
  search?: string
  selected: DataSourceItem | null
  onSelect: (item: DataSourceItem) => void
}

export default function SourcesTab({ groups, activeGroup, search, selected, onSelect }: Props) {
  const filteredGroups = useMemo(() => {
    let gs = activeGroup ? groups.filter((g) => g.market === activeGroup) : groups
    if (search) {
      const q = search.toLowerCase()
      gs = gs.map((g) => ({
        ...g,
        sources: g.sources.filter((s) =>
          s.name.toLowerCase().includes(q) ||
          s.kind_label?.toLowerCase().includes(q) ||
          s.provider?.toLowerCase().includes(q)
        ),
      })).filter((g) => g.sources.length > 0)
    }
    return gs
  }, [groups, activeGroup, search])

  const totalCount = filteredGroups.reduce((a, g) => a + g.sources.length, 0)

  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>
      <div style={headStyle}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: HUNTER.INK, margin: 0, fontFamily: HUNTER.SERIF }}>
          📊 数据源
        </h1>
        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>
          显示 {totalCount} 项
        </span>
      </div>

      {filteredGroups.length === 0 && (
        <div style={emptyStyle}>没有匹配的数据源</div>
      )}

      {filteredGroups.map((g) => (
        <div key={g.market} style={{ marginBottom: 20 }}>
          <div style={groupHeadStyle}>
            <span>{g.label}</span>
            <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{g.ready}/{g.total}</span>
          </div>
          {g.sources.map((s) => (
            <EntityCard
              key={s.key}
              title={s.name}
              subtitle={[s.kind_label, s.provider].filter(Boolean).join(' · ')}
              status={s.status}
              meta={s.volume_hint || (s.available ? undefined : '通道未开')}
              selected={selected?.key === s.key}
              onClick={() => onSelect(s)}
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
