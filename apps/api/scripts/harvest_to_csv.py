"""服务器采集 —— 下载直接落 CSV,**不经过数据库**。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      23_20260824_数据包分发方案.md

## 为什么不用完整的 docker 栈

我们要的产物就是数据包,而数据包本来就是 CSV.gz。中间过一道 Postgres
纯属多余 —— 服务器上不需要 api / web / redis / postgres 任何一个。

    下载 → 直接写 CSV.gz → 打包 → 传云盘

因子也不在服务器算(**包里不装因子,用户本地算**)。

所以这台机器只要 Python + requests(K线)+ akshare(财报)。

## 为什么 import local_kline 而不是照着抄一遍

取数那几十行里踩过的坑不能重来:

  · 限速 0.15 秒 —— 不限速的话 800 只连打,腾讯清一色 ReadTimeout
  · 退避重试 —— 一次失败就放弃的表现是"这只票没数据",和真没数据分不开
  · **腾讯字段顺序是 [date, open, close, high, low, volume]**,close 排在
    high 前面。搞错会把开盘价当收盘价存,而这种错在回测里看不出来 ——
    数字都在合理范围、曲线照样能画

`local_kline` 顶层只 import requests,不碰数据库,可以直接拿来用。

## 用法

    cd ~/hunter-harvest/hunter-community/apps/api
    PYTHONPATH=. python3 scripts/harvest_to_csv.py \
        --years 2016-2026 --out ~/hunter-harvest/out

    # 只要日线(快 5 倍)
    PYTHONPATH=. python3 scripts/harvest_to_csv.py --no-financial

    # 后台跑
    nohup ... > ~/hunter-harvest/harvest.log 2>&1 &
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))          # apps/api

from app.services.quant import local_kline          # noqa: E402
from app.services.quant import package_spec as spec  # noqa: E402

# ── 限流退避,和 data_job 一套逻辑 ────────────────────────────
# 服务器那次全 A 股失败 57%,原因见
#   24_20260827_全量下载限流问题与退避方案.md
# 腾讯是**开关式**限流:要么全成要么全败,而且会自己恢复。
# 所以对策不是"统一调慢",是"检测到被掐就重退避、恢复了就全速"。
_MISS_TRIGGER = 5              # 连续失败这么多只 = 判定被限流
_BACKOFF = (60, 300, 900)      # 退避梯度
_REST_EVERY = 500              # 每这么多只主动歇一会
_REST_SEC = 120
_RETRY_WAIT = 600              # 整轮跑完等这么久再重跑失败项

# 全 A 股清单随代码分发,不联网
_BASELINE = _HERE.parents[1] / "data" / "stocks_catalog_baseline.json"


def all_a_codes() -> list[tuple[str, str]]:
    d = json.loads(_BASELINE.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else (d.get("items") or d.get("stocks") or [])
    return [(str(x["code"]).zfill(6), x.get("name") or "") for x in items if x.get("code")]


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class YearWriter:
    """按年分卷写 CSV.gz。

    **一边下一边写,不在内存里攒** —— 全 A 股十年 1380 万行,
    攒在内存里是几 GB。
    """

    def __init__(self, out: Path, y_from: int, y_to: int):
        self.out = out
        self.files = {}
        self.writers = {}
        self.rows = {}
        for y in range(y_from, y_to + 1):
            p = out / f"klines-{y}.csv.gz"
            f = gzip.open(p, "wt", encoding="utf-8", newline="")
            self.files[y] = (p, f)
            self.writers[y] = csv.writer(f)
            self.rows[y] = 0

    def write(self, code: str, r: dict) -> None:
        y = int(r["ts"][:4])
        w = self.writers.get(y)
        if w is None:
            return
        # 列顺序必须和 package_spec.KLINE.cols 一致 —— CSV 靠位置对应
        w.writerow([code, "daily", r["ts"], r["open"], r["high"],
                    r["low"], r["close"], int(r["volume"] or 0)])
        self.rows[y] += 1

    def close(self) -> list[dict]:
        vols = []
        for y, (p, f) in sorted(self.files.items()):
            f.close()
            if self.rows[y] == 0:
                p.unlink(missing_ok=True)
                continue
            vols.append({"file": p.name, "kind": "kline",
                         "covers": [f"{y}-01-01", f"{y}-12-31"],
                         "rows": self.rows[y], "bytes": p.stat().st_size,
                         "sha256": _sha256(p)})
        return vols


def harvest_klines(codes, out: Path, y_from: int, y_to: int,
                   start: date, end: date, log_every: int = 50) -> tuple[list, dict]:
    yw = YearWriter(out, y_from, y_to)
    ok = fail = 0
    failed_codes = []
    t0 = time.time()
    miss_streak = 0
    backoff_i = 0
    unsupported = 0
    local_kline.set_retry(True)
    for i, (code, _name) in enumerate(codes, 1):
        # 北交所免费源根本没有历史日线(只给当天一条),跑也是白跑,
        # 而且白白消耗配额、把限流提前触发
        if local_kline.is_unsupported(code):
            unsupported += 1
            continue

        if i % _REST_EVERY == 0 and i < len(codes):
            print(f"[kline] 已下 {i} 只 · 主动歇 {_REST_SEC//60} 分钟", flush=True)
            time.sleep(_REST_SEC)

        rows = local_kline.fetch_daily(code, start, end)
        if not rows:
            # **拿不到就是拿不到**,不写空行不补零 —— 补一行假价格会让
            # 回测的收益凭空出现
            fail += 1
            failed_codes.append(code)
            miss_streak += 1
            if miss_streak >= _MISS_TRIGGER:
                # 被掐了。继续硬打只会延长被掐的时间,而且现在每只还要
                # 重试 3 次 = 请求量放大 3 倍
                local_kline.set_retry(False)
                wait = _BACKOFF[min(backoff_i, len(_BACKOFF) - 1)]
                backoff_i += 1
                print(f"[kline] 连续失败 {miss_streak} 只 · 疑似限流 · "
                      f"等 {wait//60} 分钟", flush=True)
                time.sleep(wait)
                miss_streak = 0
        else:
            for r in rows:
                yw.write(code, r)
            ok += 1
            # 成功就恢复全速 —— 掐是暂时的,恢复了不该继续慢
            if miss_streak or backoff_i:
                miss_streak = 0
                backoff_i = 0
                local_kline.set_retry(True)
        if i % log_every == 0:
            el = time.time() - t0
            eta = (len(codes) - i) * el / i
            print(f"[kline] {i}/{len(codes)} · 成 {ok} 败 {fail} · "
                  f"已用 {el/60:.0f}m · 预计还要 {eta/60:.0f}m", flush=True)
        time.sleep(0.15)          # 见模块头:这个 sleep 不能省

    # ── 失败的攒到最后重跑 ──────────────────────────────────
    # **不当场重试** —— 当场重试就是在被限的时候重试,必然还是失败。
    # 等整轮跑完歇一会儿,实测上游大概率已经恢复。
    if failed_codes:
        print(f"[kline] 第一轮失败 {len(failed_codes)} 只 · "
              f"歇 {_RETRY_WAIT//60} 分钟后重跑", flush=True)
        time.sleep(_RETRY_WAIT)
        local_kline.set_retry(True)
        still = []
        for j, code in enumerate(failed_codes, 1):
            rows = local_kline.fetch_daily(code, start, end)
            if rows:
                for r in rows:
                    yw.write(code, r)
                ok += 1
                fail -= 1
            else:
                still.append(code)
            if j % 50 == 0:
                print(f"[kline] 重跑 {j}/{len(failed_codes)} · "
                      f"已救回 {len(failed_codes)-len(still)}", flush=True)
            time.sleep(0.15)
        print(f"[kline] 重跑救回 {len(failed_codes)-len(still)}/{len(failed_codes)} 只",
              flush=True)
        failed_codes = still

    vols = yw.close()
    return vols, {"ok": ok, "fail": fail, "unsupported": unsupported,
                  "failed": failed_codes[:50],
                  "sec": int(time.time() - t0)}


def harvest_financial(codes, out: Path, log_every: int = 25) -> tuple[list, dict]:
    # 这里才 import —— financial_store 顶层引了 get_conn,
    # 而我们不连库。只用它的 fetch/parse,不用 save
    from app.services.quant import financial_store as fs

    p = out / "financial.csv.gz"
    ok = fail = rows_n = 0
    failed_codes = []
    t0 = time.time()
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for i, (code, _name) in enumerate(codes, 1):
            df = fs.fetch_indicator(code)
            recs = fs.parse(df) if df is not None else []
            if not recs:
                fail += 1
                failed_codes.append(code)
            else:
                ok += 1
                for rd, key, val in recs:
                    # 列顺序同 package_spec.FINANCIAL.cols
                    w.writerow([code, rd.isoformat(), key, val])
                    rows_n += 1
            if i % log_every == 0:
                el = time.time() - t0
                eta = (len(codes) - i) * el / i
                print(f"[fin] {i}/{len(codes)} · 成 {ok} 败 {fail} · "
                      f"已用 {el/60:.0f}m · 预计还要 {eta/60:.0f}m", flush=True)
            time.sleep(0.15)
    if rows_n == 0:
        p.unlink(missing_ok=True)
        return [], {"ok": 0, "fail": fail, "sec": int(time.time() - t0)}
    return ([{"file": p.name, "kind": "financial", "rows": rows_n,
              "bytes": p.stat().st_size, "sha256": _sha256(p)}],
            {"ok": ok, "fail": fail, "failed": failed_codes[:50],
             "sec": int(time.time() - t0)})


def write_meta(codes, out: Path) -> list[dict]:
    """股票名 —— 成分股和行业在服务器上没有(那要查库),
    留给用户本地同步。包里只带名字,免得界面显示两遍代码。"""
    p = out / "meta-stocks.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for code, name in codes:
            # 列顺序同 package_spec.STOCKS.cols
            w.writerow([code, name or code, "A",
                        "SH" if code[0] in "69" else "SZ", "stock"])
    return [{"file": p.name, "kind": "meta", "table": "stocks",
             "rows": len(codes), "bytes": p.stat().st_size,
             "sha256": _sha256(p)}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="", help="2016-2026 · 不填按 --months 推")
    ap.add_argument("--months", type=int, default=120)
    ap.add_argument("--out", default=str(Path.home() / "hunter-harvest" / "out"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只(试跑用)")
    ap.add_argument("--no-financial", action="store_true")
    args = ap.parse_args()

    end = date.today()
    if args.years:
        y_from, y_to = (int(x) for x in args.years.split("-"))
        start = date(y_from, 1, 1)
    else:
        start = end - timedelta(days=args.months * 31)
        y_from, y_to = start.year, end.year

    codes = all_a_codes()
    if args.limit:
        codes = codes[:args.limit]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[harvest] {len(codes)} 只 · {start} ~ {end} · "
          f"财报={'否' if args.no_financial else '是'} · 输出 {out}", flush=True)

    vols = write_meta(codes, out)
    kv, kstat = harvest_klines(codes, out, y_from, y_to, start, end)
    vols += kv
    print(f"[harvest] 日线完成 · 成 {kstat['ok']} 败 {kstat['fail']} · "
          f"{kstat['sec']/60:.0f} 分钟", flush=True)

    fstat = {}
    if not args.no_financial:
        fv, fstat = harvest_financial(codes, out)
        vols += fv
        print(f"[harvest] 财报完成 · 成 {fstat['ok']} 败 {fstat['fail']} · "
              f"{fstat['sec']/60:.0f} 分钟", flush=True)

    manifest = {
        "package": "hunter-data",
        "schema_version": spec.SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "built_by": "harvest_to_csv.py@" + os.getenv("HARVEST_REV", "server"),
        "scope": "all_a",
        "stocks": len(codes),
        "years": [y_from, y_to],
        "volumes": vols,
        # 失败清单留在包里 —— 用户能看出"这批数据里哪些票没有",
        # 而不是回测时才发现某几只永远选不出来
        "stats": {"kline": kstat, "financial": fstat},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(v["bytes"] for v in vols)
    print(f"[harvest] 全部完成 · {len(vols)} 卷 · {total/1048576:.1f} MB · {out}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
