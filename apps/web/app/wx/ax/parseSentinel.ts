/** Sentinel 报告文本解析器
 *
 * 后端格式源头：agents/sentinel_news_agent.py `_format_report()`（稳定）
 *
 * 输入示例：
 *   【新闻情报报告 — Sentinel 系统 · 大智慧】
 *
 *   📊 分析范围：共抓取 11 条相关新闻
 *   🛡️ 过滤结果：过滤 4 条投毒/低质内容，保留 7 条通过核查的有效信息
 *
 *   ✅ 已验证核心事实（高可信度）：
 *     1. [界面新闻] 大智慧预计...（可信度：0.70）
 *     2. [证券时报] ...（可信度：0.10）
 *
 *   ⚠️ 被过滤内容摘要（不建议作为投资依据）：
 *     - 大金融板块异动拉升...（使用了"异动"等主观词...）
 *
 *   📉 综合研判：看空（置信度 75%）
 *   🟢 止损条件：未触发
 */

export interface VerifiedFact { source: string; fact: string; weight: number }
export interface RejectedItem { text: string; reason: string }

export interface SentinelParsed {
  stats: { total: number; kept: number; dropped: number } | null
  verified: VerifiedFact[]
  rejected: RejectedItem[]
  opinion: string
  confidencePct: number | null
  killTriggered: boolean
  killDesc: string
  /** 无法结构化解析的部分（降级消息、格式变更兜底） */
  rawFallback: string
}

const RE_STATS   = /共抓取\s*(\d+)\s*条[\s\S]*?过滤\s*(\d+)\s*条[\s\S]*?保留\s*(\d+)\s*条/
const RE_VERIFIED = /^\s*\d+\.\s*\[([^\]]+)\]\s*(.*?)（可信度：([\d.]+)）\s*$/
const RE_REJECTED = /^\s*-\s*(.*?)（(.*?)）\s*$/
const RE_OPINION  = /综合研判：(.+?)（置信度\s*(\d+)%）/
const RE_KILL_ON  = /止损条件已触发：(.*)/
const RE_KILL_OFF = /止损条件：未触发/

export function parseSentinelReport(raw: string): SentinelParsed {
  const out: SentinelParsed = {
    stats: null, verified: [], rejected: [], opinion: '',
    confidencePct: null, killTriggered: false, killDesc: '', rawFallback: '',
  }
  if (!raw || typeof raw !== 'string') return out

  const m = raw.match(RE_STATS)
  if (m) out.stats = { total: +m[1], dropped: +m[2], kept: +m[3] }

  let inVerified = false, inRejected = false
  for (const line of raw.split('\n')) {
    if (line.includes('已验证核心事实')) { inVerified = true; inRejected = false; continue }
    if (line.includes('被过滤内容摘要')) { inVerified = false; inRejected = true; continue }
    if (line.includes('综合研判')) { inVerified = false; inRejected = false }

    if (inVerified) {
      const vm = line.match(RE_VERIFIED)
      if (vm) out.verified.push({ source: vm[1], fact: vm[2].trim(), weight: parseFloat(vm[3]) })
    }
    if (inRejected) {
      const rm = line.match(RE_REJECTED)
      if (rm) out.rejected.push({ text: rm[1].trim(), reason: rm[2].trim() })
    }

    const om = line.match(RE_OPINION)
    if (om) { out.opinion = om[1].trim(); out.confidencePct = +om[2] }
    const km = line.match(RE_KILL_ON)
    if (km) { out.killTriggered = true; out.killDesc = km[1].trim() }
    if (RE_KILL_OFF.test(line)) out.killTriggered = false
  }

  // 无法解析出任何结构化数据 → 保留原文兜底
  const hasStructure = !!out.stats || out.verified.length > 0 || out.rejected.length > 0 || !!out.opinion
  if (!hasStructure) out.rawFallback = raw.trim()

  return out
}

/** 可信度分级：>=70% 高 / 40-69% 中 / <40% 低 */
export function weightTier(w: number): { icon: string; color: string; label: string } {
  const pct = Math.round(w * 100)
  if (pct >= 70) return { icon: '🟢', color: '#3F6B40', label: `${pct}%` }
  if (pct >= 40) return { icon: '🟡', color: '#B06A32', label: `${pct}%` }
  return { icon: '⚪', color: '#7A6F63', label: `${pct}%` }
}
