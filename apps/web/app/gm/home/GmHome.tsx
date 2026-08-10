'use client';
// gm端持仓中心: 美/港股自选列表 + 行情 + 搜索添加 + 删除
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { GM, GM_RADIUS, gmPriceColor, gmFmtPct, gmFmtPrice } from '../../lib/gm/theme';
import { gmApi, getToken, GmWatchItem, GmQuote, GmApiError, GmRecap, GmPortfolio, GmGuardian } from '../../lib/gm/api';
import { useMarket } from '../../lib/gm/marketContext';
import { readSwr, writeSwr } from '../../lib/swrCache';

type SearchItem = {
  code: string; name: string; market: string;
  exchange: string; asset_type: string; type_name: string;
};

export default function GmHome() {
  const router = useRouter();
  const { market } = useMarket();
  const [needLogin, setNeedLogin] = useState(false);
  const [items, setItems] = useState<GmWatchItem[]>([]);
  const [quotes, setQuotes] = useState<Record<string, GmQuote>>({});
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [portfolio, setPortfolio] = useState<GmPortfolio | null>(null);
  const [posEdit, setPosEdit] = useState<GmWatchItem | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPortfolio = useCallback(() => {
    gmApi.portfolioSummary().then(p => { if (p.count > 0) setPortfolio(p); else setPortfolio(null); }).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    if (!getToken()) { setNeedLogin(true); setLoading(false); return; }
    // P2 SWR: 缓存命中先立即渲染, 后台并行 revalidate
    const cached = readSwr<GmWatchItem[]>('gm_watchlist');
    if (cached && cached.length) {
      setItems(cached);
      setLoading(false);
    }
    try {
      const list = await gmApi.watchlist();
      setItems(list);
      writeSwr('gm_watchlist', list);
      if (list.length) {
        const keys = list.map(s => `${s.market}:${s.code}`);
        const q = await gmApi.quotes(keys);
        const map: Record<string, GmQuote> = {};
        q.items.forEach(it => { map[`${it.market}:${it.code}`] = it; });
        setQuotes(map);
      }
    } catch (e) {
      if (e instanceof GmApiError && e.status === 401) setNeedLogin(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    loadPortfolio();
    timerRef.current = setInterval(load, 30000); // 30s轮询刷行情
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [load, loadPortfolio]);

  const filtered = useMemo(() => {
    if (market === 'us') return items.filter(i => i.market === 'US');
    if (market === 'hk') return items.filter(i => i.market === 'HK');
    return items;
  }, [items, market]);

  const usItems = filtered.filter(i => i.market === 'US');
  const hkItems = filtered.filter(i => i.market === 'HK');

  const remove = async (code: string) => {
    await gmApi.removeWatch(code);
    setItems(prev => prev.filter(i => i.code !== code));
  };

  if (loading) return <Hint text="加载中…" />;
  if (needLogin) return <NeedLogin />;

  return (
    <div>
      <RecapCard hasUs={items.length > 0} />
      <GuardianCard watch={items} />

      {/* 组合汇总(双币折算, 有仓位才显示) */}
      {portfolio && (
        <div style={{
          background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: '13px 14px', marginBottom: 12,
          display: 'flex', gap: 18,
        }}>
          <div>
            <div style={{ fontSize: 10, color: GM.MUTED }}>组合市值(折人民币)</div>
            <div style={{ fontSize: 19, fontWeight: 800 }}>¥ {portfolio.total_mv_cny.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</div>
            <div style={{ fontSize: 9, color: portfolio.fx_missing ? GM.MACRO : GM.MUTED }}>
              {portfolio.fx_missing ? '⚠ 汇率暂缺, CNY折算不完整' : `${portfolio.count} 笔持仓 · 原币+汇率折算`}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: GM.MUTED }}>今日盈亏</div>
            <div style={{ fontSize: 19, fontWeight: 800, color: gmPriceColor(portfolio.pnl_today_cny) }}>
              {portfolio.pnl_today_cny >= 0 ? '+' : ''}¥{portfolio.pnl_today_cny.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
            </div>
            {portfolio.pnl_total_cny != null && (
              <div style={{ fontSize: 9, color: gmPriceColor(portfolio.pnl_total_cny) }}>
                累计 {portfolio.pnl_total_cny >= 0 ? '+' : ''}¥{portfolio.pnl_total_cny.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 列表头 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>自选 / 持仓</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {items.length > 0 && (
            <SmallBtn onClick={() => setEditMode(!editMode)} active={editMode}>
              {editMode ? '完成' : '管理'}
            </SmallBtn>
          )}
          <SmallBtn onClick={() => setShowSearch(true)}>+ 添加</SmallBtn>
        </div>
      </div>

      {filtered.length === 0 && (
        <EmptyState onAdd={() => setShowSearch(true)} />
      )}

      {(market !== 'hk' && usItems.length > 0) && (
        <Group title={`🇺🇸 美股 · ${usItems[0] && quotes[`US:${usItems[0].code}`]?.market_state_label || ''}`}>
          {usItems.map(s => (
            <StockRow key={s.code} item={s} quote={quotes[`US:${s.code}`]}
              editMode={editMode} onRemove={() => remove(s.code)}
              onPosition={() => setPosEdit(s)}
              onClick={() => router.push(`/gm/stock/us/${s.code}`)} />
          ))}
        </Group>
      )}
      {(market !== 'us' && hkItems.length > 0) && (
        <Group title={`🇭🇰 港股 · ${hkItems[0] && quotes[`HK:${hkItems[0].code}`]?.market_state_label || ''}`}>
          {hkItems.map(s => (
            <StockRow key={s.code} item={s} quote={quotes[`HK:${s.code}`]}
              editMode={editMode} onRemove={() => remove(s.code)}
              onPosition={() => setPosEdit(s)}
              onClick={() => router.push(`/gm/stock/hk/${s.code}`)} />
          ))}
        </Group>
      )}

      <div style={{ textAlign: 'center', marginTop: 16, fontSize: 10, color: GM.MUTED }}>
        数据可能延迟 · AI 仅供研究 · 不构成投资建议
      </div>

      {showSearch && (
        <SearchSheet
          onClose={() => setShowSearch(false)}
          onAdded={() => { setShowSearch(false); load(); }}
        />
      )}
      {posEdit && (
        <PositionSheet item={posEdit} onClose={() => setPosEdit(null)}
          onSaved={() => { setPosEdit(null); loadPortfolio(); }} />
      )}
    </div>
  );
}

// ── 子组件 ──

function RecapCard({ hasUs }: { hasUs: boolean }) {
  const [recap, setRecap] = useState<GmRecap | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const gen = async () => {
    if (loading) return;
    if (recap) { setOpen(!open); return; }
    setLoading(true);
    try {
      const r = await gmApi.recap();
      if (!r.error) { setRecap(r); setOpen(true); }
    } catch { /* ignore */ }
    setLoading(false);
  };

  return (
    <div onClick={gen} style={{
      background: `linear-gradient(135deg, #1a2340, ${GM.PANEL})`,
      borderRadius: GM_RADIUS.md, padding: '14px 14px', marginBottom: 12,
      border: `1px solid ${GM.LINE}`, cursor: 'pointer',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>🌙 隔夜复盘</span>
        <span style={{ fontSize: 10, color: GM.DATA, marginLeft: 'auto' }}>
          {loading ? '⏳ 生成中…' : recap ? (open ? '收起 ▲' : '展开 ▼') : hasUs ? '点击生成 →' : ''}
        </span>
      </div>
      {!recap && (
        <div style={{ fontSize: 11, color: GM.MUTED, marginTop: 4 }}>
          {hasUs ? '汇总你的美股自选昨夜表现与今日关注(AI生成, 约15秒)' : '添加美股自选后可用'}
        </div>
      )}
      {recap && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.6 }}>
            {recap.ai.headline}
            <a href="/gm/recap" onClick={e => e.stopPropagation()} style={{
              fontSize: 10, color: GM.DATA, marginLeft: 8, textDecoration: 'none',
            }}>完整页 →</a>
          </div>
          {open && (
            <div style={{ marginTop: 8 }} onClick={e => e.stopPropagation()}>
              {recap.ai.highlights.map((h, i) => (
                <div key={i} style={{ fontSize: 11, lineHeight: 1.8, color: GM.TEXT }}>· {h}</div>
              ))}
              {recap.ai.watch_today?.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: 10, color: GM.MACRO, fontWeight: 700 }}>今日关注</div>
                  {recap.ai.watch_today.map((w, i) => (
                    <div key={i} style={{ fontSize: 11, lineHeight: 1.7, color: GM.MUTED }}>· {w}</div>
                  ))}
                </div>
              )}
              <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 6 }}>
                {recap.date} 交易日 · {recap.disclaimer}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GuardianCard({ watch }: { watch: GmWatchItem[] }) {
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<GmWatchItem | null>(null);
  const [result, setResult] = useState<GmGuardian | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async (s: GmWatchItem) => {
    setSel(s); setResult(null); setLoading(true);
    try {
      const r = await gmApi.guardian(s.market, s.code);
      if (!r.error) setResult(r);
    } catch { /* ignore */ }
    setLoading(false);
  };

  const stanceColor = (st: string) =>
    st === '继续持有' ? GM.UP : st === '警惕风险' ? GM.DOWN : GM.MACRO;

  if (watch.length === 0) return null;

  return (
    <div style={{
      background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: '13px 14px', marginBottom: 12,
      border: `1px solid ${GM.LINE}`,
    }}>
      <div onClick={() => setOpen(!open)} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>🛡 AI 持仓管家</span>
        <span style={{ fontSize: 10, color: GM.MUTED }}>多空辩论 · 综合裁判</span>
        <span style={{ fontSize: 10, color: GM.DATA, marginLeft: 'auto' }}>{open ? '收起 ▲' : '启动 ▼'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 6 }}>
            {watch.map(s => (
              <button key={`${s.market}:${s.code}`} onClick={() => run(s)} disabled={loading} style={{
                border: `1px solid ${sel?.code === s.code ? GM.BRAND : GM.LINE}`,
                background: sel?.code === s.code ? 'rgba(255,91,101,.12)' : GM.BG,
                color: GM.TEXT, borderRadius: 999, padding: '5px 12px', fontSize: 11,
                whiteSpace: 'nowrap', cursor: 'pointer',
              }}>{s.market === 'US' ? '🇺🇸' : '🇭🇰'} {s.name}</button>
            ))}
          </div>
          {loading && (
            <div style={{ fontSize: 12, color: GM.MUTED, padding: '14px 0', textAlign: 'center' }}>
              ⏳ 多头/空头研究员辩论中,裁判审议(约40秒)…
            </div>
          )}
          {result && (
            <div style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{
                  fontSize: 13, fontWeight: 800, padding: '3px 12px', borderRadius: 999,
                  color: stanceColor(result.judge.stance), background: GM.BG,
                  border: `1px solid ${stanceColor(result.judge.stance)}`,
                }}>{result.judge.stance}</span>
                <span style={{ fontSize: 10, color: GM.MUTED }}>置信度 {result.judge.confidence}%</span>
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.6, marginBottom: 8 }}>
                {result.judge.one_line}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1, background: 'rgba(47,212,159,.07)', borderRadius: 10, padding: 8 }}>
                  <div style={{ fontSize: 10, color: GM.UP, fontWeight: 700, marginBottom: 4 }}>🐂 多头</div>
                  {result.bull.points.slice(0, 3).map((p, i) => (
                    <div key={i} style={{ fontSize: 10, lineHeight: 1.6, color: GM.TEXT }}>· {p}</div>
                  ))}
                </div>
                <div style={{ flex: 1, background: 'rgba(255,91,101,.07)', borderRadius: 10, padding: 8 }}>
                  <div style={{ fontSize: 10, color: GM.DOWN, fontWeight: 700, marginBottom: 4 }}>🐻 空头</div>
                  {result.bear.points.slice(0, 3).map((p, i) => (
                    <div key={i} style={{ fontSize: 10, lineHeight: 1.6, color: GM.TEXT }}>· {p}</div>
                  ))}
                </div>
              </div>
              <div style={{ fontSize: 10, color: GM.MACRO, marginTop: 8, lineHeight: 1.6 }}>
                ⚠ 风控: {result.judge.risk_control}
              </div>
              <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 6 }}>
                {result.sources.join(' · ')}<br />{result.disclaimer}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SmallBtn({ children, onClick, active }: {
  children: React.ReactNode; onClick: () => void; active?: boolean;
}) {
  return (
    <button onClick={onClick} style={{
      border: `1px solid ${active ? GM.BRAND : GM.LINE}`, cursor: 'pointer',
      background: active ? 'rgba(255,91,101,.12)' : GM.PANEL,
      color: active ? GM.BRAND : GM.TEXT,
      borderRadius: 999, padding: '5px 14px', fontSize: 12, fontWeight: 600,
    }}>{children}</button>
  );
}

function Hint({ text }: { text: string }) {
  return <div style={{ color: GM.MUTED, padding: 40, textAlign: 'center', fontSize: 13 }}>{text}</div>;
}

function NeedLogin() {
  return (
    <div style={{ textAlign: 'center', padding: '60px 24px' }}>
      <div style={{ fontSize: 36, marginBottom: 12 }}>🔐</div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>请先登录</div>
      <div style={{ fontSize: 12, color: GM.MUTED, lineHeight: 1.7 }}>
        从微信服务号菜单进入可自动登录；<br />
        或先到 A 股端登录后再切换到全球市场。
      </div>
      <a href="/wx/home" style={{
        display: 'inline-block', marginTop: 18, padding: '8px 22px',
        background: GM.PANEL2, color: GM.TEXT, borderRadius: 999,
        fontSize: 13, textDecoration: 'none', border: `1px solid ${GM.LINE}`,
      }}>去 A 股端登录 →</a>
    </div>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px' }}>
      <div style={{ fontSize: 36, marginBottom: 12 }}>🌐</div>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>还没有美港股自选</div>
      <div style={{ fontSize: 12, color: GM.MUTED, marginBottom: 18 }}>
        搜索"苹果 / AAPL / 腾讯 / 00700"添加第一只
      </div>
      <button onClick={onAdd} style={{
        padding: '9px 26px', background: GM.BRAND, color: '#fff', border: 'none',
        borderRadius: 999, fontSize: 14, fontWeight: 600, cursor: 'pointer',
      }}>+ 添加自选</button>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, color: GM.MUTED, margin: '10px 2px 6px' }}>{title}</div>
      <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, overflow: 'hidden' }}>
        {children}
      </div>
    </div>
  );
}

