"""Wave E product: four-input demo session, DEMO.md smoke."""

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
from bc_schedule_agent.gates import GateError


ROOT = Path(__file__).resolve().parents[1]


def test_demo_md_on_disk() -> None:
    assert (ROOT / "DEMO.md").is_file()
    text = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    assert "fixture demand" not in text.lower()
    assert "four inputs" in text.lower()
    assert "who approved OT and why is on the chain" in text
    # Wave B: choose-type narration — not a bundled primary click
    assert "Download PDF / XLSX / audit.json" not in text
    assert "Click **Download all**" not in text
    assert "No “Download all” primary" in text
    assert "Click **PDF** first" in text
    assert "Click **XLSX** next" in text
    assert "**audit.json**" in text
    assert "do not re-issue" in text
    assert "Walk **PDF**, then **XLSX**, then **audit.json**" in text
    # Wave E continuity
    assert "History shelf" in text
    assert "Parameters" in text
    assert "prior-only" in text
    assert "Adopt shelf from week" in text
    assert "history_prior_week_starts" in text
    assert "compose under priors" in text.lower() or "Compose under priors" in text


def test_history_fixtures_seed(tmp_path: Path) -> None:
    from bc_schedule_agent.demo import ensure_history_fixtures, history_shelf_rows
    from bc_schedule_agent.history import WeekHistoryStore

    dest = tmp_path / "history"
    ensure_history_fixtures(dest, force=True)
    assert (dest / "index.json").is_file()
    store = WeekHistoryStore(dest)
    rows = history_shelf_rows(store)
    assert len(rows) >= 1
    assert {r["ot_label"] for r in rows} == {"zero-OT", "OT-with-reason"}


def test_resolve_ask_maps_to_scenarios() -> None:
    assert (
        resolve_ask_to_scenario("Take Mon–Fri hours and steady sales with Sam and Jordan")
        == "busy_week_zero_ot"
    )
    assert resolve_ask_to_scenario("Peak Monday overtime") == "peak_needs_ot"
    assert resolve_ask_to_scenario("Sam has pending time-off") == "time_off_gate"
    assert resolve_ask_to_scenario("apply the s.37 averaging packet") == "bad_s37_packet"


def test_fixture_files_written(tmp_path: Path) -> None:
    out = ensure_fixture_files(tmp_path / "demo")
    for sid in SCENARIO_IDS:
        base = out / sid
        assert (base / "hours_of_operation.csv").is_file()
        assert (base / "sales_projections.csv").is_file()
        assert (base / "availability.csv").is_file()
        assert (base / "time_off.csv").is_file()
        assert (base / "ask.txt").is_file()
    assert (out / "bad_s37_packet" / "averaging_packet.json").is_file()


def test_busy_week_zero_ot_downloads_and_replay(tmp_path: Path) -> None:
    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    state = session.run_scenario("busy_week_zero_ot")
    assert state["download_enabled"] is True
    assert state["pending_ot_count"] == 0
    assert state["input_source"] == "disk"
    assert state["inputs"]["hours_of_operation"]["rows"] >= 1
    assert state["inputs"]["sales_projections"]["rows"] >= 1
    assert state["inputs"]["availability"]["rows"] >= 1
    assert len(state["placed"]) >= 2
    assert len(state["roster"]) >= 2
    state = session.write_downloads(tmp_path / "busy")
    assert state["replay_sentence"]
    assert Path(state["exhibit_paths"]["pdf"]).is_file()
    assert Path(state["exhibit_paths"]["xlsx"]).is_file()
    assert Path(state["exhibit_paths"]["audit_json"]).is_file()


