/**
 * 预测存证分享页 · /p/{token}
 *
 * 方案:doc/开源hunter-community/04开源比赛/2026-08-31_预测存证分享页_方案.md
 *
 * ## 这一页要解决的信任问题
 *
 * /evaluation 能算命中率,但命中率是**我们自己算给你看的** ——
 * 你怎么知道我们没有事后挑好的预测?
 *
 * 分享页把「信我」换成「你自己看」:每条历史预测一个不可猜的公开 URL,
 * 页面上同时放「当时说了什么」和「后来实际发生了什么」。不用登录。
 *
 * ## 为什么是服务端渲染
 *
 * 1. 分享出去要有预览卡(微信/Slack 抓 OG meta),CSR 抓不到
 * 2. 这页是给不登录的人看的,不该等 JS 跑完才出内容
 *
 * 服务端取数必须走 HERMES_API_URL(容器内网),不能用浏览器那套相对路径 ——
 * Node 里 fetch('/api/...') 没有 origin,直接抛。
 *
 * ## 错的也要显示
 *
 * 下面 outcome 那一列包含方向判错的 horizon。一个只展示对的存证页
 * 没有存证价值 —— 而且评委一定会点开好几条对比。
 */

import Link from 'next/link'
import { notFound } from 'next/navigation'
import type { Metadata } from 'next'

export const dynamic = 'force-dynamic'   // 存证内容随验证进度变 · 不静态化

