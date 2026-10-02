# Invite runbook — schedule-demo (Wave D)

Operator checklist for the **signed invite** admit path on
`https://schedule-demo.kitchenstack-ai.com`.

Blueprint: `bc-schedule-agent-showcase-invite-token` (1A + 2B).  
Not Google. Not `ALLOWED_EMAILS`. Not the live KS_AI Scheduler.

**Spend / external-send gate:** the first real prospect invite send waits for
typed **yes** in CTO chat. Rehearsal mints below do not count as prospect send.

---

## 1. Where the secret lives

| Place | Role |
|-------|------|
| Vercel **schedule-demo** edge project env | `INVITE_SIGNING_SECRET` (prod verify) |
| Local mint env `edge/.env.mint.local` | Same secret for CLI mint — **gitignored** |
| Public git | Never. Hygiene scrub must not ship it. |

Redeem ledger (prod): Vercel Blob store `schedule-demo-invite-ledger` on the
edge project (`BLOB_STORE_ID` / connected store). Mint does **not** write the
ledger; first open claims `jti`.

Optional session secret: `SESSION_SECRET` on the edge (defaults to signing
secret). Do not commit either value.

---

## 2. Mint CLI

From a private checkout (estate bay or main tree), not from a stranger clone
unless they bring their own secret:

```bash
cd /path/to/bc-schedule-agent
set -a; source edge/.env.mint.local; set +a
python3 scripts/mint_invite.py \
  --label 'prospect-or-rehearsal-label' \
  --ttl-days 14 \
  --base-url https://schedule-demo.kitchenstack-ai.com
```

- **Inputs:** `--label` (operator note, not a secret) + optional `--ttl-days`
  (default 14).
- **Output:** one line — full signed URL
  `https://schedule-demo.kitchenstack-ai.com/invite?t=…`
- **Env:** `INVITE_SIGNING_SECRET` required (or `--secret`). Optional
  `INVITE_BASE_URL` instead of `--base-url`.
- **Refuse:** missing secret → exit 2, stderr starts with `REFUSE:`.

Token contract: HMAC payload with `jti`, `label`, `iat`, `exp`
(`src/bc_schedule_agent/invite_token.py`, mirrored by `edge/lib/invite.ts`).

---

## 3. Link pack template (what to send)

After typed **yes** for a real prospect, send **only** this pack:

```
BC schedule agent — demo invite

Walk (~15 min): <SIGNED_INVITE_URL>

Public repo: https://github.com/jsymchych/bc-schedule-agent
Script: DEMO.md in that repo (minutes 0–15)

Open the invite link once in a normal browser tab. Do not forward the link
before you open it — the first open burns it.
```

Optional DEMO anchor (same repo):  
`https://github.com/jsymchych/bc-schedule-agent/blob/main/DEMO.md`

| Include | Do not include |
|---------|----------------|
| Signed `/invite?t=…` URL | Raw Cloud Run `*.run.app` URL |
| Public GitHub URL | `INVITE_SIGNING_SECRET` / mint env |
| Optional `DEMO.md` link | Google allowlist / “add your email” steps |
| | Magic-email / CRM automation without typed yes |

Shared-demo policy (v1): one presenter session at a time against the hosted
process.

---

## 4. Rehearsal checklist (before prospect send)

Do this with a **rehearsal** label mint. Not a prospect send.

1. **Health** — `curl -sS -o /dev/null -w '%{http_code}\n' https://schedule-demo.kitchenstack-ai.com/api/health` → `200`.
2. **Fresh redeem** — open a newly minted invite URL in a clean browser (or
   `curl -sSI` follow once) → session cookie (`sd_session`) + demo HTML.
3. **Single-use** — same URL again with no cookie (incognito / cleared
   cookies) → refuse (`already_used` / login reason). Demo UI must not load.
4. **Unauthenticated host** — `https://schedule-demo.kitchenstack-ai.com/`
   without cookie → invite-required, not the demo UI.
5. **Cloud Run direct** — anonymous GET on the Wave A service URL → `403`
   (or `401`). Not the prospect path.
6. **Hosted walk** — with a valid session, walk `DEMO.md` minutes **0–15**
   (or headless: `python3 -c "from bc_schedule_agent.demo import run_smoke_script; assert run_smoke_script()['ok']"`).
7. **Time-off demo UI** — confirm still **absent** (deferred). Composer still
   has the time-off **gate scenario** / sheet path; there is no separate
   approve/deny time-off management UI in this pack.
8. **KS_AI Scheduler** — unchanged; no new route into generate.

---

## 5. Spend gate

| Action | Gate |
|--------|------|
| Rehearsal mint + self-open | Allowed (local / operator) |
| Edge `vercel deploy --prod` / DNS cutover | Typed **yes** (already used for Wave B) |
| **First real prospect invite send** | Typed **yes** — still required |
| Email automation / CRM blast | Out of scope without typed yes |

This seat does not send email. After rehearsal PASS, stop and wait for the
operator to type **yes** before any prospect link leaves the building.

---

## 6. Quick reference

| Item | Value |
|------|--------|
| Host | `https://schedule-demo.kitchenstack-ai.com` |
| Mint | `python3 scripts/mint_invite.py --label … --ttl-days 14` |
| Public repo | `https://github.com/jsymchych/bc-schedule-agent` |
| Walk script | `DEMO.md` |
| Edge notes | `edge/README.md` |
| Thin mint notes | `deploy/invite-mint.md` (pointer to this runbook) |
