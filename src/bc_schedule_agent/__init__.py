"""BC schedule agent — audit chain, rule graph, ingest, composer, regimes, gates, exhibits."""

from bc_schedule_agent.audit import (
    AUDIT_KINDS,
    AuditChain,
    AuditEvent,
    AuditError,
)
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.exhibit import (
    LEGAL_POSTURE,
    STATUTE_URL,
    ScheduleModel,
    write_exhibits,
)
from bc_schedule_agent.export import (
    ExportBlocked,
    build_pdf_exhibit,
    build_xlsx_exhibit,
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
    parse_time_off_sheet,
)
from bc_schedule_agent.packet import accept_or_reject_packet, check_averaging_packet
from bc_schedule_agent.replay import ReplayError, ReplayInputs, ReplayMismatch, replay
from bc_schedule_agent.ruleset import Ruleset, load_ruleset, ruleset_hash

__all__ = [
    "AUDIT_KINDS",
    "AuditChain",
    "AuditEvent",
    "AuditError",
    "ExportBlocked",
    "GateError",
    "LEGAL_POSTURE",
    "ReplayError",
    "ReplayInputs",
    "ReplayMismatch",
    "Ruleset",
    "STATUTE_URL",
    "ScheduleModel",
    "accept_or_reject_packet",
    "agent_approve_ot",
    "approve_ot",
    "build_pdf_exhibit",
    "build_xlsx_exhibit",
    "check_averaging_packet",
    "compose_week",
    "decide_time_off",
    "issue_schedule",
    "load_ruleset",
    "parse_availability_sheet",
    "parse_averaging_packet",
    "parse_coverage_demand",
    "parse_time_off_sheet",
    "pending_ot_lines",
    "refuse_ot",
    "replay",
    "ruleset_hash",
    "write_exhibits",
]

__version__ = "0.1.0"
