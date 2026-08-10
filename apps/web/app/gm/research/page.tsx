'use client';
// gm研究页: 选自选股 → 量化择时(规则模型·即时) + 深度研究(AI结论卡·按需生成)
import { useEffect, useState } from 'react';
import { GM, GM_RADIUS, gmFmtPct } from '../../lib/gm/theme';
import { gmApi, getToken, GmWatchItem, GmKpred, GmResearch, GmFiling, GmQuote } from '../../lib/gm/api';

export default function GmResearchPage() {
  const [watch, setWatch] = useState<GmWatchItem[]>([]);
  const [sel, setSel] = useState<GmWatchItem | null>(null);
  const [kpred, setKpred] = useState<GmKpred | null>(null);
  const [research, setResearch] = useState<GmResearch | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [filings, setFilings] = useState<GmFiling[]>([]);
  const [filingSrc, setFilingSrc] = useState('');

  const [recs, setRecs] = useState<{ text: string; item: GmWatchItem }[]>([]);

  useEffect(() => {
    if (!getToken()) { setLoaded(true); return; }
    gmApi.watchlist().then(async list => {
      setWatch(list);
      if (list.length) setSel(list[0]);
      // 智能推荐: 异动≥2% → 深度研究; 财报≤7天 → 关注
      try {
        const q = await gmApi.quotes(list.map(s => `${s.market}:${s.code}`));
        const qm: Record<string, GmQuote> = {};
        q.items.forEach(it => { qm[`${it.market}:${it.code}`] = it; });
        const out: { text: string; item: GmWatchItem }[] = [];
        for (const s of list) {
          const qq = qm[`${s.market}:${s.code}`];
          if (!qq) continue;
          if (qq.earnings_in_days != null && qq.earnings_in_days <= 7) {
            out.push({ text: `${s.name} 财报${qq.earnings_in_days === 0 ? '今日' : qq.earnings_in_days + '天后'} · 建议提前研究`, item: s });
          } else if (qq.change_pct != null && Math.abs(qq.change_pct) >= 2) {
            out.push({ text: `${s.name} ${qq.change_pct > 0 ? '涨' : '跌'}${Math.abs(qq.change_pct).toFixed(1)}% · 建议深度研究`, item: s });
          }
        }
        setRecs(out.slice(0, 3));
      } catch { /* ignore */ }
    }).catch(() => {}).finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    setKpred(null); setResearch(null); setFilings([]);
    if (!sel) return;
    gmApi.kpred(sel.market, sel.code).then(k => { if (!k.error) setKpred(k); }).catch(() => {});
    gmApi.scout(sel.market, sel.code).then(r => { setFilings(r.items); setFilingSrc(r.source); }).catch(() => {});
  }, [sel]);

  const runResearch = async () => {
    if (!sel || aiLoading) return;
    setAiLoading(true);
    try {
      const r = await gmApi.research(sel.market, sel.code);
      if (!r.error) setResearch(r);
    } catch { /* ignore */ }
    setAiLoading(false);
  };

  if (!loaded) return <Muted center>加载中…</Muted>;
  if (watch.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 24px' }}>
        <div style={{ fontSize: 36, marginBottom: 12 }}>🔬</div>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>先添加自选股</div>
        <div style={{ fontSize: 12, color: GM.MUTED }}>到"持仓"tab 添加美股/港股后, 这里可做择时与深度研究</div>
      </div>
    );
  }

  return (
    <div>
      {/* 智能推荐 */}
      {recs.length > 0 && (
        <div style={{
          background: 'rgba(157,124,255,.08)', border: `1px solid ${GM.AI}`,
          borderRadius: GM_RADIUS.md, padding: '10px 12px', marginBottom: 10,
        }}>
          <div style={{ fontSize: 10, color: GM.AI, fontWeight: 700, marginBottom: 4 }}>💡 智能推荐 · 基于你的自选</div>
          {recs.map((r, i) => (
            <div key={i} onClick={() => setSel(r.item)} style={{
              fontSize: 12, lineHeight: 1.9, cursor: 'pointer', color: GM.TEXT,
            }}>· {r.text} →</div>
          ))}
        </div>
      )}

      {/* 选股chips */}
      <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 8, marginBottom: 6 }}>
        {watch.map(s => (
          <button key={`${s.market}:${s.code}`} onClick={() => setSel(s)} style={{
            border: `1px solid ${sel?.code === s.code ? GM.BRAND : GM.LINE}`,
            background: sel?.code === s.code ? 'rgba(255,91,101,.12)' : GM.PANEL,
            color: GM.TEXT, borderRadius: 999, padding: '6px 14px', fontSize: 12,
            whiteSpace: 'nowrap', cursor: 'pointer', fontWeight: sel?.code === s.code ? 700 : 400,
          }}>
            {s.market === 'US' ? '🇺🇸' : '🇭🇰'} {s.name}
          </button>
        ))}
      </div>

      {/* 量化择时卡 */}
      <Card title="🎯 量化择时" right={kpred?.model}>
        {!kpred ? <Muted>计算中…</Muted> : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
              <div style={{
                fontSize: 30, fontWeight: 800,
                color: kpred.signal === '偏多' ? GM.UP : kpred.signal === '偏空' ? GM.DOWN : GM.MACRO,
              }}>{kpred.score}</div>
              <div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{kpred.signal}</div>
                <div style={{ fontSize: 10, color: GM.MUTED }}>
                  RSI {kpred.rsi14} · 20日 {gmFmtPct(kpred.chg20d_pct)}
                </div>
              </div>
            </div>
            {kpred.reasons.map((r, i) => (
              <div key={i} style={{ fontSize: 11, lineHeight: 1.9 }}>· {r}</div>
            ))}
          </>
        )}
      </Card>

      {/* 一手情报(官方公告) */}
      <Card title="🔍 一手情报" right={filingSrc}>
        {filings.length === 0 ? <Muted>加载中或暂无公告…</Muted> : filings.map((f, i) => (
          <a key={i} href={f.url} target="_blank" rel="noreferrer" style={{
            display: 'block', textDecoration: 'none', padding: '7px 0',
            borderBottom: i < filings.length - 1 ? `1px solid ${GM.LINE}` : 'none',
          }}>
            <div style={{ fontSize: 12, color: GM.TEXT, lineHeight: 1.5 }}>
              <span style={{
                fontSize: 9, color: GM.DATA, border: `1px solid ${GM.LINE}`,
                borderRadius: 4, padding: '1px 5px', marginRight: 6,
              }}>{f.form}</span>
              {f.title}
            </div>
            <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 2 }}>{f.date}</div>
          </a>
        ))}
      </Card>

      {/* 深度研究 */}
      <Card title="📊 深度研究" right="Gemini · 按需生成">
        {!research && (
          <button onClick={runResearch} disabled={aiLoading} style={{
            width: '100%', padding: '11px 0', border: 'none', borderRadius: 10,
            background: aiLoading ? GM.PANEL2 : GM.AI, color: '#fff',
            fontSize: 14, fontWeight: 700, cursor: 'pointer',
          }}>{aiLoading ? '⏳ AI 分析中(约20秒)…' : '✦ 生成投前研究结论'}</button>
        )}
        {research && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: GM.AI, fontWeight: 700 }}>✦ AI 结论</span>
              <span style={{ fontSize: 10, color: GM.MUTED }}>置信度 {research.ai.confidence}%</span>
            </div>
            <div style={{ fontSize: 14, fontWeight: 700, lineHeight: 1.6, marginBottom: 10 }}>
              {research.ai.headline}
            </div>
            <Fold label="支持证据" items={research.ai.supporting} color={GM.UP} />
            <Fold label="反对/风险" items={research.ai.opposing} color={GM.DOWN} />
            {research.ai.key_numbers?.length > 0 && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
                {research.ai.key_numbers.map((k, i) => (
                  <div key={i} style={{ background: GM.BG, borderRadius: 8, padding: '6px 10px' }}>
                    <div style={{ fontSize: 9, color: GM.MUTED }}>{k.label}</div>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>{k.value}</div>
                    <div style={{ fontSize: 8, color: GM.MUTED }}>{k.context}</div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ fontSize: 10, color: GM.MACRO, lineHeight: 1.7 }}>⚠ 风险边界: {research.ai.risk_boundary}</div>
            <div style={{ fontSize: 10, color: GM.MUTED, lineHeight: 1.7 }}>🔄 推翻条件: {research.ai.invalidation}</div>
            <div style={{ fontSize: 9, color: GM.MUTED, marginTop: 8, borderTop: `1px solid ${GM.LINE}`, paddingTop: 6 }}>
              来源: {research.sources.join(' · ')}<br />{research.disclaimer}
            </div>
          </div>
        )}
      </Card>

      {/* 投研助手对话 */}
      <AssistantChat sel={sel} />

      <div style={{ textAlign: 'center', fontSize: 10, color: GM.MUTED, marginTop: 10 }}>
        技术信号与AI结论仅供研究 · 不构成投资建议
      </div>
    </div>
  );
}

