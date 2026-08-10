'use client'
/**
 * 多专家辩论 · 深度选择器 · 用户点 ⚖️ SKILL 后弹窗
 *
 * quick   1 轮 · ~45s  · 尝鲜
 * normal  2 轮 · ~90s  · 推荐(默认)
 * deep    3 轮 · ~130s · 疑难/大额决策
 */
import { X, Zap, Scale, Brain } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import type { DebateDepth } from '../lib/debateClient'

interface Props {
  open: boolean
  onClose: () => void
  onConfirm: (depth: DebateDepth) => void
}

const OPTIONS: {
  key: DebateDepth
  label: string
  duration: string
  desc: string
  icon: React.ReactNode
  recommended?: boolean
}[] = [
  { key: 'quick',  label: 'Quick',  duration: '≈ 45s',  desc: '1 轮多空辩论 · 快速判断 · 适合尝鲜',
    icon: <Zap size={16} /> },
  { key: 'normal', label: 'Normal', duration: '≈ 90s',  desc: '2 轮多空辩论 · 决策质量与速度平衡',
    icon: <Scale size={16} />, recommended: true },
  { key: 'deep',   label: 'Deep',   duration: '≈ 130s', desc: '3 轮多空辩论 · 极致深度 · 疑难/大额决策',
    icon: <Brain size={16} /> },
]

export default function DebateDepthPicker({ open, onClose, onConfirm }: Props) {
  if (!open) return null

  return (
    <div
      onClick={(e) => e.target === e.currentTarget && onClose()}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,15,15,0.5)',
        zIndex: 200,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 20,
      }}
    >
      <div
        style={{
          background: '#fff', borderRadius: 12, width: '100%', maxWidth: 480,
          boxShadow: '0 25px 50px rgba(0,0,0,0.25)', overflow: 'hidden',
          fontFamily: HUNTER.SANS,
        }}
      >
        <div style={{
          padding: '14px 20px', borderBottom: `1px solid ${HUNTER.LINE}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: HUNTER.INK }}>
              ⚖️ 多专家辩论 · 选择深度
            </h3>
            <p style={{ margin: '3px 0 0', fontSize: 11.5, color: HUNTER.INK_F }}>
              8 位分析师 · Gemini Deep Think 综合判官 · 30 min 3 次配额
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: HUNTER.INK_F, padding: 4,
              display: 'flex', alignItems: 'center',
            }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 16 }}>
          {OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => onConfirm(opt.key)}
              style={{
                width: '100%', textAlign: 'left', marginBottom: 10,
                padding: '14px 16px',
                background: opt.recommended ? '#f9f4ec' : '#ffffff',
                border: `1.5px solid ${opt.recommended ? HUNTER.THEME : HUNTER.LINE}`,
                borderRadius: 10, cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 14,
                transition: 'all 0.1s',
                fontFamily: 'inherit',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = HUNTER.PAPER3
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = opt.recommended ? '#f9f4ec' : '#ffffff'
              }}
            >
              <div style={{
                width: 40, height: 40, borderRadius: 10,
                background: HUNTER.THEME, color: '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0,
              }}>
                {opt.icon}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontSize: 15, fontWeight: 700, color: HUNTER.INK }}>
                    {opt.label}
                  </span>
                  {opt.recommended && (
                    <span style={{
                      fontSize: 10, padding: '1px 6px', borderRadius: 3,
                      background: HUNTER.THEME, color: '#fff', fontWeight: 600,
                      letterSpacing: 0.3,
                    }}>推荐</span>
                  )}
                  <span style={{
                    marginLeft: 'auto',
                    fontSize: 11.5, color: HUNTER.INK_F,
                    fontFamily: 'ui-monospace, Menlo, monospace',
                  }}>
                    {opt.duration}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: HUNTER.INK_S, marginTop: 3, lineHeight: 1.5 }}>
                  {opt.desc}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
