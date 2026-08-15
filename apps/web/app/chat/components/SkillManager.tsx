'use client'

// 能力管理 —— 开关内置能力 · 新建/编辑/删除自定义能力
//
// 设计取舍:不让用户写 opencode 的 SKILL.md(那是给 AI 看的方法论,普通用户不会写),
// 而是写"提问模板" —— 用户会写"帮我看看 XXX 的北向资金和龙虎榜"。降低门槛先让人用起来。

import { useCallback, useEffect, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import SkillInstallCard from './SkillInstallCard'
import {
  listSkills, createSkill, patchSkill, deleteSkill, resetSkills,
  type SkillWriteResp,
  type SkillItem,
} from '../lib/skillClient'

interface Props {
  onClose: () => void
  /** 关闭时若有改动 · 通知外层刷新侧栏 */
  onChanged: () => void
}

const ICONS = ['⭐', '📊', '📈', '📋', '🔍', '📝', '💡', '🎯', '🔔', '🧭', '⚡', '🧮']

export default function SkillManager({ onClose, onChanged }: Props) {
  const [items, setItems] = useState<SkillItem[]>([])
  const [categoryOrder, setCategoryOrder] = useState<string[]>([])
  const [maxCustom, setMaxCustom] = useState(20)
  const [customCount, setCustomCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [dirty, setDirty] = useState(false)

  // 新建表单
  const [adding, setAdding] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [fName, setFName] = useState('')
  const [fIcon, setFIcon] = useState('⭐')
  const [fTpl, setFTpl] = useState('')
  const [saving, setSaving] = useState(false)
  // 「完整 SKILL」的字段 —— 默认折叠。多数人只想要一个提问快捷方式,
  // 一上来铺开七个输入框会把这件简单事搞复杂。
  const [advanced, setAdvanced] = useState(false)
  const [fSlug, setFSlug] = useState('')
  const [fDesc, setFDesc] = useState('')
  const [fCat, setFCat] = useState('')
  const [fTools, setFTools] = useState<string[]>([])
  const [fBody, setFBody] = useState('')
  const [tools, setTools] = useState<{ key: string; name: string }[]>([])
  // 保存成功但 opencode 还没认到时的提示 —— **必须显示给用户**,
  // 否则他会看到"保存成功"却发现模型不认识这个能力
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await listSkills()
      setItems(d.items || [])
      setCategoryOrder(d.category_order || [])
      setMaxCustom(d.max_custom ?? 20)
      setCustomCount(d.custom_count ?? 0)
      setErr('')
    } catch (e: any) {
      setErr(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const close = () => {
    if (dirty) onChanged()
    onClose()
  }

  const toggle = async (s: SkillItem) => {
    try {
      await patchSkill(s.key, { enabled: !s.enabled })
      setDirty(true)
      await load()
    } catch (e: any) {
      setErr(e?.message || '操作失败')
    }
  }

  const remove = async (s: SkillItem) => {
    if (!confirm(`删除「${s.name}」?`)) return
    try {
      await deleteSkill(s.key)
      setDirty(true)
      await load()
    } catch (e: any) {
      setErr(e?.message || '删除失败')
    }
  }

  // 工具列表从 /catalog/toolbox 拉 —— **做成多选而不是自由输入**:
  // 手打必然有打错的,而打错的后果是 SKILL 显示"依赖未就绪"却查不出为什么。
  useEffect(() => {
    if (!advanced || tools.length) return
    fetch('/api/catalog/toolbox', { cache: 'no-store' })
      .then((r) => r.json())
      .then((d) => setTools(
        (d.groups || []).flatMap((g: any) =>
          (g.tools || []).map((t: any) => ({ key: t.key, name: t.name }))),
      ))
      .catch(() => {})
  }, [advanced, tools.length])

  const submit = async () => {
    if (saving) return
    setSaving(true)
    setNotice('')
    try {
      const r: SkillWriteResp = await createSkill({
        name: fName, prompt_tpl: fTpl, icon: fIcon,
        slug: fSlug || undefined,
        description: fDesc || undefined,
        category: fCat || undefined,
        needs_tools: fTools,
        body: fBody || undefined,
      })
      // 后端写完文件会让 opencode 重扫。重扫没成功时(镜像旧/连不上),
      // 文件是好的但模型还看不到 —— 这一点必须说出来,不能只显示"保存成功"
      if (r && r.synced === false && r.message) setNotice(r.message)
      setFName(''); setFTpl(''); setFIcon('⭐')
      setFSlug(''); setFDesc(''); setFCat(''); setFTools([]); setFBody('')
      setAdding(false); setAdvanced(false)
      setDirty(true)
      await load()
    } catch (e: any) {
      setErr(e?.message || '创建失败')
    } finally {
      setSaving(false)
    }
  }

  const doReset = async () => {
    if (!confirm('恢复内置能力的默认状态?(自定义能力不受影响)')) return
    try {
      await resetSkills()
      setDirty(true)
      await load()
    } catch (e: any) {
      setErr(e?.message || '操作失败')
    }
  }

  const builtins = items.filter((x) => x.builtin)
  const customs = items.filter((x) => !x.builtin)
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '9px 11px', border: `1px solid ${HUNTER.LINE}`,
    borderRadius: 8, fontSize: 13, outline: 'none', background: '#fff',
    color: HUNTER.INK, boxSizing: 'border-box',
  }

  return (
    <div
      onClick={close}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(20,15,8,.45)', zIndex: 500,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: HUNTER.PAPER, borderRadius: 14, width: '100%', maxWidth: 560,
          maxHeight: '86vh', overflowY: 'auto', border: `1px solid ${HUNTER.LINE}`,
          boxShadow: '0 12px 40px rgba(30,20,10,.22)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', padding: '16px 18px 10px' }}>
          <div style={{ flex: 1, fontSize: 16, fontWeight: 700, color: HUNTER.INK, fontFamily: HUNTER.SERIF }}>
            能力管理
          </div>
          <button onClick={close} style={{ background: 'none', border: 'none', fontSize: 22, color: HUNTER.INK_F, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>

        <div style={{ padding: '0 18px 6px', fontSize: 12, color: HUNTER.INK_F, lineHeight: 1.7 }}>
          能力 = 一句写好的提问模板。点它就把模板填进输入框,你只需补上股票名。
          <br />
          模板里写 <code style={{ background: HUNTER.PAPER2, padding: '1px 4px', borderRadius: 3 }}>{'{股票}'}</code> 作占位,点击后光标会停在那里。
        </div>

        {err && (
          <div style={{ margin: '8px 18px', padding: '8px 10px', background: '#fff4f4', border: '1px solid #f3c9c9', borderRadius: 8, fontSize: 12, color: '#b23' }}>
            {err}
          </div>
        )}

        {loading ? (
          <div style={{ padding: 30, textAlign: 'center', color: HUNTER.INK_F, fontSize: 13 }}>加载中…</div>
        ) : (
          <>
            {/* 内置 · 按 category 分组 */}
            <div style={{ padding: '10px 18px 4px', fontSize: 11, color: HUNTER.INK_F, letterSpacing: 0.4 }}>
              内置能力(不可删除 · 可关闭)
            </div>
            {(() => {
              // 按 category 分组
              const groups = new Map<string, SkillItem[]>()
              for (const s of builtins) {
                const cat = s.category || '其他'
                if (!groups.has(cat)) groups.set(cat, [])
                groups.get(cat)!.push(s)
              }
              // 按 categoryOrder 排序 · 未在 order 里的追加末尾
              const orderedCats: string[] = [
                ...categoryOrder.filter((c) => groups.has(c)),
                ...Array.from(groups.keys()).filter((c) => !categoryOrder.includes(c)),
              ]
              return orderedCats.map((cat) => (
                <div key={cat}>
                  <div style={{
                    padding: '10px 18px 4px',
                    borderTop: `1px solid ${HUNTER.LINE}`,
                    background: HUNTER.PAPER2,
                    fontSize: 11.5,
                    fontWeight: 700,
                    color: HUNTER.INK_S,
                    letterSpacing: 0.5,
                    display: 'flex', alignItems: 'baseline', gap: 6,
                  }}>
                    <span>{cat}</span>
                    <span style={{ fontSize: 10, color: HUNTER.INK_F, fontWeight: 500 }}>· {groups.get(cat)!.length}</span>
                  </div>
                  {groups.get(cat)!.map((s) => (
                    <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 18px', borderTop: `1px solid ${HUNTER.LINE}` }}>
                      <span style={{ fontSize: 16 }}>{s.icon}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13.5, color: HUNTER.INK, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span>{s.name}</span>
                          {s.brand && (
                            <span title={s.brand} style={{
                              padding: '1px 5px', borderRadius: 3,
                              background: HUNTER.PAPER3, color: HUNTER.INK_F,
                              fontSize: 9.5, fontWeight: 700, letterSpacing: '.03em',
                            }}>
                              {s.brand}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.prompt_tpl}
                        </div>
                      </div>
                      <a
                        href={`/skills/${encodeURIComponent(s.key)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="查看能力详情"
                        style={{ fontSize: 11, color: HUNTER.THEME, textDecoration: 'none', padding: '2px 6px' }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        详情 ↗
                      </a>
                      <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer' }}>
                        <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} style={{ width: 16, height: 16, accentColor: HUNTER.THEME }} />
                      </label>
                    </div>
                  ))}
                </div>
              ))
            })()}

            {/* 自定义 */}
            <div style={{ padding: '14px 18px 4px', fontSize: 11, color: HUNTER.INK_F, display: 'flex', alignItems: 'center' }}>
              <span style={{ flex: 1, letterSpacing: 0.4 }}>我的能力 {customCount} / {maxCustom}</span>
              {customCount < maxCustom && !adding && !installing && (
                <span style={{ display: 'flex', gap: 12 }}>
                  <button onClick={() => setInstalling(true)} style={{ background: 'none', border: 'none', color: HUNTER.THEME, fontSize: 12, cursor: 'pointer', padding: 0 }}>
                    从 GitHub 装
                  </button>
                  <button onClick={() => setAdding(true)} style={{ background: 'none', border: 'none', color: HUNTER.THEME, fontSize: 12, cursor: 'pointer', padding: 0 }}>
                    + 新建
                  </button>
                </span>
              )}
            </div>

            {customs.length === 0 && !adding && (
              <div style={{ padding: '4px 18px 10px', fontSize: 12, color: HUNTER.INK_F }}>
                还没有自定义能力。把你常问的问题存成模板,以后一点就有。
              </div>
            )}

            {customs.map((s) => (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 18px', borderTop: `1px solid ${HUNTER.LINE}` }}>
                <span style={{ fontSize: 16 }}>{s.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, color: HUNTER.INK, fontWeight: 600 }}>{s.name}</div>
                  <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.prompt_tpl}
                  </div>
                </div>
                <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} style={{ width: 16, height: 16, accentColor: HUNTER.THEME }} />
                <button onClick={() => remove(s)} style={{ background: 'none', border: `1px solid ${HUNTER.LINE}`, color: '#c0392b', fontSize: 11, borderRadius: 6, padding: '3px 9px', cursor: 'pointer' }}>
                  删除
                </button>
              </div>
            ))}

            {installing && (
              <SkillInstallCard
                onClose={() => setInstalling(false)}
                onInstalled={async (names, msg) => {
                  setInstalling(false)
                  // 装完 opencode 没重扫时 msg 非空 —— 必须显示,
                  // 否则用户以为装好了但模型不认识
                  setNotice(msg || `已安装 ${names.length} 个能力:${names.join('、')}`)
                  setDirty(true)
                  await load()
                }}
              />
            )}

            {adding && (
              <div style={{ padding: '12px 18px', borderTop: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER3 }}>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <select value={fIcon} onChange={(e) => setFIcon(e.target.value)} style={{ ...inputStyle, width: 62, padding: '9px 6px' }}>
                    {ICONS.map((i) => <option key={i} value={i}>{i}</option>)}
                  </select>
                  <input value={fName} onChange={(e) => setFName(e.target.value)} placeholder="能力名称,如「板块扫描」" maxLength={20} style={inputStyle} />
                </div>
                <textarea
                  value={fTpl}
                  onChange={(e) => setFTpl(e.target.value)}
                  placeholder="提问模板,如:分析 {股票} 的北向资金和龙虎榜异动"
                  rows={3}
                  maxLength={500}
                  style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
                />
                <button
                  onClick={() => setAdvanced((v) => !v)}
                  style={{ background: 'none', border: 'none', color: HUNTER.THEME, fontSize: 12, cursor: 'pointer', padding: '6px 0 0', fontFamily: 'inherit' }}
                >
                  {advanced ? '收起' : '写成完整 SKILL（方法论 + 依赖）'} {advanced ? '▴' : '▾'}
                </button>

                {advanced && (
                  <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <input value={fSlug} onChange={(e) => setFSlug(e.target.value)}
                      placeholder="目录名(英文小写,留空自动生成)" style={inputStyle} />
                    <input value={fDesc} onChange={(e) => setFDesc(e.target.value)}
                      placeholder="一句话说明 —— 模型据此判断什么时候用它" style={inputStyle} />
                    <select value={fCat} onChange={(e) => setFCat(e.target.value)} style={inputStyle}>
                      <option value="">分类(默认「其他」)</option>
                      {categoryOrder.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>

                    <div>
                      <div style={{ fontSize: 11.5, color: HUNTER.INK_F, marginBottom: 4 }}>
                        需要哪些工具（勾选,不要手打）
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, maxHeight: 108, overflowY: 'auto' }}>
                        {tools.map((t) => {
                          const on = fTools.includes(t.key)
                          return (
                            <button key={t.key} onClick={() => setFTools((p) =>
                              on ? p.filter((x) => x !== t.key) : [...p, t.key])}
                              title={t.key}
                              style={{ padding: '3px 8px', borderRadius: 5, cursor: 'pointer', fontSize: 11.5,
                                fontFamily: 'inherit',
                                background: on ? HUNTER.THEME : HUNTER.PAPER,
                                color: on ? '#fff' : HUNTER.INK_S,
                                border: `1px solid ${on ? HUNTER.THEME : HUNTER.LINE}` }}>
                              {t.name}
                            </button>
                          )
                        })}
                        {!tools.length && (
                          <span style={{ fontSize: 11.5, color: HUNTER.INK_F }}>加载中…</span>
                        )}
                      </div>
                    </div>

                    <textarea value={fBody} onChange={(e) => setFBody(e.target.value)}
                      rows={8}
                      placeholder={'方法论正文（Markdown）\n\n## 什么时候用\n\n## 怎么做\n\n## 什么时候不适用\n\n—— 最后一节最有用:说清楚什么情况下不该用它'}
                      style={{ ...inputStyle, resize: 'vertical', fontFamily: 'ui-monospace, monospace', fontSize: 12, lineHeight: 1.6 }} />
                  </div>
                )}

                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button onClick={() => { setAdding(false); setAdvanced(false); setErr('') }} style={{ flex: 1, padding: '9px 0', background: HUNTER.PAPER, color: HUNTER.INK_S, border: `1px solid ${HUNTER.LINE}`, borderRadius: 8, fontSize: 13, cursor: 'pointer' }}>
                    取消
                  </button>
                  <button disabled={saving} onClick={submit} style={{ flex: 2, padding: '9px 0', background: HUNTER.THEME, color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
                    {saving ? '保存中…' : '保存'}
                  </button>
                </div>
              </div>
            )}

            {notice && (
              <div style={{ margin: '10px 18px 0', padding: '9px 11px', borderRadius: 7,
                background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG, fontSize: 12, lineHeight: 1.6 }}>
                {notice}
              </div>
            )}

            <div style={{ padding: '14px 18px 18px', borderTop: `1px solid ${HUNTER.LINE}`, marginTop: 8 }}>
              <button onClick={doReset} style={{ background: 'none', border: 'none', color: HUNTER.INK_F, fontSize: 12, cursor: 'pointer', padding: 0 }}>
                恢复内置能力默认状态
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
