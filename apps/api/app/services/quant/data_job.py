"""数据中心 · 下载任务。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      22_20260822_数据中心_技术方案.md §4

## 为什么状态放库不放内存

之前的初始化进度是模块级 dict,实测踩过两次:

  · `docker exec` 另起一个进程跑,页面上的 /init-status 永远是空的
  · 容器重建后进度全丢,而用户还在页面上等着

所以 `data_job` 一行就是一个任务,进度、暂停、续跑全靠它。
worker 每处理几只就写一次库,并且**每只之前回头读一次自己的 status** ——
这是暂停/取消能生效的唯一机制(worker 在另一个线程,没法被直接打断)。

## 同时只允许一个任务

多个任务并发打同一个上游会互相拖慢并触发限流(实测:800 只不限速
连着打,腾讯清一色 ReadTimeout)。所以建任务前先查有没有在跑的。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta

from app.services.database import get_conn

log = logging.getLogger(__name__)

# 对上游客气一点 —— 这个 sleep 不能省。回填脚本有它所以 800/800 全成功,
# 而流水线里漏掉过一次,结果 800 只清一色 ReadTimeout,
# 且"拿不到就 continue"导致最后 log 一句"更新 0/800"当作正常完成
_SLEEP_SEC = 0.15
# 每处理这么多只写一次库 —— 每只都写的话 800 只就是 800 次 UPDATE。
#
# 但**不能只按只数**:一个 6 只的任务按 10 只一刷,进度会全程停在 0
# 然后直接跳到 6(实测),用户以为卡住了。所以再加一个时间兜底,
# 超过 2 秒没写过就写一次 —— 小任务靠时间,大任务靠只数。
_FLUSH_EVERY = 10
_FLUSH_SEC = 2.0

# ── 限流退避(见 doc/.../24_20260827_全量下载限流问题与退避方案.md)──
# 连续失败这么多只就判定为被限流
_MISS_TRIGGER = 5
# 退避梯度:1 分钟 → 5 分钟 → 15 分钟。掐是暂时的,等就能恢复
_BACKOFF = (60, 300, 900)
# 每这么多只主动歇一会 —— 预防比补救便宜
_REST_EVERY = 500
_REST_SEC = 120
# 整轮跑完之后,等这么久再重跑失败的那批
_RETRY_WAIT = 600
# 孤立失败累计这么多次 = 判定这只票拿不到(退市/停牌/源里没有)
_FAIL_PERMANENT = 3

ACTIVE = ("queued", "running")


# ═══════════════════════════════════════════════════════════
# 增删查
# ═══════════════════════════════════════════════════════════

def active_job() -> dict | None:
    """正在排队或跑着的任务 —— 建新任务前要先查这个。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id, status, phase, total, done_count
                         FROM data_job WHERE status = ANY(%s)
                        ORDER BY id DESC LIMIT 1""", (list(ACTIVE),))
        r = cur.fetchone()
        if not r:
            return None
        return {"id": r[0], "status": r[1], "phase": r[2],
                "total": r[3], "done_count": r[4]}
    finally:
        cur.close(); conn.close()


def create(scope: dict, span_months: int, with_financial: bool,
           keep_raw: bool, total: int, user_id: str | None) -> int:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO data_job (user_id, scope, span_months, with_financial,
                                     keep_raw, status, total, phase)
               VALUES (%s, %s::jsonb, %s, %s, %s, 'queued', %s, '排队中')
               RETURNING id""",
            (user_id, json.dumps(scope, ensure_ascii=False), span_months,
             with_financial, keep_raw, total))
        jid = cur.fetchone()[0]
        conn.commit()
        return jid
    finally:
        cur.close(); conn.close()


