'use client'
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github.css'
import type { Message, MessagePart, MessagePartText, MessagePartTool } from '../lib/types'
import ToolCallCard from './ToolCallCard'
import FollowUpSuggestions from './FollowUpSuggestions'
import ReportPreviewButton from './ReportPreviewButton'
import HomeHero from './HomeHero'
import { isReportWorthy, getAssistantText } from '../lib/reportDetect'
import { HUNTER, HUNTER_LOGO } from '../../lib/hunter-theme'

function isText(p: MessagePart): p is MessagePartText {
  return p.type === 'text'
}
function isTool(p: MessagePart): p is MessagePartTool {
  return p.type === 'tool'
}

interface Props {
  messages: Message[]
  onOpenArtifact?: (part: MessagePartTool) => void
  /** 点建议 chip · 填输入框(不发送) · 走 draft 机制 */
  onPickSuggestion?: (text: string) => void
  /** 是否正在生成中 · true 时不显示 FollowUpSuggestions (等结束才显示) */
  busy?: boolean
  /** 打开 assistant 消息的最终报告预览 · 传入完整 text */
  onOpenReport?: (text: string, sourceMessageId: string, artifactType?: 'markdown' | 'html', overrideTitle?: string) => void
  /** 消息流底部额外内容 · 供多专家辩论等特殊 UI 插入进度卡 */
  extraBottom?: React.ReactNode
  /** 空态 quick-card 点击 · 与 sidebar 能力卡走同一 handler */
  onPickSkill?: (prompt: string, key?: string) => void
  /** 空态下把 InputBox 内联到 Hero 下方 · 由 ChatWorkspace 传入 */
  heroInput?: React.ReactNode
  /** Sprint E · HTML artifact 缓存 · 消息 id → 已生成的 HTML · 供 reopen */
  htmlArtifacts?: Record<string, { html: string; title: string }>
  /** 点消息底部 HTML 报告按钮时的 reopen 回调 */
  onReopenHtmlArtifact?: (msgId: string) => void
}

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 20 }}>
      <div
        style={{
          maxWidth: 680,
          padding: '13px 16px',
          background: '#f4eee7',
          border: '1px solid #ead9c9',
          borderRadius: '16px 16px 4px 16px',
          color: HUNTER.INK,
          fontSize: 14,
          lineHeight: 1.6,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {text}
      </div>
    </div>
  )
}

function AssistantBlock({ children, modeNote }: { children: React.ReactNode; modeNote?: string }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '42px 1fr',
        gap: 14,
        marginBottom: 24,
      }}
    >
      {/* 助手头像 · 与 sidebar / hero 同源 */}
      <img
        src={HUNTER_LOGO}
        alt="Hunter"
        width={38}
        height={38}
        style={{
          width: 38,
          height: 38,
          minWidth: 38,
          minHeight: 38,
          borderRadius: '50%',
          objectFit: 'cover',
          flexShrink: 0,
          boxShadow: '0 0 0 1px rgba(181,107,45,.42)',
        }}
        onError={(e) => {
          (e.currentTarget as HTMLImageElement).style.display = 'none'
        }}
      />
      <article style={{ minWidth: 0 }}>
        <div
          style={{
            fontFamily: HUNTER.SERIF,
            fontSize: 15,
            fontWeight: 700,
            color: HUNTER.INK,
          }}
        >
          猎鹿人 Hunter
        </div>
        {modeNote && (
          <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 3, marginBottom: 6 }}>
            {modeNote}
          </div>
        )}
        <div
          style={{
            color: HUNTER.INK,
            fontSize: 14,
            lineHeight: 1.85,
            fontFamily: HUNTER.SANS,
          }}
        >
          {children}
        </div>
      </article>
    </div>
  )
}

