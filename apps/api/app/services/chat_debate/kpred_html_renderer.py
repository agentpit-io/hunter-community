"""Kronos ProResult → 独立 HTML 文档 · Sprint E · Day 1

策略 A · 内嵌 ECharts + JSON:
  · 单文件 HTML · 无 CSS/JS 外链(除 ECharts CDN)
  · 与 /kpred 页视觉一致 · Hunter theme(铜色 · 中式红涨绿跌)
  · @media print 优化 · 一键 PDF · A4 每页 1 图 + 1 表

输出:
  · 内嵌 Artifact 面板 iframe · sandbox='allow-scripts'
  · Download as HTML · 双击浏览器可开
  · Publish 匿名公开链接
"""
from datetime import datetime, timezone, timedelta
import json
import html as _html

_CST = timezone(timedelta(hours=8))

# 8 因子中文名(与 factor_engine.py FACTOR_LABELS 对齐)
_FACTOR_LABELS = {
    "main_flow": "主力净流入", "flow_div": "筹码分布",
    "kronos": "Kronos技术", "ma_align": "均线趋势",
    "pe": "PE估值", "macd": "MACD动量",
    "rsi": "RSI超买卖", "candle_5d": "近5日K线",
}

_HUNTER_CSS = """
:root {
  --theme: #B06A32; --copper2: #D4925A;
  --up: #A4332B; --dn: #3F6B40; --hold: #7A6F63;
  --ink: #211C18; --ink-s: #4B423A; --ink-f: #7A6F63;
  --line: #D8CDBA; --paper: #FFFDF9; --paper2: #EFE8DC; --paper3: #FBF1E4;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: var(--paper); color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px; line-height: 1.6; }
.wrap { max-width: 1240px; margin: 0 auto; padding: 24px 32px 48px; }
@media (max-width: 768px) {
  .wrap { padding: 16px 14px 32px; }
}
h1 { font-family: "Songti SC", "Source Han Serif SC", Georgia, serif;
  font-size: 22px; font-weight: 700; margin-bottom: 6px; display: none; }  /* 由 header-row 替代 */
h2 { font-family: "Songti SC", Georgia, serif; font-size: 16px; font-weight: 700;
  margin: 28px 0 12px; color: var(--ink); }

/* 顶栏 · 仿 /kpred PC 页 · 股票信息 + 当前价 + N 日后预测 */
.header-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; margin-bottom: 10px;
  background: #fff; border: 1px solid var(--line); border-radius: 12px;
  gap: 20px; flex-wrap: wrap;
}
.header-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.header-left .code {
  font-family: ui-monospace, Menlo, monospace; font-size: 15px;
  font-weight: 700; color: var(--ink);
}
.header-left .name {
  font-family: "Songti SC", serif; font-size: 15px; font-weight: 700;
  color: var(--ink); letter-spacing: 0.5px;
}
.header-left .name-sub { color: var(--ink-f); font-size: 12px; font-weight: 400; }
.rating-badge {
  padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  letter-spacing: 0.3px;
}
.rating-badge.bull { background: #fbeae8; color: var(--up); }
.rating-badge.bear { background: #e6efe7; color: var(--dn); }
.rating-badge.neutral { background: var(--paper2); color: var(--hold); }
.header-right {
  display: flex; align-items: center; gap: 18px;
  font-size: 12.5px; color: var(--ink-s);
}
.header-right .metric b {
  font-family: ui-monospace, Menlo, monospace; font-size: 14px;
  color: var(--ink); font-weight: 700; margin-left: 4px;
}
.header-right .metric .up { color: var(--up); font-weight: 700; margin-left: 4px; }
.header-right .metric .dn { color: var(--dn); font-weight: 700; margin-left: 4px; }
.header-right .metric .neutral { color: var(--hold); font-weight: 700; margin-left: 4px; }

.meta-strip {
  color: var(--ink-f); font-size: 11.5px;
  margin: 0 0 16px; padding: 0 4px;
}
.meta-strip b { color: var(--ink-s); }
.meta-strip .sep { margin: 0 8px; opacity: 0.4; }

/* 蜡烛图 */
.chart-box { height: 440px; margin: 0 0 16px; background: #fff;
  border: 1px solid var(--line); border-radius: 12px; padding: 10px 6px 4px; }


/* 综合评分卡 */
.score-panel { border: 1px solid var(--line); border-radius: 14px;
  padding: 20px 22px; background: #fff; box-shadow: 0 1px 3px rgba(30,20,10,0.03); }
.score-row { display: grid; grid-template-columns: 88px 1fr auto;
  gap: 20px; align-items: center; }
.score-circle { width: 76px; height: 76px; border-radius: 50%;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  font-family: "Songti SC", serif; font-size: 22px; font-weight: 700;
  border: 3px solid; }
.score-circle .sub { font-size: 10px; color: var(--ink-f); font-weight: 400; margin-top: 1px; }
.score-bull { border-color: var(--up); color: var(--up); }
.score-bear { border-color: var(--dn); color: var(--dn); }
.score-neutral { border-color: var(--hold); color: var(--hold); }
.score-mid .rating { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
.rating-bull { color: var(--up); }
.rating-bear { color: var(--dn); }
.rating-neutral { color: var(--hold); }
.score-mid .meta { font-size: 12px; color: var(--ink-f); }
.score-mid .meta b { color: var(--ink); }
.score-right { text-align: right; font-size: 12px; line-height: 1.85; color: var(--ink-s); }
.score-right b { font-weight: 700; }
.score-right .up { color: var(--up); }
.score-right .dn { color: var(--dn); }
.score-right .neutral { color: var(--hold); }

/* 因子明细 */
.factor-head, .factor-row { display: grid;
  grid-template-columns: 110px 1fr 60px 55px 65px;
  gap: 12px; align-items: center; padding: 9px 4px;
  border-bottom: 1px solid var(--paper2); }
.factor-head { font-weight: 600; color: var(--ink-f); font-size: 10.5px;
  letter-spacing: 0.5px; text-transform: uppercase; border-bottom: 2px solid var(--line); }
.factor-row:last-child { border-bottom: none; }
.factor-name { font-size: 13px; font-weight: 500; color: var(--ink); }
.factor-bar { position: relative; height: 7px; background: var(--paper2); border-radius: 4px; overflow: hidden; }
.factor-bar-fill { position: absolute; top: 0; height: 100%; border-radius: 4px; }
.factor-bar-mid { position: absolute; top: -1px; bottom: -1px; left: 50%; width: 1px; background: var(--line); }
.factor-score { text-align: right; font-size: 12px; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }
.factor-weight { text-align: right; font-size: 11.5px; color: var(--ink-f); }
.factor-contrib { text-align: right; font-size: 12px; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }

/* 每日预测表 */
table.daily { width: 100%; border-collapse: collapse; margin: 4px 0 8px;
  background: #fff; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
table.daily th { padding: 10px 12px; background: var(--paper3);
  color: var(--ink-s); font-weight: 600; font-size: 11.5px; text-align: right;
  letter-spacing: 0.3px; border-bottom: 1px solid var(--line); }
table.daily th:first-child { text-align: left; }
table.daily td { padding: 9px 12px; border-bottom: 1px solid var(--paper2);
  text-align: right; font-size: 12.5px; font-family: ui-monospace, Menlo, monospace; }
table.daily td:first-child { text-align: left; color: var(--ink-s); font-family: inherit; }
table.daily tr:last-child td { border-bottom: none; }

/* 尾注 */
.footer { margin-top: 32px; padding-top: 16px; border-top: 1px dashed var(--line);
  text-align: center; font-size: 11px; color: var(--ink-f); line-height: 1.7; }
.footer b { color: var(--theme); font-family: "Songti SC", serif; }
.data-note { padding: 8px 12px; background: #fff8ec; border-left: 3px solid var(--copper2);
  border-radius: 4px; font-size: 12px; color: var(--ink-s); margin: 12px 0; }

/* 打印 · A4 · 每页 1 图 + 1 表 */
@media print {
  @page { size: A4; margin: 15mm 12mm 15mm 12mm; }
  html, body { background: #fff !important; }
  .wrap { max-width: 100%; padding: 0; }
  .chart-box, .score-panel { page-break-inside: avoid; break-inside: avoid; }
  h2 { page-break-after: avoid; break-after: avoid-page; }
  table.daily { page-break-inside: avoid; }
  .footer { page-break-before: avoid; }
}
"""


