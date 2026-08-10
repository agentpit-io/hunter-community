'use client'
import { useEffect, useRef, useState, KeyboardEvent } from 'react'
import { Send, Plus, Mic, Sparkles, Globe, ChevronDown } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { AgentPicker, ModelPicker } from './AgentModelPicker'

interface InputBoxProps {
  onSend: (text: string) => void
  disabled?: boolean
  currentAgent: string
  currentModelKey: string // "providerID/modelID"
  onChangeAgent: (name: string) => void
  onChangeModel: (providerID: string, modelID: string, displayName: string) => void
  autoText?: string  // 从 URL ?q= 带来的首条 · 自动填入
  autoSend?: boolean
  /** 点能力卡填入的模板;seq 递增,连点同一个能力也能重复填入 */
  draft?: { text: string; seq: number }
  /** hero: 首屏居中内联 · follow: 有消息时贴底悬浮 */
  mode?: 'hero' | 'follow'
}

/** 深度思考档位 · 只是 UI toggle，不影响后端 · 保存本地供未来接 agent 切换 */
type DepthMode = 'off' | 'on'

export default function InputBox({
  onSend,
  disabled,
  currentAgent,
  currentModelKey,
  onChangeAgent,
  onChangeModel,
  autoText,
  autoSend,
  draft,
  mode = 'follow',
}: InputBoxProps) {
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)
  const autoSentRef = useRef(false)
  const [depth, setDepth] = useState<DepthMode>('off')
  const [webSearch, setWebSearch] = useState(false)

  // 处理 URL ?q= 首条 · autoText 变化时填入
  useEffect(() => {
    if (autoText && !autoSentRef.current) {
      setText(autoText)
      if (autoSend && !disabled) {
        autoSentRef.current = true
        setTimeout(() => {
          onSend(autoText)
          setText('')
        }, 200)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoText, autoSend, disabled])

  // 能力卡填入:把模板放进输入框,并把光标选中第一个 {占位符}
  useEffect(() => {
    if (!draft?.text) return
    setText(draft.text)
    const ta = taRef.current
    if (!ta) return
    const m = /\{[^}]*\}/.exec(draft.text)
    requestAnimationFrame(() => {
      ta.focus()
      if (m) ta.setSelectionRange(m.index, m.index + m[0].length)
      else ta.setSelectionRange(draft.text.length, draft.text.length)
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 260) + 'px'
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.seq])

  const autosize = () => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 260) + 'px'
  }

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  const canSend = !!text.trim() && !disabled
  const isHero = mode === 'hero'
  const placeholder = isHero
    ? '向猎鹿人提问，或粘贴分析需求...'
    : disabled
    ? '正在生成...'
    : '继续提问，或补充你的分析需求...'
  const finePrint = isHero
    ? '信息来自公开资料与联网检索 · 请独立判断并注意投资风险'
    : '内容由 AI 生成，仅供参考，不构成任何投资建议'

  // hero 模式 · 内联居中；follow 模式 · fixed 底部悬浮
  const outerStyle: React.CSSProperties = isHero
    ? {
        width: '100%',
        display: 'flex',
        justifyContent: 'center',
        padding: '0 24px',
      }
    : {
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        padding: '0 24px 24px',
        background: 'linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.9) 30%, #ffffff 60%)',
        pointerEvents: 'none',
      }

  const innerStyle: React.CSSProperties = {
    maxWidth: isHero ? 1040 : 900,
    width: '100%',
    margin: '0 auto',
    pointerEvents: 'auto',
  }

  return (
    <div style={outerStyle}>
      <div style={innerStyle}>
        <div
          style={{
            background: '#ffffff',
            border: `1px solid ${HUNTER.LINE_STRONG}`,
            borderRadius: HUNTER.R_XL,
            padding: '19px 16px 13px',
            boxShadow: HUNTER.SHADOW,
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
        >
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              autosize()
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={placeholder}
            rows={1}
            style={{
              width: '100%',
              border: 'none',
              outline: 'none',
              resize: 'none',
              fontFamily: 'inherit',
              fontSize: 16,
              lineHeight: 1.5,
              color: HUNTER.INK,
              background: 'transparent',
              padding: '4px 6px',
              minHeight: isHero ? 58 : 36,
              maxHeight: 260,
              overflowY: 'auto',
            }}
          />

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '4px 2px 0',
              flexWrap: 'wrap',
            }}
          >
            {/* 左侧 · ＋附件 + 深度思考 + 联网搜索 */}
            <ToolPill title="附件" ariaLabel="附件">
              <Plus size={14} />
            </ToolPill>

            <ToolPill
              title="深度思考"
              active={depth === 'on'}
              onClick={() => setDepth((d) => (d === 'on' ? 'off' : 'on'))}
            >
              <Sparkles size={12} />
              <span>深度思考</span>
              <ChevronDown size={10} style={{ opacity: 0.6 }} />
            </ToolPill>

            <ToolPill
              title="联网搜索"
              active={webSearch}
              onClick={() => setWebSearch((v) => !v)}
            >
              <Globe size={12} />
              <span>联网搜索</span>
            </ToolPill>

            {/* 弹簧 */}
            <div style={{ flex: 1 }} />

            {/* 中右 · Agent + Model picker */}
            <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
              <AgentPicker value={currentAgent} onChange={onChangeAgent} />
              <div style={{ width: 1, height: 14, background: HUNTER.LINE }} />
              <ModelPicker value={currentModelKey} onChange={onChangeModel} />
            </div>

            {/* 右侧 · 语音 + 发送 */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button
                type="button"
                title="语音输入"
                aria-label="语音输入"
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  background: 'transparent',
                  border: 'none',
                  color: HUNTER.INK_F,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Mic size={14} />
              </button>
              <button
                type="button"
                onClick={handleSend}
                disabled={!canSend}
                title="发送 (Enter)"
                aria-label="发送"
                style={{
                  width: 38,
                  height: 38,
                  borderRadius: 11,
                  background: canSend ? HUNTER.THEME : '#e5e0d3',
                  color: '#fff',
                  border: 'none',
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 18,
                  transition: 'background 0.1s',
                }}
                onMouseEnter={(e) => {
                  if (canSend) e.currentTarget.style.background = HUNTER.COPPER2
                }}
                onMouseLeave={(e) => {
                  if (canSend) e.currentTarget.style.background = HUNTER.THEME
                }}
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </div>

        <div
          style={{
            textAlign: 'center',
            fontSize: 11,
            color: '#aaa9a1',
            marginTop: 14,
            opacity: 0.9,
          }}
        >
          {finePrint}
        </div>
      </div>
    </div>
  )
}

/** 输入卡内的 pill 按钮 · 与 prototype .tool-pill 对齐 */
function ToolPill({
  children,
  onClick,
  title,
  ariaLabel,
  active = false,
}: {
  children: React.ReactNode
  onClick?: () => void
  title?: string
  ariaLabel?: string
  active?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={ariaLabel || title}
      style={{
        height: 32,
        padding: '0 12px',
        border: `1px solid ${active ? HUNTER.THEME : HUNTER.LINE}`,
        background: active ? HUNTER.BRAND_PALE : '#fff',
        borderRadius: 10,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        color: active ? HUNTER.COPPER3 : '#4f514c',
        fontSize: 13,
        cursor: 'pointer',
        fontFamily: 'inherit',
        transition: 'background 0.12s, border-color 0.12s, color 0.12s',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = '#f6f5f0'
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = '#fff'
      }}
    >
      {children}
    </button>
  )
}
