// 能力目录 API 客户端 · 三层模型(数据源 / 工具箱 / SKILL)
//
// 与 skillClient 分开:那个是聊天页在用的 /api/chat/skills,结构要保持稳定
// (Step A 迁移时特意做到逐字段零差异)。这里是"能力目录"视角,多带
// 依赖是否满足这类算出来的东西,混在一起会把那个接口撑变形。
//
// 这三个接口在 middleware 里是公开的 —— 它们只描述"这套部署能拿到什么",
// 不含任何凭证(只回 configured=true/false,不回 key 本身)。

export type SourceStatus = 'ok' | 'degraded' | 'down' | 'unknown' | 'need_key' | 'unavailable'

export interface DataSourceItem {
  key: string
  name: string
  market: string
  market_label: string
  kind: string
  kind_label: string
  provider: string
  tier: string
  endpoint: string
  volume_hint: string
  requires_key: boolean
  available: boolean
  unavailable_reason: string
  note: string
  configured: boolean
  status: SourceStatus
  health: { samples: number; success_rate: number; avg_ms: number | null; last_error: string } | null
}

export interface SourceGroup {
  market: string
  label: string
  total: number
  ready: number
  sources: DataSourceItem[]
}

export interface ToolItem {
  key: string
  name: string
  server: string
  server_label: string
  origin: string
  origin_label: string
  summary: string
  needs_data: string[]
  markets: string[]
  slow: boolean
  note: string
  status: 'ready' | 'partial' | 'need_key' | 'unavailable'
  blocked_by: string[]
  need_key_for: string[]
  degraded_by: string[]
}

export interface ToolGroup {
  server: string
  label: string
  total: number
  ready: number
  tools: ToolItem[]
}

export interface CatalogSkillItem {
  key: string
  name: string
  icon: string
  hint: string
  category: string
  brand: string
  source_url: string
  prompt_tpl: string
  builtin: boolean
  needs_tools: string[]
  missing_tools: string[]
  blocked_tools: string[]
  status: 'ready' | 'blocked' | 'broken'
}

export interface SkillGroup {
  category: string
  total: number
  ready: number
  skills: CatalogSkillItem[]
}

export interface Summary {
  total: number
  ready: number
  headline: string
  need_key_count?: number
  unavailable_count?: number
  user_added?: number
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api/catalog${path}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`catalog${path} ${res.status}`)
  return res.json()
}

export const listSources = () => get<{ groups: SourceGroup[]; summary: Summary }>('/sources')
export const listToolbox = () => get<{ groups: ToolGroup[]; summary: Summary }>('/toolbox')
export const listCatalogSkills = () => get<{ groups: SkillGroup[]; summary: Summary }>('/skills')

// 状态点的颜色语义 —— 四种状态对应四种用户动作,别混:
//   unavailable 开源版没这条通道 · 用户做什么都没用
//   need_key    申请一把 key 就能用 ← 最该被看见的一种
//   ok/degraded 通道与凭证都齐了,是上游的事
//   unknown     还没调用过 · **不假装健康**
export function statusDot(s: SourceStatus | string): { color: string; label: string } {
  switch (s) {
    case 'ok':
    case 'ready':       return { color: '#3F8F6B', label: '正常' }
    case 'partial':     return { color: '#C08A2E', label: '部分数据缺' }
    case 'degraded':    return { color: '#C08A2E', label: '不稳定' }
    case 'down':        return { color: '#B5462F', label: '故障' }
    case 'need_key':    return { color: '#B06A32', label: '需要 key' }
    case 'unavailable': return { color: '#B9B4A8', label: '通道未开' }
    default:            return { color: '#CFCBBF', label: '未调用过' }
  }
}
