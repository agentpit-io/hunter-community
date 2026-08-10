// 美港股端(gm)暗色设计token —— 与A股端(wx)宣纸风完全隔离
// 色值来自《美港股独立UI·02-前后端技术方案》1.4节
export const GM = {
  BG: '#080B12',        // 页面底
  PANEL: '#141B29',     // 卡片底
  PANEL2: '#1B2438',    // 卡片hover/次级
  BRAND: '#FF5B65',     // Hunter品牌红(延续)
  UP: '#2FD49F',        // 涨(国际惯例绿涨)
  DOWN: '#FF5B65',      // 跌(红跌)
  DATA: '#62A8FF',      // 数据蓝
  AI: '#9D7CFF',        // AI紫
  MACRO: '#F2C66D',     // 宏观金
  TEXT: '#F4F7FB',      // 主文字
  MUTED: '#8F9BB3',     // 次要文字
  LINE: '#263047',      // 分隔线
} as const;

export const GM_RADIUS = { sm: 14, md: 18, lg: 24 } as const;

/** 涨跌颜色(国际惯例: 绿涨红跌) */
export function gmPriceColor(changePct: number | null | undefined): string {
  if (changePct == null || changePct === 0) return GM.MUTED;
  return changePct > 0 ? GM.UP : GM.DOWN;
}

export function gmFmtPct(v: number | null | undefined): string {
  if (v == null) return '--';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

export function gmFmtPrice(v: number | null | undefined, currency?: string): string {
  if (v == null) return '--';
  const sym = currency === 'USD' ? '$' : currency === 'HKD' ? 'HK$' : '';
  return `${sym}${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`;
}
