'use client'
// 右侧详情面板 · 按 3 类不同展示 · 320px 可折叠
import { useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type {
  DataSourceItem, ToolItem, CatalogSkillItem,
} from '../../chat/lib/catalogClient'
import { statusDot } from '../../chat/lib/catalogClient'

interface Props {
  source?: DataSourceItem
  tool?: ToolItem
  skill?: CatalogSkillItem
  onClose: () => void
  /** SKILL 详情底部"填入 chat"按钮点击回调 · 未传则不显示 */
  onPickSkillToChat?: (item: CatalogSkillItem) => void
  /** 用户源被测试/删除后回调 · 让列表重新拉一次(计数与状态会变) */
  onChanged?: () => void
}

export default function DetailPane({ source, tool, skill, onClose, onPickSkillToChat, onChanged }: Props) {
  const empty = !source && !tool && !skill
  return (
    <aside style={paneStyle}>
      <div style={headStyle}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>
          {source ? '数据源详情' : tool ? '工具详情' : skill ? 'SKILL 详情' : '选中一项以查看'}
        </span>
        <button onClick={onClose} style={closeBtnStyle} title="收起">×</button>
      </div>
      <div style={{ padding: '12px 16px', overflowY: 'auto', flex: 1 }}>
        {empty && <div style={{ color: HUNTER.INK_F, fontSize: 12 }}>点左侧任一卡片查看详情</div>}
        {source && <SourceDetail item={source} onChanged={onChanged} />}
        {tool && <ToolDetail item={tool} />}
        {skill && <SkillDetail item={skill} onPickToChat={onPickSkillToChat} />}
      </div>
    </aside>
  )
}

function SourceDetail({ item, onChanged }: { item: DataSourceItem; onChanged?: () => void }) {
  const dot = statusDot(item.status)
  return (
    <div style={{ fontSize: 12, color: HUNTER.INK_S }}>
      <Row label="名称" value={item.name} />
      <Row label="市场" value={item.market_label || item.market} />
      <Row label="类型" value={item.kind_label || item.kind} />
      <Row label="提供方" value={item.provider} />
      <Row label="状态" value={
        <span><span style={{ color: dot.color }}>●</span> {dot.label}</span>
      } />
      {item.health && (
        <>
          <Divider />
          <div style={{ fontSize: 11, color: HUNTER.INK_F, marginBottom: 4 }}>健康度</div>
          <Row label="采样" value={`${item.health.samples} 次`} small />
          <Row label="成功率" value={`${(item.health.success_rate * 100).toFixed(1)}%`} small />
          {item.health.avg_ms != null && <Row label="平均延迟" value={`${item.health.avg_ms} ms`} small />}
          {item.health.last_error && (
            <Row label="上次错误" value={<code style={codeStyle}>{item.health.last_error}</code>} small />
          )}
        </>
      )}
      <Divider />
      <Row label="覆盖" value={item.volume_hint || '—'} />
      <Row label="需要 key" value={item.requires_key ? '是' : '否'} />
      {item.note && <Row label="说明" value={item.note} />}
      <Divider />
      {item.owner === 'user'
        ? <UserSourceActions item={item} onChanged={onChanged} />
        : <div style={{ fontSize: 11, color: HUNTER.SOFT }}>
            官方源 · 健康度由真实调用被动统计,无需手工测试
          </div>}
    </div>
  )
}

/** 用户自接的源 —— 测试 / 停用 / 删除。
 *
 * 「测一次」是老板明确要的:「接入后支持测试,点一下看看能不能连通」。
 * 它跑的是**完整链路**(连通 + 字段映射),不只是 ping ——
 * 连得通但映射不出价格的源,取数时照样降级,而用户会以为它是好的。
 * 后端把这两步分开报,因为用户的下一步动作完全不同。 */
function UserSourceActions({ item, onChanged }: {
  item: DataSourceItem
  onChanged?: () => void
}) {
  const [busy, setBusy] = useState('')
  const [res, setRes] = useState<any>(null)
  const id = item.key.startsWith('user.') ? item.key.slice(5) : ''

  async function call(method: string, path: string) {
    const h: Record<string, string> = { 'Content-Type': 'application/json' }
    const t = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
    if (t) h['Authorization'] = `Bearer ${t}`
    const r = await fetch(`/api/user_sources${path}`, { method, headers: h, cache: 'no-store' })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`)
    return d
  }

  async function test() {
    setBusy('test'); setRes(null)
    try { setRes(await call('POST', `/${id}/test`)) }
    catch (e: any) { setRes({ ok: false, stage: 'error', reason: e.message }) }
    finally { setBusy(''); onChanged?.() }
  }

  // 停用/启用**不在这里**做单条开关 —— 左下角「一键用官方默认」已经覆盖了
  // "我想暂时不用自己的源"这个场景,而且是批量的。这里再放一个单条开关,
  // 两处语义相近但作用范围不同,用户会分不清点哪个。真需要单条停用时再加。

  async function remove() {
    if (!window.confirm(`删除「${item.name}」?地址和 key 都会一并删掉,无法恢复。\n\n` +
                        `如果只是想暂时不用它,用左下角的「一键用官方默认」——那个是停用,可以切回来。`)) return
    setBusy('del')
    try { await call('DELETE', `/${id}`); onChanged?.() }
    catch (e: any) { setRes({ ok: false, stage: 'error', reason: e.message }) }
    finally { setBusy('') }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: res ? 8 : 0 }}>
        <button onClick={test} disabled={!!busy} style={{ ...actBtn, flex: 1 }}>
          {busy === 'test' ? '测试中…' : '⚡ 测一次'}
        </button>
        <button onClick={remove} disabled={!!busy} style={{ ...actBtn, color: '#9B3A22' }}>
          删除
        </button>
      </div>

      {res && (
        <div style={res.ok ? testOk : testWarn}>
          <div style={{ fontWeight: 600, marginBottom: 3 }}>
            {res.ok ? '✅ 通了' : res.stage === 'mapping' ? '⚠️ 连得通,但读不懂返回' : '⚠️ 连不上'}
            {res.duration_ms != null && ` · ${res.duration_ms}ms`}
          </div>
          {res.hint && <div style={{ marginBottom: res.reason || res.mapped ? 4 : 0 }}>{res.hint}</div>}
          {res.reason && <code style={codeStyle}>{res.reason}</code>}
          {res.mapped && (
            <pre style={preStyle}>{JSON.stringify(res.mapped, null, 1)}</pre>
          )}
          {/* 映射失败时把原始返回摆出来 —— 那是用户判断"该改映射还是换来源"
              唯一的依据,也是步 6 填自定义映射时要照着看的东西 */}
          {res.sample && (
            <>
              <div style={{ fontSize: 10.5, color: HUNTER.INK_F, margin: '5px 0 2px' }}>
                上游原始返回
              </div>
              <pre style={preStyle}>{res.sample}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

const actBtn: React.CSSProperties = {
  padding: '6px 12px', fontSize: 11.5, borderRadius: 7, cursor: 'pointer',
  background: HUNTER.PAPER, color: HUNTER.INK_S,
  border: `1px solid ${HUNTER.LINE}`, fontFamily: 'inherit',
}
const testOk: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11, lineHeight: 1.6,
  background: '#EAF4EE', color: '#2F6A4F',
}
const testWarn: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11, lineHeight: 1.6,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const preStyle: React.CSSProperties = {
  margin: '3px 0 0', padding: '5px 7px', borderRadius: 4, maxHeight: 160,
  overflow: 'auto', background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}`,
  fontSize: 10, lineHeight: 1.45, color: HUNTER.INK_S,
  fontFamily: 'ui-monospace, Menlo, monospace',
  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
}

function ToolDetail({ item }: { item: ToolItem }) {
  const dot = statusDot(item.status)
  return (
    <div style={{ fontSize: 12, color: HUNTER.INK_S }}>
      <Row label="名称" value={item.name} />
      <Row label="来源" value={item.origin_label || item.origin} />
      <Row label="服务" value={item.server_label || item.server} />
      <Row label="状态" value={<span><span style={{ color: dot.color }}>●</span> {dot.label}</span>} />
      <Divider />
      {item.summary && <Row label="说明" value={item.summary} />}
      {item.slow && <Row label="耗时" value="⏱ 慢速(可能 30s+)" />}
      {item.markets?.length > 0 && <Row label="覆盖市场" value={item.markets.join(' · ')} />}
      {item.needs_data?.length > 0 && (
        <Row label="依赖数据源" value={<span style={{ fontSize: 11 }}>{item.needs_data.join(', ')}</span>} />
      )}
      {item.blocked_by?.length > 0 && (
        <Row label="被阻塞于" value={<span style={{ color: HUNTER.UP }}>{item.blocked_by.join(', ')}</span>} />
      )}
      {item.need_key_for?.length > 0 && (
        <Row label="需 key" value={<span style={{ color: HUNTER.THEME }}>{item.need_key_for.join(', ')}</span>} />
      )}
      {item.note && <Row label="备注" value={item.note} />}
      <Divider />
      <div style={{ fontSize: 11, color: HUNTER.SOFT }}>操作(Phase 2 上线): 试运行(样例参数) · 查看 schema</div>
    </div>
  )
}

function SkillDetail({ item, onPickToChat }: { item: CatalogSkillItem; onPickToChat?: (i: CatalogSkillItem) => void }) {
  const dot = statusDot(item.status)
  return (
    <div style={{ fontSize: 12, color: HUNTER.INK_S }}>
      <Row label="名称" value={<span>{item.icon} {item.name}</span>} />
      <Row label="分类" value={item.category} />
      <Row label="品牌" value={item.brand || '—'} />
      <Row label="状态" value={<span><span style={{ color: dot.color }}>●</span> {dot.label}</span>} />
      <Row label="来源" value={item.builtin ? '内置' : (item.source_url ? '从 GitHub 装' : '自建')} />
      <Divider />
      <div style={{ fontSize: 11, color: HUNTER.INK_F, marginBottom: 4 }}>提问模板</div>
      <pre style={promptStyle}>{item.prompt_tpl}</pre>
      {item.hint && (
        <>
          <div style={{ fontSize: 11, color: HUNTER.INK_F, marginBottom: 4, marginTop: 10 }}>提示</div>
          <div style={{ fontSize: 12 }}>{item.hint}</div>
        </>
      )}
      {item.needs_tools?.length > 0 && (
        <>
          <Divider />
          <Row label="依赖工具" value={<span style={{ fontSize: 11 }}>{item.needs_tools.join(', ')}</span>} />
        </>
      )}
      {item.missing_tools?.length > 0 && (
        <Row label="缺失" value={<span style={{ color: HUNTER.UP }}>{item.missing_tools.join(', ')}</span>} />
      )}
      {item.blocked_tools?.length > 0 && (
        <Row label="被阻塞" value={<span style={{ color: HUNTER.THEME }}>{item.blocked_tools.join(', ')}</span>} />
      )}
      {item.source_url && (
        <Row label="源码" value={<a href={item.source_url} target="_blank" rel="noreferrer" style={{ color: HUNTER.THEME }}>{item.source_url}</a>} />
      )}
      <Divider />
      {onPickToChat && (
        <button
          onClick={() => onPickToChat(item)}
          title="把这段提问模板填入 Hunter chat 输入框"
          style={pickBtnStyle}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = HUNTER.COPPER3 }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = HUNTER.THEME }}
        >
          填入 Hunter chat →
        </button>
      )}
      {!item.builtin && (
        <div style={{ fontSize: 11, color: HUNTER.SOFT, marginTop: 8 }}>
          操作(Phase 2 上线): 编辑 · 删除
        </div>
      )}
    </div>
  )
}

