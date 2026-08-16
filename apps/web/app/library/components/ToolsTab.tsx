'use client'
import { useMemo } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { ToolGroup, ToolItem } from '../../chat/lib/catalogClient'
import EntityCard from './EntityCard'

interface Props {
  groups: ToolGroup[]
  activeGroup?: string
  search?: string
  selected: ToolItem | null
  onSelect: (item: ToolItem) => void
}

export default function ToolsTab({ groups, activeGroup, search, selected, onSelect }: Props) {
  const filteredGroups = useMemo(() => {
    let gs = activeGroup ? groups.filter((g) => g.server === activeGroup) : groups
    if (search) {
      const q = search.toLowerCase()
      gs = gs.map((g) => ({
        ...g,
        tools: g.tools.filter((t) =>
          t.name.toLowerCase().includes(q) ||
          t.summary?.toLowerCase().includes(q) ||
          t.server_label?.toLowerCase().includes(q)
        ),
      })).filter((g) => g.tools.length > 0)
    }
    return gs
  }, [groups, activeGroup, search])

  const totalCount = filteredGroups.reduce((a, g) => a + g.tools.length, 0)

  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>
      <div style={headStyle}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: HUNTER.INK, margin: 0, fontFamily: HUNTER.SERIF }}>
          🛠 工具箱
        </h1>
        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>
          显示 {totalCount} 项
        </span>
      </div>

      {filteredGroups.length === 0 && (
        <div style={emptyStyle}>没有匹配的工具</div>
      )}

      {filteredGroups.map((g) => (
        <div key={g.server} style={{ marginBottom: 20 }}>
          <div style={groupHeadStyle}>
            <span>{g.label}</span>
            <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>{g.ready}/{g.total}</span>
          </div>
          {g.tools.map((t) => (
            <EntityCard
              key={t.key}
              title={t.name}
              subtitle={t.summary}
              status={t.status}
              tags={[
                { text: t.origin_label || t.origin, tone: 'default' },
                ...(t.slow ? [{ text: '慢', tone: 'warn' as const }] : []),
              ]}
              meta={t.markets?.length ? t.markets.join('/') : undefined}
              selected={selected?.key === t.key}
              onClick={() => onSelect(t)}
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
