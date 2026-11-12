"""M-32 · 数字口径校验（**可重复跑**）· 只读，不改任何数据。

回答一个问题：**报告正文里的每一个数字，都能在 `fin_report_fact` 里找到对应行吗？**

用法：

    # 校验一份已生成的报告
    DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5598/hunter \
      cd apps/api && PYTHONPATH=. python scripts/check_report_numbers.py --report rpt_xxx

    # 按 (项目, 交易日) 校验（报告 id 由二者推导）
    ... python scripts/check_report_numbers.py --project prj_xxx --date 2026-09-30

    # 列出某项目最近的报告
    ... python scripts/check_report_numbers.py --list prj_xxx

    # 自检：故意把报告正文里的一个数字改坏，看校验能不能拦下（**只读，直接改内存里的副本**）
    ... python scripts/check_report_numbers.py --report rpt_xxx --tamper

退出码：0 = 全部数字可追溯；1 = 有违规；2 = 用法/数据错误。

明细逐行打印「数字 → metric_key」，这张表就是「可追溯到账本或指标代码」的凭据。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.fin import report as R  # noqa: E402


def _tamper_text(text: str) -> str:
    """把正文里的第一个百分数改成一个对不上的数（模拟「AI 编数字」）。只改副本。"""
    import re
    m = re.search(r"[+\-]?\d+(?:\.\d+)?%", text or "")
    if not m:
        return (text or "") + " 另外本期收益率 +8.74%。"
    return text[:m.start()] + "+8.74%" + text[m.end():]


def _print_trace(res: dict) -> None:
    print(f"  报告 {res['report_id']} · 状态 {res['status']} · 检查了 {res['numbers_inspected']} 个数字")
    print(f"  {'字段':<22}{'数字':>14}  → metric_key")
    for row in res["trace"]:
        mk = row["metric_key"] or "!! 找不到"
        print(f"  {row['field']:<22}{row['token']:>14}  → {mk}")
    if res["violations"]:
        print("\n  ❌ 违规：")
        for v in res["violations"]:
            print(f"     [{v['field']}] {v['token']} · {v['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M-32 报告数字口径校验（只读）")
    ap.add_argument("--report", help="报告 id（rpt_...）")
    ap.add_argument("--project", help="项目 id（prj_...），配合 --date 推导报告 id")
    ap.add_argument("--date", help="交易日 YYYY-MM-DD")
    ap.add_argument("--list", dest="list_project", help="列出某项目最近的报告")
    ap.add_argument("--tamper", action="store_true",
                    help="自检：把正文里一个数字改坏，验证校验能拦下（不改库）")
    args = ap.parse_args()

    conn = R.get_conn()
    try:
        if args.list_project:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT report_id, trade_date, status, artifact_ref FROM fin_report "
                    "WHERE project_id = %s ORDER BY trade_date DESC LIMIT 20", (args.list_project,))
                rows = cur.fetchall()
            if not rows:
                print(f"项目 {args.list_project} 没有任何报告")
                return 0
            for rid, d, st, ref in rows:
                print(f"  {d}  {st:<10}  {rid}  {ref or ''}")
            return 0

        report_id = args.report
        if not report_id and args.project and args.date:
            report_id = R.report_id_for(args.project, args.date)
        if not report_id:
            ap.error("需要 --report，或 --project + --date，或 --list")

        loaded = R.load_report(conn, report_id)
        if not loaded:
            print(f"报告不存在：{report_id}")
            return 2

        if args.tamper:
            # 只改内存里的副本，**不写库**；走与落库时同一套回读校验。
            report = dict(loaded["report"])
            report["analysis_text"] = _tamper_text(report.get("analysis_text") or "")
            res = R.validate_report(report, loaded["facts"])
            print("== 自检：故意改坏一个数字（内存副本，未写库）==")
            print(f"  正文改为：…{report['analysis_text'][-80:]}")
            print(f"  校验 ok={res['ok']} · 检查 {res['checked']} 个 · 命中 {res['matched']} 个")
            for v in res["violations"]:
                print(f"  ❌ [{v['field']}] {v['token']} · {v['reason']}")
            return 0 if not res["ok"] else 1   # 拦下了才算自检通过

        res = R.validate_stored(conn, report_id)
        print("== M-32 · 报告数字口径校验 ==")
        _print_trace(res)
        print("\n  ✅ 每个数字都能追溯到 fin_report_fact" if res["ok"]
              else "\n  ❌ 存在无法追溯的数字")
        return 0 if res["ok"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
