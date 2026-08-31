'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Plus, MessageSquare, LogOut, ChevronDown, PanelLeftClose, Unlock, ShieldCheck } from 'lucide-react'
import { HUNTER, HUNTER_LOGO } from '../../lib/hunter-theme'
import type { Session } from '../lib/types'
import { listSessions, createSession } from '../lib/opencodeClient'
import CapabilityPanel from './CapabilityPanel'
import SkillManager from './SkillManager'
import ProfileEditor from './ProfileEditor'
import { getProfile } from '../lib/profileClient'
import { getUnlockStatus, onUnlockChange, peekUnlockStatus } from '../lib/unlockClient'
import { isSingleUser } from '../../lib/localSession'
import UnlockModal from './UnlockModal'

interface Props {
  currentSessionId: string | null
  onSelectSession: (id: string) => void
  onNewSession: (id: string) => void
  /** 点能力卡 → 把提问模板填进输入框 */
  onPickSkill?: (tpl: string, key: string) => void
  /** 折叠侧栏 · 由父级 sidebarCollapsed state 控制 */
  onCollapse?: () => void
  /** 侧栏标签切换 → 通知父级换主区内容(对话 / 自选股)。
   *  自选股列表放右边主区,不放侧栏 —— 侧栏 280px 塞不下卡片,
   *  而且切到自选股时对话界面本来就该让位 */
  onTabChange?: (tab: 'chat' | 'watchlist') => void
}

// Claude 风米黄 · 温暖但克制
const SB_BG = '#faf9f4'
const SB_HOVER = '#f0ede2'
const SB_ACTIVE = '#e8e2d1'

// Tab 切换样式(方案 A · 2026-08-17)
const tabBtn = (active: boolean): React.CSSProperties => ({
  flex: 1,
  padding: '10px 6px',
  fontSize: 12,
  fontWeight: active ? 600 : 500,
  color: active ? HUNTER.THEME : HUNTER.INK_S,
  background: 'transparent',
  border: 'none',
  borderBottom: active ? `2px solid ${HUNTER.THEME}` : '2px solid transparent',
  cursor: 'pointer',
  fontFamily: 'inherit',
  transition: 'color 0.1s, border-color 0.1s',
  marginBottom: -1,
})

function fmtTitle(t: string): string {
  if (!t) return '新对话'
  return t.length > 26 ? t.slice(0, 26) + '…' : t
}