function StockRow({ item, quote, editMode, onRemove, onPosition, onClick }: {
  item: GmWatchItem; quote?: GmQuote; editMode: boolean;
  onRemove: () => void; onPosition: () => void; onClick: () => void;
}) {
  return (
    <div onClick={editMode ? undefined : onClick} style={{
      display: 'flex', alignItems: 'center', padding: '11px 13px',
      borderBottom: `1px solid ${GM.LINE}`, cursor: editMode ? 'default' : 'pointer',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {quote?.name || item.name}
        </div>
        <div style={{ fontSize: 10, color: GM.MUTED, marginTop: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
          <span>{item.code}</span>
          {quote?.exchange && <span>{quote.exchange}</span>}
          {item.market === 'HK' && quote?.lot_size && (
            <span style={{ background: GM.PANEL2, borderRadius: 4, padding: '1px 5px' }}>每手{quote.lot_size}</span>
          )}
          {quote?.earnings_in_days != null && quote.earnings_in_days <= 14 && (
            <span style={{ color: GM.BRAND, background: 'rgba(255,91,101,.12)', borderRadius: 4, padding: '1px 5px' }}>
              财报{quote.earnings_in_days === 0 ? '今日' : `${quote.earnings_in_days}天`}
            </span>
          )}
          {quote?.ext_label && quote?.ext_price != null && (
            <span style={{ color: GM.DATA }}>{quote.ext_label} {quote.ext_price}</span>
          )}
          {quote?.delayed && (
            <span style={{ color: GM.MACRO }}>延迟15分</span>
          )}
        </div>
      </div>
      {quote ? (
        <div style={{ textAlign: 'right', marginLeft: 8 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: gmPriceColor(quote.change_pct) }}>
            {gmFmtPrice(quote.price, quote.currency)}
          </div>
          <div style={{ fontSize: 11, color: gmPriceColor(quote.change_pct) }}>
            {gmFmtPct(quote.change_pct)}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 11, color: GM.MUTED }}>--</div>
      )}
      {editMode && (
        <>
          <button onClick={(e) => { e.stopPropagation(); onPosition(); }} style={{
            marginLeft: 8, border: `1px solid ${GM.LINE}`, background: GM.PANEL2,
            color: GM.TEXT, borderRadius: 8, padding: '6px 10px', fontSize: 12, cursor: 'pointer',
          }}>仓位</button>
          <button onClick={(e) => { e.stopPropagation(); onRemove(); }} style={{
            marginLeft: 6, border: 'none', background: 'rgba(255,91,101,.15)',
            color: GM.DOWN, borderRadius: 8, padding: '6px 10px', fontSize: 12, cursor: 'pointer',
          }}>删除</button>
        </>
      )}
    </div>
  );
}

function PositionSheet({ item, onClose, onSaved }: {
  item: GmWatchItem; onClose: () => void; onSaved: () => void;
}) {
  const [shares, setShares] = useState('');
  const [cost, setCost] = useState('');
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await gmApi.setPosition(item.code,
        shares ? parseFloat(shares) : null,
        cost ? parseFloat(cost) : null);
      onSaved();
    } catch { setBusy(false); }
  };

  const inputStyle = {
    width: '100%', boxSizing: 'border-box' as const, padding: '10px 12px',
    background: GM.BG, border: `1px solid ${GM.LINE}`, borderRadius: 10,
    color: GM.TEXT, fontSize: 14, outline: 'none', marginBottom: 10,
  };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 200,
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', maxWidth: 480, background: GM.PANEL,
        borderRadius: '18px 18px 0 0', padding: 16,
        paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))',
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: GM.TEXT }}>
          录入仓位 · {item.name}({item.code})
        </div>
        <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 6 }}>持有股数</div>
        <input value={shares} onChange={e => setShares(e.target.value)} type="number"
               placeholder={item.market === 'HK' ? '如 100(注意每手股数)' : '如 10'} style={inputStyle} />
        <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 6 }}>
          买入成本({item.market === 'US' ? '美元' : '港币'}/股)
        </div>
        <input value={cost} onChange={e => setCost(e.target.value)} type="number"
               placeholder="选填" style={inputStyle} />
        <button onClick={save} disabled={busy} style={{
          width: '100%', padding: '11px 0', border: 'none', borderRadius: 10,
          background: GM.BRAND, color: '#fff', fontSize: 14, fontWeight: 700,
          cursor: 'pointer', opacity: busy ? 0.6 : 1,
        }}>{busy ? '保存中…' : '保存'}</button>
        <div style={{ fontSize: 10, color: GM.MUTED, marginTop: 8, textAlign: 'center' }}>
          保存后首页出现"组合市值/今日盈亏"双币汇总卡
        </div>
      </div>
    </div>
  );
}

