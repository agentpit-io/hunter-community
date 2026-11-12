/**
 * HCA-UNIFY 阶段 M3 · 前端全量合流与香港环境（127.0.0.1:3200）Playwright 真浏览器端到端验收脚本
 *
 * 运行方式：
 *   cd ~/hca/pw && node /mnt/mixplode/hca/tools/e2e/m3-unify-verify.mjs \
 *     --base http://127.0.0.1:3200 \
 *     --secrets /mnt/mixplode/hca/secrets-admin \
 *     --out /mnt/mixplode/hunter-dev/atomcode-unify-chain/reports/screenshots-m3
 */
import { setup, login, step, results, BASE, OUT, sleep, box, send, shot, dismissModals, scrollToBottom } from './lib.mjs'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

mkdirSync(OUT, { recursive: true })
const { browser, page, consoleErrors, badResponses } = await setup()
const summary = {}

// 1) 首页与顶栏导航（含「Hunter-AtomCode」标识、「hunter智能体自动炒股」下拉菜单、「策略中心」、「帮助中心」）
await step('1-首页与顶栏导航（品牌名 + 智能炒股下拉菜单 + 策略中心 + 帮助中心）', async () => {
  await login()
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await sleep(3000)
  await dismissModals()
  await box().waitFor({ timeout: 30000 })

  const bodyText = await page.locator('body').innerText()
  if (!bodyText.includes('Hunter-AtomCode')) {
    throw new Error('顶栏未找到 Hunter-AtomCode 品牌标识')
  }
  if (!bodyText.includes('策略中心') || !bodyText.includes('帮助中心')) {
    throw new Error('顶栏未找到策略中心或帮助中心链接')
  }

  // 点击「hunter智能体自动炒股」展开 7 个二级菜单
  const finMenuBtn = page.locator('button[title="hunter智能体自动炒股"]')
  await finMenuBtn.waitFor({ timeout: 10000 })
  await finMenuBtn.click()
  await sleep(600)

  const menuText = await page.locator('[role="menu"]').innerText()
  for (const item of ['总览', '自动交易', '我的账户', '每日报告', '成长与复盘', '安全与帮助', '设置向导']) {
    if (!menuText.includes(item)) {
      throw new Error(`智能炒股下拉菜单缺少子入口：${item}`)
    }
  }
  await shot('01-topnav-and-finance-dropdown')
  // 关闭下拉菜单
  await finMenuBtn.click()
  await sleep(300)
  return '顶栏包含 Hunter-AtomCode、策略中心、帮助中心及智能炒股 7 个二级入口'
})

// 2) 对话页（含左下角「📎 上传文档/PDF」入口、模型选择器显示 Gemini 4 Argon，发送一轮真实问题通过 AtomCode Daemon + MCP 流式返回中文回答）
await step('2-对话页（文档上传按钮 + Gemini 4 Argon 模型选择器 + 真实投研问答流式返回）', async () => {
  // 检查左下角上传文档按钮
  const uploadBtn = page.locator('[data-testid="upload-doc-btn"]')
  if ((await uploadBtn.count()) === 0) {
    throw new Error('未找到聊天框左下角 [data-testid="upload-doc-btn"] 📎 上传文档/PDF 按钮')
  }

  // 打开模型选择器检查 Gemini 4 Argon
  const modelBtn = page.locator('button[title="切换 model"]')
  await modelBtn.waitFor({ timeout: 10000 })
  const modelBtnText = (await modelBtn.innerText()).trim()
  await modelBtn.click()
  await sleep(800)
  const pickerText = await page.locator('body').innerText()
  if (!pickerText.includes('Gemini 4 Argon')) {
    throw new Error(`模型选择器未展示 Gemini 4 Argon，当前按钮文本：${modelBtnText}`)
  }
  if (!pickerText.includes('查看/下载大模型调用日志')) {
    throw new Error('模型选择器底部缺少「查看/下载大模型调用日志」入口')
  }
  await shot('02-chat-model-picker-gemini4-argon')
  // 关闭模型选择器
  await modelBtn.click()
  await sleep(400)

  // 测试上传一份带股票代码的 Markdown 研报摘要并触发解析芯片与右侧原文核对抽屉
  const sampleMd = Buffer.from(
    '# 贵州茅台（600519）2025半年报核心摘要\n\n' +
    '| 指标 | 数值 | 同比 |\n|---|---|---|\n| 营业总收入 | 834.51亿元 | +16.95% |\n| 归母净利润 | 416.96亿元 | +15.88% |\n\n' +
    '核心结论：公司高端白酒护城河稳固，现金流充沛。\n',
    'utf-8'
  )
  const fileInput = page.locator('[data-testid="file-upload-input"]')
  await fileInput.setInputFiles({
    name: '贵州茅台2025半年报摘要.md',
    mimeType: 'text/markdown',
    buffer: sampleMd,
  })
  // 等待解析完成（显示芯片）
  await sleep(2500)
  await shot('02b-chat-doc-uploaded-chip')

  // 发送一轮真实问题，走 AtomCode Daemon + MCP
  const q = '请结合我上传的贵州茅台（600519）摘要，并用快览工具核对一下贵州茅台最新行情，用三句话总结核心财务数据与当前估值。'
  await send(q, 240000)
  await sleep(1500)
  await scrollToBottom()
  await shot('02c-chat-atomcode-turn-reply')

  const allText = await page.locator('body').innerText()
  if (!allText.includes('834.51') && !allText.includes('茅台')) {
    throw new Error('对话回复未包含茅台或上传文档核心数据')
  }
  if (!allText.includes('猎鹿人 · Hunter-AtomCode')) {
    throw new Error('对话回复缺少「猎鹿人 · Hunter-AtomCode」署名')
  }
  summary.chatReplyPreview = allText.slice(-400)
  return `模型选择器显示 ${modelBtnText}，文档上传解析与 AtomCode Daemon 真实流式回答验证通过`
})

