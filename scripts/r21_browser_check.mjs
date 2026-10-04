// R21 · 演示站真浏览器验收（Playwright / chromium）—— 经验库开关上界面（按项目）。
//
// 用法：
//   R21_BASE=https://hunter-community.agentpit.io \
//   R9_PLAYWRIGHT=<playwright 模块路径（absolute，ESM 不认 NODE_PATH）> \
//   node scripts/r21_browser_check.mjs
//
// 验收对象（`10` §四 出口 / `R21.md` §3.2）：
//   A · 成长页 ④ —— 经验库开关**可点**、切完**页面上的「当前生效」立刻变**（不许刷新才变）、
//       硬开关两行仍是只读；每一条都对着后端 `GET /v1/fin/runtime` 的真值。
//   B · 向导第 7 步 —— 那块「这台机器要不要学习」在**开户之前**就能设置，且写着
//       「跟项目没有关系、随时可改」与「这不是自动交易开关」。
//
// 自包含：HTTP 现注册两个一次性账号（重复跑不冲突），A 有项目（验成长页）、B 无项目（导向导）。
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.R9_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || (_pw.default && _pw.default.chromium)
if (!chromium) throw new Error(`拿不到 playwright 的 chromium（来自 ${PW}）`)

const BASE = process.env.R21_BASE || 'https://hunter-community.agentpit.io'
const API = process.env.R21_API || BASE
const OUT = process.env.R21_OUT || 'docs/screenshots/r21'
const TS = process.env.R21_TS || String(Math.floor(Date.now() / 1000))

const results = []
const check = (n, ok, detail = '') => {
  results.push({ n, ok: !!ok, detail })
  console.log(`  [${ok ? 'OK  ' : 'FAIL'}] ${n}${detail ? ' — ' + detail : ''}`)
}
mkdirSync(OUT, { recursive: true })

async function api(path, { method = 'GET', body, token } = {}) {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}),
               ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const t = await r.text(); let d = null; try { d = JSON.parse(t) } catch {}
  return { status: r.status, data: d, raw: t }
}
async function register(email) {
  const r = await api('/api/auth/register', { method: 'POST', body: { email, password: 'r21ui-pass-123', display_name: email.split('@')[0] } })
  if (!r.data?.access_token) throw new Error(`注册失败 HTTP ${r.status}: ${r.raw.slice(0, 120)}`)
  return r.data.access_token
}

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 }, locale: 'zh-CN' })
const page = await ctx.newPage()
const pageErrors = [], consoleErrors = []
page.on('pageerror', e => pageErrors.push(String(e)))
page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()) })

/** 关掉合规弹层（R9 同款）。 */
async function dismissCompliance() {
  const ok = page.getByRole('button', { name: /确认继续/ }).first()
  if (await ok.count()) {
    const cb = page.locator('input[type="checkbox"]').first()
    if (await cb.count()) await cb.click().catch(() => {})
    await ok.click().catch(() => {})
    await page.waitForTimeout(1200)
  }
}
const norm = s => (s || '').replace(/\s+/g, ' ').trim()
const banner = () => page.locator('[data-r9="runtime-banner"]')

// ════════════════════════════════════════════════════════════════════════
// A · 成长页 ④（用户 A · 有 HK 项目）
// ════════════════════════════════════════════════════════════════════════
console.log('\n== A · 成长页 ④ ==')
const tokA = await register(`r21ui-a-${TS}@example.com`)
await api('/api/auth/compliance-ack', { method: 'POST', token: tokA })
const pr = await api('/api/v1/fin/projects', { method: 'POST', token: tokA, body: { tier: 'manage', markets: ['HK'] } })
const pid = pr.data?.project?.project_id
check('A0 项目已建（HK）', !!pid, String(pid))

const rtBackend = (await api(`/api/v1/fin/runtime?project_id=${pid}`, { token: tokA })).data
check('A0b 后端 runtime 三个新字段齐全',
  !!rtBackend?.ceiling && !!rtBackend?.selected && !!rtBackend?.can_change && !!rtBackend?.meta,
  JSON.stringify({ ceiling: rtBackend?.ceiling, selected: rtBackend?.selected, can_change: rtBackend?.can_change }))

