'use client';
// gm消息中心: 自选事件流(告警/异动/财报临近/官方公告) + 告警规则管理
import { useCallback, useEffect, useState } from 'react';
import { GM, GM_RADIUS } from '../../lib/gm/theme';
import { gmApi, getToken, GmMessage, GmAlertRule, GmWatchItem } from '../../lib/gm/api';

const LEVEL_STYLE: Record<string, { label: string; color: string }> = {
  high: { label: '高', color: '#FF5B65' },
  mid: { label: '中', color: '#F2C66D' },
  watch: { label: '观察', color: '#2FD49F' },
};

export default function GmMessages() {
  const [items, setItems] = useState<GmMessage[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'nologin'>('loading');
  const [rules, setRules] = useState<GmAlertRule[]>([]);
  const [showAdd, setShowAdd] = useState(false);

  const loadRules = useCallback(() => {
    gmApi.alerts().then(r => setRules(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!getToken()) { setState('nologin'); return; }
    gmApi.messages()
      .then(r => { setItems(r.items); setState('ready'); })
      .catch(() => setState('ready'));
    loadRules();
  }, [loadRules]);

  if (state === 'loading') return <Center>汇总自选事件中…</Center>;
  if (state === 'nologin') return <Center>请先登录后查看消息</Center>;

  return (
    <div>
      <div style={{ fontSize: 11, color: GM.MUTED, margin: '2px 2px 8px' }}>
        基于你的自选自动聚合: 异动 ≥3% · 7天内财报 · 最新官方公告
      </div>
      {items.length === 0 && (
        <div style={{ textAlign: 'center', padding: '50px 20px' }}>
          <div style={{ fontSize: 32, marginBottom: 10 }}>🔕</div>
          <div style={{ fontSize: 13, color: GM.MUTED }}>自选股暂无值得关注的事件</div>
        </div>
      )}
      {items.map((m, i) => {
        const lv = LEVEL_STYLE[m.level] || LEVEL_STYLE.watch;
        const inner = (
          <div style={{
            background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: '12px 13px', marginBottom: 10,
            borderLeft: `3px solid ${lv.color}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>{m.icon}</span>
              <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{m.title}</span>
              <span style={{ fontSize: 9, color: lv.color, border: `1px solid ${lv.color}`,
                             borderRadius: 5, padding: '1px 6px' }}>{lv.label}</span>
            </div>
            <div style={{ fontSize: 11, color: GM.MUTED, marginTop: 4 }}>
              {m.desc} · {m.type}
            </div>
          </div>
        );
        return m.url
          ? <a key={i} href={m.url} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>{inner}</a>
          : <div key={i}>{inner}</div>;
      })}
      {/* 告警规则管理 */}
      <div style={{ marginTop: 18 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 700 }}>⚙ 我的告警规则({rules.filter(r => r.enabled).length}条启用)</span>
          <button onClick={() => setShowAdd(true)} style={{
            border: `1px solid ${GM.LINE}`, background: GM.PANEL, color: GM.TEXT,
            borderRadius: 999, padding: '5px 14px', fontSize: 12, cursor: 'pointer', fontWeight: 600,
          }}>+ 新建</button>
        </div>
        {rules.length === 0 && (
          <div style={{ background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: 14, fontSize: 11, color: GM.MUTED }}>
            还没有规则。例:AAPL 涨跌超5%时微信提醒 / NVDA 财报前3天提醒
          </div>
        )}
        {rules.map(r => (
          <div key={r.id} style={{
            background: GM.PANEL, borderRadius: GM_RADIUS.md, padding: '10px 13px', marginBottom: 8,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {r.market === 'US' ? '🇺🇸' : '🇭🇰'} {r.name || r.code}
              </div>
              <div style={{ fontSize: 10, color: GM.MUTED, marginTop: 2 }}>
                {r.rule_type === 'change_abs' ? `涨跌幅 ≥ ${r.threshold}% 时微信提醒` : `财报前 ${r.threshold} 天提醒`}
                {r.last_triggered_at && ` · 上次触发 ${r.last_triggered_at.slice(5, 16).replace('T', ' ')}`}
              </div>
            </div>
            <button onClick={() => { gmApi.patchAlert(r.id, { enabled: !r.enabled }).then(loadRules); }} style={{
              border: 'none', borderRadius: 999, padding: '4px 12px', fontSize: 11, cursor: 'pointer',
              background: r.enabled ? 'rgba(47,212,159,.15)' : GM.PANEL2,
              color: r.enabled ? GM.UP : GM.MUTED, fontWeight: 600,
            }}>{r.enabled ? '开' : '关'}</button>
            <button onClick={() => { gmApi.delAlert(r.id).then(loadRules); }} style={{
              border: 'none', background: 'rgba(255,91,101,.12)', color: GM.DOWN,
              borderRadius: 8, padding: '4px 9px', fontSize: 11, cursor: 'pointer',
            }}>删</button>
          </div>
        ))}
        <div style={{ fontSize: 10, color: GM.MUTED, marginTop: 6 }}>
          触发后微信模板消息推送 · 冷却4小时 · 夜间(0-8点)仅推≥5%高风险异动
        </div>
      </div>

      {showAdd && <AddRuleSheet onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); loadRules(); }} />}
    </div>
  );
}

function AddRuleSheet({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [watch, setWatch] = useState<GmWatchItem[]>([]);
  const [sel, setSel] = useState<GmWatchItem | null>(null);
  const [ruleType, setRuleType] = useState<'change_abs' | 'earnings_soon'>('change_abs');
  const [threshold, setThreshold] = useState('5');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    gmApi.watchlist().then(list => { setWatch(list); if (list.length) setSel(list[0]); }).catch(() => {});
  }, []);

  const save = async () => {
    if (!sel || busy) return;
    setBusy(true);
    try {
      await gmApi.addAlert({ market: sel.market, code: sel.code, name: sel.name,
                             rule_type: ruleType, threshold: parseFloat(threshold) || 5 });
      onAdded();
    } catch { setBusy(false); }
  };

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', zIndex: 200,
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: '100%', maxWidth: 480, background: GM.PANEL, borderRadius: '18px 18px 0 0',
        padding: 16, paddingBottom: 'calc(16px + env(safe-area-inset-bottom, 0px))',
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 10 }}>新建告警规则</div>
        <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 6 }}>选择标的</div>
        <div style={{ display: 'flex', gap: 6, overflowX: 'auto', paddingBottom: 8 }}>
          {watch.map(s => (
            <button key={`${s.market}:${s.code}`} onClick={() => setSel(s)} style={{
              border: `1px solid ${sel?.code === s.code ? GM.BRAND : GM.LINE}`,
              background: sel?.code === s.code ? 'rgba(255,91,101,.12)' : GM.BG,
              color: GM.TEXT, borderRadius: 999, padding: '5px 12px', fontSize: 11,
              whiteSpace: 'nowrap', cursor: 'pointer',
            }}>{s.market === 'US' ? '🇺🇸' : '🇭🇰'} {s.name}</button>
          ))}
        </div>
        <div style={{ fontSize: 11, color: GM.MUTED, margin: '8px 0 6px' }}>规则类型</div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <button onClick={() => { setRuleType('change_abs'); setThreshold('5'); }} style={{
            flex: 1, padding: '9px 0', borderRadius: 10, cursor: 'pointer', fontSize: 12,
            border: `1px solid ${ruleType === 'change_abs' ? GM.BRAND : GM.LINE}`,
            background: ruleType === 'change_abs' ? 'rgba(255,91,101,.12)' : GM.BG, color: GM.TEXT,
          }}>📈 涨跌幅异动</button>
          <button onClick={() => { setRuleType('earnings_soon'); setThreshold('3'); }} style={{
            flex: 1, padding: '9px 0', borderRadius: 10, cursor: 'pointer', fontSize: 12,
            border: `1px solid ${ruleType === 'earnings_soon' ? GM.BRAND : GM.LINE}`,
            background: ruleType === 'earnings_soon' ? 'rgba(255,91,101,.12)' : GM.BG, color: GM.TEXT,
          }}>📅 财报临近(美股)</button>
        </div>
        <div style={{ fontSize: 11, color: GM.MUTED, marginBottom: 6 }}>
          {ruleType === 'change_abs' ? '阈值(涨跌幅% ≥)' : '提前天数'}
        </div>
        <input value={threshold} onChange={e => setThreshold(e.target.value)} type="number" style={{
          width: '100%', boxSizing: 'border-box', padding: '10px 12px', background: GM.BG,
          border: `1px solid ${GM.LINE}`, borderRadius: 10, color: GM.TEXT, fontSize: 14,
          outline: 'none', marginBottom: 12,
        }} />
        <button onClick={save} disabled={busy || !sel} style={{
          width: '100%', padding: '11px 0', border: 'none', borderRadius: 10,
          background: GM.BRAND, color: '#fff', fontSize: 14, fontWeight: 700,
          cursor: 'pointer', opacity: busy ? 0.6 : 1,
        }}>{busy ? '保存中…' : '保存规则'}</button>
      </div>
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div style={{ textAlign: 'center', padding: 50, fontSize: 12, color: GM.MUTED }}>{children}</div>;
}
