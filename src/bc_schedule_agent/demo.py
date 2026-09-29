"""Local demo session: four-input product story, week grid, audit drawer, gated download."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import event_to_prose, write_exhibits
from bc_schedule_agent.export import ExportBlocked, pending_ot_lines, schedule_hash
from bc_schedule_agent.gates import approve_ot, refuse_ot
from bc_schedule_agent.history import default_history_root
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_time_off_sheet,
)
from bc_schedule_agent.shelf import (
    ParameterShelf,
    ParameterShelfStore,
    build_parameter_shelf,
    default_shelf_root,
    observed_input_hashes,
)
from bc_schedule_agent.models import (
    AveragingPacket,
    AvailabilityWindow,
    ComposeResult,
    CoverageDemand,
    OvertimeProposal,
    TimeOffRequest,
)
from bc_schedule_agent.planner import plan_coverage
from bc_schedule_agent.replay import ReplayInputs, replay
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)  # Sunday (ESA s.1)

SCENARIO_IDS = (
    "busy_week_zero_ot",
    "peak_needs_ot",
    "time_off_gate",
    "bad_s37_packet",
)


def fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "demo"


SHEET_FILENAMES: dict[str, str] = {
    "hours_of_operation": "hours_of_operation.csv",
    "sales_projections": "sales_projections.csv",
    "availability": "availability.csv",
    "time_off": "time_off.csv",
}


def resolve_scenario_id(scenario_id: str) -> str:
    """Normalize aliases from the prior demo sprint to product scenario ids."""
    sid = scenario_id.strip().lower()
    if sid in {"clean_week", "busy", "busy_week"}:
        return "busy_week_zero_ot"
    if sid in {"ot_gate", "peak", "overtime"}:
        return "peak_needs_ot"
    if sid in {"time_off", "timeoff", "time-off"}:
        return "time_off_gate"
    if sid in {"bad_s37", "bad_packet"}:
        return "bad_s37_packet"
    return sid


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _timeoff_csv(*rows: str) -> bytes:
    header = "request_id,employee,start,end,status\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _ops_csv(*rows: str) -> bytes:
    header = "weekday,open,close\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _sales_csv(*rows: str) -> bytes:
    header = "weekday,sales\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _week_avail(*employees: str, days: range | list[int] | None = None) -> bytes:
    day_list = list(range(1, 6) if days is None else days)
    rows: list[str] = []
    for name in employees:
        for i in day_list:
            on = WEEK_START + timedelta(days=i)
            rows.append(f"{name},{on.isoformat()},06:00,22:00")
    return _avail_csv(*rows)


def _four_by_ten_schedule(start: date) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for i in range(7):
        on = start + timedelta(days=i)
        if i in (1, 2, 3, 4):
            days.append(
                {
                    "date": on.isoformat(),
                    "scheduled_hours": 10,
                    "start": "08:00",
                    "end": "18:00",
                }
            )
        else:
            days.append({"date": on.isoformat(), "scheduled_hours": 0})
    return days


def _bad_s37_packet() -> dict[str, Any]:
    """Missing employee signature → packet_rejected (s.37(2)(a)(ii))."""
    return {
        "packet_id": "agr_sam_unsigned",
        "employee": "sam",
        "in_writing": True,
        "employer_signed": True,
        "employee_signed": False,
        "signed_before_start": True,
        "period_weeks": 1,
        "repeat_count": 0,
        "start_date": WEEK_START.isoformat(),
        "expiry_date": (WEEK_START + timedelta(days=6)).isoformat(),
        "copy_received_before_start": True,
        "employer_signature_date": "2026-09-20",
        "employee_signature_date": None,
        "copy_received_date": "2026-09-21",
        "daily_schedule": _four_by_ten_schedule(WEEK_START),
    }


def demand_to_payload(demand: CoverageDemand) -> dict[str, Any]:
    """Serialize planned demand for replay / exhibit storage (not a client input)."""
    return {
        "week_start": demand.week_start.isoformat(),
        "source": demand.source,
        "demand_id": demand.demand_id,
        "shifts": [
            {
                "shift_id": s.shift_id,
                "employee": s.employee,
                "date": s.date.isoformat(),
                "start": s.start.strftime("%H:%M"),
                "end": s.end.strftime("%H:%M"),
                "meal_break_minutes": s.meal_break_minutes,
            }
            for s in demand.shifts
        ],
    }


@dataclass(frozen=True)
class DemoScenario:
    scenario_id: str
    title: str
    ask: str
    hours_of_operation: bytes
    sales_projections: bytes
    availability_raw: bytes
    time_off_raw: bytes
    averaging_packets: tuple[dict[str, Any], ...] = ()
    decision_id: str = ""


def build_scenario(scenario_id: str) -> DemoScenario:
    """Synthetic generator used only to seed disk via ``ensure_fixture_files``."""
    sid = resolve_scenario_id(scenario_id)

    if sid == "busy_week_zero_ot":
        return DemoScenario(
            scenario_id=sid,
            title="Busy week — zero OT",
            ask=(
                "Take Mon–Fri hours and steady sales with Sam, Jordan, and Riley "
                "available — draft a compliant zero-OT week."
            ),
            hours_of_operation=_ops_csv(
                "monday,09:00,17:30",
                "tuesday,09:00,17:30",
                "wednesday,09:00,17:30",
                "thursday,09:00,17:30",
                "friday,09:00,17:30",
            ),
            sales_projections=_sales_csv(
                "monday,1500",
                "tuesday,1500",
                "wednesday,1500",
                "thursday,1500",
                "friday,1500",
            ),
            # Multi-employee roster so OT-min Pass A can reallocate across people.
            availability_raw=_week_avail("sam", "jordan", "riley"),
            time_off_raw=_timeoff_csv(),
            decision_id="dec_demo_busy_week_2026w40",
        )
    if sid == "peak_needs_ot":
        # Multi-employee roster: Sam covers peak Monday only (OT); OT-min Pass A
        # reallocates Tue–Fri onto Jordan/Riley at straight time.
        peak_avail = _avail_csv(
            f"sam,{(WEEK_START + timedelta(days=1)).isoformat()},06:00,22:00",
            *[
                f"jordan,{(WEEK_START + timedelta(days=i)).isoformat()},06:00,22:00"
                for i in range(2, 6)
            ],
            *[
                f"riley,{(WEEK_START + timedelta(days=i)).isoformat()},06:00,22:00"
                for i in range(2, 6)
            ],
        )
        return DemoScenario(
            scenario_id=sid,
            title="Peak needs OT",
            ask=(
                "Peak Monday: store opens 08:00–18:30; only Sam is on Monday while "
                "Jordan and Riley cover the rest of the week — expect overtime before issue."
            ),
            hours_of_operation=_ops_csv(
                "monday,08:00,18:30",
                "tuesday,09:00,17:30",
                "wednesday,09:00,17:30",
                "thursday,09:00,17:30",
                "friday,09:00,17:30",
            ),
            sales_projections=_sales_csv(
                "monday,700",
                "tuesday,700",
                "wednesday,700",
                "thursday,700",
                "friday,700",
            ),
            availability_raw=peak_avail,
            time_off_raw=_timeoff_csv(),
            decision_id="dec_demo_peak_ot_2026w40",
        )
    if sid == "time_off_gate":
        return DemoScenario(
            scenario_id=sid,
            title="Time-off gate",
            ask=(
                "Sam has a PENDING time-off request for Monday — draft and show the gate."
            ),
            hours_of_operation=_ops_csv(
                "monday,09:00,17:30",
                "tuesday,09:00,17:30",
                "wednesday,09:00,17:30",
                "thursday,09:00,17:30",
                "friday,09:00,17:30",
            ),
            sales_projections=_sales_csv(
                "monday,500",
                "tuesday,500",
                "wednesday,500",
                "thursday,500",
                "friday,500",
            ),
            availability_raw=_week_avail("sam"),
            time_off_raw=_timeoff_csv(
                "to_sam_mon,sam,2026-09-28,2026-09-28,PENDING"
            ),
            decision_id="dec_demo_timeoff_2026w40",
        )
    if sid == "bad_s37_packet":
        return DemoScenario(
            scenario_id=sid,
            title="Bad s.37 packet",
            ask=(
                "Apply Sam's averaging packet (missing employee signature) and draft "
                "the week — packet must reject."
            ),
            hours_of_operation=_ops_csv(
                "monday,08:00,18:30",
                "tuesday,08:00,18:30",
                "wednesday,08:00,18:30",
                "thursday,08:00,18:30",
            ),
            sales_projections=_sales_csv(
                "monday,500",
                "tuesday,500",
                "wednesday,500",
                "thursday,500",
            ),
            availability_raw=_week_avail("sam", days=[1, 2, 3, 4]),
            time_off_raw=_timeoff_csv(),
            averaging_packets=(_bad_s37_packet(),),
            decision_id="dec_demo_bad_s37_2026w40",
        )
    raise ValueError(f"unknown demo scenario: {scenario_id!r}")


_ASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"time[- ]?off|pending leave|pto", re.I), "time_off_gate"),
    (re.compile(r"averag|s\.?\s*37|packet|4\s*[x×]\s*10", re.I), "bad_s37_packet"),
    (
        re.compile(r"peak|overtime|ten[- ]hour|10[- ]hour|\bot\b|18:30", re.I),
        "peak_needs_ot",
    ),
    (
        re.compile(
            r"busy|zero[- ]?ot|steady|sam and jordan|sam,\s*jordan|riley|mon.?fri",
            re.I,
        ),
        "busy_week_zero_ot",
    ),
)


def resolve_ask_to_scenario(ask: str) -> str:
    """Map plain-language ask → scenario. No live model in the test path."""
    text = (ask or "").strip()
    if not text:
        return "busy_week_zero_ot"
    for pattern, scenario_id in _ASK_PATTERNS:
        if pattern.search(text):
            return scenario_id
    return "busy_week_zero_ot"


def _input_preview(label: str, raw: bytes, *, max_lines: int = 6) -> dict[str, Any]:
    text = raw.decode("utf-8").strip()
    lines = text.splitlines()
    return {
        "label": label,
        "rows": max(0, len(lines) - 1),
        "preview": "\n".join(lines[: max_lines + 1]),
    }


def load_scenario_from_disk(
    scenario_id: str,
    *,
    root: Path | None = None,
) -> DemoScenario:
    """Load four-input sheets (+ optional packet) from ``fixtures/demo/<id>/``.

    Generators seed disk only through ``ensure_fixture_files``; callers that draft
    a week must go through this path (or an upload) — not in-memory generator bytes.
    """
    ensure_fixture_files(root)
    sid = resolve_scenario_id(scenario_id)
    meta = build_scenario(sid)
    base = (root or fixtures_dir()) / sid
    if not base.is_dir():
        raise FileNotFoundError(f"demo fixture directory missing: {base}")

    hours = (base / SHEET_FILENAMES["hours_of_operation"]).read_bytes()
    sales = (base / SHEET_FILENAMES["sales_projections"]).read_bytes()
    availability = (base / SHEET_FILENAMES["availability"]).read_bytes()
    time_off = (base / SHEET_FILENAMES["time_off"]).read_bytes()
    ask_path = base / "ask.txt"
    ask = ask_path.read_text(encoding="utf-8").strip() if ask_path.is_file() else meta.ask
    packets: tuple[dict[str, Any], ...] = ()
    packet_path = base / "averaging_packet.json"
    if packet_path.is_file():
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"averaging_packet.json must be an object: {packet_path}")
        packets = (payload,)
    return DemoScenario(
        scenario_id=sid,
        title=meta.title,
        ask=ask,
        hours_of_operation=hours,
        sales_projections=sales,
        availability_raw=availability,
        time_off_raw=time_off,
        averaging_packets=packets,
        decision_id=meta.decision_id,
    )


def _write_bytes_idempotent(path: Path, data: bytes, *, force: bool) -> None:
    """Seed missing files. Existing disk wins unless ``force=True``.

    When ``force=True`` and bytes already match, skip rewrite (idempotent).
    """
    if path.is_file():
        if not force:
            return
        if path.read_bytes() == data:
            return
    path.write_bytes(data)


def _write_text_idempotent(path: Path, text: str, *, force: bool) -> None:
    _write_bytes_idempotent(path, text.encode("utf-8"), force=force)


def ensure_fixture_files(
    root: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Seed synthetic four-input sheets under fixtures/demo/ when missing.

    Existing files are never overwritten unless ``force=True`` (disk is the
    source of truth). When ``force=True`` and on-disk bytes already match,
    rewrite is skipped (idempotent). After seed, disk feeds ``run_scenario`` /
    uploads — not in-memory rebuilds.
    """
    out = root or fixtures_dir()
    out.mkdir(parents=True, exist_ok=True)
    for sid in SCENARIO_IDS:
        scenario = build_scenario(sid)
        base = out / sid
        base.mkdir(parents=True, exist_ok=True)
        _write_bytes_idempotent(
            base / SHEET_FILENAMES["hours_of_operation"],
            scenario.hours_of_operation,
            force=force,
        )
        _write_bytes_idempotent(
            base / SHEET_FILENAMES["sales_projections"],
            scenario.sales_projections,
            force=force,
        )
        _write_bytes_idempotent(
            base / SHEET_FILENAMES["availability"],
            scenario.availability_raw,
            force=force,
        )
        _write_bytes_idempotent(
            base / SHEET_FILENAMES["time_off"],
            scenario.time_off_raw,
            force=force,
        )
        if scenario.averaging_packets:
            packet_text = (
                json.dumps(scenario.averaging_packets[0], indent=2, sort_keys=True)
                + "\n"
            )
            _write_text_idempotent(
                base / "averaging_packet.json",
                packet_text,
                force=force,
            )
        _write_text_idempotent(base / "ask.txt", scenario.ask + "\n", force=force)
    return out


