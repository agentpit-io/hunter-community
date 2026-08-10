'use client';
// gm新手引导: 3步偏好收集(localStorage), 从profile进入
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { GM, GM_RADIUS } from '../../lib/gm/theme';

export default function GmOnboarding() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [markets, setMarkets] = useState<string[]>(['us', 'hk']);
  const [style, setStyle] = useState('');

  const toggleMarket = (m: string) =>
    setMarkets(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m]);

  const finish = () => {
    try {
      localStorage.setItem('gm_pref_markets', JSON.stringify(markets));
      localStorage.setItem('gm_pref_style', style);
      localStorage.setItem('gm_onboarded', '1');
    } catch { /* ignore */ }
    router.push('/gm/home');
  };

  const Btn = ({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) => (
    <button onClick={onClick} style={{
      flex: 1, padding: '16px 0', borderRadius: GM_RADIUS.md, cursor: 'pointer', fontSize: 14,
      border: `1px solid ${active ? GM.BRAND : GM.LINE}`,
      background: active ? 'rgba(255,91,101,.12)' : GM.PANEL, color: GM.TEXT, fontWeight: 600,
    }}>{children}</button>
  );

  return (
    <div style={{ padding: '30px 8px' }}>
      <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 8 }}>偏好设置 {step + 1}/3</div>
      {step === 0 && (
        <>
          <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 16 }}>你关注哪些市场?</div>
          <div style={{ display: 'flex', gap: 10, marginBottom: 24 }}>
            <Btn active={markets.includes('us')} onClick={() => toggleMarket('us')}>🇺🇸 美股</Btn>
            <Btn active={markets.includes('hk')} onClick={() => toggleMarket('hk')}>🇭🇰 港股</Btn>
          </div>
          <NextBtn disabled={markets.length === 0} onClick={() => setStep(1)} />
        </>
      )}
      {step === 1 && (
        <>
          <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 16 }}>你的投资风格?</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
            <Btn active={style === 'short'} onClick={() => setStyle('short')}>⚡ 短线波段 · 关注异动与技术信号</Btn>
            <Btn active={style === 'long'} onClick={() => setStyle('long')}>🏔 长线持有 · 关注财报与基本面</Btn>
            <Btn active={style === 'mix'} onClick={() => setStyle('mix')}>⚖ 均衡 · 都要看</Btn>
          </div>
          <NextBtn disabled={!style} onClick={() => setStep(2)} />
        </>
      )}
      {step === 2 && (
        <>
          <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 10 }}>准备就绪 🎉</div>
          <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 16, fontSize: 12, color: GM.MUTED, lineHeight: 2, marginBottom: 24 }}>
            建议第一步: 到持仓中心添加你的美股/港股自选<br />
            然后试试: 研究页的 AI 深度研究 · 消息页的告警规则<br />
            每天早晨可看隔夜复盘
          </div>
          <button onClick={finish} style={{
            width: '100%', padding: '13px 0', border: 'none', borderRadius: GM_RADIUS.md,
            background: GM.BRAND, color: '#fff', fontSize: 15, fontWeight: 700, cursor: 'pointer',
          }}>进入全球工作台 →</button>
        </>
      )}
    </div>
  );
}

function NextBtn({ disabled, onClick }: { disabled: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      width: '100%', padding: '13px 0', border: 'none', borderRadius: 14,
      background: disabled ? '#2a3348' : GM.BRAND, color: '#fff',
      fontSize: 15, fontWeight: 700, cursor: disabled ? 'default' : 'pointer',
    }}>下一步</button>
  );
}