function AssistantChat({ sel }: { sel: GmWatchItem | null }) {
  const [msgs, setMsgs] = useState<{ role: 'user' | 'assistant'; content: string }[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput('');
    const next = [...msgs, { role: 'user' as const, content: q }];
    setMsgs(next);
    setBusy(true);
    try {
      const r = await gmApi.assistantChat(q,
        sel ? { market: sel.market, code: sel.code } : null, next.slice(-8));
      setMsgs([...next, { role: 'assistant', content: r.reply }]);
    } catch {
      setMsgs([...next, { role: 'assistant', content: '(AI暂时不可用, 请稍后重试)' }]);
    }
    setBusy(false);
  };

  const QUICK = sel
    ? [`${sel.name}最近怎么样?`, `${sel.name}财报要点是什么?`, '当前有哪些风险?']
    : ['美股本周有什么重要财报?', 'ETF热度说明什么?'];

  return (
    <Card title="💬 投研助手" right={sel ? `当前讨论: ${sel.name}` : '通用问答'}>
      {msgs.length === 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          {QUICK.map(q => (
            <button key={q} onClick={() => setInput(q)} style={{
              border: `1px solid ${GM.LINE}`, background: GM.BG, color: GM.MUTED,
              borderRadius: 999, padding: '5px 10px', fontSize: 11, cursor: 'pointer',
            }}>{q}</button>
          ))}
        </div>
      )}
      <div style={{ maxHeight: 300, overflowY: 'auto', marginBottom: 8 }}>
        {msgs.map((m, i) => (
          <div key={i} style={{
            display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            marginBottom: 8,
          }}>
            <div style={{
              maxWidth: '85%', padding: '8px 11px', borderRadius: 12, fontSize: 12,
              lineHeight: 1.7, whiteSpace: 'pre-wrap',
              background: m.role === 'user' ? GM.BRAND : GM.BG,
              color: m.role === 'user' ? '#fff' : GM.TEXT,
            }}>{m.content}</div>
          </div>
        ))}
        {busy && <div style={{ fontSize: 11, color: GM.MUTED }}>⏳ 思考中…</div>}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <input value={input} onChange={e => setInput(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') send(); }}
               placeholder={sel ? `问关于 ${sel.name} 的问题…` : '输入你的问题…'}
               style={{
                 flex: 1, padding: '9px 12px', background: GM.BG,
                 border: `1px solid ${GM.LINE}`, borderRadius: 10,
                 color: GM.TEXT, fontSize: 13, outline: 'none',
               }} />
        <button onClick={send} disabled={busy} style={{
          border: 'none', background: GM.AI, color: '#fff', borderRadius: 10,
          padding: '0 18px', fontSize: 13, fontWeight: 700, cursor: 'pointer',
          opacity: busy ? 0.5 : 1,
        }}>发送</button>
      </div>
    </Card>
  );
}

function Card({ title, right, children }: { title: string; right?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{title}</span>
        {right && <span style={{ fontSize: 9, color: GM.MUTED }}>{right}</span>}
      </div>
      {children}
    </div>
  );
}

function Fold({ label, items, color }: { label: string; items: string[]; color: string }) {
  if (!items?.length) return null;
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ fontSize: 10, color, fontWeight: 700, marginBottom: 2 }}>{label}</div>
      {items.map((s, i) => (
        <div key={i} style={{ fontSize: 11, color: GM.TEXT, lineHeight: 1.7 }}>· {s}</div>
      ))}
    </div>
  );
}

function Muted({ children, center }: { children: React.ReactNode; center?: boolean }) {
  return <div style={{ fontSize: 12, color: GM.MUTED, textAlign: center ? 'center' : 'left', padding: center ? 40 : 4 }}>{children}</div>;
}
