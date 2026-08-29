// 全站合规底栏(§3.D.3.4)· 每个页面底部常驻
// 显示:仅供研究 · Apache 2.0 · 免责/隐私链接 · GitHub
// 不使用 next/link · 避免与 auth guard 状态冲突
'use client'
export default function ComplianceFooter() {
  return (
    <footer className="fixed bottom-0 left-0 right-0 z-30 pointer-events-none">
      <div className="max-w-6xl mx-auto px-3 py-1.5 flex items-center justify-center gap-3
                      text-[10px] select-none"
           style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
        <span className="pointer-events-auto">
          Hunter Community · 开源(Apache 2.0)
        </span>
        <span>·</span>
        <a href="/legal/disclaimer" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>免责声明</a>
        <span>·</span>
        <a href="/legal/privacy" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>隐私</a>
        <span>·</span>
        <a href="https://github.com/agentpit-io/hunter-community" target="_blank"
           rel="noopener noreferrer" className="pointer-events-auto hover:underline"
           style={{ color: 'inherit' }}>GitHub</a>
        <span>·</span>
        <span className="pointer-events-auto">仅供研究 · 非投资建议</span>
      </div>
    </footer>
  )
}
