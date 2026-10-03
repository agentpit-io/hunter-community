/**
 * P3 · 「市场选择」前端真浏览器实测（Playwright · headless Chromium）。
 *
 * 「API 通 ≠ 组件通」—— P3 改了 `apps/web/app/finance/` 七个文件（含六→七步的设置向导），
 * 必须真浏览器跑一遍。本脚本做七件事，结果与截图落在 `P3_OUT`（默认 `/tmp/p3-shots`）：
 *
 * 1. **新注册一个账户**（`POST /api/auth/register`，HTTP；邮箱带时间戳，重复跑不冲突）；
 * 2. **走一遍七步向导**：风险告知 → 选档位 → **选市场（只勾港股）** → 板块偏好 → 策略选择
 *    → 短线参数 → 确认开启。中途断言「未选市场时下一步禁用」，并给「选市场」那一步截图；
 * 3. **断言后端真的按选中的市场落库**（`GET /projects/current` → `market_scope === 'HK'`、
 *    `markets === ['HK']`）—— 界面与后端对得上，不是画上去的；
 * 4. **逐页打开** 总览 / 自动交易 / 我的账户，**断言市场切换器只有 1 个页签且是「港股」**
 *    （A 股 / 美股 不该出现），并截图；
 * 5. **追加市场**：「我的账户」页点「追加市场」→ 勾美股 → 确认，断言 `markets === ['HK','US']`；
 * 6. **再打开三个页面**，断言此时切换器有 **2 个页签**（港股 + 美股）；
 * 7. 全程记录 `console error` / `pageerror` / 失败请求，**如实**落进 JSON（不瞒）。
 *
 * 用法（本机）：
 *     P3_BASE=http://127.0.0.1:3110 node scripts/p3_browser_check.mjs
 * playwright 的解析路径可用 `P3_PLAYWRIGHT` 给绝对路径（ESM 不认 NODE_PATH）。
 */
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.P3_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || _pw.default?.chromium
if (!chromium) throw new Error(`拿不到 playwright 的 chromium（来自 ${PW}）`)

const BASE = process.env.P3_BASE || 'http://127.0.0.1:3110'
const API = process.env.P3_API || 'http://127.0.0.1:8110'
const OUT = process.env.P3_OUT || '/tmp/p3-shots'
const STAMP = process.env.P3_STAMP || String(Date.now())
const EMAIL = process.env.P3_EMAIL || `p3-demo-${STAMP}@example.com`
const PASSWORD = process.env.P3_PASSWORD || 'p3demo123456'

const report = { base: BASE, email: EMAIL, at: new Date().toISOString(), steps: [], pages: [], checks: [], notes: [] }
const consoleErrors = []
const pageErrors = []
const failedRequests = []

function rec(kind, ok, detail) { report.checks.push({ kind, ok, detail }) }
async function settle(page, ms = 1600) { await page.waitForLoadState('load').catch(() => {}); await page.waitForTimeout(ms) }

/** 整页文本（**不截断**）—— 内容断言用它；`health().bodyText` 只截 400 字，够看不够判。 */
async function fullText(page) {
  return await page.evaluate(() => (document.body.innerText || '').replace(/\s+/g, ' '))
}

async function health(page) {
  return await page.evaluate(() => {
    const doc = document.documentElement
    const imgs = Array.from(document.images).map(i => ({ src: i.currentSrc || i.src, w: i.naturalWidth }))
    return {
      url: location.pathname, title: document.title,
      overflowX: doc.scrollWidth > window.innerWidth + 1,
      brokenImages: imgs.filter(i => i.w < 1).length, imgCount: imgs.length,
      bodyText: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 400),
    }
  })
}

async function api(path, token, init = {}) {
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) },
  })
  const text = await r.text()
  let body = null
  try { body = JSON.parse(text) } catch { body = { raw: text.slice(0, 300) } }
  return { status: r.status, body }
}

// ── ① 新注册（HTTP，不经浏览器）─────────────────────────────────────────
const reg = await api('/api/auth/register', null, {
  method: 'POST',
  body: JSON.stringify({ email: EMAIL, password: PASSWORD, display_name: 'P3 演示' }),
})
if (reg.status !== 200 || !reg.body?.access_token) throw new Error(`注册失败：HTTP ${reg.status} ${JSON.stringify(reg.body)}`)
const token = reg.body.access_token
report.register = { email: EMAIL, status: reg.status, ok: true }
await api('/api/auth/compliance-ack', token, { method: 'POST', body: '{}' }).catch(() => {})

const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, locale: 'zh-CN' })
await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
const page = await context.newPage()
page.on('console', m => { if (m.type() === 'error') consoleErrors.push({ url: page.url(), text: m.text() }) })
page.on('pageerror', e => pageErrors.push({ url: page.url(), text: String(e) }))
page.on('requestfailed', r => failedRequests.push({ url: r.url(), err: r.failure()?.errorText }))

mkdirSync(OUT, { recursive: true })