def get(job_id: int) -> dict | None:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id, scope, span_months, with_financial, keep_raw,
                              status, total, done_count, skipped_count, failed_count,
                              current_code, phase, message,
                              created_at, started_at, finished_at
                         FROM data_job WHERE id=%s""", (job_id,))
        r = cur.fetchone()
        if not r:
            return None
        d = {
            "id": r[0], "scope": r[1], "span_months": r[2],
            "with_financial": r[3], "keep_raw": r[4], "status": r[5],
            "total": r[6], "done_count": r[7], "skipped_count": r[8],
            "failed_count": r[9], "current_code": r[10], "phase": r[11],
            "message": r[12],
            "created_at": r[13].isoformat() if r[13] else None,
            "started_at": r[14].isoformat() if r[14] else None,
            "finished_at": r[15].isoformat() if r[15] else None,
        }
        # 剩余时间 —— 用**实际已用时间**推,不用固定速率。
        # 用固定速率的话遇到限流会一直显示"还要 5 分钟"而实际越来越慢
        if d["started_at"] and d["status"] == "running" and d["done_count"] > 0:
            el = (r[14].timestamp() and (time.time() - r[14].timestamp())) or 0
            per = el / max(1, d["done_count"] + d["skipped_count"])
            left = max(0, d["total"] - d["done_count"] - d["skipped_count"])
            d["elapsed_sec"] = int(el)
            d["eta_sec"] = int(per * left)
        return d
    finally:
        cur.close(); conn.close()


def recent(limit: int = 20) -> list[dict]:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT id, scope, span_months, with_financial, status,
                              total, done_count, skipped_count, failed_count,
                              created_at, finished_at
                         FROM data_job ORDER BY id DESC LIMIT %s""", (limit,))
        return [{
            "id": r[0], "scope": r[1], "span_months": r[2],
            "with_financial": r[3], "status": r[4], "total": r[5],
            "done_count": r[6], "skipped_count": r[7], "failed_count": r[8],
            "created_at": r[9].isoformat() if r[9] else None,
            "finished_at": r[10].isoformat() if r[10] else None,
        } for r in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def set_status(job_id: int, status: str, message: str = "") -> bool:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE data_job
                          SET status=%s, message=%s, updated_at=now(),
                              finished_at = CASE WHEN %s IN ('done','failed','canceled')
                                                 THEN now() ELSE finished_at END
                        WHERE id=%s""", (status, message, status, job_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        cur.close(); conn.close()


def _read_status(job_id: int) -> str:
    """worker 每只之前回头读一次 —— 暂停/取消靠这个生效。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM data_job WHERE id=%s", (job_id,))
        r = cur.fetchone()
        return r[0] if r else "canceled"
    finally:
        cur.close(); conn.close()


def _progress(job_id: int, *, done=None, skipped=None, failed=None,
              code=None, phase=None) -> None:
    sets, vals = ["updated_at=now()"], []
    for col, v in (("done_count", done), ("skipped_count", skipped),
                   ("failed_count", failed)):
        if v is not None:
            sets.append(f"{col}=%s"); vals.append(v)
    if code is not None:
        sets.append("current_code=%s"); vals.append(code)
    if phase is not None:
        sets.append("phase=%s"); vals.append(phase)
    vals.append(job_id)
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"UPDATE data_job SET {', '.join(sets)} WHERE id=%s", vals)
        conn.commit()
    finally:
        cur.close(); conn.close()


def reclaim_orphans() -> int:
    """启动时把上次崩掉留下的 running 任务标成 paused。

    worker 是进程内的线程,容器一重启它就没了,而库里的状态还是 running ——
    页面上会显示"进行中"然后永远不动。标成 paused,用户点一下就能续。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE data_job SET status='paused',
                              message='服务重启导致中断 · 点续跑接着下',
                              updated_at=now()
                        WHERE status = ANY(%s)""", (list(ACTIVE),))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        cur.close(); conn.close()


# ═══════════════════════════════════════════════════════════
# worker
# ═══════════════════════════════════════════════════════════

def _sleep_interruptible(job_id: int, seconds: float, step: float = 5.0) -> bool:
    """睡觉时也要能被暂停打断。

    直接 time.sleep(900) 的话,用户点暂停要等 15 分钟才生效 ——
    界面上看就是"点了没反应"。
    返回 False 表示用户暂停/取消了。
    """
    waited = 0.0
    while waited < seconds:
        st = _read_status(job_id)
        if st in ("paused", "canceled", "failed"):
            return False
        time.sleep(min(step, seconds - waited))
        waited += step
    return True


