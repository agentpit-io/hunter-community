'use client'

// 侧栏「能力」区 · Claude 风精简版（2026-08 方案 C）:
//   1. 顶部固定 4 张常用（2×2 网格）· 一步触达
//   2. 「查看全部工具 (N) →」一行文字链 · 点开右滑抽屉
//   3. 抽屉里 12+ SKILL 按 4 大类展示：单股 / 组合 / 决策 / 自建
//   4. 历史对话区因此获得 +400px 高度
//
// 为什么不直接列 MCP 工具名:技术上底层是 truesource_get_quote / kronos_kronos_forecast 之类,
// 用户看不懂也不知道能拿它干嘛。这里展示的是用用户语言写的能力卡,
// 点一下把提问模板填进输入框,光标停在 {股票} 上直接打名字。

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  BarChart3,
  TrendingUp,
  ClipboardList,
  SearchCode,
  PenLine,
  Scale,
  Newspaper,
  Sunrise,
  Target,
  Waves,
  Sliders,
  ChevronRight,
  X,
  Star,
  Settings2,
} from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { listSkills, type SkillItem } from '../lib/skillClient'

// SKILL key → 细线 outline 图标 · 对齐设计稿的灰色单色风格 · 后端 emoji 只作兜底
const SKILL_ICON: Record<string, LucideIcon> = {
  quote: BarChart3,
  forecast: TrendingUp,
  report: ClipboardList,
  compare: SearchCode,
  thesis: PenLine,
  debate: Scale,
  // 自选股整合（P0-P2）
  stock_news: Newspaper,
  watchlist_daily: Sunrise,
  portfolio_advice: Target,
  portfolio_stress: Waves,
  // 持仓建议 Sprint 1
  risk_profile: Sliders,
}

// 常用 4 张（硬编码 · 覆盖单股+组合+决策 3 大高频场景）
// 未来可改为按用户点击频次动态计算
const FAVORITE_KEYS = ['quote', 'watchlist_daily', 'portfolio_advice', 'portfolio_stress']

// 4 大分类 · 每个 SKILL 归到某一类（未在此列表的自动进"自建"）
const SKILL_CATEGORY: Record<string, '单股' | '组合' | '决策' | '自建'> = {
  quote: '单股',
  forecast: '单股',
  report: '单股',
  compare: '单股',
  thesis: '单股',
  stock_news: '单股',
  watchlist_daily: '组合',
  portfolio_advice: '组合',
  risk_profile: '组合',
  debate: '决策',
  portfolio_stress: '决策',
}

const CATEGORY_ORDER: Array<'单股' | '组合' | '决策' | '自建'> = ['单股', '组合', '决策', '自建']
const CATEGORY_HINT: Record<string, string> = {
  单股: '单只股票 · 速查 / 预测 / 财报 / 新闻',
  组合: '你的自选/持仓 · 日报 / 建议 / 画像',
  决策: '深度推演 · 辩论 / 情景模拟',
  自建: '你自己创建的能力',
}

interface Props {
  onPick: (tpl: string, key: string) => void
  onManage: () => void
  refreshKey?: number
}

