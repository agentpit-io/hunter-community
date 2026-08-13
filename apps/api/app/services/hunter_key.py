"""Hunter platform key · resolve / save / verify.

Community Edition works with your own LLM key for plain chat. The **tools and
SKILLs** (quote · kline · news · UZI deep-dive · Kronos forecast) execute on
Hunter's servers, so they need a key issued by us:

    https://hunter.agentpit.io/dev/api-keys   (free · ~30 seconds)

Two ways to supply it, checked in this order:

  1. ``HUNTER_API_KEY`` in ``.env``  — survives container recreation, best for
     an instance you run for yourself. Requires a restart to change.
  2. Pasted in the UI (bottom-left "解锁全部工具") — stored AES-encrypted in the
     ``hunter_config`` table, takes effect immediately, no restart.

It is deliberately **instance-wide, not per-user**: this is software you run on
your own machine, and the data-source provider is a process-level singleton.
Splitting it per user would mean a key lookup on every quote call for no real
benefit at self-hosted scale.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx
from loguru import logger

from app.services.database import get_conn
from app.utils.crypto import decrypt, encrypt

# Where the tool gateway lives. Override only if you run your own Hunter.
UPSTREAM = os.getenv("HUNTER_UPSTREAM_URL", "https://hunter.agentpit.io").rstrip("/")
APPLY_URL = f"{UPSTREAM}/dev/api-keys"

_ENV_KEY = (os.getenv("HUNTER_API_KEY") or "").strip()

_DDL = """
CREATE TABLE IF NOT EXISTS hunter_config (
  k          VARCHAR(64) PRIMARY KEY,
  v          TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""
_KEY_ROW = "hunter_api_key_enc"

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


# The DB key is read on every tool call, so cache it briefly. 30s is short
# enough that "paste key → click a SKILL" feels instant, long enough that a
# busy chat turn doesn't hammer Postgres.
_cache: dict = {"key": None, "at": 0.0}
_CACHE_TTL = 30.0


def resolve() -> str:
    """The key in effect right now. Empty string means "not configured"."""
    if _ENV_KEY:
        return _ENV_KEY
    now = time.time()
    if _cache["key"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["key"]
    key = ""
    try:
        _ensure_table()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT v FROM hunter_config WHERE k = %s", (_KEY_ROW,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            key = decrypt(row[0])
    except Exception as e:
        logger.warning("[hunter_key] read failed (treating as unconfigured): {}", e)
    _cache.update(key=key, at=now)
    return key


def save(plain: str) -> None:
    _ensure_table()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO hunter_config (k, v, updated_at) VALUES (%s, %s, NOW())
           ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = NOW()""",
        (_KEY_ROW, encrypt(plain.strip())),
    )
    conn.commit()
    conn.close()
    _cache.update(key=plain.strip(), at=time.time())


def clear() -> None:
    _ensure_table()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM hunter_config WHERE k = %s", (_KEY_ROW,))
    conn.commit()
    conn.close()
    _cache.update(key="", at=time.time())


def masked(key: Optional[str] = None) -> str:
    k = key if key is not None else resolve()
    if not k:
        return ""
    return f"{k[:15]}****{k[-4:]}" if len(k) > 22 else "****"


def env_locked() -> bool:
    """True when the key comes from .env — the UI must not pretend it can change it."""
    return bool(_ENV_KEY)


async def manifest(key: str = "") -> dict:
    """Ask upstream what this key unlocks.

    Returns the gateway's payload verbatim; on network failure returns a
    locked-but-honest shape so the UI degrades to "can't reach Hunter" instead
    of silently claiming everything is fine.
    """
    k = key or resolve()
    headers = {"Authorization": f"Bearer {k}"} if k else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{UPSTREAM}/api/saas/tools/manifest", headers=headers)
        if r.status_code >= 500:
            raise RuntimeError(f"upstream {r.status_code}")
        return r.json()
    except Exception as e:
        logger.warning("[hunter_key] manifest unreachable: {}", e)
        return {
            "unlocked": False,
            "apply_url": APPLY_URL,
            "message": f"连不上 Hunter 服务器({str(e)[:80]})。检查网络后重试。",
            "tools": [],
            "upstream_error": True,
        }
