'use client'
import { useEffect, useState, useCallback, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Menu, PanelLeftOpen } from 'lucide-react'
import { HUNTER } from '../lib/hunter-theme'
import TopNav from '../components/TopNav'
import ChatSidebar from './components/ChatSidebar'
import ChatWorkspace from './components/ChatWorkspace'
import ArtifactPanel, { type ReportContent } from './components/ArtifactPanel'
import DebateDepthPicker from './components/DebateDepthPicker'
import type { MessagePartTool } from './lib/types'
import type { DebateDepth } from './lib/debateClient'

function ChatPageInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [ready, setReady] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [artifactPart, setArtifactPart] = useState<MessagePartTool | null>(null)
  const [reportContent, setReportContent] = useState<ReportContent | null>(null)
  const [sidebarKey, setSidebarKey] = useState(0)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [isMobile, setIsMobile] = useState(false)
  /** 打开报告时自动收 Sidebar · 用户可点悬浮 ☰ 手动展开 */
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  /** 记 session title · 用于报告标题和下载文件名 */
  const [sessionTitle, setSessionTitle] = useState<string>('新对话')

  const [autoText, setAutoText] = useState<string | undefined>(undefined)
  const [autoSend, setAutoSend] = useState(false)
  const [draft, setDraft] = useState<{ text: string; seq: number } | undefined>(undefined)
  /** 记录用户上一次点的 SKILL key · 供 ChatWorkspace 判断"下条 send 要走特殊 handler"
   *  只有 debate 需要特殊处理 · 其他 SKILL 走普通对话 */
  const [pendingSkillKey, setPendingSkillKey] = useState<string | null>(null)
  // 默认 quick(1 轮) · 45s 出结果 · 用户在 DepthPicker 里可主动升到 normal/deep。
  // 老的 normal(2 轮) ~90s + reasoning 型模型常态 40-60s/轮,一次要等 2-3 分钟太糟。
  const [debateDepth, setDebateDepth] = useState<DebateDepth>('quick')
  const [depthPickerOpen, setDepthPickerOpen] = useState(false)
  /** 深度选择完后暂存 SKILL 模板 · 等待 depth 确认后再灌 draft */
  const [pendingDebateTpl, setPendingDebateTpl] = useState<string | null>(null)

  useEffect(() => {
    const token = localStorage.getItem('hunter_token')
    if (!token) {
      router.push('/login')
      return
    }
    setReady(true)

    const q = searchParams.get('q')
    if (q) {
      setAutoText(q)
      setAutoSend(true)
      router.replace('/chat', { scroll: false })
    }
  }, [router, searchParams])

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    setIsMobile(mq.matches)
    const onChange = () => setIsMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const handleSelectSession = useCallback((id: string) => {
    setSessionId(id)
    setArtifactPart(null)
    setReportContent(null)
    setSidebarCollapsed(false)  // 切 session · Sidebar 恢复展开
    setMobileMenuOpen(false)
  }, [])

  const handleNewSession = useCallback((id: string) => {
    setSessionId(id)
    setArtifactPart(null)
    setReportContent(null)
    setSidebarCollapsed(false)
    setSidebarKey((k) => k + 1)
    setMobileMenuOpen(false)
  }, [])

  const handlePickSkill = useCallback((tpl: string, key?: string) => {
    // 侧栏 CapabilityPanel "最近用 top 5" 需要 usage 统计 · 所有 skill 入口统一 track
    if (key) {
      // 动态 import 避免 SSR 报 window undefined
      import('./lib/skillUsage').then(({ trackSkillUsage }) => trackSkillUsage(key)).catch(() => {})
    }
    // 多专家辩论 · 先弹深度选择器 · 用户选完才把模板灌进输入框
    if (key === 'debate') {
      setPendingDebateTpl(tpl)
      setDepthPickerOpen(true)
      setMobileMenuOpen(false)
      return
    }
    setDraft((d) => ({ text: tpl, seq: (d?.seq ?? 0) + 1 }))
    setPendingSkillKey(key || null)
    setMobileMenuOpen(false)
  }, [])

  const handleDepthConfirm = useCallback((depth: DebateDepth) => {
    setDebateDepth(depth)
    setDepthPickerOpen(false)
    if (pendingDebateTpl) {
      setDraft((d) => ({ text: pendingDebateTpl, seq: (d?.seq ?? 0) + 1 }))
      setPendingSkillKey('debate')
      setPendingDebateTpl(null)
    }
  }, [pendingDebateTpl])

  /** 追问建议点击 · 走普通 send · 不设 pendingSkillKey */
  const handlePickSuggestion = useCallback((text: string) => {
    setDraft((d) => ({ text, seq: (d?.seq ?? 0) + 1 }))
    setPendingSkillKey(null)
    setMobileMenuOpen(false)
  }, [])

  const handleSessionUpdated = useCallback(() => {
    setSidebarKey((k) => k + 1)
  }, [])

  const handleSessionDeleted = useCallback(() => {
    setSessionId(null)
    setSidebarKey((k) => k + 1)
    setReportContent(null)
    setArtifactPart(null)
    setSidebarCollapsed(false)
  }, [])

  const handleOpenArtifact = useCallback((part: MessagePartTool) => {
    setArtifactPart(part)
    setReportContent(null)  // 互斥 · 不能同时打开
  }, [])

  const handleCloseArtifact = useCallback(() => {
    setArtifactPart(null)
    setReportContent(null)
    setSidebarCollapsed(false)  // 关闭 · Sidebar 恢复
  }, [])

  const handleOpenReport = useCallback((
    text: string,
    sourceMessageId: string,
    artifactType?: 'markdown' | 'html',
    overrideTitle?: string,
  ) => {
    setReportContent({
      // 若 html · text 存 htmlContent(简单复用) · 兼容 debate 的 markdown 走原字段
      text: artifactType === 'html' ? '' : text,
      htmlContent: artifactType === 'html' ? text : undefined,
      artifactType: artifactType || 'markdown',
      sessionId: sessionId || '',
      sessionTitle: overrideTitle || sessionTitle,
      sourceMessageId,  // ⚠ 必须传 · ArtifactPanel 用它做发布态查询和 Publish 授权
    })
    setArtifactPart(null)
    setSidebarCollapsed(true)  // 打开报告 · 自动收 Sidebar · 让主区最大化
  }, [sessionId, sessionTitle])

  if (!ready) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          color: HUNTER.INK_F,
          background: '#ffffff',
        }}
      >
        加载中...
      </div>
    )
  }

  const showSidebar = !isMobile || mobileMenuOpen
  const isArtifactOpen = !!(artifactPart || reportContent)

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        background: '#ffffff',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* Claude 风顶栏 · 主入口 chat + 5 一级菜单 · target=_blank */}
      <TopNav active="chat" />

      {/* 下半区 · ChatSidebar + ChatWorkspace + Artifact 三栏 · 原布局不动 */}
      <div style={{
        display: 'flex',
        flex: 1,
        minHeight: 0,   // 关键 · 允许子级 overflow · 否则整页可能撑高
        position: 'relative',
      }}>
      {isMobile && mobileMenuOpen && (
        <div
          onClick={() => setMobileMenuOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.35)',
            zIndex: 40,
          }}
        />
      )}

      {/* Sidebar · desktop 常驻 · mobile 抽屉 · 打开报告时自动收 (width 0)
       *  height 用 100% (parent is flex-1 · 已扣顶栏高度) · 避免超出屏幕 */}
      {showSidebar && (
        <div
          style={{
            position: isMobile ? 'fixed' : 'relative',
            top: isMobile ? 48 : 0,              // mobile 抽屉从顶栏下开始
            left: 0,
            height: isMobile ? 'calc(100vh - 48px)' : '100%',
            width: sidebarCollapsed && !isMobile ? 0 : 'auto',
            overflow: sidebarCollapsed && !isMobile ? 'hidden' : 'visible',
            transition: 'width 0.28s cubic-bezier(0.4, 0, 0.2, 1)',
            zIndex: 50,
            boxShadow: isMobile ? '4px 0 24px rgba(0,0,0,0.15)' : 'none',
            flexShrink: 0,
          }}
        >
          <ChatSidebar
            key={sidebarKey}
            currentSessionId={sessionId}
            onSelectSession={handleSelectSession}
            onNewSession={handleNewSession}
            onPickSkill={handlePickSkill}
            onCollapse={isMobile ? () => setMobileMenuOpen(false) : () => setSidebarCollapsed(true)}
          />
        </div>
      )}

      {/* Mobile 汉堡按钮 */}
      {isMobile && !mobileMenuOpen && (
        <button
          type="button"
          onClick={() => setMobileMenuOpen(true)}
          style={{
            position: 'absolute',
            top: 10,
            left: 12,
            zIndex: 30,
            width: 34,
            height: 34,
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            color: HUNTER.INK,
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          }}
          aria-label="打开菜单"
        >
          <Menu size={16} />
        </button>
      )}

      {/* Desktop · Sidebar 已收起 · 悬浮"展开"按钮 */}
      {!isMobile && sidebarCollapsed && (
        <button
          type="button"
          onClick={() => setSidebarCollapsed(false)}
          title="展开侧栏"
          style={{
            position: 'absolute',
            top: 12,
            left: 12,
            zIndex: 30,
            width: 36,
            height: 36,
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            color: HUNTER.INK_S,
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
          }}
        >
          <PanelLeftOpen size={16} />
        </button>
      )}

      <ChatWorkspace
        sessionId={sessionId}
        onSessionCreated={setSessionId}
        onSessionUpdated={handleSessionUpdated}
        onSessionDeleted={handleSessionDeleted}
        onOpenArtifact={handleOpenArtifact}
        onOpenReport={handleOpenReport}
        autoText={autoText}
        autoSend={autoSend}
        draft={draft}
        onPickSuggestion={handlePickSuggestion}
        onPickSkill={handlePickSkill}
        pendingSkillKey={pendingSkillKey}
        onSkillConsumed={() => setPendingSkillKey(null)}
        debateDepth={debateDepth}
      />

      {/* ArtifactPanel · 移动端不显示 (空间小) · 桌面端展开时占空间 */}
      {isArtifactOpen && !isMobile && (
        <ArtifactPanel
          part={artifactPart}
          report={reportContent}
          onClose={handleCloseArtifact}
        />
      )}
      </div>{/* end · flex row · sidebar+workspace+artifact */}

      {/* 多专家辩论 · 深度选择器 · Sprint B4 · 全屏 modal · 放在 row 外 */}
      <DebateDepthPicker
        open={depthPickerOpen}
        onClose={() => { setDepthPickerOpen(false); setPendingDebateTpl(null) }}
        onConfirm={handleDepthConfirm}
      />
    </div>
  )
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100vh',
            color: HUNTER.INK_F,
            background: '#ffffff',
          }}
        >
          加载中...
        </div>
      }
    >
      <ChatPageInner />
    </Suspense>
  )
}