export default function SkillPanel({ onPick, onManage, refreshKey = 0 }: Props) {
  const [items, setItems] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [drawer, setDrawer] = useState(false)

  const load = useCallback(async () => {
    try {
      const d = await listSkills()
      setItems((d.items || []).filter((x) => x.enabled))
      setFailed(false)
    } catch (e) {
      console.warn('[skill] load failed:', e)
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  // 拆分常用 vs 全部
  const favorites = useMemo(() => {
    const byKey = new Map(items.map((s) => [s.key, s]))
    return FAVORITE_KEYS.map((k) => byKey.get(k)).filter(Boolean) as SkillItem[]
  }, [items])

  const handlePick = useCallback(
    (tpl: string, key: string) => {
      setDrawer(false)
      onPick(tpl, key)
    },
    [onPick],
  )

  if (loading || failed || items.length === 0) return null

  return (
    <>
      <div style={{ borderBottom: `1px solid ${HUNTER.LINE}`, paddingBottom: 8, marginBottom: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '10px 12px 6px' }}>
          <div style={sectionLabel}>常用工具</div>
          <button onClick={onManage} style={miniLink} title="管理你的能力">
            <Settings2 size={11} strokeWidth={1.5} style={{ marginRight: 3 }} />
            管理
          </button>
        </div>

        {/* 顶部 2x2 网格 · 4 张常用 */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 6,
            padding: '0 10px',
          }}
        >
          {favorites.length === 0 && (
            <div
              style={{
                gridColumn: '1 / -1',
                padding: '6px 8px',
                fontSize: 11.5,
                color: HUNTER.INK_F,
              }}
            >
              未加载常用工具 · 点下方"查看全部"
            </div>
          )}
          {favorites.map((s) => {
            const Icon = SKILL_ICON[s.key]
            return (
              <button
                key={s.key}
                onClick={() => handlePick(s.prompt_tpl, s.key)}
                title={s.hint || s.prompt_tpl}
                style={favBtn}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = HUNTER.PANEL_2
                  e.currentTarget.style.borderColor = HUNTER.THEME
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = '#fbfaf5'
                  e.currentTarget.style.borderColor = HUNTER.LINE
                }}
              >
                <span style={favIconWrap}>
                  {Icon ? <Icon size={14} strokeWidth={1.5} /> : <span style={{ fontSize: 13 }}>{s.icon}</span>}
                </span>
                <span style={favName}>{s.name}</span>
              </button>
            )
          })}
        </div>

        {/* 全部工具入口 */}
        <button onClick={() => setDrawer(true)} style={viewAllBtn}>
          <span style={{ flex: 1, textAlign: 'left' }}>查看全部工具 ({items.length})</span>
          <ChevronRight size={13} strokeWidth={1.8} />
        </button>
      </div>

      {/* 抽屉 · 全部工具（右滑） */}
      {drawer && (
        <SkillDrawer items={items} onClose={() => setDrawer(false)} onPick={handlePick} onManage={onManage} />
      )}
    </>
  )
}

/** 全部工具抽屉 · 分类展示 · 覆盖 sidebar 之上 */
function SkillDrawer({
  items,
  onClose,
  onPick,
  onManage,
}: {
  items: SkillItem[]
  onClose: () => void
  onPick: (tpl: string, key: string) => void
  onManage: () => void
}) {
  const grouped = useMemo(() => {
    const map: Record<string, SkillItem[]> = { 单股: [], 组合: [], 决策: [], 自建: [] }
    for (const s of items) {
      const cat = SKILL_CATEGORY[s.key] || '自建'
      map[cat].push(s)
    }
    return map
  }, [items])

  return (
    <div style={mask} onClick={onClose} role="presentation">
      <div style={drawerBox} onClick={(e) => e.stopPropagation()} role="dialog">
        {/* 抽屉头 */}
        <div style={drawerHead}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 650, color: HUNTER.INK }}>全部分析工具</div>
            <div style={{ fontSize: 11.5, color: HUNTER.INK_F, marginTop: 2 }}>
              {items.length} 个能力 · 点击填入输入框
            </div>
          </div>
          <button onClick={onClose} style={closeBtn} aria-label="关闭">
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>

        {/* 分类列表 · 可滚动 */}
        <div style={drawerBody}>
          {CATEGORY_ORDER.map((cat) => {
            const list = grouped[cat] || []
            if (list.length === 0) return null
            return (
              <section key={cat} style={{ marginBottom: 18 }}>
                <div style={catHead}>
                  <span style={catName}>{cat}</span>
                  <span style={catHint}>{CATEGORY_HINT[cat]}</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {list.map((s) => {
                    const Icon = SKILL_ICON[s.key]
                    return (
                      <button
                        key={s.key}
                        onClick={() => onPick(s.prompt_tpl, s.key)}
                        title={s.hint || s.prompt_tpl}
                        style={drawerCard}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.background = HUNTER.BRAND_PALE
                          e.currentTarget.style.borderColor = HUNTER.THEME
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.background = '#fff'
                          e.currentTarget.style.borderColor = HUNTER.LINE
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={drawerIconWrap}>
                            {Icon ? (
                              <Icon size={15} strokeWidth={1.5} />
                            ) : (
                              <span style={{ fontSize: 14 }}>{s.icon || '⭐'}</span>
                            )}
                          </span>
                          <span style={drawerCardName}>{s.name}</span>
                          {FAVORITE_KEYS.includes(s.key) && (
                            <Star size={10} strokeWidth={1.5} style={{ color: HUNTER.THEME, marginLeft: 'auto' }} />
                          )}
                        </div>
                        {s.hint && <div style={drawerCardHint}>{s.hint}</div>}
                      </button>
                    )
                  })}
                </div>
              </section>
            )
          })}
        </div>

        {/* 抽屉底 */}
        <div style={drawerFoot}>
          <a
            href="/skills/skill-library.html"
            target="_blank"
            rel="noopener noreferrer"
            style={footLink}
          >
            浏览能力库（40+ 场景） →
          </a>
          <button
            onClick={() => {
              onClose()
              onManage()
            }}
            style={footLink}
          >
            管理我的能力
          </button>
        </div>
      </div>
    </div>
  )
}

