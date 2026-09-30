/* 模拟取消接口，不停止用户真正的识别任务。 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const p=await browser.newPage(),base=process.env.HUNTER_TEST_URL||'http://192.168.64.10:8300';
  let busy=true,finish=null,cancels=0;
  await p.route(base+'/**',async r=>r.fulfill({response:await r.fetch()}));
  if(process.env.HUNTER_TEST_LOCAL_ASSETS)await p.route('**/agent-create.js',r=>r.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'agent-create.js'),'utf8')}));
  await p.route('**/agent/builder/recognize/status',r=>r.fulfill({json:{running:busy}}));
  await p.route('**/agent/builder/recognize/cancel',async r=>{cancels++;busy=false;if(finish)finish();await r.fulfill({json:{stopped:true}})});
  await p.route('**/agent/builder/recognize',async r=>{busy=true;await new Promise(resolve=>{finish=resolve});await r.fulfill({status:409,json:{detail:'已停止识别，原始输入和已有规则已保留'}})});
  await p.goto(base+'/strategies/agent.html#view=research&create=1');
  await p.locator('#ac-stop').waitFor();await p.locator('#ac-stop').click();
  await p.locator('#ac-stop').waitFor({state:'hidden'});
  if(cancels!==1)throw Error('刷新后无法停止已有识别');
  await p.locator('#ac-prompt').fill('止损5%');await p.locator('#ac-ai').click();
  await p.waitForFunction(()=>document.querySelector('#ac-ai-status').dataset.loading==='true');
  if(await p.locator('#ac-stop').isDisabled())throw Error('加载时停止按钮不可用');
  await p.waitForTimeout(100);await p.locator('#ac-stop').click();
  await p.waitForFunction(()=>!document.querySelector('#ac-ai').disabled);
  if(cancels!==2||await p.locator('#ac-prompt').inputValue()!=='止损5%'||await p.locator('#ac-ai-status').getAttribute('data-loading')!=='false')throw Error('取消未恢复状态或丢失输入');
  console.log('CANCEL_BROWSER_OK：刷新可停止旧任务、识别中停止按钮可用、取消恢复按钮并保留原文');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
