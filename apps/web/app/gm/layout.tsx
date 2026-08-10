'use client';
// gm端(美港股)共享布局: 暗色底 + 顶栏(品牌+市场切换器) + 底部5tab
// 微信适配三件套: 字体锁定 / ?t= token捕获 / safe-area
import { useEffect } from 'react';
import { GM } from '../lib/gm/theme';
import { MarketProvider } from '../lib/gm/marketContext';
import { MarketSwitcher } from '../components/gm/MarketSwitcher';
import { BottomTabBar } from '../components/gm/BottomTabBar';
import { captureTokenFromUrl } from '../lib/gm/api';

export default function GmLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    captureTokenFromUrl();
    // 锁定微信字体缩放(防悬浮条把布局撑坏) —— 与WxHome同款
    const w = window as unknown as {
      WeixinJSBridge?: { invoke: (api: string, opts: object, cb: () => void) => void };
    };
    const lock = () => {
      try { w.WeixinJSBridge?.invoke('setFontSizeCallback', { fontSize: 0 }, () => {}); } catch { /* 非微信环境 */ }
    };
    if (w.WeixinJSBridge) lock();
    else document.addEventListener('WeixinJSBridgeReady', lock);
    return () => document.removeEventListener('WeixinJSBridgeReady', lock);
  }, []);

  return (
    <MarketProvider>
      <div style={{
        minHeight: '100vh', background: GM.BG, color: GM.TEXT,
        maxWidth: 480, margin: '0 auto',
        fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
      }}>
        <header style={{
          position: 'sticky', top: 0, zIndex: 90,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', background: GM.BG, borderBottom: `1px solid ${GM.LINE}`,
        }}>
          <div style={{ fontSize: 15, fontWeight: 700, whiteSpace: 'nowrap' }}>
            🦌 全球市场
          </div>
          <MarketSwitcher />
        </header>

        <main style={{ padding: '12px 12px calc(72px + env(safe-area-inset-bottom, 0px))' }}>
          {children}
        </main>

        <BottomTabBar />
      </div>
    </MarketProvider>
  );
}
