"""Wave B: availability, time-off, regimes, averaging packet fixtures."""

from __future__ import annotations

from datetime import date, timedelta

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.packet import check_averaging_packet

# Week of Sunday 2026-09-27 … Saturday 2026-10-03
WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _timeoff_csv(*rows: str) -> bytes:
    header = "request_id,employee,start,end,status\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _four_by_ten_schedule(start: date) -> list[dict]:
    """Mon–Thu 10h days inside a 1-week period starting Sunday."""
    # period starts Sunday; work Mon–Thu
    days = []
    for i in range(7):
        on = start + timedelta(days=i)
        if i in (1, 2, 3, 4):  # Mon–Thu
            days.append(
                {
                    "date": on.isoformat(),
                    "scheduled_hours": 10,
                    "start": "08:00",
                    "end": "18:00",
                }
            )
        else:
            days.append({"date": on.isoformat(), "scheduled_hours": 0})
    return days


def _valid_4x10_packet() -> dict:
    return {
        "packet_id": "agr_sam_2026w40",
        "employee": "sam",
        "in_writing": True,
        "employer_signed": True,
        "employee_signed": True,
        "signed_before_start": True,
        "period_weeks": 1,
        "repeat_count": 0,
        "start_date": WEEK_START.isoformat(),
        "expiry_date": (WEEK_START + timedelta(days=6)).isoformat(),
        "copy_received_before_start": True,
        "employer_signature_date": "2026-09-20",
        "employee_signature_date": "2026-09-20",
        "copy_received_date": "2026-09-21",
        "daily_schedule": _four_by_ten_schedule(WEEK_START),
    }


def test_availability_outside_window_never_places() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,09:00,17:00"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_out",
                    "employee": "sam",
                    "date": "2026-09-28",
                    "start": "08:00",
                    "end": "12:00",
                    "meal_break_minutes": 0,
                }
            ],
        },
        chain=chain,
    )
    result = compose_week(demand, availability=avail, time_off=[], chain=chain)
    assert result.placed == []
    refuse = [e for e in chain.events if e.kind == "rule_refuse"]
    assert len(refuse) == 1
    assert refuse[0].evidence["detail"] == "no covering availability row"
    assert not any(e.kind == "place" for e in chain.events)