// ─────────── styles ───────────
const sectionLabel: React.CSSProperties = {
  flex: 1,
  fontSize: 11,
  fontWeight: 600,
  color: '#a0a098',
  textTransform: 'uppercase',
  letterSpacing: '.08em',
}
const miniLink: React.CSSProperties = {
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  color: HUNTER.THEME,
  fontSize: 11,
  display: 'inline-flex',
  alignItems: 'center',
}
const favBtn: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  padding: '9px 10px',
  background: '#fbfaf5',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 8,
  cursor: 'pointer',
  fontFamily: 'inherit',
  fontSize: 12.5,
  color: HUNTER.INK,
  fontWeight: 500,
  textAlign: 'left',
  transition: 'background .12s, border-color .12s',
  minHeight: 34,
}
const favIconWrap: React.CSSProperties = {
  flexShrink: 0,
  width: 16,
  height: 16,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: HUNTER.THEME,
}
const favName: React.CSSProperties = {
  flex: 1,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}
const viewAllBtn: React.CSSProperties = {
  width: 'calc(100% - 20px)',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  margin: '8px 10px 0',
  padding: '8px 10px',
  background: 'none',
  border: `1px dashed ${HUNTER.LINE}`,
  borderRadius: 8,
  cursor: 'pointer',
  fontFamily: 'inherit',
  fontSize: 12,
  color: HUNTER.INK_S,
  fontWeight: 500,
  transition: 'border-color .12s, color .12s',
}

// 抽屉
const mask: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(30, 20, 10, 0.28)',
  zIndex: 90,
  display: 'flex',
  animation: 'fadeIn .18s ease-out',
}
const drawerBox: React.CSSProperties = {
  position: 'absolute',
  top: 0,
  left: 260,
  bottom: 0,
  width: 420,
  background: '#fff',
  boxShadow: '0 20px 60px rgba(20,15,8,.28)',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  animation: 'slideInLeft .22s ease-out',
}
const drawerHead: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  padding: '18px 20px 14px',
  borderBottom: `1px solid ${HUNTER.LINE}`,
}
const closeBtn: React.CSSProperties = {
  marginLeft: 'auto',
  background: 'none',
  border: 'none',
  padding: 6,
  cursor: 'pointer',
  color: HUNTER.INK_F,
  borderRadius: 6,
}
const drawerBody: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  padding: '16px 20px 12px',
}
const catHead: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  marginBottom: 8,
  paddingBottom: 4,
  borderBottom: `1px dashed ${HUNTER.LINE}`,
}
const catName: React.CSSProperties = {
  fontSize: 12.5,
  fontWeight: 700,
  color: HUNTER.THEME,
  marginRight: 8,
}
const catHint: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.INK_F,
}
const drawerCard: React.CSSProperties = {
  display: 'block',
  padding: '10px 12px',
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 10,
  cursor: 'pointer',
  fontFamily: 'inherit',
  textAlign: 'left',
  transition: 'background .12s, border-color .12s',
}
const drawerIconWrap: React.CSSProperties = {
  flexShrink: 0,
  width: 20,
  height: 20,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: HUNTER.THEME,
}
const drawerCardName: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: HUNTER.INK,
  flex: 1,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}
const drawerCardHint: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.INK_F,
  lineHeight: 1.5,
  overflow: 'hidden',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
}
const drawerFoot: React.CSSProperties = {
  display: 'flex',
  gap: 12,
  padding: '12px 20px',
  borderTop: `1px solid ${HUNTER.LINE}`,
  background: '#faf8f2',
}
const footLink: React.CSSProperties = {
  fontSize: 12,
  color: HUNTER.THEME,
  textDecoration: 'none',
  background: 'none',
  border: 'none',
  padding: 0,
  cursor: 'pointer',
  fontFamily: 'inherit',
}
