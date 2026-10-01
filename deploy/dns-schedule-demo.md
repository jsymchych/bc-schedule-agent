# DNS — schedule-demo.kitchenstack-ai.com

Heuristic: **Squarespace first** for `kitchenstack-ai.com`. GCP Cloud DNS zone may exist but nameservers are often not authoritative.

## Pre-check

```bash
dig NS kitchenstack-ai.com +short
# Expect Squarespace / registrar NS — not necessarily GCP Cloud DNS
dig schedule-demo.kitchenstack-ai.com +short
```

## Cutover (spend — typed yes)

1. In Squarespace → Domains → `kitchenstack-ai.com` → DNS → Custom records.
2. Add CNAME (or A/ALIAS per Vercel docs) for host `schedule-demo` → Vercel target from the **schedule-demo** edge project (not `app.`).
3. In Vercel project `bc-schedule-agent-edge` → Domains → add `schedule-demo.kitchenstack-ai.com`.
4. Wait for TLS; smoke `/api/health` then Google sign-in with an allowlisted email.

## Do not

- Point this hostname at live KS_AI Scheduler / `app.kitchenstack-ai.com`.
- Edit only `app.` allowlist and assume schedule-demo inherits it — it does not.
- Bind Cloud Run `allUsers` invoker to “make DNS work.”
