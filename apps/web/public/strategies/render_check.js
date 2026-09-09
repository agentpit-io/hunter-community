/**
 * 策略中心 · 页面脚本渲染自检(node,无需浏览器、无需构建)
 *
 *   cd apps/web/public/strategies && node render_check.js
 *   # 只查一个页面:node render_check.js workbench.html
 *
 * 为什么需要它(CLAUDE.md 里点名的那条):
 * 这几个页面的逻辑全写在 HTML 的**内联 `<script>`** 里,
 * `node --check` 只看 .js 文件、curl 只看 HTTP 200、docker build 只跑 next build ——
 * 三个都不会执行这段代码。于是内联脚本里的语法错误和 `undefined` 引用
 * (前科:`s.metrics.ann_ret` 读了一个已经删掉的字段)一路上线,
 * 表现是**整页白屏**,而所有检查都是绿的。
 *
 * 这里把内联脚本抽出来,在 node 的 `vm` 里真跑一遍,
 * 用假的 document / localStorage / fetch / echarts 兜住。
 * 它验证的是「这段脚本能不能跑起来、渲染函数能不能出 HTML」,
 * 不验证样式和交互 —— 那些要真浏览器。
 *
 * 退出码非 0 = 有页面跑不起来,别部署。
 */
const fs = require('fs')
const path = require('path')
const vm = require('vm')

const DIR = __dirname
const PAGES = process.argv.slice(2).length
  ? process.argv.slice(2)
  : ['index.html', 'factors.html', 'workbench.html', 'backtest.html', 'data.html', 'agent.html']

// ─── 假 DOM ───────────────────────────────────────────────────────
// 不解析 HTML:每次查询都返回一个新的空元素。
// 目的是让脚本**跑完**,而不是模拟浏览器 —— 真正要抓的是
// 语法错、拼错的变量名、读了不存在的字段这三类。
function makeEl(tag) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    style: { cssText: '', display: '' },
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false } },
    children: [],
    value: '',
    textContent: '',
    checked: false,
    disabled: false,
    options: [],
    selectedIndex: 0,
    addEventListener() {},
    removeEventListener() {},
    appendChild(c) { el.children.push(c); return c },
    insertBefore(c) { el.children.push(c); return c },
    removeChild() {},
    remove() {},
    setAttribute() {},
    getAttribute() { return null },
    focus() {}, click() {}, scrollIntoView() {},
    getBoundingClientRect() { return { width: 800, height: 600, top: 0, left: 0 } },
    querySelector() { return makeEl() },
    querySelectorAll() { return [] },
    closest() { return makeEl() },
  }
  let html = ''
  Object.defineProperty(el, 'innerHTML', {
    get() { return html },
    set(v) { html = String(v == null ? '' : v) },
  })
  Object.defineProperty(el, 'parentNode', { get() { return makeEl() } })
  return el
}

function makeContext(pageName) {
  const store = {}
  const body = makeEl('body')
  const doc = {
    body,
    documentElement: makeEl('html'),
    head: makeEl('head'),
    title: '',
    readyState: 'complete',
    getElementById: () => makeEl(),
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    createElement: (t) => makeEl(t),
    createTextNode: () => makeEl('text'),
    addEventListener() {},
    location: { href: 'http://localhost/strategies/' + pageName, search: '', pathname: '/strategies/' + pageName },
  }
  const ctx = {
    document: doc,
    console,
    // 网络一律不通:页面必须能在拿不到数据时把界面渲染出来
    // (这本身就是要验证的 —— 后端没起来也不许白屏)
    fetch: () => Promise.reject(new Error('render_check: 不联网')),
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v) },
      removeItem: (k) => { delete store[k] },
      clear: () => { for (const k of Object.keys(store)) delete store[k] },
    },
    location: doc.location,
    history: { pushState() {}, replaceState() {} },
    navigator: { userAgent: 'render_check', clipboard: { writeText: () => Promise.resolve() } },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval,
    requestAnimationFrame: (f) => setTimeout(f, 0),
    URL, URLSearchParams, Blob: function () {}, FormData: function () {},
    alert() {}, confirm: () => true, prompt: () => null,
    // 页面会挂 window 级监听(resize / beforeunload),没有这两个会当场 TypeError
    addEventListener() {}, removeEventListener() {},
    innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    // 页面用 CDN 的 echarts;这里给个能吃掉所有调用的替身
    echarts: {
      init: () => ({ setOption() {}, resize() {}, dispose() {}, on() {} }),
      getInstanceByDom: () => null,
    },
  }
  ctx.window = ctx
  ctx.globalThis = ctx
  return ctx
}

