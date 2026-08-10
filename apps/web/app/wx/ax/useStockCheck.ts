import { useCallback, useEffect, useRef, useState } from 'react'

const DEBOUNCE_MS = 300

export type CheckStatus = 'idle' | 'busy' | 'matched' | 'unsupported' | 'notfound'
export interface StockItem { code: string; name: string; exchange?: string; market?: string }

interface CheckResp {
  ok?: boolean
  matches?: StockItem[]
  reason?: 'unsupported_market' | 'not_found'
  message?: string
}

/**
 * AX intake 页股票检查 hook。
 * - blur 300ms debounce 自动触发
 * - Enter 立即触发（跳过 debounce）
 * - reqSeq 竞态防护：连续查询只保留最新一次结果
 * - lastCheckedQuery 去重：同 query 已查过则跳过
 * - 修改输入 → 自动回 idle，允许下次重查
 */
export function useStockCheck() {
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<StockItem | null>(null)
  const [status, setStatus] = useState<CheckStatus>('idle')
  const [msg, setMsg] = useState('')
  const [matches, setMatches] = useState<StockItem[]>([])

  const reqSeqRef = useRef(0)
  const lastCheckedRef = useRef('')
  const debounceTimerRef = useRef<number | null>(null)

  const doCheck = useCallback(async (rawQ: string) => {
    const q = rawQ.trim()
    if (!q) return
    // 同 query 且当前不是 idle（有结果展示中）→ 跳过
    if (q === lastCheckedRef.current && status !== 'idle') return

    const mySeq = ++reqSeqRef.current
    lastCheckedRef.current = q
    setStatus('busy'); setMsg(''); setMatches([])
    try {
      const r = await fetch(`/api/online-analysis/check-stock?q=${encodeURIComponent(q)}`)
      const d: CheckResp = await r.json()
      if (mySeq !== reqSeqRef.current) return   // 已被更新的请求覆盖，丢弃
      if (d.ok && Array.isArray(d.matches) && d.matches.length > 0) {
        if (d.matches.length === 1) {
          setPicked(d.matches[0])
          setStatus('idle')
        } else {
          setMatches(d.matches)
          setStatus('matched')
        }
      } else if (d.reason === 'unsupported_market') {
        setStatus('unsupported')
        setMsg(d.message || '本活动分析目前仅支持 A 股')
      } else {
        setStatus('notfound')
        setMsg(d.message || '未识别到该股票')
      }
    } catch {
      if (mySeq !== reqSeqRef.current) return
      setStatus('notfound')
      setMsg('检查失败，请稍后重试')
    }
  }, [status])

  const scheduleCheckOnBlur = useCallback((q: string) => {
    if (debounceTimerRef.current) window.clearTimeout(debounceTimerRef.current)
    debounceTimerRef.current = window.setTimeout(() => { doCheck(q) }, DEBOUNCE_MS)
  }, [doCheck])

  // Enter 键：跳过 debounce 立即触发
  const doCheckNow = useCallback((q: string) => {
    if (debounceTimerRef.current) window.clearTimeout(debounceTimerRef.current)
    doCheck(q)
  }, [doCheck])

  const onQueryChange = useCallback((v: string) => {
    setQuery(v)
    // 用户开始改动 → 清空上一次的结果状态，允许下次重查
    setStatus((s) => (s === 'idle' ? s : 'idle'))
    setMatches([])
    setMsg('')
    lastCheckedRef.current = ''
  }, [])

  const pickMatch = useCallback((s: StockItem) => {
    setPicked(s); setStatus('idle'); setMatches([]); setMsg('')
  }, [])

  const reset = useCallback(() => {
    if (debounceTimerRef.current) { window.clearTimeout(debounceTimerRef.current); debounceTimerRef.current = null }
    setPicked(null); setQuery(''); setMatches([]); setStatus('idle'); setMsg('')
    lastCheckedRef.current = ''
  }, [])

  useEffect(() => () => {
    if (debounceTimerRef.current) window.clearTimeout(debounceTimerRef.current)
  }, [])

  return {
    query, picked, status, msg, matches,
    onQueryChange, scheduleCheckOnBlur, doCheckNow, pickMatch, reset,
  }
}
