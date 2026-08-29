// gm端统一API封装 —— 相对路径 + Bearer token + 超时保护
// (A股端WxHome无统一封装是技术债, gm端从头做对)
'use client';

const TOKEN_KEY = 'hunter_token';

export function setToken(t: string) {
  if (typeof window !== 'undefined') localStorage.setItem(TOKEN_KEY, t);
}

/** 从URL ?t= 捕获token(微信OAuth跳转带过来), 存后清URL。
 * 2026-08-29: 从 gm/layout useEffect 调用 → 挪进 getToken() 内联,
 * 因为 React effect bottom-up 顺序会让子页面(如 /gm/recap)先跑 useEffect
 * 读到空 token 后直接 setState('empty'), 父 layout 再 captureTokenFromUrl 就晚了。
 * 现在任何 getToken() 调用都会先消化 URL 里的 ?t=, 消除竞态。*/
export function captureTokenFromUrl() {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  const t = url.searchParams.get('t');
  if (t) {
    setToken(t);
    url.searchParams.delete('t');
    window.history.replaceState({}, '', url.toString());
  }
}

export function getToken(): string {
  if (typeof window === 'undefined') return '';
  captureTokenFromUrl();
  return localStorage.getItem(TOKEN_KEY) || '';
}

export async function gmFetch<T = unknown>(
  path: string,
  opts: RequestInit & { timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = 12000, headers, ...rest } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(path, {
      cache: 'no-store',
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        ...(headers || {}),
      },
      ...rest,
    });
    if (resp.status === 401) throw new GmApiError('NEED_LOGIN', 401);
    if (!resp.ok) throw new GmApiError(`HTTP_${resp.status}`, resp.status);
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export class GmApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// ── 类型 ──
export interface GmWatchItem {
  code: string; name: string; market: 'US' | 'HK';
  exchange: string; asset_type: string;
}
export interface GmQuote {
  code: string; market: 'US' | 'HK'; currency: string;
  name: string; name_en?: string; exchange?: string;
  price: number; prev_close: number | null; change_pct: number | null;
  ts: string | null; delayed: boolean;
  market_state: string; market_state_label: string;
  market_state_iso?: 'PRE' | 'REGULAR' | 'POST' | 'CLOSED' | 'LUNCH';
  lot_size?: number | null; is_etf?: boolean;
  regular_price?: number | null;      // 正常时段收盘
  ext_price?: number | null;          // 盘前/盘后延时价
  ext_label?: string | null;          // '盘前' | '盘后'
  earnings_in_days?: number | null;   // 距下次财报天数
  dual_hk?: string | null;            // 美股ADR对应港股代码
  dual_us?: string | null;            // 港股对应美股ADR
}
export interface GmKlineBar {
  ts: string; open: number; high: number; low: number; close: number; volume: number;
}

