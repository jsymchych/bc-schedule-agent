# bc-schedule-agent

Local demo of a governed agent that drafts a BC Employment Standards–compliant week and leaves a replayable audit trail.

**Not** KitchenStack. **Not** Chambers. Synthetic fixtures only. Decision support, not legal advice.

## Waves A–D (this slice)

- Append-only audit event model and chain linker
- Versioned ESA rule graph (KitchenStack constraint *shape* + s.37 averaging packet terms)
- Sheet ingest: availability, time-off, averaging packet, coverage demand (fixtures)
- Composer: closed-world availability, hard refuses, standard (s.35/s.40) and averaging (s.37) OT proposals
- Human gates: time-off decide, OT approve/refuse; export blocked while OT pending
- Exhibits: one schedule model → PDF + XLSX + `audit.json` (same decision id); replay gate

No UI yet (Wave E local demo).

## Layout

```
src/bc_schedule_agent/   # audit, ruleset, ingest, packet, composer, gates, exhibit, replay
rulesets/                # versioned JSON rule graphs
tests/                   # pytest
```

## Verify

```bash
python3 -m pytest -q
```

## Legal posture

Exports name the Employment Standards Act statute URL and the ruleset version. This is decision support, not legal advice.
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01
