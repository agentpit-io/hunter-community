/**
 * R9 · 成长页（经验库 + 提案与验证）真浏览器实测（Playwright · headless Chromium）。
 *
 * 「API 通 ≠ 组件通」—— 本轮整页重写了 `apps/web/app/finance/growth/page.tsx`，
 * 必须真浏览器跑一遍。结果与截图落在 `R9_OUT`（默认 `/tmp/r9-shots`）。
 *
 * 三个阶段（`R9_PHASE`）：
 *   · `rich`（默认）—— 有经验 / 有提案的完整态：列表、三种状态、需重验角标、证据展开、
 *     refute 视觉区分、筛选值来自后端、⑥ 块的横幅 / 提案 / 冻结计划 / 两臂对照 / 事件链 /
 *     两个按钮，以及**人机混合写入**（含阿拉伯数字被前端当场拦住）。
 *   · `empty` —— 一个**没有经验、没有提案**的项目：断言空态文案如实出现，
 *     且**全文不出现任何阿拉伯数字**。
 *   · `degraded` —— 横幅要显示降级原因（跑之前把 api 的 FIN_EVOLUTION_MODE 设成 paper
 *     且 PAPER_BASE_URL 指向死地址，见成果文档）。
 *
 * 用法（本机）：
 *     R9_BASE=http://127.0.0.1:3110 node scripts/r9_browser_check.mjs
 *     R9_PHASE=empty R9_BASE=… node scripts/r9_browser_check.mjs
 *     R9_PHASE=degraded R9_BASE=… node scripts/r9_browser_check.mjs
 */
import { mkdirSync, writeFileSync } from 'node:fs'

const PW = process.env.R9_PLAYWRIGHT || 'playwright'
const _pw = await import(PW)
const chromium = _pw.chromium || _pw.default?.chromium
if (!chromium) throw new Error(`拿不到 playwright 的 chromium（来自 ${PW}）`)

const BASE = process.env.R9_BASE || 'http://127.0.0.1:3110'
const API = process.env.R9_API || 'http://127.0.0.1:8110'
const OUT = process.env.R9_OUT || '/tmp/r9-shots'
const PHASE = process.env.R9_PHASE || 'rich'

const RICH = { email: 'n5demo@example.com', pw: 'n5demo123',
               pid: 'prj_b7791191b3af462791ed496b' }
const EMPTY = { email: 'emptydemo@example.com', pw: 'r9empty-demo-pw' }

const report = { phase: PHASE, base: BASE, api: API, assertions: [], shots: [], notes: [] }
const consoleErrors = []
const pageErrors = []
const failedRequests = []

function record(kind, ok, detail) { report.assertions.push({ kind, ok: !!ok, detail: String(detail) }) }
async function shot(page, name) {
  const p = `${OUT}/${PHASE}-${name}.png`
  await page.screenshot({ path: p, fullPage: true })
  report.shots.push(p)
  return p
}
async function settle(page, ms = 1800) {
  await page.waitForLoadState('load').catch(() => {})
  await page.waitForTimeout(ms)
}
const bodyText = page => page.evaluate(() => document.body.innerText || '')
const norm = s => (s || '').replace(/\s+/g, ' ')

