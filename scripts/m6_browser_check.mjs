/**
 * M6 · 真浏览器逐页实测（Playwright · headless Chromium）
 *
 * 「API 通 ≠ 组件通」——这一轮必须真浏览器跑一遍再报完成（任务书的硬要求）。
 * 这个脚本做四件事，结果与截图都落在 `M6_OUT`（默认 `/tmp/m6-shots`）：
 *
 * 1. **逐页打开** 总览 / 自动交易 / 我的账户 / 每日报告 / 安全与帮助，
 *    记录控制台错误、页面异常、失败请求；截图。
 * 2. **关键交互跑一遍**：总开关（关→开）、风险档位（收紧）、策略切换、
 *    快照凭证展开、开新项目确认框（打开→取消）、报告事实行展开 + 切历史报告。
 * 3. **三条体检**：JS 报错 0 · 空图标 0（`<img>` 加载失败）· 无横向溢出
 *    （`scrollWidth <= innerWidth`）。
 * 4. **窄屏再看一眼**：帮助页与总览页 390×844。
 *
 * 用法（本机）：
 *     NODE_PATH=$(npm root -g 的上一级缓存路径) node scripts/m6_browser_check.mjs
 * 实测用的解释器位置见 `docs/开发文档/M6-成果与测试报告.md` 的「怎么跑」。
 */
import { mkdirSync, writeFileSync } from 'node:fs'

// playwright 的解析路径可由环境变量给（本机是从 npx 缓存里跑的：
// ESM 不认 NODE_PATH，只能给绝对路径）。装了依赖时留空即可。
const PW = process.env.M6_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
// CJS 包经 ESM 动态 import 时，命名导出可能在 default 上（取决于包结构与 node 版本）
const chromium = _pw.chromium || _pw.default?.chromium
if (!chromium) throw new Error(`拿不到 playwright 的 chromium（来自 ${PW}）`)

const BASE = process.env.M6_BASE || 'http://127.0.0.1:3110'
const OUT = process.env.M6_OUT || '/tmp/m6-shots'
const PAGES = [
  ['overview', '/finance/overview', '总览'],
  ['auto-trade', '/finance/auto-trade', '自动交易'],
  ['account', '/finance/account', '我的账户'],
  ['report', '/finance/report', '每日报告'],
  ['help', '/finance/help', '安全与帮助'],
]

const report = { base: BASE, pages: [], interactions: [], notes: [] }
const consoleErrors = []
const pageErrors = []
const failedRequests = []

function record(pageKey, kind, ok, detail) {
  report.interactions.push({ page: pageKey, kind, ok, detail })
}

async function settle(page, ms = 1200) {
  await page.waitForLoadState('load').catch(() => {})
  await page.waitForTimeout(ms)
}

/**
 * 关掉站点的「合规告知」弹层（它不是智能交易板块的，但会盖住整页、拦住点击）。
 * 不关它，后面所有点击都会被 `fixed inset-0` 接走 —— 这不是本页的 bug。
 */
async function dismissCompliance(page) {
  const ok = page.getByRole('button', { name: /确认继续/ }).first()
  if (!(await ok.count())) return false
  const cb = page.locator('input[type="checkbox"]').first()
  if (await cb.count()) await cb.click().catch(() => {})
  await ok.click().catch(() => {})
  await page.waitForTimeout(1500)
  return true
}

async function health(page) {
  return await page.evaluate(() => {
    const doc = document.documentElement
    const imgs = Array.from(document.images).map(i => ({ src: i.currentSrc || i.src, w: i.naturalWidth }))
    return {
      url: location.pathname,
      title: document.title,
      scrollWidth: doc.scrollWidth,
      innerWidth: window.innerWidth,
      overflowX: doc.scrollWidth > window.innerWidth + 1,
      brokenImages: imgs.filter(i => i.w < 1),
      imgCount: imgs.length,
      bodyText: (document.body.innerText || '').slice(0, 400),
      svgCount: document.querySelectorAll('svg').length,
    }
  })
}

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
const page = await context.newPage()
page.on('console', m => { if (m.type() === 'error') consoleErrors.push({ url: page.url(), text: m.text() }) })
page.on('pageerror', e => pageErrors.push({ url: page.url(), text: String(e) }))
page.on('requestfailed', r => failedRequests.push({ url: r.url(), err: r.failure()?.errorText }))

mkdirSync(OUT, { recursive: true })

// 先开一次，处理合规弹层（它只出现一次，之后 localStorage 有标记）
await page.goto(BASE + '/finance/overview', { waitUntil: 'load' })
await settle(page, 2000)
report.complianceDismissed = await dismissCompliance(page)
await page.screenshot({ path: `${OUT}/00-登录与合规.png`, fullPage: false })

