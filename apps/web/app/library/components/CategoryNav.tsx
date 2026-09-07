'use client'
// 左侧分类导航(240px)· 3 tab · 每 tab 若干 group
// 见方案 §3.1: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md
import { useState } from 'react'
import Link from 'next/link'
import { HUNTER } from '../../lib/hunter-theme'
import { TABS, type TabId, type LibraryQuery, buildQuery } from '../lib/nav'
import { canRenameGroup } from '../../chat/lib/catalogClient'
import type { SourceGroup, CapabilityGroup } from '../../chat/lib/catalogClient'

interface Props {
  query: LibraryQuery
  sources: SourceGroup[] | null
  /** 工具与 SKILL 合并后的能力分组(`_22` 步 3) */
  caps: CapabilityGroup[] | null
  /** 点「＋ 添加」· 由页面按当前 tab 决定加什么(_20 §2)。
   *  `presetGroup` = 从某一组的 ＋ 点进来时,表单预选好那个来源(`_21` §3) */
  onAdd?: (presetGroup?: string) => void
  /** 点「↻ 恢复初始」· 删掉当前 tab 的全部用户自定义项 */
  onReset?: () => void
  /** 改能力分组的显示名 · 传空串 = 恢复默认。
   *  只给能力那一栏 —— 数据源的组是「来源」(东方财富 / AKShare),
   *  那是客观事实不是分类,改名会让人对不上号。 */
  onRenameGroup?: (category: string, name: string) => Promise<void>
}

export default function CategoryNav({ query, sources, caps, onAdd, onReset, onRenameGroup }: Props) {
  // 概览页没有"当前在加什么"的上下文,所以这两个操作只在具体 tab 下可用
  const actionable = query.tab !== 'overview'
  return (
    <nav style={navStyle}>
      {/* 问题28:「＋ 添加」原来在侧栏**最底下**,要滚到底才看得见。
          它是这一页最主要的动作(接数据源 / 装能力),不该藏在末尾。
          挪到顶部,和「概览」并列。 */}
      <div style={{ padding: '0 12px 10px' }}>
        <button
          style={mgmtBtnStyle(!actionable)} disabled={!actionable}
          // 不能写 onClick={onAdd} —— onAdd 的第一个参数现在是预选来源(string),
          // 直接当 handler 会把 MouseEvent 当成来源传进去
          onClick={() => onAdd?.()}
          title={actionable ? ADD_HINT[query.tab] : '先选一个分类(数据源 / 能力)'}
        >＋ 添加</button>
      </div>
      {TABS.map((tab) => {
        const isActiveTab = query.tab === tab.id
        return (
          <div key={tab.id} style={{ marginBottom: 4 }}>
            <Link href={buildQuery({ tab: tab.id })} style={tabHeadStyle(isActiveTab && !query.group)}>
              <span style={{ fontSize: 15 }}>{tab.icon}</span>
              <span>{tab.label}</span>
            </Link>

            {/* 数据源按**来源**分组(`_21` §2)—— 不再按市场。
                市场变成了内容区顶部的筛选条。
                每组带一个 ＋:点「东方财富」那一行的 ＋,表单直接预选好东财 */}
            {tab.id === 'sources' && sources && (
              <GroupList tabId={tab.id} groups={sources.map(g => ({
                id: g.upstream, label: g.label, total: g.total, ready: g.ready,
                emphasis: g.owner === 'user',
              }))} activeGroup={isActiveTab ? query.group : undefined}
                onAddTo={onAdd} />
            )}
            {tab.id === 'capabilities' && caps && (
              /* 问题27:能力这边原来不传 onAddTo,于是只有数据源那几行有 ＋,
                 能力这几行光秃秃 —— 同一个侧栏两套规矩,用户会以为能力不能加。
                 传上之后两边形式统一。 */
              <GroupList tabId={tab.id} groups={caps.map(g => ({
                // id 用**原始** category:URL 的 ?group=、内容区筛选都按它走,
                // 所以用户改完名之后,他之前存的链接照样打得开。
                // label 才是改过的那个显示名。
                id: g.category, label: g.display_name || g.category,
                total: g.total, ready: g.ready,
              }))} activeGroup={isActiveTab ? query.group : undefined}
                onAddTo={onAdd} onRename={onRenameGroup} />
            )}
          </div>
        )
      })}

      <div style={separatorStyle} />

      <div style={{ padding: '0 12px' }}>
        {/* 数据源那条按钮 `_24` §3.1 **删了**(撤架后前提没了)。
            能力这条保留:它删的是用户自己加的东西,和平台无关。 */}
        {query.tab !== 'sources' && (
          <button
            style={mgmtBtnStyle(!actionable)} disabled={!actionable}
            onClick={onReset}
            title={actionable ? RESET_HINT[query.tab] : '先选一个分类'}
          >{RESET_LABEL[query.tab] || '↻ 恢复初始'}</button>
        )}
      </div>
    </nav>
  )
}

