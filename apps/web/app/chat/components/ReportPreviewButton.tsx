'use client'
/**
 * 报告预览按钮 · 消息末尾 或 SessionHeader 都可复用
 * 点击 → 打开 ArtifactPanel 的 report 模式 + 联动 Sidebar 折叠
 */
import { FileText, ArrowRight } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface Props {
  onOpen: () => void
  variant?: 'inline' | 'compact'
  /** 报告类型 · 影响按钮文案与图标 · 默认 markdown */
  artifactType?: 'markdown' | 'html'
}

export default function ReportPreviewButton({ onOpen, variant = 'inline', artifactType = 'markdown' }: Props) {
  const isHtml = artifactType === 'html'
  const inlineLabel = isHtml
    ? '📈 打开完整报告预览 · 侧栏收起 · 支持导出 HTML/PDF/发布公开链接'
    : '📄 打开完整报告预览 · 侧栏收起 · 支持导出 markdown'
  const compactLabel = isHtml ? '预览图表' : '预览报告'
  if (variant === 'compact') {
    // SessionHeader 用 · 小尺寸
    return (
      <button
        type="button"
        onClick={onOpen}
        title="打开完整报告预览"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          padding: '4px 10px',
          background: HUNTER.PAPER3,
          border: `1px solid ${HUNTER.THEME}`,
          borderRadius: 6,
          color: HUNTER.THEME,
          fontSize: 11.5,
          fontWeight: 600,
          cursor: 'pointer',
          transition: 'all 0.15s',
          fontFamily: 'inherit',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = HUNTER.THEME
          e.currentTarget.style.color = '#fff'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = HUNTER.PAPER3
          e.currentTarget.style.color = HUNTER.THEME
        }}
      >
        <FileText size={11} />
        {compactLabel}
      </button>
    )
  }

  // inline · 消息末尾 · 大按钮 · 醒目
  return (
    <button
      type="button"
      onClick={onOpen}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '11px 16px',
        background: 'linear-gradient(90deg, #f8f5ec 0%, #f0ede2 100%)',
        border: `1px solid ${HUNTER.THEME}`,
        borderLeft: `4px solid ${HUNTER.THEME}`,
        borderRadius: 10,
        cursor: 'pointer',
        color: HUNTER.COPPER3,
        fontSize: 13,
        fontWeight: 600,
        marginTop: 16,
        marginBottom: 4,
        width: '100%',
        maxWidth: 560,
        fontFamily: 'inherit',
        transition: 'all 0.15s',
        boxShadow: '0 1px 3px rgba(176,106,50,0.08)',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = `linear-gradient(90deg, ${HUNTER.THEME} 0%, ${HUNTER.COPPER2} 100%)`
        e.currentTarget.style.color = '#fff'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'linear-gradient(90deg, #f8f5ec 0%, #f0ede2 100%)'
        e.currentTarget.style.color = HUNTER.COPPER3
      }}
    >
      <FileText size={16} />
      <span style={{ flex: 1, textAlign: 'left' }}>{inlineLabel}</span>
      <ArrowRight size={14} />
    </button>
  )
}
