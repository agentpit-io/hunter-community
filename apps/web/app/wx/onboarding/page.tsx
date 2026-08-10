'use client'
// A股端新手引导: 3步 —— 选板块(存偏好) → 加自选 → 说明提醒机制
// 进入条件: 已登录且从未设置过板块偏好; 任何一步都可跳过
import { Suspense, useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'

const THEME = '#B06A32', COPPER2 = '#C89A6B'
const BG = '#F7F3EC', PAPER = '#FFFDF8', PAPER2 = '#F2EADF', LINE = '#E4D9C8'
const INK = '#2E2A24', INK_S = '#6B6055', INK_F = '#9C8F80'
const SERIF = '"Songti SC","SimSun",Georgia,serif'

const SECTORS: [string, string][] = [
  ['科技', 'tech'], ['消费', 'consumer'], ['能源', 'energy'],
  ['金融', 'finance'], ['医药', 'medical'], ['均衡', 'balanced'],
]
const RISKS: [string, string][] = [
  ['稳健为主', 'conservative'], ['平衡', 'balanced'], ['积极进取', 'aggressive'],
]

type Opp = { symbol: string; name: string; chain?: string; reason?: string }

function OnboardingInner() {
  const router = useRouter()
  const [token, setToken] = useState('')
  const [step, setStep] = useState(1)
  const [sectors, setSectors] = useState<string[]>([])
  const [risk, setRisk] = useState('balanced')
  const [saving, setSaving] = useState(false)
  const [opps, setOpps] = useState<Opp[]>([])
  const [oppsLoading, setOppsLoading] = useState(false)
  const [added, setAdded] = useState<Set<string>>(new Set())

  useEffect(() => {
    try {
      const t = localStorage.getItem('hunter_token') || ''
      if (!t) { router.replace('/wx/home'); return }
      setToken(t)
    } catch { router.replace('/wx/home') }
  }, [router])

  const authH = useCallback(() => ({
    Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
  }), [token])

  const finish = () => {
    try { localStorage.setItem('wx_onboarded', '1') } catch { /* ignore */ }
    router.replace('/wx/home?nav=discover')
  }

  // 第1步完成: 存偏好 → 拉推荐
  const saveAndNext = async () => {
    if (!sectors.length) return
    setSaving(true)
    try {
      await fetch('/api/user/preference', {
        method: 'PUT', headers: authH(),
        body: JSON.stringify({ risk_tolerance: risk, holding_period: 'medium', focus_sectors: sectors }),
      })
    } catch { /* 存失败也让用户继续, 不阻塞 */ }
    setSaving(false)
    setStep(2)
    setOppsLoading(true)
    try {
      const r = await fetch('/api/discover/opportunities', { headers: authH() })
      if (r.ok) {
        const d = await r.json()
        const list: Opp[] = []
        for (const c of (d.opportunities || d.chains || [])) {
          for (const s of (c.stocks || c.representatives || []).slice(0, 2)) {
            if (list.length < 6 && s.symbol) {
              list.push({ symbol: s.symbol, name: s.name || s.symbol, chain: c.chain_name || c.name })
            }
          }
        }
        setOpps(list)
      }
    } catch { /* ignore */ }
    setOppsLoading(false)
  }

  const addStock = async (o: Opp) => {
    if (added.has(o.symbol)) return
    try {
      const r = await fetch('/api/watchlist', {
        method: 'POST', headers: authH(),
        body: JSON.stringify({
          code: o.symbol, name: o.name, market: 'A',
          exchange: o.symbol.startsWith('6') ? 'SH' : 'SZ', asset_type: 'stock',
        }),
      })
      if (r.ok) setAdded(prev => new Set(prev).add(o.symbol))
    } catch { /* ignore */ }
  }

  const Dots = () => (
    <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 22 }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{
          width: i === step ? 20 : 7, height: 7, borderRadius: 4,
          background: i === step ? THEME : (i < step ? COPPER2 : LINE), transition: 'all .2s',
        }} />
      ))}
    </div>
  )

  return (
    <div style={{ minHeight: '100vh', background: BG, fontFamily: SERIF, maxWidth: 480, margin: '0 auto', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '18px 16px 10px', display: 'flex', alignItems: 'center' }}>
        <div style={{ flex: 1, fontSize: 13, color: INK_F }}>第 {step} / 3 步</div>
        <button onClick={finish} style={{ background: 'none', border: 'none', color: INK_F, fontSize: 13, cursor: 'pointer', fontFamily: SERIF }}>跳过</button>
      </div>

      <div style={{ flex: 1, padding: '4px 16px 20px' }}>
        <Dots />

        {step === 1 && (
          <>
            <div style={{ fontSize: 20, fontWeight: 700, color: INK, marginBottom: 6 }}>你关注哪些方向?</div>
            <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.7, marginBottom: 20 }}>
              全 A 股 5000 多只,先圈定赛道,我们才能把范围缩到几十只值得看的。可多选。
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 26 }}>
              {SECTORS.map(([label, val]) => {
                const on = sectors.includes(val)
                return (
                  <button key={val}
                    onClick={() => setSectors(p => on ? p.filter(x => x !== val) : [...p, val])}
                    style={{
                      padding: '16px 0', borderRadius: 12, cursor: 'pointer', fontSize: 15, fontFamily: SERIF,
                      border: `1.5px solid ${on ? THEME : LINE}`, background: on ? '#FBEFE4' : PAPER,
                      color: on ? THEME : INK, fontWeight: on ? 700 : 400,
                    }}>{label}</button>
                )
              })}
            </div>

            <div style={{ fontSize: 14, color: INK, marginBottom: 10, fontWeight: 600 }}>投资风格</div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
              {RISKS.map(([label, val]) => (
                <button key={val} onClick={() => setRisk(val)}
                  style={{
                    flex: 1, padding: '12px 0', borderRadius: 10, cursor: 'pointer', fontSize: 13, fontFamily: SERIF,
                    border: `1px solid ${risk === val ? THEME : LINE}`,
                    background: risk === val ? '#FBEFE4' : PAPER, color: risk === val ? THEME : INK_S,
                  }}>{label}</button>
              ))}
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <div style={{ fontSize: 20, fontWeight: 700, color: INK, marginBottom: 6 }}>先加 2-3 只自选</div>
            <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.7, marginBottom: 18 }}>
              这些是按你选的方向匹配出来的。加进自选后,AI 才能帮你盯盘和提醒。
            </div>
            {oppsLoading && <div style={{ padding: '40px 0', textAlign: 'center', color: INK_F, fontSize: 13 }}>匹配中…</div>}
            {!oppsLoading && !opps.length && (
              <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 12, padding: 18, fontSize: 13, color: INK_S, lineHeight: 1.7 }}>
                暂时没匹配到推荐,可以跳过这一步,稍后在「① 选股」里挑,或直接搜索你已经关注的股票。
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {opps.map(o => {
                const on = added.has(o.symbol)
                return (
                  <div key={o.symbol} style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 12, padding: '13px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 15, fontWeight: 600, color: INK }}>{o.name}</div>
                      <div style={{ fontSize: 11, color: INK_F, marginTop: 3 }}>{o.symbol}{o.chain ? ` · ${o.chain}` : ''}</div>
                    </div>
                    <button onClick={() => addStock(o)} disabled={on}
                      style={{
                        padding: '8px 16px', borderRadius: 8, fontSize: 13, fontFamily: SERIF,
                        border: on ? `1px solid ${LINE}` : 'none', cursor: on ? 'default' : 'pointer',
                        background: on ? PAPER2 : THEME, color: on ? INK_F : '#fff',
                      }}>{on ? '已加入' : '+ 自选'}</button>
                  </div>
                )
              })}
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <div style={{ fontSize: 20, fontWeight: 700, color: INK, marginBottom: 6 }}>以后这样用</div>
            <div style={{ fontSize: 13, color: INK_S, lineHeight: 1.7, marginBottom: 20 }}>
              不用天天盯屏幕,该看的时候手机会响。
            </div>
            <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 14, padding: '6px 14px', marginBottom: 18 }}>
              {[
                ['①', '选股', '按板块挑出值得研究的股票'],
                ['②', '盯盘', 'AI 分级预警,定时/价格/事件三种提醒'],
                ['③', '持仓', '买了之后,多空辩论帮你判断还该不该拿'],
                ['④', '挖掘', '一手情报找下一只,量化择时找入场点'],
              ].map(([n, t, d], i) => (
                <div key={n} style={{ display: 'flex', gap: 12, padding: '13px 0', borderBottom: i < 3 ? `1px solid ${PAPER2}` : 'none' }}>
                  <span style={{ fontSize: 17, color: THEME, fontWeight: 700 }}>{n}</span>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>{t}</div>
                    <div style={{ fontSize: 12, color: INK_S, marginTop: 3, lineHeight: 1.6 }}>{d}</div>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ background: '#FBEFE4', border: `1px solid ${COPPER2}`, borderRadius: 10, padding: '12px 14px', fontSize: 12, color: '#8a5a1a', lineHeight: 1.7 }}>
              💡 想让手机提醒你,记得去「② 盯盘 → 提醒设置」开启,支持开盘前简报、价格突破、突发事件三种触发。
            </div>
          </>
        )}
      </div>

      <div style={{ padding: '12px 16px calc(20px + env(safe-area-inset-bottom, 0px))', background: BG }}>
        {step === 1 && (
          <button onClick={saveAndNext} disabled={!sectors.length || saving}
            style={{
              width: '100%', padding: '15px 0', borderRadius: 12, border: 'none', fontSize: 15, fontWeight: 600,
              fontFamily: SERIF, cursor: sectors.length ? 'pointer' : 'not-allowed',
              background: sectors.length ? THEME : PAPER2, color: sectors.length ? '#fff' : INK_F,
            }}>{saving ? '保存中…' : sectors.length ? '下一步' : '请至少选一个方向'}</button>
        )}
        {step === 2 && (
          <button onClick={() => setStep(3)}
            style={{ width: '100%', padding: '15px 0', borderRadius: 12, border: 'none', background: THEME, color: '#fff', fontSize: 15, fontWeight: 600, fontFamily: SERIF, cursor: 'pointer' }}>
            {added.size ? `已加 ${added.size} 只 · 下一步` : '暂不添加 · 下一步'}
          </button>
        )}
        {step === 3 && (
          <button onClick={finish}
            style={{ width: '100%', padding: '15px 0', borderRadius: 12, border: 'none', background: THEME, color: '#fff', fontSize: 15, fontWeight: 600, fontFamily: SERIF, cursor: 'pointer' }}>
            开始使用 →
          </button>
        )}
      </div>
    </div>
  )
}

export default function WxOnboardingPage() {
  return (
    <Suspense fallback={<div style={{ minHeight: '100vh', background: BG }} />}>
      <OnboardingInner />
    </Suspense>
  )
}
