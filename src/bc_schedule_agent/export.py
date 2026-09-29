"""Issue and exhibit builders. Refuse while any OT line is PENDING_APPROVAL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import ComposeResult, OvertimeProposal, PlacedShift


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


def issue_schedule(
    result: ComposeResult,
    *,
    chain: AuditChain,
    decision_id: str,
    ruleset_version: str,
    actor: str = "agent",
    timestamp: str | None = None,
) -> IssueResult:
    """Write `issued` only when every OT line is clear of PENDING_APPROVAL."""
    assert_no_pending_ot(result.ot_proposals)
    ts = timestamp or _utc_now()
    shash = schedule_hash(result.placed)
    event = chain.append(
        kind="issued",
        actor=actor,
        subject={"decision_id": decision_id},
        evidence={
            "schedule_hash": shash,
            "ruleset_version": ruleset_version,
            "placed_count": len(result.placed),
            "ot_approved_count": sum(
                1 for p in result.ot_proposals if p.status == "APPROVED"
            ),
            "legal_posture": (
                "Decision support under the Employment Standards Act, "
                "not legal advice. "
                "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/00_96113_01"
            ),
            "timestamp": ts,
        },
        timestamp=ts,
    )
    return IssueResult(
        decision_id=decision_id,
        schedule_hash=shash,
        event_id=event.event_id,
    )


def build_pdf_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
) -> dict[str, Any]:
    """Wave C stub: PDF builder refuses while any OT line is pending."""
    assert_no_pending_ot(result.ot_proposals)
    return {
        "format": "pdf",
        "decision_id": decision_id,
        "ruleset_version": ruleset_version,
        "schedule_hash": schedule_hash(result.placed),
        "bytes": b"%PDF-stub",
        "downloadable": True,
    }


def build_xlsx_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
) -> dict[str, Any]:
    """Wave C stub: XLSX builder refuses while any OT line is pending."""
    assert_no_pending_ot(result.ot_proposals)
    return {
        "format": "xlsx",
        "decision_id": decision_id,
        "ruleset_version": ruleset_version,
        "schedule_hash": schedule_hash(result.placed),
        "rows": [
            {
                "shift_id": p.shift_id,
                "employee": p.employee,
                "date": p.date.isoformat(),
                "start": p.start.isoformat(timespec="minutes"),
                "end": p.end.isoformat(timespec="minutes"),
                "worked_hours": p.worked_hours,
            }
            for p in result.placed
        ],
        "ot_lines": [
            {
                "proposal_id": p.proposal_id,
                "employee": p.employee,
                "date": p.date.isoformat(),
                "hours": p.hours,
                "multiplier": p.multiplier,
                "section": p.section,
                "status": p.status,
            }
            for p in result.ot_proposals
        ],
        "downloadable": True,
    }


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
