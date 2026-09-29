"""Wave E: local demo session, fixture ask path, DEMO.md smoke."""

from __future__ import annotations

from pathlib import Path

import pytest

from bc_schedule_agent.demo import (
    DemoSession,
    SCENARIO_IDS,
    ensure_fixture_files,
    resolve_ask_to_scenario,
    run_smoke_script,
)
from bc_schedule_agent.export import ExportBlocked


ROOT = Path(__file__).resolve().parents[1]


def test_demo_md_on_disk() -> None:
    assert (ROOT / "DEMO.md").is_file()


def test_resolve_ask_maps_to_fixtures() -> None:
    assert resolve_ask_to_scenario("Draft a clean Mon–Fri week") == "clean_week"
    assert resolve_ask_to_scenario("ten-hour overtime Monday") == "ot_gate"
    assert resolve_ask_to_scenario("apply the s.37 averaging packet") == "bad_s37_packet"


def test_fixture_files_written() -> None:
    out = ensure_fixture_files(ROOT / "fixtures" / "demo")
    for sid in SCENARIO_IDS:
        assert (out / sid / "availability.csv").is_file()
        assert (out / sid / "demand.json").is_file()
        assert (out / sid / "ask.txt").is_file()
    assert (out / "bad_s37_packet" / "averaging_packet.json").is_file()


def test_clean_week_downloads_and_replay(tmp_path: Path) -> None:
    session = DemoSession()
    state = session.run_scenario("clean_week")
    assert state["download_enabled"] is True
    assert state["pending_ot_count"] == 0
    assert len(state["placed"]) == 5
    state = session.write_downloads(tmp_path / "clean")
    assert state["replay_sentence"]
    assert Path(state["exhibit_paths"]["pdf"]).is_file()
    assert Path(state["exhibit_paths"]["xlsx"]).is_file()
    assert Path(state["exhibit_paths"]["audit_json"]).is_file()


def test_ot_gate_blocks_then_human_approve(tmp_path: Path) -> None:
    session = DemoSession()
    state = session.run_scenario("ot_gate")
    assert state["pending_ot_count"] >= 1
    assert state["download_enabled"] is False
    with pytest.raises(ExportBlocked):
        session.write_downloads(tmp_path / "blocked")
    state = session.approve_pending_ot(human_name="Alex Rivera")
    assert state["pending_ot_count"] == 0
    assert state["download_enabled"] is True
    approved = [s for s in state["audit_drawer"] if "approved overtime" in s]
    assert approved
    state = session.write_downloads(tmp_path / "approved")
    assert state["replay_sentence"]
    assert "0 pending OT" in state["replay_sentence"]


def test_bad_s37_packet_rejected_in_drawer() -> None:
    session = DemoSession()
    state = session.run_scenario("bad_s37_packet")
    assert state["packet_status"].get("sam") == "rejected"
    hits = [
        s
        for s in state["audit_drawer"]
        if "rejected" in s.lower() and "agr_sam_unsigned" in s
    ]
    assert hits
    assert "employee_signature" in hits[0]
    assert "37(2)(a)(ii)" in hits[0]


def test_run_smoke_script_headless(tmp_path: Path) -> None:
    report = run_smoke_script(tmp_path / "smoke")
    assert report["ok"] is True
    assert [s["id"] for s in report["steps"]] == [
        "clean_week",
        "ot_gate",
        "bad_s37_packet",
    ]
    assert (tmp_path / "smoke" / "smoke_report.json").is_file()


def test_demo_app_page_serves() -> None:
    from bc_schedule_agent.demo_app import PAGE

    assert "Week grid" in PAGE
    assert "Audit drawer" in PAGE
    assert "Approve pending OT" in PAGE
    assert "browserDownload" in PAGE
    assert "/api/exhibit/pdf" in PAGE


def test_exhibit_get_serves_after_download(tmp_path: Path) -> None:
    from bc_schedule_agent.demo import DemoSession
    from bc_schedule_agent.demo_app import DemoHandler, SESSION

    # Point module SESSION at a fresh draft with exhibits on disk.
    session = DemoSession()
    session.run_scenario("clean_week")
    session.write_downloads(tmp_path / "exhibits")
    # Swap the live demo SESSION paths used by the handler.
    SESSION.exhibit_paths = dict(session.exhibit_paths)
    SESSION.result = session.result
    SESSION.chain = session.chain

    class _Fake:
        headers: dict[str, str] = {}
        wfile = __import__("io").BytesIO()
        path = "/api/exhibit/pdf"

        def send_response(self, code: int) -> None:
            self.code = code

        def send_header(self, k: str, v: str) -> None:
            self.headers[k] = v

        def end_headers(self) -> None:
            pass

    fake = _Fake()
    DemoHandler._serve_exhibit(fake, "pdf")  # type: ignore[arg-type]
    assert fake.code == 200
    assert fake.headers["Content-Type"] == "application/pdf"
    assert fake.wfile.getvalue()[:4] == b"%PDF"