// ── 接口 ──
export const gmApi = {
  watchlist: () => gmFetch<GmWatchItem[]>('/api/gm/watchlist'),
  addWatch: (s: { code: string; name: string; market: string; exchange?: string; asset_type?: string }) =>
    gmFetch<{ ok: boolean; added: boolean }>('/api/gm/watchlist', {
      method: 'POST', body: JSON.stringify({ ...s, market: s.market.toUpperCase() }),
    }),
  removeWatch: (code: string) =>
    gmFetch<{ ok: boolean }>(`/api/gm/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE' }),
  quotes: (keys: string[]) =>
    gmFetch<{ items: GmQuote[] }>(`/api/gm/quotes?codes=${encodeURIComponent(keys.join(','))}`),
  kline: (market: string, code: string, period: '1m' | '5m' | '1d', limit = 250) =>
    gmFetch<{ bars: GmKlineBar[]; delayed_note: string }>(
      `/api/gm/kline/${market}/${encodeURIComponent(code)}?period=${period}&limit=${limit}`),
  etfHot: () => gmFetch<{ items: { code: string; label: string; price: number; change_pct: number; date: string }[] }>(
    '/api/gm/discover/etf-hot'),
  search: (q: string) =>
    gmFetch<{ items: { code: string; name: string; market: string; exchange: string; asset_type: string; type_name: string }[] }>(
      `/api/watchlist/search?q=${encodeURIComponent(q)}&limit=12`),
  kpred: (market: string, code: string) =>
    gmFetch<GmKpred>(`/api/gm/kpred/${market}/${encodeURIComponent(code)}`),
  news: (market: string, code: string, limit = 10) =>
    gmFetch<{ items: GmNewsItem[] }>(`/api/gm/news/${market}/${encodeURIComponent(code)}?limit=${limit}`),
  earnings: () =>
    gmFetch<{ days: { date: string; weekday: string; items: { symbol: string; name: string; when: string; eps_forecast: string }[] }[]; partial?: boolean }>(
      '/api/gm/discover/earnings', { timeoutMs: 30000 }),
  research: (market: string, code: string) =>
    gmFetch<GmResearch>(`/api/gm/research/${market}/${encodeURIComponent(code)}`, { timeoutMs: 90000 }),
  scout: (market: string, code: string) =>
    gmFetch<{ items: GmFiling[]; source: string }>(`/api/gm/scout/${market}/${encodeURIComponent(code)}?limit=8`, { timeoutMs: 30000 }),
  discoverHk: () =>
    gmFetch<{ southbound: { net_buy_yi: number; date: string } | null;
              ah_premium: { name: string; a_code: string; h_code: string; a_price: number; h_price: number; premium_pct: number }[] }>(
      '/api/gm/discover/hk', { timeoutMs: 45000 }),
  messages: () =>
    gmFetch<{ items: GmMessage[]; count: number; watch_count: number }>('/api/gm/messages', { timeoutMs: 45000 }),
  recap: () =>
    gmFetch<GmRecap>('/api/gm/recap', { timeoutMs: 90000 }),
  analystsTop: () =>
    gmFetch<{ items: GmRating[] }>('/api/gm/discover/analysts-top'),
  analystsFor: (code: string) =>
    gmFetch<{ items: { date: string; firm: string; action: string; from_grade: string; to_grade: string }[] }>(
      `/api/gm/analysts/${encodeURIComponent(code)}`),
  setPosition: (code: string, shares: number | null, cost_price: number | null) =>
    gmFetch<{ ok: boolean }>(`/api/gm/watchlist/${encodeURIComponent(code)}/position`,
      { method: 'PUT', body: JSON.stringify({ shares, cost_price }) }),
  portfolioSummary: () =>
    gmFetch<GmPortfolio>('/api/gm/portfolio/summary', { timeoutMs: 45000 }),
  guardian: (market: string, code: string) =>
    gmFetch<GmGuardian>(`/api/gm/guardian/${market}/${encodeURIComponent(code)}`, { timeoutMs: 120000 }),
  alerts: () => gmFetch<{ items: GmAlertRule[] }>('/api/gm/alerts'),
  addAlert: (a: { market: string; code: string; name: string; rule_type: string; threshold: number }) =>
    gmFetch<{ ok: boolean; id: number }>('/api/gm/alerts', { method: 'POST', body: JSON.stringify(a) }),
  patchAlert: (id: number, patch: { enabled?: boolean; threshold?: number }) =>
    gmFetch<{ ok: boolean }>(`/api/gm/alerts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  delAlert: (id: number) =>
    gmFetch<{ ok: boolean }>(`/api/gm/alerts/${id}`, { method: 'DELETE' }),
  hkFundamentals: (code: string) =>
    gmFetch<{ items: { report_date: string; oi_yi: number | null; net_profit_yi: number | null;
                       oi_yoy: number | null; net_profit_yoy: number | null;
                       basic_eps: number | null; bps: number | null }[]; note: string }>(
      `/api/gm/fundamentals/hk/${encodeURIComponent(code)}`),
  geoOverview: () =>
    gmFetch<GmGeoOverview>('/api/geo/overview', { timeoutMs: 20000 }),
  assistantChat: (message: string, ctx: { market?: string; code?: string } | null,
                  history: { role: string; content: string }[]) =>
    gmFetch<{ reply: string; disclaimer: string }>('/api/gm/assistant/chat', {
      method: 'POST', timeoutMs: 90000,
      body: JSON.stringify({ message, market: ctx?.market, code: ctx?.code, history }),
    }),
};

export interface GmGeoOverview {
  divergence: {
    latest: { date: string; bwet: number; basket: number; div: number; regime: string };
    series: { date: string; div: number }[];
  } | null;
  jwc: { id: string; title: string; url: string; published: string | null }[];
  note: string | null;
  sources: string[]; disclaimer: string;
}

export interface GmAlertRule {
  id: number; market: string; code: string; name: string;
  rule_type: string; threshold: number | null; enabled: boolean;
  last_triggered_at: string | null;
}

export interface GmGuardian {
  error?: string; hint?: string;
  market: string; code: string; name: string;
  quote: { price: number; change_pct: number; currency: string };
  bull: { points: string[]; strongest: string };
  bear: { points: string[]; strongest: string };
  judge: { stance: '继续持有' | '减仓观察' | '警惕风险'; confidence: number; one_line: string;
           rationale: string[]; risk_control: string };
  sources: string[]; disclaimer: string;
}

export interface GmPortfolio {
  total_mv_cny: number; pnl_today_cny: number; pnl_total_cny: number | null;
  fx: { USDCNY: number | null; HKDCNY: number | null };
  fx_missing?: boolean;
  positions: { code: string; name: string; market: string; shares: number;
               cost_price: number | null; price: number; mv_local: number;
               mv_cny: number; change_pct: number; currency: string }[];
  count: number;
}

export interface GmRating {
  symbol: string; date: string; firm: string;
  action: string; from_grade: string; to_grade: string;
}

export interface GmFiling { form: string; title: string; date: string; url: string }
export interface GmMessage {
  level: 'high' | 'mid' | 'watch'; icon: string; title: string;
  desc: string; ts: string; type: string; url?: string;
}
export interface GmRecap {
  error?: string; hint?: string;
  date: string;
  stocks: { code: string; name: string; close: number; chg: number;
            market?: string; currency?: string }[];
  ai: { headline: string; portfolio: string; highlights: string[]; watch_today: string[] };
  upcoming_earnings: string[]; disclaimer: string;
}

export interface GmKpred {
  error?: string; hint?: string;
  score: number; signal: string; reasons: string[];
  price: number; ma5: number; ma20: number; ma60: number;
  rsi14: number; chg20d_pct: number | null;
  model: string; as_of: string; market_state_label: string;
}
export interface GmNewsItem { title: string; source: string; url: string; ts: string; lang: string }
export interface GmResearch {
  error?: string; hint?: string;
  market: string; code: string; name: string;
  quote: { price: number; change_pct: number; currency: string };
  tech: { score: number; signal: string };
  ai: {
    headline: string; confidence: number;
    supporting: string[]; opposing: string[];
    key_numbers: { label: string; value: string; context: string }[];
    risk_boundary: string; invalidation: string;
  };
  sources: string[]; disclaimer: string;
}
