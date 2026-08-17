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
  /** 我们**怎么取到**它(finance-data 网关 / 直连库 / 本地路由) */
  provider: string
  /** 数据**原本来自谁**(xtick / eastmoney / sec …)· `_21` §1.2 —— 这才是分组依据 */
  upstream: string
  upstream_label: string
  /** official | user —— 用户自己接的排在最前,且不可被"恢复初始"以外的操作删掉 */
  owner: string
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
  /** 分组主键 —— 按来源分组时是 upstream slug('user' / 'akshare' / …) */
  upstream: string
  label: string
  owner: 'official' | 'user'
  /** 这一组覆盖了哪些市场 · 市场已降级成筛选条(`_21` §2) */
  markets: string[]
  total: number
  ready: number
  sources: DataSourceItem[]
  /** 旧的按市场分组仍会返回它(概览页在用)· 按来源分组时不存在 */
  market?: string
}

/** 市场筛选条的选项 —— 由后端从注册表算出,不在前端写死 */
export interface MarketOption {
  value: string
  label: string
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

export interface SourcesResponse {
  groups: SourceGroup[]
  group_by: 'upstream' | 'market'
  markets: MarketOption[]
  summary: Summary
}

/** 数据源清单 · **默认按来源分组**(`_21` §2)。
 *  `market` 是筛选条,不是分组维度 —— 传了它后端会重算每组计数,
 *  所以侧栏显示的 N/M 永远和点进去看到的条数一致。 */
export const listSources = (market?: string) =>
  get<SourcesResponse>(`/sources${market ? `?market=${encodeURIComponent(market)}` : ''}`)
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
