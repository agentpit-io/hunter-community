'use client'
import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'

const API = process.env.NEXT_PUBLIC_API_URL || ''

interface NewsItem {
  id: number; code: string; title: string; source: string; url: string
  sentiment: string; published_at: string
}

const STOCKS: Record<string, string> = {
  '002595': '豪迈科技',
  '000933': '神火股份',
  '601899': '紫金矿业',
  '002001': '新和成',
  '02333': '长城汽车',
  '01378': '中国宏桥',
}

function sentimentColor(s: string) {
  if (s === 'positive') return '#ef4444'
  if (s === 'negative') return '#22c55e'
  return '#64748b'
}

function sentimentLabel(s: string) {
  if (s === 'positive') return '利好'
  if (s === 'negative') return '利空'
  return '中性'
}

export default function NewsPage() {
  const [news, setNews] = useState<NewsItem[]>([])
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(API + '/api/news?limit=100')
      .then(r => r.json())
      .then(d => { setNews(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const filtered = filter === 'all' ? news : news.filter(n => n.code === filter)

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar />
      <div className="flex-1 ml-52 p-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold mb-1">情报中心</h1>
          <p className="text-xs text-slate-500">全部自选股最新资讯，每5分钟更新</p>
        </div>

        {/* Filter */}
        <div className="flex gap-2 mb-5 flex-wrap">
          <button
            onClick={() => setFilter('all')}
            className="px-3 py-1 rounded-full text-xs transition-colors"
            style={{
              background: filter === 'all' ? '#3b82f6' : 'var(--bg-card)',
              color: filter === 'all' ? '#fff' : '#94a3b8',
              border: '1px solid var(--border)',
            }}
          >
            全部
          </button>
          {Object.entries(STOCKS).map(([code, name]) => (
            <button
              key={code}
              onClick={() => setFilter(code)}
              className="px-3 py-1 rounded-full text-xs transition-colors"
              style={{
                background: filter === code ? '#3b82f6' : 'var(--bg-card)',
                color: filter === code ? '#fff' : '#94a3b8',
                border: '1px solid var(--border)',
              }}
            >
              {name}
            </button>
          ))}
        </div>

        {/* News List */}
        {loading ? (
          <div className="text-slate-600 text-sm">加载中...</div>
        ) : filtered.length === 0 ? (
          <div className="text-slate-600 text-sm">暂无新闻数据</div>
        ) : (
          <div className="space-y-2">
            {filtered.map(n => (
              <a
                key={n.id}
                href={n.url}
                target="_blank"
                rel="noreferrer"
                className="flex gap-4 p-3 rounded-xl hover:opacity-80 transition-opacity"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}
              >
                {/* 情绪色条 */}
                <div
                  className="w-1 rounded-full flex-shrink-0"
                  style={{ background: sentimentColor(n.sentiment) }}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-slate-800 leading-snug mb-1 truncate">{n.title}</div>
                  <div className="flex gap-3 text-[11px] text-slate-500 flex-wrap">
                    <span className="text-slate-500">{STOCKS[n.code] || n.code}</span>
                    <span>{n.source}</span>
                    <span>{n.published_at?.slice(0, 16)}</span>
                    {n.sentiment && (
                      <span style={{ color: sentimentColor(n.sentiment) }}>
                        {sentimentLabel(n.sentiment)}
                      </span>
                    )}
                  </div>
                </div>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