const API = (process.env.HERMES_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

type Outcome = {
  real_change: number | null
  dir_hit: boolean | null
  abs_error: number | null
  pred_date: string | null
}
type Pred = {
  horizon: number
  pred_date: string
  last_close: number | null
  pred_close: number | null
  change_pct: number | null
  direction: string | null
  signal: string | null
  confidence: number | null
  outcome: Outcome | null
}
type Share = {
  token: string
  symbol: string
  run_date: string
  base_date: string
  model_ver: string
  is_demo: boolean
  factors: Record<string, number>
  predictions: Pred[]
}

async function load(token: string): Promise<Share | null> {
  try {
    const r = await fetch(`${API}/api/backtest/share/${encodeURIComponent(token)}`,
                          { cache: 'no-store' })
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export async function generateMetadata(
  { params }: { params: Promise<{ token: string }> },
): Promise<Metadata> {
  const { token } = await params
  const d = await load(token)
  if (!d) return { title: '分享链接无效 · Hunter' }
  const t = `${d.symbol} · ${d.run_date} 的预测存证`
  return {
    title: `${t} | Hunter`,
    description: `${d.run_date} 作出的预测与事后真实结果对照 · 仅供研究,非投资建议`,
    openGraph: { title: t, description: '预测存证 · 公开可核验' },
  }
}

export default async function SharePage(
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params
  const d = await load(token)

  // notFound() 而不是直接 return 一段"无效"的 JSX ——
  // 后者会以 HTTP 200 返回,一个不存在的存证链接返回 200 是错的。
  // 文案在同目录的 not-found.tsx。
  if (!d) notFound()

  const verified = d.predictions.filter(p => p.outcome?.real_change != null)
  const hits = verified.filter(p => p.outcome?.dir_hit).length

  return (
    <Shell>
      {/* 演示数据条带 —— 分享页是最容易被截图转发的一页,
          这个标记不能只是角落里的小字(方案 §5) */}
      {d.is_demo && (
        <div style={{
          background: '#FDF3DC', border: '1px solid #E3C89A', borderRadius: 8,
          padding: '9px 12px', fontSize: 12.5, color: '#8A5A1B',
          lineHeight: 1.8, marginBottom: 16,
        }}>
          <b>演示数据</b> · 模型版本 <code>{d.model_ver}</code>。
          下面的<b>真实收盘是真数据</b>,但「当时的预测」是复赛演示用的合成值
          (真实历史 + 高斯噪声),不代表任何真实模型的表现。
        </div>
      )}

      <div style={{ fontSize: 12, color: '#8C857A', letterSpacing: 1 }}>预测存证</div>
      <h1 style={{ fontSize: 22, fontWeight: 600, margin: '2px 0 10px', color: '#2B2723' }}>
        {d.symbol}
      </h1>

      {/* 最重要的一行:这条预测是什么时候作出的 */}
      <div style={{ fontSize: 13, color: '#4A443C', lineHeight: 2 }}>
        预测作出于 <b>{d.run_date}</b>(基准日 {d.base_date})<br />
        模型 <code style={codeS}>{d.model_ver}</code>
        {verified.length > 0 && (
          <> · 已验证 {verified.length} 个周期,方向命中 <b>{hits}/{verified.length}</b></>
        )}
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', margin: '18px 0 6px', fontSize: 13 }}>
        <thead>
          <tr style={{ color: '#8C857A', fontSize: 12 }}>
            <th style={th}>周期</th>
            <th style={th}>当时说</th>
            <th style={th}>后来</th>
            <th style={th}>差多少</th>
          </tr>
        </thead>
        <tbody>
          {d.predictions.map(p => {
            const o = p.outcome
            const done = o && o.real_change != null
            return (
              <tr key={p.horizon} style={{ borderTop: '1px solid #E7E2D9' }}>
                <td style={td}>
                  T+{p.horizon}
                  <div style={{ fontSize: 11, color: '#9C958A' }}>{p.pred_date}</div>
                </td>
                <td style={td}>
                  <b style={{ color: sign(p.change_pct) }}>{pct(p.change_pct)}</b>
                  {p.signal && <div style={{ fontSize: 11, color: '#9C958A' }}>{p.signal}</div>}
                </td>
                <td style={td}>
                  {done ? (
                    <>
                      <b style={{ color: sign(o!.real_change) }}>{pct(o!.real_change)}</b>
                      <div style={{ fontSize: 11, color: o!.dir_hit ? '#3E7A4E' : '#B4472A' }}>
                        {o!.dir_hit ? '方向对' : '方向错'}
                      </div>
                    </>
                  ) : (
                    <span style={{ color: '#9C958A' }}>尚未到验证日</span>
                  )}
                </td>
                <td style={{ ...td, color: '#6B6459' }}>
                  {done && o!.abs_error != null ? `${o!.abs_error.toFixed(2)} 个百分点` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {Object.keys(d.factors || {}).length > 0 && (
        <>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: '#4A443C', marginTop: 20 }}>
            当时的因子读数
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            {Object.entries(d.factors).map(([k, v]) => (
              <span key={k} style={{
                fontSize: 11.5, padding: '3px 9px', borderRadius: 20,
                border: '1px solid #E7E2D9', color: '#4A443C', background: '#fff',
              }}>{k} <b>{typeof v === 'number' ? v.toFixed(3) : String(v)}</b></span>
            ))}
          </div>
          <div style={{ fontSize: 11.5, color: '#8C857A', marginTop: 8, lineHeight: 1.8 }}>
            这些是作出预测**当时**的因子读数,不是现在重算的 —— 存证的意义就在这里。
          </div>
        </>
      )}

      <div style={{ fontSize: 11.5, color: '#8C857A', lineHeight: 1.9, marginTop: 24 }}>
        自己核验:<code style={codeS}>GET /api/backtest/share/{d.token}</code>
        <div style={{ marginTop: 6 }}>
          本页为历史记录展示,不预示未来表现,<b>不构成投资建议</b>。
        </div>
        <Link href="/" style={linkS}>Hunter Community →</Link>
      </div>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', background: '#FAF8F4', color: '#2B2723' }}>
      <main style={{
        maxWidth: 620, margin: '0 auto', padding: '36px 20px 60px',
      }}>
        <div style={{
          background: '#fff', border: '1px solid #E7E2D9', borderRadius: 14,
          padding: '22px 24px 26px', position: 'relative',
        }}>
          {children}
          <div style={{
            position: 'absolute', bottom: 8, right: 12, fontSize: 10,
            color: '#B0A99C', pointerEvents: 'none', userSelect: 'none',
          }}>仅供研究 · 非投资建议</div>
        </div>
      </main>
    </div>
  )
}

const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
// A 股口径:红涨绿跌
const sign = (v: number | null | undefined) =>
  v == null ? '#6B6459' : v > 0 ? '#C0392B' : v < 0 ? '#3E7A4E' : '#6B6459'

const th: React.CSSProperties = { textAlign: 'left', padding: '4px 6px', fontWeight: 500 }
const td: React.CSSProperties = { padding: '9px 6px', verticalAlign: 'top' }
const codeS: React.CSSProperties = {
  background: '#F4F1EC', padding: '2px 6px', borderRadius: 4, fontSize: 11,
}
const linkS: React.CSSProperties = {
  display: 'inline-block', marginTop: 10, fontSize: 12.5, color: '#A9714B',
}