def _upsert_coverage(code: str, data_type: str, lo: date, hi: date) -> None:
    """记下这只票下到哪儿了。**区间取并集** —— 用户先下 1 年再下 3 年,
    覆盖应该变成 3 年那个更大的区间,而不是被后一次覆写成一样大。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO data_coverage (code, data_type, covered_from, covered_to)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (code, data_type) DO UPDATE
                         SET covered_from = LEAST(data_coverage.covered_from, EXCLUDED.covered_from),
                             covered_to   = GREATEST(data_coverage.covered_to, EXCLUDED.covered_to),
                             updated_at   = now()""",
                    (code, data_type, lo, hi))
        conn.commit()
    finally:
        cur.close(); conn.close()


def _note_failure(code: str, data_type: str, isolated: bool) -> None:
    """记一次失败。

    **只有"孤立失败"才计数。** 限流的时候是成片失败,那不是这只票的问题;
    照单全记的话一次限流就把几千只票打成"永久拿不到",下次全跳过 ——
    表现是"数据越下越少",而且找不出原因。

    isolated=True 的判据在调用方:此刻 miss_streak == 0,
    也就是**前一只是成功的**,说明通道是通的,失败是这只票自己的事。
    """
    if not isolated:
        return
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO data_failure (code, data_type, fail_count, last_tried)
                       VALUES (%s,%s,1,now())
                       ON CONFLICT (code, data_type) DO UPDATE
                         SET fail_count = data_failure.fail_count + 1,
                             last_tried = now()""", (code, data_type))
        conn.commit()
    finally:
        cur.close(); conn.close()


def _clear_failure(code: str, data_type: str) -> None:
    """拿到了就把黑名单记录删掉 —— 新股上市后就有数据了,不能一直挂着"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM data_failure WHERE code=%s AND data_type=%s",
                    (code, data_type))
        conn.commit()
    finally:
        cur.close(); conn.close()


def _blacklist(data_type: str = "kline") -> set[str]:
    """连续孤立失败 >= _FAIL_PERMANENT 次、且最近 30 天内试过的,这轮跳过。

    留 30 天的口子:退市/停牌会复牌,新股会上市,不能一次判死。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""SELECT code FROM data_failure
                        WHERE data_type=%s AND fail_count >= %s
                          AND last_tried > now() - interval '30 days'""",
                    (data_type, _FAIL_PERMANENT))
        return {r[0] for r in cur.fetchall()}
    except Exception:                                         # noqa: BLE001
        return set()          # 表还没建 —— 不该因此下不了数据
    finally:
        cur.close(); conn.close()


def _save_klines(code: str, rows: list[dict]) -> int:
    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        for r in rows:
            cur.execute(
                """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                   VALUES (%s,'daily',%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (code, period, ts) DO UPDATE
                     SET open=EXCLUDED.open, high=EXCLUDED.high,
                         low=EXCLUDED.low, close=EXCLUDED.close,
                         volume=EXCLUDED.volume""",
                (code, r["ts"], r["open"], r["high"], r["low"],
                 r["close"], int(r["volume"] or 0)))
            n += cur.rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close(); conn.close()


