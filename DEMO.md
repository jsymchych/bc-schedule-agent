# BC schedule agent — 10-minute client script

Synthetic names only. No Wellington data. No live model required.

**Legal posture:** decision support under the Employment Standards Act, not legal advice.  
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01

## Prep (30 seconds)

```bash
cd /Volumes/SovereignSSD/builds/bc-schedule-agent
python3 -m pytest -q
python3 -m bc_schedule_agent.demo_app
```

Open `http://127.0.0.1:8765/`. Or run the headless smoke path (no browser):

```bash
python3 -c "from bc_schedule_agent.demo import run_smoke_script; print(run_smoke_script()['ok'])"
```

## Minute 0–2 — Clean week

1. Scenario: **Clean week** (or ask: “Draft a clean Mon–Fri week for Sam”).
2. Click **Draft week**.
3. Show the week grid: Sam Mon–Fri ~8h with meal break.
4. Open the **audit drawer**: ingest → parse (fixture demand) → place → no `rule_refuse`.
5. **Download PDF / XLSX** is enabled. Click it.
6. Point at the replay sentence: same schedule hash rebuilt from input hashes + stored demand + ruleset hash.

Proof: `issued` exists; downloads share one decision id; replay matches.

## Minute 2–5 — Overtime gate

1. Scenario: **Overtime gate** (or ask: “ten-hour day” / “overtime”).
2. Draft. Grid shows a 10h Monday. Audit shows `ot_proposed` at 1.5x (s.35 / s.40).
3. Downloads stay **disabled** while OT is `PENDING_APPROVAL`.
4. Click **Approve pending OT** as named human **Alex Rivera** (agent has no approve action).
5. Downloads enable. Download again. Replay sentence returns.

Proof: the agent cannot issue overtime silently; a named human and timestamp sit on the line.

## Minute 5–8 — Bad s.37 packet

1. Scenario: **Bad s.37 packet** (or ask: “averaging packet” / “s.37”).
2. Draft. Audit drawer shows `packet_rejected` naming the missing employee signature (s.37(2)(a)(ii)).
3. Sam stays on the **standard** regime — a human cannot override a missing term.
4. Note any daily OT proposals under s.40; they still need a named approve before issue.

Proof: incomplete averaging packets do not change the overtime regime.

## Minute 8–10 — Replay sentence (close)

1. Return to **Clean week**, draft, download once more if needed.
2. Read the drawer aloud: every place, pass/refuse, and issue is a sentence a stranger can follow.
3. Close on the product: **the record is the product** — PDF and XLSX are exhibits of one schedule model chained to `audit.json`.

## Headless checklist (CI / no browser)

| Step | Expect |
|---|---|
| `clean_week` | `download_enabled`, zero pending OT, replay sentence |
| `ot_gate` before approve | `ExportBlocked` on download |
| `ot_gate` after `Alex Rivera` | downloads + replay |
| `bad_s37_packet` | audit prose contains rejected averaging packet |

Implemented by `bc_schedule_agent.demo.run_smoke_script()`.
