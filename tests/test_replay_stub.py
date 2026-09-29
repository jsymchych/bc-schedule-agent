"""Empty-chain replay stub (Wave A). Full replay lands in Wave D."""

from __future__ import annotations

from bc_schedule_agent.audit import AuditChain


def test_empty_chain_replay_stub() -> None:
    chain = AuditChain()
    result = chain.replay_stub()
    assert result["events"] == 0
    assert result["tip_id"] is None
    assert result["issued_schedule_hash"] is None
    assert result["replay_ready"] is False


def test_issued_marks_replay_ready() -> None:
    chain = AuditChain()
    chain.append(kind="ingest", actor="agent")
    chain.append(
        kind="issued",
        actor="agent",
        evidence={"schedule_hash": "sha256:abc123"},
    )
    result = chain.replay_stub()
    assert result["events"] == 2
    assert result["issued_schedule_hash"] == "sha256:abc123"
    assert result["replay_ready"] is True