def _fmt_pct(v: float, sign: bool = True) -> str:
    """+1.23% 或 -0.45% · sign=False 时不加正号"""
    try:
        v = float(v)
    except Exception:
        return "?"
    if sign:
        return f"{'+' if v >= 0 else ''}{v:.2f}%"
    return f"{v:.2f}%"


def _color_of(v: float) -> str:
    """按数值取 CSS class · 涨红跌绿中式"""
    if v > 0.05:
        return "up"
    if v < -0.05:
        return "dn"
    return "neutral"


def _score_class(score: float) -> tuple[str, str]:
    """综合评分 → (circle class, rating class)"""
    if score > 0.15:
        return "score-bull", "rating-bull"
    if score < -0.15:
        return "score-bear", "rating-bear"
    return "score-neutral", "rating-neutral"


def _e(s) -> str:
    """HTML 转义 · 防 XSS"""
    return _html.escape(str(s or ""))


def render_kpred_html(pro_result: dict, stock_query: str = "") -> str:
    """
    ProResult → 完整独立 HTML 文档
    Args:
        pro_result: kpred.py get_kpred_pro() 返回的 dict
        stock_query: 用户原始查询串 · 仅用于 title fallback
    Returns:
        完整 <!DOCTYPE html>...</html> 字符串
    """
    name = pro_result.get("name") or stock_query or "股票"
    symbol = pro_result.get("symbol", "")
    code = symbol.split(".")[0] if "." in symbol else symbol
    last_close = float(pro_result.get("last_close", 0.0) or 0)
    last_date = pro_result.get("last_date", "")
    history = pro_result.get("history", []) or []
    predictions = pro_result.get("predictions", []) or []
    days = len(predictions)
    data_note = pro_result.get("data_note", "")
    generated_at = datetime.now(_CST).strftime("%Y-%m-%d %H:%M CST")

    pro = pro_result.get("pro") or {}
    score = float(pro.get("composite_score", 0.0) or 0)
    rating = pro.get("rating", "中性观望")
    confidence = pro.get("confidence", "?")
    conflict = pro.get("conflict_level", "?")
    sigma = float(pro.get("sigma_daily_pct", 0.0) or 0)
    factor_ret = float(pro.get("factor_return_pct", 0.0) or 0)
    adj_ret = float(pro.get("adj_return_pct", 0.0) or 0)
    kronos_ret = float(pro.get("kronos_raw_return_pct", 0.0) or 0)
    factors = pro.get("factors", []) or []

    score_class, rating_class = _score_class(score)
    score_display = f"{'+' if score >= 0 else ''}{int(round(score * 100))}"

    # rating badge 顶栏用 · 颜色跟随 score 而非仅 rating 字面
    badge_variant = "bull" if score > 0.15 else "bear" if score < -0.15 else "neutral"

    # N 日后预测价 · 顶栏关键指标 · 与 /kpred PC 页对齐
    final_pred_close = float(predictions[-1].get("close", last_close)) if predictions else last_close
    adj_ret_class = "up" if adj_ret > 0 else "dn" if adj_ret < 0 else "neutral"

    # ── ECharts 数据 ──
    hist_recent = history[-60:] if len(history) > 60 else history
    all_bars = list(hist_recent) + list(predictions)
    dates = [b.get("date", "") for b in all_bars]
    candles = [
        [float(b.get("open", 0)), float(b.get("close", 0)),
         float(b.get("low", 0)), float(b.get("high", 0))]
        for b in all_bars
    ]
    hist_count = len(hist_recent)
    chart_data = {
        "dates": dates,
        "candles": candles,
        "hist_count": hist_count,
        "last_close": last_close,
    }

    # ── 因子行 ──
    factor_rows = []
    for f in factors:
        key = f.get("key", "")
        label = f.get("label") or _FACTOR_LABELS.get(key, key)
        fscore = float(f.get("score", 0.0) or 0)
        weight = float(f.get("weight", 0.0) or 0)
        contrib = float(f.get("contribution", 0.0) or 0)

        # 横条:score>0 从中线向右(红)· score<0 从中线向左(绿)
        bar_w = min(abs(fscore) * 50, 50)
        color_cls = _color_of(fscore)
        color_var = {"up": "var(--up)", "dn": "var(--dn)", "neutral": "var(--hold)"}[color_cls]
        bar_left = 50 if fscore >= 0 else 50 - bar_w

        factor_rows.append(f"""
    <div class="factor-row">
      <span class="factor-name">{_e(label)}</span>
      <div class="factor-bar">
        <div class="factor-bar-mid"></div>
        <div class="factor-bar-fill" style="left:{bar_left:.1f}%; width:{bar_w:.1f}%; background:{color_var};"></div>
      </div>
      <span class="factor-score" style="color:{color_var}">{'+' if fscore >= 0 else ''}{fscore:.2f}</span>
      <span class="factor-weight">{int(round(weight * 100))}%</span>
      <span class="factor-contrib" style="color:{color_var}">{'+' if contrib >= 0 else ''}{contrib:.3f}</span>
    </div>""")

    # ── 每日预测表 · 空数据兜底 ──
    pred_rows = []
    if predictions:
        for p in predictions:
            try:
                close = float(p.get("close", 0))
                pct = (close - last_close) / last_close * 100 if last_close else 0
            except Exception:
                pct = 0
            color_cls = _color_of(pct / 100)
            color_var = {"up": "var(--up)", "dn": "var(--dn)", "neutral": "var(--hold)"}[color_cls]
            pred_rows.append(f"""
      <tr>
        <td>{_e(p.get('date', ''))}</td>
        <td>{float(p.get('open', 0)):.2f}</td>
        <td>{float(p.get('high', 0)):.2f}</td>
        <td>{float(p.get('low', 0)):.2f}</td>
        <td>{float(p.get('close', 0)):.2f}</td>
        <td style="color:{color_var}; font-weight:600;">{'+' if pct >= 0 else ''}{pct:.2f}%</td>
      </tr>""")
    else:
        pred_rows.append("""
      <tr>
        <td colspan="6" style="text-align:center; color:var(--ink-f); padding:28px; font-style:italic;">
          该股 Kronos 未收录 · 无 K 线预测数据
        </td>
      </tr>""")

    data_note_html = f'<div class="data-note">📌 {_e(data_note)}</div>' if data_note else ""

    # ── 完整 HTML ──
    title = f"{name}({code}) Kronos {days} 日预测"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · Hunter</title>
