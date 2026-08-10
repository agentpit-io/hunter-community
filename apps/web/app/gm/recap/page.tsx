'use client';
// 隔夜复盘完整页(可截图分享)
import { useEffect, useState } from 'react';
import { GM, GM_RADIUS, gmPriceColor, gmFmtPct } from '../../lib/gm/theme';
import { gmApi, getToken, GmRecap } from '../../lib/gm/api';

export default function GmRecapPage() {
  const [recap, setRecap] = useState<GmRecap | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'empty'>('loading');

  useEffect(() => {
    if (!getToken()) { setState('empty'); return; }
    gmApi.recap().then(r => {
      if (r.error) setState('empty');
      else { setRecap(r); setState('ready'); }
    }).catch(() => setState('empty'));
  }, []);

  if (state === 'loading') return <Center>生成复盘中(约15秒)…</Center>;
  if (state === 'empty' || !recap) return <Center>暂无复盘 · 请先在持仓中心添加美股自选</Center>;

  return (
    <div>
      <div style={{
        background: `linear-gradient(135deg, #1a2340, ${GM.PANEL})`,
        borderRadius: GM_RADIUS.lg, padding: 18, marginBottom: 12,
      }}>
        <div style={{ fontSize: 11, color: GM.MUTED }}>🌙 隔夜复盘 · {recap.date} 交易日</div>
        <div style={{ fontSize: 17, fontWeight: 800, lineHeight: 1.5, margin: '8px 0' }}>
          {recap.ai.headline}
        </div>
        <div style={{ fontSize: 12, color: GM.MUTED }}>{recap.ai.portfolio}</div>
      </div>

      <Card title="要点">
        {recap.ai.highlights.map((h, i) => (
          <div key={i} style={{ fontSize: 13, lineHeight: 2 }}>· {h}</div>
        ))}
      </Card>

      <Card title="自选股隔夜表现">
        {recap.stocks.map(s => (
          <div key={s.code} style={{
            display: 'flex', alignItems: 'center', padding: '8px 0',
            borderBottom: `1px solid ${GM.LINE}`,
          }}>
            <div style={{ flex: 1 }}>
              <span style={{ fontSize: 11, marginRight: 4 }}>{s.market === 'HK' ? '🇭🇰' : '🇺🇸'}</span>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{s.name}</span>
              <span style={{ fontSize: 10, color: GM.MUTED, marginLeft: 6 }}>{s.code}</span>
            </div>
            <span style={{ fontSize: 12, marginRight: 12, color: GM.MUTED }}>
              {s.currency === 'HKD' ? 'HK$' : '$'}{s.close}
            </span>
            <span style={{ fontSize: 13, fontWeight: 700, color: gmPriceColor(s.chg) }}>{gmFmtPct(s.chg)}</span>
          </div>
        ))}
      </Card>

      {recap.ai.watch_today?.length > 0 && (
        <Card title="今日关注">
          {recap.ai.watch_today.map((w, i) => (
            <div key={i} style={{ fontSize: 12, lineHeight: 1.9, color: GM.MACRO }}>· {w}</div>
          ))}
        </Card>
      )}

      <div style={{ textAlign: 'center', fontSize: 10, color: GM.MUTED, margin: '16px 0' }}>
        🦌 猎鹿人 Hunter · 全球市场 · {recap.disclaimer}
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: GM.MUTED, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div style={{ textAlign: 'center', padding: 60, fontSize: 12, color: GM.MUTED }}>{children}</div>;
}
