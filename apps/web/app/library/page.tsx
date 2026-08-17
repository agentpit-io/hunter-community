'use client'
// 能力库主页 · /library
// 见方案 §2-3: /doc/开源hunter-community/参考/10-前端优化/capability-library-page-plan.md
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { HUNTER } from '../lib/hunter-theme'
import AuthGuard from '../components/AuthGuard'
import {
  listSources, listToolbox, listCatalogSkills,
  type SourceGroup, type ToolGroup, type SkillGroup, type Summary, type SourcesResponse,
  type DataSourceItem, type ToolItem, type CatalogSkillItem,
} from '../chat/lib/catalogClient'
import { listUserSources, bulkEnableUserSources } from './lib/userSources'
import { parseQuery, buildQuery, TABS } from './lib/nav'
import CategoryNav from './components/CategoryNav'
import SearchBar from './components/SearchBar'
import OverviewPage from './components/OverviewPage'
import SourcesTab from './components/SourcesTab'
import ToolsTab from './components/ToolsTab'
import SkillsTab from './components/SkillsTab'
import DetailPane from './components/DetailPane'
import AddPanel from './components/AddPanel'

type SelectedItem =
  | { kind: 'source'; item: DataSourceItem }
  | { kind: 'tool'; item: ToolItem }
  | { kind: 'skill'; item: CatalogSkillItem }
  | null

// Next.js 15 · useSearchParams 必须包在 Suspense 里 · 否则 build 会 prerender error
export default function LibraryPage() {
  return (
    <Suspense fallback={<div style={wrapStyle}><div style={{ padding: 40, color: HUNTER.INK_F }}>加载中...</div></div>}>
      <LibraryContent />
    </Suspense>
  )
}

function LibraryContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const query = useMemo(() => parseQuery(searchParams), [searchParams])

  const [sources, setSources] = useState<SourcesResponse | null>(null)
  // 市场筛选条(`_21` §2)—— 原来的主分类降级成它。
  // 不放进 URL query 是有意的:它是**视图内的临时过滤**,
  // 不像 tab/group 那样值得被收藏或分享
  const [market, setMarket] = useState('')
  const [toolbox, setToolbox] = useState<{ groups: ToolGroup[]; summary: Summary } | null>(null)
  const [skills, setSkills] = useState<{ groups: SkillGroup[]; summary: Summary } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [selected, setSelected] = useState<SelectedItem>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  // 从某一组的 ＋ 点进来时,表单预选好那个来源(`_21` §3)——
  // 用户看着"东方财富"那一组点加号,意图已经明确,不该再让他选一遍
  const [addPreset, setAddPreset] = useState<string | undefined>()
  // 加完之后的结果 —— **必须显示**:装 SKILL 时 opencode 可能没重扫,
  // 那时文件写好了但模型还看不到,只说"已保存"是骗人
  const [notice, setNotice] = useState('')
  // 二次确认 —— 「一键用官方」会一次性影响用户配的全部源,
  // 而且他多半是在排查问题的当口点的,得先说清楚会发生什么、以及能不能撤
  const [confirm, setConfirm] = useState<{ text: string; action: 'disable' | 'enable' } | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([
      listSources(market).catch((e) => { console.warn('sources', e); return null }),
      listToolbox().catch((e) => { console.warn('toolbox', e); return null }),
      listCatalogSkills().catch((e) => { console.warn('skills', e); return null }),
    ]).then(([s, t, k]) => {
      if (!alive) return
      setSources(s); setToolbox(t); setSkills(k)
      if (!s && !t && !k) setError('能力目录接口全部失败 · 检查后端 /api/catalog/* 是否正常')
    })
    return () => { alive = false }
    // market 变了要重拉 —— 筛选在**后端**做,因为后端会重算每组计数。
    // 在前端过滤的话侧栏会显示 "AKShare 7/7" 但点进去只有 5 条
  }, [market])

  // 切 tab / group 时清空选中
  useEffect(() => {
    setSelected(null)
    setDetailOpen(false)
    // 面板内容按 tab 决定,切了 tab 不收起会出现"在数据源页显示接工具表单"
    setAddOpen(false)
  }, [query.tab, query.group])

  /** 重新拉一次目录 —— 加完东西后刷新计数与列表 */
  const reload = useCallback(() => {
    listSources(market).then(setSources).catch(() => {})
    listToolbox().then(setToolbox).catch(() => {})
    listCatalogSkills().then(setSkills).catch(() => {})
  }, [market])

  /** 「一键用官方默认」/「恢复初始」· 按 tab 分流。
   *
   *  数据源那条走 bulk-enable(**停用不删除**);工具与 SKILL 还没做,
   *  照实说而不是给一个点了没反应的按钮。 */
  const onReset = useCallback(async () => {
    if (query.tab !== 'sources') {
      setNotice('「恢复初始」在工具箱/SKILL 这两类还没做 —— 它要删掉你加的全部条目,'
                + '删之前得先能列出"哪些是你加的",那部分正在做')
      return
    }
    try {
      const cur = await listUserSources()
      if (!cur.platform_key) {
        setNotice('还没配平台 key —— 「用官方源」需要一把 hunt_tools_ key。'
                  + '到设置页填上就能一次解锁全部官方数据源;'
                  + '免费额度在 hunter.agentpit.io/dev/api-keys 申请')
        return
      }
      if (cur.enabled_count === 0) {
        // 已经全走官方了 —— 这时该提供的是**反向操作**,而不是重复一次同样的事
        setConfirm({
          text: `你自己的源已经全部停用,现在走的就是官方源(共 ${cur.sources.length} 条已停用)。`
                + `要把它们重新启用吗?`,
          action: 'enable',
        })
        return
      }
      setConfirm({
        text: `将停用你自己接的 ${cur.enabled_count} 个数据源,全部改走官方源。`
              + `**不会删除** —— 地址和 key 都留着,随时可以一键切回来。`,
        action: 'disable',
      })
    } catch (e: any) {
      setNotice(`读不到你的数据源:${e.message}`)
    }
  }, [query.tab])

  const doBulk = useCallback(async (enabled: boolean) => {
    setConfirm(null)
    try {
      const r = await bulkEnableUserSources(enabled)
      setNotice(enabled
        ? `已重新启用 ${r.changed} 个你自己的数据源 —— 取数会重新优先走它们。`
        : `已停用 ${r.changed} 个,现在全部走官方源。它们没被删除,再点一次按钮就能切回来。`)
      reload()
    } catch (e: any) {
      setNotice(`操作失败:${e.message}`)
    }
  }, [reload])

  const onSearch = useCallback((v: string) => {
    router.replace(buildQuery({ ...query, search: v }), { scroll: false })
  }, [router, query])

  const onSelectSource = useCallback((item: DataSourceItem) => {
    setSelected({ kind: 'source', item }); setDetailOpen(true)
  }, [])
  const onSelectTool = useCallback((item: ToolItem) => {
    setSelected({ kind: 'tool', item }); setDetailOpen(true)
  }, [])
  const onSelectSkill = useCallback((item: CatalogSkillItem) => {
    setSelected({ kind: 'skill', item }); setDetailOpen(true)
  }, [])

  // 把 SKILL 提问模板送回 chat 输入框 · 走 /chat?q= autoText 通道
  // (与 SkillDetailPage 详情页"在 Hunter chat 里使用"按钮同一链路)
  const onPickSkillToChat = useCallback((item: CatalogSkillItem) => {
    window.location.href = `/chat?q=${encodeURIComponent(item.prompt_tpl)}`
  }, [])

  return (
    <div style={wrapStyle}>
      <AuthGuard />

      {/* 顶栏 */}
      <header style={topBarStyle}>
        <Link href="/chat" style={backLinkStyle} title="返回聊天">← 返回</Link>
        <span style={{ fontSize: 14, fontWeight: 600, color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>
          能力库
        </span>
        <div style={{ flex: 1 }} />
        <SearchBar value={query.search || ''} onChange={onSearch} />
      </header>

      {/* 主体 3 栏 */}
      <div style={bodyStyle}>
        <CategoryNav
          query={query}
          sources={sources?.groups || null}
          tools={toolbox?.groups || null}
          skills={skills?.groups || null}
          onAdd={(preset) => { setAddPreset(preset); setAddOpen(true); setDetailOpen(false) }}
          onReset={onReset}
        />

        <main style={mainStyle}>
          {error && <div style={errorBoxStyle}>{error}</div>}

          {notice && (
            <div style={noticeBoxStyle} onClick={() => setNotice('')} title="点击关闭">
              {notice}
            </div>
          )}

          {confirm && (
            <div style={confirmBoxStyle}>
              <div style={{ marginBottom: 10, lineHeight: 1.8 }}>{confirm.text}</div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => setConfirm(null)} style={confirmGhost}>取消</button>
                <button onClick={() => doBulk(confirm.action === 'enable')} style={confirmPrimary}>
                  {confirm.action === 'enable' ? '重新启用' : '停用并改走官方'}
                </button>
              </div>
            </div>
          )}

          {addOpen && query.tab !== 'overview' && (
            <AddPanel
              tab={query.tab}
              presetGroup={addPreset}
              categories={skills?.groups.map((g) => g.category) || []}
              onClose={() => { setAddOpen(false); setAddPreset(undefined) }}
              onDone={(msg) => { setAddOpen(false); setAddPreset(undefined); setNotice(msg); reload() }}
            />
          )}

          {query.tab === 'overview' && (
            <OverviewPage sources={sources} tools={toolbox} skills={skills} />
          )}

          {query.tab === 'sources' && sources && (
            <SourcesTab
              groups={sources.groups}
              markets={sources.markets || []}
              activeGroup={query.group}
              activeMarket={market}
              onMarketChange={setMarket}
              search={query.search}
              selected={selected?.kind === 'source' ? selected.item : null}
              onSelect={onSelectSource}
              onAdd={(preset) => { setAddPreset(preset); setAddOpen(true); setDetailOpen(false) }}
            />
          )}
          {query.tab === 'sources' && !sources && <Loading />}

          {query.tab === 'tools' && toolbox && (
            <ToolsTab
              groups={toolbox.groups}
              activeGroup={query.group}
              search={query.search}
              selected={selected?.kind === 'tool' ? selected.item : null}
              onSelect={onSelectTool}
            />
          )}
          {query.tab === 'tools' && !toolbox && <Loading />}

          {query.tab === 'skills' && skills && (
            <SkillsTab
              groups={skills.groups}
              activeGroup={query.group}
              search={query.search}
              selected={selected?.kind === 'skill' ? selected.item : null}
              onSelect={onSelectSkill}
              onPickToChat={onPickSkillToChat}
            />
          )}
          {query.tab === 'skills' && !skills && <Loading />}
        </main>

        {detailOpen && selected && (
          <DetailPane
            source={selected.kind === 'source' ? selected.item : undefined}
            tool={selected.kind === 'tool' ? selected.item : undefined}
            skill={selected.kind === 'skill' ? selected.item : undefined}
            onClose={() => { setDetailOpen(false); setSelected(null) }}
            onPickSkillToChat={onPickSkillToChat}
          />
        )}
      </div>
    </div>
  )
}