def run(job_id: int) -> dict:
    """跑一个任务。**在线程里调**,别在事件循环里 —— 这一趟可能几小时。"""
    from app.services.quant import data_center, local_kline

    job = get(job_id)
    if not job:
        return {"error": "job_not_found"}
    if job["status"] not in ACTIVE:
        return {"skipped": job["status"]}

    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("""UPDATE data_job SET status='running', started_at=coalesce(started_at, now()),
                              phase='解析范围', updated_at=now() WHERE id=%s""", (job_id,))
        conn.commit()
    finally:
        cur.close(); conn.close()

    codes, note = data_center.resolve_scope(job["scope"], job.get("user_id"))
    if not codes:
        set_status(job_id, "failed", note or "范围解析不出股票")
        return {"error": "empty_scope", "note": note}

    end = date.today()
    latest_only = (job["span_months"] or 0) <= 0
    start = end - timedelta(days=7 if latest_only else job["span_months"] * 31)

    already = data_center._covered(codes, "kline", start, end)

    # ── 三个计数器必须共用一个基准:**本轮**。─────────────────
    #
    # 曾经为了"续跑别把计数清零"而把上一轮的 done/failed 接着往上加,
    # 结果同一只票被记两遍:上一轮下成功的票现在带着 coverage,
    # 这一轮又被算进 already(跳过)。测试人员实测
    #     成功 3487 + 跳过 3321 + 失败 2212 = 9020 > 总数 5534
    # 连带三个症状,全是这一个原因:进度条满格却还在跑、
    # "预计还要 0 秒"、三个数加起来超过总数。
    #
    # 现在改成:
    #     跳过 = 本轮开始时就已经有的(**含上一轮下好的**)
    #     成功 = 本轮新下下来的
    #     失败 = 本轮试了没拿到的
    # 三者互斥,合计必 <= 总数。
    #
    # 续跑后"成功"确实会从 15 变回 0 —— 但那 15 只不是消失了,是挪进了
    # "跳过"。**进度条不会倒退**,这才是用户真正在看的东西。
    # 而失败数也不再跨轮累加:上一轮失败的票这一轮重试成功了就该消失,
    # 累加的话这个数只涨不跌,用户看到"失败一直在增加",
    # 实际上很多早就补回来了。它要回答的是"现在还有多少没拿到"。
    done = 0
    failed = 0
    skipped = len(already)
    _progress(job_id, phase="下载日线", done=0, skipped=skipped, failed=0)
    unsupported = 0
    miss_streak = 0          # 连续失败计数 · 触发退避
    backoff_i = 0            # 退避档位
    dead = 0                 # 黑名单跳过数
    blacklist = _blacklist("kline")
    retry_queue: list[str] = []
    local_kline.set_retry(True)
    t0 = last_flush = time.time()

    for i, code in enumerate(codes, 1):
        # 每只之前回头看一眼自己的状态 —— 暂停/取消唯一的生效点
        if i % 5 == 1:
            st = _read_status(job_id)
            if st == "paused":
                _progress(job_id, done=done, skipped=skipped, failed=failed,
                          phase="已暂停")
                log.info("[data_job %s] 用户暂停 · 已下 %d 只", job_id, done)
                return {"paused": True, "done": done}
            if st in ("canceled", "failed"):
                log.info("[data_job %s] 已取消", job_id)
                return {"canceled": True, "done": done}

        if code in already:
            continue

        # 北交所等拿不到历史日线的,**算跳过不算失败**。
        #
        # 算失败的话:全 A 股每次都报 331 次失败,用户会以为系统坏了;
        # 而且失败的票不写 coverage → 下次续跑又重试一遍,永远重试。
        # 记进 coverage 之后就真的跳过了。
        if local_kline.is_unsupported(code):
            unsupported += 1
            skipped += 1
            continue

        # 反复孤立失败的(退市/停牌/源里就是没有),这轮跳过。
        # 每轮都试一遍的话,白白消耗配额、还把限流提前触发
        if code in blacklist:
            dead += 1
            skipped += 1
            continue

        try:
            rows = local_kline.fetch_daily(code, start, end)
        except Exception as e:                                # noqa: BLE001
            log.warning("[data_job %s] %s 取数异常: %s", job_id, code, type(e).__name__)
            rows = []

        if not rows:
            # **拿不到就是拿不到**,不写空行不补零。回测靠"这只票没有价格"
            # 跳过它,补一行假价格会让收益凭空出现
            failed += 1
            retry_queue.append(code)
            # miss_streak 还没自增 —— 此刻为 0 就意味着前一只是成功的,
            # 通道是通的,这次失败是这只票自己的问题
            _note_failure(code, "kline", isolated=(miss_streak == 0))
            miss_streak += 1

            # ── 限流退避 ──────────────────────────────────────
            #
            # 实测腾讯的限流是**开关式**的:要么全成要么全败,中间没有过渡,
            # 而且掐一段时间会自动恢复。服务器那次每 50 只采样:
            #
            #    100 只  50/50 全成
            #    700 只   0/50 全败   ← 被掐
            #   1900 只  50/50 全成   ← 自己恢复了
            #   3700 只   0/50 全败   ← 又被掐
            #
            # 所以「统一调慢」是错的解法:被掐时慢也没用(全败),
            # 没被掐时慢纯属浪费。正确做法是**检测到就重退避**。
            #
            # 硬打的代价实测过:那次在全败状态下跑了两千多只,
            # 每只还重试 3 次 —— 请求量放大 3 倍,持续刺激上游。
            if miss_streak >= _MISS_TRIGGER:
                local_kline.set_retry(False)     # 被限时重试只会更糟
                wait = _BACKOFF[min(backoff_i, len(_BACKOFF) - 1)]
                backoff_i += 1
                _progress(job_id, done=done, skipped=skipped, failed=failed,
                          phase=f"上游限流 · 等 {wait//60} 分钟后继续")
                log.warning("[data_job %s] 连续失败 %d 只 · 退避 %d 秒",
                            job_id, miss_streak, wait)
                if not _sleep_interruptible(job_id, wait):
                    _progress(job_id, done=done, skipped=skipped, failed=failed,
                              phase="已暂停")
                    return {"paused": True, "done": done}
                miss_streak = 0
        else:
            try:
                _save_klines(code, rows)
                # 左端记**请求的 start**,不是拿到的第一天。
                #
                # 记"拿到的"会让"已覆盖"永远判不成立:我们要 2025-08-17 起,
                # 而第一个交易日是 08-18(周末),于是 covered_from > want_from
                # → 判定没覆盖 → 下次续跑又重下一遍。次新股更极端,
                # 它的历史就是从上市那天开始,永远追不上请求的起点。
                #
                # coverage 的语义是"这段区间我们请求过了、拿到了全部可得的",
                # 所以左端用 start 才对。右端用实际最后一天 —— 那才是新鲜度。
                _upsert_coverage(code, "kline", start,
                                 date.fromisoformat(rows[-1]["ts"]))
                done += 1
                _clear_failure(code, "kline")
                # 成功就立刻恢复全速 —— 掐是暂时的,恢复了不该继续慢
                if miss_streak or backoff_i:
                    miss_streak = 0
                    backoff_i = 0
                    local_kline.set_retry(True)
            except Exception as e:                            # noqa: BLE001
                log.warning("[data_job %s] %s 入库失败: %s", job_id, code, e)
                failed += 1

        # 每 _REST_EVERY 只主动歇一会,不等被掐了才停
        if i % _REST_EVERY == 0 and i < len(codes):
            _progress(job_id, done=done, skipped=skipped, failed=failed,
                      phase=f"已下 {i} 只 · 歇 {_REST_SEC//60} 分钟(避免被限流)")
            log.info("[data_job %s] 第 %d 只 · 主动休息 %d 秒", job_id, i, _REST_SEC)
            if not _sleep_interruptible(job_id, _REST_SEC):
                _progress(job_id, done=done, skipped=skipped, failed=failed,
                          phase="已暂停")
                return {"paused": True, "done": done}

        if ((done + failed) % _FLUSH_EVERY == 0
                or time.time() - last_flush >= _FLUSH_SEC):
            _progress(job_id, done=done, skipped=skipped, failed=failed, code=code)
            last_flush = time.time()
        time.sleep(_SLEEP_SEC)

    # ── 失败的票攒到最后重跑一遍 ────────────────────────────
    #
    # **不当场重试** —— 当场重试是在被限的时候重试,必然还是失败,
    # 而且把请求量放大。等整轮跑完、歇一会儿,按实测大概率已经恢复了
    # (服务器那次全败之后自己恢复过两次)。
    if retry_queue and _read_status(job_id) == "running":
        n = len(retry_queue)
        _progress(job_id, done=done, skipped=skipped, failed=failed,
                  phase=f"{n} 只失败 · 歇 {_RETRY_WAIT//60} 分钟后重跑")
        log.info("[data_job %s] 第一轮失败 %d 只 · 等 %d 秒重跑",
                 job_id, n, _RETRY_WAIT)
        if _sleep_interruptible(job_id, _RETRY_WAIT):
            local_kline.set_retry(True)
            recovered = 0
            for j, code in enumerate(retry_queue, 1):
                if j % 5 == 1 and _read_status(job_id) != "running":
                    break
                try:
                    rows = local_kline.fetch_daily(code, start, end)
                except Exception:                             # noqa: BLE001
                    rows = []
                if rows:
                    try:
                        _save_klines(code, rows)
                        _upsert_coverage(code, "kline", start,
                                         date.fromisoformat(rows[-1]["ts"]))
                        recovered += 1
                        done += 1
                        failed -= 1
                        _clear_failure(code, "kline")
                    except Exception:                         # noqa: BLE001
                        pass
                if j % 20 == 0:
                    _progress(job_id, done=done, skipped=skipped, failed=failed,
                              phase=f"重跑失败项 {j}/{n} · 已救回 {recovered}")
                time.sleep(_SLEEP_SEC)
            log.info("[data_job %s] 重跑救回 %d/%d 只", job_id, recovered, n)

    # ── 财报 · 用户勾了才下 ──────────────────────────────────
    #
    # 单独一段而不是和日线混在一个循环里:两者速率差 5 倍
    # (1.81 vs 8.6 秒/只),混在一起进度条会忽快忽慢,而且失败原因
    # 完全不同(腾讯限流 vs AKShare 财务接口限流),混着看很难判断
    fin_done = fin_failed = 0
    if job.get("with_financial"):
        from app.services.quant import financial_store as fs
        _progress(job_id, phase="下载财报")
        # 勾了归档就要求归档也在,否则"补归档"这个操作会被整只跳过
        fin_already = fs.has_metrics(codes, need_raw=bool(job.get("keep_raw")))
        for i, code in enumerate(codes, 1):
            if i % 5 == 1:
                st = _read_status(job_id)
                if st == "paused":
                    _progress(job_id, phase="已暂停(财报阶段)")
                    return {"paused": True, "done": done, "fin_done": fin_done}
                if st in ("canceled", "failed"):
                    return {"canceled": True, "done": done, "fin_done": fin_done}
            if code in fin_already:
                continue
            r = fs.download_one(code, job.get("keep_raw") or False)
            if r.get("ok"):
                fin_done += 1
                _upsert_coverage(code, "financial", start, end)
            else:
                fin_failed += 1

            # 熔断:上游持续失败时不该继续磨。
            #
            # 财报一只永久失败要 ~80 秒(内部按年逐个请求 + 一次重试)。
            # 5400 只的任务如果上游在限流,不熔断就是磨几小时然后交出
            # 一堆失败 —— 而用户全程看着进度条以为在正常下载。
            # 前 20 只里超过 70% 失败,基本可以判定不是个别股票的问题。
            tried = fin_done + fin_failed
            if tried >= 20 and fin_failed / tried > 0.7:
                set_status(job_id, "paused",
                           f"上游财报接口大量失败({fin_failed}/{tried})· "
                           f"已暂停 · 过一会点续跑,已下好的不会重来")
                log.error("[data_job %s] 财报失败率 %.0f%% · 熔断暂停",
                          job_id, 100 * fin_failed / tried)
                return {"paused": True, "reason": "financial_upstream_failing",
                        "fin_done": fin_done, "fin_failed": fin_failed}
            if time.time() - last_flush >= _FLUSH_SEC:
                _progress(job_id, code=code,
                          phase=f"下载财报 {fin_done}/{len(codes)}")
                last_flush = time.time()
            time.sleep(_SLEEP_SEC)
        log.info("[data_job %s] 财报 %d 成功 · %d 失败", job_id, fin_done, fin_failed)

    _progress(job_id, done=done, skipped=skipped, failed=failed, phase="计算因子")

    # 一只都没成功不是"跑完了"
    if done == 0 and skipped == 0 and not job.get("with_financial"):
        set_status(job_id, "failed",
                   f"{len(codes)} 只全部失败 —— 上游可能在限流,稍后重试")
        return {"error": "all_failed", "failed": failed}

    # 因子**最后统一算**:因子是截面标准化(z-score),要拿到全池数据才能算,
    # 逐只算出来的 z-score 是错的。
    #
    # 但**一只都没下就不用算** —— 因子的输入(K 线)一行没变,算出来必然
    # 和上次一样。实测:同范围跑第二次,300 只全跳过却还在那儿算
    # 59 个调仓日 × 8 个因子,几分钟白等。
    factors = {}
    if done > 0 or fin_done > 0:
        try:
            factors = _compute_factors(codes, start, end,
                                       bool(job.get("with_financial")))
        except Exception as e:                                # noqa: BLE001
            log.error("[data_job %s] 算因子失败: %s", job_id, e)
    else:
        log.info("[data_job %s] 没有新数据 · 跳过因子计算", job_id)

    _parts = []
    if unsupported:
        _parts.append(f"{unsupported} 只北交所 · 免费源没有历史数据")
    if dead:
        _parts.append(f"{dead} 只反复拿不到 · 多半已退市或长期停牌")
    msg = (f"日线 {done} 只 · 跳过 {skipped} 只"
           + (f"(含 {' · '.join(_parts)})" if _parts else "")
           + f" · 失败 {failed} 只"
           + (f" · 财报 {fin_done} 只(失败 {fin_failed})" if job.get("with_financial") else "")
           + f" · 耗时 {int(time.time() - t0)}s"
           + ("" if (done or fin_done) else " · 数据已是最新,无需重算因子"))
    set_status(job_id, "done", msg)
    log.info("[data_job %s] 完成 · %s · 因子 %s", job_id, msg, factors)
    return {"done": done, "skipped": skipped, "failed": failed, "factors": factors}


