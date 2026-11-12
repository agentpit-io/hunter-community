/* 小鹿实验助手：先查看候选，再运行固定基准对照，结果只属于当前账号。 */
(function () {
  'use strict';
  const API = '/api/quant/agent/experiments';
  const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const status = {planning:'正在设计候选',ready:'候选待确认',queued:'等待回测',running:'回测中',done:'比较完成',failed:'失败',cancelled:'已停止',not_started:'未执行'};
  const busy = r => ['planning','queued','running'].includes(r.status);
  const number = n => Number.isFinite(n) ? n.toFixed(2) : '—';
  let opened = null;
  async function request(path, body) {
    const response = await fetch(API + path, {method:body === undefined ? 'GET' : 'POST',
      headers:apiHeaders({'Content-Type':'application/json'}), cache:'no-store',
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(response.status === 401 ? '请先登录后使用实验助手' : data.detail || '请求失败');
    return data;
  }
  function renderRun(run) {
    const comparison = Object.fromEntries((run.comparison || []).map(x => [x.id, x]));
    return '<article><h3>' + escape(run.goal) + '</h3><p>' + escape(status[run.status] || '状态未知') +
      (run.status === 'running' ? ' · ' + number(run.progress) + '%' : '') + '</p>' +
      (run.error ? '<p role="alert">' + escape(run.error) + '</p>' : '') +
      '<p>' + escape(run.start) + ' ～ ' + escape(run.end) + ' · 固定公共规则作为基准</p>' +
      '<p>放弃条件：' + escape(run.failure_rule) + '</p>' +
      '<h4>候选买入条件</h4>' + ((run.candidates || []).map(c => '<p><b>' + escape(c.label) + '</b><br>' + escape(c.reason) + '</p>').join('') || '<p>候选尚未生成。</p>') +
      ((run.results || []).length ? '<div style="overflow:auto"><table><thead><tr><th>实验</th><th>状态</th><th>净收益（元）</th><th>收益率（%）</th><th>最大回撤（%）</th><th>平仓笔数</th><th>相对基准净收益（元）</th><th>初筛结论</th></tr></thead><tbody>' +
      run.results.map(item => { const r = item.result || {}, c = comparison[item.id] || {};
        return '<tr><td>' + escape(item.label) + '</td><td>' + escape(status[item.status] || '状态未知') + '</td><td>' + number(r.net_pnl) +
          '</td><td>' + number(r.return_pct) + '</td><td>' + number(r.max_drawdown_pct) + '</td><td>' + (Number.isFinite(r.closed) ? escape(r.closed) : '—') +
          '</td><td>' + number(c.delta_net_pnl) + '</td><td>' + escape(item.id === 'baseline' ? '固定对照' : c.verdict || '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>' : '') +
      '<p>' + escape(run.note) + '</p><p>初筛：基准与候选各至少 ' + escape(run.criteria?.min_closed ?? '—') + ' 笔平仓、' + escape(run.criteria?.min_days ?? '—') +
      ' 个交易日；候选净收益为正且高于基准，最大回撤不更差。“可继续研究”仍需冻结后新数据验证。放弃条件作为事前记录，不由 AI 宣布已满足。</p>' +
      (run.data_snapshot ? '<p>行情输入快照：<code>' + escape(run.data_snapshot.sha256) + '</code></p>' : '') +
      '<details><summary>固定基准与完整候选参数</summary><pre>' + escape(JSON.stringify({baseline:run.baseline,candidates:run.candidates,implementation:run.implementation}, null, 2)) + '</pre></details>' +
      '<div class="ex-actions">' + (run.status === 'ready' ? '<button class="btn primary" data-ex-run>确认候选，运行对照回测</button>' : '') +
      (busy(run) || run.status === 'ready' ? '<button class="btn" data-ex-cancel>' + (run.cancel_requested ? '正在停止…' : '停止本次实验') + '</button>' : '') +
      '<button class="btn" data-ex-download>下载完整实验记录</button></div></article>';
  }
  window.agentExperimentRender = renderRun;
  async function open() {
    if (opened) { opened.focus(); return; }
    const dialog = document.createElement('dialog');
    opened = dialog;
    dialog.className = 'ex-dialog';
    dialog.style.cssText = 'width:min(1080px,94vw);max-height:90vh;box-sizing:border-box;border:1px solid #dce4db;border-radius:18px;padding:24px;background:#fff;color:#223628';
    dialog.innerHTML = '<style>.ex-dialog input,.ex-dialog textarea{box-sizing:border-box;max-width:100%;padding:9px;border:1px solid #cad5ca;border-radius:6px}.ex-dialog textarea{width:100%;min-height:72px}.ex-dialog table{border-collapse:collapse;min-width:760px;width:100%}.ex-dialog th,.ex-dialog td{padding:10px;border-bottom:1px solid #ddd;text-align:left}.ex-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere}.ex-dialog code{overflow-wrap:anywhere}.ex-actions{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.ex-dialog button:disabled{opacity:.55;cursor:wait}</style>' +
      '<div class="ex-actions" style="justify-content:space-between"><h2 style="margin:0">小鹿实验助手</h2><button class="btn" data-close>关闭</button></div>' +
      '<p>A 股 · 涨停后强势整理。提出买入条件候选，仓位、卖出和费用沿用固定基准。先查看候选，再运行回测。</p>' +
      '<p id="ex-message" role="status" aria-live="polite">正在读取个人实验…</p><div id="ex-form"></div><div id="ex-current"></div><div id="ex-history"></div>';
    document.body.appendChild(dialog); dialog.showModal();
    let timer, currentId, submitting = false, closed = false, sequence = 0;
    const message = s => { if (!closed) dialog.querySelector('#ex-message').textContent = s; };
    const buttons = () => { dialog.querySelectorAll('[data-ex-create],[data-ex-run],[data-ex-cancel]').forEach(b => { b.disabled = submitting; }); };
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.addEventListener('close', () => {closed=true;sequence++;clearTimeout(timer);opened=null;dialog.remove();});
    async function history() {
      const data = await request('');
      if (closed) return;
      message('累计预留试跑 ' + data.trials + ' / ' + data.budget + ' 次（含基准、失败和停止）；AI 设计 ' + data.ai_requests + ' / ' + data.ai_budget + ' 次。');
      const target = dialog.querySelector('#ex-history');
      target.innerHTML = '<h3>个人实验记录</h3>' + (data.items.map(r => '<p><button class="btn small" data-ex-open="' + escape(r.id) + '">' +
        escape(r.goal) + ' · ' + escape(status[r.status] || '状态未知') + '</button></p>').join('') || '<p>还没有实验。</p>');
      target.querySelectorAll('[data-ex-open]').forEach(button => button.onclick = () => show(button.dataset.exOpen));
      return data;
    }
    async function show(ident) {
      currentId = ident; clearTimeout(timer); const ticket = ++sequence;
      try {
        const run = await request('/' + ident);
        if (closed || ticket !== sequence) return;
        const target = dialog.querySelector('#ex-current');
        target.innerHTML = renderRun(run);
        const start = target.querySelector('[data-ex-run]'), stop = target.querySelector('[data-ex-cancel]');
        if (start) start.onclick = () => action('/run', ident);
        if (stop) {stop.disabled = run.cancel_requested === true;stop.onclick = () => action('/cancel', ident);}
        target.querySelector('[data-ex-download]').onclick = () => {
          const href = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], {type:'application/json'}));
          const link = document.createElement('a');link.href=href;link.download='hunter-experiment-'+run.id+'.json';link.click();
          setTimeout(() => URL.revokeObjectURL(href), 1000);
        };
        if (busy(run)) timer=setTimeout(() => show(ident),2500);
        else await history();
      } catch (err) {
        if (!closed && ticket === sequence) {message('进度读取失败：'+err.message+'。正在重试，请勿重复启动。');timer=setTimeout(() => show(ident),5000);}
      }
    }
    async function action(suffix, ident) {
      if (submitting) return;
      submitting=true;buttons();
      try {await request('/'+ident+suffix, {});await show(ident);}
      catch (err) {message(err.message);}
      finally {submitting=false;if (!closed) buttons();}
    }
    try {
      const data = await history(); if (closed) return;
      const end = data.data_range.last, first=data.data_range.first;
      const start = end ? new Date(end+'T12:00:00Z') : null;
      if (start) start.setUTCFullYear(start.getUTCFullYear()-1);
      const startValue = start ? [start.toISOString().slice(0,10),first].sort().pop() : '';
      dialog.querySelector('#ex-form').innerHTML = '<form><p><label>研究目标<textarea name="goal" required maxlength="2000" placeholder="例如：研究量能过滤是否有帮助，保持其他规则"></textarea></label></p>' +
        '<p><label>什么结果出现就放弃<textarea name="failure_rule" required maxlength="2000"></textarea></label></p>' +
        '<div class="ex-actions"><label>开始日期 <input name="start" type="date" required value="'+escape(startValue)+'" min="'+escape(first)+'" max="'+escape(end)+'"></label>'+
        '<label>结束日期 <input name="end" type="date" required value="'+escape(end)+'" min="'+escape(first)+'" max="'+escape(end)+'"></label></div>' +
        '<p>最多 '+escape(data.max_candidates)+' 个候选，每个只改一项条件。全部记录保留，结果不会自动应用。</p>' +
        '<button class="btn primary" data-ex-create type="submit">提出候选实验</button></form>';
      dialog.querySelector('#ex-form form').onsubmit = async event => {
        event.preventDefault(); if (submitting) return;
        submitting=true;buttons();
        const form=event.currentTarget;
        try {
          const run=await request('',Object.fromEntries(['goal','failure_rule','start','end'].map(k => [k,form.elements[k].value])));
          await history();await show(run.id);
        } catch (err) {message(err.message);}
        finally {submitting=false;if (!closed) buttons();}
      };
      const active=data.items.find(busy);
      if (active) await show(active.id);
    } catch (err) {message(err.message);}
  }
  document.addEventListener('click', event => { if (event.target.closest('[data-experiment-assistant]')) open(); });
})();
