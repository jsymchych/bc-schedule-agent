"""Shared domain models for ingest and composer (Wave B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import uuid4

TimeOffStatus = Literal["APPROVED", "PENDING", "DENIED"]
Regime = Literal["standard", "averaging"]


WEEKDAY_NAMES = {
    "sunday": 0,
    "sun": 0,
    "monday": 1,
    "mon": 1,
    "tuesday": 2,
    "tue": 2,
    "wednesday": 3,
    "wed": 3,
    "thursday": 4,
    "thu": 4,
    "friday": 5,
    "fri": 5,
    "saturday": 6,
    "sat": 6,
}


def parse_hhmm(value: str) -> time:
    text = value.strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]))


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def sunday_of(week_start: date) -> date:
    """Normalize to ESA week start (Sunday). Accepts a Sunday or any day in that week."""
    # Python: Monday=0 … Sunday=6. ESA Sunday-first → offset from Sunday.
    return week_start - timedelta(days=(week_start.weekday() + 1) % 7)


def week_dates(week_start: date) -> list[date]:
    start = sunday_of(week_start)
    return [start + timedelta(days=i) for i in range(7)]


def hours_between(start: time, end: time) -> float:
    """Hours from start to end on the same calendar day (end after start)."""
    start_m = start.hour * 60 + start.minute
    end_m = end.hour * 60 + end.minute
    if end_m <= start_m:
        raise ValueError("end must be after start on the same day")
    return (end_m - start_m) / 60.0


@dataclass(frozen=True)
class AvailabilityWindow:
    employee: str
    start: time
    end: time
    date: date | None = None
    weekday: int | None = None  # 0=Sunday … 6=Saturday

    def covers(self, on: date, shift_start: time, shift_end: time) -> bool:
        if self.date is not None and self.date != on:
            return False
        if self.weekday is not None and ((on.weekday() + 1) % 7) != self.weekday:
            return False
        return self.start <= shift_start and shift_end <= self.end


@dataclass
class TimeOffRequest:
    """Time-off row. Status mutates only via human timeoff_decided."""

    request_id: str
    employee: str
    start: date
    end: date
    status: TimeOffStatus

    def covers(self, on: date) -> bool:
        return self.start <= on <= self.end

    def blocks(self, on: date) -> bool:
        return self.status == "APPROVED" and self.covers(on)

    def awaits(self, on: date) -> bool:
        return self.status == "PENDING" and self.covers(on)


@dataclass(frozen=True)
class DaySchedule:
    """One day in an averaging agreement work schedule."""

    date: date
    scheduled_hours: float
    start: time | None = None
    end: time | None = None


@dataclass
class AveragingPacket:
    packet_id: str
    employee: str
    in_writing: bool
    employer_signed: bool
    employee_signed: bool
    signed_before_start: bool
    period_weeks: int | None
    daily_schedule: list[DaySchedule]
    repeat_count: int | None  # None = term missing; 0 is valid
    start_date: date | None
    expiry_date: date | None
    copy_received_before_start: bool
    employer_signature_date: date | None = None
    employee_signature_date: date | None = None
    copy_received_date: date | None = None
    schedule_change_note: str | None = None

    def scheduled_hours_for(self, on: date) -> float | None:
        for day in self.daily_schedule:
            if day.date == on:
                return day.scheduled_hours
        return None

    def total_scheduled_hours(self) -> float:
        return sum(d.scheduled_hours for d in self.daily_schedule)


@dataclass(frozen=True)
class HoursWindow:
    """One open/close row from hours-of-operation (weekday or date)."""

    open: time
    close: time
    date: date | None = None
    weekday: int | None = None  # 0=Sunday … 6=Saturday

    def applies(self, on: date) -> bool:
        if self.date is not None:
            return self.date == on
        if self.weekday is not None:
            return ((on.weekday() + 1) % 7) == self.weekday
        return False


@dataclass(frozen=True)
class SalesProjection:
    """One sales projection row (weekday or date). Amount is CAD."""

    amount: float
    date: date | None = None
    weekday: int | None = None  # 0=Sunday … 6=Saturday

    def applies(self, on: date) -> bool:
        if self.date is not None:
            return self.date == on
        if self.weekday is not None:
            return ((on.weekday() + 1) % 7) == self.weekday
        return False


@dataclass(frozen=True)
class CoverageShift:
    """One placement ask from a stored coverage demand (fixture or prior parse)."""

    shift_id: str
    employee: str
    date: date
    start: time
    end: time
    meal_break_minutes: int = 0  # unpaid meal break inside the span, if any

    @property
    def span_hours(self) -> float:
        return hours_between(self.start, self.end)

    @property
    def worked_hours(self) -> float:
        return max(0.0, self.span_hours - (self.meal_break_minutes / 60.0))


@dataclass(frozen=True)
class CoverageDemand:
    week_start: date
    shifts: tuple[CoverageShift, ...]
    source: str = "fixture"  # fixture | planner:<curve> | local_model:<name>
    demand_id: str = field(default_factory=lambda: f"dem_{uuid4().hex[:10]}")


@dataclass(frozen=True)
class PlacedShift:
    shift_id: str
    employee: str
    date: date
    start: time
    end: time
    worked_hours: float
    meal_break_minutes: int = 0


OtStatus = Literal["PENDING_APPROVAL", "APPROVED", "REFUSED"]


@dataclass
class OvertimeProposal:
    """OT / rest premium line. Status mutates only via human ot_approved / ot_refused."""

    employee: str
    date: date
    hours: float
    multiplier: float
    rule_id: str
    section: str
    status: OtStatus = "PENDING_APPROVAL"
    shift_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = field(default_factory=lambda: f"ot_{uuid4().hex[:10]}")
    decided_by: str | None = None
    decided_at: str | None = None


@dataclass
class ComposeResult:
    placed: list[PlacedShift] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)
    ot_proposals: list[OvertimeProposal] = field(default_factory=list)
    packet_status: dict[str, str] = field(default_factory=dict)  # employee -> accepted|rejected|none
    regime_by_employee: dict[str, Regime] = field(default_factory=dict)


def combine_dt(on: date, at: time) -> datetime:
    return datetime(on.year, on.month, on.day, at.hour, at.minute)