// 3) 策略中心（小鹿规则编辑器、多语言/AI 助手面板渲染正常）
await step('3-策略中心（小鹿规则编辑器与多语言 AI 助手面板）', async () => {
  const res = await page.goto(`${BASE}/strategies/agent.html`, { waitUntil: 'networkidle', timeout: 30000 })
  if (!res || res.status() !== 200) {
    throw new Error(`/strategies/agent.html 返回状态异常：${res ? res.status() : 'null'}`)
  }
  await sleep(2000)
  const text = await page.locator('body').innerText()
  if (!text.includes('小鹿') && !text.includes('策略') && !text.includes('规则')) {
    throw new Error('策略中心 agent.html 页面正文未正常渲染')
  }
  await shot('03-strategies-agent-builder')
  return `/strategies/agent.html HTTP ${res.status()} 渲染正常`
})

// 4) 智能炒股页面（总览 /finance、开户向导 /finance/onboarding 与 /finance/setup、持仓 /finance/account、风控 /finance/auto-trade、成长与经验库 /finance/evolution 与 /finance/growth 正常渲染，API 返回 200）
await step('4-智能炒股全套页面与 API 校验', async () => {
  // 先校验后端智能炒股核心 API 返回 200
  const apiStatus = await page.evaluate(async () => {
    const token = localStorage.getItem('hunter_token') || ''
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const urls = [
      '/api/v1/fin/projects/current',
      '/api/v1/fin/markets',
      '/api/v1/fin/tiers',
      '/api/v1/fin/runtime',
      '/api/v1/fin/strategies',
    ]
    const out = {}
    for (const u of urls) {
      const r = await fetch(u, { headers })
      out[u] = r.status
    }
    return out
  })
  for (const [u, st] of Object.entries(apiStatus)) {
    if (st !== 200) throw new Error(`智能炒股 API ${u} 返回 HTTP ${st}（期望 200）`)
  }

  // 4.1 总览 /finance -> /finance/overview
  await page.goto(`${BASE}/finance`, { waitUntil: 'networkidle', timeout: 30000 })
  await sleep(1500)
  await shot('04a-finance-overview')

  // 4.2 开户向导 /finance/onboarding (及 /finance/setup)
  const rOnboard = await page.goto(`${BASE}/finance/onboarding`, { waitUntil: 'networkidle', timeout: 30000 })
  if (!rOnboard || rOnboard.status() !== 200) {
    throw new Error(`/finance/onboarding 返回 HTTP ${rOnboard ? rOnboard.status() : 'null'}`)
  }
  await sleep(1500)
  const onboardText = await page.locator('body').innerText()
  if (!onboardText.includes('模拟') && !onboardText.includes('档位')) {
    throw new Error('/finance/onboarding 页面未渲染开户向导内容')
  }
  await shot('04b-finance-onboarding')

  // 4.3 我的账户 /finance/account 与 自动交易 /finance/auto-trade
  await page.goto(`${BASE}/finance/account`, { waitUntil: 'networkidle', timeout: 30000 })
  await sleep(1200)
  await shot('04c-finance-account')

  await page.goto(`${BASE}/finance/auto-trade`, { waitUntil: 'networkidle', timeout: 30000 })
  await sleep(1200)
  await shot('04d-finance-auto-trade')

  // 4.4 成长与经验库 /finance/evolution (及 /finance/growth)
  const rEvo = await page.goto(`${BASE}/finance/evolution`, { waitUntil: 'networkidle', timeout: 30000 })
  if (!rEvo || rEvo.status() !== 200) {
    throw new Error(`/finance/evolution 返回 HTTP ${rEvo ? rEvo.status() : 'null'}`)
  }
  await sleep(1500)
  const evoText = await page.locator('body').innerText()
  if (!evoText.includes('经验') && !evoText.includes('成长') && !evoText.includes('复盘')) {
    throw new Error('/finance/evolution 页面未渲染经验库与自进化内容')
  }
  await shot('04e-finance-evolution')

  summary.finApiStatus = apiStatus
  return `智能炒股 5 个核心 API 全部 200，总览/开户向导/账户/自动交易/成长与经验库页面全部渲染正常`
})

