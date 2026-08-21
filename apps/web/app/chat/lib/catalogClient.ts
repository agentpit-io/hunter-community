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
  /** 用户点它时替他填进输入框的那句话(`_22`)· 空 = 不进能力列表 */
  prompt_tpl: string
  /** 能不能出现在能力列表里 —— 判据在后端 to_dict 一处算好,
   *  前端不要自己重算 `prompt_tpl && !internal_only`,抄第二份就会漂 */
  pickable: boolean
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
  // `/api/catalog/*` 免登录可访问,但**带上 token 才能看到「你自己的」那组**。
  // 这里原来一个 header 都不发 —— 于是用户加完数据源,列表里仍显示
  // "你还没有接自己的数据源"。保存成功了、库里也有,就是看不见。
  //
  // 「免登录可访问」不等于「不认识用户」。后端对公开路径做的是
  // 可选身份识别:token 有效就认,没有或无效就当匿名,都不拒绝。
  const headers: Record<string, string> = {}
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) headers['Authorization'] = `Bearer ${t}`
  }
  const res = await fetch(`/api/catalog${path}`, { headers, cache: 'no-store' })
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

// ── 统一能力(`_22` 步 3)────────────────────────────────────
//
// SKILL 与工具合成一个列表。**合的是入口,不是实体** ——
// 每条都带 kind,但那只是卡片上一个小记号,不是分类维度。

export interface CapabilityItem {
  key: string
  name: string
  icon: string
  kind: 'skill' | 'tool'
  kind_label: string          // "带方法论" / "直接执行"
  category: string
  hint: string
  prompt_tpl: string
  brand: string
  builtin: boolean
  /** 从哪个仓库装来的 · `github:owner/repo@ref` · 内置项与手动新建的为空 */
  origin: string
  slow: boolean
  blocked_by: string[]
  status: 'ready' | 'blocked' | 'broken'
}

export interface CapabilityGroup {
  category: string
  total: number
  ready: number
  items: CapabilityItem[]
}

export const listCapabilities = () =>
  get<{ groups: CapabilityGroup[]; summary: Summary }>('/capabilities')
