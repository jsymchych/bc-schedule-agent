"""Replay gate: same inputs + demand + ruleset hash → same issued schedule hash."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.export import schedule_hash
from bc_schedule_agent.ingest import (
    content_hash,
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.models import ComposeResult
from bc_schedule_agent.ruleset import load_ruleset


class ReplayError(ValueError):
    """Replay inputs are incomplete or inconsistent."""


class ReplayMismatch(ReplayError):
    """Rebuilt schedule hash does not match the issued hash."""


@dataclass(frozen=True)
class ReplayInputs:
    """Inputs required to rebuild an issued week."""

    availability_raw: bytes
    demand: dict[str, Any]
    ruleset_hash: str
    time_off_raw: bytes | None = None
    averaging_packets: tuple[dict[str, Any], ...] = ()
    availability_hash: str | None = None
    demand_hash: str | None = None
    time_off_hash: str | None = None


@dataclass(frozen=True)
class ReplayResult:
    matched: bool
    schedule_hash: str
    expected_schedule_hash: str
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
    )
    return result, chain


def replay(
    inputs: ReplayInputs,
    *,
    expected_schedule_hash: str,
) -> ReplayResult:
    """Rebuild and require the schedule hash to match `issued`."""
    if not expected_schedule_hash:
        raise ReplayError("expected_schedule_hash is required")

    result, chain = rebuild_week(inputs)
    rebuilt = schedule_hash(result.placed)
    pending = sum(1 for p in result.ot_proposals if p.status == "PENDING_APPROVAL")
    refuse_count = sum(1 for e in chain.events if e.kind == "rule_refuse")
    matched = rebuilt == expected_schedule_hash
    if not matched:
        raise ReplayMismatch(
            f"schedule hash mismatch: issued={expected_schedule_hash}, "
            f"replay={rebuilt}"
        )
    return ReplayResult(
        matched=True,
        schedule_hash=rebuilt,
        expected_schedule_hash=expected_schedule_hash,
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


def week_start_from_demand(demand: dict[str, Any]) -> date:
    return date.fromisoformat(str(demand["week_start"]))
