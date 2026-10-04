"""魔法筛选器 · 会员额度(2026-09-14 用户定)。

## 口径

- **要登录才能用**:扫描 / 生成(解析)/ 字段搜索 / 命中日四个接口在路由层判 uid,匿名 401。
  `/api/quant/` 是免登录前缀(可选身份识别),所以**必须在路由里判**,前端藏按钮不算拦住。
- **普通会员每天**:AI 识别 10 次、扫描 20 次。管理员不限(自测、排障要用)。
- **一天 = 上海时间自然日**,零点重置。
- **只有真的调了模型才扣 AI 次数**:点「生成」走脚本编译和本地关键词,零 token,不扣;
  本地两条路都不通、用户点了「AI 识别 / AI 修脚本」、而且确实走到了模型调用那一步,才扣。
- **扫描先扣后退**:请求进来先原子地占一次(并发连点不会超额),脚本报错 / 上游挂了就退回 ——
  失败的扫描不该吃掉用户的次数。AI 同理:模型调用失败退回。
- **单条测试(条件行上的「测」)不扣扫描次数**,单独计 `probe`,上限宽松(每天 300),
  返回体不带结果行、只给命中数。它和「运行扫描」共用上游快照缓存,几乎不额外打上游。
- **两次扫描至少隔 5 秒**(`SCAN_GAP_S`,环境变量 `SCREEN_SCAN_GAP_S` 可改,自用本地部署设 0)。
  前端每次扫描后按 `/screener/quota` 返回的 `scan_gap_s` 倒数再显示结果;
  后端这条是给绕过页面直接调接口的,不然前端的等待拦不住任何人。

## 为什么存 postgres 不存内存

api 现在是单进程,内存计数也能用,但**容器一重建计数就清零**(本仓部署频繁,多个会话同时在部署),
等于每次部署白送所有人一整天的额度。表很小:一人一天最多三行。

## 并发安全

占用是一条 `INSERT ... ON CONFLICT DO UPDATE ... WHERE used < limit RETURNING used`:
没返回行 = 已经用满。不先 SELECT 再 UPDATE —— 两个请求同时读到 19 会一起放行,变成 21 次。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.services.database import get_conn

from app.services.market_time import market_tz
_SH = market_tz("CN_A")

# 档位 → 每天次数。以后有「高级会员」就在这里加一档,再在 tier_of 里认出来。
# preset:原样运行官方示例(2026-09-14 用户要求不扣扫描次数)。单独计数、上限宽松 ——
# 不占用户的 20 次,但也不能无限刷(官方示例列组固定,多数命中 90 秒取数缓存,300 次/天足够)
TIERS: dict[str, dict[str, int]] = {
    "normal": {"ai": 10, "scan": 20, "probe": 300, "preset": 300},
}
KIND_LABEL = {"ai": "AI 识别", "scan": "扫描", "probe": "单条测试", "preset": "官方示例运行"}

def _gap_from_env() -> float:
    """两次扫描的间隔 / 结果倒数秒数。默认 5(2026-09-14 按 5000 会员保护上游定的)。

    `SCREEN_SCAN_GAP_S=0` 给**自用的本地部署**(2026-09-17 用户要求本地扫描完立刻显示):
    只有自己一个人用,等 5 秒保护不了谁。多人共用的部署别改 —— 这 5 秒是替所有人省上游。
    只关等待,上游那层保护(90 秒取数缓存 / 同时 3 路 / 页间隔 0.15 秒,见 screen_source.fetch_rows)照旧。
    写错(非数字 / 负数)按默认 5,不按 0 —— 宁可多等也别悄悄把保护关了。"""
    raw = (os.getenv("SCREEN_SCAN_GAP_S") or "").strip()
    try:
        v = float(raw) if raw else 5.0
    except ValueError:
        return 5.0
    return v if v >= 0 else 5.0


SCAN_GAP_S = _gap_from_env()

_DDL = """
CREATE TABLE IF NOT EXISTS screen_quota_usage (
  user_id    TEXT NOT NULL,
  day        DATE NOT NULL,
  kind       TEXT NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, day, kind)
);
"""
# user_id 不做外键:额度记录不是用户资产,删用户时留几行计数无害;
# 反过来,外键会让「令牌里的 sub 在库里查不到」(单用户模式的本地会话)直接 500。

_ddl_applied = False
_ddl_lock = threading.Lock()


class QuotaExceeded(Exception):
    """今天的次数用完了。路由转 429,detail 带 quota 让前端显示。"""

    def __init__(self, kind: str, info: dict):
        self.kind = kind
        self.info = info
        super().__init__(
            f"今天的{KIND_LABEL.get(kind, kind)}次数已经用完"
            f"(普通会员每天 {info.get('limit')} 次),明天 0 点(上海时间)重置。")


class TooFast(Exception):
    def __init__(self, wait_s: float):
        self.wait_s = wait_s
        super().__init__(f"两次扫描至少间隔 {int(SCAN_GAP_S)} 秒,请 {max(1, round(wait_s))} 秒后再试。")


def today_sh() -> "datetime.date":
    return datetime.now(_SH).date()


def reset_at_sh() -> str:
    d = today_sh() + timedelta(days=1)
    return f"{d.isoformat()} 00:00(上海时间)"


def tier_of(role: str | None) -> str | None:
    """None = 不限。只认两种角色:admin 不限,其余一律普通会员。"""
    return None if role == "admin" else "normal"


def limit_of(tier: str | None, kind: str) -> int | None:
    if tier is None:
        return None
    return TIERS[tier][kind]


def info_of(kind: str, used: int, limit: int | None) -> dict:
    return {
        "kind": kind, "label": KIND_LABEL.get(kind, kind),
        "used": used, "limit": limit,
        "remaining": None if limit is None else max(0, limit - used),
        "unlimited": limit is None,
        "reset_at": reset_at_sh(),
    }


def _ensure_table() -> None:
    global _ddl_applied
    if _ddl_applied:
        return
    with _ddl_lock:
        if _ddl_applied:
            return
        conn = get_conn()
        try:
            conn.cursor().execute(_DDL)
            conn.commit()
            _ddl_applied = True
        finally:
            conn.close()


def _used(user_id: str, kind: str) -> int:
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT used FROM screen_quota_usage WHERE user_id=%s AND day=%s AND kind=%s",
                    (user_id, today_sh(), kind))
        r = cur.fetchone()
        return int(r[0]) if r else 0
    finally:
        conn.close()


def reserve(user_id: str, role: str | None, kind: str) -> dict:
    """占一次。用满抛 QuotaExceeded。→ 占用之后的 info(remaining 已扣掉这一次)。"""
    tier = tier_of(role)
    limit = limit_of(tier, kind)
    _ensure_table()
    conn = get_conn()
    try:
        cur = conn.cursor()
        day = today_sh()
        if limit is None:
            cur.execute(
                "INSERT INTO screen_quota_usage (user_id, day, kind, used) VALUES (%s,%s,%s,1) "
                "ON CONFLICT (user_id, day, kind) DO UPDATE SET used = screen_quota_usage.used + 1, "
                "updated_at = NOW() RETURNING used",
                (user_id, day, kind))
        else:
            cur.execute(
                "INSERT INTO screen_quota_usage (user_id, day, kind, used) "
                "SELECT %s, %s, %s, 1 WHERE %s > 0 "
                "ON CONFLICT (user_id, day, kind) DO UPDATE SET used = screen_quota_usage.used + 1, "
                "updated_at = NOW() WHERE screen_quota_usage.used < %s RETURNING used",
                (user_id, day, kind, limit, limit))
        r = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    if r is None:
        raise QuotaExceeded(kind, info_of(kind, limit or 0, limit))
    return info_of(kind, int(r[0]), limit)


def refund(user_id: str, role: str | None, kind: str) -> dict | None:
    """退回一次(扫描 / 模型调用失败)。退失败只影响计数,不能把原来的错误盖掉,所以吞异常。"""
    try:
        _ensure_table()
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE screen_quota_usage SET used = GREATEST(used - 1, 0), updated_at = NOW() "
                "WHERE user_id=%s AND day=%s AND kind=%s RETURNING used",
                (user_id, today_sh(), kind))
            r = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        return info_of(kind, int(r[0]) if r else 0, limit_of(tier_of(role), kind))
    except Exception:                                   # noqa: BLE001
        return None


def snapshot(user_id: str, role: str | None) -> dict:
    """GET /screener/quota 用:今天各类用了几次。"""
    tier = tier_of(role)
    return {
        "login": True,
        "tier": tier or "admin",
        "tier_label": "管理员 · 不限次数" if tier is None else "普通会员",
        "day": today_sh().isoformat(),
        "reset_at": reset_at_sh(),
        "scan_gap_s": SCAN_GAP_S,
        **{k: info_of(k, _used(user_id, k), limit_of(tier, k)) for k in ("ai", "scan")},
    }


# ─── 两次扫描的最短间隔 ───────────────────────────────────────────────
# 进程内记就够:它防的是连点 / 脚本连打,不是跨天的账。重启清零无所谓(最多多放一次)。
_last_scan: dict[str, float] = {}
_gap_lock = threading.Lock()


def check_gap(user_id: str, role: str | None, now: float | None = None) -> None:
    if tier_of(role) is None or SCAN_GAP_S <= 0:
        return
    now = time.monotonic() if now is None else now
    with _gap_lock:
        last = _last_scan.get(user_id)
        if last is not None and now - last < SCAN_GAP_S:
            raise TooFast(SCAN_GAP_S - (now - last))
        _last_scan[user_id] = now
        if len(_last_scan) > 20000:                    # 5000 会员的量级,顺手清掉过期的
            cut = now - SCAN_GAP_S
            for k in [k for k, v in _last_scan.items() if v < cut]:
                _last_scan.pop(k, None)


def mark_done(user_id: str, now: float | None = None) -> None:
    """扫描结束(成功或失败)再记一次:间隔从**上一次扫描完成**起算,不是从发起算 ——
    美股一次扫描 3 秒左右,从发起算的话结果出来 2 秒就能再扫。发起时 check_gap 也记过一次,
    挡的是「上一次还没回来就又点了」。"""
    with _gap_lock:
        _last_scan[user_id] = time.monotonic() if now is None else now
