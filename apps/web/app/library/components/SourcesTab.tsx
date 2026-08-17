'use client'
// 数据源列表 · **按来源分组**(`_21` §2)
//
// 换掉了原来的按市场分组。原因写在 `_21` §1.1:按市场分是**我们的视角**
// ("我们覆盖了哪些市场"),回答不了用户真正的问题 ——
// 「我手上有 Tushare 的 key,能接进来吗」。按来源分才对得上,
// 因为用户要替换的就是来源。
//
// 市场没有删,降级成顶部的筛选条 —— 它本身有用,只是不该当主分类。
import { useMemo, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { SourceGroup, DataSourceItem, MarketOption } from '../../chat/lib/catalogClient'
import EntityCard from './EntityCard'

interface Props {
  groups: SourceGroup[]
  markets: MarketOption[]
  activeGroup?: string
  /** 市场筛选条的当前值 · 空 = 全部 */
  activeMarket?: string
  onMarketChange: (m: string) => void
  search?: string
  selected: DataSourceItem | null
  onSelect: (item: DataSourceItem) => void
  /** 点空组里的「添加」· 带上该组的 upstream 预选 */
  onAdd?: (presetGroup?: string) => void
}

export default function SourcesTab({
  groups, markets, activeGroup, activeMarket, onMarketChange,
  search, selected, onSelect, onAdd,
}: Props) {
  const filteredGroups = useMemo(() => {
    let gs = activeGroup ? groups.filter((g) => g.upstream === activeGroup) : groups
    if (search) {
      const q = search.toLowerCase()
      gs = gs.map((g) => ({
        ...g,
        sources: g.sources.filter((s) =>
          s.name.toLowerCase().includes(q) ||
          s.kind_label?.toLowerCase().includes(q) ||
          s.provider?.toLowerCase().includes(q) ||
          s.upstream_label?.toLowerCase().includes(q)
        ),
      })).filter((g) => g.sources.length > 0)
    }
    return gs
  }, [groups, activeGroup, search])

  const totalCount = filteredGroups.reduce((a, g) => a + g.sources.length, 0)

  // ── 折叠 ──────────────────────────────────────────────────
  // 11 个来源 33 条源全铺开有五六屏,滚到底也数不清哪组是哪组。
  // 默认收起,点组头展开。
  //
  // 三种情况**必须**展开,否则折叠反而挡了事:
  //   · 「你自己的」—— 它的空状态就是添加入口,藏起来等于没有入口
  //   · 从左侧点了某个来源(activeGroup)—— 用户明确要看那一组
  //   · 搜索中 —— 把命中结果折起来等于没搜
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const isOpen = (g: SourceGroup) =>
    !!search || g.owner === 'user' || activeGroup === g.upstream || opened.has(g.upstream)
  const toggle = (id: string) => setOpened((s) => {
    const n = new Set(s)
    n.has(id) ? n.delete(id) : n.add(id)
    return n
  })

  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>
      <div style={headStyle}>
        <h1 style={{ fontSize: 18, fontWeight: 700, color: HUNTER.INK, margin: 0, fontFamily: HUNTER.SERIF }}>
          📊 数据源
        </h1>
        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>
          显示 {totalCount} 项
        </span>
      </div>

      {/* 市场筛选条 —— 原来的主分类降到这里。
          选项由后端从注册表算出,不在前端写死(加了新市场不用改两处) */}
      {!search && (
        <div style={chipRowStyle}>
          <Chip label="全部市场" active={!activeMarket} onClick={() => onMarketChange('')} />
          {markets.map((m) => (
            <Chip key={m.value} label={m.label}
                  active={activeMarket === m.value}
                  onClick={() => onMarketChange(m.value)} />
          ))}
        </div>
      )}

      {filteredGroups.length === 0 && (
        <div style={emptyStyle}>没有匹配的数据源</div>
      )}

      {filteredGroups.map((g) => {
        const open = isOpen(g)
        const isUserEmpty = g.owner === 'user' && g.sources.length === 0
        return (
          <div key={g.upstream} style={{ marginBottom: open ? 20 : 2 }}>
            <button
              onClick={() => toggle(g.upstream)}
              style={groupHeadStyle(open)}
              // 「你自己的」和搜索/选中态是强制展开的,点它收不起来 ——
              // 与其让用户点了没反应,不如直接告诉他为什么
              title={open && !opened.has(g.upstream)
                ? (search ? '搜索时不折叠' : g.owner === 'user' ? '这一组不折叠' : '你正在看这一组')
                : open ? '收起' : `展开 ${g.total} 项`}
            >
              <span style={{ width: 14, color: HUNTER.INK_F, fontSize: 10 }}>{open ? '▾' : '▸'}</span>
              <span style={{ fontWeight: g.owner === 'user' ? 700 : 600 }}>{g.label}</span>
              {/* 收起时把这一组的**市场**摆出来。折叠后用户看不到条目,
                  只有一个名字和计数太干 —— 市场是他判断"要不要点开"最有用的一条 */}
              {!open && g.markets.length > 0 && (
                <span style={{ fontSize: 11.5, color: HUNTER.INK_F, marginLeft: 8 }}>
                  {g.markets.map((m) => MARKET_CN[m] || m).join(' · ')}
                </span>
              )}
              <span style={{ flex: 1 }} />
              <span style={{ color: HUNTER.INK_F, fontSize: 12 }}>
                {isUserEmpty ? '' : `${g.ready}/${g.total}`}
              </span>
            </button>

            {open && (isUserEmpty ? (
              /* 「你自己的」空组 —— 这个空状态就是添加入口。
                 它必须显示,否则用户在这个页面上看不到任何
                 "我可以接自己的" 的迹象,而那正是这次改造的主题 */
              <div style={userEmptyStyle}>
                <div style={{ marginBottom: 8 }}>
                  你还没有接自己的数据源。接进来之后,<b>取数会优先走你的</b>,
                  你的拿不到才回落到我们的 —— 并且会明确告诉你这次用的是谁。
                </div>
                <button onClick={() => onAdd?.('user')} style={addBtnStyle}>
                  ＋ 添加我的数据源
                </button>
              </div>
            ) : (
              g.sources.map((s) => (
                <EntityCard
                  key={s.key}
                  title={s.name}
                  // 副标题里放**数据类型 + 市场**。来源已经是组标题了,
                  // 再重复一遍是噪音;市场在这里反而有用,因为按来源分组后
                  // 同一组里会混着 A股和港股(比如 AKShare)
                  subtitle={[s.kind_label, s.market_label].filter(Boolean).join(' · ')}
                  status={s.status}
                  meta={s.volume_hint || (s.available ? undefined : '通道未开')}
                  selected={selected?.key === s.key}
                  onClick={() => onSelect(s)}
                />
              ))
            ))}
          </div>
        )
      })}
    </div>
  )
}

