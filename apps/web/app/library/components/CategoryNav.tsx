'use client'
// 左侧分类导航(240px)· 3 tab · 每 tab 若干 group
// 见方案 §3.1: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md
import Link from 'next/link'
import { HUNTER } from '../../lib/hunter-theme'
import { TABS, type TabId, type LibraryQuery, buildQuery } from '../lib/nav'
import type { SourceGroup, ToolGroup, SkillGroup } from '../../chat/lib/catalogClient'

interface Props {
  query: LibraryQuery
  sources: SourceGroup[] | null
  tools: ToolGroup[] | null
  skills: SkillGroup[] | null
  /** 点「＋ 添加」· 由页面按当前 tab 决定加什么(_20 §2) */
  onAdd?: () => void
  /** 点「↻ 恢复初始」· 删掉当前 tab 的全部用户自定义项 */
  onReset?: () => void
}

export default function CategoryNav({ query, sources, tools, skills, onAdd, onReset }: Props) {
  // 概览页没有"当前在加什么"的上下文,所以这两个操作只在具体 tab 下可用
  const actionable = query.tab !== 'overview'
  return (
    <nav style={navStyle}>
      {TABS.map((tab) => {
        const isActiveTab = query.tab === tab.id
        return (
          <div key={tab.id} style={{ marginBottom: 4 }}>
            <Link href={buildQuery({ tab: tab.id })} style={tabHeadStyle(isActiveTab && !query.group)}>
              <span style={{ fontSize: 15 }}>{tab.icon}</span>
              <span>{tab.label}</span>
            </Link>

            {tab.id === 'sources' && sources && (
              <GroupList tabId={tab.id} groups={sources.map(g => ({
                id: g.market, label: g.label, total: g.total, ready: g.ready,
              }))} activeGroup={isActiveTab ? query.group : undefined} />
            )}
            {tab.id === 'tools' && tools && (
              <GroupList tabId={tab.id} groups={tools.map(g => ({
                id: g.server, label: g.label, total: g.total, ready: g.ready,
              }))} activeGroup={isActiveTab ? query.group : undefined} />
            )}
            {tab.id === 'skills' && skills && (
              <GroupList tabId={tab.id} groups={skills.map(g => ({
                id: g.category, label: g.category, total: g.total, ready: g.ready,
              }))} activeGroup={isActiveTab ? query.group : undefined} />
            )}
          </div>
        )
      })}

      <div style={separatorStyle} />

      <div style={{ padding: '0 12px' }}>
        <button
          style={mgmtBtnStyle(!actionable)} disabled={!actionable}
          onClick={onAdd}
          title={actionable ? ADD_HINT[query.tab] : '先选一个分类(数据源 / 工具箱 / SKILL)'}
        >＋ 添加</button>
        <button
          style={mgmtBtnStyle(!actionable)} disabled={!actionable}
          onClick={onReset}
          title={actionable ? '删掉这一类里你自己加的全部条目 · 内置的不受影响' : '先选一个分类'}
        >↻ 恢复初始</button>
      </div>
    </nav>
  )
}

const ADD_HINT: Record<string, string> = {
  sources: '添加自己的数据源',
  tools: '接入自己的 MCP 工具',
  skills: '从 GitHub 装,或自己写一个',
}

function GroupList({ tabId, groups, activeGroup }: {
  tabId: TabId
  groups: { id: string; label: string; total: number; ready: number }[]
  activeGroup?: string
}) {
  if (groups.length === 0) return null
  return (
    <div style={{ marginTop: 2, marginBottom: 4 }}>
      {groups.map((g) => {
        const isActive = activeGroup === g.id
        const label = g.label || g.id
        return (
          <Link key={g.id} href={buildQuery({ tab: tabId, group: g.id })} style={groupItemStyle(isActive)}>
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {label}
            </span>
            <span style={{ fontSize: 11, color: HUNTER.INK_F, marginLeft: 8 }}>
              {g.ready}/{g.total}
            </span>
          </Link>
        )
      })}
    </div>
  )
}

const navStyle: React.CSSProperties = {
  width: 240,
  minWidth: 240,
  height: '100%',
  background: HUNTER.PANEL,
  borderRight: `1px solid ${HUNTER.LINE}`,
  padding: '16px 0',
  overflowY: 'auto',
  fontFamily: HUNTER.SANS,
}

const tabHeadStyle = (active: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '8px 16px',
  fontSize: 13,
  fontWeight: 600,
  color: active ? HUNTER.THEME : HUNTER.INK,
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  textDecoration: 'none',
  cursor: 'pointer',
  transition: 'background 0.1s',
})

const groupItemStyle = (active: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  padding: '5px 16px 5px 40px',
  fontSize: 12,
  color: active ? HUNTER.THEME : HUNTER.INK_S,
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  textDecoration: 'none',
  cursor: 'pointer',
  transition: 'background 0.1s',
})

const separatorStyle: React.CSSProperties = {
  margin: '16px 12px',
  borderTop: `1px solid ${HUNTER.LINE}`,
}

const mgmtBtnStyle = (disabled: boolean): React.CSSProperties => ({
  width: '100%',
  padding: '6px 12px',
  marginBottom: 6,
  fontSize: 12,
  color: disabled ? HUNTER.SOFT : HUNTER.INK_S,
  background: 'transparent',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: HUNTER.R_SM,
  cursor: disabled ? 'not-allowed' : 'pointer',
  fontFamily: 'inherit',
  textAlign: 'left',
})
