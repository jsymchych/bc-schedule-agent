"""Pure allowlist helpers mirrored from edge/lib/allowlist.ts (fail-closed)."""

from __future__ import annotations


def parse_allowed_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def is_email_allowed(email: str | None, allowed: list[str] | tuple[str, ...]) -> bool:
    if not email:
        return False
    if len(allowed) == 0:
        return False
    return email.strip().lower() in allowed