async function dismissCompliance() {
  const ack = page.getByRole('button', { name: /确认继续/ }).first()
  if (await ack.count() && await ack.isVisible().catch(() => false)) {
    const cb = page.locator('input[type="checkbox"]').first()
    if (await cb.count()) await cb.click().catch(() => {})
    await ack.click().catch(() => {})
    await settle(page, 1000)
  }
}

const nextBtn = () => page.getByRole('button', { name: '下一步' }).first()
async function clickNext(label) {
  const b = nextBtn()
  const enabled = await b.isEnabled().catch(() => false)
  await b.click({ timeout: 15000 }).catch(() => {})
  await settle(page, 700)
  rec('向导', enabled, `${label} · 下一步${enabled ? '可点' : '禁用'}（点了）`)
  return enabled
}

// ── ② 七步向导（只勾港股）───────────────────────────────────────────────
await page.goto(BASE + '/finance/setup', { waitUntil: 'load' }); await settle(page, 2200)
await dismissCompliance()
report.steps.push({ step: 1, name: '风险告知', text: (await health(page)).bodyText.slice(0, 120) })

// 第 1 步：勾「我已阅读」→ 下一步
await page.locator('input[type="checkbox"]').first().click().catch(() => {})
await settle(page, 400)
await clickNext('第1步 风险告知')

// 第 2 步：选档位（个人资产管理 = manage）
await page.getByRole('button', { name: /个人资产管理/ }).first().click().catch(() => {})
await settle(page, 400)
await clickNext('第2步 选档位')

// 第 3 步：选市场 —— 先断言「未选时下一步禁用」，再只勾港股
const beforePick = await nextBtn().isEnabled().catch(() => true)
rec('选市场', beforePick === false, `未选任何市场时「下一步」禁用 = ${beforePick === false}`)
await page.screenshot({ path: `${OUT}/10-setup-step3-empty.png`, fullPage: true })

const hkCard = page.locator('button[aria-pressed]').filter({ hasText: '港股' }).first()
rec('选市场', await hkCard.count() === 1, `「港股」市场卡片数 = ${await hkCard.count()}`)
await hkCard.click().catch(() => {})
await settle(page, 500)
const afterPick = await nextBtn().isEnabled().catch(() => false)
rec('选市场', afterPick === true, `勾了港股之后「下一步」可点 = ${afterPick === true}`)
const step3Text = await fullText(page)
rec('选市场', /不做价格带校验/.test(step3Text), `选中港/美股时当场提示「不做价格带校验」= ${/不做价格带校验/.test(step3Text)}`)
await page.screenshot({ path: `${OUT}/11-setup-step3-HK.png`, fullPage: true })
await clickNext('第3步 选市场')

// 第 4~6 步：板块偏好 / 策略选择 / 短线参数
await page.screenshot({ path: `${OUT}/12-setup-step4-boards.png`, fullPage: true })
rec('第4步', /港股主板/.test(await fullText(page)), '板块清单按市场出（出现「港股主板」）')
await clickNext('第4步 板块偏好')
await clickNext('第5步 策略选择')
const step6Text = await fullText(page)
// 「买入 ST」是 A 股概念：没选 A 股时**硬禁清单里不该有它**。
// （文案里会多一句「这条不适用」的解释 —— 那句里当然会出现「买入 ST」四个字，
//  所以断言的是**清单里的那个短语**，不是这四个字。）
const noStBan = !/买入 ST 与退市整理期股票/.test(step6Text)
rec('第6步', /交易时点/.test(step6Text) && noStBan,
  `时点按市场出、且未选 A 股时硬禁清单不含「买入 ST 与退市整理期股票」= ${/交易时点/.test(step6Text) && noStBan}`)
rec('第6步', /不适用/.test(step6Text), '未选 A 股时明说「买入 ST」这条不适用（不是悄悄删掉）')
await page.screenshot({ path: `${OUT}/13-setup-step6-params.png`, fullPage: true })
await clickNext('第6步 短线参数')

// 第 7 步：确认 —— 断言「已选市场 / 各市场本金」两行在，且不再写「默认开设 A 股」
const step7Text = await fullText(page)
rec('第7步', /已选市场/.test(step7Text) && /各市场本金/.test(step7Text), '确认页列出已选市场与各市场本金')
rec('第7步', !/默认开设 A 股/.test(step7Text), '确认页不再出现「默认开设 A 股」')
await page.screenshot({ path: `${OUT}/14-setup-step7-confirm.png`, fullPage: true })

const openBtn = page.getByRole('button', { name: /立即开启/ }).first()
rec('第7步', await openBtn.isEnabled().catch(() => false), '「立即开启」可点')
await openBtn.click().catch(() => {})
await settle(page, 2500)
const doneText = await fullText(page)
rec('开户', /项目已开启|已创建/.test(doneText), `开户成功页出现 = ${/项目已开启|已创建/.test(doneText)}`)
await page.screenshot({ path: `${OUT}/15-setup-done.png`, fullPage: true })

