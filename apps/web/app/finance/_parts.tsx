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
import { useState } from 'react'
import { AlertTriangle, Inbox, Loader2, RotateCw } from 'lucide-react'
import { Button, Note } from './_ui'

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

/** 两选一 / 三选一的小分段控件（原型 `.seg`）。
 *  `disabledValues` 用来把**个别**选项画灰（如天花板只到 observe 时的 paper）——
 *  灰掉的点不动，服务端另有一道 400 兜底（显示与服务端两道一起拦）。 */
export function Seg<T extends string>({ options, value, onChange, disabled, disabledValues }: {
  options: { v: T; label: string }[]; value: T; onChange: (v: T) => void
  disabled?: boolean; disabledValues?: string[]
}) {
  return (
    <div className="inline-flex rounded-[10px] border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
      {options.map(o => {
        const on = o.v === value
        const off = !!disabled || (disabledValues || []).indexOf(o.v) >= 0
        return (
          <button key={o.v} disabled={off} onClick={() => onChange(o.v)}
            className="px-3 py-1.5 text-xs font-semibold whitespace-nowrap"
            style={{
              background: on ? 'rgba(176,106,50,.12)' : 'var(--bg-card)',
              color: on ? '#8A5A18' : 'var(--text-muted)',
              cursor: off ? 'not-allowed' : 'pointer',
              opacity: off && !on ? 0.5 : 1,
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

// ════════════════════════════════════════════════════════════════════════
// R21 · 经验库开关面板（成长页 ④ 与向导第 7 步共用）
// ════════════════════════════════════════════════════════════════════════
//
// 三条硬规矩（任务书 R21 §四）：
//   1. **取值全部来自后端** `GET /v1/fin/runtime` —— 前端不写死任何枚举（R9 纪律）；
//   2. **灰 = 有原因**：天花板为关时把开关画灰，旁边写清**为什么灰**；
//   3. **每次改动必填理由** —— 它要进服务端的流水账，界面不许绕过。
//
// 「实盘下单」**永远只读**（硬开关连界面入口都没有，服务端会 400）。
// 「自动生效」自 L14（2026-10-05）起**是可点的真开关**（与经验库同款），不再是只读行 ——
// 文案必须说清「只对收紧方向自动放行；放宽仍要人点确认」（`meta.auto_apply.desc` 由后端给）。

export type ModeOption = { value: string; label: string; full: string; allowed: boolean }
/** 后端的**展示表**（`GET /v1/fin/runtime` 的 `meta`）—— 枚举与中文名全在这里，前端不写死。 */
export type SwitchMeta = {
  memory_enabled: {
    label: string; on_label: string; off_label: string; on_text: string; off_text: string
    desc: string; not_auto_trade: string; locked: string
  }
  evolution_mode: {
    label: string; options: ModeOption[]; locked: string; ceiling_note: string | null
  }
  auto_apply: {
    label: string; on_label: string; off_label: string; on_text: string; off_text: string
    desc: string; not_auto_trade: string; locked: string
  }
  live_order_enabled: { label: string; on_text: string; off_text: string }
  hard_note: string
}

export type RuntimeState = {
  memory_enabled: boolean
  evolution_mode: string
  evolution_mode_requested: string
  degraded_reason: string | null
  auto_apply: boolean
  live_order_enabled: boolean
  project_id?: string | null
  ceiling?: { memory_enabled: boolean; evolution_mode: string; auto_apply: boolean }
  selected?: { memory_enabled: boolean | null; evolution_mode: string | null; auto_apply: boolean | null }
  can_change?: { memory_enabled: boolean; evolution_mode: boolean; auto_apply: boolean }
  meta?: SwitchMeta
}

/** 某个模式的长说明 —— **查后端给的展示表**，查不到就原样回值（不猜中文名）。 */
function modeFull(meta: SwitchMeta | undefined, v: string): string {
  const o = meta?.evolution_mode.options.find(x => x.value === v)
  return o ? o.full : v
}

function Toggle({ on, disabled, busy, onClick, labelOn, labelOff }: {
  on: boolean; disabled?: boolean; busy?: boolean; onClick: () => void
  labelOn: string; labelOff: string
}) {
  return (
    <button onClick={onClick} disabled={disabled || busy} aria-pressed={on}
      className="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold whitespace-nowrap"
      style={{
        borderColor: on ? 'rgba(63,107,64,.35)' : 'var(--border)',
        background: on ? 'rgba(63,107,64,.10)' : 'var(--bg-card)',
        color: on ? 'var(--green)' : 'var(--text-muted)',
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}>
      <span className="inline-block rounded-full"
        style={{ width: 26, height: 14, background: on ? 'rgba(63,107,64,.35)' : 'rgba(122,111,99,.28)', position: 'relative' }}>
        <span className="inline-block rounded-full"
          style={{ width: 10, height: 10, background: '#fff', position: 'absolute', top: 2, left: on ? 14 : 2, transition: 'left .15s' }} />
      </span>
      {on ? labelOn : labelOff}
    </button>
  )
}

export function RuntimeSwitchPanel({
  rt, memoryOn, mode, autoApplyOn, onChange, editable, busy, loadFailed, extra,
}: {
  rt: RuntimeState | null
  /** 界面上的当前取值（页面持有；初值来自 `rt`）。 */
  memoryOn: boolean
  mode: string
  /** 「自动生效」在界面上的当前取值（初值来自 `rt`）。 */
  autoApplyOn: boolean
  /** 用户确认改动时回调（**理由必填**，由面板收集）。 */
  onChange: (key: 'memory_enabled' | 'evolution_mode' | 'auto_apply', value: any, reason: string) => void
  /** 允许操作吗（天花板为关时传 `false`，开关画灰）。 */
  editable: boolean
  busy?: boolean
  /** 后端读取失败了吗（真失败，不是还在加载）—— 显式传，才分得清「加载中」与「读不到」。 */
  loadFailed?: boolean
  extra?: ReactNode
}) {
  const [pending, setPending] = useState<{ key: 'memory_enabled' | 'evolution_mode' | 'auto_apply'; value: any; label: string } | null>(null)
  const [reason, setReason] = useState('')

  if (!rt) {
    return (
      <div className="p-4">
        {loadFailed
          ? <Note tone="warn">运行设置读取失败 —— 拿不到服务端的开关状态，这里不显示任何猜测值。</Note>
          : <LoadingCard title="正在读取运行设置…" />}
      </div>
    )
  }
  const meta = rt.meta
  if (!meta) {
    // 读到了对象却没有展示表 = 后端契约不对（R9 的「不猜不编」）。
    return <div className="p-4"><Note tone="warn">运行设置读取失败（后端没有返回展示表）—— 不显示任何猜测值。</Note></div>
  }

  const canMem = rt.can_change?.memory_enabled !== false
  const canMode = rt.can_change?.evolution_mode !== false
  const canAuto = rt.can_change?.auto_apply !== false
  const notAllowed = meta.evolution_mode.options.filter(o => !o.allowed).map(o => o.value)

  const ask = (key: 'memory_enabled' | 'evolution_mode' | 'auto_apply', value: any, label: string) => {
    setReason('')
    setPending({ key, value, label })
  }
  const confirm = () => {
    if (!pending || !reason.trim()) return
    onChange(pending.key, pending.value, reason.trim())
    setPending(null)
    setReason('')
  }
  const labelOf = (key: 'memory_enabled' | 'evolution_mode' | 'auto_apply') =>
    key === 'memory_enabled' ? meta.memory_enabled.label
      : key === 'evolution_mode' ? meta.evolution_mode.label
        : meta.auto_apply.label

  return (
    <div className="p-4 flex flex-col gap-4">
      {/* ① 让系统攒经验（经验库） */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.memory_enabled.label}</div>
          <Toggle on={memoryOn} disabled={!editable || !canMem} busy={busy}
            labelOn={meta.memory_enabled.on_label} labelOff={meta.memory_enabled.off_label}
            onClick={() => ask('memory_enabled', !memoryOn,
              !memoryOn ? meta.memory_enabled.on_label : meta.memory_enabled.off_label)} />
        </div>
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{meta.memory_enabled.desc}</div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{meta.memory_enabled.not_auto_trade}</div>
        {!canMem && <Note tone="warn">{meta.memory_enabled.locked}</Note>}
      </div>

      {/* ② 学习强度（生效模式）—— 选项、短标签、长说明全部来自后端展示表 */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.evolution_mode.label}</div>
          <Seg
            options={meta.evolution_mode.options.map(o => ({ v: o.value, label: o.label }))}
            value={mode}
            disabled={!editable || !canMode || busy}
            disabledValues={notAllowed}
            onChange={(v) => ask('evolution_mode', v,
              meta.evolution_mode.options.find(o => o.value === v)?.label || v)}
          />
        </div>
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {meta.evolution_mode.options.map(o => <span key={o.value}>{o.full}<br /></span>)}
        </div>
        {!canMode && <Note tone="warn">{meta.evolution_mode.locked}</Note>}
        {canMode && meta.evolution_mode.ceiling_note && (
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{meta.evolution_mode.ceiling_note}</div>
        )}
      </div>

      {/* ③ 自动生效（AI 自己用上验证通过的改进）—— 与经验库同款的可点开关（L14）；
             文案里的「只对收紧方向放行」由后端 `meta.auto_apply.desc` 给，前端不写死。 */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{meta.auto_apply.label}</div>
          <Toggle on={autoApplyOn} disabled={!editable || !canAuto} busy={busy}
            labelOn={meta.auto_apply.on_label} labelOff={meta.auto_apply.off_label}
            onClick={() => ask('auto_apply', !autoApplyOn,
              !autoApplyOn ? meta.auto_apply.on_label : meta.auto_apply.off_label)} />
        </div>
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{meta.auto_apply.desc}</div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{meta.auto_apply.not_auto_trade}</div>
        {!canAuto && <Note tone="warn">{meta.auto_apply.locked}</Note>}
      </div>

      {/* 确认条：理由必填（要进服务端流水账） */}
      {pending && (
        <div className="rounded-xl px-3.5 py-3 flex flex-col gap-2"
          style={{ background: 'rgba(176,106,50,.06)', border: '1px solid rgba(176,106,50,.2)' }}>
          <div className="text-xs" style={{ color: '#6B5334' }}>
            把 <b>{labelOf(pending.key)}</b> 改成 <b>{pending.label}</b>？
            改完<b>立刻生效</b>（不用重启服务）。理由必填，会记进变更流水。
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <input value={reason} onChange={e => setReason(e.target.value)} maxLength={120}
              placeholder="为什么改（例如：先关掉，观察一周再说）"
              className="flex-1 min-w-[200px] rounded-[10px] border px-3 py-1.5 text-xs"
              style={{ borderColor: 'var(--border)', background: 'var(--bg-card)', color: 'var(--text)' }} />
            <Button size="sm" kind="pri" disabled={!reason.trim() || busy} onClick={confirm}>确认修改</Button>
            <Button size="sm" disabled={busy} onClick={() => { setPending(null); setReason('') }}>取消</Button>
          </div>
        </div>
      )}

      {/* 只读：本方案规定永远是关的那一项（实盘下单，硬开关连入口都没有） */}
      <div className="pt-1" style={{ borderTop: '1px dashed rgba(216,205,186,.75)' }}>
        <div className="flex items-baseline justify-between gap-3 py-2 text-sm">
          <span style={{ color: 'var(--text-muted)' }}>{meta.live_order_enabled.label}</span>
          <span className="text-right font-semibold" style={{ color: 'var(--text)' }}>
            {rt.live_order_enabled ? meta.live_order_enabled.on_text : meta.live_order_enabled.off_text}
          </span>
        </div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>{meta.hard_note}</div>
      </div>

      {/* 当前生效 */}
      <div className="flex items-center gap-3 flex-wrap text-xs pt-1"
        style={{ borderTop: '1px solid rgba(216,205,186,.7)', color: 'var(--text-muted)' }}>
        <span>生效模式：<b style={{ color: 'var(--text)' }}>{modeFull(meta, rt.evolution_mode)}</b></span>
        <span>请求模式：<b style={{ color: 'var(--text)' }}>{modeFull(meta, rt.evolution_mode_requested)}</b></span>
        <span>经验库：<b style={{ color: 'var(--text)' }}>{rt.memory_enabled ? meta.memory_enabled.on_text : meta.memory_enabled.off_text}</b></span>
        <span>自动生效：<b style={{ color: 'var(--text)' }}>{rt.auto_apply ? meta.auto_apply.on_text : meta.auto_apply.off_text}</b></span>
      </div>

      {rt.degraded_reason && (
        <Note tone="warn"><b>已降级运行</b>：{rt.degraded_reason}</Note>
      )}

      {extra}
    </div>
  )
}
