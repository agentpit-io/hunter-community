'use client';
// gm我的: 登录态 + 切回A股端 + 退出
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { GM, GM_RADIUS } from '../../lib/gm/theme';
import { getToken } from '../../lib/gm/api';

export default function GmProfile() {
  const router = useRouter();
  const [email, setEmail] = useState('');

  useEffect(() => {
    const t = getToken();
    if (t) {
      try {
        const payload = JSON.parse(atob(t.split('.')[1]));
        setEmail(payload.email || payload.sub || '');
      } catch { /* ignore */ }
    }
  }, []);

  const switchToWx = () => {
    try { localStorage.setItem('hunter_default_end', 'wx'); } catch { /* ignore */ }
    router.push('/wx/home');
  };

  const logout = () => {
    try {
      localStorage.removeItem('hunter_token');
      localStorage.removeItem('hunter_default_end');
    } catch { /* ignore */ }
    router.push('/entry?choose=1');
  };

  return (
    <div>
      <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 18, marginBottom: 14 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{email || '未登录'}</div>
        <div style={{ fontSize: 11, color: GM.MUTED, marginTop: 4 }}>🌐 全球市场工作台</div>
      </div>

      <Settings />

      <Row icon="🧭" title="新手引导 / 偏好设置" onClick={() => router.push('/gm/onboarding')} />
      <Row icon="↔" title="切换到 🇨🇳 A 股工作台" onClick={switchToWx} />
      <Row icon="🚪" title="退出登录" onClick={logout} danger />

      <div style={{ textAlign: 'center', fontSize: 10, color: GM.MUTED, marginTop: 30 }}>
        数据可能延迟 · AI 仅供研究 · 不构成投资建议
      </div>
    </div>
  );
}

function Settings() {
  const [markets, setMarkets] = useState<string[]>(['us', 'hk']);
  const [currency, setCurrency] = useState('CNY');

  useEffect(() => {
    try {
      const m = localStorage.getItem('gm_pref_markets');
      if (m) setMarkets(JSON.parse(m));
      const c = localStorage.getItem('gm_pref_currency');
      if (c) setCurrency(c);
    } catch { /* ignore */ }
  }, []);

  const toggleMarket = (m: string) => {
    const next = markets.includes(m) ? markets.filter(x => x !== m) : [...markets, m];
    if (next.length === 0) return;
    setMarkets(next);
    try { localStorage.setItem('gm_pref_markets', JSON.stringify(next)); } catch { /* ignore */ }
  };
  const setCur = (c: string) => {
    setCurrency(c);
    try { localStorage.setItem('gm_pref_currency', c); } catch { /* ignore */ }
  };

  const chip = (active: boolean): React.CSSProperties => ({
    border: `1px solid ${active ? GM.BRAND : GM.LINE}`, cursor: 'pointer',
    background: active ? 'rgba(255,91,101,.12)' : GM.BG, color: GM.TEXT,
    borderRadius: 999, padding: '5px 13px', fontSize: 12, fontWeight: active ? 700 : 400,
  });

  return (
    <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: '13px 16px', marginBottom: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>🌍 全球市场设置</div>
      <div style={{ fontSize: 10, color: GM.MUTED, marginBottom: 6 }}>关注市场</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button style={chip(markets.includes('us'))} onClick={() => toggleMarket('us')}>🇺🇸 美股</button>
        <button style={chip(markets.includes('hk'))} onClick={() => toggleMarket('hk')}>🇭🇰 港股</button>
      </div>
      <div style={{ fontSize: 10, color: GM.MUTED, marginBottom: 6 }}>主币种(组合折算显示)</div>
      <div style={{ display: 'flex', gap: 8 }}>
        {['CNY', 'USD', 'HKD'].map(c => (
          <button key={c} style={chip(currency === c)} onClick={() => setCur(c)}>{c}</button>
        ))}
      </div>
      <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 8 }}>
        当前版本组合汇总以人民币折算为主, 多币种显示逐步开放
      </div>
    </div>
  );
}

function Row({ icon, title, onClick, danger }: {
  icon: string; title: string; onClick: () => void; danger?: boolean;
}) {
  return (
    <button onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 10, width: '100%',
      background: GM.PANEL, border: 'none', borderRadius: GM_RADIUS.md,
      padding: '14px 16px', marginBottom: 10, cursor: 'pointer',
      color: danger ? GM.DOWN : GM.TEXT, fontSize: 14, fontWeight: 600, textAlign: 'left',
    }}>
      <span>{icon}</span><span>{title}</span>
      <span style={{ marginLeft: 'auto', color: GM.MUTED }}>›</span>
    </button>
  );
}
