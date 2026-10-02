"""Wave D — invite runbook + mint CLI (no prospect send)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bc_schedule_agent.invite_token import verify_token

ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-invite-signing-secret-not-for-prod"


def test_invite_runbook_covers_mint_send_rehearsal_and_spend_gate() -> None:
    runbook = ROOT / "deploy" / "invite-runbook.md"
    assert runbook.is_file()
    text = runbook.read_text(encoding="utf-8")
    assert "scripts/mint_invite.py" in text
    assert "INVITE_SIGNING_SECRET" in text
    assert ".env.mint.local" in text
    assert "https://schedule-demo.kitchenstack-ai.com/invite?t=" in text
    assert "https://github.com/jsymchych/bc-schedule-agent" in text
    assert "DEMO.md" in text
    assert "already_used" in text
    assert "typed **yes**" in text
    assert "ALLOWED_EMAILS" not in text or "Not `ALLOWED_EMAILS`" in text
    assert "Google" in text  # explicit "Not Google"
    assert "raw Cloud Run" in text.lower() or "Raw Cloud Run" in text
    assert "time-off" in text.lower()
    assert "deferred" in text.lower()
    assert "KS_AI Scheduler" in text
    # No Google allowlist operator steps (phrase may appear only as "do not")
    assert "Do not include" in text
    assert "OAuth consent" not in text


def test_invite_mint_pointer_and_demo_hosted_path() -> None:
    pointer = (ROOT / "deploy" / "invite-mint.md").read_text(encoding="utf-8")
    assert "invite-runbook.md" in pointer
    demo = (ROOT / "DEMO.md").read_text(encoding="utf-8")
    assert "signed invite" in demo.lower()
    assert "deploy/invite-runbook.md" in demo
    assert "No Google login" in demo
    edge = (ROOT / "edge" / "README.md").read_text(encoding="utf-8")
    assert "invite-runbook.md" in edge
    assert "Google OAuth is" in edge
    example = (ROOT / "edge" / ".env.example").read_text(encoding="utf-8")
    assert "INVITE_SIGNING_SECRET" in example
    assert "ALLOWED_EMAILS" not in example
    assert "GOOGLE_CLIENT" not in example


def test_mint_cli_prints_signed_url() -> None:
    script = ROOT / "scripts" / "mint_invite.py"
    assert script.is_file()
    result = subprocess.run(
        [
            "python3",
            str(script),
            "--label",
            "wave-d-rehearsal",
            "--ttl-days",
            "1",
            "--base-url",
            "https://schedule-demo.kitchenstack-ai.com",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "INVITE_SIGNING_SECRET": SECRET},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip()
    assert line.startswith("https://schedule-demo.kitchenstack-ai.com/invite?t=")
    token = line.split("t=", 1)[1]
    payload = verify_token(SECRET, token)
    assert payload["label"] == "wave-d-rehearsal"


def test_mint_cli_refuses_without_secret() -> None:
    env = {k: v for k, v in os.environ.items() if k != "INVITE_SIGNING_SECRET"}
    result = subprocess.run(
        ["python3", str(ROOT / "scripts" / "mint_invite.py"), "--label", "x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 2
    assert "REFUSE" in result.stderr


def test_time_off_approve_deny_ui_still_absent() -> None:
    """Deferred: no separate time-off management UI; gate scenario may remain."""
    app = (ROOT / "src" / "bc_schedule_agent" / "demo_app.py").read_text(encoding="utf-8")
    assert "btn-approve" in app  # OT approve stays
    assert "Approve pending OT" in app
    # No dedicated time-off approve/deny controls in the demo UI
    assert "Approve time-off" not in app
    assert "Deny time-off" not in app
    assert "btn-approve-timeoff" not in app
    assert "btn-deny-timeoff" not in app
    runbook = (ROOT / "deploy" / "invite-runbook.md").read_text(encoding="utf-8")
    assert "absent" in runbook.lower()
    assert "deferred" in runbook.lower()