function MetricStrip({ rows }: { rows: Array<{ label: string; strong: string; note?: string }> }) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${Math.min(rows.length, 3)}, 1fr)`,
        gap: 10,
        margin: '16px 0',
      }}
    >
      {rows.map((r, i) => (
        <div
          key={i}
          style={{
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 13,
            padding: 13,
            background: '#fff',
          }}
        >
          <div style={{ color: HUNTER.INK_F, fontSize: 11 }}>{r.label}</div>
          <div style={{ fontSize: 18, fontWeight: 700, marginTop: 5, color: HUNTER.INK }}>{r.strong}</div>
          {r.note && <div style={{ fontSize: 11, color: HUNTER.SUCCESS, marginTop: 4 }}>{r.note}</div>}
        </div>
      ))}
    </div>
  )
}

function SourceCard({ text }: { text: string }) {
  return (
    <div
      style={{
        marginTop: 18,
        border: `1px solid ${HUNTER.LINE}`,
        borderRadius: 14,
        padding: '14px 15px',
        background: '#fafaf7',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: 10,
          background: '#ece8df',
          display: 'grid',
          placeItems: 'center',
          fontSize: 15,
          flexShrink: 0,
          color: HUNTER.INK_S,
        }}
      >
        ▤
      </div>
      <div>
        <b style={{ fontSize: 13, color: HUNTER.INK }}>研究依据与数据来源</b>
        <div style={{ color: HUNTER.INK_F, fontSize: 11, marginTop: 3 }}>{text}</div>
      </div>
    </div>
  )
}

/** Tag chip · 支持 TAG:xxx（绿 · 中性）/ TAG_WARN:xxx（橙 · 提示） */
function renderTagCell(raw: string): React.ReactNode {
  const m = raw.trim().match(/^TAG(_WARN)?:(.+)$/)
  if (!m) return raw
  const warn = !!m[1]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 24,
        padding: '0 8px',
        borderRadius: 999,
        background: warn ? HUNTER.TAG_WARN_BG : HUNTER.TAG_OK_BG,
        color: warn ? HUNTER.TAG_WARN_FG : HUNTER.TAG_OK_FG,
        fontSize: 11,
        fontWeight: 600,
      }}
    >
      {m[2].trim()}
    </span>
  )
}

/** 抽出 markdown 尾部约定标记 `> 数据来源: xxx` · 返回 { body, source } */
function extractSource(text: string): { body: string; source: string | null } {
  // 匹配最后一段 blockquote 形式 · 或 HTML 注释形式
  const m = text.match(/(?:^|\n)\s*>\s*数据来源\s*[:：]\s*(.+?)\s*$/)
  if (m) return { body: text.slice(0, m.index).trimEnd(), source: m[1].trim() }
  const c = text.match(/<!--\s*source:\s*(.+?)\s*-->/i)
  if (c) return { body: text.replace(c[0], '').trimEnd(), source: c[1].trim() }
  return { body: text, source: null }
}

function MdText({ text }: { text: string }) {
  const { body, source } = extractSource(text)
  return (
    <div className="hunter-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          p: ({ children }) => (
            <p style={{ margin: '0 0 12px 0', lineHeight: 1.75 }}>{children}</p>
          ),
          h1: ({ children }) => (
            <h1 style={{ fontSize: 22, fontWeight: 700, margin: '20px 0 12px', color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '18px 0 10px', color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 style={{ fontSize: 15, fontWeight: 700, margin: '16px 0 8px', color: HUNTER.INK }}>{children}</h3>
          ),
          ul: ({ children }) => (
            <ul style={{ margin: '0 0 12px 0', paddingLeft: 22, lineHeight: 1.75 }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol style={{ margin: '0 0 12px 0', paddingLeft: 22, lineHeight: 1.75 }}>{children}</ol>
          ),
          li: ({ children }) => (
            <li style={{ margin: '2px 0' }}>{children}</li>
          ),
          strong: ({ children }) => (
            <strong style={{ fontWeight: 700, color: HUNTER.INK }}>{children}</strong>
          ),
          em: ({ children }) => (
            <em style={{ fontStyle: 'italic', color: HUNTER.INK_S }}>{children}</em>
          ),
          code: ({ inline, className, children, ...props }: any) => {
            if (inline) {
              return (
                <code
                  style={{
                    background: '#f0ede2',
                    padding: '1px 6px',
                    borderRadius: 4,
                    fontSize: '0.9em',
                    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                    color: HUNTER.COPPER3,
                  }}
                  {...props}
                >
                  {children}
                </code>
              )
            }
            // ```metric 特殊 fenced block · 每行 label|strong|note
            if (className === 'language-metric') {
              const src = Array.isArray(children) ? children.join('') : String(children || '')
              const rows = src
                .split('\n')
                .map((l: string) => l.trim())
                .filter(Boolean)
                .map((l: string) => {
                  const [label = '', strong = '', note = ''] = l.split('|').map((s) => s.trim())
                  return { label, strong, note }
                })
                .filter((r: any) => r.label && r.strong)
              if (rows.length > 0) return <MetricStrip rows={rows} />
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            )
          },
          pre: ({ children }: any) => (
            <pre
              style={{
                background: '#f8f6ef',
                border: `1px solid ${HUNTER.LINE}`,
                borderRadius: 8,
                padding: '12px 14px',
                overflow: 'auto',
                fontSize: 13,
                margin: '10px 0',
                fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                lineHeight: 1.5,
              }}
            >
              {children}
            </pre>
          ),
          a: ({ href, children }: any) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: HUNTER.THEME, textDecoration: 'underline' }}
            >
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: 'auto', margin: '16px 0' }}>
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'separate',
                  borderSpacing: 0,
                  border: `1px solid ${HUNTER.LINE}`,
                  borderRadius: 13,
                  overflow: 'hidden',
                  background: '#fff',
                  fontSize: 12,
                }}
              >
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th style={{ padding: '12px 13px', background: '#f5f4ef', textAlign: 'left', fontWeight: 650, borderRight: `1px solid ${HUNTER.LINE}`, borderBottom: `1px solid ${HUNTER.LINE}` }}>
              {children}
            </th>
          ),
          td: ({ children }) => {
            // 支持 TAG:xxx / TAG_WARN:xxx chip · 单元格纯文本时替换
            const raw =
              typeof children === 'string'
                ? children
                : Array.isArray(children) && children.length === 1 && typeof children[0] === 'string'
                ? (children[0] as string)
                : null
            const content = raw ? renderTagCell(raw) : children
            return (
              <td style={{ padding: '12px 13px', borderRight: `1px solid ${HUNTER.LINE}`, borderBottom: `1px solid ${HUNTER.LINE}`, verticalAlign: 'top' }}>
                {content}
              </td>
            )
          },
          blockquote: ({ children }) => (
            <blockquote
              style={{
                borderLeft: `3px solid ${HUNTER.THEME}`,
                paddingLeft: 12,
                margin: '10px 0',
                color: HUNTER.INK_S,
                fontStyle: 'italic',
              }}
            >
              {children}
            </blockquote>
          ),
        }}
      >
        {body}
      </ReactMarkdown>
      {source && <SourceCard text={source} />}
    </div>
  )
}

