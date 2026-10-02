# BC schedule agent — ~15-minute client script

Synthetic names only. No Wellington data. No live model required.

**Legal posture:** decision support under the Employment Standards Act, not legal advice.  
Statute: https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/96113_01

The product takes four sheets — hours of operations, sales projections, availability, and time-off — and drafts an ESA-compliant week that prefers zero overtime. Coverage is derived from ops + sales. A **History shelf** keeps issued weeks; a **Parameters** panel shows the active parameter shelf (id, hashes, roster). Soft priors from history bias the next draft; the current shelf stays hard authority. Closing line: **who approved OT and why is on the chain.**

**Shared-demo policy (v1):** one presenter session at a time. Do not run a second concurrent operator session against the same demo process.

## Paths

### Hosted (prospect / pitch)

1. Open **https://schedule-demo.kitchenstack-ai.com** after the operator invite (see `edge/README.md` for the admit path).
2. Walk minutes 0–15 below in that one browser tab.
3. Do **not** open the raw Cloud Run URL; admit is invite-only via the edge.

Hosted cutover and invite send wait for typed **yes** where spend/external-send applies.

### Local clone (developer / stranger)

From a fresh clone of this repo:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python3 -m pytest -q
python3 -m bc_schedule_agent.demo_app
```

Open `http://127.0.0.1:8765/`. Or run the headless smoke path (no browser):

```bash
python3 -c "from bc_schedule_agent.demo import run_smoke_script; print(run_smoke_script()['ok'])"
```

Estate checkouts may `cd` into a private builds tree instead of cloning; the venv / `pip install -e ".[dev]"` / `pytest` / `demo_app` sequence is the same.

## Prep (30 seconds)

Confirm either hosted session (invite) or local `demo_app` is up. One tab only.

## Minute 0–3 — History shelf + parameters

1. Open the demo. The **History shelf** lists active issued rows (seed starts with a small synthetic set under `fixtures/history/` — growth from 1 → few weeks, not a fill of the cap). Each row shows `week_start`, `decision_id`, and **zero-OT** vs **OT-with-reason**.
2. Open **Parameters**: active shelf id, content hashes, and derived roster.
3. Select a prior row. **Load prior (prior-only)** is the default reopen — soft priors stay on; the active shelf is not overwritten. **Adopt shelf from week** is explicit and binds that week’s parameter shelf as hard authority.

Proof: shelf is visible before you issue a new week; prior-only does not swap params.

## Minute 3–7 — Busy week, zero OT

1. Set **Week start** to the default Sunday (or keep it). Scenario: **Busy week — zero OT** (or ask: “Take Mon–Fri hours and steady sales with Sam, Jordan, and Riley available”).
2. Show the **four inputs**: hours open Mon–Fri 09:00–17:30; sales ~$1,500 each weekday; Sam, Jordan, and Riley on the roster; no time-off.
3. Click **Draft week**. Grid shows a multi-employee week at straight time — no overtime line. Parameters panel shows the bound shelf.
4. Open the **audit drawer**: ops + sales ingested, coverage planned, placed, no `rule_refuse`.
5. Three download controls are enabled: **PDF**, **XLSX**, and **audit.json**. Click **PDF** first — that issues the week once, then saves only the PDF. Click **XLSX** next, then **audit.json**. Each click fetches only that file; the second and third clicks do not re-issue. History shelf grows by one row.

Proof: `issued` exists once; all three exhibits share one decision id; replay matches; zero pending OT; shelf count increased.

## Minute 7–10 — Compose under priors (continuity)

1. Advance **Week start** by seven days (next Sunday). Keep **Busy week — zero OT**.
2. Draft again. Soft priors from the History shelf bias familiar placement; ESA and the current parameter shelf stay hard — no placements outside closed-world availability.
3. Issue via **PDF** (then XLSX / audit.json as needed). Shelf grows again. Issued evidence names `history_prior_week_starts[]`.

Proof: continuity — shelf → params → compose under priors → issue → shelf grows.

## Minute 10–12 — Peak OT: block, approve with reason, or refuse

1. Scenario: **Peak needs OT** (or ask: “Peak Monday / overtime”).
2. Four inputs: Monday open 08:00–18:30; Sam on Monday only; Jordan and Riley cover Tue–Fri. Draft. Grid shows a 10h Monday. Audit shows `ot_proposed` (s.35 / s.40) with unavoidable evidence.
3. Downloads stay **disabled** while OT is `PENDING_APPROVAL`.
4. Approve requires **name + reason**. Type **Alex Rivera** and a non-empty why (e.g. “Peak Monday: only Sam is rostered for the long open”). Empty reason stays blocked.
5. Downloads enable. Walk **PDF**, then **XLSX**, then **audit.json** as separate choices (same issue-once rule). Replay names who approved and why.
6. Re-draft the same peak week and click **Refuse OT** instead — composer records the refuse; issue still waits on a clean line.

Proof: the agent cannot issue overtime silently; a named human, timestamp, and reason sit on the line. Download type controls / gate law unchanged.

## Minute 12–13 — Time-off gate

1. Scenario: **Time-off gate** (or ask: “pending time-off”).
2. Four inputs: Sam’s Monday request is **PENDING**. Draft. Audit names that the request awaits a human. Monday does not place. Downloads stay off while the refuse is on the chain.

Proof: PENDING leave is a human gate, not an agent skip.

## Minute 13–14 — Bad s.37 packet

1. Scenario: **Bad s.37 packet** (or ask: “averaging packet”).
2. Draft. Audit drawer shows `packet_rejected` naming the missing employee signature (s.37(2)(a)(ii)).
3. Sam stays on the **standard** regime — a human cannot override a missing term.
4. Any daily OT proposals under s.40 still need a named approve-with-reason before issue.

Proof: incomplete averaging packets do not change the overtime regime.

## Minute 14–15 — Close

Read the last OT approve sentence aloud. Close on the product: **who approved OT and why is on the chain.** History proves continuity; parameters prove how this house runs. Choose the exhibit type you need — PDF to talk, XLSX to mark up, `audit.json` to prove. No “Download all” primary.

## Headless checklist (CI / no browser)

| Step | Expect |
|---|---|
| `history_seed` | History shelf lists seeded rows with zero-OT and OT-with-reason |
| `busy_week_zero_ot` | four inputs + parameter shelf; `download_enabled`; shelf grows; prior-only load safe |
| `peak_needs_ot` before approve | `ExportBlocked` on download |
| `peak_needs_ot` after `Alex Rivera` + reason | downloads + replay names who/why |
| `peak_needs_ot` refuse | `ot_refused` in the drawer |
| `time_off_gate` | pending time-off awaits human; downloads off |
| `bad_s37_packet` | audit prose contains rejected averaging packet |
| `history_continuity` | next `week_start` under priors; issue grows shelf; `history_prior_week_starts` set |

Implemented by `bc_schedule_agent.demo.run_smoke_script()`.