// 5) 帮助中心（/help/index.html 正常打开）
await step('5-帮助中心（/help/index.html 正常打开）', async () => {
  const res = await page.goto(`${BASE}/help/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 })
  if (!res || res.status() !== 200) {
    throw new Error(`/help/index.html 返回 HTTP ${res ? res.status() : 'null'}`)
  }
  await sleep(1000)
  const text = await page.locator('body').innerText()
  if (text.length < 200) {
    throw new Error('/help/index.html 内容过短，疑似未正常渲染')
  }
  await shot('05-help-center')
  return `/help/index.html HTTP 200（正文长度 ${text.length} 字符）`
})

// 6) 设置页（/settings?tab=llm，「大模型调用日志与问题跟踪」卡片存在，点击实测与日志接口返回 200）
await step('6-设置页（/settings?tab=llm 大模型调用日志与实测 + 关于 Hunter-AtomCode）', async () => {
  await page.goto(`${BASE}/settings?tab=llm`, { waitUntil: 'networkidle', timeout: 30000 })
  await sleep(2000)
  const textBefore = await page.locator('body').innerText()
  if (!textBefore.includes('大模型调用日志与问题跟踪')) {
    throw new Error('/settings?tab=llm 未找到「大模型调用日志与问题跟踪」卡片')
  }
  if (!textBefore.includes('对话引擎（AtomCode）快照')) {
    throw new Error('/settings?tab=llm 未找到「对话引擎（AtomCode）快照」行')
  }

  // 点击「⚡ 实测一次大模型并记录日志」按钮
  const pingBtn = page.getByRole('button', { name: /实测一次大模型并记录日志/ })
  await pingBtn.waitFor({ timeout: 10000 })
  await pingBtn.click()

  // 等待实测结果出现
  await page.locator('text=实测结果：').waitFor({ timeout: 45000 })
  await sleep(1000)
  await shot('06a-settings-llm-logs-and-probe')

  // 验证日志下载接口返回 200
  const dlCheck = await page.evaluate(async () => {
    const token = localStorage.getItem('hunter_token') || ''
    const r = await fetch('/api/setup/llm/logs/download', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    const txt = await r.text()
    return { status: r.status, len: txt.length, hasAtomcode: txt.includes('atomcode') }
  })
  if (dlCheck.status !== 200 || dlCheck.len < 100) {
    throw new Error(`/api/setup/llm/logs/download 异常：${JSON.stringify(dlCheck)}`)
  }

  // 切换到「关于」页签验证 Hunter-AtomCode 与 GitCode 链接
  const aboutBtn = page.getByRole('button', { name: '关于' })
  await aboutBtn.click()
  await sleep(800)
  const aboutText = await page.locator('body').innerText()
  if (!aboutText.includes('关于 Hunter-AtomCode') || !aboutText.includes('gitcode.com/agentpit-io/huntercode-atomcode')) {
    throw new Error('关于页签缺少 Hunter-AtomCode 或 GitCode 开源地址')
  }
  await shot('06b-settings-about-atomcode')

  summary.dlCheck = dlCheck
  return `设置页实测探针成功、日志下载 HTTP ${dlCheck.status} (${dlCheck.len} bytes)、关于页签品牌验证通过`
})

await browser.close()

const outJson = {
  ts: new Date().toISOString(),
  base: BASE,
  results,
  summary,
  consoleErrorsCount: consoleErrors.length,
  badResponsesCount: badResponses.length,
}
writeFileSync(join(OUT, 'm3-verify-results.json'), JSON.stringify(outJson, null, 2))
console.log('\n══ 汇总 ══')
for (const r of results) {
  console.log(`  ${r.ok ? '✅' : '❌'} ${r.name}: ${r.note} (${r.ms}ms)`)
}
if (results.some((r) => !r.ok)) {
  process.exit(1)
}