// ── 1) 逐页打开 ──────────────────────────────────────────────────────────
for (const [key, path, label] of PAGES) {
  const before = { c: consoleErrors.length, p: pageErrors.length }
  await page.goto(BASE + path, { waitUntil: 'load' })
  await settle(page, 1800)
  const h = await health(page)
  await page.screenshot({ path: `${OUT}/${key}.png`, fullPage: true })
  report.pages.push({
    key, label, path: h.url, title: h.title,
    overflowX: h.overflowX, scrollWidth: h.scrollWidth, innerWidth: h.innerWidth,
    brokenImages: h.brokenImages.length, imgCount: h.imgCount, svgCount: h.svgCount,
    newConsoleErrors: consoleErrors.length - before.c,
    newPageErrors: pageErrors.length - before.p,
    sample: h.bodyText.replace(/\s+/g, ' ').slice(0, 180),
  })
}

// ── 2) 关键交互 ──────────────────────────────────────────────────────────
await page.goto(BASE + '/finance/auto-trade', { waitUntil: 'load' }); await settle(page)

// 2.1 总开关：关 → 开（读 aria-pressed 判状态）
const sw = page.locator('button[aria-label="自动交易总开关"]').first()
if (await sw.count()) {
  const before = await sw.getAttribute('aria-pressed')
  await sw.click(); await page.waitForTimeout(2500)
  const after = await sw.getAttribute('aria-pressed')
  await page.screenshot({ path: `${OUT}/auto-trade-switch-off.png`, fullPage: false })
  await sw.click(); await page.waitForTimeout(2500)
  const back = await sw.getAttribute('aria-pressed')
  record('auto-trade', '总开关 关→开', before !== after && back === before, `${before} → ${after} → ${back}`)
} else {
  record('auto-trade', '总开关', false, '找不到开关按钮')
}

// 2.2 风险档位：点「保守」（收紧，不需要二次确认）
const riskBtn = page.getByRole('button', { name: /保守/ }).first()
if (await riskBtn.count()) {
  await riskBtn.click(); await page.waitForTimeout(2500)
  const body = await page.evaluate(() => document.body.innerText)
  record('auto-trade', '风险档位改为保守', /当前：保守/.test(body), body.includes('当前：保守') ? '已生效' : '未见「当前：保守」')
  await page.screenshot({ path: `${OUT}/auto-trade-risk.png`, fullPage: true })
} else record('auto-trade', '风险档位', false, '找不到保守档位按钮')

// 2.3 策略切换：点一个不是当前使用的策略卡
const strat = page.locator('button:has-text("点击选用")').first()
if (await strat.count()) {
  await strat.click(); await page.waitForTimeout(2500)
  const body = await page.evaluate(() => document.body.innerText)
  record('auto-trade', '切换策略', /下一次决策生效|已切到/.test(body) || body.includes('正在使用'), '已提交切换')
  await page.screenshot({ path: `${OUT}/auto-trade-strategy.png`, fullPage: true })
} else record('auto-trade', '切换策略', false, '找不到可切换的策略卡')

// 2.4 参数变更日志有行（说明改动落库并回读了）
const logText = await page.evaluate(() => document.body.innerText)
record('auto-trade', '参数变更日志可见', /auto_enabled|risk_tier|active_strategy/.test(logText),
  /auto_enabled|risk_tier|active_strategy/.test(logText) ? '日志区列出了字段名' : '日志区没看到字段名')

// 2.5 我的账户：快照凭证展开
await page.goto(BASE + '/finance/account', { waitUntil: 'load' }); await settle(page)
const snapBtn = page.locator('button:has-text("SNAP-")').first()
if (await snapBtn.count()) {
  const label = await snapBtn.innerText()
  await snapBtn.click(); await page.waitForTimeout(1500)
  const body = await page.evaluate(() => document.body.innerText)
  const ok = body.includes('快照凭证') && body.includes(label.trim())
  record('account', '快照凭证展开', ok, `点了 ${label.trim()}`)
  await page.screenshot({ path: `${OUT}/account-voucher.png`, fullPage: false })
} else record('account', '快照凭证展开', false, '找不到快照编号按钮')

// 2.6 我的账户：金额输入框只读
const ro = await page.evaluate(() => {
  const el = document.querySelector('input[aria-label="初始资金（只读）"]')
  if (!el) return { found: false }
  return { found: true, readOnly: el.readOnly, value: el.value, disabled: el.disabled }
})
record('account', '金额输入框只读', ro.found && ro.readOnly, JSON.stringify(ro))

// 2.7 我的账户：开新项目确认框（打开 → 取消，**不真的关停**）
const newBtn = page.getByRole('button', { name: /开新项目/ }).first()
if (await newBtn.count()) {
  await newBtn.click(); await page.waitForTimeout(800)
  const dlg = page.locator('[role="dialog"][aria-label="开新项目确认"]')
  const visible = await dlg.count() > 0
  const text = visible ? await dlg.innerText() : ''
  await page.screenshot({ path: `${OUT}/account-new-project-confirm.png`, fullPage: false })
  record('account', '开新项目确认框', visible && /关闭当前项目/.test(text) && /代价/.test(text),
    visible ? `确认框文案含代价说明：${/从零开始计/.test(text)}` : '确认框没出现')
  if (visible) { await page.getByRole('button', { name: /取消/ }).first().click(); await page.waitForTimeout(500) }
} else record('account', '开新项目确认框', false, '找不到按钮')

