// R13 · 演示站成长页真浏览器验收（Playwright / chromium）。
//
// 用法：
//   R13_BASE=https://hunter-community.agentpit.io \
//   R13_TOKEN=<access_token>  R9_PLAYWRIGHT=<playwright 模块路径> \
//   node scripts/r13_browser_check.mjs
//
// 验收对象 = 本轮新开的「只选港股」验收项目：成长页六块 + ⑥ 提案与验证块端到端
//   （事件链里应能看到 R13 的 已应用到模拟盘 → 观察期告警 → 已回滚 这条链）。
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.R9_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || (_pw.default && _pw.default.chromium)

const BASE = process.env.R13_BASE || 'http://127.0.0.1:3110'
const TOKEN = process.env.R13_TOKEN || ''
const OUT = process.env.R13_OUT || 'docs/screenshots/r13'

const results = []
const check = (n, ok, detail = '') => {
  results.push({ n, ok: !!ok, detail })
  console.log(`  [${ok ? 'OK  ' : 'FAIL'}] ${n}${detail ? ' — ' + detail : ''}`)
}

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 } })
const page = await ctx.newPage()
const pageErrors = []
page.on('pageerror', e => pageErrors.push(String(e)))
page.on('console', m => { if (m.type() === 'error') pageErrors.push('console: ' + m.text()) })

await page.addInitScript(t => { localStorage.setItem('hunter_token', t) }, TOKEN)
await page.goto(`${BASE}/finance/growth`, { waitUntil: 'networkidle', timeout: 90000 })
await page.waitForTimeout(3500)

const rootCount = await page.locator('[data-r9="growth-page"]').count()
const body = rootCount ? await page.locator('[data-r9="growth-page"]').first().innerText()
                         : await page.locator('body').innerText()
check('页面加载（非空白）', body.trim().length > 100, `${body.trim().length} 字`)
check('成长页根容器存在', rootCount > 0)

// 六块骨架
check('④ 经验库', /经验库/.test(body) && /证据/.test(body))
check('⑤ 四条底线', /底线|红线/.test(body))
check('⑥ 提案与验证', /提案与验证/.test(body))
check('⑥ 提案列表块存在', (await page.locator('[data-r9="proposal-list"]').count()) > 0)
check('⑥ 冻结计划块存在', (await page.locator('[data-r9="frozen-plan"]').count()) > 0)
check('⑥ 两臂对照块存在', (await page.locator('[data-r9="arm-compare"]').count()) > 0)
check('⑥ 事件链块存在', (await page.locator('[data-r9="events"]').count()) > 0)
check('运行模式横幅存在', (await page.locator('[data-r9="runtime-banner"]').count()) > 0)

// 冻结计划 hash（16+ 位十六进制）
check('冻结计划 hash 出现', /[0-9a-f]{16,}/.test(body))

// R13 端到端：事件链里应有 生效 → 告警 → 回滚 三态（EVENT_TEXT 的中文）
check('事件链含「已应用到模拟盘」', /已应用到模拟盘/.test(body))
check('事件链含「观察期告警」', /观察期告警/.test(body))
check('事件链含「已回滚到上一个已验证版本」', /已回滚到上一个已验证版本/.test(body))
check('冻结计划含观察期回滚线', /观察期回滚线/.test(body))

// 与 R9/R10 同口径：没有「自动生效」开关
const autoBox = await page.getByRole('checkbox', { name: /自动生效/ }).count()
const switches = await page.getByRole('switch').count()
check('界面没有「自动生效」开关', autoBox === 0 && switches === 0,
      `autoBox=${autoBox} switch=${switches}`)

check('无页面报错', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '))

const shot = `${OUT}/growth-hk.png`
await page.screenshot({ path: shot, fullPage: true })
console.log('  shot:', shot)
writeFileSync(`${OUT}/report.json`, JSON.stringify({ results, pageErrors, url: `${BASE}/finance/growth` }, null, 2))
const failed = results.filter(r => !r.ok).length
console.log(`\nR13 browser: ${results.length - failed}/${results.length} OK · pageErrors ${pageErrors.length}`)
await browser.close()
process.exit(failed ? 1 : 0)
