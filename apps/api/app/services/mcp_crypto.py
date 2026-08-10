"""mcp_crypto · AES-256-GCM 加密 · 用户自定义 MCP 的 API key 保护

master key 来自 env HUNTER_MCP_KMS_KEY(base64 32 bytes) · 未设置时用固定 fallback
(仅开发；生产必须 env 注入)。加密结果 base64(nonce|ciphertext|tag)。
"""
from __future__ import annotations
import base64
import os
import secrets
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ── master key 从 env 拉 · 缺失时用 fallback ────────────────────────────
_FALLBACK = b"hunter-dev-fallback-key-not-for-prod-use-32b!!!"[:32]


def _master_key() -> bytes:
    raw = os.getenv("HUNTER_MCP_KMS_KEY", "")
    if not raw:
        return _FALLBACK
    try:
        key = base64.b64decode(raw)
        if len(key) != 32:
            return _FALLBACK
        return key
    except Exception:
        return _FALLBACK


def encrypt(plaintext: str) -> str:
    """AES-256-GCM 加密 · 返回 base64(nonce+ciphertext+tag)。空串返回空串。"""
    if not plaintext:
        return ""
    aesgcm = AESGCM(_master_key())
    nonce = secrets.token_bytes(12)   # GCM 推荐 12 字节 nonce
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(encoded: Optional[str]) -> str:
    """解密 · 失败返回空串（不抛异常 · 上游可判断空串走无 auth 分支）。"""
    if not encoded:
        return ""
    try:
        blob = base64.b64decode(encoded)
        if len(blob) < 12 + 16:
            return ""
        nonce, ct = blob[:12], blob[12:]
        aesgcm = AESGCM(_master_key())
        return aesgcm.decrypt(nonce, ct, associated_data=None).decode("utf-8")
    except Exception:
        return ""


def key_hint(plaintext: str) -> str:
    """UI 回显用 · 前缀 4 字符 + 末尾 4 字符 · 中间省略号 · 太短则星号盖头。"""
    if not plaintext:
        return ""
    s = plaintext.strip()
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}****{s[-4:]}"
