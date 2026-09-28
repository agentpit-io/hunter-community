// 编辑器行为检查；测试数据只在此脚本中，不进入产品。
const vm = require('vm'), fs = require('fs'), assert = require('assert');
let posts = [], failSave = false;
const fields = {amount:{value:'10000'},hold_days:{value:'1'},board:{value:'growth'},
 require_ma5:{checked:true},amp_max:{value:'15'},no_all_shrink:{checked:true}};
const submit = {}, run = {}, msg = {}, version = {}, results = {querySelectorAll:()=>[]};
const form = {elements:fields, querySelector:()=>submit, querySelectorAll:()=>Object.values(fields),
 addEventListener:(name,fn)=>{form[name]=fn;}};
const content = {querySelector:s=>content.innerHTML.includes('id="'+s.slice(1)+'"') ? ({'#mr-form':form,'#mr-run':run,'#mr-msg':msg,'#mr-version':version,
 '#mr-results':results,'#mr-start':{value:'2025-09-25'},'#mr-end':{value:'2026-09-25'}}[s]) : null};
const close = {};
let dialog;
const cfg = {version:0,data_first:'2025-01-01',data_last:'2026-09-25',runs:[],
 editable:{amount:10000,hold_days:1,growth_only:true,main_only:false,require_ma5:true,amp_max:15,no_all_shrink:true}};
const context={window:{},document:{getElementById:()=>null,body:{appendChild:()=>{}},createElement:()=>{
 dialog={style:{},querySelector:s=>s==='#mr-close'?close:content,showModal:()=>{dialog.open=true;},addEventListener:()=>{},remove:()=>{}};return dialog;}},
 apiHeaders:x=>x, clearTimeout:()=>{},setTimeout:()=>0,Date,console,Blob,URL,
 fetch:async(url,options)=>{
  if(options.method==='POST'){
   const b=JSON.parse(options.body);posts.push({url,b});
   if(failSave) return {ok:false,status:400,json:async()=>({detail:'版本冲突'})};
   if(!url.endsWith('backtest')) cfg.version++;
   return {ok:true,json:async()=>({version:cfg.version,id:'run1'})};
  }
  return {ok:true,json:async()=>JSON.parse(JSON.stringify(cfg))};
 }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/agent-rules.js','utf8'),context);
(async()=>{
 await context.window.openAgentRules('limitup');
 assert.match(dialog.innerHTML, /<h2>编辑规则<\/h2>/);
 assert.match(content.innerHTML, /class="btn primary" id="mr-save"/);
 assert(!content.innerHTML.includes('id="mr-run"'));
 assert(!content.innerHTML.includes('id="mr-start"'));
 await form.onsubmit({preventDefault(){}});
 assert.match(msg.textContent,/保存成功/);
 fields.amount.value='20000';form.input();
 assert.equal(posts.length,1);
 failSave=true;await form.onsubmit({preventDefault(){}});
 assert.match(msg.textContent,/版本冲突/);
 failSave=false;await form.onsubmit({preventDefault(){}});
 assert.equal(posts.at(-1).b.params.amount,20000);
 await context.window.openAgentBacktest('limitup');
 assert.match(dialog.innerHTML, /<h2>手动回测<\/h2>/);
 assert(!content.innerHTML.includes('id="mr-form"'));
 assert.equal(run.disabled,false);
 await run.onclick();assert.match(posts.at(-1).url,/backtest$/);
 assert.equal(posts.at(-1).b.version,2);
 cfg.version=0;
 await context.window.openAgentBacktest('limitup');
 assert.equal(run.disabled,true);
 const n=posts.length;await run.onclick();assert.equal(posts.length,n);
 console.log('PASS 独立编辑与回测、保存按钮、失败提示、未保存禁止回测、版本绑定');
})().catch(e=>{console.error(e);process.exitCode=1;});
