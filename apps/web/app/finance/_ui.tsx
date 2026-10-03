/**
 * 智能交易板块的共享小组件与工具（一期 M1）。
 *
 * 视觉基线：`plan/ref/原型/06-新用户向导.html`（页是视觉基线，不是代码）。
 * 设计令牌直接复用现仓 `globals.css` 的 CSS 变量（--bg / --bg-card / --border /
 * --text / --text-muted / --blue(铜) / --red(涨) / --green(跌)），与原型同一套色。
 *
 * 这里**不引入任何新依赖**：仓库实际栈是 Next 15 + React 19 + Tailwind v4 + lucide-react
 * （`apps/web/package.json`），没有 shadcn/ui，也没有 TanStack Query。任务书里写的那两项
 * 与实测不符，按仓库现状做 —— 见成果报告「偏离」一节。
 */
'use client'

import type { CSSProperties, ReactNode } from 'react'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

// ── 身份 ────────────────────────────────────────────────────────────────
// 与现仓其它页面同一口径：access token 存 localStorage['hunter_token']，
// 经 BFF `/api/*` 透传 Authorization（AuthGuard 与本文件都不自己刷新 token，
// 刷新由 AuthGuard 负责；这里只做 401 → 去登录）。
export function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

export function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

/** 统一的金融接口调用：401 去登录，其余错误抛出带状态码的 Error。 */
export async function finFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api/v1/fin${path}`, { ...init, headers: { ...authHeaders(), ...(init?.headers || {}) } })
  if (r.status === 401) {
    const err = new Error('未登录') as Error & { status?: number }
    err.status = 401
    throw err
  }
  if (!r.ok) {
    let detail = ''
    let raw: any = null
    try {
      const j = await r.json()
      raw = j?.detail
      detail = typeof j?.detail === 'string' ? j.detail : (j?.detail?.message || '')
    } catch { /* 非 JSON 错误体（nginx 502 等） */ }
    const err = new Error(detail || `请求失败（HTTP ${r.status}）`) as Error & { status?: number; detail?: any }
    err.status = r.status
    // 对象型 detail（例如风险档位的 409「放宽了哪几条」）原样带上，
    // 调用方要显示二次确认框时得有全部字段，不能只剩一句 message。
    err.detail = raw
    throw err
  }
  return r.json() as Promise<T>
}

// ── 排版小件 ────────────────────────────────────────────────────────────

export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 14, overflow: 'hidden', ...style }}>
      {children}
    </div>
  )
}

export function CardHead({ title, sub, right, icon }: { title: string; sub?: string; right?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 flex-wrap" style={{ borderBottom: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2 min-w-0">
        {icon && <span style={{ color: 'var(--blue)' }}>{icon}</span>}
        <div className="min-w-0">
          <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{title}</div>
          {sub && <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{sub}</div>}
        </div>
      </div>
      {right}
    </div>
  )
}

export function Chip({ children, tone = 'copper' }: { children: ReactNode; tone?: 'copper' | 'up' | 'down' | 'ok' | 'slate' | 'amber' }) {
  const map: Record<string, { bg: string; fg: string }> = {
    copper: { bg: 'rgba(176,106,50,.13)', fg: '#8A5A18' },
    up: { bg: 'rgba(164,51,43,.10)', fg: 'var(--red)' },
    down: { bg: 'rgba(63,107,64,.10)', fg: 'var(--green)' },
    ok: { bg: 'rgba(63,107,64,.10)', fg: 'var(--green)' },
    slate: { bg: 'rgba(122,111,99,.13)', fg: '#5C5348' },
    amber: { bg: 'rgba(186,117,23,.13)', fg: '#8A5A18' },
  }
  const c = map[tone] ?? map.copper
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
      style={{ background: c.bg, color: c.fg }}>
      {children}
    </span>
  )
}

export function Note({ children, tone = 'copper' }: { children: ReactNode; tone?: 'copper' | 'ok' | 'warn' }) {
  const map = {
    copper: { bg: 'rgba(176,106,50,.06)', bd: 'rgba(176,106,50,.2)', fg: '#6B5334' },
    ok: { bg: 'rgba(63,107,64,.07)', bd: 'rgba(63,107,64,.24)', fg: '#2F5230' },
    warn: { bg: 'rgba(164,51,43,.06)', bd: 'rgba(164,51,43,.22)', fg: '#7C2A24' },
  }[tone]
  return (
    <div className="text-xs leading-relaxed rounded-xl px-3.5 py-3" style={{ background: map.bg, border: `1px solid ${map.bd}`, color: map.fg }}>
      {children}
    </div>
  )
}

export function KV({ k, v, mono }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2 text-sm" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
      <span className="shrink-0" style={{ color: 'var(--text-muted)' }}>{k}</span>
      <span className={`text-right font-semibold break-all ${mono ? 'font-mono text-xs font-normal' : ''}`} style={{ color: 'var(--text)' }}>{v}</span>
    </div>
  )
}

export function Button({ children, onClick, kind = 'ghost', disabled, size = 'md' }: {
  children: ReactNode; onClick?: () => void; kind?: 'pri' | 'ghost' | 'warn'; disabled?: boolean; size?: 'sm' | 'md' | 'lg'
}) {
  const pad = size === 'lg' ? 'px-5 py-3 text-[15px]' : size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'
  const style: CSSProperties = kind === 'pri'
    ? { background: 'var(--blue)', borderColor: 'var(--blue)', color: '#fff' }
    : kind === 'warn'
      ? { background: 'transparent', borderColor: 'rgba(164,51,43,.35)', color: 'var(--red)' }
      : { background: 'var(--bg-card)', borderColor: 'var(--border)', color: 'var(--text)' }
  return (
    <button onClick={onClick} disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-[10px] border font-semibold whitespace-nowrap transition-colors ${pad}`}
      style={{ ...style, opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
      {children}
    </button>
  )
}

