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
        layers:{pool:'进候选池', setup:'形态就绪', setup_note:'P-02~P-05 + P-22 全过', entry:'买入条件全满足'},
        fee_total:1.98, pnl_gross_total:149.14, pnl_net_total:147.16, items:[
        {no:19, symbol:'ARMK', name:'ARMK', side:'long', entry_date:'2026-08-11', exit_date:'2026-08-25',
         shares:232, amount:14134.64, pnl_abs:-265.34, pnl_pct:-1.88, pnl_gross:-263.36, fee:1.9775, grade:'A',
         scan_dates:['2026-08-06','2026-08-07','2026-08-10'], grade:'A',
         setup_dates:['2026-08-07','2026-08-10'], entry_dates:['2026-08-11'],
         hold_days:10, adds:2, legs:[
           {kind:'entry', date:'2026-08-11', rule_id:'R-04', rule_name:'VCP 突破买入', price:60.47, shares:133,
            rationale:'收盘 $60.47 突破前 20 日高点 $59.90 1.0%(<5%,未追高);量能 2.1 倍 50 日均量。'+
              '评分 A 级(340/500):形态 A80、量价配合 C40、抗跌 A80、盈亏比 S100、MACD 金叉 C40 → '+
              '仓位 15% 总资产;止损挂在 $40.45(距收盘 3.9%)'},
           {kind:'entry', date:'2026-08-13', rule_id:'R-16', rule_name:'倒三角加仓', price:61.11, shares:66},
           {kind:'exit', date:'2026-08-25', rule_id:'R-15', rule_name:'10 日不涨清仓', price:59.08,
            shares:232, pnl_abs:-263.36, pnl_pct:-1.86,
            rationale:'持有 10 个交易日仍未涨过 1R,按 R-15 清仓;当日量能 0.7 倍 20 日均量',
            followup:'卖出后 T+5 该股再跌 3.2% —— 这次是躲过了'}]},
        {no:18, symbol:'GS', name:'GS', side:'long', entry_date:'2026-07-02', exit_date:'2026-07-20',
         shares:8, amount:7806.88, pnl_abs:411.8, pnl_pct:5.27, pnl_gross:412.5, fee:0.7, grade:null,
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
    var CSV = buildHistoryCsv(FULL)
  `, ctx, { filename: 'assert-agent' })

  const H = ctx.H_FULL
  const E = ctx.H_EMPTY
  const S = ctx.H_SKEL

  // ① 区块顺序(用户 2026-09-09 指定:规则紧跟净值曲线,成长总结压最后)
  const at = (s) => H.indexOf(s)
  const order = [
    ['当前策略在最上', at('当前基于'), at('净值 vs 基准')],
    ['净值曲线在规则之前', at('净值 vs 基准'), at('当前生效的规则')],
    // 扫描筛选入口卡原来夹在净值曲线与规则之间(2026-09-10),2026-09-14 用户要求去掉 —— 规则直接接净值曲线,
    // 「卡片真的没了」在下面 ② 之后单独断言(顺序断言只能比两个都存在的位置)
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
  ]
  for (const [name, re] of need) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }
  // 扫描筛选入口卡 2026-09-14 去掉了(用户要求),卡上那三条边界提示跟着没了 —— 它们在魔法筛选器页面里照常显示。
  // 这里钉住「真的去掉了」,免得有人按老断言把卡片加回来
  if (H.indexOf('扫描筛选 · 找候选票') < 0 && !/打开扫描筛选/.test(H) &&
      !/直接跑示例/.test(H) && !/screener\.html\?preset=/.test(H)) console.log('PASS 智能体 · 看板没有扫描筛选入口卡与示例链接')
  else { failed++; console.log('FAIL 智能体 · 看板里还有扫描筛选入口卡或示例链接') }

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
    ['进场档位标在代号下方(用户 2026-09-12 要求)', /<b>ARMK<\/b><s class="gd gd-A"[^>]*>A 级<\/s>/],
    ['没评分的方向不画档位、不写占位', /<b>GS<\/b><\/td>/],
    ['净损益是扣费后的数', /-\$265/], ['扣费前的数在 hover 里', /title="扣费前 -\$263[^"]*手续费 1\.98/],
    ['脚注说明了这一列不进净值曲线', /仍是引擎的零费用口径/],
    ['脚注给出两个口径的差额', /一共扣了 <b>\$2<\/b>/]]
  for (const [name, re] of feeNeed) {
    if (re.test(H)) console.log('PASS 智能体 ·', name)
    else { failed++; console.log('FAIL 智能体 ·', name) }
  }
  if (/class="sg/.test(H)) console.log('PASS 智能体 · 信号列带收窄样式')
  else { failed++; console.log('FAIL 智能体 · 信号列没有收窄样式') }
  // ⑦-2 导出 CSV(用户 2026-09-12):前几行要有完整策略 + 规则手册,
  //   表里每条腿要有当时的触发情况与评级过程。buildHistoryCsv 是纯函数(不碰 DOM),
  //   假 DOM 下能直接调 —— 这是这个页面里少数能真跑一遍逻辑的地方,别浪费。
  {
    const csv = ctx.CSV || ''
    const need = [
      ['有导出按钮', () => /id="ag-export"/.test(H)],
      ['抬头写了策略名', () => csv.indexOf('动量突破 + 财报后漂移') >= 0],
      ['抬头写了策略说明', () => csv.indexOf('买强势股的突破') >= 0],
      ['抬头写了护栏', () => csv.indexOf('单票上限') >= 0 && csv.indexOf('# 初始资金,10000') >= 0],
      ['抬头写了规则手册全文', () => csv.indexOf('动量前 10%') >= 0 && csv.indexOf('# R-01') >= 0],
      ['抬头写了口径与手续费', () => csv.indexOf('已扣手续费') >= 0 && csv.indexOf('零费用口径') >= 0],
      ['表头有全部列', () => csv.indexOf('触发情况(当时的数据)') >= 0 && csv.indexOf('评级过程(进场当天)') >= 0],
      ['每条腿都有触发情况', () => csv.indexOf('突破前 20 日高点') >= 0 && csv.indexOf('持有 10 个交易日') >= 0],
      ['评级档位单独成列', () => /,A 级,/.test(csv)],
      ['评级过程切了出来', () => csv.indexOf('评分 A 级(340/500)') >= 0 && csv.indexOf('盈亏比 S100') >= 0],
      // 切分不能把触发条件和评级过程混在一格 —— 混了就等于没切
      // 两段必须落在**相邻的两列**(中间只有一个逗号,两侧的引号有没有取决于该段含不含逗号),
      // 挤在同一格就等于没切
      ['触发条件与评级过程落在相邻两列', () => /均量。"?,"?评分 A 级/.test(csv)],
      ['带逗号的长文本被引号包住', () => /"[^"]*评分 A 级[^"]*"/.test(csv)],
      ['数值列是裸数字', () => /,-265\.34,/.test(csv) && csv.indexOf(',-$265,') < 0],
    ]
    for (const [name, fn] of need) {
      let ok = false
      try { ok = fn() } catch (e) { ok = false }
      if (ok) console.log('PASS 智能体 · 导出 ·', name)
      else { failed++; console.log('FAIL 智能体 · 导出 ·', name) }
    }
  }
  // ⑧ 悬停日K(用户 2026-09-12):代号上挂钩子 + 三种标记的日期。
  //   日期是从成交与扫描记录里来的,不是前端推的 —— 钩子掉了不会报错、页面照常渲染,
  //   只有断言能发现「悬停没反应了」。
  const kNeed = [['代号挂了日K 钩子', /class="sym" rowspan="3" data-kchart="ARMK"/],
    ['标记里有扫描命中日', /&quot;scan&quot;:\[&quot;2026-08-06&quot;/],
    ['标记里有买入日', /&quot;buy&quot;:\[&quot;2026-08-11&quot;,&quot;2026-08-13&quot;\]/],
    ['标记里有卖出日', /&quot;sell&quot;:\[&quot;2026-08-25&quot;\]/],
    // 2026-09-15:蓝线分三层(用户问「为什么每个都显示命中了很多天」—— 原来只有「进候选池」一层)
    ['标记里有形态就绪日', /&quot;setup&quot;:\[&quot;2026-08-07&quot;,&quot;2026-08-10&quot;\]/],
    ['标记里有买入条件全满足日', /&quot;entry&quot;:\[&quot;2026-08-11&quot;\]/],
    ['候选池那层标成淡色(scanWeak)并写明名字', /&quot;scanLabel&quot;:&quot;进候选池&quot;,&quot;scanWeak&quot;:true/],
    ['脚注:淡蓝是候选池、不代表满足买入条件', /淡蓝<\/span>是进了这条线候选池的日子\(池子只做粗筛,天数多是正常的,不代表满足了买入条件\)/],
    ['脚注:中蓝是形态就绪并写出口径', /中蓝<\/b>是形态就绪\(P-02~P-05 \+ P-22 全过\)的日子/],
    ['脚注:深蓝是买入条件全满足', /深蓝<\/b>是买入条件全满足的日子/]]
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
    // 2026-09-13 研究台:没有 hash 时首页是研究台(用户批准的方案),#branch= 的老链接照样打开看板
    ['首次加载没有 hash 时打开研究台', seen[0] === '/api/quant/agent/research'],
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

// ─── 小鹿 · 「启动智能体」与「立即跑一次」同时出现时两个都要能点(2026-09-19 用户报点了没反应)──
// 原来 bindOps 写成 getElementById('ag-run') || getElementById('ag-start'),方向没跑过时两个按钮同屏,
// 只有「立即跑一次」绑上了,提示条里的「启动智能体」是死按钮。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const ag = inlineScripts(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))
  ag.forEach((src, i) => vm.runInContext(src, ctx, { filename: `agent#${i + 1}` }))
  vm.runInContext(`
    var BTN = {}
    ;['ag-run', 'ag-start'].forEach(function (id) {
      BTN[id] = { id: id, textContent: id, disabled: false, style: {}, handlers: [],
        addEventListener: function (t, f) { if (t === 'click') this.handlers.push(f) } }
    })
    var _gidStart = document.getElementById
    document.getElementById = function (id) { return BTN[id] || _gidStart(id) }
    var RUN_URLS = []
    fetch = function (url) { RUN_URLS.push(String(url)); return Promise.reject(new Error('render_check: 不联网')) }
    bindOps()
    var START_BOUND = BTN['ag-start'].handlers.length
    var RUN_BOUND = BTN['ag-run'].handlers.length
    BTN['ag-start'].handlers.forEach(function (f) { f() })
    document.getElementById = _gidStart
  `, ctx, { filename: 'assert-agent-start' })
  const checks = [
    ['两个按钮同屏时「启动智能体」绑上了点击', ctx.START_BOUND === 1],
    ['「立即跑一次」也还绑着', ctx.RUN_BOUND === 1],
    ['点「启动智能体」发 POST /api/quant/agent/run', ctx.RUN_URLS[0] === '/api/quant/agent/run'],
    ['源码里不再有 ag-run || ag-start 这种只绑一个的写法', !/getElementById\('ag-run'\)\s*\|\|/.test(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 启动按钮 ·', name)
    else { failed++; console.log('FAIL 启动按钮 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 启动按钮断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 小鹿智能体 · 研究台(2026-09-13 · docs/agent-research-plan.md)──────────────
// 要钉住的:
//   ① 两列(2026-09-18 起):运行中(待写引擎 / 回测 / 纸上跑都在这里)· 封存;卡片进对的列,淘汰的单独一块;
//   ② 封存卡有「打开看板」「解除封存」;还没引擎的卡写明「还没有引擎」,不画数字格;运行中的卡把冻结后的新数据单列;
//   ③ 对照表只列跑出数据的线,被引用为对照组的那条带「对照组」;净值图每条有净值的线一条 path + 基准一条;
//   ④ 全 null / 后端 404 骨架:四列照画、数据位 —,不出现 NaN / undefined / null 字面量;
//   ⑤ 看板视图顶上只有分页(面包屑 2026-09-14 用户要求去掉),封存的线常驻提示条带「解除封存」(新功能入口不许藏 hover);
//   ⑥ 新建表单:淘汰线在表单里写出来并声明「提交后锁定」。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const ag = inlineScripts(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))
  ag.forEach((src, i) => vm.runInContext(src, ctx, { filename: `agent#${i + 1}` }))
  vm.runInContext(`
    var NAV3 = [0, 1.2, 5.13]
    var RB = {
      common:{ start:'2026-01-02', end:'2026-09-11', days:3, bench_label:'标普500', bench_pct:11.64 },
      kill_text:'纸上跑满 30 笔完整交易后:每笔平均净损益 < 0,或同一段日期收益不如对照组 → 淘汰',
      dates:['2026-01-02','2026-05-01','2026-09-11'], bench_nav:[0, 4.2, 11.64],
      lines:[
        { key:'vcp', label:'VCP 波段线', status:'archived', status_text:'封存', archived_at:'2026-09-13',
          archive_tag:'vcp-archive-v3', archive_reason:'最好的方向 C 全年仍落后标普 500', hypothesis:'收缩后的放量突破会延续',
          branches:[{key:'base',label:'基准 v1',version:'v1'},{key:'c',label:'方向 C · 三段式',version:'v3'}],
          best_branch:'c', best_label:'方向 C · 三段式',
          metrics:{ pnl_pct:5.13, excess_pt:-6.51, max_dd_pct:-5.51, sells:14, win_rate:50, profit_factor:2.37,
                    sharpe:0.88, cycles:14, expectancy_net:364.15 }, nav:NAV3 },
        { key:'breakout', label:'突破买入', status:'backtest', status_text:'运行中', column:'running',
          frozen_at:'2026-09-15 13:25', frozen_through:'2026-09-14', frozen_note:'v14 财报风控',
          oos:{ frozen_through:'2026-09-14', days:3, cycles:2, expectancy_net:120.5, win_rate:50, net:241, pnl_pct:0.42, last:'2026-09-17' },
          branches:[{key:'breakout',label:'突破买入 · 基准',version:'v1'}], best_branch:'breakout', best_label:'突破买入 · 基准',
          metrics:{ pnl_pct:9.43, excess_pt:-2.2, max_dd_pct:-2.81, sells:30, win_rate:58.8, profit_factor:2.1,
                    sharpe:1.2, cycles:26, expectancy_net:361 }, nav:[0, 3, 9.43] },
        { key:'limitup', label:'涨停后强势整理', status:'paper', status_text:'运行中', column:'running',
          frozen_at:'2026-09-17 23:50', frozen_through:'2026-09-17', oos:{ frozen_through:'2026-09-17', days:0, cycles:0, expectancy_net:null, win_rate:null, net:0, pnl_pct:null, last:'2026-09-17' },
          branches:[{key:'limitup',label:'涨停 · 基准',version:'v1'}], best_branch:'limitup', best_label:'涨停 · 基准',
          metrics:{ pnl_pct:1.2, excess_pt:null, max_dd_pct:-0.43, sells:149, win_rate:55, profit_factor:1.3,
                    sharpe:1, cycles:149, expectancy_net:50 }, nav:null },
        { key:'donchian', label:'唐奇安突破线', status:'backtest', status_text:'运行中',
          hypothesis:'趋势一旦形成会延续', rules_draft:'进:收盘第一次突破前 55 日最高',
          compare_text:'VCP 波段线 · 方向 C · 三段式', kill_text:'…淘汰',
          verdict:{ decision:'wait', text:'全年回测进行中:已跑到 2026-05-01' },
          branches:[{key:'donchian',label:'唐奇安 · 基准',version:'v1'}], best_branch:'donchian', best_label:'唐奇安 · 基准',
          metrics:null, nav:null },
        { key:'idea-1', label:'均线回踩线', status:'idea', status_text:'待写引擎', custom:true,
          hypothesis:'回踩 EMA20 缩量企稳会延续', compare_text:'VCP 波段线 · 方向 C · 三段式', kill_text:'…淘汰',
          branches:[], best_branch:null, metrics:null, nav:null },
        { key:'old', label:'被淘汰的线', status:'killed', status_text:'淘汰',
          verdict:{ decision:'kill', text:'满 30 笔:每笔平均净损益 -12 美元 < 0 —— 淘汰' },
          branches:[{key:'old',label:'旧',version:'v1'}], best_branch:'old', best_label:'旧',
          metrics:{ pnl_pct:-2.1, excess_pt:-13.7, max_dd_pct:-6, sells:31, win_rate:40, profit_factor:0.8,
                    sharpe:-0.3, cycles:30, expectancy_net:-12 }, nav:[0, -1, -2.1] },
      ] }
    var H_RS = renderResearch(RB)
    var H_RSNULL = renderResearch({ common:{}, dates:[], lines:[
      { key:null, label:null, status:'backtest', status_text:null, branches:[{key:null,label:null}], best_branch:null,
        metrics:{ pnl_pct:null, excess_pt:null, max_dd_pct:null, cycles:null, win_rate:null, expectancy_net:null,
                  profit_factor:null, sharpe:null }, nav:[null, null], verdict:null } ] })
    var H_RSSKEL = renderResearch(null, { kind:'dev', icon:'hi-puzzle', html:'后端还没有' })
    RS.form = true
    var H_RSFORM = renderResearch(RB)
    RS.form = false
    var H_DASH = render({ state:'running', strategy:{ name:'x' }, branch:'c',
      branches:[{ key:'c', label:'方向 C · 三段式', active:true }],
      line:{ key:'vcp', label:'VCP 波段线', status:'archived', status_text:'封存', archived_at:'2026-09-13', archive_tag:'vcp-archive-v3' } })
    var H_DASHSKEL = render({}, NOTICE.dev)
  `, ctx, { filename: 'assert-research' })

  const H = ctx.H_RS
  const col = (k) => {
    const i = H.indexOf('data-stage="' + k + '"')
    const j = H.indexOf('data-stage=', i + 10)
    return i < 0 ? '' : H.slice(i, j < 0 ? H.indexOf('同口径对照') : j)
  }
  // 一张卡的 HTML(按标题找):运行中那列里有好几张,断言要落到具体那张上
  const card = (label) => {
    const i = H.indexOf('<div class="t">' + label)
    if (i < 0) return ''
    const s0 = H.lastIndexOf('<div class="rs-card', i)
    const e = H.indexOf('<div class="rs-card', i)
    return H.slice(s0, e < 0 ? H.indexOf('同口径对照') : e)
  }
  const at = (t) => H.indexOf(t)
  const banned = [/NaN/, /undefined/, />null</]
  const clean = (x) => banned.every((re) => !re.test(x))
  const checks = [
    ['⭐只有两列:运行中 → 封存(没有立项 / 全年回测 / 纸上跑列)',
      at('data-stage="running"') >= 0 && at('data-stage="running"') < at('data-stage="archived"') &&
      ['idea', 'backtest', 'paper'].every((k) => at('data-stage="' + k + '"') < 0) && (H.match(/class="rs-col"/g) || []).length === 2],
    ['封存的 VCP 进「封存」列', col('archived').indexOf('VCP 波段线') >= 0],
    ['⭐回测中 / 纸上跑 / 待写引擎的线都进「运行中」', ['突破买入', '涨停后强势整理', '唐奇安突破线', '均线回踩线'].every((t) => col('running').indexOf(t) >= 0)],
    ['唐奇安卡写着回测进度', card('唐奇安突破线').indexOf('已跑到 2026-05-01') >= 0],
    ['待写引擎的卡写明还没有引擎、状态标「待写引擎」', card('均线回踩线').indexOf('还没有引擎') >= 0 && card('均线回踩线').indexOf('待写引擎') >= 0],
    ['待写引擎的卡不画数字格(没有方向就没有数字)', card('均线回踩线').indexOf('rs-kv') < 0 && card('均线回踩线').indexOf('rs-oos') < 0],
    ['⭐冻结后单列:冻结时间、新交易日、x/30 笔、每笔净损益', /规则冻结于 2026-09-15 13:25\(v14 财报风控\)/.test(card('突破买入'))
      && /新交易日 <b>3<\/b> 个 · 完整交易 <b>2\/30<\/b> 笔/.test(card('突破买入')) && /\+\$121|\+\$120/.test(card('突破买入'))],
    ['全段数字标明含回测', card('突破买入').indexOf('全段(含回测)') >= 0],
    ['冻结后还没有新交易日:写明从下一个交易日起计', card('涨停后强势整理').indexOf('还没有新交易日') >= 0],
    ['没有冻结日的线不画冻结段', card('唐奇安突破线').indexOf('rs-oos') < 0],
    ['运行中的线都有「封存」按钮(status 是 paper 也算)', /data-arch="1"[^>]*data-line="limitup"/.test(col('running')) && /data-arch="1"[^>]*data-line="breakout"/.test(col('running'))],
    ['淘汰的线不在看板两列里,在「淘汰记录」',
      ['running', 'archived'].every((k) => col(k).indexOf('被淘汰的线') < 0) &&
      H.slice(at('淘汰记录')).indexOf('<div class="rs-card killed">') >= 0],
    ['封存卡有「打开看板」和「解除封存」', /data-open="c"/.test(col('archived')) && /data-arch="0"/.test(col('archived'))],
    ['回测中的卡有「封存」按钮', /data-arch="1"[^>]*data-line="donchian"/.test(col('running'))],
    ['最好的方向带星标', /class="best">方向 C · 三段式 ★/.test(H)],
    ['没算出来的数字位是 —', /完整交易<b><span class="ag-na">—<\/span><\/b>/.test(card('唐奇安突破线'))],
    ['对照表只列有数据的线(VCP + 突破 + 涨停 + 淘汰的那条)', (H.match(/<tr( class="ref")?><td>/g) || []).length === 4],
    ['对照表阶段列写「运行中」', /<td><span class="rs-chip [a-z]*">运行中<\/span><\/td>/.test(H)],
    ['被引用为对照组的 VCP 那行带「对照组」', /<tr class="ref"><td>VCP 波段线 · 方向 C · 三段式<span class="rs-chip cool">对照组/.test(H)],
    ['净值图:三条有净值的线 + 基准一条 = 4 条 path', (H.match(/<path d="M/g) || []).length === 4],
    ['基准是虚线', /stroke-dasharray="4 3"/.test(H)],
    ['新建入口常驻可见(按钮在研究台面板里)', /id="rs-new"/.test(H)],
    // 用户 2026-09-14 要求:研究台首页去掉扫描筛选入口卡(09-13 曾加在分页和研究台面板之间);运行看板那张同日也去掉
    ['研究台首页没有扫描筛选入口卡', H.indexOf('扫描筛选 · 找候选票') < 0 && !/打开扫描筛选/.test(H)],
    ['404 骨架里也没有扫描筛选入口卡', ctx.H_RSSKEL.indexOf('扫描筛选 · 找候选票') < 0],
    ['研究台面板仍紧跟在分页之后', H.indexOf('rs-seg') >= 0 && H.indexOf('rs-seg') < at('运行中 / 封存 · 淘汰的线收在下面')],
    ['研究台视图顶上是分页,研究台高亮', /data-go="research" class="on"/.test(H)],
    ['正常数据不出现 NaN / undefined / null', clean(H)],
    ['全 null 的线不出现 NaN / undefined / null', clean(ctx.H_RSNULL)],
    ['全 null 的线数字位全是 —', (ctx.H_RSNULL.match(/<b><span class="ag-na">—<\/span><\/b>/g) || []).length >= 6],
    ['全 null 净值不画线,写明还没有净值', ctx.H_RSNULL.indexOf('还没有净值数据') >= 0 && !/<path d="M/.test(ctx.H_RSNULL)],
    ['后端 404 骨架:两列照画', ['running', 'archived'].every((k) => ctx.H_RSSKEL.indexOf('data-stage="' + k + '"') >= 0)],
    ['后端 404 骨架:顶上说明为什么是 —', /ag-banner dev/.test(ctx.H_RSSKEL) && clean(ctx.H_RSSKEL)],
    ['新建表单写出淘汰线并声明提交后锁定', ctx.H_RSFORM.indexOf('id="rs-f-submit"') >= 0 && ctx.H_RSFORM.indexOf('提交后这一栏锁定') >= 0
      && ctx.H_RSFORM.indexOf('每笔平均净损益 &lt; 0') >= 0],
    ['创建入口合并且保存真实草案', H.includes('＋ 创建策略') && !H.includes('新建研究线') && ctx.H_RSFORM.includes('接入执行逻辑后才能回测') && />保存策略草案<\/button>/.test(ctx.H_RSFORM)],
    ['看板视图:分页里「运行看板」高亮', /data-go="dash" class="on"/.test(ctx.H_DASH)],
    // 用户 2026-09-14 要求去掉面包屑(和分页、方向卡、提示条重复)
    ['看板视图:分页右边没有面包屑', ctx.H_DASH.indexOf('rs-crumb') < 0 && !/<a href="#view=research"/.test(ctx.H_DASH)],
    ['看板视图:研究线状态仍由提示条给出', /rs-lb cool"><span class="rs-chip cool">封存/.test(ctx.H_DASH)],
    ['看板视图:封存提示条常驻,带「解除封存」', /rs-lb cool/.test(ctx.H_DASH) && /id="ag-unarchive" data-line="vcp"/.test(ctx.H_DASH)],
    ['看板视图:分页在状态条之前', ctx.H_DASH.indexOf('rs-seg') >= 0 && ctx.H_DASH.indexOf('rs-seg') < ctx.H_DASH.indexOf('ag-status')],
    ['看板 404 骨架:有分页、没有凭空的面包屑状态', ctx.H_DASHSKEL.indexOf('rs-seg') >= 0 && ctx.H_DASHSKEL.indexOf('rs-lb') < 0 && clean(ctx.H_DASHSKEL)],
  ]
  vm.runInContext(`
    var SEEN2 = []
    fetch = function (url) { SEEN2.push(String(url)); return Promise.reject(new Error('render_check: 不联网')) }
    location.hash = ''
    boot()
    location.hash = '#view=research'
    boot({ type: 'click' })
    switchBranch('donchian')
    goView('research')
    var HASH2 = location.hash
  `, ctx, { filename: 'assert-research-route' })
  const s2 = ctx.SEEN2
  checks.push(
    ['路由:没有 hash → 研究台接口', s2[0] === '/api/quant/agent/research'],
    ['路由:#view=research 时刷新按钮留在研究台', s2[1] === '/api/quant/agent/research'],
    ['路由:打开一条线 → 那个方向的看板', s2[2] === '/api/quant/agent/dashboard?branch=donchian'],
    ['路由:点「研究台」分页 → 写 #view=research 并拉研究台', s2[3] === '/api/quant/agent/research' && ctx.HASH2 === '#view=research'],
  )
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 研究台 ·', name)
    else { failed++; console.log('FAIL 研究台 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 研究台定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 魔法筛选器是一级页面(2026-09-13 用户要求)──────────────────────────
// 顶栏里「魔法筛选器」在「小鹿智能体」左边;screener.html 高亮它而不是小鹿;页面里的标题叫「魔法筛选器」。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  vm.runInContext("var SH_SC = renderShell('screener', 't', 's', ''); var SH_AG = renderShell('agent', 't', 's', '')", ctx, { filename: 'assert-magic-tab' })
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  const mt = [
    ['顶栏有「魔法筛选器」且在「小鹿智能体」左边', ctx.SH_SC.indexOf('>魔法筛选器<') >= 0 && ctx.SH_SC.indexOf('>魔法筛选器<') < ctx.SH_SC.indexOf('>小鹿智能体<')],
    ['「魔法筛选器」指向 screener.html', /href="\/strategies\/screener.html" class="tab-h active">魔法筛选器</.test(ctx.SH_SC)],
    ['在魔法筛选器页只高亮它,不高亮小鹿', !/class="tab-h active">小鹿智能体</.test(ctx.SH_SC)],
    ['在小鹿页不高亮魔法筛选器', !/class="tab-h active">魔法筛选器</.test(ctx.SH_AG) && /class="tab-h active">小鹿智能体</.test(ctx.SH_AG)],
    ['screener.html 用 renderShell(\'screener\')', /renderShell\('screener'/.test(sc)],
    ['页面里的标题叫「魔法筛选器」', /hi-sparkle"><\/span>魔法筛选器</.test(sc)],
  ]
  for (const [name, ok] of mt) {
    if (ok) console.log('PASS 魔法筛选器 ·', name)
    else { failed++; console.log('FAIL 魔法筛选器 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 魔法筛选器定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 筛选器 · ThinkScript 时间序列脚本(2026-09-15)────────────────────────
// 后端新加了逐根求值引擎(screen_series),前端要配合的只有三件小事,但漏一件用户就看得见:
//   · input / rec 语句回写时保留关键字(丢了会变成 def,用户复制回 thinkorswim 参数面板就没了)
//   · 「像不像脚本」的判据要认 input / rec / declare 开头(纯 input + plot 的短脚本才不会被当成大白话去走 AI)
//   · 语法速查里写了这套写法,用户不用再猜哪些能用
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(sc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    S.conditions = [
      { name: 'lookback', expr: '5', kind: 'input', is_bool: false, enabled: true },
      { name: 'bullStreak', expr: 'if close > open then bullStreak[1] + 1 else 0', kind: 'rec', is_bool: false, enabled: true },
      { name: 'ok', expr: 'bullStreak >= 3 and close > close[lookback]', is_bool: true, enabled: true },
    ]
    S.combine = 'all'; S.plotName = 'scan'; S.plotExpr = ''
    var TS_SCRIPT = buildScript(false)
    var TS_LOOKS = [looksLikeScript('input n = 5;\\nplot scan = close > close[n];'),
                    looksLikeScript('rec s = if close > open then s[1] + 1 else 0;\\nplot scan = s > 2;'),
                    looksLikeScript('收盘价大于20')]
    var TS_CONDS = condsOf({ conditions: [{ name: 'a', expr: '1', kind: 'input', is_bool: false }, { name: 'b', expr: 'close > 1', is_bool: true }], plot_refs: ['b'], combine: 'all' })
  `, ctx, { filename: 'assert-series' })
  const s = String(ctx.TS_SCRIPT)
  const ts = [
    ['input 行回写成 input', /^input lookback = 5;/m.test(s)],
    ['rec 行回写成 rec', /^rec bullStreak = /m.test(s)],
    ['普通条件仍是 def', /^def ok = /m.test(s)],
    ['plot 只含布尔条件(input / rec 不进 plot)', /^plot scan = ok;$/m.test(s)],
    ['looksLikeScript 认 input 开头', ctx.TS_LOOKS[0] === true],
    ['looksLikeScript 认 rec 开头', ctx.TS_LOOKS[1] === true],
    ['looksLikeScript 不把大白话当脚本', ctx.TS_LOOKS[2] === false],
    ['condsOf 带上 kind,没有的默认 def', ctx.TS_CONDS[0].kind === 'input' && ctx.TS_CONDS[1].kind === 'def'],
    ['语法速查写了时间序列写法', /ThinkScript 时间序列写法/.test(sc) && /if 条件 then 值 else 值/.test(sc)],
    ['vTok 认识 fn 类 token(函数名原样显示)', /fn: 'f'/.test(sc)],
  ]
  for (const [name, ok] of ts) {
    if (ok) console.log('PASS 时间序列脚本 ·', name)
    else { failed++; console.log('FAIL 时间序列脚本 ·', name, name.startsWith('input') || name.startsWith('rec') || name.startsWith('plot') ? s : '') }
  }
} catch (e) {
  failed++
  console.log('FAIL 时间序列脚本定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 筛选器 · 条件行 = plot 的 and 项(2026-09-15)──────────────────────────
// 用户截图:猎杀 FOMO 脚本「同时满足」下面列着 isBull(收盘价>开盘价)和 isBear(收盘价<开盘价)两条互斥条件,
// 真正 plot 里的 bullStreak >= minBullDays 等一条没列。后端现在把 plot 按 and 拆成条件行(kind='term'),
// 中间布尔定义 is_bool=false。前端要做到:按 plot 顺序渲染、term 回写进 plot 不写成 def、停用的 term 在 rebuild 后不丢
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(sc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var PT_D = {
      combine: 'all', plot_name: 'scan', plot_refs: ['accel'], plot_order: ['scan#1', 'accel', 'scan#2'],
      conditions: [
        { name: 'minBullDays', expr: '3', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '3', s: 0, e: 1 }] },
        { name: 'isBull', expr: 'close > open', kind: 'def', is_bool: false, tokens: [{ k: 'field', t: '收盘价' }, { k: 'op', t: '大于' }, { k: 'field', t: '开盘价' }] },
        { name: 'bullStreak', expr: 'if isBull then bullStreak[1] + 1 else 0', kind: 'def', is_bool: false, tokens: [{ k: 'kw', t: '如果' }, { k: 'op', t: '[1]' }] },
        { name: 'accel', expr: 'close > close[1]', kind: 'def', is_bool: true, tokens: [{ k: 'field', t: '收盘价' }, { k: 'op', t: '[1]' }] },
        { name: 'scan#1', title: 'bullStreak', expr: 'bullStreak >= minBullDays', kind: 'term', is_bool: true, tokens: [{ k: 'ref', t: 'bullStreak' }, { k: 'op', t: '大于等于' }, { k: 'ref', t: 'minBullDays(3)' }] },
        { name: 'scan#2', title: 'isBull', expr: 'isBull or volume > volume[1]', kind: 'term', is_bool: true, paren: true, tokens: [{ k: 'ref', t: 'isBull' }] },
      ],
    }
    applyParsed(PT_D)
    var PT_ROWS = condRows().map(function (x) { return x.c.name })
    var PT_SCRIPT = buildScript(false)
    var PT_HTML = vConditions()
    S.conditions[4].enabled = false
    var PT_OFF = buildScript(false)
    var PT_ALL = buildScript(true)
    var PT_WRAP = [wrapTerm({ expr: '(a or b)', paren: true }), wrapTerm({ expr: 'a || b' }), wrapTerm({ expr: 'close > 1' }), wrapTerm({ expr: '(a) or (b)' })]
  `, ctx, { filename: 'assert-plot-terms' })
  const html = String(ctx.PT_HTML)
  const s = String(ctx.PT_SCRIPT)
  const pt = [
    ['条件行按 plot 顺序', JSON.stringify(ctx.PT_ROWS) === JSON.stringify(['scan#1', 'accel', 'scan#2'])],
    ['term 不写成 def', !/def scan#/.test(s)],
    ['plot 按原顺序拼回,or 项补括号', /plot scan = bullStreak >= minBullDays and accel and \(isBull or volume > volume\[1\]\);/.test(s)],
    ['input 关键字保留', /input minBullDays = 3;/.test(s)],
    ['表头条件数 = plot 项数(3),不数中间定义', /以下<b>3<\/b> 个条件/.test(html)],
    ['中间定义 isBull 不在条件行里', !/<span class="nm">isBull<\/span><span class="body">/.test(html.split('cd-defs')[0])],
    ['中间定义折叠区里列出 isBull / bullStreak', /脚本里的中间定义 2 个/.test(html) && /cd-def"><span class="nm">isBull/.test(html)],
    ['参数区显示 minBullDays 且可改', /cd-param"><span class="tk r">minBullDays<\/span> <input class="sc-num"/.test(html)],
    ['term 行标题用引用名', /<span class="nm" title="plot 里的一项">bullStreak<\/span>/.test(html)],
    ['term 行没有「复制一条」', (html.match(/data-act="dup"/g) || []).length === 1],
    ['停用 term:buildScript(false) 去掉这一项', /plot scan = accel and \(isBull or volume > volume\[1\]\);/.test(String(ctx.PT_OFF))],
    ['停用 term:buildScript(true) 仍含这一项(rebuild 靠它不丢)', /plot scan = bullStreak >= minBullDays and accel/.test(String(ctx.PT_ALL))],
    ['wrapTerm 不叠括号 / 认 || / 比较式不包 / (a) or (b) 要包', JSON.stringify(ctx.PT_WRAP) === JSON.stringify(['(a or b)', '(a || b)', 'close > 1', '((a) or (b))'])],
  ]
  for (const [name, ok] of pt) {
    if (ok) console.log('PASS 条件行=plot项 ·', name)
    else { failed++; console.log('FAIL 条件行=plot项 ·', name, name.startsWith('plot') || name.startsWith('停用') ? s + ' | ' + ctx.PT_OFF : '') }
  }
} catch (e) {
  failed++
  console.log('FAIL 条件行=plot项定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}


// ─── 筛选器 · 审计补测(2026-09-15)───────────────────────────────────────────
// 覆盖审计列出的缺口:常量 plot、停用项在切市场 / 追加时丢失、追加合并、custom 下清孤儿、改动失败回滚、
// 编辑被登录拦下、单条测试脚本、保存弹窗、草稿恢复、数据源名字过滤、额度提示、字段插入、emoji 偏移、注释里的参数名、前面的 plot。
// 每条都注明它防的是哪种静默出错。网络一律用假的 post。
function scCtx() {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
    .forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var TOASTS = [], SENT = [], LOGINS = [], NL = String.fromCharCode(10)
    toast = function (m, k) { TOASTS.push([String(m), k || '']) }
    needLogin = function (m) { LOGINS.push(m || '') }
    localStorage.setItem('hunter_token', 't'); S.login = true
    // 假后端:按顺序取 RESP 里的回应;没有就回显当前状态(当成解析成功)
    var RESP = []
    function echo() {
      return { ok: true, status: 200, data: {
        combine: S.combine, plot_name: S.plotName || 'scan', term_host: S.termHost, extra_plots: S.extraPlots,
        plot_expr: S.plotExpr, plot_order: S.plotOrder,
        plot_refs: S.conditions.filter(function (c) { return c.is_bool && c.kind !== 'term' }).map(function (c) { return c.name }),
        conditions: S.conditions.map(function (c) { return Object.assign({ tokens: c.tokens || [] }, c) }) } }
    }
    post = async function (url, body) { SENT.push({ url: url, body: body }); var x = RESP.shift(); return x ? (typeof x === 'function' ? x(url, body) : x) : echo() }
    var ERR400 = { ok: false, status: 400, data: { detail: '假后端:解析失败' } }
    var ERR401 = { ok: false, status: 401, data: { detail: '请先登录' } }
  `, ctx, { filename: 'audit-setup' })
  return ctx
}
const AUDIT_RESULTS = []
function auditCheck(group, name, ok, detail) { AUDIT_RESULTS.push([group, name, !!ok, detail]) }

const auditJobs = []
function auditJob(group, fn) {
  auditJobs.push(Promise.resolve().then(fn).catch(e => auditCheck(group, '执行异常', false, e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)))
}

// A · 常量 plot:全停用写 false,解析回来一条都不启用
auditJob('常量plot', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['c1', 'c2'], plot_order: ['c1', 'c2'], conditions: [
      { name: 'c1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] },
      { name: 'c2', expr: 'volume > 1000', kind: 'def', is_bool: true, tokens: [] } ] })
    S.conditions.forEach(function (c) { c.enabled = false })
    var A_OFF = buildScript(false)
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: [], plot_order: [], conditions: [
      { name: 'c1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] },
      { name: 'c2', expr: 'volume > 1000', kind: 'def', is_bool: true, tokens: [] } ] })
    var A_ROWS = condRows().map(function (x) { return x.c.name + ':' + x.c.enabled })
    var A_HTML = vConditions()
  `, ctx)
  auditCheck('常量plot', '全停用回写成 plot scan = false', /plot scan = false;$/.test(ctx.A_OFF), ctx.A_OFF)
  auditCheck('常量plot', '⭐解析回来两条都停用、没有叫 false 的行', JSON.stringify(ctx.A_ROWS) === JSON.stringify(['c1:false', 'c2:false']), ctx.A_ROWS)
  auditCheck('常量plot', '表头 0 个条件', /以下<b>0<\/b> 个条件/.test(ctx.A_HTML))
})

// B · 停用项在切市场 / 追加生成时不丢
auditJob('停用不丢', async () => {
  const ctx = scCtx()
  const src = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  auditCheck('停用不丢', '⭐页面里没有 reparse(buildScript()) / reparse(buildScript(false)) 这种会丢停用 term 的回写',
    !/reparse\(buildScript\(\)\)/.test(src) && !/reparse\(buildScript\(false\)\)/.test(src))
  auditCheck('停用不丢', '切市场走 reparseKeepOff', /S\.market = b\.dataset\.mkt; S\.probe = \{\}; saveDraft\(\)[\s\S]{0,200}reparseKeepOff\(\)/.test(src))
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['c1'], plot_order: ['scan#1', 'c1'], conditions: [
      { name: 'c1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] },
      { name: 'scan#1', title: 'volume', expr: 'volume > volume[1]', kind: 'term', is_bool: true, tokens: [] } ] })
    S.conditions[1].enabled = false
  `, ctx)
  await vm.runInContext(`reparseKeepOff()`, ctx)
  auditCheck('停用不丢', '⭐回写的脚本里仍有停用的 term', /volume > volume\[1\]/.test(ctx.SENT[0].body.script), ctx.SENT[0] && ctx.SENT[0].body.script)
  auditCheck('停用不丢', '解析回来仍是停用', vm.runInContext(`S.conditions.filter(function (c) { return c.kind === 'term' })[0].enabled`, ctx) === false)
  // 追加生成:新脚本解析成功 → 合并 → 回写;停用 term 要在回写脚本里、合并后仍停用
  vm.runInContext(`
    S.mode = 'append'; S.input = 'def c9 = close > 9;' + NL + 'plot scan = c9;'
    SENT.length = 0
    RESP.push({ ok: true, status: 200, data: { combine: 'all', plot_name: 'scan', plot_refs: ['c9'], plot_order: ['c9'],
      conditions: [{ name: 'c9', expr: 'close > 9', kind: 'def', is_bool: true, tokens: [] }] } })
  `, ctx)
  await vm.runInContext(`generate()`, ctx)
  const s2 = ctx.SENT[1] && ctx.SENT[1].body.script
  auditCheck('停用不丢', '⭐追加后回写的脚本含停用 term 与新条件', /volume > volume\[1\]/.test(s2) && /def c9 = close > 9;/.test(s2), s2)
  auditCheck('停用不丢', '追加后停用 term 仍停用', vm.runInContext(`S.conditions.filter(function (c) { return c.kind === 'term' })[0].enabled`, ctx) === false)
})

// C · 追加合并
auditJob('追加合并', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', term_host: 'H', plot_refs: ['a'], plot_order: ['H#1', 'a'], conditions: [
      { name: 'n', expr: '3', kind: 'input', is_bool: false, tokens: [] },
      { name: 'a', expr: 'close > close[n]', kind: 'def', is_bool: true, tokens: [] },
      { name: 'H#1', title: 'close', expr: 'close > n', kind: 'term', is_bool: true, tokens: [] } ] })
    var FRESH = condsOf({ combine: 'all', plot_refs: ['a', 'H'], plot_order: ['a', 'scan#1', 'H'], conditions: [
      { name: 'n', expr: '5', kind: 'input', is_bool: false, tokens: [] },
      { name: 'a', expr: 'volume > volume[n] and MACD.n > n', kind: 'def', is_bool: true, tokens: [] },
      { name: 'H', expr: 'a and close > n # n 是周期', kind: 'def', is_bool: true, tokens: [] },
      { name: 'scan#1', title: 'high', expr: 'high > n_old', kind: 'term', is_bool: true, tokens: [] } ] })
    mergeAppend(FRESH)
    var C_BY = {}; S.conditions.forEach(function (c, i) { C_BY[c.name + '@' + i] = c.expr })
    var C_NAMES = S.conditions.map(function (c) { return c.name })
  `, ctx)
  const names = ctx.C_NAMES, by = ctx.C_BY
  const find = (nm) => Object.keys(by).filter(k => k.split('@')[0] === nm).map(k => by[k])
  auditCheck('追加合并', '重名的 input / def 改名为 _2', names.includes('n_2') && names.includes('a_2'), names)
  auditCheck('追加合并', '⭐和宿主同名的 def 也改名(原来会写出两句 def H)', names.includes('H_2') && names.filter(n => n === 'H').length === 0, names)
  auditCheck('追加合并', '新条件内部引用跟着改名', find('a_2')[0] === 'volume > volume[n_2] and MACD.n > n_2', find('a_2'))
  auditCheck('追加合并', '整词替换:MACD.n 不被改;n_old 不被改', /MACD\.n > n_2/.test(find('a_2')[0]) && find('scan#1')[0] === 'high > n_old', [find('a_2'), find('scan#1')])
  auditCheck('追加合并', '注释里的名字不改', find('H_2')[0] === 'a_2 and close > n_2 # n 是周期', find('H_2'))
  auditCheck('追加合并', '原有条件一个字不变', find('a')[0] === 'close > close[n]' && find('H#1')[0] === 'close > n' && find('n')[0] === '3')
  // 追加之后回写解析失败 → 条件区与生成框退回原样
  const ctx2 = scCtx()
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['c1'], plot_order: ['c1'], conditions: [
      { name: 'c1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] } ] })
    S.mode = 'append'; S.input = 'def c2 = close > 2;' + NL + 'plot scan = c2;'
    RESP.push({ ok: true, status: 200, data: { combine: 'all', plot_name: 'scan', plot_refs: ['c2'], plot_order: ['c2'],
      conditions: [{ name: 'c2', expr: 'close > 2', kind: 'def', is_bool: true, tokens: [] }] } })
    RESP.push(ERR400)
  `, ctx2)
  await vm.runInContext(`generate()`, ctx2)
  auditCheck('追加合并', '⭐追加后解析失败:条件区退回原样', JSON.stringify(vm.runInContext(`S.conditions.map(function (c) { return c.name })`, ctx2)) === JSON.stringify(['c1']))
  auditCheck('追加合并', '追加失败:生成框里的原文还给用户、报错可见', /def c2 = close > 2;/.test(vm.runInContext('S.input', ctx2)) && !!vm.runInContext('S.genError', ctx2))
})

// D · 自定义组合 / 前面的 plot:清孤儿不能删 plot 用到的定义
auditJob('清孤儿', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    applyParsed({ combine: 'custom', plot_name: 'scan', plot_expr: 'if a and volume > 0 then yes else no', plot_refs: [], plot_order: [], conditions: [
      { name: 'n', expr: '3', kind: 'input', is_bool: false, tokens: [] },
      { name: 'a', expr: 'close > close[n]', kind: 'def', is_bool: false, tokens: [] } ] })
    pruneVars()
    var D_SCRIPT = buildScript(true)
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['a'], plot_order: ['a'], extra_plots: [{ name: 'p1', expr: 'b' }], conditions: [
      { name: 'b', expr: 'volume > volume[1]', kind: 'def', is_bool: false, tokens: [] },
      { name: 'a', expr: 'close > close[1]', kind: 'def', is_bool: true, tokens: [] } ] })
    pruneVars()
    var D_EXTRA = buildScript(false)
    var D_REF = referenced('b', -1)
  `, ctx)
  auditCheck('清孤儿', '⭐custom:plot 原文用到的 a 与它用到的参数 n 都留着', /input n = 3;/.test(ctx.D_SCRIPT) && /def a = close > close\[n\];/.test(ctx.D_SCRIPT), ctx.D_SCRIPT)
  auditCheck('清孤儿', '⭐前面的 plot 原样写回,且在最后一个 plot 之前', /plot p1 = b;\nplot scan = a;$/.test(ctx.D_EXTRA), ctx.D_EXTRA)
  auditCheck('清孤儿', '前面的 plot 用到的 b 不被当孤儿', ctx.D_REF === true && /def b = /.test(ctx.D_EXTRA))
})

// E · 改动失败回滚:改数字 / 删除 / 复制 / 编辑保存(400 与 401)
auditJob('失败回滚', async () => {
  const setup = `
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['c1', 'c2'], plot_order: ['c1', 'c2'], conditions: [
      { name: 'k', expr: '20', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '20', s: 0, e: 2 }] },
      { name: 'c1', expr: 'volume > Average(volume, 20)', kind: 'def', is_bool: true, tokens: [{ k: 'num', t: '20', s: 25, e: 27 }] },
      { name: 'c2', expr: 'close > k and high > k', kind: 'def', is_bool: true, tokens: [] } ] })
  `
  let ctx = scCtx()
  vm.runInContext(setup + `RESP.push(ERR400)`, ctx)
  await vm.runInContext(`onNumChange({ target: { classList: { contains: function () { return true } }, dataset: { c: '1', t: '0' }, value: '300' } })`, ctx)
  auditCheck('失败回滚', '⭐改数字解析失败 → 表达式退回原样、报错可见', vm.runInContext(`S.conditions[1].expr`, ctx) === 'volume > Average(volume, 20)' && !!vm.runInContext('S.genError', ctx))
  ctx = scCtx()
  vm.runInContext(setup + `RESP.push(ERR400)`, ctx)
  await vm.runInContext(`onCondAction({ target: { closest: function () { return { dataset: { i: '1', act: 'del' } } } } })`, ctx)
  auditCheck('失败回滚', '删除解析失败 → 条件退回', vm.runInContext(`S.conditions.map(function (c) { return c.name }).join(',')`, ctx) === 'k,c1,c2')
  ctx = scCtx()
  vm.runInContext(setup + `RESP.push(ERR400)`, ctx)
  await vm.runInContext(`onCondAction({ target: { closest: function () { return { dataset: { i: '1', act: 'dup' } } } } })`, ctx)
  auditCheck('失败回滚', '复制解析失败 → 条件退回', vm.runInContext(`S.conditions.length`, ctx) === 3)
  ctx = scCtx()
  vm.runInContext(setup, ctx)
  await vm.runInContext(`onCondAction({ target: { closest: function () { return { dataset: { i: '1', act: 'dup' } } } } })`, ctx)
  auditCheck('失败回滚', '复制成功:回写脚本里有 def c1_2,且保留 kind', /def c1_2 = volume > Average\(volume, 20\);/.test(ctx.SENT[0].body.script)
    && vm.runInContext(`S.conditions.filter(function (c) { return c.name === 'c1_2' })[0].kind`, ctx) === 'def', ctx.SENT[0] && ctx.SENT[0].body.script)
  // 编辑保存:400 → 退回并保留带参数值的原文;401 → 同样退回、不当成功;成功改共用参数 → 提示影响几处
  ctx = scCtx()
  vm.runInContext(setup + `S.editing = 'c2'; RESP.push(ERR400)`, ctx)
  await vm.runInContext(`commitEdit('close > k(30) and high > k')`, ctx)
  auditCheck('失败回滚', '编辑 400:参数值与条件都退回,编辑框留着带值的原文',
    vm.runInContext(`S.conditions[0].expr + '|' + S.conditions[2].expr + '|' + S.editing + '|' + S.editText`, ctx) === '20|close > k and high > k|c2|close > k(30) and high > k')
  ctx = scCtx()
  vm.runInContext(setup + `S.editing = 'c2'; RESP.push(ERR401)`, ctx)
  await vm.runInContext(`commitEdit('close > k(30) and high > k')`, ctx)
  auditCheck('失败回滚', '⭐编辑被 401 拦下:不当成功,参数值退回、弹登录', vm.runInContext(`S.conditions[0].expr + '|' + S.editing`, ctx) === '20|c2' && ctx.LOGINS.length === 1)
  ctx = scCtx()
  vm.runInContext(setup + `S.editing = 'c2'`, ctx)
  await vm.runInContext(`commitEdit('close > k(30) and high > k')`, ctx)
  auditCheck('失败回滚', '编辑成功改共用参数:提示用到它的处数', ctx.TOASTS.some(t => /参数 k 改成 30/.test(t[0])) && /input k = 30;/.test(ctx.SENT[0].body.script), ctx.TOASTS)
})

// F · 单条测试拼出的脚本
auditJob('单条测试', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', term_host: 'FOMO_Setup', plot_refs: ['isGreen'], plot_order: ['FOMO_Setup#1', 'FOMO_Setup#2', 'isGreen'], conditions: [
      { name: 'minStreak', expr: '3', kind: 'input', is_bool: false, tokens: [] },
      { name: 'isGreen', expr: 'close > close[1]', kind: 'def', is_bool: true, tokens: [] },
      { name: 'greenStreak', expr: 'if isGreen then greenStreak[1] + 1 else 0', kind: 'rec', is_bool: false, tokens: [] },
      { name: 'FOMO_Setup#1', title: 'greenStreak', expr: 'greenStreak >= minStreak', kind: 'term', is_bool: true, tokens: [] },
      { name: 'FOMO_Setup#2', title: 'isGreen', expr: '(isGreen or close > 1)', kind: 'term', is_bool: true, tokens: [] } ] })
    RESP.push({ ok: true, status: 200, data: { matched: 7 } })
    RESP.push({ ok: true, status: 200, data: { matched: 2 } })
    RESP.push({ ok: false, status: 429, data: { detail: '单条测试太频繁' } })
  `, ctx)
  await vm.runInContext(`probeOne(S.conditions[3]).then(function () { return probeOne(S.conditions[1]) }).then(function () { return probeOne(S.conditions[4]) })`, ctx)
  const s1 = ctx.SENT[0].body.script, s2 = ctx.SENT[1].body.script
  auditCheck('单条测试', '⭐term:plot 写原文,input / rec 关键字保留,不输出宿主与 term 的 def',
    /plot scan = greenStreak >= minStreak;$/.test(s1) && /input minStreak = 3;/.test(s1) && /rec greenStreak = /.test(s1) && !/def FOMO_Setup/.test(s1) && !/#/.test(s1), s1)
  auditCheck('单条测试', 'def 行:plot 写名字', /plot scan = isGreen;$/.test(s2), s2)
  auditCheck('单条测试', '请求带 probe:true、limit 1', ctx.SENT[0].body.probe === true && ctx.SENT[0].body.limit === 1)
  auditCheck('单条测试', '命中数记在这一条上', vm.runInContext(`S.probe['FOMO_Setup#1'] + ',' + S.probe['isGreen']`, ctx) === '7,2')
  auditCheck('单条测试', '429 弹提示、这一条记为失败', ctx.TOASTS.some(t => t[1] === 'warn' && /太频繁/.test(t[0])) && vm.runInContext(`S.probe['FOMO_Setup#2']`, ctx) === null)
})

// G · 保存弹窗 / 保存 / 加载失败退回市场
auditJob('保存策略', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    var MODAL_HTML = ''
    var FIELDS = { '#sv-name': { value: '我的 FOMO', focus: function () {}, select: function () {}, addEventListener: function () {} },
                   '#sv-err': { style: {}, textContent: '', innerHTML: '' }, '#sv-ok': { disabled: false, textContent: '', addEventListener: function () {} } }
    var MODAL = { querySelector: function (q) { return FIELDS[q] || null }, remove: function () { MODAL.removed = true } }
    openModal = function (h) { MODAL_HTML = h; return MODAL }
    applyParsed({ combine: 'all', plot_name: 'scan', term_host: 'FOMO_Setup', plot_refs: ['isGreen'], plot_order: ['FOMO_Setup#1', 'FOMO_Setup#2', 'isGreen'], conditions: [
      { name: 'minStreak', expr: '3', kind: 'input', is_bool: false, tokens: [] },
      { name: 'isGreen', expr: 'close > close[1]', kind: 'def', is_bool: true, tokens: [] },
      { name: 'greenStreak', expr: 'if isGreen then greenStreak[1] + 1 else 0', kind: 'rec', is_bool: false, tokens: [] },
      { name: 'FOMO_Setup#1', title: 'greenStreak', expr: 'greenStreak >= minStreak', kind: 'term', is_bool: true, tokens: [] },
      { name: 'FOMO_Setup#2', title: 'close', expr: 'close > 5', kind: 'term', is_bool: true, tokens: [] } ] })
    S.conditions[4].enabled = false
    S.saved = []
    openSaveDialog()
    RESP.push({ ok: true, status: 200, data: { created: true } })
    RESP.push({ ok: true, status: 200, data: { items: [] } })
  `, ctx)
  auditCheck('保存策略', '⭐弹窗明说停用的 term 不会存进去', /停用的 <b>1<\/b> 条是 plot 里直接写的比较式,<b>不会存进去<\/b>/.test(ctx.MODAL_HTML), ctx.MODAL_HTML.slice(0, 400))
  await vm.runInContext(`doSave('我的 FOMO', MODAL)`, ctx)
  const body = ctx.SENT[0] && ctx.SENT[0].body
  auditCheck('保存策略', '⭐保存的脚本:input / rec 关键字、宿主结构、停用 term 不在里面',
    body && /input minStreak = 3;/.test(body.script) && /rec greenStreak = /.test(body.script)
    && /def FOMO_Setup = greenStreak >= minStreak and isGreen;\nplot scan = FOMO_Setup;$/.test(body.script), body && body.script)
  auditCheck('保存策略', '保存带市场与排序', body && body.market === 'us' && 'sort_by' in body)
  // 加载失败:市场退回原来的、不算加载成功
  const ctx2 = scCtx()
  vm.runInContext(`
    S.market = 'us'; S.loadedName = '旧的'
    S.saved = [{ id: 9, name: '港股那个', market: 'hk', script: 'def c = close > 1;' + NL + 'plot scan = c;' }]
    RESP.push(ERR400)
  `, ctx2)
  await vm.runInContext(`loadSavedPreset(9)`, ctx2)
  auditCheck('保存策略', '加载解析失败:市场退回、loadedName 清空', vm.runInContext(`S.market + '|' + S.loadedName`, ctx2) === 'us|')
})

// G2 · 点官方示例:不进生成框、不再点「生成」,按「追加 / 替换」开关合并(2026-09-22)
// 线上事故:追加模式下点「VCP 波段收缩」报「不认识 'sma50'」(后端大小写改写的锅,已修);同日用户要求点示例直接进条件区,
// 第一版一律替换又被纠正:「我选的追加,它点击后却是替换」。
auditJob('官方示例', async () => {
  const OLD = `applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['old1', 'old2'], plot_order: ['old1', 'old2'], conditions: [
      { name: 'sma50', expr: 'Average(close, 50)', kind: 'def', is_bool: false, tokens: [] },
      { name: 'old1', expr: 'close > sma50', kind: 'def', is_bool: true, tokens: [] },
      { name: 'old2', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] } ] })`
  const PRE = `var PRE = { key: 'vcp_range', name: 'VCP 波段收缩', market: 'us', script: 'def c_trend = close > SMA50;' + NL + 'plot scan = c_trend;' }`
  const FRESH = `{ ok: true, status: 200, data: { combine: 'all', plot_name: 'scan', plot_refs: ['c_trend'], plot_order: ['c_trend'], conditions: [
      { name: 'c_trend', expr: 'close > SMA50', kind: 'def', is_bool: true, tokens: [] } ] } }`
  const bools = (c) => vm.runInContext(`S.conditions.filter(function (c) { return c.is_bool }).map(function (c) { return c.name }).join(',')`, c)

  // ① 追加模式 + 已有条件 → 追加
  const ctx = scCtx()
  vm.runInContext(`
    S.mode = 'append'; S.market = 'us'; S.input = '用户正在写的一句'
    ${OLD}
    ${PRE}
    RESP.push(${FRESH})
    RESP.push({ ok: true, status: 200, data: { combine: 'all', plot_name: 'scan', plot_refs: ['old1', 'old2', 'c_trend'], plot_order: ['old1', 'old2', 'c_trend'], conditions: [
      { name: 'sma50', expr: 'Average(close, 50)', kind: 'def', is_bool: false, tokens: [] },
      { name: 'old1', expr: 'close > sma50', kind: 'def', is_bool: true, tokens: [] },
      { name: 'old2', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] },
      { name: 'c_trend', expr: 'close > SMA50', kind: 'def', is_bool: true, tokens: [] } ] } })
  `, ctx)
  await vm.runInContext(`loadOfficialPreset(PRE)`, ctx)
  const a0 = ctx.SENT[0] && ctx.SENT[0].body
  auditCheck('官方示例', '⭐追加模式:发去解析的是示例原文,带当前脚本当上下文', a0 && a0.script === ctx.PRE.script && !!a0.context, a0)
  auditCheck('官方示例', '⭐追加模式:原来的条件保留,示例的条件加在后面', bools(ctx) === 'old1,old2,c_trend', bools(ctx))
  auditCheck('官方示例', '⭐追加模式:生成框里用户正在写的字不动、不塞示例脚本', vm.runInContext(`S.input`, ctx) === '用户正在写的一句')
  auditCheck('官方示例', '追加模式:提示追加了几条', ctx.TOASTS.some(t => /已追加官方示例「VCP 波段收缩」的 1 个条件/.test(t[0])), ctx.TOASTS)

  // ② 替换模式 → 整套替换
  const ctx1 = scCtx()
  vm.runInContext(`
    S.mode = 'replace'; S.market = 'hk'; S.input = '用户正在写的一句'
    ${OLD}
    ${PRE}
    RESP.push(${FRESH})
  `, ctx1)
  await vm.runInContext(`loadOfficialPreset(PRE)`, ctx1)
  const b0 = ctx1.SENT[0] && ctx1.SENT[0].body
  auditCheck('官方示例', '⭐替换模式:发去解析的是示例原文,不带上下文', b0 && b0.script === ctx1.PRE.script && !b0.context, b0)
  auditCheck('官方示例', '⭐替换模式:条件整套换成示例的', bools(ctx1) === 'c_trend', bools(ctx1))
  auditCheck('官方示例', '替换模式:市场切到示例的市场', vm.runInContext(`S.market`, ctx1) === 'us')
  auditCheck('官方示例', '替换模式:生成框里的字不动', vm.runInContext(`S.input`, ctx1) === '用户正在写的一句')
  auditCheck('官方示例', '替换模式:提示替换了几条', ctx1.TOASTS.some(t => /已加载官方示例「VCP 波段收缩」 · 替换了原来的 2 个条件/.test(t[0])), ctx1.TOASTS)

  // ③ 追加模式但当前没有条件 → 就是加载
  const ctx4 = scCtx()
  vm.runInContext(`S.mode = 'append'; S.market = 'us'; ${PRE}; RESP.push(${FRESH})`, ctx4)
  await vm.runInContext(`loadOfficialPreset(PRE)`, ctx4)
  auditCheck('官方示例', '追加模式、当前没有条件:直接加载', bools(ctx4) === 'c_trend' && !(ctx4.SENT[0].body.context))

  // ④ 失败:两种模式都退回市场、条件不动、生成框不动,追加模式也不给 AI 按钮
  const ctx2 = scCtx()
  vm.runInContext(`
    S.mode = 'replace'; S.market = 'hk'
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['old1'], plot_order: ['old1'], conditions: [
      { name: 'old1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] } ] })
    RESP.push(ERR400)
  `, ctx2)
  await vm.runInContext(`loadOfficialPreset({ key: 'x', name: 'X', market: 'us', script: 'plot scan = close > 1;' })`, ctx2)
  auditCheck('官方示例', '替换模式解析失败:市场退回、原来的条件不动',
    vm.runInContext(`S.market + '|' + S.conditions.map(function (c) { return c.name }).join(',')`, ctx2) === 'hk|old1')
  const ctx3 = scCtx()
  vm.runInContext(`
    S.mode = 'append'; S.market = 'hk'; S.input = '我的草稿'
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['old1'], plot_order: ['old1'], conditions: [
      { name: 'old1', expr: 'close > 20', kind: 'def', is_bool: true, tokens: [] } ] })
    RESP.push({ ok: false, status: 400, data: { detail: { message: '不认识 xx', can_try_ai: true, kind: 'script' } } })
  `, ctx3)
  await vm.runInContext(`loadOfficialPreset({ key: 'x', name: 'X', market: 'us', script: 'plot scan = xx > 1;' })`, ctx3)
  auditCheck('官方示例', '⭐追加模式解析失败:市场退回、条件不动、生成框不动、不给 AI 按钮',
    vm.runInContext(`S.market + '|' + S.conditions.map(function (c) { return c.name }).join(',') + '|' + S.input + '|' + S.canTryAi`, ctx3) === 'hk|old1|我的草稿|false',
    vm.runInContext(`S.market + '|' + S.input + '|' + S.canTryAi`, ctx3))

  const html = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  const branch = html.slice(html.indexOf('if (!b.dataset.preset) return'), html.indexOf('if (!b.dataset.preset) return') + 300)
  auditCheck('官方示例', '⭐源码:点示例走 loadOfficialPreset,点击分支里不直接 generate()', /loadOfficialPreset\(p\)/.test(branch) && !/generate\(/.test(branch), branch)
})

// H · 草稿:宿主脚本能恢复;401 时草稿不被覆盖
auditJob('草稿', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    var DRAFT = 'input n = 3;' + NL + 'def a = close > close[n];' + NL + 'def H = a and close > 1;' + NL + 'plot scan = H;'
    RESP.push({ ok: true, status: 200, data: { combine: 'all', plot_name: 'scan', term_host: 'H', plot_refs: ['a'], plot_order: ['a', 'H#1'], conditions: [
      { name: 'n', expr: '3', kind: 'input', is_bool: false, tokens: [] },
      { name: 'a', expr: 'close > close[n]', kind: 'def', is_bool: true, tokens: [] },
      { name: 'H#1', title: 'close', expr: 'close > 1', kind: 'term', is_bool: true, tokens: [] } ] } })
  `, ctx)
  await vm.runInContext(`restoreDraft(DRAFT)`, ctx)
  vm.runInContext(`saveDraft(); var H_SAVED = localStorage.getItem(LS)`, ctx)
  auditCheck('草稿', '宿主脚本草稿恢复后再存,结构不变', ctx.H_SAVED === ctx.DRAFT, ctx.H_SAVED)
  const ctx2 = scCtx()
  vm.runInContext(`
    S.conditions = []
    localStorage.setItem(LS, 'def c = close > 1;' + NL + 'plot scan = c;')
    RESP.push(ERR401)
  `, ctx2)
  await vm.runInContext(`restoreDraft(localStorage.getItem(LS))`, ctx2)
  vm.runInContext(`S.market = 'hk'; saveDraft(); var H_AFTER = localStorage.getItem(LS); var H_MK = localStorage.getItem(LS_MK)`, ctx2)
  auditCheck('草稿', '⭐没登录恢复草稿失败后,点市场不会把草稿覆盖成空', ctx2.H_AFTER === 'def c = close > 1;\nplot scan = c;' && ctx2.H_MK === 'hk', ctx2.H_AFTER)
  vm.runInContext(`applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['c'], plot_order: ['c'], conditions: [{ name: 'c', expr: 'close > 2', kind: 'def', is_bool: true, tokens: [] }] }); saveDraft(); var H_NEW = localStorage.getItem(LS)`, ctx2)
  auditCheck('草稿', '有了新条件之后草稿正常更新', /close > 2/.test(ctx2.H_NEW))
})

// I · 数据源名字过滤(和后端文案前缀耦合,2026-09-10 失效过一次)
auditJob('数据源过滤', async () => {
  const ctx = scCtx()
  const pyPath = path.join(DIR, '..', '..', '..', 'api', 'app', 'services', 'quant', 'screen_source.py')
  let py = null
  try { py = fs.readFileSync(pyPath, 'utf8') } catch (e) { /* 下面 FAIL */ }
  auditCheck('数据源过滤', '能读到后端 screen_source.py(与前端同仓对照)', !!py, pyPath)
  if (!py) return
  const firstLit = (name) => {
    const i = py.indexOf(name + ' =')
    if (i < 0) return null
    const m = py.slice(i + name.length + 2).match(/^\s*\(?\s*[rfu]?"([^"\n]*)"/)
    return m ? m[1] : null
  }
  const delay = firstLit('DELAY_WARN'), mcap = firstLit('MARKET_CAP_WARN')
  const prefixes = vm.runInContext('DROP_PREFIX', ctx)
  auditCheck('数据源过滤', '⭐后端 DELAY_WARN 以前端 DROP_PREFIX 某一项开头(改文案要同步)', delay && prefixes.some(p => delay.indexOf(p) === 0), delay)
  auditCheck('数据源过滤', '⭐后端 MARKET_CAP_WARN 以前端 DROP_PREFIX 某一项开头', mcap && prefixes.some(p => mcap.indexOf(p) === 0), mcap)
  ctx.DELAY = delay; ctx.MCAP = mcap
  vm.runInContext(`
    var I_KEEP = dropSrc([DELAY + '……', MCAP + '……', 'TradingView 的提示', '按日线**逐根**求值'])
    var I_MASK = maskSrc('来自 TradingView 的字段 TradingView')
    var I_WARN = vWarn({ notes: [], warnings: [DELAY + 'x', '<b>x</b> **重点**'] })
  `, ctx)
  auditCheck('数据源过滤', 'dropSrc 丢掉两条后端提示与带厂商名的,保留其余', JSON.stringify(ctx.I_KEEP) === JSON.stringify(['按日线**逐根**求值']), ctx.I_KEEP)
  auditCheck('数据源过滤', 'maskSrc 报错文案只换名字不丢整条', ctx.I_MASK === '来自 行情源 的字段 行情源')
  auditCheck('数据源过滤', 'vWarn:过滤 + 先转义再加粗', !/延迟|免订阅/.test(ctx.I_WARN) && /&lt;b&gt;x&lt;\/b&gt; <b>重点<\/b>/.test(ctx.I_WARN), ctx.I_WARN)
})

// J · 额度 / 登录提示的其余分支
auditJob('额度', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    S.mode = 'replace'; S.input = '成交量大于100万'; S.aiText = '成交量大于100万'   // AI 识别读的是 aiText
    RESP.push({ ok: false, status: 400, data: { detail: { message: 'AI 没给出可用结果', quota: { ai: { remaining: 3, limit: 10 } }, quota_note: 'AI 识别已计 1 次' } } })
  `, ctx)
  await vm.runInContext(`generate(true)`, ctx)
  auditCheck('额度', '生成 400 但模型已调用:提示已计次', ctx.TOASTS.some(t => t[1] === 'warn' && /已计 1 次/.test(t[0])), ctx.TOASTS)
  vm.runInContext(`
    S.resultScan = { script: 'plot scan = close > 1;', market: 'us', asOf: null }
    RESP.push(ERR401)
    RESP.push({ ok: true, status: 200, data: { hits: ['2026-09-10'], evaluated: 250, unknown: 0 } })
  `, ctx)
  const h1 = await vm.runInContext(`hitDaysOf('AAA')`, ctx)
  const h2 = await vm.runInContext(`hitDaysOf('AAA')`, ctx)
  auditCheck('额度', '⭐命中日 401 不缓存:登录后再悬停能算出来', /登录/.test(h1.error) && Array.isArray(h2.scan) && h2.scan[0] === '2026-09-10', [h1, h2])
  const h3 = await vm.runInContext(`hitDaysOf('AAA')`, ctx)
  auditCheck('额度', '算成功的命中日会缓存(不重复请求)', h3 === h2 && ctx.SENT.filter(x => /hit-days/.test(x.url)).length === 2)
})

// K · 字段插入、复制名字规则、emoji 偏移、注释里的参数名
auditJob('杂项', async () => {
  const ctx = scCtx()
  vm.runInContext(`
    var ED = { value: 'abc def', selectionStart: 3, selectionEnd: 3, focus: function () {}, setSelectionRange: function (a, b) { ED.sel = [a, b] } }
    var _gid = document.getElementById
    document.getElementById = function (id) { return id === 'mg-input' ? ED : _gid(id) }
    insertField({ target: { dataset: { f: 'close' } } })
    var K_INS = [S.input, ED.sel.join(',')]
    ED.value = 'abcdef'; ED.selectionStart = 1; ED.selectionEnd = 4
    insertField({ target: { dataset: { f: 'RSI' } } })
    K_INS.push(S.input)
    insertField({ target: { dataset: { fam: 'EMA' } } })
    var K_FAM = S.fieldOpen.EMA === true
    S.conditions = [{ name: 'c1', kind: 'def', expr: '1' }, { name: 'c1_2', kind: 'def', expr: '1' }]
    S.termHost = 'c1_3'; S.extraPlots = [{ name: 'c1_4', expr: 'c1' }]
    var K_UNIQ = uniqName('c1')
  `, ctx)
  auditCheck('杂项', '字段插在光标处,光标移到插入之后', ctx.K_INS[0] === 'abccloseemsp def'.replace('emsp', '') && ctx.K_INS[1] === '8,8', ctx.K_INS)
  auditCheck('杂项', '有选区时替换选区', ctx.K_INS[2] === 'aRSIef', ctx.K_INS)
  auditCheck('杂项', '点族标签只展开不插入', ctx.K_FAM === true && ctx.K_INS.length === 3)
  auditCheck('杂项', '新名字避开已有条件、宿主与前面的 plot', ctx.K_UNIQ === 'c1_5', ctx.K_UNIQ)
  // emoji 偏移:后端给码点偏移,前端要按 UTF-16 切
  const ctx2 = scCtx()
  const expr = 'volume > Average(volume, 20) # 量能😀 放大\n  * 7'
  const cp = Array.from(expr).indexOf('7')
  ctx2.EXPR = expr; ctx2.CP = cp
  vm.runInContext(`
    applyParsed({ combine: 'all', plot_name: 'scan', plot_refs: ['v'], plot_order: ['v'], conditions: [
      { name: 'v', expr: EXPR, kind: 'def', is_bool: true, tokens: [{ k: 'num', t: '7', s: CP, e: CP + 1 }] } ] })
  `, ctx2)
  await vm.runInContext(`onNumChange({ target: { classList: { contains: function () { return true } }, dataset: { c: '0', t: '0' }, value: '8' } })`, ctx2)
  const sent = ctx2.SENT[0] && ctx2.SENT[0].body.script
  auditCheck('杂项', '⭐注释里有 emoji 时改数字改对位置(* 7 → * 8)', /\* 8;/.test(sent) && /放大/.test(sent) && !/8\*|> 8/.test(sent), sent)
  vm.runInContext(`
    S.conditions = [{ name: 'minStreak', kind: 'input', expr: '3' }, { name: 'c', kind: 'term', expr: 'g >= minStreak # minStreak(小盘5)' },
                    { name: 'd', kind: 'def', expr: 'close > 1 # 不用 minStreak' }]
    var K_W = withParams(S.conditions[1].expr)
    var K_A = applyParams(K_W)
    var K_CP = copySnippet(S.conditions[2])
  `, ctx)
  auditCheck('杂项', '⭐注释里的参数名不加值', ctx.K_W === 'g >= minStreak(3) # minStreak(小盘5)', ctx.K_W)
  auditCheck('杂项', '⭐注释里写着「参数名(文字)」也能保存', !ctx.K_A.err && ctx.K_A.text === 'g >= minStreak # minStreak(小盘5)', ctx.K_A)
  auditCheck('杂项', '只在注释里提到的参数不复制', ctx.K_CP.params === 0)
})

Promise.all(auditJobs).then(() => {
  for (const [group, name, ok, detail] of AUDIT_RESULTS) {
    if (ok) console.log('PASS 审计补测 ·', group, '·', name)
    else { failed++; console.log('FAIL 审计补测 ·', group, '·', name, detail !== undefined ? ' | ' + (typeof detail === 'string' ? detail : JSON.stringify(detail)).slice(0, 600) : '') }
  }
  console.log('INFO 审计补测 · 共', AUDIT_RESULTS.length, '条')
})

// ─── 筛选器 · 编辑框里的参数值(2026-09-15)────────────────────────────────
// 用户截图:条件行显示 totalReturn 大于等于 minTotalReturn(0.80),点 ✎ 编辑框里只有 `totalReturn >= minTotalReturn`,
// 阈值看不到也改不了。编辑框要带「参数名(当前值)」,保存时值写回 input、脚本里还原成参数名
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(sc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    applyParsed({
      combine: 'all', plot_name: 'scan', term_host: 'FOMO_Setup', plot_refs: [], plot_order: ['FOMO_Setup#1', 'FOMO_Setup#2'],
      conditions: [
        { name: 'minTotalReturn', expr: '0.80', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '0.80', s: 0, e: 4 }] },
        { name: 'minStreak', expr: '3', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '3', s: 0, e: 1 }] },
        { name: 'minStreak2', expr: '5', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '5', s: 0, e: 1 }] },
        { name: 'FOMO_Setup#1', title: 'totalReturn', expr: 'totalReturn >= minTotalReturn', kind: 'term', is_bool: true, tokens: [{ k: 'ref', t: 'totalReturn' }] },
        { name: 'FOMO_Setup#2', title: 'greenStreak', expr: 'greenStreak >= minStreak and greenStreak < minStreak2 * minStreak', kind: 'term', is_bool: true, tokens: [{ k: 'ref', t: 'greenStreak' }] },
      ],
    })
    S.editing = 'FOMO_Setup#1'
    var EP_HTML = vConditions()
    var EP_W = [withParams('totalReturn >= minTotalReturn'), withParams('greenStreak >= minStreak and greenStreak < minStreak2 * minStreak'),
                withParams('x.minStreak > 1 and minStreak[1] > 0 and minStreak(3) > 0'), withParams('close > 10')]
    var EP_A = applyParams('totalReturn >= minTotalReturn(0.9)')
    var EP_B = applyParams('greenStreak >= minStreak( 4 ) and greenStreak < minStreak2(6) * minStreak')
    var EP_C = applyParams('greenStreak >= minStreak(4) and greenStreak < minStreak(5)')
    var EP_D = applyParams('totalReturn >= minTotalReturn(close)')
    var EP_E = applyParams('totalReturn >= Average(close, 20)')
    var EP_RT = applyParams(withParams('greenStreak >= minStreak and greenStreak < minStreak2 * minStreak'))
    var EP_CP = [copySnippet(S.conditions[3]), copySnippet(S.conditions[4]),
                 copySnippet({ name: 'up', kind: 'def', expr: 'close > close[1]' }),
                 copySnippet({ name: 'ma', kind: 'def', expr: 'Average(close, minStreak2)' })]
    var EP_COPY_HTML = vConditions()
  `, ctx, { filename: 'assert-edit-params' })
  const html = String(ctx.EP_HTML)
  const W = ctx.EP_W, A = ctx.EP_A, B = ctx.EP_B, C = ctx.EP_C, D = ctx.EP_D, E = ctx.EP_E, RT = ctx.EP_RT, CP = ctx.EP_CP
  const ep = [
    ['⭐编辑框里带参数当前值', /<textarea class="cd-edit" id="cd-edit" spellcheck="false">totalReturn &gt;= minTotalReturn\(0\.80\)<\/textarea>/.test(html)],
    ['编辑框提示参数可直接改', /参数\(括号里的数\)直接改/.test(html)],
    ['同一个参数出现两次都带值', W[1] === 'greenStreak >= minStreak(3) and greenStreak < minStreak2(5) * minStreak(3)'],
    ['整词匹配:x.minStreak / minStreak[1] / 已带括号的不动', W[2] === 'x.minStreak > 1 and minStreak[1] > 0 and minStreak(3) > 0'],
    ['没有参数的表达式原样', W[3] === 'close > 10'],
    ['⭐保存:括号里的值写回参数,表达式还原成参数名', A.text === 'totalReturn >= minTotalReturn' && A.changes.minTotalReturn === '0.9' && !A.err],
    ['保存:括号里有空格 / 两个参数 / 没带括号的同名参数', B.text === 'greenStreak >= minStreak and greenStreak < minStreak2 * minStreak' && B.changes.minStreak === '4' && B.changes.minStreak2 === '6' && !B.err],
    ['同一参数两个不同值 → 报错不保存', !!C.err && /两个不同的值/.test(C.err)],
    ['括号里不是数字 → 报错不保存', !!D.err && /只能填数字/.test(D.err)],
    ['真正的函数调用不受影响', E.text === 'totalReturn >= Average(close, 20)' && !Object.keys(E.changes).length && !E.err],
    ['不改值原样往返', RT.text === 'greenStreak >= minStreak and greenStreak < minStreak2 * minStreak' && RT.changes.minStreak === '3' && RT.changes.minStreak2 === '5' && !RT.err],
    ['⭐复制 term:前面带用到的参数 input 行', CP[0].text === 'input minTotalReturn = 0.80;\ntotalReturn >= minTotalReturn' && CP[0].params === 1],
    ['复制:多个参数按脚本里 input 的先后,同名参数只带一次,整词匹配', CP[1].text === 'input minStreak = 3;\ninput minStreak2 = 5;\ngreenStreak >= minStreak and greenStreak < minStreak2 * minStreak' && CP[1].params === 2],
    ['复制:没用参数的 def 原样一句', CP[2].text === 'def up = close > close[1];' && CP[2].params === 0],
    ['复制:函数参数里的参数也带上', CP[3].text === 'input minStreak2 = 5;\ndef ma = Average(close, minStreak2);'],
    ['复制按钮说明会带参数', /title="复制这一条的脚本文本\(连同它用到的参数和中间定义,粘回生成框可直接用\)"/.test(String(ctx.EP_COPY_HTML))],
  ]
  // 2026-09-17:复制出来的片段要自带**间接**依赖(rng3m 用到 minRng、c_depth 用到 rng3m),粘回生成框才认得。
  // 顺序:参数在前(按脚本先后)、中间定义其次(按脚本先后)、这一条最后;没用到的定义 / 条件不带
  vm.runInContext(`
    S.conditions = [
      { name: 'minRng', kind: 'input', expr: '0.15' },
      { name: 'unused', kind: 'input', expr: '9' },
      { name: 'hi', kind: 'def', expr: 'High.3M' },
      { name: 'rng3m', kind: 'def', expr: '(hi - Low.3M) / hi' },
      { name: 'c_price', kind: 'def', expr: 'close > 10', is_bool: true },
      { name: 'c_depth', kind: 'def', expr: 'rng3m # 这里不用 unused\\n  >= minRng', is_bool: true },
      { name: 'scan#1', kind: 'term', expr: 'rng3m < 0.5', is_bool: true },
    ]
    var TD_CP = [copySnippet(S.conditions[5]), copySnippet(S.conditions[6]), copySnippet(S.conditions[4])]
  `, ctx)
  const TD = ctx.TD_CP
  ep.push(
    ['⭐复制带上间接用到的中间定义与参数(按脚本先后)', TD[0].text === 'input minRng = 0.15;\ndef hi = High.3M;\ndef rng3m = (hi - Low.3M) / hi;\ndef c_depth = rng3m # 这里不用 unused\n  >= minRng;' && TD[0].params === 1 && TD[0].defs === 2],
    ['复制 term 行也带中间定义', TD[1].text === 'def hi = High.3M;\ndef rng3m = (hi - Low.3M) / hi;\nrng3m < 0.5' && TD[1].defs === 2],
    ['没依赖的条件原样一句', TD[2].text === 'def c_price = close > 10;' && TD[2].params === 0 && TD[2].defs === 0],
    ['生成请求在追加时带上当前脚本当 context', /context: appending \? buildScript\(true\) : null/.test(sc)],
  )
  for (const [name, ok] of ep) {
    if (ok) console.log('PASS 编辑框参数值 ·', name)
    else { failed++; console.log('FAIL 编辑框参数值 ·', name, ' | ', JSON.stringify({ W, A, B, C, D, E, RT, CP }), html.slice(html.indexOf('cd-edit"'), html.indexOf('cd-edit"') + 160)) }
  }
} catch (e) {
  failed++
  console.log('FAIL 编辑框参数值定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 筛选器 · 条件宿主(2026-09-15)──────────────────────────────────────
// 用户第二份猎杀 FOMO:`def FOMO_Setup = a and b … and isGreen; plot scan = FOMO_Setup;`,界面显示「同时满足 1 个条件」
// 一整行,和脚本里写明的 7 个条件对不上。后端 term_host 把宿主的 and 项拆成条件行;前端回写必须按原结构
// (def 宿主 = 启用项;plot scan = 宿主),停用一项只从宿主里去掉,全停用写 false,宿主不能被写成 def FOMO_Setup#1
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(sc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var TH_D = {
      combine: 'all', plot_name: 'scan', term_host: 'FOMO_Setup', plot_refs: ['isGreen'],
      plot_order: ['FOMO_Setup#1', 'FOMO_Setup#2', 'isGreen'],
      conditions: [
        { name: 'minStreak', expr: '3', kind: 'input', is_bool: false, tokens: [{ k: 'num', t: '3', s: 0, e: 1 }] },
        { name: 'isGreen', expr: 'close > close[1]', kind: 'def', is_bool: true, tokens: [{ k: 'field', t: '收盘价' }, { k: 'op', t: '[1]' }] },
        { name: 'greenStreak', expr: 'if isGreen then greenStreak[1] + 1 else 0', kind: 'rec', is_bool: false, tokens: [{ k: 'kw', t: '如果' }, { k: 'op', t: '[1]' }] },
        { name: 'FOMO_Setup#1', title: 'greenStreak', expr: 'greenStreak >= minStreak', kind: 'term', is_bool: true, tokens: [{ k: 'ref', t: 'greenStreak' }] },
        { name: 'FOMO_Setup#2', title: 'isAccelerating', expr: '(isAccelerating or isLastMax)', kind: 'term', is_bool: true, paren: false, tokens: [{ k: 'ref', t: 'isAccelerating' }] },
      ],
      warnings: ['按自家全市场日线**逐根**求值', '<img src=x>**不含今天盘中**'],
    }
    applyParsed(TH_D)
    var TH_SCRIPT = buildScript(false)
    var TH_HTML = vConditions()
    S.conditions[3].enabled = false
    var TH_OFF = buildScript(false)
    var TH_ALL = buildScript(true)
    S.conditions.forEach(function (c) { if (c.is_bool) c.enabled = false })
    var TH_NONE = buildScript(false)
    applyParsed(Object.assign({}, TH_D, { term_host: '' }))
    var TH_NOHOST = buildScript(false)
  `, ctx, { filename: 'assert-term-host' })
  const html = String(ctx.TH_HTML)
  const s = String(ctx.TH_SCRIPT)
  const th = [
    ['宿主按原结构写回:def 宿主 = 各项 and', /def FOMO_Setup = greenStreak >= minStreak and \(isAccelerating or isLastMax\) and isGreen;/.test(s)],
    ['plot 仍只写宿主名', /plot scan = FOMO_Setup;$/.test(s)],
    ['term 不写成 def FOMO_Setup#k', !/def FOMO_Setup#/.test(s)],
    ['已带括号的 or 项不叠括号', !/\(\(isAccelerating/.test(s)],
    ['宿主 def 写在所有定义之后、plot 之前', s.indexOf('def FOMO_Setup =') > s.indexOf('def isGreen =') && s.indexOf('def FOMO_Setup =') > s.indexOf('rec greenStreak =')],
    ['表头条件数 = 宿主的项数(3)', /以下<b>3<\/b> 个条件/.test(html)],
    ['term 行悬停写明是宿主里的一项', /title="FOMO_Setup 里的一项"/.test(html)],
    ['停用一项:只从宿主里去掉', /def FOMO_Setup = \(isAccelerating or isLastMax\) and isGreen;/.test(String(ctx.TH_OFF))],
    ['停用后 buildScript(true) 仍含该项(rebuild 靠它不丢)', /def FOMO_Setup = greenStreak >= minStreak and/.test(String(ctx.TH_ALL))],
    ['全部停用:宿主写 false,plot 仍是宿主', /def FOMO_Setup = false;\nplot scan = FOMO_Setup;/.test(String(ctx.TH_NONE))],
    ['没有宿主时照旧写进 plot(老行为不变)', /plot scan = greenStreak >= minStreak and \(isAccelerating or isLastMax\) and isGreen;/.test(String(ctx.TH_NOHOST)) && !/def FOMO_Setup =/.test(String(ctx.TH_NOHOST))],
    ['提示里的 **重点** 渲染成粗体', /<li>按自家全市场日线<b>逐根<\/b>求值<\/li>/.test(html)],
    ['提示先转义再加粗(不注入 HTML)', /&lt;img src=x&gt;<b>不含今天盘中<\/b>/.test(html) && !/<img src=x>/.test(html)],
  ]
  for (const [name, ok] of th) {
    if (ok) console.log('PASS 条件宿主 ·', name)
    else { failed++; console.log('FAIL 条件宿主 ·', name, ' | ', JSON.stringify([s, String(ctx.TH_OFF), String(ctx.TH_NONE), String(ctx.TH_NOHOST)])) }
  }
} catch (e) {
  failed++
  console.log('FAIL 条件宿主定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 筛选器悬停日K:当前脚本的历史命中日(2026-09-13)──────────────────────
// 标记是异步来的(后端逐日回算),三件事钉住:
//   ① 用的是「跑出结果表的那份脚本」,不是条件区此刻的样子;
//   ② K 线先画、标记后到,中间鼠标换了票要丢掉旧标记(await 之后再比 seq);
//   ③ 0 天也写出来(alwaysScan),不然看不出是没算还是没命中。
{
  const sc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  const hk = [
    // 2026-09-14 起 market / asOf 在发请求那一刻就取下来(等 5 秒的这段时间里用户可能切了市场)
    ['runScan 记下跑出结果表的那份脚本', /S\.resultScan = \{ script: script, market: market, asOf: asOf, runAt: Date\.now\(\) \}/.test(sc)],
    ['筛选器把命中日来源挂到悬停日K 上', /KC\.markOf = hitDaysOf/.test(sc)],
    ['命中日请求带脚本、市场、代码、截止日', /post\(HITS_API, \{ script: sc\.script, market: sc\.market, code: code, as_of: sc\.asOf, in_result: inResult \}\)/.test(sc)],
    ['app.js:等标记之后再比一次 seq(防止画到别的票上)', /await KC\.markOf\(code, td\)[\s\S]{0,160}if \(seq !== KC\.seq\) return/.test(appJs)],
    ['app.js:0 天命中也写进图例', /mark\.alwaysScan/.test(appJs)],
    ['app.js:算不出的天数单独写', /天算不出/.test(appJs)],
    // 换票时不清头部,新票加载中会顶着上一只的现价和命中天数(2026-09-13 截图实测)
    ['app.js:换票先清上一只的现价 / 图例 / 区间,再去拉日线', /px0\.textContent = ''[\s\S]{0,200}lg0\.textContent = kcHint\(\)[\s\S]{0,120}rg0\.textContent = ''[\s\S]{0,600}await kcFetch\(code\)/.test(appJs)],
  ]
  for (const [name, ok] of hk) {
    if (ok) console.log('PASS 筛选器命中日 ·', name)
    else { failed++; console.log('FAIL 筛选器命中日 ·', name) }
  }
}

// ─── 悬停日K:命中日要「看得见」,不只是「画进去了」(2026-09-14)──────────────
// 用户报「有些命中的票图上没有蓝线」。数据和标记都对(ZD 命中 09-04 / 09-11,markArea 下标 245 / 249),
// 但 250 根挤在约 470px 里,一根 K 线宽、16% 透明度的色带肉眼看不见 —— 命中天数少又不连着的票就像没标。
// 现在每个命中日另加一条固定 2px 的竖线;色带和竖线都压在 K 线下面(z 小于蜡烛图的默认 2)。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  vm.runInContext(
    "var KV_ROWS = []; for (var i = 0; i < 250; i++) KV_ROWS.push({ ts: 'D' + String(i).padStart(9, '0'), open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 });" +
    "var KV_S = kcMarkSeries(KV_ROWS, { scan: [KV_ROWS[245].ts, KV_ROWS[249].ts, '2099-01-01'] });" +
    "var KV_E = kcMarkSeries(KV_ROWS, { scan: [] });",
    ctx, { filename: 'assert-kc-visible' })
  const s = ctx.KV_S.filter(function (x) { return x.markArea || x.markLine })[0]
  const kv = [
    ['命中日有一条带色带和竖线的标记序列', !!(s && s.markArea && s.markLine)],
    ['竖线固定 ≥ 2px(不随缩放变细)', !!(s && s.markLine.lineStyle && s.markLine.lineStyle.width >= 2)],
    ['竖线落在命中日的下标上,对不上 K 线的日子丢掉', !!(s && JSON.stringify(s.markLine.data.map(function (d) { return d.xAxis })) === '[245,249]')],
    ['色带与竖线一一对应', !!(s && s.markArea.data.length === s.markLine.data.length)],
    ['标记压在 K 线下面(z < 2),不挡读数', !!(s && s.z < 2)],
    ['竖线不画端点箭头和标签', !!(s && s.markLine.symbol && s.markLine.symbol[0] === 'none' && s.markLine.label && s.markLine.label.show === false)],
    ['没有命中日时不出标记序列', ctx.KV_E.length === 0],
  ]
  for (const [name, ok] of kv) {
    if (ok) console.log('PASS 命中日可见 ·', name)
    else { failed++; console.log('FAIL 命中日可见 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 命中日可见断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 悬停日K:左键拖动平移(仿 TradingView,2026-09-14)──────────────────────
// 用户要先把想看的那段拖到中间再滚轮放大。四件事钉住:
//   ① dataZoom 打开按住拖动平移,滚轮只缩放;② 拖动中划出弹层不关、划过别的代码不换票;
//   ③ 松手 / 窗口失焦都要结束拖动(否则 dragging 卡住,弹层再也关不掉);
//   ④ 同一只票重画(命中日后到)沿用拖动 / 缩放区间,不弹回全年。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  vm.runInContext(
    "var KD_ROWS = [{ ts: '2026-09-10', open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }, { ts: '2026-09-11', open: 1.5, high: 2, low: 1, close: 1.8, volume: 12 }];" +
    "var KD_A = kcOption(KD_ROWS, null).dataZoom[0];" +
    "var KD_B = kcOption(KD_ROWS, null, { start: 40, end: 60 }).dataZoom[0];",
    ctx, { filename: 'assert-kc-drag' })
  const css = fs.readFileSync(path.join(DIR, 'style.css'), 'utf8')
  const kd = [
    ['按住左键拖动平移已打开', ctx.KD_A.moveOnMouseMove === true],
    ['滚轮只缩放、不平移', ctx.KD_A.zoomOnMouseWheel === true && ctx.KD_A.moveOnMouseWheel === false],
    ['默认显示全年', ctx.KD_A.start === 0 && ctx.KD_A.end === 100],
    ['传入区间时沿用', ctx.KD_B.start === 40 && ctx.KD_B.end === 60],
    ['kcHide 拖动中不收起', /function kcHide\(\) \{\s*if \(KC\.dragging\) return/.test(appJs)],
    ['拖动中划过别的代码不换票', /if \(td && !KC\.dragging\) kcShow\(td\)/.test(appJs)],
    ['松手结束拖动', /document\.addEventListener\('mouseup', endDrag, true\)/.test(appJs)],
    ['窗口失焦也结束拖动', /window\.addEventListener\('blur', endDrag\)/.test(appJs)],
    ['同一只票重画先取拖动 / 缩放区间,再丢旧图(2026-09-16:首次视图改由 kcFocus 给,顺序不变)', appJs.includes('kcZoomOf(KC.chart) : kcFocus(rows, mark)') && appJs.indexOf('kcZoomOf(KC.chart) : kcFocus') < appJs.indexOf('kcDropChart()            // 顺序不能反')],
    ['新图记下是哪只票并带上区间', /KC\.chartCode = code\s*KC\.chart\.setOption\(kcOption\(rows, mark, zoom\)\)/.test(appJs)],
    ['图例提示写着左键拖动', ctx.KC_HINT === undefined ? /const KC_HINT = '左键拖动平移/.test(appJs) : /左键拖动/.test(ctx.KC_HINT)],
    ['光标:划过十字、拖动抓手', /\.kc-box \*\{cursor:crosshair!important\}/.test(css) && /\.kc-pop\.drag \.kc-box \*\{cursor:grabbing!important\}/.test(css)],
  ]
  for (const [name, ok] of kd) {
    if (ok) console.log('PASS 日K拖动 ·', name)
    else { failed++; console.log('FAIL 日K拖动 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 日K拖动断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 悬停日K:三年日线 + 默认对准这笔交易(2026-09-16 用户要求)──────────────
// 用户:「既然已经有 3 年数据了,K 线图上也支持 3 年,不然看不到前两年的买入点」。
// 三件事钉住:① 根数可配、看板设三年;② 缓存按根数分开(否则看板和筛选器串成一份);
// ③ 750 根不能全段铺开(一根不到 0.6px),默认对准买卖标记,筛选器(一年)照旧全段。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  vm.runInContext(
    "function KFROWS(n) { var a = []; for (var i = 0; i < n; i++) a.push({ ts: 'D' + String(i).padStart(9, '0'), open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }); return a; }" +
    "var KF_R3 = KFROWS(700), KF_R1 = KFROWS(250);" +
    "var KF_MARK = kcFocus(KF_R3, { buy: [KF_R3[100].ts], sell: [KF_R3[140].ts] });" +
    "var KF_ONE = kcFocus(KF_R1, { buy: [KF_R1[10].ts] });" +
    "var KF_NONE = kcFocus(KF_R3, null);" +
    "var KF_NEAR = kcFocus(KF_R3, { buy: [KF_R3[698].ts] });" +
    "var KF_LIM0 = kcLimit(); KC.limit = KC_LIMIT_3Y; var KF_LIM3 = kcLimit(); KC.limit = null;",
    ctx, { filename: 'assert-kc-3y' })
  const ag = fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8')
  const scr = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  const m = ctx.KF_MARK, none = ctx.KF_NONE, near = ctx.KF_NEAR
  const kf = [
    ['三年常量是 750 根', appJs.includes('const KC_LIMIT_3Y = 750')],
    ['首次打开排版后补量宽度(第一张图曾经空白)', appJs.includes('function kcFit(box)') && appJs.includes('kcFit(box)')],
    ['补量靠 ResizeObserver,不靠同步那一下(同步时容器还是 0)', appJs.includes('new ResizeObserver(') && appJs.includes('KC.ro.observe(box)')],
    ['根数可配:请求用 kcLimit()', appJs.includes("'?period=daily&limit=' + kcLimit()")],
    ['默认一年、看板设三年', ctx.KF_LIM0 === 250 && ctx.KF_LIM3 === 750],
    ['缓存按 代码@根数 分开', appJs.includes("const key = code + '@' + kcLimit()") && appJs.includes('KC.cache.set(key, out)')],
    ['重画沿用区间,首次用 kcFocus', appJs.includes('kcZoomOf(KC.chart) : kcFocus(rows, mark)')],
    ['一年以内不改默认视图(筛选器照旧全段)', ctx.KF_ONE === null],
    ['三年 + 有买卖标记 → 只显示标记附近', !!m && m.start > 6 && m.start < 10 && m.end > 23 && m.end < 27],
    ['窗口至少 120 根(太窄看不出形态)', !!near && (near.end - near.start) * 699 / 100 >= 119],
    ['标记贴最后一根时窗口不越界', !!near && near.end <= 100 && near.start >= 0],
    ['三年 + 没有标记 → 退回最近一年', !!none && none.start > 63 && none.start < 66 && none.end === 100],
    ['看板 boot 里设了三年', ag.includes('KC.limit = KC_LIMIT_3Y')],
    ['看板文案改成三年并说明默认对准这笔交易', ag.includes('三年的日K') && ag.includes('滚轮缩小看全程')],
    ['筛选器不设 KC.limit(蓝线只回算 250 天,给三年会像前两年没命中)', !scr.includes('KC.limit')],
  ]
  for (const [name, ok] of kf) {
    if (ok) console.log('PASS 三年日K ·', name)
    else { failed++; console.log('FAIL 三年日K ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 三年日K断言 ·', e && e.stack ? e.stack.split(String.fromCharCode(10)).slice(0, 3).join(' | ') : e)
}

// ─── 悬停日K:小鹿看板三层蓝线(2026-09-15)──────────────────────────────
// 候选池淡(1px)、形态就绪中(2px)、全满足深(2px),深的画在浅的后面(盖在上面);
// 没给 scanWeak 的(筛选器)照旧 2px —— 那边的蓝线是整份脚本真命中,不是粗筛池。
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  vm.runInContext(
    "var KL_ROWS = []; for (var i = 0; i < 30; i++) KL_ROWS.push({ ts: 'D' + String(i).padStart(9, '0'), open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 });" +
    "var KL_S = kcMarkSeries(KL_ROWS, { scan: [KL_ROWS[1].ts, KL_ROWS[2].ts, KL_ROWS[3].ts], scanWeak: true, setup: [KL_ROWS[2].ts, KL_ROWS[3].ts], entry: [KL_ROWS[3].ts], buy: [KL_ROWS[3].ts] });" +
    "var KL_SC = kcMarkSeries(KL_ROWS, { scan: [KL_ROWS[1].ts] });" +
    "var KL_NO = kcMarkSeries(KL_ROWS, { scan: [KL_ROWS[1].ts], scanWeak: true, setup: [], entry: [] });",
    ctx, { filename: 'assert-kc-layers' })
  const bands = ctx.KL_S.filter(function (x) { return x.markArea && x.markLine })
  const w = function (s) { return s && s.markLine.lineStyle.width }
  const col = function (s) { return s && s.markLine.lineStyle.color }
  // 顶层 const 不挂在 vm 的 context 对象上(ctx.KC_X 是 undefined),要在 context 里求值取出来
  const K = function (name) { return vm.runInContext(name, ctx) }
  const kl = [
    ['三层各一条标记序列', bands.length === 3],
    ['候选池那层 1px 淡线', w(bands[0]) === 1 && col(bands[0]) === K('KC_POOL_LINE')],
    ['形态就绪 2px 中蓝', w(bands[1]) === 2 && col(bands[1]) === K('KC_SETUP_LINE')],
    ['全满足 2px 深蓝,画在最后', w(bands[2]) === 2 && col(bands[2]) === K('KC_ENTRY_LINE')],
    ['三层颜色互不相同', new Set([col(bands[0]), col(bands[1]), col(bands[2])]).size === 3],
    ['各层落在自己的日子上', JSON.stringify(bands.map(function (s) { return s.markLine.data.map(function (d) { return d.xAxis }) })) === '[[1,2,3],[2,3],[3]]'],
    ['买卖标签照旧', ctx.KL_S.some(function (x) { return x.markPoint })],
    ['筛选器(没有 scanWeak)照旧 2px', ctx.KL_SC.length === 1 && w(ctx.KL_SC[0]) === 2 && col(ctx.KL_SC[0]) === K('KC_SCAN_LINE')],
    ['没有形态就绪 / 全满足日时只画候选池', ctx.KL_NO.length === 1],
  ]
  for (const [name, ok] of kl) {
    if (ok) console.log('PASS 三层蓝线 ·', name)
    else { failed++; console.log('FAIL 三层蓝线 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 三层蓝线断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 悬停日K:dispose 必须在清容器之前(app.js)────────────────────────
// 2026-09-12 线上 bug:kcRender 先 box.innerHTML='' 再 chart.dispose(),
// echarts 去 removeChild 自己那个已经被摘走的根节点 → TypeError 把 kcRender 打断,
// 表现是「头部和图例都填好了,图区一片空白」,而且只在第二只票起出现
// (第一只时还没有上一张图可丢)。假 DOM 里没有真 echarts,这个 bug 跑不出来 ——
// 只能拿源码顺序钉住它。
{
  const bad = /box\.innerHTML\s*=\s*''[\s\S]{0,80}?KC\.chart\.dispose\(\)/.test(appJs)
  if (!bad) console.log('PASS 悬停日K · dispose 排在清空容器之前')
  else { failed++; console.log('FAIL 悬停日K · 又变成先清容器再 dispose 了,第二只票起会空白') }
  // 两条清空容器的路径(kcMsg 与正常渲染)都必须先丢图。中间隔着注释,窗口放宽到 120 字符
  const paired = (appJs.match(/kcDropChart\(\)[\s\S]{0,120}?box\.innerHTML/g) || []).length
  if (/function kcDropChart/.test(appJs) && paired >= 2)
    console.log('PASS 悬停日K · 清容器的路径统一走 kcDropChart')
  else { failed++; console.log('FAIL 悬停日K · 有清容器的路径没走 kcDropChart(配上的只有', paired, '处)') }
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
    // 2026-09-14 起扫描要登录、结果等 5 秒再放:这里给个令牌,等待换成立即返回(等待本身在「会员额度」那组测)
    localStorage.setItem('hunter_token', 't')
    S.login = true                 // 页面加载时没令牌已经判成未登录了,这里模拟登录后
    revealAfter = async function () {}
    S.result = { picks: [{ code: 'X' }] }; S.probe = { c1: 3 }
    setAsOf('2026-08-15')
    var BAR_ON = vRunBar()
    var CLEARED = S.result === null && Object.keys(S.probe).length === 0
    var RES_ON = vResult()
  `, ctx, { filename: 'assert-asof-1' })
  // runScan 是 async,跑完再看
  const done = vm.runInContext(`runScan().then(function () { return probeOne(S.conditions[0]) })`, ctx, { filename: 'assert-asof-2' })
  // 2026-09-16 用户改了口径:回溯时 K 线**画到最新收盘**(「右侧范围也应该是到今天的前一天收盘」),
  // 回溯日改画一条竖线。原来那条「只保留回溯日及之前」的断言跟着改,截断的写法不许回来。
  const KC_ASOF = vm.runInContext(`
    (function () {
      const rows = [{ ts: '2026-08-14' }, { ts: '2026-08-15' }, { ts: '2026-08-18' }]
      const ss = kcMarkSeries(rows, { asOfDay: S.asOf })
      const ml = ss.filter(function (x) { return x.markLine && x.markLine.lineStyle.type === 'dashed' })
      return { idx: ml.length ? ml[0].markLine.data[0].xAxis : -1,
               color: ml.length ? ml[0].markLine.lineStyle.color : '',
               label: ml.length ? ml[0].markLine.label.formatter : '' }
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
        vm.runInContext(`S.result = { as_of: '2026-08-15', as_of_requested: '2026-08-15', universe_total: 4000, scanned: 4000, matched: 1, skipped_incomplete: 0, picks: [], columns: [], notes: [], warnings: [] }; var RES2 = vResult()`, ctx)
        return /回溯扫描结果/.test(ctx.RES2) && /截至 2026-08-15 收盘/.test(ctx.RES2) && /日线池/.test(ctx.RES2) && /panel asof/.test(ctx.RES2)
      })()],
      ['⭐没点时间回溯的正常扫描不标「回溯」(时间序列模式也带 as_of · 2026-09-16 用户指出)', (() => {
        vm.runInContext(`S.result = { as_of: '2026-09-15', as_of_requested: null, universe_total: 7424, scanned: 4058, matched: 0, skipped_incomplete: 2, picks: [], columns: [], notes: [], warnings: [] }; var RES3 = vResult()`, ctx)
        return !/回溯/.test(ctx.RES3) && !/panel asof/.test(ctx.RES3) && /扫描结果/.test(ctx.RES3) && /截至 2026-09-15 收盘/.test(ctx.RES3)
      })()],
      ['⭐官方示例普通扫描 0 命中:提示最近命中日 + 时间回溯按钮带日期(2026-09-16 用户要求)', (() => {
        vm.runInContext(`S.result = { official_preset: { key: 'fomo_short', name: '猎杀FOMO做空', market: 'us' }, as_of: '2026-09-15', as_of_requested: null, last_hit: { status: 'ready', date: '2026-08-20', matched: 3 }, universe_total: 7424, scanned: 4058, matched: 0, skipped_incomplete: 0, picks: [], columns: [], notes: [], warnings: [] }; var LH1 = vResult()`, ctx)
        return /最近一次扫描命中的日子为 <b>2026-08-20<\/b>/.test(ctx.LH1) && /id="sc-lasthit-go" data-date="2026-08-20"/.test(ctx.LH1) && /当天命中 3 只/.test(ctx.LH1)
      })()],
      ['最近命中日提示:回溯结果 / 非官方示例 / 有命中 / 查找失败都不显示', (() => {
        const base = "universe_total: 1, scanned: 1, skipped_incomplete: 0, picks: [], columns: [], notes: [], warnings: []"
        const off = "official_preset: { key: 'fomo_short', name: 'x', market: 'us' }"
        const ready = "last_hit: { status: 'ready', date: '2026-08-20', matched: 3 }"
        vm.runInContext(`var LH2 = [
          vLastHit({ ${off}, ${ready}, as_of_requested: '2026-09-01', matched: 0, ${base} }),
          vLastHit({ ${ready}, as_of_requested: null, matched: 0, ${base} }),
          vLastHit({ ${off}, ${ready}, as_of_requested: null, matched: 2, ${base} }),
          vLastHit({ ${off}, last_hit: { status: 'error' }, as_of_requested: null, matched: 0, ${base} }),
          vLastHit({ ${off}, last_hit: null, as_of_requested: null, matched: 0, ${base} })]`, ctx)
        return ctx.LH2.every(function (x) { return x === '' })
      })()],
      ['最近命中日提示:查找中 / 查完没有 各有说明', (() => {
        const base = "official_preset: { key: 'fomo_short', name: 'x', market: 'us' }, as_of_requested: null, matched: 0"
        vm.runInContext(`var LH3 = vLastHit({ ${base}, last_hit: { status: 'pending' } }); var LH4 = vLastHit({ ${base}, last_hit: { status: 'none', searched_days: 120, oldest: '2026-03-20' } })`, ctx)
        return /正在往前查找/.test(ctx.LH3) && /往前查了 120 个交易日\(到 2026-03-20\)/.test(ctx.LH4) && !/sc-lasthit-go/.test(ctx.LH4)
      })()],
      ['时间回溯弹窗支持预填日期(按钮当 click 回调时事件对象不当日期)', /typeof prefill === 'string'/.test(sc) && /value="' \+ esc\(initial\)/.test(sc)],
      ['⭐悬停日 K 回溯时画到最新收盘,不再截断(2026-09-16 用户要求)', !appJs.includes('rows.filter(function (r) { return String(r.ts || r.date ||') && appJs.includes('if (KC.asOf) mark = Object.assign(')],
      ['回溯日画竖线,落在回溯日那根上', KC_ASOF.idx === 1 && KC_ASOF.label === '回溯日'],
      ['竖线颜色与命中三层的蓝色分开', KC_ASOF.color === vm.runInContext('KC_ASOF_LINE', ctx)],
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

// ─── 脚本编译不过也要给「AI 修脚本」按钮,改动逐句摆在外面(2026-09-13)──────
// 前科:ThinkScript 脚本 17 句只错一处 average_volume_50d_calc,界面只有一行报错、没有 AI 按钮。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const sc = inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8'))
  sc.forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var FIX_SENT = []
    post = async function (url, body) {
      FIX_SENT.push(body)
      return { ok: false, status: 400, data: { detail: { message: '扫描源没有 average_volume_50d_calc', can_try_ai: true, kind: body.script.indexOf('def ') >= 0 ? 'script' : 'text' } } }
    }
    toast = function () {}
    localStorage.setItem('hunter_token', 't')     // 2026-09-14 起生成要登录
    S.login = true
    S.mode = 'replace'
    S.input = 'def c_v = average_volume_50d_calc > 1;\\nplot scan = c_v;'
  `, ctx, { filename: 'assert-fix-1' })
  const done = vm.runInContext(`generate().then(function () {
      FIX_MAGIC_SCRIPT = vMagic()
      S.input = '成交量大于一百万'
      return generate()
    }).then(function () { FIX_MAGIC_TEXT = vMagic() })`, ctx, { filename: 'assert-fix-2' })
  Promise.resolve(done).then(() => {
    vm.runInContext(`
      S.ai = { mode: 'fix', model: 'gemini-3.5-flash', attempts: 1, error: '扫描源没有 average_volume_50d_calc',
               script: 'def c_v = average_volume_60d_calc > 1;',
               changes: [{ name: 'def c_v', before: 'average_volume_50d_calc > 1', after: 'average_volume_60d_calc > 1' }] }
      var FIX_NOTE = vAiNote()
    `, ctx, { filename: 'assert-fix-3' })
    const note = ctx.FIX_NOTE
    const beforeFold = note.split('<details')[0]
    const checks = [
      ['⭐脚本编译不过 → 有「用 AI 修脚本」按钮', /id="mg-ai"/.test(ctx.FIX_MAGIC_SCRIPT) && /用 AI 修脚本/.test(ctx.FIX_MAGIC_SCRIPT)],
      ['大白话认不出 → 仍是「用 AI 识别」', /用 AI 识别/.test(ctx.FIX_MAGIC_TEXT) && !/用 AI 修脚本/.test(ctx.FIX_MAGIC_TEXT)],
      ['点生成本身不花 token(allow_ai=false)', ctx.FIX_SENT.length === 2 && ctx.FIX_SENT.every(b => b.allow_ai === false)],
      ['⭐改动逐句摆在折叠区外面', /average_volume_50d_calc &gt; 1|average_volume_50d_calc > 1/.test(beforeFold) && /average_volume_60d_calc/.test(beforeFold)],
      ['写明改了几句', /改了 1 句/.test(beforeFold)],
    ]
    for (const [name, ok] of checks) {
      if (ok) console.log('PASS AI 修脚本 ·', name)
      else { failed++; console.log('FAIL AI 修脚本 ·', name) }
    }
  }).catch(e => {
    failed++
    console.log('FAIL AI 修脚本定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
  })
} catch (e) {
  failed++
  console.log('FAIL AI 修脚本定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
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

// ─── 魔法筛选器 · 会员校验 / 额度 / 等 5 秒(2026-09-14)────────────────────
// 真正的拦截在接口层(screen_quota),这里盯前端别把三件事做丢:没登录立刻提醒、每用一次提示剩余、
// 扫描结果等 5 秒再显示且等待期间改了条件不许把旧结果当新结果。
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const scSrc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(scSrc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var MB = {}
    MB.bootLogin = S.login
    S.login = false
    MB.gateHtml = render()
    S.login = true
    S.conditions = [{ name: 'c1', expr: 'close > 20', is_bool: true, enabled: true }]
    S.quota = { login: true,
      scan: { kind: 'scan', label: '扫描', used: 20, limit: 20, remaining: 0, unlimited: false },
      ai:   { kind: 'ai', label: 'AI 识别', used: 3, limit: 10, remaining: 7, unlimited: false } }
    MB.bar0 = vRunBar()
    MB.aiLeft = aiLeftText()
    S.quota.scan.remaining = 12
    MB.bar12 = vRunBar()
    S.reveal = 3
    MB.revealHtml = vResult()
    MB.revealBar = vRunBar()
    S.reveal = 0
    localStorage.setItem('hunter_token', 't')
    var LAST = null, NEXT = null, REVEALED = null, TOASTS = []
    toast = function (m, k) { TOASTS.push(String(m)) }
    post = async function (u, b) { LAST = { url: u, body: b }; return NEXT }
    revealAfter = async function (s) { REVEALED = s; if (MB.mutate) S.conditions[0].expr = 'close > 30' }
    MB.done = (async function () {
      NEXT = { ok: true, status: 200, data: { matched: 5 } }
      await probeOne(S.conditions[0])
      MB.probeBody = LAST.body
      NEXT = { ok: true, status: 200, data: { matched: 9, rows: [],
        quota: { kind: 'scan', label: '扫描', used: 9, limit: 20, remaining: 11, unlimited: false } } }
      await runScan()
      MB.runRevealed = REVEALED
      MB.runResult = S.result && S.result.matched
      MB.runLeft = S.quota.scan.remaining
      MB.runToast = TOASTS.join(' | ')
      // 自用本地部署 SCREEN_SCAN_GAP_S=0:后端 quota 回 scan_gap_s 0 → 不倒数、直接出结果
      var gapBox = [], saveQ = S.quota
      S.quota = Object.assign({}, saveQ, { scan_gap_s: 0 }); gapBox.push(revealSeconds())
      S.quota = Object.assign({}, saveQ, { scan_gap_s: -1 }); gapBox.push(revealSeconds())
      S.quota = Object.assign({}, saveQ, { scan_gap_s: '0' }); gapBox.push(revealSeconds())
      S.quota = Object.assign({}, saveQ, { scan_gap_s: 2.5 }); gapBox.push(revealSeconds())
      S.quota = null; gapBox.push(revealSeconds())
      MB.gapSecs = gapBox
      S.quota = Object.assign({}, saveQ, { scan_gap_s: 0 })
      REVEALED = null; S.result = null
      NEXT = { ok: true, status: 200, data: { matched: 4, rows: [] } }
      await runScan()
      MB.gap0Revealed = REVEALED
      MB.gap0Result = S.result && S.result.matched
      MB.gap0Reveal = S.reveal
      S.quota = saveQ
      MB.mutate = true
      S.conditions[0].expr = 'close > 20'
      await runScan()
      MB.mutResult = S.result
      MB.mutErr = S.runError
      MB.mutate = false
      S.runError = null
      NEXT = { ok: false, status: 429, data: { detail: { kind: 'quota', message: '今天的扫描次数已经用完',
        quota: { kind: 'scan', label: '扫描', used: 20, limit: 20, remaining: 0, unlimited: false } } } }
      await runScan()
      MB.q429Err = S.runError
      MB.q429Left = S.quota.scan.remaining
      NEXT = { ok: false, status: 401, data: { detail: { message: '魔法筛选器是会员功能', need_login: true } } }
      S.quota.scan.remaining = 5
      await runScan()
      MB.after401 = S.login
    })()
  `, ctx, { filename: 'assert-member' })
  const MB = ctx.MB
  const sync = [
    ['没令牌打开页面立刻判成未登录(不等用户点按钮)', MB.bootLogin === false],
    ['未登录顶上常驻登录 / 注册提示', /class="sc-login"/.test(MB.gateHtml)],
    ['登录链接带 return_to 回到筛选器', /\/login\?return_to=%2Fstrategies%2Fscreener\.html/.test(MB.gateHtml)],
    ['注册链接也带 return_to', /\/register\?return_to=%2Fstrategies%2Fscreener\.html/.test(MB.gateHtml)],
    ['运行栏常驻今日剩余', /今日剩余 · 扫描 <b class="zero">0<\/b>\/20 · AI 识别 <b>7<\/b>\/10/.test(MB.bar0)],
    ['扫描用完:按钮禁用并写明', /id="sc-run" disabled>今天的扫描次数已用完/.test(MB.bar0)],
    ['还有次数时按钮可点', /id="sc-run">/.test(MB.bar12)],
    ['AI 按钮上写剩余次数', MB.aiLeft === '(今天还剩 7 次)'],
    ['倒数中结果区显示秒数、不出结果表', /id="sc-reveal-n">3</.test(MB.revealHtml) && !/<table/.test(MB.revealHtml)],
    ['倒数中运行按钮禁用', /id="sc-run" disabled>扫描完成 · <span id="sc-reveal-btn">3<\/span> 秒后显示/.test(MB.revealBar)],
    ['REVEAL_S 是 5', vm.runInContext('REVEAL_S', ctx) === 5],
    ['真的 revealAfter 不整页重画(只改两处数字)', /\['sc-reveal-n', 'sc-reveal-btn'\]/.test(scSrc)],
    ['app.js 有触屏:touchstart 监听', /addEventListener\('touchstart'/.test(appJs)],
    ['app.js 有触屏:触摸后补发的假鼠标事件被忽略', /if \(kcRecentTouch\(\)\) return/.test(appJs)],
    ['app.js 有触屏:✕ 关闭', /class="kc-x"/.test(appJs) && /function kcClose\(\)/.test(appJs)],
    ['app.js 触屏打开的弹层不因「移开」收起', /if \(KC\.touch\) return\s+\/\/ 手指点开/.test(appJs)],
    ['style.css:图区 touch-action:none、弹层不超屏宽',
      /\.kc-box\{touch-action:none\}/.test(fs.readFileSync(path.join(DIR, 'style.css'), 'utf8')) &&
      /\.kc-pop\{max-width:calc\(100vw - 16px\)/.test(fs.readFileSync(path.join(DIR, 'style.css'), 'utf8'))],
  ]
  for (const [name, ok] of sync) {
    if (ok) console.log('PASS 会员额度 ·', name)
    else { failed++; console.log('FAIL 会员额度 ·', name) }
  }
  vm.runInContext(`
    KC.touch = true; var HINT_T = kcHint(); KC.touch = false; var HINT_M = kcHint()
    kcEl(); KC.touch = true; KC.cur = 'X|'; kcClose(); var CLOSED = { touch: KC.touch, cur: KC.cur }
  `, ctx, { filename: 'assert-touch' })
  const tchecks = [
    ['触屏时图例提示换成手势说明', /双指缩放/.test(ctx.HINT_T) && /滚轮/.test(ctx.HINT_M)],
    ['kcClose 清掉触屏状态', ctx.CLOSED.touch === false && ctx.CLOSED.cur === null],
  ]
  for (const [name, ok] of tchecks) {
    if (ok) console.log('PASS 触屏日K ·', name)
    else { failed++; console.log('FAIL 触屏日K ·', name) }
  }
  MB.done.then(() => {
    const as = [
      ['单条测试带 probe:true(不扣扫描次数)', MB.probeBody && MB.probeBody.probe === true],
      ['扫描后调用等待 5 秒', MB.runRevealed === 5],
      ['scan_gap_s:0 → 0 秒;负数 / 字符串 / 没额度 → 按 5;小数向上取整',
        JSON.stringify(MB.gapSecs) === JSON.stringify([0, 5, 5, 3, 5])],
      ['scan_gap_s 为 0:不调 revealAfter、直接出结果', MB.gap0Revealed === null && MB.gap0Result === 4 && !MB.gap0Reveal],
      ['等完才放结果', MB.runResult === 9],
      ['返回的剩余次数记上', MB.runLeft === 11],
      ['每用一次提示剩余', /本次扫描计 1 次,今天还剩 11 \/ 20 次/.test(MB.runToast)],
      ['等待期间改了条件:不显示旧结果并说明', MB.mutResult === null && /等待期间条件改过了/.test(MB.mutErr || '')],
      ['429 用完:显示原因并把剩余记成 0', /用完/.test(MB.q429Err || '') && MB.q429Left === 0],
      ['401:切回未登录', MB.after401 === false],
    ]
    for (const [name, ok] of as) {
      if (ok) console.log('PASS 会员额度 ·', name)
      else { failed++; console.log('FAIL 会员额度 ·', name, JSON.stringify({ q: MB.q429Err, m: MB.mutErr })) }
    }
  }).catch((e) => { failed++; console.log('FAIL 会员额度异步断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e) })
} catch (e) {
  failed++
  console.log('FAIL 会员额度定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 列头排序不重新扫描、不扣次数(2026-09-14 用户要求 · GN-045)──────────────────────
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  const scSrc = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  inlineScripts(scSrc).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var RS = {}, POSTS = [], NEXT = null
    localStorage.setItem('hunter_token', 't'); S.login = true
    post = async function (u, b) { POSTS.push(b); return NEXT }
    revealAfter = async function () { RS.revealed = true }
    toast = function (m) { RS.toast = String(m) }
    S.quota = { login: true, scan: { kind: 'scan', label: '扫描', remaining: 7, limit: 20 }, ai: { kind: 'ai', label: 'AI 识别', remaining: 1, limit: 10 } }
    S.resultScan = { script: 'def c = close > 1;\\nplot scan = c;', market: 'us', asOf: null }
    S.result = { matched: 3, columns: ['pe'], picks: [
      { code: 'A', close: 1, fields: { pe: 5 } }, { code: 'B', close: 3, fields: { pe: null } }, { code: 'C', close: 2, fields: { pe: 9 } }] }
    RS.done = (async function () {
      S.sortBy = 'pe'; S.desc = true; await resortResult()
      RS.localDesc = S.result.picks.map(function (p) { return p.code }).join(''); RS.localPosts = POSTS.length
      S.desc = false; await resortResult()
      RS.localAsc = S.result.picks.map(function (p) { return p.code }).join('')
      S.result = { matched: 500, columns: [], picks: [{ code: 'A', close: 1, fields: {} }] }
      NEXT = { ok: true, status: 200, data: { matched: 500, columns: [], resorted: true, picks: [{ code: 'Z', close: 9, fields: {} }] } }
      S.sortBy = 'close'; S.desc = true; await resortResult()
      RS.remote = { body: POSTS[POSTS.length - 1], first: S.result.picks[0].code, quota: S.quota.scan.remaining }
      NEXT = { ok: false, status: 409, data: { detail: { kind: 'resort_expired', message: '这张结果已经超过 10 分钟,排序要重新运行一次扫描' } } }
      const posts0 = POSTS.length
      await resortResult()
      RS.expired = { toast: RS.toast, still: S.result.picks[0].code, oneRequest: POSTS.length - posts0 }
    })()
  `, ctx, { filename: 'assert-resort' })
  const RS = ctx.RS
  const syncChecks = [
    ['列头点击不再调用 runScan,改为 resortResult', /dataset\.sort[\s\S]{0,160}resortResult\(\)/.test(scSrc) && !/dataset\.sort[\s\S]{0,160}\brunScan\(\)/.test(scSrc)],
  ]
  for (const [name, ok] of syncChecks) {
    if (ok) console.log('PASS 列头排序 ·', name)
    else { failed++; console.log('FAIL 列头排序 ·', name) }
  }
  RS.done.then(() => {
    const as = [
      ['全部命中都在表里:前端直接排、不发请求', RS.localPosts === 0 && RS.localDesc === 'CAB'],
      ['升序时空值仍排最后', RS.localAsc === 'ACB'],
      ['命中比表里多:请求带 resort:true 与排序参数', RS.remote.body && RS.remote.body.resort === true && RS.remote.body.sort_by === 'close' && RS.remote.body.descending === true],
      ['用后端重排结果替换表', RS.remote.first === 'Z'],
      ['⭐重排不扣次数、不走 5 秒倒数', RS.remote.quota === 7 && !RS.revealed],
      ['缓存过期:提示要重新运行、不自动重跑、表保持不变', /重新运行/.test(RS.expired.toast || '') && RS.expired.still === 'Z' && RS.expired.oneRequest === 1],
    ]
    for (const [name, ok] of as) {
      if (ok) console.log('PASS 列头排序 ·', name)
      else { failed++; console.log('FAIL 列头排序 ·', name, JSON.stringify(RS)) }
    }
  }).catch((e) => { failed++; console.log('FAIL 列头排序异步断言 ·', e && e.message) })
} catch (e) {
  failed++
  console.log('FAIL 列头排序定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 官方示例原样运行不扣扫描次数(2026-09-14 用户要求)──────────────────────────
try {
  const ctx = vm.createContext(makeContext('screener.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  inlineScripts(fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')).forEach((src, i) => vm.runInContext(src, ctx, { filename: `screener#${i + 1}` }))
  vm.runInContext(`
    var OP = {}
    localStorage.setItem('hunter_token', 't'); S.login = true
    S.quota = { login: true, scan: { kind: 'scan', label: '扫描', remaining: 0, limit: 20 }, ai: { kind: 'ai', label: 'AI 识别', remaining: 3, limit: 10 } }
    applyParsed({ conditions: [{ name: 'c1', expr: 'close > 20', is_bool: true }], plot_refs: ['c1'], combine: 'all',
                  official_preset: { key: 'uptrend', name: '上升趋势', market: 'us' } })
    S.market = 'us'
    OP.barOfficial = vRunBar()
    S.market = 'hk'
    OP.barOtherMarket = vRunBar()
    S.market = 'us'
    // 只在本地切开关、没有重新解析:标记还在,但脚本变了 → 也不能再说不计次数(2026-09-14 线上实测出过)
    applyParsed({ conditions: [{ name: 'c1', expr: 'close > 20', is_bool: true }, { name: 'c2', expr: 'RSI < 70', is_bool: true }],
                  plot_refs: ['c1', 'c2'], combine: 'all', official_preset: { key: 'uptrend', name: '上升趋势', market: 'us' } })
    S.conditions[1].enabled = false
    OP.barToggledLocal = vRunBar()
    applyParsed({ conditions: [{ name: 'c1', expr: 'close > 21', is_bool: true }], plot_refs: ['c1'], combine: 'all', official_preset: null })
    OP.barModified = vRunBar()
    var TOASTS = []
    toast = function (m) { TOASTS.push(String(m)) }
    revealAfter = async function () {}
    post = async function () { return { ok: true, status: 200, data: { matched: 3, picks: [], columns: [], official_preset: { key: 'uptrend', name: '上升趋势', market: 'us' } } } }
    OP.done = (async function () {
      applyParsed({ conditions: [{ name: 'c1', expr: 'close > 20', is_bool: true }], plot_refs: ['c1'], combine: 'all', official_preset: { key: 'uptrend', name: '上升趋势', market: 'us' } })
      await runScan()
      OP.toasts = TOASTS.slice(); OP.left = S.quota.scan.remaining
    })()
  `, ctx, { filename: 'assert-official' })
  const OP = ctx.OP
  const checks = [
    ['原样的官方示例:次数用完仍可运行', /id="sc-run">/.test(OP.barOfficial) && !/今天的扫描次数已用完/.test(OP.barOfficial)],
    ['原样的官方示例:运行栏写明不计次数', /官方示例「上升趋势」· 原样运行不计扫描次数/.test(OP.barOfficial)],
    ['换了市场就不算官方示例(按钮照常禁用)', /id="sc-run" disabled>今天的扫描次数已用完/.test(OP.barOtherMarket) && !/原样运行不计/.test(OP.barOtherMarket)],
    ['改过条件就不算官方示例', /id="sc-run" disabled>今天的扫描次数已用完/.test(OP.barModified) && !/原样运行不计/.test(OP.barModified)],
    ['⭐只在本地关掉一条(没重新解析)提示也消失', /id="sc-run" disabled>今天的扫描次数已用完/.test(OP.barToggledLocal) && !/原样运行不计/.test(OP.barToggledLocal)],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 官方示例免费 ·', name)
    else { failed++; console.log('FAIL 官方示例免费 ·', name) }
  }
  OP.done.then(() => {
    const ok = OP.toasts.some(t => /官方示例「上升趋势」原样运行,不计扫描次数/.test(t)) && OP.left === 0 &&
      !OP.toasts.some(t => /本次扫描计 1 次/.test(t))
    if (ok) console.log('PASS 官方示例免费 · 运行后提示不计次数、剩余次数不变')
    else { failed++; console.log('FAIL 官方示例免费 · 运行后提示', JSON.stringify(OP.toasts), OP.left) }
  }).catch((e) => { failed++; console.log('FAIL 官方示例免费异步断言 ·', e && e.message) })
} catch (e) {
  failed++
  console.log('FAIL 官方示例免费定向断言 ·', e && e.stack ? e.stack.split('\n').slice(0, 3).join(' | ') : e)
}

// ─── 标签页图标(2026-09-14 · 第一轮评审 GN-001:页面标题的图标也要是猎鹿人 logo)──────────
// 静态页不走 Next 的 app/icon.png,不写 <link rel="icon"> 浏览器就显示空白图标。与主站同一个 /icon.png
for (const page of ['index.html', 'factors.html', 'workbench.html', 'backtest.html', 'data.html', 'agent.html', 'screener.html']) {
  const html = fs.readFileSync(path.join(DIR, page), 'utf8')
  const ok = /<link rel="icon" href="\/icon.png" type="image\/png" \/>/.test(html.split('</head>')[0] || '')
  if (ok) console.log('PASS 标签页图标 ·', page)
  else { failed++; console.log('FAIL 标签页图标 ·', page, '没有声明 /icon.png') }
}

// ─── 小鹿 · 按线的货币符号(2026-09-17 A 股「涨停后强势整理」线)──────────
// 看板金额跟 d.currency.symbol 走,研究台每条线跟 ln.currency 走;没给就是美元(老接口 / 美股线)。
// 前科风险:money() 原来写死 '$',A 股线的 1 万人民币会显示成 $10,000
try {
  const ctx = vm.createContext(makeContext('agent.html'))
  vm.runInContext(appJs, ctx, { filename: 'app.js' })
  inlineScripts(fs.readFileSync(path.join(DIR, 'agent.html'), 'utf8'))
    .forEach((src, i) => vm.runInContext(src, ctx, { filename: `agent-ccy#${i + 1}` }))
  vm.runInContext(`
    var CCY_BASE = { state:'running', paper:true, version:'v1', day_count:2,
      strategy:{ name:'涨停后强势整理(A 股)', version:'v1', summary:'x', market_label:'A股' },
      guardrails:{ initial_capital:1000000, long_only:true, triggered_today:false },
      overview:{ pnl_abs:-12345, pnl_pct:-1.23, equity:987655, cash:900000, benchmark_symbol:'沪深300' },
      nav:{ points:[], benchmark_symbol:'沪深300' }, rules:[], holdings:{ items:[] }, watchlist:{ items:[] },
      trades:{ items:[] }, versions:[], lessons:[] }
    var H_CNY = render(Object.assign({}, CCY_BASE, { currency:{ symbol:'¥', code:'CNY', unit:'元', market:'a' },
      guardrails:{ initial_capital:1000000, long_only:true, triggered_today:false, guards_off:true,
                   daily_loss_halt_pct:null, consecutive_loss_pause:null } }))
    var H_USD = render(CCY_BASE)
    var H_RS_CCY = rsCard({ key:'limitup', label:'涨停后强势整理', status:'backtest', status_text:'全年回测',
      branches:[{ key:'limitup', label:'基准' }], best_branch:'limitup', currency:{ symbol:'¥' },
      metrics:{ pnl_pct:-1, excess_pt:null, max_dd_pct:-2, cycles:30, win_rate:40, expectancy_net:-25 } })
    var H_RS_USD = rsCard({ key:'donchian', label:'唐奇安', status:'backtest', status_text:'全年回测',
      branches:[{ key:'donchian', label:'基准' }], best_branch:'donchian',
      metrics:{ pnl_pct:-1, excess_pt:null, max_dd_pct:-2, cycles:30, win_rate:40, expectancy_net:-437 } })
  `, ctx, { filename: 'assert-currency' })
  const checks = [
    ['A 股线看板金额用 ¥', /-¥12,345/.test(ctx.H_CNY) && /¥1,000,000/.test(ctx.H_CNY)],
    ['A 股线看板不出现 $', !/\$\d/.test(ctx.H_CNY)],
    ['没给 currency 仍是 $(美股线不变)', /-\$12,345/.test(ctx.H_USD) && !/¥/.test(ctx.H_USD)],
    ['渲染过 A 股线后再渲染美股线,符号换回 $', /\$1,000,000/.test(ctx.H_USD)],
    ['不设护栏的线写「不设」,不是 —', /单日亏损熔断 <b>不设<\/b>/.test(ctx.H_CNY) && /连亏停机 <b>不设<\/b>/.test(ctx.H_CNY)],
    ['研究台卡片按线的货币', /-¥25/.test(ctx.H_RS_CCY) && /-\$437/.test(ctx.H_RS_USD)],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 小鹿货币 ·', name)
    else { failed++; console.log('FAIL 小鹿货币 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 小鹿货币 · 断言脚本本身出错 ·', e && e.stack ? e.stack.split('\n')[0] : e)
}

// ─── 筛选器 · 扫描当日以结果表为准(2026-09-19)──────────────────────────
// 悬停日K 取命中日时要带 in_result(票在这次实时结果表里)、缓存键带 runAt(重扫就重取);有 note 的常驻显示
try {
  const src = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
  const appSrc = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8')
  const checks = [
    ['hit-days 请求带 in_result', /in_result:\s*inResult/.test(src)],
    ['回溯结果不传 in_result(同一份日线本来一致)', /const inResult = !sc\.asOf/.test(src)],
    ['HITS 缓存键带 runAt', /sc\.runAt/.test(src) && /runAt: Date\.now\(\)/.test(src)],
    ['有扫描当日可标时不当成算不出', /!\(Array\.isArray\(d\.hits\) && d\.hits\.length\)/.test(src)],
    ['图例常驻显示 noteShow', /mark\.noteShow/.test(appSrc)],
  ]
  for (const [name, ok] of checks) {
    if (ok) console.log('PASS 扫描当日蓝线 ·', name)
    else { failed++; console.log('FAIL 扫描当日蓝线 ·', name) }
  }
} catch (e) {
  failed++
  console.log('FAIL 扫描当日蓝线 · 断言脚本出错 ·', e && e.message)
}

// ─── 筛选器 · 结果表里的票日K 最后一根一定是蓝的(2026-09-19 用户)──────────────
// 从页面源码里抠出 pinLastBar,用假日K 真跑:快照日早于最后一根时补标挪到最后一根,真命中不挪,日K 落后时如实写明
;(async () => {
  try {
    const src = fs.readFileSync(path.join(DIR, 'screener.html'), 'utf8')
    const i = src.indexOf('async function pinLastBar')
    const j = src.indexOf('// 单独测试一条', i)
    const body = src.slice(i, j)
    const mk = (tsList) => new Function('kcFetch', body + '; return pinLastBar')(
      async () => ({ rows: tsList.map(function (t) { return { ts: t } }) }))
    const res = []
    let out = { scan: ['2026-09-11', '2026-09-17'] }
    await mk(['2026-09-17', '2026-09-18'])(out, { scan_day: '2026-09-17', scan_pinned: true, scan_note: 'x' }, 'LPCV')
    res.push(['快照日早于最后一根且是补标 → 挪到最后一根', out.scan.join() === '2026-09-11,2026-09-18' && /2026-09-18/.test(out.pinNote)])
    out = { scan: ['2026-09-17'] }
    await mk(['2026-09-17', '2026-09-18'])(out, { scan_day: '2026-09-17' }, 'X')
    res.push(['快照日是回算真命中 → 保留,最后一根另加', out.scan.join() === '2026-09-17,2026-09-18'])
    out = { scan: ['2026-09-18'] }
    await mk(['2026-09-18'])(out, { scan_day: '2026-09-18', scan_pinned: true, scan_note: '扫描当日 2026-09-18 按扫描结果标为命中' }, 'X')
    res.push(['快照日 = 最后一根 → 不动,补标说明常驻', out.scan.join() === '2026-09-18' && /按扫描结果/.test(out.pinNote)])
    out = { scan: ['2026-09-18'] }
    await mk(['2026-09-17'])(out, { scan_day: '2026-09-18', scan_pinned: true }, 'X')
    res.push(['日K 落后于快照日 → 不乱标,写明日K 只到哪天', out.scan.join() === '2026-09-18' && /只到 2026-09-17/.test(out.pinNote)])
    res.push(['hitDaysOf 对结果表里的票调 pinLastBar', /inResult && d\.scan_day\) await pinLastBar/.test(src)])
    const appSrc = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8')
    res.push(['图例有「算不出」时照样显示补标说明', /mark\.pinNote \|\| \(!mark\.unknown && mark\.noteShow\)/.test(appSrc)])
    for (const [name, ok] of res) {
      if (ok) console.log('PASS 最后一根蓝线 ·', name)
      else { failed++; console.log('FAIL 最后一根蓝线 ·', name) }
    }
  } catch (e) {
    failed++
    console.log('FAIL 最后一根蓝线 · 断言脚本出错 ·', e && e.message)
  }
})()

// setImmediate:上面有一组断言挂在 async 函数的 await 链上(微任务),同步退出会跳过它们
setImmediate(() => {
  console.log(failed ? `SOME FAILED (${failed})` : 'ALL OK')
  process.exit(failed ? 1 : 0)
})
