"""ESA s.37 averaging packet acceptance / rejection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import AveragingPacket, sunday_of

# Failure term → enforced ruleset id (must appear as rule: actors).
TERM_TO_RULE_ID: dict[str, str] = {
    "employee": "bc-esa-s37-packet-signed-both",
    "in_writing": "bc-esa-s37-packet-in-writing",
    "employer_signature": "bc-esa-s37-packet-signed-both",
    "employee_signature": "bc-esa-s37-packet-signed-both",
    "signed_before_start": "bc-esa-s37-packet-signed-both",
    "period_weeks": "bc-esa-s37-packet-length",
    "daily_schedule_present": "bc-esa-s37-packet-daily-schedule",
    "repeat_count": "bc-esa-s37-packet-repeat-count",
    "start_and_expiry_dates": "bc-esa-s37-packet-dates",
    "scheduled_hours_cap": "bc-esa-s37-packet-scheduled-hours",
    "copy_received_before_start": "bc-esa-s37-packet-copy-received",
    "draft_week_within_packet_dates": "bc-esa-s37-packet-week-in-range",
}

PACKET_ENFORCED_RULE_IDS: tuple[str, ...] = (
    "bc-esa-s37-packet-in-writing",
    "bc-esa-s37-packet-signed-both",
    "bc-esa-s37-packet-length",
    "bc-esa-s37-packet-daily-schedule",
    "bc-esa-s37-packet-repeat-count",
    "bc-esa-s37-packet-dates",
    "bc-esa-s37-packet-scheduled-hours",
    "bc-esa-s37-packet-copy-received",
    "bc-esa-s37-packet-week-in-range",
)


@dataclass(frozen=True)
class PacketTermFailure:
    term: str
    section: str
    detail: str
    rule_id: str


@dataclass(frozen=True)
class PacketCheckResult:
    accepted: bool
    failures: tuple[PacketTermFailure, ...]
    audit_sentence: str

    @property
    def missing_terms(self) -> list[str]:
        return [f.term for f in self.failures]


def _fail(term: str, section: str, detail: str) -> PacketTermFailure:
    rule_id = TERM_TO_RULE_ID.get(term)
    if rule_id is None:
        raise ValueError(f"unmapped packet term: {term}")
    return PacketTermFailure(term=term, section=section, detail=detail, rule_id=rule_id)


def check_averaging_packet(
    packet: AveragingPacket,
    *,
    draft_week_start: date,
) -> PacketCheckResult:
    """Validate all required s.37 terms. Invalid → employee stays on s.40."""
    failures: list[PacketTermFailure] = []
    week_start = sunday_of(draft_week_start)
    week_end = week_start + timedelta(days=6)

    if not packet.employee:
        failures.append(
            _fail("employee", "37(2)(a)", "packet must name one employee")
        )

    if not packet.in_writing:
        failures.append(
            _fail("in_writing", "37(2)", "averaging agreement must be in writing")
        )

    if not packet.employer_signed:
        failures.append(
            _fail("employer_signature", "37(2)(a)(i)", "employer signature missing")
        )

    if not packet.employee_signed:
        failures.append(
            _fail("employee_signature", "37(2)(a)(ii)", "employee signature missing")
        )

    if not packet.signed_before_start:
        failures.append(
            _fail(
                "signed_before_start",
                "37(2)(a)",
                "must be signed by both parties before the start date",
            )
        )

    if packet.period_weeks is None:
        failures.append(_fail("period_weeks", "37(2)", "period length not stated"))
    elif packet.period_weeks not in (1, 2, 3, 4):
        failures.append(
            _fail(
                "period_weeks",
                "37(2)",
                f"period_weeks must be 1–4, got {packet.period_weeks}",
            )
        )

    if packet.repeat_count is None:
        failures.append(
            _fail(
                "repeat_count",
                "37(2)",
                "repeat count not stated (zero is allowed when stated)",
            )
        )

    if packet.start_date is None or packet.expiry_date is None:
        failures.append(
            _fail(
                "start_and_expiry_dates",
                "37(2)",
                "start date and expiry date are required",
            )
        )
    elif packet.expiry_date < packet.start_date:
        failures.append(
            _fail(
                "start_and_expiry_dates",
                "37(2)",
                "expiry date precedes start date",
            )
        )

    if not packet.daily_schedule:
        failures.append(
            _fail(
                "daily_schedule_present",
                "37(2)",
                "work schedule for each day in the period is required",
            )
        )
    elif packet.period_weeks in (1, 2, 3, 4) and packet.start_date is not None:
        expected_days = packet.period_weeks * 7
        if len(packet.daily_schedule) < expected_days:
            covered = {d.date for d in packet.daily_schedule}
            period_dates = [
                packet.start_date + timedelta(days=i) for i in range(expected_days)
            ]
            missing = [d for d in period_dates if d not in covered]
            if missing:
                failures.append(
                    _fail(
                        "daily_schedule_present",
                        "37(2)",
                        f"missing schedule days: {', '.join(d.isoformat() for d in missing[:3])}"
                        + ("…" if len(missing) > 3 else ""),
                    )
                )

    if not packet.copy_received_before_start:
        failures.append(
            _fail(
                "copy_received_before_start",
                "37(2)(c)",
                "employee must receive a copy before the period starts",
            )
        )

    if packet.start_date is not None and packet.expiry_date is not None:
        if not (packet.start_date <= week_start and week_end <= packet.expiry_date):
            failures.append(
                _fail(
                    "draft_week_within_packet_dates",
                    "37",
                    "draft week is outside packet start/expiry",
                )
            )

    # s.37(3): scheduled hours ≤ 40 for one week, or average ≤ 40 across 2–4 weeks.
    if (
        packet.period_weeks in (1, 2, 3, 4)
        and packet.daily_schedule
        and not any(f.term == "daily_schedule_present" for f in failures)
    ):
        total = packet.total_scheduled_hours()
        average = total / packet.period_weeks
        if average > 40.0 + 1e-9:
            failures.append(
                _fail(
                    "scheduled_hours_cap",
                    "37(3)",
                    f"average weekly hours {average:.1f} exceeds 40 "
                    f"(total {total:.1f} over {packet.period_weeks} weeks)",
                )
            )

    accepted = not failures
    if accepted:
        sentence = (
            f"Packet {packet.packet_id} accepted for {packet.employee}: "
            f"valid {packet.period_weeks}-week averaging agreement under s.37(3), "
            f"signed before start, copy received before period."
        )
    else:
        parts = [f"{f.term} (s.{f.section})" for f in failures]
        sentence = (
            f"Packet {packet.packet_id} rejected for {packet.employee or 'unknown'}: "
            + "; ".join(parts)
        )
    return PacketCheckResult(
        accepted=accepted,
        failures=tuple(failures),
        audit_sentence=sentence,
    )


def accept_or_reject_packet(
    packet: AveragingPacket,
    *,
    draft_week_start: date,
    chain: AuditChain,
) -> PacketCheckResult:
    """Write packet_accepted or packet_rejected (with missing terms) to the audit chain."""
    result = check_averaging_packet(packet, draft_week_start=draft_week_start)
    subject: dict[str, Any] = {
        "employee": packet.employee,
        "packet_id": packet.packet_id,
    }
    if result.accepted:
        chain.append(
            kind="packet_accepted",
            actor="rule:bc-esa-s37-packet-scheduled-hours",
            subject=subject,
            evidence={
                "section": "37(3)",
                "period_weeks": packet.period_weeks,
                "scheduled_hours_total": packet.total_scheduled_hours(),
                "audit_sentence": result.audit_sentence,
                "employer_signature_date": (
                    None
                    if packet.employer_signature_date is None
                    else packet.employer_signature_date.isoformat()
                ),
                "employee_signature_date": (
                    None
                    if packet.employee_signature_date is None
                    else packet.employee_signature_date.isoformat()
                ),
                "enforced_packet_rules": list(PACKET_ENFORCED_RULE_IDS),
            },
        )
        for rule_id in PACKET_ENFORCED_RULE_IDS:
            if rule_id == "bc-esa-s37-packet-scheduled-hours":
                continue
            chain.append(
                kind="rule_pass",
                actor=f"rule:{rule_id}",
                subject=subject,
                evidence={
                    "section": "37",
                    "packet_id": packet.packet_id,
                    "term_ok": True,
                },
            )
    else:
        for failure in result.failures:
            chain.append(
                kind="packet_rejected",
                actor=f"rule:{failure.rule_id}",
                subject=subject,
                evidence={
                    "missing_terms": [
                        {
                            "term": failure.term,
                            "section": failure.section,
                            "detail": failure.detail,
                            "rule_id": failure.rule_id,
                        }
                    ],
                    "audit_sentence": result.audit_sentence,
                },
            )
    return result
