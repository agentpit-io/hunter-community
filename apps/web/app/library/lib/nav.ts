// 能力库导航配置 · 静态部分(3 tab)+ 动态部分(每 tab 的 group 从 API 拿)
// 见方案 §3.1: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md

export type TabId = 'overview' | 'sources' | 'tools' | 'skills'

export interface TabConfig {
  id: TabId
  icon: string
  label: string
  /** 该 tab 是否有 group(overview 没有 · 直接就是概览页) */
  hasGroups: boolean
}

export const TABS: TabConfig[] = [
  { id: 'overview', icon: '📁', label: '概览',      hasGroups: false },
  { id: 'sources',  icon: '📊', label: '数据源',    hasGroups: true },
  { id: 'tools',    icon: '🛠',  label: '工具箱',    hasGroups: true },
  { id: 'skills',   icon: '✨', label: 'SKILL 库',   hasGroups: true },
]

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
  const tab = (get('tab') || 'overview') as TabId
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
