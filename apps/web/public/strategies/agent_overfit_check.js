// 只在测试进程构造数据，产品不使用这些数值。
const vm = require('vm'), fs = require('fs'), assert = require('assert');
const ctx = {window:{}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname+'/agent-overfit.js', 'utf8'),ctx);
const card = ctx.window.agentOverfitCard;
assert(card(null).includes('充分度 —'));
const report = {score:0,scope:'<img onerror=alert(1)>',checks:[{label:'样本',status:'missing',points:0,weight:10,detail:'<script>x</script>'}]};
let html = card(report);
assert(html.includes('0 / 100'));
assert(!html.includes('<img'));
assert(!html.includes('<script>'));
assert(html.includes('未验证'));
assert(card({...report,score:null}).includes('充分度 —'));
console.log('PASS 过拟合评分：零分、缺失、文本转义与检查明细');
