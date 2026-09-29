"""Wave C: replay_inputs.json envelope round-trip + dual-plane reopen from disk."""

from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import write_exhibits
from bc_schedule_agent.history import WeekHistoryStore, week_dirname
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand
from bc_schedule_agent.replay import (
    REPLAY_INPUTS_FILENAME,
    ReplayInputs,
    load_replay_envelope,
    reopen_from_week_dir,
    replay_inputs_from_envelope,
    replay_inputs_to_envelope,
    write_replay_envelope,
)
from bc_schedule_agent.ruleset import load_ruleset
from bc_schedule_agent.shelf import build_parameter_shelf

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    return ("employee,date,start,end\n" + "\n".join(rows) + "\n").encode("utf-8")


def _compose_clean_week():
    chain = AuditChain()
    day = (WEEK_START + timedelta(days=1)).isoformat()
    avail_raw = _avail_csv(f"sam,{day},09:00,17:00")
    demand_payload = {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_envelope",
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
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand = parse_coverage_demand(demand_payload, chain=chain)
    result = compose_week(
        demand,
        availability=avail,
        time_off=[],
        chain=chain,
        prefer_zero_ot=True,
    )
    return chain, result, avail_raw, demand_payload, demand


def test_envelope_round_trip_preserves_replay_inputs(tmp_path: Path) -> None:
    ruleset = load_ruleset()
    original = ReplayInputs(
        availability_raw=_avail_csv("sam,2026-09-28,09:00,17:00"),
        demand={
            "week_start": WEEK_START.isoformat(),
            "source": "fixture",
            "demand_id": "dem_rt",
            "shifts": [],
        },
        ruleset_hash=ruleset.content_hash,
        time_off_raw=b"request_id,employee,start,end,status\n",
        averaging_packets=({"packet_id": "pkt_1"},),
        prefer_zero_ot=True,
        gate_snapshot={"ot_approvals": [], "timeoff_decisions": []},
    )
    path = write_replay_envelope(tmp_path / REPLAY_INPUTS_FILENAME, original)
    restored = load_replay_envelope(path)
    assert restored.availability_raw == original.availability_raw
    assert restored.demand == original.demand
    assert restored.ruleset_hash == original.ruleset_hash
    assert restored.time_off_raw == original.time_off_raw
    assert restored.averaging_packets == original.averaging_packets
    assert restored.prefer_zero_ot is True
    assert restored.gate_snapshot == original.gate_snapshot

    # Dict round-trip is stable under sort_keys serialization.
    again = replay_inputs_from_envelope(replay_inputs_to_envelope(restored))
    assert again.availability_raw == original.availability_raw


def test_issue_writes_envelope_and_dual_plane_reopens(tmp_path: Path) -> None:
    chain, result, avail_raw, demand_payload, demand = _compose_clean_week()
    ruleset = load_ruleset()
    history_root = tmp_path / "history"
    shelf = build_parameter_shelf(
        hours_of_operation=b"date,open,close\n2026-09-28,09:00,17:00\n",
        sales_projections=b"date,projected_sales\n2026-09-28,1000\n",
        availability_raw=avail_raw,
    )
    inputs = ReplayInputs(
        availability_raw=avail_raw,
        demand=demand_payload,
        ruleset_hash=ruleset.content_hash,
        prefer_zero_ot=True,
    )
    bundle = write_exhibits(
        result,
        chain=chain,
        decision_id="dec_env_1",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path / "exhibits" / "dec_env_1",
        timestamp="2026-09-29T19:05:00Z",
        history_root=history_root,
        parameter_shelf_id=shelf.parameter_shelf_id,
        replay_inputs=inputs,
    )

    assert bundle.paths.replay_inputs is not None
    assert bundle.paths.replay_inputs.is_file()

    week_dir = (
        history_root / "weeks" / week_dirname(WEEK_START, "dec_env_1")
    )
    week_envelope = week_dir / REPLAY_INPUTS_FILENAME
    assert week_envelope.is_file()
    assert (week_dir / "record.json").is_file()

    issued = next(e for e in chain.events if e.kind == "issued")
    assert issued.evidence["parameter_shelf_id"] == shelf.parameter_shelf_id
    assert issued.evidence["history_prior_week_starts"] == []
    chain.verify_links()

    audit = json.loads(bundle.paths.audit_json.read_text(encoding="utf-8"))
    issued_audit = next(e for e in audit["events"] if e["kind"] == "issued")
    assert issued_audit["evidence"]["parameter_shelf_id"] == shelf.parameter_shelf_id
    assert issued_audit["evidence"]["history_prior_week_starts"] == []

    reopen = reopen_from_week_dir(week_dir)
    assert reopen.matched is True
    assert reopen.schedule_hash == bundle.issue.schedule_hash
    assert reopen.gate_snapshot_hash.startswith("sha256:")

    # PDF / XLSX stay free of history prior lists and shelf ids.
    assert b"history_prior_week_starts" not in bundle.pdf_bytes
    assert shelf.parameter_shelf_id.encode() not in bundle.pdf_bytes
    with zipfile.ZipFile(bundle.paths.xlsx, "r") as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml")
        assert b"history_prior_week_starts" not in sheet
        assert shelf.parameter_shelf_id.encode() not in sheet


def test_second_issue_records_prior_week_starts(tmp_path: Path) -> None:
    history_root = tmp_path / "history"
    ruleset = load_ruleset()

    def _issue(week_start: date, decision_id: str, prior_expected: list[str]) -> None:
        chain = AuditChain()
        day = (week_start + timedelta(days=1)).isoformat()
        avail_raw = _avail_csv(f"sam,{day},09:00,17:00")
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
        avail = parse_availability_sheet(avail_raw, chain=chain)
        demand = parse_coverage_demand(demand_payload, chain=chain)
        result = compose_week(
            demand,
            availability=avail,
            time_off=[],
            chain=chain,
            prefer_zero_ot=True,
        )
        write_exhibits(
            result,
            chain=chain,
            decision_id=decision_id,
            ruleset_version=ruleset.version,
            ruleset_hash=ruleset.content_hash,
            week_start=demand.week_start,
            out_dir=tmp_path / "exhibits" / decision_id,
            timestamp="2026-09-29T19:05:00Z",
            history_root=history_root,
            replay_inputs=ReplayInputs(
                availability_raw=avail_raw,
                demand=demand_payload,
                ruleset_hash=ruleset.content_hash,
                prefer_zero_ot=True,
            ),
        )
        issued = next(e for e in chain.events if e.kind == "issued")
        assert issued.evidence["history_prior_week_starts"] == prior_expected
        chain.verify_links()

    _issue(WEEK_START, "dec_a", [])
    week2 = WEEK_START + timedelta(days=7)
    _issue(week2, "dec_b", [WEEK_START.isoformat()])

    store = WeekHistoryStore(history_root)
    assert store.active_size() == 2
    reopen = reopen_from_week_dir(
        history_root / "weeks" / week_dirname(week2, "dec_b")
    )
    assert reopen.matched is True
