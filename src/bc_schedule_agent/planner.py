"""Demand planner: hours-of-ops + sales → CoverageDemand via staffing curve."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.ingest import (
    content_hash,
    parse_hours_of_operation,
    parse_sales_projections,
)
from bc_schedule_agent.models import (
    CoverageDemand,
    CoverageShift,
    HoursWindow,
    SalesProjection,
    hours_between,
    sunday_of,
    week_dates,
)
from bc_schedule_agent.ruleset import load_ruleset
from bc_schedule_agent.staffing import StaffingCurve, load_staffing


def _pick_hours(windows: list[HoursWindow], on: date) -> HoursWindow | None:
    """Date-specific rows win over weekday rows."""
    dated = [w for w in windows if w.date is not None and w.applies(on)]
    if dated:
        return dated[0]
    weekly = [w for w in windows if w.weekday is not None and w.applies(on)]
    return weekly[0] if weekly else None


def _pick_sales(projections: list[SalesProjection], on: date) -> float:
    dated = [p for p in projections if p.date is not None and p.applies(on)]
    if dated:
        return dated[0].amount
    weekly = [p for p in projections if p.weekday is not None and p.applies(on)]
    return weekly[0].amount if weekly else 0.0


def _source_bytes(source: str | Path | bytes | list) -> bytes | None:
    """Return raw bytes when the caller passed a sheet source (not a parsed list)."""
    if isinstance(source, list):
        return None
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def plan_coverage(
    ops: str | Path | bytes | list[HoursWindow],
    sales: str | Path | bytes | list[SalesProjection],
    week_start: date,
    *,
    staffing: StaffingCurve | None = None,
    chain: AuditChain | None = None,
) -> CoverageDemand:
    """Map ops + sales through the staffing curve into a CoverageDemand.

    Emits one open-hours block per headcount slot. Slot employees are
    ``{slot_prefix}_{n}`` placeholders; composer (later tickets) places people.
    """
    curve = staffing or load_staffing()
    start = sunday_of(week_start)

    ops_raw = _source_bytes(ops)
    sales_raw = _source_bytes(sales)

    if isinstance(ops, list):
        hours = list(ops)
    else:
        hours = parse_hours_of_operation(ops, chain=chain)

    if isinstance(sales, list):
        projections = list(sales)
    else:
        projections = parse_sales_projections(sales, chain=chain)

    shifts: list[CoverageShift] = []
    day_summary: list[dict] = []
    for on in week_dates(start):
        window = _pick_hours(hours, on)
        if window is None:
            continue
        amount = _pick_sales(projections, on)
        band = curve.band_for_sales(amount)
        span = hours_between(window.open, window.close)
        meal = (
            curve.meal_break_minutes
            if span > curve.meal_break_after_hours
            else 0
        )
        for slot in range(1, band.headcount + 1):
            emp = f"{curve.slot_prefix}_{slot}"
            shifts.append(
                CoverageShift(
                    shift_id=f"sh_{on.isoformat()}_{emp}",
                    employee=emp,
                    date=on,
                    start=window.open,
                    end=window.close,
                    meal_break_minutes=meal,
                )
            )
        day_summary.append(
            {
                "date": on.isoformat(),
                "sales": amount,
                "band": band.id,
                "headcount": band.headcount,
                "open": window.open.strftime("%H:%M"),
                "close": window.close.strftime("%H:%M"),
            }
        )

    demand = CoverageDemand(
        week_start=start,
        shifts=tuple(shifts),
        source=f"planner:{curve.version}",
        demand_id=f"dem_plan_{start.isoformat()}",
    )

    if chain is not None:
        esa = load_ruleset()
        evidence: dict = {
            "staffing_hash": curve.content_hash,
            "ruleset_hash": esa.content_hash,
            "staffing_version": curve.version,
            "ruleset_version": esa.version,
            "week_start": start.isoformat(),
            "days": day_summary,
            "shift_ids": [s.shift_id for s in demand.shifts],
        }
        if ops_raw is not None:
            evidence["ops_hash"] = content_hash(ops_raw)
        if sales_raw is not None:
            evidence["sales_hash"] = content_hash(sales_raw)
        chain.append(
            kind="plan",
            actor="agent",
            subject={
                "demand_id": demand.demand_id,
                "shifts": len(demand.shifts),
                "source": demand.source,
            },
            evidence=evidence,
        )
    return demand
