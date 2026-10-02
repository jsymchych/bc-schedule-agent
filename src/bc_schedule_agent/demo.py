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
from bc_schedule_agent.history import WeekHistoryStore, default_history_root
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
from bc_schedule_agent.priors import HistoryPriors, build_history_priors
from bc_schedule_agent.replay import ReplayInputs, load_replay_envelope, replay
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)  # Sunday (ESA s.1) — default draft week
FIXTURE_WEEK_START = WEEK_START  # disk fixtures under fixtures/demo/ use this week

SCENARIO_IDS = (
    "busy_week_zero_ot",
    "peak_needs_ot",
    "time_off_gate",
    "bad_s37_packet",
)


def fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "demo"


def history_fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "history"


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


def _shift_iso_dates(text: str, delta_days: int) -> str:
    """Rewrite YYYY-MM-DD tokens by a fixed day delta (fixture week rematerialize)."""
    if delta_days == 0:
        return text

    def _repl(match: re.Match[str]) -> str:
        return (date.fromisoformat(match.group(0)) + timedelta(days=delta_days)).isoformat()

    return re.sub(r"\d{4}-\d{2}-\d{2}", _repl, text)


def _shift_bytes_dates(raw: bytes, delta_days: int) -> bytes:
    if delta_days == 0 or not raw:
        return raw
    return _shift_iso_dates(raw.decode("utf-8"), delta_days).encode("utf-8")


def _week_avail(
    *employees: str,
    days: range | list[int] | None = None,
    week_start: date | None = None,
) -> bytes:
    start = week_start or WEEK_START
    day_list = list(range(1, 6) if days is None else days)
    rows: list[str] = []
    for name in employees:
        for i in day_list:
            on = start + timedelta(days=i)
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


