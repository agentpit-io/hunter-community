'use client'
import React, { useEffect, useState } from 'react'
import { Shield, Key, RefreshCw, Upload, CheckCircle2, AlertTriangle, Trash2, Database, FileSpreadsheet, Lock } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

const SAMPLE_SETTLEMENT_CSV = `成交日期,证券代码,证券名称,行业,买卖方向,成交数量,成交均价,成本价,发生金额,手续费
2026-10-29,600036.SH,招商银行,银行,买入,2000,36.85,33.40,73700.00,12.50
2026-11-05,510300.SH,华泰柏瑞沪深300ETF,宽基ETF,买入,10000,3.985,3.720,39850.00,5.00
2026-11-10,600000.SH,浦发银行,银行,买入,5000,10.52,9.80,52600.00,8.20`

export function BrokerConnectorPanel() {
  const [statusData, setStatusData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [banner, setBanner] = useState<{ tone: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [accountPreview, setAccountPreview] = useState<any>(null)

  // OAuth 授权表单
  const [brokerId, setBrokerId] = useState('galaxy_broker')
  const [brokerName, setBrokerName] = useState('中国银河证券官方只读通道')
  const [accountId, setAccountId] = useState('GZ-88001928')
  const [accessToken, setAccessToken] = useState('gz_oauth_ro_20261113_9f2a8b7c6d5e')

  // 主备路由表单
  const [allowFallback, setAllowFallback] = useState(true)
  const [simulateTimeout, setSimulateTimeout] = useState(false)

  // 手工交割单导入表单
  const [csvFilename, setCsvFilename] = useState('galaxy_settlement_20261113.csv')
  const [csvContent, setCsvContent] = useState(SAMPLE_SETTLEMENT_CSV)

  const getHeaders = () => {
    let token = ''
    try {
      token = localStorage.getItem('hunter_token') || ''
    } catch {
      /* ignore */
    }
    return {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }
  }

  const loadStatus = async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/broker/status`, {
        headers: getHeaders(),
        cache: 'no-store',
      })
      const d = await r.json()
      setStatusData(d)
      if (d?.routing) {
        setAllowFallback(Boolean(d.routing.allow_fallback))
        setSimulateTimeout(Boolean(d.routing.simulate_primary_timeout))
      }
      if (d?.oauth?.authorized) {
        const posRes = await fetch(`${API}/api/broker/account/positions`, {
          headers: getHeaders(),
          cache: 'no-store',
        })
        if (posRes.ok) {
          setAccountPreview(await posRes.json())
        }
      } else {
        setAccountPreview(null)
      }
    } catch (e: any) {
      setBanner({ tone: 'err', text: `加载券商连接器状态失败: ${e?.message || e}` })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadStatus()
  }, [])

  const handleAuthorize = async () => {
    setBusy(true)
    setBanner(null)
    try {
      const r = await fetch(`${API}/api/broker/oauth/authorize`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          broker_id: brokerId,
          broker_name: brokerName,
          account_id: accountId,
          access_token: accessToken,
          scopes: ['account.read', 'summary.read', 'positions.read', 'trades.read'],
          ttl_hours: 24,
        }),
      })
      const d = await r.json()
      if (!r.ok || !d.ok) {
        setBanner({ tone: 'err', text: d.message || '开启只读授权失败' })
      } else {
        setBanner({ tone: 'ok', text: d.message })
        await loadStatus()
      }
    } catch (e: any) {
      setBanner({ tone: 'err', text: String(e?.message || e) })
    } finally {
      setBusy(false)
    }
  }

  const handleRevoke = async () => {
    setBusy(true)
    setBanner(null)
    try {
      const r = await fetch(`${API}/api/broker/oauth/revoke`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          reason: '用户在设置页点击一键撤销券商只读授权',
        }),
      })
      const d = await r.json()
      setAccountPreview(null)
      setBanner({
        tone: 'warn',
        text: d.message || '已一键撤销授权并销毁 AES-256-GCM 密文凭证，后续持仓调用 100% 拒绝',
      })
      await loadStatus()
    } catch (e: any) {
      setBanner({ tone: 'err', text: String(e?.message || e) })
    } finally {
      setBusy(false)
    }
  }

  const handleSaveRouting = async (nextAllowFallback: boolean, nextSimTimeout: boolean) => {
    setBusy(true)
    setBanner(null)
    try {
      const r = await fetch(`${API}/api/broker/routing/config`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          primary_provider_id: brokerId,
          allow_fallback: nextAllowFallback,
          merge_strategy: 'primary_then_fallback',
          simulate_primary_timeout: nextSimTimeout,
        }),
      })
      const d = await r.json()
      if (r.ok && d.ok) {
        setAllowFallback(nextAllowFallback)
        setSimulateTimeout(nextSimTimeout)
        setBanner({ tone: 'ok', text: d.message })
        await loadStatus()
      } else {
        setBanner({ tone: 'err', text: d.message || '保存路由配置失败' })
      }
    } catch (e: any) {
      setBanner({ tone: 'err', text: String(e?.message || e) })
    } finally {
      setBusy(false)
    }
  }

  const handleManualImport = async () => {
    setBusy(true)
    setBanner(null)
    try {
      const r = await fetch(`${API}/api/broker/manual-import`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          filename: csvFilename,
          import_type: 'settlement_csv',
          csv_content: csvContent,
        }),
      })
      const d = await r.json()
      if (r.ok && d.ok) {
        setBanner({ tone: 'ok', text: d.message })
        await loadStatus()
      } else {
        setBanner({ tone: 'err', text: d.message || '解析交割单失败' })
      }
    } catch (e: any) {
      setBanner({ tone: 'err', text: String(e?.message || e) })
    } finally {
      setBusy(false)
    }
  }

  const oauth = statusData?.oauth || {}
  const isAuthorized = Boolean(oauth.authorized)
  const manualBatch = statusData?.manual_import || null
  const auditLogs = statusData?.recent_audit_logs || []

  return (
    <div data-testid="broker-connector-panel" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* 顶部合规横幅 */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Shield size={20} style={{ color: 'var(--blue)' }} />
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: 'var(--text)' }}>
              合作券商数据源与只读授权管理（v3.4.0 · M4）
            </h2>
          </div>
          <span
            data-testid="broker-oauth-status-badge"
            style={{
              padding: '4px 12px',
              borderRadius: 999,
              fontSize: 12.5,
              fontWeight: 600,
              background: isAuthorized ? 'rgba(16,185,129,0.14)' : 'rgba(239,68,68,0.14)',
              color: isAuthorized ? '#10b981' : '#ef4444',
              border: `1px solid ${isAuthorized ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)'}`,
            }}
          >
            {isAuthorized ? `已连接只读授权 (${oauth.account_masked})` : `未连接 / 默认关闭 (${oauth.status || 'unauthorized'})`}
          </span>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '10px 0 0', lineHeight: 1.6 }}>
          安全红线承诺：① 个人账户连接器（<code>broker_account_readonly</code>）<strong>默认关闭</strong>，仅在您单独开启 OAuth 只读授权后生效；
          ② 授权凭证强制使用 <strong>AES-256-GCM</strong> 在服务端加密托管，绝不进入大模型提示词或日志；
          ③ <strong>物理隔离实盘交易</strong>，不包含任何下单、撤单、改单或银证转账接口；点击“一键撤销授权”立即销毁凭证并清空缓存。
        </p>

        {banner && (
          <div
            data-testid="broker-action-banner"
            style={{
              marginTop: 14,
              padding: '10px 14px',
              borderRadius: 8,
              fontSize: 13,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background:
                banner.tone === 'ok'
                  ? 'rgba(16,185,129,0.12)'
                  : banner.tone === 'warn'
                  ? 'rgba(245,158,11,0.14)'
                  : 'rgba(239,68,68,0.14)',
              color:
                banner.tone === 'ok' ? '#10b981' : banner.tone === 'warn' ? '#f59e0b' : '#ef4444',
              border: '1px solid currentColor',
            }}
          >
            {banner.tone === 'ok' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
            <span>{banner.text}</span>
          </div>
        )}
      </div>

      {/* 卡片 1：合作券商主备数据源路由配置 */}
      <div
        data-testid="broker-routing-card"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Database size={16} style={{ color: 'var(--blue)' }} />
          <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: 'var(--text)' }}>
            1. 合作券商主备数据源路由策略（禁跨源取均值）
          </h3>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, fontSize: 13 }}>
          <div style={{ padding: 12, background: 'var(--bg-panel)', borderRadius: 8, border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>主数据源（优先）</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text)' }}>
              中国银河证券官方数据源 (<code>galaxy_broker</code>)
            </div>
          </div>
          <div style={{ padding: 12, background: 'var(--bg-panel)', borderRadius: 8, border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>备用兜底源（超时降级）</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: 'var(--text)' }}>
              公开权威数据源 (<code>akshare_public_fallback</code>)
            </div>
          </div>
          <div style={{ padding: 12, background: 'var(--bg-panel)', borderRadius: 8, border: '1px solid var(--border)' }}>
            <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>多源融合合规锁</div>
            <div style={{ fontWeight: 600, marginTop: 4, color: '#10b981' }}>
              单一源整包切换 · 严禁跨源取平均值
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginTop: 14, alignItems: 'center' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', color: 'var(--text)' }}>
            <input
              data-testid="checkbox-allow-fallback"
              type="checkbox"
              checked={allowFallback}
              onChange={e => void handleSaveRouting(e.target.checked, simulateTimeout)}
            />
            允许主源超时自动降级至备用公开源（醒目标注 <code>is_fallback=true</code>）
          </label>
          <button
            data-testid="btn-toggle-primary-timeout"
            type="button"
            disabled={busy}
            onClick={() => void handleSaveRouting(allowFallback, !simulateTimeout)}
            style={{
              padding: '6px 12px',
              borderRadius: 6,
              border: '1px solid var(--border)',
              background: 'var(--bg-panel)',
              color: 'var(--text)',
              fontSize: 12.5,
              cursor: 'pointer',
            }}
          >
            {simulateTimeout ? '恢复主源正常连接' : '模拟主源 HTTP 504 超时演练'}
          </button>
        </div>
      </div>

      {/* 卡片 2：P2 个人账户 OAuth 只读授权与一键撤销 */}
      <div
        data-testid="broker-oauth-card"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Key size={16} style={{ color: 'var(--blue)' }} />
          <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: 'var(--text)' }}>
            2. 个人证券账户 OAuth 只读连接器（AES-256-GCM 加密托管 · 一键撤销）
          </h3>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 14 }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>合作券商通道</label>
            <select
              data-testid="select-broker-id"
              value={brokerId}
              onChange={e => {
                setBrokerId(e.target.value)
                const map: Record<string, string> = {
                  galaxy_broker: '中国银河证券官方只读通道',
                  huaxi_broker: '华西证券官方只读通道',
                  dongguan_broker: '东莞证券官方只读通道',
                }
                setBrokerName(map[e.target.value] || '合作证券公司只读通道')
              }}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: 6,
                border: '1px solid var(--border)',
                background: 'var(--bg-panel)',
                color: 'var(--text)',
                fontSize: 13,
              }}
            >
              <option value="galaxy_broker">中国银河证券 (galaxy_broker)</option>
              <option value="huaxi_broker">华西证券 (huaxi_broker)</option>
              <option value="dongguan_broker">东莞证券 (dongguan_broker)</option>
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>资金账号（自动脱敏存储）</label>
            <input
              data-testid="input-broker-account-id"
              type="text"
              value={accountId}
              onChange={e => setAccountId(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: 6,
                border: '1px solid var(--border)',
                background: 'var(--bg-panel)',
                color: 'var(--text)',
                fontSize: 13,
                boxSizing: 'border-box',
              }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              OAuth 短期只读 Token（AES-256-GCM 托管）
            </label>
            <input
              data-testid="input-broker-oauth-token"
              type="password"
              value={accessToken}
              onChange={e => setAccessToken(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                borderRadius: 6,
                border: '1px solid var(--border)',
                background: 'var(--bg-panel)',
                color: 'var(--text)',
                fontSize: 13,
                boxSizing: 'border-box',
              }}
            />
          </div>
        </div>

        {/* 当前授权状态详情 */}
        <div
          data-testid="broker-oauth-details"
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'var(--bg-panel)',
            border: '1px solid var(--border)',
            fontSize: 12.5,
            lineHeight: 1.8,
            marginBottom: 14,
          }}
        >
          <div>
            <strong>当前授权状态：</strong>
            <span style={{ color: isAuthorized ? '#10b981' : '#ef4444', fontWeight: 600 }}>
              {isAuthorized ? '已授权 (authorized)' : `未连接 (${oauth.status || 'unauthorized'})`}
            </span>
            {' · '}
            <strong>加密算法：</strong>
            <code>{oauth.encryption_alg || 'AES-256-GCM'}</code>
            {' · '}
            <strong>脱敏账号：</strong>
            <code>{oauth.account_masked || '—'}</code>
            {' · '}
            <strong>凭证掩码：</strong>
            <code>{oauth.token_masked || '—（已销毁/未托管）'}</code>
          </div>
          <div>
            <strong>已授予只读 Scope：</strong>
            {Array.isArray(oauth.scopes) && oauth.scopes.length > 0 ? (
              oauth.scopes.map((s: string) => (
                <code key={s} style={{ marginRight: 6, padding: '1px 6px', background: 'var(--bg-card)', borderRadius: 4 }}>
                  {s}
                </code>
              ))
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>无（所有 broker_account_readonly 工具调用将被 hookd 100% 拦截）</span>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <button
            data-testid="btn-broker-authorize"
            type="button"
            disabled={busy}
            onClick={() => void handleAuthorize()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--blue)',
              color: '#fff',
              fontSize: 13,
              fontWeight: 600,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            <Lock size={14} /> 开启券商只读授权（AES-256-GCM 托管）
          </button>

          <button
            data-testid="btn-broker-revoke"
            type="button"
            disabled={busy}
            onClick={() => void handleRevoke()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 16px',
              borderRadius: 8,
              border: '1px solid #ef4444',
              background: 'rgba(239,68,68,0.12)',
              color: '#ef4444',
              fontSize: 13,
              fontWeight: 600,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            <Trash2 size={14} /> 一键撤销授权（销毁凭证并清空缓存）
          </button>

          <button
            data-testid="btn-broker-refresh"
            type="button"
            disabled={busy || loading}
            onClick={() => void loadStatus()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid var(--border)',
              background: 'var(--bg-panel)',
              color: 'var(--text)',
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={14} /> 刷新状态
          </button>
        </div>

        {/* 已授权时的只读持仓快照预览 */}
        {isAuthorized && accountPreview?.data?.positions && (
          <div data-testid="authorized-positions-preview" style={{ marginTop: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text)' }}>
              只读持仓快照预览（来自 <code>broker_account_readonly.get_positions</code> · 账号 {accountPreview.data.account_id}）
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left', color: 'var(--text-muted)' }}>
                    <th style={{ padding: '6px 8px' }}>证券代码</th>
                    <th style={{ padding: '6px 8px' }}>名称</th>
                    <th style={{ padding: '6px 8px' }}>行业</th>
                    <th style={{ padding: '6px 8px' }}>持仓数量</th>
                    <th style={{ padding: '6px 8px' }}>成本价</th>
                    <th style={{ padding: '6px 8px' }}>最新价</th>
                    <th style={{ padding: '6px 8px' }}>最新市值(元)</th>
                    <th style={{ padding: '6px 8px' }}>仓位占比</th>
                  </tr>
                </thead>
                <tbody>
                  {accountPreview.data.positions.map((pos: any) => (
                    <tr key={pos.symbol} style={{ borderBottom: '1px dashed var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontFamily: 'monospace' }}>{pos.symbol}</td>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>{pos.name}</td>
                      <td style={{ padding: '6px 8px' }}>{pos.sector}</td>
                      <td style={{ padding: '6px 8px' }}>{pos.quantity}</td>
                      <td style={{ padding: '6px 8px' }}>{pos.cost_price}</td>
                      <td style={{ padding: '6px 8px' }}>{pos.last_price}</td>
                      <td style={{ padding: '6px 8px' }}>{Number(pos.market_value).toLocaleString('en-US', { minimumFractionDigits: 2 })}</td>
                      <td style={{ padding: '6px 8px' }}>{pos.weight_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* 卡片 3：未授权用户 Excel/CSV 交割单与持仓手工导入兜底通道 */}
      <div
        data-testid="broker-manual-import-card"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <FileSpreadsheet size={16} style={{ color: 'var(--blue)' }} />
          <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0, color: 'var(--text)' }}>
            3. Excel / CSV 交割单与持仓手工导入（不联网授权用户的离线兜底通道）
          </h3>
        </div>
        <p style={{ fontSize: 12.5, color: 'var(--text-muted)', margin: '0 0 12px', lineHeight: 1.6 }}>
          如果您倾向于不开启在线 OAuth 账户授权，可直接粘贴或上传从券商软件导出的 CSV / Excel 交割单文本，
          系统将在本地解析持仓集中度与历史交割记录，无缝接入 <code>portfolio_risk_check</code>（组合风险巡检）与 <code>after_close_review</code>（盘后复盘）。
        </p>

        <textarea
          data-testid="textarea-settlement-csv"
          rows={5}
          value={csvContent}
          onChange={e => setCsvContent(e.target.value)}
          style={{
            width: '100%',
            padding: 10,
            borderRadius: 8,
            border: '1px solid var(--border)',
            background: 'var(--bg-panel)',
            color: 'var(--text)',
            fontFamily: 'monospace',
            fontSize: 12,
            boxSizing: 'border-box',
            marginBottom: 10,
          }}
        />

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button
            data-testid="btn-upload-settlement-csv"
            type="button"
            disabled={busy}
            onClick={() => void handleManualImport()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--blue)',
              color: '#fff',
              fontSize: 13,
              fontWeight: 600,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            <Upload size={14} /> 解析并导入交割单与持仓
          </button>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            导入文件名：<code>{csvFilename}</code>
          </span>
        </div>

        {manualBatch && (
          <div
            data-testid="manual-import-summary-box"
            style={{
              marginTop: 14,
              padding: 12,
              borderRadius: 8,
              background: 'var(--bg-panel)',
              border: '1px solid var(--border)',
              fontSize: 12.5,
              lineHeight: 1.7,
            }}
          >
            <div>
              <strong>最新导入批次：</strong>
              <code>{manualBatch.batch_id}</code> · 共解析 <strong>{manualBatch.row_count}</strong> 条交割记录 · 汇总持仓{' '}
              <strong>{manualBatch.risk_summary?.position_count || 0}</strong> 只标的 · 总市值{' '}
              <strong>¥{Number(manualBatch.risk_summary?.total_market_value || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}</strong>
            </div>
            <div>
              <strong>单票最高集中度：</strong>
              <code>{manualBatch.risk_summary?.max_single_stock_symbol || '-'}</code> (
              <strong>{manualBatch.risk_summary?.max_single_stock_weight_pct || 0}%</strong>) ·{' '}
              <span style={{ color: '#f59e0b' }}>{manualBatch.risk_summary?.concentration_warning}</span>
            </div>
          </div>
        )}
      </div>

      {/* 卡片 4：broker_audit_log 合规审计留痕 */}
      <div
        data-testid="broker-audit-log-card"
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          padding: 20,
        }}
      >
        <h3 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 10px', color: 'var(--text)' }}>
          4. 券商接口与安全门卫合规审计留痕（<code>broker_audit_log</code>）
        </h3>
        <div
          style={{
            maxHeight: 200,
            overflowY: 'auto',
            padding: 10,
            borderRadius: 8,
            background: 'var(--bg-panel)',
            border: '1px solid var(--border)',
            fontFamily: 'monospace',
            fontSize: 12,
          }}
        >
          {auditLogs.length === 0 ? (
            <div style={{ color: 'var(--text-muted)' }}>暂无审计日志记录</div>
          ) : (
            auditLogs.map((log: any, idx: number) => (
              <div key={log.trace_id || idx} style={{ padding: '4px 0', borderBottom: '1px dashed var(--border)' }}>
                [{log.retrieved_at}] user=<strong>{log.user_id}</strong> mcp=<code>{log.mcp_server}.{log.tool_name}</code>{' '}
                provider=<code>{log.provider_id}</code> scope=<code>{log.license_scope}</code> status=
                <strong style={{ color: log.status_code === 'ok' ? '#10b981' : '#ef4444' }}>{log.status_code}</strong>{' '}
                {log.error_message ? `msg=${log.error_message}` : ''}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
