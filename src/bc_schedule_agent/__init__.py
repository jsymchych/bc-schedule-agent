"""BC schedule agent — audit chain, rule graph, ingest, composer, regimes."""

from bc_schedule_agent.audit import (
    AUDIT_KINDS,
    AuditChain,
    AuditEvent,
    AuditError,
)
from bc_schedule_agent.composer import compose_week
from bc_schedule_agent.ingest import (
    parse_availability_sheet,
    parse_averaging_packet,
    parse_coverage_demand,
    parse_time_off_sheet,
)
from bc_schedule_agent.packet import accept_or_reject_packet, check_averaging_packet
from bc_schedule_agent.ruleset import Ruleset, load_ruleset, ruleset_hash

__all__ = [
    "AUDIT_KINDS",
    "AuditChain",
    "AuditEvent",
    "AuditError",
    "Ruleset",
    "accept_or_reject_packet",
    "check_averaging_packet",
    "compose_week",
    "load_ruleset",
    "parse_availability_sheet",
    "parse_averaging_packet",
    "parse_coverage_demand",
    "parse_time_off_sheet",
    "ruleset_hash",
]

__version__ = "0.1.0"
