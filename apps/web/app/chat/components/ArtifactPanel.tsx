'use client'
import { X, Maximize2, Minimize2, Copy, Check, ChevronDown, Eye, Code2, Download, Printer, Globe } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { MessagePartTool } from '../lib/types'
import { HUNTER } from '../../lib/hunter-theme'
import ReportViewer from './ReportViewer'
import PublishArtifactModal from './PublishArtifactModal'
import { copyReport, downloadReport, inferArtifactTitle } from '../lib/reportDetect'
import { getStatus, type ArtifactStatus } from '../lib/artifactClient'

/**
 * Artifact 面板 · 两种模式:
 *   - tool 模式   · 展示 tool_call JSON output (原有)
 *   - report 模式 · 展示 assistant 报告 · Claude 风:
 *     · 👁/</> tab 切换预览/源码
 *     · Copy dropdown (Copy MD · Download · Print PDF · Publish)
 *     · Publish 弹窗 · 完全模仿 Claude
 */
export interface ReportContent {
  text: string                                // markdown 内容 (若 type=html · 可传摘要用于 title)
  sessionId: string
  sessionTitle: string
  sourceMessageId?: string
  agent?: string
  model?: string
  // Sprint E · HTML 富可视化 artifact 支持
  artifactType?: 'markdown' | 'html'          // 默认 'markdown'
  htmlContent?: string                        // artifactType='html' 时必填 · 完整 HTML 文档
}

interface Props {
  part: MessagePartTool | null
  report: ReportContent | null
  onClose: () => void
}

function fmtOutput(output: any): string {
  if (output == null) return '(no output)'
  if (typeof output === 'string') {
    try {
      const parsed = JSON.parse(output)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return output
    }
  }
  try {
    return JSON.stringify(output, null, 2)
  } catch {
    return String(output)
  }
}