// ─── 取内联脚本 ───────────────────────────────────────────────────
function inlineScripts(html) {
  const out = []
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi
  let m
  while ((m = re.exec(html))) {
    if (/\bsrc\s*=/.test(m[1])) continue          // 外链(app.js / CDN)另外处理
    out.push(m[2])
  }
  return out
}

let failed = 0
const appJs = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8')

for (const page of PAGES) {
  const file = path.join(DIR, page)
  if (!fs.existsSync(file)) { console.log('SKIP', page, '(文件不存在)'); continue }
  const ctx = vm.createContext(makeContext(page))
  let stage = 'app.js'
  try {
    vm.runInContext(appJs, ctx, { filename: 'app.js' })
    const parts = inlineScripts(fs.readFileSync(file, 'utf8'))
    parts.forEach((src, i) => {
      stage = `${page} 内联脚本 #${i + 1}`
      vm.runInContext(src, ctx, { filename: `${page}#${i + 1}` })
    })
    const rendered = ctx.document.body.innerHTML.length
    if (!rendered) throw new Error('跑完了但 body 是空的 —— 页面没渲染出任何东西')
    console.log('PASS', page, `· ${parts.length} 段内联脚本 · body ${rendered} 字符`)
  } catch (e) {
    failed++
    console.log('FAIL', page, '·', stage, '·', e && e.stack ? e.stack.split('\n')[0] : e)
    if (e && e.stack) console.log('     ', e.stack.split('\n').slice(1, 4).join('\n      '))
  }
}

