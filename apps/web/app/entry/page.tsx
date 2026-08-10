'use client';
// 入口分流页: 选择进入A股工作台(/wx/home)或美港股工作台(/gm/home)
// 记住选择 → localStorage['hunter_default_end'], 下次由调用方直接跳转
import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

const C = {
  BG: '#080B12', PANEL: '#141B29', TEXT: '#F4F7FB', MUTED: '#8F9BB3',
  LINE: '#263047', BRAND: '#FF5B65', DATA: '#62A8FF',
};

function EntryInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [remember, setRemember] = useState(true);

  // 记住过偏好的用户自动直进对应端(?choose=1 强制显示选择器, 供退出/切换端用)
  useEffect(() => {
    if (searchParams.get('choose') === '1') return;
    let saved = '';
    try { saved = localStorage.getItem('hunter_default_end') || ''; } catch { /* ignore */ }
    if (saved === 'wx' || saved === 'gm') {
      const t = searchParams.get('t');
      const suffix = t ? `?t=${encodeURIComponent(t)}` : '';
      router.replace(saved === 'wx' ? `/wx/home${suffix}` : `/gm/home${suffix}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = (end: 'wx' | 'gm') => {
    if (remember) {
      try { localStorage.setItem('hunter_default_end', end); } catch { /* ignore */ }
      // P0-b: 同步写后端 Redis, 下次 OAuth callback 可直接 302 到目标工作台
      // 跳过 /entry 那一跳客户端 replace (省 300-800ms)
      const t = new URLSearchParams(window.location.search).get('t')
        || (typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : '');
      if (t) {
        fetch('/api/user/entry-preference', {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${t}`,
          },
          body: JSON.stringify({ end }),
          keepalive: true,  // 即使页面立即跳转也能完成上报
        }).catch(() => { /* 不阻塞跳转 */ });
      }
    }
    // 透传 ?t= token(微信OAuth可能带过来)
    const t = new URLSearchParams(window.location.search).get('t');
    const suffix = t ? `?t=${encodeURIComponent(t)}` : '';
    router.push(end === 'wx' ? `/wx/home${suffix}` : `/gm/home${suffix}`);
  };

  const card = (props: {
    flag: string; title: string; subtitle: string; desc: string;
    cta: string; border: string; onClick: () => void;
  }) => (
    <button onClick={props.onClick} style={{
      display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
      background: C.PANEL, border: `1px solid ${props.border}`, borderRadius: 18,
      padding: '18px 16px', marginBottom: 14, color: C.TEXT,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <span style={{ fontSize: 22 }}>{props.flag}</span>
        <span style={{ fontSize: 18, fontWeight: 700 }}>{props.title}</span>
        <span style={{ fontSize: 11, color: C.MUTED }}>{props.subtitle}</span>
      </div>
      <div style={{ fontSize: 12, color: C.MUTED, lineHeight: 1.6, marginBottom: 10 }}>{props.desc}</div>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.DATA }}>{props.cta} →</div>
    </button>
  );

  return (
    <div style={{
      minHeight: '100vh', background: C.BG, color: C.TEXT,
      maxWidth: 480, margin: '0 auto', padding: '48px 20px',
      fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    }}>
      <div style={{ textAlign: 'center', marginBottom: 36 }}>
        <div style={{ fontSize: 44, marginBottom: 8 }}>🦌</div>
        <div style={{ fontSize: 20, fontWeight: 700 }}>猎鹿人 · Hunter</div>
        <div style={{ fontSize: 13, color: C.MUTED, marginTop: 6 }}>你主要关注哪个市场？</div>
      </div>

      {card({
        flag: '🇨🇳', title: 'A 股', subtitle: '深度研究 · 一手情报 · 量化择时',
        desc: '自选股 · AI 持仓管家 · 副驾每日简报 · 29 条产业链 · 政采信号 · Kronos 择时',
        cta: '进入 A 股工作台', border: 'rgba(255,91,101,.45)', onClick: () => go('wx'),
      })}
      {card({
        flag: '🌐', title: '美股 / 港股', subtitle: '隔夜复盘 · 财报预期 · 南向 AH',
        desc: '全球持仓 · 实时/延迟行情 · 分钟级K线 · ETF 热度 · 财报日历',
        cta: '进入全球工作台', border: 'rgba(98,168,255,.45)', onClick: () => go('gm'),
      })}

      <label style={{
        display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center',
        fontSize: 12, color: C.MUTED, marginTop: 10, cursor: 'pointer',
      }}>
        <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} />
        记住我的选择（下次直接进入）
      </label>
    </div>
  );
}

export default function EntryPage() {
  return <Suspense fallback={null}><EntryInner /></Suspense>;
}