function Row({ label, value, small }: { label: string; value: React.ReactNode; small?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 10, padding: small ? '2px 0' : '4px 0', alignItems: 'flex-start' }}>
      <span style={{ width: 70, color: HUNTER.INK_F, fontSize: small ? 11 : 12, flexShrink: 0 }}>{label}</span>
      <span style={{ flex: 1, fontSize: small ? 11 : 12, wordBreak: 'break-word' }}>{value}</span>
    </div>
  )
}

function Divider() {
  return <div style={{ height: 1, background: HUNTER.LINE, margin: '10px 0' }} />
}

const paneStyle: React.CSSProperties = {
  width: 320,
  minWidth: 320,
  height: '100%',
  background: '#fff',
  borderLeft: `1px solid ${HUNTER.LINE}`,
  display: 'flex',
  flexDirection: 'column',
}

const headStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '10px 14px',
  borderBottom: `1px solid ${HUNTER.LINE}`,
}

const closeBtnStyle: React.CSSProperties = {
  width: 24,
  height: 24,
  borderRadius: '50%',
  border: 'none',
  background: HUNTER.PANEL_2,
  color: HUNTER.INK_F,
  fontSize: 16,
  cursor: 'pointer',
  padding: 0,
  lineHeight: 1,
}

const codeStyle: React.CSSProperties = {
  display: 'block',
  padding: 6,
  background: HUNTER.PANEL_2,
  borderRadius: 4,
  fontSize: 11,
  color: HUNTER.INK_S,
  fontFamily: 'ui-monospace, monospace',
  wordBreak: 'break-all',
}

const pickBtnStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  background: HUNTER.THEME,
  color: '#fff',
  border: 'none',
  borderRadius: HUNTER.R_MD,
  fontSize: 13,
  fontWeight: 600,
  cursor: 'pointer',
  transition: 'background 0.15s',
}

const promptStyle: React.CSSProperties = {
  padding: 8,
  background: HUNTER.PANEL_2,
  borderRadius: 4,
  fontSize: 11,
  color: HUNTER.INK_S,
  fontFamily: 'ui-monospace, monospace',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  margin: 0,
}
