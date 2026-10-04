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

// ════════════════════════════════════════════════════════════════════════
// R21 · 经验库开关面板（成长页 ④ 与向导第 7 步共用）
// ════════════════════════════════════════════════════════════════════════
//
// 三条硬规矩（任务书 R21 §四）：
//   1. **取值全部来自后端** `GET /v1/fin/runtime` —— 前端不写死任何枚举（R9 纪律）；
//   2. **灰 = 有原因**：天花板为关时把开关画灰，旁边写清**为什么灰**；
//   3. **每次改动必填理由** —— 它要进服务端的流水账，界面不许绕过。
//
// 「自动生效 / 实盘下单」两项**永远只读**（硬开关连界面入口都没有，服务端会 400）。

export type RuntimeState = {
  memory_enabled: boolean
  evolution_mode: string
  evolution_mode_requested: string
  degraded_reason: string | null
  auto_apply: boolean
  live_order_enabled: boolean
  project_id?: string | null
  ceiling?: { memory_enabled: boolean; evolution_mode: string }
  selected?: { memory_enabled: boolean | null; evolution_mode: string | null }
  can_change?: { memory_enabled: boolean; evolution_mode: boolean }
}

/** 生效模式的中文名（**值来自后端**，前端只做展示映射）。 */
export const MODE_TEXT: Record<string, string> = {
  off: '关闭（不复盘、不提提案）',
  observe: '观察（拉行情 · 复盘 · 建经验 · 展示提案，没有委托发送能力）',
  paper: '模拟盘（独立模拟账本跑现行臂与候选臂）',
}
/** 面板上小分段的短标签（同上，只是短）。 */
export const MODE_SHORT: Record<string, string> = {
  off: '关闭', observe: '只观察', paper: '模拟验证',
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
  rt, memoryOn, mode, onChange, editable, busy, extra,
}: {
  rt: RuntimeState | null
  /** 界面上的当前取值（页面持有；初值来自 `rt`）。 */
  memoryOn: boolean
  mode: string
  /** 用户确认改动时回调（**理由必填**，由面板收集）。 */
  onChange: (key: 'memory_enabled' | 'evolution_mode', value: any, reason: string) => void
  /** 允许操作吗（天花板为关时传 `false`，开关画灰）。 */
  editable: boolean
  busy?: boolean
  extra?: ReactNode
}) {
  const [pending, setPending] = useState<{ key: 'memory_enabled' | 'evolution_mode'; value: any; label: string } | null>(null)
  const [reason, setReason] = useState('')

  if (!rt) {
    return (
      <div className="p-4">
        <LoadingCard title="正在读取运行设置…" />
      </div>
    )
  }

  const canMem = rt.can_change?.memory_enabled !== false
  const canMode = rt.can_change?.evolution_mode !== false
  const ceilingMode = rt.ceiling?.evolution_mode || 'off'
  const order: Record<string, number> = { off: 0, observe: 1, paper: 2 }

  const ask = (key: 'memory_enabled' | 'evolution_mode', value: any, label: string) => {
    setReason('')
    setPending({ key, value, label })
  }
  const confirm = () => {
    if (!pending || !reason.trim()) return
    onChange(pending.key, pending.value, reason.trim())
    setPending(null)
    setReason('')
  }

  return (
    <div className="p-4 flex flex-col gap-4">
      {/* ① 让系统攒经验（经验库） */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>① 让系统攒经验（经验库）</div>
          <Toggle on={memoryOn} disabled={!editable || !canMem} busy={busy}
            labelOn="开" labelOff="关"
            onClick={() => ask('memory_enabled', !memoryOn, !memoryOn ? '开' : '关')} />
        </div>
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          开着：每天收盘后自动复盘，把结论存下来，下次决策时参考。
          关着：它不攒新经验、也查不到旧经验（已经冻结的决策快照不受影响）。
        </div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
          ⚠️ 这不是「自动交易」开关 —— 那个在「自动交易」页。
        </div>
        {!canMem && (
          <Note tone="warn">
            部署侧已锁死（环境变量 <b>FIN_MEMORY_ENABLED=0</b>）：界面开不出经验库。
            要开，得先让部署侧把那一行改成 <b>1</b> 并重建 api。
          </Note>
        )}
      </div>

      {/* ② 学习强度（生效模式） */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>② 学习强度（生效模式）</div>
          <Seg
            options={(['off', 'observe', 'paper'] as const).map(v => ({
              v,
              label: MODE_SHORT[v] || v,
            }))}
            value={mode as any}
            disabled={!editable || !canMode || busy}
            onChange={(v) => ask('evolution_mode', v, MODE_SHORT[v] || v)}
          />
        </div>
        <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          关闭：不主动提「改策略参数」的提案。<br />
          只观察：复盘、攒经验、看得见提案，但一个单也不会多下。<br />
          模拟验证：还能在独立的模拟盘里跑两套方案对比。
        </div>
        {!canMode && (
          <Note tone="warn">
            部署侧已锁死（环境变量 <b>FIN_EVOLUTION_MODE=off</b>）：界面改不动学习强度。
            要改，得先让部署侧把那一行改成 <b>observe</b> 或 <b>paper</b> 并重建 api。
          </Note>
        )}
        {canMode && ceilingMode !== 'paper' && (
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            部署侧允许的上限：<b>{MODE_SHORT[ceilingMode] || ceilingMode}</b> —— 更高的选项界面上给不出来。
          </div>
        )}
      </div>

      {/* 确认条：理由必填（要进服务端流水账） */}
      {pending && (
        <div className="rounded-xl px-3.5 py-3 flex flex-col gap-2"
          style={{ background: 'rgba(176,106,50,.06)', border: '1px solid rgba(176,106,50,.2)' }}>
          <div className="text-xs" style={{ color: '#6B5334' }}>
            把 <b>{pending.key === 'memory_enabled' ? '经验库' : '学习强度'}</b> 改成 <b>{pending.label}</b>？
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

      {/* 只读：本方案规定永远是关的两项 */}
      <div className="pt-1" style={{ borderTop: '1px dashed rgba(216,205,186,.75)' }}>
        <div className="flex items-baseline justify-between gap-3 py-2 text-sm">
          <span style={{ color: 'var(--text-muted)' }}>自动生效</span>
          <span className="text-right font-semibold" style={{ color: 'var(--text)' }}>
            {rt.auto_apply ? '开（异常）' : '关（本方案恒为关闭）'}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-2 text-sm">
          <span style={{ color: 'var(--text-muted)' }}>实盘下单</span>
          <span className="text-right font-semibold" style={{ color: 'var(--text)' }}>
            {rt.live_order_enabled ? '开（异常）' : '无（本项目不接券商）'}
          </span>
        </div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
          这两项本方案规定不许开，界面上没有入口（服务端也会直接拒绝）。
        </div>
      </div>

      {/* 当前生效 */}
      <div className="flex items-center gap-3 flex-wrap text-xs pt-1"
        style={{ borderTop: '1px solid rgba(216,205,186,.7)', color: 'var(--text-muted)' }}>
        <span>生效模式：<b style={{ color: 'var(--text)' }}>{MODE_TEXT[rt.evolution_mode] || rt.evolution_mode}</b></span>
        <span>请求模式：<b style={{ color: 'var(--text)' }}>{MODE_TEXT[rt.evolution_mode_requested] || rt.evolution_mode_requested}</b></span>
        <span>经验库：<b style={{ color: 'var(--text)' }}>{rt.memory_enabled ? '已启用' : '未启用'}</b></span>
      </div>

      {rt.degraded_reason && (
        <Note tone="warn"><b>已降级运行</b>：{rt.degraded_reason}</Note>
      )}

      {extra}
    </div>
  )
}
