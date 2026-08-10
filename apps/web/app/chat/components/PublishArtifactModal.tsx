'use client'
/**
 * Publish artifact 弹窗 · 完全模仿 Claude.ai
 *
 * 3 态状态机:
 *   loading      查状态中 (getStatus)
 *   unpublished  未发布 · 显示 [Publish] 按钮
 *   published    已发布 · 显示 URL + [Copy link] + [Unpublish]
 *   blocked      曾发布 · 已撤销 · 不可 republish · 显示提示
 */
import { useEffect, useState } from 'react'
import { X, Link2, Check, Loader2, AlertCircle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  getStatus,
  publishArtifact,
  unpublishArtifact,
  type ArtifactStatus,
} from '../lib/artifactClient'

interface Props {
  open: boolean
  onClose: () => void
  messageId: string
  sessionId?: string
  title: string
  artifactType?: 'markdown' | 'html'   // Sprint E · 默认 markdown
  contentMd?: string
  contentHtml?: string
  onStatusChange?: (status: ArtifactStatus) => void
}

type Mode = 'loading' | 'unpublished' | 'published' | 'blocked' | 'error'

export default function PublishArtifactModal({
  open,
  onClose,
  messageId,
  sessionId,
  title,
  artifactType,
  contentMd,
  contentHtml,
  onStatusChange,
}: Props) {
  const [mode, setMode] = useState<Mode>('loading')
  const [status, setStatus] = useState<ArtifactStatus | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const load = async () => {
    if (!messageId) return
    setMode('loading')
    setErr('')
    try {
      const s = await getStatus(messageId)
      setStatus(s)
      onStatusChange?.(s)
      if (s.published) setMode('published')
      else if (s.republish_blocked) setMode('blocked')
      else setMode('unpublished')
    } catch (e: any) {
      setErr(e?.message || String(e))
      setMode('error')
    }
  }

  useEffect(() => {
    if (open) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, messageId])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const handlePublish = async () => {
    setBusy(true)
    setErr('')
    try {
      const resp = await publishArtifact({
        sessionId,
        messageId,
        title,
        artifactType: artifactType || 'markdown',
        contentMd,
        contentHtml,
      })
      const newStatus: ArtifactStatus = {
        published: true,
        short_id: resp.short_id,
        public_url: resp.public_url,
        published_at: resp.published_at,
      }
      setStatus(newStatus)
      onStatusChange?.(newStatus)
      setMode('published')
    } catch (e: any) {
      setErr(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleUnpublish = async () => {
    if (!status?.short_id) return
    if (!confirm('撤销发布后 · 此链接失效且不可 republish · 想再发只能新建对话。\n\n确定撤销吗?')) return
    setBusy(true)
    setErr('')
    try {
      await unpublishArtifact(status.short_id)
      const newStatus: ArtifactStatus = {
        published: false,
        short_id: status.short_id,
        republish_blocked: true,
      }
      setStatus(newStatus)
      onStatusChange?.(newStatus)
      setMode('blocked')
    } catch (e: any) {
      setErr(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleCopyLink = async () => {
    if (!status?.public_url) return
    try {
      await navigator.clipboard.writeText(status.public_url)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }

  if (!open) return null

  return (
    <div
      onClick={(e) => e.target === e.currentTarget && onClose()}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15, 15, 15, 0.5)',
        zIndex: 200,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div
        style={{
          background: '#ffffff',
          borderRadius: 12,
          width: '100%',
          maxWidth: 520,
          boxShadow: '0 25px 50px rgba(0,0,0,0.25)',
          overflow: 'hidden',
          fontFamily: HUNTER.SANS,
        }}
      >
        <div
          style={{
            padding: '16px 20px',
            borderBottom: `1px solid ${HUNTER.LINE}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: HUNTER.INK }}>
            发布 artifact · 公开链接
          </h3>
          <button
            onClick={onClose}
            type="button"
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: HUNTER.INK_F,
              padding: 4,
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20 }}>
          {mode === 'loading' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: HUNTER.INK_F, fontSize: 13 }}>
              <Loader2 size={16} style={{ animation: 'spin 1.2s linear infinite' }} />
              查询发布状态...
            </div>
          )}

          {mode === 'error' && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 12, background: '#fbeaea', borderRadius: 8, color: HUNTER.UP, fontSize: 13 }}>
              <AlertCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>加载失败</div>
                <div>{err}</div>
                <button onClick={load} style={btnLinkStyle}>重试</button>
              </div>
            </div>
          )}

          {mode === 'unpublished' && (
            <>
              <p style={{ margin: '0 0 16px', fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.6 }}>
                将此报告发布为公开链接 · 任何人通过链接可查看
                <br />
                <strong style={{ color: HUNTER.INK }}>你的对话上下文保持私有</strong>
              </p>
              <button
                type="button"
                onClick={handlePublish}
                disabled={busy}
                style={{
                  ...btnPrimaryStyle,
                  opacity: busy ? 0.6 : 1,
                  cursor: busy ? 'wait' : 'pointer',
                }}
              >
                {busy ? <Loader2 size={14} style={{ animation: 'spin 1.2s linear infinite' }} /> : null}
                {busy ? '发布中...' : '立即发布'}
              </button>
              {err && (
                <div style={{ marginTop: 10, color: HUNTER.UP, fontSize: 12 }}>
                  ⚠ {err}
                </div>
              )}
            </>
          )}

          {mode === 'published' && status && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: HUNTER.DN }} />
                <strong style={{ color: HUNTER.INK, fontSize: 14 }}>已发布 · 公开可访问</strong>
              </div>
              <p style={{ margin: '0 0 16px', fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.6 }}>
                任何人通过链接可查看此报告 · 无需登录
                <br />
                你的对话上下文<strong style={{ color: HUNTER.INK }}>保持私有</strong> · 不会一同曝光
              </p>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 12px',
                  background: '#f8f6ef',
                  border: `1px solid ${HUNTER.LINE}`,
                  borderRadius: 8,
                  marginBottom: 16,
                }}
              >
                <input
                  readOnly
                  value={status.public_url}
                  onFocus={(e) => e.currentTarget.select()}
                  style={{
                    flex: 1,
                    border: 'none',
                    outline: 'none',
                    background: 'transparent',
                    fontSize: 12.5,
                    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                    color: HUNTER.INK_S,
                  }}
                />
                <button
                  type="button"
                  onClick={handleCopyLink}
                  title="复制链接"
                  style={{
                    background: 'transparent',
                    border: `1px solid ${HUNTER.LINE}`,
                    borderRadius: 6,
                    padding: '5px 8px',
                    cursor: 'pointer',
                    color: copied ? HUNTER.DN : HUNTER.THEME,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 4,
                    fontSize: 12,
                    fontFamily: 'inherit',
                    fontWeight: 600,
                  }}
                >
                  {copied ? <Check size={13} /> : <Link2 size={13} />}
                  {copied ? '已复制' : '复制链接'}
                </button>
              </div>

              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, borderTop: `1px solid ${HUNTER.LINE}`, paddingTop: 14 }}>
                <button
                  type="button"
                  onClick={handleUnpublish}
                  disabled={busy}
                  style={{
                    padding: '7px 14px',
                    background: '#ffffff',
                    border: `1px solid ${HUNTER.UP}`,
                    color: HUNTER.UP,
                    borderRadius: 6,
                    cursor: busy ? 'wait' : 'pointer',
                    fontSize: 12.5,
                    fontWeight: 600,
                    fontFamily: 'inherit',
                    opacity: busy ? 0.6 : 1,
                    flexShrink: 0,
                  }}
                >
                  {busy ? '撤销中...' : '撤销发布'}
                </button>
                <div style={{ fontSize: 11.5, color: HUNTER.INK_F, lineHeight: 1.5 }}>
                  撤销后此链接立即失效 · <b style={{ color: HUNTER.INK_S }}>不可再次发布</b>
                  <br />
                  如需重新分享 · 请新建对话生成新的 artifact
                </div>
              </div>
              {err && (
                <div style={{ marginTop: 10, color: HUNTER.UP, fontSize: 12 }}>⚠ {err}</div>
              )}
            </>
          )}

          {mode === 'blocked' && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: HUNTER.INK_F }} />
                <strong style={{ color: HUNTER.INK, fontSize: 14 }}>已撤销发布</strong>
              </div>
              <p style={{ margin: '0 0 16px', fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.6 }}>
                此内容已被撤销发布 · <strong>不可再次发布</strong>
                <br />
                若需重新分享 · 请创建新对话 · 让 Hunter 重新生成报告
              </p>
              <button type="button" onClick={onClose} style={btnPrimaryStyle}>
                知道了
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const btnPrimaryStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '8px 18px',
  background: HUNTER.THEME,
  color: '#fff',
  border: 'none',
  borderRadius: 6,
  cursor: 'pointer',
  fontSize: 13,
  fontWeight: 600,
  fontFamily: 'inherit',
}

const btnLinkStyle: React.CSSProperties = {
  marginTop: 8,
  background: 'transparent',
  border: 'none',
  color: HUNTER.THEME,
  textDecoration: 'underline',
  cursor: 'pointer',
  fontSize: 12,
  padding: 0,
}
