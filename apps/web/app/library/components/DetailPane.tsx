'use client'
// 右侧详情面板 · 按 3 类不同展示 · 320px 可折叠
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
}

export default function DetailPane({ source, tool, skill, onClose }: Props) {
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
        {source && <SourceDetail item={source} />}
        {tool && <ToolDetail item={tool} />}
        {skill && <SkillDetail item={skill} />}
      </div>
    </aside>
  )
}

function SourceDetail({ item }: { item: DataSourceItem }) {
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
      <div style={{ fontSize: 11, color: HUNTER.SOFT }}>操作(Phase 2 上线): 试运行 · 配 key · 健康趋势图</div>
    </div>
  )
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

function SkillDetail({ item }: { item: CatalogSkillItem }) {
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
      <div style={{ fontSize: 11, color: HUNTER.SOFT }}>
        操作(Phase 2 上线): {item.builtin ? '试用(填入 chat)' : '编辑 · 删除 · 试用'}
      </div>
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
