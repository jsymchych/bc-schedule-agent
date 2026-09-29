"""Wave G: PDF pagination, XLSX OT sheet, idempotent fixture writes."""

from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.demo import SCENARIO_IDS, ensure_fixture_files
from bc_schedule_agent.exhibit import write_exhibits
from bc_schedule_agent.export import pending_ot_lines
from bc_schedule_agent.gates import approve_ot
from bc_schedule_agent.ingest import parse_availability_sheet, parse_coverage_demand
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)


def _avail_csv(*rows: str) -> bytes:
    return ("employee,date,start,end\n" + "\n".join(rows) + "\n").encode("utf-8")


def _peak_ot_bundle(tmp_path: Path):
    chain = AuditChain()
    avail_raw = _avail_csv("sam,2026-09-28,06:00,20:00")
    avail = parse_availability_sheet(avail_raw, chain=chain)
    demand_payload = {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_ship_harden_ot",
        "shifts": [
            {
                "shift_id": "sh_peak",
                "employee": "sam",
                "date": "2026-09-28",
                "start": "08:00",
                "end": "18:30",
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
    assert pending_ot_lines(result.ot_proposals)
    reason = "Peak close: only Sam available for the full window"
    for proposal in list(pending_ot_lines(result.ot_proposals)):
        approve_ot(
            proposal,
            human_name="Alex Rivera",
            reason=reason,
            chain=chain,
            timestamp="2026-09-29T18:00:00Z",
        )
    # Lengthen the chain so the PDF must paginate.
    for i in range(90):
        chain.append(
            kind="rule_pass",
            actor="agent",
            subject={"employee": "sam", "date": "2026-09-28"},
            evidence={"detail": f"padding event {i:03d} for pagination proof"},
            timestamp=f"2026-09-29T18:{i // 60:02d}:{i % 60:02d}Z",
        )
    ruleset = load_ruleset()
    bundle = write_exhibits(
        result,
        chain=chain,
        decision_id="dec_ship_harden",
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.content_hash,
        week_start=demand.week_start,
        out_dir=tmp_path / "exhibits",
        timestamp="2026-09-29T19:00:00Z",
    )
    return bundle, chain, reason


def test_pdf_audit_appendix_paginates(tmp_path: Path) -> None:
    bundle, chain, _reason = _peak_ot_bundle(tmp_path)
    pdf = bundle.pdf_bytes
    assert pdf.startswith(b"%PDF-1.4")
    count_match = re.search(rb"/Count\s+(\d+)", pdf)
    assert count_match is not None
    assert int(count_match.group(1)) >= 2
    last_prose = next(e for e in reversed(chain.events) if e.kind == "rule_pass")
    # Last padding prose must appear — no mid-appendix truncate.
    assert b"padding event 089" in pdf
    assert last_prose.evidence["detail"].encode("latin-1") in pdf


def test_xlsx_ot_sheet_has_approver_and_reason(tmp_path: Path) -> None:
    bundle, _chain, reason = _peak_ot_bundle(tmp_path)
    with zipfile.ZipFile(bundle.paths.xlsx, "r") as zf:
        names = set(zf.namelist())
        assert "xl/worksheets/sheet2.xml" in names
        workbook = zf.read("xl/workbook.xml").decode("utf-8")
        assert 'name="ot"' in workbook
        sheet2 = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert "human_name" in sheet2
        assert "timestamp" in sheet2
        assert "reason" in sheet2
        assert "Alex Rivera" in sheet2
        assert "2026-09-29T18:00:00Z" in sheet2
        assert reason in sheet2
        # Non-empty OT data rows (header + at least one line).
        assert sheet2.count("<row r=") >= 2


def test_ensure_fixture_files_tmp_path_and_idempotent(tmp_path: Path) -> None:
    root = ensure_fixture_files(tmp_path / "demo")
    for sid in SCENARIO_IDS:
        base = root / sid
        assert (base / "hours_of_operation.csv").is_file()
        assert (base / "sales_projections.csv").is_file()
        assert (base / "availability.csv").is_file()
        assert (base / "time_off.csv").is_file()
        assert (base / "ask.txt").is_file()
    assert (root / "bad_s37_packet" / "averaging_packet.json").is_file()

    sample = root / "busy_week_zero_ot" / "ask.txt"
    before = sample.read_bytes()
    mtime_before = sample.stat().st_mtime_ns
    # Second seed must not rewrite existing disk (source of truth).
    ensure_fixture_files(tmp_path / "demo")
    assert sample.read_bytes() == before
    assert sample.stat().st_mtime_ns == mtime_before
    # force=True with matching bytes is also a no-op on content/mtime.
    ensure_fixture_files(tmp_path / "demo", force=True)
    assert sample.read_bytes() == before
    assert sample.stat().st_mtime_ns == mtime_before
