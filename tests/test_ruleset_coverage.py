"""Ruleset–engine parity: every rule is enforced|appendix; enforced ids have actors + fixtures."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
)
from bc_schedule_agent.packet import accept_or_reject_packet, check_averaging_packet
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)
MON = date(2026, 9, 28)
SRC = Path(__file__).resolve().parents[1] / "src" / "bc_schedule_agent"


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _compose(shifts: list[dict], *, avail_rows: list[str] | None = None, packets=None):
    chain = AuditChain()
    rows = avail_rows or [f"sam,{d.isoformat()},00:00,23:59" for d in (
        WEEK_START + timedelta(days=i) for i in range(7)
    )]
    avail = parse_availability_sheet(_avail_csv(*rows), chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "shifts": shifts,
        },
        chain=chain,
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        averaging_packets=packets or [],
    )
    return result, chain


def _actors(chain: AuditChain) -> set[str]:
    out: set[str] = set()
    for event in chain.events:
        if event.actor.startswith("rule:"):
            out.add(event.actor.removeprefix("rule:"))
    return out


def _source_rule_literals() -> set[str]:
    """Rule ids referenced as string literals in composer/packet (rule: actors)."""
    found: set[str] = set()
    for name in ("composer.py", "packet.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        found.update(re.findall(r"(bc-esa-[a-z0-9-]+)", text))
    return found


def _valid_packet(**overrides) -> dict:
    payload = {
        "packet_id": "agr_cov",
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
        "daily_schedule": [
            {
                "date": (WEEK_START + timedelta(days=i)).isoformat(),
                "scheduled_hours": 10 if i in (1, 2, 3, 4) else 0,
                "start": "08:00" if i in (1, 2, 3, 4) else None,
                "end": "18:00" if i in (1, 2, 3, 4) else None,
            }
            for i in range(7)
        ],
    }
    for day in payload["daily_schedule"]:
        if day["scheduled_hours"] == 0:
            day.pop("start", None)
            day.pop("end", None)
    payload.update(overrides)
    return payload


# --- Fixture paths: each enforced id → audit actor appears ---

def fixture_s32_meal() -> set[str]:
    _, chain = _compose(
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
    return _actors(chain)


def fixture_s33_split() -> set[str]:
    _, chain = _compose(
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
    return _actors(chain)


def fixture_s34_min() -> set[str]:
    _, chain = _compose(
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
    return _actors(chain)


def fixture_s34_long() -> set[str]:
    # Day scheduled >8h via first shift; second reporting shift under 4h → s.34(2)
    _, chain = _compose(
        [
            {
                "shift_id": "sh_long",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "06:00",
                "end": "15:00",
                "meal_break_minutes": 30,
            },
            {
                "shift_id": "sh_addon",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "16:00",
                "end": "19:00",
                "meal_break_minutes": 0,
            },
        ]
    )
    return _actors(chain)


def fixture_s35_threshold() -> set[str]:
    # At-or-under 8h → rule_pass on the daily OT threshold actor
    _, chain = _compose(
        [
            {
                "shift_id": "sh_ok",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "09:00",
                "end": "17:00",
                "meal_break_minutes": 30,
            }
        ]
    )
    return _actors(chain)


def fixture_s40_rates() -> set[str]:
    _, chain = _compose(
        [
            {
                "shift_id": "sh_ot",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "08:00",
                "end": "19:00",
                "meal_break_minutes": 30,
            }
        ]
    )
    return _actors(chain)


def fixture_s40_weekly() -> set[str]:
    shifts = []
    for i, day in enumerate(
        (WEEK_START + timedelta(days=d) for d in range(1, 7))  # Mon–Sat
    ):
        shifts.append(
            {
                "shift_id": f"sh_w{i}",
                "employee": "sam",
                "date": day.isoformat(),
                "start": "09:00",
                "end": "17:00",
                "meal_break_minutes": 30,
            }
        )
    _, chain = _compose(shifts)
    return _actors(chain)


def fixture_s36_weekly_rest() -> set[str]:
    # Work every day with only short overnight gaps → no 32h free block
    shifts = []
    for i in range(7):
        day = WEEK_START + timedelta(days=i)
        shifts.append(
            {
                "shift_id": f"sh_r{i}",
                "employee": "sam",
                "date": day.isoformat(),
                "start": "08:00",
                "end": "16:00",
                "meal_break_minutes": 30,
            }
        )
    _, chain = _compose(shifts)
    return _actors(chain)


def fixture_s36_between() -> set[str]:
    tue = date(2026, 9, 29)
    _, chain = _compose(
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
        ]
    )
    return _actors(chain)


def fixture_s39_excessive() -> set[str]:
    _, chain = _compose(
        [
            {
                "shift_id": "sh_x",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "04:00",
                "end": "22:00",
                "meal_break_minutes": 60,
            }
        ]
    )
    return _actors(chain)


def fixture_s37_packet_accept() -> set[str]:
    chain = AuditChain()
    packet = parse_averaging_packet(_valid_packet(), chain=chain)
    accept_or_reject_packet(packet, draft_week_start=WEEK_START, chain=chain)
    return _actors(chain)


def fixture_s37_packet_rejects() -> set[str]:
    actors: set[str] = set()
    cases = [
        {"in_writing": False},
        {"employer_signed": False},
        {"period_weeks": 5},
        {"_omit_repeat_count": True},
        {"start_date": None, "expiry_date": None},
        {"daily_schedule": []},
        {"copy_received_before_start": False},
        {
            "start_date": "2026-10-10",
            "expiry_date": "2026-10-16",
        },
        {
            "period_weeks": 1,
            "daily_schedule": [
                {
                    "date": (WEEK_START + timedelta(days=i)).isoformat(),
                    "scheduled_hours": 8,
                    "start": "09:00",
                    "end": "17:00",
                }
                for i in range(7)
            ],
        },
    ]
    for overrides in cases:
        chain = AuditChain()
        omit_repeat = overrides.pop("_omit_repeat_count", False)
        payload = _valid_packet(**overrides)
        if omit_repeat:
            payload.pop("repeat_count", None)
        packet = parse_averaging_packet(payload, chain=chain)
        accept_or_reject_packet(packet, draft_week_start=WEEK_START, chain=chain)
        actors |= _actors(chain)
    return actors


def fixture_s37_ot_daily() -> set[str]:
    payload = _valid_packet()
    payload["daily_schedule"] = [
        {
            "date": (WEEK_START + timedelta(days=i)).isoformat(),
            "scheduled_hours": 12 if i == 1 else 0,
            "start": "06:00" if i == 1 else None,
            "end": "18:00" if i == 1 else None,
        }
        for i in range(7)
    ]
    for d in payload["daily_schedule"]:
        if d["scheduled_hours"] == 0:
            d.pop("start", None)
            d.pop("end", None)
    chain = AuditChain()
    packet = parse_averaging_packet(payload, chain=chain)
    _, chain2 = _compose(
        [
            {
                "shift_id": "sh_12p1",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "06:00",
                "end": "19:30",
                "meal_break_minutes": 30,
            }
        ],
        packets=[packet],
    )
    return _actors(chain) | _actors(chain2)


def fixture_s37_beyond() -> set[str]:
    payload = _valid_packet()
    # scheduled 10h Mon; work 11h → beyond agreed
    chain = AuditChain()
    packet = parse_averaging_packet(payload, chain=chain)
    _, chain2 = _compose(
        [
            {
                "shift_id": "sh_beyond",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "08:00",
                "end": "19:30",
                "meal_break_minutes": 30,
            }
        ],
        packets=[packet],
    )
    return _actors(chain) | _actors(chain2)


def fixture_s37_weekly_avg() -> set[str]:
    payload = _valid_packet()
    # Mon–Fri 9h under averaging 4x10 packet schedule → over 40 weekly
    chain = AuditChain()
    packet = parse_averaging_packet(payload, chain=chain)
    shifts = []
    for i in (1, 2, 3, 4, 5):
        day = WEEK_START + timedelta(days=i)
        shifts.append(
            {
                "shift_id": f"sh_avg{i}",
                "employee": "sam",
                "date": day.isoformat(),
                "start": "08:00",
                "end": "17:30",
                "meal_break_minutes": 30,
            }
        )
    _, chain2 = _compose(shifts, packets=[packet])
    return _actors(chain) | _actors(chain2)


def fixture_s37_rest_premium() -> set[str]:
    payload = _valid_packet()
    chain = AuditChain()
    packet = parse_averaging_packet(payload, chain=chain)
    shifts = []
    for i in range(7):
        day = WEEK_START + timedelta(days=i)
        shifts.append(
            {
                "shift_id": f"sh_ar{i}",
                "employee": "sam",
                "date": day.isoformat(),
                "start": "08:00",
                "end": "16:00",
                "meal_break_minutes": 30,
            }
        )
    _, chain2 = _compose(shifts, packets=[packet])
    return _actors(chain) | _actors(chain2)


FIXTURE_PATHS: dict[str, callable] = {
    "bc-esa-s32-meal-break": fixture_s32_meal,
    "bc-esa-s33-split-shift": fixture_s33_split,
    "bc-esa-s34-min-daily-hours": fixture_s34_min,
    "bc-esa-s34-min-daily-hours-long": fixture_s34_long,
    "bc-esa-s35-daily-overtime-threshold": fixture_s35_threshold,
    "bc-esa-s40-overtime-rates": fixture_s40_rates,
    "bc-esa-s36-weekly-rest": fixture_s36_weekly_rest,
    "bc-esa-s36-between-shifts": fixture_s36_between,
    "bc-esa-s39-no-excessive-hours": fixture_s39_excessive,
    "bc-esa-s37-packet-in-writing": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-signed-both": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-length": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-daily-schedule": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-repeat-count": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-dates": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-scheduled-hours": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-copy-received": fixture_s37_packet_rejects,
    "bc-esa-s37-packet-week-in-range": fixture_s37_packet_rejects,
    "bc-esa-s37-ot-over-12-daily": fixture_s37_ot_daily,
    "bc-esa-s37-ot-beyond-agreed-day": fixture_s37_beyond,
    "bc-esa-s37-ot-average-weekly": fixture_s37_weekly_avg,
    "bc-esa-s37-rest-premium": fixture_s37_rest_premium,
}


def test_every_rule_has_enforced_or_appendix_status() -> None:
    rs = load_ruleset()
    assert rs.version == "esa_bc_v1.1"
    for rule in rs.rules:
        assert rule.get("status") in ("enforced", "appendix"), rule["id"]
    assert set(rs.appendix_ids()) == {
        "bc-esa-s32-paid-meal-break",
        "bc-esa-s16-minimum-wage",
        "bc-esa-s44-stat-holiday-entitlement",
        "bc-esa-s46-stat-holiday-pay",
        "bc-esa-stat-holidays-list",
        "bc-esa-s37-schedule-change",
    }


def test_enforced_ids_appear_as_rule_actors_in_source() -> None:
    rs = load_ruleset()
    literals = _source_rule_literals()
    missing = [rid for rid in rs.enforced_ids() if rid not in literals]
    assert missing == [], f"enforced ids missing from composer/packet literals: {missing}"


def test_each_enforced_id_has_fixture_actor_path() -> None:
    rs = load_ruleset()
    missing_registry = [rid for rid in rs.enforced_ids() if rid not in FIXTURE_PATHS]
    assert missing_registry == [], f"no fixture path: {missing_registry}"

    # Accept path also covers packet rule_pass actors
    accept_actors = fixture_s37_packet_accept()
    for rid in (
        "bc-esa-s37-packet-in-writing",
        "bc-esa-s37-packet-signed-both",
        "bc-esa-s37-packet-length",
        "bc-esa-s37-packet-daily-schedule",
        "bc-esa-s37-packet-repeat-count",
        "bc-esa-s37-packet-dates",
        "bc-esa-s37-packet-scheduled-hours",
        "bc-esa-s37-packet-copy-received",
        "bc-esa-s37-packet-week-in-range",
    ):
        assert rid in accept_actors or rid in fixture_s37_packet_rejects()

    failures: list[str] = []
    for rid, fn in FIXTURE_PATHS.items():
        actors = fn()
        if rid not in actors:
            failures.append(f"{rid} not in actors={sorted(actors)}")
    assert failures == [], "fixture actor drift:\n" + "\n".join(failures)


def test_s34_2_hard_refuse_fixture() -> None:
    result, chain = _compose(
        [
            {
                "shift_id": "sh_long",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "06:00",
                "end": "15:00",
                "meal_break_minutes": 30,
            },
            {
                "shift_id": "sh_addon",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "16:00",
                "end": "19:00",
                "meal_break_minutes": 0,
            },
        ]
    )
    refuses = [
        e
        for e in chain.events
        if e.kind == "rule_refuse" and e.actor == "rule:bc-esa-s34-min-daily-hours-long"
    ]
    assert len(refuses) == 1
    assert refuses[0].subject.get("shift_id") == "sh_addon"
    assert len(result.placed) == 1


def test_s39_hard_refuse_over_16() -> None:
    result, chain = _compose(
        [
            {
                "shift_id": "sh_x",
                "employee": "sam",
                "date": MON.isoformat(),
                "start": "04:00",
                "end": "22:00",
                "meal_break_minutes": 60,
            }
        ]
    )
    # span 18h − 1h meal = 17h worked > 16
    assert result.placed == []
    refuses = [
        e
        for e in chain.events
        if e.kind == "rule_refuse" and e.actor == "rule:bc-esa-s39-no-excessive-hours"
    ]
    assert len(refuses) == 1


def test_s36_1_rest_premium_when_no_32h_gap() -> None:
    shifts = [
        {
            "shift_id": f"sh_r{i}",
            "employee": "sam",
            "date": (WEEK_START + timedelta(days=i)).isoformat(),
            "start": "08:00",
            "end": "16:00",
            "meal_break_minutes": 30,
        }
        for i in range(7)
    ]
    result, chain = _compose(shifts)
    assert len(result.placed) == 7
    ot = [
        e
        for e in chain.events
        if e.kind == "ot_proposed" and e.actor == "rule:bc-esa-s36-weekly-rest"
    ]
    assert len(ot) == 1
    assert ot[0].evidence["multiplier"] == 1.5


def test_packet_term_failure_carries_rule_id() -> None:
    chain = AuditChain()
    payload = _valid_packet(in_writing=False)
    packet = parse_averaging_packet(payload, chain=chain)
    check = check_averaging_packet(packet, draft_week_start=WEEK_START)
    assert not check.accepted
    assert any(
        f.term == "in_writing" and f.rule_id == "bc-esa-s37-packet-in-writing"
        for f in check.failures
    )
