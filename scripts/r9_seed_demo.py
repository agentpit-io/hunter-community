"""R9 演示数据播种（开发库 · 全部经唯一入口 / 唯一写路径，不直改业务表）。

跑法：docker exec -i hunter-community-api-1 python - < /tmp/r9_seed.py
幂等：经验按 statement 去重；提案用固定 proposal_id，存在即跳过。
"""
import json
import sys

import psycopg2.extras

sys.path.insert(0, "/app")

from app.services.fin import memory as M          # noqa: E402
from app.services.fin import evolution as EV      # noqa: E402

PID = "prj_b7791191b3af462791ed496b"
UID = "e622b871-e7d8-4976-8619-52d139608c76"

TRADE_CN = "trd_c94d95bd04474e0194eb9ff6"
TRADE_HK = "trd_3a89f19c2e6d45aab4e7d094"
REPORT_CN = "rpt_2f32f11da1a355cfc0cbde9b"
REPORT_HK = "rpt_fe61f06bfb44b2fa9c1a9e85"
SNAPSHOT = "SNAP-20261011-093100-601398"

P1 = "evp_r9demo_passed00000001"
P2 = "evp_r9demo_inconclusive001"

EXP = [
    dict(
        kind="verified",
        statement="缩量整理之后追高的回撤概率显著上升",
        applicability="A 股主板 · 震荡市 · 成交额中等以上",
        invalidation_condition="单边趋势市失效；样本口径变更后需重验",
        method="按同形态样本分组比较后续回撤分布",
        sample_size=12,
        polarity="refute",
        symbols=["CN_A:601398"],
        regime_tags=["bear"],
        regime_source="human",
        confidence=0.82,
        evidence=[{"evidence_kind": "trade", "ref_id": TRADE_CN}],
    ),
    dict(
        kind="fact",
        statement="长假前一周消费板块的资金流入更有持续性",
        applicability="A 股 · 节前窗口",
        invalidation_condition="节前无成交放量时失效",
        polarity="support",
        symbols=["CN_A:600519"],
        regime_tags=["bear"],
        regime_source="human",
        confidence=0.58,
        evidence=[{"evidence_kind": "report", "ref_id": REPORT_CN}],
    ),
    dict(
        kind="hypothesis",
        statement="同板块第二只标的改用限价挂单比市价单更省滑点",
        applicability="港股主板",
        invalidation_condition="未验证，不授予执行效力",
        polarity="neutral",
        symbols=["HK:00700"],
        regime_tags=["unknown"],
        regime_source="human",
        valid_until="2026-09-01T00:00:00+08:00",   # 已过期 → 需重验角标
        evidence=[{"evidence_kind": "trade", "ref_id": TRADE_HK}],
    ),
    dict(
        kind="fact",
        statement="新能源车板块存在周初必涨的效应",
        applicability="A 股 · 板块层面",
        invalidation_condition="被数据否定，不再作为买入依据",
        status="已推翻",
        polarity="refute",
        symbols=["CN_A:002594"],
        regime_tags=["unknown"],
        regime_source="human",
        confidence=0.05,
        evidence=[{"evidence_kind": "snapshot", "ref_id": SNAPSHOT}],
    ),
]


def qi(project_id, stmt):
    for it in M.query(project_id=project_id, caller="jwt", user_id=UID)["items"]:
        if it["statement"] == stmt:
            return it
    return None


def seed_experiences():
    made = []
    for spec in EXP:
        ex = qi(PID, spec["statement"])
        if ex:
            made.append(ex)
            print("  · 已存在，跳过:", ex["experience_id"], ex["status"])
            continue
        row = M.append_evidence(caller="jwt", project_id=PID, user_id=UID, **spec)
        made.append(row)
        print("  · 写入:", row["experience_id"], spec["kind"], row["status"], row["source"])
    return made


def make_shadow(validation_id, keys, capital, cand_final, inc_final, points):
    rows = []
    for i, d in enumerate(keys):
        frac = (i + 1) / len(keys)
        inc_total = round(capital * (1.0 + (inc_final - 1.0) * frac), 4)
        cand_total = round(capital * (1.0 + (cand_final - 1.0) * frac), 4)
        for arm, total in (("incumbent", inc_total), ("candidate", cand_total)):
            sig = {"action": "buy", "reason": "影子决策", "fill": {"amount": 5000.0, "qty": 100, "price": 50.0}}
            rows.append(dict(
                validation_id=validation_id, arm=arm, trade_date=d, point="CN_A-0930",
                symbol="601398", quote_as_of=f"{d}T09:30:00+08:00",
                signal=sig, filled=True, fee=5.0, slippage=1.0,
                position={"cash_available": total, "cash_frozen": 0.0, "positions": []},
                valuation={"cash_available": total, "cash_frozen": 0.0, "market_value": 0.0,
                           "total_assets": total, "nav": round(total / capital, 6),
                           "initial_capital": capital},
            ))
    return rows


