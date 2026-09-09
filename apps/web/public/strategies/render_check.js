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
  : ['index.html', 'factors.html', 'workbench.html', 'backtest.html', 'data.html']

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

console.log(failed ? `SOME FAILED (${failed})` : 'ALL OK')
process.exit(failed ? 1 : 0)