<style>{_HUNTER_CSS}</style>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
</head>
<body>
<div class="wrap">
  <!-- 顶栏 · 仿 /kpred PC 页 · 股票信息 + rating badge + 当前价 + N 日后预测 -->
  <div class="header-row">
    <div class="header-left">
      <span class="code">{_e(code)}</span>
      <span class="name">{_e(name)}</span>
      <span class="rating-badge {badge_variant}">{_e(rating)}</span>
    </div>
    <div class="header-right">
      <span class="metric">当前<b>¥{last_close:.2f}</b></span>
      <span class="metric">{days}日后预测<b>¥{final_pred_close:.2f}</b>
        <span class="{adj_ret_class}">{_fmt_pct(adj_ret)}</span>
      </span>
    </div>
  </div>

  <div class="meta-strip">
    生成时间:<b>{generated_at}</b>
    <span class="sep">·</span>数据截至:<b>{_e(last_date)}</b>
    <span class="sep">·</span>日波动:<b>{sigma:.2f}%</b>
    <span class="sep">·</span>置信度:<b>{_e(confidence)}</b>
    <span class="sep">·</span>信号分歧:<b>{_e(conflict)}</b>
  </div>

  {data_note_html}

  <div class="chart-box" id="chart"></div>

  <!-- 综合评分卡 · rating 已移顶栏 · 此处保留分数环 + 3 收益率 -->
  <div class="score-panel">
    <div class="score-row">
      <div class="score-circle {score_class}">
        {score_display}<span class="sub">/100</span>
      </div>
      <div class="score-mid">
        <div class="rating {rating_class}">综合评分 {score_display}</div>
        <div class="meta">
          8 因子加权 · 40% Kronos + 60% 因子 · 分数区间 [-100, +100]
        </div>
      </div>
      <div class="score-right">
        <div>因子预期 <b class="up">{_fmt_pct(factor_ret)}</b></div>
        <div>调整后预期 <b class="{adj_ret_class}">{_fmt_pct(adj_ret)}</b></div>
        <div>Kronos 原始 <b class="neutral">{_fmt_pct(kronos_ret)}</b></div>
      </div>
    </div>
  </div>

  <h2>因子明细</h2>
  <div class="factor-head">
    <span>因子</span><span style="text-align:center">方向(← 空 &nbsp;&nbsp; 多 →)</span>
    <span style="text-align:right">评分</span><span style="text-align:right">权重</span>
    <span style="text-align:right">贡献</span>
  </div>
  {''.join(factor_rows)}

  <h2>每日预测</h2>
  <table class="daily">
    <thead><tr>
      <th>日期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>vs 当前</th>
    </tr></thead>
    <tbody>{''.join(pred_rows)}</tbody>
  </table>

  <div class="footer">
    Powered by <b>Hunter · 猎鹿人</b> · Kronos 金融时序大模型
    · 8 因子加权(40% Kronos + 60% 因子)· 不构成投资建议 · 仅供参考
  </div>
