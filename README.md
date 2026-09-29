# bc-schedule-agent

Local demo of a governed agent that drafts a BC Employment Standards–compliant week and leaves a replayable audit trail.

**Not** KitchenStack. **Not** Chambers. Synthetic fixtures only. Decision support, not legal advice.

## Wave A (this slice)

- Append-only audit event model and chain linker
- Versioned ESA rule graph (KitchenStack constraint *shape* + s.37 averaging packet terms)
- Unit tests: chain links, stable ruleset hash, empty-chain replay stub

No ingest, no UI, no PDF in this wave.

## Layout

```
src/bc_schedule_agent/   # audit + ruleset loaders
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
