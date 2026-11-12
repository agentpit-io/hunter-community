"""运行时配置 · 大模型三件套的唯一读取入口(设计方案 3.3)。

**优先级:环境变量非空 → 数据库(向导写入)→ 未配置。每一项独立判断。**

为什么不能只读环境变量:一键部署的用户不会先去写 `.env`,大模型配置要能在
浏览器里填完就生效(存数据库、加密)。为什么环境变量仍要优先:老用户(以及
演示站)三项都写在 `.env` 里,行为必须与改造前逐位一致,且向导不能把它改掉。

> ⚠️ compose 的 `${X:-}` 会把「未设置」变成**空字符串**注入容器,所以判断一律
> 用「非空」,不能用「已定义」。`os.getenv("LLM_BASE_URL") is not None` 在容器里
> 永远为真。

存储沿用 `hunter_config` 表(平台 key 就存在那儿),写法照抄
`app/services/hunter_key.py`:`_ensure_table` 幂等建表、30 秒缓存、
`app/utils/crypto.py` 加密 api_key。

键:
    llm.base_url · llm.api_key(加密) · llm.model · llm.sanitize · llm.tested_at
    llm.builtin · llm.agent_models
    setup.completed_at

**数据库不可用时不抛异常**(迁移还没跑完、库挂了、密钥变过导致解不开):
打 warning、按「这一项没配」处理。理由同 `hunter_key.resolve()` ——
配置读取在很多请求的主干上,让它抛异常等于整站 500。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.services.database import get_conn
from app.utils.crypto import decrypt, encrypt

# ── 键名 ────────────────────────────────────────────────────────────
K_BASE_URL = "llm.base_url"
K_API_KEY = "llm.api_key"          # 入库前过 crypto.encrypt
K_MODEL = "llm.model"
K_SANITIZE = "llm.sanitize"
K_TESTED_AT = "llm.tested_at"
K_SETUP_DONE = "setup.completed_at"
# 内置额度模式的标记与随它一起写下的 agent 侧模型名(P2 · 方案 4.5 / 4.8)
K_BUILTIN = "llm.builtin"            # "1" = 这台实例走 HunterCode 内置额度
K_AGENT_MODELS = "llm.agent_models"  # JSON:{"ASSISTANT_MODEL_CHAT": "hunter-chat", ...}

_LLM_KEYS = (K_BASE_URL, K_API_KEY, K_MODEL, K_SANITIZE)
# 同一次查库顺手把这两项也捞出来,省一次往返(它们和大模型配置总是一起读)
_READ_KEYS = _LLM_KEYS + (K_BUILTIN, K_AGENT_MODELS)

# 单项 → (环境变量名, 数据库键)
_ITEMS = {
    "base_url": ("LLM_BASE_URL", K_BASE_URL),
    "api_key": ("LLM_API_KEY", K_API_KEY),
    "model": ("LLM_DEFAULT_MODEL", K_MODEL),
    "sanitize": ("LLM_SCHEMA_SANITIZE", K_SANITIZE),
}

# schema 清洗开关没配时的取值。与 scripts/llm-shim、gen-config.py 的默认一致:
# auto = 只有模型名含 gemini 才绕 shim。
SANITIZE_DEFAULT = "auto"

_DDL = """
CREATE TABLE IF NOT EXISTS hunter_config (
  k          VARCHAR(64) PRIMARY KEY,
  v          TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_ddl_applied = False


def _ensure_table() -> None:
    global _ddl_applied
    if _ddl_applied:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(_DDL)
    conn.commit()
    conn.close()
    _ddl_applied = True


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    sanitize: str        # "1" / "0" / "auto"
    configured: bool     # 三件套齐全才算配好(与改造前 gen-config 的判据一致)
    source: str          # "env"(三项全来自环境变量) | "db" | "none"


# ── 缓存 ────────────────────────────────────────────────────────────
# 每次取数都读库太吵,30 秒足够让「向导保存 → 下一条对话」感觉是即时的,
# 又不会让一次繁忙的对话把 Postgres 打满。与 hunter_key 同一个数量级。
_CACHE_TTL = 30.0
_cache: dict = {"rows": None, "at": 0.0}


def invalidate() -> None:
    """让下一次读取重新查库。写库之后必须调。"""
    _cache.update(rows=None, at=0.0)


def _env(item: str) -> str:
    """环境变量的**非空**取值。空串 = 没配(见文件头的 compose 说明)。"""
    val = (os.environ.get(_ITEMS[item][0]) or "").strip()
    if val:
        return val
    if item == "api_key":
        # HCA 兼容：支持 HCA_LLM_API_KEY 以及 LLM_API_KEY_FILE / HCA_LLM_API_KEY_FILE 文件挂载
        hca_val = (os.environ.get("HCA_LLM_API_KEY") or "").strip()
        if hca_val:
            return hca_val
        key_file = (
            os.environ.get("LLM_API_KEY_FILE")
            or os.environ.get("HCA_LLM_API_KEY_FILE")
            or ""
        ).strip()
        if key_file:
            try:
                with open(key_file, "r", encoding="utf-8") as fh:
                    file_val = fh.read().strip()
                if file_val:
                    return file_val
            except OSError:
                pass
    return ""


def _read_db() -> dict:
    """真正查库。返回 {数据库键: 明文}。测试里被 monkeypatch 掉。"""
    _ensure_table()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT k, v FROM hunter_config WHERE k = ANY(%s)", (list(_READ_KEYS),))
    rows = {k: (v or "") for k, v in (cur.fetchall() or [])}
    conn.close()
    enc = rows.get(K_API_KEY) or ""
    if enc:
        try:
            rows[K_API_KEY] = decrypt(enc)
        except Exception as e:  # noqa: BLE001
            # JWT_SECRET 变过 → 派生的 AES 密钥也变了 → 老密文解不开。
            # 这不是 bug,是设计(见 crypto.py 文件头)。当成「没配」并说清楚,
            # 让向导引导用户重新填,而不是抛 500。
            logger.warning("[runtime_config] llm.api_key 解密失败(JWT_SECRET 变过?)"
                           "· 按未配置处理 · {}", type(e).__name__)
            rows[K_API_KEY] = ""
    return rows


def _db() -> dict:
    now = time.time()
    if _cache["rows"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["rows"]
    try:
        rows = _read_db()
    except Exception as e:  # noqa: BLE001
        # 迁移还没跑完 / 库挂了 —— 照抄 hunter_key.resolve() 的处理:
        # 不抛异常,按未配置处理。调用方会走「大模型尚未配置」那条路。
        logger.warning("[runtime_config] 读数据库失败(按未配置处理): {}", e)
        rows = {}
    _cache.update(rows=rows, at=now)
    return rows


def _pick(item: str, rows_box: list) -> tuple[str, str]:
    """返回 (值, 来源)。rows_box 是个单元素列表,用来把「数据库只查一次、
    而且三项都来自环境变量时压根不查」这件事传递下去。"""
    val = _env(item)
    if val:
        return val, "env"
    if rows_box[0] is None:
        rows_box[0] = _db()
    val = (rows_box[0].get(_ITEMS[item][1]) or "").strip()
    return (val, "db") if val else ("", "none")


def llm() -> LLMConfig:
    """当前生效的大模型配置。

    三项都来自环境变量时**不查库**(老用户零行为变化、零额外依赖)。
    """
    rows_box: list = [None]
    base_url, s_base = _pick("base_url", rows_box)
    api_key, s_key = _pick("api_key", rows_box)
    model, s_model = _pick("model", rows_box)
    sanitize, _ = _pick("sanitize", rows_box)

    base_url = base_url.rstrip("/")
    configured = bool(base_url and api_key and model)
    if not configured:
        overall = "none"
    elif s_base == s_key == s_model == "env":
        # 三项全锁死在环境变量里 —— gen-config 据此把 provider 写进项目文件
        # (优先级最高,向导改不动),正好实现「环境变量锁定」。
        overall = "env"
    else:
        overall = "db"
    return LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        sanitize=(sanitize or SANITIZE_DEFAULT).lower(),
        configured=configured,
        source=overall,
    )


def source(key: str) -> str:
    """单项来源:"env" | "db" | "none"。key 可写 "model" 或 "llm.model"。"""
    item = key[4:] if key.startswith("llm.") else key
    if item not in _ITEMS:
        raise ValueError(f"未知配置项 {key!r},可用:{', '.join(_ITEMS)}")
    return _pick(item, [None])[1]


def env_locked() -> bool:
    """有任何一项写在环境变量里 = 向导不能假装能改它。

    语义与 `hunter_key.env_locked()` 一致(环境变量有值就锁)。
    需要知道**具体哪一项**被锁时用 `source()`,向导逐项显示。
    """
    return any(_env(i) for i in ("base_url", "api_key", "model"))


# ── 内置额度模式(P2 · 方案 4.8)────────────────────────────────────
# 官方内置额度网关的地址。用户在向导里选「使用 HunterCode 内置额度」时由向导
# 填进来,**不是**代码里的兜底默认值 —— 没选这条路的部署,一个字节都不会发到
# 这个地址(M1 3.4 那条铁律:`LLM_BASE_URL` 不许有指回我们服务器的默认值)。
BUILTIN_BASE_URL = "https://hunter.agentpit.io/api/saas/llm/v1"
BUILTIN_CHAT_MODEL = "hunter-chat"
BUILTIN_DEEP_MODEL = "hunter-deep"
#: 网关额度接口。base_url 去掉末尾的 /v1 再接这个。
BUILTIN_QUOTA_PATH = "/quota"

#: 内置额度路径下写给 agent 侧的模型名(方案 4.5「向导把这些变量一并指向 hunter-deep」)。
#: 长任务(深度分析 / 深度研究)走 `hunter-deep`,其余走 `hunter-chat` ——
#: deep 的输出单价是 chat 的几倍,不该让路由分类、摘要压缩这些短任务也吃它。
BUILTIN_AGENT_MODELS = {
    # 对话 / 路由 / 摘要 / 子智能体
    "ASSISTANT_MODEL_ROUTE": BUILTIN_CHAT_MODEL,
    "ASSISTANT_MODEL_CHAT": BUILTIN_CHAT_MODEL,
    "ASSISTANT_MODEL_COMPRESS": BUILTIN_CHAT_MODEL,
    "AGENT_MODEL_ROUTER": BUILTIN_CHAT_MODEL,
    "AGENT_MODEL_ROUTE_LITE": BUILTIN_CHAT_MODEL,
    "AGENT_MODEL_GENERAL_FINANCE": BUILTIN_CHAT_MODEL,
    "AGENT_SUB_WL_MODEL": BUILTIN_CHAT_MODEL,
    "AGENT_SUB_PORT_MODEL": BUILTIN_CHAT_MODEL,
    "AGENT_SUB_EVENT_MODEL": BUILTIN_CHAT_MODEL,
    "SIGNAL_ANALYSIS_MODEL": BUILTIN_CHAT_MODEL,
    # 长任务
    "AGENT_SUB_RESEARCH_MODEL": BUILTIN_DEEP_MODEL,
    "AGENT_SUB_UZI_MODEL": BUILTIN_DEEP_MODEL,
}


def builtin_quota_url(base_url: str) -> str:
    """把 `.../api/saas/llm/v1` 换成 `.../api/saas/llm/quota`。不是网关地址就返回空串。"""
    b = (base_url or "").rstrip("/")
    if not is_builtin_base(b):
        return ""
    return b[: -len("/v1")] + BUILTIN_QUOTA_PATH


def is_builtin_base(url: str) -> bool:
    """这个地址是不是内置额度网关。

    判据是**路径**而不是域名:自建一套 hermes 的人(以及我们自己的测试环境)
    用的是同一套路由 `/api/saas/llm/v1`,额度接口也在同一个地方,该认。
    """
    return (url or "").rstrip("/").lower().endswith("/api/saas/llm/v1")


def builtin() -> bool:
    """这台实例当前是不是走内置额度。

    两条判据取或:
      · 向导保存时写下的 `llm.builtin` 标记(正常路径);
      · 当前生效地址就是内置额度网关 —— 覆盖「.env 里写死网关地址」的锁定实例,
        那种情况下向导从没写过标记,但额度展示与错误引导照样应该生效。
    """
    if is_builtin_base(llm().base_url):
        return True
    return (_db().get(K_BUILTIN) or "").strip() == "1"


# ── agent 侧模型名(深度分析 / 子智能体)──────────────────────────────
# 这些变量在 `docker-compose.yml` 里写成 `${X:-}`,**没有 .env 时注进容器的是
# 空串**,于是 `os.getenv("X", "默认值")` 拿到的是 `""` 而不是默认值 ——
# 请求打到上游时 `model` 是空的。P1 在测试机上实测到这个坑(P1 报告第 9 节第 1 条):
# 表现是「工具调用成功,但之后的 LLM 汇总整个失败,只能用模板兜底」。
#
# 这里统一成和大模型三件套一样的优先级:**环境变量非空 → 数据库 → 代码默认值**。
# 内置额度路径下向导会把这一批写进数据库(chat 类 → hunter-chat,长任务 → hunter-deep),
# 用户不用手工配;自带 key 的实例什么都不写,行为回到「代码默认值」——
# 也就是这个坑被踩之前本来就该有的样子。
def agent_models() -> dict:
    """数据库里存着的一批 agent 侧模型名。读不出来就是空字典。"""
    raw = (_db().get(K_AGENT_MODELS) or "").strip()
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("[runtime_config] llm.agent_models 不是合法 JSON(按未配置处理): {}", e)
        return {}
    if not isinstance(d, dict):
        logger.warning("[runtime_config] llm.agent_models 不是对象(按未配置处理)")
        return {}
    return {str(k): str(v).strip() for k, v in d.items() if v and isinstance(v, str)}


def agent_model(env_name: str, default: str = "") -> str:
    """一个 agent 侧模型名的当前取值。

    **调用点必须是惰性的**(在函数体里调,不要 `X = agent_model(...)` 写成模块级
    常量)—— 向导热生效之后不重启容器,模块级常量会一直是旧值。
    """
    v = (os.environ.get(env_name) or "").strip()
    if v:
        return v
    v = (agent_models().get(env_name) or "").strip()
    return v or default


# ── 写 ──────────────────────────────────────────────────────────────
def _set(conn, k: str, v: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO hunter_config (k, v, updated_at) VALUES (%s, %s, NOW())
           ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = NOW()""",
        (k, v),
    )


def save_llm(cfg, tested_token: Optional[str] = None) -> None:
    """把向导填的配置写进数据库并失效缓存。

    ⚠️ `tested_token` 现在**只是记个时间戳,不做校验**。设计方案 4.5 里它是
    「对地址 + 模型 + key 摘要做 HMAC、10 分钟有效」的凭证,校验逻辑属于 M2 的
    `PUT /api/setup/llm`。M1 先把参数留出来,是为了 M2 接上时不用改调用方签名;
    **在这里造一个半成品的 HMAC 校验比没有更糟** —— 它会给人「已经验过了」的
    错觉。M2 补校验时,把校验放在这里或路由层都行,但必须真的比对摘要与有效期。

    环境变量锁定时**不在这里拒绝** —— 拒绝(409)是接口层的事,这里只负责写。
    写进去也不会生效(`llm()` 里环境变量优先),但向导不该走到这一步。
    """
    base_url = (getattr(cfg, "base_url", None) or "").strip().rstrip("/")
    api_key = (getattr(cfg, "api_key", None) or "").strip()
    model = (getattr(cfg, "model", None) or "").strip()
    sanitize = (getattr(cfg, "sanitize", None) or SANITIZE_DEFAULT).strip().lower()

    _ensure_table()
    conn = get_conn()
    try:
        _set(conn, K_BASE_URL, base_url)
        # 明文永远不落库 —— AES-256-GCM,密钥由 JWT_SECRET 派生(crypto.py)
        _set(conn, K_API_KEY, encrypt(api_key) if api_key else "")
        _set(conn, K_MODEL, model)
        _set(conn, K_SANITIZE, sanitize)
        if tested_token:
            _set(conn, K_TESTED_AT, str(int(time.time())))
        conn.commit()
    finally:
        conn.close()
    invalidate()
    # 只打来源与长度,**绝不打印 key 本身**
    logger.info("[runtime_config] 已保存大模型配置 · base_url={} · model={} · apiKey {}",
                base_url or "(空)", model or "(空)", f"{len(api_key)} 字符" if api_key else "无")


def get_str(k: str) -> str:
    """读一个裸字符串配置(不加密、不缓存)。给 M2 的 setup.completed_at 用。"""
    try:
        _ensure_table()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT v FROM hunter_config WHERE k = %s", (k,))
        row = cur.fetchone()
        conn.close()
        return (row[0] or "") if row else ""
    except Exception as e:  # noqa: BLE001
        logger.warning("[runtime_config] 读 {} 失败: {}", k, e)
        return ""


def set_str(k: str, v: str) -> None:
    _ensure_table()
    conn = get_conn()
    try:
        _set(conn, k, v)
        conn.commit()
    finally:
        conn.close()


def save_builtin(on: bool, models: Optional[dict] = None) -> None:
    """记下「这台实例走不走内置额度」,顺带写下 agent 侧的一批模型名。

    `on=False` 时把两项都清空 —— 用户从内置额度切回自带 key 时,那批指向
    `hunter-chat` / `hunter-deep` 的模型名必须一起消失,否则深度分析会拿着
    只有我们网关认识的模型名去打他自己的上游,400 到底。
    """
    payload = json.dumps(models or {}, ensure_ascii=False, sort_keys=True) if on else ""
    _ensure_table()
    conn = get_conn()
    try:
        _set(conn, K_BUILTIN, "1" if on else "")
        _set(conn, K_AGENT_MODELS, payload)
        conn.commit()
    finally:
        conn.close()
    invalidate()
    logger.info("[runtime_config] 内置额度标记 = {} · agent 模型 {} 项",
                "1" if on else "(空)", len(models or {}) if on else 0)


# ─────────────────────────────────────────────────────────────────────
# ONE_API_* 的上游地址(M1 合并时补)
# ─────────────────────────────────────────────────────────────────────
def one_api_base_url() -> str:
    """`ONE_API_BASE_URL` 非空时用它,否则回落到当前生效的大模型地址。

    **为什么要有这个函数**:仓库里有 10 处(attribution / signal_monitor ×2 /
    online_analysis.thesis_gen / research_assistant ×2 / gm 的 guardian·research·
    assistant·recap)把 `ONE_API_BASE_URL` 的默认值写成了我们自己演示站的网关 IP。
    M1 已经把 `LLM_BASE_URL` 那 8 处硬编码删掉了 —— 开源用户没配地址时,他的数据
    不该被发到我们的服务器上。这 10 处是同一类问题,只修一半反而更糟:同一个部署里
    一部分功能老老实实报「未配置」,另一部分照样往我们这儿发。

    `ONE_API_*` 是内部部署用的一组独立变量(网关 / key / 模型名各一个),
    **优先级保持不变**:填了就用填的。只是「没填」时的落点从「我们的网关」
    改成「这个部署自己配的大模型地址」——对已经配了 ONE_API_BASE_URL 的部署
    逐字节无变化,对没配的部署从「偷偷发给我们」变成「用你自己的网关或如实报未配置」。

    注意这些调用点都还会读 `ONE_API_KEY`,为空时自己就跳过了,所以这里回落到
    空字符串是安全的(不会拿着别人的地址发无 key 请求)。
    """
    v = (os.getenv("ONE_API_BASE_URL") or "").strip()
    if v:
        return v.rstrip("/")
    return llm().base_url
