#!/usr/bin/env python3
"""Dump FastAPI's app.openapi() to docs/api-reference.json.

Run manually or from CI:
    HUNTER_MINIMAL_BOOT=1 JWT_SECRET=ci-only \
        python scripts/export-openapi.py

Bypasses the runtime `HUNTER_ENABLE_DOCS` gate (we don't want /docs live
in production but we do want a shipped JSON reference).
"""
import json
import os
import sys
from pathlib import Path

# Boot with minimal env · no DB required for openapi() introspection
os.environ.setdefault("HUNTER_MINIMAL_BOOT", "1")
os.environ.setdefault("JWT_SECRET", "openapi-export-placeholder")
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder@localhost/placeholder")

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parent / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from main import app  # noqa: E402 · after sys.path append

# Force-generate openapi even if docs_url is None (that only gates the
# HTTP endpoint · the schema itself is always available).
spec = app.openapi()

# Strip auto-registered internal fields we don't want to publish
if "servers" in spec:
    spec.pop("servers")

out = HERE.parent / "docs" / "api-reference.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True))

paths = spec.get("paths", {})
print(f"[openapi] wrote {out} · {len(paths)} paths")
