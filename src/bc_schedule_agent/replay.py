"""Dual-plane replay: placement schedule hash + gate snapshot (OT / time-off)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.export import (
    empty_gate_snapshot,
    gate_snapshot_hash,
    schedule_hash,
)
from bc_schedule_agent.ingest import (
    content_hash,
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.models import ComposeResult
from bc_schedule_agent.ruleset import load_ruleset

ReplayPlane = Literal["placement", "gate"]

ENVELOPE_SCHEMA_VERSION = "1"
REPLAY_INPUTS_FILENAME = "replay_inputs.json"


class ReplayError(ValueError):
    """Replay inputs are incomplete or inconsistent."""


class ReplayMismatch(ReplayError):
    """Rebuilt evidence does not match the issued plane."""

    def __init__(self, message: str, *, plane: ReplayPlane) -> None:
        super().__init__(message)
        self.plane = plane


@dataclass(frozen=True)
class ReplayInputs:
    """Inputs required to rebuild an issued week (placement + optional gate plane)."""

    availability_raw: bytes
    demand: dict[str, Any]
    ruleset_hash: str
    time_off_raw: bytes | None = None
    averaging_packets: tuple[dict[str, Any], ...] = ()
    availability_hash: str | None = None
    demand_hash: str | None = None
    time_off_hash: str | None = None
    gate_snapshot: dict[str, Any] | None = None
    prefer_zero_ot: bool = False


@dataclass(frozen=True)
class ReplayResult:
    matched: bool
    schedule_hash: str
    expected_schedule_hash: str
    gate_snapshot_hash: str
    expected_gate_snapshot_hash: str
    placed_count: int
    refuse_count: int
    pending_ot_count: int
    compose: ComposeResult
    chain: AuditChain


def _verify_optional_hash(raw: bytes, expected: str | None, label: str) -> None:
    if expected is None:
        return
    actual = content_hash(raw)
    if actual != expected:
        raise ReplayError(f"{label} hash mismatch: expected {expected}, got {actual}")


def _normalize_gate_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return empty_gate_snapshot()
    ot = list(snapshot.get("ot_approvals") or [])
    toff = list(snapshot.get("timeoff_decisions") or [])
    ot.sort(
        key=lambda row: (
            str(row.get("proposal_id") or ""),
            str(row.get("timestamp") or ""),
        )
    )
    toff.sort(
        key=lambda row: (
            str(row.get("request_id") or ""),
            str(row.get("timestamp") or ""),
        )
    )
    return {"ot_approvals": ot, "timeoff_decisions": toff}


def rebuild_week(inputs: ReplayInputs) -> tuple[ComposeResult, AuditChain]:
    """Rebuild a week from stored bytes + demand + current ruleset hash check."""
    ruleset = load_ruleset()
    if ruleset.content_hash != inputs.ruleset_hash:
        raise ReplayError(
            f"ruleset hash mismatch: expected {inputs.ruleset_hash}, "
            f"got {ruleset.content_hash}"
        )

    _verify_optional_hash(
        inputs.availability_raw, inputs.availability_hash, "availability"
    )
    demand_raw = json.dumps(
        inputs.demand, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    _verify_optional_hash(demand_raw, inputs.demand_hash, "demand")
    if inputs.time_off_raw is not None:
        _verify_optional_hash(inputs.time_off_raw, inputs.time_off_hash, "time_off")

    chain = AuditChain()
    availability = parse_availability_sheet(inputs.availability_raw, chain=chain)
    time_off = (
        parse_time_off_sheet(inputs.time_off_raw, chain=chain)
        if inputs.time_off_raw is not None
        else []
    )
    packets = [
        parse_averaging_packet(payload, chain=chain)
        for payload in inputs.averaging_packets
    ]
    demand = parse_coverage_demand(inputs.demand, chain=chain)
    result = compose_week(
        demand,
        availability=availability,
        time_off=time_off,
        chain=chain,
        averaging_packets=packets or None,
        prefer_zero_ot=inputs.prefer_zero_ot,
    )
    return result, chain


def replay(
    inputs: ReplayInputs,
    *,
    expected_schedule_hash: str,
    expected_gate_snapshot: dict[str, Any] | None = None,
) -> ReplayResult:
    """Rebuild and dual-check placement hash + gate snapshot against `issued`."""
    if not expected_schedule_hash:
        raise ReplayError("expected_schedule_hash is required")

    result, chain = rebuild_week(inputs)
    rebuilt = schedule_hash(result.placed)
    pending = sum(1 for p in result.ot_proposals if p.status == "PENDING_APPROVAL")
    refuse_count = sum(1 for e in chain.events if e.kind == "rule_refuse")

    if rebuilt != expected_schedule_hash:
        raise ReplayMismatch(
            f"placement plane mismatch: issued={expected_schedule_hash}, "
            f"replay={rebuilt}",
            plane="placement",
        )

    actual_gate = _normalize_gate_snapshot(inputs.gate_snapshot)
    expected_gate = _normalize_gate_snapshot(expected_gate_snapshot)
    actual_gate_hash = gate_snapshot_hash(actual_gate)
    expected_gate_hash = gate_snapshot_hash(expected_gate)
    if actual_gate_hash != expected_gate_hash:
        raise ReplayMismatch(
            f"gate plane mismatch: issued={expected_gate_hash}, "
            f"replay={actual_gate_hash}",
            plane="gate",
        )

    return ReplayResult(
        matched=True,
        schedule_hash=rebuilt,
        expected_schedule_hash=expected_schedule_hash,
        gate_snapshot_hash=actual_gate_hash,
        expected_gate_snapshot_hash=expected_gate_hash,
        placed_count=len(result.placed),
        refuse_count=refuse_count,
        pending_ot_count=pending,
        compose=result,
        chain=chain,
    )


def issued_schedule_hash(chain: AuditChain) -> str:
    """Read schedule_hash from the `issued` event."""
    issued = next((e for e in chain.events if e.kind == "issued"), None)
    if issued is None:
        raise ReplayError("no issued event on chain")
    value = issued.evidence.get("schedule_hash")
    if not isinstance(value, str) or not value:
        raise ReplayError("issued event missing schedule_hash")
    return value


def issued_gate_snapshot(chain: AuditChain) -> dict[str, Any]:
    """Read gate_snapshot from the `issued` event."""
    issued = next((e for e in chain.events if e.kind == "issued"), None)
    if issued is None:
        raise ReplayError("no issued event on chain")
    value = issued.evidence.get("gate_snapshot")
    if not isinstance(value, dict):
        raise ReplayError("issued event missing gate_snapshot")
    return _normalize_gate_snapshot(value)


def week_start_from_demand(demand: dict[str, Any]) -> date:
    return date.fromisoformat(str(demand["week_start"]))


def _b64_encode(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    return base64.b64encode(raw).decode("ascii")


def _b64_decode(value: str | None) -> bytes | None:
    if value is None:
        return None
    return base64.b64decode(value.encode("ascii"))


def replay_inputs_to_envelope(inputs: ReplayInputs) -> dict[str, Any]:
    """Serialize ReplayInputs for disk (raw sheets as base64)."""
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "availability_raw_b64": _b64_encode(inputs.availability_raw),
        "demand": inputs.demand,
        "ruleset_hash": inputs.ruleset_hash,
        "time_off_raw_b64": _b64_encode(inputs.time_off_raw),
        "averaging_packets": list(inputs.averaging_packets),
        "availability_hash": inputs.availability_hash,
        "demand_hash": inputs.demand_hash,
        "time_off_hash": inputs.time_off_hash,
        "gate_snapshot": (
            _normalize_gate_snapshot(inputs.gate_snapshot)
            if inputs.gate_snapshot is not None
            else None
        ),
        "prefer_zero_ot": bool(inputs.prefer_zero_ot),
    }


def replay_inputs_from_envelope(raw: dict[str, Any]) -> ReplayInputs:
    """Restore ReplayInputs from a disk envelope dict."""
    if not isinstance(raw, dict):
        raise ReplayError("envelope must be an object")
    avail_b64 = raw.get("availability_raw_b64")
    if not isinstance(avail_b64, str) or not avail_b64:
        raise ReplayError("envelope missing availability_raw_b64")
    demand = raw.get("demand")
    if not isinstance(demand, dict):
        raise ReplayError("envelope missing demand object")
    ruleset_hash = raw.get("ruleset_hash")
    if not isinstance(ruleset_hash, str) or not ruleset_hash:
        raise ReplayError("envelope missing ruleset_hash")
    packets = raw.get("averaging_packets") or []
    if not isinstance(packets, list):
        raise ReplayError("envelope averaging_packets must be a list")
    gate = raw.get("gate_snapshot")
    if gate is not None and not isinstance(gate, dict):
        raise ReplayError("envelope gate_snapshot must be an object or null")
    return ReplayInputs(
        availability_raw=_b64_decode(avail_b64) or b"",
        demand=demand,
        ruleset_hash=ruleset_hash,
        time_off_raw=_b64_decode(raw.get("time_off_raw_b64")),
        averaging_packets=tuple(packets),
        availability_hash=raw.get("availability_hash"),
        demand_hash=raw.get("demand_hash"),
        time_off_hash=raw.get("time_off_hash"),
        gate_snapshot=_normalize_gate_snapshot(gate) if gate is not None else None,
        prefer_zero_ot=bool(raw.get("prefer_zero_ot", False)),
    )


def write_replay_envelope(path: Path, inputs: ReplayInputs) -> Path:
    """Write replay_inputs.json beside week exhibits."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = replay_inputs_to_envelope(inputs)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_replay_envelope(path: Path) -> ReplayInputs:
    """Load ReplayInputs from a replay_inputs.json path."""
    path = Path(path)
    if not path.is_file():
        raise ReplayError(f"replay envelope not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return replay_inputs_from_envelope(raw)


def with_gate_snapshot(
    inputs: ReplayInputs, gate_snapshot: dict[str, Any] | None
) -> ReplayInputs:
    """Return inputs with a frozen gate snapshot (issued plane)."""
    return replace(
        inputs,
        gate_snapshot=(
            _normalize_gate_snapshot(gate_snapshot)
            if gate_snapshot is not None
            else None
        ),
    )


def reopen_from_week_dir(
    week_dir: Path,
    *,
    expected_schedule_hash: str | None = None,
    expected_gate_snapshot: dict[str, Any] | None = None,
) -> ReplayResult:
    """Restore ReplayInputs from week-dir envelope and dual-plane check.

    Looks for ``replay_inputs.json`` under ``week_dir``. When expected hashes
    are omitted, uses ``record.json`` schedule_hash and the envelope's own
    gate_snapshot (reopen identity check).
    """
    week_dir = Path(week_dir)
    envelope_path = week_dir / REPLAY_INPUTS_FILENAME
    inputs = load_replay_envelope(envelope_path)

    schedule_hash_value = expected_schedule_hash
    if schedule_hash_value is None:
        record_path = week_dir / "record.json"
        if not record_path.is_file():
            raise ReplayError(
                f"week dir missing record.json and no expected_schedule_hash: {week_dir}"
            )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        schedule_hash_value = record.get("schedule_hash")
        if not isinstance(schedule_hash_value, str) or not schedule_hash_value:
            raise ReplayError("record.json missing schedule_hash")

    gate_expected = expected_gate_snapshot
    if gate_expected is None:
        gate_expected = inputs.gate_snapshot

    return replay(
        inputs,
        expected_schedule_hash=schedule_hash_value,
        expected_gate_snapshot=gate_expected,
    )
