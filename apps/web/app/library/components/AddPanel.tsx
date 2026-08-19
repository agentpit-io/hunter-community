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
import SourceAddPanel from './SourceAddPanel'
import RecommendedSkills from './RecommendedSkills'
import type { TabId } from '../lib/nav'

interface Props {
  tab: TabId
  /** 从某一组的 ＋ 点进来时的预选来源 slug(`_21` §3)· 'user' = 从空组进来 */
  presetGroup?: string
  categories: string[]
  onClose: () => void
  onDone: (msg: string) => void
}

export default function AddPanel({ tab, presetGroup, categories, onClose, onDone }: Props) {
  return (
    <section style={wrap}>
      <div style={head}>
        <span style={{ flex: 1, fontSize: 14, fontWeight: 600, color: HUNTER.INK }}>
          {TITLE[tab] || '添加'}
        </span>
        <button onClick={onClose} style={iconBtn} title="关闭"><X size={15} strokeWidth={2} /></button>
      </div>

      {(tab === 'skills' || tab === 'capabilities') && (
        <>
          {/* 推荐排在自建表单**前面**(`_24` §4.2)。
              删掉依赖平台的那批之后能力页开箱几乎是空的,
              「推荐安装」不再是锦上添花,而是开箱唯一的内容来源 ——
              那它就该是打开面板第一眼看到的东西,不是滚到底才有。 */}
          <div style={{ marginBottom: 14 }}>
            <div style={sectionTitle}>✨ 推荐安装 · 来自 GitHub 的开源 SKILL</div>
            <RecommendedSkills onDone={onDone} />
          </div>
          <div style={sectionTitle}>✍️ 或者自己写一个</div>
          <SkillAddPanel categories={categories} onClose={onClose} onDone={onDone} />
        </>
      )}

      {tab === 'tools' && (
        <ToolAddPanel onClose={onClose} onDone={onDone} />
      )}

      {tab === 'sources' && (
        <SourceAddPanel presetGroup={presetGroup} onClose={onClose} onDone={onDone} />
      )}
    </section>
  )
}

const sectionTitle: React.CSSProperties = {
  fontSize: 12, fontWeight: 600, color: HUNTER.INK,
  margin: '0 0 8px',
}

const TITLE: Record<string, string> = {
  skills: '加一个 SKILL',
  capabilities: '加一个能力',
  tools: '接入一个工具',
  sources: '添加数据源',
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
