"""向导第 1 步 · 环境自检（设计方案 4.3 + M1 交接第 7 条）。

检查项：六个服务连通 / 数据库迁移账本 / 密钥来源与强度 / 卷可写 /
api ↔ opencode 双向连通 / 访问方式。

## 三条规矩

1. **每一项都真探测**，不读配置猜结论。「配置里写了 redis 地址」和
   「redis 连得上」是两件事，而用户来看这一页恰恰是因为有东西不对。
2. **探测不能把这个接口拖死**：每项 3 秒超时、互相独立，任何一项挂掉都只影响自己。
3. **算不出就写「未知」并说明原因**，不给 `ok` 也不给 `fail` ——
   把"没测出来"显示成"正常"，比不显示更糟。
"""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

PROBE_TIMEOUT = 3.0
# 需要对端现扫一遍目录的探测用这个(见 probe_opencode_to_api)
SCAN_PROBE_TIMEOUT = 10.0

# .env.example 里的示例值 —— 照抄它等于把公开字符串当密钥用（与 boot.sh 同一份）
WEAK_JWT_SAMPLE = "change-me-in-production-please"
WEAK_INTERNAL_SAMPLE = "hunter-internal-local"
MIN_SECRET_LEN = 32


def _item(key: str, label: str, state: str, detail: str, hint: str = "") -> dict:
    """state: ok | warn | fail | unknown"""
    return {"key": key, "label": label, "state": state, "detail": detail, "hint": hint}


# ── 服务连通 ────────────────────────────────────────────────────────
def _tcp(host: str, port: int) -> tuple[bool, str]:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True, f"{int((time.monotonic() - t0) * 1000)} ms"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _http(url: str) -> tuple[bool, str, int]:
    t0 = time.monotonic()
    try:
        r = httpx.get(url, timeout=PROBE_TIMEOUT)
        return r.status_code < 500, f"HTTP {r.status_code} · {int((time.monotonic() - t0) * 1000)} ms", r.status_code
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", 0


def probe_postgres() -> dict:
    from app.services.database import get_conn
    t0 = time.monotonic()
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return _item("postgres", "数据库 postgres", "ok",
                     f"连接正常 · {int((time.monotonic() - t0) * 1000)} ms")
    except Exception as e:  # noqa: BLE001
        return _item("postgres", "数据库 postgres", "fail", f"{type(e).__name__}: {e}",
                     "看日志：docker compose logs postgres --tail 50")


def probe_redis() -> dict:
    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    p = urlparse(url)
    ok, detail = _tcp(p.hostname or "redis", p.port or 6379)
    return _item("redis", "缓存 redis", "ok" if ok else "fail",
                 detail if ok else f"连不上 {p.hostname}:{p.port or 6379} · {detail}",
                 "" if ok else "看日志：docker compose logs redis --tail 50")


def probe_opencode() -> dict:
    """探对话引擎（AtomCode Daemon 或 OpenCode）。"""
    from app.services import opencode_admin
    if opencode_admin.is_atomcode_backend():
        base = opencode_admin.daemon_url()
        ok, detail, _ = _http(f"{base}/health")
        return _item("opencode", "对话引擎 AtomCode Daemon", "ok" if ok else "fail",
                     detail if ok else f"连不上 {base} · {detail}",
                     "" if ok else "看日志：docker compose logs daemon --tail 50")
    base = os.getenv("OPENCODE_URL", "http://opencode:3901").rstrip("/")
    ok, detail, _ = _http(f"{base}/app")
    return _item("opencode", "对话引擎 opencode", "ok" if ok else "fail",
                 detail if ok else f"连不上 {base} · {detail}",
                 "" if ok else "看日志：docker compose logs opencode --tail 50")


def probe_shim() -> dict:
    url = os.getenv("LLM_SHIM_URL", "http://llm-shim:3999/v1").rstrip("/")
    root = url[:-3] if url.endswith("/v1") else url
    ok, detail, _ = _http(f"{root}/healthz")
    return _item("llm-shim", "schema 清洗层 llm-shim", "ok" if ok else "fail",
                 detail if ok else f"连不上 {root} · {detail}",
                 "" if ok else "看日志：docker compose logs llm-shim --tail 50")


