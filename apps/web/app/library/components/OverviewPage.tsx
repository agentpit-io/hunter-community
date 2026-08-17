'use client'
// 概览页 · 3 类总览 + 待处理提示
import Link from 'next/link'
import { HUNTER } from '../../lib/hunter-theme'
import type {
  SourceGroup, ToolGroup, SkillGroup, Summary,
} from '../../chat/lib/catalogClient'
import { buildQuery } from '../lib/nav'

interface Props {
  sources: { groups: SourceGroup[]; summary: Summary } | null
  tools:   { groups: ToolGroup[];   summary: Summary } | null
  skills:  { groups: SkillGroup[];  summary: Summary } | null
}

export default function OverviewPage({ sources, tools, skills }: Props) {
  return (
    <div style={wrapStyle}>
      <h1 style={titleStyle}>能力库 · 全景</h1>
      <p style={subtitleStyle}>
        当前部署已装的所有能力 · 一屏看健康 · 找到问题直接进 tab 处理
      </p>

      <Card
        icon="📊"
        label="数据源"
        summary={sources?.summary}
        // 跳过空组 —— 「你自己的」在没接任何源时是 0/0,而 0/0 夹在
        // 一排 "7/7 6/7" 中间读起来像"坏了"。它的空状态该在数据源页
        // 里展开讲(那里有地方说清楚),不是这张概览卡
        groups={sources?.groups.filter((g) => g.total > 0).map((g) => ({
          label: g.label, ready: g.ready, total: g.total,
        })) || []}
        href={buildQuery({ tab: 'sources' })}
      />

      <Card
        icon="🛠"
        label="工具箱"
        summary={tools?.summary}
        groups={tools?.groups.map((g) => ({
          label: g.label, ready: g.ready, total: g.total,
        })) || []}
        href={buildQuery({ tab: 'tools' })}
      />

      <Card
        icon="✨"
        label="SKILL 库"
        summary={skills?.summary}
        groups={skills?.groups.map((g) => ({
          label: g.category, ready: g.ready, total: g.total,
        })) || []}
        href={buildQuery({ tab: 'skills' })}
        extra={skills?.summary.user_added != null ? `自建 ${skills.summary.user_added}` : undefined}
      />

      <div style={managementBoxStyle}>
        <div style={{ fontSize: 12, color: HUNTER.INK_F, marginBottom: 8 }}>
          管理操作(将在 Phase 2 开放)
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button style={btnDisabledStyle} disabled>＋ 添加自定义 SKILL / MCP</button>
          <button style={btnDisabledStyle} disabled>↻ 恢复所有 SKILL 到初始</button>
        </div>
      </div>
    </div>
  )
}

function Card({ icon, label, summary, groups, href, extra }: {
  icon: string
  label: string
  summary?: Summary
  groups: { label: string; ready: number; total: number }[]
  href: string
  extra?: string
}) {
  const total = summary?.total ?? 0
  const ready = summary?.ready ?? 0
  const pct = total > 0 ? Math.round((ready / total) * 100) : 0
  return (
    <Link href={href} style={cardStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 20 }}>{icon}</span>
          <span style={{ fontSize: 15, fontWeight: 600, color: HUNTER.INK }}>{label}</span>
        </div>
        <span style={{ fontSize: 13, color: HUNTER.INK_F }}>
          {ready} / {total} 就绪
        </span>
      </div>

      <div style={progressBgStyle}>
        <div style={{ ...progressBarStyle, width: `${pct}%` }} />
      </div>

      <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: '4px 12px' }}>
        {groups.map((g, i) => (
          <span key={i} style={{ fontSize: 11, color: HUNTER.INK_F }}>
            {g.label} <span style={{ color: HUNTER.INK }}>{g.ready}/{g.total}</span>
          </span>
        ))}
      </div>

      {(summary?.headline || extra) && (
        <div style={{ marginTop: 8, fontSize: 11, color: HUNTER.INK_F }}>
          {summary?.headline}{extra ? ` · ${extra}` : ''}
        </div>
      )}
    </Link>
  )
}

const wrapStyle: React.CSSProperties = {
  padding: '20px 24px',
  maxWidth: 720,
}

const titleStyle: React.CSSProperties = {
  fontSize: 20,
  fontWeight: 700,
  color: HUNTER.INK,
  margin: 0,
  fontFamily: HUNTER.SERIF,
}

const subtitleStyle: React.CSSProperties = {
  fontSize: 12,
  color: HUNTER.INK_F,
  marginTop: 4,
  marginBottom: 20,
}

const cardStyle: React.CSSProperties = {
  display: 'block',
  padding: 16,
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: HUNTER.R_LG,
  marginBottom: 12,
  textDecoration: 'none',
  color: 'inherit',
  transition: 'border-color 0.1s, box-shadow 0.1s',
}

const progressBgStyle: React.CSSProperties = {
  marginTop: 12,
  height: 6,
  background: HUNTER.PANEL_2,
  borderRadius: 3,
  overflow: 'hidden',
}

const progressBarStyle: React.CSSProperties = {
  height: '100%',
  background: HUNTER.SUCCESS,
  transition: 'width 0.3s',
}

const managementBoxStyle: React.CSSProperties = {
  marginTop: 24,
  padding: 16,
  background: HUNTER.PANEL,
  borderRadius: HUNTER.R_LG,
}

const btnDisabledStyle: React.CSSProperties = {
  padding: '8px 14px',
  fontSize: 13,
  color: HUNTER.SOFT,
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: HUNTER.R_MD,
  cursor: 'not-allowed',
  fontFamily: 'inherit',
}
