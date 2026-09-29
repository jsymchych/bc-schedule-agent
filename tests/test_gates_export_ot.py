"""Wave C: human gates, blocked export, OT approve/refuse."""

from __future__ import annotations

from datetime import date

import pytest

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.export import (
    ExportBlocked,
    build_pdf_exhibit,
    build_xlsx_exhibit,
    issue_schedule,
    pending_ot_lines,
)
from bc_schedule_agent.gates import (
    GateError,
    agent_approve_ot,
    approve_ot,
    decide_time_off,
    refuse_ot,
)
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_coverage_demand,
    parse_time_off_sheet,
)

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _timeoff_csv(*rows: str) -> bytes:
    header = "request_id,employee,start,end,status\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _ot_week() -> tuple[AuditChain, object]:
    """Compose a 10h day that produces one PENDING_APPROVAL OT line."""
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
    result = compose_week(demand, availability=avail, time_off=[], chain=chain)
    return chain, result


def test_pending_time_off_awaits_human_gate() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,08:00,18:00"),
        chain=chain,
    )
    time_off = parse_time_off_sheet(
        _timeoff_csv("to_sam_mon,sam,2026-09-28,2026-09-28,PENDING"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_wait",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand, availability=avail, time_off=time_off, chain=chain
    )
    assert result.placed == []
    refuse = [e for e in chain.events if e.kind == "rule_refuse"]
    assert len(refuse) == 1
    assert refuse[0].evidence["detail"] == "pending time-off awaits human decision"
    assert refuse[0].subject["pending_request_id"] == "to_sam_mon"

    decide_time_off(
        time_off[0],
        decision="deny",
        human_name="Alex Rivera",
        chain=chain,
        timestamp="2026-09-29T12:00:00Z",
    )
    assert time_off[0].status == "DENIED"
    decided = [e for e in chain.events if e.kind == "timeoff_decided"]
    assert len(decided) == 1
    assert decided[0].actor == "human:Alex Rivera"
    assert decided[0].evidence["decision"] == "deny"
    assert decided[0].evidence["timestamp"] == "2026-09-29T12:00:00Z"

    # After deny, re-compose places the shift.
    chain2 = AuditChain()
    result2 = compose_week(
        demand, availability=avail, time_off=time_off, chain=chain2
    )
    assert len(result2.placed) == 1


def test_timeoff_decided_approve_blocks_place() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,08:00,18:00"),
        chain=chain,
    )
    time_off = parse_time_off_sheet(
        _timeoff_csv("to_sam_mon,sam,2026-09-28,2026-09-28,PENDING"),
        chain=chain,
    )
    decide_time_off(
        time_off[0],
        decision="approve",
        human_name="Jordan Lee",
        chain=chain,
    )
    assert time_off[0].status == "APPROVED"
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_block",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(
        demand, availability=avail, time_off=time_off, chain=chain
    )
    assert result.placed == []
    refuse = [
        e
        for e in chain.events
        if e.kind == "rule_refuse" and e.evidence.get("request_id") == "to_sam_mon"
    ]
    assert refuse
    assert refuse[-1].subject["blocking_request_id"] == "to_sam_mon"


def test_export_blocked_until_named_human_approves_ot() -> None:
    chain, result = _ot_week()
    assert len(result.ot_proposals) == 1
    assert result.ot_proposals[0].status == "PENDING_APPROVAL"
    assert pending_ot_lines(result.ot_proposals)

    with pytest.raises(ExportBlocked, match="PENDING_APPROVAL"):
        issue_schedule(
            result,
            chain=chain,
            decision_id="dec_demo_1",
            ruleset_version="bc-esa-demo-v1",
        )
    with pytest.raises(ExportBlocked, match="PENDING_APPROVAL"):
        build_pdf_exhibit(
            result, decision_id="dec_demo_1", ruleset_version="bc-esa-demo-v1"
        )
    with pytest.raises(ExportBlocked, match="PENDING_APPROVAL"):
        build_xlsx_exhibit(
            result, decision_id="dec_demo_1", ruleset_version="bc-esa-demo-v1"
        )
    assert not any(e.kind == "issued" for e in chain.events)

    proposal = result.ot_proposals[0]
    assert proposal.employee == "sam"
    assert proposal.hours == 2.0
    assert proposal.multiplier == 1.5
    assert proposal.rule_id
    assert proposal.section

    approve_ot(
        proposal,
        human_name="Sam Chen",
        reason="Peak Saturday: only Sam can cover the late close",
        chain=chain,
        timestamp="2026-09-29T15:00:00Z",
    )
    assert proposal.status == "APPROVED"
    assert proposal.decided_by == "Sam Chen"
    assert proposal.reason == "Peak Saturday: only Sam can cover the late close"
    approved = [e for e in chain.events if e.kind == "ot_approved"]
    assert len(approved) == 1
    assert approved[0].actor == "human:Sam Chen"
    assert approved[0].evidence["timestamp"] == "2026-09-29T15:00:00Z"
    assert approved[0].evidence["human_name"] == "Sam Chen"
    assert approved[0].evidence["reason"] == proposal.reason

    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_demo_1",
        ruleset_version="bc-esa-demo-v1",
    )
    assert issued.decision_id == "dec_demo_1"
    assert any(e.kind == "issued" for e in chain.events)
    pdf = build_pdf_exhibit(
        result, decision_id="dec_demo_1", ruleset_version="bc-esa-demo-v1"
    )
    xlsx = build_xlsx_exhibit(
        result, decision_id="dec_demo_1", ruleset_version="bc-esa-demo-v1"
    )
    assert pdf["downloadable"] is True
    assert xlsx["downloadable"] is True


def test_ot_refused_returns_line_to_composer() -> None:
    chain, result = _ot_week()
    proposal = result.ot_proposals[0]
    assert len(result.placed) == 1

    refuse = refuse_ot(
        proposal,
        human_name="Pat Nguyen",
        chain=chain,
        result=result,
        timestamp="2026-09-29T16:00:00Z",
    )
    assert proposal.status == "REFUSED"
    assert refuse.returned_to_composer["status"] == "ot_refused"
    assert refuse.returned_to_composer["proposal_id"] == proposal.proposal_id
    assert "pulled_placements" in refuse.returned_to_composer
    assert result.ot_proposals == []
    assert result.placed == []
    refused_events = [e for e in chain.events if e.kind == "ot_refused"]
    assert len(refused_events) == 1
    assert refused_events[0].actor == "human:Pat Nguyen"
    assert refused_events[0].evidence["returned_to_composer"] is True

    # After refuse, no PENDING_APPROVAL remains — export OT gate clears.
    assert pending_ot_lines(result.ot_proposals) == []
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_after_refuse",
        ruleset_version="bc-esa-demo-v1",
    )
    assert issued.decision_id == "dec_after_refuse"


def test_agent_has_no_approve_action() -> None:
    chain, result = _ot_week()
    proposal = result.ot_proposals[0]
    with pytest.raises(GateError, match="agent has no approve action"):
        agent_approve_ot(proposal, chain=chain)
    with pytest.raises(GateError, match="agent has no approve action"):
        approve_ot(
            proposal,
            human_name="agent",
            reason="should not matter",
            chain=chain,
        )
    assert proposal.status == "PENDING_APPROVAL"
    assert not any(e.kind == "ot_approved" for e in chain.events)
