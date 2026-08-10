'use client';
// 美/港股详情页共用组件: 价格头部 + K线(SVG蜡烛) + 市场插件(美股时段/港股每手)
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { GM, GM_RADIUS, gmPriceColor, gmFmtPct, gmFmtPrice } from '../../lib/gm/theme';
import { gmApi, GmApiError, GmQuote, GmKlineBar, GmKpred, GmNewsItem } from '../../lib/gm/api';

type Period = '1d' | '5m' | '1m';
const PERIODS: { key: Period; label: string }[] = [
  { key: '1d', label: '日K' },
  { key: '5m', label: '5分' },
  { key: '1m', label: '1分' },
];

export function GmStockDetail({ market, code }: { market: 'US' | 'HK'; code: string }) {
  const router = useRouter();
  const [quote, setQuote] = useState<GmQuote | null>(null);
  const [period, setPeriod] = useState<Period>('1d');
  const [bars, setBars] = useState<GmKlineBar[]>([]);
  const [delayedNote, setDelayedNote] = useState('');
  const [loadingBars, setLoadingBars] = useState(true);
  const [kpred, setKpred] = useState<GmKpred | null>(null);
  const [news, setNews] = useState<GmNewsItem[]>([]);
  const [ratings, setRatings] = useState<{ date: string; firm: string; action: string; from_grade: string; to_grade: string }[]>([]);
  const [hkFin, setHkFin] = useState<Awaited<ReturnType<typeof gmApi.hkFundamentals>>['items']>([]);
  const [showAlert, setShowAlert] = useState(false);

  useEffect(() => {
    gmApi.quotes([`${market}:${code}`]).then(r => setQuote(r.items[0] || null)).catch(() => {});
    gmApi.kpred(market, code).then(k => { if (!k.error) setKpred(k); }).catch(() => {});
    gmApi.news(market, code, 8).then(r => setNews(r.items)).catch(() => {});
    if (market === 'US') {
      gmApi.analystsFor(code).then(r => setRatings(r.items)).catch(() => {});
    }
    if (market === 'HK') {
      gmApi.hkFundamentals(code).then(r => setHkFin(r.items)).catch(() => {});
    }
  }, [market, code]);

  const loadBars = useCallback(async (p: Period) => {
    setLoadingBars(true);
    try {
      const r = await gmApi.kline(market, code, p, p === '1d' ? 120 : 240);
      setBars(r.bars);
      setDelayedNote(r.delayed_note || '');
    } catch { setBars([]); }
    setLoadingBars(false);
  }, [market, code]);

  useEffect(() => { loadBars(period); }, [period, loadBars]);

  return (
    <div>
      {/* 返回 + 标题 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <button onClick={() => router.back()} style={{
          border: 'none', background: GM.PANEL, color: GM.TEXT, borderRadius: 8,
          padding: '6px 12px', fontSize: 13, cursor: 'pointer',
        }}>← 返回</button>
        <div style={{ fontSize: 15, fontWeight: 700 }}>
          {market === 'US' ? '🇺🇸' : '🇭🇰'} {quote?.name || code}
        </div>
      </div>

      {/* 价格头部 */}
      <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <div style={{ fontSize: 28, fontWeight: 800, color: gmPriceColor(quote?.change_pct) }}>
            {gmFmtPrice(quote?.price, quote?.currency)}
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, color: gmPriceColor(quote?.change_pct) }}>
            {gmFmtPct(quote?.change_pct)}
          </div>
        </div>
        {/* 盘前/盘后价格与正常收盘分离 */}
        {quote?.ext_price != null && quote?.ext_label && (
          <div style={{ display: 'flex', gap: 14, marginTop: 6, fontSize: 11, color: GM.MUTED }}>
            <span>正常收盘 <b style={{ color: GM.TEXT }}>{gmFmtPrice(quote.regular_price, quote.currency)}</b></span>
            <span>{quote.ext_label} <b style={{ color: gmPriceColor(quote.change_pct) }}>{gmFmtPrice(quote.ext_price, quote.currency)}</b></span>
          </div>
        )}
        <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          <Tag>{code}</Tag>
          {quote?.exchange && <Tag>{quote.exchange}</Tag>}
          {quote?.market_state_label && <Tag color={GM.DATA}>{quote.market_state_label}</Tag>}
          {quote?.earnings_in_days != null && quote.earnings_in_days <= 21 && (
            <Tag color={GM.BRAND}>📅 财报 {quote.earnings_in_days === 0 ? '今日' : `${quote.earnings_in_days} 天`}</Tag>
          )}
          {market === 'HK' && quote?.lot_size && <Tag color={GM.MACRO}>每手 {quote.lot_size} 股</Tag>}
          {quote?.delayed && <Tag color={GM.MACRO}>数据延迟约15分钟</Tag>}
          {market === 'US' && <Tag color={GM.MUTED}>无涨跌停 · 注意极端波动</Tag>}
          {quote?.dual_hk && (
            <a href={`/gm/stock/hk/${quote.dual_hk}`} style={{ textDecoration: 'none' }}>
              <Tag color={GM.AI}>美港双重上市 · 看港股 {quote.dual_hk} →</Tag>
            </a>
          )}
          {quote?.dual_us && (
            <a href={`/gm/stock/us/${quote.dual_us}`} style={{ textDecoration: 'none' }}>
              <Tag color={GM.AI}>美港双重上市 · 看美股 {quote.dual_us} →</Tag>
            </a>
          )}
        </div>
        <button onClick={() => setShowAlert(true)} style={{
          marginTop: 10, border: `1px solid ${GM.LINE}`, background: 'transparent',
          color: GM.TEXT, borderRadius: 8, padding: '7px 14px', fontSize: 12, cursor: 'pointer',
        }}>⚡ 添加告警</button>
        {showAlert && (
          <AlertSheet market={market} code={code} name={quote?.name || code}
                      onClose={() => setShowAlert(false)} />
        )}
      </div>

      {/* K线 */}
      <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          {PERIODS.map(p => (
            <button key={p.key} onClick={() => setPeriod(p.key)} style={{
              border: 'none', cursor: 'pointer', fontSize: 12, padding: '5px 14px',
              borderRadius: 999,
              background: period === p.key ? GM.PANEL2 : 'transparent',
              color: period === p.key ? GM.TEXT : GM.MUTED,
              fontWeight: period === p.key ? 600 : 400,
            }}>{p.label}</button>
          ))}
        </div>
        {loadingBars ? (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: GM.MUTED, fontSize: 12 }}>
            加载中…
          </div>
        ) : bars.length === 0 ? (
          <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: GM.MUTED, fontSize: 12 }}>
            暂无K线数据
          </div>
        ) : (
          <CandleChart bars={bars} />
        )}
        {delayedNote && (
          <div style={{ fontSize: 10, color: GM.MUTED, marginTop: 6, textAlign: 'right' }}>{delayedNote}</div>
        )}
      </div>

      {/* 择时信号(规则模型) */}
      {kpred && (
        <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 700 }}>🎯 择时信号</span>
            <span style={{
              fontSize: 12, fontWeight: 700, padding: '2px 10px', borderRadius: 999,
              color: kpred.signal === '偏多' ? GM.UP : kpred.signal === '偏空' ? GM.DOWN : GM.MACRO,
              background: GM.BG, border: `1px solid ${GM.LINE}`,
            }}>{kpred.signal} · {kpred.score}分</span>
            <span style={{ fontSize: 9, color: GM.MUTED, marginLeft: 'auto' }}>{kpred.model}</span>
          </div>
          <div style={{ display: 'flex', gap: 12, fontSize: 10, color: GM.MUTED, marginBottom: 8 }}>
            <span>MA20 {kpred.ma20}</span><span>MA60 {kpred.ma60}</span>
            <span>RSI {kpred.rsi14}</span>
            {kpred.chg20d_pct != null && <span>20日 {gmFmtPct(kpred.chg20d_pct)}</span>}
          </div>
          {kpred.reasons.map((r, i) => (
            <div key={i} style={{ fontSize: 11, color: GM.TEXT, lineHeight: 1.8 }}>· {r}</div>
          ))}
        </div>
      )}

      {/* 财务摘要(港股, 年度) */}
      {hkFin.length > 0 && (
        <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>💰 财务摘要(年度)</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ color: GM.MUTED }}>
                  <th style={{ textAlign: 'left', padding: '4px 0' }}>报告期</th>
                  <th style={{ textAlign: 'right' }}>营收(亿)</th>
                  <th style={{ textAlign: 'right' }}>净利(亿)</th>
                  <th style={{ textAlign: 'right' }}>净利YoY</th>
                  <th style={{ textAlign: 'right' }}>EPS</th>
                </tr>
              </thead>
              <tbody>
                {hkFin.map(f => (
                  <tr key={f.report_date} style={{ borderTop: `1px solid ${GM.LINE}` }}>
                    <td style={{ padding: '6px 0', color: GM.MUTED }}>{f.report_date.slice(0, 4)}年报</td>
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{f.oi_yi ?? '--'}</td>
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{f.net_profit_yi ?? '--'}</td>
                    <td style={{ textAlign: 'right', color: gmPriceColor(f.net_profit_yoy) }}>
                      {f.net_profit_yoy != null ? gmFmtPct(f.net_profit_yoy) : '--'}
                    </td>
                    <td style={{ textAlign: 'right', color: GM.MUTED }}>{f.basic_eps ?? '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 6 }}>金额单位: 亿(报告币种) · 来源: 东财港股财报</div>
        </div>
      )}

      {/* 分析师评级(美股) */}
      {ratings.length > 0 && (
        <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>🎓 分析师评级动向</div>
          {ratings.slice(0, 6).map((r, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', padding: '6px 0', fontSize: 11,
              borderBottom: i < Math.min(ratings.length, 6) - 1 ? `1px solid ${GM.LINE}` : 'none',
            }}>
              <span style={{ color: GM.MUTED, width: 44 }}>{r.date.slice(5)}</span>
              <span style={{ flex: 1, color: GM.TEXT }}>{r.firm}</span>
              <span style={{
                fontWeight: 600,
                color: r.action === '上调' || r.action === '新覆盖' ? GM.UP
                  : r.action === '下调' ? GM.DOWN : GM.MUTED,
              }}>
                {r.action}{r.to_grade ? ` → ${r.to_grade}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 新闻 */}
      {news.length > 0 && (
        <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>📰 相关新闻</div>
          {news.map((n, i) => (
            <a key={i} href={n.url} target="_blank" rel="noreferrer" style={{
              display: 'block', textDecoration: 'none', padding: '8px 0',
              borderBottom: i < news.length - 1 ? `1px solid ${GM.LINE}` : 'none',
            }}>
              <div style={{ fontSize: 12, color: GM.TEXT, lineHeight: 1.5 }}>{n.title}</div>
              <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 3 }}>
                {n.source} · {n.ts ? n.ts.slice(5, 16).replace('T', ' ') : ''} · EN
              </div>
            </a>
          ))}
        </div>
      )}

      <div style={{ textAlign: 'center', fontSize: 10, color: GM.MUTED, marginTop: 16 }}>
        数据可能延迟 · AI 仅供研究 · 不构成投资建议
      </div>
    </div>
  );
}

function AlertSheet({ market, code, name, onClose }: {
  market: 'US' | 'HK'; code: string; name: string; onClose: () => void;
}) {
  const [threshold, setThreshold] = useState(5);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const submit = async () => {
    setBusy(true); setMsg('');
    try {
      await gmApi.addAlert({ market, code, name, rule_type: 'change_abs', threshold });
      setMsg('✅ 已添加 · 触发时微信提醒, 可在消息页管理');
      setTimeout(onClose, 1600);
    } catch (e) {
      setMsg(e instanceof GmApiError && e.status === 401 ? '请先登录后再设置告警' : '添加失败, 请稍后重试');
    }
    setBusy(false);
  };
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)', zIndex: 50,
      display: 'flex', alignItems: 'flex-end',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', maxWidth: 480, margin: '0 auto', background: GM.PANEL,
        borderRadius: '14px 14px 0 0', padding: '16px 16px 24px',
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>⚡ 涨跌告警 · {name}</div>
        <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 12 }}>
          当日涨跌幅超过阈值时微信提醒(冷却4小时, 夜间只推≥5%)
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          {[3, 5, 8].map(t => (
            <button key={t} onClick={() => setThreshold(t)} style={{
              flex: 1, border: 'none', cursor: 'pointer', borderRadius: 8, padding: '10px 0',
              fontSize: 13, fontWeight: 700,
              background: threshold === t ? GM.BRAND : GM.PANEL2,
              color: threshold === t ? '#fff' : GM.TEXT,
            }}>±{t}%</button>
          ))}
        </div>
        <button disabled={busy} onClick={submit} style={{
          width: '100%', border: 'none', cursor: 'pointer', borderRadius: 10, padding: '12px 0',
          fontSize: 14, fontWeight: 700, background: GM.BRAND, color: '#fff',
          opacity: busy ? 0.6 : 1,
        }}>{busy ? '添加中…' : '确认添加'}</button>
        {msg && <div style={{ fontSize: 11, color: GM.MUTED, marginTop: 10, textAlign: 'center' }}>{msg}</div>}
      </div>
    </div>
  );
}

function Tag({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      fontSize: 10, color: color || GM.MUTED, background: GM.BG,
      border: `1px solid ${GM.LINE}`, borderRadius: 6, padding: '2px 7px',
    }}>{children}</span>
  );
}

/** 轻量SVG蜡烛图(自适应宽度480内) */
function CandleChart({ bars }: { bars: GmKlineBar[] }) {
  const W = 440, H = 180, PAD = 4;
  const data = bars.slice(-120);
  const hi = Math.max(...data.map(b => b.high));
  const lo = Math.min(...data.map(b => b.low));
  const span = hi - lo || 1;
  const bw = (W - PAD * 2) / data.length;
  const y = (v: number) => PAD + (hi - v) / span * (H - PAD * 2);

  return (
    <div style={{ overflow: 'hidden' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        {data.map((b, i) => {
          const up = b.close >= b.open;
          const color = up ? GM.UP : GM.DOWN;
          const x = PAD + i * bw + bw / 2;
          const bodyTop = y(Math.max(b.open, b.close));
          const bodyH = Math.max(1, Math.abs(y(b.open) - y(b.close)));
          return (
            <g key={i}>
              <line x1={x} x2={x} y1={y(b.high)} y2={y(b.low)} stroke={color} strokeWidth={1} />
              <rect x={x - Math.max(0.8, bw * 0.32)} y={bodyTop}
                    width={Math.max(1.6, bw * 0.64)} height={bodyH}
                    fill={up ? 'transparent' : color} stroke={color} strokeWidth={1} />
            </g>
          );
        })}
        <text x={W - 6} y={12} textAnchor="end" fontSize={9} fill={GM.MUTED}>{hi.toFixed(2)}</text>
        <text x={W - 6} y={H - 4} textAnchor="end" fontSize={9} fill={GM.MUTED}>{lo.toFixed(2)}</text>
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: GM.MUTED, marginTop: 2 }}>
        <span>{data[0]?.ts?.slice(0, 10)}</span>
        <span>{data[data.length - 1]?.ts?.slice(0, 16).replace('T', ' ')}</span>
      </div>
    </div>
  );
}