// ─── 针对因子参数的定向断言 ───────────────────────────────────────
// 光"跑起来了"不够:参数区是这次改动的重点,要确认它真的渲染出了输入框。
// 后端接口在这里是不通的,所以直接把一份 spec 塞进去,验证渲染分支。
try {
  const ctx = vm.createContext(makeContext('workbench.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const wb = inlineScripts(fs.readFileSync(path.join(DIR, 'workbench.html'), 'utf8'))
  wb.forEach((src, i) => vm.runInContext(src, ctx, { filename: `workbench#${i + 1}` }))

  vm.runInContext(`
    FACTOR_PARAM_SPECS = { high52_prox: normalizeParamSpec([
      {key:'window', label:'回看窗口', default:250, min:60, max:500, step:5, unit:'日', hint:'x'},
      {key:'near_pct', label:'贴近阈值', type:'float', default:100, min:1, max:100, step:0.5, unit:'%', hint:'y'},
      {key:'outside', label:'超出阈值的', type:'select', default:'floor',
       options:[{value:'floor',label:'并到最低分'},{value:'drop',label:'不打分'}], hint:'z'},
    ]) }
    draft = { factors:['high52_prox','roe'], weights:{high52_prox:60, roe:40}, params:{}, config:{} }
    var HTML = renderLeftPanel()
  `, ctx, { filename: 'assert-params' })

  const html = ctx.HTML
  const need = [
    ['52 周高点距离的参数入口', /⚙ 参数/],
    ['贴近阈值输入框', /data-pkey="near_pct"/],
    ['select 型参数画成下拉框', /<select[^>]*data-pkey="outside"/],
    ['下拉框选项', /并到最低分/],
    ['没有参数的因子明确写出来', /无可调参数/],
  ]
  for (const [name, re] of need) {
    if (re.test(html)) console.log('PASS 参数区 ·', name)
    else { failed++; console.log('FAIL 参数区 ·', name) }
  }

  // 用户调过参数 → 必须进 payload;调回默认 → 不进(省一次实时重算)
  vm.runInContext(`
    draft.params = { high52_prox: { window:250, near_pct:5, outside:'drop' } }
    var P1 = factorsPayload(draft)
    draft.params = { high52_prox: { window:250, near_pct:100, outside:'floor' } }
    var P2 = factorsPayload(draft)
  `, ctx, { filename: 'assert-payload' })
  const p1 = ctx.P1.find(f => f.key === 'high52_prox')
  const p2 = ctx.P2.find(f => f.key === 'high52_prox')
  if (p1 && p1.params && p1.params.near_pct === 5 && p1.params.outside === 'drop') {
    console.log('PASS 参数区 · 调过的参数进了请求体')
  } else { failed++; console.log('FAIL 参数区 · 调过的参数没进请求体', JSON.stringify(p1)) }
  if (p2 && !p2.params) console.log('PASS 参数区 · 调回默认不再带 params')
  else { failed++; console.log('FAIL 参数区 · 调回默认仍带 params', JSON.stringify(p2)) }

  // 参数定义还没拉到时:不能把用户存好的参数丢掉
  vm.runInContext(`
    FACTOR_PARAM_SPECS = null
    draft.params = { high52_prox: { near_pct: 5 } }
    var P3 = factorsPayload(draft)
  `, ctx, { filename: 'assert-offline' })
  const p3 = ctx.P3.find(f => f.key === 'high52_prox')
  if (p3 && p3.params && p3.params.near_pct === 5) {
    console.log('PASS 参数区 · 定义没拉到时仍把已存参数发给后端')
  } else { failed++; console.log('FAIL 参数区 · 定义没拉到时把用户的参数弄丢了', JSON.stringify(p3)) }
} catch (e) {
  failed++
  console.log('FAIL 参数区定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 针对小鹿智能体的定向断言 ─────────────────────────────────────
// 这个页面的全部数字来自后端,本地跑不到接口,所以直接把两份 fixture
// 塞进渲染函数验证两件事:
//   ① 区块顺序 —— 净值曲线 → 规则 → 持仓 → 操作报告 → 演进 → 成长总结
//   ② 字段为 null 时落到 `—`,不出现 NaN / undefined / 凭空的 0.00
// 第 ② 条是仓内铁律「空的比假的好」的机器可验形式:光靠 review 看不住,
// 以后有人给某个字段加 `|| 0` 兜底,这里会当场红。
// fixture 是测试固件,不是产品数据 —— 产品代码里一个业务数字都没有。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const ag = inlineScripts(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))
  ag.forEach((src, i) => vm.runInContext(src, ctx, { filename: `agent#${i + 1}` }))

  vm.runInContext(`
    var FULL = {
      state:'running', paper:true, version:'v7', day_count:34, iteration_count:7,
      last_run_text:'09-09 05:32 ET', next_run_text:'09-10 05:30 ET',
      strategy:{name:'动量突破 + 财报后漂移', version:'v7', summary:'买强势股的突破',
                market_label:'美股', market_note:'暂不支持 A 股', universe:'S&P 500', universe_size:503,
                rebalance:'事件驱动', data_source:'日线 + 财报日历'},
      guardrails:{initial_capital:10000, max_position_pct:15, max_holdings:8,
                  daily_loss_halt_pct:-3, consecutive_loss_pause:3, long_only:true, triggered_today:false},
      pipeline:{date:'2026-09-09', steps:[
        {key:'collect', name:'收集数据', status:'ok', at:'05:30', duration_ms:2400, summary:'503 只'},
        {key:'backtest', name:'滚动回测', status:'ok', at:'05:31', duration_ms:18000, summary:'重跑 7 条规则'},
        {key:'review', name:'复盘总结', status:'ok', at:'05:32', duration_ms:900, summary:'产出 1 条教训'},
        {key:'adjust', name:'调整策略', status:'warn', at:'05:32', duration_ms:120, summary:'改 1 条规则'}]},
      overview:{pnl_abs:1024, pnl_pct:10.2, equity:11024, benchmark_symbol:'SPY', benchmark_pct:6.4,
                excess_pt:3.8, max_dd_pct:-10, max_dd_abs:-1003, dd_from:'08-26', dd_to:'09-01',
                trades_total:100, trades_win:40, win_rate:40, profit_factor:2.7, sharpe:1.28,
                risk_free_pct:4.3, holdings_count:6, max_holdings:8, invested_pct:62, cash:4189},
      nav:{benchmark_symbol:'SPY', points:[
        {date:'2026-08-01', agent_pct:0, benchmark_pct:0},
        {date:'2026-08-15', agent_pct:8.6, benchmark_pct:4.3},
        {date:'2026-09-01', agent_pct:-2.3, benchmark_pct:-0.4},
        {date:'2026-09-09', agent_pct:10.2, benchmark_pct:6.4}],
        version_marks:[{index:1, version:'v6'}], drawdown:{from_index:1, to_index:2, pct:-10}},
      rules:[{id:'R-01', kind:'buy', condition:'动量前 10%', since_text:'v1 起', status:'active',
              stats:[{label:'触发', value:23},{label:'触发后胜率', value:'43%'},{label:'样本', value:null}]},
             {id:'R-03', kind:'risk', condition:'跌破 -6% 出', since_text:'观察期', status:'observing', stats:[]}],
      holdings:{as_of:'09-09 16:00 ET', quote_delay_min:15, items:[
        {symbol:'NVDA', name:'英伟达', cost:178.4, price:195.62, pnl_pct:9.7, hold_days:18,
         bench_pct:2.1, sharpe:1.94, entry_rule:'R-02', entry_rule_text:'财报后放量突破'},
        {symbol:'COST', name:'好市多', cost:903.1, price:918.44, pnl_pct:1.7, hold_days:9,
         bench_pct:0.9, sharpe:null, sharpe_na_reason:'持有 9 日,样本不足', entry_rule:'R-04'}]},
      watchlist:{items:[
        {symbol:'MU', score:92, price:142.3, rule_id:'R-02', progress_pct:82, gap:'距 20 日高还差 0.8%'},
        {symbol:'SMCI', price:41.88, blocked:true, blocked_reason:'波动率 68% 超风险预算'}]},
      trades:{date:'2026-09-09', items:[
        {ts_market:'09:31', ts_market_tz:'ET', ts_local:'21:31 沪', side:'buy', symbol:'SHOP',
         shares:41, price:121.55, amount:4983, position_pct:12.4, rule_id:'R-02',
         rule_name:'财报后放量突破', rationale:'量能达 20 日均量 2.3 倍'},
        {ts_market:'15:47', ts_market_tz:'ET', side:'sell', symbol:'TSLA', shares:14, price:232.8,
         pnl_abs:-251, pnl_pct:-7.1, rule_id:'R-03', rationale:'尾盘跌破止损线',
         adjustment:'今晚把 R-03 止损收到 -6%'}]},
      versions:[{label:'v5 → v6', date:'09-02', change:'改移动止盈', reason:'多次出场后继续涨',
                 effect:'4 笔平均多留 2.1 pt', status:'released'},
                {label:'v6 → v7', date:'09-06', change:'排除财报当日', reason:'隔夜跳空', status:'current'}],
      lessons:[{date:'09-09', title:'止损设太宽', kind:'loss', what:'两笔止损亏 487 美元',
                why:'-8% 是拍脑袋定的', learned:'该按回来的概率定',
                landed:{status:'landed', text:'R-03 止损 -8% → -6%'}},
               {date:'09-08', title:'移动止盈更优', kind:'validated', what:'触发 4 次',
                landed:{status:'pending', text:'样本 4/15,继续累积'}}]
    }
    // 后端字段全空的极端情况 —— 页面必须显示 —,而不是 NaN / 0.00 / undefined
    var EMPTY = {
      state:'running', version:null, day_count:null, iteration_count:null,
      strategy:{name:null, summary:null, market_label:null, universe:null, universe_size:null,
                rebalance:null, data_source:null},
      guardrails:{initial_capital:null, max_position_pct:null, max_holdings:null,
                  daily_loss_halt_pct:null, consecutive_loss_pause:null, triggered_today:null},
      pipeline:{steps:[{key:'collect', name:'收集数据', status:'fail', at:null,
                        duration_ms:null, summary:null}]},
      overview:{pnl_abs:null, pnl_pct:null, equity:null, benchmark_symbol:null, benchmark_pct:null,
                excess_pt:null, max_dd_pct:null, max_dd_abs:null, trades_total:null, trades_win:null,
                win_rate:null, profit_factor:null, sharpe:null, risk_free_pct:null,
                holdings_count:null, max_holdings:null, invested_pct:null, cash:null},
      nav:{points:[]},
      rules:[{id:null, kind:'buy', condition:null, since_text:null, stats:[{label:'触发', value:null}]}],
      holdings:{items:[{symbol:'X', name:null, cost:null, price:null, pnl_pct:null, hold_days:null,
                        bench_pct:null, sharpe:null, entry_rule:null}]},
      watchlist:{items:[{symbol:'Y', score:null, price:null, rule_id:null, progress_pct:null, gap:null}]},
      trades:{items:[{side:'buy', symbol:'Z', shares:null, price:null, rule_id:null, rationale:null}]},
      versions:[{label:null, date:null, change:null}],
      lessons:[{date:null, title:null, kind:'loss', what:null, landed:null}]
    }
    var H_FULL = render(FULL)
    var H_EMPTY = render(EMPTY)
    // 后端接口整个不存在(404)时传的就是这个 —— 页面必须照常出骨架
    var H_SKEL = render({}, NOTICE.dev)
  `, ctx, { filename: 'assert-agent' })

  const H = ctx.H_FULL
  const E = ctx.H_EMPTY
  const S = ctx.H_SKEL

  // ① 区块顺序(用户 2026-09-09 指定:规则紧跟净值曲线,成长总结压最后)
  const at = (s) => H.indexOf(s)
  const order = [
    ['当前策略在最上', at('当前基于'), at('净值 vs 基准')],
    ['净值曲线在规则之前', at('净值 vs 基准'), at('当前生效的规则')],
    ['规则在持仓之前', at('当前生效的规则'), at('持仓明细')],
    ['持仓在操作报告之前', at('持仓明细'), at('今日操作报告')],
    ['操作报告在策略演进之前', at('今日操作报告'), at('策略演进')],
    ['成长总结排最后', at('策略演进'), at('每日成长总结')],
  ]
  for (const [name, a, b] of order) {
    if (a >= 0 && b >= 0 && a < b) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name, `(${a} → ${b})`) }
  }

  // ② 关键内容真的渲染出来了
  const need = [
    ['触发的规则挂在交易上', /class="ag-rule"[^>]*>R-03</],
    ['净值曲线画出了 svg', /<svg[^>]*viewBox="0 0 720 250"/],
    ['换版竖线', /stroke-dasharray="3 3"/],
    ['教训带落地状态', /已落地 · R-03 止损/],
    ['未落地的教训也标出来', /暂不落地 · 样本 4\/15/],
    ['样本不足的个股 Sharpe 显示 —', /class="n ag-na" title="持有 9 日,样本不足">—</],
    ['被护栏否决的候选', /已否决/],
  ]
  for (const [name, re] of need) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }

  // ③ 铁律:字段全空时不许出现假数字
  const banned = [['NaN', /NaN/], ['undefined', /undefined/], ['凭空的 null 字面量', />null</]]
  for (const [name, re] of banned) {
    if (!re.test(E)) console.log('PASS 智能体 · 空数据不出现', name)
    else { failed++; console.log('FAIL 智能体 · 空数据里出现了', name) }
  }
  if ((E.match(/—/g) || []).length >= 20) console.log('PASS 智能体 · 空字段落到 —')
  else { failed++; console.log('FAIL 智能体 · 空字段没落到 —,只有', (E.match(/—/g) || []).length, '处') }

  // ④ 后端整个不存在时,骨架照常渲染 —— 不许整页换成一张说明卡。
  //   用户 2026-09-09 明确要求:「把没获取到后端数据的地方都用 — 表示」。
  //   整页替换掉的话,连这一页长什么样都看不见,而「这页会展示什么」本身就是信息。
  const skel = ['当前基于', '护栏', '净值 vs 基准', '当前生效的规则', '持仓明细',
    '今日观察列表', '今日操作报告', '策略演进', '每日成长总结']
  const lost = skel.filter(s => S.indexOf(s) < 0)
  if (!lost.length) console.log('PASS 智能体 · 后端 404 时九个区块骨架都在')
  else { failed++; console.log('FAIL 智能体 · 后端 404 时丢了区块:', lost.join(' / ')) }
  for (const [name, re] of banned) {
    if (!re.test(S)) console.log('PASS 智能体 · 骨架不出现', name)
    else { failed++; console.log('FAIL 智能体 · 骨架里出现了', name) }
  }
  // 买卖方向 / 教训类型缺失时不许猜 —— 猜错比留空严重
  if (!/>卖出</.test(S) && !/>买入</.test(S)) console.log('PASS 智能体 · 骨架不猜买卖方向')
  else { failed++; console.log('FAIL 智能体 · 骨架凭空写了买卖方向') }
  if (!/亏损教训|验证通过/.test(S)) console.log('PASS 智能体 · 骨架不猜教训类型')
  else { failed++; console.log('FAIL 智能体 · 骨架凭空写了教训类型') }
  // 护栏尤其不能猜:用户会据此判断风险敞口
  if (!/只做多|可做空/.test(S)) console.log('PASS 智能体 · 骨架不猜交易方向')
  else { failed++; console.log('FAIL 智能体 · 骨架凭空写了交易方向') }
  if (/只做多 · 不加杠杆/.test(H)) console.log('PASS 智能体 · 有数据时如实写交易方向')
  else { failed++; console.log('FAIL 智能体 · long_only=true 没渲染出来') }
  if (/ag-banner dev/.test(S)) console.log('PASS 智能体 · 骨架顶上说明了为什么全是 —')
  else { failed++; console.log('FAIL 智能体 · 骨架没有提示条,用户不知道为什么全是 —') }
} catch (e) {
  failed++
  console.log('FAIL 智能体定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

console.log(failed ? `SOME FAILED (${failed})` : 'ALL OK')
process.exit(failed ? 1 : 0)
