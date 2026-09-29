/* 浏览器交互回归：模拟 AI 回答，不写真实策略数据。 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const p=await browser.newPage({viewport:{width:1440,height:1100}}), errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  const base=process.env.HUNTER_TEST_URL||'http://192.168.64.10:8300';
  const rows=[{type:'breakout',params:{days:20},source:'突破20日最高价',confirmed:false},{type:'stop',params:{pct:5},source:'止损5%',confirmed:false}];
  let requests=[], fail=false;
  await p.route('**/agent/builder/recognize',route=>{
    const body=route.request().postDataJSON();requests.push(body);
    return route.fulfill({status:fail?400:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'测试识别失败'}:{rules:body.single?[{...rows[0],params:{days:30},source:'突破30日最高价'}]:rows})});
  });
  await p.goto(base+'/strategies/agent.html#view=research&create=1');
  await p.waitForFunction(()=>document.querySelector('#ac-execution')?.textContent.includes('已入库'));
  await p.locator('#rs-f-hyp').fill('突破20日最高价，止损5%');
  await p.locator('#ac-ai').click();await p.locator('[data-row="1"]').waitFor();
  await p.locator('[data-row="1"] [data-confirm]').check();
  await p.locator('[data-row="0"] [data-confirm]').check();
  await p.locator('[data-row="0"] [data-param="days"]').fill('30');
  if(await p.locator('[data-row="0"] [data-confirm]').isChecked())throw Error('编辑未撤销确认');
  if(!await p.locator('[data-row="1"] [data-confirm]').isChecked())throw Error('其他行确认被清空');
  await p.locator('[data-row="0"] [data-source]').fill('突破30日最高价');
  await p.locator('[data-row="0"] [data-retry]').click();
  await p.getByText('此条已重新识别，请重新确认',{exact:true}).waitFor();
  if(!requests[1].single||requests[1].text!=='突破30日最高价')throw Error('不是单条请求');
  if(!await p.locator('[data-row="1"] [data-confirm]').isChecked())throw Error('单条识别影响其他行');
  fail=true;await p.locator('[data-row="0"] [data-retry]').click();await p.getByText('测试识别失败',{exact:true}).waitFor();
  if(await p.locator('[data-row="0"] [data-param="days"]').inputValue()!=='30')throw Error('失败覆盖原规则');
  await p.locator('#ac-test').click();await p.getByText('请核对原文并确认成交口径',{exact:true}).waitFor();
  await p.setViewportSize({width:390,height:900});
  const overflow=await p.locator('#ac-editor').evaluate(e=>e.scrollWidth>e.clientWidth+4);
  if(overflow)throw Error('规则编辑器窄屏溢出');
  if(errors.length)throw Error(errors.join(';'));
  console.log('BUILDER_BROWSER_OK：逐条编辑、确认撤销、单条AI隔离、错误保留、回测闸门、窄屏');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
