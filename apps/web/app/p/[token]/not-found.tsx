/**
 * /p/{token} 找不到时的页面。
 *
 * 为什么单独一个文件:页面里直接 return 一段"链接无效"的 JSX 会以
 * **HTTP 200** 返回 —— 一个不存在的存证链接返回 200 是错的,
 * 爬虫会收录它,监控也看不出异常。
 *
 * Next 的 App Router 里,页面无法自己设状态码,只能 notFound() +
 * 同路由段的 not-found.tsx,这样才是真 404。
 */
import Link from 'next/link'

export default function NotFound() {
  return (
    <div style={{ minHeight: '100vh', background: '#FAF8F4', color: '#2B2723' }}>
      <main style={{ maxWidth: 620, margin: '0 auto', padding: '36px 20px 60px' }}>
        <div style={{
          background: '#fff', border: '1px solid #E7E2D9', borderRadius: 14,
          padding: '22px 24px 26px',
        }}>
          <h1 style={{ fontSize: 19, fontWeight: 600, marginBottom: 8 }}>分享链接无效</h1>
          <p style={{ fontSize: 13.5, color: '#6B6459', lineHeight: 1.9 }}>
            这个存证链接不存在,或者对应的预测记录已经被清理。
          </p>
          <Link href="/" style={{ display: 'inline-block', marginTop: 12,
                                  fontSize: 12.5, color: '#A9714B' }}>
            Hunter Community →
          </Link>
        </div>
      </main>
    </div>
  )
}
