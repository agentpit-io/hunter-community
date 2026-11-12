'use client'
import {
  useEffect,
  useRef,
  useState,
  useCallback,
  KeyboardEvent,
  DragEvent,
  ClipboardEvent,
  RefObject,
} from 'react'
import { Send, Square, Paperclip, X, FileText, Loader2 } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { fillTemplate, templateSlots } from '../lib/templateFill'
import { ModelPicker } from './AgentModelPicker'
import type { UploadedAttachment } from '../lib/types'
import { uploadChatFile, deleteChatFile, importPortfolioFromFile } from '../lib/fileClient'

const ACCEPT_TYPES = [
  '.pdf', 'application/pdf',
  '.xlsx', '.xls', '.csv',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-excel', 'text/csv',
  '.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.md', 'text/markdown', '.txt', 'text/plain',
  'image/*',
].join(',')

const MAX_IMAGE_BYTES = 8 * 1024 * 1024
const MAX_DOC_BYTES = 50 * 1024 * 1024
const MAX_ATTACHMENTS = 5

function nanoid() {
  return Math.random().toString(36).slice(2, 10)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(file)
  })
}

function extBadgeStyle(ext: UploadedAttachment['fileExt']): { label: string; bg: string } {
  switch (ext) {
    case 'pdf':
      return { label: 'PDF', bg: '#d9483b' }
    case 'xlsx':
    case 'xls':
    case 'csv':
      return { label: 'XLS', bg: '#217346' }
    case 'docx':
      return { label: 'DOC', bg: '#2b579a' }
    case 'md':
    case 'txt':
      return { label: 'MD', bg: '#4a4a4a' }
    default:
      return { label: 'IMG', bg: '#8c6d46' }
  }
}

interface InputBoxProps {
  onSend: (text: string, attachments?: UploadedAttachment[]) => void
  sessionId?: string | null
  disabled?: boolean
  generating?: boolean
  onAbort?: () => void
  currentAgent: string
  currentModelKey: string
  onChangeAgent: (name: string) => void
  onChangeModel: (providerID: string, modelID: string, displayName: string) => void
  autoText?: string
  autoSend?: boolean
  draft?: { text: string; seq: number }
  onDraftConsumed?: () => void
  onAutoTextConsumed?: () => void
  clearSeq?: number
  mode?: 'hero' | 'follow'
  wrapperRef?: RefObject<HTMLDivElement | null>
  /** 点击已上传文档卡片预览原文 */
  onPreviewDocument?: (att: UploadedAttachment, pageNo?: number) => void
}

