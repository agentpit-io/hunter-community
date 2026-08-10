'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { HUNTER } from '../../lib/hunter-theme'
import type { Message, MessagePart, MessagePartTool, OpencodeEvent } from '../lib/types'
import {
  createSession,
  getSession,
  listMessages,
  listSessions,
  renameSession,
  sendMessage,
  switchSessionAgent,
  switchSessionModel,
} from '../lib/opencodeClient'
import { useOpencodeSSE } from '../lib/useSSE'
import { condenseMemory } from '../lib/profileClient'
import MessageList from './MessageList'
import InputBox from './InputBox'
import SessionHeader from './SessionHeader'
import DebateProgressCard, { type DebatePhase } from './DebateProgressCard'
import KpredProgressCard from './KpredProgressCard'
import { runDebate, listSessionDebates, type DebateProgressEvent, type DebateDepth } from '../lib/debateClient'
import { runKpred, listSessionKpreds, extractDays, type KpredProgressEvent } from '../lib/kpredClient'

interface Props {
  sessionId: string | null
  onSessionCreated: (id: string) => void
  onSessionUpdated: () => void
  onSessionDeleted: () => void
  onOpenArtifact: (part: MessagePartTool) => void
  autoText?: string
  autoSend?: boolean
  /** 点能力卡填入输入框(seq 递增以支持连点同一个能力) */
  draft?: { text: string; seq: number }
  /** 点空态提示 chip 或深入追问建议 · 走 draft 机制填输入 */
  onPickSuggestion?: (text: string) => void
  /** 空态 quick-card 点击 · 同 sidebar onPickSkill(含辩论 depth 选择) */
  onPickSkill?: (tpl: string, key?: string) => void
  /** 打开 assistant 报告 · 传给 page · page 联动收 Sidebar + 打开 ArtifactPanel */
  onOpenReport?: (text: string, sourceMessageId: string, artifactType?: 'markdown' | 'html', overrideTitle?: string) => void
  /** 用户上次点的 SKILL key · 用于识别下条 send 走什么路径(如 'debate') */
  pendingSkillKey?: string | null
  /** SKILL 消费后通知 page 清 pending · 避免下条普通消息被误判 */
  onSkillConsumed?: () => void
  /** 辩论深度 · 由 page 层的 DepthPicker 决定 · 默认 normal */
  debateDepth?: DebateDepth
}

function reduceEvents(prev: Message[], events: OpencodeEvent[]): Message[] {
  const byId = new Map<string, Message>()
  prev.forEach((m) => byId.set(m.id, m))
  // 清临时乐观 id · 若真实 id 已到 · SSE 会替换 (临时 tmp_user_* 保留直到真 msg.updated 覆盖)

  for (const ev of events) {
    const props: any = (ev as any).properties || {}

    // ── message.updated · 消息元数据（含首次创建）
    if (ev.type === 'message.updated' && props.info) {
      const info = props.info as Message
      const existing = byId.get(info.id)
      byId.set(info.id, {
        ...(existing || {}),
        ...info,
        parts: info.parts || existing?.parts || [],
      })

    // ── message.part.updated · part 全量更新（messageID 在 part 内）
    } else if (ev.type === 'message.part.updated' && props.part) {
      const partAny: any = props.part
      const mid = partAny.messageID
      if (!mid) continue
      let m = byId.get(mid)
      if (!m) {
        // 消息还没建 · 建占位
        m = {
          id: mid,
          sessionID: props.sessionID || partAny.sessionID || '',
          role: 'assistant',
          parts: [],
          time: { created: Date.now() },
        }
      }
      const parts = [...(m.parts || [])]
      const idx = parts.findIndex((p: any) => {
        if (p.id && partAny.id) return p.id === partAny.id
        if (p.type === 'tool' && partAny.type === 'tool') return p.callID === partAny.callID
        return false
      })
      if (idx >= 0) parts[idx] = partAny as MessagePart
      else parts.push(partAny as MessagePart)
      byId.set(mid, { ...m, parts })

    // ── message.part.delta · 流式 text chunk (Gemini 逐 token 流)
    } else if (ev.type === 'message.part.delta' && props.messageID && props.partID) {
      const m = byId.get(props.messageID)
      if (!m) continue
      const parts = [...(m.parts || [])]
      const idx = parts.findIndex((p: any) => p.id === props.partID)
      if (idx < 0) continue
      const existing: any = parts[idx]
      if (props.field === 'text') {
        parts[idx] = { ...existing, text: (existing.text || '') + (props.delta || '') }
      } else if (props.field) {
        parts[idx] = { ...existing, [props.field]: (existing[props.field] || '') + (props.delta || '') }
      }
      byId.set(props.messageID, { ...m, parts })
    }
  }

  // 过滤 tmp_user_* 乐观占位 · 若真实 user 已到
  // 原用时间戳匹配 · 但客户端 vs 服务器时钟差可能 > 5s · 不可靠
  // 改用【内容匹配】· 更可靠
  const values = Array.from(byId.values()).filter((m) => m && m.id)
  const realUserTexts = new Set<string>()
  values.forEach((m) => {
    const id = m.id || ''
    if (!id.startsWith('tmp_') && m.role === 'user') {
      const text = (m.parts || [])
        .filter((p: any) => p.type === 'text')
        .map((p: any) => p.text || '')
        .join('\n')
        .trim()
      if (text) realUserTexts.add(text)
    }
  })
  const filtered = values.filter((m) => {
    const id = m.id || ''
    if (!id.startsWith('tmp_user_')) return true
    const tmpText = (m.parts || [])
      .filter((p: any) => p.type === 'text')
      .map((p: any) => p.text || '')
      .join('\n')
      .trim()
    return !realUserTexts.has(tmpText)
  })

  return filtered.sort(
    (a, b) => (a.time?.created || 0) - (b.time?.created || 0),
  )
}

