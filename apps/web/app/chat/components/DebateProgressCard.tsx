'use client'
/**
 * 多专家辩论 · 6 阶段进度卡 · 显示在 chat 消息流里(替代 assistant 正常回复)
 *
 * 6 个圆点线性推进 · 当前阶段旋转 · 已完成阶段变绿钩
 * (Claude 风·选定方案·非弹幕滚动式)
 */
import { HUNTER } from '../../lib/hunter-theme'
import { Loader2, Check, Users } from 'lucide-react'
import type { DebateDepth } from '../lib/debateClient'

export type DebatePhase = 'idle' | 'technical' | 'news' | 'bull' | 'bear' | 'judge' | 'risk' | 'done' | 'error'

interface Props {
  phase: DebatePhase
  pct: number
  text: string
  stockName?: string
  stockCode?: string
  errorMsg?: string
  depth?: DebateDepth
}

// Sprint B · 6 逻辑阶段(risk 阶段内部含 3 方风控 + 委员会主席 4 次调用 · 前端合成一栏)
const STAGES: { key: DebatePhase; label: string; role: string }[] = [
  { key: 'technical', label: '技术面分析',       role: '技术分析师' },
  { key: 'news',      label: '新闻情报过滤',     role: 'Sentinel 新闻官(5 层过滤)' },
  { key: 'bull',      label: '多头论证',         role: '多头研究员' },
  { key: 'bear',      label: '空头反驳',         role: '空头研究员' },
  { key: 'judge',     label: '综合判官裁决',     role: '综合判官 · Deep Think Pro' },
  { key: 'risk',      label: '3 方风控 + 委员会', role: '激进 / 中性 / 保守 → 主席裁决' },
]

// 每个阶段的进度阈值 · 用于判断哪些阶段已完成
const PHASE_ORDER: Record<DebatePhase, number> = {
  idle: -1, technical: 0, news: 1, bull: 2, bear: 3, judge: 4, risk: 5, done: 6, error: -1,
}

const DEPTH_LABEL: Record<DebateDepth, string> = {
  quick: '1 轮辩论 · ~45s',
  normal: '2 轮辩论 · ~90s',
  deep: '3 轮辩论 · ~130s',
}

export default function DebateProgressCard({ phase, pct, text, stockName, stockCode, errorMsg, depth }: Props) {
  const currentIdx = PHASE_ORDER[phase] ?? -1
  const isDone = phase === 'done'
  const isError = phase === 'error'

  return (
    <div
      style={{
        margin: '0 24px 20px',
        padding: 18,
        background: isError ? '#fbeaea' : HUNTER.PAPER3,
        border: `1px solid ${isError ? HUNTER.UP : HUNTER.LINE}`,
        borderRadius: 12,
        fontFamily: HUNTER.SANS,
        color: HUNTER.INK,
      }}
    >
      {/* 头 · 标题 + 股票 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <div
          style={{
            width: 32, height: 32, borderRadius: 8,
            background: isError ? HUNTER.UP : HUNTER.THEME,
            color: '#fff',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <Users size={16} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700 }}>
            {isError ? '多专家辩论 · 失败' : isDone ? '多专家辩论 · 已完成' : '多专家辩论进行中'}
          </div>
          <div style={{ fontSize: 11.5, color: HUNTER.INK_F, marginTop: 1 }}>
            {stockName ? `${stockName}(${stockCode}) · ` : ''}
            8 位分析师 · {depth ? DEPTH_LABEL[depth] : '2 轮辩论'}
          </div>
        </div>
        <div style={{ fontSize: 20, fontWeight: 700, color: isError ? HUNTER.UP : HUNTER.THEME, fontFamily: HUNTER.SERIF }}>
          {isDone ? '100%' : `${pct}%`}
        </div>
      </div>

      {/* 6 阶段圆点 */}
      <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
        {STAGES.map((s, i) => {
          const done = isDone || i < currentIdx
          const active = !isDone && i === currentIdx
          const pending = i > currentIdx
          return (
            <div key={s.key} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '6px 4px',
              opacity: pending ? 0.5 : 1,
              transition: 'opacity 0.2s',
            }}>
              <div style={{
                width: 20, height: 20, borderRadius: '50%',
                border: `1.5px solid ${done ? HUNTER.DN : active ? HUNTER.THEME : HUNTER.LINE}`,
                background: done ? HUNTER.DN : active ? HUNTER.PAPER : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0,
              }}>
                {done ? (
                  <Check size={12} color="#fff" strokeWidth={3} />
                ) : active ? (
                  <Loader2 size={11} color={HUNTER.THEME} style={{ animation: 'spin 1s linear infinite' }} />
                ) : (
                  <span style={{ fontSize: 10, color: HUNTER.INK_F, fontWeight: 600 }}>{i + 1}</span>
                )}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: active ? 600 : 500, color: done ? HUNTER.DN : active ? HUNTER.INK : HUNTER.INK_S }}>
                  {s.label}
                </div>
                <div style={{ fontSize: 10.5, color: HUNTER.INK_F, marginTop: 1 }}>
                  {s.role}
                </div>
              </div>
              {active && (
                <div style={{
                  fontSize: 10.5,
                  color: HUNTER.THEME,
                  fontWeight: 600,
                  fontFamily: 'ui-monospace, Menlo, monospace',
                }}>
                  进行中
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 当前阶段文案 */}
      {!isError && text && !isDone && (
        <div style={{
          padding: '10px 12px',
          background: '#ffffff',
          borderRadius: 8,
          border: `1px solid ${HUNTER.LINE}`,
          fontSize: 12,
          color: HUNTER.INK_S,
          fontStyle: 'italic',
        }}>
          {text}
        </div>
      )}

      {/* 错误提示 */}
      {isError && errorMsg && (
        <div style={{
          padding: '10px 12px',
          background: '#fff',
          borderRadius: 8,
          border: `1px solid ${HUNTER.UP}`,
          fontSize: 12,
          color: HUNTER.UP,
        }}>
          ⚠ {errorMsg}
        </div>
      )}

      {isDone && (
        <div style={{
          padding: '10px 12px',
          background: '#EAF3EB',
          borderRadius: 8,
          border: `1px solid ${HUNTER.DN}`,
          fontSize: 12,
          color: HUNTER.DN,
          fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <Check size={13} strokeWidth={3} />
          报告已生成 · 请查看右侧 Artifact 面板
        </div>
      )}
    </div>
  )
}
