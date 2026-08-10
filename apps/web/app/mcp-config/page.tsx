'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, Plus, Trash2, RefreshCw, Zap, ExternalLink, X, Check, AlertCircle } from 'lucide-react'
import { HUNTER } from '../lib/hunter-theme'

interface McpItem {
  id: number
  name: string
  slug: string
  transport: 'sse' | 'http'
  endpoint: string
  headers: Record<string, string>
  api_key_hint: string
  enabled: boolean
  timeout_ms: number
  last_ok_at: string | null
  last_err: string
  call_count: number
  error_count: number
  created_at: string
  updated_at: string
  has_api_key: boolean
}

function authH(): Record<string, string> {
  const t = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }
}

async function apiCall<T = any>(method: string, path: string, body?: any): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: authH(),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let msg = text.slice(0, 200)
    try { msg = JSON.parse(text).detail || msg } catch {}
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

// ── 每一条都在 2026-08-10 用真实 initialize + tools/list 握手验过 ──────────
// 验证脚本与全量结果见 doc/codex/自定义MCP/04-实施与验收/2026-08-10_股票类MCP实测选型.md
// 删掉的旧条目(别再加回来):
//   mcp.polygon.io      → 全路径自签证书,SSL 握手就失败
//   mcp.newsapi.org     → 域名不存在(NXDOMAIN)
//   mcp.perplexity.ai   → 域名不存在(NXDOMAIN)
// 角标按「**取数**要不要 key」判定,不是按「列出工具要不要 key」——
// 两者会不一致:Bargo 不填 key 能列出 62 个工具,但一调用就 401。
// 只标"能列出"会让用户以为白嫖得到,加完发现取不到数,比不给模板更糟。
//   needsKey=false → 已实测 tools/call 无 key 返回真数据
//   needsKey=true  → tools/call 必须有 key
// verified: 'call' = 真取到数据 | 'list' = 只验到能列出工具,取数未验(缺真 key)
const OFFICIAL_TEMPLATES = [
  { icon: '📈', name: 'Alpha Vantage 美股', endpoint: 'https://mcp.alphavantage.co/mcp?apikey={API_KEY}',
    transport: 'http' as const, docs: 'https://www.alphavantage.co/support/#api-key',
    needsKey: true, verified: 'list' as const,
    desc: '131 个工具 · 美股/外汇/财报/技术指标 · 免费 25 次/天 · 工具清单已实测' },
  { icon: '🏛️', name: 'Bargo 机构级情报', endpoint: 'https://www.bargo.ai/mcp?token={API_KEY}',
    transport: 'http' as const, docs: 'https://www.bargo.ai',
    needsKey: true, verified: 'list' as const,
    desc: '62 个工具 · 13F持仓/内部人交易/国会交易/期权流 · 清单免 key,取数须填 key' },
  { icon: '🪙', name: 'CoinGecko 加密行情', endpoint: 'https://mcp.coingecko.com/mcp',
    transport: 'http' as const, docs: 'https://www.coingecko.com/en/api',
    needsKey: false, verified: 'call' as const,
    desc: '11 个工具 · 币价/K线/涨跌榜/新闻 · 完全免费 · 已取到真实数据' },
  { icon: '💼', name: 'Financial Datasets', endpoint: 'https://mcp.financialdatasets.ai/mcp',
    transport: 'http' as const, docs: 'https://www.financialdatasets.ai',
    needsKey: true, verified: 'list' as const,
    desc: '美股财报/资产负债表 · Bearer 认证 · 无 key 连工具清单都看不到' },
  { icon: '📊', name: 'Finnhub', endpoint: 'https://mcp.finnhub.io/mcp',
    transport: 'http' as const, docs: 'https://finnhub.io/register',
    needsKey: true, verified: 'list' as const,
    desc: '实时报价/公司档案/新闻 · Bearer 认证 · 免费档可用' },
  { icon: '🔢', name: 'Twelve Data', endpoint: 'https://mcp.twelvedata.com/mcp',
    transport: 'http' as const, docs: 'https://twelvedata.com/pricing',
    needsKey: true, verified: 'list' as const,
    desc: '全球股票/外汇/加密时间序列 · Bearer 认证 · 免费 800 次/天' },
  { icon: '🔍', name: 'Exa 联网搜索', endpoint: 'https://mcp.exa.ai/mcp',
    transport: 'http' as const, docs: 'https://exa.ai',
    needsKey: false, verified: 'call' as const,
    desc: '语义级网页搜索+抓取 · 补充财经新闻面 · 已取到真实数据' },
  { icon: '🕷️', name: 'Firecrawl 网页抓取', endpoint: 'https://mcp.firecrawl.dev/mcp',
    transport: 'http' as const, docs: 'https://firecrawl.dev',
    needsKey: false, verified: 'call' as const,
    desc: '抓任意公告/研报页转 Markdown · 已取到真实数据' },
]

