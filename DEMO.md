# BC schedule agent — ~15-minute client script

Synthetic names only. No Wellington data. No live model required.

**Legal posture:** decision support under the Employment Standards Act, not legal advice.  
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01

The product takes four sheets — hours of operations, sales projections, availability, and time-off — and drafts an ESA-compliant week that prefers zero overtime. Coverage is derived from ops + sales. Closing line: **who approved OT and why is on the chain.**

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

## Minute 0–4 — Busy week, zero OT

1. Scenario: **Busy week — zero OT** (or ask: “Take Mon–Fri hours and steady sales with Sam and Jordan available”).
2. Show the **four inputs**: hours open Mon–Fri 09:00–17:30; sales ~$1,500 each weekday; Sam and Jordan available; no time-off.
3. Click **Draft week**. Grid shows Sam and Jordan covering each weekday at 8h with a meal break — no overtime line.
4. Open the **audit drawer**: ops + sales ingested, coverage planned, placed, no `rule_refuse`.
5. **Download PDF / XLSX / audit.json** is enabled. Click it.

Proof: `issued` exists; downloads share one decision id; replay matches; zero pending OT.

## Minute 4–8 — Peak OT: block, approve with reason, or refuse

1. Scenario: **Peak needs OT** (or ask: “Peak Monday / overtime”).
2. Four inputs: Monday open 08:00–18:30; only Sam available. Draft. Grid shows a 10h Monday. Audit shows `ot_proposed` (s.35 / s.40) with unavoidable evidence.
3. Downloads stay **disabled** while OT is `PENDING_APPROVAL`.
4. Approve requires **name + reason**. Type **Alex Rivera** and a non-empty why (e.g. “Peak Monday: only Sam covers the long open”). Empty reason stays blocked.
5. Downloads enable. Download again. Replay names who approved and why.
6. Re-draft the same peak week and click **Refuse OT** instead — composer records the refuse; issue still waits on a clean line.

Proof: the agent cannot issue overtime silently; a named human, timestamp, and reason sit on the line.

## Minute 8–11 — Time-off gate

1. Scenario: **Time-off gate** (or ask: “pending time-off”).
2. Four inputs: Sam’s Monday request is **PENDING**. Draft. Audit names that the request awaits a human. Monday does not place. Downloads stay off while the refuse is on the chain.

Proof: PENDING leave is a human gate, not an agent skip.

## Minute 11–14 — Bad s.37 packet

1. Scenario: **Bad s.37 packet** (or ask: “averaging packet”).
2. Draft. Audit drawer shows `packet_rejected` naming the missing employee signature (s.37(2)(a)(ii)).
3. Sam stays on the **standard** regime — a human cannot override a missing term.
4. Any daily OT proposals under s.40 still need a named approve-with-reason before issue.

Proof: incomplete averaging packets do not change the overtime regime.

## Minute 14–15 — Close

Read the last OT approve sentence aloud. Close on the product: **who approved OT and why is on the chain.** PDF, XLSX, and `audit.json` are exhibits of that record.

## Headless checklist (CI / no browser)

| Step | Expect |
|---|---|
| `busy_week_zero_ot` | four inputs shown; `download_enabled`; zero pending OT; replay; `audit.json` on disk |
| `peak_needs_ot` before approve | `ExportBlocked` on download |
| `peak_needs_ot` after `Alex Rivera` + reason | downloads + replay names who/why |
| `peak_needs_ot` refuse | `ot_refused` in the drawer |
| `time_off_gate` | pending time-off awaits human; downloads off |
| `bad_s37_packet` | audit prose contains rejected averaging packet |

Implemented by `bc_schedule_agent.demo.run_smoke_script()`.
