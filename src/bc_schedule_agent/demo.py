"""Local demo session: fixture demand path, week grid, audit drawer, gated download."""

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
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.models import (
    AveragingPacket,
    AvailabilityWindow,
    ComposeResult,
    CoverageDemand,
    OvertimeProposal,
    TimeOffRequest,
)
from bc_schedule_agent.replay import ReplayInputs, replay
from bc_schedule_agent.ruleset import load_ruleset

WEEK_START = date(2026, 9, 27)  # Sunday (ESA s.1)


def fixtures_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "demo"


def _avail_csv(*rows: str) -> bytes:
    header = "employee,date,start,end\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _timeoff_csv(*rows: str) -> bytes:
    header = "request_id,employee,start,end,status\n"
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _clean_week_demand() -> dict[str, Any]:
    shifts = []
    for i in (1, 2, 3, 4, 5):
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


def _ot_week_demand() -> dict[str, Any]:
    return {
        "week_start": WEEK_START.isoformat(),
        "source": "fixture",
        "demand_id": "dem_ot_week",
        "shifts": [
            {
                "shift_id": "sh_ot_mon",
                "employee": "sam",
                "date": "2026-09-28",
                "start": "08:00",
                "end": "18:30",
                "meal_break_minutes": 30,
            }
        ],
    }


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


def _wide_availability() -> bytes:
    rows = [
        f"sam,{(WEEK_START + timedelta(days=i)).isoformat()},06:00,22:00"
        for i in range(7)
    ]
    return _avail_csv(*rows)


SCENARIO_IDS = ("clean_week", "ot_gate", "bad_s37_packet")


@dataclass(frozen=True)
class DemoScenario:
    scenario_id: str
    title: str
    ask: str
    availability_raw: bytes
    time_off_raw: bytes | None
    demand: dict[str, Any]
    averaging_packets: tuple[dict[str, Any], ...] = ()
    decision_id: str = ""


def build_scenario(scenario_id: str) -> DemoScenario:
    sid = scenario_id.strip().lower()
    if sid == "clean_week":
        return DemoScenario(
            scenario_id=sid,
            title="Clean week",
            ask="Draft a clean Mon–Fri week for Sam, 09:00–17:30 with a meal break.",
            availability_raw=_wide_availability(),
            time_off_raw=_timeoff_csv(),
            demand=_clean_week_demand(),
            decision_id="dec_demo_clean_week_2026w40",
        )
    if sid == "ot_gate":
        return DemoScenario(
            scenario_id=sid,
            title="Overtime gate",
            ask="Cover Monday with a ten-hour day for Sam — expect overtime.",
            availability_raw=_wide_availability(),
            time_off_raw=_timeoff_csv(),
            demand=_ot_week_demand(),
            decision_id="dec_demo_ot_week_2026w40",
        )
    if sid in {"bad_s37_packet", "bad_s37", "bad_packet"}:
        return DemoScenario(
            scenario_id="bad_s37_packet",
            title="Bad s.37 packet",
            ask="Apply Sam's averaging packet and draft the agreed 4×10 week.",
            availability_raw=_wide_availability(),
            time_off_raw=_timeoff_csv(),
            demand={
                "week_start": WEEK_START.isoformat(),
                "source": "fixture",
                "demand_id": "dem_bad_packet",
                "shifts": [
                    {
                        "shift_id": f"sh_{(WEEK_START + timedelta(days=i)).isoformat()}",
                        "employee": "sam",
                        "date": (WEEK_START + timedelta(days=i)).isoformat(),
                        "start": "08:00",
                        "end": "18:30",
                        "meal_break_minutes": 30,
                    }
                    for i in (1, 2, 3, 4)
                ],
            },
            averaging_packets=(_bad_s37_packet(),),
            decision_id="dec_demo_bad_s37_2026w40",
        )
    raise ValueError(f"unknown demo scenario: {scenario_id!r}")


_ASK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"overtime|ten[- ]hour|10[- ]hour|\bot\b", re.I), "ot_gate"),
    (re.compile(r"averag|s\.?\s*37|packet|4\s*[x×]\s*10", re.I), "bad_s37_packet"),
    (re.compile(r"clean|mon.?fri|coverage|week", re.I), "clean_week"),
)


def resolve_ask_to_scenario(ask: str) -> str:
    """Map plain-language ask → fixture scenario. No live model in the test path."""
    text = (ask or "").strip()
    if not text:
        return "clean_week"
    for pattern, scenario_id in _ASK_PATTERNS:
        if pattern.search(text):
            return scenario_id
    return "clean_week"


