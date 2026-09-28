/* 个人规则编辑器：保存版本后才允许回测，结果独立显示。 */
(function () {
  'use strict';
  let generation = 0, timer;
  const escape = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function request(url, body) {
    const r = await fetch(url, {method: body ? 'POST' : 'GET', headers: apiHeaders({'Content-Type':'application/json'}),
      cache:'no-store', ...(body ? {body:JSON.stringify(body)} : {})});
    const d = await r.json();
    if (!r.ok) throw new Error(r.status === 401 ? '请登录后编辑个人规则' : (d.detail || '请求失败'));
    return d;
  }
  window.openAgentRules = async function (branch) {
    if (!['limitup','limitup_yin'].includes(branch)) return;
    const token = ++generation;
    clearTimeout(timer);
    let dialog = document.getElementById('manual-rules-dialog');
    if (dialog) dialog.remove();
    dialog = document.createElement('dialog');
    dialog.id = 'manual-rules-dialog';
    dialog.style.cssText = 'width:min(880px,94vw);max-height:90vh;overflow:auto;border:1px solid #ddd;border-radius:14px;padding:24px;background:#fff;color:#243746';
    dialog.innerHTML = '<button type="button" style="float:right" id="mr-close">关闭</button><h2>编辑规则与手动回测</h2><div id="mr-content">加载中…</div>';
    document.body.appendChild(dialog);
    dialog.showModal();
    dialog.querySelector('#mr-close').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { generation++; clearTimeout(timer); dialog.remove(); });
    const root = dialog.querySelector('#mr-content'), url = '/api/quant/agent/rules/' + branch;
    let cfg, dirty = false, busy = false;
    const current = () => token === generation && dialog.open;
    const fmt = n => Number.isFinite(n) ? n.toFixed(2) : '—';
    const message = s => { if (current()) root.querySelector('#mr-msg').textContent = s; };
    function drawResults(data) {
      const target = root.querySelector('#mr-results');
      if (!target) return;
      target.innerHTML = (data.runs || []).map(run => {
        const r = run.result;
        return '<article style="border-top:1px solid #ddd;margin-top:16px;padding-top:12px"><b>个人版本 v' +
          escape(run.version) + ' · ' + escape(run.start) + '～' + escape(run.end) + '</b><p>' +
          ({queued:'等待回测',running:'回测中 ' + escape(run.progress) + '%',done:'回测完成',failed:'回测失败'}[run.status] || '状态未知') +
          (run.error ? '：' + escape(run.error) : '') + '</p>' +
          (r ? '<p>净收益 ¥' + fmt(r.net_pnl) + ' · 收益率 ' + fmt(r.return_pct) + '% · 最大回撤 ' + fmt(r.max_drawdown_pct) +
            '%</p><p>已平仓 ' + escape(r.closed) + ' 笔 · 未平仓 ' + escape(r.open) + ' 笔 · 扣费后胜率 ' + fmt(r.win_rate) +
            (r.win_rate == null ? '' : '%') + '</p><p>' + escape(r.note) + '</p><button type="button" data-download="' + escape(run.id) + '">下载回测明细</button>' : '') +
          '<details><summary>本次规则快照</summary><pre style="white-space:pre-wrap">' + (run.rules || []).map(r => escape(r.id + '：' + r.condition)).join('<br>') + '</pre></details></article>';
      }).join('') || '<p>保存规则后可手动运行回测。</p>';
      target.querySelectorAll('[data-download]').forEach(btn => btn.onclick = () => {
        const run = data.runs.find(x => x.id === btn.dataset.download);
        const blob = new Blob([JSON.stringify(run, null, 2)], {type:'application/json'});
        const link = document.createElement('a'), href = URL.createObjectURL(blob);
        link.href = href; link.download = branch + '-v' + run.version + '-backtest.json'; link.click();
        setTimeout(() => URL.revokeObjectURL(href), 1000);
      });
    }
    async function poll() {
      if (!current()) return;
      try {
        const latest = await request(url);
        if (!current()) return;
        cfg.runs = latest.runs;
        drawResults(latest);
        if ((latest.runs || []).some(x => ['queued','running'].includes(x.status))) timer = setTimeout(poll, 2500);
      } catch (e) { message('进度读取失败：' + e.message + '。关闭后重开可恢复查看。'); }
    }
    try {
      cfg = await request(url);
      if (!current()) return;
      const p = cfg.editable, yin = branch === 'limitup_yin';
      const field = (name, label, control) => '<label style="display:block;margin:12px 0">' + label + ' ' + control + '</label>';
      const number = (name, label, min, max, step) => field(name, label,
        '<input name="' + name + '" type="number" min="' + min + '" max="' + max + '" step="' + step + '" value="' + escape(p[name]) + '">');
      const check = (name, label) => field(name, label, '<input type="checkbox" name="' + name + '"' + (p[name] ? ' checked' : '') + '>');
      const end = cfg.data_last ? new Date(cfg.data_last + 'T12:00:00Z') : new Date();
      const start = new Date(end); start.setFullYear(start.getFullYear() - 1);
      if (cfg.data_first && start < new Date(cfg.data_first)) start.setTime(new Date(cfg.data_first + 'T12:00:00Z').getTime());
      const iso = d => d.toISOString().slice(0, 10);
      root.innerHTML = '<p>修改保存为你的个人版本，公共看板规则和历史记录保留。下方回测只使用对应的已保存版本。</p>' +
        '<form id="mr-form">' + number('amount','每个信号买入金额（元）',100,100000,100) +
        number('hold_days','持有交易日数',1,60,1) +
        (yin ? '<p>交易板块：沪深主板；信号：涨停后连续三根阴线。</p>' :
          '<label>交易板块 <select name="board"><option value="all">主板与双创</option><option value="growth">创业板 / 科创板</option><option value="main">沪深主板</option></select></label>' +
          check('require_ma5','收盘高于 MA5 且 MA5 向上') +
          number('amp_max','三天整理幅度上限（%，留空表示不限）',0.1,100,0.1) +
          check('no_all_shrink','排除三天成交量都低于涨停日')) +
        '<p>涨停判定、三天整理信号、成交与手续费口径沿用引擎；参数改动会实际参与回测。</p>' +
        '<button type="submit">保存规则</button> <span id="mr-version">当前个人版本 v' + cfg.version + '</span></form>' +
        '<hr><label>开始日期 <input id="mr-start" type="date" value="' + iso(start) + '"></label> ' +
        '<label>结束日期 <input id="mr-end" type="date" value="' + iso(end) + '"></label> ' +
        '<button type="button" id="mr-run">手动回测</button><p>最多一年；结束日期须不晚于已入库的最近交易日。</p>' +
        '<p id="mr-msg" role="status"></p><div id="mr-results"></div>';
      const form = root.querySelector('#mr-form'), runButton = root.querySelector('#mr-run');
      if (!yin) form.elements.board.value = p.main_only ? 'main' : p.growth_only ? 'growth' : 'all';
      function buttons() { form.querySelectorAll('input,select').forEach(el => { el.disabled = busy; }); runButton.disabled = busy || dirty || !cfg.version; form.querySelector('button').disabled = busy; }
      form.addEventListener('input', () => {dirty = true; buttons(); message('有未保存修改，请先保存再回测。');});
      form.onsubmit = async e => {
        e.preventDefault(); if (busy) return;
        const params = {...cfg.editable, amount:Number(form.elements.amount.value), hold_days:Number(form.elements.hold_days.value)};
        if (!yin) Object.assign(params, {growth_only:form.elements.board.value === 'growth', main_only:form.elements.board.value === 'main',
          require_ma5:form.elements.require_ma5.checked, amp_max:form.elements.amp_max.value === '' ? null : Number(form.elements.amp_max.value),
          no_all_shrink:form.elements.no_all_shrink.checked});
        busy = true; buttons();
        try {
          const saved = await request(url, {version:cfg.version, params});
          cfg.version = saved.version; cfg.editable = params; dirty = false;
          if (current()) root.querySelector('#mr-version').textContent = '当前个人版本 v' + cfg.version;
          message('规则保存成功，可以手动回测。');
        } catch (err) { message(err.message); }
        finally { busy = false; if (current()) buttons(); }
      };
      runButton.onclick = async () => {
        if (busy || dirty || !cfg.version) return;
        busy = true; buttons();
        try {
          await request(url + '/backtest', {version:cfg.version, start:root.querySelector('#mr-start').value, end:root.querySelector('#mr-end').value});
          message('回测已启动；关闭窗口后仍可回来查看结果。');
          clearTimeout(timer); await poll();
        } catch (err) { message(err.message); }
        finally { busy = false; if (current()) buttons(); }
      };
      buttons(); drawResults(cfg);
      if ((cfg.runs || []).some(x => ['queued','running'].includes(x.status))) timer = setTimeout(poll, 1000);
    } catch (e) { if (current()) root.textContent = e.message; }
  };
})();
