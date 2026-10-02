/**
 * N5 · 多市场前端真浏览器实测（Playwright · headless Chromium）。
 *
 * 「API 通 ≠ 组件通」—— 本轮改了 `apps/web/app/finance/` 六个文件，必须真浏览器跑一遍。
 * 本脚本做五件事，结果与截图落在 `N5_OUT`（默认 `/tmp/n5-shots`）：
 *
 * 1. **登录**（调 `/api/auth/login` 拿 token 塞进 localStorage，与 `_ui.getToken` 同一键）；
 * 2. **逐页打开** 总览 / 自动交易 / 我的账户 / 每日报告，记录 console error、pageerror、
 *    失败请求，截图；
 * 3. **市场切换器真点一遍**：A股 → 港股 → 美股，断言金额符号随之变化
 *    （`¥` / `HK$` / `$`）、市场状态条出现三个市场；
 * 4. **跨市场合计区**：断言合计与汇率来源/时刻显示出来（或如实显示 `—` + 原因）；
 * 5. **报告页**：断言历史表出现「市场」列、事实行金额带币种符号。
 *
 * 用法（本机）：
 *     N5_BASE=http://127.0.0.1:3110 node scripts/n5_browser_check.mjs
 * playwright 的解析路径可用 `N5_PLAYWRIGHT` 给绝对路径（ESM 不认 NODE_PATH）。
 */
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.N5_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || _pw.default?.chromium
if (!chromium) throw new Error(`拿不到 playwright 的 chromium（来自 ${PW}）`)

const BASE = process.env.N5_BASE || 'http://127.0.0.1:3110'
const API = process.env.N5_API || 'http://127.0.0.1:8102'
const OUT = process.env.N5_OUT || '/tmp/n5-shots'
const EMAIL = process.env.N5_EMAIL || 'n5demo@example.com'
const PASSWORD = process.env.N5_PASSWORD || 'n5demo123'

const report = { base: BASE, pages: [], interactions: [], notes: [] }
const consoleErrors = []
const pageErrors = []
const failedRequests = []

function record(pageKey, kind, ok, detail) {
  report.interactions.push({ page: pageKey, kind, ok, detail })
}

async function settle(page, ms = 1500) {
  await page.waitForLoadState('load').catch(() => {})
  await page.waitForTimeout(ms)
}

async function health(page) {
  return await page.evaluate(() => {
    const doc = document.documentElement
    const imgs = Array.from(document.images).map(i => ({ src: i.currentSrc || i.src, w: i.naturalWidth }))
    return {
      url: location.pathname, title: document.title,
      scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth,
      overflowX: doc.scrollWidth > window.innerWidth + 1,
      brokenImages: imgs.filter(i => i.w < 1).length, imgCount: imgs.length,
      bodyText: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 600),
    }
  })
}

