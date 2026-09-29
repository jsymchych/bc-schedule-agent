"""Ruleset load and hash stability."""

from __future__ import annotations

from bc_schedule_agent.ruleset import load_ruleset, ruleset_hash


def test_ruleset_hash_stable() -> None:
    first = load_ruleset()
    second = load_ruleset()
    assert first.version == "esa_bc_v1.1"
    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:")
    # Re-hashing the on-disk payload must match the loader's stored hash.
    import json

    raw = json.loads(first.path.read_text(encoding="utf-8"))
    assert ruleset_hash(raw) == first.content_hash


def test_ruleset_includes_ks_shape_and_s37() -> None:
    rs = load_ruleset()
    ids = set(rs.rule_ids)
    # 14 KitchenStack-shaped ESA constraint ids
    for kid in (
        "bc-esa-s32-meal-break",
        "bc-esa-s32-paid-meal-break",
        "bc-esa-s33-split-shift",
        "bc-esa-s34-min-daily-hours",
        "bc-esa-s34-min-daily-hours-long",
        "bc-esa-s35-daily-overtime-threshold",
        "bc-esa-s40-overtime-rates",
        "bc-esa-s36-weekly-rest",
        "bc-esa-s36-between-shifts",
        "bc-esa-s39-no-excessive-hours",
        "bc-esa-s16-minimum-wage",
        "bc-esa-s44-stat-holiday-entitlement",
        "bc-esa-s46-stat-holiday-pay",
        "bc-esa-stat-holidays-list",
    ):
        assert kid in ids
    # s.37 packet terms present
    assert "bc-esa-s37-packet-signed-both" in ids
    assert "bc-esa-s37-packet-scheduled-hours" in ids
    assert rs.get("bc-esa-s32-meal-break")["constraint"]["type"] == (
        "max_consecutive_hours_without_break"
    )