/**
 * 把消息列表按「轮」分组 —— 连续的 assistant 消息合并成一块。
 *
 * ## 为什么需要
 *
 * opencode 每做一步就发一条 assistant 消息。用户说一句「按这个地址装 SKILL」,
 * 模型要 open repo → read SKILL.md → read plugin.json → stage → read 下一个,
 * 五次工具调用就是**五条独立消息**,界面上排出五个「猎鹿人 Hunter」头像块:
 *
 *     猎鹿人 Hunter   深度思考完成 · 数据截至 2026/08/31
 *       [hunter_cap_skill_repo_open  999ms]
 *     猎鹿人 Hunter   深度思考完成 · 数据截至 2026/08/31
 *       [hunter_cap_skill_repo_read  396ms]
 *     ...
 *
 * 每块都重复一遍头像、名字、"深度思考完成"。用户看到的是"AI 回复了五次",
 * 而实际上这是**一次回答的五个步骤**。
 *
 * ## 合并规则
 *
 * 一条 user 消息开一轮,后面所有连续的 assistant 消息归进同一轮,
 * 直到下一条 user 消息。工具卡片按原顺序排在一起,最终答复在最下面 ——
 * 也就是产品经理说的"把这些都合并到最底下,一次回复完"。
 *
 * 只改渲染分组,不动数据:messages 数组本身、事件 reducer、
 * artifact 与 message id 的对应关系全部不变。合并只是视觉上的。
 */
/**
 * 这段文本看起来是模型的**内部盘算**,不是给用户的答复。
 *
 * ## 为什么需要
 *
 * 原来只有一条位置规则:「隐藏最后一个 tool_call 之前的 text 段」。
 * 它假设模型一定会调工具 —— planning 在前、工具在中、答复在后。
 *
 * 但模型**卡住时一个工具都不调**。用户问「帮我写一份 长鑫科技 的深度投研报告」,
 * 而长鑫科技(ChangXin Memory)还没上市、查不到代码,模型就在原地打转:
 *
 *     , I will find the stock code for 长鑫科技 (ChangXin Storage) or perform...
 *     Wait, the rules say: "若股票代码/名称有歧义 · 直接调 watchlist_stock_quickview"...
 *     Let's do this: First check user sources to see if there any web search...
 *     No preambles! No explanations! No thinking output!
 *
 * 一个 tool_call 都没有 → lastToolIdx = -1 → **一段都不隐藏**,
 * 整屏英文自言自语原样糊在用户脸上,里面还把我们的系统提示词一条条念了出来。
 *
 * ## 为什么是折叠而不是删掉
 *
 * 删掉的话,模型只输出了盘算(没给出答复)的那次,用户会看到一个**空回复** ——
 * 比看到乱码更让人不知所措。折叠起来至少还能展开看到"它卡在哪了"。
 *
 * ## 判定:宁可漏,不可滥
 *
 * 要 3 个以上标志才算。真正的答复不会连着说三次 "Let's call the tools"。
 * 只命中一两个(比如一句正常的英文里带了 "First,")的照常显示 ——
 * 误折叠用户的正经答复,比漏折叠一段盘算糟得多。
 */
