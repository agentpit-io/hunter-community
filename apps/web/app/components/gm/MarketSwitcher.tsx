'use client';
// 顶部市场切换器: 全部 · 🇺🇸美股 · 🇭🇰港股
import { GM } from '../../lib/gm/theme';
import { useMarket, GmMarket } from '../../lib/gm/marketContext';

const OPTIONS: { key: GmMarket; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'us', label: '🇺🇸 美股' },
  { key: 'hk', label: '🇭🇰 港股' },
];

export function MarketSwitcher() {
  const { market, setMarket } = useMarket();
  return (
    <div style={{ display: 'flex', gap: 4, background: GM.PANEL, borderRadius: 999, padding: 3 }}>
      {OPTIONS.map(o => (
        <button
          key={o.key}
          onClick={() => setMarket(o.key)}
          style={{
            border: 'none', cursor: 'pointer', fontSize: 12, padding: '5px 12px',
            borderRadius: 999, whiteSpace: 'nowrap',
            background: market === o.key ? GM.PANEL2 : 'transparent',
            color: market === o.key ? GM.TEXT : GM.MUTED,
            fontWeight: market === o.key ? 600 : 400,
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
