'use client'
import { HelpCircle } from 'lucide-react'
import { Card, CardHead, Note } from '../_ui'

/**
 * 安全与帮助 · 一期只交付三条底线（M-07 的正文与 FAQ 在 M6 补）。
 * 三条底线是产品信任口径，先说清楚；FAQ 里的**人机协作四条是二期的事**（H-08），
 * 这里一条都不出现。
 */
export default function FinanceHelpPage() {
  const items = [
    { t: '只模拟，不接实盘', d: '全部是虚拟资金，不绑券商、不配交易凭证，系统里没有任何开关能切到实盘。' },
    { t: '唯一账本', d: '钱、持仓、成交价全部由确定性代码算出来，AI 只提买卖意图，碰不到账本。' },
    { t: '随时能停', d: '一个开关暂停全部自动交易；已有持仓按规则处理，不受影响。' },
  ]
  return (
    <div className="max-w-3xl flex flex-col gap-4">
      <Card>
        <CardHead title="三条底线" icon={<HelpCircle className="w-4 h-4" />} />
        <div className="p-4 flex flex-col gap-3">
          {items.map((it, i) => (
            <div key={it.t} className="flex gap-3">
              <span className="w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-bold"
                style={{ background: 'rgba(176,106,50,.13)', color: 'var(--blue)' }}>{i + 1}</span>
              <div>
                <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{it.t}</div>
                <div className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{it.d}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Note tone="copper">
        「怎么用 / 怎么停 / FAQ」将在 <b>M6（M-07）</b> 补齐。本页现在只说三条底线，
        不写还没有的功能。
      </Note>
    </div>
  )
}
