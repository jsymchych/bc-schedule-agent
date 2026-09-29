"""Wave C: OT-min two-pass compose and approve_ot reason gate."""

from __future__ import annotations

from datetime import date

import pytest

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import event_to_prose
from bc_schedule_agent.export import ExportBlocked, issue_schedule, pending_ot_lines
from bc_schedule_agent.gates import GateError, approve_ot
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def test_zero_ot_week_issues() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv(
            "sam,2026-09-28,08:00,18:00",
            "jordan,2026-09-29,08:00,18:00",
        ),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_mon",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                },
                {
                    "shift_id": "sh_tue",
                    "employee": "jordan",
                    "date": "2026-09-29",
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                },
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    assert len(result.placed) == 2
    assert result.ot_proposals == []
    assert pending_ot_lines(result.ot_proposals) == []
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_zero_ot",
        ruleset_version="bc-esa-demo-v1",
    )
    assert issued.decision_id == "dec_zero_ot"
    assert not any(e.kind == "ot_unavoidable" for e in chain.events)


def test_peak_needs_ot_unavoidable() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,06:00,20:00"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_peak",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "18:30",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    assert len(result.placed) == 1
    assert len(result.ot_proposals) >= 1
    assert all(p.status == "PENDING_APPROVAL" for p in result.ot_proposals)
    unavoidable = [e for e in chain.events if e.kind == "ot_unavoidable"]
    assert len(unavoidable) == 1
    assert "reason_unavoidable" in unavoidable[0].evidence
    assert any(
        p.evidence.get("reason_unavoidable") for p in result.ot_proposals
    )

    with pytest.raises(ExportBlocked, match="PENDING_APPROVAL"):
        issue_schedule(
            result,
            chain=chain,
            decision_id="dec_peak",
            ruleset_version="bc-esa-demo-v1",
        )

    reason = "Peak close: only Sam available for the full window"
    approve_ot(
        result.ot_proposals[0],
        human_name="Alex Rivera",
        reason=reason,
        chain=chain,
        timestamp="2026-09-29T18:00:00Z",
    )
    # Approve remaining bands if any
    for proposal in list(pending_ot_lines(result.ot_proposals)):
        approve_ot(
            proposal,
            human_name="Alex Rivera",
            reason=reason,
            chain=chain,
            timestamp="2026-09-29T18:00:00Z",
        )
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_peak",
        ruleset_version="bc-esa-demo-v1",
    )
    assert issued.decision_id == "dec_peak"
    approved = [e for e in chain.events if e.kind == "ot_approved"]
    assert approved
    assert approved[0].evidence["reason"] == reason
    prose = event_to_prose(approved[0])
    assert reason in prose


def test_alternate_employee_avoids_ot() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv(
            "sam,2026-09-28,06:00,20:00",
            "jordan,2026-09-28,06:00,20:00",
        ),
        chain=chain,
    )
    # Two full shifts both preferred to sam — Pass A repairs second onto jordan.
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_a",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "16:30",
                    "meal_break_minutes": 30,
                },
                {
                    "shift_id": "sh_b",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "16:30",
                    "meal_break_minutes": 30,
                },
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    assert len(result.placed) == 2
    employees = sorted(p.employee for p in result.placed)
    assert employees == ["jordan", "sam"]
    assert result.ot_proposals == []
    repairs = [e for e in chain.events if e.kind == "repair"]
    assert repairs
    assert "jordan" in repairs[0].evidence["detail"]
    issue_schedule(
        result,
        chain=chain,
        decision_id="dec_alternate",
        ruleset_version="bc-esa-demo-v1",
    )


def test_approve_ot_without_reason_fails() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,06:00,20:00"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_ot",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "18:30",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    proposal = result.ot_proposals[0]
    with pytest.raises(GateError, match="non-empty reason"):
        approve_ot(proposal, human_name="Alex Rivera", reason="", chain=chain)
    with pytest.raises(GateError, match="non-empty reason"):
        approve_ot(proposal, human_name="Alex Rivera", reason="   ", chain=chain)
    assert proposal.status == "PENDING_APPROVAL"
    assert proposal.reason is None


def test_split_slots_zero_ot_when_alternate_covers_tail() -> None:
    """Long single ask + two people → Pass A split keeps straight time."""
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv(
            "sam,2026-09-28,06:00,20:00",
            "jordan,2026-09-28,06:00,20:00",
        ),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_long",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "18:30",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    assert len(result.placed) == 2
    assert result.ot_proposals == []
    assert any(e.kind == "repair" for e in chain.events)
    assert not any(e.kind == "ot_unavoidable" for e in chain.events)
