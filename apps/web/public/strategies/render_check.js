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
  : ['index.html', 'factors.html', 'workbench.html', 'backtest.html', 'data.html',
     'agent.html', 'screener.html']

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
    focus() {}, click() {}, scrollIntoView() {}, select() {}, setSelectionRange() {},
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

// fetch 一律 reject,有的页面(data.html 的 loadOverview)不 catch。以前收尾是同步 process.exit,
// 这些 rejection 来不及冒出来;改成 setImmediate 收尾之后 node 20 会把它当未处理异常直接崩。
// 它们不是要抓的错(页面拿不到数据本来就该静默),所以吞掉。
process.on('unhandledRejection', () => {})

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

// ─── 美股池(2026-09-11)────────────────────────────────────────
// 美股策略对沪深300 算超额 = 数字照出、毫无意义。基准选项必须跟着股票池走
try {
  const ctx = vm.createContext(makeContext('workbench.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  inlineScripts(fs.readFileSync(path.join(DIR, 'workbench.html'), 'utf8'))
    .forEach((src, i) => vm.runInContext(src, ctx, { filename: `workbench#${i + 1}` }))
  vm.runInContext(`
    var US_OPTS = benchOptions({ universe: 'us_all', benchmark: '.INX' })
    var A_OPTS = benchOptions({ universe: 'hs300', benchmark: '000300' })
    draft.config = { universe: 'us_all', top_n: 20, rebalance: 'M', cost_bps: 5, benchmark: '.INX' }
    var US_PANEL = renderMiddlePanel()
    var MKT = [isUsCode('AAPL'), isUsCode('BRK.A'), isUsCode('600519'), isUsCode('.INX')]
    var NAMES_OK = !!UNIVERSE_NAME.us_all && BENCHMARK_NAME['.INX'] === '标普 500'
  `, ctx, { filename: 'assert-us' })
  // ↑ app.js 里的 const 不会挂到 vm 的全局对象上,只能在脚本里面取值再用 var 带出来
  const checks = [
    ['美股池的基准只有标普500', /\.INX/.test(ctx.US_OPTS) && !/000300/.test(ctx.US_OPTS)],
    ['A 股池的基准里没有标普500', /000300/.test(ctx.A_OPTS) && !/\.INX/.test(ctx.A_OPTS)],
    ['美股池配置面板能渲染、选中美股池', /value="us_all"[^>]*selected/.test(ctx.US_PANEL)],
    ['美股池默认 5 bps 被选中', /value="5" selected/.test(ctx.US_PANEL)],
    ['代码判市场与后端同口径', JSON.stringify(ctx.MKT) === '[true,true,false,true]'],
    ['名称表有美股池与标普500', ctx.NAMES_OK === true],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 美股池 ·', name)
    else { failed++; console.log('FAIL 美股池 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 美股池 · 断言脚本本身出错 ·', e && e.stack ? e.stack.split('\n')[0] : e)
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
//   ① 区块顺序 —— 净值曲线 → 规则 → 持仓 → 历史交易记录 → 演进 → 成长总结
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
      // 故意乱序 + 混进「没评分」和「被否决」两种边界,用来测排序(见 ⑤)
      watchlist:{items:[
        {symbol:'IFF', score:70, price:83.61, rule_id:'R-01', progress_pct:50, gap:'还在枢轴下方 5.7%'},
        {symbol:'SMCI', score:90, price:41.88, blocked:true, blocked_reason:'波动率 68% 超风险预算'},
        {symbol:'NOSC', price:12.5, rule_id:'R-01', progress_pct:30, gap:'评分还没算出来'},
        {symbol:'MU', score:92, price:142.3, rule_id:'R-02', progress_pct:82, gap:'距 20 日高还差 0.8%'},
        {symbol:'ZD', score:87, price:56.22, rule_id:'R-01', progress_pct:50, gap:'还差 C-01 收盘'},
        {symbol:'VOYA', score:83, price:103.41, rule_id:'R-01', progress_pct:50, gap:'还在枢轴下方 2.1%'},
        {symbol:'FILL', price:9.9, filler:true, rs_pct:99.9, rs_raw_pct:412,
         gap:'不是今天的候选 —— 它没过任何一条买入规则'}], matched:6, filled:1},
      // 历史交易记录:第一笔是加过两次仓的(3 腿),第二笔是干净的一买一卖
      history:{fee_note:'手续费按阶梯式(当月累计 ≤30 万股 0.0035 美元/股…)买卖各收一次',
        scope_note:'这一列只进这张表 —— 上面的总览、净值曲线、胜率仍是引擎的零费用口径',
        fee_total:1.98, pnl_gross_total:149.14, pnl_net_total:147.16, items:[
        {no:19, symbol:'ARMK', name:'ARMK', side:'long', entry_date:'2026-08-11', exit_date:'2026-08-25',
         shares:232, amount:14134.64, pnl_abs:-265.34, pnl_pct:-1.88, pnl_gross:-263.36, fee:1.9775,
         scan_dates:['2026-08-06','2026-08-07','2026-08-10'],
         hold_days:10, adds:2, legs:[
           {kind:'entry', date:'2026-08-11', rule_id:'R-04', rule_name:'VCP 突破买入', price:60.47, shares:133,
            rationale:'收盘 $60.47 突破前 20 日高点 $59.90 1.0%(<5%,未追高);量能 2.1 倍 50 日均量'},
           {kind:'entry', date:'2026-08-13', rule_id:'R-16', rule_name:'倒三角加仓', price:61.11, shares:66},
           {kind:'exit', date:'2026-08-25', rule_id:'R-15', rule_name:'10 日不涨清仓', price:59.08,
            shares:232, pnl_abs:-263.36, pnl_pct:-1.86,
            rationale:'持有 10 个交易日仍未涨过 1R,按 R-15 清仓;当日量能 0.7 倍 20 日均量',
            followup:'卖出后 T+5 该股再跌 3.2% —— 这次是躲过了'}]},
        {no:18, symbol:'GS', name:'GS', side:'long', entry_date:'2026-07-02', exit_date:'2026-07-20',
         shares:8, amount:7806.88, pnl_abs:411.8, pnl_pct:5.27, pnl_gross:412.5, fee:0.7,
         hold_days:13, adds:0, legs:[
           {kind:'entry', date:'2026-07-02', rule_id:'R-04', rule_name:'VCP 突破买入', price:975.86, shares:8},
           {kind:'exit', date:'2026-07-20', rule_id:'R-13', rule_name:'移动止盈', price:1027.42,
            shares:8, pnl_abs:412.5, pnl_pct:5.28}]}]},
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
      history:{items:[{no:null, symbol:null, side:null, shares:null, amount:null, pnl_abs:null,
                       pnl_pct:null, hold_days:null, fee:null, pnl_gross:null,
                       legs:[{kind:'entry', date:null, rule_id:null, rule_name:null, price:null, shares:null}]}]},
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
    // 扫描筛选夹在净值曲线与规则之间(用户 2026-09-10 指定)。
    // 位置变了就不再是那个意思了,所以钉住。
    ['净值曲线在扫描筛选之前', at('净值 vs 基准'), at('扫描筛选 · 找候选票')],
    ['扫描筛选在规则之前', at('扫描筛选 · 找候选票'), at('当前生效的规则')],
    ['规则在持仓之前', at('当前生效的规则'), at('持仓明细')],
    ['持仓在历史交易记录之前', at('持仓明细'), at('历史交易记录')],
    // 「今日操作报告」2026-09-12 撤掉了(历史逐笔记录覆盖了它,还多了已平仓的盈亏),
    // 它独有的买卖理由搬到了历史记录的信号列 hover 上 —— 见下面的断言
    ['历史交易记录在策略演进之前', at('历史交易记录'), at('策略演进')],
    ['成长总结排最后', at('策略演进'), at('每日成长总结')],
  ]
  for (const [name, a, b] of order) {
    if (a >= 0 && b >= 0 && a < b) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name, `(${a} → ${b})`) }
  }

  // ② 关键内容真的渲染出来了
  const need = [
    ['触发的规则挂在成交腿上', /class="ag-rule"[^>]*>R-15</],
    ['净值曲线画出了 svg', /<svg[^>]*viewBox="0 0 720 250"/],
    ['换版竖线', /stroke-dasharray="3 3"/],
    ['教训带落地状态', /已落地 · R-03 止损/],
    ['未落地的教训也标出来', /暂不落地 · 样本 4\/15/],
    ['样本不足的个股 Sharpe 显示 —', /class="n ag-na" title="持有 9 日,样本不足">—</],
    ['被护栏否决的候选', /已否决/],
    // 扫描筛选的三条边界必须写在入口上,不许只留在文档里 ——
    // 用户是从这里点进去的,风险提示放在别处等于没提示
    ['扫描入口标了延迟', /延迟 15 分钟/],
    ['扫描入口标了不能回测', /不能直接拿去回测/],
    ['扫描入口标了没有历史', /只有当前快照、没有历史/],
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
  const skel = ['当前基于', '护栏', '净值 vs 基准', '当前生效的规则', '持仓明细', '历史交易记录',
    '今日观察列表', '策略演进', '每日成长总结']
  const lost = skel.filter(s => S.indexOf(s) < 0)
  if (!lost.length) console.log('PASS 智能体 · 后端 404 时九个区块骨架都在')
  else { failed++; console.log('FAIL 智能体 · 后端 404 时丢了区块:', lost.join(' / ')) }
  for (const [name, re] of banned) {
    if (!re.test(S)) console.log('PASS 智能体 · 骨架不出现', name)
    else { failed++; console.log('FAIL 智能体 · 骨架里出现了', name) }
  }
  // 买卖方向 / 教训类型缺失时不许猜 —— 猜错比留空严重
  if (!/>入场</.test(S) && !/>出场</.test(S) && !/>做多</.test(S)) console.log('PASS 智能体 · 骨架不猜买卖方向')
  else { failed++; console.log('FAIL 智能体 · 骨架凭空写了买卖方向') }
  if (!/亏损教训|验证通过/.test(S)) console.log('PASS 智能体 · 骨架不猜教训类型')
  else { failed++; console.log('FAIL 智能体 · 骨架凭空写了教训类型') }
  // ⑤ 观察列表排序(用户 2026-09-12 要求):评分高的在前,没评分的殿后,blocked 一律最后。
  //   列表只露 5 张卡片、其余滚动,所以排序直接决定了用户先看到谁 ——
  //   排错等于把最好的候选藏进滚动区里,比排版难看严重得多,必须钉死。
  const WL = (H.match(/class="sy">([A-Z]+)</g) || []).map(s => s.replace(/[^A-Z]/g, ''))
  const WANT = ['MU', 'ZD', 'VOYA', 'IFF', 'NOSC', 'SMCI', 'FILL']
  if (WL.join(',') === WANT.join(',')) console.log('PASS 智能体 · 观察列表按评分降序、缺分殿后、否决最末')
  else { failed++; console.log('FAIL 智能体 · 观察列表顺序不对:', WL.join(',') , '应为', WANT.join(',')) }
  // 限高靠 JS 量完再设(fitWatch 按 id 找容器),id 丢了就退化成一条长列表,且不会报错 —— 只能靠断言
  if (/class="ag-wl" id="ag-watch"/.test(H)) console.log('PASS 智能体 · 观察列表容器带 id(限高脚本靠它)')
  else { failed++; console.log('FAIL 智能体 · 观察列表容器丢了 id=ag-watch,限高会失效') }
  if (/按评分排序/.test(H)) console.log('PASS 智能体 · 表头写明了排序口径')
  else { failed++; console.log('FAIL 智能体 · 表头没写排序口径') }
  // ⑥ RS 补位(用户 2026-09-12):观察列表凑不满 5 条时补几只 RS 最强的进来。
  //   这些票**没过任何一条买入规则**,一旦长得跟真候选一样,用户就会以为智能体在等它们 ——
  //   那是用排版编造了一个不存在的结论。所以补位卡片里不许出现评分 / 进度条 / 「距 R-xx 触发」。
  const fi = H.indexOf('class="ag-w fill"')
  const seg = fi < 0 ? '' : H.slice(fi, H.indexOf('</div></div>', fi) + 12)
  if (seg) console.log('PASS 智能体 · 补位卡片渲染出来了')
  else { failed++; console.log('FAIL 智能体 · 补位卡片没渲染') }
  const dirty = [['评分', /评分/], ['进度条', /ag-gap/], ['触发进度', /触发/]]
    .filter(([, re]) => re.test(seg)).map(([n]) => n)
  if (seg && !dirty.length) console.log('PASS 智能体 · 补位卡片不冒充候选(无评分 / 进度条 / 触发进度)')
  else if (seg) { failed++; console.log('FAIL 智能体 · 补位卡片里出现了', dirty.join(' / ')) }
  if (/另补 1 只 RS 最强/.test(H)) console.log('PASS 智能体 · 表头把补位的只数单独报出来')
  else { failed++; console.log('FAIL 智能体 · 表头没把补位只数和真候选分开报') }
  // ⑦ 历史交易记录:一个持仓周期一组。加仓过的票有 3 腿,编号 / 大小 / 盈亏必须 rowspan 跨整组 ——
  //   平铺成 3 条独立记录的话,「这一笔最后是赚是亏」就看不出来了。
  if (/rowspan="3"/.test(H)) console.log('PASS 智能体 · 加仓周期的编号与盈亏跨整组(rowspan=3)')
  else { failed++; console.log('FAIL 智能体 · 加仓周期没合并,退化成逐笔平铺') }
  if (/rowspan="2"/.test(H)) console.log('PASS 智能体 · 一买一卖的周期是 2 腿')
  else { failed++; console.log('FAIL 智能体 · 一买一卖的周期腿数不对') }
  const need2 = [['净损益按笔算(扣费后)', /-\$265/], ['回报按笔算(扣费后)', /-1\.88%/],
    ['出场规则挂在腿上', /R-15</],
    ['笔数与总览口径的差异写出来了', /总览按<b>每次卖出<\/b>计数/]]
  for (const [name, re] of need2) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }
  if (/class="sg why" title="[^"]{10,}"/.test(H)) console.log('PASS 智能体 · 买卖理由跟着搬到了信号列的 hover 上')
  else { failed++; console.log('FAIL 智能体 · 「今日操作报告」撤掉后买卖理由丢了') }
  // 手续费 2026-09-12 从「没有这个字段」变成「按阶梯算」,断言跟着反过来:
  //   必须有这一列,净损益必须是扣完费的,且扣费前的数要能 hover 到 ——
  //   只给净的,用户拿它和上面总览(引擎零费用口径)对不上,会以为哪边算错了
  const feeNeed = [['有手续费列', /<th[^>]*>手续费/], ['有代号列', /<th[^>]*>代号/],
    ['代号填的是股票代码', /class="sym"[^>]*><b>ARMK</],
    ['净损益是扣费后的数', /-\$265/], ['扣费前的数在 hover 里', /title="扣费前 -\$263[^"]*手续费 1\.98/],
    ['脚注说明了这一列不进净值曲线', /仍是引擎的零费用口径/],
    ['脚注给出两个口径的差额', /一共扣了 <b>\$2<\/b>/]]
  for (const [name, re] of feeNeed) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }
  if (/class="sg/.test(H)) console.log('PASS 智能体 · 信号列带收窄样式')
  else { failed++; console.log('FAIL 智能体 · 信号列没有收窄样式') }
  // ⑧ 悬停日K(用户 2026-09-12):代号上挂钩子 + 三种标记的日期。
  //   日期是从成交与扫描记录里来的,不是前端推的 —— 钩子掉了不会报错、页面照常渲染,
  //   只有断言能发现「悬停没反应了」。
  const kNeed = [['代号挂了日K 钩子', /class="sym" rowspan="3" data-kchart="ARMK"/],
    ['标记里有扫描命中日', /&quot;scan&quot;:\[&quot;2026-08-06&quot;/],
    ['标记里有买入日', /&quot;buy&quot;:\[&quot;2026-08-11&quot;,&quot;2026-08-13&quot;\]/],
    ['标记里有卖出日', /&quot;sell&quot;:\[&quot;2026-08-25&quot;\]/],
    ['脚注说明了三种标记各是什么', /蓝色竖带<\/span>是扫描筛选命中的那些天/]]
  for (const [name, re] of kNeed) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }
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

// ─── 小鹿智能体 · 迭代方向(2026-09-12)────────────────────────────────
// 后端把 dashboard 扩成 ?branch=<key>,顶层多 branch / branches[]。要钉住的:
//   ① 有 branches 时渲染出与数组等长的卡、active 的带高亮 class、
//      区块夹在状态条(.ag-status)之后、「当前基于」(.ag-strat)之前;
//   ② 没有 branches / 空数组 / 后端 404 骨架 → 这一块不出现(旧契约不变);
//   ③ 全 null 的方向卡不出现 NaN / undefined / null 字面量;
//   ④ 切换写 hash、重拉带 ?branch=;刷新 / 重试按钮传 Event 进 boot 时按 hash 恢复;首次无 hash 不带参数。
// 上面那组 agent.html 断言一条都没动 —— 这组只加不改。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const ag = inlineScripts(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))
  ag.forEach((src, i) => vm.runInContext(src, ctx, { filename: `agent#${i + 1}` }))

  vm.runInContext(`
    var BR_BASE = { state:'running', paper:true, version:'v3', day_count:25, iteration_count:3,
      strategy:{ name:'VCP 波段交易', version:'v3', summary:'x', market_label:'美股' },
      guardrails:{ initial_capital:100000, long_only:true, triggered_today:false },
      overview:{ pnl_pct:1.2 }, nav:{ points:[] }, rules:[], holdings:{ items:[] },
      watchlist:{ items:[] }, trades:{ items:[] }, versions:[], lessons:[] }
    var BR_LIST = [
      { key:'base', label:'基准 v1', direction:'规则固定,不优化', version:'v1',
        pnl_pct:-0.26, benchmark_pct:-1.3, excess_pt:1.03, trades_total:2, win_rate:0, max_dd_pct:-0.6, active:false },
      { key:'buy', label:'方向 A · 调买入', direction:'固定卖出规则,只优化买入时机', version:'v3',
        pnl_pct:1.2, benchmark_pct:-1.3, excess_pt:2.5, trades_total:9, win_rate:44.4, max_dd_pct:-1.1, active:true },
      { key:'sell', label:'方向 B · 调卖出', direction:'固定买入时机,只优化卖出时机', version:'v2',
        pnl_pct:null, benchmark_pct:null, excess_pt:null, trades_total:0, win_rate:null, max_dd_pct:null, active:false },
    ]
    var H_BR = render({ state:'running', paper:true, version:'v3', day_count:25, iteration_count:3,
      strategy:BR_BASE.strategy, guardrails:BR_BASE.guardrails, overview:BR_BASE.overview, nav:BR_BASE.nav,
      rules:[], holdings:BR_BASE.holdings, watchlist:BR_BASE.watchlist, trades:BR_BASE.trades,
      versions:[], lessons:[], branch:'buy', branches:BR_LIST })
    var H_NOBR = render(BR_BASE)
    var H_EMPTYBR = render({ state:'running', strategy:BR_BASE.strategy, branch:'buy', branches:[] })
    // 全 null 的方向(刚开的分支,一笔都没跑)
    var H_NULLBR = render({ state:'running', strategy:BR_BASE.strategy, branch:'x', branches:[
      { key:'x', label:null, direction:null, version:null, pnl_pct:null, benchmark_pct:null, excess_pt:null,
        trades_total:null, win_rate:null, max_dd_pct:null, active:null } ] })
    var H_SKELBR = render({}, NOTICE.dev)
  `, ctx, { filename: 'assert-branches' })

  const H = ctx.H_BR
  const cardCount = (H.match(/class="ag-brc/g) || []).length
  const onCount = (H.match(/class="ag-brc on"/g) || []).length
  const iStatus = H.indexOf('class="ag-status"')
  const iBr = H.indexOf('class="ag-br"')
  const iStrat = H.indexOf('class="ag-strat"')
  const banned = [['NaN', /NaN/], ['undefined', /undefined/], ['凭空的 null 字面量', />null</]]
  const checks = [
    ['卡片数与 branches 等长', cardCount === 3],
    ['只有 active 的那张高亮,且是 buy', onCount === 1 && /class="ag-brc on"[^>]*data-branch="buy"/.test(H)],
    ['区块在状态条之后', iStatus >= 0 && iBr >= 0 && iStatus < iBr],
    ['区块在「当前基于」之前', iBr >= 0 && iStrat >= 0 && iBr < iStrat],
    ['卡片是 button、带 data-branch(常驻可见,不藏 hover)', /<button[^>]*class="ag-brc[^"]*"[^>]*data-branch="sell"/.test(H)],
    ['卡片写了 label / direction / version', /方向 A · 调买入/.test(H) && /只优化买入时机/.test(H) && /class="ver">v3</.test(H)],
    ['总收益按红涨绿跌着色', /<b class="pos">\+1\.20%<\/b>/.test(H) && /<b class="neg">-0\.26%<\/b>/.test(H)],
    ['对比基准以 pt 计', /\+2\.50 pt/.test(H) && /\+1\.03 pt/.test(H)],
    ['交易笔数与胜率', /9 笔 · 胜率 44\.4%/.test(H)],
    ['最大回撤', /<b class="neg">-1\.10%<\/b>/.test(H)],
    ['0 笔但胜率算不出 → 胜率 —(不是 0%)', /0 笔 · 胜率 <span class="ag-na">—<\/span>/.test(H)],
    ['没有 branches 字段时这一块不出现', !/ag-br"|ag-brc/.test(ctx.H_NOBR)],
    ['branches 为空数组时这一块不出现', !/ag-br"|ag-brc/.test(ctx.H_EMPTYBR)],
    ['后端 404 骨架里也没有这一块', !/ag-br"|ag-brc/.test(ctx.H_SKELBR)],
    ['全 null 的方向卡照样画出来', (ctx.H_NULLBR.match(/class="ag-brc/g) || []).length === 1],
    ['全 null 的方向卡落到 —', (ctx.H_NULLBR.match(/—/g) || []).length >= 6],
  ]
  for (const [name, re] of banned) {
    checks.push(['全 null 的方向卡不出现 ' + name, !re.test(ctx.H_NULLBR)])
  }

  // ④ 切换与 hash:换掉 fetch 抓 URL(fetch 在 boot 的第一段同步代码里就被调用,不用等 await)
  vm.runInContext(`
    var SEEN = []
    fetch = function (url) { SEEN.push(String(url)); return Promise.reject(new Error('render_check: 不联网')) }
    boot()                       // 首次加载:没有 hash
    switchBranch('sell')         // 点卡片
    boot({ type: 'click' })      // 刷新 / 重试按钮把 Event 传进来
    var HASH = location.hash
  `, ctx, { filename: 'assert-branch-switch' })
  const seen = ctx.SEEN
  checks.push(
    ['首次加载没有 hash 时不带 branch 参数', seen[0] === '/api/quant/agent/dashboard'],
    ['点卡片后 hash 写成 #branch=sell', ctx.HASH === '#branch=sell'],
    ['点卡片后重拉带 ?branch=sell', seen[1] === '/api/quant/agent/dashboard?branch=sell'],
    ['刷新按钮传 Event 进来时按 hash 恢复', seen[2] === '/api/quant/agent/dashboard?branch=sell'],
  )
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 迭代方向 ·', name)
    else { failed++; console.log('FAIL 迭代方向 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 迭代方向定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 登录态续期(app.js)─────────────────────────────────────────────
// 策略中心这几页不经过主站 AuthGuard,续期全靠 app.js 自己。
// 这里只验两个纯函数:判过期、换请求头。真正的续期流程要真浏览器 + 真 token 才测得了。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  ctx.atob = (s) => Buffer.from(s, 'base64').toString('binary')
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  const now = Math.floor(Date.now() / 1000)
  ctx.T_OLD  = 'h.' + b64({ sub: 'u', exp: now - 10 }) + '.s'
  ctx.T_SOON = 'h.' + b64({ sub: 'u', exp: now + 30 }) + '.s'
  ctx.T_NEW  = 'h.' + b64({ sub: 'u', exp: now + 3600, email: '中文邮箱@例子' }) + '.s'
  vm.runInContext(`
    var R_OLD = tokenStale(T_OLD), R_SOON = tokenStale(T_SOON), R_NEW = tokenStale(T_NEW)
    var R_BAD = tokenStale('not-a-jwt')
    var H1 = withAuth({ headers: { 'Content-Type': 'application/json', 'authorization': 'Bearer OLD' } }, 'NEW')
    var H2 = withAuth({ method: 'POST', body: '{}' }, 'NEW')
    var H3 = withAuth({ headers: [['authorization', 'Bearer OLD'], ['X-A', '1']] }, 'NEW')
  `, ctx, { filename: 'assert-auth' })
  const keysAuth = (o) => Object.keys(o).filter(k => k.toLowerCase() === 'authorization')
  const checks = [
    ['已过期的 token 判为要续', ctx.R_OLD === true],
    ['60 秒内过期的也提前续', ctx.R_SOON === true],
    ['还有 1 小时的不续(payload 里有中文也能解)', ctx.R_NEW === false],
    ['解不出的 token 不瞎续(交给 401 兜底)', ctx.R_BAD === false],
    ['换头:旧的小写 authorization 被替换,不是并存', keysAuth(ctx.H1.headers).length === 1 && ctx.H1.headers.Authorization === 'Bearer NEW'],
    ['换头:其它头保留', ctx.H1.headers['Content-Type'] === 'application/json'],
    ['换头:原来没头也能加上,method/body 保留', ctx.H2.headers.Authorization === 'Bearer NEW' && ctx.H2.method === 'POST' && ctx.H2.body === '{}'],
    ['换头:数组写法也能换', ctx.H3.headers.filter(p => p[0].toLowerCase() === 'authorization').length === 1 && ctx.H3.headers.some(p => p[0] === 'X-A')],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 续期 ·', name)
    else { failed++; console.log('FAIL 续期 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 续期定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 对照表命中:「忘掉它」必须在折叠区外面、常驻可见 ────────────────
// 对照表全站共享、AI 学来的。学错一条所有人反复用到,纠错入口藏进折叠区等于没有。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
  sc.forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    S.kw = { source_text: 'x', notes: [], matched: [
      { text: '成交量大于100万', expr: 'volume > 1000000' },
      { text: '市盈率大于10小于20', expr: 'price_earnings_ttm > 10', learned: { id: 7, key: 'k' } },
      { text: '市盈率大于10小于20', expr: 'price_earnings_ttm < 20', learned: { id: 7, key: 'k' } },
    ] }
    var KWN = vKwNote()
  `, ctx, { filename: 'assert-learned' })
  const h = ctx.KWN
  const fold = h.indexOf('<details')
  const btn = h.indexOf('data-forget="7"')
  const checks = [
    ['「忘掉它」在折叠区外面(排在 <details> 前)', btn >= 0 && fold >= 0 && btn < fold],
    ['一条对照表记录展开成两个条件,只出一个按钮', (h.match(/data-forget="7"/g) || []).length === 1],
    ['规则识别的句子没有「忘掉它」', !/data-forget="[^7]/.test(h)],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 对照表 ·', name)
    else { failed++; console.log('FAIL 对照表 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 对照表定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 系统示例可以对自己隐藏:✕ 常驻、隐藏后直接不显示 ─────────────────
// 示例是全站共用的,只能对自己隐藏。入口不能藏进 hover(没人发现)。
// 不留恢复入口是用户 2026-09-11 的明确决定(看了「已隐藏 N 个 · 恢复」之后要求去掉)。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
  sc.forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    S.meta = Object.assign({}, S.meta || {}, { presets: [
      { key: 'uptrend', name: '上升趋势', desc: 'd1', script: 'x' },
      { key: 'vcp',     name: 'VCP 波动收缩', desc: 'd2', script: 'y' },
      { key: 'hk_div',  name: '港股高股息', desc: 'd3', script: 'z' },
    ] })
    var ALL = vPresetChips()
    setHiddenPresets(['vcp', 'gone_key'])        // gone_key:后端已删掉的示例,不能让计数虚高
    var SOME = vPresetChips()
    setHiddenPresets(['uptrend', 'vcp', 'hk_div'])
    var NONE = vPresetChips()
    localStorage.setItem(LS_HIDE, '{坏掉的 json')
    var BROKEN = vPresetChips()
    setHiddenPresets([])
    var STORED = localStorage.getItem(LS_HIDE)
  `, ctx, { filename: 'assert-hide-preset' })
  const checks = [
    ['每个示例都有常驻的隐藏 ✕', (ctx.ALL.match(/data-hide="/g) || []).length === 3],
    ['✕ 不靠 hover 出现(不写 opacity:0 / display:none)',
      !/\.mg-sys \.hide\{[^}]*(opacity:\s*0[;}]|display:\s*none)/.test(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))],
    ['隐藏的那个不再渲染', !/data-preset="vcp"/.test(ctx.SOME) && /data-preset="uptrend"/.test(ctx.SOME)],
    ['⭐隐藏后不留任何痕迹(不显示「已隐藏 N 个」)', !/已隐藏|恢复|data-restore/.test(ctx.SOME + ctx.NONE)],
    ['全部隐藏时一个示例都不画、也不报错', ctx.NONE === ''],
    ['localStorage 写坏了照常渲染全部示例', (ctx.BROKEN.match(/data-preset="/g) || []).length === 3],
    ['全部恢复后不留空键', ctx.STORED === null],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 隐藏示例 ·', name)
    else { failed++; console.log('FAIL 隐藏示例 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 隐藏示例定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 时间回溯:入口常驻、退出常驻、请求真的带了日期、结果标明是哪一天 ──────
// 最怕的是"回溯了却不知道自己在回溯":旧结果留着、K 线露出之后的走势、请求没带日期。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
  sc.forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    S.conditions = [{ name: 'c1', expr: 'close > 20', is_bool: true, enabled: true }]
    S.combine = 'all'; S.plotName = 'scan'; S.plotExpr = ''
    var BAR_OFF = vRunBar()
    var SENT = []
    post = async function (url, body) { SENT.push(body); return { ok: true, status: 200, data: { matched: 0, picks: [], as_of: body.as_of } } }
    toast = function () {}
    S.result = { picks: [{ code: 'X' }] }; S.probe = { c1: 3 }
    setAsOf('2026-08-15')
    var BAR_ON = vRunBar()
    var CLEARED = S.result === null && Object.keys(S.probe).length === 0
    var RES_ON = vResult()
  `, ctx, { filename: 'assert-asof-1' })
  // runScan 是 async,跑完再看
  const done = vm.runInContext(`runScan().then(function () { return probeOne(S.conditions[0]) })`, ctx, { filename: 'assert-asof-2' })
  const KC_ROWS = vm.runInContext(`
    (function () {
      // 悬停日 K:回溯时只画到那天。直接调 kcRender 依赖 DOM 太多,这里验证同一条过滤逻辑
      const rows = [{ ts: '2026-08-14' }, { ts: '2026-08-15' }, { ts: '2026-08-18' }]
      return rows.filter(function (r) { return String(r.ts || r.date || '').slice(0, 10) <= S.asOf }).length
    })()
  `, ctx, { filename: 'assert-asof-3' })
  Promise.resolve(done).then(() => {
    const sent = ctx.SENT
    const checks = [
      ['没回溯时运行栏有「时间回溯」按钮', /id="sc-asof"/.test(ctx.BAR_OFF) && !/sc-asof-off/.test(ctx.BAR_OFF)],
      ['回溯后运行栏显示日期,且「回到今天」常驻', /回溯到 <b>2026-08-15<\/b>/.test(ctx.BAR_ON) && /id="sc-asof-off"/.test(ctx.BAR_ON)],
      ['⭐切换回溯日时清掉旧结果与单条测试', ctx.CLEARED === true],
      ['回溯后清了结果,结果区不画', ctx.RES_ON === ''],
      ['⭐运行扫描的请求带 as_of', sent.length >= 1 && sent[0].as_of === '2026-08-15'],
      ['单条测试的请求也带 as_of', sent.length >= 2 && sent[1].as_of === '2026-08-15'],
      ['结果区标明回溯到哪一天', (() => {
        vm.runInContext(`S.result = { as_of: '2026-08-15', universe_total: 4000, scanned: 4000, matched: 1, skipped_incomplete: 0, picks: [], columns: [], notes: [], warnings: [] }; var RES2 = vResult()`, ctx)
        return /回溯扫描结果/.test(ctx.RES2) && /截至 2026-08-15 收盘/.test(ctx.RES2) && /日线池/.test(ctx.RES2) && /panel asof/.test(ctx.RES2)
      })()],
      ['悬停日 K 只保留回溯日及之前', KC_ROWS === 2],
      ['回到今天后按钮恢复、请求不带 as_of', (() => {
        vm.runInContext(`setAsOf(null); var BAR_BACK = vRunBar()`, ctx)
        return /时间回溯<\/button>/.test(ctx.BAR_BACK) && vm.runInContext('S.asOf', ctx) === null
      })()],
      ['⭐回溯日不进草稿(刷新回到今天)', (() => {
        vm.runInContext(`S.asOf = '2026-08-15'; saveDraft(); var DRAFT = localStorage.getItem(LS) || ''`, ctx)
        return !/2026-08-15/.test(ctx.DRAFT)
      })()],
    ]
    for (const [name, ok] of checks) {
      if (ok) console.log('PASS 时间回溯 ·', name)
      else { failed++; console.log('FAIL 时间回溯 ·', name) }
    }
  }).catch(e => {
    failed++
    console.log('FAIL 时间回溯定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
  })
} catch (e) {
  failed++
  console.log('FAIL 时间回溯定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 保存的扫描策略:开关状态必须能往返 ─────────────────────────────
// 这个功能最容易悄悄写错的地方:保存时如果用了 buildScript(true)(草稿用的那个),
// 停用的条件也会被写进 plot —— 页面一切正常、保存也成功,但加载回来
// 用户关掉的开关全亮了。没有任何报错,只有用户会发现。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
  sc.forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))

  vm.runInContext(`
    S.combine = 'all'; S.plotName = 'scan'; S.plotExpr = ''
    S.conditions = [
      { name: 'avg90',    expr: 'Average(volume, 90)', is_bool: false, enabled: true },
      { name: 'cond_vol', expr: 'avg90 > 1000000',     is_bool: true,  enabled: true },
      { name: 'cond_px',  expr: 'close > 20',          is_bool: true,  enabled: false },
      { name: 'cond_rsi', expr: 'RSI(14) < 70',        is_bool: true,  enabled: true },
    ]
    var SAVE_SCRIPT = buildScript(false)
    S.saved = [{ id: 7, name: '放量突破', market: 'us', script: SAVE_SCRIPT },
               { id: 8, name: '低估值',   market: 'a',  script: SAVE_SCRIPT }]
    S.loadedName = '放量突破'
    var CHIPS = vSavedChips()
  `, ctx, { filename: 'assert-saved' })

  const sv = ctx.SAVE_SCRIPT
  const plot = (sv.match(/plot\s+scan\s*=\s*([^;]+);/) || [])[1] || ''
  const checks = [
    ['停用的条件仍以 def 保存', /def cond_px = close > 20;/.test(sv)],
    ['停用的条件**不进** plot(否则加载回来开关全亮)', !/cond_px/.test(plot)],
    ['启用的条件都进了 plot', /cond_vol/.test(plot) && /cond_rsi/.test(plot)],
    ['中间变量不进 plot', !/avg90/.test(plot)],
    ['每个保存的策略都有常驻的 ✕ 删除', (ctx.CHIPS.match(/data-del="/g) || []).length === 2],
    ['当前加载的那个高亮', /mg-mine on"[^>]*>\s*<button class="nm" data-saved="7"/.test(ctx.CHIPS)],
    ['没加载的那个不高亮', !/mg-mine on"[^>]*>\s*<button class="nm" data-saved="8"/.test(ctx.CHIPS)],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 保存策略 ·', name)
    else { failed++; console.log('FAIL 保存策略 ·', name, '| plot =', plot) }
  }
} catch (e) {
  failed++
  console.log('FAIL 保存策略定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// setImmediate:上面有一组断言挂在 async 函数的 await 链上(微任务),同步退出会跳过它们
setImmediate(() => {
  console.log(failed ? `SOME FAILED (${failed})` : 'ALL OK')
  process.exit(failed ? 1 : 0)
})
