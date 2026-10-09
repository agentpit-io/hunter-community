/* 真正执行渲染器，验证缺数、失败、转义与候选确认入口。 */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={window:{},document:{addEventListener(){}},console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname+'/agent-experiments.js','utf8'),context);
const render=context.window.agentExperimentRender;
const base={goal:'<img src=x onerror=alert(1)>',failure_rule:'不改善则放弃',start:'2025-01-01',end:'2025-06-01',note:'研究口径',
 baseline:{},candidates:[{id:'amp-tight',label:'候选',reason:'检验整理结构'}],results:[]};
const ready=render({...base,status:'ready'});
assert(ready.includes('data-ex-run'));
assert(ready.includes('&lt;img'));
assert(!ready.includes('<img'));
for(const state of ['planning','queued','running','done','failed','cancelled']) {
 const output=render({...base,status:state,progress:null,results:[{id:'x',label:'候选',status:'failed'}]});
 assert(!output.includes('data-ex-run'));
 assert(!/NaN|undefined|>null</.test(output));
 assert(output.includes('—'));
 assert(!output.includes('0.00'));
}
const completed=render({...base,status:'done',results:[{id:'baseline',label:'基准',status:'done',result:{net_pnl:0,return_pct:0,max_drawdown_pct:0,closed:0}}]});
assert(completed.includes('0.00'));
assert(!completed.includes('data-ex-cancel'));
assert(completed.includes('data-ex-download'));
console.log('PASS 实验助手渲染：缺数、真实零值、失败、转义、确认与下载');