export default function ChatWorkspace({
  sessionId,
  onSessionCreated,
  onSessionUpdated,
  onSessionDeleted,
  onOpenArtifact,
  onPickSuggestion,
  onPickSkill,
  onOpenReport,
  autoText,
  autoSend,
  draft,
  pendingSkillKey,
  onSkillConsumed,
  debateDepth,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 多专家辩论 · 6 阶段进度 · null 表示未在辩论中
  const [debate, setDebate] = useState<{
    phase: DebatePhase
    pct: number
    text: string
    stockName?: string
    stockCode?: string
    errorMsg?: string
  } | null>(null)

  // Sprint E · Kronos 预测进度 · 与 debate 结构相似但阶段更少
  const [kpred, setKpred] = useState<{
    phase: 'start' | 'kronos' | 'factor' | 'rendering' | 'done' | 'error'
    pct: number
    text: string
    stockName?: string
    stockCode?: string
    errorMsg?: string
  } | null>(null)

  // Sprint E · HTML artifact 内容缓存 · 消息 id → {html, title}
  // 关闭 Artifact 面板后再想打开 · 靠这个 lookup 恢复 · 只在当前 session 有效
  const [htmlArtifacts, setHtmlArtifacts] = useState<Record<string, { html: string; title: string }>>({})

  // Agent · Model state · 持久 localStorage
  const [currentAgent, setCurrentAgent] = useState('build')
  const [currentModelKey, setCurrentModelKey] = useState('oneapi/gemini-3.5-flash')

  useEffect(() => {
    const savedAgent = localStorage.getItem('hunter_chat_agent')
    if (savedAgent) setCurrentAgent(savedAgent)
    const savedModel = localStorage.getItem('hunter_chat_model')
    if (savedModel) setCurrentModelKey(savedModel)
  }, [])

  const { events, status: sseStatus } = useOpencodeSSE(sessionId)

  useEffect(() => {
    if (sessionId) return
    ;(async () => {
      try {
        const list = await listSessions()
        const latest = list.sort(
          (a, b) => (b.time?.updated || b.time?.created || 0) - (a.time?.updated || a.time?.created || 0),
        )[0]
        if (latest) onSessionCreated(latest.id)
        else {
          const s = await createSession('新对话')
          onSessionCreated(s.id)
        }
      } catch (e: any) {
        setError(`初始化 session 失败: ${e?.message || e}`)
      }
    })()
  }, [sessionId, onSessionCreated])

  useEffect(() => {
    if (!sessionId) {
      setMessages([])
      return
    }
    ;(async () => {
      try {
        // 并行拉 opencode 消息 + 本 session 历史辩论 + kpred
        const [msgs, debates, kpreds] = await Promise.all([
          listMessages(sessionId),
          listSessionDebates(sessionId).catch(() => []),
          listSessionKpreds(sessionId).catch(() => []),
        ])
        const base = Array.isArray(msgs) ? msgs : []

        // 把每个历史辩论 · 恢复成 user + assistant 两条 · id 用 task_id 派生
        // 便被 Artifact publish 关联(publish 用 sourceMessageId=debate_<taskId>)
        const debateMessages: Message[] = []
        for (const d of debates) {
          const ts = d.created_at ? new Date(d.created_at).getTime() : Date.now()
          debateMessages.push({
            id: `debate_user_${d.task_id}`,
            sessionID: sessionId,
            role: 'user',
            parts: [{ type: 'text', text: d.question || `对 ${d.stock_name} 做多空辩论` }],
            time: { created: ts - 1 },
          })
          debateMessages.push({
            id: `debate_${d.task_id}`,
            sessionID: sessionId,
            role: 'assistant',
            parts: [{ type: 'text', text: d.content_md }],
            time: { created: ts },
          })
        }

        // Sprint H · kpred 报告恢复 · user + assistant 两条 · 同步 htmlArtifacts 缓存
        const kpredMessages: Message[] = []
        const kpredHtmlRestore: Record<string, { html: string; title: string }> = {}
        for (const k of kpreds) {
          const ts = k.created_at ? new Date(k.created_at).getTime() : Date.now()
          const assistantId = `kpred_${k.task_id}`
          kpredMessages.push({
            id: `kpred_user_${k.task_id}`,
            sessionID: sessionId,
            role: 'user',
            parts: [{ type: 'text', text: k.question || `用 Kronos 预测 ${k.stock_name} 未来 ${k.days} 天走势` }],
            time: { created: ts - 1 },
          })
          kpredMessages.push({
            id: assistantId,
            sessionID: sessionId,
            role: 'assistant',
            parts: [{ type: 'text', text: k.summary_md }],
            time: { created: ts },
          })
          kpredHtmlRestore[assistantId] = {
            html: k.content_html,
            title: `${k.stock_name} · Kronos 预测`,
          }
        }
        if (Object.keys(kpredHtmlRestore).length > 0) {
          setHtmlArtifacts((prev) => ({ ...prev, ...kpredHtmlRestore }))
        }

        // 合并 · 按时间排序 · debate/kpred 消息天然与 opencode 消息交错
        const combined = [...base, ...debateMessages, ...kpredMessages].sort(
          (a, b) => (a.time?.created || 0) - (b.time?.created || 0),
        )
        setMessages(combined)
      } catch (e: any) {
        setError(`拉消息失败: ${e?.message || e}`)
      }
    })()
  }, [sessionId])

  useEffect(() => {
    if (events.length === 0) return
    setMessages((prev) => reduceEvents(prev, events))
  }, [events])

  // ── 离开会话时把「用户说过的话」交给后端浓缩进记忆体 ──
  // 只取 role=user 的文本:assistant 的回复是模型说的,拿它当用户偏好会串味。
  // 每个会话只浓缩一次(localStorage 记账)—— 后端浓缩是累加计数的,重复调会让
  // "问过 8 次茅台"变成 16 次。
  const msgRef = useRef<Message[]>([])
  const sidRef = useRef<string | null>(null)
  useEffect(() => { msgRef.current = messages }, [messages])

  const condenseSession = useCallback((sid: string | null, msgs: Message[]) => {
    if (!sid) return
    const key = 'hunter_condensed'
    let done: string[] = []
    try { done = JSON.parse(localStorage.getItem(key) || '[]') } catch { done = [] }
    if (done.includes(sid)) return
    const texts = msgs
      .filter((m) => m.role === 'user')
      .flatMap((m) => (m.parts || []).filter((p: any) => p.type === 'text').map((p: any) => p.text || ''))
      .filter(Boolean)
    if (texts.length === 0) return
    void condenseMemory(sid, texts).catch(() => { /* 记忆写失败不打扰用户 */ })
    try { localStorage.setItem(key, JSON.stringify([...done, sid].slice(-200))) } catch { /* 配额满忽略 */ }
  }, [])

  useEffect(() => {
    const prevSid = sidRef.current
    if (prevSid && prevSid !== sessionId) condenseSession(prevSid, msgRef.current)
    sidRef.current = sessionId
  }, [sessionId, condenseSession])

  useEffect(() => {
    // 关标签 / 刷新时补一次
    const onLeave = () => condenseSession(sidRef.current, msgRef.current)
    window.addEventListener('beforeunload', onLeave)
    return () => {
      window.removeEventListener('beforeunload', onLeave)
      onLeave()
    }
  }, [condenseSession])

  const handleChangeAgent = async (name: string) => {
    setCurrentAgent(name)
    localStorage.setItem('hunter_chat_agent', name)
    if (sessionId) {
      try {
        await switchSessionAgent(sessionId, name)
      } catch (e: any) {
        console.warn('[chat] switch agent:', e)
      }
    }
  }

  const handleChangeModel = async (providerID: string, modelID: string, _displayName: string) => {
    const key = `${providerID}/${modelID}`
    setCurrentModelKey(key)
    localStorage.setItem('hunter_chat_model', key)
    if (sessionId) {
      try {
        await switchSessionModel(sessionId, providerID, modelID)
      } catch (e: any) {
        console.warn('[chat] switch model:', e)
      }
    }
  }

  const handleSend = async (text: string) => {
    if (!sessionId || busy) return

    // ── 多专家辩论 SKILL ──
    const isDebateByPattern = /做\s*多\s*空\s*辩\s*论/.test(text)
    if (pendingSkillKey === 'debate' || isDebateByPattern) {
      onSkillConsumed?.()
      void handleDebateSend(text, sessionId)
      return
    }

    // ── Kronos 走势预测 SKILL (Sprint E) ──
    // 模式匹配: "用 Kronos 预测" 是 SKILL 模板固定短语
    const isKpredByPattern = /用\s*Kronos\s*预测/i.test(text) || /Kronos.*未来.*天走势/i.test(text)
    if (pendingSkillKey === 'forecast' || isKpredByPattern) {
      onSkillConsumed?.()
      void handleKpredSend(text, sessionId)
      return
    }

    setBusy(true)
    setError(null)
    // 判断是否第一条 user message · 用于后面自动生成标题
    const isFirstUserMsg = !messages.some((m) => m.role === 'user')

    const tempId = `tmp_user_${Date.now()}`
    setMessages((prev) => [
      ...prev,
      {
        id: tempId,
        sessionID: sessionId,
        role: 'user',
        parts: [{ type: 'text', text }],
        time: { created: Date.now() },
      },
    ])
    try {
      const [providerID, ...rest] = currentModelKey.split('/')
      const modelID = rest.join('/')
      await sendMessage({
        sessionId,
        text,
        agent: currentAgent,
        model: providerID && modelID ? { providerID, modelID } : undefined,
      })

      // 自动生成标题 · 若这是首条 user msg 且当前 session title 是"新对话"或空
      if (isFirstUserMsg) {
        void autoTitleIfNeeded(sessionId, text)
      }
    } catch (e: any) {
      setError(`发送失败: ${e?.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 多专家辩论专用 send · 不走 opencode
   *
   * 1. 从用户文本抽股票查询 · 兼容 SKILL 默认模板 "对 {股票} 做多空辩论..."
   *    抽不出就把整条丢给后端 · resolve_stock 会尽力匹配
   * 2. 本地注入 user 消息 · 显示"对 xxx 做多空辩论"
   * 3. 调 debateClient.runDebate · 6 阶段进度更新 debate state
   * 4. 完成后注入 assistant 消息 · id=`debate_${taskId}` · 打开 Artifact
   */
  const handleDebateSend = async (text: string, sid: string) => {
    setBusy(true)
    setError(null)

    // 抽股票查询 · SKILL 默认模板是 "对 {股票} 做多空辩论 · 给出买卖决策"
    // 匹配 "对 xxx 做多空" 之间的部分 · 抽不出兜底整条
    // ⚠ 用户可能没完全替换占位符 · 输入是"对 {中际旭创} 做..." · 必须剥 {}
    const m = text.match(/对\s*(.+?)\s*(?:做多空辩论|辩论)/)
    let stockQuery = (m ? m[1] : text).trim()
    // 剥占位符括号(半/全角) · 剥前后杂字符
    stockQuery = stockQuery.replace(/[{}【】\[\]（）()<>《》「」『』]/g, '').trim()
    stockQuery = stockQuery.replace(/^(股票|代码)\s*[:：]?\s*/, '').trim()

    const isFirstUserMsg = !messages.some((m) => m.role === 'user')

    // 注入 user 消息
    const userTempId = `tmp_user_${Date.now()}`
    setMessages((prev) => [
      ...prev,
      {
        id: userTempId,
        sessionID: sid,
        role: 'user',
        parts: [{ type: 'text', text }],
        time: { created: Date.now() },
      },
    ])

    setDebate({ phase: 'technical', pct: 0, text: '正在启动多专家辩论...' })

    try {
      const final = await runDebate(
        { stockQuery, question: text, sessionId: sid, depth: debateDepth || 'normal' },
        (ev: DebateProgressEvent) => {
          setDebate((prev) => ({
            phase: ev.phase as DebatePhase,
            pct: ev.pct,
            text: ev.text,
            stockName: prev?.stockName,
            stockCode: prev?.stockCode,
          }))
        },
        ({ stockCode, stockName }) => {
          setDebate((prev) => ({
            phase: prev?.phase || 'technical',
            pct: prev?.pct || 0,
            text: prev?.text || '',
            stockCode,
            stockName,
          }))
        },
      )

      // 注入 assistant 消息 · markdown 报告
      const assistantId = `debate_${final.taskId}`
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          sessionID: sid,
          role: 'assistant',
          parts: [{ type: 'text', text: final.markdown }],
          time: { created: Date.now() },
        },
      ])

      // 自动打开 Artifact 面板 · 用户直接看报告
      if (onOpenReport) {
        onOpenReport(final.markdown, assistantId)
      }

      // 自动生成 session 标题 · 用股票名
      if (isFirstUserMsg && final.stockName) {
        void autoTitleIfNeeded(sid, `⚖️ ${final.stockName} 多空辩论`)
      }

      // 3 秒后隐藏进度卡 · 让报告完全展示
      setTimeout(() => setDebate(null), 3000)
    } catch (e: any) {
      const msg = e?.message || String(e)
      setDebate({
        phase: 'error',
        pct: 0,
        text: '',
        errorMsg: msg,
      })
      setError(`多专家辩论失败: ${msg}`)
      // 错误卡保留 · 让用户看清失败原因 · 手动关
    } finally {
      setBusy(false)
    }
  }

  /**
   * Sprint E · Kronos 预测专用 send · 输出 HTML 富可视化 artifact
   *
   * 与 handleDebateSend 同构 · 但:
   * · 阶段更少(4-5 秒)· 无 depth 选择
   * · 从文本抽股票查询 + 天数(regex)
   * · 出 HTML 而不是 markdown · 交 Artifact iframe 渲染
   */
  const handleKpredSend = async (text: string, sid: string) => {
    setBusy(true)
    setError(null)

    // 抽股票查询: SKILL 模板 "用 Kronos 预测 {股票} 未来 N 天走势"
    // 抽出 "{...}"之间的 · 兜底整条
    let stockQuery = ''
    const m1 = text.match(/预测\s*(.+?)\s*(?:未来|走势|\d)/)
    if (m1) stockQuery = m1[1]
    else {
      const m2 = text.match(/预测\s*(.+?)$/)
      if (m2) stockQuery = m2[1]
      else stockQuery = text
    }
    // 剥占位符括号 + 常见杂字符
    stockQuery = stockQuery.replace(/[{}【】\[\]（）()<>《》「」『』]/g, '').trim()
    stockQuery = stockQuery.replace(/(未来|走势|\d+\s*[天日]|Kronos|预测)/gi, '').trim()

    const days = extractDays(text)   // 默认 5
    const isFirstUserMsg = !messages.some((m) => m.role === 'user')

    const userTempId = `tmp_user_${Date.now()}`
    setMessages((prev) => [
      ...prev,
      {
        id: userTempId,
        sessionID: sid,
        role: 'user',
        parts: [{ type: 'text', text }],
        time: { created: Date.now() },
      },
    ])

    setKpred({ phase: 'start', pct: 5, text: '正在启动 Kronos 预测...' })

    try {
      const final = await runKpred(
        { stockQuery, days, question: text, sessionId: sid },
        (ev: KpredProgressEvent) => {
          setKpred((prev) => ({
            phase: ev.phase as any,
            pct: ev.pct,
            text: ev.text,
            stockName: prev?.stockName || ev.stock_name,
            stockCode: prev?.stockCode || ev.stock_code,
          }))
        },
      )

      // 注入 assistant 消息 · text = markdown 摘要
      const assistantId = `kpred_${final.taskId}`
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          sessionID: sid,
          role: 'assistant',
          parts: [{ type: 'text', text: final.summary }],
          time: { created: Date.now() },
        },
      ])

      // 缓存 HTML · 供关闭后再打开
      const artifactTitle = `${final.stockName} · Kronos 预测`
      setHtmlArtifacts((prev) => ({
        ...prev,
        [assistantId]: { html: final.htmlContent, title: artifactTitle },
      }))

      // 打开 Artifact 面板 · 传 HTML 类型的 report
      if (onOpenReport) {
        onOpenReport(final.htmlContent, assistantId, 'html', artifactTitle)
      }

      if (isFirstUserMsg && final.stockName) {
        void autoTitleIfNeeded(sid, `📈 ${final.stockName} Kronos 预测`)
      }

      // 3 秒后隐藏进度卡 · 让报告完全展示
      setTimeout(() => setKpred(null), 3000)
    } catch (e: any) {
      const msg = e?.message || String(e)
      setKpred({ phase: 'error', pct: 0, text: '', errorMsg: msg })
      setError(`Kronos 预测失败: ${msg}`)
    } finally {
      setBusy(false)
    }
  }

  // 自动生成 session 标题:第一条 user msg 前 20 字 · 剪掉换行 · 加 ... 若超长
  // 只在当前标题是"新对话"或空时才改 · 已改过的不动
  const autoTitleIfNeeded = async (sid: string, firstText: string) => {
    try {
      const cur = await getSession(sid)
      if (cur.title && cur.title !== '新对话' && cur.title !== 'New Chat') return
      const cleaned = firstText.replace(/\s+/g, ' ').trim()
      const title = cleaned.length > 20 ? cleaned.slice(0, 20) + '…' : cleaned
      if (!title) return
      await renameSession(sid, title)
      onSessionUpdated()
    } catch (e) {
      console.warn('[chat] auto title failed:', e)
    }
  }

  // 空态 = 首屏 Hero 版本 · InputBox 内联在 Hero 下方
  const isEmpty = messages.length === 0
  const inputEl = (
    <InputBox
      onSend={handleSend}
      disabled={busy || !sessionId}
      currentAgent={currentAgent}
      currentModelKey={currentModelKey}
      onChangeAgent={handleChangeAgent}
      onChangeModel={handleChangeModel}
      autoText={autoText}
      autoSend={autoSend}
      draft={draft}
      mode={isEmpty ? 'hero' : 'follow'}
    />
  )

  return (
    <div
      style={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        background: '#ffffff',
        overflow: 'hidden',
        position: 'relative',
        minWidth: 0,
      }}
    >
      {/* 有消息才显示会话头 · 空态给 Hero 让位 */}
      {!isEmpty && (
        <SessionHeader
          sessionId={sessionId}
          onSessionUpdated={onSessionUpdated}
          onSessionDeleted={onSessionDeleted}
        />
      )}

      {error && (
        <div
          style={{
            padding: '10px 20px',
            background: '#fbeaea',
            borderBottom: `1px solid ${HUNTER.LINE}`,
            color: HUNTER.UP,
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span style={{ flex: 1 }}>⚠ {error}</span>
          <button
            onClick={() => setError(null)}
            style={{ background: 'none', border: 'none', color: HUNTER.UP, cursor: 'pointer', fontSize: 12 }}
          >
            关闭
          </button>
        </div>
      )}

      <MessageList
        messages={messages}
        onOpenArtifact={onOpenArtifact}
        onPickSuggestion={onPickSuggestion}
        onPickSkill={onPickSkill}
        onOpenReport={onOpenReport}
        busy={busy}
        heroInput={isEmpty ? inputEl : null}
        htmlArtifacts={htmlArtifacts}
        onReopenHtmlArtifact={(msgId) => {
          const art = htmlArtifacts[msgId]
          if (art && onOpenReport) {
            onOpenReport(art.html, msgId, 'html', art.title)
          }
        }}
        extraBottom={
          debate ? (
            <DebateProgressCard
              phase={debate.phase}
              pct={debate.pct}
              text={debate.text}
              stockName={debate.stockName}
              stockCode={debate.stockCode}
              errorMsg={debate.errorMsg}
              depth={debateDepth}
            />
          ) : kpred ? (
            <KpredProgressCard
              phase={kpred.phase}
              pct={kpred.pct}
              text={kpred.text}
              stockName={kpred.stockName}
              stockCode={kpred.stockCode}
              errorMsg={kpred.errorMsg}
            />
          ) : null
        }
      />

      {/* 有消息态 · InputBox 贴底 · 空态 InputBox 已在 MessageList 内联 */}
      {!isEmpty && inputEl}

      {process.env.NODE_ENV !== 'production' && (
        <div
          style={{
            position: 'absolute',
            bottom: 4,
            left: 8,
            fontSize: 9,
            color: HUNTER.INK_F,
            opacity: 0.5,
            fontFamily: 'ui-monospace, monospace',
            pointerEvents: 'none',
          }}
        >
          {sessionId?.slice(0, 12) || '(none)'} · sse={sseStatus} · msgs={messages.length} · {currentAgent}/{currentModelKey.split('/').pop()}
        </div>
      )}
    </div>
  )
}
