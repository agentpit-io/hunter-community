// 猎鹿人品牌 token · 手机端专用（PC 端各页保留其本地 T token 不动）
export const HUNTER = {
  THEME:   '#B06A32',
  COPPER2: '#D4925A',
  COPPER3: '#7C4A22',

  UP:      '#A4332B',  // 涨 / BUY（中式红涨绿跌）
  DN:      '#3F6B40',  // 跌 / SELL
  HOLD_C:  '#7A6F63',

  BG:      '#F7F3EC',
  PAPER:   '#FFFDF9',
  PAPER2:  '#EFE8DC',
  PAPER3:  '#FBF1E4',

  INK:     '#211C18',
  INK_S:   '#4B423A',
  INK_F:   '#7A6F63',

  LINE:    '#D8CDBA',
  // 与 prototype style.css 对齐的补充 token · /chat 重设计（Phase 0）
  LINE_STRONG: '#CFCBBF',   // 输入卡强边框
  PANEL:       '#F5F5F0',   // 侧栏更深一档面板
  PANEL_2:     '#EFEEE8',   // hover 底
  SOFT:        '#A2A49D',   // 二级图标灰
  SUCCESS:     '#2F725B',   // metric 说明字 · tag 绿
  BRAND_PALE:  '#F6EEE7',   // 铜色柔和 badge 底
  TAG_OK_BG:   '#EEF5F1', TAG_OK_FG: '#2C6B55',
  TAG_WARN_BG: '#F8EEE6', TAG_WARN_FG: '#9B571F',
  SHADOW:      '0 18px 55px rgba(38,31,25,.08)',
  SHADOW_BRAND:'0 8px 20px rgba(181,107,45,.18)',

  HEADER_BG: 'linear-gradient(160deg,#252815 0%,#353A1A 55%,#282C14 100%)',
  SERIF:   '"Songti SC","Source Han Serif SC",Georgia,serif',
  SANS:    '-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif',

  R_SM: 8, R_MD: 10, R_LG: 14, R_XL: 20,
} as const

// /chat 重设计 · Hero + assistant avatar + sidebar brand-mark 共用同一张图
// 用预裁圆的 round 版 · 图本身自带深红圆背景 · 直接 border-radius:50% 就是完整品牌 mark
export const HUNTER_LOGO = '/hunter/deer-round.png'

export const MOBILE_BREAKPOINT = 640

export type DecisionType = 'BUY' | 'HOLD' | 'SELL'

export function decisionStyle(d: DecisionType) {
  return {
    BUY:  { color: HUNTER.UP,     bg: '#FBEDEA',    border: '#E6C0BA', label: '买入' },
    SELL: { color: HUNTER.DN,     bg: '#EAF3EB',    border: '#BFD8BF', label: '卖出' },
    HOLD: { color: HUNTER.HOLD_C, bg: HUNTER.PAPER2, border: HUNTER.LINE, label: '持有' },
  }[d]
}