export default function McpConfigPage() {
  const router = useRouter()
  const [items, setItems] = useState<McpItem[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [selectedTpl, setSelectedTpl] = useState<typeof OFFICIAL_TEMPLATES[number] | 'custom' | null>(null)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({ name: '', transport: 'http', endpoint: '', api_key: '', headers: '' })
  const [toast, setToast] = useState<{ msg: string, kind: 'ok' | 'err' } | null>(null)

  useEffect(() => {
    if (!localStorage.getItem('hunter_token')) {
      router.push('/login')
      return
    }
    void load()
  }, [router])

  const load = async () => {
    setLoading(true)
    try {
      const d = await apiCall<{ items: McpItem[] }>('GET', '/user_mcp')
      setItems(d.items || [])
      setErr('')
    } catch (e: any) {
      setErr(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  const showToast = (msg: string, kind: 'ok' | 'err' = 'ok') => {
    setToast({ msg, kind })
    setTimeout(() => setToast(null), 2600)
  }

  const openAdd = (tpl: typeof OFFICIAL_TEMPLATES[number] | 'custom' | null) => {
    setSelectedTpl(tpl)
    setForm(tpl && tpl !== 'custom'
      ? { name: tpl.name, transport: tpl.transport, endpoint: tpl.endpoint, api_key: '', headers: '' }
      : { name: '', transport: 'http', endpoint: '', api_key: '', headers: '' })
    setAddOpen(true)
  }

  const saveMcp = async () => {
    if (!form.name.trim() || !form.endpoint.trim()) {
      showToast('名称和 endpoint 必填', 'err'); return
    }
    // endpoint 里留了 {API_KEY} 占位符却没填 key → 渲染出来是空串,必然 401
    if (form.endpoint.includes('{API_KEY}') && !form.api_key.trim()) {
      showToast('该数据源要把 key 拼进 URL,请填写 API Key', 'err'); return
    }
    setSaving(true)
    try {
      let headers = {}
      if (form.headers.trim()) {
        try { headers = JSON.parse(form.headers) } catch { showToast('Headers 不是合法 JSON', 'err'); setSaving(false); return }
      }
      await apiCall('POST', '/user_mcp', {
        name: form.name.trim(),
        transport: form.transport,
        endpoint: form.endpoint.trim(),
        api_key: form.api_key.trim(),
        headers,
      })
      setAddOpen(false)
      await load()
      showToast('✅ MCP 已接入，无需审批 · 你在 chat 现在可以用了')
    } catch (e: any) {
      showToast(e?.message || '保存失败', 'err')
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async (m: McpItem) => {
    try {
      await apiCall('PATCH', `/user_mcp/${m.id}`, { enabled: !m.enabled })
      await load()
    } catch (e: any) { showToast(e?.message || '切换失败', 'err') }
  }

  const removeMcp = async (m: McpItem) => {
    if (!confirm(`删除「${m.name}」?`)) return
    try {
      await apiCall('DELETE', `/user_mcp/${m.id}`)
      await load()
      showToast('已删除')
    } catch (e: any) { showToast(e?.message || '删除失败', 'err') }
  }

  const testMcp = async (m: McpItem) => {
    showToast('正在测试...')
    try {
      const r = await apiCall<{ ok: boolean, tool_count?: number, duration_ms: number, error?: string }>(
        'POST', `/user_mcp/${m.id}/test`)
      if (r.ok) {
        showToast(`✓ 连通 · ${r.tool_count} 个 tool · ${r.duration_ms}ms`)
      } else {
        showToast(`✗ 失败：${r.error?.slice(0, 80)}`, 'err')
      }
      await load()
    } catch (e: any) { showToast(e?.message || '测试失败', 'err') }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#fbfbf8', color: HUNTER.INK, fontFamily: HUNTER.SANS }}>
      {/* 顶栏 */}
      <div style={{
        height: 56, padding: '0 26px',
        background: 'rgba(255,255,255,.72)', backdropFilter: 'blur(12px)',
        borderBottom: `1px solid ${HUNTER.LINE}`,
        display: 'flex', alignItems: 'center', gap: 14, position: 'sticky', top: 0, zIndex: 5,
      }}>
        <button onClick={() => router.push('/chat')} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 4, color: HUNTER.INK_F, fontSize: 13,
        }}>
          <ArrowLeft size={14} /> 返回对话
        </button>
        <div style={{ fontFamily: HUNTER.SERIF, fontSize: 17, fontWeight: 600, marginLeft: 8 }}>
          MCP 组件
        </div>
        <div style={{ color: HUNTER.INK_F, fontSize: 12 }}>
          · 接入你自己的数据源 · 无审批 · 秒生效
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <button
            onClick={() => openAdd('custom')}
            style={{
              height: 34, padding: '0 14px', background: HUNTER.THEME, color: '#fff',
              border: 'none', borderRadius: 10, cursor: 'pointer', fontSize: 13, fontWeight: 600,
              boxShadow: '0 4px 12px rgba(181,107,45,.2)',
            }}
          >
            + 添加 MCP Server
          </button>
        </div>
      </div>

      <div style={{ padding: '22px 26px 40px', maxWidth: 1200, margin: '0 auto' }}>
        {err && (
          <div style={{
            padding: '10px 14px', marginBottom: 16,
            background: '#fbeaea', color: HUNTER.UP, border: '1px solid #f3c9c9',
            borderRadius: 10, fontSize: 13, display: 'flex', gap: 8, alignItems: 'center',
          }}>
            <AlertCircle size={14} /> {err}
          </div>
        )}

        {/* 我的 MCP */}
        <div style={{ marginBottom: 26 }}>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
            <span style={{
              padding: '4px 10px', background: HUNTER.BRAND_PALE, color: HUNTER.COPPER3,
              borderRadius: 999, fontSize: 11, fontWeight: 600, marginRight: 8,
            }}>我的</span>
            自建 MCP <span style={{ color: HUNTER.INK_F, fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
              {items.length} 个 · 仅你可见
            </span>
          </div>

          {loading ? (
            <div style={{ padding: 40, textAlign: 'center', color: HUNTER.INK_F }}>加载中...</div>
          ) : items.length === 0 ? (
            <div style={{
              padding: '40px 20px', textAlign: 'center', color: HUNTER.INK_F,
              background: '#fff', border: `1px dashed ${HUNTER.LINE}`, borderRadius: 14,
            }}>
              <div style={{ fontSize: 36, marginBottom: 10, opacity: 0.5 }}>🔌</div>
              <div style={{ fontSize: 14, marginBottom: 16 }}>还没有自建 MCP · 从下面推荐里选一个开始</div>
              <button
                onClick={() => openAdd('custom')}
                style={{
                  padding: '8px 16px', background: HUNTER.THEME, color: '#fff',
                  border: 'none', borderRadius: 10, cursor: 'pointer', fontSize: 13, fontWeight: 600,
                }}
              >自定义 MCP →</button>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(320px,1fr))', gap: 14 }}>
              {items.map(m => (
                <div key={m.id} style={{
                  background: '#fff', border: `1px solid ${HUNTER.LINE}`, borderRadius: 14, padding: 18,
                  boxShadow: '0 4px 18px rgba(40,35,27,.025)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                    <div style={{
                      width: 38, height: 38, borderRadius: 10, background: HUNTER.BRAND_PALE,
                      color: HUNTER.COPPER3, display: 'grid', placeItems: 'center', fontSize: 16,
                    }}>{m.transport === 'sse' ? '📡' : '🔌'}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</div>
                      <div style={{ fontSize: 10.5, color: HUNTER.INK_F, fontFamily: 'ui-monospace,menlo,monospace' }}>
                        {m.transport} · slug={m.slug}
                      </div>
                    </div>
                    <span style={{
                      padding: '2px 8px', borderRadius: 999, fontSize: 10.5, fontWeight: 600,
                      background: m.last_err ? '#fbeaea' : m.enabled ? '#eef5f1' : HUNTER.PANEL_2,
                      color: m.last_err ? HUNTER.UP : m.enabled ? '#2c6b55' : HUNTER.INK_F,
                    }}>{m.last_err ? '🔴 错误' : m.enabled ? '🟢 启用' : '⚪ 停用'}</span>
                  </div>

                  <div style={{
                    padding: '6px 10px', background: HUNTER.PANEL, borderRadius: 6,
                    fontFamily: 'ui-monospace,menlo,monospace', fontSize: 11, color: HUNTER.COPPER3,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginBottom: 10,
                  }}>{m.endpoint}</div>

                  {m.has_api_key && (
                    <div style={{ fontSize: 11, color: HUNTER.INK_F, marginBottom: 8 }}>
                      🔒 API Key: <code style={{ color: HUNTER.COPPER3 }}>{m.api_key_hint || '****'}</code>
                    </div>
                  )}

                  {m.last_err && (
                    <div style={{
                      padding: '6px 8px', background: '#fbeaea', color: HUNTER.UP,
                      borderRadius: 6, fontSize: 11, marginBottom: 10, wordBreak: 'break-all',
                    }}>⚠ {m.last_err.slice(0, 150)}</div>
                  )}

                  <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8,
                    padding: '10px 0', borderTop: `1px dashed ${HUNTER.LINE}`,
                  }}>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 10, color: HUNTER.INK_F, textTransform: 'uppercase' }}>调用</div>
                      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2 }}>{m.call_count}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 10, color: HUNTER.INK_F, textTransform: 'uppercase' }}>错误</div>
                      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2,
                        color: m.error_count > 0 ? HUNTER.UP : HUNTER.INK }}>{m.error_count}</div>
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 10, color: HUNTER.INK_F, textTransform: 'uppercase' }}>状态</div>
                      <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2 }}>
                        {m.last_ok_at ? '✓' : '?'}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center' }}>
                    <button onClick={() => testMcp(m)} style={btnStyle}>
                      <Zap size={11} /> 测试
                    </button>
                    <button onClick={() => toggleEnabled(m)} style={btnStyle}>
                      {m.enabled ? '停用' : '启用'}
                    </button>
                    <button onClick={() => removeMcp(m)} style={{ ...btnStyle, color: HUNTER.UP, borderColor: '#f3c9c9' }}>
                      <Trash2 size={11} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 官方推荐 */}
        <div>
          <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
            <span style={{
              padding: '4px 10px', background: HUNTER.BRAND_PALE, color: HUNTER.COPPER3,
              borderRadius: 999, fontSize: 11, fontWeight: 600, marginRight: 8,
            }}>推荐</span>
            官方精选 MCP <span style={{ color: HUNTER.INK_F, fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
              一键接入 · 只需填 API key
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(280px,1fr))', gap: 14 }}>
            {OFFICIAL_TEMPLATES.map(tpl => (
              <div key={tpl.name} onClick={() => openAdd(tpl)} style={{
                background: 'linear-gradient(180deg,#fff9f0 0%,#fff 40%)',
                border: '1px solid #c8b39f', borderRadius: 14, padding: 18, cursor: 'pointer',
                transition: '.15s', boxShadow: '0 4px 18px rgba(40,35,27,.025)',
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-2px)'; e.currentTarget.style.boxShadow = '0 12px 24px rgba(40,35,27,.06)' }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.boxShadow = '0 4px 18px rgba(40,35,27,.025)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <div style={{ fontSize: 26 }}>{tpl.icon}</div>
                  <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700, flex: 1 }}>{tpl.name}</div>
                  <span style={{
                    padding: '2px 7px', borderRadius: 999, fontSize: 10, fontWeight: 600,
                    background: tpl.needsKey ? '#fbeaea' : '#eef5f1',
                    color: tpl.needsKey ? HUNTER.UP : '#2c6b55',
                  }}>{tpl.needsKey ? '需 key' : '免 key 直用'}</span>
                </div>
                <div style={{ color: HUNTER.INK_F, fontSize: 12, marginBottom: 8, lineHeight: 1.5 }}>{tpl.desc}</div>
                {/* 验证程度如实标注 —— 没拿真 key 验过取数的就别说"实测可用" */}
                <div style={{ fontSize: 10.5, color: HUNTER.INK_F, marginBottom: 8 }}>
                  {tpl.verified === 'call'
                    ? <span style={{ color: '#2c6b55' }}>✓ 已实测取到真实数据</span>
                    : <span>◐ 已实测连通与工具清单，取数待你的 key 验证</span>}
                </div>
                <div style={{ paddingTop: 10, borderTop: `1px solid ${HUNTER.PANEL_2}`, color: HUNTER.INK_F, fontSize: 11 }}>
                  🔗 <a href={tpl.docs} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}
                    style={{ color: HUNTER.THEME, textDecoration: 'none' }}>获取 API key →</a>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 安全说明 */}
        <div style={{
          marginTop: 32, padding: '18px 22px', background: HUNTER.PANEL, borderRadius: 12,
          fontSize: 12.5, color: HUNTER.INK_F, lineHeight: 1.75,
        }}>
          <b style={{ color: HUNTER.INK }}>安全说明</b><br />
          · API key 采用 AES-256-GCM 加密存储 · UI 只显示末 4 位 · 传输走 HTTPS<br />
          · 你的 MCP 数据不会被 Hunter 用于训练或分享给其他用户<br />
          · SSRF 防护：禁止填内网地址（127./10./172./192.）
        </div>
      </div>

      {/* 添加 MCP 弹窗 */}
      {addOpen && (
        <div onClick={() => setAddOpen(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(20,15,8,.55)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
          backdropFilter: 'blur(4px)',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 14, width: '100%', maxWidth: 560,
            maxHeight: 'calc(100vh - 60px)', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 30px 80px rgba(20,15,8,.35)',
          }}>
            <div style={{
              padding: '16px 20px', borderBottom: `1px solid ${HUNTER.LINE}`,
              display: 'flex', alignItems: 'center', gap: 10, fontFamily: HUNTER.SERIF, fontWeight: 600, fontSize: 15,
            }}>
              {selectedTpl && selectedTpl !== 'custom' && <span style={{ fontSize: 20 }}>{selectedTpl.icon}</span>}
              添加 {selectedTpl && selectedTpl !== 'custom' ? selectedTpl.name : '自定义 MCP'}
              <button onClick={() => setAddOpen(false)} style={{
                marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer',
                color: HUNTER.INK_F, fontSize: 20, lineHeight: 1,
              }}>×</button>
            </div>

            <div style={{ padding: '18px 20px', overflowY: 'auto', flex: 1 }}>
              <div style={{ marginBottom: 14 }}>
                <div style={inputLbl}>名称 <span style={{ color: HUNTER.UP }}>*</span></div>
                <input value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                  placeholder="Polygon 美股行情" maxLength={40} style={inputStyle} />
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={inputLbl}>Transport</div>
                <select value={form.transport} onChange={e => setForm({ ...form, transport: e.target.value })}
                  style={inputStyle}>
                  <option value="http">HTTP (JSON-RPC)</option>
                  <option value="sse">SSE (Server-Sent Events)</option>
                </select>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={inputLbl}>Endpoint <span style={{ color: HUNTER.UP }}>*</span></div>
                <input value={form.endpoint} onChange={e => setForm({ ...form, endpoint: e.target.value })}
                  placeholder="https://mcp.polygon.io/mcp"
                  style={{ ...inputStyle, fontFamily: 'ui-monospace,menlo,monospace', fontSize: 12.5 }} />
                <div style={inputHint}>
                  必须是 https:// · 禁止内网地址 · 若该数据源要求把 key 放在 URL 里，
                  用 <code style={{ color: HUNTER.THEME }}>{'{API_KEY}'}</code> 占位（例：
                  <code>…/mcp?apikey={'{API_KEY}'}</code>），别直接把 key 写进来
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={inputLbl}>
                  API Key
                  {form.endpoint.includes('{API_KEY}') && (
                    <span style={{ color: HUNTER.UP, marginLeft: 6 }}>* 该数据源必填</span>
                  )}
                </div>
                <input type="password" value={form.api_key}
                  onChange={e => setForm({ ...form, api_key: e.target.value })}
                  placeholder="sk_live_xxxxx"
                  style={{ ...inputStyle, fontFamily: 'ui-monospace,menlo,monospace', fontSize: 12.5 }} />
                <div style={inputHint}>
                  🔒 AES-256-GCM 加密存储 · UI 只显示后 4 位 ·
                  {form.endpoint.includes('{API_KEY}')
                    ? ' 发请求时才替换进 URL，不会明文存在 endpoint 里'
                    : ' 默认作为 Authorization: Bearer 发送'}
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={inputLbl}>附加 Headers <span style={{ color: HUNTER.INK_F, fontWeight: 400 }}>可选 · JSON</span></div>
                <textarea value={form.headers} onChange={e => setForm({ ...form, headers: e.target.value })}
                  placeholder='{"Referer": "https://hunter.agentpit.io"}' rows={2}
                  style={{ ...inputStyle, fontFamily: 'ui-monospace,menlo,monospace', fontSize: 12, resize: 'vertical' }} />
              </div>

              <div style={{
                padding: '10px 12px', background: '#eef1f7', color: '#3b558a', borderRadius: 8,
                fontSize: 11.5, lineHeight: 1.6,
              }}>
                💡 <b>保存后</b>：AES 加密 API key → 存 DB → 自动测试 endpoint 连通 → 通过后立即在 chat 里可用 · <b>无需重启 · 无需审批</b>
              </div>
            </div>

            <div style={{
              padding: '14px 20px', borderTop: `1px solid ${HUNTER.LINE}`, background: HUNTER.PANEL,
              display: 'flex', gap: 8,
            }}>
              <button onClick={() => setAddOpen(false)} style={{ ...btnStyle, flex: 1, height: 38 }}>取消</button>
              <button onClick={saveMcp} disabled={saving} style={{
                flex: 2, height: 38, background: HUNTER.THEME, color: '#fff', border: 'none',
                borderRadius: 10, cursor: saving ? 'not-allowed' : 'pointer',
                fontSize: 13, fontWeight: 600, opacity: saving ? 0.6 : 1,
                boxShadow: '0 4px 12px rgba(181,107,45,.2)',
              }}>{saving ? '保存中...' : '✓ 保存并启用'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', right: 24, bottom: 24, zIndex: 200,
          padding: '12px 18px',
          background: toast.kind === 'ok' ? '#2f725b' : '#8f4f1d',
          color: '#fff', borderRadius: 10, fontSize: 13, fontWeight: 600,
          boxShadow: '0 8px 24px rgba(0,0,0,.25)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>{toast.msg}</div>
      )}
    </div>
  )
}

const btnStyle: React.CSSProperties = {
  height: 28, padding: '0 10px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 8,
  background: '#fff', color: HUNTER.INK, fontSize: 12, fontWeight: 600, cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: 'inherit',
}
const inputLbl: React.CSSProperties = { fontSize: 12.5, fontWeight: 600, marginBottom: 6 }
const inputHint: React.CSSProperties = { fontSize: 11, color: HUNTER.INK_F, marginTop: 6 }
const inputStyle: React.CSSProperties = {
  width: '100%', padding: '9px 12px', background: '#fff',
  border: `1px solid ${HUNTER.LINE}`, borderRadius: 10, outline: 'none', fontSize: 13,
  fontFamily: 'inherit', boxSizing: 'border-box',
}
