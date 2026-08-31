'use client'

/**
 * 自选股面板 · 侧栏「⭐ 自选股」标签
 *
 * 方案见 doc/开源hunter-community/04开源比赛/
 *        2026-08-30_导航重构方案-对话与自选股双栏.md
 *
 * ## 布局:大框 + 三个附属小框
 *
 *   ┌──────────────────────┐
 *   │ 贵州茅台      600519 │   大框:名 / 码 / 价 / 涨跌
 *   │ 1297.40      +0.39%  │
 *   ├──────┬──────┬────────┤
 *   │📊预测│💰成本│🎲概率  │   三个小框附属在下面
 *   │命中57│ 2 手 │开发中  │
 *   └──────┴──────┴────────┘
 *
 * ## 三个小框为什么是这三个
 *
 * 复赛评委给的四项建议里,前三项都是**针对单只股票**的:
 * 预测评估 / 交易成本 / 概率校准(第四项免责声明是全站的,不占卡片位)。
 *
 * 评委测试说明现在的动线是 Act1→Act6 分散在六个页面,要来回跳。
 * 挂到卡片上之后:**点开一只票,一屏看完三项。**
 *
 * 而且这不是为评委硬凑 —— "我关注这只票 → 模型对它准不准 →
 * 真交易要付多少 → 预测有多确定" 本来就是一条自然的思考链。
 *
 * ## 小框上直接显数字,不用点进去
 *
 * 只写"预测评估"四个字等于没说。显 `命中 57%` 才有信息量,
 * 而这个数我们本来就有(`pred_backtest` 已 seed 1710 条)。
 */

import { useEffect, useState, useCallback } from 'react'
import { Plus, RefreshCw } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

type Stock = {
  code: string
  name: string
  market?: string
  shares?: number
}

type Quote = {
  price?: number
  change_pct?: number
}

type Perf = {
  /** 方向命中率 0-1 · 拿不到时为 undefined —— **不填 0**,
   *  0 和"没数据"在界面上长得一样,而含义完全相反 */
  hit_rate?: number
  samples?: number
}

