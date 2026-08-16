// SKILL 使用频次统计 · localStorage · 供 CapabilityPanel "最近用 top N"
//
// 存储:
//   hunter_skill_usage = { [key: string]: { count: number; lastAt: number } }
// 排序:
//   lastAt 降序(最近用优先)· count 用于 v2 "常用"标签
//
// 用户点侧栏 SKILL / 输入模板 / 从概览进入 · 都算一次 track。
// 用户在 /library 详情面板试用也算(Phase 2 再接)。

const KEY = 'hunter_skill_usage'

export interface SkillUsage {
  count: number
  lastAt: number   // ms epoch
}

type UsageMap = Record<string, SkillUsage>

function read(): UsageMap {
  if (typeof window === 'undefined') return {}
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return {}
    const d = JSON.parse(raw)
    return typeof d === 'object' && d ? d : {}
  } catch {
    return {}
  }
}

function write(m: UsageMap): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(KEY, JSON.stringify(m))
    // 广播 · CapabilityPanel 监听 storage 事件 · 无需硬 reload
    window.dispatchEvent(new Event('hunter-skill-usage'))
  } catch {
    /* 隐私模式 · 忽略 */
  }
}

/** 记一次使用 · 幂等 · O(1) */
export function trackSkillUsage(skillKey: string): void {
  if (!skillKey) return
  const m = read()
  const prev = m[skillKey] || { count: 0, lastAt: 0 }
  m[skillKey] = { count: prev.count + 1, lastAt: Date.now() }
  write(m)
}

/** 拿"最近用" top N · 按 lastAt 降序 */
export function getRecentSkills(n = 5): string[] {
  const m = read()
  return Object.entries(m)
    .sort((a, b) => b[1].lastAt - a[1].lastAt)
    .slice(0, n)
    .map(([k]) => k)
}

/** 拿"最常用" top N · 按 count 降序(v2 用) */
export function getTopSkills(n = 5): string[] {
  const m = read()
  return Object.entries(m)
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, n)
    .map(([k]) => k)
}

/** 清空所有使用记录 · /library "恢复初始" 会调用(Phase 2) */
export function clearSkillUsage(): void {
  if (typeof window === 'undefined') return
  try {
    localStorage.removeItem(KEY)
    window.dispatchEvent(new Event('hunter-skill-usage'))
  } catch { /* ignore */ }
}
