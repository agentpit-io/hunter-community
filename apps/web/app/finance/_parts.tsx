'use client'
/**
 * 智能交易板块的页级组件（一期 M6）。
 *
 * 视觉基线：`plan/ref/原型/*.html`（逐页重构，不抄代码）。设计令牌与 `_ui.tsx`
 * 同源，颜色一律走 `globals.css` 的 CSS 变量，不新引入任何依赖。
 *
 * 这里放的是**四个正文页共用**的东西：区块标题、栅格、表格、状态示意
 * （空 / 加载中 / 失败）、迷你走势图、时间线。页面自己只写业务。
 *
 * 三态（空 / 加载中 / 失败）不是装饰：`05 §3.2` M-03 的验收项写着「错误态与空态可见」。
 * 空态必须说明**为什么空**，不能只是一块白。
 */
import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, Loader2, RotateCw } from 'lucide-react'

export function SectionTitle({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-3 flex-wrap mb-3">
      <div className="min-w-0">
        <h2 className="text-base font-bold m-0" style={{ color: 'var(--text)' }}>{title}</h2>
        {sub && <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{sub}</div>}
      </div>
      {right}
    </div>
  )
}

/** 栅格：`cols` 为窄屏 / 宽屏两段（与原型 `.g.g4` / `.g.s2-1` 对应）。 */
export function Grid({ cols, children }: { cols: 1 | 2 | 3 | 4; children: ReactNode }) {
  const cls =
    cols === 1 ? 'grid gap-4'
      : cols === 2 ? 'grid gap-4 md:grid-cols-2'
        : cols === 3 ? 'grid gap-4 sm:grid-cols-2 xl:grid-cols-3'
          : 'grid gap-4 sm:grid-cols-2 xl:grid-cols-4'
  return <div className={cls}>{children}</div>
}

