# schedule-demo edge

Thin Vercel project for **`https://schedule-demo.kitchenstack-ai.com`**.

Flow: **single-use signed invite** (`/invite?t=…`) → redeem ledger burns `jti` →
HttpOnly session cookie (~12h) → reverse proxy → Cloud Run `bc-schedule-agent-demo`.

Not the live KS_AI Scheduler. Not `app.kitchenstack-ai.com`. Google OAuth is
**not** on the admit path.

Operator mint / send / rehearsal: **`../deploy/invite-runbook.md`**.

## Env

Copy `.env.example`. Required for prod:

| Var | Notes |
|-----|--------|
| `INVITE_SIGNING_SECRET` | HMAC secret shared with mint CLI (crown jewel) |
| Redeem ledger | Vercel Blob `schedule-demo-invite-ledger` (`BLOB_STORE_ID`) or KV REST |
| `CLOUD_RUN_ORIGIN` | Wave A URL, e.g. `https://bc-schedule-agent-demo-….run.app` |
| `GCP_SA_JSON` | SA JSON with `roles/run.invoker` on the demo service |

Local/dev may set `INVITE_REDEEM_BACKEND=memory` — **not** for production.

## Mint an invite

```bash
cd ..   # bc-schedule-agent root
set -a; source edge/.env.mint.local; set +a   # gitignored
python3 scripts/mint_invite.py --label prospect --ttl-days 14
# → https://schedule-demo.kitchenstack-ai.com/invite?t=…
```

## Shared-demo policy (v1)

One presenter session at a time. Process-local synthetic state — no multi-tenant sticky sessions. Documented for public clone / hosted pitch alike.

## Spend gate

DNS cutover + `vercel deploy --prod` wait for typed **yes** in CTO chat. See
`../deploy/edge-vercel.sh` and `../deploy/dns-schedule-demo.md`. **External
prospect invite send** also waits for typed **yes** (see invite runbook).

## Local

```bash
cd edge
cp .env.example .env.local   # fill secrets; INVITE_REDEEM_BACKEND=memory ok locally
npm install
npm run dev                  # :3007
```
