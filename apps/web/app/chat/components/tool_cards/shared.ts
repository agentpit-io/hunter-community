// 富卡片共用 helper · 数值/颜色/跳转 prompt 构造
import { HUNTER } from '../../../lib/hunter-theme'

export function parseToolOutput<T = any>(output: string | undefined | null): T | null {
  if (!output) return null
  try {
    return JSON.parse(output) as T
  } catch {
    return null
  }
}

export function fmtMoney(v: number, digits = 2): string {
  const s = v >= 0 ? '' : '-'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${s}¥${(abs / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${s}¥${(abs / 1e4).toFixed(2)}万`
  return `${s}¥${abs.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export function fmtVolume(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return v.toLocaleString('zh-CN')
}

export function fmtPct(v: number, digits = 2): string {
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

/** 涨跌色 · 中式：红涨 / 绿跌 */
export function upDnColor(v: number): string {
  if (v > 0.01) return HUNTER.UP
  if (v < -0.01) return HUNTER.DN
  return HUNTER.INK_F
}

/** 把 prompt 编码后跳转 · 走 chat 现有 autoText+autoSend 机制 */
export function goChat(prompt: string) {
  window.location.href = '/chat?q=' + encodeURIComponent(prompt)
}

/** 显示 toast · 简版 · 无需引入 UI 库 */
export function toast(msg: string, kind: 'ok' | 'warn' | 'err' = 'ok') {
  const el = document.createElement('div')
  const bg = kind === 'ok' ? '#2f725b' : kind === 'warn' ? '#8f4f1d' : '#a4332b'
  el.style.cssText = `
    position:fixed;right:24px;bottom:24px;z-index:9999;
    padding:10px 16px;background:${bg};color:#fff;
    border-radius:10px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.25);
    animation:toast-in .22s ease-out;
  `
  el.textContent = msg
  document.body.appendChild(el)
  setTimeout(() => {
    el.style.transition = 'opacity .3s'
    el.style.opacity = '0'
    setTimeout(() => el.remove(), 350)
  }, 2400)
}
