import React, { useState } from 'react'
import { T } from './tokens'

export interface EmptyHintV2Props {
  hasStock: boolean
  onExampleClick: (query: string) => void
  onOpenExpertGrid: () => void
}

const EXAMPLES_WITH_STOCK = [
  '这只票最近能买吗？',
  '基本面怎么样？',
  '什么时候是买点？',
]
const EXAMPLES_NO_STOCK = [
  '茅台最近能买吗？',
  '00700 腾讯怎么样？',
  '半导体板块看法',
]

export function EmptyHintV2({ hasStock, onExampleClick, onOpenExpertGrid }: EmptyHintV2Props) {
  const [gridOpen, setGridOpen] = useState(false)
  const examples = hasStock ? EXAMPLES_WITH_STOCK : EXAMPLES_NO_STOCK
  return (
    <div style={{ padding: '30px 20px', textAlign: 'center' }}>
      <div style={{ fontSize: 40, marginBottom: 8 }}>👋</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: T.INK, fontFamily: T.SERIF, marginBottom: 4 }}>
        我是猎鹿人投研助手
      </div>
      <div style={{ fontSize: 12, color: T.INK_F, marginBottom: 20, lineHeight: 1.6 }}>
        帮你判断"买不买 · 该不该拿 · 什么时机"
      </div>
      <div style={{ fontSize: 11, color: T.INK_F, marginBottom: 10, letterSpacing: 0.5 }}>
        你可以问：
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {examples.map(ex => (
          <button
            key={ex} onClick={() => onExampleClick(ex)}
            style={{
              background: T.PAPER, border: `1px solid ${T.LINE}`, borderRadius: 12,
              padding: '12px 16px', fontSize: 13, color: T.INK, cursor: 'pointer',
              textAlign: 'left', display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            <span style={{ color: T.THEME }}>💬</span>{ex}
          </button>
        ))}
      </div>
      <div
        style={{
          marginTop: 24, fontSize: 11, color: T.INK_F, cursor: 'pointer', padding: 6,
        }}
        onClick={() => { setGridOpen(true); onOpenExpertGrid() }}
      >
        ⋯ 或者：从 5 位专家里直接开始
      </div>
    </div>
  )
}
