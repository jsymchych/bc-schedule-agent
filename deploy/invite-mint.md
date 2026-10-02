# Mint schedule-demo invites (signed URL)

Full operator checklist (mint + link pack + rehearsal + spend gate):
**[`invite-runbook.md`](invite-runbook.md)**.

Admit path: single-use signed invite (blueprint `bc-schedule-agent-showcase-invite-token`). Not Google.

## Quick mint

Signing secret for prod lives in the schedule-demo edge project and locally in
`edge/.env.mint.local` (gitignored). Do not commit it.

```bash
cd /path/to/bc-schedule-agent
set -a; source edge/.env.mint.local; set +a
python3 scripts/mint_invite.py \
  --label 'prospect-name' \
  --ttl-days 14 \
  --base-url https://schedule-demo.kitchenstack-ai.com
```

Send that URL + public GitHub URL (+ optional `DEMO.md` anchor). Do **not**
send the raw Cloud Run URL. Real prospect send waits for typed **yes**.
