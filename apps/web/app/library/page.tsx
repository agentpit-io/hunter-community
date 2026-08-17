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
  type SourceGroup, type ToolGroup, type SkillGroup, type Summary,
  type DataSourceItem, type ToolItem, type CatalogSkillItem,
} from '../chat/lib/catalogClient'
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

  const [sources, setSources] = useState<{ groups: SourceGroup[]; summary: Summary } | null>(null)
  const [toolbox, setToolbox] = useState<{ groups: ToolGroup[]; summary: Summary } | null>(null)
  const [skills, setSkills] = useState<{ groups: SkillGroup[]; summary: Summary } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [selected, setSelected] = useState<SelectedItem>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  // 加完之后的结果 —— **必须显示**:装 SKILL 时 opencode 可能没重扫,
  // 那时文件写好了但模型还看不到,只说"已保存"是骗人
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let alive = true
    Promise.all([
      listSources().catch((e) => { console.warn('sources', e); return null }),
      listToolbox().catch((e) => { console.warn('toolbox', e); return null }),
      listCatalogSkills().catch((e) => { console.warn('skills', e); return null }),
    ]).then(([s, t, k]) => {
      if (!alive) return
      setSources(s); setToolbox(t); setSkills(k)
      if (!s && !t && !k) setError('能力目录接口全部失败 · 检查后端 /api/catalog/* 是否正常')
    })
    return () => { alive = false }
  }, [])

  // 切 tab / group 时清空选中
  useEffect(() => {
    setSelected(null)
    setDetailOpen(false)
    // 面板内容按 tab 决定,切了 tab 不收起会出现"在数据源页显示接工具表单"
    setAddOpen(false)
  }, [query.tab, query.group])

  /** 重新拉一次目录 —— 加完东西后刷新计数与列表 */
  const reload = useCallback(() => {
    listSources().then(setSources).catch(() => {})
    listToolbox().then(setToolbox).catch(() => {})
    listCatalogSkills().then(setSkills).catch(() => {})
  }, [])

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
          onAdd={() => { setAddOpen(true); setDetailOpen(false) }}
          onReset={() => setNotice('「恢复初始」还没做 —— 它要删掉你在这一类里加的全部条目,'
                                   + '删之前得先能列出"哪些是你加的",那部分正在做')}
        />

        <main style={mainStyle}>
          {error && <div style={errorBoxStyle}>{error}</div>}

          {notice && (
            <div style={noticeBoxStyle} onClick={() => setNotice('')} title="点击关闭">
              {notice}
            </div>
          )}

          {addOpen && query.tab !== 'overview' && (
            <AddPanel
              tab={query.tab}
              categories={skills?.groups.map((g) => g.category) || []}
              onClose={() => setAddOpen(false)}
              onDone={(msg) => { setAddOpen(false); setNotice(msg); reload() }}
            />
          )}

          {query.tab === 'overview' && (
            <OverviewPage sources={sources} tools={toolbox} skills={skills} />
          )}

          {query.tab === 'sources' && sources && (
            <SourcesTab
              groups={sources.groups}
              activeGroup={query.group}
              search={query.search}
              selected={selected?.kind === 'source' ? selected.item : null}
              onSelect={onSelectSource}
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

const errorBoxStyle: React.CSSProperties = {
  margin: '20px 24px',
  padding: 12,
  background: HUNTER.TAG_WARN_BG,
  color: HUNTER.TAG_WARN_FG,
  borderRadius: HUNTER.R_MD,
  fontSize: 12,
}