const ADD_HINT: Record<string, string> = {
  sources: '添加自己的数据源',
  capabilities: '装一个 SKILL,或接入自己的 MCP 工具',
}

const RESET_LABEL: Record<string, string> = {
  capabilities: '↻ 恢复初始',
}
const RESET_HINT: Record<string, string> = {
  capabilities: '删掉你自己加的全部 SKILL 与工具 · 内置的不受影响',
}

function GroupList({ tabId, groups, activeGroup, onAddTo, onRename }: {
  tabId: TabId
  groups: { id: string; label: string; total: number; ready: number; emphasis?: boolean }[]
  activeGroup?: string
  /** 给每组挂一个 ＋ · 传了才渲染。点它 = 「给这个来源加一个我自己的」 */
  onAddTo?: (group: string) => void
  /** 传了才出现 ✎ · 点它就地改这一组的显示名。空串 = 恢复默认 */
  onRename?: (category: string, name: string) => Promise<void>
}) {
  // ＋ 只在**悬停或选中**时露出来。11 个来源每行都常驻一个 ＋,
  // 视觉上是 11 个同等重量的号召 —— 而用户进这个页面九成是来看有什么源的,
  // 不是来加源的。用 opacity 而不是条件渲染:留着占位,
  // 露出时行内元素不会横向跳一下。
  const [hover, setHover] = useState<string | null>(null)
  // 正在改名的那一组(存原始 category)· 同时只允许改一个
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 改名要登录(接口是硬鉴权的)。没 token 就干脆不显示 ✎ ——
  // 显示一个点了必然报 401 的按钮,比没有这个按钮更糟
  const renamable = !!onRename && canRenameGroup()

  const startEdit = (id: string, label: string) => {
    setEditing(id); setDraft(label); setErr('')
  }
  const cancelEdit = () => { setEditing(null); setDraft(''); setErr('') }

  const commit = async (g: { id: string; label: string }) => {
    if (!onRename) return
    const next = draft.trim()
    // 没改动就直接收起来,不打无谓的请求
    if (next === g.label) { cancelEdit(); return }
    setBusy(true); setErr('')
    try {
      // 清空 = 恢复默认名字(后端收到空串就删掉这条覆盖)
      await onRename(g.id, next)
      cancelEdit()
    } catch (e: any) {
      // 失败**不收起输入框** —— 收起来的话用户刚打的字就没了,
      // 而"重名了""太长了"这类错误恰恰是要他改一下再存的
      setErr(e?.message || '改名失败')
    } finally {
      setBusy(false)
    }
  }

  if (groups.length === 0) return null
  return (
    <div style={{ marginTop: 2, marginBottom: 4 }}>
      {groups.map((g) => {
        const isActive = activeGroup === g.id
        const label = g.label || g.id
        const showAdd = hover === g.id || isActive
        const isEditing = editing === g.id

        if (isEditing) {
          return (
            <div key={g.id} style={{ padding: '2px 12px 4px 36px' }}>
              <input
                autoFocus
                value={draft}
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); commit(g) }
                  // Esc 明确放弃 —— 有它在,失焦保存才不会让人觉得"没法反悔"
                  if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
                }}
                onBlur={() => { if (!busy && !err) commit(g) }}
                style={renameInputStyle}
                placeholder={g.id}
                title={`原名「${g.id}」· 清空回车恢复默认`}
              />
              {/* 报错时**也要保留 Esc 那句** —— 出错后 onBlur 不再自动保存
                  (免得把改坏的名字存进去),这时 Esc 是唯一的退出方式,
                  提示要是被错误文案顶掉,用户就被卡在这个输入框里了 */}
              {err && <div style={renameErrStyle}>{err}</div>}
              <div style={renameHintStyle}>
                {err ? 'Esc 取消 · 改完回车重试' : '回车保存 · Esc 取消 · 清空恢复默认'}
              </div>
            </div>
          )
        }

        return (
          <div
            key={g.id}
            style={groupRowStyle(isActive)}
            onMouseEnter={() => setHover(g.id)}
            onMouseLeave={() => setHover((h) => (h === g.id ? null : h))}
          >
            <Link href={buildQuery({ tab: tabId, group: g.id })} style={groupLinkStyle(isActive, g.emphasis)}>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {label}
              </span>
              <span style={{ fontSize: 11, color: HUNTER.INK_F, marginLeft: 8 }}>
                {/* 「你自己的」空组显示"—"而不是 0/0 —— 0/0 读起来像"坏了",
                    而它其实是"还没加过",两件完全不同的事 */}
                {g.emphasis && g.total === 0 ? '—' : `${g.ready}/${g.total}`}
              </span>
            </Link>
            {renamable && (
              <button
                onClick={(e) => { e.preventDefault(); startEdit(g.id, label) }}
                style={groupIconBtnStyle(showAdd, 4)}
                tabIndex={showAdd ? 0 : -1}
                aria-hidden={!showAdd}
                title={label === g.id ? `给「${g.id}」改个名字` : `改名 · 原名「${g.id}」`}
              >✎</button>
            )}
            {onAddTo && (
              <button
                onClick={(e) => { e.preventDefault(); onAddTo(g.id) }}
                style={groupIconBtnStyle(showAdd, 12)}
                tabIndex={showAdd ? 0 : -1}
                aria-hidden={!showAdd}
                title={g.emphasis ? '添加一个自己的数据源' : `接一个自己的${label}数据源`}
              >＋</button>
            )}
          </div>
        )
      })}
    </div>
  )
}

