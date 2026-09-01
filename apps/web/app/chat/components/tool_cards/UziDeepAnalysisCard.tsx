'use client'
// SKILL · hunter-UZI-Skill 深度分析（Sprint 3 P2 · Phase 1 MVP）
// 展示 stock_deep_analysis tool 的 markdown 结果 + 数据覆盖率 + LLM 元信息
import { Radar, CheckCircle2, AlertCircle, Copy, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { HUNTER } from '../../../lib/hunter-theme'

interface UziData {
  type: 'uzi_deep_analysis'
  code: string
  name?: string
  depth: string
  markdown: string
  dims_covered: string[]
  dims_missing: string[]
  duration_ms: number
  model?: string
  note?: string
}

// dim key → 中文标签
const DIM_LABEL: Record<string, string> = {
  quote:        '行情',
  kline:        'K线',
  financials:   '财务',
  lhb:          '龙虎榜',
  fund_holders: '十大股东',
  governance:   '治理',
  news:         '新闻',
  research:     '研报',
}

/**
 * 模型是不是把这张卡的内容又复述了一遍。
 *
 * ## 为什么要判断,而不是一律折叠
 *
 * 一律折叠有个反效果:系统提示本来就要求模型「不复述卡片里的数字,
 * 只给一句简短总结」。**如果它照做了**,而卡片又默认折叠,
 * 用户就只剩一个折叠条加一句话 —— 比之前更糟。
 *
 * 所以不赌模型听不听话,直接看这一轮的正文里到底有没有卡片的内容:
 *   复述了 → 折叠(屏幕上留一份就够)
 *   没复述 → 展开(卡片就是唯一的内容)
 *
 * ## 判定
 *
 * 从卡片 markdown 里抽出若干条 ≥12 字的实质句子,看有多少出现在正文里。
 * 超过四成就算复述。取样而不是全文比对:模型复述时常会改标点、
 * 调语序、加一两句自己的话,全文比对会漏判。
 */
function looksDuplicated(markdown?: string, turnText?: string): boolean {
  if (!markdown || !turnText) return false
  if (turnText.length < 200) return false      // 正文很短 = 只是一句总结,没复述
  const norm = (x: string) => x.replace(/[\s*#`>\-—·、,。:;!?()【】]/g, '')
  const body = norm(turnText)
  const lines = markdown
    .split(/[\n。]/)
    .map(norm)
    .filter((l) => l.length >= 12)
  if (lines.length < 3) return false
  const sample = lines.filter((_, i) => i % Math.max(1, Math.floor(lines.length / 12)) === 0).slice(0, 12)
  const hit = sample.filter((l) => body.includes(l.slice(0, 12))).length
  return hit / sample.length > 0.4
}

export default function UziDeepAnalysisCard(
  { data, turnText }: { data: UziData; turnText?: string },
) {
  const [copied, setCopied] = useState(false)
  /** 卡片正文默认折叠。
   *
   *  模型拿到这张卡之后**还会把里面的内容再复述一遍**(系统提示里那条
   *  "不需要复述卡片里的数字"它并不总是遵守)。于是同一份深度分析在
   *  屏幕上出现两次:上面是这张富卡片,下面是模型的正文,一字不差。
   *  用户要往下滚很久才发现下面是重复的。
   *
   *  哪个该折?折卡片。正文是对话的主体、还带着模型自己的组织和补充;
   *  卡片是过程产物,想看原始排版再展开。
   *
   *  头部一直可见 —— 它带着覆盖率、耗时、数据维度这些正文里没有的信息。
   */
  // 被复述了才折叠 —— 见 looksDuplicated 的说明
  const [expanded, setExpanded] = useState(() => !looksDuplicated(data.markdown, turnText))

  // 老 session 里可能存的是缺少覆盖度字段的 output（早期 tool schema · 或只回了 markdown）,
  // 不兜底会 undefined.length 直接把整个 /chat 页崩成白屏 —— 上层 tryRenderRichCard
  // 的 try/catch 只包了 JSX 创建,catch 不到子组件里的 render 异常。
  const dimsCovered = Array.isArray(data.dims_covered) ? data.dims_covered : []
  const dimsMissing = Array.isArray(data.dims_missing) ? data.dims_missing : []

  const coverage = dimsCovered.length + dimsMissing.length > 0
    ? Math.round((dimsCovered.length / (dimsCovered.length + dimsMissing.length)) * 100)
    : 0

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(data.markdown || '')
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (_e) {
      // silent
    }
  }

  return (
    <div style={cardStyle}>
      {/* 头部 · 点击展开/收起正文 */}
      <div
        onClick={() => setExpanded(v => !v)}
        title={expanded ? '收起分析正文' : '展开分析正文'}
        style={{
          padding: '14px 18px',
          display: 'flex', alignItems: 'center', gap: 8,
          borderBottom: expanded ? `1px solid ${HUNTER.LINE}` : 'none',
          background: `linear-gradient(90deg, ${HUNTER.BRAND_PALE} 0%, ${HUNTER.PAPER} 100%)`,
          cursor: 'pointer', userSelect: 'none',
        }}>
        <Radar size={16} style={{ color: HUNTER.COPPER3 }} />
        <span style={{ fontFamily: HUNTER.SERIF, fontWeight: 700, fontSize: 14 }}>
          {data.name || data.code} · 深度分析
        </span>
        <span style={{
          padding: '2px 8px',
          borderRadius: 4,
          background: HUNTER.COPPER3,
          color: '#fff',
          fontSize: 10.5,
          letterSpacing: '.03em',
        }}>
          {data.depth?.toUpperCase() || 'LITE'}
        </span>
        <span style={{ marginLeft: 'auto', color: HUNTER.INK_F, fontSize: 11 }}>
          {(data.duration_ms / 1000).toFixed(1)}s · 覆盖率 {coverage}%
        </span>
        <span style={{ color: HUNTER.INK_F, fontSize: 11, marginLeft: 6 }}>
          {expanded ? '收起 ▲' : '展开 ▼'}
        </span>
      </div>

      {/* 数据覆盖度 chips · 跟着折叠 */}
      {expanded && <div style={{
        padding: '10px 18px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        borderBottom: `1px solid ${HUNTER.LINE}`,
        background: HUNTER.PAPER2,
      }}>
        {dimsCovered.map(d => (
          <span key={d} title="该维度有数据" style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            padding: '2px 7px', borderRadius: 4, fontSize: 10.5,
            background: '#fde7e0', color: HUNTER.UP, fontWeight: 600,
          }}>
            <CheckCircle2 size={10} /> {DIM_LABEL[d] || d}
          </span>
        ))}
        {dimsMissing.map(d => (
          <span key={d} title="数据未 seed（akshare backfill 中）" style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            padding: '2px 7px', borderRadius: 4, fontSize: 10.5,
            background: HUNTER.PAPER, color: HUNTER.INK_F, fontWeight: 500,
            border: `1px dashed ${HUNTER.LINE}`,
          }}>
            <AlertCircle size={10} /> {DIM_LABEL[d] || d}
          </span>
        ))}
      </div>}

      {/* markdown 正文 · 默认折叠(见 expanded 的说明) */}
      {expanded && <div className="uzi-md" style={{
        padding: '16px 20px',
        fontSize: 13.5, lineHeight: 1.75, color: HUNTER.INK,
        maxHeight: 720, overflowY: 'auto',
      }}>
        {data.markdown ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h3: ({ children }) => (
                <h3 style={{
                  fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700,
                  color: HUNTER.COPPER3, margin: '18px 0 8px', paddingBottom: 4,
                  borderBottom: `1px solid ${HUNTER.LINE}`,
                }}>{children}</h3>
              ),
              strong: ({ children }) => (
                <strong style={{ color: HUNTER.THEME, fontWeight: 700 }}>{children}</strong>
              ),
              ul: ({ children }) => (
                <ul style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ul>
              ),
              li: ({ children }) => (
                <li style={{ margin: '3px 0' }}>{children}</li>
              ),
              p: ({ children }) => (
                <p style={{ margin: '8px 0' }}>{children}</p>
              ),
            }}
          >
            {data.markdown}
          </ReactMarkdown>
        ) : (
          <StalledAnalysisNotice durationMs={data.duration_ms || 0} coverage={coverage} />
        )}
      </div>}

      {/* footer · 复制按钮也跟着折叠 —— 正文都收起来了,
          留一个"复制 markdown"在那儿会让人以为要复制的是别的东西 */}
      {expanded && <div style={{
        padding: '10px 18px',
        display: 'flex', alignItems: 'center', gap: 10,
        borderTop: `1px solid ${HUNTER.LINE}`,
        background: HUNTER.PAPER,
        fontSize: 11, color: HUNTER.INK_F,
      }}>
        <span>数据源 · finance-data</span>
        {data.model && <span>· LLM {data.model}</span>}
        <button
          onClick={copyMarkdown}
          style={{
            marginLeft: 'auto',
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '4px 10px', borderRadius: 6,
            background: copied ? HUNTER.UP : HUNTER.THEME, color: '#fff',
            border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 600,
          }}
        >
          <Copy size={11} /> {copied ? '已复制' : '复制 markdown'}
        </button>
      </div>}

      {data.note && (
        <div style={{
          padding: '8px 18px',
          borderTop: `1px dashed ${HUNTER.LINE}`,
          background: HUNTER.PAPER2,
          fontSize: 10.5, color: HUNTER.INK_F, fontStyle: 'italic',
        }}>
          {data.note}
        </div>
      )}
    </div>
  )
}