// ── ③ 后端真落库（界面与后端对得上）────────────────────────────────────
const cur = await api('/api/v1/fin/projects/current', token)
const proj = cur.body?.project || {}
const mkts = (cur.body?.markets || []).map(m => m.market)
report.project = { project_id: proj.project_id, market_scope: proj.market_scope, currency: proj.currency, markets: mkts, tier: proj.tier }
rec('后端', proj.market_scope === 'HK', `market_scope = ${proj.market_scope}（期望 HK）`)
rec('后端', mkts.length === 1 && mkts[0] === 'HK', `markets = ${JSON.stringify(mkts)}（期望 ["HK"]）`)

// ── ④ 三个页面：切换器只有港股 ─────────────────────────────────────────
async function tabsOf(path) {
  await page.goto(BASE + path, { waitUntil: 'load' }); await settle(page, 2000)
  await dismissCompliance()
  return await page.locator('button[role="tab"]').allInnerTexts()
}
for (const [key, path, label] of [
  ['overview', '/finance/overview', '总览'],
  ['auto-trade', '/finance/auto-trade', '自动交易'],
  ['account', '/finance/account', '我的账户'],
]) {
  const tabs = await tabsOf(path)
  const h = await health(page)
  report.pages.push({ key, label, path, tabs, overflowX: h.overflowX, brokenImages: h.brokenImages, sample: h.bodyText.slice(0, 200) })
  await page.screenshot({ path: `${OUT}/2${report.pages.length}-${key}-only-HK.png`, fullPage: true })
  rec('切换器', tabs.length === 1 && tabs[0].includes('港股'), `${label} 切换器 = ${JSON.stringify(tabs)}（期望只有港股）`)
  rec('切换器', !tabs.some(t => t.includes('A 股') || t.includes('美股')), `${label} 没有未选市场的页签`)
}

// ── ⑤ 追加市场（在我的账户页）───────────────────────────────────────────
const addBtn = page.getByRole('button', { name: /追加市场/ }).first()
rec('追加市场', await addBtn.count() === 1, '「追加市场」入口常驻可见（不是悬停才出现）')
await addBtn.click().catch(() => {})
await settle(page, 900)
const modalText = await page.evaluate(() => document.body.innerText)
rec('追加市场', /不回填历史/.test(modalText), '弹层写明「追加后从此刻开始记账，不回填历史」')
rec('追加市场', !/移除市场/.test(modalText.replace(/没有「移除市场」/g, '')), '弹层没有任何「移除市场」控件')
await page.screenshot({ path: `${OUT}/30-add-market-modal.png`, fullPage: true })
await page.getByRole('button', { name: /美股/ }).first().click().catch(() => {})
await settle(page, 400)
const confirmBtn = page.getByRole('button', { name: /确认追加/ }).first()
await confirmBtn.click().catch(() => {})
await settle(page, 3000)
await dismissCompliance()

const cur2 = await api('/api/v1/fin/projects/current', token)
const mkts2 = (cur2.body?.markets || []).map(m => m.market)
report.afterAdd = { market_scope: cur2.body?.project?.market_scope, markets: mkts2 }
rec('追加市场', mkts2.length === 2 && mkts2.includes('HK') && mkts2.includes('US'), `追加后 markets = ${JSON.stringify(mkts2)}（期望含 HK 与 US）`)

// ── ⑥ 再打开三页：此时两个页签 ─────────────────────────────────────────
for (const [key, path, label] of [
  ['overview', '/finance/overview', '总览'],
  ['auto-trade', '/finance/auto-trade', '自动交易'],
  ['account', '/finance/account', '我的账户'],
]) {
  const tabs = await tabsOf(path)
  report.pages.push({ key: key + '-after-add', label: label + '（追加后）', path, tabs })
  await page.screenshot({ path: `${OUT}/4${report.pages.length}-${key}-HK-US.png`, fullPage: true })
  rec('切换器', tabs.length === 2 && tabs.some(t => t.includes('港股')) && tabs.some(t => t.includes('美股')),
    `${label}（追加后）切换器 = ${JSON.stringify(tabs)}（期望 港股 + 美股）`)
  rec('切换器', !tabs.some(t => t.includes('A 股')), `${label}（追加后）仍没有 A 股页签`)
}

// ── ⑦ 服务端「不能移除」的守门（双保险的硬防线）──────────────────────
const rm = await api(`/api/v1/fin/projects/${proj.project_id}/markets`, token, {
  method: 'POST', body: JSON.stringify({ markets: ['US'] }),
})
rec('只增不减', rm.status === 400, `传子集 {US} 被服务端拒 = HTTP ${rm.status} ${JSON.stringify(rm.body?.detail || rm.body)}`)

await browser.close()

report.consoleErrors = consoleErrors
report.pageErrors = pageErrors
report.failedRequests = failedRequests
report.summary = {
  checks: report.checks.length,
  passed: report.checks.filter(c => c.ok).length,
  failed: report.checks.filter(c => !c.ok).length,
  consoleErrors: consoleErrors.length,
  pageErrors: pageErrors.length,
  failedRequests: failedRequests.length,
}
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2))
console.log(JSON.stringify(report.summary, null, 2))
console.log('--- 失败项 ---')
for (const c of report.checks.filter(x => !x.ok)) console.log('✗', c.kind, '·', c.detail)
console.log(`报告与截图：${OUT}`)
