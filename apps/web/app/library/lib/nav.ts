// 能力库导航配置 · 静态部分(3 tab)+ 动态部分(每 tab 的 group 从 API 拿)
// 见方案 §3.1: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md

// `capabilities` 是 `_22` 步 3 合并出来的:原来的 tools + skills 两个顶层
// 分类合成一个「能力」。合的是**入口**,不是实体 —— 每个条目仍带 kind
// (📋 带方法论 / 🔧 直接执行),但那只是个记号,不是分类维度。
//
// `tools` / `skills` 两个 id **保留**:老链接(侧栏 chip、文档里的 URL、
// 用户收藏)都指向它们,直接删会让那些链接落到概览页,而用户不知道为什么。
// parseQuery 把它们重定向到 capabilities。
export type TabId = 'overview' | 'sources' | 'capabilities' | 'tools' | 'skills'

export interface TabConfig {
  id: TabId
  icon: string
  label: string
  /** 该 tab 是否有 group(overview 没有 · 直接就是概览页) */
  hasGroups: boolean
}

export const TABS: TabConfig[] = [
  { id: 'overview',     icon: '📁', label: '概览',   hasGroups: false },
  { id: 'sources',      icon: '📊', label: '数据源', hasGroups: true },
  { id: 'capabilities', icon: '✨', label: '能力',   hasGroups: true },
]

/** 老 tab → 新 tab。删掉分类不该让旧链接静默落到概览页 */
const TAB_ALIAS: Record<string, TabId> = {
  tools: 'capabilities',
  skills: 'capabilities',
}

/** URL query state · /library?tab=X&group=Y&search=Z */
export interface LibraryQuery {
  tab: TabId
  group?: string
  search?: string
}

export function parseQuery(searchParams: URLSearchParams | Record<string, string | string[] | undefined>): LibraryQuery {
  const get = (k: string): string | undefined => {
    if (searchParams instanceof URLSearchParams) return searchParams.get(k) || undefined
    const v = (searchParams as any)[k]
    return Array.isArray(v) ? v[0] : v
  }
  const raw = (get('tab') || 'overview') as TabId
  // 先过别名:?tab=tools / ?tab=skills 的老链接落到合并后的「能力」,
  // 而不是静默退回概览页
  const tab = TAB_ALIAS[raw] || raw
  const validTab = TABS.some((t) => t.id === tab) ? tab : 'overview'
  return {
    tab: validTab,
    group: get('group'),
    search: get('search'),
  }
}

export function buildQuery(q: Partial<LibraryQuery>): string {
  const params = new URLSearchParams()
  if (q.tab && q.tab !== 'overview') params.set('tab', q.tab)
  if (q.group) params.set('group', q.group)
  if (q.search) params.set('search', q.search)
  const s = params.toString()
  return s ? `/library?${s}` : '/library'
}
