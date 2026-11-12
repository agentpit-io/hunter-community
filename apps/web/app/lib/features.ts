/**
 * 分期开关（`05-最小原型开发任务清单.md` §1.3 / 总控规则 §六-10）。
 *
 * 一期（最小原型）只做「AI 自主」：人只看、不动手。人机协作的整块 UI
 * **一律不渲染**——不是灰掉、不是禁用。灰色按钮会骗人：用户看见一个
 * 「我来下单」的区域，会以为「这东西已经有、只是现在不让我用」，
 * 而真相是后端那条人工委托通道还不存在。
 *
 * 因此二期的这些东西（H-01 / H-02 / H-03 / H-05 / G-01）全部挂在下面这个开关后面：
 *   运行模式三选一 · 我的选股池 · 我来下单 · 持仓「归属」列 ·
 *   成交「来源」列 · 总览「待我确认」条
 * `false`（默认）时它们的代码路径不产生任何 DOM 节点。
 *
 * 二期把 `copilot` 置 true 重新构建即可，业务代码不用改结构。
 * 与原型侧 `plan/ref/_gen/slim.mjs` 的 `FEATURES = { copilot, gate }` 同语义。
 *
 * ⚠️ 这是分期边界的**唯一开关**：任何一期都不允许「先做个能点的入口、后端回头补」。
 */
export const FEATURE_COPILOT: boolean = process.env.NEXT_PUBLIC_FEATURE_COPILOT === 'true'

/** 三期（把关与归因）的开关，一期恒 false。 */
export const FEATURE_GATE: boolean = process.env.NEXT_PUBLIC_FEATURE_GATE === 'true'
