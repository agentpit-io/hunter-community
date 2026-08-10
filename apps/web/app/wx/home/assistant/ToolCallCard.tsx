import React, { useState } from 'react'
import { T } from './tokens'
import type { ToolBundle, ToolItem } from './types'

export function ToolCallCard({ bundle }: { bundle: ToolBundle }) {
  const [expanded, setExpanded] = useState(bundle.status === 'error')

  const running = bundle.tools.filter(t => t.status === 'running').length
  const done = bundle.tools.filter(t => t.status === 'ok').length
  const err = bundle.tools.filter(t => t.status === 'error').length
  const totalDuration = bundle.tools.reduce((s, t) => s + (t.duration_ms || 0), 0)

  // Level 1: 全部 running（进行中）
  if (bundle.status === 'pending' && running === bundle.tools.length) {
    return (
      <BundleShell>
        <div style={{ fontSize: 12, color: T.INK_S, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Spinner /> 正在思考 · {bundle.tools.length} 个专家分析中
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            {bundle.tools.map(t => (
              <span key={t.tool_id} style={{
                width: 6, height: 6, borderRadius: '50%',
                background: t.status === 'running' ? T.COPPER : t.status === 'ok' ? T.DN : T.UP,
                animation: t.status === 'running' ? 'pulseDot 1.4s infinite ease-in-out' : 'none',
              }} />
            ))}
          </div>
        </div>
        <style>{`@keyframes pulseDot {0%,100%{opacity:.3}50%{opacity:1}}`}</style>
      </BundleShell>
    )
  }

  // Level 2/3: 完成态（折叠 or 展开）
  return (
    <BundleShell>
      <div
        style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, cursor: 'pointer' }}
        onClick={() => setExpanded(e => !e)}
      >
        <span style={{ color: err ? T.UP : T.DN, fontSize: 13 }}>{err ? '⚠' : '✓'}</span>
        <span style={{ color: T.INK_S }}>
          已完成 {done + err} 项分析 · 用时 {(totalDuration / 1000).toFixed(1)}s
          {err > 0 && <span style={{ color: T.UP, marginLeft: 4 }}>· {err} 失败</span>}
        </span>
        <span style={{ marginLeft: 'auto', color: T.INK_F, fontSize: 11 }}>
          {expanded ? '收起 ▴' : '展开 ▾'}
        </span>
      </div>
      {expanded && (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {bundle.reason && (
            <div style={{ fontSize: 11, color: T.INK_F, fontStyle: 'italic' }}>
              调度理由：{bundle.reason}
            </div>
          )}
          {bundle.tools.map(t => (
            <ToolItemCard key={t.tool_id} item={t} />
          ))}
        </div>
      )}
    </BundleShell>
  )
}

function BundleShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      margin: '10px 0', padding: '10px 12px',
      background: T.TOOL_BG, border: `1px solid ${T.TOOL_LN}`, borderRadius: 10,
    }}>{children}</div>
  )
}

function Spinner() {
  return (
    <span style={{
      display: 'inline-block', width: 12, height: 12, borderRadius: '50%',
      border: `2px solid ${T.COPPER}`, borderTopColor: 'transparent',
      animation: 'spin .8s linear infinite',
    }}>
      <style>{`@keyframes spin {to{transform:rotate(360deg)}}`}</style>
    </span>
  )
}