await page.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, tokA)
await page.goto(`${BASE}/finance/growth`, { waitUntil: 'networkidle', timeout: 90000 })
await page.waitForTimeout(3000)
await dismissCompliance()
await page.goto(`${BASE}/finance/growth`, { waitUntil: 'networkidle', timeout: 90000 })
await page.waitForTimeout(3500)

const bodyText = norm(await page.locator('body').innerText())
check('A1 页面加载（非空白）', bodyText.length > 100, `${bodyText.length} 字`)
check('A2 成长页根容器存在', (await page.locator('[data-r9="growth-page"]').count()) > 0)
check('A3 运行模式横幅存在', (await banner().count()) > 0)

let bt = norm(await banner().innerText())
await page.screenshot({ path: `${OUT}/A-growth-4-switch.png`, fullPage: true })
check('A4 横幅含「生效模式」', bt.includes('生效模式'), bt.match(/生效模式[^请]*/)?.[0] || '')
check('A5 生效模式是「观察」（天花板 observe）', /观察/.test(bt))
check('A6 只读行「关（本方案恒为关闭）」', bt.includes('关（本方案恒为关闭）'))
check('A7 只读行「无（本项目不接券商）」', bt.includes('无（本项目不接券商）'))

// 经验库开关：可点的 Toggle（button[aria-pressed]）
const tog = banner().locator('button[aria-pressed]')
const toggleCount = await tog.count()
check('A8 经验库开关可点（aria-pressed 按钮存在）', toggleCount === 1, `count=${toggleCount}`)
const pressed0 = toggleCount ? await tog.first().getAttribute('aria-pressed') : null
check('A9 初始为「开」（天花板=1、未单独设置 → 生效=开）', pressed0 === 'true', `aria-pressed=${pressed0}`)
check('A9b 当前生效写「经验库：已启用」', bt.includes('经验库：已启用'))

// 天花板 observe → 「模拟验证」画灰、点不动
const paperBtn = banner().getByRole('button', { name: '模拟验证' })
check('A10 天花板 observe →「模拟验证」不可选',
  (await paperBtn.count()) === 1 && !(await paperBtn.first().isEnabled().catch(() => false)))

// 硬开关：没有 switch / 没有「自动生效」勾选框
const swCount = await page.getByRole('switch').count()
const autoCb = await page.getByRole('checkbox', { name: /自动生效/ }).count()
check('A11 界面没有「自动生效」开关（也无 role=switch）', swCount === 0 && autoCb === 0,
  `switch=${swCount} autoBox=${autoCb}`)

// ── 切换：开 → 关，立刻生效 ──
await tog.first().click()
await page.waitForTimeout(500)
const confirmBar = banner().locator('input[placeholder*="为什么改"]')
check('A12 点开关 → 出现确认条（理由必填）', (await confirmBar.count()) === 1)
const confirmBtn = banner().getByRole('button', { name: '确认修改' })
check('A13 未填理由时「确认修改」禁用', !(await confirmBtn.first().isEnabled().catch(() => false)))
await confirmBar.fill('R21 真浏览器验收：先关掉')
await page.waitForTimeout(200)
await confirmBtn.first().click()
await page.waitForTimeout(2500)   // 只等接口往返，**不刷新页面**
const after = norm(await banner().innerText())
await page.screenshot({ path: `${OUT}/A-growth-4-after-off.png`, fullPage: true })
check('A14 切完立刻变（不刷新）：「已改，立刻生效」', after.includes('已改，立刻生效'), after.match(/已改[^。]*/)?.[0] || '')
check('A15 当前生效变成「经验库：未启用」', after.includes('经验库：未启用'))
const pressed1 = await tog.first().getAttribute('aria-pressed')
check('A16 aria-pressed 变 false', pressed1 === 'false', `aria-pressed=${pressed1}`)

// 后端也真的变了（同一枪里核对，防前端自嗨）
const rtAfterOff = (await api(`/api/v1/fin/runtime?project_id=${pid}`, { token: tokA })).data
check('A16b 后端 memory_enabled 同步为 false', rtAfterOff?.memory_enabled === false,
  JSON.stringify({ memory_enabled: rtAfterOff?.memory_enabled, selected: rtAfterOff?.selected }))