function SearchSheet({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<SearchItem[]>([]);
  const [busy, setBusy] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!q.trim()) { setResults([]); return; }
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await gmApi.search(q.trim());
        // gm端只收美股/港股
        setResults(r.items.filter(i => i.market === 'US' || i.market === 'HK'));
      } catch { setResults([]); }
    }, 350);
  }, [q]);

  const add = async (s: SearchItem) => {
    setBusy(s.code);
    try {
      await gmApi.addWatch({ code: s.code, name: s.name, market: s.market,
                             exchange: s.exchange, asset_type: s.asset_type });
      onAdded();
    } catch { setBusy(''); }
  };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 200,
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', maxWidth: 480, maxHeight: '75vh', overflow: 'auto',
        background: GM.PANEL, borderRadius: '18px 18px 0 0', padding: 16,
        paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))',
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10, color: GM.TEXT }}>添加美股 / 港股</div>
        <input
          autoFocus value={q} onChange={e => setQ(e.target.value)}
          placeholder="输入代码或名称: AAPL / 苹果 / 00700 / 腾讯"
          style={{
            width: '100%', boxSizing: 'border-box', padding: '10px 12px',
            background: GM.BG, border: `1px solid ${GM.LINE}`, borderRadius: 10,
            color: GM.TEXT, fontSize: 14, outline: 'none',
          }}
        />
        <div style={{ marginTop: 10 }}>
          {results.map(s => (
            <div key={`${s.market}:${s.code}`} style={{
              display: 'flex', alignItems: 'center', padding: '10px 4px',
              borderBottom: `1px solid ${GM.LINE}`,
            }}>
              <span style={{ fontSize: 16, marginRight: 8 }}>{s.market === 'US' ? '🇺🇸' : '🇭🇰'}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: GM.TEXT }}>{s.name}</div>
                <div style={{ fontSize: 10, color: GM.MUTED }}>{s.code} · {s.type_name || s.market}</div>
              </div>
              <button onClick={() => add(s)} disabled={busy === s.code} style={{
                border: 'none', background: GM.BRAND, color: '#fff', borderRadius: 999,
                padding: '6px 16px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                opacity: busy === s.code ? 0.5 : 1,
              }}>{busy === s.code ? '…' : '添加'}</button>
            </div>
          ))}
          {q.trim() && results.length === 0 && (
            <div style={{ color: GM.MUTED, fontSize: 12, textAlign: 'center', padding: 20 }}>
              没有找到美股/港股结果
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