def _bad_s37_packet(*, week_start: date | None = None) -> dict[str, Any]:
    """Missing employee signature → packet_rejected (s.37(2)(a)(ii))."""
    start = week_start or WEEK_START
    return {
        "packet_id": "agr_sam_unsigned",
        "employee": "sam",
        "in_writing": True,
        "employer_signed": True,
        "employee_signed": False,
        "signed_before_start": True,
        "period_weeks": 1,
        "repeat_count": 0,
        "start_date": start.isoformat(),
        "expiry_date": (start + timedelta(days=6)).isoformat(),
        "copy_received_before_start": True,
        "employer_signature_date": "2026-09-20",
        "employee_signature_date": None,
        "copy_received_date": "2026-09-21",
        "daily_schedule": _four_by_ten_schedule(start),
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


def ensure_history_fixtures(
    dest: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Copy small synthetic history seed into a history root when empty.

    Disk wins unless ``force=True``. Seed shows growth from 1 → few weeks —
    not a fill of ``history_max_weeks``.
    """
    src = history_fixtures_dir()
    if not src.is_dir() or not (src / "index.json").is_file():
        raise FileNotFoundError(f"history fixtures missing: {src}")
    out = Path(dest) if dest is not None else default_history_root()
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.json"
    if index_path.is_file() and not force:
        return out

    import shutil

    # Replace dest contents from seed (force or empty).
    for child in ("index.json", "weeks"):
        target = out / child
        source = src / child
        if not source.exists():
            continue
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return out


def _ot_label_from_audit(audit_path: Path | None) -> str:
    """zero-OT vs OT-with-reason from week audit events."""
    if audit_path is None or not audit_path.is_file():
        return "zero-OT"
    raw = json.loads(audit_path.read_text(encoding="utf-8"))
    for event in list(raw.get("events") or []):
        kind = str(event.get("kind") or "")
        if kind in {"ot_approved", "ot_proposed"}:
            # Issued OT-with-reason means a human approved; proposed alone is
            # still "needs OT" for shelf display after issue (approved on chain).
            if kind == "ot_approved":
                return "OT-with-reason"
            # Fall through — issued weeks with only ot_proposed shouldn't appear
            # (issue requires approve). Treat approved as the positive marker.
    # Also accept issued evidence that names an OT approve path via prose.
    for event in list(raw.get("events") or []):
        if str(event.get("kind") or "") == "ot_approved":
            return "OT-with-reason"
    return "zero-OT"


def history_shelf_rows(
    store: WeekHistoryStore | None = None,
    *,
    history_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Active history rows for DEMO shelf UI."""
    hist = store or WeekHistoryStore(history_root or default_history_root())
    index = hist.load_index()
    rows: list[dict[str, Any]] = []
    for row in index.rows:
        audit_path = hist.root / row.week_dir / "audit.json"
        if not audit_path.is_file():
            listed = row.exhibit_paths.get("audit_json")
            audit_path = Path(listed) if listed else None
        label = _ot_label_from_audit(audit_path if isinstance(audit_path, Path) else None)
        rows.append(
            {
                "week_start": row.week_start,
                "decision_id": row.decision_id,
                "ot_label": label,
                "parameter_shelf_id": row.parameter_shelf_id,
                "schedule_hash": row.schedule_hash,
            }
        )
    return rows


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
    week_start: date = WEEK_START
    history_root: Path | None = None
    freeze_parameter_shelf: bool = False
    prior_load: dict[str, Any] | None = None
    history_priors_week_starts: list[str] = field(default_factory=list)
    last_history_priors: HistoryPriors | None = None

    def reset(self) -> None:
        root = self.fixtures_root
        shelf_root = self.shelf_root
        history_root = self.history_root
        week_start = self.week_start
        self.__dict__.update(DemoSession().__dict__)
        self.fixtures_root = root
        self.shelf_root = shelf_root
        self.history_root = history_root
        self.week_start = week_start

    def history_store(self) -> WeekHistoryStore:
        return WeekHistoryStore(self.history_root or default_history_root())

    def set_week_start(self, week_start: date | str) -> dict[str, Any]:
        """Unlock draft week from the single-constant lock (Sunday expected)."""
        ws = (
            week_start
            if isinstance(week_start, date)
            else date.fromisoformat(str(week_start))
        )
        self.week_start = ws
        return self.to_state()

    def history_shelf(self) -> list[dict[str, Any]]:
        return history_shelf_rows(self.history_store())

    def load_prior_week(
        self,
        week_start: str,
        *,
        adopt_shelf: bool = False,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """Consult a prior issued week.

        Default **prior-only**: soft priors stay on; active shelf unchanged.
        Explicit **adopt_shelf**: bind that week's parameter shelf as hard authority
        (freeze until next scenario draft rebuilds).
        """
        store = self.history_store()
        index = store.load_index()
        matches = [r for r in index.rows if r.week_start == str(week_start)]
        if decision_id:
            matches = [r for r in matches if r.decision_id == decision_id]
        if not matches:
            raise ValueError(
                f"no active history row for week_start={week_start!r}"
                + (f" decision_id={decision_id!r}" if decision_id else "")
            )
        row = matches[-1]
        mode = "adopt-shelf" if adopt_shelf else "prior-only"
        self.prior_load = {
            "mode": mode,
            "week_start": row.week_start,
            "decision_id": row.decision_id,
            "parameter_shelf_id": row.parameter_shelf_id,
        }
        if adopt_shelf:
            if not row.parameter_shelf_id:
                raise ValueError("prior week has no parameter_shelf_id to adopt")
            shelf_store = ParameterShelfStore(self.shelf_root or default_shelf_root())
            try:
                shelf = shelf_store.load(row.parameter_shelf_id)
            except FileNotFoundError as exc:
                raise ValueError(
                    f"parameter shelf {row.parameter_shelf_id} not on disk"
                ) from exc
            shelf_store.save(shelf, set_active=True)
            self.parameter_shelf = shelf
            self.freeze_parameter_shelf = True
            # Best-effort: restore availability / time-off / packets from envelope.
            envelope = store.root / row.week_dir / "replay_inputs.json"
            if envelope.is_file():
                inputs = load_replay_envelope(envelope)
                self.availability_raw = inputs.availability_raw
                self.time_off_raw = inputs.time_off_raw
                self.packet_payloads = list(inputs.averaging_packets)
                if inputs.demand:
                    self.demand_override_raw = json.dumps(
                        inputs.demand, indent=2, sort_keys=True
                    ).encode("utf-8")
                self._set_input_previews()
        else:
            # prior-only — do not overwrite active shelf
            self.freeze_parameter_shelf = False
        self.last_error = None
        return self.to_state()

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
                self.week_start,
                chain=self.chain,
            )
        self.demand_payload = demand_to_payload(self.demand)
        self._set_input_previews()

        if self.freeze_parameter_shelf and self.parameter_shelf is not None:
            # Adopted shelf stays hard authority until a fresh scenario clears freeze.
            pass
        else:
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

        hist = self.history_store()
        priors = build_history_priors(hist)
        self.history_priors_week_starts = list(priors.week_starts)
        self.last_history_priors = priors if priors.week_starts else None

        self.result = compose_week(
            self.demand,
            availability=self.availability,
            time_off=self.time_off,
            chain=self.chain,
            averaging_packets=self.packets or None,
            prefer_zero_ot=True,
            parameter_shelf=self.parameter_shelf,
            observed_shelf_hashes=observed,
            history_priors=self.last_history_priors,
        )
        self.last_error = None
        self.exhibit_paths = {}
        self.replay_sentence = None
        return self.to_state()

    def run_scenario(
        self,
        scenario_id: str,
        *,
        ask: str | None = None,
        week_start: date | str | None = None,
    ) -> dict[str, Any]:
        """Draft a week from disk fixtures (generators seed only via ensure_fixture_files)."""
        if week_start is not None:
            self.set_week_start(week_start)
        root = self.fixtures_root or fixtures_dir()
        scenario = load_scenario_from_disk(scenario_id, root=root)
        preserved_week = self.week_start
        preserved_history = self.history_root
        preserved_fixtures = self.fixtures_root
        preserved_shelf_root = self.shelf_root
        self.reset()
        self.week_start = preserved_week
        self.history_root = preserved_history
        self.fixtures_root = preserved_fixtures
        self.shelf_root = preserved_shelf_root
        self.freeze_parameter_shelf = False
        self.prior_load = None
        self.scenario_id = scenario.scenario_id
        self.ask = ask if ask is not None else scenario.ask
        delta = (self.week_start - FIXTURE_WEEK_START).days
        self.decision_id = scenario.decision_id
        if delta != 0:
            # Distinct decision id per selectable week so history can grow.
            iso = self.week_start.isoformat().replace("-", "")
            self.decision_id = f"{scenario.decision_id}_{iso}"
        self.hours_of_operation = scenario.hours_of_operation
        self.sales_projections = scenario.sales_projections
        self.availability_raw = _shift_bytes_dates(scenario.availability_raw, delta)
        self.time_off_raw = (
            _shift_bytes_dates(scenario.time_off_raw, delta)
            if scenario.time_off_raw
            else scenario.time_off_raw
        )
        if scenario.averaging_packets:
            shifted: list[dict[str, Any]] = []
            for packet in scenario.averaging_packets:
                blob = json.dumps(packet)
                shifted.append(json.loads(_shift_iso_dates(blob, delta)))
            self.packet_payloads = shifted
        else:
            self.packet_payloads = []
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
            history_root=self.history_root or default_history_root(),
            parameter_shelf_id=(
                self.parameter_shelf.parameter_shelf_id
                if self.parameter_shelf is not None
                else None
            ),
            replay_inputs=ReplayInputs(
                availability_raw=self.availability_raw,
                demand=self.demand_payload or {},
                ruleset_hash=ruleset.content_hash,
                time_off_raw=self.time_off_raw,
                averaging_packets=tuple(self.packet_payloads),
                prefer_zero_ot=True,
            ),
        )
        self.exhibit_dir = target
        self.exhibit_paths = {
            "pdf": str(bundle.paths.pdf),
            "xlsx": str(bundle.paths.xlsx),
            "audit_json": str(bundle.paths.audit_json),
        }
        if bundle.paths.replay_inputs is not None:
            self.exhibit_paths["replay_inputs"] = str(bundle.paths.replay_inputs)
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
            history_priors=self.last_history_priors,
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
            (self.week_start + timedelta(days=i)).isoformat() for i in range(7)
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
            "week_start": self.week_start.isoformat(),
            "week_days": week_days,
            "input_source": self.input_source,
            "roster": roster,
            "parameter_shelf": shelf_state,
            "history_shelf": self.history_shelf(),
            "history_priors_week_starts": list(self.history_priors_week_starts),
            "prior_load": dict(self.prior_load) if self.prior_load else None,
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


def _report_paths(paths: dict[str, str], repo_root: Path) -> dict[str, str]:
    """Prefer repo-relative paths in committed smoke reports (no estate absolutes)."""
    out: dict[str, str] = {}
    for key, raw in paths.items():
        path = Path(raw)
        try:
            out[key] = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            out[key] = path.as_posix()
    return out


def run_smoke_script(out_root: Path | None = None) -> dict[str, Any]:
    """Headless ~15-minute proof: busy → peak OT → time-off → bad packet → history continuity."""
    ensure_fixture_files()
    repo_root = Path(__file__).resolve().parents[2]
    root = out_root or (repo_root / "artifacts" / "demo" / "smoke")
    root.mkdir(parents=True, exist_ok=True)
    history_root = root / "history"
    shelf_root = root / "parameters"
    ensure_history_fixtures(history_root, force=True)
    session = DemoSession(history_root=history_root, shelf_root=shelf_root)
    report: dict[str, Any] = {"steps": []}

    # 0. Seeded history shelf visible before first issue of this run
    seeded = session.history_shelf()
    assert len(seeded) >= 1
    assert all("week_start" in r and "decision_id" in r and "ot_label" in r for r in seeded)
    assert {r["ot_label"] for r in seeded} <= {"zero-OT", "OT-with-reason"}
    assert "OT-with-reason" in {r["ot_label"] for r in seeded}
    assert "zero-OT" in {r["ot_label"] for r in seeded}
    report["steps"].append(
        {
            "id": "history_seed",
            "shelf_size": len(seeded),
            "ot_labels": [r["ot_label"] for r in seeded],
        }
    )

    # 1. Busy week → zero OT download + replay
    state = session.run_scenario("busy_week_zero_ot")
    assert state["download_enabled"] is True
    assert state["pending_ot_count"] == 0
    assert state["inputs"]["hours_of_operation"]["rows"] >= 1
    assert state["inputs"]["sales_projections"]["rows"] >= 1
    assert state["parameter_shelf"] is not None
    assert state["parameter_shelf"]["parameter_shelf_id"]
    assert state["parameter_shelf"]["derived_roster"]
    assert state["parameter_shelf"]["hashes"]
    # prior-only load of a seeded week (does not overwrite shelf)
    prior_ws = seeded[0]["week_start"]
    prior_state = session.load_prior_week(prior_ws, adopt_shelf=False)
    assert prior_state["prior_load"]["mode"] == "prior-only"
    shelf_id_before = state["parameter_shelf"]["parameter_shelf_id"]
    assert prior_state["parameter_shelf"]["parameter_shelf_id"] == shelf_id_before
    state = session.write_downloads(root / "busy_week_zero_ot")
    assert state["replay_sentence"]
    assert Path(state["exhibit_paths"]["audit_json"]).is_file()
    after_busy = session.history_shelf()
    assert len(after_busy) == len(seeded) + 1
    report["steps"].append(
        {
            "id": "busy_week_zero_ot",
            "download_enabled": state["download_enabled"],
            "replay": state["replay_sentence"],
            "paths": _report_paths(state["exhibit_paths"], repo_root),
            "shelf_size": len(after_busy),
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
            "paths": _report_paths(state["exhibit_paths"], repo_root),
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

    # 5. Continuity: next week under priors → shelf grows; download gate still clean
    before = len(session.history_shelf())
    next_week = WEEK_START + timedelta(days=7)
    state = session.run_scenario("busy_week_zero_ot", week_start=next_week)
    assert state["week_start"] == next_week.isoformat()
    assert state["download_enabled"] is True
    assert WEEK_START.isoformat() in state["history_priors_week_starts"] or any(
        r["week_start"] == WEEK_START.isoformat() for r in state["history_shelf"]
    )
    assert state["history_priors_week_starts"], "expected soft priors from history"
    # prior-only default already active; optional adopt of just-issued busy shelf
    busy_row = next(
        r
        for r in state["history_shelf"]
        if r["week_start"] == WEEK_START.isoformat()
        and "busy" in r["decision_id"]
    )
    # Adopt shelf from the issued busy week (hashes must match current sheets
    # only when sheets match — here we recompose same scenario at a new week,
    # so adopt is exercised as an explicit API after issue path separately).
    adopt_probe = DemoSession(history_root=history_root, shelf_root=shelf_root)
    adopt_probe.run_scenario("busy_week_zero_ot")
    adopt_probe.write_downloads(root / "adopt_probe")
    adopt_id = adopt_probe.parameter_shelf.parameter_shelf_id  # type: ignore[union-attr]
    # Same sheets → adopt freezes without mismatch
    adopt_probe.load_prior_week(
        WEEK_START.isoformat(),
        adopt_shelf=True,
        decision_id=adopt_probe.decision_id,
    )
    assert adopt_probe.prior_load["mode"] == "adopt-shelf"
    assert adopt_probe.parameter_shelf is not None
    assert adopt_probe.parameter_shelf.parameter_shelf_id == adopt_id

    state = session.write_downloads(root / "busy_week_under_priors")
    assert state["download_enabled"] is True
    after = len(session.history_shelf())
    assert after == before + 1
    issued = next(
        e for e in session.chain.events if e.kind == "issued"
    )
    prior_starts = list(issued.evidence.get("history_prior_week_starts") or [])
    assert prior_starts, "issued evidence must name history_prior_week_starts"
    report["steps"].append(
        {
            "id": "history_continuity",
            "week_start": next_week.isoformat(),
            "shelf_before": before,
            "shelf_after": after,
            "history_prior_week_starts": prior_starts,
            "priors": state["history_priors_week_starts"],
            "busy_row": busy_row,
            "adopt_mode": adopt_probe.prior_load["mode"],
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
