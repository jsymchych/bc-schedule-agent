"""Wave F: disk fixtures are source of truth; upload API; multi-employee roster."""

from __future__ import annotations

import io
import json
from pathlib import Path

from bc_schedule_agent.demo import (
    DemoSession,
    ensure_fixture_files,
    load_scenario_from_disk,
)
from bc_schedule_agent.demo_app import DemoHandler, SESSION, UPLOAD_PATHS


def test_run_scenario_loads_disk_not_generator_only(tmp_path: Path) -> None:
    root = ensure_fixture_files(tmp_path / "demo")
    busy = root / "busy_week_zero_ot"
    sales = busy / "sales_projections.csv"
    # Mutate disk: drop Mon–Fri sales into quiet band (headcount 1).
    sales.write_text(
        "weekday,sales\n"
        "monday,500\n"
        "tuesday,500\n"
        "wednesday,500\n"
        "thursday,500\n"
        "friday,500\n",
        encoding="utf-8",
    )
    session = DemoSession(fixtures_root=root)
    state = session.run_scenario("busy_week_zero_ot")
    assert state["input_source"] == "disk"
    # Quiet band → fewer placements than the seeded steady (headcount 2) week.
    assert len(state["placed"]) == 5
    assert state["pending_ot_count"] == 0


def test_load_scenario_from_disk_reads_ask_and_sheets(tmp_path: Path) -> None:
    root = ensure_fixture_files(tmp_path / "demo")
    scenario = load_scenario_from_disk("peak_needs_ot", root=root)
    text = scenario.availability_raw.decode("utf-8")
    assert "sam," in text
    assert "jordan," in text
    assert "riley," in text
    # Monday rows for jordan/riley must be absent; Sam is Monday-only.
    assert "jordan,2026-09-28," not in text
    assert "riley,2026-09-28," not in text
    assert "sam,2026-09-29," not in text
    assert "Jordan" in scenario.ask or "Sam" in scenario.ask


def test_busy_and_peak_use_multi_employee_roster() -> None:
    session = DemoSession()
    busy = session.run_scenario("busy_week_zero_ot")
    assert busy["pending_ot_count"] == 0
    assert len(busy["roster"]) >= 2
    avail = session.availability_raw.decode("utf-8")
    assert "sam," in avail and "jordan," in avail and "riley," in avail

    peak = session.run_scenario("peak_needs_ot")
    assert peak["pending_ot_count"] >= 1
    assert "sam" in peak["roster"]
    # OT-min reallocates Tue–Fri onto Jordan/Riley (Sam is Monday-only).
    assert {"jordan", "riley"} & set(peak["roster"])
    peak_avail = session.availability_raw.decode("utf-8")
    assert "jordan," in peak_avail and "riley," in peak_avail
    assert "jordan,2026-09-28," not in peak_avail
    assert "sam,2026-09-29," not in peak_avail


def test_upload_sheet_recomposes(tmp_path: Path) -> None:
    root = ensure_fixture_files(tmp_path / "demo")
    session = DemoSession(fixtures_root=root)
    session.run_scenario("busy_week_zero_ot")
    quiet_sales = (
        "weekday,sales\n"
        "monday,400\n"
        "tuesday,400\n"
        "wednesday,400\n"
        "thursday,400\n"
        "friday,400\n"
    ).encode("utf-8")
    state = session.upload_sheet("sales_projections", quiet_sales)
    assert state["input_source"] == "upload"
    assert state["pending_ot_count"] == 0
    assert len(state["placed"]) == 5


def test_upload_endpoints_registered() -> None:
    assert set(UPLOAD_PATHS) == {
        "/api/upload/hours_of_operation",
        "/api/upload/sales_projections",
        "/api/upload/availability",
        "/api/upload/time_off",
        "/api/upload/averaging_packet",
        "/api/upload/demand",
    }


def test_demo_handler_upload_sales(tmp_path: Path) -> None:
    root = ensure_fixture_files(tmp_path / "demo")
    SESSION.reset()
    SESSION.fixtures_root = root
    SESSION.run_scenario("busy_week_zero_ot")

    class _Fake:
        headers: dict[str, str] = {}
        wfile = io.BytesIO()
        path = "/api/upload/sales_projections"
        code = 0
        body = {
            "csv": (
                "weekday,sales\n"
                "monday,400\n"
                "tuesday,400\n"
                "wednesday,400\n"
                "thursday,400\n"
                "friday,400\n"
            )
        }

        def send_response(self, code: int) -> None:
            self.code = code

        def send_header(self, k: str, v: str) -> None:
            self.headers[k] = v

        def end_headers(self) -> None:
            pass

        def _read_json(self) -> dict:
            return self.body

        def _json(self, code: int, payload: dict) -> None:
            self.code = code
            self.payload = payload
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _upload_bytes(self, body: dict) -> bytes:
            return DemoHandler._upload_bytes(self, body)  # type: ignore[arg-type]

    fake = _Fake()
    # Exercise upload path body of do_POST without binding a real socket.
    path = fake.path
    body = fake._read_json()
    raw = DemoHandler._upload_bytes(fake, body)  # type: ignore[arg-type]
    state = SESSION.upload_sheet(UPLOAD_PATHS[path], raw)
    fake._json(200, state)
    assert fake.code == 200
    assert state["input_source"] == "upload"
    assert len(state["placed"]) == 5
