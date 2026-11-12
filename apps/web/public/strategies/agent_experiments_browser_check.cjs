/* 隔离浏览器测试：API 测试替身仅存在于此脚本，不进入产品。 */
const fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
 const browser=await chromium.launch({headless:true,...(process.env.TEST_BROWSER_PATH ? {executablePath:process.env.TEST_BROWSER_PATH} : {})});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const ident='00000000000000000000000000000001';let created=false,starts=0,phase='planning';
  const run={id:ident,goal:'检验量能过滤',failure_rule:'不改善则放弃',start:'2025-01-01',end:'2025-12-31',baseline:{amount:10000},
   candidates:[{id:'volume-off',label:'不排除连续缩量',reason:'检验量能过滤是否排除了有效信号',params:{amount:10000,no_all_shrink:false}}],
   results:[],note:'历史探索，不是独立样本外验证',criteria:{min_closed:30,min_days:120},implementation:{}};
  await page.route('http://hunter.test/**',async route => {
   const url=new URL(route.request().url());let payload;
   if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:'<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>.btn{padding:10px 15px;border:1px solid #acbdad;border-radius:7px;background:white;cursor:pointer}.primary{background:#20583c;color:white}body{font-family:Arial,sans-serif}</style><button data-experiment-assistant>实验助手</button><script>function apiHeaders(x){return x||{}}</script><script src="/agent-experiments.js"></script></html>'});
   if(url.pathname==='/agent-experiments.js') return route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'agent-experiments.js'),'utf8')});
   if(url.pathname.endsWith('/experiments') && route.request().method()==='POST') {
    assert.equal(route.request().postDataJSON().goal,'检验量能过滤');created=true;payload={id:ident,status:'planning'};
   } else if(url.pathname.endsWith('/experiments')) {
    payload={items:created ? [{...run,status:phase}] : [],trials:starts ? 2 : 0,budget:20,ai_requests:created ? 1 : 0,ai_budget:20,max_candidates:3,data_range:{first:'2024-01-01',last:'2025-12-31'}};
   } else if(url.pathname.endsWith('/run')) {
    starts++;phase='done';await new Promise(r=>setTimeout(r,150));payload={id:ident,status:'queued'};
   } else {
    phase=phase==='planning' ? 'ready' : phase;
    payload={...run,status:phase,...(phase==='done' ? {results:[{id:'baseline',label:'固定基准',status:'done',result:{net_pnl:0,return_pct:0,max_drawdown_pct:0,closed:0}}],comparison:[]} : {})};
   }
   return route.fulfill({contentType:'application/json',body:JSON.stringify(payload)});
  });
  await page.goto('http://hunter.test/');await page.getByRole('button',{name:'实验助手',exact:true}).click();
  await page.getByLabel('研究目标').fill('检验量能过滤');await page.getByLabel('什么结果出现就放弃').fill('不改善则放弃');
  await page.getByRole('button',{name:'提出候选实验',exact:true}).click();
  await page.waitForSelector('[data-ex-run]');
  assert(await page.getByText('不排除连续缩量',{exact:true}).count());
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  if(process.env.TEST_SCREENSHOT_DIR) {
   fs.mkdirSync(process.env.TEST_SCREENSHOT_DIR,{recursive:true});
   await page.screenshot({path:path.join(process.env.TEST_SCREENSHOT_DIR,'experiment-mobile.png')});
  }
  await page.setViewportSize({width:1280,height:900});
  if(process.env.TEST_SCREENSHOT_DIR) await page.screenshot({path:path.join(process.env.TEST_SCREENSHOT_DIR,'experiment-desktop.png')});
  await page.getByRole('button',{name:'确认候选，运行对照回测'}).click();
  await page.waitForSelector('table');assert.equal(starts,1);
  assert((await page.locator('table').textContent()).includes('0.00'));
  await page.getByRole('button',{name:'关闭',exact:true}).click();assert.equal(await page.locator('dialog').count(),0);
  await page.getByRole('button',{name:'实验助手',exact:true}).click();
  await page.locator('[data-ex-open]').click();await page.waitForSelector('table');
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS 浏览器：创建候选、确认回测、真实零值、历史重开、390px 布局，无 JS 异常（API 使用测试替身）');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
