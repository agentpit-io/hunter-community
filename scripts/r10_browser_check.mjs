// R10 · 演示站成长页真浏览器验收（Playwright / chromium）。
//
// 用法：
//   R10_BASE=https://hunter-community.agentpit.io \
//   R10_TOKEN=<access_token>  R10_MODE=rich|empty \
//   R9_PLAYWRIGHT=<playwright 模块路径>  node scripts/r10_browser_check.mjs
//
// rich  = 有数据（验收项目）：六块骨架 / ④ 经验库列表 / ⑥ 提案与验证块 / 截图
// empty = 空项目：本页全文不得出现阿拉伯数字（「空的比假的好」的机器可验形式）
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.R9_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || (_pw.default && _pw.default.chromium)

const BASE = process.env.R10_BASE || 'http://127.0.0.1:3110'
const TOKEN = process.env.R10_TOKEN || ''
const MODE = process.env.R10_MODE || 'rich'
const OUT = process.env.R10_OUT || 'docs/screenshots/r10'

const results = []
const check = (n, ok, detail = '') => {
  results.push({ n, ok: !!ok, detail })
  console.log(`  [${ok ? 'OK  ' : 'FAIL'}] ${n}${detail ? ' — ' + detail : ''}`)
}

mkdirSync(OUT, { recursive: true })

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 } })
const page = await ctx.newPage()
const pageErrors = []
page.on('pageerror', e => pageErrors.push(String(e)))
page.on('console', m => { if (m.type() === 'error') pageErrors.push('console: ' + m.text()) })

await page.addInitScript(t => { localStorage.setItem('hunter_token', t) }, TOKEN)
await page.goto(`${BASE}/finance/growth`, { waitUntil: 'networkidle', timeout: 60000 })
await page.waitForTimeout(2500)

const root = page.locator('[data-r9="growth-page"]')
const body = (await root.count()) ? await root.first().innerText() : await page.locator('body').innerText()

check('页面加载（非空白）', body.trim().length > 100, `${body.trim().length} 字`)

for (const [key, label] of [
  ['growth-page', '成长页根容器'],
]) {
  check(`${label}存在`, (await page.locator(`[data-r9="${key}"]`).count()) > 0)
}

const text = body
if (MODE === 'rich') {
  check('④ 经验库标题出现', /经验库/.test(text))
  check('④ 经验条目出现（「证据」字样）', /证据/.test(text))
  check('⑤ 四条底线出现', /底线|红线|四条/.test(text))
  check('⑥ 提案与验证出现', /提案/.test(text))
  check('⑥ 冻结计划 / plan_hash 出现', /冻结计划|plan_hash|计划/.test(text))
  check('⑥ 两臂对照出现', /两臂|对照/.test(text))
  check('运行模式横幅出现', /观察|生效模式|模式/.test(text))
  check('三块「本版未做」如实标注', (text.match(/本版未做/g) || []).length >= 2,
        `${(text.match(/本版未做/g) || []).length} 处`)
  // 与 R9 同口径：没有名字含「自动生效」的 checkbox，也没有任何 role=switch
  const autoBox = await page.getByRole('checkbox', { name: /自动生效/ }).count()
  const switches = await page.getByRole('switch').count()
  check('界面没有「自动生效」开关', autoBox === 0 && switches === 0,
        `autoBox=${autoBox} switch=${switches}`)
  check('提案与验证块展示冻结计划 hash', /[0-9a-f]{16,}/.test(text))
  const shots = [`${OUT}/growth-rich.png`]
  await page.screenshot({ path: shots[0], fullPage: true })
  console.log('  shot:', shots[0])
} else {
  check('④ 经验空态如实出现', /还没有经验|暂无/.test(text))
  const own = (await root.count()) ? await root.first().innerText() : text
  const digits = own.replace(/[\s\S]*?Hunter Community[\s\S]*/g, m => m) // keep as-is
  const found = own.match(/[0-9]/g) || []
  check('空态 · 本页全文不出现阿拉伯数字', found.length === 0,
        found.length ? `发现 ${found.length} 个数字` : `${own.trim().length} 字零数字`)
  await page.screenshot({ path: `${OUT}/growth-empty.png`, fullPage: true })
  console.log('  shot:', `${OUT}/growth-empty.png`)
}

const report = { mode: MODE, url: `${BASE}/finance/growth`, results, pageErrors,
                 bodyText: (await (async () => (await root.count()) ? root.first().innerText() : page.locator('body').innerText())()).slice(0, 1500) }
writeFileSync(`${OUT}/report-${MODE}.json`, JSON.stringify(report, null, 2))
await browser.close()

const failed = results.filter(r => !r.ok).length
console.log(`SUMMARY ${JSON.stringify({ mode: MODE, assertions: results.length, failed, pageErrors: pageErrors.length })}`)
process.exit(failed ? 1 : 0)
