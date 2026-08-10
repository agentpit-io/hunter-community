"""AES-256-GCM for at-rest secrets (SaaS keys · MCP creds · ...).

The AES key is derived from JWT_SECRET via HKDF-SHA256 so ops has only
one secret to rotate. Losing JWT_SECRET means users must re-enter their
SaaS keys · this is intentional (fits their threat model).

Ciphertext layout:  base64(nonce[12] || ciphertext_with_tag)
"""
import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _kdf() -> bytes:
    secret = (os.environ.get("JWT_SECRET") or "").encode()
    if not secret:
        raise RuntimeError("JWT_SECRET is not configured · cannot derive AES key")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"hunter-community-saas-key-v1",
        info=b"aes-256-gcm",
    ).derive(secret)


def encrypt(plaintext: str) -> str:
    """Returns base64(nonce || ciphertext-with-tag)."""
    if plaintext is None:
        return ""
    if plaintext == "":
        return ""
    aead = AESGCM(_kdf())
    nonce = os.urandom(12)
    ct = aead.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(ciphertext_b64: str) -> str:
    """Inverse of encrypt · returns empty string on empty input."""
    if not ciphertext_b64:
        return ""
    data = base64.b64decode(ciphertext_b64)
    if len(data) < 13:
        raise ValueError("ciphertext too short")
    nonce, ct = data[:12], data[12:]
    return AESGCM(_kdf()).decrypt(nonce, ct, None).decode()


def mask(secret: str, keep: int = 4) -> str:
    """Return a display-safe suffix of a secret (last `keep` chars)."""
    if not secret:
        return ""
    if len(secret) <= keep:
        return "*" * len(secret)
    return "*" * (len(secret) - keep) + secret[-keep:]