def test_approved_time_off_blocks_place_with_request_id() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,08:00,18:00"),
        chain=chain,
    )
    time_off = parse_time_off_sheet(
        _timeoff_csv("to_sam_fri,sam,2026-09-28,2026-09-28,APPROVED"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_to",
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
    assert refuse[0].subject["blocking_request_id"] == "to_sam_fri"
    assert refuse[0].evidence["request_id"] == "to_sam_fri"
    assert not any(e.kind == "place" for e in chain.events)


def test_daily_overtime_ask_standard_regime() -> None:
    chain = AuditChain()
    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,06:00,20:00"),
        chain=chain,
    )
    # 10h worked with meal → daily OT 2h at 1.5x under s.35/s.40
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
    assert len(result.placed) == 1
    assert result.placed[0].worked_hours == 10.0
    ot = [e for e in chain.events if e.kind == "ot_proposed"]
    assert len(ot) == 1
    assert ot[0].evidence["multiplier"] == 1.5
    assert ot[0].evidence["hours"] == 2.0
    assert ot[0].evidence["section"] == "35 / 40"
    assert ot[0].evidence["status"] == "PENDING_APPROVAL"
    assert result.regime_by_employee["sam"] == "standard"


def test_valid_4x10_averaging_packet_straight_time() -> None:
    chain = AuditChain()
    packet = parse_averaging_packet(_valid_4x10_packet(), chain=chain)
    check = check_averaging_packet(packet, draft_week_start=WEEK_START)
    assert check.accepted

    avail_rows = [
        f"sam,{(WEEK_START + timedelta(days=i)).isoformat()},07:00,19:00"
        for i in (1, 2, 3, 4)
    ]
    avail = parse_availability_sheet(_avail_csv(*avail_rows), chain=chain)
    shifts = []
    for i in (1, 2, 3, 4):
        on = WEEK_START + timedelta(days=i)
        shifts.append(
            {
                "shift_id": f"sh_{on.isoformat()}",
                "employee": "sam",
                "date": on.isoformat(),
                "start": "08:00",
                "end": "18:30",
                "meal_break_minutes": 30,
            }
        )
    demand = parse_coverage_demand(
        {"week_start": WEEK_START.isoformat(), "source": "fixture", "shifts": shifts},
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        averaging_packets=[packet],
    )
    assert result.packet_status["sam"] == "accepted"
    assert result.regime_by_employee["sam"] == "averaging"
    assert len(result.placed) == 4
    accepted = [e for e in chain.events if e.kind == "packet_accepted"]
    assert len(accepted) == 1
    assert "37(3)" in accepted[0].evidence["audit_sentence"]
    assert accepted[0].evidence["employee_signature_date"] == "2026-09-20"
    # 10h days matching schedule → straight time, no OT
    assert not any(e.kind == "ot_proposed" for e in chain.events)
    passes = [
        e
        for e in chain.events
        if e.kind == "rule_pass" and e.evidence.get("straight_time")
    ]
    assert len(passes) == 4


def test_packet_missing_employee_signature_rejected() -> None:
    chain = AuditChain()
    payload = _valid_4x10_packet()
    payload["employee_signed"] = False
    payload["employee_signature_date"] = None
    packet = parse_averaging_packet(payload, chain=chain)

    avail = parse_availability_sheet(
        _avail_csv("sam,2026-09-28,07:00,19:00"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_std",
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
        averaging_packets=[packet],
    )
    rejected = [e for e in chain.events if e.kind == "packet_rejected"]
    assert len(rejected) == 1
    terms = rejected[0].evidence["missing_terms"]
    assert any(
        t["term"] == "employee_signature" and t["section"] == "37(2)(a)(ii)"
        for t in terms
    )
    assert result.regime_by_employee["sam"] == "standard"
    # Falls back to s.40 — 10h day proposes 1.5x
    ot = [e for e in chain.events if e.kind == "ot_proposed"]
    assert len(ot) == 1
    assert ot[0].evidence["section"] == "35 / 40"


def test_two_week_90_hour_packet_rejected() -> None:
    """IGM example: two-week schedule totalling 90 hours → reject s.37(3)."""
    chain = AuditChain()
    start = WEEK_START
    days = []
    # 9h × 10 work days = 90 over 14 calendar days
    work_offsets = [1, 2, 3, 4, 5, 8, 9, 10, 11, 12]  # skip Sundays
    for i in range(14):
        on = start + timedelta(days=i)
        if i in work_offsets:
            days.append(
                {
                    "date": on.isoformat(),
                    "scheduled_hours": 9,
                    "start": "08:00",
                    "end": "17:00",
                }
            )
        else:
            days.append({"date": on.isoformat(), "scheduled_hours": 0})
    payload = {
        "packet_id": "agr_sam_90",
        "employee": "sam",
        "in_writing": True,
        "employer_signed": True,
        "employee_signed": True,
        "signed_before_start": True,
        "period_weeks": 2,
        "repeat_count": 0,
        "start_date": start.isoformat(),
        "expiry_date": (start + timedelta(days=13)).isoformat(),
        "copy_received_before_start": True,
        "daily_schedule": days,
    }
    packet = parse_averaging_packet(payload, chain=chain)
    result = check_averaging_packet(packet, draft_week_start=WEEK_START)
    assert not result.accepted
    assert any(
        f.term == "scheduled_hours_cap" and f.section == "37(3)" for f in result.failures
    )

    from bc_schedule_agent.packet import accept_or_reject_packet

    accept_or_reject_packet(packet, draft_week_start=WEEK_START, chain=chain)
    rejected = [e for e in chain.events if e.kind == "packet_rejected"]
    assert rejected
    assert any(
        t["section"] == "37(3)" for t in rejected[0].evidence["missing_terms"]
    )


def test_averaging_extra_hour_on_12h_day_is_2x() -> None:
    chain = AuditChain()
    payload = _valid_4x10_packet()
    # Rebuild as one 12h scheduled day (Monday) for the beyond-12 case
    mon = WEEK_START + timedelta(days=1)
    payload["daily_schedule"] = [
        {
            "date": (WEEK_START + timedelta(days=i)).isoformat(),
            "scheduled_hours": 12 if i == 1 else 0,
            "start": "06:00" if i == 1 else None,
            "end": "18:00" if i == 1 else None,
        }
        for i in range(7)
    ]
    # Fix None start/end for zero days
    for d in payload["daily_schedule"]:
        if d["scheduled_hours"] == 0:
            d.pop("start", None)
            d.pop("end", None)

    packet = parse_averaging_packet(payload, chain=chain)
    avail = parse_availability_sheet(
        _avail_csv(f"sam,{mon.isoformat()},05:00,22:00"),
        chain=chain,
    )
    # 13h worked (12.5 span + wait — use 13.5 span with 30m meal = 13h)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_12p1",
                    "employee": "sam",
                    "date": mon.isoformat(),
                    "start": "06:00",
                    "end": "19:30",
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
        averaging_packets=[packet],
    )
    assert result.packet_status["sam"] == "accepted"
    assert result.placed[0].worked_hours == 13.0
    ot = [e for e in chain.events if e.kind == "ot_proposed"]
    assert any(
        e.evidence["multiplier"] == 2.0 and e.evidence["section"] in ("37(4)", "37(6)(b)")
        for e in ot
    )
    two_x = [e for e in ot if e.evidence["multiplier"] == 2.0]
    assert two_x[0].evidence["hours"] == 1.0


def test_averaging_extra_hour_on_4h_day_straight_time() -> None:
    """IGM s.37(6): extra hour on a scheduled 4-hour day is straight time."""
    chain = AuditChain()
    payload = _valid_4x10_packet()
    mon = WEEK_START + timedelta(days=1)
    payload["daily_schedule"] = [
        {
            "date": (WEEK_START + timedelta(days=i)).isoformat(),
            "scheduled_hours": 4 if i == 1 else 0,
            "start": "09:00" if i == 1 else None,
            "end": "13:00" if i == 1 else None,
        }
        for i in range(7)
    ]
    for d in payload["daily_schedule"]:
        if d["scheduled_hours"] == 0:
            d.pop("start", None)
            d.pop("end", None)

    packet = parse_averaging_packet(payload, chain=chain)
    avail = parse_availability_sheet(
        _avail_csv(f"sam,{mon.isoformat()},08:00,18:00"),
        chain=chain,
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": [
                {
                    "shift_id": "sh_4p1",
                    "employee": "sam",
                    "date": mon.isoformat(),
                    "start": "09:00",
                    "end": "14:00",
                    "meal_break_minutes": 0,
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
        averaging_packets=[packet],
    )
    assert result.placed[0].worked_hours == 5.0
    assert not any(e.kind == "ot_proposed" for e in chain.events)
    passes = [
        e
        for e in chain.events
        if e.kind == "rule_pass" and e.evidence.get("igm_example")
    ]
    assert len(passes) == 1