const PLANNING_MARKERS = [
  /\bLet'?s\s+(do|call|check|look|run|write|start|see|try)\b/i,
  /\bWait[,!]/,
  /\bI (will|must|should|need to)\s+(call|check|find|use|search)\b/i,
  /\bFirst,?\s+(I|let)\b/i,
  /the (rules|instructions) say/i,
  /No preambles|No explanations|No thinking output/i,
  /\bActually,\s+let/i,
  /\bSo I (will|must)\b/i,
]

function looksLikePlanning(text: string): boolean {
  const t = (text || '').trim()
  if (t.length < 80) return false          // 太短的不折 · 可能就是一句正常回答
  let hits = 0
  for (const re of PLANNING_MARKERS) if (re.test(t)) hits++
  return hits >= 3
}

/** 折叠起来的内部盘算 —— 默认收起 · 想看能展开 */
function PlanningBlock({ text }: { text: string }) {
  return (
    <details style={{
      margin: '6px 0', padding: '6px 10px', borderRadius: 8,
      background: '#F7F5F1', border: '1px solid #E7E2D9',
    }}>
      <summary style={{
        cursor: 'pointer', fontSize: 12, color: '#8C857A', userSelect: 'none',
      }}>
        模型的内部推演(没有调用任何工具)· 点开查看
      </summary>
      <div style={{
        marginTop: 6, fontSize: 12, color: '#6B6459', lineHeight: 1.8,
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        maxHeight: 320, overflowY: 'auto',
      }}>{text}</div>
    </details>
  )
}

function groupTurns(
  messages: Message[],
): Array<{ role: 'user' | 'assistant'; msgs: Message[] }> {
  const out: Array<{ role: 'user' | 'assistant'; msgs: Message[] }> = []
  for (const m of messages) {
    const role: 'user' | 'assistant' = m.role === 'user' ? 'user' : 'assistant'
    const last = out[out.length - 1]
    // user 永远自成一块(两条相邻的用户消息也该分开显示)
    if (role === 'assistant' && last && last.role === 'assistant') {
      last.msgs.push(m)
    } else {
      out.push({ role, msgs: [m] })
    }
  }
  return out
}