// ── 切回开（复位，验收完不留关闭态）──
await tog.first().click()
await page.waitForTimeout(500)
await banner().locator('input[placeholder*="为什么改"]').fill('R21 真浏览器验收：再开回来')
await banner().getByRole('button', { name: '确认修改' }).first().click()
await page.waitForTimeout(2500)
const after2 = norm(await banner().innerText())
check('A17 再开回来立刻生效', after2.includes('已改，立刻生效') && after2.includes('经验库：已启用'))

check('A18 成长页 pageErrors = 0', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '))

// ════════════════════════════════════════════════════════════════════════
// B · 向导第 7 步（用户 B · 无项目）
// ════════════════════════════════════════════════════════════════════════
console.log('\n== B · 向导第 7 步 ==')
const errB0 = pageErrors.length
const tokB = await register(`r21ui-b-${TS}@example.com`)
await api('/api/auth/compliance-ack', { method: 'POST', token: tokB })
const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1400 }, locale: 'zh-CN' })
const p2 = await ctx2.newPage()
const peB = []
p2.on('pageerror', e => peB.push(String(e)))
await p2.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, tokB)
await p2.goto(`${BASE}/finance/setup`, { waitUntil: 'networkidle', timeout: 90000 })
await p2.waitForTimeout(2500)
// 合规弹层
{
  const ok = p2.getByRole('button', { name: /确认继续/ }).first()
  if (await ok.count()) { const cb = p2.locator('input[type="checkbox"]').first(); if (await cb.count()) await cb.click().catch(()=>{}); await ok.click().catch(()=>{}); await p2.waitForTimeout(1200) }
}
await p2.goto(`${BASE}/finance/setup`, { waitUntil: 'networkidle', timeout: 90000 })
await p2.waitForTimeout(2500)

async function next(label) {
  const b = p2.getByRole('button', { name: '下一步' }).first()
  const ok = await b.isEnabled().catch(() => false)
  if (ok) { await b.click().catch(() => {}); await p2.waitForTimeout(1200) }
  return ok
}
// 1 风险告知
await p2.locator('input[type="checkbox"]').first().click().catch(() => {})
await next('step1')
// 2 选档位
await p2.getByRole('button', { name: /个人资产管理/ }).first().click().catch(() => {})
await next('step2')
// 3 选市场（港股）
await p2.getByRole('button', { name: /港股/ }).first().click().catch(() => {})
await next('step3')
// 4 板块偏好
await next('step4')
// 5 策略选择
await next('step5')
// 6 短线参数
await next('step6')

const step7 = norm(await p2.locator('body').innerText())
await p2.screenshot({ path: `${OUT}/B-setup-step7.png`, fullPage: true })
check('B1 到达第 7 步（确认并开启）', step7.includes('第 7 步') && step7.includes('确认并开启'))
check('B2 有「这台机器要不要学习」块', step7.includes('这台机器要不要学习'))
check('B3「与档位/项目无关、随时可改」（开户之前就能设置）',
  step7.includes('没有关系') && step7.includes('随时改'),
  norm(step7.match(/这几项管的是[^。]*。[^。]*。/)?.[0] || '').slice(0, 90))
check('B4「这不是自动交易开关 —— 那个在「自动交易」页」',
  step7.includes('这不是') && step7.includes('自动交易') && step7.includes('那个在'))
const memTogB = p2.locator('[data-r9] button[aria-pressed], button[aria-pressed]')
check('B5 经验库开关在开户之前可点', (await memTogB.count()) >= 1, `count=${await memTogB.count()}`)
check('B6 向导页 pageErrors = 0', peB.length === 0, peB.slice(0, 3).join(' | '))
check('B7 全程 pageErrors = 0', pageErrors.length === errB0, `${errB0} → ${pageErrors.length}`)

const failed = results.filter(r => !r.ok).length
writeFileSync(`${OUT}/report.json`, JSON.stringify({ base: BASE, results, pageErrors, consoleErrors }, null, 2))
console.log(`\nR21 browser: ${results.length - failed}/${results.length} OK · pageErrors ${pageErrors.length + peB.length} · consoleErrors ${consoleErrors.length}`)
await browser.close()
process.exit(failed ? 1 : 0)