@dataclass
class DemoSession:
    """In-memory one-screen state for the local demo."""

    scenario_id: str | None = None
    ask: str = ""
    chain: AuditChain = field(default_factory=AuditChain)
    result: ComposeResult | None = None
    demand: CoverageDemand | None = None
    demand_payload: dict[str, Any] | None = None
    demand_override_raw: bytes | None = None
    availability: list[AvailabilityWindow] = field(default_factory=list)
    time_off: list[TimeOffRequest] = field(default_factory=list)
    packets: list[AveragingPacket] = field(default_factory=list)
    hours_of_operation: bytes = b""
    sales_projections: bytes = b""
    availability_raw: bytes = b""
    time_off_raw: bytes | None = None
    packet_payloads: list[dict[str, Any]] = field(default_factory=list)
    decision_id: str = ""
    exhibit_dir: Path | None = None
    exhibit_paths: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None
    replay_sentence: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    fixtures_root: Path | None = None
    input_source: str = "none"  # disk | upload | none
    parameter_shelf: ParameterShelf | None = None
    shelf_root: Path | None = None

    def reset(self) -> None:
        root = self.fixtures_root
        shelf_root = self.shelf_root
        self.__dict__.update(DemoSession().__dict__)
        self.fixtures_root = root
        self.shelf_root = shelf_root

    def _set_input_previews(self) -> None:
        self.inputs = {
            "hours_of_operation": _input_preview(
                "Hours of operations", self.hours_of_operation or b""
            ),
            "sales_projections": _input_preview(
                "Sales projections", self.sales_projections or b""
            ),
            "availability": _input_preview(
                "Availability", self.availability_raw or b""
            ),
            "time_off": _input_preview(
                "Time-off requests", self.time_off_raw or b""
            ),
        }

    def _compose_from_session_inputs(self) -> dict[str, Any]:
        if not self.hours_of_operation or not self.sales_projections:
            raise RuntimeError("hours_of_operation and sales_projections required")
        if not self.availability_raw:
            raise RuntimeError("availability required")

        self.chain = AuditChain()
        self.availability = parse_availability_sheet(
            self.availability_raw, chain=self.chain
        )
        self.time_off = (
            parse_time_off_sheet(self.time_off_raw, chain=self.chain)
            if self.time_off_raw is not None
            else []
        )
        self.packets = [
            parse_averaging_packet(payload, chain=self.chain)
            for payload in self.packet_payloads
        ]
        if self.demand_override_raw is not None:
            from bc_schedule_agent.ingest import parse_coverage_demand

            self.demand = parse_coverage_demand(
                self.demand_override_raw, chain=self.chain
            )
        else:
            self.demand = plan_coverage(
                self.hours_of_operation,
                self.sales_projections,
                WEEK_START,
                chain=self.chain,
            )
        self.demand_payload = demand_to_payload(self.demand)
        self._set_input_previews()

        self.parameter_shelf = build_parameter_shelf(
            hours_of_operation=self.hours_of_operation,
            sales_projections=self.sales_projections,
            availability_raw=self.availability_raw,
            time_off_raw=self.time_off_raw,
            averaging_packets=list(self.packet_payloads) or None,
            demand_override_raw=self.demand_override_raw,
            availability=self.availability,
        )
        ParameterShelfStore(self.shelf_root or default_shelf_root()).save(
            self.parameter_shelf
        )
        observed = observed_input_hashes(
            hours_of_operation=self.hours_of_operation,
            sales_projections=self.sales_projections,
            availability_raw=self.availability_raw,
            time_off_raw=self.time_off_raw,
            averaging_packets=list(self.packet_payloads) or None,
            demand_override_raw=self.demand_override_raw,
        )

        self.result = compose_week(
            self.demand,
            availability=self.availability,
            time_off=self.time_off,
            chain=self.chain,
            averaging_packets=self.packets or None,
            prefer_zero_ot=True,
            parameter_shelf=self.parameter_shelf,
            observed_shelf_hashes=observed,
        )
        self.last_error = None
        self.exhibit_paths = {}
        self.replay_sentence = None
        return self.to_state()

    def run_scenario(self, scenario_id: str, *, ask: str | None = None) -> dict[str, Any]:
        """Draft a week from disk fixtures (generators seed only via ensure_fixture_files)."""
        root = self.fixtures_root or fixtures_dir()
        scenario = load_scenario_from_disk(scenario_id, root=root)
        self.reset()
        self.scenario_id = scenario.scenario_id
        self.ask = ask if ask is not None else scenario.ask
        self.decision_id = scenario.decision_id
        self.hours_of_operation = scenario.hours_of_operation
        self.sales_projections = scenario.sales_projections
        self.availability_raw = scenario.availability_raw
        self.time_off_raw = scenario.time_off_raw
        self.packet_payloads = list(scenario.averaging_packets)
        self.demand_override_raw = None
        self.input_source = "disk"
        return self._compose_from_session_inputs()

    def upload_sheet(self, sheet: str, raw: bytes) -> dict[str, Any]:
        """Replace one input sheet (or optional packet/demand) and recompose when ready."""
        key = sheet.strip().lower().replace("-", "_")
        aliases = {
            "hours": "hours_of_operation",
            "hours_of_ops": "hours_of_operation",
            "ops": "hours_of_operation",
            "sales": "sales_projections",
            "avail": "availability",
            "timeoff": "time_off",
            "packet": "averaging_packet",
            "averaging": "averaging_packet",
        }
        key = aliases.get(key, key)

        if key == "hours_of_operation":
            self.hours_of_operation = raw
        elif key == "sales_projections":
            self.sales_projections = raw
        elif key == "availability":
            self.availability_raw = raw
        elif key == "time_off":
            self.time_off_raw = raw
        elif key == "averaging_packet":
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, list):
                if not all(isinstance(p, dict) for p in payload):
                    raise ValueError("averaging_packet list entries must be objects")
                self.packet_payloads = list(payload)
            elif isinstance(payload, dict):
                self.packet_payloads = [payload]
            else:
                raise ValueError("averaging_packet must be an object or list")
        elif key == "demand":
            self.demand_override_raw = raw
        else:
            raise ValueError(
                "unknown sheet; expected hours_of_operation|sales_projections|"
                "availability|time_off|averaging_packet|demand"
            )

        self.input_source = "upload"
        self._set_input_previews()
        if (
            self.hours_of_operation
            and self.sales_projections
            and self.availability_raw
        ):
            if self.time_off_raw is None:
                self.time_off_raw = _timeoff_csv()
            if not self.scenario_id:
                self.scenario_id = "upload"
            if not self.decision_id:
                self.decision_id = "dec_demo_upload"
            return self._compose_from_session_inputs()
        return self.to_state()

    def run_ask(self, ask: str) -> dict[str, Any]:
        return self.run_scenario(resolve_ask_to_scenario(ask), ask=ask)

    def approve_pending_ot(self, *, human_name: str, reason: str) -> dict[str, Any]:
        if self.result is None:
            raise RuntimeError("no draft loaded")
        pending = pending_ot_lines(self.result.ot_proposals)
        if not pending:
            raise RuntimeError("no PENDING_APPROVAL overtime lines")
        for proposal in pending:
            approve_ot(
                proposal,
                human_name=human_name,
                reason=reason,
                chain=self.chain,
                timestamp="2026-09-29T19:00:00Z",
            )
        self.last_error = None
        return self.to_state()

    def refuse_pending_ot(self, *, human_name: str) -> dict[str, Any]:
        if self.result is None:
            raise RuntimeError("no draft loaded")
        pending = list(pending_ot_lines(self.result.ot_proposals))
        if not pending:
            raise RuntimeError("no PENDING_APPROVAL overtime lines")
        for proposal in pending:
            refuse_ot(
                proposal,
                human_name=human_name,
                chain=self.chain,
                result=self.result,
                timestamp="2026-09-29T19:00:00Z",
            )
        self.last_error = None
        return self.to_state()

    def download_enabled(self) -> bool:
        if self.result is None:
            return False
        if pending_ot_lines(self.result.ot_proposals):
            return False
        if any(e.kind == "rule_refuse" for e in self.chain.events):
            return False
        return True

    def write_downloads(self, out_dir: Path | None = None) -> dict[str, Any]:
        if self.result is None or self.demand is None:
            raise RuntimeError("no draft loaded")
        if not self.download_enabled():
            raise ExportBlocked("downloads disabled: pending OT or rule_refuse on chain")
        ruleset = load_ruleset()
        target = out_dir or (
            Path(__file__).resolve().parents[2]
            / "artifacts"
            / "demo"
            / (self.scenario_id or "session")
        )
        bundle = write_exhibits(
            self.result,
            chain=self.chain,
            decision_id=self.decision_id or "dec_demo",
            ruleset_version=ruleset.version,
            ruleset_hash=ruleset.content_hash,
            week_start=self.demand.week_start,
            out_dir=target,
            timestamp="2026-09-29T19:05:00Z",
            history_root=default_history_root(),
            parameter_shelf_id=(
                self.parameter_shelf.parameter_shelf_id
                if self.parameter_shelf is not None
                else None
            ),
        )
        self.exhibit_dir = target
        self.exhibit_paths = {
            "pdf": str(bundle.paths.pdf),
            "xlsx": str(bundle.paths.xlsx),
            "audit_json": str(bundle.paths.audit_json),
        }
        inputs = ReplayInputs(
            availability_raw=self.availability_raw,
            demand=self.demand_payload or {},
            ruleset_hash=ruleset.content_hash,
            time_off_raw=self.time_off_raw,
            averaging_packets=tuple(self.packet_payloads),
            gate_snapshot=bundle.issue.gate_snapshot,
            prefer_zero_ot=True,
        )
        replay_result = replay(
            inputs,
            expected_schedule_hash=bundle.issue.schedule_hash,
            expected_gate_snapshot=bundle.issue.gate_snapshot,
        )
        issued_pending = len(pending_ot_lines(self.result.ot_proposals))
        who_why = _ot_approver_sentence(self.result.ot_proposals)
        self.replay_sentence = (
            f"Replay matched schedule_hash={replay_result.schedule_hash} "
            f"and gate_snapshot_hash={replay_result.gate_snapshot_hash} "
            f"for decision {bundle.issue.decision_id} "
            f"({replay_result.placed_count} placed, "
            f"{replay_result.refuse_count} refuses, "
            f"{issued_pending} pending OT)."
            + (f" {who_why}" if who_why else "")
        )
        self.last_error = None
        return self.to_state()

    def to_state(self) -> dict[str, Any]:
        week_days = [
            (WEEK_START + timedelta(days=i)).isoformat() for i in range(7)
        ]
        placed_rows: list[dict[str, Any]] = []
        if self.result is not None:
            for p in self.result.placed:
                placed_rows.append(
                    {
                        "shift_id": p.shift_id,
                        "employee": p.employee,
                        "date": p.date.isoformat(),
                        "start": p.start.isoformat(timespec="minutes"),
                        "end": p.end.isoformat(timespec="minutes"),
                        "worked_hours": p.worked_hours,
                    }
                )
        ot_rows: list[dict[str, Any]] = []
        if self.result is not None:
            for o in self.result.ot_proposals:
                ot_rows.append(_ot_row(o))
        audit = [event_to_prose(e) for e in self.chain.events]
        pending = (
            pending_ot_lines(self.result.ot_proposals) if self.result is not None else []
        )
        shash = (
            schedule_hash(self.result.placed)
            if self.result is not None and self.result.placed
            else None
        )
        roster = sorted({p["employee"] for p in placed_rows}) if placed_rows else []
        shelf_state: dict[str, Any] | None = None
        if self.parameter_shelf is not None:
            shelf_state = {
                "parameter_shelf_id": self.parameter_shelf.parameter_shelf_id,
                "staffing_version": self.parameter_shelf.staffing_version,
                "derived_roster": list(self.parameter_shelf.derived_roster),
                "hashes": self.parameter_shelf.content_hashes(),
            }
        return {
            "scenario_id": self.scenario_id,
            "ask": self.ask,
            "week_start": WEEK_START.isoformat(),
            "week_days": week_days,
            "input_source": self.input_source,
            "roster": roster,
            "parameter_shelf": shelf_state,
            "inputs": dict(self.inputs),
            "placed": placed_rows,
            "ot_proposals": ot_rows,
            "pending_ot_count": len(pending),
            "download_enabled": self.download_enabled(),
            "decision_id": self.decision_id,
            "schedule_hash": shash,
            "audit_drawer": audit,
            "exhibit_paths": dict(self.exhibit_paths),
            "replay_sentence": self.replay_sentence,
            "last_error": self.last_error,
            "packet_status": dict(self.result.packet_status) if self.result else {},
            "refused_count": (
                sum(1 for e in self.chain.events if e.kind == "rule_refuse")
            ),
        }


