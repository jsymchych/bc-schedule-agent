"""Versioned staffing curve loader. Hash reuses ESA ruleset canonical JSON."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bc_schedule_agent.ruleset import ruleset_hash

DEFAULT_STAFFING_NAME = "staffing_demo_v1.json"


@dataclass(frozen=True)
class StaffingBand:
    id: str
    headcount: int
    max_sales_inclusive: float | None  # None = open upper bound


@dataclass(frozen=True)
class StaffingCurve:
    version: str
    currency: str
    bands: tuple[StaffingBand, ...]
    slot_prefix: str
    meal_break_after_hours: float
    meal_break_minutes: int
    path: Path
    content_hash: str

    def band_for_sales(self, sales: float) -> StaffingBand:
        ordered = sorted(
            self.bands,
            key=lambda b: (
                float("inf") if b.max_sales_inclusive is None else b.max_sales_inclusive
            ),
        )
        for band in ordered:
            if band.max_sales_inclusive is None or sales <= band.max_sales_inclusive:
                return band
        return ordered[-1]


def default_staffing_path() -> Path:
    return Path(__file__).resolve().parents[2] / "rulesets" / DEFAULT_STAFFING_NAME


def load_staffing(path: Path | None = None) -> StaffingCurve:
    target = path or default_staffing_path()
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("staffing root must be an object")
    version = str(raw.get("version", ""))
    if not version:
        raise ValueError("staffing.version is required")
    bands_raw = raw.get("bands")
    if not isinstance(bands_raw, list) or not bands_raw:
        raise ValueError("staffing.bands must be a non-empty list")
    bands: list[StaffingBand] = []
    for item in bands_raw:
        if not isinstance(item, dict):
            raise ValueError("each staffing band must be an object")
        band_id = str(item.get("id") or "")
        if not band_id:
            raise ValueError("staffing band id is required")
        headcount = int(item["headcount"])
        if headcount < 0:
            raise ValueError(f"band {band_id}: headcount must be >= 0")
        max_sales = item.get("max_sales_inclusive", _MISSING)
        if max_sales is _MISSING:
            raise ValueError(f"band {band_id}: max_sales_inclusive required (null ok)")
        max_parsed: float | None
        if max_sales is None:
            max_parsed = None
        else:
            max_parsed = float(max_sales)
        bands.append(
            StaffingBand(
                id=band_id,
                headcount=headcount,
                max_sales_inclusive=max_parsed,
            )
        )
    content_hash = ruleset_hash(raw)
    return StaffingCurve(
        version=version,
        currency=str(raw.get("currency", "CAD")),
        bands=tuple(bands),
        slot_prefix=str(raw.get("slot_prefix") or "slot"),
        meal_break_after_hours=float(raw.get("meal_break_after_hours", 5)),
        meal_break_minutes=int(raw.get("meal_break_minutes", 30)),
        path=target,
        content_hash=content_hash,
    )


_MISSING = object()


def staffing_payload_hash(payload: dict[str, Any]) -> str:
    """Alias for the shared canonical JSON hash (tests / callers)."""
    return ruleset_hash(payload)
