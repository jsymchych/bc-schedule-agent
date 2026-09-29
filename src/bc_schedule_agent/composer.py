"""Composer: place shifts under closed-world availability; both OT regimes."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import (
    AvailabilityWindow,
    AveragingPacket,
    ComposeResult,
    CoverageDemand,
    CoverageShift,
    OvertimeProposal,
    PlacedShift,
    Regime,
    TimeOffRequest,
    combine_dt,
    sunday_of,
)
from bc_schedule_agent.packet import accept_or_reject_packet


def hours_between_times(start: time, end: time) -> float:
    """Hours from start to end on the same calendar day."""
    start_m = start.hour * 60 + start.minute
    end_m = end.hour * 60 + end.minute
    if end_m <= start_m:
        raise ValueError("end must be after start on the same day")
    return (end_m - start_m) / 60.0


def _covering_availability(
    windows: list[AvailabilityWindow],
    *,
    employee: str,
    on: date,
    start: time,
    end: time,
) -> AvailabilityWindow | None:
    for window in windows:
        if window.employee != employee:
            continue
        if window.covers(on, start, end):
            return window
    return None


def _blocking_time_off(
    requests: list[TimeOffRequest],
    *,
    employee: str,
    on: date,
) -> TimeOffRequest | None:
    for req in requests:
        if req.employee == employee and req.blocks(on):
            return req
    return None


def _pending_time_off(
    requests: list[TimeOffRequest],
    *,
    employee: str,
    on: date,
) -> TimeOffRequest | None:
    for req in requests:
        if req.employee == employee and req.awaits(on):
            return req
    return None


def _refuse(
    chain: AuditChain,
    result: ComposeResult,
    *,
    rule_id: str,
    section: str,
    shift: CoverageShift,
    detail: str,
    extra_subject: dict[str, Any] | None = None,
    extra_evidence: dict[str, Any] | None = None,
) -> None:
    subject = {
        "employee": shift.employee,
        "date": shift.date.isoformat(),
        "shift_id": shift.shift_id,
        **(extra_subject or {}),
    }
    evidence = {
        "section": section,
        "detail": detail,
        "start": shift.start.isoformat(timespec="minutes"),
        "end": shift.end.isoformat(timespec="minutes"),
        **(extra_evidence or {}),
    }
    chain.append(
        kind="rule_refuse",
        actor=f"rule:{rule_id}",
        subject=subject,
        evidence=evidence,
    )
    result.refused.append({"rule_id": rule_id, "section": section, **subject, **evidence})


def _check_hard_constraints(
    chain: AuditChain,
    result: ComposeResult,
    shift: CoverageShift,
    *,
    already_placed: list[PlacedShift],
) -> bool:
    """Return True if the shift may be placed. Hard refuses are not approvable."""
    worked = shift.worked_hours
    span = shift.span_hours

    # s.34(1) — reporting shift under 2 hours
    if worked < 2.0 - 1e-9:
        _refuse(
            chain,
            result,
            rule_id="bc-esa-s34-min-daily-hours",
            section="34(1)",
            shift=shift,
            detail=f"scheduled reporting shift under 2 hours ({worked:.2f}h)",
        )
        return False

    # s.34(2) — when day is scheduled over 8 hours, reporting shift must be ≥4h
    same_day_for_34 = [
        p
        for p in already_placed
        if p.employee == shift.employee and p.date == shift.date
    ]
    day_scheduled = sum(
        hours_between_times(p.start, p.end) for p in same_day_for_34
    ) + span
    if day_scheduled > 8.0 + 1e-9 and worked < 4.0 - 1e-9:
        _refuse(
            chain,
            result,
            rule_id="bc-esa-s34-min-daily-hours-long",
            section="34(2)",
            shift=shift,
            detail=(
                f"day scheduled over 8 hours ({day_scheduled:.2f}h) but "
                f"reporting shift under 4 hours ({worked:.2f}h)"
            ),
            extra_evidence={"day_scheduled_hours": day_scheduled},
        )
        return False

    # s.39 — hard refuse excessive daily hours (>16)
    day_worked = sum(p.worked_hours for p in same_day_for_34) + worked
    if day_worked > 16.0 + 1e-9:
        _refuse(
            chain,
            result,
            rule_id="bc-esa-s39-no-excessive-hours",
            section="39",
            shift=shift,
            detail=f"excessive daily hours ({day_worked:.2f}h > hard limit 16)",
            extra_evidence={"daily_hours": day_worked, "hard_limit": 16},
        )
        return False

    # s.32 — more than 5 consecutive hours without a 30-minute meal break
    if span > 5.0 + 1e-9 and shift.meal_break_minutes < 30:
        _refuse(
            chain,
            result,
            rule_id="bc-esa-s32-meal-break",
            section="32(1)",
            shift=shift,
            detail=(
                f"more than 5 consecutive hours without a 30-minute meal break "
                f"(span {span:.2f}h, break {shift.meal_break_minutes}m)"
            ),
        )
        return False

    # s.33 — split shift spanning more than 12 hours (same employee, same day)
    if same_day_for_34:
        day_starts = [p.start for p in same_day_for_34] + [shift.start]
        day_ends = [p.end for p in same_day_for_34] + [shift.end]
        earliest = min(day_starts)
        latest = max(day_ends)
        span_hours = (
            combine_dt(shift.date, latest) - combine_dt(shift.date, earliest)
        ).total_seconds() / 3600.0
        if span_hours > 12.0 + 1e-9:
            _refuse(
                chain,
                result,
                rule_id="bc-esa-s33-split-shift",
                section="33",
                shift=shift,
                detail=f"split shift spans {span_hours:.2f}h (max 12)",
            )
            return False

    # s.36(2) — less than 8 hours between shifts
    for prev in already_placed:
        if prev.employee != shift.employee:
            continue
        prev_end = combine_dt(prev.date, prev.end)
        this_start = combine_dt(shift.date, shift.start)
        gap = (this_start - prev_end).total_seconds() / 3600.0
        if 0 <= gap < 8.0 - 1e-9:
            _refuse(
                chain,
                result,
                rule_id="bc-esa-s36-between-shifts",
                section="36(2)",
                shift=shift,
                detail=f"less than 8 hours between shifts ({gap:.2f}h)",
                extra_evidence={"prior_shift_id": prev.shift_id},
            )
            return False
        # Also check reverse if this shift is earlier than a later placement (order-safe)
        this_end = combine_dt(shift.date, shift.end)
        prev_start = combine_dt(prev.date, prev.start)
        gap2 = (prev_start - this_end).total_seconds() / 3600.0
        if 0 <= gap2 < 8.0 - 1e-9:
            _refuse(
                chain,
                result,
                rule_id="bc-esa-s36-between-shifts",
                section="36(2)",
                shift=shift,
                detail=f"less than 8 hours between shifts ({gap2:.2f}h)",
                extra_evidence={"prior_shift_id": prev.shift_id},
            )
            return False

    return True


def _propose_ot(
    chain: AuditChain,
    result: ComposeResult,
    proposal: OvertimeProposal,
) -> None:
    chain.append(
        kind="ot_proposed",
        actor=f"rule:{proposal.rule_id}",
        subject={
            "employee": proposal.employee,
            "date": proposal.date.isoformat(),
            "shift_id": proposal.shift_id,
        },
        evidence={
            "proposal_id": proposal.proposal_id,
            "hours": proposal.hours,
            "multiplier": proposal.multiplier,
            "rule_id": proposal.rule_id,
            "section": proposal.section,
            "status": proposal.status,
            **proposal.evidence,
        },
    )
    result.ot_proposals.append(proposal)


def _standard_daily_ot(
    chain: AuditChain,
    result: ComposeResult,
    *,
    employee: str,
    on: date,
    daily_hours: float,
    shift_id: str | None,
) -> None:
    """ESA s.35 / s.40 standard regime daily OT proposals."""
    if daily_hours <= 8.0 + 1e-9:
        chain.append(
            kind="rule_pass",
            actor="rule:bc-esa-s35-daily-overtime-threshold",
            subject={"employee": employee, "date": on.isoformat()},
            evidence={"daily_hours": daily_hours, "section": "35(1)", "regime": "standard"},
        )
        return

    # Over 8 up to 12 → 1.5x; over 12 → 2x on the excess over 12, plus 1.5x on 8–12
    if daily_hours > 12.0 + 1e-9:
        hours_15 = 4.0  # 8→12
        hours_2 = daily_hours - 12.0
        _propose_ot(
            chain,
            result,
            OvertimeProposal(
                employee=employee,
                date=on,
                hours=hours_15,
                multiplier=1.5,
                rule_id="bc-esa-s40-overtime-rates",
                section="35 / 40",
                shift_id=shift_id,
                evidence={"band": "8_to_12", "regime": "standard"},
            ),
        )
        _propose_ot(
            chain,
            result,
            OvertimeProposal(
                employee=employee,
                date=on,
                hours=hours_2,
                multiplier=2.0,
                rule_id="bc-esa-s40-overtime-rates",
                section="35 / 40",
                shift_id=shift_id,
                evidence={"band": "over_12", "regime": "standard"},
            ),
        )
    else:
        _propose_ot(
            chain,
            result,
            OvertimeProposal(
                employee=employee,
                date=on,
                hours=daily_hours - 8.0,
                multiplier=1.5,
                rule_id="bc-esa-s40-overtime-rates",
                section="35 / 40",
                shift_id=shift_id,
                evidence={"band": "8_to_12", "regime": "standard"},
            ),
        )


def _averaging_daily_ot(
    chain: AuditChain,
    result: ComposeResult,
    *,
    employee: str,
    on: date,
    worked: float,
    scheduled: float,
    shift_id: str | None,
    packet_id: str,
) -> None:
    """ESA s.37 averaging daily OT. Matching agreed schedule up to 12h is straight time."""
    # Time within agreed schedule and ≤12 → straight (no OT event).
    # Over 12 in a day → 2x (s.37(4)).
    # Beyond agreed day: IGM s.37(6) —
    #   extra is OT at 1.5x only when that extra time is over 8 hours,
    #   or over the scheduled hours if 8+ were scheduled; 2x over 12.

    if worked > 12.0 + 1e-9:
        over12 = worked - 12.0
        _propose_ot(
            chain,
            result,
            OvertimeProposal(
                employee=employee,
                date=on,
                hours=over12,
                multiplier=2.0,
                rule_id="bc-esa-s37-ot-over-12-daily",
                section="37(4)",
                shift_id=shift_id,
                evidence={
                    "regime": "averaging",
                    "packet_id": packet_id,
                    "scheduled_hours": scheduled,
                    "worked_hours": worked,
                },
            ),
        )
        # Also time beyond agreed that falls in the 8–12 or scheduled band
        beyond = max(0.0, min(worked, 12.0) - scheduled)
        if beyond > 1e-9 and scheduled >= 8.0 - 1e-9:
            _propose_ot(
                chain,
                result,
                OvertimeProposal(
                    employee=employee,
                    date=on,
                    hours=beyond,
                    multiplier=1.5,
                    rule_id="bc-esa-s37-ot-beyond-agreed-day",
                    section="37(6)",
                    shift_id=shift_id,
                    evidence={
                        "regime": "averaging",
                        "packet_id": packet_id,
                        "scheduled_hours": scheduled,
                    },
                ),
            )
        return

    if worked <= scheduled + 1e-9:
        chain.append(
            kind="rule_pass",
            actor="rule:bc-esa-s37-ot-beyond-agreed-day",
            subject={"employee": employee, "date": on.isoformat()},
            evidence={
                "section": "37(3)",
                "regime": "averaging",
                "packet_id": packet_id,
                "scheduled_hours": scheduled,
                "worked_hours": worked,
                "straight_time": True,
            },
        )
        return

    # Beyond agreed schedule, still ≤12
    beyond = worked - scheduled
    # IGM: extra hour on a scheduled 4-hour day → straight time (no OT)
    # because the extra is not "over 8" and scheduled < 8.
    if scheduled < 8.0 - 1e-9 and worked <= 8.0 + 1e-9:
        chain.append(
            kind="rule_pass",
            actor="rule:bc-esa-s37-ot-beyond-agreed-day",
            subject={"employee": employee, "date": on.isoformat()},
            evidence={
                "section": "37(6)",
                "regime": "averaging",
                "packet_id": packet_id,
                "scheduled_hours": scheduled,
                "worked_hours": worked,
                "beyond_hours": beyond,
                "straight_time": True,
                "igm_example": "extra_on_sub_8_scheduled_day",
            },
        )
        return

    # Extra beyond agreed when scheduled ≥8, or when worked pushes past 8
    _propose_ot(
        chain,
        result,
        OvertimeProposal(
            employee=employee,
            date=on,
            hours=beyond,
            multiplier=1.5,
            rule_id="bc-esa-s37-ot-beyond-agreed-day",
            section="37(6)",
            shift_id=shift_id,
            evidence={
                "regime": "averaging",
                "packet_id": packet_id,
                "scheduled_hours": scheduled,
                "worked_hours": worked,
            },
        ),
    )


def _standard_weekly_ot(
    chain: AuditChain,
    result: ComposeResult,
    *,
    employee: str,
    hours_by_day: dict[date, float],
) -> None:
    """s.40(3): weekly 1.5x on hours over 40, counting only the first 8 each day."""
    countable = sum(min(h, 8.0) for h in hours_by_day.values())
    if countable <= 40.0 + 1e-9:
        return
    _propose_ot(
        chain,
        result,
        OvertimeProposal(
            employee=employee,
            date=max(hours_by_day),
            hours=countable - 40.0,
            multiplier=1.5,
            rule_id="bc-esa-s40-overtime-rates",
            section="40(3)",
            evidence={"regime": "standard", "weekly_countable_hours": countable},
        ),
    )


def _longest_weekly_rest_hours(
    shifts: list[PlacedShift],
    *,
    week_start: date,
) -> float:
    """Longest consecutive free hours inside Sunday–Saturday week boundary."""
    start = sunday_of(week_start)
    week_begin = datetime(start.year, start.month, start.day, 0, 0)
    week_end = week_begin + timedelta(days=7)
    intervals: list[tuple[datetime, datetime]] = []
    for shift in shifts:
        intervals.append(
            (combine_dt(shift.date, shift.start), combine_dt(shift.date, shift.end))
        )
    if not intervals:
        return 7 * 24.0
    intervals.sort(key=lambda pair: pair[0])
    # Merge overlapping / abutting work blocks
    merged: list[tuple[datetime, datetime]] = [intervals[0]]
    for a, b in intervals[1:]:
        last_a, last_b = merged[-1]
        if a <= last_b:
            merged[-1] = (last_a, max(last_b, b))
        else:
            merged.append((a, b))
    gaps: list[float] = []
    cursor = week_begin
    for a, b in merged:
        if a > cursor:
            gaps.append((a - cursor).total_seconds() / 3600.0)
        cursor = max(cursor, b)
    if week_end > cursor:
        gaps.append((week_end - cursor).total_seconds() / 3600.0)
    return max(gaps) if gaps else 0.0


def _weekly_rest_premium(
    chain: AuditChain,
    result: ComposeResult,
    *,
    employee: str,
    shifts: list[PlacedShift],
    week_start: date,
    rule_id: str,
    section: str,
    regime: str,
    packet_id: str | None = None,
) -> None:
    """s.36(1) / s.37(8)/(9): propose 1.5x when no 32h consecutive free interval."""
    longest = _longest_weekly_rest_hours(shifts, week_start=week_start)
    if longest >= 32.0 - 1e-9:
        chain.append(
            kind="rule_pass",
            actor=f"rule:{rule_id}",
            subject={"employee": employee, "week_start": sunday_of(week_start).isoformat()},
            evidence={
                "section": section,
                "longest_rest_hours": longest,
                "regime": regime,
                **({"packet_id": packet_id} if packet_id else {}),
            },
        )
        return
    # Premium applies to hours that invade the missing rest — use shortfall as hours.
    shortfall = 32.0 - longest
    _propose_ot(
        chain,
        result,
        OvertimeProposal(
            employee=employee,
            date=max((s.date for s in shifts), default=sunday_of(week_start)),
            hours=shortfall,
            multiplier=1.5,
            rule_id=rule_id,
            section=section,
            evidence={
                "regime": regime,
                "longest_rest_hours": longest,
                "required_rest_hours": 32,
                "rest_shortfall_hours": shortfall,
                **({"packet_id": packet_id} if packet_id else {}),
            },
        ),
    )


def _averaging_weekly_ot(
    chain: AuditChain,
    result: ComposeResult,
    *,
    employee: str,
    hours_by_day: dict[date, float],
    packet: AveragingPacket,
) -> None:
    """s.37(5): 1.5x on average weekly hours over 40, less daily OT already counted."""
    if not hours_by_day:
        return
    total = sum(hours_by_day.values())
    weeks = packet.period_weeks or 1
    # Draft week only: treat this week's hours against 40 for a 1-week packet,
    # or against 40 * weeks / weeks (=40 average) using this week's total as
    # the period sample when multi-week schedule isn't fully placed.
    average = total / 1.0 if weeks == 1 else total  # single draft week sample
    # For multi-week, ESA averages over the full period; without full period
    # placements, apply the weekly 40 threshold to this draft week's hours.
    threshold = 40.0
    if average <= threshold + 1e-9:
        chain.append(
            kind="rule_pass",
            actor="rule:bc-esa-s37-ot-average-weekly",
            subject={"employee": employee},
            evidence={
                "section": "37(5)",
                "regime": "averaging",
                "packet_id": packet.packet_id,
                "weekly_hours": total,
                "threshold": threshold,
            },
        )
        return
    # Remove daily OT hours already counted for this employee in this compose.
    daily_ot = sum(
        p.hours
        for p in result.ot_proposals
        if p.employee == employee
        and p.rule_id
        in (
            "bc-esa-s37-ot-over-12-daily",
            "bc-esa-s37-ot-beyond-agreed-day",
        )
    )
    excess = max(0.0, total - threshold - daily_ot)
    if excess <= 1e-9:
        chain.append(
            kind="rule_pass",
            actor="rule:bc-esa-s37-ot-average-weekly",
            subject={"employee": employee},
            evidence={
                "section": "37(5)",
                "regime": "averaging",
                "packet_id": packet.packet_id,
                "weekly_hours": total,
                "daily_ot_removed": daily_ot,
                "straight_after_daily_ot": True,
            },
        )
        return
    _propose_ot(
        chain,
        result,
        OvertimeProposal(
            employee=employee,
            date=max(hours_by_day),
            hours=excess,
            multiplier=1.5,
            rule_id="bc-esa-s37-ot-average-weekly",
            section="37(5)",
            evidence={
                "regime": "averaging",
                "packet_id": packet.packet_id,
                "weekly_hours": total,
                "daily_ot_removed": daily_ot,
                "period_weeks": packet.period_weeks,
            },
        ),
    )


def _daily_worked(
    placed: list[PlacedShift], *, employee: str, on: date
) -> float:
    return sum(p.worked_hours for p in placed if p.employee == employee and p.date == on)


def _weekly_countable(
    placed: list[PlacedShift], *, employee: str
) -> float:
    by_day: dict[date, float] = defaultdict(float)
    for p in placed:
        if p.employee == employee:
            by_day[p.date] += p.worked_hours
    return sum(min(h, 8.0) for h in by_day.values())


def _add_minutes(at: time, minutes: int) -> time:
    total = at.hour * 60 + at.minute + minutes
    if total < 0 or total > 24 * 60:
        raise ValueError("time out of day bounds")
    return time(total // 60, total % 60)


def _hard_constraints_ok(
    shift: CoverageShift,
    *,
    already_placed: list[PlacedShift],
) -> bool:
    """Silent hard-constraint check (no audit / refuse side effects)."""
    worked = shift.worked_hours
    span = shift.span_hours
    if worked < 2.0 - 1e-9:
        return False
    same_day = [
        p
        for p in already_placed
        if p.employee == shift.employee and p.date == shift.date
    ]
    day_scheduled = sum(hours_between_times(p.start, p.end) for p in same_day) + span
    if day_scheduled > 8.0 + 1e-9 and worked < 4.0 - 1e-9:
        return False
    day_worked = sum(p.worked_hours for p in same_day) + worked
    if day_worked > 16.0 + 1e-9:
        return False
    if span > 5.0 + 1e-9 and shift.meal_break_minutes < 30:
        return False
    if same_day:
        day_starts = [p.start for p in same_day] + [shift.start]
        day_ends = [p.end for p in same_day] + [shift.end]
        earliest = min(day_starts)
        latest = max(day_ends)
        span_hours = (
            combine_dt(shift.date, latest) - combine_dt(shift.date, earliest)
        ).total_seconds() / 3600.0
        if span_hours > 12.0 + 1e-9:
            return False
    for prev in already_placed:
        if prev.employee != shift.employee:
            continue
        prev_end = combine_dt(prev.date, prev.end)
        this_start = combine_dt(shift.date, shift.start)
        gap = (this_start - prev_end).total_seconds() / 3600.0
        if 0 <= gap < 8.0 - 1e-9:
            return False
        this_end = combine_dt(shift.date, shift.end)
        prev_start = combine_dt(prev.date, prev.start)
        gap2 = (prev_start - this_end).total_seconds() / 3600.0
        if 0 <= gap2 < 8.0 - 1e-9:
            return False
    return True


def _straight_time_ok(
    *,
    employee: str,
    on: date,
    add_worked: float,
    already_placed: list[PlacedShift],
) -> bool:
    """True when adding add_worked keeps daily ≤8 and weekly countable ≤40."""
    daily = _daily_worked(already_placed, employee=employee, on=on) + add_worked
    if daily > 8.0 + 1e-9:
        return False
    # Weekly countable uses min(day, 8); after this place the day contributes min(daily, 8).
    by_day: dict[date, float] = defaultdict(float)
    for p in already_placed:
        if p.employee == employee:
            by_day[p.date] += p.worked_hours
    by_day[on] = by_day.get(on, 0.0) + add_worked
    countable = sum(min(h, 8.0) for h in by_day.values())
    return countable <= 40.0 + 1e-9


def _roster_from_availability(availability: list[AvailabilityWindow]) -> list[str]:
    seen: list[str] = []
    for window in availability:
        if window.employee not in seen:
            seen.append(window.employee)
    return seen


def _eligible_employee(
    employee: str,
    shift: CoverageShift,
    *,
    availability: list[AvailabilityWindow],
    time_off: list[TimeOffRequest],
    already_placed: list[PlacedShift],
    require_straight: bool,
) -> bool:
    trial = CoverageShift(
        shift_id=shift.shift_id,
        employee=employee,
        date=shift.date,
        start=shift.start,
        end=shift.end,
        meal_break_minutes=shift.meal_break_minutes,
    )
    if _covering_availability(
        availability,
        employee=employee,
        on=shift.date,
        start=shift.start,
        end=shift.end,
    ) is None:
        return False
    if _pending_time_off(time_off, employee=employee, on=shift.date) is not None:
        return False
    if _blocking_time_off(time_off, employee=employee, on=shift.date) is not None:
        return False
    if not _hard_constraints_ok(trial, already_placed=already_placed):
        return False
    if require_straight and not _straight_time_ok(
        employee=employee,
        on=shift.date,
        add_worked=trial.worked_hours,
        already_placed=already_placed,
    ):
        return False
    return True


def _place_shift(
    chain: AuditChain,
    result: ComposeResult,
    shift: CoverageShift,
    *,
    employee: str,
    start: time | None = None,
    end: time | None = None,
    meal_break_minutes: int | None = None,
    shift_id: str | None = None,
) -> PlacedShift:
    start_t = start if start is not None else shift.start
    end_t = end if end is not None else shift.end
    meal = (
        shift.meal_break_minutes if meal_break_minutes is None else meal_break_minutes
    )
    sid = shift_id or shift.shift_id
    trial = CoverageShift(
        shift_id=sid,
        employee=employee,
        date=shift.date,
        start=start_t,
        end=end_t,
        meal_break_minutes=meal,
    )
    placed = PlacedShift(
        shift_id=sid,
        employee=employee,
        date=shift.date,
        start=start_t,
        end=end_t,
        worked_hours=trial.worked_hours,
        meal_break_minutes=meal,
    )
    if employee not in result.regime_by_employee:
        result.regime_by_employee[employee] = "standard"
    chain.append(
        kind="place",
        actor="agent",
        subject={
            "employee": placed.employee,
            "date": placed.date.isoformat(),
            "shift_id": placed.shift_id,
        },
        evidence={
            "start": placed.start.isoformat(timespec="minutes"),
            "end": placed.end.isoformat(timespec="minutes"),
            "worked_hours": placed.worked_hours,
            "regime": result.regime_by_employee[placed.employee],
        },
    )
    result.placed.append(placed)
    return placed


def _try_pass_a_full(
    chain: AuditChain,
    result: ComposeResult,
    shift: CoverageShift,
    *,
    availability: list[AvailabilityWindow],
    time_off: list[TimeOffRequest],
    roster: list[str],
) -> bool:
    """Pass A: place full shift on preferred or alternate without OT."""
    ordered = [shift.employee] + [e for e in roster if e != shift.employee]
    for employee in ordered:
        if not _eligible_employee(
            employee,
            shift,
            availability=availability,
            time_off=time_off,
            already_placed=result.placed,
            require_straight=True,
        ):
            continue
        if employee != shift.employee:
            chain.append(
                kind="repair",
                actor="agent",
                subject={
                    "shift_id": shift.shift_id,
                    "from_employee": shift.employee,
                    "to_employee": employee,
                    "date": shift.date.isoformat(),
                },
                evidence={
                    "detail": (
                        f"alternate {employee} placed instead of {shift.employee} "
                        "to keep straight time"
                    ),
                    "pass": "A",
                },
            )
        _place_shift(chain, result, shift, employee=employee)
        return True
    return False


def _try_pass_a_split(
    chain: AuditChain,
    result: ComposeResult,
    shift: CoverageShift,
    *,
    availability: list[AvailabilityWindow],
    time_off: list[TimeOffRequest],
    roster: list[str],
) -> bool:
    """Pass A bounded split: first employee takes ≤8h residual, second takes remainder."""
    span_minutes = int(round(hours_between_times(shift.start, shift.end) * 60))
    if span_minutes < 240:  # need room for two ≥2h reporting shifts
        return False

    ordered = [shift.employee] + [e for e in roster if e != shift.employee]
    for first in ordered:
        capacity = 8.0 - _daily_worked(
            result.placed, employee=first, on=shift.date
        )
        # First segment worked hours target (before meal): leave ≥2h for second.
        # Use span minutes for wall-clock split; meal assigned to longer segment.
        first_wall_h = min(capacity, hours_between_times(shift.start, shift.end) - 2.0)
        if first_wall_h < 2.0 - 1e-9:
            continue
        first_minutes = int(round(first_wall_h * 60))
        # Snap so second also ≥2h wall
        if span_minutes - first_minutes < 120:
            first_minutes = span_minutes - 120
        if first_minutes < 120:
            continue
        mid = _add_minutes(shift.start, first_minutes)
        meal_first = shift.meal_break_minutes if first_minutes >= (span_minutes - first_minutes) else 0
        meal_second = 0 if meal_first else shift.meal_break_minutes
        first_shift = CoverageShift(
            shift_id=f"{shift.shift_id}__a",
            employee=first,
            date=shift.date,
            start=shift.start,
            end=mid,
            meal_break_minutes=meal_first,
        )
        if not _eligible_employee(
            first,
            first_shift,
            availability=availability,
            time_off=time_off,
            already_placed=result.placed,
            require_straight=True,
        ):
            continue

        for second in ordered:
            if second == first:
                continue
            second_shift = CoverageShift(
                shift_id=f"{shift.shift_id}__b",
                employee=second,
                date=shift.date,
                start=mid,
                end=shift.end,
                meal_break_minutes=meal_second,
            )
            # Trial place first into a temp list for second eligibility
            trial_placed = list(result.placed)
            trial_placed.append(
                PlacedShift(
                    shift_id=first_shift.shift_id,
                    employee=first,
                    date=shift.date,
                    start=shift.start,
                    end=mid,
                    worked_hours=first_shift.worked_hours,
                    meal_break_minutes=meal_first,
                )
            )
            if not _eligible_employee(
                second,
                second_shift,
                availability=availability,
                time_off=time_off,
                already_placed=trial_placed,
                require_straight=True,
            ):
                continue
            chain.append(
                kind="repair",
                actor="agent",
                subject={
                    "shift_id": shift.shift_id,
                    "date": shift.date.isoformat(),
                    "split": [first, second],
                },
                evidence={
                    "detail": (
                        f"split {shift.shift_id} across {first} then {second} "
                        "to keep straight time"
                    ),
                    "pass": "A",
                    "mid": mid.isoformat(timespec="minutes"),
                },
            )
            _place_shift(
                chain,
                result,
                shift,
                employee=first,
                start=shift.start,
                end=mid,
                meal_break_minutes=meal_first,
                shift_id=first_shift.shift_id,
            )
            _place_shift(
                chain,
                result,
                shift,
                employee=second,
                start=mid,
                end=shift.end,
                meal_break_minutes=meal_second,
                shift_id=second_shift.shift_id,
            )
            return True
    return False


def _pass_b_place(
    chain: AuditChain,
    result: ComposeResult,
    shift: CoverageShift,
    *,
    availability: list[AvailabilityWindow],
    time_off: list[TimeOffRequest],
    roster: list[str],
    unavoidable_shift_ids: set[str],
) -> bool:
    """Pass B: place residual coverage even if OT; mark reason_unavoidable."""
    ordered = [shift.employee] + [e for e in roster if e != shift.employee]
    for employee in ordered:
        if not _eligible_employee(
            employee,
            shift,
            availability=availability,
            time_off=time_off,
            already_placed=result.placed,
            require_straight=False,
        ):
            continue
        reason = (
            f"residual coverage for {shift.shift_id} on {shift.date.isoformat()} "
            f"requires {employee}; no straight-time alternate or split remained"
        )
        chain.append(
            kind="ot_unavoidable",
            actor="agent",
            subject={
                "shift_id": shift.shift_id,
                "employee": employee,
                "date": shift.date.isoformat(),
            },
            evidence={"reason_unavoidable": reason, "pass": "B"},
        )
        _place_shift(chain, result, shift, employee=employee)
        unavoidable_shift_ids.add(shift.shift_id)
        return True

    # Nobody can take it even with OT — hard refuse like legacy availability miss.
    _refuse(
        chain,
        result,
        rule_id="availability-closed-world",
        section="availability",
        shift=shift,
        detail="no covering availability row after OT-min passes",
    )
    return False


def _apply_regime_ot(
    demand: CoverageDemand,
    *,
    chain: AuditChain,
    result: ComposeResult,
    accepted: dict[str, AveragingPacket],
    unavoidable_shift_ids: set[str] | None = None,
) -> None:
    """Regime OT after all placements. Stamp reason_unavoidable on Pass B lines."""
    unavoidable = unavoidable_shift_ids or set()
    by_emp_day: dict[str, dict[date, list[PlacedShift]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for p in result.placed:
        by_emp_day[p.employee][p.date].append(p)

    before = len(result.ot_proposals)
    for employee, days in by_emp_day.items():
        regime: Regime = result.regime_by_employee.get(employee, "standard")
        hours_by_day = {
            on: sum(s.worked_hours for s in shifts) for on, shifts in days.items()
        }
        packet = accepted.get(employee)

        for on, day_shifts in days.items():
            daily = hours_by_day[on]
            shift_id = day_shifts[-1].shift_id if day_shifts else None
            if regime == "averaging" and packet is not None:
                scheduled = packet.scheduled_hours_for(on)
                if scheduled is None:
                    _standard_daily_ot(
                        chain,
                        result,
                        employee=employee,
                        on=on,
                        daily_hours=daily,
                        shift_id=shift_id,
                    )
                else:
                    _averaging_daily_ot(
                        chain,
                        result,
                        employee=employee,
                        on=on,
                        worked=daily,
                        scheduled=scheduled,
                        shift_id=shift_id,
                        packet_id=packet.packet_id,
                    )
            else:
                _standard_daily_ot(
                    chain,
                    result,
                    employee=employee,
                    on=on,
                    daily_hours=daily,
                    shift_id=shift_id,
                )

        if regime == "standard":
            _standard_weekly_ot(
                chain, result, employee=employee, hours_by_day=hours_by_day
            )
            _weekly_rest_premium(
                chain,
                result,
                employee=employee,
                shifts=[s for day in days.values() for s in day],
                week_start=demand.week_start,
                rule_id="bc-esa-s36-weekly-rest",
                section="36(1)",
                regime="standard",
            )
        elif regime == "averaging" and packet is not None:
            _averaging_weekly_ot(
                chain,
                result,
                employee=employee,
                hours_by_day=hours_by_day,
                packet=packet,
            )
            rest_section = "37(8)" if (packet.period_weeks or 1) == 1 else "37(9)"
            _weekly_rest_premium(
                chain,
                result,
                employee=employee,
                shifts=[s for day in days.values() for s in day],
                week_start=demand.week_start,
                rule_id="bc-esa-s37-rest-premium",
                section=rest_section,
                regime="averaging",
                packet_id=packet.packet_id,
            )

    if unavoidable:
        for proposal in result.ot_proposals[before:]:
            sid = proposal.shift_id or ""
            base = sid.split("__", 1)[0] if sid else ""
            if sid in unavoidable or base in unavoidable:
                reason = (
                    f"residual coverage for {base or sid} on "
                    f"{proposal.date.isoformat()} requires overtime"
                )
                proposal.evidence = {
                    **proposal.evidence,
                    "reason_unavoidable": reason,
                }
                # Keep ot_proposed evidence in sync on the chain event already written:
                # stamp is on the proposal object for approve / exhibit consumers.


def compose_week(
    demand: CoverageDemand,
    *,
    availability: list[AvailabilityWindow],
    time_off: list[TimeOffRequest],
    chain: AuditChain,
    averaging_packets: list[AveragingPacket] | None = None,
    prefer_zero_ot: bool = False,
) -> ComposeResult:
    """Place demand shifts. Closed-world availability. Averaging only after packet_accepted.

    When ``prefer_zero_ot`` is True, Pass A places straight-time only (alternate
    employees / bounded split); Pass B proposes OT only for residual coverage
    with ``reason_unavoidable`` evidence.
    """
    result = ComposeResult()
    packets = averaging_packets or []
    accepted: dict[str, AveragingPacket] = {}

    for packet in packets:
        check = accept_or_reject_packet(
            packet, draft_week_start=demand.week_start, chain=chain
        )
        if check.accepted:
            accepted[packet.employee] = packet
            result.packet_status[packet.employee] = "accepted"
            result.regime_by_employee[packet.employee] = "averaging"
        else:
            result.packet_status[packet.employee] = "rejected"
            result.regime_by_employee[packet.employee] = "standard"

    if prefer_zero_ot:
        roster = _roster_from_availability(availability)
        residuals: list[CoverageShift] = []
        for shift in demand.shifts:
            if shift.employee not in result.regime_by_employee:
                result.regime_by_employee[shift.employee] = "standard"
            if _try_pass_a_full(
                chain,
                result,
                shift,
                availability=availability,
                time_off=time_off,
                roster=roster,
            ):
                continue
            if _try_pass_a_split(
                chain,
                result,
                shift,
                availability=availability,
                time_off=time_off,
                roster=roster,
            ):
                continue
            residuals.append(shift)

        unavoidable_ids: set[str] = set()
        for shift in residuals:
            _pass_b_place(
                chain,
                result,
                shift,
                availability=availability,
                time_off=time_off,
                roster=roster,
                unavoidable_shift_ids=unavoidable_ids,
            )
        _apply_regime_ot(
            demand,
            chain=chain,
            result=result,
            accepted=accepted,
            unavoidable_shift_ids=unavoidable_ids,
        )
        return result

    for shift in demand.shifts:
        if shift.employee not in result.regime_by_employee:
            result.regime_by_employee[shift.employee] = "standard"

        # Closed-world availability
        if _covering_availability(
            availability,
            employee=shift.employee,
            on=shift.date,
            start=shift.start,
            end=shift.end,
        ) is None:
            _refuse(
                chain,
                result,
                rule_id="availability-closed-world",
                section="availability",
                shift=shift,
                detail="no covering availability row",
            )
            continue

        waiting = _pending_time_off(
            time_off, employee=shift.employee, on=shift.date
        )
        if waiting is not None:
            _refuse(
                chain,
                result,
                rule_id="timeoff-pending",
                section="time_off",
                shift=shift,
                detail="pending time-off awaits human decision",
                extra_subject={"pending_request_id": waiting.request_id},
                extra_evidence={
                    "request_id": waiting.request_id,
                    "status": "PENDING",
                },
            )
            continue

        blocked = _blocking_time_off(
            time_off, employee=shift.employee, on=shift.date
        )
        if blocked is not None:
            _refuse(
                chain,
                result,
                rule_id="timeoff-approved",
                section="time_off",
                shift=shift,
                detail="approved time-off blocks placement",
                extra_subject={"blocking_request_id": blocked.request_id},
                extra_evidence={"request_id": blocked.request_id},
            )
            continue

        if not _check_hard_constraints(
            chain, result, shift, already_placed=result.placed
        ):
            continue

        placed = PlacedShift(
            shift_id=shift.shift_id,
            employee=shift.employee,
            date=shift.date,
            start=shift.start,
            end=shift.end,
            worked_hours=shift.worked_hours,
            meal_break_minutes=shift.meal_break_minutes,
        )
        chain.append(
            kind="place",
            actor="agent",
            subject={
                "employee": placed.employee,
                "date": placed.date.isoformat(),
                "shift_id": placed.shift_id,
            },
            evidence={
                "start": placed.start.isoformat(timespec="minutes"),
                "end": placed.end.isoformat(timespec="minutes"),
                "worked_hours": placed.worked_hours,
                "regime": result.regime_by_employee[placed.employee],
            },
        )
        result.placed.append(placed)

    _apply_regime_ot(
        demand, chain=chain, result=result, accepted=accepted, unavoidable_shift_ids=None
    )
    return result
