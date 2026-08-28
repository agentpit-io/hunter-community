"""数据包导入 —— 用户把从云盘下载的包丢进目录,这里读它、校验、导库。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      23_20260824_数据包分发方案.md §5.6

## 用户的操作只有两步

    ① 把下载好的 .tar 拖进 hunter-community/data-packages/
    ② 在「数据」页点一下

"这是什么内容"靠包里的 `manifest.json` 判断,**不靠猜文件名** ——
用户放错包、下了一半、拿了个旧版本,都要能在导入前发现。

## 为什么不做浏览器上传

浏览器出于安全限制只给文件名、拿不到完整路径,所以"选文件"要么
上传整个文件(几百 MB~几 GB 走 HTTP,要分片才稳),要么后端读路径
(但容器看不到宿主机的下载目录)。用挂载目录两个问题一起绕开了。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import tarfile
import time
from datetime import date
from pathlib import Path

from app.services.database import get_conn
from app.services.quant import package_spec as spec

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(os.getenv("HUNTER_PACKAGE_DIR", "/opt/hunter-packages"))


# ═══════════════════════════════════════════════════════════
# 发现与识别
# ═══════════════════════════════════════════════════════════

def _read_manifest(tar_path: Path) -> dict | None:
    """只读 manifest,不解压整个包。"""
    try:
        with tarfile.open(tar_path, "r") as tar:
            m = tar.extractfile("manifest.json")
            if not m:
                return None
            return json.loads(m.read().decode("utf-8"))
    except Exception as e:                                    # noqa: BLE001
        log.warning("[pkg] 读 %s 的 manifest 失败: %s", tar_path.name, e)
        return None


def list_packages() -> list[dict]:
    """列出目录里能识别的数据包。

    识别不了的也要列出来并说明为什么 —— 用户把包放进去却看不到它,
    比看到一条"这个文件不是数据包"更让人困惑。
    """
    if not PACKAGE_DIR.exists():
        return []
    out = []
    for p in sorted(PACKAGE_DIR.glob("*.tar")):
        st = p.stat()
        man = _read_manifest(p)
        if not man:
            out.append({"file": p.name, "bytes": st.st_size, "ok": False,
                        "why": "读不到 manifest.json —— 可能不是数据包,或者下载不完整"})
            continue
        vols = man.get("volumes") or []
        out.append({
            "file": p.name,
            "bytes": st.st_size,
            "ok": True,
            "scope": man.get("scope"),
            "stocks": man.get("stocks"),
            "years": man.get("years"),
            "built_at": man.get("built_at"),
            "built_by": man.get("built_by"),
            "schema_version": man.get("schema_version"),
            "volumes": len(vols),
            "rows": sum(v.get("rows", 0) for v in vols),
            # 版本比我们新 → 让用户先更新代码,而不是导到一半报
            # "某某表不存在"
            "schema_ok": (man.get("schema_version") or "") <= spec.SCHEMA_VERSION,
        })
    return out


def inspect(file: str) -> dict:
    """导入前的预检 —— 页面上那张确认卡靠它。"""
    p = PACKAGE_DIR / file
    if not p.exists():
        return {"error": "not_found", "message": f"{file} 不在 {PACKAGE_DIR}"}
    man = _read_manifest(p)
    if not man:
        return {"error": "bad_package",
                "message": "读不到 manifest.json —— 可能不是数据包,或者下载不完整"}

    pkg_ver = man.get("schema_version") or ""
    if pkg_ver > spec.SCHEMA_VERSION:
        return {"error": "schema_too_new",
                "message": (f"这个包需要 {pkg_ver} 或更新的版本,"
                            f"你当前是 {spec.SCHEMA_VERSION}。"
                            f"请先 git pull && docker compose build 再导入")}

    unknown = [v["file"] for v in (man.get("volumes") or [])
               if spec.vol_of(v["file"]) is None]

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT count(DISTINCT code) FROM data_coverage WHERE data_type='kline'")
        have = (cur.fetchone() or [0])[0]
    finally:
        cur.close(); conn.close()

    vols = man.get("volumes") or []
    return {
        "file": file,
        "scope": man.get("scope"),
        "stocks": man.get("stocks"),
        "years": man.get("years"),
        "built_at": man.get("built_at"),
        "built_by": man.get("built_by"),
        "volumes": vols,
        "total_rows": sum(v.get("rows", 0) for v in vols),
        "bytes": p.stat().st_size,
        "you_have": have,
        # 认不出的卷要说出来,不能默默跳过
        "unknown_volumes": unknown,
        "note": ("导入是合并,不会覆盖你已有的数据"
                 if have else "你还没有任何数据,导入后就能选股回测"),
    }


# ═══════════════════════════════════════════════════════════
# 导入
# ═══════════════════════════════════════════════════════════

def _sha256_stream(f, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    while True:
        b = f.read(chunk)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def _import_volume(tar: tarfile.TarFile, vol_meta: dict) -> tuple[int, str]:
    """导一卷。返回 (行数, 错误说明)。"""
    name = vol_meta["file"]
    v = spec.vol_of(name)
    if v is None:
        return 0, f"不认识的卷 {name}"

    # ── 校验必须在解压之前 ──────────────────────────────
    #
    # gzip 被截断时**不一定报错** —— 有些实现正常解出前面完整的部分、
    # 只在最后报 EOF,而调用方忽略了退出码就得到一个"看起来正常但
    # 少了尾巴"的 CSV。先比哈希绕开这一整类问题。
    want = vol_meta.get("sha256")
    if want:
        m = tar.extractfile(name)
        if m is None:
            return 0, f"包里找不到 {name}"
        got = _sha256_stream(m)
        if got != want:
            return 0, f"{name} 校验失败(文件损坏或下载不完整)"

    m = tar.extractfile(name)
    if m is None:
        return 0, f"包里找不到 {name}"

    conn = get_conn(); cur = conn.cursor()
    try:
        # 临时表没有索引和唯一约束,COPY 能跑满速;
        # 再用一条集合操作合并进正表,比逐行 upsert 快一个数量级。
        #
        # ⚠ **不能用 ON COMMIT DROP** —— 下面还要 commit 好几次
        # (合并一次、写 coverage 一次),第一次 commit 就把表删了,
        # 后面全部 UndefinedTable。改成显式 DROP,并且每卷用独立的表名
        # 避免同一连接里前一卷的残留。
        tmp = "_imp"
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        cur.execute(f"CREATE TEMP TABLE {tmp} (LIKE {v.table} INCLUDING DEFAULTS)")
        with gzip.open(m, "rb") as gz:
            cur.copy_expert(
                f"COPY {tmp} ({v.col_sql}) FROM STDIN WITH CSV", gz)
        staged = cur.rowcount

        # stocks 的主键含 user_id,而包里不带 —— 统一填 '' (全局股票),
        # 不会碰用户自己加的自选(那些 user_id 是他的 uuid)
        if v.table == "stocks":
            cur.execute(f"UPDATE {tmp} SET user_id = '' WHERE user_id IS NULL")
            cur.execute(f"UPDATE {tmp} SET enabled = TRUE WHERE enabled IS NULL")

        cur.execute(
            f"INSERT INTO {v.table} ({v.col_sql}"
            + (", user_id, enabled" if v.table == "stocks" else "")
            + f") SELECT {v.col_sql}"
            + (", '', TRUE" if v.table == "stocks" else "")
            + f" FROM {tmp}"
            f" ON CONFLICT ({v.conflict_sql}) DO UPDATE SET {v.update_sql}")
        conn.commit()

        # K 线卷导完立刻写 coverage —— **按这一卷实际覆盖的区间**,
        # 不是拷我们的记录。拷的话用户的库会声称覆盖了某些区间,
        # 而实际取决于他导了哪几卷
        if v.table == "klines":
            cur.execute("""
                INSERT INTO data_coverage (code, data_type, covered_from, covered_to)
                SELECT code, 'kline', min(ts), max(ts) FROM "_imp" GROUP BY code
                ON CONFLICT (code, data_type) DO UPDATE
                  SET covered_from = LEAST(data_coverage.covered_from, EXCLUDED.covered_from),
                      covered_to   = GREATEST(data_coverage.covered_to, EXCLUDED.covered_to),
                      updated_at   = now()""")
            conn.commit()
        elif v.table == "financial_metric":
            cur.execute("""
                INSERT INTO data_coverage (code, data_type, covered_from, covered_to)
                SELECT code, 'financial', min(report_date), max(report_date)
                  FROM "_imp" GROUP BY code
                ON CONFLICT (code, data_type) DO UPDATE
                  SET covered_from = LEAST(data_coverage.covered_from, EXCLUDED.covered_from),
                      covered_to   = GREATEST(data_coverage.covered_to, EXCLUDED.covered_to),
                      updated_at   = now()""")
            conn.commit()

        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.commit()
        return staged, ""
    except Exception as e:                                    # noqa: BLE001
        conn.rollback()
        return 0, f"{name} 导入失败: {type(e).__name__} {str(e)[:120]}"
    finally:
        cur.close(); conn.close()


def run(job_id: int, file: str) -> dict:
    """跑一个导入任务。**在线程里调**。

    复用 `data_job` 的进度/暂停/取消 —— 对用户来说和「下载数据」
    是同一个体验,只是数据来源从上游换成了本地的包。
    """
    from app.services.quant import data_job as dj
    try:
        return _run(job_id, file)
    except Exception as e:                                    # noqa: BLE001
        # **线程里的异常必须落到任务状态上。**
        #
        # 实测踩过:往 current_code(VARCHAR(10))塞文件名触发
        # StringDataRightTruncation,异常在后台线程里被 asyncio 吞掉,
        # 表现是任务永远停在 queued、页面上一动不动 ——
        # 不去翻容器日志根本不知道发生了什么。
        log.exception("[pkg] 导入 %s 崩了", file)
        dj.set_status(job_id, "failed",
                      f"导入出错:{type(e).__name__} {str(e)[:160]}")
        return {"error": type(e).__name__, "message": str(e)[:200]}


def _run(job_id: int, file: str) -> dict:
    from app.services.quant import data_job as dj

    p = PACKAGE_DIR / file
    if not p.exists():
        dj.set_status(job_id, "failed", f"{file} 不见了")
        return {"error": "not_found"}

    man = _read_manifest(p)
    if not man:
        dj.set_status(job_id, "failed", "读不到 manifest.json")
        return {"error": "bad_package"}

    vols = man.get("volumes") or []
    # 状态要转成 running —— 不转的话任务实际在跑、页面上却一直显示
    # "排队中",用户以为卡住了。下载任务那边是在 run() 开头转的,
    # 导入这边一开始漏了
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE data_job SET status='running',
                              started_at=coalesce(started_at, now()), updated_at=now()
                        WHERE id=%s""", (job_id,))
        conn.commit()
    finally:
        cur.close(); conn.close()
    dj._progress(job_id, phase="导入中", done=0, failed=0)
    t0 = time.time()
    done = failed = 0
    rows_total = 0
    errors: list[str] = []

    with tarfile.open(p, "r") as tar:
        for i, vm in enumerate(vols, 1):
            st = dj._read_status(job_id)
            if st == "paused":
                dj._progress(job_id, phase="已暂停")
                return {"paused": True, "done": done}
            if st in ("canceled", "failed"):
                return {"canceled": True, "done": done}

            # ⚠ current_code 是 VARCHAR(10)(给股票代码用的),塞文件名会
            # StringDataRightTruncation —— 而这个异常发生在后台线程里,
            # 表现是任务永远停在 queued、页面上一动不动。
            # 卷名放 phase(TEXT),current_code 留空。
            dj._progress(job_id, done=done, failed=failed,
                         phase=f"导入 {i}/{len(vols)} · {vm['file']}")
            n, err = _import_volume(tar, vm)
            if err:
                failed += 1
                errors.append(err)
                log.warning("[pkg] %s", err)
            else:
                done += 1
                rows_total += n

    if done == 0:
        dj.set_status(job_id, "failed",
                      "一卷都没导进去 · " + (errors[0] if errors else ""))
        return {"error": "all_failed", "errors": errors}

    # 包里没有因子(它比原料还大,而且是纯本地计算)——
    # 不算的话用户导完立刻点回测会得到"选不出股票",和没导一样
    dj._progress(job_id, phase="计算因子")
    factors = {}
    try:
        factors = _recompute_factors(man)
    except Exception as e:                                    # noqa: BLE001
        log.error("[pkg] 算因子失败: %s", e)

    # 用户看得懂的话。原来写的是"导入 6 卷 · 191489 行" ——
    # **"卷"是我们的内部概念**,用户不知道一卷是什么;而 191489
    # 和 191,489 在一眼扫过去时是两个量级
    _stocks = man.get("stocks") or 0
    _years = man.get("years") or []
    msg = (f"导入成功 · 已加载 {rows_total:,} 行数据"
           + (f" · {_stocks} 只股票" if _stocks else "")
           + (f" · {_years[0]}–{_years[1]}" if len(_years) == 2 else "")
           + f" · 耗时 {int(time.time()-t0)}s"
           + (f" · ⚠ {failed} 个分卷失败" if failed else ""))
    dj.set_status(job_id, "done", msg)
    log.info("[pkg] %s · 因子 %s", msg, factors)
    return {"done": done, "failed": failed, "rows": rows_total,
            "errors": errors, "factors": factors}


def _recompute_factors(man: dict) -> dict:
    """按包覆盖的年份重算因子。"""
    from datetime import date as _date
    from app.services.quant import data_job as dj, universe as uv

    years = man.get("years") or []
    if len(years) == 2:
        start = _date(int(years[0]), 1, 1)
        end = min(_date(int(years[1]), 12, 31), _date.today())
    else:
        end = _date.today()
        start = _date(end.year - 3, 1, 1)

    codes = uv.covered_codes()
    if not codes:
        return {}
    # 包里带了财报就把基本面因子也算上
    has_fin = any(v.get("kind") == "financial" for v in (man.get("volumes") or []))
    return dj._compute_factors(codes, start, end, has_fin)
