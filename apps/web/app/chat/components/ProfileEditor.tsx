'use client'

// 我的画像 —— 设置投资偏好 + 查看/清空系统浓缩的记忆
//
// 设计原则:每一项都要能真实影响 AI 的回答,不做摆设项;全部可跳过,不做必填长表单。
// 画像会作为 system prompt 随每条消息带给模型(见 web/app/api/opencode BFF)。

import { useCallback, useEffect, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import {
  getProfile, saveProfile, getMemory, clearMemory,
  SECTOR_PRESETS, TABOO_PRESETS,
  type Profile, type ProfileOptions, type MemoryResp,
} from '../lib/profileClient'

interface Props {
  onClose: () => void
  /** 首次引导模式:精简为 3 步,可跳过 */
  wizard?: boolean
}

const DRAWDOWNS = [10, 20, 30]

export default function ProfileEditor({ onClose, wizard = false }: Props) {
  const [p, setP] = useState<Profile | null>(null)
  const [opt, setOpt] = useState<ProfileOptions | null>(null)
  const [mem, setMem] = useState<MemoryResp | null>(null)
  const [step, setStep] = useState(0)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [toast, setToast] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await getProfile()
      setP(d.profile)
      setOpt(d.options)
      if (!wizard) setMem(await getMemory())
    } catch (e: any) {
      setErr(e?.message || '加载失败')
    }
  }, [wizard])

  useEffect(() => {
    void load()
  }, [load])

  const label = (k: string) => opt?.labels?.[k] || k
  const set = (patch: Partial<Profile>) => setP((prev) => (prev ? { ...prev, ...patch } : prev))
  const toggleIn = (arr: string[], v: string) =>
    arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]

  const persist = async (extra: Partial<Profile> = {}, close = false) => {
    if (!p || saving) return
    setSaving(true)
    try {
      const d = await saveProfile({ ...p, ...extra })
      setP(d.profile)
      setToast(d.msg || '已保存')
      setTimeout(() => setToast(''), 2200)
      if (close) onClose()
    } catch (e: any) {
      setErr(e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const wipe = async () => {
    if (!confirm('清空系统记住的内容?画像设置不受影响。')) return
    try {
      await clearMemory()
      setMem(await getMemory())
      setToast('记忆已清空')
      setTimeout(() => setToast(''), 2200)
    } catch (e: any) {
      setErr(e?.message || '操作失败')
    }
  }

  const card: React.CSSProperties = {
    borderTop: `1px solid ${HUNTER.LINE}`, padding: '14px 18px',
  }
  const chip = (active: boolean): React.CSSProperties => ({
    padding: '6px 12px', borderRadius: 16, fontSize: 12.5, cursor: 'pointer',
    border: `1px solid ${active ? HUNTER.THEME : HUNTER.LINE}`,
    background: active ? HUNTER.THEME : HUNTER.PAPER,
    color: active ? '#fff' : HUNTER.INK_S,
  })
  const rowTitle: React.CSSProperties = { fontSize: 13.5, fontWeight: 600, color: HUNTER.INK, marginBottom: 3 }
  const rowHint: React.CSSProperties = { fontSize: 11, color: HUNTER.INK_F, marginBottom: 9, lineHeight: 1.6 }

  if (!p || !opt) {
    return (
      <Shell onClose={onClose}>
        <div style={{ padding: 40, textAlign: 'center', color: HUNTER.INK_F, fontSize: 13 }}>
          {err || '加载中…'}
        </div>
      </Shell>
    )
  }

  // ── 首次引导:3 步,每步都能跳过 ──
  if (wizard) {
    const steps = [
      {
        title: '你的风险偏好?',
        hint: '影响回答里对风险与弹性的侧重',
        body: (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {opt.risk_styles.map((v) => (
              <button key={v} onClick={() => set({ risk_style: v })} style={chip(p.risk_style === v)}>
                {label(v)}
              </button>
            ))}
          </div>
        ),
      },
      {
        title: '你主要看哪些市场和行业?',
        hint: '决定默认查哪个市场、推荐时优先哪些板块',
        body: (
          <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              {opt.markets.map((v) => (
                <button key={v} onClick={() => set({ markets: toggleIn(p.markets, v) })} style={chip(p.markets.includes(v))}>
                  {label(v)}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {SECTOR_PRESETS.map((v) => (
                <button key={v} onClick={() => set({ sectors: toggleIn(p.sectors, v) })} style={chip(p.sectors.includes(v))}>
                  {v}
                </button>
              ))}
            </div>
          </>
        ),
      },
      {
        title: '希望回答多详细?',
        hint: '直接控制回答长度',
        body: (
          <div style={{ display: 'flex', gap: 8 }}>
            {opt.verbosity.map((v) => (
              <button key={v} onClick={() => set({ verbosity: v })} style={chip(p.verbosity === v)}>
                {label(v)}
              </button>
            ))}
          </div>
        ),
      },
    ]
    const cur = steps[step]
    const last = step === steps.length - 1
    return (
      <Shell onClose={() => void persist({ onboarded: true }, true)}>
        <div style={{ padding: '4px 18px 0', fontSize: 11, color: HUNTER.INK_F }}>
          {step + 1} / {steps.length} · 全部可跳过,之后随时能改
        </div>
        <div style={{ padding: '14px 18px 4px' }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>{cur.title}</div>
          <div style={rowHint}>{cur.hint}</div>
          {cur.body}
        </div>
        <div style={{ display: 'flex', gap: 10, padding: '18px' }}>
          <button
            onClick={() => (last ? void persist({ onboarded: true }, true) : setStep(step + 1))}
            style={{ flex: 1, padding: '10px 0', background: HUNTER.PAPER, color: HUNTER.INK_S, border: `1px solid ${HUNTER.LINE}`, borderRadius: 8, fontSize: 13, cursor: 'pointer' }}
          >
            跳过
          </button>
          <button
            disabled={saving}
            onClick={() => (last ? void persist({ onboarded: true }, true) : setStep(step + 1))}
            style={{ flex: 2, padding: '10px 0', background: HUNTER.THEME, color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}
          >
            {last ? (saving ? '保存中…' : '完成') : '下一步'}
          </button>
        </div>
      </Shell>
    )
  }

  // ── 完整编辑 ──
  const syms = mem?.memory?.mentioned_symbols || []
  const topics = mem?.memory?.recurring_topics || []
  const positions = mem?.memory?.stated_positions || []

  return (
    <Shell onClose={onClose} title="我的画像">
      {toast && (
        <div style={{ margin: '0 18px 8px', padding: '8px 10px', background: '#f2f8f2', border: '1px solid #cfe5cf', borderRadius: 8, fontSize: 12, color: '#2c6b2c' }}>
          {toast}
        </div>
      )}
      {err && (
        <div style={{ margin: '0 18px 8px', padding: '8px 10px', background: '#fff4f4', border: '1px solid #f3c9c9', borderRadius: 8, fontSize: 12, color: '#b23' }}>
          {err}
        </div>
      )}
      <div style={{ padding: '0 18px 8px', fontSize: 12, color: HUNTER.INK_F, lineHeight: 1.7 }}>
        这些设置会在每次对话时告诉 AI,让它按你的习惯调整侧重与详略。全部可留空。
      </div>

      <div style={card}>
        <div style={rowTitle}>风险偏好</div>
        <div style={rowHint}>保守型多提示风险与回撤;积极型多讲弹性与催化剂</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {opt.risk_styles.map((v) => (
            <button key={v} onClick={() => set({ risk_style: p.risk_style === v ? '' : v })} style={chip(p.risk_style === v)}>{label(v)}</button>
          ))}
        </div>
        <div style={rowTitle}>可接受回撤</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {DRAWDOWNS.map((v) => (
            <button key={v} onClick={() => set({ max_drawdown: p.max_drawdown === v ? null : v })} style={chip(p.max_drawdown === v)}>{v}%</button>
          ))}
          <button onClick={() => set({ max_drawdown: null })} style={chip(p.max_drawdown === null)}>不设限</button>
        </div>
      </div>

      <div style={card}>
        <div style={rowTitle}>持有周期</div>
        <div style={rowHint}>短线侧重技术面与资金流;长线侧重财报与行业格局</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {opt.horizons.map((v) => (
            <button key={v} onClick={() => set({ horizon: p.horizon === v ? '' : v })} style={chip(p.horizon === v)}>{label(v)}</button>
          ))}
        </div>
      </div>

      <div style={card}>
        <div style={rowTitle}>关注市场</div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          {opt.markets.map((v) => (
            <button key={v} onClick={() => set({ markets: toggleIn(p.markets, v) })} style={chip(p.markets.includes(v))}>{label(v)}</button>
          ))}
        </div>
        <div style={rowTitle}>关注行业</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 12 }}>
          {SECTOR_PRESETS.map((v) => (
            <button key={v} onClick={() => set({ sectors: toggleIn(p.sectors, v) })} style={chip(p.sectors.includes(v))}>{v}</button>
          ))}
        </div>
        <div style={rowTitle}>市值偏好</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {opt.cap_prefs.map((v) => (
            <button key={v} onClick={() => set({ cap_pref: p.cap_pref === v ? '' : v })} style={chip(p.cap_pref === v)}>{label(v)}</button>
          ))}
        </div>
      </div>

      <div style={card}>
        <div style={rowTitle}>看重什么(按顺序点选)</div>
        <div style={rowHint}>决定回答里各维度的篇幅。已选:{p.weight_order.map(label).join(' > ') || '未设置'}</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {opt.weights.map((v) => (
            <button key={v} onClick={() => set({ weight_order: toggleIn(p.weight_order, v) })} style={chip(p.weight_order.includes(v))}>{label(v)}</button>
          ))}
        </div>
        <div style={rowTitle}>回答详细程度</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {opt.verbosity.map((v) => (
            <button key={v} onClick={() => set({ verbosity: p.verbosity === v ? '' : v })} style={chip(p.verbosity === v)}>{label(v)}</button>
          ))}
        </div>
      </div>

      <div style={card}>
        <div style={rowTitle}>回避</div>
        <div style={rowHint}>出现这类标的时会主动提示</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {TABOO_PRESETS.map((v) => (
            <button key={v} onClick={() => set({ taboos: toggleIn(p.taboos, v) })} style={chip(p.taboos.includes(v))}>{v}</button>
          ))}
        </div>
      </div>

      {/* 系统浓缩的记忆 —— 必须可见可清空,这是隐私底线 */}
      <div style={card}>
        <div style={rowTitle}>系统记住的内容</div>
        <div style={rowHint}>
          从你的对话里自动提炼,只记事实不做推断。已浓缩 {mem?.session_count ?? 0} 个会话。
        </div>
        {syms.length === 0 && topics.length === 0 && positions.length === 0 ? (
          <div style={{ fontSize: 12, color: HUNTER.INK_F }}>还没有记录 —— 多聊几次就有了</div>
        ) : (
          <div style={{ fontSize: 12, color: HUNTER.INK_S, lineHeight: 1.9 }}>
            {syms.length > 0 && (
              <div>常看:{syms.slice(0, 8).map((s) => `${s.name || s.code}(${s.count})`).join('、')}</div>
            )}
            {topics.length > 0 && <div>常问:{topics.slice(0, 6).map((t) => t.topic).join('、')}</div>}
            {positions.length > 0 && (
              <div>你提过的持仓:{positions.slice(0, 5).map((x) => x.symbol).join('、')} <span style={{ color: HUNTER.INK_F }}>(用户自述)</span></div>
            )}
          </div>
        )}
        <button onClick={wipe} style={{ marginTop: 10, background: 'none', border: `1px solid ${HUNTER.LINE}`, color: '#c0392b', fontSize: 12, borderRadius: 6, padding: '5px 12px', cursor: 'pointer' }}>
          清空记忆
        </button>
      </div>

      <div style={{ display: 'flex', gap: 10, padding: 18, borderTop: `1px solid ${HUNTER.LINE}` }}>
        <button onClick={onClose} style={{ flex: 1, padding: '11px 0', background: HUNTER.PAPER, color: HUNTER.INK_S, border: `1px solid ${HUNTER.LINE}`, borderRadius: 9, fontSize: 14, cursor: 'pointer' }}>
          关闭
        </button>
        <button disabled={saving} onClick={() => void persist({}, false)} style={{ flex: 2, padding: '11px 0', background: HUNTER.THEME, color: '#fff', border: 'none', borderRadius: 9, fontSize: 14, fontWeight: 600, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </Shell>
  )
}

function Shell({ children, onClose, title }: { children: React.ReactNode; onClose: () => void; title?: string }) {
  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(20,15,8,.45)', zIndex: 520, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ background: HUNTER.PAPER, borderRadius: 14, width: '100%', maxWidth: 560, maxHeight: '88vh', overflowY: 'auto', border: `1px solid ${HUNTER.LINE}`, boxShadow: '0 12px 40px rgba(30,20,10,.22)' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', padding: '16px 18px 8px' }}>
          <div style={{ flex: 1, fontSize: 16, fontWeight: 700, color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>
            {title || '完善你的偏好'}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 22, color: HUNTER.INK_F, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        {children}
      </div>
    </div>
  )
}
