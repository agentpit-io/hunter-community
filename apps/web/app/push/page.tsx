'use client'
import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { Bell, Settings, Trash2, Send, CheckCircle2, AlertCircle, Plus } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

function authFetch(url: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch(url, { ...options, headers: { ...headers, ...(options.headers as Record<string, string> || {}) } })
}

interface Template {
  id: string; title: string; emoji: string; description: string
  default_time: string; default_content_type: string; color: string
}
interface Task {
  id: number; name: string; template_id: string; schedule_time: string
  content_type: string; custom_content: string; target_chat: string
  enabled: boolean; last_run_date: string | null; last_status: string; last_message: string
}

const CONTENT_LABEL: Record<string, string> = {
  news_today: '今日新闻速递', watchlist_morning: '自选股早报',
  close_review: '盘后复盘', fundflow_topn: '资金流向 TopN',
  weekly_summary: '周度持仓汇总', custom: '自定义文本',
}

export default function PushPage() {
  const [templates, setTemplates] = useState<Template[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [defaultChat, setDefaultChat] = useState('')
  const [feishuOk, setFeishuOk] = useState<boolean | null>(null)
  const [editing, setEditing] = useState<{ template?: Template; task?: Task } | null>(null)
  const [toast, setToast] = useState('')

  const reload = async () => {
    const [t, k, f] = await Promise.all([
      authFetch(`${API}/api/push/templates`).then(r => r.json()),
      authFetch(`${API}/api/push/tasks`).then(r => r.json()),
      authFetch(`${API}/api/feishu/config`).then(r => r.json()).catch(() => null),
    ])
    setTemplates(t.templates || [])
    setTasks(k.tasks || [])
    if (f?.configured !== undefined) {
      setFeishuOk(!!f.configured)
      setDefaultChat(f.home_channel || '')
    }
  }
  useEffect(() => { reload() }, [])

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000) }

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除这个推送任务？')) return
    await authFetch(`${API}/api/push/tasks/${id}`, { method: 'DELETE' })
    showToast('已删除'); reload()
  }
  const handleToggle = async (task: Task) => {
    await authFetch(`${API}/api/push/tasks/${task.id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !task.enabled }),
    }); reload()
  }
  const handleTest = async (id: number) => {
    showToast('正在试发...')
    const r = await authFetch(`${API}/api/push/test/${id}`, { method: 'POST' }).then(x => x.json())
    showToast(r.ok ? '✅ 已发送到飞书' : '❌ ' + (r.error || '失败')); reload()
  }

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <div className="flex-1 ml-52 p-6">
        <div className="mb-5">
          <h1 className="text-xl font-bold flex items-center gap-2" style={{ color: 'var(--text)' }}>
            <Bell className="w-5 h-5" style={{ color: 'var(--blue)' }} />推送中心
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            配置定时推送任务 · 推送执行记录请查看<a href="/push-manage" className="underline ml-1" style={{ color: 'var(--blue)' }}>推送管理</a>
          </p>
        </div>

        <div className="rounded-xl px-4 py-3 mb-5 flex items-center justify-between"
          style={{
            background: feishuOk ? 'rgba(34,197,94,0.08)' : 'rgba(245,158,11,0.08)',
            border: `1px solid ${feishuOk ? 'rgba(34,197,94,0.3)' : 'rgba(245,158,11,0.3)'}`,
          }}>
          <div className="flex items-center gap-2 text-sm">
            {feishuOk ? <CheckCircle2 className="w-4 h-4 text-green-600" /> : <AlertCircle className="w-4 h-4 text-amber-600" />}
            <span style={{ color: 'var(--text)' }}>
              {feishuOk
                ? `飞书已连接 · 默认推送目标 ${defaultChat ? defaultChat.slice(0, 12) + '...' : '未设置 FEISHU_HOME_CHANNEL'}`
                : '飞书未连接，先去信息渠道配置 App ID/Secret'}
            </span>
          </div>
          {!feishuOk && <a href="/channels" className="text-sm underline" style={{ color: 'var(--blue)' }}>去配置 →</a>}
        </div>

        <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>推荐模板</div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-8">
          {templates.map(tpl => (
            <div key={tpl.id} className="rounded-xl p-4 relative"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="flex items-start gap-2 mb-2">
                <div className="text-2xl">{tpl.emoji}</div>
                <div className="flex-1">
                  <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>{tpl.title}</div>
                  <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>建议时间：{tpl.default_time}</div>
                </div>
              </div>
              <p className="text-xs leading-relaxed mb-10" style={{ color: 'var(--text-muted)' }}>{tpl.description}</p>
              <button onClick={() => setEditing({ template: tpl })}
                className="absolute bottom-3 right-3 flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium"
                style={{ background: 'rgba(37,99,235,0.1)', color: 'var(--blue)' }}>
                <Settings className="w-3 h-3" />配置
              </button>
            </div>
          ))}
        </div>

        <div className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
          我的推送（{tasks.length}）
        </div>
        {tasks.length === 0 && (
          <div className="rounded-xl px-4 py-6 text-center text-sm"
            style={{ background: 'var(--bg-card)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
            还没有配置任何推送任务，点上面模板的"配置"按钮新增
          </div>
        )}
        <div className="space-y-2">
          {tasks.map(t => (
            <div key={t.id} className="rounded-xl p-4 flex items-center gap-4"
              style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="text-2xl font-mono" style={{ color: t.enabled ? 'var(--blue)' : 'var(--text-muted)' }}>
                {t.schedule_time}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm flex items-center gap-2" style={{ color: 'var(--text)' }}>
                  {t.name}
                  <span className="text-xs px-1.5 py-0.5 rounded"
                    style={{ background: 'rgba(0,0,0,0.05)', color: 'var(--text-muted)' }}>
                    {CONTENT_LABEL[t.content_type] || t.content_type}
                  </span>
                </div>
                <div className="text-xs mt-1 truncate" style={{ color: 'var(--text-muted)' }}>
                  {t.last_run_date
                    ? `上次推送 ${t.last_run_date} · ${t.last_status === 'ok' ? '✅' : '❌'} ${t.last_message || ''}`
                    : '未推送过'}
                </div>
              </div>
              <button onClick={() => handleTest(t.id)}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs"
                style={{ color: 'var(--blue)' }} title="立即试发一次">
                <Send className="w-3.5 h-3.5" />试发
              </button>
              <button onClick={() => handleToggle(t)}
                className="px-2.5 py-1.5 rounded-lg text-xs font-medium"
                style={{
                  background: t.enabled ? 'rgba(34,197,94,0.1)' : 'rgba(0,0,0,0.05)',
                  color: t.enabled ? 'rgb(22,163,74)' : 'var(--text-muted)',
                }}>
                {t.enabled ? '已启用' : '已停用'}
              </button>
              <button onClick={() => setEditing({ task: t })} className="p-1.5 rounded-lg" style={{ color: 'var(--text-muted)' }}>
                <Settings className="w-4 h-4" />
              </button>
              <button onClick={() => handleDelete(t.id)} className="p-1.5 rounded-lg" style={{ color: 'rgb(220,38,38)' }}>
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {editing && (
        <ConfigModal
          template={editing.template} task={editing.task} defaultChat={defaultChat}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); showToast('已保存') }}
        />
      )}
      {toast && (
        <div className="fixed bottom-6 right-6 px-4 py-2 rounded-lg text-sm shadow-lg z-50"
          style={{ background: 'var(--text)', color: 'var(--bg)' }}>{toast}</div>
      )}
    </div>
  )
}

function ConfigModal({ template, task, defaultChat, onClose, onSaved }: {
  template?: Template; task?: Task; defaultChat: string; onClose: () => void; onSaved: () => void
}) {
  const isEdit = !!task
  const [name, setName] = useState(task?.name || (template ? `${template.emoji} ${template.title}` : ''))
  const [time, setTime] = useState(task?.schedule_time || template?.default_time || '20:00')
  const [contentType, setContentType] = useState(task?.content_type || template?.default_content_type || 'custom')
  const [customContent, setCustomContent] = useState(task?.custom_content || '')
  const [targetChat, setTargetChat] = useState(task?.target_chat || defaultChat)
  const [enabled, setEnabled] = useState(task?.enabled ?? true)
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    const body = {
      name, schedule_time: time, content_type: contentType,
      custom_content: customContent, target_chat: targetChat, enabled,
      template_id: task?.template_id || template?.id || 'custom',
    }
    try {
      if (isEdit) {
        await fetch(`${API}/api/push/tasks/${task!.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        })
      } else {
        await authFetch(`${API}/api/push/tasks`, { method: 'POST', body: JSON.stringify(body) })
      }
      onSaved()
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.4)' }} onClick={onClose}>
      <div className="rounded-2xl w-full max-w-md p-6"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}>
        <h3 className="font-bold text-base mb-4" style={{ color: 'var(--text)' }}>
          {isEdit ? '编辑推送' : `配置${template?.emoji ? ' ' + template.emoji : ''} ${template?.title || '推送'}`}
        </h3>
        <div className="space-y-3 text-sm">
          <Field label="任务名称">
            <input value={name} onChange={e => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-lg outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
          </Field>
          <Field label="推送时间（HH:MM，每天此刻发送）">
            <input type="time" value={time} onChange={e => setTime(e.target.value)}
              className="w-full px-3 py-2 rounded-lg outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
          </Field>
          <Field label="推送内容">
            <select value={contentType} onChange={e => setContentType(e.target.value)}
              className="w-full px-3 py-2 rounded-lg outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }}>
              <option value="news_today">今日新闻速递</option>
              <option value="watchlist_morning">自选股早报</option>
              <option value="close_review">盘后复盘</option>
              <option value="fundflow_topn">资金流向 TopN</option>
              <option value="weekly_summary">周度持仓汇总</option>
              <option value="custom">自定义文本</option>
            </select>
          </Field>
          {(template?.id === 'custom' || contentType === 'custom') && (
            <Field label="自定义内容">
              <textarea value={customContent} onChange={e => setCustomContent(e.target.value)} rows={4}
                className="w-full px-3 py-2 rounded-lg outline-none resize-none"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
            </Field>
          )}
          <Field label="飞书目标群（chat_id）">
            <input value={targetChat} onChange={e => setTargetChat(e.target.value)} placeholder={defaultChat}
              className="w-full px-3 py-2 rounded-lg outline-none font-mono text-xs"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>留空使用默认 FEISHU_HOME_CHANNEL</div>
          </Field>
          <label className="flex items-center gap-2 cursor-pointer pt-1">
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
            <span style={{ color: 'var(--text)' }}>启用此推送</span>
          </label>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm" style={{ color: 'var(--text-muted)' }}>取消</button>
          <button onClick={handleSave} disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5"
            style={{ background: 'var(--blue)', color: '#fff' }}>
            <Plus className="w-3.5 h-3.5" />
            {saving ? '保存中...' : (isEdit ? '保存修改' : '创建推送')}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>{label}</div>
      {children}
    </div>
  )
}
