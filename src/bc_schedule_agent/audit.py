"""Append-only audit event model. Events are never edited after write."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

AUDIT_KINDS = frozenset(
    {
        "ingest",
        "parse",
        "plan",
        "place",
        "rule_pass",
        "rule_refuse",
        "repair",
        "ot_proposed",
        "ot_approved",
        "ot_refused",
        "timeoff_decided",
        "packet_accepted",
        "packet_rejected",
        "issued",
    }
)

ActorKind = Literal["agent", "human", "rule"]


class AuditError(ValueError):
    """Refuse malformed or non-append chain mutations."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_event_id() -> str:
    return f"aud_{uuid4().hex[:12]}"


@dataclass(frozen=True)
class AuditEvent:
    """One append-only row in a draft chain."""

    event_id: str
    timestamp: str
    prev_event_id: str | None
    actor: str
    kind: str
    subject: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in AUDIT_KINDS:
            raise AuditError(f"unknown audit kind: {self.kind}")
        if not self.event_id:
            raise AuditError("event_id is required")
        if not self.timestamp:
            raise AuditError("timestamp is required")
        if not self.actor:
            raise AuditError("actor is required")
        if not (
            self.actor == "agent"
            or self.actor.startswith("human:")
            or self.actor.startswith("rule:")
        ):
            raise AuditError(
                "actor must be 'agent', 'human:<name>', or 'rule:<id>'"
            )


class AuditChain:
    """One append-only chain per draft. No in-place edits."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    @property
    def tip_id(self) -> str | None:
        if not self._events:
            return None
        return self._events[-1].event_id

    def append(
        self,
        *,
        kind: str,
        actor: str,
        subject: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=event_id or _new_event_id(),
            timestamp=timestamp or _utc_now(),
            prev_event_id=self.tip_id,
            actor=actor,
            kind=kind,
            subject=dict(subject or {}),
            evidence=dict(evidence or {}),
        )
        if any(e.event_id == event.event_id for e in self._events):
            raise AuditError(f"duplicate event_id: {event.event_id}")
        self._events.append(event)
        return event

    def verify_links(self) -> None:
        """Raise if prev_event_id chain is broken."""
        prev: str | None = None
        for event in self._events:
            if event.prev_event_id != prev:
                raise AuditError(
                    f"broken chain at {event.event_id}: "
                    f"expected prev={prev!r}, got {event.prev_event_id!r}"
                )
            prev = event.event_id

    def replay_stub(self) -> dict[str, Any]:
        """Empty-chain / early-wave replay stub.

        Later waves rebuild the schedule from input hashes + demand + ruleset
        hash and match the `issued` schedule hash. Wave A only asserts chain
        integrity and returns a stub payload.
        """
        self.verify_links()
        issued = next((e for e in self._events if e.kind == "issued"), None)
        return {
            "events": len(self._events),
            "tip_id": self.tip_id,
            "issued_schedule_hash": (
                None if issued is None else issued.evidence.get("schedule_hash")
            ),
            "replay_ready": issued is not None,
        }
