// 能力面板 API 客户端 · 走 hermes-api /api/chat/skills
// 与 opencodeClient 分开:能力是我们自己的业务数据,不经 opencode

export interface SkillItem {
  key: string          // 内置为 quote/forecast…;自建为 custom:{id}
  builtin: boolean
  icon: string
  name: string
  prompt_tpl: string   // 含 {股票} 占位符
  hint: string
  brand?: string       // 品牌名（如 "Kronos" / "UZI" / "Hunter 内置"）· 仅内置有
  source_url?: string  // 品牌来源（GitHub / 文档 URL）· 可空
  category?: string    // 分类（综合分析/估值建模/…）· 用于分组展示
  enabled: boolean
  sort_order: number
  id: number | null
}

export interface SkillDetail {
  key: string
  icon: string
  name: string
  brand: string
  category: string
  source_url: string
  prompt_tpl: string
  hint: string
  long_desc: string
  tools: string[]
}

export interface SkillListResp {
  items: SkillItem[]
  custom_count: number
  max_custom: number
  category_order?: string[]  // 后端返回的分类展示顺序 · 前端 SkillManager 按此渲染分组
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) h['Authorization'] = `Bearer ${t}`
  }
  return h
}

async function req<T = any>(method: string, path: string, body?: any): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    let msg = t.slice(0, 160)
    try {
      msg = JSON.parse(t)?.detail || msg
    } catch {
      /* 非 JSON 错误体,用原文 */
    }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

export const listSkills = () => req<SkillListResp>('GET', '/chat/skills')

/** 新建自建能力的表单。
 *
 * 后端自 2026-08-15 起把它**写成 user-skills/{slug}/SKILL.md 文件**而不是数据库行,
 * 所以能带方法论正文与依赖声明 —— 跟内置那 23 个是同一种东西了。
 * 只填前三个也能建(旧 UI 的行为),其余走默认值。
 */
export interface SkillDraft {
  name: string                 // 显示名(中文即可)
  prompt_tpl: string
  icon?: string
  slug?: string                // 目录名(英文)· 不填由后端从显示名生成
  description?: string         // **给模型看的**:它据此判断这次要不要用
  category?: string
  needs_tools?: string[]
  needs_data?: string[]
  body?: string                // Markdown 方法论正文
}

/** 写入结果 · `synced=false` 时 opencode 还没认到,必须把 message 显示给用户 */
export interface SkillWriteResp {
  ok: boolean
  key?: string
  synced?: boolean
  skill_count?: number
  needs_restart?: boolean
  message?: string
  reason?: string
}

export const createSkill = (draft: SkillDraft) =>
  req<SkillWriteResp>('POST', '/chat/skills', {
    icon: '⭐', needs_tools: [], needs_data: [], ...draft,
  })

export const patchSkill = (
  key: string,
  patch: Partial<Pick<SkillItem, 'name' | 'icon' | 'prompt_tpl' | 'enabled' | 'sort_order'>>,
) => req('PATCH', `/chat/skills/${encodeURIComponent(key)}`, patch)

export const deleteSkill = (key: string) =>
  req<SkillWriteResp>('DELETE', `/chat/skills/${encodeURIComponent(key)}`)

export const resetSkills = () => req('POST', '/chat/skills/reset', {})

// 内置能力详情（公开无需登录 · 供 /skills/[key] 页读）· 自定义能力无此端点
export const getSkillDetail = (key: string) =>
  req<SkillDetail>('GET', `/chat/skills/detail/${encodeURIComponent(key)}`)

/**
 * 模板里第一个 {xxx} 占位符的位置 —— 填入输入框后光标停在这,用户直接打股票名。
 * 返回 null 表示没有占位符(整条直接可发)。
 */
export function placeholderRange(tpl: string): { start: number; end: number } | null {
  const m = /\{[^}]*\}/.exec(tpl)
  return m ? { start: m.index, end: m.index + m[0].length } : null
}
