"""Named, versioned parameter shelf — hard authority for draft compose.

Current shelf is closed-world for availability, approved time-off, and demand.
History (Wave A) is soft prior; this shelf is hard. ESA ruleset unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.ingest import content_hash, parse_availability_sheet
from bc_schedule_agent.models import AvailabilityWindow
from bc_schedule_agent.staffing import StaffingCurve, load_staffing

SHELF_SCHEMA_VERSION = "1"
SHELF_MISMATCH_RULE = "parameter-shelf-mismatch"
SHELF_MISMATCH_SECTION = "parameter_shelf"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_shelf_root() -> Path:
    """Home artifacts/parameters — survives demo_app restart."""
    return Path(__file__).resolve().parents[2] / "artifacts" / "parameters"


def packets_content_hash(payloads: list[dict[str, Any]] | None) -> str | None:
    """Canonical hash of optional averaging packet payloads."""
    if not payloads:
        return None
    canonical = json.dumps(payloads, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return content_hash(canonical)


def roster_from_availability(
    availability: list[AvailabilityWindow] | None = None,
    *,
    availability_raw: bytes | None = None,
) -> list[str]:
    """Distinct employees from availability — derived roster on the shelf."""
    windows = availability
    if windows is None:
        if availability_raw is None:
            return []
        windows = parse_availability_sheet(availability_raw)
    seen: list[str] = []
    for window in windows:
        if window.employee not in seen:
            seen.append(window.employee)
    return seen


def _make_shelf_id(parts: dict[str, str | None]) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:12]
    return f"shelf_{digest}"


@dataclass
class ParameterShelf:
    """Versioned snapshot of how this house runs for the active draft week."""

    parameter_shelf_id: str
    staffing_version: str
    staffing_hash: str
    hours_of_operation_hash: str
    sales_projections_hash: str
    availability_hash: str
    time_off_hash: str | None = None
    averaging_packets_hash: str | None = None
    demand_override_hash: str | None = None
    derived_roster: list[str] = field(default_factory=list)
    schema_version: str = SHELF_SCHEMA_VERSION
    created_at: str | None = None

    def content_hashes(self) -> dict[str, str | None]:
        return {
            "hours_of_operation": self.hours_of_operation_hash,
            "sales_projections": self.sales_projections_hash,
            "availability": self.availability_hash,
            "time_off": self.time_off_hash,
            "averaging_packets": self.averaging_packets_hash,
            "demand_override": self.demand_override_hash,
            "staffing": self.staffing_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ParameterShelf:
        return cls(
            parameter_shelf_id=str(raw["parameter_shelf_id"]),
            staffing_version=str(raw["staffing_version"]),
            staffing_hash=str(raw["staffing_hash"]),
            hours_of_operation_hash=str(raw["hours_of_operation_hash"]),
            sales_projections_hash=str(raw["sales_projections_hash"]),
            availability_hash=str(raw["availability_hash"]),
            time_off_hash=raw.get("time_off_hash"),
            averaging_packets_hash=raw.get("averaging_packets_hash"),
            demand_override_hash=raw.get("demand_override_hash"),
            derived_roster=[str(x) for x in list(raw.get("derived_roster") or [])],
            schema_version=str(raw.get("schema_version") or SHELF_SCHEMA_VERSION),
            created_at=raw.get("created_at"),
        )


def build_parameter_shelf(
    *,
    hours_of_operation: bytes,
    sales_projections: bytes,
    availability_raw: bytes,
    time_off_raw: bytes | None = None,
    averaging_packets: list[dict[str, Any]] | None = None,
    demand_override_raw: bytes | None = None,
    staffing: StaffingCurve | None = None,
    availability: list[AvailabilityWindow] | None = None,
    created_at: str | None = None,
) -> ParameterShelf:
    """Build a named shelf from draft inputs. Hashes match ingest content_hash."""
    curve = staffing or load_staffing()
    hours_hash = content_hash(hours_of_operation)
    sales_hash = content_hash(sales_projections)
    avail_hash = content_hash(availability_raw)
    to_hash = content_hash(time_off_raw) if time_off_raw is not None else None
    pkt_hash = packets_content_hash(averaging_packets)
    dem_hash = (
        content_hash(demand_override_raw) if demand_override_raw is not None else None
    )
    roster = roster_from_availability(
        availability, availability_raw=availability_raw
    )
    parts = {
        "hours_of_operation": hours_hash,
        "sales_projections": sales_hash,
        "availability": avail_hash,
        "time_off": to_hash,
        "averaging_packets": pkt_hash,
        "demand_override": dem_hash,
        "staffing": curve.content_hash,
        "staffing_version": curve.version,
    }
    shelf_id = _make_shelf_id(parts)
    return ParameterShelf(
        parameter_shelf_id=shelf_id,
        staffing_version=curve.version,
        staffing_hash=curve.content_hash,
        hours_of_operation_hash=hours_hash,
        sales_projections_hash=sales_hash,
        availability_hash=avail_hash,
        time_off_hash=to_hash,
        averaging_packets_hash=pkt_hash,
        demand_override_hash=dem_hash,
        derived_roster=roster,
        created_at=created_at or _utc_now(),
    )


def observed_input_hashes(
    *,
    hours_of_operation: bytes,
    sales_projections: bytes,
    availability_raw: bytes,
    time_off_raw: bytes | None = None,
    averaging_packets: list[dict[str, Any]] | None = None,
    demand_override_raw: bytes | None = None,
    staffing: StaffingCurve | None = None,
) -> dict[str, str | None]:
    """Hashes of the inputs about to compose — compared to the bound shelf."""
    curve = staffing or load_staffing()
    return {
        "hours_of_operation": content_hash(hours_of_operation),
        "sales_projections": content_hash(sales_projections),
        "availability": content_hash(availability_raw),
        "time_off": content_hash(time_off_raw) if time_off_raw is not None else None,
        "averaging_packets": packets_content_hash(averaging_packets),
        "demand_override": (
            content_hash(demand_override_raw)
            if demand_override_raw is not None
            else None
        ),
        "staffing": curve.content_hash,
    }


def shelf_mismatches(
    shelf: ParameterShelf,
    observed: dict[str, str | None],
) -> list[str]:
    """Field names whose observed hash diverges from the bound shelf."""
    expected = shelf.content_hashes()
    bad: list[str] = []
    for key, want in expected.items():
        got = observed.get(key)
        if want != got:
            bad.append(key)
    return bad


def enforce_shelf_authority(
    shelf: ParameterShelf,
    observed: dict[str, str | None],
    *,
    chain: AuditChain,
) -> bool:
    """Hard-refuse when draft inputs diverge from the bound shelf. Returns True if ok."""
    bad = shelf_mismatches(shelf, observed)
    if not bad:
        return True
    chain.append(
        kind="rule_refuse",
        actor=f"rule:{SHELF_MISMATCH_RULE}",
        subject={
            "rule_id": SHELF_MISMATCH_RULE,
            "parameter_shelf_id": shelf.parameter_shelf_id,
            "mismatched_fields": bad,
        },
        evidence={
            "section": SHELF_MISMATCH_SECTION,
            "detail": (
                "draft inputs diverge from bound parameter shelf; "
                "current shelf is hard authority"
            ),
            "expected": {k: shelf.content_hashes().get(k) for k in bad},
            "observed": {k: observed.get(k) for k in bad},
        },
    )
    return False


class ParameterShelfStore:
    """Disk-backed named shelves under artifacts/parameters/."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_shelf_root()
        self.shelves_dir = self.root / "shelves"
        self.active_path = self.root / "active.json"

    def ensure_layout(self) -> None:
        self.shelves_dir.mkdir(parents=True, exist_ok=True)

    def shelf_path(self, parameter_shelf_id: str) -> Path:
        return self.shelves_dir / parameter_shelf_id / "shelf.json"

    def save(self, shelf: ParameterShelf, *, set_active: bool = True) -> Path:
        self.ensure_layout()
        path = self.shelf_path(shelf.parameter_shelf_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(shelf.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if set_active:
            self.active_path.write_text(
                json.dumps(
                    {
                        "parameter_shelf_id": shelf.parameter_shelf_id,
                        "path": str(path.relative_to(self.root)),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        return path

    def load(self, parameter_shelf_id: str) -> ParameterShelf:
        path = self.shelf_path(parameter_shelf_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ParameterShelf.from_dict(raw)

    def load_active(self) -> ParameterShelf | None:
        if not self.active_path.is_file():
            return None
        pointer = json.loads(self.active_path.read_text(encoding="utf-8"))
        shelf_id = pointer.get("parameter_shelf_id")
        if not shelf_id:
            return None
        return self.load(str(shelf_id))
