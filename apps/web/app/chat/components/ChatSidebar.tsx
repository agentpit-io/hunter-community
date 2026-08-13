'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Plus, MessageSquare, LogOut, ChevronDown, PanelLeftClose, Unlock, ShieldCheck } from 'lucide-react'
import { HUNTER, HUNTER_LOGO } from '../../lib/hunter-theme'
import type { Session } from '../lib/types'
import { listSessions, createSession } from '../lib/opencodeClient'
import SkillPanel from './SkillPanel'
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
}

// Claude 风米黄 · 温暖但克制
const SB_BG = '#faf9f4'
const SB_HOVER = '#f0ede2'
const SB_ACTIVE = '#e8e2d1'

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

export default function ChatSidebar({ currentSessionId, onSelectSession, onNewSession, onPickSkill, onCollapse }: Props) {
  const router = useRouter()
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [showSkillMgr, setShowSkillMgr] = useState(false)
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

      {/* 能力区 —— 让用户知道能问什么(自身失败时静默不显示,不影响聊天) */}
      {onPickSkill && (
        <SkillPanel
          onPick={onPickSkill}
          onManage={() => setShowSkillMgr(true)}
          refreshKey={skillRefresh}
        />
      )}

      {/* Session 列表 · 提升到 SkillPanel 之下 · 占据大部分空间 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0 8px' }}>
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

      {/* 平台 key 入口 · 未解锁时这是整个侧栏最该被看见的一行
          —— 左侧那些工具与 SKILL 都要它,所以给整行宽度 + 铜色描边,
          解锁后退回一行安静的状态显示。 */}
      <div style={{ padding: '6px 10px 0' }}>
        <button
          onClick={() => setShowUnlock(true)}
          title={unlocked ? '已接入 Hunter 服务 · 全部工具可用' : '申请免费 key · 解锁全部工具与 SKILL'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            width: '100%',
            padding: '8px 6px',
            background: unlocked ? HUNTER.TAG_OK_BG : HUNTER.BRAND_PALE,
            border: `1px solid ${unlocked ? '#BFD8CC' : HUNTER.THEME}`,
            borderRadius: 8,
            color: unlocked ? HUNTER.TAG_OK_FG : HUNTER.COPPER3,
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {unlocked ? <ShieldCheck size={13} strokeWidth={1.8} /> : <Unlock size={13} strokeWidth={1.8} />}
          <span>{unlocked ? '已解锁全部工具' : '申请 Key · 解锁全部工具'}</span>
        </button>
      </div>

      {/* 底部工具入口 · MCP 组件 + 策略中心 · 合并为一行两 icon 节省纵向 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 6,
          padding: '6px 10px 8px',
          borderTop: `1px solid ${HUNTER.LINE}`,
        }}
      >
        <a
          href="/mcp-config"
          title="MCP 组件 · 接入你自己的数据源(Polygon/AlphaVantage/自研)"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            padding: '8px 6px',
            background: '#f4f2ea',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 8,
            color: HUNTER.INK_S,
            fontSize: 12,
            textDecoration: 'none',
            fontWeight: 500,
            transition: 'background .12s, border-color .12s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = HUNTER.BRAND_PALE
            e.currentTarget.style.borderColor = HUNTER.THEME
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = '#f4f2ea'
            e.currentTarget.style.borderColor = HUNTER.LINE
          }}
        >
          <span style={{ fontSize: 13 }}>🔌</span>
          <span>MCP 组件</span>
        </a>
        <a
          href="/strategies/index.html"
          target="_blank"
          rel="noopener noreferrer"
          title="策略中心 · 多因子选股 · 组合回测"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            padding: '8px 6px',
            background: '#faf4ea',
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 8,
            color: HUNTER.INK_S,
            fontSize: 12,
            textDecoration: 'none',
            fontWeight: 500,
            transition: 'background .12s, border-color .12s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = HUNTER.BRAND_PALE
            e.currentTarget.style.borderColor = HUNTER.THEME
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = '#faf4ea'
            e.currentTarget.style.borderColor = HUNTER.LINE
          }}
        >
          <span style={{ fontSize: 13, color: HUNTER.COPPER3 }}>📊</span>
          <span>策略中心</span>
        </a>
      </div>

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
