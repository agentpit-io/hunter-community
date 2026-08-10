import Link from 'next/link'

export default function NotFound() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#F7F3EC',
        color: '#211C18',
        fontFamily: '-apple-system,"PingFang SC",sans-serif',
        padding: 24,
        textAlign: 'center',
      }}
    >
      <div style={{ fontSize: 56, marginBottom: 12 }}>📭</div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 8px', fontFamily: '"Songti SC",Georgia,serif' }}>
        报告不存在或已撤销发布
      </h1>
      <p style={{ fontSize: 14, color: '#4B423A', margin: '0 0 24px', maxWidth: 420, lineHeight: 1.6 }}>
        此公开链接可能已被作者撤销发布 · 或从未存在。
      </p>
      <Link
        href="/"
        style={{
          padding: '10px 22px',
          background: '#B06A32',
          color: '#fff',
          borderRadius: 8,
          textDecoration: 'none',
          fontSize: 14,
          fontWeight: 600,
        }}
      >
        回到 Hunter 首页
      </Link>
    </div>
  )
}
