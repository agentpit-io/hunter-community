// 日K 请求回归：不依赖自选股登记，市场、根数分别隔离缓存。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const src = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8')
const body = src.slice(src.indexOf('function kcSymbol('), src.indexOf('function kcMarkOf('))
const calls = []
const KC = { cache: new Map() }
let limit = 250
const api = new Function('KC', 'kcLimit', 'apiHeaders', 'fetch', body + '; return { kcFetch, kcSymbol }')(
  KC, () => limit, () => ({}), async url => {
    calls.push(url)
    return { json: async () => [{ ts: '2026-10-08', close: 1 }] }
  })
;(async () => {
  await api.kcFetch('PLTR', 'us')
  assert.equal(calls[0], '/api/kline/PLTR.US?period=daily&limit=250')
  await api.kcFetch('PLTR', 'us')
  assert.equal(calls.length, 1)
  await api.kcFetch('00700', 'hk')
  assert.equal(calls[1], '/api/kline/00700.HK?period=daily&limit=250')
  await api.kcFetch('00700', 'a')
  assert.equal(calls[2], '/api/kline/00700?period=daily&limit=250')
  limit = 750
  await api.kcFetch('PLTR', 'us')
  assert.equal(calls[3], '/api/kline/PLTR.US?period=daily&limit=750')
  assert.equal(api.kcSymbol('BRK.B', 'us'), 'BRK.B.US')
  assert.equal(api.kcSymbol('PLTR.US', 'us'), 'PLTR.US')
  assert.equal(api.kcSymbol('600519', 'a'), '600519')
  assert.equal(api.kcSymbol('PLTR'), 'PLTR')
  const html = fs.readFileSync(path.join(__dirname, 'screener.html'), 'utf8')
  assert.match(html, /data-kmarket=.*S\.resultScan\.market/)
  assert.match(html, /pinLastBar\(out, d, code, sc\.market\)/)
  assert.match(html, /await kcFetch\(code, market\)/)
  console.log('PASS 日K市场请求、缓存隔离、显式后缀和蓝线复用')
})().catch(e => { console.error(e); process.exitCode = 1 })