def probe_opencode_to_api() -> dict:
    """探对话引擎与 MCP / API 的连通状态。"""
    from app.services import opencode_admin
    if opencode_admin.is_atomcode_backend():
        base = opencode_admin.daemon_url()
        headers = opencode_admin.daemon_headers()
        if not headers:
            return _item("skill-sync", "AtomCode Daemon ↔ MCP 连通", "unknown",
                         "尚未读取到 /run/hca/daemon-token，跳过 MCP 状态探测")
        try:
            r = httpx.get(f"{base}/mcp/status", headers=headers, timeout=SCAN_PROBE_TIMEOUT)
            if r.status_code == 200 and isinstance(r.json(), dict):
                servers = r.json().get("servers") or []
                total = len(servers)
                connected = sum(1 for s in servers if isinstance(s, dict) and s.get("status") == "connected")
                state = "ok" if (total > 0 and connected == total) else ("warn" if connected > 0 else "fail")
                return _item("skill-sync", "AtomCode Daemon ↔ MCP 连通", state,
                             f"MCP 服务已就绪 {connected}/{total} 个")
            return _item("skill-sync", "AtomCode Daemon ↔ MCP 连通", "warn",
                         f"daemon /mcp/status 返回 HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            return _item("skill-sync", "AtomCode Daemon ↔ MCP 连通", "warn", f"{type(e).__name__}: {e}")

    base = os.getenv("OPENCODE_URL", "http://opencode:3901").rstrip("/")
    key = os.getenv("HUNTER_INTERNAL_KEY", "")
    if not key:
        return _item("skill-sync", "opencode → api 反向连通", "unknown",
                     "HUNTER_INTERNAL_KEY 为空，无法构造探测地址")
    try:
        r = httpx.get(f"{base}/skill", timeout=SCAN_PROBE_TIMEOUT)
        if r.status_code == 401:
            return _item("skill-sync", "opencode → api 反向连通", "unknown",
                         "opencode 开了 Basic Auth，本探测不带凭据，跳过")
        n = len(r.json()) if r.status_code == 200 else -1
        if n >= 0:
            return _item("skill-sync", "opencode → api 反向连通", "ok",
                         f"opencode 当前可见 {n} 个 SKILL（其中用户 SKILL 由 api 提供）")
        return _item("skill-sync", "opencode → api 反向连通", "warn",
                     f"opencode /skill 返回 HTTP {r.status_code}")
    except httpx.TimeoutException:
        return _item("skill-sync", "opencode → api 反向连通", "warn",
                     f"对话引擎还在启动(等它 {SCAN_PROBE_TIMEOUT:.0f} 秒没扫完 SKILL)",
                     "等半分钟点一次「重新检查」;一直这样再看 docker compose logs opencode")
    except Exception as e:  # noqa: BLE001
        return _item("skill-sync", "opencode → api 反向连通", "warn", f"{type(e).__name__}: {e}")


# ── 迁移账本 ────────────────────────────────────────────────────────
def probe_migrations() -> dict:
    from app.services.database import get_conn
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('schema_migrations')")
        if not (cur.fetchone() or [None])[0]:
            conn.close()
            return _item("migrations", "数据库迁移", "fail", "账本表 schema_migrations 不存在",
                         "api 启动时会自动迁移；看 docker compose logs api | grep 迁移")
        cur.execute("SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 1")
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM schema_migrations")
        n = (cur.fetchone() or [0])[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        return _item("migrations", "数据库迁移", "unknown", f"查不到账本：{type(e).__name__}: {e}")

    latest = row[0] if row else "(空)"
    # 镜像里带着全部迁移文件，比一下有没有没跑完的
    pending = []
    try:
        from app import migrate as _m
        files = _m.list_migration_files(_m.resolve_migrations_dir())
        conn = get_conn()
        applied = _m.fetch_applied(conn)
        conn.close()
        pending = [p.name for p in files if p.name not in applied]
    except Exception as e:  # noqa: BLE001
        logger.warning("[setup] 比对待执行迁移失败: {}", e)

    if pending:
        return _item("migrations", "数据库迁移", "fail",
                     f"已应用 {n} 个，最新 {latest}；还有 {len(pending)} 个没执行："
                     + "、".join(pending[:5]),
                     "重启 api 会重跑：docker compose restart api，"
                     "然后看 docker compose logs api | grep 迁移")
    return _item("migrations", "数据库迁移", "ok", f"已应用 {n} 个，最新 {latest}")


# ── 密钥 ────────────────────────────────────────────────────────────
def _secret_source(name: str) -> str:
    """密钥是从哪来的。与 boot.sh 的优先级一致：环境变量非空 → 密钥卷 → 本次生成。

    ⚠️ boot.sh 会把读到的值 **export** 进 api 进程，所以在 api 里
    `os.environ[name]` 一定非空，靠它区分不了来源。

    权威判据是 boot.sh 导出的 `HUNTER_SECRET_SRC_<名字>`（它自己就知道优先级
    命中了哪一条）。**不要再回去比对密钥卷文件的内容** —— boot.sh 会把生效值
    写回文件「哪怕它来自环境变量」（那是为了用户以后从 .env 里删掉也不丢），
    所以"文件里有同一个值"永远成立，比对的结果恒为 volume。

    M3 实测（2026-09-18，Zeabur 等价 compose）：密钥由模板注入成环境变量、
    整个栈里连 hunter_secrets 卷都没有，向导第 1 步照样显示
    「来源：首启自动生成（hunter_secrets 卷）」—— 两个说法都不成立。
    本地部署在 .env 里显式设了 JWT_SECRET 时同样报错。

    老镜像没有那个导出变量，退回旧的比对逻辑（结论不比现在更差）。
    """
    cur = (os.environ.get(name) or "").strip()
    if not cur:
        return "none"
    declared = (os.environ.get(f"HUNTER_SECRET_SRC_{name}") or "").strip()
    if declared in ("env", "volume", "generated"):
        return declared
    f = Path(os.getenv("HUNTER_SECRETS_FILE", "/opt/hunter-secrets/secrets.env"))
    try:
        if f.is_file():
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith(f"{name}="):
                    return "volume" if line[len(name) + 1:].strip() == cur else "env"
    except Exception:  # noqa: BLE001
        pass
    return "env"


def probe_secrets() -> list:
    out = []
    for name, label, weak in (
        ("JWT_SECRET", "JWT_SECRET（签名 + 加密已存 key）", WEAK_JWT_SAMPLE),
        ("HUNTER_INTERNAL_KEY", "HUNTER_INTERNAL_KEY（内部接口口令）", WEAK_INTERNAL_SAMPLE),
    ):
        val = (os.environ.get(name) or "").strip()
        src = _secret_source(name)
        src_cn = {"env": "环境变量（.env 或云平台模板注入）",
                  "volume": "首启自动生成（hunter_secrets 卷）",
                  # 「还没落进卷」这句话是错的:boot.sh 生成之后紧接着就 _persist_secrets 写回去了,
        # 写不进去时它会在日志里单独告警。这里只说事实:这把是这次启动第一次生成的。
        "generated": "本次启动首次生成",
                  "none": "未设置"}[src]
        if not val:
            out.append(_item(f"secret:{name}", label, "fail", "未设置",
                             "api 启动时本该自动生成，看 docker compose logs api | grep 密钥"))
            continue
        if val == weak:
            out.append(_item(f"secret:{name}", label, "fail",
                             f"用的是 .env.example 里的示例值（公开字符串）· 来源：{src_cn}",
                             "换成随机值后 docker compose up -d；"
                             "⚠️ 换 JWT_SECRET 会让已保存的 key 全部解不开，需要重新填写"))
            continue
        if len(val) < MIN_SECRET_LEN:
            out.append(_item(f"secret:{name}", label, "warn",
                             f"只有 {len(val)} 个字符（建议 ≥48，至少 {MIN_SECRET_LEN}）· 来源：{src_cn}",
                             "换成更长的随机值；⚠️ 换 JWT_SECRET 会让已保存的 key 解不开"))
            continue
        out.append(_item(f"secret:{name}", label, "ok", f"{len(val)} 个字符 · 来源：{src_cn}"))
    return out


# ── 卷可写 ──────────────────────────────────────────────────────────
def probe_volumes() -> list:
    out = []
    for env, default, label in (
        ("HUNTER_USER_SKILLS_DIR", "/opt/hunter-user-skills", "用户 SKILL 目录"),
        ("HUNTER_PACKAGE_DIR", "/opt/hunter-packages", "数据包目录"),
    ):
        p = Path(os.getenv(env) or default)
        key = f"volume:{p.name}"
        if not p.exists():
            out.append(_item(key, f"{label}（{p}）", "fail", "目录不存在",
                             "检查 docker-compose.yml 里对应的具名卷有没有挂上"))
            continue
        probe = p / ".hunter-write-probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            n = len([x for x in p.iterdir() if not x.name.startswith(".")])
            out.append(_item(key, f"{label}（{p}）", "ok", f"可写 · 现有 {n} 项"))
        except Exception as e:  # noqa: BLE001
            out.append(_item(key, f"{label}（{p}）", "fail", f"不可写：{type(e).__name__}: {e}",
                             "K8s / Sealos 上需要 fsGroup: 1001 或 initContainer chown"))
    return out


# ── 访问方式 ────────────────────────────────────────────────────────
def probe_access(src, token_configured: bool) -> dict:
    from app.services import setup_guard

    single = (os.getenv("HUNTER_SINGLE_USER", "1") or "1").strip() not in ("0", "false", "no")
    kind_cn = {"local": "本机", "lan": "内网", "public": "公网", "unknown": "判不出"}[src.kind]
    via_cn = {"forwarded": "web 转发头", "peer": "直连对端", "none": "拿不到"}[src.via]

    if token_configured:
        return _item("access", "访问方式", "ok",
                     f"来源 {src.ip or '未知'}（{kind_cn} · 判据来自{via_cn}）· "
                     f"{'单用户模式' if single else '多用户模式'} · 已设置初始化口令")
    if src.kind == "public":
        return _item("access", "访问方式", "fail",
                     f"来源 {src.ip}（公网）· 没有设置初始化口令",
                     "在部署平台的环境变量里设 HUNTER_SETUP_TOKEN 后重启 api")
    # 口令为空 + 判为本机/内网 —— 这个判断是可以被伪造的，必须说清楚
    state = "warn" if single else "warn"
    return _item("access", "访问方式", state,
                 f"来源 {src.ip or '未知'}（{kind_cn} · 判据来自{via_cn}）· "
                 f"{'单用户模式' if single else '多用户模式'} · 没有设置初始化口令",
                 "来源判断依赖 HTTP 转发头，公网上的访问者可以伪造它。"
                 "这台实例如果能从公网打开，请设置 HUNTER_SETUP_TOKEN 后重启 api。")


# ── 汇总 ────────────────────────────────────────────────────────────
def run(src, token_configured: bool) -> dict:
    items = [
        probe_postgres(),
        probe_redis(),
        probe_opencode(),
        probe_shim(),
        probe_opencode_to_api(),
        probe_migrations(),
        *probe_secrets(),
        *probe_volumes(),
        probe_access(src, token_configured),
    ]
    order = {"fail": 0, "warn": 1, "unknown": 2, "ok": 3}
    worst = min((order[i["state"]] for i in items), default=3)
    return {
        "items": items,
        "summary": {
            "ok": sum(1 for i in items if i["state"] == "ok"),
            "warn": sum(1 for i in items if i["state"] == "warn"),
            "fail": sum(1 for i in items if i["state"] == "fail"),
            "unknown": sum(1 for i in items if i["state"] == "unknown"),
        },
        # fail 不挡用户继续 —— 有些项（比如卷不可写）只影响一部分功能，
        # 而向导的主线是把大模型配好。页面上标红即可。
        "worst": ["fail", "warn", "unknown", "ok"][worst],
    }
