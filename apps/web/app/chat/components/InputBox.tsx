'use client'
import { useEffect, useRef, useState, useCallback, KeyboardEvent, DragEvent, ClipboardEvent } from 'react'
import { Send, Paperclip, X, Image as ImageIcon } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { ModelPicker } from './AgentModelPicker'

/** 待发送的图片附件 · 内联 base64 · 由 BFF 拦截后走 tesseract OCR 抽文本 */
export interface Attachment {
  id: string
  filename: string
  mime: string
  dataUrl: string        // "data:image/png;base64,..." · 直接传给 opencode file part
  sizeKB: number
}

interface InputBoxProps {
  onSend: (text: string, attachments?: Attachment[]) => void
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

// 单张图上限 8MB · 后端 OCR 端点 10MB,留一点 base64 膨胀余量(~33%)
// 大于这个后 tesseract 也没什么额外精度,反而序列化慢
const MAX_IMAGE_BYTES = 8 * 1024 * 1024
const MAX_ATTACHMENTS = 4

function nanoid() {
  return Math.random().toString(36).slice(2, 10)
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(file)
  })
}

export default function InputBox({
  onSend,
  disabled,
  currentModelKey,
  onChangeModel,
  autoText,
  autoSend,
  draft,
  mode = 'follow',
}: InputBoxProps) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const autoSentRef = useRef(false)

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

  // 把 File[] 转成 Attachment[] 加入队列 · 前置校验类型/大小/张数
  const acceptFiles = useCallback(async (files: File[]) => {
    setUploadError(null)
    const room = MAX_ATTACHMENTS - attachments.length
    if (room <= 0) {
      setUploadError(`最多 ${MAX_ATTACHMENTS} 张图`)
      return
    }
    const kept: Attachment[] = []
    for (const f of files.slice(0, room)) {
      if (!f.type.startsWith('image/')) {
        setUploadError(`不支持的类型: ${f.type || f.name}`)
        continue
      }
      if (f.size > MAX_IMAGE_BYTES) {
        setUploadError(`图片超 ${Math.round(MAX_IMAGE_BYTES / 1024 / 1024)}MB: ${f.name}`)
        continue
      }
      try {
        const dataUrl = await readAsDataUrl(f)
        kept.push({
          id: nanoid(),
          filename: f.name || 'clipboard-image.png',
          mime: f.type || 'image/png',
          dataUrl,
          sizeKB: Math.round(f.size / 1024),
        })
      } catch (e) {
        setUploadError(`读取失败: ${(e as Error).message}`)
      }
    }
    if (kept.length) setAttachments((prev) => [...prev, ...kept])
  }, [attachments.length])

  // 回形针 · 打开文件选择器
  const openFilePicker = () => fileInputRef.current?.click()

  const onFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length) await acceptFiles(files)
    // 清 input value · 让用户下次能重选同一张图
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // 粘贴 · Cmd+V 截图直接入队
  const onPaste = async (e: ClipboardEvent<HTMLDivElement>) => {
    const items = Array.from(e.clipboardData?.items || [])
    const imageItems = items.filter((it) => it.type.startsWith('image/'))
    if (imageItems.length === 0) return
    // 阻止图片被粘成文本(浏览器会尝试转 base64 塞进 textarea · 巨丑)
    e.preventDefault()
    const files = imageItems.map((it) => it.getAsFile()).filter(Boolean) as File[]
    await acceptFiles(files)
  }

  // 拖拽区 · 整个卡片都接
  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (Array.from(e.dataTransfer.types).includes('Files')) {
      e.preventDefault()
      setDragOver(true)
    }
  }
  const onDragLeave = () => setDragOver(false)
  const onDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragOver(false)
    const files = Array.from(e.dataTransfer.files || [])
    if (files.length) await acceptFiles(files)
  }

  const removeAttachment = (id: string) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id))

  const handleSend = () => {
    const trimmed = text.trim()
    // 允许"仅图片"发送 · BFF 会把图片 OCR 成 text part 塞给 LLM
    if (!trimmed && attachments.length === 0) return
    if (disabled) return
    onSend(trimmed, attachments.length ? attachments : undefined)
    setText('')
    setAttachments([])
    setUploadError(null)
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  const canSend = (!!text.trim() || attachments.length > 0) && !disabled
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
          onPaste={onPaste}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          style={{
            background: '#ffffff',
            border: `1px solid ${dragOver ? HUNTER.THEME : HUNTER.LINE_STRONG}`,
            borderRadius: HUNTER.R_XL,
            padding: '19px 16px 13px',
            boxShadow: dragOver
              ? `0 0 0 3px ${HUNTER.THEME}22, ${HUNTER.SHADOW}`
              : HUNTER.SHADOW,
            transition: 'border-color 0.15s, box-shadow 0.15s',
            position: 'relative',
          }}
        >
          {/* 拖拽提示 · 只在拖入时显示 */}
          {dragOver && (
            <div style={{
              position: 'absolute', inset: 0, borderRadius: HUNTER.R_XL,
              background: 'rgba(176,106,50,0.06)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              gap: 8, fontSize: 14, color: HUNTER.THEME, fontWeight: 600,
              pointerEvents: 'none', zIndex: 2,
            }}>
              <ImageIcon size={18} /> 放开以上传图片
            </div>
          )}

          {/* 附件缩略图条 · 有附件才显示 */}
          {attachments.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              {attachments.map((a) => (
                <div
                  key={a.id}
                  style={{
                    position: 'relative',
                    width: 72, height: 72,
                    borderRadius: 10,
                    border: `1px solid ${HUNTER.LINE_STRONG}`,
                    overflow: 'hidden',
                    background: '#f8f5f0',
                  }}
                  title={`${a.filename} · ${a.sizeKB}KB`}
                >
                  <img
                    src={a.dataUrl} alt={a.filename}
                    style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                  />
                  <button
                    type="button" onClick={() => removeAttachment(a.id)}
                    aria-label="移除"
                    style={{
                      position: 'absolute', top: 3, right: 3,
                      width: 20, height: 20, borderRadius: '50%',
                      background: 'rgba(0,0,0,0.62)', color: '#fff',
                      border: 'none', cursor: 'pointer',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      padding: 0,
                    }}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {uploadError && (
            <div style={{
              fontSize: 12, color: '#c05a4a', marginBottom: 8, paddingLeft: 6,
            }}>
              {uploadError}
            </div>
          )}

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
            {/* 左侧 · 回形针 · 加图片 */}
            <button
              type="button"
              onClick={openFilePicker}
              disabled={disabled || attachments.length >= MAX_ATTACHMENTS}
              title={
                attachments.length >= MAX_ATTACHMENTS
                  ? `已达上限 ${MAX_ATTACHMENTS} 张`
                  : '上传图片(支持粘贴/拖拽)'
              }
              aria-label="上传图片"
              style={{
                width: 34, height: 34,
                borderRadius: 10,
                background: 'transparent',
                color: attachments.length >= MAX_ATTACHMENTS ? '#c9c3b6' : HUNTER.INK_S,
                border: 'none',
                cursor: disabled || attachments.length >= MAX_ATTACHMENTS ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transition: 'background 0.1s, color 0.1s',
              }}
              onMouseEnter={(e) => {
                if (!disabled && attachments.length < MAX_ATTACHMENTS) {
                  e.currentTarget.style.background = '#f4eee7'
                  e.currentTarget.style.color = HUNTER.THEME
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = attachments.length >= MAX_ATTACHMENTS ? '#c9c3b6' : HUNTER.INK_S
              }}
            >
              <Paperclip size={16} />
            </button>

            {/* 隐藏 file input · 由回形针触发 */}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={onFileInputChange}
              style={{ display: 'none' }}
            />

            {/* 弹簧 · 把 ModelPicker + 发送按钮推到右边 */}
            <div style={{ flex: 1 }} />

            {/* 中右 · Model picker(AgentPicker 已隐藏 · 走 opencode 默认 agent + BFF 注入的金融 system prompt) */}
            <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
              <ModelPicker value={currentModelKey} onChange={onChangeModel} />
            </div>

            {/* 右侧 · 发送(语音输入未实现 · 已隐藏) */}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
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
