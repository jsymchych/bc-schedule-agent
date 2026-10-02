# schedule-demo edge

Thin Vercel project for **`https://schedule-demo.kitchenstack-ai.com`**.

Flow: Google OAuth → `ALLOWED_EMAILS` (fail-closed) → reverse proxy → Cloud Run `bc-schedule-agent-demo`.

Not the live KS_AI Scheduler. Not `app.kitchenstack-ai.com`. Invite edits belong on **this** project's env.

## Env

Copy `.env.example`. Required for prod:

| Var | Notes |
|-----|--------|
| `AUTH_SECRET` | NextAuth secret |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client; add `https://schedule-demo.kitchenstack-ai.com/api/auth/callback/google` |
| `ALLOWED_EMAILS` | Comma-separated. **Empty denies everyone.** |
| `CLOUD_RUN_ORIGIN` | Wave A URL, e.g. `https://bc-schedule-agent-demo-….run.app` |
| `GCP_SA_JSON` | SA JSON with `roles/run.invoker` on the demo service |

## Shared-demo policy (v1)

One presenter session at a time. Process-local synthetic state — no multi-tenant sticky sessions. Documented for public clone / hosted pitch alike.

## Spend gate

DNS cutover + `vercel deploy --prod` wait for typed **yes** in CTO chat. See `../deploy/edge-vercel.sh` and `../deploy/dns-schedule-demo.md`.

## Local

```bash
cd edge
cp .env.example .env.local   # fill secrets
npm install
npm run dev                  # :3007
```
