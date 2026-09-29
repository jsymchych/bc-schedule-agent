"""Human gates: time-off decide and OT approve/refuse. Agent has no approve action."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import ComposeResult, OvertimeProposal, TimeOffRequest

TimeOffDecision = Literal["approve", "deny"]


class GateError(ValueError):
    """Refuse malformed or agent-side approve attempts."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_human_name(human_name: str) -> str:
    name = (human_name or "").strip()
    if not name:
        raise GateError("named human required")
    if name.lower() in {"agent", "system", "rule"}:
        raise GateError("agent has no approve action")
    return name


def decide_time_off(
    request: TimeOffRequest,
    *,
    decision: TimeOffDecision,
    human_name: str,
    chain: AuditChain,
    timestamp: str | None = None,
) -> TimeOffRequest:
    """Human decides a PENDING time-off row. APPROVED blocks place; DENIED ignored."""
    name = _require_human_name(human_name)
    if decision not in ("approve", "deny"):
        raise GateError(f"unknown time-off decision: {decision!r}")
    if request.status != "PENDING":
        raise GateError(
            f"time-off {request.request_id} is {request.status}, expected PENDING"
        )

    ts = timestamp or _utc_now()
    new_status = "APPROVED" if decision == "approve" else "DENIED"
    request.status = new_status
    chain.append(
        kind="timeoff_decided",
        actor=f"human:{name}",
        subject={
            "request_id": request.request_id,
            "employee": request.employee,
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
        },
        evidence={
            "decision": decision,
            "status": new_status,
            "human_name": name,
            "timestamp": ts,
        },
        timestamp=ts,
    )
    return request


def approve_ot(
    proposal: OvertimeProposal,
    *,
    human_name: str,
    chain: AuditChain,
    timestamp: str | None = None,
) -> OvertimeProposal:
    """Named human approves a PENDING_APPROVAL OT / rest line."""
    name = _require_human_name(human_name)
    if proposal.status != "PENDING_APPROVAL":
        raise GateError(
            f"OT {proposal.proposal_id} is {proposal.status}, expected PENDING_APPROVAL"
        )

    ts = timestamp or _utc_now()
    proposal.status = "APPROVED"
    proposal.decided_by = name
    proposal.decided_at = ts
    chain.append(
        kind="ot_approved",
        actor=f"human:{name}",
        subject={
            "proposal_id": proposal.proposal_id,
            "employee": proposal.employee,
            "date": proposal.date.isoformat(),
            "shift_id": proposal.shift_id,
        },
        evidence={
            "hours": proposal.hours,
            "multiplier": proposal.multiplier,
            "rule_id": proposal.rule_id,
            "section": proposal.section,
            "status": "APPROVED",
            "human_name": name,
            "timestamp": ts,
        },
        timestamp=ts,
    )
    return proposal


@dataclass(frozen=True)
class OtRefuseResult:
    """Refuse returns the line to the composer for repair / re-place."""

    proposal: OvertimeProposal
    returned_to_composer: dict[str, Any]


def refuse_ot(
    proposal: OvertimeProposal,
    *,
    human_name: str,
    chain: AuditChain,
    result: ComposeResult | None = None,
    timestamp: str | None = None,
) -> OtRefuseResult:
    """Named human refuses OT. Line returns to composer with ot_refused on the chain."""
    name = _require_human_name(human_name)
    if proposal.status != "PENDING_APPROVAL":
        raise GateError(
            f"OT {proposal.proposal_id} is {proposal.status}, expected PENDING_APPROVAL"
        )

    ts = timestamp or _utc_now()
    proposal.status = "REFUSED"
    proposal.decided_by = name
    proposal.decided_at = ts
    chain.append(
        kind="ot_refused",
        actor=f"human:{name}",
        subject={
            "proposal_id": proposal.proposal_id,
            "employee": proposal.employee,
            "date": proposal.date.isoformat(),
            "shift_id": proposal.shift_id,
        },
        evidence={
            "hours": proposal.hours,
            "multiplier": proposal.multiplier,
            "rule_id": proposal.rule_id,
            "section": proposal.section,
            "status": "REFUSED",
            "human_name": name,
            "timestamp": ts,
            "returned_to_composer": True,
        },
        timestamp=ts,
    )

    returned: dict[str, Any] = {
        "proposal_id": proposal.proposal_id,
        "employee": proposal.employee,
        "date": proposal.date.isoformat(),
        "hours": proposal.hours,
        "multiplier": proposal.multiplier,
        "rule_id": proposal.rule_id,
        "section": proposal.section,
        "shift_id": proposal.shift_id,
        "status": "ot_refused",
    }

    if result is not None:
        # Drop refused line from active proposals; pull related place for re-compose.
        result.ot_proposals = [
            p for p in result.ot_proposals if p.proposal_id != proposal.proposal_id
        ]
        if proposal.shift_id:
            pulled = [p for p in result.placed if p.shift_id == proposal.shift_id]
            result.placed = [p for p in result.placed if p.shift_id != proposal.shift_id]
            if pulled:
                returned["pulled_placements"] = [
                    {
                        "shift_id": p.shift_id,
                        "employee": p.employee,
                        "date": p.date.isoformat(),
                    }
                    for p in pulled
                ]
        result.refused.append({"rule_id": "ot_refused", **returned})

    return OtRefuseResult(proposal=proposal, returned_to_composer=returned)


def agent_approve_ot(*_args: Any, **_kwargs: Any) -> None:
    """Explicit non-path: the agent cannot approve OT."""
    raise GateError("agent has no approve action")
