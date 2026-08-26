"""把库里的数据打成可分发的数据包。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      23_20260824_数据包分发方案.md

## 用法

    docker compose exec -T api python3 /app/scripts/build_data_package.py \
        --scope hs300 --years 2024-2026 --out /opt/hunter-data/packages

    # 全量
    docker compose exec -T api python3 /app/scripts/build_data_package.py \
        --scope all_a --years 2016-2026

## 产出

    hunter-data-hs300-20260826.tar.gz
    └── manifest.json          说明书 · 谁在导入前靠它判断"这是什么"
        meta.csv.gz            股票名 / 成分股 / 行业
        klines-2024.csv.gz     按年分卷
        klines-2025.csv.gz
        klines-2026.csv.gz
        financial.csv.gz

## 为什么按年分卷

一卷约 23 MB。断了只重下这一卷(十几秒),而不是从头来 ——
这让"云盘不支持断点续传"从致命问题降级成小麻烦。
用户也可以只导他要的年份。

## 为什么不打包因子

`factor_value` 比它的原料 `klines` 还大 2 倍多,而因子是纯本地计算
(800 只 × 59 个调仓日几分钟)。打进去除了让包翻倍,还会让用户拿到
**我们这边算法版本**算的因子 —— 他本地代码若已更新,库里就是新旧
混着的值,而这种不一致在回测结果里看不出来。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, "/app")

from app.services.database import get_conn                    # noqa: E402
from app.services.quant import package_spec as spec           # noqa: E402

# 列定义**从 package_spec 拿,不在这里另写一份** ——
# CSV 靠位置对应列,两边各写一份的话改一处忘另一处,
# 数据照样导进去、只是价格列里装的是成交量,而且不报任何错。
SCHEMA_VERSION = spec.SCHEMA_VERSION
KLINE_COLS = spec.KLINE.col_sql
FIN_COLS = spec.FINANCIAL.col_sql
STOCK_COLS = spec.STOCKS.col_sql
INDCOMP_COLS = spec.INDEX_COMPONENT.col_sql
INDUSTRY_COLS = spec.INDUSTRY.col_sql


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_out(sql: str, params: tuple, dest: Path) -> int:
    """COPY … TO STDOUT 直接写进 .gz。返回行数。

    用 psycopg 的 copy_expert 而不是先落磁盘再压 —— 全量 3 GB 的中间
    文件对打包机器是不必要的负担。
    """
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        with gzip.open(dest, "wt", encoding="utf-8", newline="") as gz:
            cur.copy_expert(cur.mogrify(sql, params).decode(), gz)
            n = cur.rowcount
        return n if n and n > 0 else _count_lines(dest)
    finally:
        cur.close(); conn.close()


def _count_lines(p: Path) -> int:
    n = 0
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for _ in f:
            n += 1
    return n


def build(scope: str, y_from: int, y_to: int, out_dir: Path,
          codes: list[str] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    work = Path(tempfile.mkdtemp(prefix="hunterpack-"))
    volumes = []
    t0 = time.time()

    # 范围 —— codes 为空表示全部
    where_code = "" if not codes else " AND code = ANY(%s)"
    cp = (codes,) if codes else ()

    print(f"[pack] scope={scope} years={y_from}-{y_to} "
          f"stocks={'全部' if not codes else len(codes)}")

    # ── 元数据(股票名 / 成分股 / 行业)────────────────────
    for name, cols, table, extra in (
        ("stocks", STOCK_COLS, "stocks", " WHERE market='A'"),
        ("index_component", INDCOMP_COLS, "index_component", ""),
        ("stock_industry", INDUSTRY_COLS, "stock_industry", ""),
    ):
        f = work / f"meta-{name}.csv.gz"
        try:
            rows = _copy_out(
                f"COPY (SELECT {cols} FROM {table}{extra}) TO STDOUT WITH CSV",
                (), f)
        except Exception as e:                                # noqa: BLE001
            # 元数据缺一张不该让整个包失败 —— 但要说出来,
            # 否则用户导完发现没有股票名会以为是导入 bug
            print(f"[pack] ⚠ {table} 导出失败({type(e).__name__})· 包里不含这张表")
            continue
        volumes.append({"file": f.name, "kind": "meta", "table": table,
                        "rows": rows, "bytes": f.stat().st_size,
                        "sha256": _sha256(f)})
        print(f"[pack]   {f.name:28} {rows:>9} 行 · {f.stat().st_size/1048576:.1f} MB")

    # ── K 线 · 按年分卷 ──────────────────────────────────
    for y in range(y_from, y_to + 1):
        f = work / f"klines-{y}.csv.gz"
        sql = (f"COPY (SELECT {KLINE_COLS} FROM klines "
               f"WHERE period='daily' AND ts >= '{y}-01-01' AND ts <= '{y}-12-31'"
               f"{where_code} ORDER BY code, ts) TO STDOUT WITH CSV")
        rows = _copy_out(sql, cp, f)
        if rows == 0:
            f.unlink(missing_ok=True)
            print(f"[pack]   klines-{y}                 (无数据 · 跳过)")
            continue
        volumes.append({"file": f.name, "kind": "kline",
                        "covers": [f"{y}-01-01", f"{y}-12-31"],
                        "rows": rows, "bytes": f.stat().st_size,
                        "sha256": _sha256(f)})
        print(f"[pack]   {f.name:28} {rows:>9} 行 · {f.stat().st_size/1048576:.1f} MB")

    # ── 财报指标 ────────────────────────────────────────
    f = work / "financial.csv.gz"
    sql = (f"COPY (SELECT {FIN_COLS} FROM financial_metric "
           f"WHERE TRUE{where_code} ORDER BY code, report_date) TO STDOUT WITH CSV")
    rows = _copy_out(sql, cp, f)
    if rows:
        volumes.append({"file": f.name, "kind": "financial", "rows": rows,
                        "bytes": f.stat().st_size, "sha256": _sha256(f)})
        print(f"[pack]   {f.name:28} {rows:>9} 行 · {f.stat().st_size/1048576:.1f} MB")
    else:
        f.unlink(missing_ok=True)

    # ── manifest ────────────────────────────────────────
    #
    # **这东西最容易被跳过,但省事时不装、出问题时最难查。**
    # schema_version 拦住版本不匹配(旧代码导新包会报一堆说不清的错);
    # 每卷的 sha256 拦住下载不完整(半个 CSV 导进去的表现是
    # "某只票缺三个月",在回测里看不出来)。
    manifest = {
        "package": "hunter-data",
        "schema_version": SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "built_by": _git_rev(),
        "scope": scope,
        "stocks": len(codes) if codes else _distinct_codes(),
        "years": [y_from, y_to],
        "volumes": volumes,
    }
    (work / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 打成 tar.gz ─────────────────────────────────────
    #
    # 卷本身已经是 .gz,外层 tar 不再压缩(w: 而不是 w:gz)——
    # 二次压缩几乎没有收益,只是白白多花几分钟 CPU。
    pkg = out_dir / f"hunter-data-{scope}-{stamp}.tar"
    with tarfile.open(pkg, "w") as tar:
        for p in sorted(work.iterdir()):
            tar.add(p, arcname=p.name)
    shutil.rmtree(work, ignore_errors=True)

    total_mb = pkg.stat().st_size / 1048576
    print(f"\n[pack] 完成 · {pkg}")
    print(f"[pack] {len(volumes)} 卷 · {total_mb:.1f} MB · 耗时 {time.time()-t0:.0f}s")
    return pkg


def _git_rev() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5, cwd="/app")
        return f"hunter-community@{(r.stdout or '').strip() or 'unknown'}"
    except Exception:                                         # noqa: BLE001
        return "hunter-community@unknown"


def _distinct_codes() -> int:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT count(DISTINCT code) FROM klines WHERE period='daily'")
        return (cur.fetchone() or [0])[0]
    finally:
        cur.close(); conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="all_a",
                    help="包的名字 · hs300 / zz500 / all_a / 自定义")
    ap.add_argument("--years", default="",
                    help="年份区间 2016-2026 · 不填则按库里实际范围")
    ap.add_argument("--out", default="/opt/hunter-data/packages")
    ap.add_argument("--codes-from-scope", action="store_true",
                    help="按 --scope 解析出股票范围(hs300/zz500/all_a),"
                         "否则打包库里全部")
    args = ap.parse_args()

    if args.years:
        y_from, y_to = (int(x) for x in args.years.split("-"))
    else:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT min(ts), max(ts) FROM klines WHERE period='daily'")
        lo, hi = cur.fetchone() or (None, None)
        cur.close(); conn.close()
        if not lo:
            print("[pack] 库里没有 K 线数据"); return 1
        y_from, y_to = lo.year, hi.year

    codes = None
    if args.codes_from_scope and args.scope != "all_a":
        from app.services.quant import data_center as dc
        codes, note = dc.resolve_scope({"kind": args.scope})
        if not codes:
            print(f"[pack] 范围解析不出股票: {note}"); return 1

    build(args.scope, y_from, y_to, Path(args.out), codes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
