"""Audit chain link tests."""

from __future__ import annotations

import pytest

from bc_schedule_agent.audit import AuditChain, AuditError


def test_event_chain_links() -> None:
    chain = AuditChain()
    a = chain.append(kind="ingest", actor="agent", subject={"fixture": "avail.csv"})
    b = chain.append(
        kind="rule_pass",
        actor="rule:bc-esa-s34-min-daily-hours",
        subject={"employee": "sam"},
        evidence={"hours": 8},
    )
    c = chain.append(
        kind="issued",
        actor="agent",
        evidence={"schedule_hash": "sha256:deadbeef", "ruleset_version": "esa_bc_v1"},
    )

    assert a.prev_event_id is None
    assert b.prev_event_id == a.event_id
    assert c.prev_event_id == b.event_id
    chain.verify_links()
    assert len(chain) == 3
    assert chain.tip_id == c.event_id


def test_broken_chain_detected() -> None:
    chain = AuditChain()
    chain.append(kind="ingest", actor="agent", event_id="aud_aaa")
    # Force a broken middle by poking private state (simulates corrupt load).
    from bc_schedule_agent.audit import AuditEvent

    bad = AuditEvent(
        event_id="aud_bad",
        timestamp="2026-09-29T00:00:00Z",
        prev_event_id="aud_missing",
        actor="agent",
        kind="parse",
    )
    chain._events.append(bad)  # noqa: SLF001 — intentional corruption for test
    with pytest.raises(AuditError, match="broken chain"):
        chain.verify_links()


def test_unknown_kind_refused() -> None:
    chain = AuditChain()
    with pytest.raises(AuditError, match="unknown audit kind"):
        chain.append(kind="mutate", actor="agent")