export default function InputBox({
  onSend,
  sessionId,
  disabled,
  generating,
  onAbort,
  currentModelKey,
  onChangeModel,
  autoText,
  autoSend,
  draft,
  onDraftConsumed,
  onAutoTextConsumed,
  clearSeq,
  mode = 'follow',
  wrapperRef,
  onPreviewDocument,
}: InputBoxProps) {
  const [text, setText] = useState('')
  const [attachments, setAttachments] = useState<UploadedAttachment[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [importingPortfolioId, setImportingPortfolioId] = useState<string | null>(null)

  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const autoSentRef = useRef(false)
  const tplSlotsRef = useRef<string[]>([])

  // 换会话时清空未发送文本和未发送附件
  const clearSeqRef = useRef(clearSeq)
  useEffect(() => {
    if (clearSeqRef.current === clearSeq) return
    clearSeqRef.current = clearSeq
    setText('')
    setAttachments([])
    tplSlotsRef.current = []
    setHint(null)
  }, [clearSeq])

  // 处理 URL ?q= 首条 · autoText 变化时填入
  useEffect(() => {
    if (autoText && !autoSentRef.current) {
      setText(autoText)
      if (autoSend && !disabled) {
        autoSentRef.current = true
        setTimeout(() => {
          onSend(autoText)
          setText('')
          onAutoTextConsumed?.()
        }, 200)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoText, autoSend, disabled])

  // 能力卡填入
  useEffect(() => {
    if (!draft?.text) return
    setText(draft.text)
    tplSlotsRef.current = templateSlots(draft.text)
    onDraftConsumed?.()
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

  // 统一处理用户选入/拖入/粘贴的文件（文档走后端实时解析，图片走内联预览+OCR）
  const acceptFiles = useCallback(
    async (files: File[]) => {
      setHint(null)
      const room = MAX_ATTACHMENTS - attachments.length
      if (room <= 0) {
        setHint(`单条消息最多同时挂载 ${MAX_ATTACHMENTS} 个文件`)
        return
      }

      for (const f of files.slice(0, room)) {
        const ext = (f.name.split('.').pop() || '').toLowerCase()
        const isImage = f.type.startsWith('image/')
        const isDoc = ['pdf', 'xlsx', 'xls', 'csv', 'docx', 'md', 'txt'].includes(ext)

        if (!isImage && !isDoc) {
          setHint(
            `暂不支持 "${f.name}" 的格式，请上传 PDF、Excel(.xlsx/.xls/.csv)、Word(.docx)、Markdown(.md) 或图片`,
          )
          continue
        }

        if (isImage) {
          if (f.size > MAX_IMAGE_BYTES) {
            setHint(`图片超过 ${Math.round(MAX_IMAGE_BYTES / 1024 / 1024)}MB 上限：${f.name}`)
            continue
          }
          try {
            const dataUrl = await readAsDataUrl(f)
            setAttachments((prev) => [
              ...prev,
              {
                id: nanoid(),
                kind: 'image',
                filename: f.name || 'clipboard-image.png',
                fileExt: 'image',
                sizeBytes: f.size,
                status: 'ready',
                dataUrl,
                mime: f.type || 'image/png',
              },
            ])
          } catch (e: any) {
            setHint(`读取图片失败：${e?.message || e}`)
          }
          continue
        }

        // 文档类型（PDF / Excel / Word / Markdown）
        if (f.size > MAX_DOC_BYTES) {
          setHint(`文件 "${f.name}" 超过 50MB 上限，建议拆分或上传摘要版`)
          continue
        }

        const tempId = `uploading_${nanoid()}`
        const docExt = (ext || 'pdf') as UploadedAttachment['fileExt']
        setAttachments((prev) => [
          ...prev,
          {
            id: tempId,
            kind: 'document',
            filename: f.name,
            fileExt: docExt,
            sizeBytes: f.size,
            status: 'parsing',
          },
        ])

        try {
          const uploaded = await uploadChatFile(f, sessionId || undefined)
          setAttachments((prev) =>
            prev.map((item) => (item.id === tempId ? uploaded : item)),
          )
        } catch (e: any) {
          const errMsg = e?.message || '解析失败'
          setAttachments((prev) =>
            prev.map((item) =>
              item.id === tempId
                ? { ...item, status: 'error', errorMsg: errMsg }
                : item,
            ),
          )
        }
      }
    },
    [attachments.length, sessionId],
  )

  const openFilePicker = () => fileInputRef.current?.click()

  const onFileInputChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length) await acceptFiles(files)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // 支持 Ctrl+V 粘贴文件（PDF/Excel/Word/截图）
  const onPaste = async (e: ClipboardEvent<HTMLDivElement>) => {
    const items = Array.from(e.clipboardData?.items || [])
    const fileItems = items.filter((it) => it.kind === 'file')
    if (fileItems.length === 0) return
    const files = fileItems.map((it) => it.getAsFile()).filter(Boolean) as File[]
    if (files.length === 0) return
    e.preventDefault()
    await acceptFiles(files)
  }

  // 拖拽文件进入输入框
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

  const removeAttachment = (att: UploadedAttachment) => {
    setAttachments((prev) => prev.filter((a) => a.id !== att.id))
    if (att.fileId) {
      void deleteChatFile(att.fileId).catch(() => {})
    }
  }

  const hasParsingAttachment = attachments.some(
    (a) => a.status === 'uploading' || a.status === 'parsing',
  )
  const readyAttachments = attachments.filter((a) => a.status === 'ready')

  const handleSendWithPrompt = (overridePrompt?: string) => {
    if (disabled || hasParsingAttachment) return
    const rawText = (overridePrompt !== undefined ? overridePrompt : text).trim()

    if (!rawText && readyAttachments.length === 0) return

    let finalPrompt = rawText
    if (!finalPrompt && readyAttachments.length > 0) {
      const firstDoc = readyAttachments.find((a) => a.kind === 'document')
      if (firstDoc) {
        finalPrompt = `请精读我上传的这份《${firstDoc.filename}》文档，提炼核心财务数据、关键表格指标与投资结论，并结合关联股票的最新实时行情进行交叉分析。`
      } else {
        finalPrompt = '请识别并分析我上传的图片内容。'
      }
    }

    const filled = fillTemplate(finalPrompt, tplSlotsRef.current)
    if (!filled.ok) {
      setHint(`先把 ${filled.unfilled} 换成具体的股票名称或代码再发送`)
      const ta = taRef.current
      if (ta) {
        const at = text.indexOf(filled.unfilled)
        ta.focus()
        if (at >= 0) ta.setSelectionRange(at, at + filled.unfilled.length)
      }
      return
    }

    onSend(filled.text, readyAttachments.length > 0 ? readyAttachments : undefined)
    tplSlotsRef.current = []
    setHint(null)
    setText('')
    setAttachments([])
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const handleSend = () => handleSendWithPrompt()

  const handleImportPortfolioAndDiagnose = async (att: UploadedAttachment) => {
    if (!att.fileId || importingPortfolioId) return
    setImportingPortfolioId(att.fileId)
    try {
      const res = await importPortfolioFromFile(att.fileId)
      handleSendWithPrompt(
        `我刚从《${att.filename}》导入了 ${res.imported_count} 只持仓股票，请结合表格中的持仓股数与成本价，调用 portfolio_portfolio_rebalance 和 portfolio_portfolio_stress 帮我做持仓诊断与极端行情压力测试。`,
      )
    } catch (e: any) {
      setHint(e?.message || '导入持仓失败')
    } finally {
      setImportingPortfolioId(null)
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  // 汇总当前已就绪文档中识别出的关联股票（去重，最多展示 6 只）
  const aggregatedStocks: Array<{ code: string; name: string; market: string }> = []
  const seenCodes = new Set<string>()
  for (const att of readyAttachments) {
    for (const s of att.detectedStocks || []) {
      if (s?.code && !seenCodes.has(s.code)) {
        seenCodes.add(s.code)
        aggregatedStocks.push(s)
      }
    }
  }

  const canSend = (!!text.trim() || readyAttachments.length > 0) && !disabled && !hasParsingAttachment
  const showStop = !!generating && !!onAbort
  const btnActive = showStop || canSend
  const isHero = mode === 'hero'
  const placeholder = isHero
    ? '向猎鹿人提问，或拖拽 / 点击回形针上传 PDF 财报、研报、Excel 交割单...'
    : disabled
    ? '正在生成...'
    : '继续提问，或上传 PDF 财报/研报进行交叉分析...'
  const finePrint = isHero
    ? '信息来自公开资料与联网检索 · 支持导入 PDF 财报/研报、Excel、Word、Markdown 深度分析'
    : '内容由 AI 生成，仅供参考，不构成任何投资建议'

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
        padding: '0 24px max(24px, env(safe-area-inset-bottom))',
        background:
          'linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.9) 30%, #ffffff 60%)',
        pointerEvents: 'none',
      }

  const innerStyle: React.CSSProperties = {
    maxWidth: isHero ? 1040 : 900,
    width: '100%',
    margin: '0 auto',
    pointerEvents: 'auto',
  }

  return (
    <div ref={wrapperRef} style={outerStyle}>
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
          {/* 拖拽高亮浮层 */}
          {dragOver && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                borderRadius: HUNTER.R_XL,
                background: 'rgba(176,106,50,0.07)',
                border: `2px dashed ${HUNTER.THEME}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
                fontSize: 14,
                color: HUNTER.THEME,
                fontWeight: 600,
                pointerEvents: 'none',
                zIndex: 5,
              }}
            >
              <FileText size={18} /> 放开以上传文件（PDF 财报/研报、Excel、Word、Markdown）进行分析
            </div>
          )}

          {/* 已挂载文件预览条（DocumentAttachmentCard + 图片缩略图） */}
          {attachments.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {attachments.map((att) => {
                  if (att.kind === 'image' && att.dataUrl) {
                    return (
                      <div
                        key={att.id}
                        style={{
                          position: 'relative',
                          width: 72,
                          height: 72,
                          borderRadius: 10,
                          border: `1px solid ${HUNTER.LINE_STRONG}`,
                          overflow: 'hidden',
                          background: '#f8f5f0',
                        }}
                        title={`${att.filename} · ${formatSize(att.sizeBytes)}`}
                      >
                        <img
                          src={att.dataUrl}
                          alt={att.filename}
                          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                        />
                        <button
                          type="button"
                          onClick={() => removeAttachment(att)}
                          aria-label="移除附件"
                          style={{
                            position: 'absolute',
                            top: 3,
                            right: 3,
                            width: 20,
                            height: 20,
                            borderRadius: '50%',
                            background: 'rgba(0,0,0,0.62)',
                            color: '#fff',
                            border: 'none',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: 0,
                          }}
                        >
                          <X size={12} />
                        </button>
                      </div>
                    )
                  }

                  const badge = extBadgeStyle(att.fileExt)
                  const isParsing = att.status === 'uploading' || att.status === 'parsing'
                  const isErr = att.status === 'error'
                  const isPortfolioSheet = !!att.extraMeta?.is_portfolio_sheet
                  const hasStrategyDraft = !!att.extraMeta?.has_strategy_draft

                  return (
                    <div
                      key={att.id}
                      data-testid="uploaded-file-card"
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 6,
                        padding: '10px 12px',
                        borderRadius: 12,
                        border: `1px solid ${isErr ? '#e29b90' : '#e3dacb'}`,
                        background: isErr ? '#fdf4f2' : '#faf8f3',
                        minWidth: 260,
                        maxWidth: '100%',
                        flex: '1 1 300px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {/* 格式徽标 */}
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: 8,
                            background: badge.bg,
                            color: '#fff',
                            fontSize: 11,
                            fontWeight: 700,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                            letterSpacing: 0.5,
                          }}
                        >
                          {badge.label}
                        </div>

                        {/* 文件名 + 状态 */}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span
                              onClick={() => {
                                if (att.status === 'ready' && onPreviewDocument) {
                                  onPreviewDocument(att, 1)
                                }
                              }}
                              style={{
                                fontSize: 13,
                                fontWeight: 600,
                                color: HUNTER.INK,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                cursor: att.status === 'ready' && onPreviewDocument ? 'pointer' : 'default',
                              }}
                              title={att.filename}
                            >
                              {att.filename}
                            </span>
                            <span style={{ fontSize: 11, color: HUNTER.INK_F, flexShrink: 0 }}>
                              ({formatSize(att.sizeBytes)})
                            </span>
                          </div>

                          <div style={{ fontSize: 12, marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
                            {isParsing && (
                              <span style={{ color: HUNTER.THEME, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                <Loader2 size={12} className="animate-spin" />
                                正在解析文字与财务表格...
                              </span>
                            )}
                            {att.status === 'ready' && (
                              <span
                                data-testid="uploaded-file-ready-status"
                                style={{ color: '#2b7a4b', fontWeight: 500 }}
                              >
                                ✅ 已就绪 · {att.totalPages || 1}页 · {att.tableCount || 0}张表
                              </span>
                            )}
                            {isErr && (
                              <span
                                data-testid="uploaded-file-error-status"
                                style={{ color: '#c0392b', fontWeight: 500 }}
                              >
                                ⚠️ {att.errorMsg || '文件解析失败'}
                              </span>
                            )}
                          </div>
                        </div>

                        {/* 移除按钮 */}
                        <button
                          type="button"
                          onClick={() => removeAttachment(att)}
                          title="移除该文件"
                          aria-label="移除该文件"
                          style={{
                            width: 24,
                            height: 24,
                            borderRadius: 6,
                            border: 'none',
                            background: 'transparent',
                            color: HUNTER.INK_F,
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                          }}
                        >
                          <X size={15} />
                        </button>
                      </div>

                      {/* Excel 持仓单 / Markdown 策略草稿专属联动按钮 */}
                      {att.status === 'ready' && (isPortfolioSheet || hasStrategyDraft) && (
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 2 }}>
                          {isPortfolioSheet && (
                            <button
                              type="button"
                              onClick={() => handleImportPortfolioAndDiagnose(att)}
                              disabled={!!importingPortfolioId}
                              style={{
                                fontSize: 11,
                                padding: '3px 9px',
                                borderRadius: 999,
                                border: `1px solid ${HUNTER.THEME}`,
                                background: '#fff7ed',
                                color: HUNTER.THEME,
                                fontWeight: 600,
                                cursor: 'pointer',
                              }}
                            >
                              💼 一键导入持仓并诊断 ({att.extraMeta?.portfolio_rows?.length || 0}只)
                            </button>
                          )}
                          {hasStrategyDraft && (
                            <button
                              type="button"
                              onClick={() =>
                                handleSendWithPrompt(
                                  `请提取《${att.filename}》中的量化选股与买卖条件规则，转化为结构化策略参数并评估历史回测要点。`,
                                )
                              }
                              style={{
                                fontSize: 11,
                                padding: '3px 9px',
                                borderRadius: 999,
                                border: `1px solid ${HUNTER.THEME}`,
                                background: '#fff7ed',
                                color: HUNTER.THEME,
                                fontWeight: 600,
                                cursor: 'pointer',
                              }}
                            >
                              ⚡ 提取文中策略规则
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* 投研联动亮点：关联股票识别芯片 + 3 个快捷追问胶囊 */}
              {readyAttachments.some((a) => a.kind === 'document') && (
                <div
                  data-testid="uploaded-file-actions-bar"
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                    padding: '8px 10px',
                    borderRadius: 10,
                    background: '#f6f2ea',
                    border: '1px solid #e7decb',
                  }}
                >
                  {aggregatedStocks.length > 0 && (
                    <div
                      data-testid="detected-stocks-bar"
                      style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 12 }}
                    >
                      <span style={{ color: HUNTER.INK_S, fontWeight: 600 }}>🎯 识别到关联标的：</span>
                      {aggregatedStocks.map((s) => (
                        <span
                          key={s.code}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '2px 8px',
                            borderRadius: 999,
                            background: '#fff',
                            border: '1px solid #dccbb2',
                            color: HUNTER.THEME,
                            fontWeight: 600,
                            fontSize: 11,
                          }}
                        >
                          {s.code} {s.name}
                        </span>
                      ))}
                    </div>
                  )}

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, color: HUNTER.INK_F }}>快捷追问：</span>
                    {[
                      {
                        label: '📊 提炼核心财务指标并与最新盘面对比',
                        prompt:
                          '请提炼我上传文档中的核心财务指标与关键表格数据（附准确页码来源），并自动调用实时行情工具与最新盘面走势进行交叉对比分析。',
                      },
                      {
                        label: '🔍 核验研报核心逻辑与主力资金动向',
                        prompt:
                          '请梳理这份文档的核心投资逻辑与盈利预测（标注页码来源），并结合关联标的的最新实时股价、估值分位与近期新闻进行核验。',
                      },
                      {
                        label: '⚠️ 排查财报隐藏风险点与异常科目',
                        prompt:
                          '请仔细排查上传文档中的潜在财务风险点、异常变动科目（如存货、应收、毛利率或现金流变动，注明具体页码），并给出客观风险评估。',
                      },
                    ].map((q) => (
                      <button
                        key={q.label}
                        type="button"
                        data-testid="quick-doc-prompt-chip"
                        onClick={() => {
                          setText(q.prompt)
                          requestAnimationFrame(() => {
                            taRef.current?.focus()
                            autosize()
                          })
                        }}
                        style={{
                          fontSize: 11,
                          padding: '3px 9px',
                          borderRadius: 999,
                          border: '1px solid #d9cbb5',
                          background: '#ffffff',
                          color: HUNTER.INK,
                          cursor: 'pointer',
                          transition: 'border-color 0.15s, background 0.15s',
                        }}
                      >
                        {q.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {hint && (
            <div style={{ fontSize: 12, color: '#c05a4a', marginBottom: 8, paddingLeft: 6 }}>
              {hint}
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

          {/* 隐藏的文件选择器 */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT_TYPES}
            onChange={onFileInputChange}
            style={{ display: 'none' }}
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
            {/* 左下角：回形针上传文件按钮 */}
            <button
              type="button"
              data-testid="upload-file-btn"
              onClick={openFilePicker}
              disabled={disabled}
              title="导入 PDF 财报/研报进行分析（支持拖拽或粘贴文件）"
              aria-label="导入 PDF 财报/研报进行分析"
              style={{
                height: 32,
                padding: '0 10px',
                borderRadius: 8,
                border: `1px solid ${HUNTER.LINE}`,
                background: '#faf8f3',
                color: HUNTER.INK_S,
                cursor: disabled ? 'not-allowed' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                fontSize: 12,
                fontWeight: 500,
                transition: 'border-color 0.15s, color 0.15s',
              }}
            >
              <Paperclip size={14} />
              <span>上传文档/PDF</span>
            </button>

            <div style={{ flex: 1 }} />

            <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
              <ModelPicker value={currentModelKey} onChange={onChangeModel} />
            </div>

            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button
                type="button"
                data-testid="chat-send-btn"
                onClick={showStop ? onAbort : handleSend}
                disabled={!btnActive}
                title={
                  showStop
                    ? '停止生成'
                    : hasParsingAttachment
                    ? '正在解析文档，请稍候...'
                    : '发送 (Enter)'
                }
                aria-label={showStop ? '停止生成' : '发送'}
                style={{
                  width: 38,
                  height: 38,
                  borderRadius: 11,
                  background: btnActive ? HUNTER.THEME : '#e5e0d3',
                  color: '#fff',
                  border: 'none',
                  cursor: btnActive ? 'pointer' : 'not-allowed',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 18,
                  transition: 'background 0.1s',
                }}
                onMouseEnter={(e) => {
                  if (btnActive) e.currentTarget.style.background = HUNTER.COPPER2
                }}
                onMouseLeave={(e) => {
                  if (btnActive) e.currentTarget.style.background = HUNTER.THEME
                }}
              >
                {showStop ? <Square size={13} fill="currentColor" /> : <Send size={16} />}
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
