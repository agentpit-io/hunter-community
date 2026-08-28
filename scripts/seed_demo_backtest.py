"""Seed 复赛演示用的历史预测数据 · 3 只股票 × 90 天 × 5 horizons.

★ 合规提示:
  · 生成的所有行 model_ver='demo-v1' · 前端应展示"演示数据"徽标
  · 真实收盘 real_change 走 akshare 真数据(禁 mock)· 若拉不到该股票就跳过 · 不编数字
  · 预测 pred_change 是"复赛演示假设" · 用真实历史 + 高斯噪声合成 · 保证 ~55-65% 方向命中率
  · 因子分值是从 pred_change 和真实数据反推的示意值 · 用于 UI 展示因子归因流程
  · 生产环境请用 services/backtest/jobs.py + scheduler.py 跑真实每日流水线

Usage:
    # 在 fin-r1 容器内
    docker compose exec api python /app/scripts/seed_demo_backtest.py \
        --symbols 600519.SH 000001.SZ 600276.SH --days 90

    # 想清空重种:
    docker compose exec api python /app/scripts/seed_demo_backtest.py --wipe
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import Json, execute_values

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed_demo_backtest")

MODEL_VER = "demo-v1"
SEED = 20260829
FACTOR_NAMES = ["kronos", "main_flow", "news_sentiment", "technical",
                "valuation", "volatility", "sector_beta", "macro"]


def get_conn():
    url = os.getenv("FINDATA_DB_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("FINDATA_DB_URL/DATABASE_URL 未配置 · 无法连接数据库")
    return psycopg2.connect(url, connect_timeout=10)


def _bars_from_eastmoney_df(df) -> list[dict]:
    bars = []
    for _, row in df.iterrows():
        d = row.get("日期")
        ts = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        bars.append({
            "ts": ts,
            "open": float(row.get("开盘") or 0),
            "high": float(row.get("最高") or 0),
            "low": float(row.get("最低") or 0),
            "close": float(row.get("收盘") or 0),
            "volume": int(row.get("成交量") or 0),
        })
    return bars


def _bars_from_sina_df(df) -> list[dict]:
    bars = []
    for _, row in df.iterrows():
        d = row.get("date")
        ts = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        bars.append({
            "ts": ts,
            "open": float(row.get("open") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
            "close": float(row.get("close") or 0),
            "volume": int(row.get("volume") or 0),
        })
    return bars


def fetch_real_kline(symbol: str, days: int) -> list[dict]:
    """真实日 K · 3 次重试 + 东财/新浪双源 · GCP→大陆网络不稳自动兜底."""
    import time
    import akshare as ak
    from datetime import datetime, timedelta
    bare = symbol.split(".")[0]
    end = datetime.now()
    start = end - timedelta(days=days + 60)

    # 东财优先(前复权更准)· 3 次重试
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=bare, period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                return _bars_from_eastmoney_df(df)[-(days + 30):]
        except Exception as e:
            log.info("akshare 东财 attempt=%d %s: %s", attempt + 1, symbol, str(e)[:80])
            time.sleep(1.5 * (attempt + 1))

    # 新浪兜底
    sina_sym = ("sh" if symbol.endswith(".SH") else "sz") + bare
    for attempt in range(2):
        try:
            df = ak.stock_zh_a_daily(symbol=sina_sym, adjust="qfq")
            if df is not None and not df.empty:
                return _bars_from_sina_df(df)[-(days + 30):]
        except Exception as e:
            log.info("akshare 新浪 attempt=%d %s: %s", attempt + 1, sina_sym, str(e)[:80])
            time.sleep(1.5 * (attempt + 1))

    return []


def synthesize_predictions(bars: list[dict], symbol: str) -> tuple[list[dict], list[dict], list[dict]]:
    """从真实日 K 合成历史预测三张表的行."""
    rng = random.Random(f"{SEED}:{symbol}")
    snap_rows, bt_rows, cons_rows = [], [], []

    # 跨 base_date 的记忆:某 pred_date 上一次是谁预测的、预测了多少
    # (跨 run_date 保存 · 做 consistency 比较)
    last_pred_by_target: dict[tuple[str, int], tuple[date, float]] = {}

    # bars 按时间升序 · 每个日期 D 生成 5 个 horizon 预测(1..5),对应 D+1..D+5
    for i in range(len(bars) - 6):
        run_bar = bars[i]
        run_date_str = (run_bar.get("ts") or "")[:10]
        if not run_date_str:
            continue
        try:
            run_date = date.fromisoformat(run_date_str)
        except ValueError:
            continue
        last_close = float(run_bar.get("close") or 0)
        if last_close <= 0:
            continue

        for h in range(1, 6):
            if i + h >= len(bars):
                break
            future_bar = bars[i + h]
            future_date_str = (future_bar.get("ts") or "")[:10]
            if not future_date_str:
                continue
            future_date = date.fromisoformat(future_date_str)
            real_close = float(future_bar.get("close") or 0)
            if real_close <= 0:
                continue

            real_change = (real_close - last_close) / last_close * 100.0

            # 合成预测:真值 * skill(小) + 大噪声 · 目标 ~58% 方向命中率
            # 真实模型极少能到 70%+ · 演示数据必须落在"看着可信"的区间
            skill = 0.10 + rng.random() * 0.15    # 0.10-0.25
            noise = rng.gauss(0, max(1.5, abs(real_change) * 1.2))
            pred_change = real_change * skill + noise
            pred_close = last_close * (1 + pred_change / 100)

            # 因子分值:每个因子给个 [-1, 1] 分 · 加权和 ≈ pred_change 方向
            factors = {}
            target_score = max(-1.0, min(1.0, pred_change / 5.0))
            for fn in FACTOR_NAMES:
                base = target_score * (0.5 + rng.random() * 0.6)
                factors[fn] = round(base + rng.gauss(0, 0.25), 3)
                factors[fn] = max(-1.0, min(1.0, factors[fn]))
            score = round(sum(factors.values()) / len(factors), 3)

            if pred_change > 0.5:
                direction, signal = "up", ("强多" if pred_change > 2 else "偏多")
            elif pred_change < -0.5:
                direction, signal = "down", ("强空" if pred_change < -2 else "偏空")
            else:
                direction, signal = "flat", "中性"

            confidence = round(45 + skill * 80 + rng.gauss(0, 8), 2)
            confidence = max(15.0, min(88.0, confidence))

            snap_rows.append({
                "symbol": symbol, "run_date": run_date, "pred_date": future_date,
                "horizon": h, "base_date": run_date,
                "last_close": round(last_close, 4), "pred_close": round(pred_close, 4),
                "change_pct": round(pred_change, 4), "direction": direction,
                "score": score, "signal": signal, "confidence": confidence,
                "factors": factors, "model_ver": MODEL_VER, "clipped": False,
            })

            abs_error = round(abs(pred_change - real_change), 4)
            rel_error = round(abs_error / max(abs(real_change), 0.1), 4)
            dir_hit = ((pred_change > 0 and real_change > 0) or
                       (pred_change < 0 and real_change < 0) or
                       (abs(pred_change) < 0.5 and abs(real_change) < 0.5))
            amt_hit = abs_error < 1.0  # 幅度命中:误差 < 1%
            bt_rows.append({
                "symbol": symbol, "run_date": run_date, "pred_date": future_date,
                "horizon": h, "base_date": run_date,
                "pred_change": round(pred_change, 4),
                "real_change": round(real_change, 4), "abs_error": abs_error,
                "rel_error": rel_error, "dir_hit": dir_hit, "amt_hit": amt_hit,
                "signal": signal, "factors": factors, "model_ver": MODEL_VER,
            })

            # consistency:与上一次预测同一 pred_date 的比较(跨 run_date)
            key = (symbol, i + h)  # 用 bars 索引唯一标识目标日
            prev = last_pred_by_target.get(key)
            if prev is not None:
                prev_run, prev_change = prev
                delta = round(pred_change - prev_change, 4)
                if (prev_change > 0) != (pred_change > 0) and abs(delta) > 0.3:
                    verdict = "reversal"
                elif abs(delta) < 0.3:
                    verdict = "consistent"
                elif abs(pred_change) > abs(prev_change):
                    verdict = "strengthen"
                else:
                    verdict = "weaken"
                top_f = max(factors.items(), key=lambda kv: abs(kv[1]))
                factor_delta = {k: round(v, 3) for k, v in factors.items()}
                driver_share = round(abs(top_f[1]) / max(sum(abs(v) for v in factors.values()), 0.001) * 100, 2)
                cons_rows.append({
                    "symbol": symbol, "pred_date": future_date,
                    "prev_run": prev_run, "curr_run": run_date,
                    "prev_base": prev_run, "curr_base": run_date,
                    "prev_change": round(prev_change, 4), "curr_change": round(pred_change, 4),
                    "delta": delta, "verdict": verdict, "factor_delta": factor_delta,
                    "top_driver": top_f[0], "driver_share": driver_share,
                })
            last_pred_by_target[key] = (run_date, pred_change)

    return snap_rows, bt_rows, cons_rows


def upsert_snap(cur, rows: list[dict]):
    if not rows:
        return
    execute_values(cur, """
        INSERT INTO pred_snapshot
          (symbol, run_date, pred_date, horizon, base_date, last_close, pred_close,
           change_pct, direction, score, signal, confidence, factors, model_ver, clipped)
        VALUES %s
        ON CONFLICT (symbol, base_date, pred_date) DO UPDATE SET
          run_date=EXCLUDED.run_date, clipped=EXCLUDED.clipped,
          pred_close=EXCLUDED.pred_close, change_pct=EXCLUDED.change_pct,
          direction=EXCLUDED.direction, score=EXCLUDED.score,
          signal=EXCLUDED.signal, confidence=EXCLUDED.confidence,
          factors=EXCLUDED.factors, created_at=NOW()
    """, [(r["symbol"], r["run_date"], r["pred_date"], r["horizon"], r["base_date"],
           r["last_close"], r["pred_close"], r["change_pct"], r["direction"],
           r["score"], r["signal"], r["confidence"], Json(r["factors"]),
           r["model_ver"], r["clipped"]) for r in rows])


def upsert_bt(cur, rows: list[dict]):
    if not rows:
        return
    execute_values(cur, """
        INSERT INTO pred_backtest
          (symbol, run_date, pred_date, horizon, base_date, pred_change, real_change,
           abs_error, rel_error, dir_hit, amt_hit, signal, factors, model_ver)
        VALUES %s
        ON CONFLICT (symbol, base_date, pred_date) DO UPDATE SET
          pred_change=EXCLUDED.pred_change, real_change=EXCLUDED.real_change,
          abs_error=EXCLUDED.abs_error, rel_error=EXCLUDED.rel_error,
          dir_hit=EXCLUDED.dir_hit, amt_hit=EXCLUDED.amt_hit,
          signal=EXCLUDED.signal, factors=EXCLUDED.factors, model_ver=EXCLUDED.model_ver
    """, [(r["symbol"], r["run_date"], r["pred_date"], r["horizon"], r["base_date"],
           r["pred_change"], r["real_change"], r["abs_error"], r["rel_error"],
           r["dir_hit"], r["amt_hit"], r["signal"], Json(r["factors"]),
           r["model_ver"]) for r in rows])


def upsert_cons(cur, rows: list[dict]):
    if not rows:
        return
    execute_values(cur, """
        INSERT INTO pred_consistency
          (symbol, pred_date, prev_run, curr_run, prev_base, curr_base,
           prev_change, curr_change, delta, verdict, factor_delta,
           top_driver, driver_share)
        VALUES %s
        ON CONFLICT (symbol, pred_date, curr_base) DO UPDATE SET
          prev_run=EXCLUDED.prev_run, curr_run=EXCLUDED.curr_run,
          prev_change=EXCLUDED.prev_change, curr_change=EXCLUDED.curr_change,
          delta=EXCLUDED.delta, verdict=EXCLUDED.verdict,
          factor_delta=EXCLUDED.factor_delta,
          top_driver=EXCLUDED.top_driver, driver_share=EXCLUDED.driver_share
    """, [(r["symbol"], r["pred_date"], r["prev_run"], r["curr_run"],
           r["prev_base"], r["curr_base"], r["prev_change"], r["curr_change"],
           r["delta"], r["verdict"], Json(r["factor_delta"]),
           r["top_driver"], r["driver_share"]) for r in rows])


def wipe_demo(conn):
    cur = conn.cursor()
    for tbl in ("pred_snapshot", "pred_backtest", "pred_consistency"):
        cur.execute(f"DELETE FROM {tbl} WHERE model_ver=%s", (MODEL_VER,))
        log.info("wiped %s where model_ver=%s → %d rows", tbl, MODEL_VER, cur.rowcount)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                    default=["600519.SH", "000001.SZ", "600276.SH"])
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--wipe", action="store_true", help="删旧 demo-v1 行后重建")
    args = ap.parse_args()

    conn = get_conn()
    if args.wipe:
        wipe_demo(conn)

    total = {"snap": 0, "bt": 0, "cons": 0}
    for sym in args.symbols:
        log.info("=== %s · 拉 %d 天真实 K 线 ===", sym, args.days)
        bars = fetch_real_kline(sym, args.days)
        if not bars:
            log.warning("%s 拉不到 K 线数据 · 跳过(禁 mock 兜底)", sym)
            continue
        log.info("%s: %d bars · 生成预测…", sym, len(bars))
        snap, bt, cons = synthesize_predictions(bars, sym)
        cur = conn.cursor()
        upsert_snap(cur, snap)
        upsert_bt(cur, bt)
        upsert_cons(cur, cons)
        conn.commit()
        total["snap"] += len(snap)
        total["bt"] += len(bt)
        total["cons"] += len(cons)
        log.info("%s: pred_snapshot +%d · pred_backtest +%d · pred_consistency +%d",
                 sym, len(snap), len(bt), len(cons))

    conn.close()
    log.info("=== 完成 · snap=%d bt=%d cons=%d · model_ver=%s ===",
             total["snap"], total["bt"], total["cons"], MODEL_VER)


if __name__ == "__main__":
    main()
