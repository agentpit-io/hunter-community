'use client'
// 功能地图 · Hunter 全部功能一览
// 数据源: /api/ax/features/all (返回 10 项全量,每项带 used + assigned 状态)
// 按 route 前缀分 3 大类展示: 持仓中心 / 研究工具 / 发现&偏好

import { useCallback, useEffect, useState } from 'react'

// ── 设计 token (与 WxAx.tsx 一致) ────────────────────────────────────────
const THEME  = '#B06A32'
const UP     = '#A4332B'
const DN     = '#2E7D32'
const GOLD   = '#C89855'
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

interface FeatureItem {
  id: string; icon: string; title: string; desc: string; route: string
  used: boolean; assigned: boolean
}
interface FeaturesAllResp {
  ok: boolean; ax_active: boolean; features: FeatureItem[]
  assigned_used_count: number; assigned_total: number; unlocked: boolean
}

// 按 route 前缀分组的元数据 (决定分组顺序与标题)
// 与底部导航的四步闭环保持一致: ①选股 ②盯盘 ③持仓 ④挖掘
const GROUP_META: { key: string; title: string; icon: string; desc: string }[] = [
  { key: 'discover',  title: '① 选股', icon: '🔎', desc: '板块偏好 · 产业链匹配,圈定值得看的方向' },
  { key: 'watchlist', title: '② 盯盘', icon: '🛡️', desc: '综合概览 · 自选股清单 · 提醒设置' },
  { key: 'holding',   title: '③ 持仓', icon: '📁', desc: '持仓报告 · 持仓研判 · 事件解读' },
  { key: 'kpred',     title: '④ 挖掘', icon: '🔬', desc: '深度研究 · 一手情报 · 量化择时' },
  { key: 'backtest',  title: '⑤ 回测', icon: '📊', desc: '我的成绩单 · 跟踪股票 · 判定参数' },
  { key: 'profile',   title: '个性化',  icon: '🎨', desc: '板块偏好与推荐策略' },
]

function getToken(): string {
  try { return localStorage.getItem('hunter_token') || '' } catch { return '' }
}
function goto(target: string) {
  console.log('[ax:map] navigate', target)
  try { window.location.assign(target) } catch { window.location.href = target }
}
function groupOf(route: string): string {
  const [t, sub] = (route || '').split(':')
  // 持仓报告/持仓研判/事件解读 已迁到 ③持仓,归组时对齐新位置
  if (t === 'watchlist' && sub === 'position') return 'holding'
  if (t === 'kpred' && (sub === 'hold' || sub === 'event')) return 'holding'
  return t || 'other'
}

