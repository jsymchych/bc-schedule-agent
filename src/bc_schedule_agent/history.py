"""Rolling week history shelf — index + per-week dirs under artifacts/history/."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_SCHEMA_VERSION = "1"
HISTORY_MAX_WEEKS_FLOOR = 1
HISTORY_MAX_WEEKS_CEILING = 52
DEFAULT_HISTORY_MAX_WEEKS = 8


def clamp_history_max_weeks(value: int) -> int:
    """Hard floor 1, hard ceiling 52."""
    return max(HISTORY_MAX_WEEKS_FLOOR, min(HISTORY_MAX_WEEKS_CEILING, int(value)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_history_root() -> Path:
    """Home artifacts/history — survives demo_app restart."""
    return Path(__file__).resolve().parents[2] / "artifacts" / "history"


def week_dirname(week_start: date | str, decision_id: str) -> str:
    ws = week_start.isoformat() if isinstance(week_start, date) else str(week_start)
    return f"{ws}_{decision_id}"


@dataclass
class HistoryRow:
    """One issued week on the active shelf (and on disk under weeks/)."""

    week_start: str
    decision_id: str
    week_dir: str
    exhibit_paths: dict[str, str] = field(default_factory=dict)
    schedule_hash: str | None = None
    gate_snapshot_hash: str | None = None
    # Placeholders for later waves (replay envelope, composition priors).
    parameter_shelf_id: str | None = None
    history_prior_week_starts: list[str] = field(default_factory=list)
    issued_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HistoryRow:
        return cls(
            week_start=str(raw["week_start"]),
            decision_id=str(raw["decision_id"]),
            week_dir=str(raw["week_dir"]),
            exhibit_paths={
                str(k): str(v) for k, v in dict(raw.get("exhibit_paths") or {}).items()
            },
            schedule_hash=raw.get("schedule_hash"),
            gate_snapshot_hash=raw.get("gate_snapshot_hash"),
            parameter_shelf_id=raw.get("parameter_shelf_id"),
            history_prior_week_starts=[
                str(x) for x in list(raw.get("history_prior_week_starts") or [])
            ],
            issued_at=raw.get("issued_at"),
        )


@dataclass
class HistoryIndex:
    schema_version: str = HISTORY_SCHEMA_VERSION
    history_max_weeks: int = DEFAULT_HISTORY_MAX_WEEKS
    rows: list[HistoryRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "history_max_weeks": self.history_max_weeks,
            "rows": [r.to_dict() for r in self.rows],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HistoryIndex:
        max_weeks = clamp_history_max_weeks(
            int(raw.get("history_max_weeks", DEFAULT_HISTORY_MAX_WEEKS))
        )
        rows = [HistoryRow.from_dict(r) for r in list(raw.get("rows") or [])]
        return cls(
            schema_version=str(raw.get("schema_version") or HISTORY_SCHEMA_VERSION),
            history_max_weeks=max_weeks,
            rows=rows,
        )


class WeekHistoryStore:
    """Disk-backed rolling index. Active rows = min(issued_count, history_max_weeks)."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        history_max_weeks: int = DEFAULT_HISTORY_MAX_WEEKS,
    ) -> None:
        self.root = Path(root) if root is not None else default_history_root()
        self.weeks_dir = self.root / "weeks"
        self.index_path = self.root / "index.json"
        self.history_max_weeks = clamp_history_max_weeks(history_max_weeks)

    def ensure_layout(self) -> None:
        self.weeks_dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> HistoryIndex:
        """Reload from disk — no process memory required after restart."""
        if not self.index_path.is_file():
            return HistoryIndex(history_max_weeks=self.history_max_weeks)
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        index = HistoryIndex.from_dict(raw)
        # Caller-configured max wins when reopening a fresh store against an older index.
        index.history_max_weeks = self.history_max_weeks
        return index

    def save_index(self, index: HistoryIndex) -> None:
        self.ensure_layout()
        index.history_max_weeks = clamp_history_max_weeks(index.history_max_weeks)
        payload = index.to_dict()
        self.index_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def active_size(self) -> int:
        return len(self.load_index().rows)

    def record_issue(
        self,
        *,
        week_start: date | str,
        decision_id: str,
        exhibit_paths: dict[str, str] | None = None,
        schedule_hash: str | None = None,
        gate_snapshot_hash: str | None = None,
        parameter_shelf_id: str | None = None,
        issued_at: str | None = None,
    ) -> HistoryRow:
        """Append (or replace same week_start+decision_id) and enforce rolling max.

        Draft-only sessions never call this — only successful issue paths.
        Overflow drops oldest from the active index; week dirs may remain on disk.
        """
        self.ensure_layout()
        ws = week_start.isoformat() if isinstance(week_start, date) else str(week_start)
        dirname = week_dirname(ws, decision_id)
        week_path = self.weeks_dir / dirname
        week_path.mkdir(parents=True, exist_ok=True)

        relative_dir = f"weeks/{dirname}"
        paths = {str(k): str(v) for k, v in dict(exhibit_paths or {}).items()}
        index = self.load_index()
        prior_starts = [r.week_start for r in index.rows]

        row = HistoryRow(
            week_start=ws,
            decision_id=decision_id,
            week_dir=relative_dir,
            exhibit_paths=paths,
            schedule_hash=schedule_hash,
            gate_snapshot_hash=gate_snapshot_hash,
            parameter_shelf_id=parameter_shelf_id,
            history_prior_week_starts=list(prior_starts),
            issued_at=issued_at or _utc_now(),
        )

        (week_path / "record.json").write_text(
            json.dumps(row.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Replace existing row with same week_start + decision_id; else append.
        replaced = False
        new_rows: list[HistoryRow] = []
        for existing in index.rows:
            if (
                existing.week_start == row.week_start
                and existing.decision_id == row.decision_id
            ):
                new_rows.append(row)
                replaced = True
            else:
                new_rows.append(existing)
        if not replaced:
            new_rows.append(row)

        # Rolling window: keep newest history_max_weeks by issuance order.
        max_weeks = clamp_history_max_weeks(self.history_max_weeks)
        if len(new_rows) > max_weeks:
            new_rows = new_rows[-max_weeks:]

        index.rows = new_rows
        index.history_max_weeks = max_weeks
        self.save_index(index)
        return row