/**
 * 币种 → 符号。**符号表只有这一处**，且**不把货币符号写死在源码里** ——
 * 由 `Intl` 按 zh-CN 的货币数据给出（CNY→¥、HKD→HK$），与后端
 * `report.CURRENCY_SYMBOL` 显示同一批符号。
 *
 * 为什么要覆盖 USD：zh-CN 的 Intl 数据把美元显示成 `US$`，而产品约定是 `$`
 * （港股 `HK$` 与美元 `$` 并排时不至于混淆）。**只覆盖这一条**，其余一律信 Intl。
 * 认不出的币种返回空串 —— 不加符号，**绝不默认成人民币**。
 */
const SYMBOL_OVERRIDE: Record<string, string> = { USD: '$' }
const _symCache: Record<string, string> = {}

export function currencySymbol(currency: string | null | undefined): string {
  const c = (currency || '').toUpperCase()
  if (!c) return ''
  if (c in _symCache) return _symCache[c]
  let sym = SYMBOL_OVERRIDE[c] || ''
  if (!sym) {
    try {
      const parts = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: c }).formatToParts(0)
      sym = parts.find(p => p.type === 'currency')?.value || ''
    } catch { sym = '' }
  }
  _symCache[c] = sym
  return sym
}

/** 金额格式化（整数分位，模拟盘金额都到分）。
 *
 * 二期起**按币种出符号**（A 股 ¥、港股 HK$、美股 $）；`currency` 缺省 `CNY`，
 * 一期调用点行为逐字不变。币种缺省/认不出时**不加符号**。 */
export function money(v: number | null | undefined, currency: string = 'CNY'): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return currencySymbol(currency) + v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const s = (v * 100).toFixed(v * 100 % 1 === 0 ? 0 : 1)
  return (v > 0 ? '+' : '') + s + '%'
}

// ── 占位页（四个正文页留给 M6）────────────────────────────────────────────
// 「先做个能点的入口、后端回头补」被总控规则 §六-10 明令禁止 —— 所以这里
// 不画假卡片、不显示假数字，只如实说明这一页什么时候来、会展示什么。
export function ComingSoon({ title, purpose, milestone }: { title: string; purpose: string; milestone: string }) {
  return (
    <div className="max-w-3xl">
      <Card>
        <CardHead title={title} sub={`交付里程碑：${milestone}`} />
        <div className="p-5 flex flex-col items-center text-center gap-2.5">
          <div className="w-12 h-12 rounded-full flex items-center justify-center text-xl"
            style={{ background: 'rgba(176,106,50,.11)', color: 'var(--blue)' }}>🛠</div>
          <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>本页尚未交付</div>
          <div className="text-xs leading-relaxed max-w-md" style={{ color: 'var(--text-muted)' }}>
            {purpose}
            <br />该页排在 <b>{milestone}</b>。一期（M1）只交付「智能交易」入口、路由骨架与新用户向导——
            这里不放任何占位数字，因为还没有账本数据可展示。
          </div>
        </div>
      </Card>
    </div>
  )
}

/** 拉一次「当前项目」，供各页显示一致的状态。
 *
 * P3：把 P1 的 `markets` 数组挂到返回的 `project` 上（`project.markets`），
 * 各页据此决定**展示哪些市场** —— 不再自己拼三个市场（见 `_market.marketsOf`）。
 * 接口本来就返回 `{project, param, markets, tier_template}`，这里只是把
 * `markets` 并进 `project`，各页不用改两处取值。
 * 没有进行中的项目时 `project` 仍是 `null`。
 */
export function useCurrentProject() {
  const router = useRouter()
  const [state, setState] = useState<{ loading: boolean; project: any | null; error: string }>({ loading: true, project: null, error: '' })
  useEffect(() => {
    let alive = true
    finFetch<{ project: any | null; markets?: any[] }>('/projects/current')
      .then(d => {
        if (!alive) return
        setState({
          loading: false,
          project: d.project ? { ...d.project, markets: d.markets || [] } : null,
          error: '',
        })
      })
      .catch(e => {
        if (!alive) return
        if ((e as any)?.status === 401) { router.push('/login'); return }
        setState({ loading: false, project: null, error: e?.message || '加载失败' })
      })
    return () => { alive = false }
  }, [router])
  return state
}
