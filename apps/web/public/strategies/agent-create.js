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
    const box = document.createElement('div'); box.id = 'ac-editor'
    box.innerHTML = '<style>#ac-editor{margin-top:12px;min-width:0;overflow-wrap:anywhere}#ac-editor .ac-row{border:1px solid var(--line);border-radius:9px;padding:12px;margin:10px 0;background:var(--bg,#fafaf8)}#ac-editor .ac-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}#ac-editor label{display:inline-block;margin-right:14px}#ac-editor input[type=checkbox]{width:auto}#ac-editor input[type=number],#ac-editor input[type=date]{width:150px}#ac-editor .ac-state{color:var(--brand);font-weight:bold}#ac-result{white-space:pre-wrap;max-height:400px;overflow:auto}#ac-msg{color:var(--brand);white-space:pre-wrap}#ac-editor select{max-width:100%}@media(max-width:640px){.rs-f{grid-template-columns:1fr}#ac-editor label{max-width:100%;margin-right:0}}</style>' +
      '<div class="ac-actions"><button type="button" class="btn primary small" id="ac-ai">AI 整理为规则</button><button type="button" class="btn small" id="ac-add">手动添加一条</button></div>' +
      '<div class="ag-note">AI 只整理受支持规则；没有写清的参数保持待确认。买入条件全部满足，卖出条件任一满足。修改后必须重新确认。</div>' +
      '<div class="ac-actions"><select id="ac-saved" aria-label="我的个人策略"><option value="">加载已保存的个人策略…</option></select><button type="button" class="btn small" id="ac-load">加载</button><button type="button" class="btn small" id="ac-new">另建策略</button></div>' +
      '<div id="ac-personal-list"></div><div id="ac-msg" role="status"></div><div id="ac-rows"></div>' +
      '<div class="ac-actions"><label>回测开始 <input type="date" id="ac-start"></label><label>回测结束 <input type="date" id="ac-end"></label><label>初始资金（美元） <input type="number" id="ac-initial" value="100000" min="1000" max="10000000"></label><label>单边滑点（基点） <input type="number" id="ac-slip" value="5" min="0" max="100"></label></div>' +
      '<div class="ag-note" id="ac-execution"></div><label><input type="checkbox" id="ac-confirm"> 我已核对原始描述没有遗漏，并确认成交口径、费用与滑点设置</label>' +
      '<div class="ac-actions"><button type="button" class="btn primary small" id="ac-test">保存确认版本并回测</button><button type="button" class="btn small" id="ac-refresh">刷新回测状态</button></div><div id="ac-result"></div>'
    root.parentElement.appendChild(box)
    const state = {rows:[], schema:[], id:null, version:null, busy:false, timer:null, items:[]}
    if (current && current.timer) clearTimeout(current.timer)
    current = state
    const el = id => document.getElementById(id)
    const active = () => document.getElementById('ac-editor') === box
    const msg = s => { if(active()) el('ac-msg').textContent = s }
    function personalList() {
      el('ac-personal-list').innerHTML=state.items.map(x=>'<div class="ac-row"><b>'+escape(x.name)+'</b> · v'+x.version+'<div class="ac-actions"><button type="button" class="btn small" data-personal-load="'+escape(x.id)+'">加载策略</button><button type="button" class="btn small" data-personal-delete="'+escape(x.id)+'">删除</button></div></div>').join('')
      el('ac-personal-list').querySelectorAll('[data-personal-load]').forEach(b=>b.onclick=()=>{el('ac-saved').value=b.dataset.personalLoad;el('ac-load').click()})
      el('ac-personal-list').querySelectorAll('[data-personal-delete]').forEach(b=>b.onclick=()=>action(async()=>{
        const id=b.dataset.personalDelete, item=state.items.find(x=>x.id===id)
        if(!confirm('删除个人策略「'+item.name+'」？\n删除后不再出现在列表，历史回测快照保留。'))return
        await request('/'+id,undefined,'DELETE')
        state.items=state.items.filter(x=>x.id!==id)
        Array.from(el('ac-saved').options).filter(o=>o.value===id).forEach(o=>o.remove())
        if(state.id===id){state.id=null;state.version=null;state.rows=[];el('rs-f-label').value='';el('rs-f-hyp').value='';root.value='';invalidate();renderRows();el('ac-result').textContent=''}
        personalList();msg('策略已删除')
      }))
    }
    function invalidate() { if(active()) el('ac-confirm').checked = false }
    async function action(fn) {
      if(state.busy) return
      state.busy = true
      box.querySelectorAll('button').forEach(b=>b.disabled=true)
      try { await fn() } catch(e) { msg(e.message || '请求失败') }
      finally { state.busy=false; if(active()) box.querySelectorAll('button').forEach(b=>b.disabled=false) }
    }
    function renderRows() {
      if (!active()) return
      el('ac-rows').innerHTML = state.rows.map((r,i)=>{
        const spec=state.schema.find(x=>x.type===r.type)
        return '<div class="ac-row" data-row="'+i+'"><div class="ac-state">规则 '+(i+1)+' · '+(r.confirmed?'已确认':'待确认')+'</div>' +
          '<label>原始描述（可修改后单条重新识别）</label><textarea data-source rows="2">'+escape(r.source)+'</textarea>' +
          '<label>规则类型</label><select data-type>'+state.schema.map(x=>'<option value="'+x.type+'"'+(x.type===r.type?' selected':'')+'>'+escape(x.label)+'</option>').join('')+'</select>' +
          (spec?spec.fields.map(f=>'<label>'+escape(f.label)+' <input data-param="'+f.key+'" type="number" min="'+f.min+'" max="'+f.max+'" step="'+(f.integer?'1':'any')+'" value="'+escape(r.params[f.key])+'"></label>').join(''):'') +
          '<div class="ac-actions"><button type="button" class="btn small" data-retry>仅重新识别这一条</button><button type="button" class="btn small" data-remove>删除此条</button><label><input data-confirm type="checkbox"'+(r.confirmed?' checked':'')+(r.type==='pending'?' disabled':'')+'>确认此条规则</label></div></div>'
      }).join('')
      el('ac-rows').querySelectorAll('[data-row]').forEach(card=>{
        const i=+card.dataset.row, r=state.rows[i]
        function change() {r.confirmed=false;card.querySelector('[data-confirm]').checked=false;card.querySelector('.ac-state').textContent='规则 '+(i+1)+' · 待确认';invalidate()}
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
          const original=r.source, before=JSON.stringify(r); msg('正在重新识别这一条，其他规则保持不变…')
          const data=await request('/recognize',{text:original,single:true})
          if(!active()||state.rows[i]!==r||JSON.stringify(r)!==before) {msg('识别期间此条已被修改，返回结果未覆盖你的编辑');return}
          state.rows[i]=data.rules[0];invalidate();renderRows();msg('此条已重新识别，请重新确认')
        })
      })
    }
    function poolRef() {
      const p=el('rs-f-pool'), o=p.options[p.selectedIndex]
      if(!o||!o.dataset.id)throw Error('请选择股票池')
      return {kind:o.dataset.kind,id:o.dataset.id,script:o.dataset.script}
    }
    async function save() {
      const body={id:state.id,version:state.version,name:el('rs-f-label').value,rules:state.rows,pool:poolRef(),source_text:el('rs-f-hyp').value+'\n'+root.value}
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
    el('ac-ai').onclick=()=>action(async()=>{
      if(state.rows.length&&!confirm('重新整理全部规则会替换当前规则和确认状态，继续吗？'))return
      const text=el('rs-f-hyp').value+'\n'+root.value, before=JSON.stringify(state.rows)
      if(text.length>6000)throw Error('策略思路与规则草案合计最多六千字，请精简后重试')
      msg('AI 正在整理，缺参数的内容会保留为待确认…')
      const data=await request('/recognize',{text})
      if(!active())return
      if(text!==el('rs-f-hyp').value+'\n'+root.value||before!==JSON.stringify(state.rows)){msg('识别期间内容已被修改，返回结果未覆盖你的编辑');return}
      state.rows=data.rules;invalidate();renderRows();msg('请逐条核对。暂不支持的内容可保留草案，不能带入回测。')
    })
    el('ac-add').onclick=()=>{if(state.rows.length>=30){msg('最多三十条规则');return}state.rows.push({type:'pending',params:{},source:'',confirmed:false});invalidate();renderRows()}
    el('rs-f-hyp').addEventListener('input',()=>{state.rows.forEach(r=>r.confirmed=false);invalidate();renderRows()})
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
      el('rs-f-hyp').value=cfg.source_text||cfg.rules.map(r=>r.source).join('\n');root.value=''
      invalidate();renderRows();result(cfg);msg('已加载版本 '+cfg.version+'，股票池快照：'+cfg.pool.name+'。再次保存时会读取所选筛选器的当前脚本。')
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
