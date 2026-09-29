"""Wave A: week history store — index, append on issue, rolling max, restart."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import write_exhibits
from bc_schedule_agent.history import (
    DEFAULT_HISTORY_MAX_WEEKS,
    HISTORY_MAX_WEEKS_CEILING,
    HISTORY_MAX_WEEKS_FLOOR,
    WeekHistoryStore,
    clamp_history_max_weeks,
)
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    return ("employee,date,start,end\n" + "\n".join(rows) + "\n").encode("utf-8")


def _issue_clean_week(
    tmp_path: Path,
    *,
    week_start: date,
    decision_id: str,
    history_root: Path,
    history_max_weeks: int = DEFAULT_HISTORY_MAX_WEEKS,
):
    chain = AuditChain()
    day = (week_start + timedelta(days=1)).isoformat()  # Monday
    avail_raw = _avail_csv(f"sam,{day},09:00,17:00")
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand_payload = {
        "week_start": week_start.isoformat(),
        "source": "fixture",
        "demand_id": f"dem_{decision_id}",
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
    }
    demand = parse_coverage_demand(demand_payload, chain=chain)
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


def test_clamp_history_max_weeks_floor_and_ceiling():
    assert clamp_history_max_weeks(0) == HISTORY_MAX_WEEKS_FLOOR
    assert clamp_history_max_weeks(-3) == HISTORY_MAX_WEEKS_FLOOR
    assert clamp_history_max_weeks(1) == 1
    assert clamp_history_max_weeks(52) == HISTORY_MAX_WEEKS_CEILING
    assert clamp_history_max_weeks(99) == HISTORY_MAX_WEEKS_CEILING


def test_first_issue_yields_history_size_one(tmp_path: Path):
    history_root = tmp_path / "history"
    _issue_clean_week(
        tmp_path,
        week_start=WEEK_START,
        decision_id="dec_hist_1",
        history_root=history_root,
    )
    store = WeekHistoryStore(history_root)
    index = store.load_index()
    assert len(index.rows) == 1
    assert index.rows[0].week_start == WEEK_START.isoformat()
    assert index.rows[0].decision_id == "dec_hist_1"
    assert (history_root / "index.json").is_file()
    week_dir = history_root / "weeks" / f"{WEEK_START.isoformat()}_dec_hist_1"
    assert (week_dir / "record.json").is_file()
    assert index.rows[0].exhibit_paths["pdf"].endswith("dec_hist_1.pdf")
    assert index.rows[0].schedule_hash
    assert index.rows[0].gate_snapshot_hash
    # Placeholders present for later waves
    assert index.rows[0].parameter_shelf_id is None


def test_append_grows_active_index(tmp_path: Path):
    history_root = tmp_path / "history"
    _issue_clean_week(
        tmp_path,
        week_start=WEEK_START,
        decision_id="dec_hist_a",
        history_root=history_root,
    )
    week_b = WEEK_START + timedelta(days=7)
    _issue_clean_week(
        tmp_path,
        week_start=week_b,
        decision_id="dec_hist_b",
        history_root=history_root,
    )
    store = WeekHistoryStore(history_root)
    index = store.load_index()
    assert len(index.rows) == 2
    assert [r.decision_id for r in index.rows] == ["dec_hist_a", "dec_hist_b"]


def test_overflow_drops_oldest_from_active_index(tmp_path: Path):
    history_root = tmp_path / "history"
    max_weeks = 2
    ids = []
    for i in range(3):
        ws = WEEK_START + timedelta(days=7 * i)
        dec = f"dec_ov_{i}"
        ids.append((ws, dec))
        _issue_clean_week(
            tmp_path,
            week_start=ws,
            decision_id=dec,
            history_root=history_root,
            history_max_weeks=max_weeks,
        )
    store = WeekHistoryStore(history_root, history_max_weeks=max_weeks)
    index = store.load_index()
    assert len(index.rows) == max_weeks
    assert [r.decision_id for r in index.rows] == ["dec_ov_1", "dec_ov_2"]
    # Oldest week dir may remain on disk for forensics
    oldest = history_root / "weeks" / f"{ids[0][0].isoformat()}_{ids[0][1]}"
    assert oldest.is_dir()
    assert (oldest / "record.json").is_file()


def test_restart_reloads_index_from_disk(tmp_path: Path):
    history_root = tmp_path / "history"
    _issue_clean_week(
        tmp_path,
        week_start=WEEK_START,
        decision_id="dec_restart",
        history_root=history_root,
    )
    # New process memory: fresh store instance, load only from disk.
    reloaded = WeekHistoryStore(history_root)
    index = reloaded.load_index()
    assert len(index.rows) == 1
    raw = json.loads((history_root / "index.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1"
    assert len(raw["rows"]) == 1
    assert raw["rows"][0]["decision_id"] == "dec_restart"


def test_draft_only_does_not_enter_history(tmp_path: Path):
    """Compose without write_exhibits leaves the shelf empty."""
    history_root = tmp_path / "history"
    chain = AuditChain()
    day = (WEEK_START + timedelta(days=1)).isoformat()
    avail = parse_availability_sheet(
        _avail_csv(f"sam,{day},09:00,17:00"), chain=chain
    )
    demand = parse_coverage_demand(
        {
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_draft",
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
    compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    store = WeekHistoryStore(history_root)
    assert store.active_size() == 0
    assert not (history_root / "index.json").exists()