function Loading() {
  return <div style={{ padding: 40, textAlign: 'center', color: HUNTER.INK_F, fontSize: 13 }}>加载中...</div>
}

const wrapStyle: React.CSSProperties = {
  height: '100vh',
  display: 'flex',
  flexDirection: 'column',
  background: HUNTER.BG,
  fontFamily: HUNTER.SANS,
}

const topBarStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 16,
  padding: '10px 20px',
  height: 56,
  background: '#fff',
  borderBottom: `1px solid ${HUNTER.LINE}`,
  flexShrink: 0,
}

const backLinkStyle: React.CSSProperties = {
  fontSize: 13,
  color: HUNTER.INK_S,
  textDecoration: 'none',
  padding: '4px 8px',
  borderRadius: HUNTER.R_SM,
  transition: 'background 0.1s',
}

const bodyStyle: React.CSSProperties = {
  flex: 1,
  display: 'flex',
  overflow: 'hidden',
}

const mainStyle: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  background: HUNTER.BG,
}

const noticeBoxStyle: React.CSSProperties = {
  margin: '0 0 14px', padding: '10px 13px', borderRadius: 9, cursor: 'pointer',
  background: HUNTER.TAG_OK_BG, color: HUNTER.TAG_OK_FG,
  fontSize: 12.5, lineHeight: 1.7,
}

// 确认框用**警示色**而不是通知色 —— 它要用户做决定,不是通知他事已办完
const confirmBoxStyle: React.CSSProperties = {
  margin: '0 0 14px', padding: '12px 14px', borderRadius: 9,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
  fontSize: 12.5,
}
const confirmGhost: React.CSSProperties = {
  padding: '6px 16px', borderRadius: 7, fontSize: 12, cursor: 'pointer',
  background: 'transparent', color: 'inherit',
  border: '1px solid currentColor', opacity: 0.7, fontFamily: 'inherit',
}
const confirmPrimary: React.CSSProperties = {
  padding: '6px 16px', borderRadius: 7, fontSize: 12, fontWeight: 600,
  cursor: 'pointer', background: HUNTER.THEME, color: '#fff',
  border: 'none', fontFamily: 'inherit',
}

const errorBoxStyle: React.CSSProperties = {
  margin: '20px 24px',
  padding: 12,
  background: HUNTER.TAG_WARN_BG,
  color: HUNTER.TAG_WARN_FG,
  borderRadius: HUNTER.R_MD,
  fontSize: 12,
}
