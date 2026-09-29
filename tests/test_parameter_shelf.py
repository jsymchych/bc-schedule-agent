"""Wave B: parameter shelf — schema, hashes, roster, mismatch hard-refuse, issue bind."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import write_exhibits
from bc_schedule_agent.history import WeekHistoryStore
from bc_schedule_agent.ingest import content_hash, parse_availability_sheet, parse_coverage_demand
from bc_schedule_agent.ruleset import load_ruleset
from bc_schedule_agent.shelf import (
    SHELF_MISMATCH_RULE,
    ParameterShelfStore,
    build_parameter_shelf,
    observed_input_hashes,
    roster_from_availability,
)
from bc_schedule_agent.staffing import load_staffing

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    return ("employee,date,start,end\n" + "\n".join(rows) + "\n").encode("utf-8")


def _ops_csv() -> bytes:
    return b"weekday,open,close\nmon,09:00,17:00\n"


def _sales_csv() -> bytes:
    return b"weekday,sales\nmon,1000\n"


def _timeoff_csv() -> bytes:
    return (
        b"request_id,employee,start,end,status\n"
        b"to_1,sam,2026-09-30,2026-09-30,APPROVED\n"
    )


def test_hashes_match_ingest(tmp_path: Path):
    day = (WEEK_START + timedelta(days=1)).isoformat()
    hours = _ops_csv()
    sales = _sales_csv()
    avail = _avail_csv(f"sam,{day},09:00,17:00", f"alex,{day},09:00,17:00")
    time_off = _timeoff_csv()
    staffing = load_staffing()
    shelf = build_parameter_shelf(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail,
        time_off_raw=time_off,
        staffing=staffing,
    )
    assert shelf.hours_of_operation_hash == content_hash(hours)
    assert shelf.sales_projections_hash == content_hash(sales)
    assert shelf.availability_hash == content_hash(avail)
    assert shelf.time_off_hash == content_hash(time_off)
    assert shelf.staffing_hash == staffing.content_hash
    assert shelf.staffing_version == staffing.version
    assert shelf.parameter_shelf_id.startswith("shelf_")

    store = ParameterShelfStore(tmp_path / "parameters")
    path = store.save(shelf)
    assert path.is_file()
    reloaded = store.load(shelf.parameter_shelf_id)
    assert reloaded.parameter_shelf_id == shelf.parameter_shelf_id
    assert reloaded.content_hashes() == shelf.content_hashes()
    active = store.load_active()
    assert active is not None
    assert active.parameter_shelf_id == shelf.parameter_shelf_id


def test_roster_derived_from_availability():
    day = (WEEK_START + timedelta(days=1)).isoformat()
    avail = _avail_csv(
        f"sam,{day},09:00,17:00",
        f"alex,{day},12:00,20:00",
        f"sam,{day},18:00,22:00",
    )
    windows = parse_availability_sheet(avail)
    roster = roster_from_availability(windows)
    assert roster == ["sam", "alex"]
    shelf = build_parameter_shelf(
        hours_of_operation=_ops_csv(),
        sales_projections=_sales_csv(),
        availability_raw=avail,
        availability=windows,
    )
    assert shelf.derived_roster == ["sam", "alex"]


def test_shelf_mismatch_hard_refuses():
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
    # Draft tries to compose with a different availability sheet than the bound shelf.
    observed = observed_input_hashes(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail_b,
    )
    chain = AuditChain()
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_mismatch",
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
    )
    refuses = [e for e in chain.events if e.kind == "rule_refuse"]
    assert len(refuses) == 1
    assert refuses[0].subject["rule_id"] == SHELF_MISMATCH_RULE
    assert "availability" in refuses[0].subject["mismatched_fields"]
    assert result.placed == []


def test_issue_records_parameter_shelf_id(tmp_path: Path):
    history_root = tmp_path / "history"
    day = (WEEK_START + timedelta(days=1)).isoformat()
    hours = _ops_csv()
    sales = _sales_csv()
    avail = _avail_csv(f"sam,{day},09:00,17:00")
    shelf = build_parameter_shelf(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail,
    )
    ParameterShelfStore(tmp_path / "parameters").save(shelf)

    chain = AuditChain()
    avail_windows = parse_availability_sheet(avail, chain=chain)
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_shelf_issue",
            "shifts": [
                {
                    "shift_id": "sh_am",
                    "employee": "sam",
                    "date": day,
                    "start": "09:00",
                    "end": "17:00",
                    "meal_break_minutes": 30,
                }
            ],
        },
        chain=chain,
    )
    observed = observed_input_hashes(
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=avail,
    )
    result = compose_week(
        demand,
        availability=avail_windows,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
        parameter_shelf=shelf,
        observed_shelf_hashes=observed,
    )
    assert result.placed
    ruleset = load_ruleset()
    write_exhibits(
        result,
        chain=chain,
        decision_id="dec_shelf_1",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path / "exhibits" / "dec_shelf_1",
        timestamp="2026-09-29T19:05:00Z",
        history_root=history_root,
        parameter_shelf_id=shelf.parameter_shelf_id,
    )
    store = WeekHistoryStore(history_root)
    index = store.load_index()
    assert len(index.rows) == 1
    assert index.rows[0].parameter_shelf_id == shelf.parameter_shelf_id
    record = json.loads(
        (
            history_root
            / "weeks"
            / f"{WEEK_START.isoformat()}_dec_shelf_1"
            / "record.json"
        ).read_text(encoding="utf-8")
    )
    assert record["parameter_shelf_id"] == shelf.parameter_shelf_id