// 2.8 每日报告：展开事实行 + 切历史报告
await page.goto(BASE + '/finance/report', { waitUntil: 'load' }); await settle(page)
const expand = page.getByRole('button', { name: /^展开$/ }).first()
if (await expand.count()) {
  await expand.click(); await page.waitForTimeout(900)
  const rows = await page.locator('table tbody tr').count()
  record('report', '逐数字追溯展开', rows > 0, `事实行表格 ${rows} 行`)
  await page.screenshot({ path: `${OUT}/report-facts.png`, fullPage: false })
} else record('report', '逐数字追溯展开', false, '找不到展开按钮')

const hist = page.locator('table tbody tr button').last()
if (await hist.count()) {
  const d = await hist.innerText()
  await hist.click(); await page.waitForTimeout(1600)
  const body = await page.evaluate(() => document.body.innerText)
  record('report', '切历史报告', body.includes(d.trim()), `点了 ${d.trim()}`)
  await page.screenshot({ path: `${OUT}/report-history.png`, fullPage: true })
}

// ── 2.9) 渲染文本里不许出现人机协作的入口词（一期分期边界的机器化检查）──────
// 判据是**渲染出来的字**，不是源码里的注释 —— 源码里解释「为什么不画」是可以的。
const BANNED = ['运行模式', '我的选股池', '我来下单', '归属', '来源列', '待我确认', '与实盘完全一致']
report.bannedWords = {}
for (const [key, path] of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'load' }); await settle(page, 1200)
  const text = await page.evaluate(() => document.body.innerText)
  report.bannedWords[key] = BANNED.filter(w => text.includes(w))
}
report.noCopilotBlocks = Object.values(report.bannedWords).every(a => a.length === 0)

// ── 2.10) 后端停掉 → 报错态（可选：等一个信号文件出现再测）────────────────
if (process.env.M6_DOWN_SIGNAL) {
  const fs = await import('node:fs')
  const sig = process.env.M6_DOWN_SIGNAL
  for (let i = 0; i < 120 && !fs.existsSync(sig); i++) await page.waitForTimeout(500)
  for (const [key, path] of [['overview', '/finance/overview'], ['auto-trade', '/finance/auto-trade']]) {
    await page.goto(BASE + path, { waitUntil: 'load' }); await settle(page, 2500)
    const text = await page.evaluate(() => document.body.innerText)
    await page.screenshot({ path: `${OUT}/${key}-backend-down.png`, fullPage: true })
    report.interactions.push({ page: key, kind: '后端停掉→报错态', ok: /失败|读不到/.test(text),
      detail: text.replace(/\s+/g, ' ').slice(0, 120) })
  }
}

// ── 3) 窄屏 ──────────────────────────────────────────────────────────────
await page.setViewportSize({ width: 390, height: 844 })
for (const [key, path] of [['overview', '/finance/overview'], ['help', '/finance/help']]) {
  await page.goto(BASE + path, { waitUntil: 'load' }); await settle(page, 1500)
  const h = await health(page)
  await page.screenshot({ path: `${OUT}/${key}-narrow.png`, fullPage: true })
  report.pages.push({ key: key + '@390', label: '窄屏', path: h.url, overflowX: h.overflowX,
    scrollWidth: h.scrollWidth, innerWidth: h.innerWidth, brokenImages: h.brokenImages.length })
}

// ── 汇总 ─────────────────────────────────────────────────────────────────
report.consoleErrors = consoleErrors
report.pageErrors = pageErrors
report.failedRequests = failedRequests.filter(r => !/favicon/.test(r.url))
report.summary = {
  consoleErrors: consoleErrors.length,
  pageErrors: pageErrors.length,
  failedRequests: report.failedRequests.length,
  overflowPages: report.pages.filter(p => p.overflowX).map(p => p.key),
  brokenImagesTotal: report.pages.reduce((n, p) => n + (p.brokenImages || 0), 0),
  interactionsFailed: report.interactions.filter(i => !i.ok).length,
}
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report.summary, null, 2))
console.log(JSON.stringify(report.interactions, null, 1))
if (report.consoleErrors.length) console.log('CONSOLE ERRORS:', JSON.stringify(report.consoleErrors.slice(0, 10), null, 1))
if (report.pageErrors.length) console.log('PAGE ERRORS:', JSON.stringify(report.pageErrors.slice(0, 10), null, 1))
if (report.failedRequests.length) console.log('FAILED REQUESTS:', JSON.stringify(report.failedRequests.slice(0, 10), null, 1))
await browser.close()
