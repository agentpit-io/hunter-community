'use client'

// 「解锁全部工具」弹窗 · 开源版拿平台 key 的唯一入口
//
// 触发点有两个:侧栏左下角那颗按钮,以及在未解锁时点任意一张 SKILL 卡。
// 后者是关键 —— 用户点「行情速查」时最想知道的是"为什么不能用、怎么才能用",
// 这时候弹窗比在输入框里塞一句模板有用得多。
//
// 三段式:说清楚为什么要 key → 一键去申请 → 粘回来保存。
// 保存走后端 PUT,后端会先向 Hunter 验一次再落库,所以这里能直接把失败原因显示出来,
// 不会出现"存下了但第一次调用才发现是废 key"。

import { useEffect, useState } from 'react'
import { X, ExternalLink, Check, Loader2, ShieldCheck, AlertCircle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  getUnlockStatus, saveKey, clearKey,
  type UnlockStatus, APPLY_URL_FALLBACK,
} from '../lib/unlockClient'

interface Props {
  /** 从某张 SKILL 卡点进来时传它的名字,文案里点名会更贴题 */
  triggeredBy?: string
  onClose: () => void
  onUnlocked?: () => void
}

export default function UnlockModal({ triggeredBy, onClose, onUnlocked }: Props) {
  const [st, setSt] = useState<UnlockStatus | null>(null)
  const [key, setKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { void getUnlockStatus(true).then(setSt) }, [])

  // apply_url 兜底:空/相对路径都不合法 · 强制走前端 fallback
  // 见 2026-08-29 事故:HUNTER_UPSTREAM_URL 未配时后端返 /dev/api-keys · 前端跳相对路径 404
  const _rawApplyUrl = st?.apply_url || ''
  const applyUrl = /^https?:\/\//i.test(_rawApplyUrl) ? _rawApplyUrl : APPLY_URL_FALLBACK

  const submit = async () => {
    const k = key.trim()
    if (!k) return
    setSaving(true); setErr('')
    try {
      const next = await saveKey(k)
      setSt(next)
      setKey('')
      onUnlocked?.()
    } catch (e: any) {
      setErr(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const unbind = async () => {
    if (!confirm('解除后左侧工具会重新变为未解锁,确定吗?')) return
    await clearKey()
    setSt(await getUnlockStatus(true))
  }

  const unlocked = !!st?.unlocked

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        background: 'rgba(28,22,16,.42)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 520, maxHeight: '86vh', overflowY: 'auto',
          background: HUNTER.PAPER, borderRadius: HUNTER.R_XL,
          border: `1px solid ${HUNTER.LINE}`, boxShadow: HUNTER.SHADOW,
        }}
      >
        {/* 头 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '16px 18px', borderBottom: `1px solid ${HUNTER.LINE}`,
        }}>
          <ShieldCheck size={18} style={{ color: HUNTER.THEME }} />
          <div style={{ flex: 1, fontSize: 15, fontWeight: 600, color: HUNTER.INK }}>
            {unlocked ? '已解锁全部工具' : '解锁全部工具与 SKILL'}
          </div>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: HUNTER.SOFT, padding: 4, lineHeight: 0,
          }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: 18 }}>
          {unlocked ? (
            <>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '10px 12px', borderRadius: HUNTER.R_MD,
                background: HUNTER.TAG_OK_BG, color: HUNTER.TAG_OK_FG,
                fontSize: 13, marginBottom: 14,
              }}>
                <Check size={14} />
                <span>已接入 Hunter 服务 · {st?.masked}</span>
              </div>
              {st!.tools.length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 12, color: HUNTER.INK_F, marginBottom: 6 }}>当前可用</div>
                  {st!.tools.map((t) => (
                    <div key={t.name} style={{ display: 'flex', gap: 8, padding: '4px 0', fontSize: 13 }}>
                      <Check size={13} style={{ color: HUNTER.SUCCESS, flexShrink: 0, marginTop: 3 }} />
                      <span style={{ color: HUNTER.INK }}>{t.title}</span>
                      <span style={{ color: HUNTER.INK_F }}>· {t.desc}</span>
                    </div>
                  ))}
                </div>
              )}
              {st?.env_locked ? (
                <div style={{ fontSize: 12, color: HUNTER.INK_F }}>
                  这把 key 来自 <code>.env</code> 的 <code>HUNTER_API_KEY</code>,
                  要更换请改 <code>.env</code> 后 <code>docker compose up -d</code>。
                </div>
              ) : (
                <button onClick={unbind} style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: 12, color: HUNTER.INK_F, textDecoration: 'underline', padding: 0,
                }}>
                  解除绑定
                </button>
              )}
            </>
          ) : (
            <>
              <p style={{ fontSize: 13, lineHeight: 1.7, color: HUNTER.INK_S, margin: '0 0 12px' }}>
                {triggeredBy
                  ? <>「<b>{triggeredBy}</b>」这类能力要用 Hunter 平台的数据与算力,</>
                  : <>左侧的工具与 SKILL 要用 Hunter 平台的数据与算力,</>}
                需要一把我们签发的 key。<b>免费</b>,大约 30 秒。
              </p>
              <p style={{ fontSize: 12, lineHeight: 1.7, color: HUNTER.INK_F, margin: '0 0 14px' }}>
                普通对话不受影响 —— 你填自己的大模型 key 就能一直聊。
                这把 key 只用来解锁行情速查、K 线、财报、关键新闻、
                UZI 深度分析、Kronos 走势预测等能力。
              </p>

              {st?.configured && !st.upstream_error && (
                <div style={{
                  display: 'flex', gap: 8, padding: '10px 12px', marginBottom: 14,
                  borderRadius: HUNTER.R_MD, background: HUNTER.TAG_WARN_BG,
                  color: HUNTER.TAG_WARN_FG, fontSize: 12, lineHeight: 1.6,
                }}>
                  <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>当前这把 key（{st.masked}）已失效或被吊销,换一把新的。</span>
                </div>
              )}
              {st?.upstream_error && (
                <div style={{
                  display: 'flex', gap: 8, padding: '10px 12px', marginBottom: 14,
                  borderRadius: HUNTER.R_MD, background: HUNTER.TAG_WARN_BG,
                  color: HUNTER.TAG_WARN_FG, fontSize: 12, lineHeight: 1.6,
                }}>
                  <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>{st.message}</span>
                </div>
              )}

              <a
                href={applyUrl} target="_blank" rel="noopener noreferrer"
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  padding: '10px 14px', marginBottom: 14,
                  background: HUNTER.THEME, color: '#fff', borderRadius: HUNTER.R_MD,
                  fontSize: 13, fontWeight: 600, textDecoration: 'none',
                }}
              >
                去申请 Key（免费）
                <ExternalLink size={14} />
              </a>

              <div style={{ fontSize: 12, color: HUNTER.INK_F, marginBottom: 6 }}>
                申请到之后粘到这里
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void submit() }}
                  placeholder="hunt_tools_..."
                  spellCheck={false}
                  disabled={st?.env_locked}
                  style={{
                    flex: 1, padding: '9px 11px', fontSize: 13,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    border: `1px solid ${HUNTER.LINE_STRONG}`, borderRadius: HUNTER.R_MD,
                    background: st?.env_locked ? HUNTER.PAPER2 : '#fff', color: HUNTER.INK,
                    outline: 'none',
                  }}
                />
                <button
                  onClick={submit}
                  disabled={saving || !key.trim() || st?.env_locked}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '9px 14px', fontSize: 13, fontWeight: 600,
                    background: key.trim() && !st?.env_locked ? HUNTER.INK : HUNTER.PAPER2,
                    color: key.trim() && !st?.env_locked ? '#fff' : HUNTER.SOFT,
                    border: 'none', borderRadius: HUNTER.R_MD,
                    cursor: key.trim() && !saving && !st?.env_locked ? 'pointer' : 'default',
                  }}
                >
                  {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                  保存
                </button>
              </div>

              {st?.env_locked && (
                <div style={{ fontSize: 12, color: HUNTER.INK_F, marginTop: 8, lineHeight: 1.6 }}>
                  这台实例的 key 由 <code>.env</code> 的 <code>HUNTER_API_KEY</code> 提供,
                  UI 改不了 —— 请改 <code>.env</code> 后 <code>docker compose up -d</code>。
                </div>
              )}
              {err && (
                <div style={{ fontSize: 12, color: HUNTER.UP, marginTop: 8, lineHeight: 1.6 }}>{err}</div>
              )}
              <div style={{ fontSize: 11, color: HUNTER.SOFT, marginTop: 12, lineHeight: 1.6 }}>
                key 加密存在你自己的数据库里,不会随代码或日志外传。
                也可以写进项目根目录 <code>.env</code> 的 <code>HUNTER_API_KEY</code>。
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