const cardStyle: React.CSSProperties = {
  margin: '12px 0',
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 14,
  overflow: 'hidden',
  boxShadow: '0 4px 18px rgba(40,35,27,.04)',
}


// 分析长时间未出内容时的提示(§3.C 复赛演示 UX · 2026-08-29 用户报)
// 逻辑:三档提醒
//   0-15s   转圈 · 请稍候
//   15-45s  转圈 · 稍慢 · 大概再等 20 秒(thinking 模型消耗大)
//   45s+    警告 · 分析未出内容 · 建议重试(不再无声等待)
function StalledAnalysisNotice({ durationMs, coverage }: { durationMs: number; coverage: number }) {
  const [elapsed, setElapsed] = useState(Math.round(durationMs / 1000))
  useEffect(() => {
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [])

  if (elapsed < 15) {
    return (
      <div style={{ color: HUNTER.INK_F, fontStyle: 'italic', textAlign: 'center', padding: 20 }}>
        <Loader2 size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
        分析生成中 · 请稍候({elapsed}s / 覆盖率 {coverage}%)
      </div>
    )
  }
  if (elapsed < 45) {
    return (
      <div style={{
        color: HUNTER.COPPER3, textAlign: 'center', padding: 20,
        background: HUNTER.BRAND_PALE, borderRadius: 6,
      }}>
        <Loader2 size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
        分析仍在生成 · 已 {elapsed} 秒 · Gemini 3.5 thinking 模型消耗较大 · 通常 30-45 秒内出结果
      </div>
    )
  }
  // 45s+ 明确异常
  return (
    <div style={{
      color: HUNTER.UP, textAlign: 'center', padding: 20,
      background: '#fde7e0', borderRadius: 6,
    }}>
      <AlertCircle size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
      分析已跑 {elapsed} 秒仍未出内容 · 可能异常(LLM 超时 / max_tokens 全被 reasoning 占用)·
      请<b>关闭本条对话重新发起</b>。若持续无法出结果 · 联系管理员查 opencode 日志。
    </div>
  )
}
