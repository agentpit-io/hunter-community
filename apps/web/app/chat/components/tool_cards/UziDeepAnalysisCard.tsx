'use client'
// SKILL · hunter-UZI-Skill 深度分析（Sprint 3 P2 · Phase 1 MVP）
// 展示 stock_deep_analysis tool 的 markdown 结果 + 数据覆盖率 + LLM 元信息
import { Radar, CheckCircle2, AlertCircle, Copy } from 'lucide-react'
import { useState } from 'react'
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
  /** uzi_mcp 在后端超时 / 5xx 时返回的是 {error, detail} 而不是报告 ——
   *  这张卡是按 tool 名分发的,拿到的可能就是这份错误对象。 */
  error?: string
  detail?: string
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
  // 工具失败时 duration_ms 根本不存在 · 原来直接 toFixed 会在头部显示 "NaNs"
  const durationLabel = Number.isFinite(data.duration_ms) ? `${(data.duration_ms / 1000).toFixed(1)}s` : '—'
  const failed = typeof data.error === 'string' && data.error.length > 0

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
        <span style={{ marginLeft: 'auto', color: failed ? HUNTER.UP : HUNTER.INK_F, fontSize: 11 }}>
          {failed ? '调用失败' : `${durationLabel} · 覆盖率 ${coverage}%`}
        </span>
        <span style={{ color: HUNTER.INK_F, fontSize: 11, marginLeft: 6 }}>
          {expanded ? '收起 ▲' : '展开 ▼'}
        </span>
      </div>

      {/* 数据覆盖度 chips · 跟着折叠 · 调用失败时没有覆盖度可言,不画 */}
      {expanded && !failed && <div style={{
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
        ) : failed ? (
          <FailedNotice error={data.error!} detail={data.detail} />
        ) : (
          <EmptyReportNotice durationLabel={durationLabel} coverage={coverage} />
        )}
      </div>}

      {/* footer · 复制按钮也跟着折叠 —— 正文都收起来了,
          留一个"复制 markdown"在那儿会让人以为要复制的是别的东西;
          调用失败时没有 markdown 可复制,整条 footer 都不画 */}
      {expanded && !failed && <div style={{
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


/**
 * 工具调用失败(uzi_mcp 返回 {error, detail})时的提示。
 *
 * 这张卡只在 tool state 为 completed 时才会被渲染(见 ToolCallCard.tryRenderRichCard),
 * 也就是说**工具已经收工了,后面不会再有内容来**。原来这里放的是一个从 0 起跳、
 * 每秒 +1 的计时器,文案写"分析已跑 N 秒仍未出内容" —— 那个 N 只是卡片挂在屏幕上的
 * 秒数,跟分析毫无关系(2026-09-07 用户截图里的"2062 秒"就是这么来的),而且它让人
 * 以为还在等。失败就明说失败、说清楚原因、给出下一步,不再假装在生成。
 */
function FailedNotice({ error, detail }: { error: string; detail?: string }) {
  const isTimeout = /timeout|超时|未响应/i.test(error + (detail || ''))
  return (
    <div style={{
      color: HUNTER.UP, padding: '16px 20px',
      background: '#fde7e0', borderRadius: 6, lineHeight: 1.7,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>
        <AlertCircle size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
        深度分析工具本次调用失败,没有拿到报告
      </div>
      <div style={{ fontSize: 12.5, fontFamily: 'monospace', wordBreak: 'break-all', opacity: .9 }}>
        {error}{detail ? ` · ${detail}` : ''}
      </div>
      <div style={{ fontSize: 12.5, marginTop: 8, color: HUNTER.INK_S }}>
        {isTimeout
          ? '通常是上游数据源(finance-data / akshare)或 LLM 一时卡住。直接再发一次「深度分析 + 股票代码」即可;'
          : '直接再发一次「深度分析 + 股票代码」即可;'}
        若反复失败,请管理员在 api 容器日志里 grep <code>[uzi]</code> 看是哪一路超了预算。
        下面正文里如果出现了分析内容,那是模型在没有数据的情况下自己写的,<b>其中的数字不可信</b>。
      </div>
    </div>
  )
}

/** 工具返回了 200,但 markdown 为空 —— 推理型模型把 max_tokens 全花在 reasoning 上、
 *  或模型拒答。同样是终态,不再计时。 */
function EmptyReportNotice({ durationLabel, coverage }: { durationLabel: string; coverage: number }) {
  return (
    <div style={{
      color: HUNTER.UP, textAlign: 'center', padding: 20,
      background: '#fde7e0', borderRadius: 6, lineHeight: 1.7,
    }}>
      <AlertCircle size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
      工具已返回(耗时 {durationLabel} · 数据覆盖率 {coverage}%),但 LLM 没有产出报告正文 ·
      多半是 max_tokens 被 reasoning 占满或模型拒答 · 请<b>重新发起一次</b>;
      持续如此请管理员查 api 容器日志里的「LLM 返回空 markdown」。
    </div>
  )
}