</div>

<script>
(function() {{
  var chartData = {json.dumps(chart_data, ensure_ascii=False)};
  if (typeof echarts === 'undefined') {{
    document.getElementById('chart').innerHTML =
      '<div style="text-align:center; padding:40px; color:#999">图表加载失败 · 请查看下方数据表</div>';
    return;
  }}
  var chart = echarts.init(document.getElementById('chart'));
  var histCount = chartData.hist_count;
  var hist = chartData.candles.slice(0, histCount).map(function(c, i) {{ return [i].concat(c); }});
  var pred = chartData.candles.slice(histCount).map(function(c, i) {{ return [i + histCount].concat(c); }});

  // 空数据兜底 · 只显示提示 · 不初始化 ECharts
  if (chartData.dates.length === 0) {{
    document.getElementById('chart').innerHTML =
      '<div style="text-align:center; padding:80px 20px; color:#999; font-size:13px;">' +
      '该股 Kronos 未收录 · 无可展示的 K 线数据' +
      '</div>';
    return;
  }}

  // X 轴稀疏化 · 目标显示约 12 个日期标签
  var labelInterval = Math.max(1, Math.floor(chartData.dates.length / 12)) - 1;

  chart.setOption({{
    animation: false,
    grid: {{ left: 62, right: 24, top: 20, bottom: 60 }},
    legend: {{
      show: true, bottom: 8, itemGap: 22,
      data: [
        {{ name: '历史 K 线', icon: 'circle' }},
        {{ name: '预测 K 线(金色 · 已多因子调整)', icon: 'circle' }},
      ],
      textStyle: {{ fontSize: 11.5, color: '#4B423A' }},
      selectedMode: false,
    }},
    xAxis: {{
      type: 'category', data: chartData.dates, boundaryGap: true,
      axisLine: {{ lineStyle: {{ color: '#D8CDBA' }} }},
      axisTick: {{ show: false }},
      splitLine: {{ show: false }},
      axisLabel: {{
        fontSize: 11, color: '#7A6F63',
        interval: labelInterval,
        formatter: function(v) {{ return v.length >= 10 ? v.slice(5) : v; }},
      }},
    }},
    yAxis: {{
      scale: true, splitNumber: 8,
      axisLine: {{ show: false }},
      axisTick: {{ show: false }},
      splitLine: {{ lineStyle: {{ color: '#E5E1D5', type: 'solid' }} }},
      axisLabel: {{
        fontSize: 11, color: '#7A6F63',
        formatter: function(v) {{
          return v >= 1000 ? v.toLocaleString('zh-CN') : v.toFixed(2);
        }},
      }},
    }},
    tooltip: {{
      trigger: 'axis', axisPointer: {{ type: 'cross' }},
      backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#D8CDBA', borderWidth: 1,
      textStyle: {{ color: '#211C18', fontSize: 12 }},
      formatter: function(params) {{
        var p = params.find(function(x) {{ return x.componentSubType === 'candlestick'; }});
        if (!p) return '';
        var v = p.value.slice(1);
        var o = v[0], c = v[1], l = v[2], h = v[3];
        var pct = ((c - chartData.last_close) / chartData.last_close * 100).toFixed(2);
        var color = c >= o ? '#A4332B' : '#3F6B40';
        var sign = pct >= 0 ? '+' : '';
        var isPred = p.seriesName.indexOf('预测') === 0;
        var tag = isPred ? '<span style="color:#B06A32;">[预测]</span> ' : '';
        return tag + p.name + '<br>开 ' + o.toFixed(2) + ' · 高 ' + h.toFixed(2) +
          '<br>低 ' + l.toFixed(2) + ' · 收 <b style="color:' + color + '">' + c.toFixed(2) + '</b>' +
          '<br>vs 现价 <b>' + sign + pct + '%</b>';
      }},
    }},
    series: [
      {{
        name: '历史 K 线', type: 'candlestick', data: hist,
        barWidth: '65%',
        itemStyle: {{
          color: '#A4332B', color0: '#3F6B40',
          borderColor: '#A4332B', borderColor0: '#3F6B40',
        }},
        markLine: {{
          silent: true, symbol: 'none',
          lineStyle: {{ color: '#B06A32', type: 'dashed', width: 1 }},
          label: {{
            show: true, position: 'insideEndTop',
            formatter: '预测起点',
            color: '#B06A32', fontSize: 10, fontWeight: 600,
            backgroundColor: 'rgba(251,243,224,0.95)',
            padding: [3, 7], borderRadius: 3, borderColor: '#D8CDBA', borderWidth: 1,
          }},
          data: histCount > 0 && histCount < chartData.dates.length
            ? [{{ xAxis: histCount - 0.5 }}]
            : [],
        }},
      }},
      {{
        name: '预测 K 线(金色 · 已多因子调整)', type: 'candlestick', data: pred,
        barWidth: '45%',
        itemStyle: {{
          color: '#fbbf24', color0: '#fed7aa',
          borderColor: '#d97706', borderColor0: '#fdba74',
          opacity: 0.82,
        }},
      }},
    ],
  }});

  window.addEventListener('resize', function() {{
    chart.resize();
    setTimeout(reportHeight, 60);
  }});

  // ── 通知父窗口(PublicHtmlArtifactView / ArtifactPanel)动态调 iframe 高度 ──
  // 消除"iframe 撑到父容器高度但内容较短漏空白"或"内容被硬切"的 bug
  // 父页监听 message · 收到后 iframe.height = data.height + buffer
  // 父页只放大不缩小 · 应对分批上报
  function reportHeight() {{
    var h = Math.max(
      document.documentElement.scrollHeight,
      document.body.scrollHeight,
      document.documentElement.offsetHeight,
      document.body.offsetHeight
    );
    try {{
      window.parent.postMessage({{ type: 'hunter-artifact-height', height: h }}, '*');
    }} catch(e) {{}}
  }}
  // 密集上报 · 覆盖 ECharts 异步 render / 字体加载 / DOM 布局所有时机
  // 4000ms 那次在父端 "沉降(3500ms)" 之后 · 用于覆盖为精确高度(允许缩小)
  reportHeight();
  [16, 100, 300, 600, 1000, 1500, 2500, 4000].forEach(function(delay) {{
    setTimeout(reportHeight, delay);
  }});
  window.addEventListener('load', function() {{
    reportHeight();
    setTimeout(reportHeight, 200);
  }});
  // MutationObserver · DOM 变化时也上报(ECharts 内部改 SVG 都能捕获)
  if (typeof MutationObserver !== 'undefined') {{
    var pending = null;
    var observer = new MutationObserver(function() {{
      if (pending) clearTimeout(pending);
      pending = setTimeout(reportHeight, 80);
    }});
    observer.observe(document.body, {{ childList: true, subtree: true, attributes: true }});
    // 5 秒后关掉 · 避免长期性能消耗
    setTimeout(function() {{ observer.disconnect(); }}, 5000);
  }}
}})();
</script>
</body>
</html>"""
    return html