function ToolItemCard({ item }: { item: ToolItem }) {
  const ok = item.status === 'ok'
  const running = item.status === 'running'
  return (
    <div style={{
      background: T.PAPER, border: `1px solid ${T.LINE}`, borderRadius: 8,
      padding: '8px 10px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: T.INK }}>{item.display_name}</span>
        <span style={{ fontSize: 10, color: T.INK_F }}>
          {running ? (item.progress != null ? `${item.progress}%` : '运行中…')
            : ok ? `${((item.duration_ms || 0) / 1000).toFixed(1)}s` : '失败'}
        </span>
      </div>
      {running && item.progress_detail && (
        <div style={{ fontSize: 11, color: T.INK_F }}>{item.progress_detail}</div>
      )}
      {ok && item.summary && (
        <div style={{ fontSize: 11.5, color: T.INK_S, lineHeight: 1.55 }}>
          {renderSummary(item.name, item.summary)}
        </div>
      )}
      {!ok && !running && item.error && (
        <div style={{ fontSize: 11.5, color: T.UP }}>
          {item.error.code}: {item.error.message}
        </div>
      )}
    </div>
  )
}

function renderSummary(name: string, s: Record<string, unknown>): React.ReactNode {
  // 针对不同 tool 挑关键字段展示，避免全塞 JSON
  if (name === 'research') {
    const conc = String(s.conclusion || '')
    const kps = Array.isArray(s.key_points) ? (s.key_points as string[]) : []
    return (
      <>
        {conc && <div style={{ fontWeight: 500 }}>结论：{conc}</div>}
        {kps.slice(0, 3).map((k, i) => <div key={i}>· {k}</div>)}
      </>
    )
  }
  if (name === 'skill') {
    const sn = String(s.name || '')
    const desc = String(s.description || '')
    return (
      <>
        <div style={{ fontWeight: 500 }}>📋 已加载 Skill：{sn}</div>
        {desc && <div style={{ fontSize: 11, color: T.INK_F, marginTop: 2 }}>{desc}</div>}
      </>
    )
  }
  if (name === 'scout') {
    const cnt = typeof s.count === 'number' ? s.count : 0
    const days = typeof s.period_days === 'number' ? s.period_days : 0
    const op = String(s.opinion || '')
    return (
      <>
        <div style={{ fontWeight: 500 }}>近 {days} 天 · 捕获 {cnt} 条关键事件</div>
        {op && <div>· {op.slice(0, 100)}</div>}
      </>
    )
  }
  if (name === 'quant_predict') {
    const tr = String(s.trend || '')
    const pct = s.pct_predicted
    const rating = String(s.rating || '')
    const horizon = typeof s.horizon_days === 'number' ? s.horizon_days : String(s.horizon_days ?? '')
    return (
      <div>
        {horizon}日预测 · {rating} · {tr === 'up' ? '看涨' : tr === 'down' ? '看空' : '中性'}
        {typeof pct === 'number' && (
          <span style={{ color: pct >= 0 ? T.UP : T.DN, marginLeft: 6 }}>
            {pct >= 0 ? '+' : ''}{pct}%
          </span>
        )}
      </div>
    )
  }
  if (name === 'hold_judge') {
    const dec = String(s.decision || '')
    const conf = typeof s.confidence === 'number' ? s.confidence : 0
    const rsn = String(s.key_reason || '')
    return (
      <>
        <div style={{ fontWeight: 600, color: dec === 'SELL' ? T.UP : dec === 'BUY' ? T.DN : T.INK }}>
          {dec} · 置信度 {(conf * 100).toFixed(0)}%
        </div>
        {rsn && <div>· {rsn.slice(0, 150)}</div>}
      </>
    )
  }
  if (name === 'event_interpret') {
    const op = String(s.opinion || '')
    const impact = String(s.impact || 'neutral')
    const color = impact === 'positive' ? T.DN : impact === 'negative' ? T.UP : T.INK
    return (
      <>
        <div style={{ fontWeight: 500, color }}>
          {impact === 'positive' ? '📈 利好' : impact === 'negative' ? '📉 利空' : '⚪ 中性'}
        </div>
        {op && <div>· {op.slice(0, 150)}</div>}
      </>
    )
  }
  if (name === 'get_quote') {
    const nm = String(s.name ?? '')
    const cd = String(s.code ?? '')
    const pr = String(s.price ?? '')
    const cp = typeof s.change_pct === 'number' ? (s.change_pct as number) : null
    return (
      <div>
        {nm}（{cd}）· 现价 {pr}
        {cp != null && (
          <span style={{ color: cp >= 0 ? T.UP : T.DN, marginLeft: 6 }}>
            {cp >= 0 ? '+' : ''}{cp}%
          </span>
        )}
      </div>
    )
  }
  // 兜底：显示前 3 个字段
  const entries = Object.entries(s).slice(0, 3)
  const text = entries.map(([k, v]) => `${k}: ${String(v).slice(0, 40)}`).join(' · ')
  return <div>{text}</div>
}
