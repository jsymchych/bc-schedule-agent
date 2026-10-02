# bc-schedule-agent

Local (and invite-gated hosted) demo of a governed agent that drafts a BC Employment Standards–compliant week and leaves a replayable audit trail.

**Not** KitchenStack. **Not** Chambers. Synthetic fixtures only. Decision support, not legal advice.

## Install (stranger / clone path)

Requires Python 3.11+. From the repo root (after `git clone`):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python3 -m pytest -q
```

`pyproject.toml` sets `pythonpath = ["src"]` for pytest. Editable install puts `bc_schedule_agent` on `PYTHONPATH` for `python3 -m …` as well.

Estate operators may still keep a checkout under a private builds tree; the instructions above are the supported stranger path.

## Local demo

**Shared-demo policy (v1):** one presenter session at a time. Process-local synthetic state — no multi-tenant sticky sessions. Do not open a second operator tab against the same process.

```bash
python3 -m bc_schedule_agent.demo_app
# http://127.0.0.1:8765/
```

Headless smoke (no browser, no live model):

```bash
python3 -c "from bc_schedule_agent.demo import run_smoke_script; assert run_smoke_script()['ok']"
```

Walkthrough script: [`DEMO.md`](DEMO.md).

## Hosted walkthrough

Invite-gated demo (no public Cloud Run):

- **https://schedule-demo.kitchenstack-ai.com**

Unauthenticated visitors do not reach the demo UI. Direct Cloud Run URLs are not the prospect path. Edge admit details live in `edge/README.md`.

Public proof repo: https://github.com/jsymchych/bc-schedule-agent

## Waves A–E (engine)

- Append-only audit event model and chain linker
- Versioned ESA rule graph (KitchenStack constraint *shape* + s.37 averaging packet terms)
- Sheet ingest: availability, time-off, averaging packet, coverage demand (fixtures)
- Composer: closed-world availability, hard refuses, standard (s.35/s.40) and averaging (s.37) OT proposals
- Human gates: time-off decide, OT approve/refuse; export blocked while OT pending
- Exhibits: one schedule model → PDF + XLSX + `audit.json` (same decision id); replay gate
- Local one-screen demo + `DEMO.md` client script

## Layout

```
src/bc_schedule_agent/   # audit … exhibit, replay, demo, demo_app
fixtures/demo/           # synthetic scenario sheets
artifacts/               # issued exhibits (clean_week, demo smoke)
DEMO.md                  # client walkthrough (hosted + local)
edge/                    # thin schedule-demo invite edge (Vercel)
deploy/                  # Cloud Run + DNS operator notes
tests/                   # pytest
```

## Verify

```bash
python3 -m pytest -q
test -f DEMO.md
```

## Legal posture

Exports name the Employment Standards Act statute URL and the ruleset version. This is decision support, not legal advice.
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01
