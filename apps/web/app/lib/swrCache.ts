// P2 首屏 SWR 缓存工具
//
// 用于让 /wx/home 与 /gm/home 首屏立即渲染上次的数据快照,
// 后台并行 revalidate 拉最新数据后再覆盖,老用户回访时视觉上"秒开"。
//
// 用法:
//   const cached = readSwr<Stock[]>('watchlist')
//   if (cached) setStocks(cached)     // 立即渲染, 不进 loading 态
//   const fresh = await fetch(...)
//   setStocks(fresh)
//   writeSwr('watchlist', fresh)
//
// 命名空间隔离: 缓存写入时会记录当前 hunter_token JWT 里的 sub (user_id) 指纹,
// 读取时若发现 uid 不一致 (换号) 则视为 miss, 避免 A 用户看到 B 用户的自选。

type SwrRecord<T> = { data: T; at: number; uid: string }

const PREFIX = 'hunter_swr:'
const DEFAULT_TTL_MS = 6 * 3600 * 1000  // 6 小时: 老用户回访足够, 不至于陈旧太久

function decodeUid(token: string | null): string {
  if (!token) return ''
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.sub || payload.email || ''
  } catch {
    return ''
  }
}

export function readSwr<T>(key: string, ttlMs: number = DEFAULT_TTL_MS): T | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    const rec = JSON.parse(raw) as SwrRecord<T>
    if (!rec || typeof rec.at !== 'number') return null
    if (Date.now() - rec.at > ttlMs) return null
    const currUid = decodeUid(localStorage.getItem('hunter_token'))
    if (rec.uid && currUid && rec.uid !== currUid) return null
    return rec.data
  } catch {
    return null
  }
}

export function writeSwr<T>(key: string, data: T): void {
  if (typeof window === 'undefined') return
  try {
    const uid = decodeUid(localStorage.getItem('hunter_token'))
    localStorage.setItem(PREFIX + key, JSON.stringify({ data, at: Date.now(), uid }))
  } catch {
    /* localStorage 满 / 无痕模式 → 静默失败 */
  }
}

export function clearSwr(key: string): void {
  if (typeof window === 'undefined') return
  try { localStorage.removeItem(PREFIX + key) } catch { /* ignore */ }
}