export default function WatchlistPanel() {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [perf, setPerf] = useState<Record<string, Perf>>({})
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try {
      const r = await fetch('/api/stocks', {
        headers: authHeader(),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      const list: Stock[] = d.stocks || []
      setStocks(list)
      setLoading(false)

      // 行情和历史表现分别拉 —— **一只失败不影响其它只**。
      // 用 Promise.allSettled 而不是 all:一只票的接口挂了
      // 不该让整个列表空白
      const qs = await Promise.allSettled(
        list.map(s =>
          fetch(`/api/quote/${s.code}`, { headers: authHeader() })
            .then(x => x.json())
            .then(q => [s.code, q] as const)
        )
      )
      const qmap: Record<string, Quote> = {}
      qs.forEach(x => {
        if (x.status === 'fulfilled' && x.value?.[1]?.price != null) {
          qmap[x.value[0]] = x.value[1]
        }
      })
      setQuotes(qmap)

      const ps = await Promise.allSettled(
        list.map(s =>
          fetch(`/api/backtest/accuracy?symbol=${encodeURIComponent(s.code)}&days=90`,
                { headers: authHeader() })
            .then(x => x.json())
            .then(p => [s.code, p] as const)
        )
      )
      const pmap: Record<string, Perf> = {}
      ps.forEach(x => {
        if (x.status !== 'fulfilled') return
        const [code, p] = x.value
        const hit = p?.direction_hit_rate ?? p?.hit_rate
        if (typeof hit === 'number') pmap[code] = { hit_rate: hit, samples: p?.samples }
      })
      setPerf(pmap)
    } catch (e: any) {
      setLoading(false)
      setErr(e?.message || '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) {
    return <Hint>加载中…</Hint>
  }
  if (err) {
    // 报错要说清是什么错,别只写"加载失败" —— 用户没法据此做任何事
    return <Hint>拿不到自选股 · {err}</Hint>
  }
  if (stocks.length === 0) {
    return (
      <div style={{ padding: '18px 14px', textAlign: 'center' }}>
        <div style={{ fontSize: 12.5, color: HUNTER.INK_F, lineHeight: 1.8 }}>
          还没有自选股。<br />
          点下面的按钮添加,<br />
          或者<b>直接在对话里说</b>:<br />
          <span style={{ color: HUNTER.COPPER3 }}>「把贵州茅台加进自选,买了 2 手」</span>
        </div>
        <AddButton />
      </div>
    )
  }

  return (
    <div style={{ padding: '8px 10px 4px' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 8, padding: '0 2px',
      }}>
        <span style={{ fontSize: 11, color: HUNTER.INK_F }}>{stocks.length} 只</span>
        <button onClick={load} title="刷新行情"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: HUNTER.INK_F, fontSize: 11, fontFamily: 'inherit', padding: 2,
          }}>
          <RefreshCw size={11} /> 刷新
        </button>
      </div>

      {stocks.map(s => (
        <StockCard key={s.code} stock={s} quote={quotes[s.code]} perf={perf[s.code]} />
      ))}

      <AddButton />
    </div>
  )
}

function StockCard({ stock, quote, perf }: { stock: Stock; quote?: Quote; perf?: Perf }) {
  const pct = quote?.change_pct
  const up = typeof pct === 'number' && pct > 0
  const down = typeof pct === 'number' && pct < 0
  // A 股红涨绿跌 —— 和欧美相反。写死成绿涨会让国内用户看反
  const pctColor = up ? '#C0392B' : down ? '#1E8449' : HUNTER.INK_F

  const hit = perf?.hit_rate
  const hitTxt = typeof hit === 'number' ? `命中 ${Math.round(hit * 100)}%` : '暂无'
  // 55% 是文档里定的高亮线(§3.A.2.2)· 保持一致
  const hitColor = typeof hit === 'number' && hit >= 0.55 ? '#1E8449' : HUNTER.INK_S

  return (
    <div style={{
      border: `1px solid ${HUNTER.LINE}`, borderRadius: 10,
      marginBottom: 8, overflow: 'hidden', background: '#fff',
    }}>
      {/* 大框 · 名 / 码 / 价 / 涨跌 */}
      <div style={{ padding: '9px 11px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: HUNTER.INK }}>{stock.name}</span>
          <span style={{ fontSize: 11, color: HUNTER.INK_F }}>{stock.code}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: 3 }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: HUNTER.INK }}>
            {quote?.price != null ? quote.price.toFixed(2) : '—'}
          </span>
          <span style={{ fontSize: 12, fontWeight: 600, color: pctColor }}>
            {typeof pct === 'number' ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '—'}
          </span>
        </div>
      </div>

      {/* 三个小框 · 对应复赛评委的三项建议 */}
      <div style={{ display: 'flex', borderTop: `1px solid ${HUNTER.LINE}` }}>
        <MiniBox
          href={`/evaluation?symbol=${encodeURIComponent(stock.code)}`}
          icon="📊" label="预测评估" value={hitTxt} valueColor={hitColor}
          title="该股近 90 日方向命中率 · 点击看完整评估看板"
        />
        <MiniBox
          href={`/strategies/backtest.html?symbol=${encodeURIComponent(stock.code)}`}
          icon="💰" label="交易成本"
          value={stock.shares ? `${stock.shares} 手` : '未填'}
          title="按持仓手数算真实买卖成本 · 点击填手数并看毛/净对比"
          border
        />
        <MiniBox
          href={`/calibration?symbol=${encodeURIComponent(stock.code)}`}
          icon="🎲" label="概率校准" value="开发中"
          title="预测区间与三类概率 · 后端已就绪,前端开发中"
          border dim
        />
      </div>
    </div>
  )
}

function MiniBox({ href, icon, label, value, valueColor, title, border, dim }: {
  href: string; icon: string; label: string; value: string
  valueColor?: string; title?: string; border?: boolean; dim?: boolean
}) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" title={title}
      style={{
        flex: 1, padding: '6px 4px 7px', textAlign: 'center',
        textDecoration: 'none', cursor: 'pointer',
        borderLeft: border ? `1px solid ${HUNTER.LINE}` : 'none',
        opacity: dim ? 0.55 : 1,
        transition: 'background .1s',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = HUNTER.BRAND_PALE }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
    >
      <div style={{ fontSize: 10.5, color: HUNTER.INK_F, whiteSpace: 'nowrap' }}>
        {icon} {label}
      </div>
      <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 2, color: valueColor || HUNTER.INK_S }}>
        {value}
      </div>
    </a>
  )
}

function AddButton() {
  return (
    <a href="/watchlist" target="_blank" rel="noopener noreferrer"
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
        marginTop: 6, padding: '7px 0',
        border: `1px dashed ${HUNTER.LINE}`, borderRadius: 9,
        color: HUNTER.INK_F, fontSize: 12, textDecoration: 'none',
      }}>
      <Plus size={13} /> 添加自选股
    </a>
  )
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: '20px 12px', textAlign: 'center', color: HUNTER.INK_F, fontSize: 12 }}>
      {children}
    </div>
  )
}

function authHeader(): Record<string, string> {
  try {
    const t = localStorage.getItem('hunter_token')
    return t ? { Authorization: `Bearer ${t}` } : {}
  } catch {
    return {}
  }
}
