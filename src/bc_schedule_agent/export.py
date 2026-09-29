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


def issue_schedule(
    result: ComposeResult,
    *,
    chain: AuditChain,
    decision_id: str,
    ruleset_version: str,
    ruleset_hash: str | None = None,
    actor: str = "agent",
    timestamp: str | None = None,
) -> IssueResult:
    """Write `issued` only when every OT line is clear of PENDING_APPROVAL."""
    assert_no_pending_ot(result.ot_proposals)
    ts = timestamp or _utc_now()
    shash = schedule_hash(result.placed)
    evidence: dict[str, Any] = {
        "schedule_hash": shash,
        "ruleset_version": ruleset_version,
        "placed_count": len(result.placed),
        "ot_approved_count": sum(
            1 for p in result.ot_proposals if p.status == "APPROVED"
        ),
        "legal_posture": LEGAL_POSTURE,
        "statute_url": STATUTE_URL,
        "timestamp": ts,
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
