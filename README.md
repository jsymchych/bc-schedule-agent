# bc-schedule-agent

Local demo of a governed agent that drafts a BC Employment Standards–compliant week and leaves a replayable audit trail.

**Not** KitchenStack. **Not** Chambers. Synthetic fixtures only. Decision support, not legal advice.

## Waves A–E

- Append-only audit event model and chain linker
- Versioned ESA rule graph (KitchenStack constraint *shape* + s.37 averaging packet terms)
- Sheet ingest: availability, time-off, averaging packet, coverage demand (fixtures)
- Composer: closed-world availability, hard refuses, standard (s.35/s.40) and averaging (s.37) OT proposals
- Human gates: time-off decide, OT approve/refuse; export blocked while OT pending
- Exhibits: one schedule model → PDF + XLSX + `audit.json` (same decision id); replay gate
- Local one-screen demo + `DEMO.md` 10-minute client script

## Layout

```
src/bc_schedule_agent/   # audit … exhibit, replay, demo, demo_app
fixtures/demo/           # synthetic scenario sheets
artifacts/               # issued exhibits (clean_week, demo smoke)
DEMO.md                  # 10-minute client walkthrough
tests/                   # pytest
```

## Local demo

One operator, one browser session. Run `demo_app` locally and walk `DEMO.md` in that single tab — do not open a second operator session against the same port.

```bash
python3 -m bc_schedule_agent.demo_app
# http://127.0.0.1:8765/
```

Headless smoke (no browser, no live model):

```bash
python3 -c "from bc_schedule_agent.demo import run_smoke_script; assert run_smoke_script()['ok']"
```

## Verify

```bash
python3 -m pytest -q
test -f DEMO.md
```

## Legal posture

Exports name the Employment Standards Act statute URL and the ruleset version. This is decision support, not legal advice.
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01
