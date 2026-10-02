"""Signed invite tokens (1A) + single-use jti contract (2B).

Mirrored by edge/lib/invite.ts — keep encode/verify compatible.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_payload(secret: str, payload_obj: dict[str, Any]) -> str:
    payload = _b64url(
        json.dumps(payload_obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = _b64url(
        hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{payload}.{sig}"


def verify_token(secret: str, token: str, *, now: int | None = None) -> dict[str, Any]:
    """Verify HMAC and expiry. Does not consult redeem ledger."""
    if not secret:
        raise ValueError("secret required")
    if not token or "." not in token:
        raise ValueError("malformed token")
    payload_b64, sig = token.rsplit(".", 1)
    expected = _b64url(
        hmac.new(
            secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
    )
    if not hmac.compare_digest(expected, sig):
        raise ValueError("bad signature")
    obj = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("bad payload")
    jti = obj.get("jti")
    exp = obj.get("exp")
    if not isinstance(jti, str) or not jti:
        raise ValueError("missing jti")
    if not isinstance(exp, int):
        raise ValueError("missing exp")
    ts = int(time.time() if now is None else now)
    if ts >= exp:
        raise ValueError("expired")
    return obj


def mint_invite(
    secret: str,
    *,
    label: str = "invite",
    ttl_seconds: int = 14 * 24 * 3600,
    now: int | None = None,
    jti: str | None = None,
) -> str:
    ts = int(time.time() if now is None else now)
    payload = {
        "jti": jti or secrets.token_urlsafe(16),
        "label": label,
        "iat": ts,
        "exp": ts + int(ttl_seconds),
    }
    return sign_payload(secret, payload)


def invite_url(base_url: str, token: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/invite?t={quote(token, safe='')}"