function timeAgo(ts?: number): string {
  if (!ts) return ''
  const diff = Date.now() - ts
  const min = Math.floor(diff / 60_000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min}分`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d`
  return new Date(ts).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function groupSessions(sessions: Session[]) {
  const now = Date.now()
  const today: Session[] = []
  const yesterday: Session[] = []
  const week: Session[] = []
  const older: Session[] = []

  sessions.forEach((s) => {
    const t = s.time?.updated || s.time?.created || 0
    const ageDays = (now - t) / 86400_000
    if (ageDays < 1) today.push(s)
    else if (ageDays < 2) yesterday.push(s)
    else if (ageDays < 7) week.push(s)
    else older.push(s)
  })

  return { today, yesterday, week, older }
}

export default function ChatSidebar({ currentSessionId, onSelectSession, onNewSession, onPickSkill, onCollapse, onTabChange }: Props) {
  const router = useRouter()
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [showSkillMgr, setShowSkillMgr] = useState(false)
  // Tab 切换 · 一次只显对话或能力 · localStorage 持久 · 默认对话(最常用)
  // (2026-08-17 方案 A · doc: sidebar-history-space-plan.md)
  const [activeTab, setActiveTab] = useState<'chat' | 'watchlist'>(() => {
    if (typeof window === 'undefined') return 'chat'
    return (localStorage.getItem('hunter_sidebar_tab') as any) === 'watchlist' ? 'watchlist' : 'chat'
  })
  const switchTab = (t: 'chat' | 'watchlist') => {
    onTabChange?.(t)
    setActiveTab(t)
    try { localStorage.setItem('hunter_sidebar_tab', t) } catch { /* ignore */ }

  // 首次挂载把当前标签同步给父级 —— localStorage 记着上次是"自选股"的话,
  // 刷新后主区也该直接显自选股,而不是显对话再等用户点一下
  useEffect(() => { onTabChange?.(activeTab) }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  }
  const [skillRefresh, setSkillRefresh] = useState(0)
  const [showProfile, setShowProfile] = useState(false)
  const [wizard, setWizard] = useState(false)
  const [userInitial, setUserInitial] = useState('U')
  const [userEmail, setUserEmail] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [showUnlock, setShowUnlock] = useState(false)
  const [unlocked, setUnlocked] = useState(peekUnlockStatus()?.unlocked ?? false)

  // 解锁状态 · 与 SkillPanel 共用同一份缓存,弹窗里存完 key 两边同时变
  useEffect(() => {
    void getUnlockStatus().then((st) => setUnlocked(st.unlocked))
    return onUnlockChange((st) => setUnlocked(st.unlocked))
  }, [])

  const load = async () => {
    setLoading(true)
    try {
      const list = await listSessions()
      // 按 updated 排序 · 新 → 旧
      list.sort((a, b) => (b.time?.updated || b.time?.created || 0) - (a.time?.updated || a.time?.created || 0))
      setSessions(list)
    } catch (e) {
      console.error('[sidebar] listSessions:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // 账号信息直接从 JWT 解 —— 登录只写 hunter_token(见 app/login·app/sso),
    // 从来没有写过 hunter_email,读它永远是空,底部会一直显示占位的"用户"。
    let email = ''
    let admin = false
    try {
      const token = localStorage.getItem('hunter_token') || ''
      if (token) {
        const payload = JSON.parse(atob(token.split('.')[1]))
        email = payload.email || ''
        // 与主侧栏 (app/components/Sidebar.tsx) 及后端 _require_admin 同一口径
        admin = payload.role === 'ADMIN' || payload.email === 'hangeaiagent@gmail.com'
      }
    } catch {
      /* token 损坏时退回未登录展示 */
    }
    setUserEmail(email)
    setUserInitial(email ? email[0].toUpperCase() : 'U')
    setIsAdmin(admin)
    load()
    // 没走过引导的用户弹一次 3 步向导(可跳过, 跳过也算走过)
    if (email) {
      getProfile()
        .then((d) => {
          if (!d.profile?.onboarded) { setWizard(true); setShowProfile(true) }
        })
        .catch(() => { /* 画像服务不可用时不打扰用户 */ })
    }
    // 60s 刷一次
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  const handleNew = async () => {
    if (creating) return
    setCreating(true)
    try {
      const s = await createSession('新对话')
      await load()
      onNewSession(s.id)
    } catch (e: any) {
      console.error('[sidebar] createSession:', e)
      alert(`新建对话失败: ${e?.message || e}`)
    } finally {
      setCreating(false)
    }
  }

  const handleLogout = () => {
    localStorage.removeItem('hunter_token')
    localStorage.removeItem('hunter_email')
    router.push('/login')
  }

  const groups = groupSessions(sessions)

  const renderGroup = (label: string, list: Session[]) => {
    if (list.length === 0) return null
    return (
      <div style={{ marginBottom: 12 }}>
        <div
          style={{
            padding: '6px 12px 4px',
            fontSize: 10.5,
            fontWeight: 600,
            color: HUNTER.INK_F,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
          }}
        >
          {label}
        </div>
        {list.map((s) => {
          const isActive = s.id === currentSessionId
          return (
            <button
              key={s.id}
              onClick={() => onSelectSession(s.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                width: '100%',
                padding: '7px 10px',
                margin: '1px 4px',
                background: isActive ? SB_ACTIVE : 'transparent',
                border: 'none',
                borderRadius: 6,
                color: isActive ? HUNTER.INK : HUNTER.INK_S,
                fontSize: 13,
                textAlign: 'left',
                cursor: 'pointer',
                transition: 'background 0.1s',
                fontFamily: 'inherit',
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.background = SB_HOVER
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.background = 'transparent'
              }}
            >
              <MessageSquare size={13} style={{ flexShrink: 0, color: HUNTER.INK_F }} />
              <span
                style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {fmtTitle(s.title)}
              </span>
            </button>
          )
        })}
      </div>
    )
  }

  return (
    <aside
      style={{
        width: 260,
        flexShrink: 0,
        background: SB_BG,
        borderRight: `1px solid ${HUNTER.LINE}`,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',   /* parent (page.tsx wrapper) 已 flex-1 或 mobile calc(100vh-48) */
        overflow: 'hidden',
        fontFamily: HUNTER.SANS,
      }}
    >
      {/* 顶部 · 品牌 + 返回 + 收起 */}
      <div style={{ padding: '14px 14px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <Link
          href="/"
          title="返回主页"
          style={{
            display: 'flex',
            alignItems: 'center',
            padding: '4px 4px',
            borderRadius: 6,
            color: HUNTER.INK_F,
            textDecoration: 'none',
            transition: 'background 0.1s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = SB_HOVER)}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
        >
          <ArrowLeft size={12} />
        </Link>
        {/* 品牌圆形 mark · 用预裁圆的 deer-round · width/height 双约束避免 Tailwind height:auto 撑大 */}
        <img
          src={HUNTER_LOGO}
          alt="猎鹿人 Logo"
          width={36}
          height={36}
          style={{
            width: 36,
            height: 36,
            minWidth: 36,
            minHeight: 36,
            borderRadius: '50%',
            objectFit: 'cover',
            flexShrink: 0,
            boxShadow: '0 0 0 1px rgba(181,107,45,.35)',
          }}
        />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <strong
            style={{
              fontFamily: HUNTER.SERIF,
              fontSize: 17,
              letterSpacing: '.03em',
              color: HUNTER.INK,
              fontWeight: 500,
            }}
          >
            猎鹿人
          </strong>
          <span style={{ fontFamily: HUNTER.SERIF, fontSize: 13, color: HUNTER.COPPER3 }}>· Hunter</span>
        </div>
        {onCollapse && (
          <button
            type="button"
            onClick={onCollapse}
            title="收起侧栏"
            aria-label="收起侧栏"
            style={{
              width: 30,
              height: 30,
              border: `1px solid transparent`,
              background: 'transparent',
              borderRadius: 8,
              display: 'grid',
              placeItems: 'center',
              color: HUNTER.INK_F,
              cursor: 'pointer',
              flexShrink: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = SB_HOVER
              e.currentTarget.style.borderColor = HUNTER.LINE
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.borderColor = 'transparent'
            }}
          >
            <PanelLeftClose size={14} />
          </button>
        )}
      </div>

      {/* 新对话按钮 · 44px + 铜色阴影 */}
      <div style={{ padding: '0 12px 12px' }}>
        <button
          onClick={handleNew}
          disabled={creating}
          style={{
            width: '100%',
            height: 44,
            background: HUNTER.THEME,
            color: '#fff',
            border: 'none',
            borderRadius: 12,
            fontSize: 14,
            fontWeight: 650,
            letterSpacing: '.02em',
            cursor: creating ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            justifyContent: 'center',
            transition: 'background 0.1s',
            fontFamily: 'inherit',
            boxShadow: HUNTER.SHADOW_BRAND,
          }}
          onMouseEnter={(e) => {
            if (!creating) e.currentTarget.style.background = HUNTER.COPPER3
          }}
          onMouseLeave={(e) => {
            if (!creating) e.currentTarget.style.background = HUNTER.THEME
          }}
        >
          <Plus size={16} /> {creating ? '新建中...' : '新建对话'}
        </button>
      </div>

      {/* Tab 切换栏 · **对话 / 自选股 两选一**(2026-08-30 导航重构)
       *
       * 原来是「对话 / 能力 / 策略↗」三个。改动理由:
       *   · 「策略↗」和顶栏「策略中心」是同一个页面 —— 两个按钮一个去处
       *   · 「能力」不该是个平级标签 —— 它是对话的辅助信息,不是另一种视图。
       *     而且切过去之后对话列表就看不见了,来回切很烦
       *   · 腾出来的位置给「自选股」—— 每张卡片挂预测评估/交易成本/概率校准
       *     三个入口,正好对上复赛评委的三项建议
       *
       * 方案见 doc/开源hunter-community/04开源比赛/
       *        2026-08-30_导航重构方案-对话与自选股双栏.md
       */}
      <div style={{ display: 'flex', borderBottom: `1px solid ${HUNTER.LINE}`, padding: '0 12px' }}>
        <button onClick={() => switchTab('chat')} style={tabBtn(activeTab === 'chat')} title="历史对话">
          💬 对话
        </button>
        <button onClick={() => switchTab('watchlist')} style={tabBtn(activeTab === 'watchlist')} title="自选股 · 预测评估 / 交易成本 / 概率校准">
          ⭐ 自选股
        </button>
      </div>

      {/* Session 列表 · Tab 选中"对话"时占满剩余空间(~700px = 15-17 条对话) */}
      {activeTab === 'chat' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0 8px', minHeight: 120 }}>
          {loading && sessions.length === 0 ? (
            <div style={{ padding: '20px 12px', textAlign: 'center', color: HUNTER.INK_F, fontSize: 12 }}>
              加载中...
            </div>
          ) : sessions.length === 0 ? (
            <div style={{ padding: '20px 12px', textAlign: 'center', color: HUNTER.INK_F, fontSize: 12 }}>
              暂无对话 · 点击上方新建
            </div>
          ) : (
            <>
              {renderGroup('Today', groups.today)}
              {renderGroup('Yesterday', groups.yesterday)}
              {renderGroup('This Week', groups.week)}
              {renderGroup('Older', groups.older)}
            </>
          )}
        </div>
      )}

      {/* 能力区 · **常驻在对话列表下方**,不再是独立标签(2026-08-30)
       *
       * 原来它是「能力」标签,切过去对话列表就没了。而实际情况是:
       * 侧栏下半部分本来就是空的(对话通常只有几条),
       * 而数据源/工具箱/SKILL 却要切换才能看到 —— **位置有,东西没放对**。
       *
       * 现在放在这里:不用切换就看得见,顺便填上那片空白。
       * `flexShrink: 0` 保证对话多的时候它不被压扁,而是对话区先滚动。
       */}
      {activeTab === 'chat' && onPickSkill && (
        <div style={{
          flexShrink: 0, maxHeight: 260, overflowY: 'auto',
          borderTop: `1px solid ${HUNTER.LINE}`,
        }}>
          <CapabilityPanel
            onPick={onPickSkill}
            onManage={() => setShowSkillMgr(true)}
            refreshKey={skillRefresh}
          />
        </div>
      )}

      {activeTab === 'watchlist' && (
        <div style={{ flex: 1, padding: '18px 14px', color: HUNTER.INK_F, fontSize: 12, lineHeight: 1.9 }}>
          自选股显示在右边 →<br />
          每只股票下有<b>预测评估 / 交易成本 / 概率校准</b>三个入口。<br /><br />
          也可以<b>直接在对话里说</b>:<br />
          <span style={{ color: HUNTER.COPPER3 }}>「把贵州茅台加进自选,买了 2 手」</span>
        </div>
      )}

      {/* 平台 key 入口 · 未解锁大按钮 · 已解锁小图标(2026-08-29 事故:key 配上后
          按钮消失 · 用户想删/换找不到入口 · 只能 curl DELETE 或改数据库) */}
      <div style={{ padding: '6px 10px 0' }}>
        {!unlocked ? (
          <button
            onClick={() => setShowUnlock(true)}
            title="申请免费 key · 解锁全部工具与 SKILL"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              width: '100%', padding: '8px 6px',
              background: HUNTER.BRAND_PALE, border: `1px solid ${HUNTER.THEME}`,
              borderRadius: 8, color: HUNTER.COPPER3,
              fontSize: 12, fontWeight: 600, cursor: 'pointer',
            }}
          >
            <Unlock size={13} strokeWidth={1.8} />
            <span>申请 Key · 解锁全部工具</span>
          </button>
        ) : (
          <button
            onClick={() => setShowUnlock(true)}
            title="Hunter key 已配置 · 点击查看/更换/清除"
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              width: '100%', padding: '6px',
              background: 'transparent', border: `1px dashed ${HUNTER.LINE}`,
              borderRadius: 8, color: HUNTER.INK_F,
              fontSize: 11, cursor: 'pointer',
            }}
          >
            <Unlock size={11} strokeWidth={1.6} />
            <span>Hunter key 已配置 · 管理</span>
          </button>
        )}
      </div>

      {/* 底部工具入口 · MCP 组件已迁入 /library(数据源+工具箱)· 策略中心已成第三 tab · 此处不再展示 */}

      {/* 底部 · agentpit.io / 专业版 · 点击展开菜单（画像 / 退出） */}
      <ProfileFooter
        userEmail={userEmail}
        userInitial={userInitial}
        isAdmin={isAdmin}
        onProfile={() => { setWizard(false); setShowProfile(true) }}
        onLogout={handleLogout}
      />

      {showSkillMgr && (
        <SkillManager
          onClose={() => setShowSkillMgr(false)}
          onChanged={() => setSkillRefresh((v) => v + 1)}
          onPickTemplate={onPickSkill}
        />
      )}

      {showProfile && (
        <ProfileEditor wizard={wizard} onClose={() => { setShowProfile(false); setWizard(false) }} />
      )}

      {showUnlock && <UnlockModal onClose={() => setShowUnlock(false)} />}
    </aside>
  )
}

function ProfileFooter({
  userEmail,
  userInitial,
  isAdmin,
  onProfile,
  onLogout,
}: {
  userEmail: string
  userInitial: string
  isAdmin: boolean
  onProfile: () => void
  onLogout: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  return (
    <div ref={ref} style={{ padding: '11px 12px', borderTop: `1px solid ${HUNTER.LINE}`, position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={userEmail || '未登录'}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '8px 6px',
          background: open ? SB_HOVER : 'transparent',
          border: 'none',
          borderRadius: 11,
          cursor: 'pointer',
          fontFamily: 'inherit',
          textAlign: 'left',
        }}
        onMouseEnter={(e) => {
          if (!open) e.currentTarget.style.background = SB_HOVER
        }}
        onMouseLeave={(e) => {
          if (!open) e.currentTarget.style.background = 'transparent'
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: HUNTER.THEME,
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 13,
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          {userInitial}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: HUNTER.INK, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            agentpit.io
          </div>
          <div style={{ color: HUNTER.INK_F, fontSize: 11, marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
            专业版
            {isAdmin && (
              <span style={{ color: HUNTER.THEME, border: `1px solid ${HUNTER.LINE}`, borderRadius: 4, padding: '0 4px', fontSize: 9.5 }}>
                ADMIN
              </span>
            )}
          </div>
        </div>
        <ChevronDown size={14} style={{ color: HUNTER.INK_F, flexShrink: 0, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }} />
      </button>

      {open && (
        <div
          style={{
            position: 'absolute',
            bottom: 'calc(100% - 4px)',
            left: 12,
            right: 12,
            background: '#fff',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 10,
            boxShadow: '0 12px 30px rgba(30,20,10,0.14)',
            padding: 6,
            zIndex: 20,
          }}
        >
          {userEmail && (
            <div style={{ padding: '6px 10px 8px', fontSize: 11, color: HUNTER.INK_F, borderBottom: `1px solid ${HUNTER.LINE}`, marginBottom: 4, wordBreak: 'break-all' }}>
              {userEmail}
            </div>
          )}
          <MenuItem onClick={() => { setOpen(false); onProfile() }}>我的画像</MenuItem>
          {/* 单用户模式下清了 token 会被立刻换回来,这个入口没有意义,藏掉 */}
          {!isSingleUser() && (
            <MenuItem onClick={() => { setOpen(false); onLogout() }} danger>
              <LogOut size={12} /> 退出登录
            </MenuItem>
          )}
        </div>
      )}
    </div>
  )
}

function MenuItem({ children, onClick, danger = false }: { children: React.ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 10px',
        background: 'transparent',
        border: 'none',
        borderRadius: 6,
        color: danger ? HUNTER.UP : HUNTER.INK_S,
        fontSize: 13,
        textAlign: 'left',
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = danger ? '#fbeaea' : '#faf9f4')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      {children}
    </button>
  )
}
