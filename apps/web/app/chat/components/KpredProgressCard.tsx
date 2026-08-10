'use client'
/**
 * Kronos 走势预测 · 简易进度卡 · Sprint E
 * kpred 是快操作(3-8s)· 单条 loading bar 就够 · 不用像 debate 6 圆点
 */
import { Loader2, Check, TrendingUp } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

export type KpredPhase = 'start' | 'kronos' | 'factor' | 'rendering' | 'done' | 'error'

interface Props {
  phase: KpredPhase
  pct: number
  text: string
  stockName?: string
  stockCode?: string
  errorMsg?: string
}

const PHASE_LABEL: Record<KpredPhase, string> = {
  start: '启动预测',
  kronos: '调用 Kronos 模型',
  factor: '8 因子加权计算',
  rendering: '生成 HTML 报告',
  done: '完成',
  error: '失败',
}

export default function KpredProgressCard({ phase, pct, text, stockName, stockCode, errorMsg }: Props) {
  const isDone = phase === 'done'
  const isError = phase === 'error'

  return (
    <div
      style={{
        margin: '0 24px 20px',
        padding: 16,
        background: isError ? '#fbeaea' : HUNTER.PAPER3,
        border: `1px solid ${isError ? HUNTER.UP : HUNTER.LINE}`,
        borderRadius: 12,
        fontFamily: HUNTER.SANS,
        color: HUNTER.INK,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <div
          style={{
            width: 30, height: 30, borderRadius: 8,
            background: isError ? HUNTER.UP : HUNTER.THEME,
            color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {isDone ? <Check size={15} strokeWidth={3} /> : <TrendingUp size={15} />}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 14, fontWeight: 700 }}>
            {isError ? 'Kronos 预测 · 失败' : isDone ? 'Kronos 预测 · 完成' : `Kronos 走势预测中`}
          </div>
          <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 1 }}>
            {stockName ? `${stockName}(${stockCode}) · ` : ''}
            {PHASE_LABEL[phase]} · 通常 3-8s
          </div>
        </div>
        {!isError && (
          <div style={{ fontSize: 16, fontWeight: 700, color: HUNTER.THEME, fontFamily: HUNTER.SERIF }}>
            {pct}%
          </div>
        )}
      </div>

      {/* 进度条 */}
      {!isError && (
        <div style={{ height: 4, background: '#fff', borderRadius: 2, overflow: 'hidden', marginBottom: 8 }}>
          <div
            style={{
              width: `${pct}%`,
              height: '100%',
              background: isDone ? HUNTER.DN : HUNTER.THEME,
              transition: 'width 0.3s ease',
            }}
          />
        </div>
      )}

      {/* 当前文案 */}
      {!isDone && !isError && text && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: HUNTER.INK_S, fontStyle: 'italic' }}>
          <Loader2 size={11} style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }} />
          {text}
        </div>
      )}

      {/* 完成提示 */}
      {isDone && (
        <div style={{ fontSize: 11.5, color: HUNTER.DN, fontWeight: 600 }}>
          ✓ 报告已生成 · 请查看右侧 Artifact 面板
        </div>
      )}

      {/* 错误 */}
      {isError && errorMsg && (
        <div style={{
          padding: '8px 10px', background: '#fff', borderRadius: 6,
          border: `1px solid ${HUNTER.UP}`, fontSize: 11.5, color: HUNTER.UP,
        }}>
          ⚠ {errorMsg}
        </div>
      )}
    </div>
  )
}
