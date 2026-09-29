"""Wave A: hours/sales ingest, staffing curve, plan_coverage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.ingest import parse_hours_of_operation, parse_sales_projections
from bc_schedule_agent.planner import plan_coverage
from bc_schedule_agent.ruleset import load_ruleset, ruleset_hash
from bc_schedule_agent.staffing import load_staffing

WEEK_START = date(2026, 9, 27)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "planner"


def _ops_csv(*rows: str) -> bytes:
    header = "weekday,open,close\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _sales_csv(*rows: str) -> bytes:
    header = "weekday,sales\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def test_staffing_hash_stable() -> None:
    first = load_staffing()
    second = load_staffing()
    assert first.version == "staffing_demo_v1"
    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:")
    import json

    raw = json.loads(first.path.read_text(encoding="utf-8"))
    assert ruleset_hash(raw) == first.content_hash


def test_hours_of_operation_weekday_parse() -> None:
    chain = AuditChain()
    windows = parse_hours_of_operation(
        _ops_csv("monday,09:00,17:00", "friday,10:00,18:00"),
        chain=chain,
    )
    assert len(windows) == 2
    assert windows[0].weekday == 1
    assert windows[0].open.hour == 9
    assert chain.events[0].kind == "ingest"
    assert chain.events[0].subject["sheet"] == "hours_of_operation"
    assert "input_hash" in chain.events[0].evidence


def test_sales_projections_parse() -> None:
    chain = AuditChain()
    rows = parse_sales_projections(
        _sales_csv("monday,1500", "saturday,5000"),
        chain=chain,
    )
    assert len(rows) == 2
    assert rows[0].amount == 1500.0
    assert chain.events[0].kind == "ingest"
    assert chain.events[0].subject["sheet"] == "sales_projections"


def test_plan_coverage_sales_changes_demand() -> None:
    ops = _ops_csv(
        "monday,09:00,17:30",
        "tuesday,09:00,17:30",
        "wednesday,09:00,17:30",
        "thursday,09:00,17:30",
        "friday,09:00,17:30",
    )
    quiet = _sales_csv(
        "monday,500",
        "tuesday,500",
        "wednesday,500",
        "thursday,500",
        "friday,500",
    )
    peak = _sales_csv(
        "monday,5000",
        "tuesday,5000",
        "wednesday,5000",
        "thursday,5000",
        "friday,5000",
    )
    chain_q = AuditChain()
    chain_p = AuditChain()
    quiet_demand = plan_coverage(ops, quiet, WEEK_START, chain=chain_q)
    peak_demand = plan_coverage(ops, peak, WEEK_START, chain=chain_p)

    assert quiet_demand.source.startswith("planner:")
    assert peak_demand.source.startswith("planner:")
    assert len(peak_demand.shifts) > len(quiet_demand.shifts)
    # quiet band → 1 headcount × 5 open weekdays; peak → 4
    assert len(quiet_demand.shifts) == 5
    assert len(peak_demand.shifts) == 20

    plan_q = next(e for e in chain_q.events if e.kind == "plan")
    plan_p = next(e for e in chain_p.events if e.kind == "plan")
    staffing = load_staffing()
    esa = load_ruleset()
    assert plan_q.evidence["staffing_hash"] == staffing.content_hash
    assert plan_p.evidence["staffing_hash"] == staffing.content_hash
    assert plan_q.evidence["ruleset_hash"] == esa.content_hash
    assert plan_p.evidence["ruleset_hash"] == esa.content_hash
    assert plan_q.evidence["ops_hash"]
    assert plan_q.evidence["sales_hash"] != plan_p.evidence["sales_hash"]


def test_plan_coverage_fixture_sheets() -> None:
    chain = AuditChain()
    demand = plan_coverage(
        FIXTURES / "hours_of_operation.csv",
        FIXTURES / "sales_projections.csv",
        WEEK_START,
        chain=chain,
    )
    assert demand.week_start == WEEK_START
    assert len(demand.shifts) > 0
    kinds = [e.kind for e in chain.events]
    assert kinds.count("ingest") == 2
    assert "plan" in kinds
    plan = next(e for e in chain.events if e.kind == "plan")
    assert plan.evidence["staffing_hash"].startswith("sha256:")
    assert plan.evidence["ruleset_hash"].startswith("sha256:")


def test_plan_coverage_closed_day_skipped() -> None:
    ops = _ops_csv("monday,09:00,17:00")  # only Monday open
    sales = _sales_csv("monday,1200", "tuesday,5000")
    demand = plan_coverage(ops, sales, WEEK_START)
    dates = {s.date for s in demand.shifts}
    assert dates == {date(2026, 9, 28)}  # Monday only
