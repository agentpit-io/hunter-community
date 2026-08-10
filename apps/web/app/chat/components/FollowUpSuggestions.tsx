'use client'
/**
 * FollowUpSuggestions · 每条 assistant 回复完成后 · 生成 5 个深入追问建议
 *
 * 规则式（非 LLM · 零延迟零成本）:
 *   1. 从当前 session 最近 user msg + assistant reply 抽 symbol / 主题
 *   2. 命中股票 · 给 stock-specific 5 追问
 *   3. 未命中 · 给通用深化 5 追问
 *
 * 与 SkillPanel 的区别:
 *   SkillPanel = 用户主动能力入口 (固定 5 内置 + 自定义)
 *   FollowUp    = 场景化深化 (根据当前对话内容动态生成)
 */
import { Sparkles } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import type { Message, MessagePartText } from '../lib/types'

// 首批常见股票 · 覆盖多数场景 · 未来接 stocks_catalog
const KNOWN_STOCKS = new Map<string, string>([
  ['600519', '贵州茅台'],
  ['000858', '五粮液'],
  ['600036', '招商银行'],
  ['000001', '平安银行'],
  ['601318', '中国平安'],
  ['600031', '三一重工'],
  ['000651', '格力电器'],
  ['000333', '美的集团'],
  ['600276', '恒瑞医药'],
  ['300750', '宁德时代'],
  ['002594', '比亚迪'],
  ['600887', '伊利股份'],
  ['600009', '上海机场'],
  ['600030', '中信证券'],
  ['600690', '海尔智家'],
  ['000725', '京东方 A'],
  ['600585', '海螺水泥'],
  ['0700.HK', '腾讯控股'],
  ['9988.HK', '阿里巴巴'],
  ['1810.HK', '小米集团'],
  ['3690.HK', '美团'],
  ['9999.HK', '网易'],
  ['AAPL', 'Apple'],
  ['TSLA', 'Tesla'],
  ['NVDA', 'NVIDIA'],
  ['MSFT', 'Microsoft'],
  ['GOOGL', 'Google'],
  ['BABA', '阿里 (美股)'],
  ['PDD', '拼多多'],
])

interface Detected {
  code?: string
  name?: string
  topic?: 'valuation' | 'trend' | 'fundamentals' | 'compare' | 'policy' | 'generic'
}

function extractSymbol(text: string): { code?: string; name?: string } {
  if (!text) return {}
  // 优先匹配代码
  const codeMatch = text.match(/(\d{6}(?:\.SH|\.SZ)?|\d{5}\.HK|[A-Z]{2,5}(?![a-z]))/i)
  if (codeMatch) {
    const raw = codeMatch[1].toUpperCase().replace(/\.SH$|\.SZ$/i, '')
    const key = KNOWN_STOCKS.has(raw) ? raw : codeMatch[1].toUpperCase()
    return { code: key, name: KNOWN_STOCKS.get(key) }
  }
  // 匹配中文股票名
  for (const [code, name] of KNOWN_STOCKS) {
    if (text.includes(name)) return { code, name }
    // 去掉品牌后缀模糊匹配
    const shortName = name.replace(/(集团|股份|控股|A)$/, '')
    if (shortName.length >= 2 && text.includes(shortName)) {
      return { code, name }
    }
  }
  return {}
}

function detectTopic(text: string): Detected['topic'] {
  if (!text) return 'generic'
  const t = text.toLowerCase()
  if (/估值|pe|pb|市盈|市净/i.test(text)) return 'valuation'
  if (/走势|k线|涨跌|回调|支撑|阻力|技术/i.test(text)) return 'trend'
  if (/财报|营收|利润|现金流|应收|毛利/i.test(text)) return 'fundamentals'
  if (/对比|vs|同业|竞争/i.test(text)) return 'compare'
  if (/政策|监管|规则|行业/i.test(text)) return 'policy'
  return 'generic'
}

