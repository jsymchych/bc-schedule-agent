"""Wave D product: dual-plane faithful replay (placement + gate snapshot)."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.export import ExportBlocked, issue_schedule, pending_ot_lines
from bc_schedule_agent.gates import approve_ot, decide_time_off
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.replay import ReplayInputs, ReplayMismatch, replay
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _timeoff_csv(*rows: str) -> bytes:
    header = "request_id,employee,start,end,status\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _issue_peak_ot_week() -> tuple[object, bytes, dict, object]:
    """Compose unavoidable OT, approve with reason, issue."""
    chain = AuditChain()
    avail_raw = _avail_csv("sam,2026-09-28,06:00,20:00")
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand_payload = {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_peak_replay",
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
    }
    demand = parse_coverage_demand(demand_payload, chain=chain)
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    assert pending_ot_lines(result.ot_proposals)
    reason = "Peak close: only Sam available for the full window"
    for proposal in list(pending_ot_lines(result.ot_proposals)):
        approve_ot(
            proposal,
            human_name="Alex Rivera",
            reason=reason,
            chain=chain,
            timestamp="2026-09-29T18:00:00Z",
        )
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_peak_replay",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )
    assert issued.gate_snapshot["ot_approvals"]
    assert issued.gate_snapshot["ot_approvals"][0]["reason"] == reason
    assert issued.gate_snapshot["ot_approvals"][0]["human_name"] == "Alex Rivera"
    return issued, avail_raw, demand_payload, ruleset


def test_clean_week_dual_plane_matches() -> None:
    chain = AuditChain()
    avail_raw = _avail_csv(
        "sam,2026-09-28,08:00,19:00",
        "sam,2026-09-29,08:00,19:00",
        "sam,2026-09-30,08:00,19:00",
        "sam,2026-10-01,08:00,19:00",
        "sam,2026-10-02,08:00,19:00",
    )
    avail = parse_availability_sheet(avail_raw, chain=chain)
    shifts = []
    for day, shift_id in (
        ("2026-09-28", "sh_mon"),
        ("2026-09-29", "sh_tue"),
        ("2026-09-30", "sh_wed"),
        ("2026-10-01", "sh_thu"),
        ("2026-10-02", "sh_fri"),
    ):
        shifts.append(
            {
                "shift_id": shift_id,
                "employee": "sam",
                "date": day,
                "start": "09:00",
                "end": "17:30",
                "meal_break_minutes": 30,
            }
        )
    demand_payload = {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_clean_dual",
        "shifts": shifts,
    }
    demand = parse_coverage_demand(demand_payload, chain=chain)
    result = compose_week(demand, availability=avail, time_off=[], chain=chain)
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_clean_dual",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )
    assert issued.gate_snapshot == {"ot_approvals": [], "timeoff_decisions": []}
    replay_result = replay(
        ReplayInputs(
            availability_raw=avail_raw,
            demand=demand_payload,
            ruleset_hash=ruleset.content_hash,
            gate_snapshot=issued.gate_snapshot,
        ),
        expected_schedule_hash=issued.schedule_hash,
        expected_gate_snapshot=issued.gate_snapshot,
    )
    assert replay_result.matched is True
    assert replay_result.placed_count == 5


def test_strip_ot_reason_breaks_gate_plane() -> None:
    issued, avail_raw, demand_payload, ruleset = _issue_peak_ot_week()
    stripped = copy.deepcopy(issued.gate_snapshot)
    stripped["ot_approvals"][0].pop("reason")

    with pytest.raises(ReplayMismatch, match="gate plane mismatch") as exc:
        replay(
            ReplayInputs(
                availability_raw=avail_raw,
                demand=demand_payload,
                ruleset_hash=ruleset.content_hash,
                gate_snapshot=stripped,
            ),
            expected_schedule_hash=issued.schedule_hash,
            expected_gate_snapshot=issued.gate_snapshot,
        )
    assert exc.value.plane == "gate"


def test_strip_ot_approver_breaks_gate_plane() -> None:
    issued, avail_raw, demand_payload, ruleset = _issue_peak_ot_week()
    stripped = copy.deepcopy(issued.gate_snapshot)
    stripped["ot_approvals"][0].pop("human_name")

    with pytest.raises(ReplayMismatch, match="gate plane mismatch") as exc:
        replay(
            ReplayInputs(
                availability_raw=avail_raw,
                demand=demand_payload,
                ruleset_hash=ruleset.content_hash,
                gate_snapshot=stripped,
            ),
            expected_schedule_hash=issued.schedule_hash,
            expected_gate_snapshot=issued.gate_snapshot,
        )
    assert exc.value.plane == "gate"


def test_ot_week_faithful_gate_matches() -> None:
    issued, avail_raw, demand_payload, ruleset = _issue_peak_ot_week()
    replay_result = replay(
        ReplayInputs(
            availability_raw=avail_raw,
            demand=demand_payload,
            ruleset_hash=ruleset.content_hash,
            gate_snapshot=issued.gate_snapshot,
        ),
        expected_schedule_hash=issued.schedule_hash,
        expected_gate_snapshot=issued.gate_snapshot,
    )
    assert replay_result.matched is True
    assert replay_result.gate_snapshot_hash.startswith("sha256:")


def test_timeoff_decision_in_gate_snapshot() -> None:
    chain = AuditChain()
    avail_raw = _avail_csv("sam,2026-09-28,08:00,18:00")
    avail = parse_availability_sheet(avail_raw, chain=chain)
    pending_raw = _timeoff_csv(
        "to_sam_mon,sam,2026-09-28,2026-09-28,PENDING"
    )
    time_off = parse_time_off_sheet(pending_raw, chain=chain)
    decide_time_off(
        time_off[0],
        decision="deny",
        human_name="Alex Rivera",
        chain=chain,
        timestamp="2026-09-29T12:00:00Z",
    )
    demand_payload = {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_toff_replay",
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
    }
    demand = parse_coverage_demand(demand_payload, chain=chain)
    result = compose_week(
        demand, availability=avail, time_off=time_off, chain=chain
    )
    assert len(result.placed) == 1
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_toff_replay",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )
    assert len(issued.gate_snapshot["timeoff_decisions"]) == 1
    assert issued.gate_snapshot["timeoff_decisions"][0]["decision"] == "deny"

    # Replay inputs carry post-decision status so placement plane still matches.
    denied_raw = _timeoff_csv(
        "to_sam_mon,sam,2026-09-28,2026-09-28,DENIED"
    )
    stripped = copy.deepcopy(issued.gate_snapshot)
    stripped["timeoff_decisions"][0].pop("human_name")
    with pytest.raises(ReplayMismatch, match="gate plane mismatch") as exc:
        replay(
            ReplayInputs(
                availability_raw=avail_raw,
                demand=demand_payload,
                ruleset_hash=ruleset.content_hash,
                time_off_raw=denied_raw,
                gate_snapshot=stripped,
            ),
            expected_schedule_hash=issued.schedule_hash,
            expected_gate_snapshot=issued.gate_snapshot,
        )
    assert exc.value.plane == "gate"

    assert (
        replay(
            ReplayInputs(
                availability_raw=avail_raw,
                demand=demand_payload,
                ruleset_hash=ruleset.content_hash,
                time_off_raw=denied_raw,
                gate_snapshot=issued.gate_snapshot,
            ),
            expected_schedule_hash=issued.schedule_hash,
            expected_gate_snapshot=issued.gate_snapshot,
        ).matched
        is True
    )


def test_pending_ot_still_blocks_issue_before_gate_snapshot() -> None:
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
    with pytest.raises(ExportBlocked):
        issue_schedule(
            result,
            chain=chain,
            decision_id="dec_blocked",
            ruleset_version="bc-esa-demo-v1",
        )