def _ot_approver_sentence(proposals: list[OvertimeProposal]) -> str:
    approved = [o for o in proposals if o.status == "APPROVED" and o.decided_by]
    if not approved:
        return ""
    parts = []
    for o in approved:
        why = o.reason or "(no reason)"
        parts.append(f"{o.decided_by} approved OT ({why})")
    return "Who approved OT and why is on the chain: " + "; ".join(parts) + "."


def _ot_row(o: OvertimeProposal) -> dict[str, Any]:
    return {
        "proposal_id": o.proposal_id,
        "employee": o.employee,
        "date": o.date.isoformat(),
        "hours": o.hours,
        "multiplier": o.multiplier,
        "rule_id": o.rule_id,
        "section": o.section,
        "status": o.status,
        "decided_by": o.decided_by,
        "decided_at": o.decided_at,
        "reason": o.reason,
    }


def run_smoke_script(out_root: Path | None = None) -> dict[str, Any]:
    """Headless ~15-minute proof: busy zero-OT → peak OT → time-off → bad packet."""
    ensure_fixture_files()
    root = out_root or (
        Path(__file__).resolve().parents[2] / "artifacts" / "demo" / "smoke"
    )
    root.mkdir(parents=True, exist_ok=True)
    session = DemoSession()
    report: dict[str, Any] = {"steps": []}

    # 1. Busy week → zero OT download + replay
    state = session.run_scenario("busy_week_zero_ot")
    assert state["download_enabled"] is True
    assert state["pending_ot_count"] == 0
    assert state["inputs"]["hours_of_operation"]["rows"] >= 1
    assert state["inputs"]["sales_projections"]["rows"] >= 1
    state = session.write_downloads(root / "busy_week_zero_ot")
    assert state["replay_sentence"]
    assert Path(state["exhibit_paths"]["audit_json"]).is_file()
    report["steps"].append(
        {
            "id": "busy_week_zero_ot",
            "download_enabled": state["download_enabled"],
            "replay": state["replay_sentence"],
            "paths": state["exhibit_paths"],
        }
    )

    # 2. Peak OT → blocked, then named human + reason → downloads
    state = session.run_scenario("peak_needs_ot")
    assert state["download_enabled"] is False
    assert state["pending_ot_count"] >= 1
    blocked = True
    try:
        session.write_downloads(root / "ot_blocked")
        blocked = False
    except ExportBlocked:
        pass
    assert blocked
    reason = "Peak Monday: only Sam is rostered for the long open"
    state = session.approve_pending_ot(human_name="Alex Rivera", reason=reason)
    assert state["pending_ot_count"] == 0
    assert state["download_enabled"] is True
    state = session.write_downloads(root / "peak_needs_ot")
    assert state["replay_sentence"]
    assert "0 pending OT" in state["replay_sentence"]
    assert "Alex Rivera" in state["replay_sentence"]
    assert reason in state["replay_sentence"]
    report["steps"].append(
        {
            "id": "peak_needs_ot",
            "approved_by": "Alex Rivera",
            "reason": reason,
            "replay": state["replay_sentence"],
            "paths": state["exhibit_paths"],
        }
    )

    # 2b. Refuse path (separate draft)
    state = session.run_scenario("peak_needs_ot")
    state = session.refuse_pending_ot(human_name="Alex Rivera")
    assert state["pending_ot_count"] == 0
    refused = [s for s in state["audit_drawer"] if "refused overtime" in s.lower()]
    assert refused, "expected ot_refused prose after refuse"
    report["steps"].append(
        {
            "id": "peak_needs_ot_refuse",
            "refused_by": "Alex Rivera",
            "audit_hit": refused[0],
        }
    )

    # 3. Time-off gate → pending awaits human
    state = session.run_scenario("time_off_gate")
    hits = [
        s
        for s in state["audit_drawer"]
        if "pending time-off" in s.lower() or "awaits human" in s.lower()
    ]
    assert hits, "expected pending time-off gate prose in audit drawer"
    assert state["download_enabled"] is False
    report["steps"].append(
        {
            "id": "time_off_gate",
            "refused_count": state["refused_count"],
            "audit_hit": hits[0],
        }
    )

    # 4. Bad s.37 packet → packet_rejected; standard regime
    state = session.run_scenario("bad_s37_packet")
    rejected = [
        s
        for s in state["audit_drawer"]
        if "rejected" in s.lower() and "agr_sam_unsigned" in s
    ]
    assert rejected, "expected packet_rejected prose in audit drawer"
    assert any(
        "employee_signature" in s and "37(2)(a)(ii)" in s for s in rejected
    ), f"drawer must name missing signature term, got {rejected!r}"
    report["steps"].append(
        {
            "id": "bad_s37_packet",
            "packet_status": state["packet_status"],
            "audit_hit": rejected[0],
            "pending_ot_count": state["pending_ot_count"],
        }
    )

    report["ok"] = True
    report["scenarios"] = list(SCENARIO_IDS)
    report["closing"] = (
        "Who approved OT and why is on the chain."
    )
    (root / "smoke_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
