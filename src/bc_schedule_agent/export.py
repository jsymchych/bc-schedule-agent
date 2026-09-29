"""Issue and export gate. Refuse while any OT line is PENDING_APPROVAL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import ComposeResult, OvertimeProposal, PlacedShift

STATUTE_URL = (
    "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01"
)
LEGAL_POSTURE = (
    "Decision support under the Employment Standards Act, not legal advice. "
    f"{STATUTE_URL}"
)


class ExportBlocked(ValueError):
    """Downloads / issued stay off while OT waits for a named human."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pending_ot_lines(proposals: list[OvertimeProposal]) -> list[OvertimeProposal]:
    return [p for p in proposals if p.status == "PENDING_APPROVAL"]


def assert_no_pending_ot(proposals: list[OvertimeProposal]) -> None:
    pending = pending_ot_lines(proposals)
    if pending:
        ids = ", ".join(p.proposal_id for p in pending)
        raise ExportBlocked(
            f"export blocked: {len(pending)} OT line(s) PENDING_APPROVAL ({ids})"
        )


def schedule_hash(placed: list[PlacedShift]) -> str:
    payload = [
        {
            "shift_id": p.shift_id,
            "employee": p.employee,
            "date": p.date.isoformat(),
            "start": p.start.isoformat(timespec="minutes"),
            "end": p.end.isoformat(timespec="minutes"),
            "worked_hours": p.worked_hours,
        }
        for p in sorted(placed, key=lambda s: (s.date, s.employee, s.shift_id))
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True)
class IssueResult:
    decision_id: str
    schedule_hash: str
    event_id: str
    gate_snapshot: dict[str, Any]


def empty_gate_snapshot() -> dict[str, Any]:
    """Canonical empty gate plane (no OT approvals, no time-off decisions)."""
    return {"ot_approvals": [], "timeoff_decisions": []}


def build_gate_snapshot(chain: AuditChain) -> dict[str, Any]:
    """Freeze OT who/reason/ts and time-off decisions for dual-plane replay."""
    ot_approvals: list[dict[str, Any]] = []
    timeoff_decisions: list[dict[str, Any]] = []
    for event in chain.events:
        if event.kind == "ot_approved":
            ot_approvals.append(
                {
                    "proposal_id": event.subject.get("proposal_id"),
                    "employee": event.subject.get("employee"),
                    "date": event.subject.get("date"),
                    "shift_id": event.subject.get("shift_id"),
                    "human_name": event.evidence.get("human_name"),
                    "reason": event.evidence.get("reason"),
                    "timestamp": event.evidence.get("timestamp"),
                    "hours": event.evidence.get("hours"),
                    "multiplier": event.evidence.get("multiplier"),
                    "rule_id": event.evidence.get("rule_id"),
                    "section": event.evidence.get("section"),
                }
            )
        elif event.kind == "timeoff_decided":
            timeoff_decisions.append(
                {
                    "request_id": event.subject.get("request_id"),
                    "employee": event.subject.get("employee"),
                    "start": event.subject.get("start"),
                    "end": event.subject.get("end"),
                    "decision": event.evidence.get("decision"),
                    "status": event.evidence.get("status"),
                    "human_name": event.evidence.get("human_name"),
                    "timestamp": event.evidence.get("timestamp"),
                }
            )
    ot_approvals.sort(
        key=lambda row: (
            str(row.get("proposal_id") or ""),
            str(row.get("timestamp") or ""),
        )
    )
    timeoff_decisions.sort(
        key=lambda row: (
            str(row.get("request_id") or ""),
            str(row.get("timestamp") or ""),
        )
    )
    return {
        "ot_approvals": ot_approvals,
        "timeoff_decisions": timeoff_decisions,
    }


def gate_snapshot_hash(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def issue_schedule(
    result: ComposeResult,
    *,
    chain: AuditChain,
    decision_id: str,
    ruleset_version: str,
    ruleset_hash: str | None = None,
    actor: str = "agent",
    timestamp: str | None = None,
    parameter_shelf_id: str | None = None,
    history_prior_week_starts: list[str] | None = None,
) -> IssueResult:
    """Write `issued` only when every OT line is clear of PENDING_APPROVAL."""
    assert_no_pending_ot(result.ot_proposals)
    ts = timestamp or _utc_now()
    shash = schedule_hash(result.placed)
    snapshot = build_gate_snapshot(chain)
    evidence: dict[str, Any] = {
        "schedule_hash": shash,
        "gate_snapshot": snapshot,
        "gate_snapshot_hash": gate_snapshot_hash(snapshot),
        "ruleset_version": ruleset_version,
        "placed_count": len(result.placed),
        "ot_approved_count": sum(
            1 for p in result.ot_proposals if p.status == "APPROVED"
        ),
        "legal_posture": LEGAL_POSTURE,
        "statute_url": STATUTE_URL,
        "timestamp": ts,
        # Wave C: shelf id + prior week starts (empty until history / Wave D).
        "parameter_shelf_id": parameter_shelf_id,
        "history_prior_week_starts": list(history_prior_week_starts or []),
    }
    if ruleset_hash is not None:
        evidence["ruleset_hash"] = ruleset_hash
    event = chain.append(
        kind="issued",
        actor=actor,
        subject={"decision_id": decision_id},
        evidence=evidence,
        timestamp=ts,
    )
    return IssueResult(
        decision_id=decision_id,
        schedule_hash=shash,
        event_id=event.event_id,
        gate_snapshot=snapshot,
    )


def build_pdf_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Delegate to exhibit renderer (Wave D). Extra kwargs accepted for forward compat."""
    from bc_schedule_agent.exhibit import build_pdf_exhibit as _build

    return _build(
        result,
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        **kwargs,
    )


def build_xlsx_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Delegate to exhibit renderer (Wave D). Extra kwargs accepted for forward compat."""
    from bc_schedule_agent.exhibit import build_xlsx_exhibit as _build

    return _build(
        result,
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        **kwargs,
    )


def proposal_snapshot(proposal: OvertimeProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "employee": proposal.employee,
        "date": proposal.date.isoformat(),
        "hours": proposal.hours,
        "multiplier": proposal.multiplier,
        "rule_id": proposal.rule_id,
        "section": proposal.section,
        "status": proposal.status,
        "shift_id": proposal.shift_id,
    }
