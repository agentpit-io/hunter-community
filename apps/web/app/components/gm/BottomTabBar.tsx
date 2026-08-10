'use client';
// gm端底部5-tab导航(路由式, 区别于WxHome的state式)
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { GM } from '../../lib/gm/theme';

const TABS = [
  { href: '/gm/home', label: '持仓', icon: '🛡' },
  { href: '/gm/research', label: '研究', icon: '🔬' },
  { href: '/gm/discover', label: '发现', icon: '🔎' },
  { href: '/gm/messages', label: '消息', icon: '🔔' },
  { href: '/gm/profile', label: '我的', icon: '👤' },
];

export function BottomTabBar() {
  const pathname = usePathname();
  return (
    <nav style={{
      position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
      width: '100%', maxWidth: 480, zIndex: 100,
      display: 'flex', justifyContent: 'space-around',
      background: GM.PANEL, borderTop: `1px solid ${GM.LINE}`,
      paddingBottom: 'env(safe-area-inset-bottom, 0px)',
    }}>
      {TABS.map(t => {
        const active = pathname === t.href || pathname.startsWith(t.href + '/');
        return (
          <Link key={t.href} href={t.href} style={{
            flex: 1, textAlign: 'center', padding: '8px 0 6px', textDecoration: 'none',
            color: active ? GM.TEXT : GM.MUTED, position: 'relative',
          }}>
            <div style={{ fontSize: 18, lineHeight: '22px' }}>{t.icon}</div>
            <div style={{ fontSize: 10, fontWeight: active ? 600 : 400 }}>{t.label}</div>
            {active && (
              <div style={{
                position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)',
                width: 18, height: 2, borderRadius: 2, background: GM.BRAND,
              }} />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
