/**
 * 报告识别 + 下载工具
 *
 * 启发式:【长文 + markdown 特征】= 值得预览的"报告"
 *   - 长度 > 300 字
 *   - 且满足以下任一:
 *     · ≥ 2 个二/三级标题
 *     · 有 GFM 表格
 *     · 有 fenced code block
 *     · ≥ 3 个数字列表 + 至少 1 个标题
 */
import type { Message, MessagePart, MessagePartText } from './types'

/**
 * 从 markdown 推断 artifact 标题 · 3 层 fallback:
 *   1. 第一个 # h1 标题
 *   2. 第一行非空文本前 40 字 (剪掉 markdown 语法字符)
 *   3. session title (默认对话名)
 */
export function inferArtifactTitle(text: string, sessionTitle: string): string {
  if (!text) return sessionTitle
  const h1 = text.match(/^# +(.+?)$/m)
  if (h1 && h1[1].trim()) return h1[1].trim().slice(0, 40)
  const firstLine = text
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.length > 0)
  if (firstLine) {
    const clean = firstLine.replace(/^[#*>\-\s`]+/, '').trim()
    if (clean) return clean.slice(0, 40)
  }
  return sessionTitle
}

/**
 * 拼接 message 里 "最终" text · 排除 tool_call 之前的 planning 段
 *
 * Gemini function-calling 常在调 tool 前吐一段英文规划文本:
 *   "I will predict the future 5-day trend... First, I need to..."
 * 这段是模型内部编排 · 用户不需要 · 也不应进入 Artifact/Publish 的报告内容。
 *
 * 规则:
 *  · 找到最后一个 tool_call 的 index
 *  · 若存在 · 只取 index 之后的 text 段拼接
 *  · 若不存在(纯对话) · 取全部 text 段(原行为不变)
 */
export function getAssistantText(msg: Message): string {
  const parts = msg.parts || []
  const lastToolIdx = parts
    .map((p, i) => (p.type === 'tool' ? i : -1))
    .filter((i) => i >= 0)
    .pop()
  const selected = lastToolIdx == null
    ? parts
    : parts.slice(lastToolIdx + 1)   // 只留最终答复段
  return selected
    .filter((p: MessagePart): p is MessagePartText => p.type === 'text')
    .map((p) => p.text || '')
    .join('\n')
}

/**
 * 是否值得作报告预览
 */
export function isReportWorthy(msg: Message, busy: boolean): boolean {
  if (!msg || msg.role !== 'assistant') return false
  if (busy) return false
  const text = getAssistantText(msg)
  if (text.length < 300) return false

  const headers = (text.match(/^#{1,3} /gm) || []).length
  if (headers >= 2) return true

  // GFM 表格特征:表头分隔行 |---|---|
  if (/\n\|[-\s|:]+\|/.test(text)) return true

  // fenced code block
  if (/```[\s\S]+?```/.test(text)) return true

  // 数字列表 ≥ 3 且有标题
  const numberedLists = (text.match(/^\d+\.\s/gm) || []).length
  if (numberedLists >= 3 && headers >= 1) return true

  return false
}

/**
 * 从报告 markdown 里提取 HTML 代码块(若有)
 * 返回 { hasHtml, htmlContent }
 */
export function extractHtmlBlock(text: string): { hasHtml: boolean; htmlContent: string } {
  const m = text.match(/```html\s*\n?([\s\S]+?)```/i)
  if (m && m[1]) return { hasHtml: true, htmlContent: m[1].trim() }
  return { hasHtml: false, htmlContent: '' }
}

/**
 * 生成 md 文件名:{标题}_{YYYYMMDD-HHmm}.md
 * 清洗特殊字符 · 保留中文
 */
function safeFilename(title: string): string {
  const clean = title.replace(/[^\w一-龥-]+/g, '_').replace(/_+/g, '_').slice(0, 40)
  const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, '-')
  return `${clean || 'hunter报告'}_${ts}.md`
}

/**
 * 触发浏览器下载 markdown 文件
 * 前置 YAML frontmatter · 便于归档
 */
export function downloadReport(text: string, meta: { sessionTitle?: string; sessionId?: string; agent?: string; model?: string }) {
  const now = new Date()
  const dateStr = now.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
  const frontmatter = [
    '---',
    `title: ${meta.sessionTitle || '猎鹿人报告'}`,
    `date: ${dateStr}`,
    meta.sessionId ? `session: ${meta.sessionId}` : '',
    meta.agent ? `agent: ${meta.agent}` : '',
    meta.model ? `model: ${meta.model}` : '',
    'source: hunter.agentpit.io',
    '---',
    '',
    '',
  ].filter(Boolean).join('\n')

  const blob = new Blob([frontmatter + text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = safeFilename(meta.sessionTitle || 'hunter报告')
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 300)
}

/**
 * 复制到剪贴板 · 报告纯文本(去掉 frontmatter · 便于粘贴到别处)
 */
export async function copyReport(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