def test_peak_needs_ot_blocks_then_human_approve_with_reason(tmp_path: Path) -> None:
    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    state = session.run_scenario("peak_needs_ot")
    assert state["pending_ot_count"] >= 1
    assert state["download_enabled"] is False
    assert state["input_source"] == "disk"
    # Multi-employee roster: Jordan/Riley cover Tue–Fri; Monday still needs Sam OT.
    assert {"sam", "jordan", "riley"} & set(state["roster"])
    assert "sam" in state["roster"]
    with pytest.raises(ExportBlocked):
        session.write_downloads(tmp_path / "blocked")
    with pytest.raises(GateError, match="non-empty reason"):
        session.approve_pending_ot(human_name="Alex Rivera", reason="")
    reason = "Peak Monday: only Sam is rostered for the long open"
    state = session.approve_pending_ot(human_name="Alex Rivera", reason=reason)
    assert state["pending_ot_count"] == 0
    assert state["download_enabled"] is True
    approved = [s for s in state["audit_drawer"] if "approved overtime" in s]
    assert approved
    assert reason in approved[0]
    state = session.write_downloads(tmp_path / "approved")
    assert state["replay_sentence"]
    assert "0 pending OT" in state["replay_sentence"]
    assert "Alex Rivera" in state["replay_sentence"]
    assert reason in state["replay_sentence"]


def test_peak_needs_ot_refuse(tmp_path: Path) -> None:
    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    session.run_scenario("peak_needs_ot")
    state = session.refuse_pending_ot(human_name="Alex Rivera")
    assert state["pending_ot_count"] == 0
    hits = [s for s in state["audit_drawer"] if "refused overtime" in s]
    assert hits


def test_time_off_gate_in_drawer(tmp_path: Path) -> None:
    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    state = session.run_scenario("time_off_gate")
    hits = [
        s
        for s in state["audit_drawer"]
        if "pending time-off" in s.lower() or "awaits human" in s.lower()
    ]
    assert hits
    assert state["download_enabled"] is False


def test_bad_s37_packet_rejected_in_drawer(tmp_path: Path) -> None:
    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
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
        "history_seed",
        "busy_week_zero_ot",
        "peak_needs_ot",
        "peak_needs_ot_refuse",
        "time_off_gate",
        "bad_s37_packet",
        "history_continuity",
    ]
    assert report["closing"] == "Who approved OT and why is on the chain."
    assert (tmp_path / "smoke" / "smoke_report.json").is_file()
    assert (tmp_path / "smoke" / "busy_week_zero_ot" / "audit.json").is_file()
    continuity = next(s for s in report["steps"] if s["id"] == "history_continuity")
    assert continuity["shelf_after"] == continuity["shelf_before"] + 1
    assert continuity["history_prior_week_starts"]


def test_demo_app_page_serves() -> None:
    from bc_schedule_agent.demo_app import PAGE

    assert "Week grid" in PAGE
    assert "Audit drawer" in PAGE
    assert "Four inputs" in PAGE
    assert "Approve pending OT" in PAGE
    assert "Refuse OT" in PAGE
    assert "Named human" in PAGE
    assert "audit.json" in PAGE
    assert "browserDownload" in PAGE
    assert "downloadType" in PAGE
    assert "exhibitsIssued" in PAGE
    assert 'id="btn-download-pdf"' in PAGE
    assert 'id="btn-download-xlsx"' in PAGE
    assert 'id="btn-download-audit"' in PAGE
    assert 'id="btn-download"' not in PAGE
    assert "Download PDF / XLSX / audit.json" not in PAGE
    assert "Promise.all" not in PAGE
    assert 'browserDownload("/api/exhibit/" + kind)' in PAGE
    assert 'downloadType("pdf", "PDF")' in PAGE
    assert 'downloadType("xlsx", "XLSX")' in PAGE
    assert 'downloadType("audit.json", "audit.json")' in PAGE
    assert "saved to Downloads." in PAGE
    assert 'api("/api/download"' in PAGE
    assert "if (!exhibitsIssued(state))" in PAGE
    assert 'id="week-start"' in PAGE
    assert "History shelf" in PAGE
    assert 'id="history-shelf"' in PAGE
    assert 'id="parameters-panel"' in PAGE
    assert "Adopt shelf from week" in PAGE
    assert "prior-only" in PAGE
    assert "/api/load-prior" in PAGE
    from bc_schedule_agent.demo_app import UPLOAD_PATHS

    assert "/api/upload/hours_of_operation" in UPLOAD_PATHS
    assert "/api/upload/sales_projections" in UPLOAD_PATHS
    assert "/api/upload/availability" in UPLOAD_PATHS
    assert "/api/upload/time_off" in UPLOAD_PATHS
    assert "/api/upload/averaging_packet" in UPLOAD_PATHS
    assert "/api/upload/demand" in UPLOAD_PATHS


