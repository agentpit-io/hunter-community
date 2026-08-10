/**
 * 研究助手 · 智能推荐规则
 *
 * 基于用户当前股票状态推荐下一步动作。本次不做事件推荐（缺 /api/events 端点）。
 * 未来可加：近 7 天事件 → 事件解读。
 */
import type { TargetMode } from './intentRouter'

export interface RecommendationInput {
  stockCode: string          // 当前用户选中的股票代码；空字符串代表未选
  stockName: string
  isInWatchlist: boolean     // 是否在自选股
  hasThesis: boolean         // 是否已录入持仓 thesis
}

export interface Recommendation {
  mode: TargetMode
  title: string
  reason: string
  cta:   string
}

/** 返回推荐；返回 null 时前端应该展示"先选股"引导 */
export function getRecommendation(input: RecommendationInput): Recommendation | null {
  const { stockCode, stockName, isInWatchlist, hasThesis } = input
  if (!stockCode) return null

  const name = stockName || stockCode

  // 优先级 1：已录 thesis → 持仓研判（多空辩论复核持仓假设）
  if (hasThesis) {
    return {
      mode:   'hold',
      title:  '持仓研判',
      reason: `你已录入 ${name} 的持仓逻辑，AI 帮你多空辩论复核假设是否成立`,
      cta:    '开始研判',
    }
  }

  // 优先级 2：在自选股（无 thesis）→ 深度研究（先建立认知框架）
  if (isInWatchlist) {
    return {
      mode:   'research',
      title:  '深度研究',
      reason: `${name} 在你的自选，3 分钟建立完整认知框架`,
      cta:    '开始研究',
    }
  }

  // 优先级 3：非自选、无 thesis → 深度研究兜底
  return {
    mode:   'research',
    title:  '深度研究',
    reason: `快速了解 ${name} 的基本面、技术面和 Kronos 量化视角`,
    cta:    '开始研究',
  }
}