def main():
    print("== 1) 经验 ==")
    seed_experiences()

    print("== 2) 冻结快照 ==")
    snap = M.query(project_id=PID, caller="jwt", user_id=UID, freeze=True)
    snap_id = snap["memory_snapshot_id"]
    ids = [i["experience_id"] for i in snap["items"]]
    print("  snapshot:", snap_id, "items:", ids)
    # 证据只取 regime_tag 一致的两条（unknown 不得与明确 regime 混组）
    ev_refs = [i["experience_id"] for i in snap["items"]
               if (i.get("regime_tags") or []) == ["bear"]]
    print("  evidence_refs:", ev_refs)

    print("== 3) 读基线配置 ==")
    conn = EV.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            base = EV.read_config(cur, PID)
    finally:
        conn.rollback()
        conn.close()
    print("  base:", base)
    # 这里改 `hold_days_max` / `take_profit_pct` 两个白名单字段。
    # （历史注释已作废：R6 初版白名单曾把 `stop_loss_pct` 写成正号范围 `[0.005,0.5]`，
    #  与 `fin_param` 的负号口径对不上；R10 已把它改正为负号 `[-0.5,-0.005]`，
    #  `stop_loss_pct` 现在也能提案了 —— 见 `evolution.ALGO_VERSION` 的 v2 说明。）
    hd = int(base["hold_days_max"])
    new_hd = 1 if hd > 1 else hd + 1
    cand1 = {**base, "hold_days_max": new_hd}
    diff1 = EV.config_diff(base, cand1)
    tp = float(base["take_profit_pct"])
    new_tp = round(tp + 0.2, 6)
    cand2 = {**base, "take_profit_pct": new_tp}
    diff2 = EV.config_diff(base, cand2)

    def proposals_exist():
        return {p["proposal_id"] for p in EV.list_proposals(project_id=PID, user_id=UID, limit=100)}

    have = proposals_exist()

    print("== 4) 提案 P1（验证通过）==")
    if P1 in have:
        print("  已存在，跳过")
    else:
        out = EV.propose(project_id=PID, evidence_refs=ev_refs, evidence_snapshot_id=snap_id,
                         candidate_config=cand1, param_diff=diff1, target="strategy",
                         regime_tags=["bear"],
                         rationale="持有天数收紧一档：失败经验显示回撤尾部集中在拿太久未走的样本上",
                         created_by="r9-seed", proposal_id=P1)
        print("  created:", out["proposal_id"], out["direction"], out["plan_hash"])

    print("== 6) 取校验窗口用的交易日 ==")
    conn = EV.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT trade_date FROM fin_market_calendar
                            WHERE market='CN_A' AND is_trading AND trade_date <= CURRENT_DATE
                            ORDER BY trade_date DESC LIMIT 30""")
            days = [str(r[0]) for r in cur.fetchall()]
            cur.execute("SELECT initial_capital FROM fin_project WHERE project_id=%s", (PID,))
            capital = float(cur.fetchone()[0])
    finally:
        conn.rollback()
        conn.close()
    days = sorted(days)
    p1_days = days[-20:]                     # 20 个可比样本（两臂都有决策）
    p2_days = days[-3:]                      # 只有 3 个 → 样本不足 → inconclusive
    window_start = days[0]
    print("  capital:", capital, "p1_days:", p1_days[0], "→", p1_days[-1], "p2_days:", p2_days)

    print("== 7) P1 影子 + 判定（应 passed）==")
    EV.start_validation(proposal_id=P1, market="CN_A", trade_date=window_start, actor="r9-seed")
    w = EV.record_shadow_events(records=make_shadow(P1, p1_days, capital, 1.03, 1.000, None))
    print("  shadow:", w)
    v = EV.evaluate_validation(proposal_id=P1, market="CN_A", trade_date=days[-1], actor="r9-seed")
    print("  verdict:", v.get("verdict"), "|", str(v.get("reason"))[:90])

    print("== 5) 提案 P2（不结论）==")
    if P2 in have:
        print("  已存在，跳过")
    else:
        out = EV.propose(project_id=PID, evidence_refs=ev_refs, evidence_snapshot_id=snap_id,
                         candidate_config=cand2, param_diff=diff2, target="strategy",
                         regime_tags=["bear"],
                         rationale="止盈幅度放宽一档：样本还太少，先跑影子攒可比样本",
                         created_by="r9-seed", proposal_id=P2)
        print("  created:", out["proposal_id"], out["direction"], out["plan_hash"])

    print("== 8) P2 影子 + 判定（应 inconclusive）==")
    EV.start_validation(proposal_id=P2, market="CN_A", trade_date=window_start, actor="r9-seed")
    w = EV.record_shadow_events(records=make_shadow(P2, p2_days, capital, 1.01, 1.000, None))
    print("  shadow:", w)
    v = EV.evaluate_validation(proposal_id=P2, market="CN_A", trade_date=days[-1], actor="r9-seed")
    print("  verdict:", v.get("verdict"), "|", str(v.get("reason"))[:90])

    print("== 9) 对 P2 试一次生效（应被闸门拒 → rejected_by_gate）==")
    try:
        EV.apply_proposal(proposal_id=P2, expected_base_config_hash=base_hash(PID),
                          actor="r9-seed", confirm=True)
        print("  ⚠ 竟然生效了 —— 不该发生")
    except Exception as exc:                       # noqa: BLE001
        print("  被拒（预期）:", type(exc).__name__, str(exc)[:110])

    print("== 完成 ==")
    for p in EV.list_proposals(project_id=PID, user_id=UID, limit=20):
        print("  ", p["proposal_id"], p["status"], p["target"], p["direction"])


def base_hash(project_id):
    conn = EV.get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            return EV.hash_config(EV.read_config(cur, project_id))
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main()
