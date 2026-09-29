"""Acceptance a4: hard refuses are not approvable (meal, split, sub-2h, rest)."""

from __future__ import annotations

from datetime import date

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand

WEEK_START = date(2026, 9, 27)
MON = date(2026, 9, 28)


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _compose(
    shifts: list[dict],
    *,
    avail_rows: list[str] | None = None,
) -> tuple:
    chain = AuditChain()
    rows = avail_rows or [f"sam,{MON.isoformat()},00:00,23:59"]
    avail = parse_availability_sheet(_avail_csv(*rows), chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": shifts,
        },
        chain=chain,
    )
    result = compose_week(demand, availability=avail, time_off=[], chain=chain)
    return result, chain


def _refuses(chain: AuditChain, rule_id: str) -> list:
    actor = f"rule:{rule_id}"
    return [e for e in chain.events if e.kind == "rule_refuse" and e.actor == actor]


def test_meal_break_s32_hard_refuse() -> None:
    result, chain = _compose(
        [
            {
                "shift_id": "sh_meal",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "09:00",
                "end": "15:00",
                "meal_break_minutes": 0,
            }
        ]
    )
    refuses = _refuses(chain, "bc-esa-s32-meal-break")
    assert len(refuses) == 1
    assert refuses[0].evidence["section"] == "32(1)"
    assert result.placed == []
    assert not any(e.kind == "ot_proposed" for e in chain.events)
    assert result.ot_proposals == []


def test_split_span_s33_hard_refuse() -> None:
    result, chain = _compose(
        [
            {
                "shift_id": "sh_am",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "06:00",
                "end": "10:00",
                "meal_break_minutes": 0,
            },
            {
                "shift_id": "sh_pm",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "15:00",
                "end": "19:00",
                "meal_break_minutes": 0,
            },
        ]
    )
    # Span 06:00–19:00 = 13h > 12 → second shift refused
    refuses = _refuses(chain, "bc-esa-s33-split-shift")
    assert len(refuses) == 1
    assert refuses[0].evidence["section"] == "33"
    assert refuses[0].subject.get("shift_id") == "sh_pm"
    assert len(result.placed) == 1
    assert result.placed[0].shift_id == "sh_am"
    assert not any(
        e.kind == "ot_proposed" and e.subject.get("shift_id") == "sh_pm"
        for e in chain.events
    )


def test_sub_two_hour_s34_hard_refuse() -> None:
    result, chain = _compose(
        [
            {
                "shift_id": "sh_short",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "09:00",
                "end": "10:30",
                "meal_break_minutes": 0,
            }
        ]
    )
    refuses = _refuses(chain, "bc-esa-s34-min-daily-hours")
    assert len(refuses) == 1
    assert refuses[0].evidence["section"] == "34(1)"
    assert "under 2 hours" in refuses[0].evidence["detail"]
    assert result.placed == []
    assert result.ot_proposals == []
    assert not any(e.kind == "ot_proposed" for e in chain.events)


def test_rest_gap_s36_hard_refuse() -> None:
    tue = date(2026, 9, 29)
    result, chain = _compose(
        [
            {
                "shift_id": "sh_late",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "16:00",
                "end": "22:00",
                "meal_break_minutes": 30,
            },
            {
                "shift_id": "sh_early",
                "employee": "sam",
                "date": tue.isoformat(),
                "start": "04:00",
                "end": "10:00",
                "meal_break_minutes": 30,
            },
        ],
        avail_rows=[
            f"sam,{MON.isoformat()},00:00,23:59",
            f"sam,{tue.isoformat()},00:00,23:59",
        ],
    )
    # Gap 22:00 → 04:00 = 6h < 8 → second shift refused
    refuses = _refuses(chain, "bc-esa-s36-between-shifts")
    assert len(refuses) == 1
    assert refuses[0].evidence["section"] == "36(2)"
    assert refuses[0].subject.get("shift_id") == "sh_early"
    assert len(result.placed) == 1
    assert result.placed[0].shift_id == "sh_late"
    assert not any(
        e.kind == "ot_proposed" and e.subject.get("shift_id") == "sh_early"
        for e in chain.events
    )