export default function WxAxMap() {
  const [data, setData] = useState<FeaturesAllResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setErr('')
    const tk = getToken()
    if (!tk) { setErr('登录已失效,请回到服务号菜单重新进入'); setLoading(false); return }
    try {
      const r = await fetch('/api/ax/features/all', {
        headers: { Authorization: `Bearer ${tk}` }, cache: 'no-store',
      })
      if (r.status === 401) {
        try { localStorage.removeItem('hunter_token') } catch {}
        setErr('登录已失效,请回到服务号菜单重新进入'); return
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setData(await r.json())
    } catch (e) {
      setErr(e instanceof Error ? e.message : '加载失败')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  // 页面回前台自动刷新(用户从 wx/home 逛完回来看进度)
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', onVis)
    }
  }, [load])

  const items = data?.features ?? []
  const usedCount = data?.assigned_used_count ?? 0
  const total = data?.assigned_total ?? 4
  const unlocked = data?.unlocked ?? false

  // 按 route 前缀分组;保留 GROUP_META 定义的顺序,未列入的分组归到最后
  const grouped = GROUP_META
    .map(g => ({ ...g, items: items.filter(f => groupOf(f.route) === g.key) }))
    .filter(g => g.items.length > 0)
  const uncategorized = items.filter(f => !GROUP_META.find(g => g.key === groupOf(f.route)))
  if (uncategorized.length) grouped.push({ key: 'other', title: '其他', icon: '✨', desc: '', items: uncategorized })

  return (
    <div style={{ minHeight: '100vh', background: BG, color: INK, fontFamily: '-apple-system,"PingFang SC","Microsoft YaHei",sans-serif' }}>
      {/* ── 顶部标题栏 ── */}
      <div style={{ background: HEADER_BG, borderRadius: '0 0 18px 18px', padding: '22px 22px 22px', textAlign: 'center', position: 'relative' }}>
        <button
          onClick={() => goto('/wx/ax')}
          aria-label="返回"
          style={{ position: 'absolute', left: 14, top: 20, background: 'transparent',
                   border: 'none', color: COPPER2, fontSize: 22, padding: 4, cursor: 'pointer' }}
        >‹</button>
        <div style={{ fontFamily: SERIF, fontSize: 22, fontWeight: 700, color: COPPER2 }}>🗺 功能地图</div>
        <div style={{ fontSize: 12, color: PAPER2, marginTop: 6 }}>Hunter 全部 {items.length || 10} 项功能一览</div>
        {data && (
          <div style={{ display: 'inline-block', marginTop: 12, padding: '5px 14px',
                        borderRadius: 999, border: `1px solid ${COPPER2}`,
                        color: unlocked ? '#8FE38F' : COPPER2, fontSize: 12, letterSpacing: 1 }}>
            {unlocked
              ? `🎉 通关任务 ${usedCount}/${total} 已解锁`
              : `本次任务 ${usedCount}/${total} · 金色标记即通关项`}
          </div>
        )}
      </div>

      <div style={{ padding: '16px 16px 40px', maxWidth: 480, margin: '0 auto' }}>
        {loading && <div style={{ textAlign: 'center', color: INK_F, padding: '30px 0' }}>加载中...</div>}

        {err && (
          <div style={{ background: PAPER, border: `1px solid ${LINE}`, borderRadius: 14, padding: '18px 16px', textAlign: 'center' }}>
            <div style={{ color: UP, fontSize: 14, lineHeight: 1.7 }}>{err}</div>
            <button
              onClick={() => goto('/wx/ax')}
              style={{ marginTop: 14, padding: '10px 22px', background: THEME, color: '#fff',
                       border: 'none', borderRadius: 10, fontSize: 14, cursor: 'pointer' }}
            >返回通关页</button>
          </div>
        )}

        {!loading && !err && grouped.map(g => (
          <div key={g.key} style={{ marginBottom: 18 }}>
            {/* 分组标题 */}
            <div style={{ display: 'flex', alignItems: 'baseline', marginBottom: 10, paddingLeft: 4 }}>
              <span style={{ fontSize: 18, marginRight: 8 }}>{g.icon}</span>
              <span style={{ fontSize: 16, fontWeight: 700, color: INK }}>{g.title}</span>
              <span style={{ fontSize: 12, color: INK_F, marginLeft: 8 }}>{g.items.length} 项</span>
            </div>

            {/* 该组的功能卡片 */}
            {g.items.map((f) => {
              const target = `/wx/home?nav=${encodeURIComponent(f.route)}`
              const isAssigned = f.assigned
              const isUsed = f.used
              // 边框: assigned 时金色, used 时绿色, 否则普通
              const border = isUsed
                ? `1px solid ${DN}`
                : isAssigned
                  ? `1.5px solid ${GOLD}`
                  : `1px solid ${LINE}`
              return (
                <div key={f.id} style={{
                  background: PAPER, border, borderRadius: 12,
                  padding: '14px 14px', marginBottom: 8,
                  opacity: isUsed ? 0.85 : 1, position: 'relative',
                }}>
                  {/* Badge: 通关任务标记 */}
                  {isAssigned && !isUsed && (
                    <div style={{ position: 'absolute', top: 10, right: 10, fontSize: 10,
                                  background: GOLD, color: '#fff', padding: '2px 8px',
                                  borderRadius: 999, fontWeight: 600 }}>
                      通关任务
                    </div>
                  )}
                  {isUsed && (
                    <div style={{ position: 'absolute', top: 10, right: 10, fontSize: 10,
                                  background: DN, color: '#fff', padding: '2px 8px',
                                  borderRadius: 999, fontWeight: 600 }}>
                      ✓ 已体验
                    </div>
                  )}

                  <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                    <span style={{ fontSize: 22, marginRight: 12, width: 26, textAlign: 'center', flexShrink: 0 }}>{f.icon}</span>
                    <div style={{ flex: 1, minWidth: 0, paddingRight: isAssigned || isUsed ? 60 : 0 }}>
                      <div style={{ fontSize: 15, fontWeight: 600, color: INK, marginBottom: 3 }}>{f.title}</div>
                      <div style={{ fontSize: 12, color: INK_S, lineHeight: 1.6 }}>{f.desc}</div>
                    </div>
                  </div>

                  {/* CTA */}
                  <a
                    href={target}
                    onClick={(e) => { e.preventDefault(); goto(target) }}
                    style={{
                      display: 'block', textAlign: 'center', marginTop: 12,
                      padding: '9px 0', fontSize: 13, fontWeight: 600,
                      background: isUsed ? 'transparent' : THEME,
                      color: isUsed ? THEME : '#fff',
                      border: isUsed ? `1px solid ${THEME}` : 'none',
                      borderRadius: 8, textDecoration: 'none',
                    }}
                  >{isUsed ? '再逛一次 →' : '前往体验 →'}</a>
                </div>
              )
            })}
          </div>
        ))}

        {/* ── 底部说明 ── */}
        {!loading && !err && (
          <div style={{ background: PAPER2, borderRadius: 12, padding: '14px 16px', marginTop: 8, fontSize: 12, color: INK_S, lineHeight: 1.8 }}>
            📍 <b>通关任务</b>: 完成 {total} 项即解锁股民礼 (金色标记)<br />
            🎯 未标记为通关任务的功能也可以逛,但不计入进度<br />
            ✨ 进入任一功能自动记为已体验,回到本页刷新即可看到进度
          </div>
        )}

        {/* ── 返回按钮 ── */}
        <div style={{ marginTop: 20 }}>
          <button
            onClick={() => goto('/wx/ax')}
            style={{
              width: '100%', padding: '12px 0', background: 'transparent',
              color: THEME, border: `1.5px solid ${THEME}`, borderRadius: 10,
              fontSize: 14, fontWeight: 600, cursor: 'pointer',
            }}
          >← 返回通关页</button>
        </div>

        <div style={{ fontSize: 11, color: INK_F, marginTop: 20, textAlign: 'center', lineHeight: 1.6 }}>
          产品数据分析由 AI 生成,仅供参考,不构成任何投资建议。
        </div>
      </div>
    </div>
  )
}