def ensure_fixture_files(root: Path | None = None) -> Path:
    """Write synthetic demo sheets under fixtures/demo/ (idempotent)."""
    out = root or fixtures_dir()
    out.mkdir(parents=True, exist_ok=True)
    for sid in SCENARIO_IDS:
        scenario = build_scenario(sid)
        base = out / sid
        base.mkdir(parents=True, exist_ok=True)
        (base / "availability.csv").write_bytes(scenario.availability_raw)
        if scenario.time_off_raw is not None:
            (base / "time_off.csv").write_bytes(scenario.time_off_raw)
        (base / "demand.json").write_text(
            json.dumps(scenario.demand, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if scenario.averaging_packets:
            (base / "averaging_packet.json").write_text(
                json.dumps(scenario.averaging_packets[0], indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        (base / "ask.txt").write_text(scenario.ask + "\n", encoding="utf-8")
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
    availability: list[AvailabilityWindow] = field(default_factory=list)
    time_off: list[TimeOffRequest] = field(default_factory=list)
    packets: list[AveragingPacket] = field(default_factory=list)
    availability_raw: bytes = b""
    time_off_raw: bytes | None = None
    packet_payloads: list[dict[str, Any]] = field(default_factory=list)
    decision_id: str = ""
    exhibit_dir: Path | None = None
    exhibit_paths: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None
    replay_sentence: str | None = None

    def reset(self) -> None:
        self.__dict__.update(DemoSession().__dict__)

    def run_scenario(self, scenario_id: str, *, ask: str | None = None) -> dict[str, Any]:
        scenario = build_scenario(scenario_id)
        self.reset()
        self.scenario_id = scenario.scenario_id
        self.ask = ask if ask is not None else scenario.ask
        self.decision_id = scenario.decision_id
        self.availability_raw = scenario.availability_raw
        self.time_off_raw = scenario.time_off_raw
        self.demand_payload = scenario.demand
        self.packet_payloads = list(scenario.averaging_packets)

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
        self.demand = parse_coverage_demand(self.demand_payload, chain=self.chain)

        self.result = compose_week(
            self.demand,
            availability=self.availability,
            time_off=self.time_off,
            chain=self.chain,
            averaging_packets=self.packets or None,
        )
        self.last_error = None
        self.exhibit_paths = {}
        self.replay_sentence = None
        return self.to_state()

    def run_ask(self, ask: str) -> dict[str, Any]:
        return self.run_scenario(resolve_ask_to_scenario(ask), ask=ask)

    def approve_pending_ot(
        self, *, human_name: str, reason: str = "Demo approve: coverage required"
    ) -> dict[str, Any]:
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
        )
        self.exhibit_dir = target
        self.exhibit_paths = {
            "pdf": str(bundle.paths.pdf),
            "xlsx": str(bundle.paths.xlsx),
            "audit_json": str(bundle.paths.audit_json),
        }
        # Replay proof sentence for the drawer / DEMO script.
        inputs = ReplayInputs(
            availability_raw=self.availability_raw,
            demand=self.demand_payload or {},
            ruleset_hash=ruleset.content_hash,
            time_off_raw=self.time_off_raw,
            averaging_packets=tuple(self.packet_payloads),
            gate_snapshot=bundle.issue.gate_snapshot,
        )
        replay_result = replay(
            inputs,
            expected_schedule_hash=bundle.issue.schedule_hash,
            expected_gate_snapshot=bundle.issue.gate_snapshot,
        )
        # Dual-plane: placement hash + gate snapshot. Pending OT count comes
        # from the issued session — rebuild would re-propose PENDING lines.
        issued_pending = len(pending_ot_lines(self.result.ot_proposals))
        self.replay_sentence = (
            f"Replay matched schedule_hash={replay_result.schedule_hash} "
            f"and gate_snapshot_hash={replay_result.gate_snapshot_hash} "
            f"for decision {bundle.issue.decision_id} "
            f"({replay_result.placed_count} placed, "
            f"{replay_result.refuse_count} refuses, "
            f"{issued_pending} pending OT)."
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
        return {
            "scenario_id": self.scenario_id,
            "ask": self.ask,
            "week_start": WEEK_START.isoformat(),
            "week_days": week_days,
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
    }


def run_smoke_script(out_root: Path | None = None) -> dict[str, Any]:
    """Headless 10-minute proof path: clean → OT gate → bad packet → replay."""
    ensure_fixture_files()
    root = out_root or (
        Path(__file__).resolve().parents[2] / "artifacts" / "demo" / "smoke"
    )
    root.mkdir(parents=True, exist_ok=True)
    session = DemoSession()
    report: dict[str, Any] = {"steps": []}

    # 1. Clean week → download + replay
    state = session.run_scenario("clean_week")
    assert state["download_enabled"] is True
    assert state["pending_ot_count"] == 0
    state = session.write_downloads(root / "clean_week")
    assert state["replay_sentence"]
    report["steps"].append(
        {
            "id": "clean_week",
            "download_enabled": state["download_enabled"],
            "replay": state["replay_sentence"],
            "paths": state["exhibit_paths"],
        }
    )

    # 2. OT gate → downloads off, then human approve → on
    state = session.run_scenario("ot_gate")
    assert state["download_enabled"] is False
    assert state["pending_ot_count"] >= 1
    blocked = True
    try:
        session.write_downloads(root / "ot_blocked")
        blocked = False
    except ExportBlocked:
        pass
    assert blocked
    state = session.approve_pending_ot(human_name="Alex Rivera")
    assert state["pending_ot_count"] == 0
    assert state["download_enabled"] is True
    state = session.write_downloads(root / "ot_approved")
    assert state["replay_sentence"]
    assert "0 pending OT" in state["replay_sentence"]
    report["steps"].append(
        {
            "id": "ot_gate",
            "approved_by": "Alex Rivera",
            "replay": state["replay_sentence"],
            "paths": state["exhibit_paths"],
        }
    )

    # 3. Bad s.37 packet → packet_rejected in drawer; standard regime OT may apply
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
    (root / "smoke_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
