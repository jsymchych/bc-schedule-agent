"""Sheet parsers: availability, time-off, averaging packet, coverage demand."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, time
from pathlib import Path
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import (
    WEEKDAY_NAMES,
    AvailabilityWindow,
    AveragingPacket,
    CoverageDemand,
    CoverageShift,
    DaySchedule,
    TimeOffRequest,
    TimeOffStatus,
    parse_hhmm,
    parse_iso_date,
)


def content_hash(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _read_bytes(source: str | Path | bytes) -> tuple[bytes, str]:
    if isinstance(source, bytes):
        return source, "bytes"
    path = Path(source)
    data = path.read_bytes()
    return data, str(path)


def _csv_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    return rows


def parse_availability_sheet(
    source: str | Path | bytes,
    *,
    chain: AuditChain | None = None,
) -> list[AvailabilityWindow]:
    """Parse availability CSV: employee, date|weekday, start, end.

    Closed world: absence of a covering row means not available.
    """
    raw, label = _read_bytes(source)
    rows = _csv_rows(raw.decode("utf-8"))
    windows: list[AvailabilityWindow] = []
    for i, row in enumerate(rows):
        employee = row.get("employee") or row.get("name")
        if not employee:
            raise ValueError(f"availability row {i}: employee required")
        start = parse_hhmm(row["start"])
        end = parse_hhmm(row["end"])
        date_val: date | None = None
        weekday: int | None = None
        if row.get("date"):
            date_val = parse_iso_date(row["date"])
        elif row.get("weekday"):
            key = row["weekday"].lower()
            if key not in WEEKDAY_NAMES:
                raise ValueError(f"availability row {i}: unknown weekday {row['weekday']!r}")
            weekday = WEEKDAY_NAMES[key]
        else:
            raise ValueError(f"availability row {i}: date or weekday required")
        windows.append(
            AvailabilityWindow(
                employee=employee,
                start=start,
                end=end,
                date=date_val,
                weekday=weekday,
            )
        )
    if chain is not None:
        chain.append(
            kind="ingest",
            actor="agent",
            subject={"sheet": "availability", "rows": len(windows)},
            evidence={"input_hash": content_hash(raw), "source": label},
        )
    return windows


def parse_time_off_sheet(
    source: str | Path | bytes,
    *,
    chain: AuditChain | None = None,
) -> list[TimeOffRequest]:
    """Parse time-off CSV: request_id, employee, start, end, status."""
    raw, label = _read_bytes(source)
    rows = _csv_rows(raw.decode("utf-8"))
    requests: list[TimeOffRequest] = []
    for i, row in enumerate(rows):
        employee = row.get("employee")
        if not employee:
            raise ValueError(f"time-off row {i}: employee required")
        status_raw = (row.get("status") or "").upper()
        if status_raw not in ("APPROVED", "PENDING", "DENIED"):
            raise ValueError(f"time-off row {i}: status must be APPROVED|PENDING|DENIED")
        request_id = row.get("request_id") or row.get("id") or f"to_{i+1}"
        requests.append(
            TimeOffRequest(
                request_id=request_id,
                employee=employee,
                start=parse_iso_date(row["start"]),
                end=parse_iso_date(row["end"]),
                status=status_raw,  # type: ignore[arg-type]
            )
        )
    if chain is not None:
        chain.append(
            kind="ingest",
            actor="agent",
            subject={"sheet": "time_off", "rows": len(requests)},
            evidence={"input_hash": content_hash(raw), "source": label},
        )
    return requests


def _schedule_from_payload(raw_days: Any) -> list[DaySchedule]:
    if not isinstance(raw_days, list):
        raise ValueError("daily_schedule must be a list")
    days: list[DaySchedule] = []
    for item in raw_days:
        if not isinstance(item, dict):
            raise ValueError("daily_schedule entries must be objects")
        on = parse_iso_date(str(item["date"]))
        if "scheduled_hours" in item:
            hours = float(item["scheduled_hours"])
            start = parse_hhmm(item["start"]) if item.get("start") else None
            end = parse_hhmm(item["end"]) if item.get("end") else None
        elif item.get("start") and item.get("end"):
            start = parse_hhmm(item["start"])
            end = parse_hhmm(item["end"])
            from bc_schedule_agent.models import hours_between

            hours = hours_between(start, end)
        else:
            raise ValueError("daily_schedule day needs scheduled_hours or start/end")
        days.append(DaySchedule(date=on, scheduled_hours=hours, start=start, end=end))
    return days


def parse_averaging_packet(
    source: str | Path | bytes | dict[str, Any],
    *,
    chain: AuditChain | None = None,
) -> AveragingPacket:
    """Parse an averaging packet JSON with all s.37 terms present or explicitly false/missing."""
    if isinstance(source, dict):
        payload = source
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        label = "dict"
    else:
        raw, label = _read_bytes(source)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("averaging packet root must be an object")

    period_weeks = payload.get("period_weeks")
    if period_weeks is not None:
        period_weeks = int(period_weeks)

    repeat_count = payload.get("repeat_count", _MISSING)
    if repeat_count is _MISSING:
        repeat_parsed: int | None = None
    else:
        repeat_parsed = int(repeat_count)

    start_date = (
        parse_iso_date(payload["start_date"]) if payload.get("start_date") else None
    )
    expiry_date = (
        parse_iso_date(payload["expiry_date"]) if payload.get("expiry_date") else None
    )

    daily_raw = payload.get("daily_schedule") or []
    daily_schedule = _schedule_from_payload(daily_raw) if daily_raw else []

    packet = AveragingPacket(
        packet_id=str(payload.get("packet_id") or payload.get("id") or "agr_unknown"),
        employee=str(payload.get("employee") or ""),
        in_writing=bool(payload.get("in_writing", False)),
        employer_signed=bool(payload.get("employer_signed", False)),
        employee_signed=bool(payload.get("employee_signed", False)),
        signed_before_start=bool(payload.get("signed_before_start", False)),
        period_weeks=period_weeks,
        daily_schedule=daily_schedule,
        repeat_count=repeat_parsed,
        start_date=start_date,
        expiry_date=expiry_date,
        copy_received_before_start=bool(payload.get("copy_received_before_start", False)),
        employer_signature_date=(
            parse_iso_date(payload["employer_signature_date"])
            if payload.get("employer_signature_date")
            else None
        ),
        employee_signature_date=(
            parse_iso_date(payload["employee_signature_date"])
            if payload.get("employee_signature_date")
            else None
        ),
        copy_received_date=(
            parse_iso_date(payload["copy_received_date"])
            if payload.get("copy_received_date")
            else None
        ),
        schedule_change_note=payload.get("schedule_change_note"),
    )
    if chain is not None:
        chain.append(
            kind="ingest",
            actor="agent",
            subject={
                "sheet": "averaging_packet",
                "packet_id": packet.packet_id,
                "employee": packet.employee,
            },
            evidence={"input_hash": content_hash(raw), "source": label},
        )
    return packet


_MISSING = object()


def parse_coverage_demand(
    source: str | Path | bytes | dict[str, Any],
    *,
    chain: AuditChain | None = None,
) -> CoverageDemand:
    """Parse a stored coverage demand (fixture JSON). No live model in the gate."""
    if isinstance(source, dict):
        payload = source
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        label = "dict"
    else:
        raw, label = _read_bytes(source)
        payload = json.loads(raw.decode("utf-8"))

    week_start = parse_iso_date(str(payload["week_start"]))
    shifts_raw = payload.get("shifts") or []
    shifts: list[CoverageShift] = []
    for i, item in enumerate(shifts_raw):
        shifts.append(
            CoverageShift(
                shift_id=str(item.get("shift_id") or f"sh_{i+1}"),
                employee=str(item["employee"]),
                date=parse_iso_date(str(item["date"])),
                start=parse_hhmm(str(item["start"])),
                end=parse_hhmm(str(item["end"])),
                meal_break_minutes=int(item.get("meal_break_minutes") or 0),
            )
        )
    demand = CoverageDemand(
        week_start=week_start,
        shifts=tuple(shifts),
        source=str(payload.get("source") or "fixture"),
        demand_id=str(payload.get("demand_id") or f"dem_{content_hash(raw)[-10:]}"),
    )
    if chain is not None:
        chain.append(
            kind="parse",
            actor="agent",
            subject={"demand_id": demand.demand_id, "shifts": len(demand.shifts)},
            evidence={
                "input_hash": content_hash(raw),
                "source": demand.source,
                "demand": {
                    "week_start": demand.week_start.isoformat(),
                    "shift_ids": [s.shift_id for s in demand.shifts],
                },
            },
        )
    return demand


def time_off_status(value: str) -> TimeOffStatus:
    upper = value.upper()
    if upper not in ("APPROVED", "PENDING", "DENIED"):
        raise ValueError(f"bad time-off status: {value}")
    return upper  # type: ignore[return-value]