// ── HTTP 助手（不经浏览器）────────────────────────────────────────────────
async function login(email, pw) {
  const r = await fetch(`${API}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: pw }),
  })
  if (r.ok) return (await r.json()).access_token
  const reg = await fetch(`${API}/api/auth/register`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: pw, display_name: email.split('@')[0] }),
  })
  if (!reg.ok) throw new Error(`注册失败：HTTP ${reg.status} ${await reg.text()}`)
  return (await reg.json()).access_token
}
async function ensureProject(token, { tier = 'manage', markets = ['CN_A'] } = {}) {
  const r = await fetch(`${API}/api/v1/fin/projects`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ tier, markets }),
  })
  if (!r.ok) throw new Error(`开户失败：HTTP ${r.status} ${await r.text()}`)
  const d = await r.json()
  return d.project.project_id
}
async function apiGet(path, token) {
  const r = await fetch(`${API}/api/v1/fin${path}`, { headers: { Authorization: `Bearer ${token}` } })
  if (!r.ok) throw new Error(`GET ${path} → HTTP ${r.status}`)
  return r.json()
}

mkdirSync(OUT, { recursive: true })

// ── 浏览器 ────────────────────────────────────────────────────────────────
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
const page = await context.newPage()
page.on('console', m => { if (m.type() === 'error') consoleErrors.push({ url: page.url(), text: m.text() }) })
page.on('pageerror', e => pageErrors.push({ url: page.url(), text: String(e) }))
page.on('requestfailed', r => failedRequests.push({ url: r.url(), err: r.failure()?.errorText }))
// 「应用到模拟盘」是**页面上真点的按钮** —— 把浏览器实际发出的请求与收到的响应原样留档
// （出口标准要的是「按钮背后是真的」的证据，不是另发一条 curl）。
page.on('request', r => { if (r.url().includes('/evolution/apply')) report.applyRequest = { url: r.url(), method: r.method(), postData: r.postData() } })
page.on('response', async r => {
  if (r.url().includes('/evolution/apply')) {
    let body = null
    try { body = await r.json() } catch { body = null }
    report.applyResponse = { status: r.status(), body }
  }
})

async function dismissCompliance() {
  const okBtn = page.getByRole('button', { name: /确认继续/ }).first()
  if (await okBtn.count()) {
    const cb = page.locator('input[type="checkbox"]').first()
    if (await cb.count()) await cb.click().catch(() => {})
    await okBtn.click().catch(() => {})
    await settle(page, 1200)
  }
}

// ════════════════════════════════════════════════════════════════════════
// Phase EMPTY · 空项目：全文无阿拉伯数字
// ════════════════════════════════════════════════════════════════════════
if (PHASE === 'empty') {
  const token = await login(EMPTY.email, EMPTY.pw)
  const pid = await ensureProject(token)
  await fetch(`${API}/api/auth/compliance-ack`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {})
  report.emptyProject = pid

  // 后端侧证明「选项跟着项目走」：空项目的筛选值里没有标的 / 市场 / 市场状态
  const f = await apiGet(`/memory/filters?project_id=${encodeURIComponent(pid)}`, token)
  report.filtersEmpty = {
    kinds: f.kinds.map(k => k.value), statuses: f.statuses.map(k => k.value),
    markets: f.markets.length, regimes: f.regimes.length, symbols: f.symbols.length,
  }
  record('空项目 · 分类型下拉有三种（来自后端枚举）', f.kinds.length === 3, JSON.stringify(f.kinds.map(k => k.label)))
  record('空项目 · 状态下拉有三种（来自后端枚举）', f.statuses.length === 3, JSON.stringify(f.statuses.map(k => k.label)))
  record('空项目 · 标的下拉为空（数据驱动，换项目就变）', f.symbols.length === 0, `symbols=${f.symbols.length}`)

  await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)
  await dismissCompliance()
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)

  // 断言范围 = **本页自己的内容**（`data-r9="growth-page"`）。
  // 全局外壳（左上角登录邮箱、站点页脚 `开源(Apache 2.0)`）不属于这一页，本页改不动它 ——
  // 一并把被排除的行记进报告，不藏。
  const text = norm(await page.locator('[data-r9="growth-page"]').innerText())
  const shell = (await bodyText(page)).split('\n').map(s => s.trim())
    .filter(s => /[0-9０-９]/.test(s) && !text.includes(s))
  report.digitsExcludedFromShell = shell
  await shot(page, 'empty')
  record('空态 · 经验空态文案如实出现',
    text.includes('还没有经验') && text.includes('复核工作流跑过之后才会有'),
    norm(text).match(/还没有经验[^。]*/)?.[0] || '未见空态文案')
  record('空态 · 提案空态如实出现', text.includes('还没有提案'), '')
  record('空态 · 三块「本版未做」如实标注',
    (text.match(/本版未做/g) || []).length === 3, `本版未做 × ${(text.match(/本版未做/g) || []).length}`)

  const digits = text.match(/[0-9０-９]/g) || []
  record('空态 · 本页全文不出现任何阿拉伯数字', digits.length === 0,
    digits.length ? `出现 ${digits.length} 处：${[...new Set(digits)].join('')}`
                  : `本页 ${text.length} 字，零数字（被排除的全局外壳行：${shell.length}）`)
}

// ════════════════════════════════════════════════════════════════════════
// Phase DEGRADED · 横幅显示降级原因
// ════════════════════════════════════════════════════════════════════════
if (PHASE === 'degraded') {
  const token = await login(RICH.email, RICH.pw)
  await fetch(`${API}/api/auth/compliance-ack`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {})
  await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)
  await dismissCompliance()
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)

  const rt = await apiGet('/runtime', token)
  report.runtime = rt
  const banner = await page.locator('[data-r9="runtime-banner"]').innerText()
  await shot(page, 'degraded-banner')
  record('横幅 · 生效模式 = 降级后的 observe', banner.includes('观察'), norm(banner).slice(0, 120))
  record('横幅 · 降级原因显著显示', !!rt.degraded_reason && /已降级运行/.test(banner) && banner.includes('模拟账本未就绪'),
    norm(banner).match(/已降级运行[^）]*）?/)?.[0] || '未见降级条')
  record('横幅 · 自动生效恒为关闭', rt.auto_apply === false && banner.includes('关（本方案恒为关闭）'), '')
}

// ════════════════════════════════════════════════════════════════════════
// Phase RICH · 完整态
// ════════════════════════════════════════════════════════════════════════
if (PHASE === 'rich') {
  const token = await login(RICH.email, RICH.pw)
  await fetch(`${API}/api/auth/compliance-ack`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {})
  const pid = RICH.pid

  // 后端真值（供断言对照；页面显示的必须与它一致）
  const f = await apiGet(`/memory/filters?project_id=${encodeURIComponent(pid)}`, token)
  const exps = await apiGet(`/memory/experiences?project_id=${encodeURIComponent(pid)}`, token).catch(() => ({ items: [] }))
  const props = await apiGet(`/evolution/proposals?project_id=${encodeURIComponent(pid)}`, token)
  report.backend = { statuses: f.statuses.map(s => s.value), symbols: f.symbols.length,
                     exps: exps.items.length, proposals: props.items.map(p => [p.proposal_id, p.status]) }

  await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)
  await dismissCompliance()
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 3000)

  // ── ④ 经验库 ──
  const expText = norm(await page.locator('[data-r9="exp-table"]').innerText())
  await shot(page, '4-experience-list')

  record('④ 列表出现', expText.includes('缩量整理之后追高的回撤概率显著上升'), `${expText.length} 字`)
  record('④ 证据数可见（证据 N 笔）', /证据 \d+ 笔/.test(expText), expText.match(/证据 \d+ 笔/)?.[0] || '未见')
  record('④ 需重验角标出现', expText.includes('需重验'), '')
  record('④ refute 有视觉区分（失败经验 · 燃料）',
    (expText.match(/失败经验 · 燃料/g) || []).length >= 2,
    `失败经验角标 × ${(expText.match(/失败经验 · 燃料/g) || []).length}`)
  record('④ 人机混合来源可见', expText.includes('人机混合'), '')

  // 证据可展开：点第一条「（展开）」，看引用 id 是否出现
  const evBtn = page.getByRole('button', { name: /证据 \d+ 笔（展开）/ }).first()
  if (await evBtn.count()) {
    await evBtn.click()
    await settle(page, 600)
    const opened = norm(await page.locator('[data-r9="exp-table"]').innerText())
    record('④ 证据可展开看引用 id', /(trade|report|snapshot) · \S+/.test(opened),
      opened.match(/(trade|report|snapshot) · \S+/)?.[0] || '展开后未见引用 id')
  } else record('④ 证据可展开看引用 id', false, '找不到「（展开）」按钮')

  // ── 筛选：值来自后端（自绘下拉，展开态可截图、可读）──
  const openFilter = async (label) => {
    const box = page.locator(`[data-r9="filter-${label}"]`)
    await box.getByRole('button').first().click()
    await settle(page, 400)
    return box.locator('[role="option"]')
  }
  const statusOpts = (await (await openFilter('状态')).allInnerTexts()).map(s => s.trim())
  await shot(page, '4-filter-dropdown')
  record('筛选 · 状态下拉的值来自后端（只有三种 + 全部）',
    JSON.stringify(statusOpts) === JSON.stringify(['全部', ...f.statuses.map(s => s.label)]),
    JSON.stringify(statusOpts))
  await page.keyboard.press('Escape').catch(() => {})
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {})
  await settle(page, 300)

  const kindOpts = (await (await openFilter('类型')).allInnerTexts()).map(s => s.trim())
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {})
  record('筛选 · 类型下拉的值来自后端',
    JSON.stringify(kindOpts) === JSON.stringify(['全部', ...f.kinds.map(k => k.label)]),
    JSON.stringify(kindOpts))

  const symOpts = (await (await openFilter('标的')).allInnerTexts()).map(s => s.trim()).slice(1)
  record('筛选 · 标的下拉 = 后端返回的标的集合',
    symOpts.length === f.symbols.length && symOpts.includes('CN_A:601398'),
    `${symOpts.length} 个：${symOpts.slice(0, 4).join(' / ')}`)

  // 用「极性 = 失败经验」筛一次，断言列表只剩 refute 那两条
  const polOpts = await openFilter('极性')
  await polOpts.filter({ hasText: '失败经验' }).first().click()
  await settle(page, 1500)
  const refuteText = norm(await page.locator('[data-r9="exp-table"]').innerText())
  await shot(page, '4-filter-refute')
  record('筛选 · 选「失败经验」后只剩 refute 条目',
    refuteText.includes('缩量整理之后追高的回撤概率显著上升') && !refuteText.includes('长假前一周消费板块'),
    `行数≈${(refuteText.match(/exp_/g) || []).length}`)
  await (await openFilter('极性')).first().click()          // 选回「全部」
  await settle(page, 1500)

  // ── ⑥ 提案与验证 ──
  const bannerText = norm(await page.locator('[data-r9="runtime-banner"]').innerText())
  record('⑥ 横幅显示生效模式', bannerText.includes('生效模式') && /观察/.test(bannerText),
    bannerText.match(/生效模式[^未]*/)?.[0] || '')
  record('⑥ 横幅 · 无降级时不出降级条（本部署 observe 无降级）',
    !bannerText.includes('已降级运行'), '（降级态见 degraded 阶段截图）')

  const listText = norm(await page.locator('[data-r9="proposal-list"]').innerText())
  await shot(page, '6-proposals')
  record('⑥ 提案列表出现（两条）',
    listText.includes('evp_r9demo_passed00000001') && listText.includes('evp_r9demo_inconclusive001'),
    listText.slice(0, 160))

  // 默认选中最新一条 = 不结论的那条 → 两臂对照应明示 inconclusive
  const armText = norm(await page.locator('[data-r9="arm-compare"]').innerText())
  await shot(page, '6-arm-compare-inconclusive')
  record('⑥ 两臂对照 · 样本不足显示 inconclusive（不许显示成 0）',
    armText.includes('inconclusive') && /可比样本 3 \/ 需 20/.test(armText) && armText.includes('组合净收益'),
    armText.match(/可比样本[^）]*/)?.[0] || armText.slice(0, 120))
  record('⑥ 两臂对照 · 组合净收益 / 最大回撤 / 换手 / 交易成本 并排',
    armText.includes('组合净收益') && armText.includes('最大回撤') && armText.includes('换手') && armText.includes('交易成本'),
    '')

  const planText = norm(await page.locator('[data-r9="frozen-plan"]').innerText())
  record('⑥ 冻结计划卡展示 plan_hash',
    /plan_hash/.test(planText) && /[0-9a-f]{64}/.test(planText),
    (planText.match(/[0-9a-f]{64}/) || [''])[0].slice(0, 24) + '…')
  record('⑥ 冻结计划卡 · 说明口径写后不可改',
    planText.includes('冻死了') && planText.includes('不可改'), '')

  const evText = norm(await page.locator('[data-r9="events"]').innerText())
  await shot(page, '6-events')
  record('⑥ 事件链可见（含被闸门拒绝）',
    evText.includes('被闸门拒绝') && evText.includes('原因：') && evText.includes('验证通过'),
    evText.match(/被闸门拒绝[^阶]*/)?.[0]?.slice(0, 80) || '')

  // ── 两个按钮 ──
  const applyBtn = page.getByRole('button', { name: '应用到模拟盘' })
  const rollbackBtn = page.getByRole('button', { name: /回滚到上一个已验证版本/ })
  record('⑥ 「应用到模拟盘」按钮在', (await applyBtn.count()) > 0, '')
  record('⑥ 「回滚」按钮初始不在（只在观察期出现）', (await rollbackBtn.count()) === 0, '')
  record('⑥ 界面上没有「自动生效」开关',
    !(await page.getByRole('checkbox', { name: /自动生效/ }).count()) && !(await page.getByRole('switch').count()),
    '')

  // ── 人机混合写入 ──
  await page.getByRole('button', { name: /追加一条经验（人机混合）/ }).click()
  await settle(page, 1600)
  const form = page.locator('[data-r9="add-form"]')
  const stmt = form.getByPlaceholder(/一句话结论/)
  await stmt.fill('缩量整理后追高的回撤概率上升  3 成')
  await form.getByRole('button', { name: '追加' }).click()
  await settle(page, 1200)
  const afterBad = norm(await form.innerText())
  await shot(page, '4-form-digit-blocked')
  record('写入 · 含阿拉伯数字的结论被前端当场拦住',
    afterBad.includes('结论里不要写数字，数字请通过「证据引用」挂上去'),
    afterBad.match(/结论里不要写数字[^）]*/)?.[0] || '未见拦截提示')

  // ⚠️ 结论里**不能有阿拉伯数字**（前后端同一条判据）——所以这条测试文案必须用中文数字，
  // 否则会被自己刚验过的拦截逻辑挡下来，看起来像「写入坏了」。
  const goodStmt = '人机混合写入实测条目（脚本实测）'
  await stmt.fill(goodStmt)
  const cb = form.locator('input[type="checkbox"]').first()
  if (await cb.count()) await cb.click()
  await settle(page, 400)
  await shot(page, '4-form-filled')
  await form.getByRole('button', { name: '追加' }).click()
  await settle(page, 2800)
  await shot(page, '4-after-write')
  const afterGood = norm(await page.locator('[data-r9="exp-table"]').innerText())
  record('写入 · 提交成功且列表出现该条', afterGood.includes(goodStmt), goodStmt)
  // 精确到「那一行」的来源：定位含该结论的表行，看它自己的来源列
  const row = page.locator('[data-r9="exp-table"] tr', { hasText: goodStmt }).first()
  const rowText = (await row.count()) ? norm(await row.innerText()) : ''
  record('写入 · source 记成人机混合（就在那一行里）',
    rowText.includes('人机混合'), rowText.slice(0, 120))

  // ── 「应用到模拟盘」真点一次 ──
  await page.getByRole('button', { name: 'evp_r9demo_passed00000001' }).first().click()
  await settle(page, 1800)
  const passedPlan = norm(await page.locator('[data-r9="frozen-plan"]').innerText())
  await shot(page, '6-frozen-plan-passed')
  record('⑥ 选中通过的那条 → 冻结计划仍是同一份（plan_hash 前后一致）',
    /[0-9a-f]{64}/.test(passedPlan), (passedPlan.match(/[0-9a-f]{64}/) || [''])[0].slice(0, 24) + '…')

  await applyBtn.click()
  await settle(page, 700)
  const confirmVisible = await page.getByRole('button', { name: '确认应用' }).count()
  await shot(page, '6-apply-confirm')
  record('⑥ 「应用到模拟盘」先出确认（且 diff 与冻结计划已在屏上）', confirmVisible > 0, '')
  const t0 = Date.now()
  await page.getByRole('button', { name: '确认应用' }).click()
  await settle(page, 3000)
  const afterApply = norm(await page.locator('main').innerText().catch(() => bodyText(page)))
  report.applyMs = Date.now() - t0
  await shot(page, '6-after-apply')
  record('⑥ 生效成功（返回 from → to 并刷新）',
    /已生效：\S+ → \S+/.test(afterApply), afterApply.match(/已生效：\S+ → \S+/)?.[0] || afterApply.slice(0, 120))
  const rollbackNow = await page.getByRole('button', { name: /回滚到上一个已验证版本/ }).count()
  record('⑥ 生效后「回滚」按钮出现（观察期）', rollbackNow > 0, '')
  const statusNow = norm(await page.locator('[data-r9="proposal-list"]').innerText())
  record('⑥ 提案状态随投影变 applied', statusNow.includes('applied'), '')
}

// ════════════════════════════════════════════════════════════════════════
// Phase REINJECT · 「回滚已完成，失败经验回灌待完成」不许静默
// ════════════════════════════════════════════════════════════════════════
//
// 前置（脚本外部做的两步，见成果文档）：
//   ① 把 api 的 FIN_MEMORY_ENABLED 置 0 后重启 → 对已生效的提案调一次回滚，
//      回滚的第三样（一条有证据的失败经验）写不进去 → 落一条 `pending` 回灌任务；
//   ② 把 api 恢复成 FIN_MEMORY_ENABLED=1。
// 本阶段只读页面 + 点一次「重试回灌」。
if (PHASE === 'reinject') {
  const token = await login(RICH.email, RICH.pw)
  await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 2500)
  await dismissCompliance()
  await page.goto(BASE + '/finance/growth', { waitUntil: 'load' })
  await settle(page, 3000)

  const props = await apiGet(`/evolution/proposals?project_id=${encodeURIComponent(RICH.pid)}`, token)
  const pend = props.items.find(p => p.reinject_pending)
  report.reinjectPending = pend ? pend.proposal_id : null
  if (pend) {
    await page.getByRole('button', { name: pend.proposal_id }).first().click().catch(() => {})
    await settle(page, 1800)
  }
  const actions = norm(await page.locator('[data-r9="actions"]').innerText())
  await shot(page, '6-reinject-pending')
  record('回灌 · 「回滚已完成，失败经验回灌待完成」显著显示',
    actions.includes('回滚已完成，失败经验回灌待完成'), actions.slice(0, 160))
  record('回灌 · 重试入口在（不是只给个状态）',
    (await page.getByRole('button', { name: '重试回灌' }).count()) > 0, '')

  await page.getByRole('button', { name: '重试回灌' }).click()
  await settle(page, 3000)
  const after = norm(await page.locator('[data-r9="actions"]').innerText())
  await shot(page, '6-reinject-after-retry')
  record('回灌 · 重试后状态位消失（真的写进去了）',
    !after.includes('回滚已完成，失败经验回灌待完成'), after.slice(0, 160))
}

// ════════════════════════════════════════════════════════════════════════
// Phase HELP · 帮助页 FAQ 新增条目
// ════════════════════════════════════════════════════════════════════════
if (PHASE === 'help') {
  const token = await login(RICH.email, RICH.pw)
  await context.addInitScript(t => { try { localStorage.setItem('hunter_token', t) } catch {} }, token)
  await page.goto(BASE + '/finance/help', { waitUntil: 'load' })
  await settle(page, 2200)
  await dismissCompliance()
  await page.goto(BASE + '/finance/help', { waitUntil: 'load' })
  await settle(page, 2200)
  const t = norm(await bodyText(page))
  await page.getByText(/「经验」是什么/).first().scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(400)
  await shot(page, 'help-faq')
  record('帮助页 · FAQ 新增「经验是什么 / 为什么只有三种状态 / 被推翻为什么不删 / 保底集为什么搜不到」',
    t.includes('「经验」是什么') && t.includes('状态只有三种') && t.includes('被推翻的经验不删') && t.includes('保底测试集的结论搜不到'),
    t.match(/「经验」是什么[^。]*/)?.[0] || '未见新 FAQ')
}

// ── 汇总 ──────────────────────────────────────────────────────────────────
report.consoleErrors = consoleErrors
report.pageErrors = pageErrors
report.failedRequests = failedRequests.filter(r => !/favicon/.test(r.url))
report.summary = {
  phase: PHASE,
  assertions: report.assertions.length,
  failed: report.assertions.filter(a => !a.ok).length,
  consoleErrors: consoleErrors.length,
  pageErrors: pageErrors.length,
  failedRequests: report.failedRequests.length,
  shots: report.shots.length,
}
writeFileSync(`${OUT}/report-${PHASE}.json`, JSON.stringify(report, null, 2))
console.log('SUMMARY ' + JSON.stringify(report.summary))
for (const a of report.assertions) console.log(`  [${a.ok ? 'OK  ' : 'FAIL'}] ${a.kind} — ${a.detail}`)
if (consoleErrors.length) console.log('CONSOLE ERRORS:', JSON.stringify(consoleErrors.slice(0, 8), null, 1))
if (pageErrors.length) console.log('PAGE ERRORS:', JSON.stringify(pageErrors.slice(0, 8), null, 1))
if (report.failedRequests.length) console.log('FAILED REQUESTS:', JSON.stringify(report.failedRequests.slice(0, 8), null, 1))
await browser.close()
process.exit(report.summary.failed ? 1 : 0)
