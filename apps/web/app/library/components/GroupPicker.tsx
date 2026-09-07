'use client'
// 把一个能力移到别的组 · 或就地建一个新组。
//
// **为什么有两个 variant**:第一版只把它放在右侧详情面板的「类目」那一行,
// 结果用户根本没走到那一步 —— 他在列表里看卡片,不会为了改分类先点开详情。
// 现在卡片上直接给一个「移动」,详情面板里那个保留(改完能立刻看到类目变了)。
// 同一份逻辑两个壳,别再分成两个组件各写一遍。
//
// **为什么是下拉不是拖拽**:拖拽要处理拖到折叠区、拖出可视区、触屏没有
// 悬停这一堆情况,而这件事的节奏是"整理一次管很久",不是高频操作。

import { useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import { canRenameGroup } from '../../chat/lib/catalogClient'
import type { CapabilityItem, CapabilityGroup } from '../../chat/lib/catalogClient'

export default function GroupPicker({ item, groups, onMove, compact }: {
  item: CapabilityItem
  groups: CapabilityGroup[]
  onMove?: (itemKey: string, category: string) => Promise<void>
  /** true = 卡片上的紧凑版(只有一个「移动」按钮)· false/省略 = 详情面板的完整行 */
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 移动要登录(接口是硬鉴权的)· 没 token 就不给入口,
  // 免得点了必然 401
  const editable = !!onMove && canRenameGroup()
  const moved = item.category !== item.default_category
  const nameOf = (c: string) => groups.find((g) => g.category === c)?.display_name || c

  // 卡片上的按钮和 <select> 都是卡片点击区的一部分 —— 不拦住事件的话,
  // 点「移动」会顺带触发卡片的 onClick(打开详情),双击更糟(直接跳去对话框)
  const stop = (e: React.SyntheticEvent) => { e.stopPropagation() }

  const run = async (category: string) => {
    if (!onMove) return
    setBusy(true); setErr('')
    try {
      await onMove(item.key, category)
      setOpen(false); setCreating(false); setDraft('')
    } catch (e: any) {
      // 失败不收起面板 —— "这个组刚被删了""名字太长了"这类错误
      // 恰恰是要他改一下再试的
      setErr(e?.message || '移动失败')
    } finally {
      setBusy(false)
    }
  }

  if (!editable) {
    // 没登录:紧凑版什么都不显示,完整行仍要显示它在哪个组
    return compact ? null : <span>{nameOf(item.category)}</span>
  }

  const panel = (
    <div style={{ marginTop: 6 }} onClick={stop} onDoubleClick={stop}>
      {!creating ? (
        <select
          value=""
          disabled={busy}
          style={pickerStyle}
          onClick={stop}
          onChange={(e) => {
            const v = e.target.value
            if (v === '__new__') { setCreating(true); setDraft(''); setErr('') }
            else if (v) run(v)
          }}
        >
          <option value="">移动到…</option>
          {groups
            .filter((g) => g.category !== item.category)
            .map((g) => (
              <option key={g.category} value={g.category}>
                {g.display_name || g.category}
              </option>
            ))}
          {/* 新建分组是"自由分类"的关键 —— 不该被预设的那几个类目框住 */}
          <option value="__new__">＋ 新建分组…</option>
        </select>
      ) : (
        <input
          autoFocus
          value={draft}
          disabled={busy}
          placeholder="新分组的名字"
          style={pickerStyle}
          onClick={stop}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter') { e.preventDefault(); if (draft.trim()) run(draft.trim()) }
            if (e.key === 'Escape') { e.preventDefault(); setCreating(false); setErr('') }
          }}
        />
      )}

      <div style={{ marginTop: 5, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {moved && (
          <button style={linkBtnStyle} disabled={busy}
                  onClick={(e) => { stop(e); run('') }}>
            恢复默认（{nameOf(item.default_category)}）
          </button>
        )}
        <button
          style={linkBtnStyle}
          disabled={busy}
          onClick={(e) => { stop(e); setOpen(false); setCreating(false); setErr('') }}
        >取消</button>
      </div>

      {creating && !err && <div style={pickerHintStyle}>回车建组并移进去 · Esc 返回列表</div>}
      {err && <div style={pickerErrStyle}>{err}</div>}
    </div>
  )

  // ── 卡片上的紧凑版 ──
  if (compact) {
    return (
      <div style={{ flexShrink: 0 }} onClick={stop} onDoubleClick={stop}>
        {!open ? (
          <button style={cardBtnStyle}
                  onClick={(e) => { stop(e); setOpen(true); setErr('') }}
                  title={`当前在「${nameOf(item.category)}」· 点这里换一个组`}>
            移动
          </button>
        ) : (
          <div style={{ width: 190 }}>{panel}</div>
        )}
      </div>
    )
  }

  // ── 详情面板里的完整行 ──
  return (
    <div>
      <span>{nameOf(item.category)}</span>
      {moved && (
        <span style={movedTagStyle} title={`默认分组是「${nameOf(item.default_category)}」`}>
          已移动
        </span>
      )}
      {!open && (
        <button style={linkBtnStyle} onClick={() => { setOpen(true); setErr('') }}>移动…</button>
      )}
      {open && panel}
    </div>
  )
}

const linkBtnStyle: React.CSSProperties = {
  padding: 0, marginLeft: 8, fontSize: 11,
  color: HUNTER.THEME, background: 'none', border: 'none',
  cursor: 'pointer', fontFamily: 'inherit', textDecoration: 'underline',
}

// 卡片上那个 —— 和旁边「用它 →」同级,但更轻:
// 「用它」是主操作,「移动」是整理时才用的次要操作,不该抢它的视觉重量
const cardBtnStyle: React.CSSProperties = {
  flexShrink: 0, padding: '4px 9px', marginRight: 6,
  fontSize: 11.5, color: HUNTER.INK_F,
  background: 'transparent', border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
  whiteSpace: 'nowrap',
}

const movedTagStyle: React.CSSProperties = {
  marginLeft: 6, padding: '1px 5px', fontSize: 10,
  color: HUNTER.TAG_WARN_FG, background: HUNTER.TAG_WARN_BG,
  borderRadius: 3, whiteSpace: 'nowrap',
}

const pickerStyle: React.CSSProperties = {
  width: '100%', padding: '3px 6px', fontSize: 12,
  color: HUNTER.INK, background: HUNTER.PAPER,
  border: `1px solid ${HUNTER.LINE}`, borderRadius: 4,
  fontFamily: 'inherit', boxSizing: 'border-box',
}

const pickerHintStyle: React.CSSProperties = {
  marginTop: 3, fontSize: 10, lineHeight: 1.3, color: HUNTER.INK_F,
}

const pickerErrStyle: React.CSSProperties = {
  // 与 DetailPane 的「删除失败」同色 · 这一页报错文案统一用它
  marginTop: 3, fontSize: 10, lineHeight: 1.3, color: '#9B3A22',
}