function suggest(detected: Detected): string[] {
  const { code, name, topic } = detected
  const label = name || code

  if (label) {
    // stock-specific · 按 topic 微调
    switch (topic) {
      case 'valuation':
        return [
          `${label} 当前估值处于历史什么分位`,
          `与主要竞争对手估值对比`,
          `未来 3 年估值合理区间`,
          `${label} 的成长性能否支撑当前估值`,
          `估值下修的主要风险有哪些`,
        ]
      case 'trend':
        return [
          `${label} 短期支撑位和阻力位在哪`,
          `近期北向资金流向`,
          `${label} 龙虎榜近 30 日情况`,
          `技术形态给出的下一步方向`,
          `融资余额环比变化`,
        ]
      case 'fundamentals':
        return [
          `${label} 应收账款有无异常`,
          `现金流质量如何 · 与净利润差距`,
          `毛利率与同业的对比`,
          `商誉减值风险扫描`,
          `管理层股权激励与业绩指引`,
        ]
      case 'compare':
        return [
          `深入对比 ${label} 与同业的市占率`,
          `${label} 的核心竞争力差异化在哪`,
          `估值溢价 / 折价的合理性`,
          `未来 3 年谁更有增长空间`,
          `${label} 的护城河是否在收窄`,
        ]
      default:
        return [
          `深入分析 ${label} 的估值水平`,
          `${label} 最近的北向资金流向`,
          `${label} 有哪些主要风险`,
          `${label} 与主要竞争对手对比`,
          `为 ${label} 起草投资论点`,
        ]
    }
  }

  // 通用深化 · 无股票
  return [
    '举个具体的例子说明',
    '这里面有哪些风险需要注意',
    '给出结论的信心度评估',
    '数据来源是什么 · 引用出处',
    '换个反面角度看这个问题',
  ]
}

interface Props {
  messages: Message[]
  currentIndex: number // 当前 assistant message 在 messages 中的位置
  onPick: (text: string) => void
}

export default function FollowUpSuggestions({ messages, currentIndex, onPick }: Props) {
  // 找最近的 user + assistant text · 用于抽 symbol / topic
  const lastAssistantText = messages
    .slice(0, currentIndex + 1)
    .reverse()
    .find((m) => m.role === 'assistant')?.parts
    .filter((p): p is MessagePartText => p.type === 'text')
    .map((p) => p.text)
    .join(' ') || ''

  const lastUserText = messages
    .slice(0, currentIndex + 1)
    .reverse()
    .find((m) => m.role === 'user')?.parts
    .filter((p): p is MessagePartText => p.type === 'text')
    .map((p) => p.text)
    .join(' ') || ''

  const combined = `${lastUserText} ${lastAssistantText}`
  const detected: Detected = {
    ...extractSymbol(combined),
    topic: detectTopic(combined),
  }

  const followUps = suggest(detected)

  return (
    <div
      style={{
        marginTop: 12,
        padding: '10px 0 4px',
        borderTop: `1px dashed ${HUNTER.LINE}`,
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: HUNTER.INK_F,
          marginBottom: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          fontWeight: 500,
        }}
      >
        <Sparkles size={11} style={{ color: HUNTER.THEME }} />
        深入追问 · 帮你想
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {followUps.map((q, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onPick(q)}
            style={{
              padding: '5px 12px',
              background: '#f8f6ef',
              border: `1px solid ${HUNTER.LINE}`,
              borderRadius: 16,
              color: HUNTER.INK_S,
              fontSize: 12,
              cursor: 'pointer',
              transition: 'all 0.15s',
              fontFamily: 'inherit',
              lineHeight: 1.4,
              textAlign: 'left',
              maxWidth: 480,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#f0ede2'
              e.currentTarget.style.borderColor = HUNTER.THEME
              e.currentTarget.style.color = HUNTER.THEME
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = '#f8f6ef'
              e.currentTarget.style.borderColor = HUNTER.LINE
              e.currentTarget.style.color = HUNTER.INK_S
            }}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}