export default function ArtifactPanel({ part, report, onClose }: Props) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [iframeHeight, setIframeHeight] = useState<number>(800)
  const settledRef = useRef(false)

  // 3.5s 后进入沉降态 · 允许精确匹配内容高度 · 消除底部空白
  useEffect(() => {
    const t = setTimeout(() => { settledRef.current = true }, 3500)
    return () => clearTimeout(t)
  }, [])

  // 监听 HTML 里 postMessage 上报的高度 · 动态调整
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e?.data?.type === 'hunter-artifact-height' && typeof e.data.height === 'number') {
        const target = Math.max(400, Math.ceil(e.data.height) + 32)
        setIframeHeight((prev) =>
          settledRef.current ? target : Math.max(prev, target)
        )
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])
  const [copied, setCopied] = useState(false)
  const [viewMode, setViewMode] = useState<'preview' | 'source'>('preview')
  const [copyMenuOpen, setCopyMenuOpen] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false)
  const [artifactStatus, setArtifactStatus] = useState<ArtifactStatus | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const isReport = !!report
  const artifactTitle = isReport
    ? inferArtifactTitle(report!.text, report!.sessionTitle)
    : ''

  // Esc 关闭
  useEffect(() => {
    if (!part && !report) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (publishOpen) return  // Publish 弹窗自己处理 Esc
        if (copyMenuOpen) return setCopyMenuOpen(false)
        if (fullscreen) return setFullscreen(false)
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [part, report, fullscreen, onClose, copyMenuOpen, publishOpen])

  // 点外关闭 Copy dropdown
  useEffect(() => {
    if (!copyMenuOpen) return
    const onClick = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setCopyMenuOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [copyMenuOpen])

  // 打开报告 · 拉发布状态
  // Community 本地部署不启用发布功能 · 端点会 500(schema 未迁移) · 直接跳过调用
  // useEffect(() => {
  //   if (!isReport || !report?.sourceMessageId) return
  //   getStatus(report.sourceMessageId).then(setArtifactStatus).catch(() => {})
  // }, [isReport, report?.sourceMessageId])

  if (!part && !report) return null

  const width = fullscreen ? '95vw' : (isReport ? 'min(60vw, 900px)' : 460)

  const isHtml = isReport && report?.artifactType === 'html' && !!report.htmlContent

  const handleCopy = async () => {
    let ok = false
    if (isReport && report) {
      if (isHtml) {
        try {
          await navigator.clipboard.writeText(report.htmlContent || '')
          ok = true
        } catch {}
      } else {
        ok = await copyReport(report.text)
      }
    } else if (part) {
      try {
        await navigator.clipboard.writeText(fmtOutput(part.state?.output))
        ok = true
      } catch {}
    }
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
    setCopyMenuOpen(false)
  }

  const handleDownload = () => {
    if (isReport && report) {
      if (isHtml) {
        // 下载完整 HTML 文件 · 双击浏览器可开
        const blob = new Blob([report.htmlContent || ''], { type: 'text/html;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${artifactTitle}.html`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } else {
        downloadReport(report.text, {
          sessionTitle: artifactTitle,
          sessionId: report.sessionId,
          agent: report.agent,
          model: report.model,
        })
      }
    }
    setCopyMenuOpen(false)
  }

  const handlePrint = () => {
    setCopyMenuOpen(false)
    if (isHtml && iframeRef.current) {
      // iframe 内部有自己 @media print · 直接打印 iframe
      try {
        iframeRef.current.contentWindow?.focus()
        iframeRef.current.contentWindow?.print()
        return
      } catch (e) {
        console.warn('[artifact] iframe print failed · fallback to window.print', e)
      }
    }
    // markdown 类型走 @media print 全局 CSS
    setTimeout(() => window.print(), 100)
  }

  const handlePublishClick = () => {
    setCopyMenuOpen(false)
    if (isReport && report?.sourceMessageId) {
      setPublishOpen(true)
    }
  }

  // Community 本地部署不启用发布功能(需要 hunter_artifacts.published_artifact 表迁移)
  // 如需启用 · 跑 db/migrations 后把 canPublish 改回条件表达式
  const canPublish = false
  const alreadyPublished = artifactStatus?.published
  const publishBlocked = artifactStatus?.republish_blocked

  return (
    <>
    <aside
      className="artifact-panel"
      style={{
        position: fullscreen ? 'fixed' : 'relative',
        top: 0,
        right: 0,
        width,
        height: '100vh',
        background: isReport ? '#ffffff' : '#faf9f4',
        borderLeft: `1px solid ${HUNTER.LINE}`,
        display: 'flex',
        flexDirection: 'column',
        boxShadow: fullscreen ? '-20px 0 40px rgba(0,0,0,0.15)' : (isReport ? '-4px 0 16px rgba(30,20,10,0.05)' : 'none'),
        zIndex: fullscreen ? 50 : 1,
        transition: 'width 0.25s ease',
        flexShrink: 0,
      }}
    >
      {/* 顶栏工具组 */}
      <div
        className="artifact-toolbar"
        style={{
          padding: '10px 14px',
          borderBottom: `1px solid ${HUNTER.LINE}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          minHeight: 48,
          background: isReport ? HUNTER.PAPER : 'transparent',
        }}
      >
        {/* 左 · 视图 tab (只 report 模式) + 标题 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
          {isReport && (
            <div style={{ display: 'inline-flex', border: `1px solid ${HUNTER.LINE}`, borderRadius: 6, overflow: 'hidden' }}>
              <button
                type="button"
                onClick={() => setViewMode('preview')}
                title="预览"
                style={{
                  ...tabBtnStyle,
                  background: viewMode === 'preview' ? HUNTER.PAPER3 : 'transparent',
                  color: viewMode === 'preview' ? HUNTER.INK : HUNTER.INK_F,
                }}
              >
                <Eye size={13} />
              </button>
              <button
                type="button"
                onClick={() => setViewMode('source')}
                title="源码"
                style={{
                  ...tabBtnStyle,
                  background: viewMode === 'source' ? HUNTER.PAPER3 : 'transparent',
                  color: viewMode === 'source' ? HUNTER.INK : HUNTER.INK_F,
                  borderLeft: `1px solid ${HUNTER.LINE}`,
                }}
              >
                <Code2 size={13} />
              </button>
            </div>
          )}
          {isReport && <span style={{ fontSize: 15 }}>📄</span>}
          <span
            style={{
              fontFamily: isReport ? HUNTER.SERIF : 'ui-monospace, Menlo, monospace',
              fontSize: isReport ? 14 : 13,
              fontWeight: isReport ? 700 : 600,
              color: HUNTER.INK,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {isReport ? artifactTitle : part!.tool}
          </span>
          {isReport && <span style={{ fontSize: 10, color: HUNTER.INK_F, fontWeight: 600 }}>MD</span>}
        </div>

        {/* 右 · 操作组 */}
        <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
          {isReport ? (
            /* Copy dropdown · Claude 风 */
            <div ref={menuRef} style={{ position: 'relative' }}>
              <button
                type="button"
                onClick={() => setCopyMenuOpen(!copyMenuOpen)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '5px 10px',
                  background: copyMenuOpen ? HUNTER.PAPER3 : '#ffffff',
                  border: `1px solid ${HUNTER.LINE}`,
                  borderRadius: 6,
                  color: HUNTER.INK_S,
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                {copied ? <Check size={12} style={{ color: HUNTER.DN }} /> : <Copy size={12} />}
                {copied ? '已复制' : 'Copy'}
                <ChevronDown size={11} />
              </button>
              {copyMenuOpen && (
                <div
                  style={{
                    position: 'absolute',
                    top: '100%',
                    right: 0,
                    marginTop: 4,
                    minWidth: 220,
                    background: '#fff',
                    border: `1px solid ${HUNTER.LINE}`,
                    borderRadius: 8,
                    boxShadow: '0 10px 30px rgba(0,0,0,0.15)',
                    padding: 4,
                    zIndex: 100,
                  }}
                >
                  <MenuItem icon={<Copy size={13} />} label={isHtml ? 'Copy HTML source' : 'Copy markdown'} onClick={handleCopy} />
                  <MenuItem icon={<Download size={13} />} label={isHtml ? 'Download as HTML' : 'Download as MD'} onClick={handleDownload} />
                  <MenuItem icon={<Printer size={13} />} label="Print as PDF" onClick={handlePrint} />
                  {canPublish && (
                    <>
                      <div style={{ height: 1, background: HUNTER.LINE, margin: '4px 6px' }} />
                      <MenuItem
                        icon={<Globe size={13} />}
                        label={alreadyPublished ? 'Manage published link' : (publishBlocked ? 'Republish (blocked)' : 'Publish artifact')}
                        subLabel={alreadyPublished ? 'Public URL created' : (publishBlocked ? '已撤销 · 不可 republish' : undefined)}
                        onClick={handlePublishClick}
                        highlight={alreadyPublished}
                      />
                    </>
                  )}
                </div>
              )}
            </div>
          ) : (
            <button type="button" onClick={handleCopy} title="复制" style={iconBtnStyle}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#f0ede2')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
              {copied ? <Check size={13} style={{ color: HUNTER.DN }} /> : <Copy size={13} />}
            </button>
          )}

          <button
            type="button"
            onClick={() => setFullscreen(!fullscreen)}
            title={fullscreen ? '恢复' : '全屏'}
            style={iconBtnStyle}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#f0ede2')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            {fullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
          </button>
          <button
            type="button"
            onClick={onClose}
            title="关闭 (Esc)"
            style={iconBtnStyle}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#f0ede2')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }} className="artifact-body">
        {isReport && report ? (
          isHtml ? (
            // HTML 富可视化 artifact (Sprint E) · iframe sandbox 隔离
            viewMode === 'preview' ? (
              <iframe
                ref={iframeRef}
                srcDoc={report.htmlContent || ''}
                sandbox="allow-scripts"
                title={artifactTitle}
                scrolling="no"
                style={{
                  width: '100%',
                  height: `${iframeHeight}px`,
                  border: 'none',
                  background: '#fff',
                  display: 'block',
                }}
              />
            ) : (
              <pre
                style={{
                  margin: 0,
                  padding: '20px 32px 40px',
                  fontSize: 12,
                  lineHeight: 1.5,
                  fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                  color: HUNTER.INK_S,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  background: '#fbfaf5',
                  minHeight: '100%',
                }}
              >
                {report.htmlContent}
              </pre>
            )
          ) : viewMode === 'preview' ? (
            <ReportViewer text={report.text} title={artifactTitle} />
          ) : (
            <pre
              style={{
                margin: 0,
                padding: '20px 32px 40px',
                fontSize: 12.5,
                lineHeight: 1.6,
                fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                color: HUNTER.INK_S,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                background: '#fbfaf5',
                minHeight: '100%',
              }}
            >
              {report.text}
            </pre>
          )
        ) : part ? (
          <div style={{ padding: 16 }}>
            {part.state?.input && (
              <>
                <div style={{ fontSize: 10, fontWeight: 700, color: HUNTER.INK_F, marginBottom: 6, letterSpacing: '0.05em' }}>
                  INPUT
                </div>
                <pre style={preStyle}>{JSON.stringify(part.state.input, null, 2)}</pre>
              </>
            )}
            <div style={{ fontSize: 10, fontWeight: 700, color: HUNTER.INK_F, margin: '20px 0 6px', letterSpacing: '0.05em' }}>
              OUTPUT
            </div>
            <pre style={{ ...preStyle, background: '#ffffff' }}>{fmtOutput(part.state?.output)}</pre>
          </div>
        ) : null}
      </div>
    </aside>

    {/* Publish 弹窗 · 独立层 · Sprint E · 支持 HTML 类型 */}
    {isReport && report?.sourceMessageId && (
      <PublishArtifactModal
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        messageId={report.sourceMessageId}
        sessionId={report.sessionId}
        title={artifactTitle}
        artifactType={isHtml ? 'html' : 'markdown'}
        contentMd={isHtml ? undefined : report.text}
        contentHtml={isHtml ? report.htmlContent : undefined}
        onStatusChange={setArtifactStatus}
      />
    )}
    </>
  )
}

function MenuItem({ icon, label, subLabel, onClick, highlight }: {
  icon: React.ReactNode
  label: string
  subLabel?: string
  onClick: () => void
  highlight?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '8px 10px',
        background: 'transparent',
        border: 'none',
        borderRadius: 6,
        textAlign: 'left',
        cursor: 'pointer',
        color: highlight ? HUNTER.THEME : HUNTER.INK_S,
        fontFamily: 'inherit',
        fontSize: 12.5,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = '#faf9f4')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      <span style={{ color: highlight ? HUNTER.THEME : HUNTER.INK_F, display: 'flex', alignItems: 'center' }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: highlight ? 700 : 500 }}>{label}</div>
        {subLabel && <div style={{ fontSize: 10.5, color: HUNTER.INK_F, marginTop: 1 }}>{subLabel}</div>}
      </div>
    </button>
  )
}

const iconBtnStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  background: 'transparent',
  border: 'none',
  color: HUNTER.INK_F,
  cursor: 'pointer',
  borderRadius: 6,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'background 0.1s',
}

const tabBtnStyle: React.CSSProperties = {
  width: 30,
  height: 26,
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  transition: 'background 0.1s',
}

const preStyle: React.CSSProperties = {
  background: '#f7f5ef',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 8,
  padding: '10px 12px',
  fontSize: 12,
  lineHeight: 1.55,
  color: HUNTER.INK_S,
  margin: '0 0 20px 0',
  fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
  overflow: 'auto',
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-word' as const,
}
