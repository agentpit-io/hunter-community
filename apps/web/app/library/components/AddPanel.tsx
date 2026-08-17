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

      {tab === 'skills' && (
        <SkillAddPanel categories={categories} onClose={onClose} onDone={onDone} />
      )}

      {tab === 'tools' && (
        <ToolAddPanel onClose={onClose} onDone={onDone} />
      )}

      {tab === 'sources' && <SourcesPlaceholder presetGroup={presetGroup} />}
    </section>
  )
}

const TITLE: Record<string, string> = {
  skills: '加一个 SKILL',
  tools: '接入一个工具',
  sources: '添加数据源',
}

/**
 * 数据源表单是步 2(`_21` §8)—— 表还没建,所以这里仍是占位。
 *
 * 但**步 1 已经把分类换成按来源分**了,所以这段话必须跟着改:
 * 现在用户是从「东方财富」那一组的 ＋ 点进来的,他期待的是
 * "接一个我自己的东财源",而不是泛泛的"添加数据源"。
 * 占位文案要接住这个具体意图,否则他会以为点错了地方。
 *
 * 放说明而不是 disabled 按钮的理由不变:disabled 只告诉用户"不能点",
 * 不告诉他为什么、什么时候能用、以及现在有没有别的办法。
 */
function SourcesPlaceholder({ presetGroup }: { presetGroup?: string }) {
  const named = presetGroup && presetGroup !== 'user' ? UPSTREAM_CN[presetGroup] : ''
  return (
    <div style={{ fontSize: 12.5, color: HUNTER.INK_S, lineHeight: 1.85 }}>
      {named && (
        <p style={{ margin: '0 0 10px', color: HUNTER.INK }}>
          你要接的是<b>自己的{named}数据源</b>。接进来之后取数会优先走你的,
          你的拿不到才回落到我们的。
        </p>
      )}
      <p style={{ margin: '0 0 10px' }}>
        这个表单正在做(步 2)。它比加工具多一件事:<b>第三方 API 的返回格式各不相同</b>。
        不过如果你接的是<b>已知来源</b>(Tushare / AKShare / 东财这些),
        字段映射我们内置,你只要填地址和 key —— 只有接完全自定义的接口时才需要自己填映射。
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

// 只用于占位文案里那句"你自己的 XX 数据源"。
// 正式表单(步 2)的来源下拉直接用后端返回的 upstream_label,不再另立一份 ——
// 两处各写一份中文名就是下一次清单漂移的开始(`_13` §3.1)
const UPSTREAM_CN: Record<string, string> = {
  akshare: 'AKShare', yahoo: 'Yahoo', xtick: 'XTick', eastmoney: '东方财富',
  cninfo: '巨潮资讯', cls: '财联社', tushare: 'Tushare', alpaca: 'Alpaca',
  sec: 'SEC', hkex: '港交所', truesource: 'TrueSource', internal: '平台自建',
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
