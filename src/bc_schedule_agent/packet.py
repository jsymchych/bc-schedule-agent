"""ESA s.37 averaging packet acceptance / rejection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from bc_schedule_agent.audit import AuditChain
from bc_schedule_agent.models import AveragingPacket, sunday_of


@dataclass(frozen=True)
class PacketTermFailure:
    term: str
    section: str
    detail: str


@dataclass(frozen=True)
class PacketCheckResult:
    accepted: bool
    failures: tuple[PacketTermFailure, ...]
    audit_sentence: str

    @property
    def missing_terms(self) -> list[str]:
        return [f.term for f in self.failures]


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
            PacketTermFailure("employee", "37(2)(a)", "packet must name one employee")
        )

    if not packet.in_writing:
        failures.append(
            PacketTermFailure("in_writing", "37(2)", "averaging agreement must be in writing")
        )

    if not packet.employer_signed:
        failures.append(
            PacketTermFailure(
                "employer_signature",
                "37(2)(a)(i)",
                "employer signature missing",
            )
        )

    if not packet.employee_signed:
        failures.append(
            PacketTermFailure(
                "employee_signature",
                "37(2)(a)(ii)",
                "employee signature missing",
            )
        )

    if not packet.signed_before_start:
        failures.append(
            PacketTermFailure(
                "signed_before_start",
                "37(2)(a)",
                "must be signed by both parties before the start date",
            )
        )

    if packet.period_weeks is None:
        failures.append(
            PacketTermFailure("period_weeks", "37(2)", "period length not stated")
        )
    elif packet.period_weeks not in (1, 2, 3, 4):
        failures.append(
            PacketTermFailure(
                "period_weeks",
                "37(2)",
                f"period_weeks must be 1–4, got {packet.period_weeks}",
            )
        )

    if packet.repeat_count is None:
        failures.append(
            PacketTermFailure(
                "repeat_count",
                "37(2)",
                "repeat count not stated (zero is allowed when stated)",
            )
        )

    if packet.start_date is None or packet.expiry_date is None:
        failures.append(
            PacketTermFailure(
                "start_and_expiry_dates",
                "37(2)",
                "start date and expiry date are required",
            )
        )
    elif packet.expiry_date < packet.start_date:
        failures.append(
            PacketTermFailure(
                "start_and_expiry_dates",
                "37(2)",
                "expiry date precedes start date",
            )
        )

    if not packet.daily_schedule:
        failures.append(
            PacketTermFailure(
                "daily_schedule_present",
                "37(2)",
                "work schedule for each day in the period is required",
            )
        )
    elif packet.period_weeks in (1, 2, 3, 4) and packet.start_date is not None:
        expected_days = packet.period_weeks * 7
        if len(packet.daily_schedule) < expected_days:
            # Allow sparse zero-hour days only if every calendar day is listed.
            covered = {d.date for d in packet.daily_schedule}
            period_dates = [
                packet.start_date + timedelta(days=i) for i in range(expected_days)
            ]
            missing = [d for d in period_dates if d not in covered]
            if missing:
                failures.append(
                    PacketTermFailure(
                        "daily_schedule_present",
                        "37(2)",
                        f"missing schedule days: {', '.join(d.isoformat() for d in missing[:3])}"
                        + ("…" if len(missing) > 3 else ""),
                    )
                )

    if not packet.copy_received_before_start:
        failures.append(
            PacketTermFailure(
                "copy_received_before_start",
                "37(2)(c)",
                "employee must receive a copy before the period starts",
            )
        )

    if packet.start_date is not None and packet.expiry_date is not None:
        if not (packet.start_date <= week_start and week_end <= packet.expiry_date):
            failures.append(
                PacketTermFailure(
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
                PacketTermFailure(
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
            },
        )
    else:
        chain.append(
            kind="packet_rejected",
            actor="rule:bc-esa-s37-packet-signed-both",
            subject=subject,
            evidence={
                "missing_terms": [
                    {"term": f.term, "section": f.section, "detail": f.detail}
                    for f in result.failures
                ],
                "audit_sentence": result.audit_sentence,
            },
        )
    return result