// ── 0) 登录拿 token（HTTP，不经浏览器）──────────────────────────────────
const loginRes = await fetch(`${API}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
})
if (!loginRes.ok) throw new Error(`登录失败：HTTP ${loginRes.status} ${await loginRes.text()}`)
const token = (await loginRes.json()).access_token
report.login = { email: EMAIL, ok: !!token }

// 合规确认是**服务端**记的（`POST /auth/compliance-ack`），先记上 —— 否则弹层会盖住整页、
// 拦掉所有点击（那不是本页的 bug）。已确认过时这个请求是幂等的。
await fetch(`${API}/api/auth/compliance-ack`, {
  method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
}).catch(() => {})

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
const page = await context.newPage()
page.on('console', m => { if (m.type() === 'error') consoleErrors.push({ url: page.url(), text: m.text() }) })
page.on('pageerror', e => pageErrors.push({ url: page.url(), text: String(e) }))
page.on('requestfailed', r => failedRequests.push({ url: r.url(), err: r.failure()?.errorText }))

mkdirSync(OUT, { recursive: true })

// 合规弹层（若有）先关掉，否则整页点击被它接走。
await page.goto(BASE + '/finance/overview', { waitUntil: 'load' }); await settle(page, 2200)
const okBtn = page.getByRole('button', { name: /确认继续/ }).first()
if (await okBtn.count()) {
  const cb = page.locator('input[type="checkbox"]').first()
  if (await cb.count()) await cb.click().catch(() => {})
  await okBtn.click().catch(() => {})
  await settle(page, 1500)
}
report.complianceDismissed = true
await page.screenshot({ path: `${OUT}/00-overview-CN_A.png`, fullPage: true })

// ── 1) 逐页打开 ──────────────────────────────────────────────────────────
const PAGES = [
  ['overview', '/finance/overview', '总览'],
  ['auto-trade', '/finance/auto-trade', '自动交易'],
  ['account', '/finance/account', '我的账户'],
  ['report', '/finance/report', '每日报告'],
]
for (const [key, path, label] of PAGES) {
  const before = { c: consoleErrors.length, p: pageErrors.length }
  await page.goto(BASE + path, { waitUntil: 'load' }); await settle(page)
  const h = await health(page)
  await page.screenshot({ path: `${OUT}/${key}.png`, fullPage: true })
  report.pages.push({
    key, label, path: h.url, title: h.title, overflowX: h.overflowX,
    brokenImages: h.brokenImages, newConsoleErrors: consoleErrors.length - before.c,
    newPageErrors: pageErrors.length - before.p,
    sample: h.bodyText.slice(0, 200),
  })
}

// ── 2) 市场切换器：逐市场点一遍，断言金额符号随市场变 ────────────────────
async function pickMarket(m) {
  // 兜底：任何遗留弹层（合规 / 确认框）先关掉，否则它会接走点击。
  const ack = page.getByRole('button', { name: /确认继续/ }).first()
  if (await ack.count() && await ack.isVisible().catch(() => false)) {
    const cb = page.locator('input[type="checkbox"]').first()
    if (await cb.count()) await cb.click().catch(() => {})
    await ack.click().catch(() => {})
    await settle(page, 1000)
  }
  const btn = page.locator(`button[role="tab"]:has-text("${m}")`).first()
  if (!(await btn.count())) return null
  await btn.click({ timeout: 15000 }).catch(() => {})
  await settle(page, 2000)
  return await page.evaluate(() => document.body.innerText)
}

await page.goto(BASE + '/finance/overview', { waitUntil: 'load' }); await settle(page)
const textCN = await page.evaluate(() => document.body.innerText)
record('overview', '市场状态条（三市场）',
  /A 股/.test(textCN) && /港股/.test(textCN) && /美股/.test(textCN) && /(交易中|休市|盘前|盘后|午间休市|日历缺失)/.test(textCN),
  textCN.replace(/\s+/g, ' ').slice(0, 120))

const textHK = await pickMarket('港股')
await page.screenshot({ path: `${OUT}/01-overview-HK.png`, fullPage: true })
record('overview', '切到港股 → 金额变 HK$', !!textHK && textHK.includes('HK$'), (textHK || '').match(/HK\$[\d,]+\.\d\d/)?.[0] || '未见 HK$')

const textUS = await pickMarket('美股')
await page.screenshot({ path: `${OUT}/02-overview-US.png`, fullPage: true })
record('overview', '切到美股 → 金额变 $', !!textUS && /\$[\d,]+\.\d\d/.test(textUS), (textUS || '').match(/\$[\d,]+\.\d\d/)?.[0] || '未见 $')

await pickMarket('A 股')
const textBack = await page.evaluate(() => document.body.innerText)
record('overview', '切回 A 股 → 金额变 ¥', textBack.includes('¥'), textBack.match(/¥[\d,]+\.\d\d/)?.[0] || '未见 ¥')

record('overview', '跨市场合计区（带汇率来源/时刻）',
  /跨市场合计/.test(textBack) && (/汇率来源/.test(textBack) || /取不到汇率/.test(textBack) || /合计算不出/.test(textBack)),
  textBack.includes('汇率来源') ? '合计已标注汇率来源与时刻' : (textBack.match(/跨市场合计[^。]*—[^。]*/) || ['未见到合计区'])[0])

// 自动交易页：时刻表按市场动态出
await page.goto(BASE + '/finance/auto-trade', { waitUntil: 'load' }); await settle(page)
const atCN = await page.evaluate(() => document.body.innerText)
const pickAT = page.locator('button[role="tab"]:has-text("港股")').first()
if (await pickAT.count()) {
  await pickAT.click(); await settle(page, 2000)
  const atHK = await page.evaluate(() => document.body.innerText)
  await page.screenshot({ path: `${OUT}/03-auto-trade-HK.png`, fullPage: true })
  record('auto-trade', '时刻表随市场切换',
    atCN.includes('15:30') && atHK.includes('16:15') && !atHK.includes('按沪深交易日历走'),
    `A股含 15:30=${atCN.includes('15:30')} · 港股含 16:15=${atHK.includes('16:15')} · 旧文案已去掉=${!atHK.includes('按沪深交易日历走')}`)
} else record('auto-trade', '时刻表随市场切换', false, '找不到港股切换按钮')

// 我的账户页：切到港股看规则与币种
await page.goto(BASE + '/finance/account', { waitUntil: 'load' }); await settle(page)
const pickAcc = page.locator('button[role="tab"]:has-text("港股")').first()
if (await pickAcc.count()) {
  await pickAcc.click(); await settle(page, 2000)
  const accHK = await page.evaluate(() => document.body.innerText)
  await page.screenshot({ path: `${OUT}/04-account-HK.png`, fullPage: true })
  record('account', '港股规则与币种',
    /港股规则|港股/.test(accHK) && /HKD/.test(accHK) && !/本版本不提供/.test(accHK),
    `含 HKD=${accHK.includes('HKD')} · 旧文案「本版本不提供」已去掉=${!accHK.includes('本版本不提供')}`)
}

// 报告页：市场列 + 事实行币种
await page.goto(BASE + '/finance/report', { waitUntil: 'load' }); await settle(page)
const expand = page.getByRole('button', { name: /^展开$/ }).first()
if (await expand.count()) { await expand.click(); await settle(page, 1000) }
const repText = await page.evaluate(() => document.body.innerText)
await page.screenshot({ path: `${OUT}/05-report.png`, fullPage: true })
record('report', '历史表出现「市场」列并列出三个市场',
  /市场/.test(repText) && /跨市场汇总/.test(repText) && /港股/.test(repText) && /美股/.test(repText),
  repText.replace(/\s+/g, ' ').match(/.{0,40}跨市场汇总.{0,40}/)?.[0] || '未见市场列')
record('report', '事实行金额带币种符号',
  /HK\$[\d,]+/.test(repText) || /¥[\d,]+/.test(repText),
  (repText.match(/HK\$[\d,]+\.\d\d/) || repText.match(/¥[\d,]+\.\d\d/) || ['未见'])[0])

// ── 3) 汇总 ─────────────────────────────────────────────────────────────
report.consoleErrors = consoleErrors
report.pageErrors = pageErrors
report.failedRequests = failedRequests.filter(r => !/favicon/.test(r.url))
report.summary = {
  login: !!token,
  consoleErrors: consoleErrors.length,
  pageErrors: pageErrors.length,
  failedRequests: report.failedRequests.length,
  overflowPages: report.pages.filter(p => p.overflowX).map(p => p.key),
  brokenImagesTotal: report.pages.reduce((n, p) => n + (p.brokenImages || 0), 0),
  interactionsFailed: report.interactions.filter(i => !i.ok).length,
}
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2))
console.log('SUMMARY ' + JSON.stringify(report.summary))
for (const i of report.interactions) console.log(`  [${i.ok ? 'OK ' : 'FAIL'}] ${i.page} · ${i.kind} — ${i.detail}`)
if (consoleErrors.length) console.log('CONSOLE ERRORS:', JSON.stringify(consoleErrors.slice(0, 10), null, 1))
if (pageErrors.length) console.log('PAGE ERRORS:', JSON.stringify(pageErrors.slice(0, 10), null, 1))
if (report.failedRequests.length) console.log('FAILED REQUESTS:', JSON.stringify(report.failedRequests.slice(0, 10), null, 1))
await browser.close()
