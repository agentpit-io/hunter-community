'use client'
/**
 * 报告预览组件 · 在 ArtifactPanel 里替代 JSON 显示
 * 渲染 markdown · 支持 fenced ```html``` 代码块用 iframe 沙盒显示
 */
import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github.css'
import DOMPurify from 'isomorphic-dompurify'
import { HUNTER } from '../../lib/hunter-theme'
import { extractHtmlBlock } from '../lib/reportDetect'

interface Props {
  text: string
  title?: string
}

export default function ReportViewer({ text, title }: Props) {
  const { hasHtml, htmlContent } = useMemo(() => extractHtmlBlock(text), [text])

  const safeHtml = useMemo(() => {
    if (!hasHtml) return ''
    return DOMPurify.sanitize(htmlContent, {
      // 允许标准结构 · style/script 也过滤
      ADD_ATTR: ['target'],
      FORBID_TAGS: ['script', 'style'],
      FORBID_ATTR: ['onerror', 'onload', 'onclick'],
    })
  }, [hasHtml, htmlContent])

  return (
    <div
      style={{
        padding: '28px 40px 40px',
        maxWidth: 820,
        margin: '0 auto',
        fontFamily: HUNTER.SANS,
        color: HUNTER.INK,
        lineHeight: 1.75,
        fontSize: 15,
      }}
      className="hunter-report"
    >
      {title && (
        <h1
          style={{
            fontFamily: HUNTER.SERIF,
            fontSize: 26,
            fontWeight: 700,
            color: HUNTER.INK,
            margin: '0 0 20px 0',
            paddingBottom: 12,
            borderBottom: `2px solid ${HUNTER.THEME}`,
          }}
        >
          📄 {title}
        </h1>
      )}

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          p: ({ children }) => (
            <p style={{ margin: '0 0 14px 0', lineHeight: 1.8 }}>{children}</p>
          ),
          h1: ({ children }) => (
            <h1 style={{ fontSize: 24, fontWeight: 700, margin: '28px 0 14px', color: HUNTER.INK, fontFamily: HUNTER.SERIF, borderBottom: `1px solid ${HUNTER.LINE}`, paddingBottom: 6 }}>{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: '24px 0 12px', color: HUNTER.INK, fontFamily: HUNTER.SERIF, borderLeft: `4px solid ${HUNTER.THEME}`, paddingLeft: 10 }}>{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 style={{ fontSize: 17, fontWeight: 700, margin: '20px 0 10px', color: HUNTER.INK_S }}>{children}</h3>
          ),
          ul: ({ children }) => (
            <ul style={{ margin: '0 0 14px 0', paddingLeft: 26, lineHeight: 1.8 }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol style={{ margin: '0 0 14px 0', paddingLeft: 26, lineHeight: 1.8 }}>{children}</ol>
          ),
          li: ({ children }) => <li style={{ margin: '4px 0' }}>{children}</li>,
          strong: ({ children }) => <strong style={{ fontWeight: 700, color: HUNTER.INK }}>{children}</strong>,
          em: ({ children }) => <em style={{ fontStyle: 'italic', color: HUNTER.INK_S }}>{children}</em>,
          code: ({ inline, className, children, ...props }: any) => {
            if (inline) {
              return (
                <code
                  style={{
                    background: '#f0ede2',
                    padding: '1px 6px',
                    borderRadius: 4,
                    fontSize: '0.9em',
                    fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                    color: HUNTER.COPPER3,
                  }}
                  {...props}
                >
                  {children}
                </code>
              )
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            )
          },
          pre: ({ children }: any) => (
            <pre
              style={{
                background: '#f8f6ef',
                border: `1px solid ${HUNTER.LINE}`,
                borderRadius: 8,
                padding: '14px 16px',
                overflow: 'auto',
                fontSize: 13,
                margin: '12px 0',
                fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                lineHeight: 1.55,
              }}
            >
              {children}
            </pre>
          ),
          a: ({ href, children }: any) => (
            <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: HUNTER.THEME, textDecoration: 'underline' }}>
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div style={{ overflowX: 'auto', margin: '14px 0' }}>
              <table style={{ borderCollapse: 'collapse', fontSize: 13, minWidth: '100%' }}>{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th style={{ border: `1px solid ${HUNTER.LINE}`, padding: '8px 12px', background: HUNTER.PAPER3, textAlign: 'left', fontWeight: 700, color: HUNTER.INK }}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td style={{ border: `1px solid ${HUNTER.LINE}`, padding: '8px 12px' }}>{children}</td>
          ),
          blockquote: ({ children }) => (
            <blockquote
              style={{
                borderLeft: `4px solid ${HUNTER.THEME}`,
                paddingLeft: 14,
                margin: '12px 0',
                color: HUNTER.INK_S,
                fontStyle: 'italic',
                background: '#faf9f4',
                padding: '10px 14px',
                borderRadius: '0 6px 6px 0',
              }}
            >
              {children}
            </blockquote>
          ),
          hr: () => (
            <hr style={{ border: 'none', borderTop: `1px dashed ${HUNTER.LINE}`, margin: '20px 0' }} />
          ),
        }}
      >
        {text}
      </ReactMarkdown>

      {/* HTML iframe 沙盒 · 若报告含 ```html``` 代码块 */}
      {hasHtml && (
        <div style={{ marginTop: 28, paddingTop: 20, borderTop: `2px solid ${HUNTER.LINE}` }}>
          <div
            style={{
              fontSize: 12,
              color: HUNTER.INK_F,
              marginBottom: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span style={{ padding: '2px 8px', background: HUNTER.THEME, color: '#fff', borderRadius: 4, fontSize: 10, fontWeight: 700 }}>HTML 预览</span>
            <span>· sandbox 沙盒渲染 · 已过滤 script/style</span>
          </div>
          <iframe
            sandbox="allow-same-origin"
            srcDoc={safeHtml}
            style={{
              width: '100%',
              minHeight: 500,
              border: `1px solid ${HUNTER.LINE}`,
              borderRadius: 8,
              background: '#fff',
            }}
            title="HTML 预览"
          />
        </div>
      )}
    </div>
  )
}
