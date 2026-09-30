/* 创建策略：每条编辑、重新识别及确认，回测只使用后端保存的版本快照。 */
(function () {
  'use strict'
  const API = '/api/quant/agent/builder'
  const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
  let current = null
  async function request(path, data, method) {
    const r = await fetch(API + path, {method:method || (data === undefined ? 'GET':'POST'), headers:apiHeaders({'Content-Type':'application/json'}), ...(data === undefined ? {} : {body:JSON.stringify(data)})})
    let d; try { d = await r.json() } catch (_) { throw Error('服务返回异常，请稍后重试') }
    if (!r.ok) throw Error(r.status === 401 ? '请重新登录后再试' : d.detail || '请求失败，请稍后重试')
    return d
  }
  function mount() {
    const root = document.getElementById('rs-f-rules')
    if (!root || document.getElementById('ac-editor')) return
    const languages = {text:'自然语言 · 中文 / English',python:'Python',javascript:'JavaScript',thinkscript:'ThinkScript',pine:'Pine Script',cpp:'C++',rust:'Rust',my:'My语言'}
    const presets = [
      ['均线趋势','收盘价高于20日均线时买入，跌破20日均线时卖出。止损5%，单票仓位上限20%，单笔风险上限1%，最多持仓5只。'],
      ['放量突破','收盘突破前20日最高价，且成交量达到前20日均量的1.5倍时买入。止损5%，止盈15%，单票仓位上限20%，单笔风险上限1%，最多持仓5只。'],
      ['移动止损','从持仓最高收盘价回撤5%时卖出。'],
      ['时间退出','持有达到15个交易日时卖出。'],
      ['识别已有代码','请按左侧代码逐条整理买入、卖出、止损和仓位规则；缺失参数或不支持的逻辑保持待确认，不要补写。']
    ]
    const workspace=document.createElement('div');workspace.id='ac-workspace'
    workspace.innerHTML='<section class="ac-code"><div class="ac-toolbar"><b>规则编辑器</b><label>输入语言 <select id="ac-language" aria-label="输入语言">'+Object.entries(languages).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('')+'</select></label></div><div id="ac-code-body"></div><div class="ag-note ac-code-note">支持粘贴多种语言代码，由 AI 转成规则后确认；不直接执行原代码。</div></section><aside class="ac-assistant"><div class="ac-toolbar"><b>✦ AI 规则助手</b></div><p class="ag-note">写下交易规则，或点击预设填入。也可以结合左侧已有代码进行识别。</p><div class="ac-presets">'+presets.map((p,i)=>'<button type="button" class="btn small" data-ai-preset="'+i+'">'+p[0]+'</button>').join('')+'</div><label for="ac-prompt">告诉 AI 你想怎么交易</label><textarea id="ac-prompt" maxlength="2000" rows="6" placeholder="例如：突破前20日高点买入，止损5%。未确定的参数可以稍后逐条补充。"></textarea><button type="button" class="btn primary small" id="ac-ai">AI 识别为规则</button><button type="button" class="btn small" id="ac-stop" hidden style="margin-left:8px">停止识别</button><div id="ac-ai-status" role="status" aria-live="polite" aria-atomic="true" hidden></div><p class="ag-note">预设仅填入，不自动识别。识别前可修改所有参数。</p></aside>'
    root.before(workspace);workspace.querySelector('#ac-code-body').appendChild(root)
    root.rows=14;root.spellcheck=false;root.placeholder='输入交易规则，或粘贴 Python / JavaScript / ThinkScript / Pine 等策略代码…'
    const box = document.createElement('div'); box.id = 'ac-editor'
    box.innerHTML = '<style>#ac-editor{margin-top:12px;min-width:0;overflow-wrap:anywhere}#ac-editor .ac-row{border:1px solid var(--line);border-radius:9px;padding:12px;margin:10px 0;background:var(--bg,#fafaf8)}#ac-editor .ac-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}#ac-editor label{display:inline-block;margin-right:14px}#ac-editor input[type=checkbox]{width:auto}#ac-editor input[type=number],#ac-editor input[type=date]{width:150px}#ac-editor .ac-state{color:var(--brand);font-weight:bold}#ac-result{white-space:pre-wrap;max-height:400px;overflow:auto}#ac-msg{color:var(--brand);white-space:pre-wrap}#ac-editor select{max-width:100%}@media(max-width:640px){.rs-f{grid-template-columns:1fr}#ac-editor label{max-width:100%;margin-right:0}}</style>' +
      '<style>#ac-rule-results[hidden],#ac-code-body[hidden],.ac-rule-detail[hidden]{display:none} .ac-rule-tabs{display:flex;gap:8px;padding:10px 14px;border-bottom:1px solid var(--line)}.ac-rule-tabs [aria-pressed=true]{color:var(--brand);border-color:var(--brand);background:var(--bg)}#ac-rule-results{padding:12px;min-height:330px}#ac-rule-results>.ac-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}#ac-rows{max-height:520px;overflow:auto;margin-top:10px}#ac-rows .ac-row{border:1px solid var(--line);border-radius:8px;margin:7px 0;padding:10px;background:white}#ac-rows .ac-row-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}#ac-rows .ac-rule-summary{flex:1;min-width:120px;overflow:hidden}#ac-rows .ac-rule-title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}#ac-rows .ac-rule-params{font-size:12px;color:var(--muted);overflow-wrap:anywhere}#ac-rows .ac-state{color:var(--brand);font-size:12px}#ac-rows .ac-row-tools{display:flex;align-items:center;gap:5px;flex-wrap:wrap}#ac-rows .ac-row-tools label{font-size:12px;white-space:nowrap}#ac-rows input[type=checkbox]{width:auto}#ac-rows .ac-rule-detail{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}#ac-rows .ac-rule-detail label{display:block;font-size:12px;margin:8px 0}#ac-rows .ac-rule-detail input{width:120px}#ac-rows .ac-row-msg{color:var(--brand);font-size:12px;overflow-wrap:anywhere}#ac-workspace{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(270px,1fr);border:1px solid var(--line);border-radius:12px;overflow:hidden;min-width:0}.ac-code,.ac-assistant{min-width:0}.ac-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px 14px;border-bottom:1px solid var(--line);background:var(--bg)}.ac-toolbar label{display:flex;align-items:center;gap:8px;font-size:13px}.ac-toolbar select{width:auto;max-width:100%}#rs-f-rules{font-family:Consolas,monospace;line-height:1.7;border:0;border-radius:0;min-height:330px;resize:vertical;tab-size:2}.ac-code-note{padding:10px 14px}.ac-assistant{padding:0 14px 14px;background:var(--bg);border-left:1px solid var(--line)}.ac-assistant .ac-toolbar{margin:0 -14px 12px}.ac-assistant label{display:block;margin:16px 0 8px}.ac-presets{display:flex;gap:8px;flex-wrap:wrap}#ac-prompt{min-height:130px;resize:vertical}#ac-stop[hidden]{display:none}#ac-ai{margin-top:12px}#ac-ai-status{margin-top:10px;color:var(--brand);font-size:13px;line-height:1.6;overflow-wrap:anywhere}#ac-ai-status[data-loading=true]:before{content:"";display:inline-block;width:12px;height:12px;margin-right:8px;border:2px solid var(--line);border-top-color:var(--brand);border-radius:50%;vertical-align:middle;animation:ac-spin .8s linear infinite}@keyframes ac-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){#ac-ai-status[data-loading=true]:before{animation:none}}.ac-count{text-align:right;font-size:12px;color:var(--muted);margin-top:4px}@media(max-width:850px){#ac-workspace{grid-template-columns:1fr}.ac-assistant{border-left:0;border-top:1px solid var(--line)}}@media(max-width:640px){.ac-toolbar label{display:block}.ac-toolbar select{width:100%}}</style>'+
      '<div class="ac-actions"><b>逐条核对规则</b><button type="button" class="btn small" id="ac-add">手动添加一条</button></div>' +
      '<div class="ag-note">AI 只整理受支持规则；没有写清的参数保持待确认。买入条件全部满足，卖出条件任一满足。修改后必须重新确认。</div>' +
      '<div class="ac-actions"><select id="ac-saved" aria-label="我的个人策略"><option value="">加载已保存的个人策略…</option></select><button type="button" class="btn small" id="ac-load">加载</button><button type="button" class="btn small" id="ac-new">另建策略</button></div>' +
      '<div id="ac-personal-list"></div><div id="ac-msg" role="status"></div><div id="ac-rows"></div>' +
      '<div class="ac-actions"><label>回测开始 <input type="date" id="ac-start"></label><label>回测结束 <input type="date" id="ac-end"></label><label>初始资金（美元） <input type="number" id="ac-initial" value="100000" min="1000" max="10000000"></label><label>单边滑点（基点） <input type="number" id="ac-slip" value="5" min="0" max="100"></label></div>' +
      '<div class="ag-note" id="ac-execution"></div><label><input type="checkbox" id="ac-confirm"> 我已核对原始描述没有遗漏，并确认成交口径、费用与滑点设置</label>' +
      '<div class="ac-actions"><button type="button" class="btn primary small" id="ac-test">保存确认版本并回测</button><button type="button" class="btn small" id="ac-refresh">刷新回测状态</button></div><div id="ac-result"></div>'
    workspace.after(box)
    const state = {rows:[], schema:[], id:null, version:null, busy:false, timer:null, items:[], editing:null}
    if (current && current.timer) clearTimeout(current.timer)
    current = state
    const el = id => document.getElementById(id)
    const codeBody=el('ac-code-body'), tabs=document.createElement('div')
    tabs.className='ac-rule-tabs'
    tabs.innerHTML='<button type="button" class="btn small" id="ac-tab-source" aria-pressed="true">规则原文 / 代码</button><button type="button" class="btn small" id="ac-tab-rows" aria-pressed="false">识别结果 <span id="ac-rule-count">0</span></button>'
    codeBody.before(tabs)
    const results=document.createElement('div');results.id='ac-rule-results';results.hidden=true
    results.appendChild(el('ac-add').parentElement)
    results.appendChild(box.querySelector('.ag-note'))
    results.appendChild(el('ac-rows'))
    codeBody.after(results)
    function showRules(show=true){codeBody.hidden=show;results.hidden=!show;el('ac-tab-source').setAttribute('aria-pressed',String(!show));el('ac-tab-rows').setAttribute('aria-pressed',String(show))}
    el('ac-tab-source').onclick=()=>showRules(false)
    el('ac-tab-rows').onclick=()=>showRules(true)
    const active = () => document.getElementById('ac-editor') === box
    const msg = s => { if(active()) el('ac-msg').textContent = s }
    const description=el('rs-f-hyp'), counter=document.createElement('div');counter.className='ac-count';counter.id='ac-description-count';description.after(counter)
    const countDescription=()=>{const chars=Array.from(description.value);if(chars.length>100)description.value=chars.slice(0,100).join('');counter.textContent=Array.from(description.value).length+' / 100 · 选填'}
    description.addEventListener('input',countDescription);countDescription()
    const sourceInput=()=>[root.value,el('ac-prompt').value].filter(x=>x.trim()).join('\n\n')
    const inputFingerprint=()=>JSON.stringify([root.value,el('ac-prompt').value,el('ac-language').value])
    const revoke=()=>{state.rows.forEach(r=>r.confirmed=false);invalidate();renderRows()}
    workspace.querySelectorAll('[data-ai-preset]').forEach(b=>b.onclick=()=>{if(el('ac-prompt').value.trim()&&!confirm('用此预设替换右侧输入？左侧内容会保留。'))return;el('ac-prompt').value=presets[+b.dataset.aiPreset][1];revoke();el('ac-prompt').focus()})
    el('ac-prompt').addEventListener('input',revoke)
    el('ac-language').addEventListener('change',revoke)
    root.addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();root.setRangeText('  ',root.selectionStart,root.selectionEnd,'end');root.dispatchEvent(new Event('input',{bubbles:true}))}})
    function personalList() {
      el('ac-personal-list').innerHTML=state.items.map(x=>'<div class="ac-row"><b>'+escape(x.name)+'</b> · v'+x.version+'<div class="ac-actions"><button type="button" class="btn small" data-personal-load="'+escape(x.id)+'">加载策略</button><button type="button" class="btn small" data-personal-delete="'+escape(x.id)+'">删除</button></div></div>').join('')
      el('ac-personal-list').querySelectorAll('[data-personal-load]').forEach(b=>b.onclick=()=>{el('ac-saved').value=b.dataset.personalLoad;el('ac-load').click()})
      el('ac-personal-list').querySelectorAll('[data-personal-delete]').forEach(b=>b.onclick=()=>action(async()=>{
        const id=b.dataset.personalDelete, item=state.items.find(x=>x.id===id)
        if(!confirm('删除个人策略「'+item.name+'」？\n删除后不再出现在列表，历史回测快照保留。'))return
        await request('/'+id,undefined,'DELETE')
        state.items=state.items.filter(x=>x.id!==id)
        Array.from(el('ac-saved').options).filter(o=>o.value===id).forEach(o=>o.remove())
        if(state.id===id){state.id=null;state.version=null;state.rows=[];el('rs-f-label').value='';description.value='';countDescription();root.value='';el('ac-prompt').value='';el('ac-language').value='text';invalidate();renderRows();el('ac-result').textContent=''}
        personalList();msg('策略已删除')
      }))
    }
    function invalidate() { if(active()) el('ac-confirm').checked = false }
    async function action(fn, report=msg) {
      if(state.busy) return
      state.busy = true
      ;[box,workspace].forEach(n=>n.querySelectorAll('button:not(#ac-stop)').forEach(b=>b.disabled=true))
      try { await fn() } catch(e) { report(e.message || '请求失败') }
      finally { state.busy=false; if(active()) [box,workspace].forEach(n=>n.querySelectorAll('button:not(#ac-stop)').forEach(b=>b.disabled=false)) }
    }
    function renderRows() {
      if (!active()) return
      el('ac-rows').innerHTML = state.rows.map((r,i)=>{
        const spec=state.schema.find(x=>x.type===r.type)
        const params=spec&&r.type!=='pending'?spec.label+' · '+spec.fields.map(f=>f.label+' '+(r.params[f.key]??'—')).join(' / '):'待补充或暂不支持，不能直接回测'
        return '<div class="ac-row" data-row="'+i+'"><div class="ac-row-head"><span class="ac-state">'+(i+1)+' · '+(r.confirmed?'已确认':'待确认')+'</span><div class="ac-rule-summary"><div class="ac-rule-title" title="'+escape(r.source)+'">'+escape(r.source||'请编辑此条规则')+'</div><div class="ac-rule-params">'+escape(params)+'</div></div><div class="ac-row-tools"><button type="button" class="btn small" data-edit aria-expanded="'+(state.editing===r)+'">'+(state.editing===r?'收起':'编辑')+'</button><button type="button" class="btn small" data-retry>AI 重新识别</button><button type="button" class="btn small" data-remove>删除</button><label><input data-confirm type="checkbox"'+(r.confirmed?' checked':'')+(r.type==='pending'?' disabled':'')+'>确认</label></div></div><div class="ac-row-msg" role="status"></div><div class="ac-rule-detail"'+(state.editing===r?'':' hidden')+'>'+
          '<label>原始描述（修改后可单条重新识别）</label><textarea data-source rows="3">'+escape(r.source)+'</textarea>'+
          '<label>规则类型</label><select data-type>'+state.schema.map(x=>'<option value="'+x.type+'"'+(x.type===r.type?' selected':'')+'>'+escape(x.label)+'</option>').join('')+'</select>'+
          (spec?spec.fields.map(f=>'<label>'+escape(f.label)+' <input data-param="'+f.key+'" type="number" min="'+f.min+'" max="'+f.max+'" step="'+(f.integer?'1':'any')+'" value="'+escape(r.params[f.key])+'"></label>').join(''):'')+'</div></div>'
      }).join('')
      el('ac-rule-count').textContent=state.rows.length
      if(!state.rows.length)el('ac-rows').innerHTML='<div class="ag-note">尚无规则，可在右侧识别或手动添加。</div>'
      el('ac-rows').querySelectorAll('[data-row]').forEach(card=>{
        const i=+card.dataset.row, r=state.rows[i]
        card.querySelector('[data-edit]').onclick=()=>{state.editing=state.editing===r?null:r;renderRows()}
        function change() {r.confirmed=false;card.querySelector('[data-confirm]').checked=false;card.querySelector('.ac-state').textContent=(i+1)+' · 待确认';card.querySelector('.ac-rule-title').textContent=r.source||'请编辑此条规则';card.querySelector('.ac-rule-title').title=r.source;const spec=state.schema.find(x=>x.type===r.type);card.querySelector('.ac-rule-params').textContent=spec&&r.type!=='pending'?spec.label+' · '+spec.fields.map(f=>f.label+' '+(r.params[f.key]??'—')).join(' / '):'待补充或暂不支持，不能直接回测';invalidate()}
        card.querySelector('[data-source]').oninput=e=>{r.source=e.target.value;change()}
        card.querySelector('[data-type]').onchange=e=>{r.type=e.target.value;r.params={};change();renderRows()}
        card.querySelectorAll('[data-param]').forEach(inp=>{inp.oninput=()=>{r.params[inp.dataset.param]=inp.value===''?null:Number(inp.value);change()}})
        card.querySelector('[data-confirm]').onchange=e=>{
          const spec=state.schema.find(x=>x.type===r.type)
          const valid=r.source.trim() && spec && spec.fields.every(f=>typeof r.params[f.key]==='number' && Number.isFinite(r.params[f.key]) && r.params[f.key]>=f.min && r.params[f.key]<=f.max && (!f.integer||Number.isInteger(r.params[f.key])))
          if(e.target.checked&&!valid){e.target.checked=false;msg('请补齐此条参数，检查数值范围后再确认');return}
          r.confirmed=e.target.checked;invalidate();renderRows()
        }
        card.querySelector('[data-remove]').onclick=()=>{state.rows.splice(i,1);invalidate();renderRows()}
        card.querySelector('[data-retry]').onclick=()=>action(async()=>{
          const original=r.source, before=JSON.stringify(r); card.querySelector('.ac-row-msg').textContent='正在重新识别这一条…';msg('正在重新识别这一条，其他规则保持不变…')
          const data=await request('/recognize',{text:original,single:true})
          if(!active()||state.rows[i]!==r||JSON.stringify(r)!==before) {msg('识别期间此条已被修改，返回结果未覆盖你的编辑');return}
          state.rows[i]=data.rules[0];invalidate();renderRows();msg('此条已重新识别，请重新确认')
        },text=>{if(card.isConnected)card.querySelector('.ac-row-msg').textContent=text;msg(text)})
      })
    }
    function poolRef() {
      const p=el('rs-f-pool'), o=p.options[p.selectedIndex]
      if(!o||!o.dataset.id)throw Error('请选择股票池')
      return {kind:o.dataset.kind,id:o.dataset.id,script:o.dataset.script}
    }
    async function save() {
      const body={id:state.id,version:state.version,name:el('rs-f-label').value,rules:state.rows,pool:poolRef(),source_text:root.value,description:description.value,source_language:el('ac-language').value,assistant_prompt:el('ac-prompt').value}
      const cfg=await request('',body)
      if(!active())return cfg
      state.id=cfg.id;state.version=cfg.version
      let option=Array.from(el('ac-saved').options).find(o=>o.value===cfg.id)
      if(!option){option=document.createElement('option');option.value=cfg.id;el('ac-saved').appendChild(option)}
      option.textContent=cfg.name+' · v'+cfg.version;el('ac-saved').value=cfg.id
      state.items=state.items.filter(x=>x.id!==cfg.id).concat([{id:cfg.id,name:cfg.name,version:cfg.version}]);personalList()
      msg('已保存个人策略版本 '+cfg.version+'；公共研究线未改变')
      return cfg
    }
    function result(cfg) {
      if(!active())return
      const runs=cfg.runs||[], run=runs[runs.length-1]
      if(!run){el('ac-result').textContent='尚无个人回测结果';return}
      const r=run.result, fmt=v=>Number.isFinite(v)?v.toFixed(2):'—'
      el('ac-result').textContent='版本 '+run.snapshot.version+' · '+({queued:'排队中',running:'回测中',done:'已完成',failed:'失败'}[run.status]||run.status)+' · '+(run.progress||0)+'%\n'+(run.error||'') + (r?'\n区间 '+r.start+' → '+r.end+'\n总收益 '+fmt(r.return_pct)+'% · 最大回撤 '+fmt(r.max_drawdown_pct)+'%\n完整交易 '+r.closed+' 笔 · 胜率 '+fmt(r.win_rate)+'% · 信号 '+r.signals+' 个\n因数据不足无法判断 '+r.unknown+' 次\n'+r.note:'')
      if(r){const b=document.createElement('button');b.className='btn small';b.textContent='下载本次规则快照与交易记录';b.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(run,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='个人策略回测-'+run.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};el('ac-result').appendChild(b)}
      clearTimeout(state.timer)
      if(['queued','running'].includes(run.status))state.timer=setTimeout(()=>refresh().catch(e=>msg(e.message)),3000)
    }
    async function refresh(){if(!state.id||!active())return;result(await request('/'+state.id))}
    function aiStatus(text) {
      if(!active())return
      el('ac-ai-status').hidden=false
      el('ac-ai-status').textContent=text
    }
    async function syncRecognition() {
      const status=await request('/recognize/status')
      if(active())el('ac-stop').hidden=!status.running
      return status.running
    }
    el('ac-stop').onclick=async()=>{
      if(el('ac-stop').disabled)return
      el('ac-stop').disabled=true
      try {
        await request('/recognize/cancel',{})
        if(active()){el('ac-stop').hidden=true;aiStatus('已停止识别，原始输入和已有规则已保留')}
      } catch(e){aiStatus('停止失败：'+e.message+'，请重试')}
      finally{if(active())el('ac-stop').disabled=false}
    }
    syncRecognition().then(running=>{if(running)aiStatus('你有一次识别尚未结束，可点击停止识别后重试')}).catch(()=>{})
    el('ac-ai').onclick=()=>action(async()=>{
      if(state.rows.length&&!confirm('重新整理全部规则会替换当前规则和确认状态，继续吗？'))return
      const text=sourceInput(), fingerprint=inputFingerprint(), before=JSON.stringify(state.rows)
      if(!text.trim())throw Error('请在左侧填写规则或代码，或在右侧输入交易要求')
      if(text.length>6000)throw Error('规则编辑器与 AI 输入合计最多六千字，请精简后重试')
      el('ac-msg').textContent=''
      aiStatus('AI 正在识别整理，缺参数的内容会保留为待确认…')
      el('ac-stop').hidden=false
      el('ac-ai-status').dataset.loading='true'
      el('ac-ai').textContent='正在识别…'
      el('ac-ai').setAttribute('aria-busy','true')
      try {
        const data=await request('/recognize',{text,language:el('ac-language').value})
        if(!active())return
        if(fingerprint!==inputFingerprint()||before!==JSON.stringify(state.rows)){aiStatus('识别期间内容已被修改，返回结果未覆盖你的编辑');return}
        state.rows=data.rules;state.editing=null;invalidate();renderRows();showRules();aiStatus('识别完成，请在左侧逐条核对并确认。暂不支持的内容不能带入回测。')
      } finally {
        if(active()){
          el('ac-stop').hidden=true
          el('ac-ai-status').dataset.loading='false'
          el('ac-ai').textContent='AI 识别为规则'
          el('ac-ai').setAttribute('aria-busy','false')
        }
      }
    },text=>{aiStatus(text);syncRecognition().catch(()=>{})})
    el('ac-add').onclick=()=>{if(state.rows.length>=30){msg('最多三十条规则');return}const row={type:'pending',params:{},source:'',confirmed:false};state.rows.push(row);state.editing=row;invalidate();renderRows();showRules()}
    root.addEventListener('input',()=>{state.rows.forEach(r=>r.confirmed=false);invalidate();renderRows()})
    ;['rs-f-pool','ac-start','ac-end','ac-initial','ac-slip'].forEach(id=>el(id).addEventListener('change',invalidate))
    el('rs-f-submit').addEventListener('click',e=>{e.stopImmediatePropagation();e.preventDefault();action(save)},true)
    el('ac-test').onclick=()=>action(async()=>{
      if(!el('ac-confirm').checked)throw Error('请核对原文并确认成交口径')
      if(!state.rows.length||state.rows.some(r=>!r.confirmed||r.type==='pending'))throw Error('请先补齐并逐条确认所有规则')
      const before=JSON.stringify(state.rows), selected=JSON.stringify(poolRef())
      const cfg=await save()
      if(!active()||before!==JSON.stringify(state.rows)||selected!==JSON.stringify(poolRef())||!el('ac-confirm').checked)throw Error('保存期间规则或设置发生变化，请重新确认后回测')
      const run=await request('/'+cfg.id+'/backtest',{version:cfg.version,start:el('ac-start').value,end:el('ac-end').value,initial:Number(el('ac-initial').value),slippage_bps:Number(el('ac-slip').value),execution_confirmed:true})
      result({runs:[run]});msg('回测已提交，使用刚保存的确认版本')
    })
    el('ac-refresh').onclick=()=>action(refresh)
    el('ac-new').onclick=()=>{state.id=null;state.version=null;invalidate();msg('下次保存将创建另一条个人策略，现有规则保留供修改')}
    el('ac-load').onclick=()=>action(async()=>{
      const id=el('ac-saved').value;if(!id)return
      if(state.rows.length&&!confirm('加载将替换当前未保存的规则，继续吗？'))return
      const cfg=await request('/'+id);if(!active())return
      state.id=cfg.id;state.version=cfg.version;state.rows=cfg.rules
      el('rs-f-label').value=cfg.name
      const p=el('rs-f-pool'), opt=Array.from(p.options).find(o=>o.dataset.kind===cfg.pool.ref.kind&&o.dataset.id===String(cfg.pool.ref.id))
      if(opt)p.selectedIndex=opt.index;else p.value=''
      description.value=cfg.description||'';countDescription();root.value=cfg.source_text??cfg.rules.map(r=>r.source).join('\n');el('ac-language').value=cfg.source_language||'text';el('ac-prompt').value=cfg.assistant_prompt||''
      state.editing=null;invalidate();renderRows();showRules();result(cfg);msg('已加载版本 '+cfg.version+'，股票池快照：'+cfg.pool.name+'。再次保存时会读取所选筛选器的当前脚本。')
    })
    request('').then(data=>{
      if(!active())return
      state.schema=data.schema;state.items=data.items
      personalList()
      el('ac-saved').innerHTML='<option value="">选择已保存的个人策略</option>'+data.items.map(x=>'<option value="'+escape(x.id)+'">'+escape(x.name)+' · v'+x.version+'</option>').join('')
      el('ac-execution').textContent=data.execution+' 首版回测仅支持美股，日期请使用已入库历史范围；不支持的规则不能带入执行。'
      if(data.data_range && data.data_range.last){
        el('ac-execution').textContent+=' 已入库基准范围：'+data.data_range.first+' 至 '+data.data_range.last+'；个股覆盖可能更短。'
        el('ac-start').min=el('ac-end').min=data.data_range.first
        el('ac-start').max=el('ac-end').max=data.data_range.last
        el('ac-end').value=data.data_range.last
      }
      renderRows()
    }).catch(e=>msg(e.message))
  }
  new MutationObserver(mount).observe(document.body,{childList:true,subtree:true})
  mount()
})()
