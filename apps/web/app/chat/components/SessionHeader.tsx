'use client'
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Edit3, Trash2, Copy, Check, Share2, Download, MoreHorizontal } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { deleteSession, getSession, renameSession } from '../lib/opencodeClient'
import type { Session } from '../lib/types'

interface Props {
  sessionId: string | null
  onSessionUpdated: () => void
  onSessionDeleted: () => void
  /** 分享 · 由父级触发 PublishArtifactModal · 无报告时 undefined = 禁用 */
  onShare?: () => void
  /** 导出 · 无报告时 undefined = 禁用 */
  onExport?: () => void
}

export default function SessionHeader({ sessionId, onSessionUpdated, onSessionDeleted, onShare, onExport }: Props) {
  const [session, setSession] = useState<Session | null>(null)
  const [open, setOpen] = useState(false)
  /** 菜单从哪个按钮打开的 —— 决定它贴左还是贴右。
   *
   *  这个下拉被**两个**按钮共用:标题右边那个 ∨,和最右上角的 ···。
   *  而菜单一直写死 `left: 24`(贴着标题)。于是从右上角的 ··· 点开时,
   *  菜单出现在屏幕最左边、离按钮一整个屏幕宽 ——
   *  用户点了右上角,东西弹在左上角,第一反应是"这是不是弹错了"。
   */
  const [anchor, setAnchor] = useState<'title' | 'more'>('title')
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [copied, setCopied] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const load = async () => {
    if (!sessionId) {
      setSession(null)
      return
    }
    try {
      const s = await getSession(sessionId)
      setSession(s)
      setEditTitle(s.title || '')
    } catch {}
  }

  useEffect(() => {
    load()
  }, [sessionId])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const handleRename = async () => {
    if (!sessionId || !editTitle.trim()) {
      setEditing(false)
      return
    }
    if (editTitle === session?.title) {
      setEditing(false)
      return
    }
    try {
      await renameSession(sessionId, editTitle.trim())
      await load()
      onSessionUpdated()
    } catch (e: any) {
      alert(`重命名失败: ${e?.message || e}`)
    } finally {
      setEditing(false)
      setOpen(false)
    }
  }

  const handleDelete = async () => {
    if (!sessionId) return
    if (!confirm(`删除对话"${session?.title || sessionId}"?`)) return
    try {
      await deleteSession(sessionId)
      onSessionDeleted()
    } catch (e: any) {
      alert(`删除失败: ${e?.message || e}`)
    } finally {
      setOpen(false)
    }
  }

  const handleCopyId = async () => {
    if (!sessionId) return
    try {
      await navigator.clipboard.writeText(sessionId)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }

  if (!sessionId) return null

  return (
    <div
      ref={ref}
      style={{
        padding: '0 28px',
        height: 62,
        borderBottom: `1px solid ${HUNTER.LINE}`,
        background: 'rgba(255,255,255,.78)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        position: 'sticky',
        top: 0,
        zIndex: 4,
        display: 'flex',
        alignItems: 'center',
      }}
    >
      {editing ? (
        <input
          value={editTitle}
          onChange={(e) => setEditTitle(e.target.value)}
          onBlur={handleRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleRename()
            if (e.key === 'Escape') {
              setEditTitle(session?.title || '')
              setEditing(false)
            }
          }}
          autoFocus
          style={{
            flex: 1,
            padding: '4px 8px',
            border: `1px solid ${HUNTER.THEME}`,
            borderRadius: 6,
            fontSize: 14,
            fontFamily: 'inherit',
            color: HUNTER.INK,
            background: '#ffffff',
            outline: 'none',
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => { setAnchor('title'); setOpen(!open) }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '3px 8px',
            background: open ? '#f8f6ef' : 'transparent',
            border: 'none',
            borderRadius: 6,
            color: HUNTER.INK,
            fontSize: 14,
            fontWeight: 600,
            cursor: 'pointer',
            fontFamily: 'inherit',
            maxWidth: '100%',
            minWidth: 0,
          }}
          onMouseEnter={(e) => {
            if (!open) e.currentTarget.style.background = '#faf9f4'
          }}
          onMouseLeave={(e) => {
            if (!open) e.currentTarget.style.background = 'transparent'
          }}
        >
          <span
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: 400,
            }}
          >
            {session?.title || '新对话'}
          </span>
          <ChevronDown size={12} style={{ opacity: 0.6 }} />
        </button>
      )}

      {open && !editing && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            // 从标题的 ∨ 打开就贴左,从右上角的 ··· 打开就贴右
            ...(anchor === 'more' ? { right: 16 } : { left: 24 }),
            marginTop: 4,
            minWidth: 200,
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(30,20,10,0.12)',
            padding: 4,
            zIndex: 100,
          }}
        >
          <button
            type="button"
            onClick={() => setEditing(true)}
            style={dropdownItemStyle}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#faf9f4')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <Edit3 size={12} /> 重命名
          </button>
          <button
            type="button"
            onClick={handleCopyId}
            style={dropdownItemStyle}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#faf9f4')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            {copied ? <Check size={12} style={{ color: HUNTER.DN }} /> : <Copy size={12} />}
            {copied ? '已复制' : '复制 session ID'}
          </button>
          <div style={{ height: 1, background: HUNTER.LINE, margin: '4px 6px' }} />
          <button
            type="button"
            onClick={handleDelete}
            style={{ ...dropdownItemStyle, color: HUNTER.UP }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#fbeaea')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <Trash2 size={12} /> 删除对话
          </button>
        </div>
      )}

      {/* 右侧 · 导出 / ••• · 无报告时 disabled + tooltip
          Community 本地部署去掉"分享"按钮 · 发布功能依赖 hunter_artifacts.published_artifact 表 */}
      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
        <SoftBtn
          onClick={onExport}
          disabled={!onExport}
          title={onExport ? '导出 Markdown' : '生成报告后可导出'}
          icon={<Download size={13} />}
          label="导出"
        />
        <SoftBtn
          onClick={() => { setAnchor('more'); setOpen(true) }}
          title="更多"
          icon={<MoreHorizontal size={14} />}
          label=""
        />
      </div>
    </div>
  )
}

function SoftBtn({
  onClick,
  disabled = false,
  title,
  icon,
  label,
}: {
  onClick?: () => void
  disabled?: boolean
  title?: string
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        height: 36,
        padding: label ? '0 13px' : '0 10px',
        border: `1px solid ${HUNTER.LINE}`,
        background: '#fff',
        borderRadius: 10,
        display: 'flex',
        alignItems: 'center',
        gap: 7,
        cursor: disabled ? 'not-allowed' : 'pointer',
        color: disabled ? '#b8b1a3' : HUNTER.INK_S,
        fontSize: 13,
        fontFamily: 'inherit',
        opacity: disabled ? 0.55 : 1,
        transition: 'background .1s',
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = '#f4f3ee'
      }}
      onMouseLeave={(e) => {
        if (!disabled) e.currentTarget.style.background = '#fff'
      }}
    >
      {icon}
      {label && <span>{label}</span>}
    </button>
  )
}

const dropdownItemStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  width: '100%',
  padding: '7px 10px',
  background: 'transparent',
  border: 'none',
  borderRadius: 6,
  fontSize: 12.5,
  color: HUNTER.INK_S,
  textAlign: 'left',
  cursor: 'pointer',
  fontFamily: 'inherit',
  transition: 'background 0.1s',
}
