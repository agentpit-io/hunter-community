/* 评分只使用后端证据；缺失结果不补分，所有文本转义。 */
(function () {
  'use strict';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g,
    ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
  const labels = {pass:'已具备', warn:'需关注', blocked:'存在限制', missing:'未验证', insufficient:'样本不足'};
  window.agentOverfitCard = function (report) {
    if (!report) return '<section class="ag-overfit"><b>过拟合监测 · 验证充分度 —</b><p>尚无检查结果。</p></section>';
    const score = Number.isFinite(report.score) ? esc(report.score) + ' / 100' : '—';
    return '<section class="ag-overfit" style="margin:12px 0;padding:16px;border:1px solid #d8dee5;border-radius:12px;background:var(--surface,#fff)">' +
      '<b>过拟合监测 · 验证充分度 ' + score + '</b><p>' + esc(report.label) + ' · ' + esc(report.scope) +
      (report.as_of ? ' · 截至 ' + esc(report.as_of) : '') + '</p><p>' + esc(report.warning) + '</p>' +
      '<p><strong>下一步：</strong>' + esc(report.next_step) + '</p>' +
      '<details><summary>查看得分依据、缺失证据与改进建议</summary>' +
      (Array.isArray(report.checks) ? report.checks : []).map(c => '<div style="padding:10px 0;border-top:1px solid #ddd">' +
        '<strong>' + esc(c.label) + '</strong> · ' + esc(labels[c.status] || '未知') + ' · ' + esc(c.points) + '/' + esc(c.weight) +
        ' 分<p>' + esc(c.detail) + '</p><p>建议：' + esc(c.next_step) + '</p></div>').join('') +
      '<small>评分规则 ' + esc(report.version) + '；这是产品检查清单，尚未经过预测效果校准。缺失项不计分，低分也可能只是证据不足。</small></details></section>';
  };
})();
