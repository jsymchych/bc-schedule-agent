"""Wave D: exhibits (PDF/XLSX/audit.json) and replay gate."""

from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import (
    LEGAL_POSTURE,
    STATUTE_URL,
    write_exhibits,
)
from bc_schedule_agent.export import issue_schedule, schedule_hash
from bc_schedule_agent.ingest import (
    content_hash,
    parse_availability_sheet,
    parse_coverage_demand,
)
from bc_schedule_agent.replay import ReplayInputs, ReplayMismatch, replay
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)  # Sunday
DECISION_ID = "dec_clean_week_2026w40"


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _clean_week_demand() -> dict:
    """Mon–Fri 8h days with meal break: zero OT, zero refuse when available."""
    shifts = []
    for i in (1, 2, 3, 4, 5):  # Mon–Fri
        on = WEEK_START + timedelta(days=i)
        shifts.append(
            {
                "shift_id": f"sh_{on.isoformat()}",
                "employee": "sam",
                "date": on.isoformat(),
                "start": "09:00",
                "end": "17:30",
                "meal_break_minutes": 30,
            }
        )
    return {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_clean_week",
        "shifts": shifts,
    }


def _clean_week_availability() -> bytes:
    rows = [
        f"sam,{(WEEK_START + timedelta(days=i)).isoformat()},08:00,19:00"
        for i in (1, 2, 3, 4, 5)
    ]
    return _avail_csv(*rows)


def _compose_clean_week() -> tuple[AuditChain, object, bytes, dict, object]:
    chain = AuditChain()
    avail_raw = _clean_week_availability()
    demand_payload = _clean_week_demand()
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand = parse_coverage_demand(demand_payload, chain=chain)
    result = compose_week(demand, availability=avail, time_off=[], chain=chain)
    return chain, result, avail_raw, demand_payload, demand


def test_clean_week_issued_zero_refuse_zero_pending_ot(tmp_path: Path) -> None:
    chain, result, avail_raw, demand_payload, demand = _compose_clean_week()
    ruleset = load_ruleset()

    assert len(result.placed) == 5
    assert result.ot_proposals == []
    assert not any(e.kind == "rule_refuse" for e in chain.events)
    assert not any(e.kind == "ot_proposed" for e in chain.events)

    bundle = write_exhibits(
        result,
        chain=chain,
        decision_id=DECISION_ID,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path / "exhibits",
        timestamp="2026-09-29T18:00:00Z",
    )

    issued_events = [e for e in chain.events if e.kind == "issued"]
    assert len(issued_events) == 1
    issued = issued_events[0]
    assert issued.subject["decision_id"] == DECISION_ID
    assert issued.evidence["schedule_hash"] == bundle.model.schedule_hash
    assert issued.evidence["ruleset_hash"] == ruleset.content_hash
    assert issued.evidence["statute_url"] == STATUTE_URL
    assert LEGAL_POSTURE in issued.evidence["legal_posture"]

    assert bundle.paths.pdf.is_file()
    assert bundle.paths.xlsx.is_file()
    assert bundle.paths.audit_json.is_file()
    assert bundle.pdf_bytes.startswith(b"%PDF-1.4")
    assert DECISION_ID.encode("utf-8") in bundle.pdf_bytes
    assert STATUTE_URL.encode("latin-1") in bundle.pdf_bytes
    assert b"not legal advice" in bundle.pdf_bytes

    with zipfile.ZipFile(bundle.paths.xlsx, "r") as zf:
        names = set(zf.namelist())
        assert "xl/worksheets/sheet1.xml" in names
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert DECISION_ID in sheet
        assert STATUTE_URL in sheet
        assert "worked_hours" in sheet

    audit = json.loads(bundle.paths.audit_json.read_text(encoding="utf-8"))
    assert audit["decision_id"] == DECISION_ID
    assert audit["schedule_hash"] == bundle.issue.schedule_hash
    assert audit["ruleset_hash"] == ruleset.content_hash
    assert audit["statute_url"] == STATUTE_URL
    assert audit["hours_and_multipliers_only"] is True
    assert any(e["kind"] == "issued" for e in audit["events"])
    assert all("prose" in e for e in audit["events"])

    # Same decision id on all three artifacts.
    assert bundle.model.decision_id == DECISION_ID
    assert audit["decision_id"] == DECISION_ID
    assert DECISION_ID in sheet


def test_exhibit_same_decision_id_on_pdf_xlsx_audit(tmp_path: Path) -> None:
    chain, result, _, _, demand = _compose_clean_week()
    ruleset = load_ruleset()
    bundle = write_exhibits(
        result,
        chain=chain,
        decision_id=DECISION_ID,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path,
    )
    audit = json.loads(bundle.paths.audit_json.read_text(encoding="utf-8"))
    assert (
        bundle.model.decision_id
        == audit["decision_id"]
        == DECISION_ID
        == bundle.issue.decision_id
    )
    assert DECISION_ID.encode() in bundle.pdf_bytes
    with zipfile.ZipFile(bundle.paths.xlsx) as zf:
        assert DECISION_ID in zf.read("xl/worksheets/sheet1.xml").decode()


def test_replay_same_inputs_match_issued_hash() -> None:
    chain, result, avail_raw, demand_payload, _demand = _compose_clean_week()
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id=DECISION_ID,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )
    assert issued.schedule_hash == schedule_hash(result.placed)
    assert issued.gate_snapshot == {"ot_approvals": [], "timeoff_decisions": []}
    event = next(e for e in chain.events if e.kind == "issued")
    assert event.evidence["gate_snapshot"] == issued.gate_snapshot
    assert event.evidence["gate_snapshot_hash"].startswith("sha256:")

    replay_result = replay(
        ReplayInputs(
            availability_raw=avail_raw,
            demand=demand_payload,
            ruleset_hash=ruleset.content_hash,
            availability_hash=content_hash(avail_raw),
            demand_hash=content_hash(
                json.dumps(
                    demand_payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ),
            gate_snapshot=issued.gate_snapshot,
        ),
        expected_schedule_hash=issued.schedule_hash,
        expected_gate_snapshot=issued.gate_snapshot,
    )
    assert replay_result.matched is True
    assert replay_result.schedule_hash == issued.schedule_hash
    assert replay_result.gate_snapshot_hash == event.evidence["gate_snapshot_hash"]
    assert replay_result.refuse_count == 0
    assert replay_result.pending_ot_count == 0
    assert replay_result.placed_count == 5


def test_replay_mismatch_fails() -> None:
    chain, result, avail_raw, demand_payload, _ = _compose_clean_week()
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id=DECISION_ID,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )

    tampered = json.loads(json.dumps(demand_payload))
    tampered["shifts"][0]["end"] = "18:00"  # changes worked hours → hash

    with pytest.raises(ReplayMismatch, match="placement plane mismatch") as exc:
        replay(
            ReplayInputs(
                availability_raw=avail_raw,
                demand=tampered,
                ruleset_hash=ruleset.content_hash,
                gate_snapshot=issued.gate_snapshot,
            ),
            expected_schedule_hash=issued.schedule_hash,
            expected_gate_snapshot=issued.gate_snapshot,
        )
    assert exc.value.plane == "placement"


def test_issued_event_carries_schedule_hash() -> None:
    chain, result, _, _, _ = _compose_clean_week()
    ruleset = load_ruleset()
    issued = issue_schedule(
        result,
        chain=chain,
        decision_id="dec_hash_check",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
    )
    event = next(e for e in chain.events if e.kind == "issued")
    assert event.evidence["schedule_hash"] == issued.schedule_hash
    assert event.evidence["schedule_hash"].startswith("sha256:")