const navStyle: React.CSSProperties = {
  width: 240,
  minWidth: 240,
  height: '100%',
  background: HUNTER.PANEL,
  borderRight: `1px solid ${HUNTER.LINE}`,
  padding: '16px 0',
  overflowY: 'auto',
  fontFamily: HUNTER.SANS,
}

const tabHeadStyle = (active: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '8px 16px',
  fontSize: 13,
  fontWeight: 600,
  color: active ? HUNTER.THEME : HUNTER.INK,
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  textDecoration: 'none',
  cursor: 'pointer',
  transition: 'background 0.1s',
})

// 行容器与链接分开:＋ 按钮要和 Link 平级,不能嵌在 Link 里
// (嵌进去点 ＋ 会先触发跳转,`_20` 那次「加号重叠」就是布局没分层)
const groupRowStyle = (active: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  transition: 'background 0.1s',
})

const groupLinkStyle = (active: boolean, emphasis?: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  flex: 1,
  minWidth: 0,
  padding: '5px 4px 5px 40px',
  fontSize: 12,
  // 「你自己的」加粗 —— 用户脱离我们的能力是这次改造的主题,
  // 在视觉上也该看得出它和我们的源不是一回事
  fontWeight: emphasis ? 600 : 400,
  color: active ? HUNTER.THEME : emphasis ? HUNTER.INK : HUNTER.INK_S,
  textDecoration: 'none',
  cursor: 'pointer',
})

// ＋ 与 ✎ 共用。`padRight` 区分谁在最右边(＋ 靠边 12,✎ 挤在它左边 4)
const groupIconBtnStyle = (visible: boolean, padRight: number): React.CSSProperties => ({
  padding: `2px ${padRight}px 2px 4px`,
  fontSize: 13,
  lineHeight: 1,
  color: HUNTER.INK_F,
  background: 'none',
  border: 'none',
  // 不可见时连指针事件一起关掉 —— 否则鼠标划过那块空白仍会变成手型,
  // 用户会以为那里藏了什么可点的东西
  opacity: visible ? 1 : 0,
  pointerEvents: visible ? 'auto' : 'none',
  cursor: 'pointer',
  fontFamily: 'inherit',
  transition: 'opacity 0.12s',
})

// 改名输入框:左内边距对齐组名文字(40 - 4 的 border/padding),
// 这样点 ✎ 之后字不会横向跳一下
const renameInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '3px 6px',
  fontSize: 12,
  color: HUNTER.INK,
  background: HUNTER.PANEL,
  border: `1px solid ${HUNTER.THEME}`,
  borderRadius: HUNTER.R_SM,
  outline: 'none',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
}

const renameHintStyle: React.CSSProperties = {
  marginTop: 2,
  fontSize: 10,
  lineHeight: 1.3,
  color: HUNTER.INK_F,
}

const renameErrStyle: React.CSSProperties = {
  marginTop: 2,
  fontSize: 10,
  lineHeight: 1.3,
  // 与 DetailPane.tsx 的「删除失败」同色 —— 这一页的报错文案统一用它。
  // 不用 HUNTER.UP:那个是"涨"的红,语义完全是另一回事
  color: '#9B3A22',
}

const separatorStyle: React.CSSProperties = {
  margin: '16px 12px',
  borderTop: `1px solid ${HUNTER.LINE}`,
}

const mgmtBtnStyle = (disabled: boolean): React.CSSProperties => ({
  width: '100%',
  padding: '6px 12px',
  marginBottom: 6,
  fontSize: 12,
  color: disabled ? HUNTER.SOFT : HUNTER.INK_S,
  background: 'transparent',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: HUNTER.R_SM,
  cursor: disabled ? 'not-allowed' : 'pointer',
  fontFamily: 'inherit',
  textAlign: 'left',
})
