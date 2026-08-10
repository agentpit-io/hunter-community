'use client';
// gm发现页: ETF板块热度(真实数据) + 财报日历(占位)
import { useEffect, useState } from 'react';
import { GM, GM_RADIUS, gmPriceColor, gmFmtPct } from '../../lib/gm/theme';
import { gmApi, GmGeoOverview } from '../../lib/gm/api';

type EtfItem = { code: string; label: string; price: number; change_pct: number; date: string };
type EarnDay = { date: string; weekday: string; items: { symbol: string; name: string; when: string; eps_forecast: string }[] };

export default function GmDiscover() {
  const [etfs, setEtfs] = useState<EtfItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [earnDays, setEarnDays] = useState<EarnDay[]>([]);
  const [earnPartial, setEarnPartial] = useState(false);
  const [earnLoaded, setEarnLoaded] = useState(false);
  const [hk, setHk] = useState<Awaited<ReturnType<typeof gmApi.discoverHk>> | null>(null);
  const [hkLoaded, setHkLoaded] = useState(false);
  const [ratings, setRatings] = useState<Awaited<ReturnType<typeof gmApi.analystsTop>>['items']>([]);
  const [geo, setGeo] = useState<GmGeoOverview | null>(null);

  useEffect(() => {
    gmApi.etfHot().then(r => setEtfs(r.items)).catch(() => {}).finally(() => setLoaded(true));
    gmApi.earnings().then(r => { setEarnDays(r.days); setEarnPartial(!!r.partial); }).catch(() => {}).finally(() => setEarnLoaded(true));
    gmApi.discoverHk().then(setHk).catch(() => {}).finally(() => setHkLoaded(true));
    gmApi.analystsTop().then(r => setRatings(r.items)).catch(() => {});
    gmApi.geoOverview().then(setGeo).catch(() => {});
  }, []);

  return (
    <div>
      <Section title="📈 ETF 板块热度" subtitle={etfs[0] ? `${etfs[0].date} 收盘` : ''}>
        {!loaded && <Muted>加载中…</Muted>}
        {loaded && etfs.length === 0 && <Muted>暂无数据</Muted>}
        {etfs.map(e => (
          <div key={e.code} style={{
            display: 'flex', alignItems: 'center', padding: '10px 13px',
            borderBottom: `1px solid ${GM.LINE}`,
          }}>
            <div style={{ flex: 1 }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{e.code}</span>
              <span style={{ fontSize: 11, color: GM.MUTED, marginLeft: 8 }}>{e.label}</span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: gmPriceColor(e.change_pct) }}>
              {gmFmtPct(e.change_pct)}
            </div>
          </div>
        ))}
      </Section>

      <Section title="📅 本周财报日历" subtitle={earnLoaded && earnDays.length ? (earnPartial ? '美股 · 部分日期获取失败' : '美股 · Nasdaq') : ''}>
        {!earnLoaded && <Muted>加载中…</Muted>}
        {earnLoaded && earnDays.length === 0 && <Muted>暂无数据(数据源可能暂不可达)</Muted>}
        {earnDays.map(d => (
          <div key={d.date} style={{ padding: '8px 13px', borderBottom: `1px solid ${GM.LINE}` }}>
            <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 4 }}>
              {d.weekday} {d.date.slice(5)}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {d.items.slice(0, 8).map(it => (
                <span key={it.symbol} style={{
                  fontSize: 11, background: GM.PANEL2 ?? GM.BG, borderRadius: 6,
                  padding: '3px 8px', color: GM.TEXT,
                }} title={it.name}>
                  <b>{it.symbol}</b>
                  {it.when && <span style={{ color: GM.MUTED, fontSize: 9 }}> {it.when}</span>}
                  {it.eps_forecast && <span style={{ color: GM.MUTED, fontSize: 9 }}> 预期{it.eps_forecast}</span>}
                </span>
              ))}
              {d.items.length > 8 && <span style={{ fontSize: 10, color: GM.MUTED }}>+{d.items.length - 8}家</span>}
            </div>
          </div>
        ))}
      </Section>
      {ratings.length > 0 && (
        <Section title="⬆ 分析师动向" subtitle="近10日 上调/新覆盖">
          {ratings.map((r, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', padding: '9px 13px',
              borderBottom: `1px solid ${GM.LINE}`,
            }}>
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 13, fontWeight: 700 }}>{r.symbol}</span>
                <span style={{ fontSize: 10, color: GM.MUTED, marginLeft: 8 }}>{r.firm}</span>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 11, color: GM.UP, fontWeight: 600 }}>
                  {r.action}{r.to_grade ? ` → ${r.to_grade}` : ''}
                </div>
                <div style={{ fontSize: 9, color: GM.MUTED }}>{r.date.slice(5)}</div>
              </div>
            </div>
          ))}
        </Section>
      )}

      <Section title="🌊 南向资金" subtitle={hk?.southbound?.date || ''}>
        {!hkLoaded && <Muted>加载中…</Muted>}
        {hkLoaded && !hk?.southbound && <Muted>暂无数据</Muted>}
        {hk?.southbound && (
          <div style={{ padding: '12px 13px', display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 12, color: GM.MUTED }}>港股通净{hk.southbound.net_buy_yi >= 0 ? '买入' : '卖出'}</span>
            <span style={{
              fontSize: 20, fontWeight: 800,
              color: hk.southbound.net_buy_yi >= 0 ? GM.UP : GM.DOWN,
            }}>{Math.abs(hk.southbound.net_buy_yi).toFixed(1)} 亿</span>
          </div>
        )}
      </Section>

      <Section title="⚖ AH 溢价榜" subtitle="A股价/(H股价×汇率)-1 · 越高代表A股越贵">
        {!hkLoaded && <Muted>加载中…</Muted>}
        {hkLoaded && (!hk?.ah_premium || hk.ah_premium.length === 0) && <Muted>暂无数据</Muted>}
        {hk?.ah_premium?.map(p => (
          <div key={p.h_code} style={{
            display: 'flex', alignItems: 'center', padding: '9px 13px',
            borderBottom: `1px solid ${GM.LINE}`,
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{p.name}</div>
              <div style={{ fontSize: 9, color: GM.MUTED }}>
                A {p.a_code} ¥{p.a_price} · H {p.h_code} HK${p.h_price}
              </div>
            </div>
            <div style={{
              fontSize: 13, fontWeight: 700,
              color: p.premium_pct > 50 ? GM.MACRO : GM.TEXT,
            }}>+{p.premium_pct}%</div>
          </div>
        ))}
      </Section>
      {geo?.divergence && (
        <Section title="🌍 地缘风险 · 油运背离度" subtitle={`${geo.divergence.latest.date} · 20日窗口`}>
          <div style={{ padding: '12px 13px' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{
                fontSize: 22, fontWeight: 800,
                color: Math.abs(geo.divergence.latest.div) >= 10 ? GM.MACRO : GM.TEXT,
              }}>{geo.divergence.latest.div > 0 ? '+' : ''}{geo.divergence.latest.div.toFixed(1)}pp</span>
              <span style={{
                fontSize: 11, fontWeight: 700, borderRadius: 6, padding: '2px 8px',
                background: GM.PANEL2 ?? GM.BG,
                color: geo.divergence.latest.regime === '定价关闭' ? GM.MACRO
                  : geo.divergence.latest.regime === '定价绕行' ? GM.UP : GM.MUTED,
              }}>{geo.divergence.latest.regime}</span>
            </div>
            <div style={{ fontSize: 10, color: GM.MUTED, margin: '4px 0 8px' }}>
              运费ETF(BWET) {gmFmtPct(geo.divergence.latest.bwet)} vs 船东股组合 {gmFmtPct(geo.divergence.latest.basket)}
            </div>
            <GeoSpark series={geo.divergence.series} />
            {geo.note && <div style={{ fontSize: 11, lineHeight: 1.7, color: GM.TEXT, marginTop: 8 }}>{geo.note}</div>}
          </div>
          {geo.jwc.length > 0 && (
            <div style={{ borderTop: `1px solid ${GM.LINE}`, padding: '8px 13px' }}>
              <div style={{ fontSize: 10, color: GM.MUTED, marginBottom: 4 }}>战争险 Listed Areas 通函(Lloyd&apos;s JWC)</div>
              {geo.jwc.slice(0, 3).map(j => (
                <a key={j.id} href={j.url} target="_blank" rel="noreferrer"
                   style={{ display: 'block', fontSize: 11, color: GM.TEXT, padding: '3px 0', textDecoration: 'none' }}>
                  📄 {j.title}{j.published && <span style={{ color: GM.MUTED, fontSize: 9 }}> · {j.published.slice(0, 7)}</span>}
                </a>
              ))}
            </div>
          )}
          <div style={{ fontSize: 9, color: GM.MUTED, padding: '6px 13px 10px' }}>{geo.disclaimer}</div>
        </Section>
      )}
    </div>
  );
}

function GeoSpark({ series }: { series: { date: string; div: number }[] }) {
  if (series.length < 2) return null;
  const W = 300, H = 44;
  const vals = series.map(s => s.div);
  const lo = Math.min(...vals, 0), hi = Math.max(...vals, 0);
  const x = (i: number) => (i / (series.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }}>
      <line x1={0} y1={y(0)} x2={W} y2={y(0)} stroke={GM.LINE} strokeDasharray="3 3" />
      <polyline points={pts} fill="none" stroke={GM.MACRO} strokeWidth={1.6} />
    </svg>
  );
}

function Section({ title, subtitle, children }: {
  title: string; subtitle?: string; children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', margin: '4px 2px 6px' }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{title}</span>
        {subtitle && <span style={{ fontSize: 10, color: GM.MUTED }}>{subtitle}</span>}
      </div>
      <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, overflow: 'hidden' }}>{children}</div>
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: 16, fontSize: 12, color: GM.MUTED }}>{children}</div>;
}