export default function MessageList({ messages, onOpenArtifact, onPickSuggestion, busy, onOpenReport, extraBottom, onPickSkill, heroInput, htmlArtifacts, onReopenHtmlArtifact }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  // 用户是否在底部 · 决定是否自动滚
  // 初始 true · 用户手动上滚后转 false · 滚回底部又转 true
  const userAtBottomRef = useRef(true)
  const [showJumpBtn, setShowJumpBtn] = useState(false)

  const NEAR_BOTTOM_PX = 120

  const checkAtBottom = () => {
    const el = scrollRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
  }

  const handleScroll = () => {
    const atBottom = checkAtBottom()
    userAtBottomRef.current = atBottom
    setShowJumpBtn(!atBottom)
  }

  const jumpToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    userAtBottomRef.current = true
    setShowJumpBtn(false)
  }

  // messages 变化 · 只在用户当前处于底部时才自动滚
  // 避免用户上滚看历史时被强拉回底部
  useEffect(() => {
    if (userAtBottomRef.current) {
      // 用 requestAnimationFrame 等 DOM 更新完再滚 · 避免抖动
      requestAnimationFrame(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
      })
    }
  }, [messages])

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      style={{
        flex: 1,
        overflowY: 'auto',
        background: '#ffffff',
        position: 'relative',
      }}
    >
      {messages.length === 0 && (
        <div style={{ padding: '0 0 24px' }}>
          <HomeHero
            onPick={(prompt, key) => {
              if (key && onPickSkill) onPickSkill(prompt, key)
              else onPickSuggestion?.(prompt)
            }}
          />
          {heroInput}
        </div>
      )}

      <div
        style={{
          maxWidth: 900,
          margin: '0 auto',
          padding: messages.length === 0 ? '0' : '40px 24px 200px',
        }}
      >

        {groupTurns(messages).map((turn, tIdx, turns) => {
          if (turn.role === 'user') {
            const m = turn.msgs[0]
            const text = (m.parts || []).filter(isText).map((p) => p.text).join('\n')
            return <UserBubble key={m.id} text={text} />
          }

          // 这一轮里所有 assistant 消息的 parts 拼在一起(见 groupTurns 的说明)
          const parts = turn.msgs.flatMap((m) => m.parts || [])
          // 锚点 = 这一轮的最后一条。artifact / 报告按钮都挂在它上面 ——
          // 前面几条只是过程中的工具调用。
          const anchor = turn.msgs[turn.msgs.length - 1]
          const anchorIdx = messages.indexOf(anchor)

          const isLastAssistant = tIdx === turns.length - 1
          const hasText = parts.some((p) => isText(p) && (p as MessagePartText).text)
          const reportWorthy = isReportWorthy(anchor, isLastAssistant ? !!busy : false)

          const ts = anchor.time?.updated || anchor.time?.created
          const modeNote = ts
            ? `深度思考完成 · 数据截至 ${new Date(ts).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })}`
            : undefined

          // A · 过滤 Gemini function-calling 前置 planning 段
          // Gemini 常在调 tool 前吐 "I will predict... First I need to..."
          // 这类文本是内部编排 · 用户不需要 · 找到最后一个 tool_call 位置 ·
          // 之前的 text 段一律不渲染(tool_call 卡片本身仍显示)
          const lastToolIdx = parts
            .map((p, i) => (isTool(p) ? i : -1))
            .filter((i) => i >= 0)
            .pop() ?? -1

          // artifact 可能挂在这一轮的任意一条上,逐条找
          const artMsgId = turn.msgs.map((m) => m.id).find((id) => htmlArtifacts?.[id])

          return (
            <AssistantBlock key={anchor.id} modeNote={modeNote}>
              {parts.map((part, i) => {
                // 隐藏所有位于最后 tool_call 之前的 text 段
                if (isText(part) && i < lastToolIdx) return null
                if (isText(part)) {
                  // 没调工具时位置规则失效 —— 再按内容判一次(见 looksLikePlanning)
                  return looksLikePlanning(part.text)
                    ? <PlanningBlock key={i} text={part.text} />
                    : <MdText key={i} text={part.text} />
                }
                if (isTool(part)) return <ToolCallCard key={i} part={part} onOpenArtifact={onOpenArtifact} />
                return null
              })}

              {reportWorthy && onOpenReport && !artMsgId && (
                <ReportPreviewButton
                  onOpen={() => onOpenReport(getAssistantText(anchor), anchor.id)}
                />
              )}

              {artMsgId && onReopenHtmlArtifact && (
                <ReportPreviewButton
                  artifactType="html"
                  onOpen={() => onReopenHtmlArtifact(artMsgId)}
                />
              )}

              {isLastAssistant && !busy && hasText && onPickSuggestion && anchorIdx >= 0 && (
                <FollowUpSuggestions
                  messages={messages}
                  currentIndex={anchorIdx}
                  onPick={onPickSuggestion}
                />
              )}
            </AssistantBlock>
          )
        })}

        {extraBottom}

        <div ref={bottomRef} />
      </div>

      {/* 回底按钮 · 只在用户上滚离开底部时显示 */}
      {showJumpBtn && (
        <button
          type="button"
          onClick={jumpToBottom}
          title="回到底部"
          style={{
            position: 'sticky',
            bottom: 20,
            left: '50%',
            transform: 'translateX(-50%)',
            marginLeft: 'auto',
            marginRight: 'auto',
            display: 'block',
            width: 40,
            height: 40,
            borderRadius: '50%',
            background: '#ffffff',
            border: `1px solid ${HUNTER.LINE}`,
            color: HUNTER.INK_S,
            cursor: 'pointer',
            boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
            transition: 'all 0.15s',
            zIndex: 10,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = HUNTER.THEME
            e.currentTarget.style.color = '#fff'
            e.currentTarget.style.borderColor = HUNTER.THEME
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = '#ffffff'
            e.currentTarget.style.color = HUNTER.INK_S
            e.currentTarget.style.borderColor = HUNTER.LINE
          }}
        >
          <ChevronDown size={18} style={{ margin: 'auto', display: 'block' }} />
        </button>
      )}
    </div>
  )
}
