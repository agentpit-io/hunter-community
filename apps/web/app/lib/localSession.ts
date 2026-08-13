// 单用户模式 · 自动取会话,不出现登录页
//
// 开源版跑在你自己机器上,为了看一眼行情先编一个邮箱和密码是白花的力气 ——
// 能打开这个浏览器的人本来就能读数据库。所以前端开机时静默换一个 token,
// 用户全程看不到登录表单。
//
// 后端 /api/auth/local-session 在多用户模式下返回 403,这里据此判断
// "这台实例是有真账号的",调用方回退到正常登录流程。
//
// access token 只有 1 小时,所以这个函数还承担续期:AuthGuard 拦到 401 时
// 会强制重取一次再重放请求,用户不会因为泡了一小时咖啡就被踢回登录页。

const API = process.env.NEXT_PUBLIC_API_URL || ''

/** null = 这台实例不是单用户模式(或后端不可用),调用方该走登录页 */
let multiUser = false
let singleUser = false      // 换到过 token 才置真 —— 用于隐藏"退出登录"这类无意义入口
let inflight: Promise<string | null> | null = null

function store(d: any): string | null {
  const token = d?.access_token || d?.token
  if (!token) return null
  try {
    localStorage.setItem('hunter_token', token)
    if (d.refresh_token) localStorage.setItem('hunter_refresh', d.refresh_token)
  } catch {
    /* 隐私模式下 localStorage 可能不可写 · 拿不到就当没登录 */
  }
  return token
}

/**
 * 确保本地有可用 token。
 * @param force 忽略已有 token,强制换一把新的(token 过期后调用)
 */
export async function ensureLocalSession(force = false): Promise<string | null> {
  if (typeof window === 'undefined') return null
  if (!force) {
    const existing = localStorage.getItem('hunter_token')
    if (existing) return existing
  }
  if (multiUser) return null
  if (inflight) return inflight

  inflight = (async () => {
    try {
      // 注意用原始 fetch:AuthGuard 会包一层 401 处理,那层又会调回这里
      const f = (window as any).__hunterOriginalFetch || window.fetch
      const r = await f(`${API}/api/auth/local-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
      })
      if (r.status === 403) {
        multiUser = true   // 这台实例开了多用户,别再试了
        return null
      }
      if (!r.ok) return null
      const token = store(await r.json())
      if (token) singleUser = true
      return token
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** 已知这台实例是多用户模式吗(要显示登录表单) */
export function isMultiUser(): boolean {
  return multiUser
}

/**
 * 已确认是单用户实例吗。
 * 用来藏掉"退出登录"这种在单用户模式下点了等于没点的入口
 * —— 清掉 token 后 AuthGuard 立刻又换一把回来。
 */
export function isSingleUser(): boolean {
  return singleUser
}
