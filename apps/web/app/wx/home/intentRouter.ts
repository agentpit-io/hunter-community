/**
 * 研究助手 · 用户自然语言意图 → 智能体路由
 *
 * 目前用关键词表匹配（快、无网络依赖、无成本）。若线上误判率 > 15%，
 * 未来可替换成 LLM 意图分类（Gemini Flash 一次调用即可）。
 */

export type ResearchMode = 'assistant' | 'research' | 'scout' | 'kpred' | 'hold' | 'event'

// 目标智能体子集（不含 assistant 本身）
export type TargetMode = Exclude<ResearchMode, 'assistant'>

const INTENT_KEYWORDS: Record<TargetMode, string[]> = {
  hold:     ['还能拿', '该不该拿', '撤不撤', '止损', '持仓', '要不要卖', '继续拿', '拿不拿'],
  event:    ['新闻', '事件', '公告', '利好', '利空', '影响', '最近发生', '什么消息', '突发'],
  kpred:    ['买点', '卖点', '时机', '什么时候', '预测', '走势', '入场', '几号', '啥时候'],
  scout:    ['机构', '调研', '北向', '研发', '一手', '资金流', '主力', '大单'],
  research: ['怎么样', '值不值', '分析', '看看', '了解', '基本面', '业绩'],
}

// 优先级顺序：更"具体"的意图先匹配（避免"影响"命中 event 后被 research 兜底）
const PRIORITY: TargetMode[] = ['hold', 'event', 'kpred', 'scout', 'research']

export function routeByIntent(q: string): { mode: TargetMode; matched: boolean } {
  const trimmed = (q || '').trim().toLowerCase()
  if (!trimmed) return { mode: 'research', matched: false }
  for (const mode of PRIORITY) {
    if (INTENT_KEYWORDS[mode].some(kw => trimmed.includes(kw.toLowerCase()))) {
      return { mode, matched: true }
    }
  }
  return { mode: 'research', matched: false }  // 兜底
}

// 目标智能体的中文标签，用于路由后 toast 提示
export const MODE_LABEL: Record<TargetMode, string> = {
  research: '深度研究',
  scout:    '一手情报',
  kpred:    '量化择时',
  hold:     '持仓研判',
  event:    '事件解读',
}