def test_selectable_week_start_shifts_grid(tmp_path: Path) -> None:
    from datetime import timedelta

    from bc_schedule_agent.demo import WEEK_START, DemoSession

    session = DemoSession(history_root=tmp_path / "h", shelf_root=tmp_path / "p")
    next_week = WEEK_START + timedelta(days=7)
    state = session.run_scenario("busy_week_zero_ot", week_start=next_week)
    assert state["week_start"] == next_week.isoformat()
    assert state["week_days"][0] == next_week.isoformat()
    assert state["download_enabled"] is True
    assert len(state["placed"]) >= 1
    assert all(p["date"] >= next_week.isoformat() for p in state["placed"])


def test_exhibit_get_serves_after_download(tmp_path: Path) -> None:
    from bc_schedule_agent.demo import DemoSession
    from bc_schedule_agent.demo_app import DemoHandler, SESSION

    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    session.run_scenario("busy_week_zero_ot")
    session.write_downloads(tmp_path / "exhibits")
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

    fake_audit = _Fake()
    fake_audit.wfile = __import__("io").BytesIO()
    DemoHandler._serve_exhibit(fake_audit, "audit.json")  # type: ignore[arg-type]
    assert fake_audit.code == 200
    assert "application/json" in fake_audit.headers["Content-Type"]


def test_each_type_fetches_only_its_path_second_click_no_reissue(tmp_path: Path) -> None:
    """After issue-once, each type control serves only its path; later types do not re-issue."""
    from bc_schedule_agent.demo import DemoSession
    from bc_schedule_agent.demo_app import DemoHandler, PAGE, SESSION

    session = DemoSession(
        history_root=tmp_path / "history", shelf_root=tmp_path / "shelf"
    )
    session.run_scenario("busy_week_zero_ot")
    session.write_downloads(tmp_path / "per_type")
    issued_before = sum(1 for e in session.chain.events if e.kind == "issued")
    assert issued_before == 1
    SESSION.exhibit_paths = dict(session.exhibit_paths)
    SESSION.result = session.result
    SESSION.chain = session.chain

    class _Fake:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.wfile = __import__("io").BytesIO()
            self.code = 0

        def send_response(self, code: int) -> None:
            self.code = code

        def send_header(self, k: str, v: str) -> None:
            self.headers[k] = v

        def end_headers(self) -> None:
            pass

    expectations = [
        ("pdf", "application/pdf", b"%PDF"),
        (
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK",
        ),
        ("audit.json", "application/json", b"{"),
    ]
    for kind, ctype, magic in expectations:
        fake = _Fake()
        DemoHandler._serve_exhibit(fake, kind)  # type: ignore[arg-type]
        assert fake.code == 200, kind
        assert ctype in fake.headers["Content-Type"], kind
        body = fake.wfile.getvalue()
        assert body.startswith(magic), kind
        # One path per click: disposition names that file only
        cd = fake.headers.get("Content-Disposition", "")
        assert "filename=" in cd
        if kind == "pdf":
            assert ".pdf" in cd and ".xlsx" not in cd and "audit" not in cd
        elif kind == "xlsx":
            assert ".xlsx" in cd and ".pdf" not in cd and "audit" not in cd
        else:
            assert "audit" in cd and ".pdf" not in cd and ".xlsx" not in cd

    # Second/third type clicks only GET exhibits — issued count stays one
    issued_after = sum(1 for e in SESSION.chain.events if e.kind == "issued")
    assert issued_after == 1
    assert "if (!exhibitsIssued(state))" in PAGE
    assert 'browserDownload("/api/exhibit/" + kind)' in PAGE
    assert "Promise.all" not in PAGE
