'use client'

// 能力库 · 添加面板(_20 步 1)
//
// 三种"添加"收到一处,**按当前 tab 决定加什么** —— 在数据源 tab 就是加数据源,
// 在工具箱 tab 就是接工具。不做一个笼统的「添加」再让用户选类型:
// 用户点进某个 tab 时,他想加什么已经确定了。
//
// 表单开在右侧内容区而不是弹窗 —— 三种表单都不短,弹窗会把列表挡住,
// 而用户填的时候经常要回头看已有条目做参考。

import { X } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import SkillAddPanel from '../../chat/components/SkillAddPanel'
import ToolAddPanel from '../../chat/components/ToolAddPanel'
import type { TabId } from '../lib/nav'

interface Props {
  tab: TabId
  categories: string[]
  onClose: () => void
  onDone: (msg: string) => void
}

export default function AddPanel({ tab, categories, onClose, onDone }: Props) {
  return (
    <section style={wrap}>
      <div style={head}>
        <span style={{ flex: 1, fontSize: 14, fontWeight: 600, color: HUNTER.INK }}>
          {TITLE[tab] || '添加'}
        </span>
        <button onClick={onClose} style={iconBtn} title="关闭"><X size={15} strokeWidth={2} /></button>
      </div>

      {tab === 'skills' && (
        <SkillAddPanel categories={categories} onClose={onClose} onDone={onDone} />
      )}

      {tab === 'tools' && (
        <ToolAddPanel onClose={onClose} onDone={onDone} />
      )}

      {tab === 'sources' && <SourcesPlaceholder />}
    </section>
  )
}

const TITLE: Record<string, string> = {
  skills: '加一个 SKILL',
  tools: '接入一个工具',
  sources: '添加数据源',
}

/**
 * 数据源的表单还没做(后端也还没有 user_data_source 表)。
 *
 * 这里放一段**说清楚现状**的占位,而不是一个 disabled 按钮 ——
 * disabled 按钮只告诉用户"不能点",不告诉他为什么、什么时候能用、
 * 以及现在有没有别的办法。
 */
function SourcesPlaceholder() {
  return (
    <div style={{ fontSize: 12.5, color: HUNTER.INK_S, lineHeight: 1.85 }}>
      <p style={{ margin: '0 0 10px' }}>
        自定义数据源还在做。它比加工具复杂的地方在于:
        <b>第三方 API 的返回格式各不相同</b>,要有一层字段映射把它对齐到我们的格式,
        并且要能当场测一次、把原始返回和映射结果并排给你看 ——
        否则填完了也不知道对没对。
      </p>
      <p style={{ margin: '0 0 10px' }}>
        在那之前,如果你有自己的数据服务,<b>可以先包成一个 MCP 工具接进来</b>：
        到「工具箱」标签页点添加,模型一样能调到。
      </p>
      <p style={{ margin: 0, color: HUNTER.INK_F }}>
        另外,如果只是想换掉我们默认的数据地址(而不是新增一个源),
        设置页里已经可以改 data / llm / kronos 三个的地址与 key。
      </p>
    </div>
  )
}

const wrap: React.CSSProperties = {
  margin: '0 0 16px', padding: '14px 16px', borderRadius: 10,
  border: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER3,
}
const head: React.CSSProperties = {
  display: 'flex', alignItems: 'center', marginBottom: 10,
}
const iconBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: HUNTER.INK_F,
  cursor: 'pointer', padding: 2, display: 'flex',
}
