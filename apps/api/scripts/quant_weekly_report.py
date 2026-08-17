"""E-2 · Quant 数据质量周报
(Phase E · 2026-08-18)

每周一 08:00 CST · APScheduler 触发 · markdown 报告
5 项检查:
  1. 各因子本周覆盖 code 数(应 ≥ 250 for hs300)
  2. 异常 z-score(|z| > 5 · Top 20)
  3. K 线完整性(本周交易日 vs klines 行数)
  4. AKShare / finance-data 数据延迟(最新数据日期 vs 今天)
  5. backtest_result 本周新增数

用法:
  # 手动
  ./venv/bin/python3 scripts/quant_weekly_report.py
  # cron / APScheduler
  · 见 services/quant/scheduler.py weekly_report_job
"""
from datetime import date, timedelta
from app.services.database import get_conn


def report_md() -> str:
    md = ["# Quant 数据质量周报 · " + date.today().isoformat(), ""]
    week_start = date.today() - timedelta(days=7)

    conn = get_conn(); cur = conn.cursor()

    # 1. 因子覆盖
    md.append("## 1. 因子覆盖(本周新增)")
    cur.execute("""
        SELECT factor_key, COUNT(DISTINCT code) AS n, COUNT(*) AS rows
        FROM factor_value
        WHERE trade_date >= %s
        GROUP BY factor_key ORDER BY factor_key
    """, (week_start,))
    md.append("| 因子 | 覆盖 code 数 | 行数 | 状态 |")
    md.append("|---|---|---|---|")
    rows = cur.fetchall()
    if not rows:
        md.append("| _(本周无因子更新)_ |||🚨|")
    for k, n, rs in rows:
        status = "✅" if n >= 250 else "⚠" if n >= 100 else "❌"
        md.append(f"| {k} | {n} | {rs} | {status} |")

    # 2. 异常值
    md.append("\n## 2. 异常 z-score(|z| > 5 · Top 20)")
    cur.execute("""
        SELECT factor_key, code, trade_date, z_score
        FROM factor_value
        WHERE trade_date >= %s AND ABS(z_score) > 5
        ORDER BY ABS(z_score) DESC LIMIT 20
    """, (week_start,))
    outliers = cur.fetchall()
    if not outliers:
        md.append("✅ 无异常值")
    else:
        md.append("| 因子 | code | 日期 | z-score |")
        md.append("|---|---|---|---|")
        for k, c, d, z in outliers:
            md.append(f"| {k} | {c} | {d} | {float(z):+.2f} |")

    # 3. K 线完整性
    md.append("\n## 3. K 线完整性")
    cur.execute("""
        SELECT COUNT(DISTINCT code) AS codes,
               COUNT(*) AS rows,
               MIN(ts) AS earliest,
               MAX(ts) AS latest
        FROM klines WHERE period='daily'
    """)
    r = cur.fetchone()
    md.append(f"- 总 K 线:{r[1]} 行 · {r[0]} 只 code · {r[2]} → {r[3]}")
    lag = (date.today() - r[3]).days if r[3] else None
    md.append(f"- 最新数据延迟:**{lag} 天** " + ("✅" if (lag is not None and lag <= 3) else "⚠"))

    # 4. factor_ic 覆盖
    md.append("\n## 4. IC 覆盖")
    cur.execute("""
        SELECT COUNT(DISTINCT factor_key) AS factors,
               COUNT(*) AS rows,
               MAX(trade_date) AS latest
        FROM factor_ic
    """)
    r = cur.fetchone()
    md.append(f"- factor_ic 表:{r[0]} 因子 · {r[1]} 行 · 最新 {r[2]}")

    # 5. backtest_result 本周新增
    md.append("\n## 5. 回测活动(本周)")
    cur.execute("""
        SELECT COUNT(*) FROM backtest_result WHERE created_at >= %s
    """, (week_start,))
    n = cur.fetchone()[0]
    md.append(f"- 本周新回测:{n} 次")

    cur.execute("SELECT COUNT(*) FROM strategy WHERE is_public = TRUE")
    pubs = cur.fetchone()[0]
    md.append(f"- 公开策略总数:{pubs}")

    cur.close(); conn.close()

    md.append("\n---\n_自动生成 · APScheduler quant_weekly_report_job_")
    return "\n".join(md)


def send_report():
    """发送(v1 · 打印到日志 + 保存文件 · SMTP 集成留 v2)"""
    from datetime import datetime
    md = report_md()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = f"/tmp/quant-weekly-{ts}.md"
    with open(out_path, "w") as f:
        f.write(md)
    print(f"[weekly] 报告已写 {out_path}")
    # 打印 · pm2 log 可见
    print(md)
    return out_path


if __name__ == "__main__":
    send_report()
