"""Versioned rule graph loader. Hash is over canonical JSON bytes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RULESET_NAME = "esa_bc_v1.json"


@dataclass(frozen=True)
class Ruleset:
    version: str
    jurisdiction: str
    statute: str
    source_url: str
    rules: tuple[dict[str, Any], ...]
    path: Path
    content_hash: str

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(str(r["id"]) for r in self.rules)

    def get(self, rule_id: str) -> dict[str, Any] | None:
        for rule in self.rules:
            if rule.get("id") == rule_id:
                return rule
        return None


def ruleset_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 over canonical JSON (sorted keys, compact separators)."""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"sha256:{digest}"


def default_ruleset_path() -> Path:
    return Path(__file__).resolve().parents[2] / "rulesets" / DEFAULT_RULESET_NAME


def load_ruleset(path: Path | None = None) -> Ruleset:
    target = path or default_ruleset_path()
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ruleset root must be an object")
    version = str(raw.get("version", ""))
    if not version:
        raise ValueError("ruleset.version is required")
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("ruleset.rules must be a non-empty list")
    for rule in rules:
        if not isinstance(rule, dict) or "id" not in rule or "constraint" not in rule:
            raise ValueError("each rule needs id and constraint")
    content_hash = ruleset_hash(raw)
    return Ruleset(
        version=version,
        jurisdiction=str(raw.get("jurisdiction", "")),
        statute=str(raw.get("statute", "")),
        source_url=str(raw.get("source_url", "")),
        rules=tuple(rules),
        path=target,
        content_hash=content_hash,
    )
