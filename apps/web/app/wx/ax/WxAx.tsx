'use client'

/**
 * AdventureX 展位活动 H5（V4 · 功能体验轨道）
 *
 * 入口：服务号菜单「活动注册」→ /api/wx/oauth?source=adventurex
 *   → 新用户:   /wx/ax?state=xxx  （一步提交：股票+邮箱 → 分析结果邮件送达）
 *   → 老用户:   /wx/ax?t=JWT      （直接进 summary 页看进度/奖励）
 *   → 报告链接:  /wx/ax?report=ID  （邮件里的完整报告链接）
 *
 * 流程：Step 1 邮箱+股票一步启动 → 邮件送达（分析+账号密码+PC 地址+核销码）
 *       Step 2 打卡 4 项功能（2 基础 + 2 随机）→ 达阈自动解锁股民礼
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useStockCheck } from './useStockCheck'
import { parseSentinelReport, weightTier } from './parseSentinel'

// ── 猎鹿人主题 ─────────────────────────────────────────────────────────────
const THEME  = '#B06A32'
const UP     = '#A4332B'
const DN     = '#3F6B40'
const BG     = '#F7F3EC'
const PAPER  = '#FFFDF9'
const PAPER2 = '#EFE8DC'
const INK    = '#211C18'
const INK_S  = '#4B423A'
const INK_F  = '#7A6F63'
const LINE   = '#D8CDBA'
const COPPER2 = '#D4925A'
const HEADER_BG = 'linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%)'
const SERIF  = '"Songti SC","Source Han Serif SC",Georgia,serif'

type Step = 'loading' | 'blocked' | 'intake' | 'summary'

interface StockItem { code: string; name: string; exchange?: string; market?: string }
interface Activity {
  email: string
  level1_code: string; level1_done_at: string | null
  level1_stock_name: string; level1_stock_code: string
  level1_redeemed_at: string | null
  level1_email_sent_at: string | null
  level2_code: string; level2_done_at: string | null
  level2_redeemed_at: string | null
  member_months: number; member_expires_at: string | null
  features_used: string[]
}
interface FeatureItem { id: string; icon: string; title: string; desc: string; route: string; used: boolean }
interface FeaturesResp {
  ok: boolean; ax_active: boolean; features: FeatureItem[]
  used_count: number; total_count: number; unlocked: boolean
  level2_code: string; level2_redeemed_at?: string | null
}
interface AnalysisResult {
  decision?: string; confidence?: number; key_reason?: string
  bull_summary?: string; bear_summary?: string; sentinel_summary?: string
  investment_plan?: string
  stop_loss?: number | string; report_id?: number
  sentinel_conflicts?: boolean
}

const DISCLAIMER = '产品数据分析由 AI 生成，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。'

const wrap: React.CSSProperties = { minHeight: '100vh', background: BG, color: INK, fontFamily: '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif' }
const body: React.CSSProperties = { padding: '18px 16px 40px', maxWidth: 480, margin: '0 auto' }
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '12px 14px', fontSize: 16, borderRadius: 10,
  border: `1.5px solid ${LINE}`, background: '#fff', color: INK, outline: 'none', boxSizing: 'border-box',
}

const Card = ({ children, style = {} }: { children: React.ReactNode; style?: React.CSSProperties }) => (
  <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 14, padding: '20px 18px', ...style }}>{children}</div>
)
const Btn = ({ children, onClick, disabled = false, ghost = false }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; ghost?: boolean
}) => (
  <button onClick={onClick} disabled={disabled} style={{
    width: '100%', padding: '13px 0', borderRadius: 10, fontSize: 16, fontWeight: 600,
    border: ghost ? `1.5px solid ${THEME}` : 'none',
    background: ghost ? 'transparent' : disabled ? '#C9B9A5' : THEME,
    color: ghost ? THEME : '#fff', cursor: disabled ? 'not-allowed' : 'pointer',
  }}>{children}</button>
)
const Header = ({ sub }: { sub: string }) => (
  <div style={{ background: HEADER_BG, borderRadius: '0 0 18px 18px', padding: '26px 22px 22px', textAlign: 'center' }}>
    <div style={{ fontFamily: SERIF, fontSize: 24, fontWeight: 700, color: COPPER2 }}>猎鹿人 · Hunter</div>
    <div style={{ fontSize: 13, color: PAPER2, marginTop: 6 }}>只对你说真话的 AI 投资管家</div>
    <div style={{ display: 'inline-block', marginTop: 12, padding: '4px 14px', borderRadius: 999, border: `1px solid ${COPPER2}`, color: COPPER2, fontSize: 12, letterSpacing: 1 }}>
      ── {sub} ──
    </div>
  </div>
)
// 后端返回的技术错误码 → 用户能读懂的中文
const ERR_MSG_MAP: Record<string, string> = {
  UNAUTHORIZED: '登录状态失效，请点下方「一键重新授权」重进活动',
  INVALID_TOKEN: '登录 token 无效，请点下方「一键重新授权」重进活动',
  'Unknown Error': '未知错误，请稍后重试',
}
const friendlyErr = (e: string) => ERR_MSG_MAP[e] || e

const ErrLine = ({ err }: { err: string }) => {
  if (!err) return null
  const isExpired = err.includes('微信授权已过期') || err.includes('授权已过期')
                 || err === 'UNAUTHORIZED' || err === 'INVALID_TOKEN'
                 || err.includes('登录状态失效') || err.includes('token 无效')
  return (
    <div style={{ color: UP, fontSize: 13, marginTop: 10, textAlign: 'center', lineHeight: 1.7 }}>
      {friendlyErr(err)}
      {isExpired && (
        <div style={{ marginTop: 8 }}>
          <a href="/api/wx/oauth?source=adventurex"
             style={{ display: 'inline-block', padding: '8px 20px', background: THEME, color: '#fff',
                      borderRadius: 8, fontSize: 14, textDecoration: 'none', fontWeight: 600 }}>
            🔄 一键重新授权
          </a>
        </div>
      )}
    </div>
  )
}
const CodeBadge = ({ code, redeemed }: { code: string; redeemed: boolean }) => (
  <span style={{
    display: 'inline-block', padding: '3px 12px', borderRadius: 8, fontSize: 16, fontWeight: 700,
    letterSpacing: 2, fontFamily: 'ui-monospace,monospace',
    background: redeemed ? PAPER2 : '#FBF1E4', color: redeemed ? INK_F : THEME,
    border: `1.5px dashed ${redeemed ? LINE : THEME}`,
    textDecoration: redeemed ? 'line-through' : 'none',
  }}>{code}</span>
)

function getToken() { try { return localStorage.getItem('hunter_token') || '' } catch { return '' } }
function setTokenLS(t: string) { try { localStorage.setItem('hunter_token', t) } catch {} }

// 附带 status + data 的 API 错误(便于调用方读 need_password 等业务标志)
class ApiError extends Error {
  status: number
  data: Record<string, unknown>
  constructor(msg: string, status: number, data: Record<string, unknown>) {
    super(msg); this.status = status; this.data = data
  }
}

async function api(path: string, opts: RequestInit = {}) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(opts.headers as Record<string, string> || {}) }
  const tk = getToken()
  if (tk) headers['Authorization'] = `Bearer ${tk}`
  const r = await fetch(path, { ...opts, headers })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new ApiError(data.error || data.detail || `请求失败(${r.status})`, r.status, data)
  return data
}

// ── V4.2：结构化报告详情卡（替代原来的一坨文本） ─────────────────────────
const SectionTitle = ({ icon, children, color = INK }: {
  icon: string; children: React.ReactNode; color?: string
}) => (
  <div style={{
    fontFamily: SERIF, fontSize: 15, fontWeight: 700, color,
    marginBottom: 8, letterSpacing: 0.3,
  }}>
    <span style={{ marginRight: 6 }}>{icon}</span>{children}
  </div>
)

const CollapsibleText = ({ text, maxLines = 4, color = INK_S }: {
  text: string; maxLines?: number; color?: string
}) => {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n').filter(l => l.trim().length > 0)
  const shouldFold = lines.length > maxLines
  const visible = open || !shouldFold ? lines : lines.slice(0, maxLines)
  return (
    <div>
      <p style={{ margin: 0, fontSize: 13.5, color, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
        {visible.join('\n')}
      </p>
      {shouldFold && (
        <button onClick={() => setOpen(!open)} style={{
          marginTop: 6, padding: 0, background: 'none', border: 'none',
          color: THEME, fontSize: 12, fontWeight: 600, cursor: 'pointer',
        }}>
          {open ? '▲ 收起' : '▼ 展开全部'}
        </button>
      )}
    </div>
  )
}

interface ReportDetailProps {
  result: AnalysisResult
  stock: { code: string; name: string }
  score: number | null
  decision: string
  decisionCn: string
  decisionColor: string
}

const ReportDetailCard: React.FC<ReportDetailProps> = ({
  result, stock, score, decision, decisionCn, decisionColor,
}) => {
  const parsed = parseSentinelReport(result.sentinel_summary || '')
  const [showAllVerified, setShowAllVerified] = useState(false)
  const [showRejected, setShowRejected]       = useState(false)

  const stopLossNum = typeof result.stop_loss === 'number'
    ? result.stop_loss
    : (result.stop_loss ? parseFloat(String(result.stop_loss)) : null)

  return (
    <Card style={{ marginBottom: 14 }}>
      {/* 头部：股票 + 分数 + 综合裁判 */}
      <div style={{ textAlign: 'center', paddingBottom: 14, borderBottom: `1px dashed ${LINE}` }}>
        <div style={{ fontFamily: SERIF, fontSize: 20, fontWeight: 700 }}>
          {stock.name} <span style={{ fontSize: 14, color: INK_F }}>{stock.code}</span>
        </div>
        {score != null && (
          <div style={{ marginTop: 10 }}>
            <span style={{ fontSize: 42, fontWeight: 800, color: THEME, fontFamily: SERIF }}>{score}</span>
            <span style={{ fontSize: 14, color: INK_F }}> / 100 真相评分</span>
          </div>
        )}
        {decisionCn && (
          <div style={{ marginTop: 6, fontSize: 15 }}>
            综合裁判：<b style={{ color: decisionColor }}>{decisionCn}（{decision}）</b>
          </div>
        )}
      </div>

      {result.sentinel_conflicts && (
        <div style={{ marginTop: 12, padding: '9px 14px', borderRadius: 8,
                      background: '#FBF1E4', border: `1px solid #E6C89E`,
                      color: '#7C4A22', fontSize: 12, lineHeight: 1.7 }}>
          ⚠️ Sentinel 情报与技术面存在矛盾，置信度已被 AI 修正
        </div>
      )}

      {/* 核心理由（一句话，不折叠） */}
      {result.key_reason && (
        <div style={{ marginTop: 14 }}>
          <SectionTitle icon="📝">核心理由</SectionTitle>
          <p style={{ margin: 0, fontSize: 14, color: INK_S, lineHeight: 1.85 }}>
            {result.key_reason}
          </p>
        </div>
      )}

      {/* 利好证据 */}
      {result.bull_summary && (
        <div style={{ marginTop: 14 }}>
          <SectionTitle icon="🐂" color={UP}>利好证据</SectionTitle>
          <CollapsibleText text={result.bull_summary} maxLines={4} />
        </div>
      )}

      {/* 风险提示 */}
      {result.bear_summary && (
        <div style={{ marginTop: 14 }}>
          <SectionTitle icon="🐻" color={DN}>风险提示</SectionTitle>
          <CollapsibleText text={result.bear_summary} maxLines={4} />
        </div>
      )}

      {/* 操作建议 */}
      {result.investment_plan && (
        <div style={{ marginTop: 14 }}>
          <SectionTitle icon="💡" color={THEME}>操作建议</SectionTitle>
          <CollapsibleText text={result.investment_plan} maxLines={4} />
        </div>
      )}

      {/* 止损参考价 */}
      {stopLossNum != null && !isNaN(stopLossNum) && (
        <div style={{
          marginTop: 14, padding: '12px 16px', borderRadius: 10,
          background: '#FBEDEA', border: `1px solid #E6C0BA`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: 13, color: INK_S }}>🛡 止损参考价</span>
          <span style={{ fontFamily: SERIF, fontSize: 20, fontWeight: 800, color: UP }}>
            ¥{stopLossNum.toFixed(2)}
          </span>
        </div>
      )}

      {/* 情报摘要（结构化） */}
      {(parsed.stats || parsed.verified.length > 0 || parsed.rawFallback) && (
        <div style={{ marginTop: 18, paddingTop: 14, borderTop: `1px dashed ${LINE}` }}>
          <SectionTitle icon="🛰" color={THEME}>情报摘要</SectionTitle>

          {parsed.rawFallback ? (
            /* 降级消息或未匹配格式 → 展示原文 */
            <div style={{ fontSize: 12, color: INK_F, lineHeight: 1.7, background: PAPER2,
                          padding: '10px 12px', borderRadius: 8 }}>
              {parsed.rawFallback}
            </div>
          ) : (
            <>
              {parsed.stats && (
                <div style={{
                  fontSize: 12, color: INK_S, background: PAPER2, padding: '8px 12px',
                  borderRadius: 8, marginBottom: 10, display: 'flex', gap: 12, flexWrap: 'wrap',
                }}>
                  <span>📊 抓取 <b>{parsed.stats.total}</b></span>
                  <span>✅ 保留 <b style={{ color: DN }}>{parsed.stats.kept}</b></span>
                  <span>❌ 过滤 <b style={{ color: UP }}>{parsed.stats.dropped}</b></span>
                </div>
              )}

              {parsed.verified.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, color: INK_F, marginBottom: 6, fontWeight: 600 }}>
                    ✅ 已验证核心事实
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {(showAllVerified ? parsed.verified : parsed.verified.slice(0, 3)).map((v, i) => {
                      const t = weightTier(v.weight)
                      return (
                        <div key={i} style={{
                          padding: '9px 12px', background: PAPER, border: `1px solid ${LINE}`,
                          borderLeft: `3px solid ${t.color}`, borderRadius: 8,
                        }}>
                          <div style={{ fontSize: 11, color: INK_F, marginBottom: 4 }}>
                            <span style={{ background: PAPER2, padding: '1px 6px', borderRadius: 4 }}>
                              {v.source}
                            </span>
                            <span style={{ marginLeft: 8, color: t.color, fontWeight: 600 }}>
                              {t.icon} {t.label}
                            </span>
                          </div>
                          <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.7 }}>{v.fact}</div>
                        </div>
                      )
                    })}
                  </div>
                  {parsed.verified.length > 3 && (
                    <button onClick={() => setShowAllVerified(!showAllVerified)} style={{
                      marginTop: 8, padding: 0, background: 'none', border: 'none',
                      color: THEME, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    }}>
                      {showAllVerified
                        ? '▲ 收起'
                        : `▼ 展开全部 ${parsed.verified.length} 条`}
                    </button>
                  )}
                </div>
              )}

              {parsed.rejected.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <button onClick={() => setShowRejected(!showRejected)} style={{
                    padding: '6px 10px', background: PAPER2, border: `1px dashed ${LINE}`,
                    borderRadius: 8, color: INK_S, fontSize: 12, cursor: 'pointer',
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                  }}>
                    {showRejected ? '▲ 收起' : `▼ 显示 ${parsed.rejected.length} 条被过滤内容`}
                  </button>
                  {showRejected && (
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {parsed.rejected.map((r, i) => (
                        <div key={i} style={{
                          padding: '8px 12px', background: '#FEF7F5', border: `1px solid #F0D4CC`,
                          borderRadius: 8, fontSize: 12, color: INK_F, lineHeight: 1.7,
                        }}>
                          <div style={{ color: INK_S }}>{r.text}</div>
                          <div style={{ marginTop: 4, fontSize: 11, color: INK_F, fontStyle: 'italic' }}>
                            过滤原因：{r.reason}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {parsed.opinion && (
                <div style={{
                  padding: '9px 12px', background: '#FBF1E4', borderRadius: 8,
                  fontSize: 12, color: THEME, fontWeight: 600, marginTop: 8,
                }}>
                  📉 综合研判：{parsed.opinion}
                  {parsed.confidencePct != null && `（置信度 ${parsed.confidencePct}%）`}
                </div>
              )}

              {parsed.killTriggered && (
                <div style={{
                  padding: '9px 12px', background: '#FBEDEA', border: `1px solid #E6C0BA`,
                  borderRadius: 8, fontSize: 12, color: UP, marginTop: 8,
                }}>
                  🚨 止损条件已触发：{parsed.killDesc}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </Card>
  )
}

export default function WxAx() {
  const params = useSearchParams()
  const [step, setStep] = useState<Step>('loading')
  const [regState, setRegState] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [needPassword, setNeedPassword] = useState(false)  // 邮箱已注册需要密码绑定
  const [agree, setAgree] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [activity, setActivity] = useState<Activity | null>(null)

  // 股票选择（V4.2 失焦自动查询 + 渐进式邮箱披露）
  const stock = useStockCheck()
  const { query, picked, status: checkStatus, msg: checkMsg, matches } = stock
  // 邮箱区揭示后自动滚动定位用
  const emailRef = useRef<HTMLInputElement | null>(null)
  const emailBlockRef = useRef<HTMLDivElement | null>(null)

  // V4：功能体验进度
  const [features, setFeatures] = useState<FeaturesResp | null>(null)

  // 报告详情（?report= 深链或刚提交完成）
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [reportStock, setReportStock] = useState<{ code: string; name: string } | null>(null)

  // 刚提交后短暂展示 stock name（引导用户看邮件）
  const [justSubmitted, setJustSubmitted] = useState<string>('')

  const refreshMe = useCallback(async () => {
    try {
      const me = await api('/api/ax/me')
      setActivity(me.activity)
      return me.activity as Activity | null
    } catch { return null }
  }, [])

  const loadFeatures = useCallback(async () => {
    try {
      const d = await api('/api/ax/features')
      setFeatures(d as FeaturesResp)
    } catch { /* 未参与活动等 → 静默 */ }
  }, [])

  // ── 入口路由 ────────────────────────────────────────────────────────────
  useEffect(() => {
    const st = params?.get('state') || ''
    const t = params?.get('t') || ''
    if (t) setTokenLS(t)

    // enterSummary(bootMe?):
    //   若 bootMe 已在 probeToken 阶段拿到 (且是 active 结果),复用它避免二次
    //   fetch /api/ax/me;仅还需拉 features
    //   若 bootMe 缺失,回退到旧行为(refreshMe + loadFeatures 并行)
    const enterSummary = async (bootMe?: { activity: Activity | null }) => {
      if (bootMe) {
        setActivity(bootMe.activity)
        await loadFeatures()
      } else {
        await Promise.all([refreshMe(), loadFeatures()])
      }
      setStep('summary')
    }

    // 探测本地 token 状态,并顺带返回 me 数据供 enterSummary 复用(P0 去重):
    //   active   — 有效 token 且已绑定活动(有 ax_event 行)
    //   stale    — token 签名有效但活动状态为空(如后端清理了 openid 绑定)
    //   ghost    — token 已失效(401),已清 localStorage
    //   no_token — 本地无 token
    const probeToken = async (): Promise<{
      state: 'active' | 'stale' | 'ghost' | 'no_token'
      me?: { activity: Activity | null }
    }> => {
      const tk = getToken()
      if (!tk) return { state: 'no_token' }
      try {
        const r = await fetch('/api/ax/me', { headers: { Authorization: `Bearer ${tk}` } })
        if (r.ok) {
          const me = await r.json()
          if (me.ax_active && me.activity?.registered_at) {
            return { state: 'active', me }
          }
          return { state: 'stale', me }
        }
        if (r.status === 401) {
          console.warn('[ax boot] hunter_token 被后端拒绝 (401),判为幽灵 token,清除 localStorage')
          try { localStorage.removeItem('hunter_token') } catch {}
          return { state: 'ghost' }
        }
      } catch (e) {
        console.warn('[ax boot] /api/ax/me 探测网络异常', e)
      }
      return { state: 'stale' }  // 兜底: 不 clear,也不当 valid
    }

    const boot = async () => {
      console.log('[ax boot] start', { has_state: !!st, has_url_t: !!t, has_stored_token: !!getToken() })

      // 邮件里的完整报告链接：?report=<id>
      const rep = params?.get('report') || ''
      if (rep && getToken()) {
        try {
          const r = await api(`/api/online-analysis/reports/${rep}`)
          const fcRaw = r.final_conclusion
          const fc = typeof fcRaw === 'string' ? JSON.parse(fcRaw) : (fcRaw || {})
          setReportStock({ code: r.stock_code || '', name: r.stock_name || '' })
          setResult({ ...fc, confidence: fc.confidence ?? r.confidence, decision: fc.decision || r.thesis_status })
        } catch (e) { console.warn('[ax boot] 读报告失败', e) }
      }

      // Fix 3:探测 token 是否有效 + 是否绑定了活动 (顺带拿 me 数据供 enterSummary 复用)
      const probe = await probeToken()
      const tokenState = probe.state
      console.log('[ax boot] token 探测结果', tokenState, 'me_activity:', !!probe.me?.activity)

      // URL 显式带 state → 强制 intake(无论 token 状态)。
      // 覆盖场景:「登陆注册」入口对 @test 临时号用户带 t+state 跳来时,
      // 若不这样处理,tokenState=active 会跳过邮箱补录直接进 summary。
      if (st) {
        console.log('[ax boot] 有 state → 强制进 intake', {
          state_prefix: st.slice(0, 8), token_state: tokenState,
        })
        setRegState(st); setStep('intake'); return
      }

      // 无 state + active:已完成流程的用户 → summary
      if (tokenState === 'active') {
        console.log('[ax boot] 无 state + token active → 直接进 summary (复用 probe 拿到的 me)')
        try { await enterSummary(probe.me) } catch (e) {
          console.error('[ax boot] enterSummary 异常', e)
          setErr('加载活动状态失败,请刷新重试')
        }
        return
      }

      // 无 state + stale token → 仍尝试进 summary(活动可能刚开始)
      if (tokenState === 'stale') {
        console.log('[ax boot] 无 state + stale token → 尝试进 summary (复用 probe 拿到的 me)')
        try { await enterSummary(probe.me) } catch (e) {
          console.error('[ax boot] enterSummary 异常', e); setStep('blocked')
        }
        return
      }

      // 无 state 无 token → blocked
      console.log('[ax boot] 无 state 无 token → blocked')
      setStep('blocked')
    }
    boot()
  }, [params, refreshMe, loadFeatures])

  // V4：页面回到前台时自动刷新进度（用户从主 app 回来）
  useEffect(() => {
    if (step !== 'summary') return
    const onVis = () => { if (document.visibilityState === 'visible') { loadFeatures(); refreshMe() } }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', onVis)
    }
  }, [step, loadFeatures, refreshMe])

  // ── V4.2：邮箱区揭示后自动滚动 + 桌面 autofocus（移动端不 focus 避免键盘遮挡）
  useEffect(() => {
    if (!(picked && picked.market === 'A')) return
    // 等一帧让 DOM 就位再滚
    const timer = window.setTimeout(() => {
      try {
        emailBlockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      } catch { /* 老浏览器不支持 smooth，忽略 */ }
      const isMobile = typeof navigator !== 'undefined' &&
        /iPhone|iPad|iPod|Android|Mobile/i.test(navigator.userAgent)
      if (!isMobile) emailRef.current?.focus()
    }, 240)  // 等揭示动画差不多结束
    return () => window.clearTimeout(timer)
  }, [picked])

  // ── V4：Step 1 合并提交（股票 + 邮箱 → 分析 + 邮件）─────────────────────
  const doIntake = async () => {
    setErr('')
    if (!picked || picked.market !== 'A') { setErr('请先选择一只 A 股'); return }
    if (!email.includes('@')) { setErr('请输入有效邮箱'); return }
    if (!agree) { setErr('请勾选同意接收产品通知'); return }
    if (needPassword && !password) { setErr('请输入邮箱账号的原密码'); return }
    console.log('[ax intake] submitting', {
      state_prefix: (regState || '').slice(0, 8),
      email, stock_code: picked.code, stock_name: picked.name,
      has_password: !!password,
      has_stored_token: !!getToken(),
    })
    setBusy(true)
    try {
      const r = await api('/api/ax/register-and-analyze', {
        method: 'POST',
        body: JSON.stringify({
          state: regState, email,
          stock_code: picked.code, stock_name: picked.name,
          market: picked.market || 'A',
          exchange: picked.exchange || '',
          password: needPassword ? password : '',
        }),
      })
      console.log('[ax intake] success', { task_id: r.task_id, email: r.email })
      setTokenLS(r.token)
      setJustSubmitted(picked.name)
      await Promise.all([refreshMe(), loadFeatures()])
      setStep('summary')
    } catch (e: unknown) {
      // 邮箱已注册 → 展开密码框走绑定流程,不作为错误提示
      if (e instanceof ApiError && e.status === 409 && e.data?.need_password) {
        console.log('[ax intake] 邮箱已注册,请求密码绑定', { email })
        setNeedPassword(true)
        setErr('')
        setBusy(false)
        return
      }
      const msg = e instanceof Error ? e.message : '提交失败'
      console.error('[ax intake] failed', { error: msg, err: e })
      setErr(msg)
    } finally { setBusy(false) }
  }

  // ── 渲染 ────────────────────────────────────────────────────────────────
  if (step === 'loading') return (
    <div style={{ ...wrap, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <span style={{ color: INK_F }}>加载中...</span>
    </div>
  )

  if (step === 'blocked') return (
    <div style={wrap}>
      <Header sub="AdventureX 专属体验" />
      <div style={body}>
        <Card style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 40, marginBottom: 10 }}>📱</div>
          <div style={{ fontSize: 15, lineHeight: 1.8, color: INK_S }}>
            请回到微信服务号，<br />点击底部菜单「<b style={{ color: THEME }}>活动注册</b>」进入活动
          </div>
        </Card>
      </div>
    </div>
  )

  // ── Step 1：intake（股票 + 邮箱 一步启动）──────────────────────────────
  if (step === 'intake') return (
    <div style={wrap}>
      <Header sub="第一步 · AI 真相体检" />
      <div style={body}>
        <Card>
          <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 4 }}>输入股票 + 邮箱，一步开始</div>
          <div style={{ fontSize: 13, color: INK_F, marginBottom: 16, lineHeight: 1.7 }}>
            AI 后台分析（约 1-3 分钟）→ 分析报告 · 账号密码 · PC 登录地址一并发到你的邮箱
          </div>

          {/* 揭示动画：仅注入一次 */}
          <style>{`
            @keyframes axEmailStepEnter {
              from { opacity: 0; transform: translateY(-8px); }
              to   { opacity: 1; transform: translateY(0); }
            }
            .ax-email-step { animation: axEmailStepEnter 220ms ease-out; will-change: opacity, transform; }
            @keyframes axSpin { to { transform: rotate(360deg); } }
            .ax-spinner {
              display: inline-block; width: 14px; height: 14px; border-radius: 50%;
              border: 2px solid ${PAPER2}; border-top-color: ${THEME};
              animation: axSpin .8s linear infinite; vertical-align: -3px; margin-right: 6px;
            }
          `}</style>

          {/* ── Step 1: 股票 ─────────────────────────────────────── */}
          <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 6 }}>
            <span style={{
              display: 'inline-block', width: 20, height: 20, borderRadius: 10, textAlign: 'center',
              lineHeight: '20px', fontSize: 12, fontWeight: 700, marginRight: 8,
              background: picked ? DN : THEME, color: '#fff',
            }}>{picked ? '✓' : '1'}</span>
            <span style={{ fontSize: 13, color: INK_S, letterSpacing: 0.5 }}>
              选一只你关心的股票（仅支持 A 股）
            </span>
          </div>
          <input
            style={{
              ...inputStyle,
              background: picked ? PAPER2 : '#fff',
              cursor: picked ? 'default' : 'text',
            }}
            placeholder="股票名称或代码，如 中际旭创 / 600519"
            value={picked ? `${picked.name}  ${picked.code}` : query}
            disabled={!!picked}
            aria-busy={checkStatus === 'busy'}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !picked) { e.preventDefault(); stock.doCheckNow(query) }
            }}
            onBlur={() => { if (!picked && query.trim()) stock.scheduleCheckOnBlur(query) }}
            onChange={(e) => stock.onQueryChange(e.target.value)}
          />
          {!picked && (
            <>
              <div style={{ fontSize: 11, color: INK_F, marginTop: 6, letterSpacing: 0.3 }}>
                输入后自动识别 · 或点下方按钮进入下一步
              </div>
              {/* 「下一步 →」显式触发查询：给不习惯点空白失焦的用户一条明确路径 */}
              <button
                onClick={() => stock.doCheckNow(query)}
                disabled={!query.trim() || checkStatus === 'busy'}
                style={{
                  width: '100%', marginTop: 10, padding: '11px 0', borderRadius: 10,
                  border: 'none', fontSize: 15, fontWeight: 600,
                  background: (!query.trim() || checkStatus === 'busy') ? '#C9B9A5' : THEME,
                  color: '#fff',
                  cursor: (!query.trim() || checkStatus === 'busy') ? 'not-allowed' : 'pointer',
                }}>
                {checkStatus === 'busy' ? '识别中...' : '下一步 →'}
              </button>
            </>
          )}
          {picked && (
            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1, padding: '8px 12px', background: '#F0F9F0', border: `1px solid #C7E0C9`,
                            borderRadius: 10, fontSize: 13, color: DN }}>
                ✅ 已识别 · <b>{picked.name}</b> {picked.code}
                <span style={{ marginLeft: 8, fontSize: 11, background: '#fff', color: DN,
                               padding: '1px 6px', borderRadius: 4, border: `1px solid #C7E0C9` }}>A股</span>
              </div>
              <button onClick={stock.reset}
                style={{ padding: '8px 12px', background: PAPER2, border: `1px solid ${LINE}`, borderRadius: 10,
                         color: INK_S, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>× 重选</button>
            </div>
          )}

          {/* 结果四态：busy / matched / unsupported / notfound（picked 已在上面处理） */}
          {!picked && checkStatus === 'busy' && (
            <div style={{ marginTop: 8, padding: '10px 14px', background: PAPER2,
                          borderRadius: 10, color: INK_S, fontSize: 13 }}>
              <span className="ax-spinner" />正在识别股票…
            </div>
          )}
          {!picked && checkStatus === 'matched' && matches.length > 0 && (
            <div style={{ marginTop: 8, border: `1px solid ${LINE}`, borderRadius: 10, overflow: 'hidden' }}>
              <div style={{ padding: '8px 14px', background: PAPER2, fontSize: 12, color: INK_F }}>
                找到 {matches.length} 个匹配，点击选择：
              </div>
              {matches.map((c) => (
                <div key={c.code} onClick={() => stock.pickMatch(c)}
                     style={{ padding: '10px 14px', fontSize: 15, borderTop: `1px solid ${PAPER2}`,
                              background: '#fff', cursor: 'pointer' }}>
                  <b>{c.name}</b> <span style={{ color: INK_F, fontSize: 13 }}>{c.code}</span>
                  <span style={{ marginLeft: 8, fontSize: 11, background: '#F0F9F0', color: DN,
                                 padding: '1px 6px', borderRadius: 4 }}>A股</span>
                </div>
              ))}
            </div>
          )}
          {!picked && checkStatus === 'unsupported' && (
            <div style={{ marginTop: 8, padding: '12px 14px', background: '#FEF2F2', border: '1px solid #FEE2E2',
                          borderRadius: 10, color: UP, fontSize: 13, lineHeight: 1.7 }}>
              ⚠️ {checkMsg}
            </div>
          )}
          {!picked && checkStatus === 'notfound' && (
            <div style={{ marginTop: 8, padding: '12px 14px', background: PAPER2,
                          borderRadius: 10, color: INK_S, fontSize: 13, lineHeight: 1.7 }}>
              🔍 {checkMsg}
            </div>
          )}

          {/* ── Step 2: 邮箱（picked 存在且 A 股才渲染，渐进式披露）────────── */}
          {picked && picked.market === 'A' && (
            <div ref={emailBlockRef} className="ax-email-step" style={{ marginTop: 18 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 6 }}>
                <span style={{
                  display: 'inline-block', width: 20, height: 20, borderRadius: 10, textAlign: 'center',
                  lineHeight: '20px', fontSize: 12, fontWeight: 700, marginRight: 8,
                  background: THEME, color: '#fff',
                }}>2</span>
                <span style={{ fontSize: 13, color: INK_S, letterSpacing: 0.5 }}>留个邮箱接收报告</span>
              </div>
              <input ref={emailRef} style={inputStyle} type="email" placeholder="your@email.com" value={email}
                     onChange={(e) => {
                       setEmail(e.target.value)
                       // 用户改邮箱 → 重置需要密码的状态(可能换邮箱重试)
                       if (needPassword) { setNeedPassword(false); setPassword(''); setErr('') }
                     }}
                     autoComplete="email" inputMode="email" />

              {/* 邮箱已注册 → 展开密码框,支持绑定 */}
              {needPassword && (
                <div style={{ marginTop: 12, padding: '12px 14px', background: PAPER2,
                              borderRadius: 10, border: `1px solid ${THEME}` }}>
                  <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.6, marginBottom: 10 }}>
                    🔑 此邮箱已注册过 Hunter 账号,输入原密码可将<b>本次微信</b>绑定到该账号
                    <br />
                    <span style={{ fontSize: 12, color: INK_F }}>
                      绑定后可用同一账号在电脑版 hunter.agentpit.io 与微信端互通
                    </span>
                  </div>
                  <input style={inputStyle} type="password" placeholder="邮箱账号原密码"
                         value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete="current-password" />
                  <div style={{ fontSize: 11, color: INK_F, marginTop: 6 }}>
                    忘记密码? 换一个新邮箱即可重新注册,或到 hunter.agentpit.io 找回密码
                  </div>
                </div>
              )}

              <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '14px 2px', fontSize: 13, color: INK_S }}>
                <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)}
                       style={{ accentColor: THEME, width: 16, height: 16 }} />
                同意接收产品通知
              </label>

              <div style={{ marginTop: 10 }}>
                <Btn onClick={doIntake}
                     disabled={busy || !email.includes('@') || !agree || checkStatus === 'busy'
                               || (needPassword && !password)}>
                  {busy ? '启动中...'
                    : needPassword ? '🔗 验证密码 · 绑定并开始分析'
                    : '⚡ 开始分析并接收邮件'}
                </Btn>
              </div>
              <ErrLine err={err} />
              <div style={{ fontSize: 12, color: INK_F, marginTop: 14, textAlign: 'center', lineHeight: 1.7 }}>
                完成分析即通关解锁 🎫 体验礼：铜钱钥匙链 + 3 个月 pro 会员<br />
                继续体验全部功能，可再升级为高级伴手礼
              </div>
              <div style={{ fontSize: 11, color: INK_F, marginTop: 8, textAlign: 'center' }}>ℹ️ 我们不会向第三方分享你的邮箱</div>
            </div>
          )}
        </Card>
        <div style={{ fontSize: 11, color: INK_F, marginTop: 14, textAlign: 'center', lineHeight: 1.6 }}>{DISCLAIMER}</div>
      </div>
    </div>
  )

  // ── Step 2 + Step 3：summary（进度追踪 + 奖励 + 报告详情）────────────────
  const a = activity
  const unlocked = !!a?.level2_code
  const usedCount = features?.used_count ?? 0
  const totalCount = features?.total_count ?? 4
  const pct = totalCount > 0 ? Math.round(usedCount * 100 / totalCount) : 0
  const unusedFeatures = (features?.features ?? []).filter((f) => !f.used)

  const score = result?.confidence != null ? Math.round((result.confidence <= 1 ? result.confidence * 100 : result.confidence)) : null
  const decision = result?.decision || ''
  const decisionCn = decision === 'BUY' ? '看好' : decision === 'SELL' ? '谨慎' : decision === 'HOLD' ? '中性' : ''
  const decisionColor = decision === 'BUY' ? UP : decision === 'SELL' ? DN : INK_S

  return (
    <div style={wrap}>
      <Header sub={unlocked ? '我的奖励' : (justSubmitted ? '分析已提交' : '我的体验')} />
      <div style={body}>

        {/* 刚提交后：提示邮件送达 */}
        {justSubmitted && !unlocked && (
          <Card style={{ marginBottom: 14, textAlign: 'center' }}>
            <div style={{ fontSize: 34, marginBottom: 6 }}>✅</div>
            <div style={{ fontFamily: SERIF, fontSize: 18, fontWeight: 700 }}>分析已在后台启动</div>
            <div style={{ fontSize: 14, color: INK_S, marginTop: 10, lineHeight: 1.9 }}>
              约 <b>1-3 分钟</b> 后，「<b style={{ color: THEME }}>{justSubmitted}</b>」的分析报告 + 你的账号密码 + PC 登录地址会一并发到邮箱
            </div>
          </Card>
        )}

        {/* ?report 深链：展示报告详情（V4.2 结构化重构） */}
        {result && reportStock && (
          <ReportDetailCard
            key={result.report_id || reportStock.code}
            result={result}
            stock={reportStock}
            score={score}
            decision={decision}
            decisionCn={decisionCn}
            decisionColor={decisionColor}
          />
        )}

        {/* 奖励卡片 */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 14, gap: 8 }}>
            <div style={{ fontSize: 18, fontWeight: 700 }}>🎁 我的奖励</div>
            {a?.email && (
              <div style={{ fontSize: 12, color: INK_F, marginLeft: 'auto', fontFamily: 'ui-monospace,monospace' }}>
                📧 {a.email}
              </div>
            )}
          </div>
          {/* 体验礼 */}
          <div style={{ padding: '12px 0', borderBottom: `1px dashed ${LINE}` }}>
            {a?.level1_code ? (
              <>
                <div style={{ fontSize: 15, fontWeight: 600 }}>✅ 🎫 体验礼（{a.level1_redeemed_at ? '已领取' : '已解锁'}）</div>
                <div style={{ fontSize: 13, color: INK_S, margin: '4px 0 8px' }}>铜钱钥匙链 + 3 个月 pro 会员</div>
                <CodeBadge code={a.level1_code} redeemed={!!a.level1_redeemed_at} />
              </>
            ) : (
              <>
                <div style={{ fontSize: 15, fontWeight: 600, color: INK_F }}>🔓 体验礼（分析中）</div>
                <div style={{ fontSize: 13, color: INK_S, margin: '6px 0 4px' }}>分析完成后核销码将送达邮箱与微信</div>
              </>
            )}
          </div>
          {/* 股民礼 */}
          <div style={{ padding: '12px 0' }}>
            {a?.level2_code ? (
              <>
                <div style={{ fontSize: 15, fontWeight: 600 }}>✅ 💎 股民礼（{a.level2_redeemed_at ? '已领取' : '已解锁'}）</div>
                <div style={{ fontSize: 13, color: INK_S, margin: '4px 0 8px' }}>高级伴手礼 + 3 个月会员</div>
                <CodeBadge code={a.level2_code} redeemed={!!a.level2_redeemed_at} />
              </>
            ) : (
              <>
                <div style={{ fontSize: 15, fontWeight: 600, color: INK_F }}>🔓 股民礼（未解锁）</div>
                <div style={{ fontSize: 13, color: INK_S, margin: '6px 0 4px', lineHeight: 1.8 }}>
                  完成全部 <b>{totalCount}</b> 项功能体验 → 高级伴手礼 + 3 个月会员
                </div>
              </>
            )}
          </div>
        </Card>

        {/* Step 2：功能体验进度 & 未体验清单（未解锁时展示） */}
        {!unlocked && (
          <Card style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontSize: 15, fontWeight: 700, flex: 1 }}>🎯 功能体验进度</div>
              <div style={{ fontSize: 14, color: THEME, fontWeight: 700 }}>{usedCount} / {totalCount}</div>
            </div>
            <div style={{ height: 8, background: PAPER2, borderRadius: 4, overflow: 'hidden', marginBottom: 8 }}>
              <div style={{ height: '100%', width: `${pct}%`, background: THEME, transition: 'width .35s ease' }} />
            </div>
            <div style={{ fontSize: 11, color: INK_F, marginBottom: 14, textAlign: 'right' }}>{pct}%</div>

            {unusedFeatures.length === 0 && features ? (
              <div style={{ padding: '12px 0', textAlign: 'center', color: DN, fontSize: 14 }}>
                ✅ 全部完成！股民礼正在解锁中…
              </div>
            ) : (
              <>
                <div style={{ fontSize: 13, color: INK_S, marginBottom: 10 }}>还有 <b>{unusedFeatures.length}</b> 项未体验，点击直达：</div>
                <div>
                  {unusedFeatures.slice(0, 6).map((f) => {
                    const target = `/wx/home?nav=${encodeURIComponent(f.route)}`
                    return (
                      <a key={f.id} href={target}
                         onClick={(e) => {
                           // 兜底: 微信 X5 内核偶发吞 <a> click, 强制原生导航
                           e.preventDefault()
                           console.log('[ax] nav 跳转', target)
                           try { window.location.assign(target) }
                           catch { window.location.href = target }
                         }}
                         style={{ display: 'flex', alignItems: 'center', padding: '11px 12px', marginBottom: 8,
                                  background: PAPER2, borderRadius: 10, textDecoration: 'none', color: INK }}>
                        <span style={{ fontSize: 18, marginRight: 12, width: 22, textAlign: 'center' }}>{f.icon}</span>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 14, fontWeight: 600 }}>{f.title}</div>
                          <div style={{ fontSize: 11, color: INK_F, marginTop: 2 }}>{f.desc}</div>
                        </div>
                        <span style={{ color: THEME, fontSize: 15 }}>›</span>
                      </a>
                    )
                  })}
                </div>
                {unusedFeatures.length > 6 && (
                  <div style={{ fontSize: 12, color: INK_F, textAlign: 'center', margin: '4px 0 12px' }}>… 还有 {unusedFeatures.length - 6} 项</div>
                )}
                <div style={{ marginTop: 12 }}>
                  <a href="/wx/ax/map"
                     onClick={(e) => {
                       e.preventDefault()
                       const target = '/wx/ax/map'
                       console.log('[ax] 打开功能地图', target)
                       try { window.location.assign(target) }
                       catch { window.location.href = target }
                     }}
                     style={{ display: 'block', padding: '13px 0', textAlign: 'center', background: THEME, color: '#fff',
                              borderRadius: 12, fontSize: 15, fontWeight: 600, textDecoration: 'none' }}>
                    🗺 打开功能地图 · 一次逛完
                  </a>
                </div>
                <div style={{ fontSize: 11, color: INK_F, marginTop: 10, textAlign: 'center', lineHeight: 1.6 }}>
                  进入任一功能自动记为已体验 · 完成后回到此页面刷新即可看到进度
                </div>
              </>
            )}
          </Card>
        )}

        {/* 领取指引 + 会员信息 */}
        <Card style={{ marginTop: 14, background: PAPER2 }}>
          <div style={{ fontSize: 13, color: INK_S, lineHeight: 2 }}>
            📍 凭核销码到 <b>agentpit.io 展位</b>领取实物<br />
            ✅ 会员已自动开通{a?.member_months ? `（${a.member_months} 个月）` : ''}<br />
            📧 账号密码已发到你的邮箱，回家电脑上 <a href="https://hunter.agentpit.io" style={{ color: THEME }}>hunter.agentpit.io</a> 继续用
          </div>
        </Card>

        <div style={{ fontSize: 11, color: INK_F, marginTop: 14, textAlign: 'center', lineHeight: 1.6 }}>{DISCLAIMER}</div>
      </div>
    </div>
  )
}