export function Tbl({ head, rows, foot }: { head: (string | { t: string; r?: boolean })[]; rows: ReactNode[][]; foot?: ReactNode }) {
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {head.map((h, i) => {
                const label = typeof h === 'string' ? h : h.t
                const right = typeof h !== 'string' && h.r
                return (
                  <th key={i} className={`whitespace-nowrap px-3 py-2 text-xs font-semibold ${right ? 'text-right' : 'text-left'}`}
                    style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>{label}</th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={head.length} className="px-3 py-6 text-center text-xs" style={{ color: 'var(--text-muted)' }}>没有数据</td></tr>
            )}
            {rows.map((r, i) => (
              <tr key={i}>
                {r.map((c, j) => {
                  const right = typeof head[j] !== 'string' && (head[j] as { r?: boolean }).r
                  return (
                    <td key={j} className={`px-3 py-2.5 align-top ${right ? 'text-right' : 'text-left'}`}
                      style={{ borderBottom: '1px dashed rgba(216,205,186,.75)', color: 'var(--text)' }}>{c}</td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {foot && <div className="px-3 py-2.5 text-[11px] leading-relaxed flex items-start gap-1.5 flex-wrap"
        style={{ color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}>{foot}</div>}
    </div>
  )
}

/** 占比条。`pct` 是 0~100 的数；超过 100 会夹住（显示用，不参与计算）。 */
export function Meter({ pct, tone = 'copper' }: { pct: number | null; tone?: 'copper' | 'green' | 'red' }) {
  const color = tone === 'green' ? 'var(--green)' : tone === 'red' ? 'var(--red)' : 'var(--blue)'
  const w = pct === null || !Number.isFinite(pct) ? 0 : Math.max(0, Math.min(100, pct))
  return (
    <div className="h-2 rounded-full overflow-hidden" style={{ background: 'rgba(122,111,99,.16)' }}>
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
    </div>
  )
}

/** 迷你走势图（净值 sparkline）。数据少于 2 个点就不画 —— 一个点画出来是条直线，会骗人。 */
export function Sparkline({ values, width = 120, height = 32 }: { values: number[]; width?: number; height?: number }) {
  const pts = values.filter(v => Number.isFinite(v))
  if (pts.length < 2) return <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>数据不足</span>
  const min = Math.min(...pts), max = Math.max(...pts)
  const span = max - min || 1
  const d = pts.map((v, i) => `${(i / (pts.length - 1)) * width},${height - ((v - min) / span) * (height - 4) - 2}`).join(' ')
  const up = pts[pts.length - 1] >= pts[0]
  const color = up ? 'var(--red)' : 'var(--green)'
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" style={{ display: 'block', overflow: 'visible' }}>
      <polyline points={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

/** 净值 + 基准的双线小图（我的账户页顶部）。基准没有数据时只画一条。 */
export function MiniChart({ series, width = 560, height = 120 }: { series: number[]; width?: number; height?: number }) {
  const pts = series.filter(v => Number.isFinite(v))
  if (pts.length < 2) return null
  const min = Math.min(...pts), max = Math.max(...pts)
  const span = max - min || 1
  const x = (i: number) => (i / (pts.length - 1)) * width
  const y = (v: number) => height - ((v - min) / span) * (height - 10) - 5
  const line = pts.map((v, i) => `${x(i)},${y(v)}`).join(' ')
  const area = `0,${height} ${line} ${width},${height}`
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true" style={{ display: 'block' }}>
      <polygon points={area} fill="rgba(176,106,50,.10)" />
      <polyline points={line} fill="none" stroke="var(--blue)" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  )
}

export function EmptyState({ title, desc, cta }: { title: string; desc: string; cta?: ReactNode }) {
  return (
    <div className="flex flex-col items-center text-center gap-2 py-6">
      <span className="w-12 h-12 rounded-full flex items-center justify-center" style={{ background: 'rgba(176,106,50,.11)', color: 'var(--blue)' }}>
        <Inbox className="w-5 h-5" />
      </span>
      <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{title}</div>
      <div className="text-xs leading-relaxed max-w-sm" style={{ color: 'var(--text-muted)' }}>{desc}</div>
      {cta}
    </div>
  )
}

export function ErrorState({ title, desc, onRetry }: { title: string; desc: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center text-center gap-2 py-6">
      <span className="w-12 h-12 rounded-full flex items-center justify-center" style={{ background: 'rgba(164,51,43,.09)', color: 'var(--red)' }}>
        <AlertTriangle className="w-5 h-5" />
      </span>
      <div className="text-sm font-bold" style={{ color: 'var(--text)' }}>{title}</div>
      <div className="text-xs leading-relaxed max-w-md" style={{ color: 'var(--text-muted)' }}>{desc}</div>
      {onRetry && (
        <button onClick={onRetry} className="mt-1 inline-flex items-center gap-1.5 rounded-[10px] border px-3 py-1.5 text-xs font-semibold"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-card)', color: 'var(--text)', cursor: 'pointer' }}>
          <RotateCw className="w-3.5 h-3.5" />重试
        </button>
      )}
    </div>
  )
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5 py-1" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-9 rounded-lg flex items-center px-3 gap-2" style={{ background: 'rgba(122,111,99,.07)', color: 'var(--text-muted)' }}>
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          <span className="text-xs">正在从模拟账本读取…</span>
        </div>
      ))}
    </div>
  )
}

export function LoadingCard({ title }: { title: string }) {
  return (
    <div className="text-sm flex items-center gap-2 py-2" style={{ color: 'var(--text-muted)' }}>
      <Loader2 className="w-4 h-4 animate-spin" />{title}
    </div>
  )
}

/** 两选一 / 三选一的小分段控件（原型 `.seg`）。 */
export function Seg<T extends string>({ options, value, onChange, disabled }: { options: { v: T; label: string }[]; value: T; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="inline-flex rounded-[10px] border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
      {options.map(o => {
        const on = o.v === value
        return (
          <button key={o.v} disabled={disabled} onClick={() => onChange(o.v)}
            className="px-3 py-1.5 text-xs font-semibold whitespace-nowrap"
            style={{
              background: on ? 'rgba(176,106,50,.12)' : 'var(--bg-card)',
              color: on ? '#8A5A18' : 'var(--text-muted)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              borderLeft: '1px solid var(--border)',
            }}>{o.label}</button>
        )
      })}
    </div>
  )
}

/** 时间线上的一个动作（原型 `plainTimeline`）。 */
export function TimelineItem({ time, title, chip, reason, extra }: { time: string; title: ReactNode; chip?: ReactNode; reason?: string; extra?: ReactNode }) {
  return (
    <div className="flex gap-3 py-3" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
      <div className="shrink-0 text-xs font-bold pt-0.5" style={{ color: '#8A5A18', width: 46, fontVariantNumeric: 'tabular-nums' }}>{time}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap text-sm" style={{ color: 'var(--text)' }}>
          <span className="font-semibold">{title}</span>{chip}
        </div>
        {reason && <div className="text-xs mt-1 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{reason}</div>}
        {extra}
      </div>
    </div>
  )
}

/** KPI 大数字卡（原型 `kpi`）。`value` 传字符串，`—` 由调用方决定。 */
export function Kpi({ label, value, delta, deltaTone, hint, icon }: {
  label: string; value: ReactNode; delta?: ReactNode; deltaTone?: 'up' | 'down' | 'flat'; hint?: ReactNode; icon?: ReactNode
}) {
  const dcolor = deltaTone === 'up' ? 'var(--red)' : deltaTone === 'down' ? 'var(--green)' : 'var(--text-muted)'
  return (
    <div className="rounded-2xl border p-4" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{label}</div>
        {icon && <span style={{ color: 'var(--blue)' }}>{icon}</span>}
      </div>
      <div className="text-2xl font-extrabold mt-1.5 break-all" style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      <div className="flex items-center gap-2 mt-1 flex-wrap">
        {delta !== undefined && <span className="text-sm font-bold" style={{ color: dcolor }}>{delta}</span>}
        {hint && <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{hint}</span>}
      </div>
    </div>
  )
}

/** 一条只读信息行（原型 `kv`）。 */
export function RowKV({ k, v, mono }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2 text-sm" style={{ borderBottom: '1px dashed rgba(216,205,186,.75)' }}>
      <span className="shrink-0" style={{ color: 'var(--text-muted)' }}>{k}</span>
      <span className={`text-right font-semibold break-all ${mono ? 'font-mono text-xs font-normal' : ''}`} style={{ color: 'var(--text)' }}>{v}</span>
    </div>
  )
}
