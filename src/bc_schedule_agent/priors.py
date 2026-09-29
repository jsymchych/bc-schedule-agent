"""Deterministic composition priors from active week history (soft bias only)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any

from bc_schedule_agent.history import HistoryIndex, HistoryRow, WeekHistoryStore


def _window_key(start: time | str, end: time | str) -> str:
    def _fmt(value: time | str) -> str:
        if isinstance(value, time):
            return value.isoformat(timespec="minutes")
        text = str(value)
        # Accept HH:MM or HH:MM:SS
        parts = text.split(":")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"

    return f"{_fmt(start)}-{_fmt(end)}"


def _parse_time(value: str) -> time:
    parts = str(value).split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour, minute)


@dataclass
class HistoryPriors:
    """Soft prior bag. Never hard-refuses; shelf + ESA stay authoritative."""

    week_starts: list[str] = field(default_factory=list)
    # employee -> ISO weekday (Mon=0 … Sun=6) -> count
    day_affinity: dict[str, dict[int, int]] = field(default_factory=dict)
    # employee -> "HH:MM-HH:MM" -> count
    window_affinity: dict[str, dict[str, int]] = field(default_factory=dict)
    # ISO weekday -> "HH:MM-HH:MM" -> coverage count (band shape summary)
    coverage_bands: dict[int, dict[str, int]] = field(default_factory=dict)
    # Telemetry only — never drives placement refuses
    ot_reasons: list[str] = field(default_factory=list)

    def candidate_score(
        self,
        employee: str,
        *,
        on: date,
        start: time,
        end: time,
    ) -> float:
        """Higher = more familiar pattern for this employee / day / window."""
        weekday = on.weekday()
        day_hits = int(self.day_affinity.get(employee, {}).get(weekday, 0))
        window = _window_key(start, end)
        window_hits = int(self.window_affinity.get(employee, {}).get(window, 0))
        # Overlap credit: any historical window for this employee on this weekday
        # that intersects the ask (bounded soft signal).
        overlap = 0.0
        for key, count in self.window_affinity.get(employee, {}).items():
            try:
                left, right = key.split("-", 1)
                hist_start = _parse_time(left)
                hist_end = _parse_time(right)
            except (TypeError, ValueError):
                continue
            if hist_end <= start or end <= hist_start:
                continue
            overlap += float(count)
        return float(day_hits) * 10.0 + float(window_hits) * 5.0 + overlap

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_starts": list(self.week_starts),
            "day_affinity": {
                emp: {str(k): v for k, v in days.items()}
                for emp, days in self.day_affinity.items()
            },
            "window_affinity": {
                emp: dict(windows) for emp, windows in self.window_affinity.items()
            },
            "coverage_bands": {
                str(day): dict(bands) for day, bands in self.coverage_bands.items()
            },
            "ot_reasons": list(self.ot_reasons),
        }


def _bump_nested_int(bucket: dict[Any, dict[Any, int]], outer: Any, inner: Any) -> None:
    inner_map = bucket.setdefault(outer, {})
    inner_map[inner] = int(inner_map.get(inner, 0)) + 1


def record_placement(
    priors: HistoryPriors,
    *,
    employee: str,
    on: date,
    start: time | str,
    end: time | str,
) -> None:
    """Accumulate one historical placement into the bag."""
    weekday = on.weekday() if isinstance(on, date) else date.fromisoformat(str(on)).weekday()
    on_date = on if isinstance(on, date) else date.fromisoformat(str(on))
    start_t = start if isinstance(start, time) else _parse_time(str(start))
    end_t = end if isinstance(end, time) else _parse_time(str(end))
    window = _window_key(start_t, end_t)
    _bump_nested_int(priors.day_affinity, employee, weekday)
    _bump_nested_int(priors.window_affinity, employee, window)
    _bump_nested_int(priors.coverage_bands, weekday, window)
    # Keep week_starts caller-owned; this helper only mutates affinity maps.
    _ = on_date  # date normalized for weekday above


def priors_from_placements(
    placements: list[dict[str, Any]],
    *,
    week_starts: list[str] | None = None,
    ot_reasons: list[str] | None = None,
) -> HistoryPriors:
    """Build a prior bag from in-memory placement rows (tests / synthetic)."""
    priors = HistoryPriors(
        week_starts=list(week_starts or []),
        ot_reasons=list(ot_reasons or []),
    )
    for row in placements:
        record_placement(
            priors,
            employee=str(row["employee"]),
            on=row["date"] if isinstance(row["date"], date) else date.fromisoformat(str(row["date"])),
            start=row["start"],
            end=row["end"],
        )
    return priors


def _ingest_audit_events(priors: HistoryPriors, events: list[dict[str, Any]]) -> None:
    for event in events:
        kind = event.get("kind")
        subject = dict(event.get("subject") or {})
        evidence = dict(event.get("evidence") or {})
        if kind == "place":
            employee = subject.get("employee")
            on = subject.get("date")
            start = evidence.get("start")
            end = evidence.get("end")
            if employee and on and start and end:
                record_placement(
                    priors,
                    employee=str(employee),
                    on=date.fromisoformat(str(on)),
                    start=str(start),
                    end=str(end),
                )
        elif kind in ("ot_proposed", "ot_unavoidable", "ot_approved"):
            reason = evidence.get("reason_unavoidable") or evidence.get("detail")
            if reason:
                text = str(reason)
                if text not in priors.ot_reasons:
                    priors.ot_reasons.append(text)


def _audit_path_for_row(store: WeekHistoryStore, row: HistoryRow) -> Path | None:
    week_path = store.root / row.week_dir
    durable = week_path / "audit.json"
    if durable.is_file():
        return durable
    listed = row.exhibit_paths.get("audit_json")
    if listed:
        path = Path(listed)
        if path.is_file():
            return path
    return None


def build_history_priors(
    store: WeekHistoryStore,
    *,
    index: HistoryIndex | None = None,
) -> HistoryPriors:
    """Build priors from **active** index rows only (1…history_max_weeks).

    Overflow weeks already dropped from the index do not contribute.
    """
    active = index if index is not None else store.load_index()
    priors = HistoryPriors(week_starts=[r.week_start for r in active.rows])
    for row in active.rows:
        audit_path = _audit_path_for_row(store, row)
        if audit_path is None:
            continue
        raw = json.loads(audit_path.read_text(encoding="utf-8"))
        events = list(raw.get("events") or [])
        _ingest_audit_events(priors, events)
    return priors


def order_candidates(
    preferred: str,
    roster: list[str],
    *,
    on: date,
    start: time,
    end: time,
    history_priors: HistoryPriors | None,
) -> list[str]:
    """Preferred first; remaining roster sorted by soft affinity (desc), then roster order."""
    rest = [e for e in roster if e != preferred]
    if history_priors is None:
        return [preferred] + rest

    def _key(employee: str) -> tuple[float, int]:
        score = history_priors.candidate_score(
            employee, on=on, start=start, end=end
        )
        try:
            roster_i = roster.index(employee)
        except ValueError:
            roster_i = 10_000
        return (-score, roster_i)

    rest_sorted = sorted(rest, key=_key)
    return [preferred] + rest_sorted
