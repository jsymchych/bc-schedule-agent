"""BC schedule agent — audit chain, rule graph, ingest, planner, composer, regimes, gates, exhibits, demo."""

from bc_schedule_agent.audit import (
    AUDIT_KINDS,
    AuditChain,
    AuditEvent,
    AuditError,
)
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.demo import DemoSession, run_smoke_script
from bc_schedule_agent.exhibit import (
    LEGAL_POSTURE,
    STATUTE_URL,
    ScheduleModel,
    write_exhibits,
)
from bc_schedule_agent.export import (
    ExportBlocked,
    build_gate_snapshot,
    build_pdf_exhibit,
    build_xlsx_exhibit,
    empty_gate_snapshot,
    gate_snapshot_hash,
    issue_schedule,
    pending_ot_lines,
)
from bc_schedule_agent.gates import (
    GateError,
    agent_approve_ot,
    approve_ot,
    decide_time_off,
    refuse_ot,
)
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_hours_of_operation,
    parse_sales_projections,
    parse_time_off_sheet,
)
from bc_schedule_agent.packet import accept_or_reject_packet, check_averaging_packet
from bc_schedule_agent.planner import plan_coverage
from bc_schedule_agent.priors import HistoryPriors, build_history_priors
from bc_schedule_agent.replay import (
    ReplayError,
    ReplayInputs,
    ReplayMismatch,
    issued_gate_snapshot,
    load_replay_envelope,
    reopen_from_week_dir,
    replay,
    write_replay_envelope,
)
from bc_schedule_agent.ruleset import Ruleset, load_ruleset, ruleset_hash
from bc_schedule_agent.shelf import (
    ParameterShelf,
    ParameterShelfStore,
    build_parameter_shelf,
    enforce_shelf_authority,
)
from bc_schedule_agent.staffing import StaffingCurve, load_staffing

__all__ = [
    "AUDIT_KINDS",
    "AuditChain",
    "AuditEvent",
    "AuditError",
    "DemoSession",
    "ExportBlocked",
    "GateError",
    "HistoryPriors",
    "LEGAL_POSTURE",
    "ParameterShelf",
    "ParameterShelfStore",
    "ReplayError",
    "ReplayInputs",
    "ReplayMismatch",
    "Ruleset",
    "STATUTE_URL",
    "ScheduleModel",
    "StaffingCurve",
    "accept_or_reject_packet",
    "agent_approve_ot",
    "approve_ot",
    "build_gate_snapshot",
    "build_history_priors",
    "build_parameter_shelf",
    "build_pdf_exhibit",
    "build_xlsx_exhibit",
    "check_averaging_packet",
    "compose_week",
    "decide_time_off",
    "empty_gate_snapshot",
    "enforce_shelf_authority",
    "gate_snapshot_hash",
    "issue_schedule",
    "issued_gate_snapshot",
    "load_replay_envelope",
    "load_ruleset",
    "load_staffing",
    "parse_availability_sheet",
    "parse_averaging_packet",
    "parse_coverage_demand",
    "parse_hours_of_operation",
    "parse_sales_projections",
    "parse_time_off_sheet",
    "pending_ot_lines",
    "plan_coverage",
    "refuse_ot",
    "reopen_from_week_dir",
    "replay",
    "ruleset_hash",
    "run_smoke_script",
    "write_exhibits",
    "write_replay_envelope",
]

__version__ = "0.1.0"