function Chip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return <button onClick={onClick} style={chipStyle(active)}>{label}</button>
}

const headStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  marginBottom: 12,
}

const chipRowStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 6,
  marginBottom: 16,
}

const chipStyle = (active: boolean): React.CSSProperties => ({
  padding: '3px 12px',
  fontSize: 12,
  borderRadius: 999,
  border: `1px solid ${active ? HUNTER.THEME : HUNTER.LINE}`,
  background: active ? HUNTER.BRAND_PALE : 'transparent',
  color: active ? HUNTER.THEME : HUNTER.INK_S,
  cursor: 'pointer',
  fontFamily: 'inherit',
})

// 折叠时市场标签用的短名 —— 组头空间窄,"全球/跨市场"太长。
// 完整名仍走后端返回的 market_label(筛选条和条目副标题用的是那个)
const MARKET_CN: Record<string, string> = {
  a: 'A股', hk: '港股', us: '美股', global: '全球',
}

const groupHeadStyle = (open: boolean): React.CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  gap: 2,
  width: '100%',
  fontSize: 13,
  color: HUNTER.INK_S,
  padding: open ? '4px 4px 6px' : '7px 4px',
  background: 'none',
  border: 'none',
  borderBottom: open ? 'none' : `1px solid ${HUNTER.LINE}`,
  cursor: 'pointer',
  fontFamily: 'inherit',
  textAlign: 'left',
})

const userEmptyStyle: React.CSSProperties = {
  padding: '14px 16px',
  borderRadius: 10,
  border: `1px dashed ${HUNTER.LINE}`,
  background: HUNTER.PAPER3,
  fontSize: 12.5,
  color: HUNTER.INK_S,
  lineHeight: 1.8,
}

const addBtnStyle: React.CSSProperties = {
  padding: '5px 14px',
  fontSize: 12,
  borderRadius: HUNTER.R_SM,
  border: `1px solid ${HUNTER.THEME}`,
  background: 'transparent',
  color: HUNTER.THEME,
  cursor: 'pointer',
  fontFamily: 'inherit',
}

const emptyStyle: React.CSSProperties = {
  padding: '40px 20px',
  textAlign: 'center',
  color: HUNTER.INK_F,
  fontSize: 13,
}
