"""Wave D: composition priors — soft bias, shelf hard-refuse, ESA-green, overflow drop."""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import write_exhibits
from bc_schedule_agent.history import WeekHistoryStore
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand
from bc_schedule_agent.priors import (
    HistoryPriors,
    build_history_priors,
    priors_from_placements,
)
from bc_schedule_agent.ruleset import load_ruleset
from bc_schedule_agent.shelf import (
    SHELF_MISMATCH_RULE,
    build_parameter_shelf,
    observed_input_hashes,
)

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    return ("employee,date,start,end\n" + "\n".join(rows) + "\n").encode("utf-8")


def _ops_csv() -> bytes:
    return b"weekday,open,close\nmon,09:00,17:00\n"


def _sales_csv() -> bytes:
    return b"weekday,sales\nmon,1000\n"


def _issue_named(
    tmp_path: Path,
    *,
    week_start: date,
    decision_id: str,
    history_root: Path,
    employee: str,
    history_max_weeks: int = 8,
):
    chain = AuditChain()
    day = (week_start + timedelta(days=1)).isoformat()  # Monday
    avail_raw = _avail_csv(f"{employee},{day},09:00,17:00")
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": week_start.isoformat(),
            "source": "fixture",
            "demand_id": f"dem_{decision_id}",
            "shifts": [
                {
                    "shift_id": "sh_am",
                    "employee": employee,
                    "date": day,
                    "start": "09:00",
                    "end": "17:00",
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
    ruleset = load_ruleset()
    return write_exhibits(
        result,
        chain=chain,
        decision_id=decision_id,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path / "exhibits" / decision_id,
        timestamp="2026-09-29T19:05:00Z",
        history_root=history_root,
        history_max_weeks=history_max_weeks,
    )


def test_priors_bias_placement_affinity():
    """When preferred is unavailable, high day/window affinity wins over roster order."""
    monday = WEEK_START + timedelta(days=1)
    # jordan listed first in availability (roster order); alex has history affinity.
    avail_raw = _avail_csv(
        f"jordan,{monday.isoformat()},09:00,17:00",
        f"alex,{monday.isoformat()},09:00,17:00",
    )
    chain = AuditChain()
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_prior_bias",
            "shifts": [
                {
                    "shift_id": "sh_am",
                    "employee": "sam",  # preferred unavailable
                    "date": monday.isoformat(),
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )

    without = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=AuditChain(),
        prefer_zero_ot=True,
    )
    assert len(without.placed) == 1
    assert without.placed[0].employee == "jordan"

    priors = priors_from_placements(
        [
            {
                "employee": "alex",
                "date": monday - timedelta(days=7),
                "start": "09:00",
                "end": "17:00",
            },
            {
                "employee": "alex",
                "date": monday - timedelta(days=14),
                "start": "09:00",
                "end": "17:00",
            },
        ],
        week_starts=["2026-09-13", "2026-09-20"],
    )
    with_priors = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=AuditChain(),
        prefer_zero_ot=True,
        history_priors=priors,
    )
    assert len(with_priors.placed) == 1
    assert with_priors.placed[0].employee == "alex"
    assert priors.week_starts == ["2026-09-13", "2026-09-20"]


def test_shelf_mismatch_still_hard_refuses_under_priors():
    day = (WEEK_START + timedelta(days=1)).isoformat()
    hours = _ops_csv()
    sales = _sales_csv()
    avail_a = _avail_csv(f"sam,{day},09:00,17:00")
    avail_b = _avail_csv(f"alex,{day},09:00,17:00")
    shelf = build_parameter_shelf(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail_a,
    )
    observed = observed_input_hashes(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail_b,
    )
    priors = priors_from_placements(
        [
            {
                "employee": "alex",
                "date": day,
                "start": "09:00",
                "end": "17:00",
            }
        ],
        week_starts=["2026-09-20"],
    )
    chain = AuditChain()
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_prior_shelf",
            "shifts": [
                {
                    "shift_id": "sh_am",
                    "employee": "alex",
                    "date": day,
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    avail_windows = parse_availability_sheet(avail_b, chain=chain)
    result = compose_week(
        demand,
        availability=avail_windows,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
        parameter_shelf=shelf,
        observed_shelf_hashes=observed,
        history_priors=priors,
    )
    refuses = [e for e in chain.events if e.kind == "rule_refuse"]
    assert len(refuses) == 1
    assert refuses[0].subject["rule_id"] == SHELF_MISMATCH_RULE
    assert result.placed == []


def test_esa_green_under_priors():
    monday = WEEK_START + timedelta(days=1)
    avail_raw = _avail_csv(f"sam,{monday.isoformat()},09:00,17:00")
    chain = AuditChain()
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_prior_esa",
            "shifts": [
                {
                    "shift_id": "sh_am",
                    "employee": "sam",
                    "date": monday.isoformat(),
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    priors = priors_from_placements(
        [
            {
                "employee": "sam",
                "date": monday - timedelta(days=7),
                "start": "09:00",
                "end": "17:00",
            }
        ],
        week_starts=["2026-09-20"],
    )
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
        history_priors=priors,
    )
    assert result.placed
    refuses = [e for e in chain.events if e.kind == "rule_refuse"]
    assert refuses == []
    # No model call in gate path — chain stays local agents/rules only.
    assert all(
        not str(e.actor).startswith("model:") for e in chain.events
    )


def test_overflow_drops_oldest_from_priors(tmp_path: Path):
    history_root = tmp_path / "history"
    max_weeks = 2
    starts = [
        WEEK_START,
        WEEK_START + timedelta(days=7),
        WEEK_START + timedelta(days=14),
    ]
    employees = ["alex", "alex", "jordan"]
    for i, (ws, emp) in enumerate(zip(starts, employees)):
        _issue_named(
            tmp_path,
            week_start=ws,
            decision_id=f"dec_prior_{i}",
            history_root=history_root,
            employee=emp,
            history_max_weeks=max_weeks,
        )

    store = WeekHistoryStore(history_root, history_max_weeks=max_weeks)
    index = store.load_index()
    assert len(index.rows) == 2
    assert index.rows[0].week_start == starts[1].isoformat()
    assert index.rows[1].week_start == starts[2].isoformat()

    # Durable audit lives in week dirs for prior harvest after exhibit out_dir moves.
    for row in index.rows:
        assert (history_root / row.week_dir / "audit.json").is_file()

    priors = build_history_priors(store)
    assert priors.week_starts == [starts[1].isoformat(), starts[2].isoformat()]
    # Oldest alex week dropped — only second alex week + jordan remain.
    assert "alex" in priors.day_affinity
    assert "jordan" in priors.day_affinity
    # Monday affinity for alex should be 1 (one remaining week), not 2.
    monday_iso = (starts[1] + timedelta(days=1)).weekday()
    assert priors.day_affinity["alex"].get(monday_iso, 0) == 1
    assert isinstance(priors, HistoryPriors)


def test_candidate_score_prefers_matching_window():
    monday = WEEK_START + timedelta(days=1)
    priors = priors_from_placements(
        [
            {
                "employee": "alex",
                "date": monday,
                "start": "09:00",
                "end": "17:00",
            },
            {
                "employee": "jordan",
                "date": monday,
                "start": "12:00",
                "end": "20:00",
            },
        ]
    )
    alex = priors.candidate_score(
        "alex", on=monday, start=time(9, 0), end=time(17, 0)
    )
    jordan = priors.candidate_score(
        "jordan", on=monday, start=time(9, 0), end=time(17, 0)
    )
    assert alex > jordan