def _compute_factors(codes: list[str], start: date, end: date,
                     with_financial: bool = False) -> dict:
    """把因子算到每个调仓日上。

    **不能只算当天一个截面** —— 回测要的是每个调仓日的因子值,
    只有当天的话回测一期都选不出票,用户下完立刻点回测得到"选不出股票",
    和没下没区别。

    `with_financial=True` 时连基本面因子一起算。这一条是补的 ——
    原来只算 LOCAL_ONLY,结果:任务显示"财报 292 只成功",
    `financial_metric` 里也确实有 313 只的数据,而 `factor_value` 里
    基本面因子只有 10 只(那还是之前手工测试留下的)。
    用户看到"财报 300 只"却在回测时被告知"这些因子没有数据" ——
    **下载成功和因子可用之间断了一环**。
    """
    from app.services.quant import backtest_engine as bt, factor_engine as fe
    days = sorted(set(bt._rebalance_dates(start, end, "W"))
                  | set(bt._rebalance_dates(start, end, "M")))
    if not days:
        return {}
    keys = list(fe.LOCAL_ONLY)
    if with_financial:
        # 只算真正落了库的那几个(_make_db_factor)。其余仍走 akshare_client
        # 的因子不放进来 —— 那些每只要 8.6 秒,几百只 × 几十个调仓日
        # 会让任务从几分钟变成几十小时
        keys += [k for k in fe.AKSHARE_ONLY if k in _DB_BACKED]
    out: dict[str, int] = {}
    for d in days:
        for k in keys:
            try:
                out[k] = out.get(k, 0) + fe.compute_and_store(k, codes, d)
            except Exception as e:                            # noqa: BLE001
                log.warning("[data_job] 因子 %s @ %s 失败: %s", k, d, e)
    return out


# 已经改成从 financial_metric 读的因子(见 factor_engine._make_db_factor)。
# 这些算起来是纯本地查询,毫秒级,可以放心放进每次任务。
_DB_BACKED = {"roa", "gross_margin", "revenue_growth_yoy", "earnings_growth_yoy"}
