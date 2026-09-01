'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Save, Loader2, CheckCircle } from 'lucide-react'
import { IDLE_LOGOUT_KEY } from '../components/AuthGuard'

function getToken() {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}
function authHeaders(): Record<string, string> {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

const STYLE_OPTIONS   = ['价值投资', '趋势交易', '指数投资', '均衡配置', '成长投资']
const RISK_OPTIONS    = ['保守型（最大回撤 < 10%）', '稳健型（最大回撤 < 20%）', '积极型（可承受较大波动）']
const PERIOD_OPTIONS  = ['短线交易（< 1 个月）', '中线持有（1–6 个月）', '长线持有（> 6 个月）']
const MARKET_OPTIONS  = ['A股', '港股', '美股', 'A股 + 港股', '全市场']
const SECTOR_OPTIONS  = ['新能源', '消费', '医药', '科技', '金融', '能源', '地产', '军工', '基础材料', '农业']

interface Pref {
  investment_style: string
  risk_tolerance:   string
  holding_period:   string
  focus_sectors:    string[]
  market_scope:     string
  push_focus:       string
}

const IDLE_OPTIONS = [
  { v: 0,   label: '不自动登出' },
  { v: 15,  label: '15 分钟' },
  { v: 30,  label: '30 分钟' },
  { v: 60,  label: '1 小时' },
  { v: 240, label: '4 小时' },
]

export default function PreferencePage() {
  const [idleMin, setIdleMin] = useState(0)
  useEffect(() => {
    try { setIdleMin(Number(localStorage.getItem(IDLE_LOGOUT_KEY) || 0) || 0) } catch {}
  }, [])
  const router = useRouter()
  const [pref, setPref] = useState<Pref>({
    investment_style: '',
    risk_tolerance:   '',
    holding_period:   '',
    focus_sectors:    [],
    market_scope:     'A股',
    push_focus:       '',
  })
  const [loading,  setLoading]  = useState(true)
  const [saving,   setSaving]   = useState(false)
  const [saved,    setSaved]    = useState(false)
  const [err,      setErr]      = useState('')

  useEffect(() => {
    const token = getToken()
    if (!token) { router.push('/login'); return }
    fetch('/api/user/preference', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setPref(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const toggleSector = (s: string) => {
    setPref(p => ({
      ...p,
      focus_sectors: p.focus_sectors.includes(s)
        ? p.focus_sectors.filter(x => x !== s)
        : [...p.focus_sectors, s],
    }))
  }

  const handleSave = async () => {
    setSaving(true); setErr(''); setSaved(false)
    try {
      const r = await fetch('/api/user/preference', {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify(pref),
      })
      if (!r.ok) throw new Error('保存失败')
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--blue)' }} />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>投资偏好设置</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
          Hunter 会根据你的偏好，个性化分析报告和推送内容
        </p>
      </div>

      <div className="space-y-6">

        {/* 投资风格 */}
        <Section title="投资风格">
          <RadioGroup
            options={STYLE_OPTIONS}
            value={pref.investment_style}
            onChange={v => setPref(p => ({ ...p, investment_style: v }))}
          />
        </Section>

        {/* 风险偏好 */}
        <Section title="风险偏好">
          <RadioGroup
            options={RISK_OPTIONS}
            value={pref.risk_tolerance}
            onChange={v => setPref(p => ({ ...p, risk_tolerance: v }))}
          />
        </Section>

        {/* 持仓周期 */}
        <Section title="持仓周期">
          <RadioGroup
            options={PERIOD_OPTIONS}
            value={pref.holding_period}
            onChange={v => setPref(p => ({ ...p, holding_period: v }))}
          />
        </Section>

        {/* 市场范围 */}
        <Section title="关注市场">
          <RadioGroup
            options={MARKET_OPTIONS}
            value={pref.market_scope}
            onChange={v => setPref(p => ({ ...p, market_scope: v }))}
          />
        </Section>

        {/* 安全 · 闲置自动登出
            存 localStorage 不走后端:这是**这台设备上**的行为,
            不该跟着账号同步到别的机器(公司电脑想 15 分钟锁,
            家里那台不一定想)。 */}
        <Section title="闲置自动登出" subtitle="这台设备生效 · 不同步到其他设备">
          <div className="flex flex-wrap gap-2">
            {IDLE_OPTIONS.map(o => {
              const active = idleMin === o.v
              return (
                <button
                  key={o.v}
                  onClick={() => {
                    setIdleMin(o.v)
                    try {
                      if (o.v) localStorage.setItem(IDLE_LOGOUT_KEY, String(o.v))
                      else localStorage.removeItem(IDLE_LOGOUT_KEY)
                    } catch { /* 隐私模式 */ }
                  }}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors"
                  style={{
                    background:  active ? 'var(--blue)' : 'transparent',
                    color:       active ? '#fff' : 'var(--text)',
                    borderColor: active ? 'var(--blue)' : 'var(--border)',
                  }}
                >
                  {o.label}
                </button>
              )
            })}
          </div>
          <div className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
            选「不自动登出」时,登录状态会自动续期,长时间不动也不会掉线。
          </div>
        </Section>

        {/* 关注板块（多选） */}
        <Section title="关注板块" subtitle="可多选">
          <div className="flex flex-wrap gap-2">
            {SECTOR_OPTIONS.map(s => {
              const active = pref.focus_sectors.includes(s)
              return (
                <button
                  key={s}
                  onClick={() => toggleSector(s)}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors"
                  style={{
                    background:   active ? 'var(--blue)' : 'transparent',
                    color:        active ? '#fff' : 'var(--text)',
                    borderColor:  active ? 'var(--blue)' : 'var(--border)',
                  }}
                >
                  {s}
                </button>
              )
            })}
          </div>
        </Section>

        {/* 推送偏好（自由文本） */}
        <Section title="推送偏好" subtitle="可选，描述你希望 Hunter 重点关注什么">
          <textarea
            rows={3}
            value={pref.push_focus}
            onChange={e => setPref(p => ({ ...p, push_focus: e.target.value }))}
            placeholder="例如：只推送自选股异动，不需要宏观信号推送；每天收盘后推送一次即可"
            className="w-full px-3 py-2 rounded-lg text-sm border resize-none"
            style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }}
          />
        </Section>

      </div>

      {/* 保存按钮 */}
      <div className="mt-8 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
          style={{ background: 'var(--blue)', color: '#fff', opacity: saving ? 0.7 : 1 }}
        >
          {saving
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : saved
            ? <CheckCircle className="w-4 h-4" />
            : <Save className="w-4 h-4" />}
          {saving ? '保存中...' : saved ? '已保存' : '保存偏好'}
        </button>
        {err && <span className="text-sm" style={{ color: '#ef4444' }}>{err}</span>}
        {saved && <span className="text-sm" style={{ color: '#22c55e' }}>偏好已更新，下次分析和推送将自动生效</span>}
      </div>
    </div>
  )
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <div className="mb-3">
        <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{title}</span>
        {subtitle && <span className="text-xs ml-2" style={{ color: 'var(--text-muted)' }}>{subtitle}</span>}
      </div>
      {children}
    </div>
  )
}

function RadioGroup({ options, value, onChange }: {
  options: string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(opt => {
        const active = value === opt
        return (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className="px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors"
            style={{
              background:  active ? 'var(--blue)' : 'transparent',
              color:       active ? '#fff' : 'var(--text)',
              borderColor: active ? 'var(--blue)' : 'var(--border)',
            }}
          >
            {opt}
          </button>
        )
      })}
    </div>
  )
}
